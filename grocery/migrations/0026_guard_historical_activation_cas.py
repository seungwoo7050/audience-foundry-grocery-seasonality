from django.db import migrations

UPGRADE_GUARDS = r"""
CREATE OR REPLACE FUNCTION grocery_guard_historical_activation()
RETURNS trigger AS $$
DECLARE
    capability text := current_setting('grocery.historical_transition_id', true);
    channel_record grocery_historicalretailpublicationchannel%ROWTYPE;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'historical publication events are append-only'
            USING ERRCODE = '55000';
    END IF;
    IF capability IS NULL OR capability IS DISTINCT FROM NEW.id::text THEN
        RAISE EXCEPTION 'historical activation requires transition capability'
            USING ERRCODE = '42501';
    END IF;
    SELECT * INTO channel_record
      FROM grocery_historicalretailpublicationchannel
     WHERE channel = NEW.channel_id
     FOR SHARE;
    IF NOT FOUND
       OR NEW.sequence IS DISTINCT FROM channel_record.version + 1
       OR NEW.previous_revision_id IS DISTINCT FROM channel_record.current_revision_id THEN
        RAISE EXCEPTION 'historical activation does not match current state'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.operation = 'WITHDRAW' THEN
        IF NEW.target_revision_id IS NOT NULL OR NEW.previous_revision_id IS NULL THEN
            RAISE EXCEPTION 'historical withdrawal shape is invalid'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.operation NOT IN ('ACTIVATE', 'ROLLBACK')
       OR NEW.target_revision_id IS NULL
       OR NEW.target_revision_id IS NOT DISTINCT FROM NEW.previous_revision_id
       OR NOT EXISTS (
           SELECT 1
             FROM grocery_historicalretailpublicationrevision revision
            WHERE revision.id = NEW.target_revision_id
              AND revision.sealed_at IS NOT NULL
              AND grocery_historical_review_matches(
                  revision.monthly_review_id, 'MONTHLY', revision.code_manifest_sha256
              )
              AND grocery_historical_review_matches(
                  revision.regional_review_id,
                  'REGIONAL_DAILY',
                  revision.code_manifest_sha256
              )
              AND grocery_historical_review_matches(
                  revision.market_review_id,
                  'MARKET_DAILY',
                  revision.code_manifest_sha256
              )
       ) THEN
        RAISE EXCEPTION 'historical activation target is not current and sealed'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.operation = 'ROLLBACK' AND NOT EXISTS (
        SELECT 1
          FROM grocery_historicalretailpublicationactivation prior
         WHERE prior.channel_id = NEW.channel_id
           AND prior.sequence < NEW.sequence
           AND prior.operation IN ('ACTIVATE', 'ROLLBACK')
           AND prior.target_revision_id = NEW.target_revision_id
    ) THEN
        RAISE EXCEPTION 'historical rollback target was not previously current'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION grocery_guard_historical_channel()
RETURNS trigger AS $$
DECLARE
    capability text := current_setting('grocery.historical_transition_id', true);
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'historical publication channel cannot be deleted'
            USING ERRCODE = '55000';
    END IF;
    IF capability IS NULL
       OR capability !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
        RAISE EXCEPTION 'historical channel requires transition capability'
            USING ERRCODE = '42501';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.channel IS DISTINCT FROM 'HISTORICAL_RETAIL'
           OR NEW.version IS DISTINCT FROM 0::bigint
           OR NEW.current_revision_id IS NOT NULL THEN
            RAISE EXCEPTION 'historical channel bootstrap is invalid'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.channel IS DISTINCT FROM OLD.channel
       OR NEW.version IS DISTINCT FROM OLD.version + 1
       OR NOT EXISTS (
           SELECT 1
             FROM grocery_historicalretailpublicationactivation activation
            WHERE activation.id::text = capability
              AND activation.channel_id = OLD.channel
              AND activation.sequence = NEW.version
              AND activation.previous_revision_id IS NOT DISTINCT FROM OLD.current_revision_id
              AND activation.target_revision_id IS NOT DISTINCT FROM NEW.current_revision_id
       ) THEN
        RAISE EXCEPTION 'historical channel requires its matching activation'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION grocery_assert_historical_channel_state(channel_key varchar)
RETURNS void AS $$
DECLARE
    channel_record grocery_historicalretailpublicationchannel%ROWTYPE;
    activation_count bigint;
    maximum_sequence bigint;
    latest_target uuid;
BEGIN
    SELECT * INTO channel_record
      FROM grocery_historicalretailpublicationchannel
     WHERE channel = channel_key;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'historical channel state is missing' USING ERRCODE = '23514';
    END IF;
    SELECT count(*), coalesce(max(sequence), 0)
      INTO activation_count, maximum_sequence
      FROM grocery_historicalretailpublicationactivation
     WHERE channel_id = channel_key;
    IF activation_count = 0 THEN
        IF channel_record.version IS DISTINCT FROM 0::bigint
           OR channel_record.current_revision_id IS NOT NULL THEN
            RAISE EXCEPTION 'empty historical channel state is inconsistent'
                USING ERRCODE = '23514';
        END IF;
        RETURN;
    END IF;
    SELECT target_revision_id INTO latest_target
      FROM grocery_historicalretailpublicationactivation
     WHERE channel_id = channel_key
     ORDER BY sequence DESC
     LIMIT 1;
    IF channel_record.version IS DISTINCT FROM maximum_sequence
       OR maximum_sequence IS DISTINCT FROM activation_count
       OR channel_record.current_revision_id IS DISTINCT FROM latest_target
       OR EXISTS (
           SELECT 1
             FROM grocery_historicalretailpublicationactivation current_event
             LEFT JOIN grocery_historicalretailpublicationactivation previous_event
               ON previous_event.channel_id = current_event.channel_id
              AND previous_event.sequence = current_event.sequence - 1
            WHERE current_event.channel_id = channel_key
              AND (
                  (current_event.sequence = 1 AND current_event.previous_revision_id IS NOT NULL)
                  OR (
                      current_event.sequence > 1
                      AND (
                          previous_event.id IS NULL
                          OR current_event.previous_revision_id
                             IS DISTINCT FROM previous_event.target_revision_id
                      )
                  )
              )
       ) THEN
        RAISE EXCEPTION 'historical channel event history is inconsistent'
            USING ERRCODE = '23514';
    END IF;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION grocery_check_historical_activation_deferred()
RETURNS trigger AS $$
BEGIN
    PERFORM grocery_assert_historical_channel_state(NEW.channel_id);
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION grocery_check_historical_channel_deferred()
RETURNS trigger AS $$
BEGIN
    PERFORM grocery_assert_historical_channel_state(NEW.channel);
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER grocery_history_activation_state_consistent
AFTER INSERT ON grocery_historicalretailpublicationactivation
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION grocery_check_historical_activation_deferred();

CREATE CONSTRAINT TRIGGER grocery_history_channel_state_consistent
AFTER INSERT OR UPDATE ON grocery_historicalretailpublicationchannel
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION grocery_check_historical_channel_deferred();
"""


RESTORE_GUARDS = r"""
DROP TRIGGER IF EXISTS grocery_history_channel_state_consistent
    ON grocery_historicalretailpublicationchannel;
DROP TRIGGER IF EXISTS grocery_history_activation_state_consistent
    ON grocery_historicalretailpublicationactivation;
DROP FUNCTION IF EXISTS grocery_check_historical_channel_deferred();
DROP FUNCTION IF EXISTS grocery_check_historical_activation_deferred();
DROP FUNCTION IF EXISTS grocery_assert_historical_channel_state(varchar);

CREATE OR REPLACE FUNCTION grocery_guard_historical_activation()
RETURNS trigger AS $$
DECLARE
    channel_record grocery_historicalretailpublicationchannel%ROWTYPE;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'historical publication events are append-only';
    END IF;
    SELECT * INTO channel_record
      FROM grocery_historicalretailpublicationchannel
     WHERE channel = NEW.channel_id
     FOR KEY SHARE;
    IF NOT FOUND
       OR NEW.sequence <> channel_record.version + 1
       OR NEW.previous_revision_id IS DISTINCT FROM channel_record.current_revision_id THEN
        RAISE EXCEPTION 'historical publication event does not match current state';
    END IF;
    IF NEW.target_revision_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM grocery_historicalretailpublicationrevision
         WHERE id = NEW.target_revision_id AND sealed_at IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'historical publication target is not sealed';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION grocery_guard_historical_channel()
RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'historical publication channel cannot be deleted';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.channel <> 'HISTORICAL_RETAIL'
           OR NEW.version <> 0
           OR NEW.current_revision_id IS NOT NULL THEN
            RAISE EXCEPTION 'historical publication channel must start withdrawn';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.channel IS DISTINCT FROM OLD.channel
       OR NEW.version <> OLD.version + 1
       OR NOT EXISTS (
           SELECT 1 FROM grocery_historicalretailpublicationactivation
            WHERE channel_id = OLD.channel
              AND sequence = NEW.version
              AND previous_revision_id IS NOT DISTINCT FROM OLD.current_revision_id
              AND target_revision_id IS NOT DISTINCT FROM NEW.current_revision_id
       ) THEN
        RAISE EXCEPTION 'historical publication channel requires a matching event';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


class Migration(migrations.Migration):
    dependencies = [("grocery", "0025_guard_historical_publication_seals")]

    operations = [migrations.RunSQL(UPGRADE_GUARDS, RESTORE_GUARDS)]
