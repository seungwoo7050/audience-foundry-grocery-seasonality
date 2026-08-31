"""Fail-closed helpers for the local-only Phase 0 review and publication path."""

from __future__ import annotations

import re
import uuid
from enum import StrEnum
from typing import Any, Final

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import transaction

LOCAL_OPERATOR_USERNAME: Final = "phase0-local-operator"
LOCAL_APPROVAL_REASON_CODE: Final = "LOCAL_PHASE0_SOURCE_GATE_APPROVED"
PUBLIC_COPY_REVISIONS: Final = frozenset({"ko-v1", "ko-v2", "ko-v3"})

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_PERMISSION_SPECS: Final = frozenset(
    {
        ("grocery", "reviewdecision", "review_generation"),
        ("grocery", "publicationactivation", "publish_publication"),
    }
)


class LocalPhase0Code(StrEnum):
    ENVIRONMENT_DENIED = "LOCAL_PHASE0_ENVIRONMENT_DENIED"
    PERMISSION_MISSING = "LOCAL_PHASE0_PERMISSION_MISSING"
    OPERATOR_MISSING = "LOCAL_PHASE0_OPERATOR_MISSING"
    OPERATOR_CONFLICT = "LOCAL_PHASE0_OPERATOR_CONFLICT"
    ACTOR_ID_INVALID = "LOCAL_PHASE0_ACTOR_ID_INVALID"
    UUID_INVALID = "LOCAL_PHASE0_UUID_INVALID"
    SHA256_INVALID = "LOCAL_PHASE0_SHA256_INVALID"
    COPY_REVISION_INVALID = "LOCAL_PHASE0_COPY_REVISION_INVALID"
    GENERATION_INVALID = "LOCAL_PHASE0_GENERATION_INVALID"
    REVIEW_FAILED = "LOCAL_PHASE0_REVIEW_FAILED"
    PUBLICATION_FAILED = "LOCAL_PHASE0_PUBLICATION_FAILED"
    PERSISTENCE_FAILED = "LOCAL_PHASE0_PERSISTENCE_FAILED"


class LocalPhase0Error(RuntimeError):
    """A local command failure containing only one fixed operational code."""

    def __init__(self, code: LocalPhase0Code) -> None:
        self.code = code
        super().__init__(code.value)


def require_local_phase0_environment() -> None:
    if settings.DEBUG is not True or getattr(settings, "ADMIN_ENABLED", None) is not False:
        raise LocalPhase0Error(LocalPhase0Code.ENVIRONMENT_DENIED)


def _parse_uuid(value: str) -> uuid.UUID:
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):  # fmt: skip
        raise LocalPhase0Error(LocalPhase0Code.UUID_INVALID) from None
    if str(parsed) != value:
        raise LocalPhase0Error(LocalPhase0Code.UUID_INVALID)
    return parsed


def require_uuid(value: object) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        return _parse_uuid(value)
    raise LocalPhase0Error(LocalPhase0Code.UUID_INVALID)


def require_sha256(value: object) -> str:
    if isinstance(value, str) and _SHA256.fullmatch(value) is not None:
        return value
    raise LocalPhase0Error(LocalPhase0Code.SHA256_INVALID)


def require_copy_revision(value: object) -> str:
    if isinstance(value, str) and value in PUBLIC_COPY_REVISIONS:
        return value
    raise LocalPhase0Error(LocalPhase0Code.COPY_REVISION_INVALID)


def _required_permissions(*, lock: bool) -> tuple[Permission, ...]:
    query = Permission.objects.select_related("content_type").filter(
        content_type__app_label="grocery",
        content_type__model__in=("reviewdecision", "publicationactivation"),
        codename__in=("review_generation", "publish_publication"),
    )
    if lock:
        query = query.select_for_update()
    permissions = tuple(query.order_by("content_type__model", "codename"))
    actual = {
        (
            permission.content_type.app_label,
            permission.content_type.model,
            permission.codename,
        )
        for permission in permissions
    }
    if actual != _PERMISSION_SPECS:
        raise LocalPhase0Error(LocalPhase0Code.PERMISSION_MISSING)
    return permissions


def _validate_operator_shape(actor: Any) -> None:
    if (
        getattr(actor, "username", None) != LOCAL_OPERATOR_USERNAME
        or getattr(actor, "email", None) != ""
        or getattr(actor, "first_name", None) != ""
        or getattr(actor, "last_name", None) != ""
        or getattr(actor, "is_active", None) is not True
        or getattr(actor, "is_staff", None) is not False
        or getattr(actor, "is_superuser", None) is not False
        or actor.has_usable_password()
        or actor.groups.exists()
    ):
        raise LocalPhase0Error(LocalPhase0Code.OPERATOR_CONFLICT)


def _validate_exact_permissions(actor: Any, required: tuple[Permission, ...]) -> None:
    expected_ids = {permission.id for permission in required}
    actual_ids = set(actor.user_permissions.values_list("id", flat=True))
    if actual_ids != expected_ids:
        raise LocalPhase0Error(LocalPhase0Code.OPERATOR_CONFLICT)


def canonical_actor_id(actor: Any) -> int:
    actor_id = getattr(actor, "pk", None)
    if not isinstance(actor_id, int) or isinstance(actor_id, bool) or actor_id < 1:
        raise LocalPhase0Error(LocalPhase0Code.ACTOR_ID_INVALID)
    return actor_id


@transaction.atomic
def bootstrap_local_operator() -> tuple[Any, bool]:
    """Create or complete the exact non-login local actor without widening authority."""

    require_local_phase0_environment()
    required = _required_permissions(lock=True)
    required_ids = {permission.id for permission in required}
    user_model = get_user_model()
    actor = (
        user_model._default_manager.select_for_update()
        .filter(username=LOCAL_OPERATOR_USERNAME)
        .first()
    )
    created = actor is None
    if actor is None:
        actor = user_model(
            username=LOCAL_OPERATOR_USERNAME,
            email="",
            first_name="",
            last_name="",
            is_active=True,
            is_staff=False,
            is_superuser=False,
        )
        actor.set_unusable_password()
        actor.full_clean()
        actor.save()
    else:
        _validate_operator_shape(actor)
        current_ids = set(actor.user_permissions.values_list("id", flat=True))
        if not current_ids.issubset(required_ids):
            raise LocalPhase0Error(LocalPhase0Code.OPERATOR_CONFLICT)

    actor.user_permissions.set(required)
    _validate_operator_shape(actor)
    _validate_exact_permissions(actor, required)
    canonical_actor_id(actor)
    return actor, created


def get_local_operator(*, lock: bool) -> Any:
    require_local_phase0_environment()
    required = _required_permissions(lock=lock)
    user_model = get_user_model()
    query = user_model._default_manager.all()
    if lock:
        query = query.select_for_update()
    actor = query.filter(username=LOCAL_OPERATOR_USERNAME).first()
    if actor is None:
        raise LocalPhase0Error(LocalPhase0Code.OPERATOR_MISSING)
    _validate_operator_shape(actor)
    _validate_exact_permissions(actor, required)
    canonical_actor_id(actor)
    return actor
