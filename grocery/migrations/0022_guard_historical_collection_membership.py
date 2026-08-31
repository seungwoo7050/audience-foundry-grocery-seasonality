from django.db import migrations

CREATE_GUARDS = r"""
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
     FOR KEY SHARE;
    SELECT artifact_id, status
      INTO parse_artifact, parse_status
      FROM grocery_parserun
     WHERE id = NEW.parse_run_id
     FOR KEY SHARE;
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
     FOR KEY SHARE;
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
     FOR KEY SHARE;
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
     FOR KEY SHARE;
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

CREATE TRIGGER grocery_history_part_append_only
BEFORE INSERT OR UPDATE OR DELETE ON grocery_historicalsourcecollectionpart
FOR EACH ROW EXECUTE FUNCTION grocery_guard_historical_collection_part();

CREATE TRIGGER grocery_monthly_fact_append_only
BEFORE INSERT OR UPDATE OR DELETE ON grocery_monthlyregionalretailprice
FOR EACH ROW EXECUTE FUNCTION grocery_guard_historical_monthly_fact();

CREATE TRIGGER grocery_regional_fact_append_only
BEFORE INSERT OR UPDATE OR DELETE ON grocery_dailyregionalretailprice
FOR EACH ROW EXECUTE FUNCTION grocery_guard_historical_regional_fact();

CREATE TRIGGER grocery_market_fact_append_only
BEFORE INSERT OR UPDATE OR DELETE ON grocery_dailymarketretailprice
FOR EACH ROW EXECUTE FUNCTION grocery_guard_historical_market_fact();
"""


DROP_GUARDS = r"""
DROP TRIGGER IF EXISTS grocery_market_fact_append_only ON grocery_dailymarketretailprice;
DROP TRIGGER IF EXISTS grocery_regional_fact_append_only ON grocery_dailyregionalretailprice;
DROP TRIGGER IF EXISTS grocery_monthly_fact_append_only ON grocery_monthlyregionalretailprice;
DROP TRIGGER IF EXISTS grocery_history_part_append_only
    ON grocery_historicalsourcecollectionpart;
DROP FUNCTION IF EXISTS grocery_guard_historical_market_fact();
DROP FUNCTION IF EXISTS grocery_guard_historical_regional_fact();
DROP FUNCTION IF EXISTS grocery_guard_historical_monthly_fact();
DROP FUNCTION IF EXISTS grocery_guard_historical_collection_part();
"""


class Migration(migrations.Migration):
    dependencies = [("grocery", "0021_bind_historical_source_endpoints")]

    operations = [migrations.RunSQL(CREATE_GUARDS, DROP_GUARDS)]
