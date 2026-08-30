from collections.abc import Mapping, Sequence

import pytest
from django.core.exceptions import ImproperlyConfigured

from config.settings import validate_production_environment

_SAFE_SECRET = "x" * 50
_SAFE_HOSTS = ("prices.example",)
_SAFE_ORIGINS = ("https://prices.example",)


def validate(
    environment: Mapping[str, str],
    *,
    debug: bool = False,
    admin_enabled: bool = False,
    secret_key: str = _SAFE_SECRET,
    allowed_hosts: Sequence[str] = _SAFE_HOSTS,
    csrf_trusted_origins: Sequence[str] = _SAFE_ORIGINS,
) -> None:
    validate_production_environment(
        environment,
        debug=debug,
        admin_enabled=admin_enabled,
        secret_key=secret_key,
        allowed_hosts=allowed_hosts,
        csrf_trusted_origins=csrf_trusted_origins,
    )


def production_environment() -> dict[str, str]:
    return {
        "DJANGO_SECRET_KEY": "present",
        "DJANGO_ALLOWED_HOSTS": "present",
        "DJANGO_CSRF_TRUSTED_ORIGINS": "present",
    }


def test_debug_environment_keeps_local_development_defaults() -> None:
    validate(
        {},
        debug=True,
        admin_enabled=True,
        secret_key="local",
        allowed_hosts=(),
        csrf_trusted_origins=(),
    )


def test_complete_production_environment_is_accepted_with_admin_disabled() -> None:
    validate(production_environment())


@pytest.mark.parametrize(
    (
        "environment",
        "admin_enabled",
        "secret_key",
        "allowed_hosts",
        "csrf_trusted_origins",
        "code",
    ),
    [
        ({}, False, _SAFE_SECRET, _SAFE_HOSTS, _SAFE_ORIGINS, "production_secret_key_required"),
        (
            {"DJANGO_SECRET_KEY": "present"},
            False,
            "short",
            _SAFE_HOSTS,
            _SAFE_ORIGINS,
            "production_secret_key_required",
        ),
        (
            {
                "DJANGO_SECRET_KEY": "present",
                "DJANGO_CSRF_TRUSTED_ORIGINS": "present",
            },
            False,
            _SAFE_SECRET,
            _SAFE_HOSTS,
            _SAFE_ORIGINS,
            "production_allowed_hosts_required",
        ),
        (
            production_environment(),
            False,
            _SAFE_SECRET,
            ("*",),
            _SAFE_ORIGINS,
            "production_allowed_hosts_required",
        ),
        (
            {
                "DJANGO_SECRET_KEY": "present",
                "DJANGO_ALLOWED_HOSTS": "present",
            },
            False,
            _SAFE_SECRET,
            _SAFE_HOSTS,
            _SAFE_ORIGINS,
            "production_csrf_origins_required",
        ),
        (
            production_environment(),
            False,
            _SAFE_SECRET,
            _SAFE_HOSTS,
            ("http://prices.example",),
            "production_csrf_origin_invalid",
        ),
        (
            production_environment(),
            False,
            _SAFE_SECRET,
            _SAFE_HOSTS,
            ("https://prices.example/unexpected",),
            "production_csrf_origin_invalid",
        ),
        (
            production_environment(),
            True,
            _SAFE_SECRET,
            _SAFE_HOSTS,
            _SAFE_ORIGINS,
            "production_admin_strong_auth_not_configured",
        ),
    ],
)
def test_incomplete_or_unsafe_production_environment_fails_closed(
    environment: Mapping[str, str],
    admin_enabled: bool,
    secret_key: str,
    allowed_hosts: Sequence[str],
    csrf_trusted_origins: Sequence[str],
    code: str,
) -> None:
    with pytest.raises(ImproperlyConfigured, match=f"^{code}$"):
        validate(
            environment,
            admin_enabled=admin_enabled,
            secret_key=secret_key,
            allowed_hosts=allowed_hosts,
            csrf_trusted_origins=csrf_trusted_origins,
        )


def test_validation_error_never_reflects_supplied_values() -> None:
    marker = "must-not-be-reflected"
    with pytest.raises(ImproperlyConfigured) as caught:
        validate(
            production_environment(),
            csrf_trusted_origins=(f"https://prices.example/{marker}",),
        )

    assert marker not in str(caught.value)
