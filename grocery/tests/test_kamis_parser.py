"""Synthetic contract tests; these fixtures are not live source evidence."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

import pytest

from grocery.source.kamis import (
    KAMIS_RETAIL_COVERAGE_IDENTITY,
    ExactIdentityRegistry,
    IdentityContractEvidence,
    KamisParseError,
    ReferenceDateStatus,
    ReferencePeriod,
    ValueStatus,
    parse_recent_price_rows,
)

SYNTHETIC_EVIDENCE = IdentityContractEvidence(
    codebook_sha256="1" * 64,
    unit_contract_sha256="2" * 64,
    coverage_evidence_revision="synthetic-test-only-v1",
)

SYNTHETIC_ROW: dict[str, object] = {
    "ctgry_cd": "200",
    "ctgry_nm": "채소류",
    "dd1_bfr_cnvs_prc": "2300",
    "dd1_bfr_prc": "2300",
    "exmn_dd_cnvs_prc": "2400",
    "exmn_dd_prc": "2400",
    "exmn_ymd": "20260829",
    "grd_cd": "04",
    "grd_nm": "상품",
    "item_cd": "212",
    "item_nm": "합성채소",
    "mm1_bfr_cnvs_prc": "2200",
    "mm1_bfr_prc": None,
    "se_cd": "01",
    "se_nm": "소매",
    "unit": "포기",
    "unit_sz": "1",
    "vrty_cd": "00",
    "vrty_nm": "합성품종",
    "ww1_bfr_cnvs_prc": "2100",
    "ww1_bfr_prc": "2100",
    "yy1_bfr_cnvs_prc": "2000",
    "yy1_bfr_prc": "2000",
}


@pytest.fixture
def identity_registry() -> ExactIdentityRegistry:
    return ExactIdentityRegistry(
        item_names={("200", "212"): "합성채소"},
        variety_names={("200", "212", "00"): "합성품종"},
        grade_names={("200", "212", "00", "04"): "상품"},
        units={("200", "212", "00", "04"): ("포기", "1")},
        evidence=SYNTHETIC_EVIDENCE,
    )


def test_parses_typed_current_and_reference_values(
    identity_registry: ExactIdentityRegistry,
) -> None:
    result = parse_recent_price_rows([SYNTHETIC_ROW], identity_registry=identity_registry)

    assert len(result.result_hash) == 64
    row = result.rows[0]
    assert row.semantic_identity_key == (
        "01",
        "200",
        "212",
        "00",
        "04",
        "포기",
        "1",
        KAMIS_RETAIL_COVERAGE_IDENTITY,
    )
    assert row.source_effective_date.isoformat() == "2026-08-29"
    assert row.current_price == Decimal("2400")
    assert [reference.period for reference in row.references] == [
        ReferencePeriod.WEEK,
        ReferencePeriod.MONTH,
        ReferencePeriod.YEAR,
    ]
    assert row.references[0].value == Decimal("2100")
    assert row.references[1].value_status is ValueStatus.UNAVAILABLE
    assert row.references[1].value is None
    assert row.references[1].unavailable_reason == "SOURCE_VALUE_MISSING"
    assert all(
        reference.reference_date_status is ReferenceDateStatus.SOURCE_REFERENCE_DATE_UNAVAILABLE
        for reference in row.references
    )
    assert all(reference.source_reference_date is None for reference in row.references)


def test_hashes_are_deterministic_and_result_is_identity_sorted(
    identity_registry: ExactIdentityRegistry,
) -> None:
    second_row = deepcopy(SYNTHETIC_ROW)
    second_row.update(
        {
            "item_cd": "213",
            "item_nm": "합성시금치",
            "vrty_nm": "합성시금치품종",
            "unit": "g",
            "unit_sz": "100",
        }
    )
    registry = ExactIdentityRegistry(
        item_names={**identity_registry.item_names, ("200", "213"): "합성시금치"},
        variety_names={
            **identity_registry.variety_names,
            ("200", "213", "00"): "합성시금치품종",
        },
        grade_names={
            **identity_registry.grade_names,
            ("200", "213", "00", "04"): "상품",
        },
        units={
            **identity_registry.units,
            ("200", "213", "00", "04"): ("g", "100"),
        },
        evidence=SYNTHETIC_EVIDENCE,
    )

    forward = parse_recent_price_rows([second_row, SYNTHETIC_ROW], identity_registry=registry)
    reverse = parse_recent_price_rows([SYNTHETIC_ROW, second_row], identity_registry=registry)

    assert forward == reverse
    assert [row.item_code for row in forward.rows] == ["212", "213"]


@pytest.mark.parametrize("field", sorted(SYNTHETIC_ROW))
def test_missing_field_fails_closed(
    identity_registry: ExactIdentityRegistry,
    field: str,
) -> None:
    row = deepcopy(SYNTHETIC_ROW)
    del row[field]

    with pytest.raises(KamisParseError, match="missing_field") as raised:
        parse_recent_price_rows([row], identity_registry=identity_registry)

    assert raised.value.field == field


def test_extra_field_fails_without_echoing_it(identity_registry: ExactIdentityRegistry) -> None:
    row = deepcopy(SYNTHETIC_ROW)
    row["provider_new_sensitive_value"] = "must-not-appear"

    with pytest.raises(KamisParseError, match="unknown_field") as raised:
        parse_recent_price_rows([row], identity_registry=identity_registry)

    assert "provider_new_sensitive_value" not in str(raised.value)
    assert "must-not-appear" not in str(raised.value)


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("se_nm", "소매변경", "product_class_name_drift"),
        ("ctgry_nm", "채소", "category_name_drift"),
        ("item_nm", "변경된이름", "item_code_name_drift"),
        ("vrty_nm", "변경된품종", "variety_code_name_drift"),
        ("grd_nm", "변경된등급", "grade_code_name_drift"),
        ("unit", "kg", "unit_identity_drift"),
        ("unit_sz", "2", "unit_identity_drift"),
    ],
)
def test_identity_or_unit_drift_fails_closed(
    identity_registry: ExactIdentityRegistry,
    field: str,
    value: str,
    code: str,
) -> None:
    row = deepcopy(SYNTHETIC_ROW)
    row[field] = value

    with pytest.raises(KamisParseError, match=code):
        parse_recent_price_rows([row], identity_registry=identity_registry)


def test_coverage_revision_drift_fails_closed() -> None:
    registry = ExactIdentityRegistry(
        item_names={("200", "212"): "합성채소"},
        variety_names={("200", "212", "00"): "합성품종"},
        grade_names={("200", "212", "00", "04"): "상품"},
        units={("200", "212", "00", "04"): ("포기", "1")},
        evidence=SYNTHETIC_EVIDENCE,
        coverage_identity="UNREVIEWED_COVERAGE_REVISION",
    )

    with pytest.raises(KamisParseError, match="coverage_identity_drift"):
        parse_recent_price_rows([SYNTHETIC_ROW], identity_registry=registry)


@pytest.mark.parametrize("value", [0, "0", "-1", "1.0", " 1", "1 ", "", None])
def test_invalid_current_price_fails_closed(
    identity_registry: ExactIdentityRegistry,
    value: object,
) -> None:
    row = deepcopy(SYNTHETIC_ROW)
    row["exmn_dd_prc"] = value

    with pytest.raises(KamisParseError, match="invalid_positive_scale_zero_price"):
        parse_recent_price_rows([row], identity_registry=identity_registry)


@pytest.mark.parametrize("value", [0, "0", "-", "1.5", "", [], {}])
def test_only_json_null_marks_a_reference_unavailable(
    identity_registry: ExactIdentityRegistry,
    value: object,
) -> None:
    row = deepcopy(SYNTHETIC_ROW)
    row["ww1_bfr_prc"] = value

    with pytest.raises(KamisParseError, match="invalid_positive_scale_zero_price"):
        parse_recent_price_rows([row], identity_registry=identity_registry)


@pytest.mark.parametrize("value", ["20260230", "2026-08-29", 20260829, None])
def test_invalid_effective_date_fails_closed(
    identity_registry: ExactIdentityRegistry,
    value: object,
) -> None:
    row = deepcopy(SYNTHETIC_ROW)
    row["exmn_ymd"] = value

    with pytest.raises(KamisParseError, match="invalid_effective_date"):
        parse_recent_price_rows([row], identity_registry=identity_registry)


def test_duplicate_semantic_identity_fails_closed(
    identity_registry: ExactIdentityRegistry,
) -> None:
    later = deepcopy(SYNTHETIC_ROW)
    later["exmn_ymd"] = "20260830"

    with pytest.raises(KamisParseError, match="duplicate_semantic_identity"):
        parse_recent_price_rows([SYNTHETIC_ROW, later], identity_registry=identity_registry)


@pytest.mark.parametrize("items", [None, {}, "row", b"row", 1])
def test_items_must_be_an_array(identity_registry: ExactIdentityRegistry, items: object) -> None:
    with pytest.raises(KamisParseError, match="items_not_array"):
        parse_recent_price_rows(items, identity_registry=identity_registry)


def test_empty_items_have_a_stable_result_hash(identity_registry: ExactIdentityRegistry) -> None:
    first = parse_recent_price_rows([], identity_registry=identity_registry)
    second = parse_recent_price_rows([], identity_registry=identity_registry)

    assert first == second
    assert first.rows == ()
    assert first.input_row_count == 0
    assert first.accepted_row_count == 0
    assert first.out_of_scope_row_count == 0


def test_out_of_scope_rows_are_reconciled_without_becoming_facts(
    identity_registry: ExactIdentityRegistry,
) -> None:
    wholesale_row = deepcopy(SYNTHETIC_ROW)
    wholesale_row["se_cd"] = "02"
    wholesale_row["se_nm"] = "합성중도매"
    other_category_row = deepcopy(SYNTHETIC_ROW)
    other_category_row["ctgry_cd"] = "100"
    other_category_row["ctgry_nm"] = "합성식량작물"

    result = parse_recent_price_rows(
        [other_category_row, SYNTHETIC_ROW, wholesale_row],
        identity_registry=identity_registry,
    )

    assert result.input_row_count == 3
    assert result.accepted_row_count == 1
    assert result.out_of_scope_row_count == 2
    assert len(result.out_of_scope_row_hashes) == 2
    assert result.input_row_count == result.accepted_row_count + result.out_of_scope_row_count


def test_out_of_scope_order_does_not_change_result_hash(
    identity_registry: ExactIdentityRegistry,
) -> None:
    first = deepcopy(SYNTHETIC_ROW)
    first["se_cd"] = "02"
    second = deepcopy(SYNTHETIC_ROW)
    second["ctgry_cd"] = "600"

    forward = parse_recent_price_rows([first, second], identity_registry=identity_registry)
    reverse = parse_recent_price_rows([second, first], identity_registry=identity_registry)

    assert forward == reverse


def test_out_of_scope_schema_type_drift_still_fails_closed(
    identity_registry: ExactIdentityRegistry,
) -> None:
    row = deepcopy(SYNTHETIC_ROW)
    row["se_cd"] = "02"
    row["exmn_ymd"] = 20260829

    with pytest.raises(KamisParseError, match="field_type_drift"):
        parse_recent_price_rows([row], identity_registry=identity_registry)


def test_out_of_scope_price_sentinel_still_fails_closed(
    identity_registry: ExactIdentityRegistry,
) -> None:
    row = deepcopy(SYNTHETIC_ROW)
    row["se_cd"] = "02"
    row["ww1_bfr_prc"] = "-"

    with pytest.raises(KamisParseError, match="invalid_positive_scale_zero_price"):
        parse_recent_price_rows([row], identity_registry=identity_registry)
