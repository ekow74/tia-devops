\set ON_ERROR_STOP on

BEGIN;

INSERT INTO concept(concept_id, domain, canonical_label, status)
VALUES ('pharm:concept:rl', 'PHARM', 'Release-lot test concept', 'ACTIVE');

INSERT INTO claim(claim_id, domain, concept_id) VALUES
  ('pharm:claim:rl_candidate', 'PHARM', 'pharm:concept:rl'),
  ('pharm:claim:rl_approved1', 'PHARM', 'pharm:concept:rl'),
  ('pharm:claim:rl_approved2', 'PHARM', 'pharm:concept:rl'),
  ('pharm:claim:rl_recall', 'PHARM', 'pharm:concept:rl');

INSERT INTO claim_revision(
  claim_revision_id, claim_id, revision_number, claim_type, claim_text,
  context_frame, verification_mode, safety_class, authority_type,
  content_hash, status
) VALUES
('pharm:claimrev:rl_candidate','pharm:claim:rl_candidate',1,'MECHANISM','candidate text','{"scope":"UNIVERSAL_DEFINITION"}'::jsonb,'MACHINE_ENTAILED','ROUTINE','TEST',repeat('a',64),'CANDIDATE'),
('pharm:claimrev:rl_approved1','pharm:claim:rl_approved1',1,'MECHANISM','approved text one','{"scope":"UNIVERSAL_DEFINITION"}'::jsonb,'MACHINE_ENTAILED','ROUTINE','TEST',repeat('b',64),'APPROVED'),
('pharm:claimrev:rl_approved2','pharm:claim:rl_approved2',1,'MECHANISM','approved text two','{"scope":"UNIVERSAL_DEFINITION"}'::jsonb,'MACHINE_ENTAILED','ROUTINE','TEST',repeat('c',64),'APPROVED'),
('pharm:claimrev:rl_recall','pharm:claim:rl_recall',1,'MECHANISM','approved recall text','{"scope":"UNIVERSAL_DEFINITION"}'::jsonb,'MACHINE_ENTAILED','ROUTINE','TEST',repeat('d',64),'APPROVED');

-- RL1: unapproved revision cannot enter a candidate lot.
INSERT INTO release_lot(lot_id,bundle_sha256,frozen_at,registry_snapshot_at,status)
VALUES ('lot:rl1', repeat('1',64), NULL, now(), 'CANDIDATE');
DO $$
BEGIN
  BEGIN
    INSERT INTO release_lot_claim(lot_id,claim_revision_id,content_hash)
    VALUES ('lot:rl1','pharm:claimrev:rl_candidate',repeat('a',64));
    RAISE EXCEPTION 'TEST_FAIL: RL1 unexpectedly succeeded';
  EXCEPTION WHEN SQLSTATE 'P0001' THEN
    IF SQLERRM <> 'release-lot membership requires exact APPROVED non-recalled claim revision' THEN RAISE; END IF;
  END;
END $$;

-- Empty lot cannot freeze.
DO $$
BEGIN
  BEGIN
    UPDATE release_lot SET status='FROZEN' WHERE lot_id='lot:rl1';
    RAISE EXCEPTION 'TEST_FAIL: empty lot freeze unexpectedly succeeded';
  EXCEPTION WHEN SQLSTATE 'P0001' THEN
    IF SQLERRM <> 'release lot cannot freeze or release without pinned claims' THEN RAISE; END IF;
  END;
END $$;

-- Positive path: APPROVED exact revision -> FROZEN -> RELEASED.
INSERT INTO release_lot(lot_id,bundle_sha256,frozen_at,registry_snapshot_at,status)
VALUES ('lot:positive', repeat('2',64), NULL, now(), 'CANDIDATE');
INSERT INTO release_lot_claim(lot_id,claim_revision_id,content_hash)
VALUES ('lot:positive','pharm:claimrev:rl_approved1',repeat('b',64));
UPDATE release_lot SET status='FROZEN' WHERE lot_id='lot:positive';
DO $$ DECLARE frozen_at_value TIMESTAMPTZ; BEGIN
  SELECT frozen_at INTO frozen_at_value FROM release_lot WHERE lot_id='lot:positive';
  IF frozen_at_value IS NULL OR frozen_at_value > clock_timestamp() THEN
    RAISE EXCEPTION 'freeze timestamp was not set at freeze';
  END IF;
END $$;
UPDATE release_lot SET status='RELEASED' WHERE lot_id='lot:positive';
DO $$ DECLARE s TEXT; BEGIN
  SELECT status INTO s FROM release_lot WHERE lot_id='lot:positive';
  IF s <> 'RELEASED' THEN RAISE EXCEPTION 'positive path failed: %', s; END IF;
END $$;

-- RL5: released membership cannot be removed.
DO $$
BEGIN
  BEGIN
    DELETE FROM release_lot_claim WHERE lot_id='lot:positive' AND claim_revision_id='pharm:claimrev:rl_approved1';
    RAISE EXCEPTION 'TEST_FAIL: RL5 unexpectedly succeeded';
  EXCEPTION WHEN SQLSTATE 'P0001' THEN
    IF SQLERRM <> 'release-lot membership is immutable unless lot is CANDIDATE' THEN RAISE; END IF;
  END;
END $$;

-- Released membership also cannot be expanded.
DO $$
BEGIN
  BEGIN
    INSERT INTO release_lot_claim(lot_id,claim_revision_id,content_hash)
    VALUES ('lot:positive','pharm:claimrev:rl_approved2',repeat('c',64));
    RAISE EXCEPTION 'TEST_FAIL: released-lot insert unexpectedly succeeded';
  EXCEPTION WHEN SQLSTATE 'P0001' THEN
    IF SQLERRM <> 'release-lot membership is immutable unless lot is CANDIDATE' THEN RAISE; END IF;
  END;
END $$;

-- RL2: a pinned revision that is no longer APPROVED blocks freeze.
INSERT INTO release_lot(lot_id,bundle_sha256,frozen_at,registry_snapshot_at,status)
VALUES ('lot:rl2', repeat('3',64), NULL, now(), 'CANDIDATE');
INSERT INTO release_lot_claim(lot_id,claim_revision_id,content_hash)
VALUES ('lot:rl2','pharm:claimrev:rl_approved2',repeat('c',64));
UPDATE claim_revision SET status='SUPPORTED' WHERE claim_revision_id='pharm:claimrev:rl_approved2';
DO $$
BEGIN
  BEGIN
    UPDATE release_lot SET status='FROZEN' WHERE lot_id='lot:rl2';
    RAISE EXCEPTION 'TEST_FAIL: RL2 unexpectedly succeeded';
  EXCEPTION WHEN SQLSTATE 'P0001' THEN
    IF SQLERRM <> 'release lot cannot freeze or release with non-approved, stale, or recalled claim revision' THEN RAISE; END IF;
  END;
END $$;
UPDATE claim_revision SET status='APPROVED' WHERE claim_revision_id='pharm:claimrev:rl_approved2';

-- State machine requires a freeze before release.
INSERT INTO release_lot(lot_id,bundle_sha256,frozen_at,registry_snapshot_at,status)
VALUES ('lot:state', repeat('4',64), NULL, now(), 'CANDIDATE');
INSERT INTO release_lot_claim(lot_id,claim_revision_id,content_hash)
VALUES ('lot:state','pharm:claimrev:rl_approved2',repeat('c',64));
DO $$
BEGIN
  BEGIN
    UPDATE release_lot SET status='RELEASED' WHERE lot_id='lot:state';
    RAISE EXCEPTION 'TEST_FAIL: direct release unexpectedly succeeded';
  EXCEPTION WHEN SQLSTATE 'P0001' THEN
    IF SQLERRM <> 'release lot must be FROZEN before RELEASED' THEN RAISE; END IF;
  END;
END $$;

-- RL4 variant: recalled revision cannot be newly pinned.
UPDATE claim_revision SET status='RECALLED', recalled=true WHERE claim_revision_id='pharm:claimrev:rl_candidate';
INSERT INTO release_lot(lot_id,bundle_sha256,frozen_at,registry_snapshot_at,status)
VALUES ('lot:rl4', repeat('5',64), NULL, now(), 'CANDIDATE');
DO $$
BEGIN
  BEGIN
    INSERT INTO release_lot_claim(lot_id,claim_revision_id,content_hash)
    VALUES ('lot:rl4','pharm:claimrev:rl_candidate',repeat('a',64));
    RAISE EXCEPTION 'TEST_FAIL: RL4 unexpectedly succeeded';
  EXCEPTION WHEN SQLSTATE 'P0001' THEN
    IF SQLERRM <> 'release-lot membership requires exact APPROVED non-recalled claim revision' THEN RAISE; END IF;
  END;
END $$;

-- Recall propagation: a released lot becomes QUARANTINED in the same transaction.
INSERT INTO release_lot(lot_id,bundle_sha256,frozen_at,registry_snapshot_at,status)
VALUES ('lot:recall', repeat('6',64), NULL, now(), 'CANDIDATE');
INSERT INTO release_lot_claim(lot_id,claim_revision_id,content_hash)
VALUES ('lot:recall','pharm:claimrev:rl_recall',repeat('d',64));
UPDATE release_lot SET status='FROZEN' WHERE lot_id='lot:recall';
UPDATE release_lot SET status='RELEASED' WHERE lot_id='lot:recall';
UPDATE claim_revision SET status='RECALLED', recalled=true WHERE claim_revision_id='pharm:claimrev:rl_recall';
DO $$ DECLARE s TEXT; BEGIN
  SELECT status INTO s FROM release_lot WHERE lot_id='lot:recall';
  IF s <> 'QUARANTINED' THEN RAISE EXCEPTION 'recall propagation failed: %', s; END IF;
END $$;

ROLLBACK;
\echo 'PACK_BUILDER_RELEASE_LOT_REGRESSION: PASS'
