import uuid

import django.core.validators
from django.db import migrations, models

IMMUTABLE_TRIGGER_SQL = """
CREATE FUNCTION grocery_priceserieskey_reject_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION 'grocery_priceserieskey rows are immutable'
        USING ERRCODE = '55000';
END;
$function$;

CREATE TRIGGER grocery_priceserieskey_immutable
BEFORE UPDATE OR DELETE ON grocery_priceserieskey
FOR EACH ROW
EXECUTE FUNCTION grocery_priceserieskey_reject_mutation();
"""

DROP_IMMUTABLE_TRIGGER_SQL = """
DROP TRIGGER IF EXISTS grocery_priceserieskey_immutable ON grocery_priceserieskey;
DROP FUNCTION IF EXISTS grocery_priceserieskey_reject_mutation();
"""


class Migration(migrations.Migration):
    dependencies = [("grocery", "0002_artifact_parse_runs")]

    operations = [
        migrations.CreateModel(
            name="PriceSeriesKey",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "product_class_code",
                    models.CharField(
                        choices=[("01", "Retail")],
                        default="01",
                        max_length=2,
                    ),
                ),
                ("product_class_name", models.CharField(max_length=100)),
                (
                    "category_code",
                    models.CharField(
                        choices=[("200", "Vegetables"), ("400", "Fruit")],
                        max_length=3,
                    ),
                ),
                ("category_name", models.CharField(max_length=100)),
                (
                    "item_code",
                    models.CharField(
                        max_length=32,
                        validators=[django.core.validators.RegexValidator("^[0-9]+$")],
                    ),
                ),
                ("item_name", models.CharField(max_length=200)),
                (
                    "variety_code",
                    models.CharField(
                        max_length=32,
                        validators=[django.core.validators.RegexValidator("^[0-9]+$")],
                    ),
                ),
                ("variety_name", models.CharField(max_length=200)),
                (
                    "grade_code",
                    models.CharField(
                        max_length=32,
                        validators=[django.core.validators.RegexValidator("^[0-9]+$")],
                    ),
                ),
                ("grade_name", models.CharField(max_length=200)),
                ("raw_unit", models.CharField(max_length=64)),
                ("raw_unit_size", models.CharField(max_length=64)),
                ("coverage_identity", models.CharField(max_length=128)),
                ("identity_evidence_revision", models.CharField(max_length=128)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=(
                            "product_class_code",
                            "category_code",
                            "item_code",
                            "variety_code",
                            "grade_code",
                            "raw_unit",
                            "raw_unit_size",
                            "coverage_identity",
                        ),
                        name="grocery_series_semantic_identity_uniq",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("product_class_code", "01")),
                        name="grocery_series_product_class_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("category_code__in", ("200", "400"))),
                        name="grocery_series_category_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("item_code__regex", "^[0-9]+$"),
                            ("variety_code__regex", "^[0-9]+$"),
                            ("grade_code__regex", "^[0-9]+$"),
                        ),
                        name="grocery_series_codes_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(("product_class_name", ""), _negated=True),
                            models.Q(("category_name", ""), _negated=True),
                            models.Q(("item_name", ""), _negated=True),
                            models.Q(("variety_name", ""), _negated=True),
                            models.Q(("grade_name", ""), _negated=True),
                        ),
                        name="grocery_series_names_nonempty",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(("raw_unit", ""), _negated=True),
                            models.Q(("raw_unit_size", ""), _negated=True),
                        ),
                        name="grocery_series_raw_unit_nonempty",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(("coverage_identity", ""), _negated=True),
                            models.Q(("identity_evidence_revision", ""), _negated=True),
                        ),
                        name="grocery_series_evidence_nonempty",
                    ),
                ]
            },
        ),
        migrations.RunSQL(
            sql=IMMUTABLE_TRIGGER_SQL,
            reverse_sql=DROP_IMMUTABLE_TRIGGER_SQL,
        ),
    ]
