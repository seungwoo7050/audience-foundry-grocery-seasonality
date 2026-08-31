# ruff: noqa: S608 -- only fixed migration fragments are composed below.

from django.db import migrations


def _activation_guard(approval_condition: str) -> str:
    return rf"""
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
              AND ({approval_condition})
       ) THEN
        RAISE EXCEPTION 'historical activation target is not eligible and sealed'
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
"""


_CURRENT_REVIEWS = r"""
grocery_historical_review_matches(
    revision.monthly_review_id, 'MONTHLY', revision.code_manifest_sha256
)
AND grocery_historical_review_matches(
    revision.regional_review_id, 'REGIONAL_DAILY', revision.code_manifest_sha256
)
AND grocery_historical_review_matches(
    revision.market_review_id, 'MARKET_DAILY', revision.code_manifest_sha256
)
""".strip()

ALLOW_LAST_KNOWN_GOOD = _activation_guard(f"NEW.operation = 'ROLLBACK' OR ({_CURRENT_REVIEWS})")
RESTORE_CURRENT_REVIEW_REQUIREMENT = _activation_guard(_CURRENT_REVIEWS)


class Migration(migrations.Migration):
    dependencies = [("grocery", "0026_guard_historical_activation_cas")]

    operations = [migrations.RunSQL(ALLOW_LAST_KNOWN_GOOD, RESTORE_CURRENT_REVIEW_REQUIREMENT)]
