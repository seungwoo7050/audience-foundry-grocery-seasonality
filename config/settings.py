import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlparse

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


def env_positive_int(name: str, default: int, *, maximum: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        raise ImproperlyConfigured(f"{name.lower()}_invalid") from None
    if value < 1 or value > maximum:
        raise ImproperlyConfigured(f"{name.lower()}_invalid")
    return value


def database_config() -> dict[str, object]:
    value = os.environ.get(
        "DATABASE_URL",
        "postgresql://grocery:local-grocery-only@127.0.0.1:55434/grocery",
    )
    parsed = urlparse(value)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("DATABASE_URL must use PostgreSQL")
    if not all((parsed.hostname, parsed.path.removeprefix("/"), parsed.username)):
        raise ValueError("DATABASE_URL is incomplete")
    allowed_options = {"sslmode", "target_session_attrs"}
    options = {key: val for key, val in parse_qsl(parsed.query) if key in allowed_options}
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(parsed.path.removeprefix("/")),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname,
        "PORT": parsed.port or 5432,
        "CONN_MAX_AGE": int(os.environ.get("DATABASE_CONN_MAX_AGE", "0")),
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": options,
    }


DEBUG = env_bool("DJANGO_DEBUG", True)
ADMIN_ENABLED = env_bool("ADMIN_ENABLED", DEBUG)
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "local-development-only-not-for-production")
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "")
DEPLOY_VERSION = os.environ.get("DEPLOY_VERSION", "0000000")
CONTROL_PLANE_OPERATIONS_ENABLED = env_bool("CONTROL_PLANE_OPERATIONS_ENABLED", False)


def validate_production_environment(
    environment: Mapping[str, str],
    *,
    debug: bool,
    admin_enabled: bool,
    secret_key: str,
    allowed_hosts: Sequence[str],
    csrf_trusted_origins: Sequence[str],
    deploy_version: str,
) -> None:
    """Reject incomplete production settings without reflecting any supplied value."""

    if debug:
        return
    if "DJANGO_SECRET_KEY" not in environment or len(secret_key) < 50:
        raise ImproperlyConfigured("production_secret_key_required")
    if (
        "DJANGO_ALLOWED_HOSTS" not in environment
        or not allowed_hosts
        or any(
            host == "*" or host.startswith(".") or any(character in host for character in "/?#@:")
            for host in allowed_hosts
        )
    ):
        raise ImproperlyConfigured("production_allowed_hosts_required")
    if "DJANGO_CSRF_TRUSTED_ORIGINS" not in environment or not csrf_trusted_origins:
        raise ImproperlyConfigured("production_csrf_origins_required")
    for origin in csrf_trusted_origins:
        parsed_origin = urlparse(origin)
        if (
            parsed_origin.scheme != "https"
            or not parsed_origin.hostname
            or parsed_origin.username is not None
            or parsed_origin.password is not None
            or parsed_origin.query
            or parsed_origin.fragment
            or parsed_origin.path not in ("", "/")
        ):
            raise ImproperlyConfigured("production_csrf_origin_invalid")
    if (
        "DEPLOY_VERSION" not in environment
        or len(deploy_version) != 40
        or any(character not in "0123456789abcdef" for character in deploy_version)
    ):
        raise ImproperlyConfigured("production_deploy_version_required")
    if admin_enabled:
        raise ImproperlyConfigured("production_admin_strong_auth_not_configured")


def validate_hsts_configuration(
    *,
    seconds: int,
    include_subdomains: bool,
    preload: bool,
) -> None:
    """Reject a preload opt-in that cannot meet the browser preload contract."""

    if preload and (not include_subdomains or seconds < 31_536_000):
        raise ImproperlyConfigured("production_hsts_preload_invalid")


validate_production_environment(
    os.environ,
    debug=DEBUG,
    admin_enabled=ADMIN_ENABLED,
    secret_key=SECRET_KEY,
    allowed_hosts=ALLOWED_HOSTS,
    csrf_trusted_origins=CSRF_TRUSTED_ORIGINS,
    deploy_version=DEPLOY_VERSION,
)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "grocery.apps.GroceryConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "grocery.security.SecurityHeadersMiddleware",
    "grocery.observability.RequestIdMiddleware",
    "grocery.security.AdminExposureMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]
WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {"default": database_config()}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "Asia/Seoul"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"


def staticfiles_storage_backend(*, debug: bool) -> str:
    if debug:
        return "django.contrib.staticfiles.storage.StaticFilesStorage"
    return "whitenoise.storage.CompressedManifestStaticFilesStorage"


STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": staticfiles_storage_backend(debug=DEBUG),
    },
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", not DEBUG)
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_HSTS_SECONDS = int(
    os.environ.get("DJANGO_SECURE_HSTS_SECONDS", "0" if DEBUG else "31536000")
)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool(
    "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS",
    False,
)
SECURE_HSTS_PRELOAD = env_bool("DJANGO_SECURE_HSTS_PRELOAD", False)
SECURE_PROXY_SSL_HEADER = (
    ("HTTP_X_FORWARDED_PROTO", "https")
    if env_bool("DJANGO_TRUST_X_FORWARDED_PROTO", False)
    else None
)
validate_hsts_configuration(
    seconds=SECURE_HSTS_SECONDS,
    include_subdomains=SECURE_HSTS_INCLUDE_SUBDOMAINS,
    preload=SECURE_HSTS_PRELOAD,
)
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "no-referrer"
X_FRAME_OPTIONS = "DENY"

KAMIS_CONFIRMATION_MAX_AGE_HOURS = env_positive_int(
    "KAMIS_CONFIRMATION_MAX_AGE_HOURS",
    36,
    maximum=168,
)
KAMIS_HISTORICAL_MONTHLY_MAX_AGE_HOURS = env_positive_int(
    "KAMIS_HISTORICAL_MONTHLY_MAX_AGE_HOURS",
    192,
    maximum=744,
)
KAMIS_HISTORICAL_DAILY_MAX_AGE_HOURS = env_positive_int(
    "KAMIS_HISTORICAL_DAILY_MAX_AGE_HOURS",
    36,
    maximum=168,
)
QA_STATE_PREVIEWS_ENABLED = DEBUG and env_bool("QA_STATE_PREVIEWS_ENABLED", False)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "observability_allowlist": {
            "()": "grocery.observability.ObservabilityAllowlistFilter",
        }
    },
    "formatters": {
        "structured_json": {
            "()": "grocery.observability.StructuredJsonFormatter",
        }
    },
    "handlers": {
        "null": {"class": "logging.NullHandler"},
        "structured_console": {
            "class": "logging.StreamHandler",
            "filters": ["observability_allowlist"],
            "formatter": "structured_json",
        },
    },
    "loggers": {
        "django.request": {"handlers": ["null"], "propagate": False},
        "django.server": {"handlers": ["null"], "propagate": False},
        "grocery.audit": {
            "handlers": ["structured_console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
