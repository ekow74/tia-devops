-- Pack Builder release-lot integrity controls.
-- These controls gate the first release-lot freeze, not Phase-0 source registration.

BEGIN;

-- A transaction-scoped gate gives release-lot, pinning, withdrawal, RecallEvent,
-- and human-dual review mutation paths the same order before tuple locks. The
-- revision row locks below remain the per-revision eligibility interlock.
CREATE FUNCTION lock_release_lot_integrity_gate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF current_setting('transaction_isolation') <> 'read committed' THEN
    RAISE EXCEPTION 'release-lot mutations require READ COMMITTED isolation';
  END IF;
  PERFORM pg_advisory_xact_lock(1263749968, 1);
  RETURN NULL;
END $$;

CREATE TRIGGER trg_release_lot_integrity_gate
BEFORE INSERT OR UPDATE OR DELETE ON release_lot
FOR EACH STATEMENT EXECUTE FUNCTION lock_release_lot_integrity_gate();
CREATE TRIGGER trg_release_lot_claim_integrity_gate
BEFORE INSERT OR UPDATE OR DELETE ON release_lot_claim
FOR EACH STATEMENT EXECUTE FUNCTION lock_release_lot_integrity_gate();
CREATE TRIGGER trg_claim_revision_release_lot_integrity_gate
BEFORE UPDATE OF status, recalled ON claim_revision
FOR EACH STATEMENT EXECUTE FUNCTION lock_release_lot_integrity_gate();
CREATE TRIGGER trg_recall_event_release_lot_integrity_gate
BEFORE INSERT ON recall_event
FOR EACH STATEMENT EXECUTE FUNCTION lock_release_lot_integrity_gate();
CREATE TRIGGER trg_human_dual_review_release_lot_integrity_gate
BEFORE UPDATE OR DELETE ON human_dual_review
FOR EACH STATEMENT EXECUTE FUNCTION lock_release_lot_integrity_gate();

CREATE FUNCTION reject_release_lot_truncate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'TRUNCATE is prohibited on durable release-lot tables';
END $$;

CREATE TRIGGER trg_release_lot_truncate_guard
BEFORE TRUNCATE ON release_lot
FOR EACH STATEMENT EXECUTE FUNCTION reject_release_lot_truncate();
CREATE TRIGGER trg_release_lot_claim_truncate_guard
BEFORE TRUNCATE ON release_lot_claim
FOR EACH STATEMENT EXECUTE FUNCTION reject_release_lot_truncate();

CREATE FUNCTION protect_recall_event_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP='DELETE' THEN
    RAISE EXCEPTION 'RecallEvent is append-only; only completed_at may be set once';
  END IF;

  IF NEW.recall_id IS DISTINCT FROM OLD.recall_id OR
     NEW.claim_id IS DISTINCT FROM OLD.claim_id OR
     NEW.claim_revision_id IS DISTINCT FROM OLD.claim_revision_id OR
     NEW.content_hash IS DISTINCT FROM OLD.content_hash OR
     NEW.reason IS DISTINCT FROM OLD.reason OR
     NEW.initiated_at IS DISTINCT FROM OLD.initiated_at OR
     (OLD.completed_at IS NOT NULL AND NEW.completed_at IS DISTINCT FROM OLD.completed_at) THEN
    RAISE EXCEPTION 'RecallEvent is append-only; only completed_at may be set once';
  END IF;

  RETURN NEW;
END $$;

CREATE TRIGGER trg_recall_event_append_only
BEFORE UPDATE OR DELETE ON recall_event
FOR EACH ROW EXECUTE FUNCTION protect_recall_event_append_only();

CREATE FUNCTION reject_recall_event_truncate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'TRUNCATE is prohibited on RecallEvent records';
END $$;

CREATE TRIGGER trg_recall_event_truncate_guard
BEFORE TRUNCATE ON recall_event
FOR EACH STATEMENT EXECUTE FUNCTION reject_recall_event_truncate();

-- Membership is mutable only while the lot is CANDIDATE. New or updated membership
-- must point at an exact currently APPROVED, non-recalled revision.
CREATE FUNCTION enforce_release_lot_claim_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  old_lot_status TEXT;
  new_lot_status TEXT;
  revision_claim_id TEXT;
  rev_status TEXT;
  rev_recalled BOOLEAN;
BEGIN
  IF TG_OP IN ('INSERT','UPDATE') THEN
    SELECT claim_id, status, recalled
      INTO revision_claim_id, rev_status, rev_recalled
    FROM claim_revision
    WHERE claim_revision_id=NEW.claim_revision_id
      AND content_hash=NEW.content_hash
    FOR SHARE;

    IF NOT FOUND THEN
      RAISE EXCEPTION 'release-lot membership requires exact APPROVED non-recalled claim revision';
    END IF;

    IF rev_status IS DISTINCT FROM 'APPROVED' OR rev_recalled OR EXISTS (
      SELECT 1
      FROM recall_event re
      WHERE re.claim_id=revision_claim_id
        AND (re.claim_revision_id IS NULL OR re.claim_revision_id=NEW.claim_revision_id)
        AND (re.content_hash IS NULL OR re.content_hash=NEW.content_hash)
    ) THEN
      RAISE EXCEPTION 'release-lot membership requires exact APPROVED non-recalled claim revision';
    END IF;
  END IF;

  IF TG_OP='DELETE' THEN
    SELECT status INTO old_lot_status
    FROM release_lot
    WHERE lot_id=OLD.lot_id
    FOR UPDATE;
    IF old_lot_status IS DISTINCT FROM 'CANDIDATE' THEN
      RAISE EXCEPTION 'release-lot membership is immutable unless lot is CANDIDATE';
    END IF;
  ELSIF TG_OP='UPDATE' AND OLD.lot_id IS DISTINCT FROM NEW.lot_id THEN
    PERFORM lot_id
    FROM release_lot
    WHERE lot_id IN (OLD.lot_id, NEW.lot_id)
    ORDER BY lot_id
    FOR UPDATE;
    SELECT status INTO old_lot_status FROM release_lot WHERE lot_id=OLD.lot_id;
    SELECT status INTO new_lot_status FROM release_lot WHERE lot_id=NEW.lot_id;
    IF old_lot_status IS DISTINCT FROM 'CANDIDATE'
       OR new_lot_status IS DISTINCT FROM 'CANDIDATE' THEN
      RAISE EXCEPTION 'release-lot membership is immutable unless lot is CANDIDATE';
    END IF;
  ELSIF TG_OP IN ('INSERT','UPDATE') THEN
    SELECT status INTO new_lot_status
    FROM release_lot
    WHERE lot_id=NEW.lot_id
    FOR UPDATE;
    IF new_lot_status IS DISTINCT FROM 'CANDIDATE' THEN
      RAISE EXCEPTION 'release-lot membership is immutable unless lot is CANDIDATE';
    END IF;
  END IF;

  RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
END $$;

CREATE TRIGGER trg_release_lot_claim_mutation
BEFORE INSERT OR UPDATE OR DELETE ON release_lot_claim
FOR EACH ROW EXECUTE FUNCTION enforce_release_lot_claim_mutation();

-- FROZEN/RELEASED lots are durable release records and cannot be deleted.
CREATE FUNCTION protect_release_lot_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status IS DISTINCT FROM 'CANDIDATE' THEN
    RAISE EXCEPTION 'non-candidate release lot cannot be deleted';
  END IF;
  RETURN OLD;
END $$;

CREATE TRIGGER trg_release_lot_delete_guard
BEFORE DELETE ON release_lot
FOR EACH ROW EXECUTE FUNCTION protect_release_lot_delete();

-- Release-lot state machine + freeze validation.
-- Lock every exact pinned claim revision FOR SHARE in stable order so concurrent
-- approval withdrawal cannot race a freeze/release decision.
CREATE FUNCTION enforce_release_lot_state_transition() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  member_count BIGINT;
BEGIN
  IF TG_OP='INSERT' THEN
    IF NEW.status IS DISTINCT FROM 'CANDIDATE' OR NEW.frozen_at IS NOT NULL THEN
      RAISE EXCEPTION 'release lot must be created as CANDIDATE without a freeze timestamp';
    END IF;
    RETURN NEW;
  END IF;

  IF OLD.status IS DISTINCT FROM 'CANDIDATE' AND (
    NEW.lot_id IS DISTINCT FROM OLD.lot_id OR
    NEW.bundle_sha256 IS DISTINCT FROM OLD.bundle_sha256 OR
    NEW.frozen_at IS DISTINCT FROM OLD.frozen_at OR
    NEW.registry_snapshot_at IS DISTINCT FROM OLD.registry_snapshot_at
  ) THEN
    RAISE EXCEPTION 'non-candidate release lot identity is immutable';
  END IF;

  IF OLD.status='CANDIDATE' AND NEW.status<>'FROZEN'
     AND NEW.frozen_at IS DISTINCT FROM OLD.frozen_at THEN
    RAISE EXCEPTION 'frozen_at is set only when release lot freezes';
  END IF;

  IF NEW.status IS NOT DISTINCT FROM OLD.status THEN
    RETURN NEW;
  END IF;

  IF OLD.status IN ('QUARANTINED','RECALLED') THEN
    RAISE EXCEPTION 'terminal release lot cannot transition to another status';
  END IF;

  IF OLD.status='FROZEN' AND NEW.status='CANDIDATE' THEN
    RAISE EXCEPTION 'frozen release lot cannot return to CANDIDATE';
  END IF;

  IF NEW.status='RELEASED' AND OLD.status<>'FROZEN' THEN
    RAISE EXCEPTION 'release lot must be FROZEN before RELEASED';
  END IF;

  IF OLD.status='RELEASED' AND NEW.status IN ('CANDIDATE','FROZEN') THEN
    RAISE EXCEPTION 'released release lot cannot transition to CANDIDATE or FROZEN';
  END IF;

  -- Quarantine and recall remain explicit exits for candidates, frozen lots,
  -- and released lots. Both resulting states are terminal and cannot be reopened.
  IF NOT (
    (OLD.status='CANDIDATE' AND NEW.status IN ('FROZEN','QUARANTINED','RECALLED')) OR
    (OLD.status='FROZEN' AND NEW.status IN ('RELEASED','QUARANTINED','RECALLED')) OR
    (OLD.status='RELEASED' AND NEW.status IN ('QUARANTINED','RECALLED'))
  ) THEN
    RAISE EXCEPTION 'invalid release lot status transition';
  END IF;

  IF NEW.status IN ('FROZEN','RELEASED') THEN
    SELECT count(*) INTO member_count
    FROM release_lot_claim
    WHERE lot_id=NEW.lot_id;

    IF member_count=0 THEN
      RAISE EXCEPTION 'release lot cannot freeze or release without pinned claims';
    END IF;

    PERFORM cr.claim_revision_id
    FROM release_lot_claim rlc
    JOIN claim_revision cr
      ON cr.claim_revision_id=rlc.claim_revision_id
     AND cr.content_hash=rlc.content_hash
    WHERE rlc.lot_id=NEW.lot_id
    ORDER BY cr.claim_revision_id
    FOR SHARE OF cr;

    IF EXISTS (
      SELECT 1
      FROM release_lot_claim rlc
      LEFT JOIN claim_revision cr
        ON cr.claim_revision_id=rlc.claim_revision_id
       AND cr.content_hash=rlc.content_hash
      WHERE rlc.lot_id=NEW.lot_id
        AND (
          cr.claim_revision_id IS NULL OR cr.status<>'APPROVED' OR cr.recalled OR
          EXISTS (
            SELECT 1
            FROM recall_event re
            WHERE re.claim_id=cr.claim_id
              AND (re.claim_revision_id IS NULL OR re.claim_revision_id=cr.claim_revision_id)
              AND (re.content_hash IS NULL OR re.content_hash=rlc.content_hash)
          )
        )
    ) THEN
      RAISE EXCEPTION 'release lot cannot freeze or release with non-approved, stale, or recalled claim revision';
    END IF;
  END IF;

  IF OLD.status='CANDIDATE' AND NEW.status='FROZEN' THEN
    NEW.frozen_at := clock_timestamp();
  END IF;

  RETURN NEW;
END $$;

CREATE TRIGGER trg_release_lot_state_transition
BEFORE INSERT OR UPDATE ON release_lot
FOR EACH ROW EXECUTE FUNCTION enforce_release_lot_state_transition();

-- Any approved revision leaving APPROVED (including STALE/RECALLED) invalidates
-- release lots that pin that exact content. This also closes the post-freeze side
-- of the approval-withdrawal concurrency race.
CREATE FUNCTION quarantine_release_lots_on_claim_withdrawal() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status='APPROVED' AND NEW.status IS DISTINCT FROM 'APPROVED' THEN
    UPDATE release_lot rl
       SET status='QUARANTINED'
     WHERE rl.status IN ('FROZEN','RELEASED')
       AND EXISTS (
         SELECT 1
         FROM release_lot_claim rlc
         WHERE rlc.lot_id=rl.lot_id
           AND rlc.claim_revision_id=NEW.claim_revision_id
           AND rlc.content_hash=NEW.content_hash
       );
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_claim_revision_release_lot_quarantine
AFTER UPDATE OF status, recalled ON claim_revision
FOR EACH ROW EXECUTE FUNCTION quarantine_release_lots_on_claim_withdrawal();

-- RecallEvent is independently authoritative for release eligibility. It must
-- quarantine matching lots even if claim_revision operational status has not yet
-- been updated by the recall workflow.
CREATE FUNCTION quarantine_release_lots_on_recall_event() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  -- Match the eligibility predicate exactly and lock revisions before scanning
  -- lots. The statement-level gate establishes the common lock order; ordering
  -- the row locks prevents cross-revision deadlocks for multi-revision recalls.
  PERFORM cr.claim_revision_id
  FROM claim_revision cr
  WHERE cr.claim_id=NEW.claim_id
    AND (NEW.claim_revision_id IS NULL OR cr.claim_revision_id=NEW.claim_revision_id)
    AND (NEW.content_hash IS NULL OR cr.content_hash=NEW.content_hash)
  ORDER BY cr.claim_revision_id
  FOR NO KEY UPDATE OF cr;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'RecallEvent matches no claim revision';
  END IF;

  UPDATE release_lot rl
     SET status='QUARANTINED'
   WHERE rl.status IN ('FROZEN','RELEASED')
     AND EXISTS (
       SELECT 1
       FROM release_lot_claim rlc
       JOIN claim_revision cr
         ON cr.claim_revision_id=rlc.claim_revision_id
        AND cr.content_hash=rlc.content_hash
       WHERE rlc.lot_id=rl.lot_id
         AND cr.claim_id=NEW.claim_id
         AND (NEW.claim_revision_id IS NULL OR cr.claim_revision_id=NEW.claim_revision_id)
         AND (NEW.content_hash IS NULL OR rlc.content_hash=NEW.content_hash)
     );
  RETURN NEW;
END $$;

CREATE TRIGGER trg_recall_event_release_lot_quarantine
AFTER INSERT ON recall_event
FOR EACH ROW EXECUTE FUNCTION quarantine_release_lots_on_recall_event();

COMMIT;
