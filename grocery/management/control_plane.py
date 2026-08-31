"""Fail-closed identities and release lock for private production operations.

This module is not an authentication boundary.  The enable flag and expected-release
check only prevent accidental execution by the wrong release.  A production platform
must put these commands behind external MFA/IAM and provision separate role-specific
database credentials.  The complete database grant matrix remains a production-platform
checkpoint; application-level permission checks here are defense in depth.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, Literal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import transaction

from grocery.management.local_phase0 import (
    canonical_actor_id,
    get_local_operator,
    require_local_phase0_environment,
)

CONTROL_REVIEWER_USERNAME: Final = "grocery-control-reviewer"
CONTROL_PUBLISHER_USERNAME: Final = "grocery-control-publisher"
CONTROL_APPROVAL_REASON_CODE: Final = "CONTROL_PLANE_SOURCE_GATE_APPROVED"
CONTROL_TRANSITION_REASON_CODES: Final[dict[str, str]] = {
    "ACTIVATE": "CONTROL_PLANE_PUBLICATION_ACTIVATED",
    "ROLLBACK": "CONTROL_PLANE_PUBLICATION_ROLLED_BACK",
    "WITHDRAW": "CONTROL_PLANE_PUBLICATION_WITHDRAWN",
}

ControlRole = Literal["reviewer", "publisher"]

_RELEASE_SHA: Final = re.compile(r"[0-9a-f]{40}\Z")
PermissionSpec = tuple[str, str, str]

_ROLE_CONTRACTS: Final[dict[ControlRole, tuple[str, tuple[PermissionSpec, ...]]]] = {
    "reviewer": (
        CONTROL_REVIEWER_USERNAME,
        (
            ("grocery", "reviewdecision", "review_generation"),
            (
                "grocery",
                "historicalcollectionreviewdecision",
                "review_historical_collection",
            ),
        ),
    ),
    "publisher": (
        CONTROL_PUBLISHER_USERNAME,
        (
            ("grocery", "publicationactivation", "publish_publication"),
            (
                "grocery",
                "historicalretailpublicationchannel",
                "publish_historical_publication",
            ),
        ),
    ),
}


class ControlPlaneCode(StrEnum):
    ENVIRONMENT_DENIED = "CONTROL_PLANE_ENVIRONMENT_DENIED"
    RELEASE_SHA_INVALID = "CONTROL_PLANE_RELEASE_SHA_INVALID"
    RELEASE_SHA_MISMATCH = "CONTROL_PLANE_RELEASE_SHA_MISMATCH"
    PERMISSION_MISSING = "CONTROL_PLANE_PERMISSION_MISSING"
    ACTOR_MISSING = "CONTROL_PLANE_ACTOR_MISSING"
    ACTOR_CONFLICT = "CONTROL_PLANE_ACTOR_CONFLICT"
    ACTOR_ID_INVALID = "CONTROL_PLANE_ACTOR_ID_INVALID"
    REVIEW_FAILED = "CONTROL_PLANE_REVIEW_FAILED"
    PUBLICATION_FAILED = "CONTROL_PLANE_PUBLICATION_FAILED"
    TRANSITION_FAILED = "CONTROL_PLANE_TRANSITION_FAILED"
    PERSISTENCE_FAILED = "CONTROL_PLANE_PERSISTENCE_FAILED"


class ControlPlaneError(RuntimeError):
    """A production command failure containing only one fixed operational code."""

    def __init__(self, code: ControlPlaneCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True, slots=True)
class OperationActor:
    actor: Any
    actor_id: int
    production: bool


@dataclass(frozen=True, slots=True)
class BootstrappedActors:
    reviewer: Any
    reviewer_created: bool
    publisher: Any
    publisher_created: bool


def require_production_operation_environment(expected_release_sha: object) -> None:
    """Require the explicit private-job flag and an exact running release lock."""

    if (
        settings.DEBUG is not False
        or getattr(settings, "ADMIN_ENABLED", None) is not False
        or getattr(settings, "QA_STATE_PREVIEWS_ENABLED", None) is not False
        or getattr(settings, "CONTROL_PLANE_OPERATIONS_ENABLED", None) is not True
    ):
        raise ControlPlaneError(ControlPlaneCode.ENVIRONMENT_DENIED)

    deploy_version = getattr(settings, "DEPLOY_VERSION", None)
    if (
        not isinstance(deploy_version, str)
        or _RELEASE_SHA.fullmatch(deploy_version) is None
        or not isinstance(expected_release_sha, str)
        or _RELEASE_SHA.fullmatch(expected_release_sha) is None
    ):
        raise ControlPlaneError(ControlPlaneCode.RELEASE_SHA_INVALID)
    if expected_release_sha != deploy_version:
        raise ControlPlaneError(ControlPlaneCode.RELEASE_SHA_MISMATCH)


def preflight_operation(expected_release_sha: object) -> bool:
    """Validate the active local or production command boundary without touching data."""

    production_requested = (
        getattr(settings, "CONTROL_PLANE_OPERATIONS_ENABLED", False) is True
        or expected_release_sha is not None
    )
    if production_requested:
        require_production_operation_environment(expected_release_sha)
        return True
    require_local_phase0_environment()
    return False


def _required_permissions(role: ControlRole, *, lock: bool) -> tuple[Permission, ...]:
    _username, specs = _ROLE_CONTRACTS[role]
    query = Permission.objects.select_related("content_type").filter(
        content_type__app_label="grocery",
        content_type__model__in=tuple(spec[1] for spec in specs),
        codename__in=tuple(spec[2] for spec in specs),
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
    if actual != set(specs):
        raise ControlPlaneError(ControlPlaneCode.PERMISSION_MISSING)
    return permissions


def _validate_actor_shape(
    actor: Any,
    *,
    role: ControlRole,
    permissions: tuple[Permission, ...],
) -> None:
    _validate_actor_identity(actor, role=role)
    if set(actor.user_permissions.values_list("id", flat=True)) != {
        permission.id for permission in permissions
    }:
        raise ControlPlaneError(ControlPlaneCode.ACTOR_CONFLICT)


def _validate_actor_identity(actor: Any, *, role: ControlRole) -> None:
    username, _permission_spec = _ROLE_CONTRACTS[role]
    if (
        getattr(actor, "username", None) != username
        or getattr(actor, "email", None) != ""
        or getattr(actor, "first_name", None) != ""
        or getattr(actor, "last_name", None) != ""
        or getattr(actor, "is_active", None) is not True
        or getattr(actor, "is_staff", None) is not False
        or getattr(actor, "is_superuser", None) is not False
        or actor.has_usable_password()
        or actor.groups.exists()
    ):
        raise ControlPlaneError(ControlPlaneCode.ACTOR_CONFLICT)


def _control_actor_id(actor: Any) -> int:
    actor_id = getattr(actor, "pk", None)
    if not isinstance(actor_id, int) or isinstance(actor_id, bool) or actor_id < 1:
        raise ControlPlaneError(ControlPlaneCode.ACTOR_ID_INVALID)
    return actor_id


def _get_control_actor(*, role: ControlRole, lock: bool) -> Any:
    permissions = _required_permissions(role, lock=lock)
    username, _permission_spec = _ROLE_CONTRACTS[role]
    user_model = get_user_model()
    query = user_model._default_manager.all()
    if lock:
        query = query.select_for_update()
    actor = query.filter(username=username).first()
    if actor is None:
        raise ControlPlaneError(ControlPlaneCode.ACTOR_MISSING)
    _validate_actor_shape(actor, role=role, permissions=permissions)
    _control_actor_id(actor)
    return actor


def resolve_operation_actor(
    *,
    role: ControlRole,
    expected_release_sha: object,
    lock: bool,
) -> OperationActor:
    """Select only the fixed actor for this environment and operation role."""

    production = preflight_operation(expected_release_sha)
    if production:
        actor = _get_control_actor(role=role, lock=lock)
        actor_id = _control_actor_id(actor)
    else:
        actor = get_local_operator(lock=lock)
        actor_id = canonical_actor_id(actor)
    return OperationActor(actor=actor, actor_id=actor_id, production=production)


def _create_actor(*, role: ControlRole, permissions: tuple[Permission, ...]) -> Any:
    username, _permission_spec = _ROLE_CONTRACTS[role]
    user_model = get_user_model()
    actor = user_model(
        username=username,
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
    actor.user_permissions.set(permissions)
    _validate_actor_shape(actor, role=role, permissions=permissions)
    _control_actor_id(actor)
    return actor


@transaction.atomic
def bootstrap_control_plane_actors(expected_release_sha: object) -> BootstrappedActors:
    """Create both fixed actors once; any existing semantic drift fails atomically."""

    require_production_operation_environment(expected_release_sha)
    permissions = {role: _required_permissions(role, lock=True) for role in _ROLE_CONTRACTS}
    user_model = get_user_model()
    existing = {
        actor.username: actor
        for actor in user_model._default_manager.select_for_update().filter(
            username__in=tuple(contract[0] for contract in _ROLE_CONTRACTS.values())
        )
    }

    actors: dict[ControlRole, Any] = {}
    created: dict[ControlRole, bool] = {}
    for role, (username, _permission_spec) in _ROLE_CONTRACTS.items():
        actor = existing.get(username)
        was_created = actor is None
        if actor is None:
            actor = _create_actor(role=role, permissions=permissions[role])
        else:
            _validate_actor_identity(actor, role=role)
            expected_ids = {permission.id for permission in permissions[role]}
            current_ids = set(actor.user_permissions.values_list("id", flat=True))
            if not current_ids.issubset(expected_ids):
                raise ControlPlaneError(ControlPlaneCode.ACTOR_CONFLICT)
            actor.user_permissions.set(permissions[role])
            _validate_actor_shape(actor, role=role, permissions=permissions[role])
            _control_actor_id(actor)
        actors[role] = actor
        created[role] = was_created

    return BootstrappedActors(
        reviewer=actors["reviewer"],
        reviewer_created=created["reviewer"],
        publisher=actors["publisher"],
        publisher_created=created["publisher"],
    )
