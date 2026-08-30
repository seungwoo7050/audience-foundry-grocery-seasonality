import uuid

from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.test import TestCase

from grocery.models import PriceSeriesKey


def series_fields(**overrides: str) -> dict[str, str]:
    fields = {
        "product_class_code": "01",
        "product_class_name": "소매",
        "category_code": "200",
        "category_name": "채소류",
        "item_code": "212",
        "item_name": "배추",
        "variety_code": "00",
        "variety_name": "월동",
        "grade_code": "04",
        "grade_name": "상품",
        "raw_unit": "포기",
        "raw_unit_size": "1",
        "coverage_identity": "KAMIS_RETAIL_ALL_REGIONS_22_CITIES_V1",
        "identity_evidence_revision": "kamis-codebook-and-coverage-v1",
    }
    fields.update(overrides)
    return fields


def create_series(**overrides: str) -> PriceSeriesKey:
    return PriceSeriesKey.objects.create(**series_fields(**overrides))


def get_or_validate_series(**overrides: str) -> PriceSeriesKey:
    fields = series_fields(**overrides)
    return PriceSeriesKey.get_or_validate(
        product_class_code=fields["product_class_code"],
        product_class_name=fields["product_class_name"],
        category_code=fields["category_code"],
        category_name=fields["category_name"],
        item_code=fields["item_code"],
        item_name=fields["item_name"],
        variety_code=fields["variety_code"],
        variety_name=fields["variety_name"],
        grade_code=fields["grade_code"],
        grade_name=fields["grade_name"],
        raw_unit=fields["raw_unit"],
        raw_unit_size=fields["raw_unit_size"],
        coverage_identity=fields["coverage_identity"],
        identity_evidence_revision=fields["identity_evidence_revision"],
    )


class PriceSeriesKeyTests(TestCase):
    def test_valid_series_preserves_leading_zero_codes_and_semantic_identity(self) -> None:
        series = create_series(item_code="006", variety_code="01", grade_code="04")

        self.assertIsInstance(series.id, uuid.UUID)
        self.assertEqual(series.product_class_code, "01")
        self.assertEqual(series.item_code, "006")
        self.assertEqual(series.variety_code, "01")
        self.assertEqual(series.grade_code, "04")
        self.assertIn(series.coverage_identity, str(series))

    def test_get_or_validate_is_idempotent_and_fails_closed_on_reviewed_drift(self) -> None:
        original = get_or_validate_series()
        repeated = get_or_validate_series()

        self.assertEqual(repeated.id, original.id)
        self.assertEqual(PriceSeriesKey.objects.count(), 1)

        for changed_field in (
            "product_class_name",
            "category_name",
            "item_name",
            "variety_name",
            "grade_name",
            "identity_evidence_revision",
        ):
            with self.subTest(changed_field=changed_field):
                with self.assertRaisesMessage(ValidationError, "drifted"):
                    get_or_validate_series(**{changed_field: "CHANGED"})

        self.assertEqual(PriceSeriesKey.objects.count(), 1)

    def test_semantic_unique_constraint_excludes_names_but_rejects_name_drift(self) -> None:
        original = create_series()
        duplicate = PriceSeriesKey(**series_fields(item_name="바뀐 이름"))

        with self.assertRaises(IntegrityError), transaction.atomic():
            PriceSeriesKey.objects.bulk_create([duplicate])

        self.assertEqual(PriceSeriesKey.objects.get().id, original.id)

    def test_database_checks_reject_invalid_enum_code_name_raw_and_evidence_fields(self) -> None:
        invalid_variants = (
            {"product_class_code": "02"},
            {"category_code": "300"},
            {"item_code": "A12"},
            {"variety_code": ""},
            {"grade_code": "04-A"},
            {"item_name": ""},
            {"raw_unit": ""},
            {"raw_unit_size": ""},
            {"coverage_identity": ""},
            {"identity_evidence_revision": ""},
        )
        for overrides in invalid_variants:
            with self.subTest(overrides=overrides):
                invalid = PriceSeriesKey(**series_fields(**overrides))
                with self.assertRaises(IntegrityError), transaction.atomic():
                    PriceSeriesKey.objects.bulk_create([invalid])

        self.assertFalse(PriceSeriesKey.objects.exists())

    def test_model_validation_rejects_non_digit_codes_before_insert(self) -> None:
        with self.assertRaises(ValidationError):
            create_series(item_code="12A")

    def test_orm_and_database_both_block_update_and_delete(self) -> None:
        series = create_series()
        series.item_name = "수정 시도"

        with self.assertRaisesMessage(ValidationError, "immutable"):
            series.save()
        with self.assertRaisesMessage(ValidationError, "immutable"):
            series.delete()

        with self.assertRaisesMessage(DatabaseError, "immutable"), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE grocery_priceserieskey SET item_name = %s WHERE id = %s",
                    ["직접 수정 시도", series.id],
                )

        with self.assertRaisesMessage(DatabaseError, "immutable"), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM grocery_priceserieskey WHERE id = %s",
                    [series.id],
                )

        series.refresh_from_db()
        self.assertEqual(series.item_name, "배추")
