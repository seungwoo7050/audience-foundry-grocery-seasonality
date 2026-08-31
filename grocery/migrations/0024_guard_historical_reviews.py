from django.db import migrations

CREATE_REVIEW_GUARDS = r"""
CREATE OR REPLACE FUNCTION grocery_guard_historical_review_insert()
RETURNS trigger AS $$
DECLARE
    capability text := current_setting('grocery.historical_review_id', true);
    collection_record grocery_historicalsourcecollection%ROWTYPE;
    source_record grocery_sourceconfiguration%ROWTYPE;
    expected_mode varchar;
BEGIN
    IF capability IS NULL OR capability IS DISTINCT FROM NEW.id::text THEN
        RAISE EXCEPTION 'historical review insert requires review capability'
            USING ERRCODE = '42501';
    END IF;
    SELECT * INTO collection_record
      FROM grocery_historicalsourcecollection
     WHERE id = NEW.collection_id
     FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'historical review collection is missing'
            USING ERRCODE = '23514';
    END IF;
    SELECT * INTO source_record
      FROM grocery_sourceconfiguration
     WHERE id = collection_record.source_configuration_id
     FOR SHARE;
    expected_mode := CASE collection_record.kind
        WHEN 'MONTHLY' THEN 'HISTORICAL_MONTHLY'
        WHEN 'REGIONAL_DAILY' THEN 'HISTORICAL_REGIONAL'
        WHEN 'MARKET_DAILY' THEN 'HISTORICAL_MARKET'
        ELSE ''
    END;
    IF source_record.state IS DISTINCT FROM 'ACTIVE'
       OR source_record.publication_mode IS DISTINCT FROM expected_mode
       OR collection_record.completed_at IS NULL
       OR collection_record.completed_at > NEW.decided_at THEN
        RAISE EXCEPTION 'historical review source and collection are not reviewable'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.supersedes_id IS NOT NULL AND NOT EXISTS (
        SELECT 1
          FROM grocery_historicalcollectionreviewdecision previous
         WHERE previous.id = NEW.supersedes_id
           AND previous.collection_id = NEW.collection_id
           AND NOT EXISTS (
               SELECT 1
                 FROM grocery_historicalcollectionreviewdecision replacement
                WHERE replacement.supersedes_id = previous.id
           )
    ) THEN
        RAISE EXCEPTION 'historical review supersedes must be the current tail'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.decision = 'APPROVE' AND (
        collection_record.state IS DISTINCT FROM 'VALIDATED'
        OR NEW.approved_result_sha256 IS DISTINCT FROM collection_record.result_sha256
        OR NEW.approved_partition_manifest_sha256
           IS DISTINCT FROM collection_record.partition_manifest_sha256
    ) THEN
        RAISE EXCEPTION 'historical approval hashes do not match the collection'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION grocery_reject_historical_review_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'historical review decisions are append-only'
        USING ERRCODE = '55000';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER grocery_history_review_validate
BEFORE INSERT ON grocery_historicalcollectionreviewdecision
FOR EACH ROW EXECUTE FUNCTION grocery_guard_historical_review_insert();

CREATE TRIGGER grocery_history_review_immutable
BEFORE UPDATE OR DELETE ON grocery_historicalcollectionreviewdecision
FOR EACH ROW EXECUTE FUNCTION grocery_reject_historical_review_mutation();
"""


DROP_REVIEW_GUARDS = r"""
DROP TRIGGER IF EXISTS grocery_history_review_immutable
    ON grocery_historicalcollectionreviewdecision;
DROP TRIGGER IF EXISTS grocery_history_review_validate
    ON grocery_historicalcollectionreviewdecision;
DROP FUNCTION IF EXISTS grocery_reject_historical_review_mutation();
DROP FUNCTION IF EXISTS grocery_guard_historical_review_insert();
"""


class Migration(migrations.Migration):
    dependencies = [("grocery", "0023_serialize_historical_collection_writes")]

    operations = [migrations.RunSQL(CREATE_REVIEW_GUARDS, DROP_REVIEW_GUARDS)]
