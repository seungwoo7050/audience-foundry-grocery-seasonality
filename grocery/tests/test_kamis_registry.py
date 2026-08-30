from grocery.source.registry import (
    INITIAL_RETAIL_IDENTITY_REGISTRY,
    OFFICIAL_DOCS_ZIP_SHA256,
)


def test_initial_registry_contains_exactly_reviewed_five_plus_five_series() -> None:
    registry = INITIAL_RETAIL_IDENTITY_REGISTRY

    vegetable_count = sum(key[0] == "200" for key in registry.units)
    fruit_count = sum(key[0] == "400" for key in registry.units)

    assert vegetable_count == 5
    assert fruit_count == 5
    assert len(registry.units) == 10
    assert registry.evidence.codebook_sha256 == OFFICIAL_DOCS_ZIP_SHA256


def test_reviewed_units_and_names_preserve_exact_identity() -> None:
    registry = INITIAL_RETAIL_IDENTITY_REGISTRY

    assert registry.item_names[("400", "414")] == "포도"
    assert registry.variety_names[("400", "414", "12")] == "샤인머스켓"
    assert registry.grade_names[("400", "414", "12", "24")] == "L과"
    assert registry.units[("400", "414", "12", "24")] == ("kg", "2")
    assert registry.units[("200", "212", "00", "04")] == ("포기", "1")
