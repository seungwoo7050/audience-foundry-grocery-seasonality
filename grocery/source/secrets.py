"""Fail-closed loading for the local KAMIS development credential.

The loader does not source a shell or retain file contents.  Callers must make an
explicit ``reveal()`` at the narrow HTTP invocation boundary.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Final

KAMIS_API_KEY: Final = "KAMIS_API_KEY"
DEFAULT_LOCAL_SECRET_PATH: Final = Path(".env.local")
_MAX_SECRET_FILE_BYTES: Final = 16 * 1024
_REDACTED: Final = "<redacted>"


class SecretLoadError(RuntimeError):
    """A credential-loading failure whose message is a non-sensitive code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class SecretValue:
    """A deliberately redacted wrapper around credential material."""

    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        self.__value = value

    def __repr__(self) -> str:
        return _REDACTED

    def __str__(self) -> str:
        return _REDACTED

    def __format__(self, format_spec: str) -> str:
        del format_spec
        return _REDACTED

    def reveal(self) -> str:
        """Reveal the value only for immediate use by the KAMIS HTTP client."""

        return self.__value


def load_kamis_api_key(
    *,
    environment: Mapping[str, str] | None = None,
    path: Path = DEFAULT_LOCAL_SECRET_PATH,
) -> SecretValue:
    """Load KAMIS_API_KEY from the process environment or a strict local file."""

    selected_environment = os.environ if environment is None else environment
    if KAMIS_API_KEY in selected_environment:
        return SecretValue(
            _validate_value(selected_environment[KAMIS_API_KEY], source="environment")
        )

    contents = _read_secret_file(path)
    return SecretValue(_parse_secret_file(contents))


def _read_secret_file(path: Path) -> str:
    try:
        before_open = path.lstat()
    except FileNotFoundError:
        raise SecretLoadError("secret_file_missing") from None
    except OSError:
        raise SecretLoadError("secret_file_unreadable") from None

    if stat.S_ISLNK(before_open.st_mode):
        raise SecretLoadError("secret_file_symlink")
    _validate_file_metadata(before_open)

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise SecretLoadError("secret_file_unreadable") from None

    try:
        after_open = os.fstat(descriptor)
        _validate_file_metadata(after_open)
        if (before_open.st_dev, before_open.st_ino) != (after_open.st_dev, after_open.st_ino):
            raise SecretLoadError("secret_file_changed")
        raw = os.read(descriptor, _MAX_SECRET_FILE_BYTES + 1)
    except SecretLoadError:
        raise
    except OSError:
        raise SecretLoadError("secret_file_unreadable") from None
    finally:
        os.close(descriptor)

    if len(raw) > _MAX_SECRET_FILE_BYTES:
        raise SecretLoadError("secret_file_too_large")
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise SecretLoadError("secret_file_invalid_encoding") from None


def _validate_file_metadata(metadata: os.stat_result) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise SecretLoadError("secret_file_not_regular")
    if metadata.st_uid != os.geteuid():
        raise SecretLoadError("secret_file_wrong_owner")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise SecretLoadError("secret_file_permissions")


def _parse_secret_file(contents: str) -> str:
    if "\x00" in contents:
        raise SecretLoadError("secret_file_nul")

    found: str | None = None
    prefix = f"{KAMIS_API_KEY}="
    for line in contents.splitlines():
        if not line or line.startswith("#"):
            continue
        if not line.startswith(prefix):
            raise SecretLoadError("secret_file_malformed")
        if found is not None:
            raise SecretLoadError("secret_key_duplicate")
        found = _validate_value(line.removeprefix(prefix), source="file")

    if found is None:
        raise SecretLoadError("secret_key_missing")
    return found


def _validate_value(value: str, *, source: str) -> str:
    if not value:
        code = "environment_value_empty" if source == "environment" else "secret_key_empty"
        raise SecretLoadError(code)
    if "\x00" in value or any(character.isspace() for character in value):
        code = (
            "environment_value_invalid" if source == "environment" else "secret_key_unsafe_syntax"
        )
        raise SecretLoadError(code)
    if any(character in value for character in ("$", "`", "'", '"', "\\")):
        code = (
            "environment_value_invalid" if source == "environment" else "secret_key_unsafe_syntax"
        )
        raise SecretLoadError(code)
    return value
