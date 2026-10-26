\set ON_ERROR_STOP on
BEGIN;

INSERT INTO concept(concept_id,domain,canonical_label,status)
VALUES ('pharm:concept:rl_extra','PHARM','Release-lot extra tests','ACTIVE');
INSERT INTO claim(claim_id,domain,concept_id) VALUES
 ('pharm:claim:rl_event','PHARM','pharm:concept:rl_extra'),
 ('pharm:claim:rl_stale','PHARM','pharm:concept:rl_extra'),
 ('pharm:claim:rl_state','PHARM','pharm:concept:rl_extra');
INSERT INTO claim_revision(claim_revision_id,claim_id,revision_number,claim_type,claim_text,context_frame,verification_mode,safety_class,authority_type,content_hash,status) VALUES
 ('pharm:claimrev:rl_event','pharm:claim:rl_event',1,'MECHANISM','event text','{"scope":"UNIVERSAL_DEFINITION"}'::jsonb,'MACHINE_ENTAILED','ROUTINE','TEST',repeat('e',64),'APPROVED'),
 ('pharm:claimrev:rl_stale','pharm:claim:rl_stale',1,'MECHANISM','stale text','{"scope":"UNIVERSAL_DEFINITION"}'::jsonb,'MACHINE_ENTAILED','ROUTINE','TEST',repeat('f',64),'APPROVED'),
 ('pharm:claimrev:rl_state','pharm:claim:rl_state',1,'MECHANISM','state text','{"scope":"UNIVERSAL_DEFINITION"}'::jsonb,'MACHINE_ENTAILED','ROUTINE','TEST',repeat('9',64),'APPROVED');

-- FROZEN -> CANDIDATE is blocked and non-candidate lot deletion is blocked.
INSERT INTO release_lot(lot_id,bundle_sha256,frozen_at,registry_snapshot_at,status)
VALUES ('lot:extra-state',repeat('9',64),now(),now(),'CANDIDATE');
INSERT INTO release_lot_claim VALUES ('lot:extra-state','pharm:claimrev:rl_state',repeat('9',64));
UPDATE release_lot SET status='FROZEN' WHERE lot_id='lot:extra-state';
DO $$ BEGIN
  BEGIN
    UPDATE release_lot SET status='CANDIDATE' WHERE lot_id='lot:extra-state';
    RAISE EXCEPTION 'TEST_FAIL: frozen rollback succeeded';
  EXCEPTION WHEN SQLSTATE 'P0001' THEN
    IF SQLERRM LIKE 'TEST_FAIL:%' OR SQLERRM NOT LIKE 'frozen release lot cannot return to CANDIDATE%' THEN RAISE; END IF;
  END;
  BEGIN
    DELETE FROM release_lot WHERE lot_id='lot:extra-state';
    RAISE EXCEPTION 'TEST_FAIL: frozen lot delete succeeded';
  EXCEPTION WHEN SQLSTATE 'P0001' THEN
    IF SQLERRM LIKE 'TEST_FAIL:%' OR SQLERRM NOT LIKE 'non-candidate release lot cannot be deleted%' THEN RAISE; END IF;
  END;
END $$;

-- Any APPROVED -> non-APPROVED transition quarantines a frozen lot.
INSERT INTO release_lot(lot_id,bundle_sha256,frozen_at,registry_snapshot_at,status)
VALUES ('lot:extra-stale',repeat('8',64),now(),now(),'CANDIDATE');
INSERT INTO release_lot_claim VALUES ('lot:extra-stale','pharm:claimrev:rl_stale',repeat('f',64));
UPDATE release_lot SET status='FROZEN' WHERE lot_id='lot:extra-stale';
UPDATE claim_revision SET status='STALE' WHERE claim_revision_id='pharm:claimrev:rl_stale';
DO $$ DECLARE s TEXT; BEGIN
  SELECT status INTO s FROM release_lot WHERE lot_id='lot:extra-stale';
  IF s <> 'QUARANTINED' THEN RAISE EXCEPTION 'stale quarantine failed: %',s; END IF;
END $$;

-- RecallEvent quarantines independently of claim_revision status mutation.
INSERT INTO release_lot(lot_id,bundle_sha256,frozen_at,registry_snapshot_at,status)
VALUES ('lot:extra-event',repeat('7',64),now(),now(),'CANDIDATE');
INSERT INTO release_lot_claim VALUES ('lot:extra-event','pharm:claimrev:rl_event',repeat('e',64));
UPDATE release_lot SET status='FROZEN' WHERE lot_id='lot:extra-event';
UPDATE release_lot SET status='RELEASED' WHERE lot_id='lot:extra-event';
INSERT INTO recall_event(claim_id,claim_revision_id,content_hash,reason)
VALUES ('pharm:claim:rl_event','pharm:claimrev:rl_event',repeat('e',64),'additional regression');
DO $$ DECLARE s TEXT; BEGIN
  SELECT status INTO s FROM release_lot WHERE lot_id='lot:extra-event';
  IF s <> 'QUARANTINED' THEN RAISE EXCEPTION 'recall_event quarantine failed: %',s; END IF;
END $$;

-- Terminal quarantine cannot be reopened.
DO $$ BEGIN
  BEGIN
    UPDATE release_lot SET status='RELEASED' WHERE lot_id='lot:extra-event';
    RAISE EXCEPTION 'TEST_FAIL: terminal lot reopened';
  EXCEPTION WHEN SQLSTATE 'P0001' THEN
    IF SQLERRM LIKE 'TEST_FAIL:%' OR SQLERRM NOT LIKE 'terminal release lot cannot transition to another status%' THEN RAISE; END IF;
  END;
END $$;

ROLLBACK;
\echo 'PACK_BUILDER_RELEASE_LOT_ADDITIONAL_REGRESSION: PASS'
