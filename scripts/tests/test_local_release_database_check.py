from __future__ import annotations

import pytest

from scripts.local_release_database_check import is_fixed_local_release_database, main

_FIXED_URL = "postgresql://grocery:local-grocery-only@127.0.0.1:55434/grocery"


def test_fixed_loopback_compose_database_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DATABASE_URL", _FIXED_URL)

    assert is_fixed_local_release_database(_FIXED_URL)
    assert main() == 0
    assert capsys.readouterr().out == "local_release_database=ok\n"


@pytest.mark.parametrize(
    "value",
    (
        None,
        "",
        "postgresql://grocery:private@database.example:5432/grocery",
        "postgresql://grocery:local-grocery-only@localhost:55434/grocery",
        "postgresql://grocery:local-grocery-only@127.0.0.1:5432/grocery",
        "postgresql://grocery:local-grocery-only@127.0.0.1:55434/other",
        "postgresql://grocery:local-grocery-only@127.0.0.1:55434/grocery?sslmode=require",
    ),
)
def test_every_nonfixed_database_shape_is_rejected_without_reflection(
    value: str | None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    marker = "must-not-be-reflected"
    monkeypatch.setenv("DATABASE_URL", value or marker)

    assert not is_fixed_local_release_database(value)
    assert main() == 2
    output = capsys.readouterr().out
    assert output == ("local_release_database=failed code=fixed_loopback_database_required\n")
    assert marker not in output
