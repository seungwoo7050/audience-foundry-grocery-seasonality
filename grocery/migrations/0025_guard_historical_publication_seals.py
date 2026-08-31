from django.db import migrations, models

CREATE_SEAL_GUARDS = r"""
CREATE OR REPLACE FUNCTION grocery_historical_review_matches(
    review_key uuid,
    expected_kind varchar,
    expected_manifest varchar
)
RETURNS boolean AS $$
    SELECT EXISTS (
        SELECT 1
          FROM grocery_historicalcollectionreviewdecision review
          JOIN grocery_historicalsourcecollection collection
            ON collection.id = review.collection_id
         WHERE review.id = review_key
           AND review.decision = 'APPROVE'
           AND collection.kind = expected_kind
           AND collection.state = 'VALIDATED'
           AND collection.code_manifest_sha256 = expected_manifest
           AND review.approved_result_sha256 = collection.result_sha256
           AND review.approved_partition_manifest_sha256
               = collection.partition_manifest_sha256
           AND NOT EXISTS (
               SELECT 1
                 FROM grocery_historicalcollectionreviewdecision replacement
                WHERE replacement.supersedes_id = review.id
           )
    );
$$ LANGUAGE sql STABLE;

CREATE OR REPLACE FUNCTION grocery_guard_historical_publication_revision()
RETURNS trigger AS $$
DECLARE
    capability text := current_setting('grocery.historical_seal_id', true);
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'historical publication revisions are immutable'
            USING ERRCODE = '55000';
    END IF;
    IF capability IS NULL OR capability IS DISTINCT FROM NEW.id::text THEN
        RAISE EXCEPTION 'historical publication revision requires seal capability'
            USING ERRCODE = '42501';
    END IF;
    IF NEW.monthly_review_id = NEW.regional_review_id
       OR NEW.monthly_review_id = NEW.market_review_id
       OR NEW.regional_review_id = NEW.market_review_id
       OR NOT grocery_historical_review_matches(
           NEW.monthly_review_id, 'MONTHLY', NEW.code_manifest_sha256
       )
       OR NOT grocery_historical_review_matches(
           NEW.regional_review_id, 'REGIONAL_DAILY', NEW.code_manifest_sha256
       )
       OR NOT grocery_historical_review_matches(
           NEW.market_review_id, 'MARKET_DAILY', NEW.code_manifest_sha256
       ) THEN
        RAISE EXCEPTION 'historical publication reviews are not current exact approvals'
            USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.sealed_at IS NOT NULL THEN
            RAISE EXCEPTION 'historical publication revisions must start unsealed'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.sealed_at IS NOT NULL OR NEW.sealed_at IS NULL
       OR NEW.id IS DISTINCT FROM OLD.id
       OR NEW.monthly_review_id IS DISTINCT FROM OLD.monthly_review_id
       OR NEW.regional_review_id IS DISTINCT FROM OLD.regional_review_id
       OR NEW.market_review_id IS DISTINCT FROM OLD.market_review_id
       OR NEW.code_manifest_sha256 IS DISTINCT FROM OLD.code_manifest_sha256
       OR NEW.compatibility_report_sha256 IS DISTINCT FROM OLD.compatibility_report_sha256
       OR NEW.fact_hash_version IS DISTINCT FROM OLD.fact_hash_version
       OR NEW.typed_fact_set_sha256 IS DISTINCT FROM OLD.typed_fact_set_sha256
       OR NEW.series_count IS DISTINCT FROM OLD.series_count
       OR NEW.monthly_fact_count IS DISTINCT FROM OLD.monthly_fact_count
       OR NEW.regional_fact_count IS DISTINCT FROM OLD.regional_fact_count
       OR NEW.market_fact_count IS DISTINCT FROM OLD.market_fact_count
       OR NEW.month_min IS DISTINCT FROM OLD.month_min
       OR NEW.month_max IS DISTINCT FROM OLD.month_max
       OR NEW.date_min IS DISTINCT FROM OLD.date_min
       OR NEW.date_max IS DISTINCT FROM OLD.date_max
       OR NEW.public_copy_revision IS DISTINCT FROM OLD.public_copy_revision
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'historical publication only permits a one-time seal'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER grocery_history_publication_revision_guard
BEFORE INSERT OR UPDATE OR DELETE ON grocery_historicalretailpublicationrevision
FOR EACH ROW EXECUTE FUNCTION grocery_guard_historical_publication_revision();
"""


DROP_SEAL_GUARDS = r"""
DROP TRIGGER IF EXISTS grocery_history_publication_revision_guard
    ON grocery_historicalretailpublicationrevision;
DROP FUNCTION IF EXISTS grocery_guard_historical_publication_revision();
DROP FUNCTION IF EXISTS grocery_historical_review_matches(uuid, varchar, varchar);
"""


class Migration(migrations.Migration):
    dependencies = [("grocery", "0024_guard_historical_reviews")]

    operations = [
        migrations.AddConstraint(
            model_name="historicalretailpublicationrevision",
            constraint=models.CheckConstraint(
                condition=(
                    ~models.Q(monthly_review=models.F("regional_review"))
                    & ~models.Q(monthly_review=models.F("market_review"))
                    & ~models.Q(regional_review=models.F("market_review"))
                ),
                name="grocery_history_publication_reviews_distinct",
            ),
        ),
        migrations.RunSQL(CREATE_SEAL_GUARDS, DROP_SEAL_GUARDS),
    ]
