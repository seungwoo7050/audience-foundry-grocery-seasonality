from django.db import migrations

LOCK_GUARDS = r"""
CREATE OR REPLACE FUNCTION grocery_guard_historical_collection_part()
RETURNS trigger AS $$
DECLARE
    collection_source uuid;
    collection_state varchar;
    parse_artifact uuid;
    parse_status varchar;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'historical collection parts are append-only';
    END IF;
    SELECT source_configuration_id, state
      INTO collection_source, collection_state
      FROM grocery_historicalsourcecollection
     WHERE id = NEW.collection_id
     FOR SHARE;
    SELECT artifact_id, status
      INTO parse_artifact, parse_status
      FROM grocery_parserun
     WHERE id = NEW.parse_run_id
     FOR SHARE;
    IF collection_state IS DISTINCT FROM 'STARTED'
       OR parse_status IS DISTINCT FROM 'VALIDATED'
       OR NOT EXISTS (
           SELECT 1
             FROM grocery_fetchattempt
            WHERE source_configuration_id = collection_source
              AND artifact_id = parse_artifact
              AND state = 'SUCCEEDED'
              AND request_scope_sha256 = NEW.partition_scope_sha256
       ) THEN
        RAISE EXCEPTION 'historical collection part does not match a started audited scope';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION grocery_guard_historical_monthly_fact()
RETURNS trigger AS $$
DECLARE
    collection_state varchar;
    collection_kind varchar;
    window_min varchar;
    window_max varchar;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'historical monthly facts are append-only';
    END IF;
    SELECT state, kind, month_min, month_max
      INTO collection_state, collection_kind, window_min, window_max
      FROM grocery_historicalsourcecollection
     WHERE id = NEW.collection_id
     FOR SHARE;
    IF collection_state IS DISTINCT FROM 'STARTED'
       OR collection_kind IS DISTINCT FROM 'MONTHLY'
       OR NEW.year_month NOT BETWEEN window_min AND window_max
       OR NOT EXISTS (
        SELECT 1
          FROM grocery_historicalsourcecollectionpart p
         WHERE p.collection_id = NEW.collection_id
           AND p.id = NEW.collection_part_id
    ) THEN
        RAISE EXCEPTION 'historical monthly fact is outside its started collection';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION grocery_guard_historical_regional_fact()
RETURNS trigger AS $$
DECLARE
    collection_state varchar;
    collection_kind varchar;
    window_min date;
    window_max date;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'historical regional facts are append-only';
    END IF;
    SELECT state, kind, date_min, date_max
      INTO collection_state, collection_kind, window_min, window_max
      FROM grocery_historicalsourcecollection
     WHERE id = NEW.collection_id
     FOR SHARE;
    IF collection_state IS DISTINCT FROM 'STARTED'
       OR collection_kind IS DISTINCT FROM 'REGIONAL_DAILY'
       OR NEW.survey_date NOT BETWEEN window_min AND window_max
       OR NOT EXISTS (
        SELECT 1
          FROM grocery_historicalsourcecollectionpart p
         WHERE p.collection_id = NEW.collection_id
           AND p.id = NEW.collection_part_id
    ) THEN
        RAISE EXCEPTION 'historical regional fact is outside its started collection';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION grocery_guard_historical_market_fact()
RETURNS trigger AS $$
DECLARE
    collection_state varchar;
    collection_kind varchar;
    window_min date;
    window_max date;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'historical market facts are append-only';
    END IF;
    SELECT state, kind, date_min, date_max
      INTO collection_state, collection_kind, window_min, window_max
      FROM grocery_historicalsourcecollection
     WHERE id = NEW.collection_id
     FOR SHARE;
    IF collection_state IS DISTINCT FROM 'STARTED'
       OR collection_kind IS DISTINCT FROM 'MARKET_DAILY'
       OR NEW.survey_date NOT BETWEEN window_min AND window_max
       OR NOT EXISTS (
        SELECT 1
          FROM grocery_historicalsourcecollectionpart p
          JOIN grocery_retailmarketkey m
            ON m.id = NEW.market_id
         WHERE p.collection_id = NEW.collection_id
           AND p.id = NEW.collection_part_id
           AND m.region_id = NEW.region_id
    ) THEN
        RAISE EXCEPTION 'historical market fact is outside its started collection';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


REVERSE_LOCK_GUARDS = LOCK_GUARDS.replace("FOR SHARE;", "FOR KEY SHARE;")


class Migration(migrations.Migration):
    dependencies = [("grocery", "0022_guard_historical_collection_membership")]

    operations = [migrations.RunSQL(LOCK_GUARDS, REVERSE_LOCK_GUARDS)]
