from collections.abc import Mapping, Sequence

import pytest
from django.core.exceptions import ImproperlyConfigured

from config.settings import (
    env_positive_int,
    validate_hsts_configuration,
    validate_production_environment,
)

_SAFE_SECRET = "x" * 50
_SAFE_HOSTS = ("prices.example",)
_SAFE_ORIGINS = ("https://prices.example",)
_SAFE_DEPLOY_VERSION = "a" * 40


def validate(
    environment: Mapping[str, str],
    *,
    debug: bool = False,
    admin_enabled: bool = False,
    secret_key: str = _SAFE_SECRET,
    allowed_hosts: Sequence[str] = _SAFE_HOSTS,
    csrf_trusted_origins: Sequence[str] = _SAFE_ORIGINS,
    deploy_version: str = _SAFE_DEPLOY_VERSION,
) -> None:
    validate_production_environment(
        environment,
        debug=debug,
        admin_enabled=admin_enabled,
        secret_key=secret_key,
        allowed_hosts=allowed_hosts,
        csrf_trusted_origins=csrf_trusted_origins,
        deploy_version=deploy_version,
    )


def production_environment() -> dict[str, str]:
    return {
        "DJANGO_SECRET_KEY": "present",
        "DJANGO_ALLOWED_HOSTS": "present",
        "DJANGO_CSRF_TRUSTED_ORIGINS": "present",
        "DEPLOY_VERSION": "present",
    }


def test_debug_environment_keeps_local_development_defaults() -> None:
    validate(
        {},
        debug=True,
        admin_enabled=True,
        secret_key="local",
        allowed_hosts=(),
        csrf_trusted_origins=(),
        deploy_version="0000000",
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
            production_environment(),
            False,
            _SAFE_SECRET,
            (".prices.example",),
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


def test_production_requires_exact_full_lowercase_release_sha() -> None:
    missing = production_environment()
    missing.pop("DEPLOY_VERSION")
    with pytest.raises(
        ImproperlyConfigured,
        match="^production_deploy_version_required$",
    ):
        validate(missing)

    with pytest.raises(
        ImproperlyConfigured,
        match="^production_deploy_version_required$",
    ):
        validate(production_environment(), deploy_version="G" * 40)


def test_positive_integer_environment_bound_is_explicit() -> None:
    assert env_positive_int("MISSING_TEST_VALUE", 36, maximum=168) == 36

    with pytest.raises(ImproperlyConfigured, match="^bounded_test_value_invalid$"):
        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setenv("BOUNDED_TEST_VALUE", "0")
            env_positive_int("BOUNDED_TEST_VALUE", 36, maximum=168)


def test_hsts_preload_requires_explicit_subdomain_scope_and_one_year() -> None:
    validate_hsts_configuration(
        seconds=31_536_000,
        include_subdomains=True,
        preload=True,
    )
    validate_hsts_configuration(
        seconds=31_536_000,
        include_subdomains=False,
        preload=False,
    )

    with pytest.raises(
        ImproperlyConfigured,
        match="^production_hsts_preload_invalid$",
    ):
        validate_hsts_configuration(
            seconds=31_536_000,
            include_subdomains=False,
            preload=True,
        )
    with pytest.raises(
        ImproperlyConfigured,
        match="^production_hsts_preload_invalid$",
    ):
        validate_hsts_configuration(
            seconds=31_535_999,
            include_subdomains=True,
            preload=True,
        )
