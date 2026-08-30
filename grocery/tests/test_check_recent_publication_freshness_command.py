import io
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

_COMMAND = "check_recent_publication_freshness"
_LOAD = "grocery.management.commands.check_recent_publication_freshness.load_active_publication"
_SOURCE_FETCH = "grocery.source.client.KamisHttpClient.fetch_recent_prices"
_EXPECTED_KEYS = {
    "check",
    "channel",
    "publication_state",
    "freshness_state",
}


def invoke() -> tuple[io.StringIO, object]:
    output = io.StringIO()
    result = call_command(_COMMAND, stdout=output)
    return output, result


def parsed_receipt(output: io.StringIO) -> dict[str, str]:
    lines = output.getvalue().splitlines()
    assert len(lines) == 1
    value = json.loads(lines[0])
    assert isinstance(value, dict)
    assert set(value) == _EXPECTED_KEYS
    return value


def test_current_publication_emits_one_safe_receipt_and_exits_successfully() -> None:
    active = SimpleNamespace(
        freshness_state="current",
        revision_id="private-revision-id",
        actor_id="private-actor-id",
        typed_fact_set_sha256="a" * 64,
    )
    with (
        patch(_LOAD, return_value=active) as load,
        patch(_SOURCE_FETCH) as source_fetch,
    ):
        output, result = invoke()

    assert result is None
    load.assert_called_once_with()
    source_fetch.assert_not_called()
    assert parsed_receipt(output) == {
        "check": "FRESHNESS",
        "channel": "RECENT_RETAIL",
        "publication_state": "AVAILABLE",
        "freshness_state": "CURRENT",
    }
    receipt = output.getvalue()
    assert "private-revision-id" not in receipt
    assert "private-actor-id" not in receipt
    assert "a" * 64 not in receipt


@pytest.mark.parametrize(
    ("active", "expected_code", "publication_state", "freshness_state"),
    (
        (
            SimpleNamespace(freshness_state="stale"),
            "RECENT_PUBLICATION_FRESHNESS_STALE",
            "AVAILABLE",
            "STALE",
        ),
        (
            None,
            "RECENT_PUBLICATION_FRESHNESS_UNAVAILABLE",
            "UNAVAILABLE",
            "UNAVAILABLE",
        ),
    ),
)
def test_stale_or_unavailable_publication_emits_receipt_and_nonzero_fixed_code(
    active: object,
    expected_code: str,
    publication_state: str,
    freshness_state: str,
) -> None:
    output = io.StringIO()
    with (
        patch(_LOAD, return_value=active) as load,
        pytest.raises(CommandError) as caught,
    ):
        call_command(_COMMAND, stdout=output)

    load.assert_called_once_with()
    assert caught.value.returncode != 0
    assert str(caught.value) == f"code={expected_code}"
    assert parsed_receipt(output) == {
        "check": "FRESHNESS",
        "channel": "RECENT_RETAIL",
        "publication_state": publication_state,
        "freshness_state": freshness_state,
    }


@pytest.mark.parametrize(
    "failure",
    (
        RuntimeError("serviceKey=secret-value raw-row actor=42 hash=" + "b" * 64),
        ValueError("https://provider.example/?serviceKey=secret-value"),
    ),
)
def test_internal_failure_is_redacted_and_uses_only_fixed_failure_state(
    failure: Exception,
) -> None:
    output = io.StringIO()
    with patch(_LOAD, side_effect=failure), pytest.raises(CommandError) as caught:
        call_command(_COMMAND, stdout=output)

    assert caught.value.returncode != 0
    assert str(caught.value) == "code=RECENT_PUBLICATION_FRESHNESS_FAILED"
    assert parsed_receipt(output) == {
        "check": "FRESHNESS",
        "channel": "RECENT_RETAIL",
        "publication_state": "UNAVAILABLE",
        "freshness_state": "UNAVAILABLE",
    }
    combined = output.getvalue() + str(caught.value) + repr(caught.value)
    assert "secret-value" not in combined
    assert "raw-row" not in combined
    assert "actor=42" not in combined
    assert "b" * 64 not in combined
    assert "provider.example" not in combined


def test_unknown_freshness_state_is_not_reflected_and_fails_closed() -> None:
    marker = "private-unknown-state"
    output = io.StringIO()
    with (
        patch(_LOAD, return_value=SimpleNamespace(freshness_state=marker)),
        pytest.raises(CommandError) as caught,
    ):
        call_command(_COMMAND, stdout=output)

    assert str(caught.value) == "code=RECENT_PUBLICATION_FRESHNESS_FAILED"
    assert marker not in output.getvalue()
    assert marker not in str(caught.value)
    assert parsed_receipt(output)["freshness_state"] == "UNAVAILABLE"
