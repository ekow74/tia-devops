-- KSP Pack Builder release-lot integrity controls.
-- Gates the first release-lot freeze, not Phase-0 source registration.

-- Membership is mutable only while the lot is CANDIDATE. New or updated
-- membership must point at an exact currently APPROVED, non-recalled revision.
CREATE FUNCTION enforce_release_lot_claim_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  old_lot_status TEXT;
  new_lot_status TEXT;
  rev_status TEXT;
  rev_recalled BOOLEAN;
BEGIN
  IF TG_OP IN ('UPDATE','DELETE') THEN
    SELECT status INTO old_lot_status
    FROM release_lot
    WHERE lot_id=OLD.lot_id
    FOR UPDATE;

    IF old_lot_status IS DISTINCT FROM 'CANDIDATE' THEN
      RAISE EXCEPTION 'release-lot membership is immutable unless lot is CANDIDATE';
    END IF;
  END IF;

  IF TG_OP IN ('INSERT','UPDATE') THEN
    SELECT status INTO new_lot_status
    FROM release_lot
    WHERE lot_id=NEW.lot_id
    FOR UPDATE;

    IF new_lot_status IS DISTINCT FROM 'CANDIDATE' THEN
      RAISE EXCEPTION 'release-lot membership is immutable unless lot is CANDIDATE';
    END IF;

    SELECT status, recalled
      INTO rev_status, rev_recalled
    FROM claim_revision
    WHERE claim_revision_id=NEW.claim_revision_id
      AND content_hash=NEW.content_hash
    FOR SHARE;

    IF NOT FOUND OR rev_status IS DISTINCT FROM 'APPROVED' OR rev_recalled THEN
      RAISE EXCEPTION 'release-lot membership requires exact APPROVED non-recalled claim revision';
    END IF;
  END IF;

  RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
END $$;

CREATE TRIGGER trg_release_lot_claim_mutation
BEFORE INSERT OR UPDATE OR DELETE ON release_lot_claim
FOR EACH ROW EXECUTE FUNCTION enforce_release_lot_claim_mutation();

-- A lot cannot freeze or release empty or with any non-approved/recalled pinned
-- revision. RELEASED must come from FROZEN, keeping the freeze boundary explicit.
CREATE FUNCTION enforce_release_lot_state_transition() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  member_count BIGINT;
BEGIN
  IF NEW.status IN ('FROZEN','RELEASED') AND NEW.status IS DISTINCT FROM OLD.status THEN
    SELECT count(*) INTO member_count
    FROM release_lot_claim
    WHERE lot_id=NEW.lot_id;

    IF member_count=0 THEN
      RAISE EXCEPTION 'release lot cannot freeze or release without pinned claims';
    END IF;

    IF EXISTS (
      SELECT 1
      FROM release_lot_claim rlc
      JOIN claim_revision cr
        ON cr.claim_revision_id=rlc.claim_revision_id
       AND cr.content_hash=rlc.content_hash
      WHERE rlc.lot_id=NEW.lot_id
        AND (cr.status<>'APPROVED' OR cr.recalled)
    ) THEN
      RAISE EXCEPTION 'release lot cannot freeze or release with non-approved, stale, or recalled claim revision';
    END IF;
  END IF;

  IF NEW.status='RELEASED' AND OLD.status<>'FROZEN' THEN
    RAISE EXCEPTION 'release lot must be FROZEN before RELEASED';
  END IF;

  RETURN NEW;
END $$;

CREATE TRIGGER trg_release_lot_state_transition
BEFORE UPDATE OF status ON release_lot
FOR EACH ROW EXECUTE FUNCTION enforce_release_lot_state_transition();

-- Recall/currentness propagation: a previously frozen/released lot cannot
-- remain releasable if one of its exact pinned revisions becomes STALE/RECALLED.
CREATE FUNCTION quarantine_release_lots_on_claim_withdrawal() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status IN ('STALE','RECALLED')
     AND (OLD.status IS DISTINCT FROM NEW.status OR OLD.recalled IS DISTINCT FROM NEW.recalled) THEN
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
