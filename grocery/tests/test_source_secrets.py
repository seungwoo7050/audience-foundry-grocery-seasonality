"""Synthetic tests for local credential loading; the real .env.local is never read."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from grocery.source.secrets import SecretLoadError, load_kamis_api_key

SYNTHETIC_SECRET = "synthetic+credential/segment="


def _secret_file(tmp_path: Path, contents: str, *, mode: int = 0o600) -> Path:
    path = tmp_path / "synthetic.env.local"
    path.write_text(contents, encoding="utf-8")
    path.chmod(mode)
    return path


def _assert_error_is_redacted(error: SecretLoadError) -> None:
    rendered = f"{error!s} {error!r}"
    assert SYNTHETIC_SECRET not in rendered
    assert "synthetic.env.local" not in rendered


def test_process_environment_wins_without_reading_the_file(tmp_path: Path) -> None:
    insecure_path = _secret_file(tmp_path, "not a valid assignment", mode=0o644)

    secret = load_kamis_api_key(
        environment={"KAMIS_API_KEY": SYNTHETIC_SECRET},
        path=insecure_path,
    )

    assert str(secret) == "<redacted>"
    assert repr(secret) == "<redacted>"
    assert f"{secret}" == "<redacted>"
    assert secret.reveal() == SYNTHETIC_SECRET


def test_owner_only_file_accepts_one_exact_assignment(tmp_path: Path) -> None:
    path = _secret_file(
        tmp_path,
        f"# local-only credential\n\nKAMIS_API_KEY={SYNTHETIC_SECRET}\n",
    )

    secret = load_kamis_api_key(environment={}, path=path)

    assert repr(secret) == "<redacted>"
    assert secret.reveal() == SYNTHETIC_SECRET


@pytest.mark.parametrize(
    ("contents", "code"),
    [
        ("", "secret_key_missing"),
        ("KAMIS_API_KEY=", "secret_key_empty"),
        ("export KAMIS_API_KEY=anything", "secret_file_malformed"),
        ("OTHER_KEY=anything", "secret_file_malformed"),
        (" KAMIS_API_KEY=anything", "secret_file_malformed"),
        ("KAMIS_API_KEY=$OTHER_KEY", "secret_key_unsafe_syntax"),
        ("KAMIS_API_KEY=`command`", "secret_key_unsafe_syntax"),
        ('KAMIS_API_KEY="quoted"', "secret_key_unsafe_syntax"),
        ("KAMIS_API_KEY='quoted'", "secret_key_unsafe_syntax"),
        ("KAMIS_API_KEY=escaped\\value", "secret_key_unsafe_syntax"),
        ("KAMIS_API_KEY= leading", "secret_key_unsafe_syntax"),
        ("KAMIS_API_KEY=trailing ", "secret_key_unsafe_syntax"),
        ("KAMIS_API_KEY=embedded\tspace", "secret_key_unsafe_syntax"),
        ("KAMIS_API_KEY=first\nKAMIS_API_KEY=second", "secret_key_duplicate"),
        ("KAMIS_API_KEY=first\ncontinued-secret", "secret_file_malformed"),
        ("KAMIS_API_KEY=first\x00second", "secret_file_nul"),
    ],
)
def test_file_syntax_fails_with_code_only(tmp_path: Path, contents: str, code: str) -> None:
    path = _secret_file(tmp_path, contents)

    with pytest.raises(SecretLoadError) as raised:
        load_kamis_api_key(environment={}, path=path)

    assert raised.value.code == code
    assert str(raised.value) == code
    _assert_error_is_redacted(raised.value)


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("", "environment_value_empty"),
        (" ", "environment_value_invalid"),
        ("$OTHER_KEY", "environment_value_invalid"),
        ("line-one\nline-two", "environment_value_invalid"),
    ],
)
def test_invalid_environment_value_fails_with_code_only(
    tmp_path: Path, value: str, code: str
) -> None:
    with pytest.raises(SecretLoadError) as raised:
        load_kamis_api_key(
            environment={"KAMIS_API_KEY": value},
            path=tmp_path / "must-not-be-read",
        )

    assert raised.value.code == code
    assert str(raised.value) == code
    _assert_error_is_redacted(raised.value)


def test_group_or_other_permissions_are_rejected(tmp_path: Path) -> None:
    path = _secret_file(tmp_path, f"KAMIS_API_KEY={SYNTHETIC_SECRET}\n", mode=0o640)

    with pytest.raises(SecretLoadError) as raised:
        load_kamis_api_key(environment={}, path=path)

    assert raised.value.code == "secret_file_permissions"
    _assert_error_is_redacted(raised.value)


def test_symlink_is_rejected_without_following_it(tmp_path: Path) -> None:
    target = _secret_file(tmp_path, f"KAMIS_API_KEY={SYNTHETIC_SECRET}\n")
    link = tmp_path / "secret-link"
    link.symlink_to(target)

    with pytest.raises(SecretLoadError) as raised:
        load_kamis_api_key(environment={}, path=link)

    assert raised.value.code == "secret_file_symlink"
    _assert_error_is_redacted(raised.value)


def test_non_regular_path_is_rejected(tmp_path: Path) -> None:
    directory = tmp_path / "not-a-file"
    directory.mkdir(mode=0o700)

    with pytest.raises(SecretLoadError) as raised:
        load_kamis_api_key(environment={}, path=directory)

    assert raised.value.code == "secret_file_not_regular"


def test_invalid_utf8_is_rejected_without_leaking_bytes(tmp_path: Path) -> None:
    path = tmp_path / "synthetic.env.local"
    path.write_bytes(b"KAMIS_API_KEY=synthetic\xffcredential")
    path.chmod(0o600)

    with pytest.raises(SecretLoadError) as raised:
        load_kamis_api_key(environment={}, path=path)

    assert raised.value.code == "secret_file_invalid_encoding"
    assert "synthetic" not in repr(raised.value)


def test_missing_file_error_contains_only_a_code(tmp_path: Path) -> None:
    with pytest.raises(SecretLoadError) as raised:
        load_kamis_api_key(environment={}, path=tmp_path / "synthetic.env.local")

    assert str(raised.value) == "secret_file_missing"
    _assert_error_is_redacted(raised.value)


def test_owner_execute_bit_does_not_grant_group_or_other_access(tmp_path: Path) -> None:
    path = _secret_file(tmp_path, f"KAMIS_API_KEY={SYNTHETIC_SECRET}\n", mode=0o700)

    secret = load_kamis_api_key(environment={}, path=path)

    assert secret.reveal() == SYNTHETIC_SECRET
    assert os.stat(path).st_mode & 0o077 == 0
