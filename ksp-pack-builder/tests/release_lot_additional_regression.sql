\set ON_ERROR_STOP on

BEGIN;

INSERT INTO concept(concept_id,domain,canonical_label,status)
VALUES ('pharm:concept:rl_s02','PHARM','Release-lot S02 regression','ACTIVE');
INSERT INTO claim(claim_id,domain,concept_id) VALUES
  ('pharm:claim:rl_matrix','PHARM','pharm:concept:rl_s02'),
  ('pharm:claim:rl_recall_pin','PHARM','pharm:concept:rl_s02'),
  ('pharm:claim:rl_recall_freeze','PHARM','pharm:concept:rl_s02'),
  ('pharm:claim:rl_recall_freeze2','PHARM','pharm:concept:rl_s02'),
  ('pharm:claim:rl_recall_hash','PHARM','pharm:concept:rl_s02'),
  ('pharm:claim:rl_recall_mismatch','PHARM','pharm:concept:rl_s02'),
  ('pharm:claim:rl_recall_unmatched','PHARM','pharm:concept:rl_s02'),
  ('pharm:claim:rl_stale','PHARM','pharm:concept:rl_s02'),
  ('pharm:claim:rl_quarantine','PHARM','pharm:concept:rl_s02');
INSERT INTO claim_revision(
  claim_revision_id,claim_id,revision_number,claim_type,claim_text,context_frame,
  verification_mode,safety_class,authority_type,content_hash,status
) VALUES
  ('pharm:claimrev:rl_matrix','pharm:claim:rl_matrix',1,'MECHANISM','matrix text','{"scope":"UNIVERSAL_DEFINITION"}','MACHINE_ENTAILED','ROUTINE','TEST',repeat('1',64),'APPROVED'),
  ('pharm:claimrev:rl_recall_pin','pharm:claim:rl_recall_pin',1,'MECHANISM','recall pin text','{"scope":"UNIVERSAL_DEFINITION"}','MACHINE_ENTAILED','ROUTINE','TEST',repeat('2',64),'APPROVED'),
  ('pharm:claimrev:rl_recall_freeze','pharm:claim:rl_recall_freeze',1,'MECHANISM','recall freeze text','{"scope":"UNIVERSAL_DEFINITION"}','MACHINE_ENTAILED','ROUTINE','TEST',repeat('3',64),'APPROVED'),
  ('pharm:claimrev:rl_recall_freeze2','pharm:claim:rl_recall_freeze2',1,'MECHANISM','recall freeze text two','{"scope":"UNIVERSAL_DEFINITION"}','MACHINE_ENTAILED','ROUTINE','TEST',repeat('6',64),'APPROVED'),
  ('pharm:claimrev:rl_recall_hash','pharm:claim:rl_recall_hash',1,'MECHANISM','hash-only recall text','{"scope":"UNIVERSAL_DEFINITION"}','MACHINE_ENTAILED','ROUTINE','TEST',repeat('7',64),'APPROVED'),
  ('pharm:claimrev:rl_stale','pharm:claim:rl_stale',1,'MECHANISM','stale text','{"scope":"UNIVERSAL_DEFINITION"}','MACHINE_ENTAILED','ROUTINE','TEST',repeat('4',64),'APPROVED'),
  ('pharm:claimrev:rl_quarantine','pharm:claim:rl_quarantine',1,'MECHANISM','quarantine text','{"scope":"UNIVERSAL_DEFINITION"}','MACHINE_ENTAILED','ROUTINE','TEST',repeat('5',64),'APPROVED');

-- Exercise all 25 source/target pairs in the explicit five-state machine.
DO $$
DECLARE
  source_status TEXT;
  target_status TEXT;
  lot_id_value TEXT;
  actual_status TEXT;
  expected_message TEXT;
  transition_allowed BOOLEAN;
  raised BOOLEAN;
  status_values TEXT[] := ARRAY['CANDIDATE','FROZEN','RELEASED','QUARANTINED','RECALLED'];
BEGIN
  FOREACH source_status IN ARRAY status_values LOOP
    FOREACH target_status IN ARRAY status_values LOOP
      lot_id_value := 'lot:matrix:' || source_status || ':' || target_status;
      INSERT INTO release_lot(lot_id,bundle_sha256,frozen_at,registry_snapshot_at,status)
      VALUES (lot_id_value,repeat('a',64),NULL,clock_timestamp(),'CANDIDATE');
      INSERT INTO release_lot_claim(lot_id,claim_revision_id,content_hash)
      VALUES (lot_id_value,'pharm:claimrev:rl_matrix',repeat('1',64));

      IF source_status IN ('FROZEN','RELEASED') THEN
        UPDATE release_lot SET status='FROZEN' WHERE lot_id=lot_id_value;
        IF source_status='RELEASED' THEN
          UPDATE release_lot SET status='RELEASED' WHERE lot_id=lot_id_value;
        END IF;
      ELSIF source_status IN ('QUARANTINED','RECALLED') THEN
        UPDATE release_lot SET status=source_status WHERE lot_id=lot_id_value;
      END IF;

      transition_allowed :=
        source_status=target_status OR
        (source_status='CANDIDATE' AND target_status IN ('FROZEN','QUARANTINED','RECALLED')) OR
        (source_status='FROZEN' AND target_status IN ('RELEASED','QUARANTINED','RECALLED')) OR
        (source_status='RELEASED' AND target_status IN ('QUARANTINED','RECALLED'));
      expected_message := CASE
        WHEN source_status IN ('QUARANTINED','RECALLED') AND source_status<>target_status
          THEN 'terminal release lot cannot transition to another status'
        WHEN source_status='FROZEN' AND target_status='CANDIDATE'
          THEN 'frozen release lot cannot return to CANDIDATE'
        WHEN source_status='CANDIDATE' AND target_status='RELEASED'
          THEN 'release lot must be FROZEN before RELEASED'
        WHEN source_status='RELEASED' AND target_status IN ('CANDIDATE','FROZEN')
          THEN 'released release lot cannot transition to CANDIDATE or FROZEN'
        ELSE 'invalid release lot status transition'
      END;

      raised := false;
      BEGIN
        UPDATE release_lot SET status=target_status WHERE lot_id=lot_id_value;
      EXCEPTION WHEN SQLSTATE 'P0001' THEN
        raised := true;
        IF transition_allowed OR SQLERRM<>expected_message THEN
          RAISE EXCEPTION 'status matrix mismatch % -> %: %',
            source_status,target_status,SQLERRM;
        END IF;
      END;

      IF transition_allowed=raised THEN
        RAISE EXCEPTION 'status matrix outcome mismatch % -> %',
          source_status,target_status;
      END IF;
      SELECT status INTO actual_status FROM release_lot WHERE lot_id=lot_id_value;
      IF actual_status IS DISTINCT FROM
         (CASE WHEN transition_allowed THEN target_status ELSE source_status END) THEN
        RAISE EXCEPTION 'status matrix persisted wrong state % -> %: %',
          source_status,target_status,actual_status;
      END IF;
    END LOOP;
  END LOOP;
END $$;

-- Identity fields are immutable after both FROZEN and RELEASED.
DO $$
DECLARE
  final_status TEXT;
  identity_column TEXT;
  assignment TEXT;
  lot_id_value TEXT;
  identity_columns TEXT[] := ARRAY['lot_id','bundle_sha256','frozen_at','registry_snapshot_at'];
BEGIN
  FOREACH final_status IN ARRAY ARRAY['FROZEN','RELEASED'] LOOP
    lot_id_value := 'lot:identity:' || final_status;
    INSERT INTO release_lot(lot_id,bundle_sha256,frozen_at,registry_snapshot_at,status)
    VALUES (lot_id_value,repeat('b',64),NULL,clock_timestamp(),'CANDIDATE');
    INSERT INTO release_lot_claim(lot_id,claim_revision_id,content_hash)
    VALUES (lot_id_value,'pharm:claimrev:rl_matrix',repeat('1',64));
    PERFORM clock_timestamp();
    UPDATE release_lot SET status='FROZEN' WHERE lot_id=lot_id_value;
    IF final_status='RELEASED' THEN
      UPDATE release_lot SET status='RELEASED' WHERE lot_id=lot_id_value;
    END IF;

    FOREACH identity_column IN ARRAY identity_columns LOOP
      assignment := CASE identity_column
        WHEN 'lot_id' THEN format('lot_id=lot_id || %L','-changed')
        WHEN 'bundle_sha256' THEN format('bundle_sha256=%L',repeat('f',64))
        WHEN 'frozen_at' THEN 'frozen_at=frozen_at + interval ''1 second'''
        WHEN 'registry_snapshot_at' THEN 'registry_snapshot_at=registry_snapshot_at + interval ''1 second'''
      END;
      BEGIN
        EXECUTE format('UPDATE release_lot SET %s WHERE lot_id=%L',assignment,lot_id_value);
        RAISE EXCEPTION 'TEST_FAIL: % identity changed for %',identity_column,final_status;
      EXCEPTION WHEN SQLSTATE 'P0001' THEN
        IF SQLERRM<>'non-candidate release lot identity is immutable' THEN RAISE; END IF;
      END;
    END LOOP;
  END LOOP;
END $$;

-- Freeze timestamps are assigned by the state transition, not lot creation.
DO $$
DECLARE
  started_at TIMESTAMPTZ := clock_timestamp();
  frozen_at_value TIMESTAMPTZ;
BEGIN
  INSERT INTO release_lot(lot_id,bundle_sha256,frozen_at,registry_snapshot_at,status)
  VALUES ('lot:freeze-time',repeat('c',64),NULL,clock_timestamp(),'CANDIDATE');
  INSERT INTO release_lot_claim(lot_id,claim_revision_id,content_hash)
  VALUES ('lot:freeze-time','pharm:claimrev:rl_matrix',repeat('1',64));
  UPDATE release_lot SET status='FROZEN' WHERE lot_id='lot:freeze-time';
  SELECT frozen_at INTO frozen_at_value FROM release_lot WHERE lot_id='lot:freeze-time';
  IF frozen_at_value<started_at OR frozen_at_value>clock_timestamp() THEN
    RAISE EXCEPTION 'frozen_at does not reflect the freeze operation';
  END IF;
END $$;

-- A candidate cannot claim a freeze timestamp, and non-candidate creation is forbidden.
DO $$ BEGIN
  INSERT INTO release_lot(lot_id,bundle_sha256,frozen_at,registry_snapshot_at,status)
  VALUES ('lot:candidate-time',repeat('c',64),NULL,clock_timestamp(),'CANDIDATE');
  BEGIN
    UPDATE release_lot SET frozen_at=clock_timestamp()
    WHERE lot_id='lot:candidate-time';
    RAISE EXCEPTION 'TEST_FAIL: candidate timestamp mutation succeeded';
  EXCEPTION WHEN SQLSTATE 'P0001' THEN
    IF SQLERRM<>'frozen_at is set only when release lot freezes' THEN RAISE; END IF;
  END;
  BEGIN
    INSERT INTO release_lot(lot_id,bundle_sha256,frozen_at,registry_snapshot_at,status)
    VALUES ('lot:insert-frozen',repeat('c',64),clock_timestamp(),clock_timestamp(),'FROZEN');
    RAISE EXCEPTION 'TEST_FAIL: non-candidate lot creation succeeded';
  EXCEPTION WHEN SQLSTATE 'P0001' THEN
    IF SQLERRM<>'release lot must be created as CANDIDATE without a freeze timestamp' THEN RAISE; END IF;
  END;
END $$;

-- RL-REC-1: an exact RecallEvent blocks pinning while the revision stays APPROVED.
INSERT INTO recall_event(claim_id,claim_revision_id,content_hash,reason)
VALUES ('pharm:claim:rl_recall_pin','pharm:claimrev:rl_recall_pin',repeat('2',64),'RL-REC-1 exact');
-- Completion is one-time audit metadata; it never cancels the authoritative recall.
DO $$
DECLARE
  event_id BIGINT;
  completed_value TIMESTAMPTZ;
  identity_update TEXT;
  identity_updates TEXT[] := ARRAY[
    'recall_id=recall_id + 100000',
    'claim_id=''pharm:claim:rl_recall_hash''',
    'claim_revision_id=''pharm:claimrev:rl_recall_freeze2''',
    'content_hash=repeat(''f'',64)',
    'reason=reason || '' changed''',
    'initiated_at=initiated_at + interval ''1 second'''
  ];
BEGIN
  SELECT recall_id INTO event_id
  FROM recall_event
  WHERE claim_id='pharm:claim:rl_recall_pin';

  UPDATE recall_event SET completed_at=clock_timestamp()
  WHERE recall_id=event_id;
  SELECT completed_at INTO completed_value
  FROM recall_event WHERE recall_id=event_id;
  IF completed_value IS NULL THEN
    RAISE EXCEPTION 'RecallEvent completion timestamp was not set';
  END IF;
  UPDATE recall_event SET completed_at=completed_at
  WHERE recall_id=event_id;

  FOREACH identity_update IN ARRAY identity_updates LOOP
    BEGIN
      EXECUTE format('UPDATE recall_event SET %s WHERE recall_id=%s',identity_update,event_id);
      RAISE EXCEPTION 'TEST_FAIL: RecallEvent identity changed: %',identity_update;
    EXCEPTION WHEN SQLSTATE 'P0001' THEN
      IF SQLERRM<>'RecallEvent is append-only; only completed_at may be set once' THEN RAISE; END IF;
    END;
  END LOOP;

  BEGIN
    UPDATE recall_event SET completed_at=NULL WHERE recall_id=event_id;
    RAISE EXCEPTION 'TEST_FAIL: completed_at was cleared';
  EXCEPTION WHEN SQLSTATE 'P0001' THEN
    IF SQLERRM<>'RecallEvent is append-only; only completed_at may be set once' THEN RAISE; END IF;
  END;
  BEGIN
    UPDATE recall_event SET completed_at=completed_value + interval '1 second'
    WHERE recall_id=event_id;
    RAISE EXCEPTION 'TEST_FAIL: completed_at was changed';
  EXCEPTION WHEN SQLSTATE 'P0001' THEN
    IF SQLERRM<>'RecallEvent is append-only; only completed_at may be set once' THEN RAISE; END IF;
  END;
  BEGIN
    DELETE FROM recall_event WHERE recall_id=event_id;
    RAISE EXCEPTION 'TEST_FAIL: RecallEvent was deleted';
  EXCEPTION WHEN SQLSTATE 'P0001' THEN
    IF SQLERRM<>'RecallEvent is append-only; only completed_at may be set once' THEN RAISE; END IF;
  END;
END $$;
INSERT INTO release_lot(lot_id,bundle_sha256,frozen_at,registry_snapshot_at,status)
VALUES ('lot:recall-pin-exact',repeat('d',64),NULL,clock_timestamp(),'CANDIDATE');
DO $$ BEGIN
  BEGIN
    INSERT INTO release_lot_claim(lot_id,claim_revision_id,content_hash)
    VALUES ('lot:recall-pin-exact','pharm:claimrev:rl_recall_pin',repeat('2',64));
    RAISE EXCEPTION 'TEST_FAIL: matching recall permitted pinning';
  EXCEPTION WHEN SQLSTATE 'P0001' THEN
    IF SQLERRM<>'release-lot membership requires exact APPROVED non-recalled claim revision' THEN RAISE; END IF;
  END;
END $$;
DO $$ DECLARE s TEXT; BEGIN
  SELECT status INTO s FROM claim_revision WHERE claim_revision_id='pharm:claimrev:rl_recall_pin';
  IF s<>'APPROVED' THEN RAISE EXCEPTION 'RecallEvent unexpectedly changed revision status'; END IF;
END $$;

-- A claim-wide RecallEvent (NULL revision and hash) has the same fail-closed effect.
INSERT INTO recall_event(claim_id,claim_revision_id,content_hash,reason)
VALUES ('pharm:claim:rl_recall_freeze',NULL,NULL,'RL-REC-1 claim-wide');
INSERT INTO release_lot(lot_id,bundle_sha256,frozen_at,registry_snapshot_at,status)
VALUES ('lot:recall-pin-wide',repeat('e',64),NULL,clock_timestamp(),'CANDIDATE');
DO $$ BEGIN
  BEGIN
    INSERT INTO release_lot_claim(lot_id,claim_revision_id,content_hash)
    VALUES ('lot:recall-pin-wide','pharm:claimrev:rl_recall_freeze',repeat('3',64));
    RAISE EXCEPTION 'TEST_FAIL: claim-wide recall permitted pinning';
  EXCEPTION WHEN SQLSTATE 'P0001' THEN
    IF SQLERRM<>'release-lot membership requires exact APPROVED non-recalled claim revision' THEN RAISE; END IF;
  END;
END $$;

-- A hash-only recall is valid if it matches an existing revision.
INSERT INTO recall_event(claim_id,claim_revision_id,content_hash,reason)
VALUES ('pharm:claim:rl_recall_hash',NULL,repeat('7',64),'RL-REC-1 hash-only');
INSERT INTO release_lot(lot_id,bundle_sha256,frozen_at,registry_snapshot_at,status)
VALUES ('lot:recall-pin-hash',repeat('e',64),NULL,clock_timestamp(),'CANDIDATE');
DO $$ BEGIN
  BEGIN
    INSERT INTO release_lot_claim(lot_id,claim_revision_id,content_hash)
    VALUES ('lot:recall-pin-hash','pharm:claimrev:rl_recall_hash',repeat('7',64));
    RAISE EXCEPTION 'TEST_FAIL: matching hash-only recall permitted pinning';
  EXCEPTION WHEN SQLSTATE 'P0001' THEN
    IF SQLERRM<>'release-lot membership requires exact APPROVED non-recalled claim revision' THEN RAISE; END IF;
  END;
END $$;

-- MATCH SIMPLE skips a composite FK when content_hash is NULL; matching must
-- reject a revision belonging to another claim, and reject events with no target.
DO $$ BEGIN
  BEGIN
    INSERT INTO recall_event(claim_id,claim_revision_id,content_hash,reason)
    VALUES ('pharm:claim:rl_recall_mismatch','pharm:claimrev:rl_recall_freeze2',NULL,'N2 mismatched claim/revision');
    RAISE EXCEPTION 'TEST_FAIL: mismatched RecallEvent target was accepted';
  EXCEPTION WHEN SQLSTATE 'P0001' THEN
    IF SQLERRM<>'RecallEvent matches no claim revision' THEN RAISE; END IF;
  END;
  BEGIN
    INSERT INTO recall_event(claim_id,claim_revision_id,content_hash,reason)
    VALUES ('pharm:claim:rl_recall_unmatched',NULL,repeat('f',64),'N2 unmatched hash-only');
    RAISE EXCEPTION 'TEST_FAIL: unmatched hash-only RecallEvent was accepted';
  EXCEPTION WHEN SQLSTATE 'P0001' THEN
    IF SQLERRM<>'RecallEvent matches no claim revision' THEN RAISE; END IF;
  END;
  BEGIN
    INSERT INTO recall_event(claim_id,claim_revision_id,content_hash,reason)
    VALUES ('pharm:claim:rl_recall_unmatched',NULL,NULL,'N2 claim-wide with no revisions');
    RAISE EXCEPTION 'TEST_FAIL: unmatched claim-wide RecallEvent was accepted';
  EXCEPTION WHEN SQLSTATE 'P0001' THEN
    IF SQLERRM<>'RecallEvent matches no claim revision' THEN RAISE; END IF;
  END;
END $$;

-- RL-REC-2: a matching RecallEvent added after pinning blocks freeze.
INSERT INTO release_lot(lot_id,bundle_sha256,frozen_at,registry_snapshot_at,status)
VALUES ('lot:recall-freeze',repeat('6',64),NULL,clock_timestamp(),'CANDIDATE');
INSERT INTO release_lot_claim(lot_id,claim_revision_id,content_hash)
VALUES ('lot:recall-freeze','pharm:claimrev:rl_recall_freeze2',repeat('6',64));
INSERT INTO recall_event(claim_id,claim_revision_id,content_hash,reason)
VALUES ('pharm:claim:rl_recall_freeze2','pharm:claimrev:rl_recall_freeze2',repeat('6',64),'RL-REC-2');
DO $$ BEGIN
  BEGIN
    UPDATE release_lot SET status='FROZEN' WHERE lot_id='lot:recall-freeze';
    RAISE EXCEPTION 'TEST_FAIL: matching recall permitted freeze';
  EXCEPTION WHEN SQLSTATE 'P0001' THEN
    IF SQLERRM<>'release lot cannot freeze or release with non-approved, stale, or recalled claim revision' THEN RAISE; END IF;
  END;
END $$;
DO $$ DECLARE s TEXT; BEGIN
  SELECT status INTO s FROM claim_revision WHERE claim_revision_id='pharm:claimrev:rl_recall_freeze2';
  IF s<>'APPROVED' THEN RAISE EXCEPTION 'RecallEvent unexpectedly changed revision status'; END IF;
END $$;

-- TRUNCATE cannot bypass row-level durability guards.
DO $$ BEGIN
  BEGIN
    TRUNCATE release_lot_claim;
    RAISE EXCEPTION 'TEST_FAIL: release_lot_claim truncated';
  EXCEPTION WHEN SQLSTATE 'P0001' THEN
    IF SQLERRM<>'TRUNCATE is prohibited on durable release-lot tables' THEN RAISE; END IF;
  END;
  BEGIN
    TRUNCATE release_lot CASCADE;
    RAISE EXCEPTION 'TEST_FAIL: release_lot truncated';
  EXCEPTION WHEN SQLSTATE 'P0001' THEN
    IF SQLERRM<>'TRUNCATE is prohibited on durable release-lot tables' THEN RAISE; END IF;
  END;
  BEGIN
    TRUNCATE recall_event;
    RAISE EXCEPTION 'TEST_FAIL: recall_event truncated';
  EXCEPTION WHEN SQLSTATE 'P0001' THEN
    IF SQLERRM<>'TRUNCATE is prohibited on RecallEvent records' THEN RAISE; END IF;
  END;
END $$;

-- Approval withdrawal still quarantines a frozen lot; RecallEvent quarantine is independent.
INSERT INTO release_lot(lot_id,bundle_sha256,frozen_at,registry_snapshot_at,status)
VALUES ('lot:stale-quarantine',repeat('7',64),NULL,clock_timestamp(),'CANDIDATE');
INSERT INTO release_lot_claim(lot_id,claim_revision_id,content_hash)
VALUES ('lot:stale-quarantine','pharm:claimrev:rl_stale',repeat('4',64));
UPDATE release_lot SET status='FROZEN' WHERE lot_id='lot:stale-quarantine';
UPDATE claim_revision SET status='STALE' WHERE claim_revision_id='pharm:claimrev:rl_stale';
DO $$ DECLARE s TEXT; BEGIN
  SELECT status INTO s FROM release_lot WHERE lot_id='lot:stale-quarantine';
  IF s<>'QUARANTINED' THEN RAISE EXCEPTION 'stale quarantine failed: %',s; END IF;
END $$;

INSERT INTO release_lot(lot_id,bundle_sha256,frozen_at,registry_snapshot_at,status)
VALUES ('lot:recall-quarantine',repeat('8',64),NULL,clock_timestamp(),'CANDIDATE');
INSERT INTO release_lot_claim(lot_id,claim_revision_id,content_hash)
VALUES ('lot:recall-quarantine','pharm:claimrev:rl_quarantine',repeat('5',64));
UPDATE release_lot SET status='FROZEN' WHERE lot_id='lot:recall-quarantine';
UPDATE release_lot SET status='RELEASED' WHERE lot_id='lot:recall-quarantine';
INSERT INTO recall_event(claim_id,claim_revision_id,content_hash,reason)
VALUES ('pharm:claim:rl_quarantine','pharm:claimrev:rl_quarantine',repeat('5',64),'quarantine propagation');
DO $$ DECLARE s TEXT; BEGIN
  SELECT status INTO s FROM release_lot WHERE lot_id='lot:recall-quarantine';
  IF s<>'QUARANTINED' THEN RAISE EXCEPTION 'RecallEvent quarantine failed: %',s; END IF;
END $$;

ROLLBACK;
\echo 'PACK_BUILDER_RELEASE_LOT_ADDITIONAL_REGRESSION: PASS'
