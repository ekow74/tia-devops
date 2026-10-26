#!/usr/bin/env bash
set -euo pipefail
: "${DATABASE_URL:?Set DATABASE_URL to disposable PostgreSQL 18 database}"

psqlq() { psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -X "$@"; }

# Scenario A: freeze gets the revision SHARE lock first. Withdrawal waits, then
# proceeds after freeze commit and must quarantine the frozen lot in its transaction.
psqlq <<'SQL'
INSERT INTO concept(concept_id,domain,canonical_label,status)
VALUES ('pharm:concept:rl_concurrency','PHARM','RL concurrency','ACTIVE')
ON CONFLICT (concept_id) DO NOTHING;
INSERT INTO claim(claim_id,domain,concept_id)
VALUES ('pharm:claim:rl_conca','PHARM','pharm:concept:rl_concurrency');
INSERT INTO claim_revision(claim_revision_id,claim_id,revision_number,claim_type,claim_text,context_frame,verification_mode,safety_class,authority_type,content_hash,status)
VALUES ('pharm:claimrev:rl_conca','pharm:claim:rl_conca',1,'MECHANISM','concurrency a','{"scope":"UNIVERSAL_DEFINITION"}'::jsonb,'MACHINE_ENTAILED','ROUTINE','TEST',repeat('1',64),'APPROVED');
INSERT INTO release_lot(lot_id,bundle_sha256,frozen_at,registry_snapshot_at,status)
VALUES ('lot:conca',repeat('a',64),now(),now(),'CANDIDATE');
INSERT INTO release_lot_claim(lot_id,claim_revision_id,content_hash)
VALUES ('lot:conca','pharm:claimrev:rl_conca',repeat('1',64));
SQL

psqlq > /tmp/rl-freeze-first.log 2>&1 <<'SQL' &
BEGIN;
UPDATE release_lot SET status='FROZEN' WHERE lot_id='lot:conca';
SELECT pg_sleep(2);
COMMIT;
SQL
freeze_pid=$!
sleep 0.35
psqlq > /tmp/rl-withdraw-second.log 2>&1 <<'SQL' &
BEGIN;
UPDATE claim_revision SET status='SUPPORTED' WHERE claim_revision_id='pharm:claimrev:rl_conca';
COMMIT;
SQL
withdraw_pid=$!
wait "$freeze_pid"
wait "$withdraw_pid"

status_a=$(psqlq -Atc "SELECT status FROM release_lot WHERE lot_id='lot:conca'")
[[ "$status_a" == "QUARANTINED" ]] || {
  echo "RL-CONC-A FAIL: expected QUARANTINED, got $status_a" >&2
  cat /tmp/rl-freeze-first.log /tmp/rl-withdraw-second.log >&2
  exit 1
}
echo 'RL-CONC-A PASS: freeze-first then withdrawal => QUARANTINED'

# Scenario B: withdrawal gets the claim row lock first. Freeze waits, then must
# observe the non-APPROVED status and fail with the exact Pack Builder trigger.
psqlq <<'SQL'
INSERT INTO claim(claim_id,domain,concept_id)
VALUES ('pharm:claim:rl_concb','PHARM','pharm:concept:rl_concurrency');
INSERT INTO claim_revision(claim_revision_id,claim_id,revision_number,claim_type,claim_text,context_frame,verification_mode,safety_class,authority_type,content_hash,status)
VALUES ('pharm:claimrev:rl_concb','pharm:claim:rl_concb',1,'MECHANISM','concurrency b','{"scope":"UNIVERSAL_DEFINITION"}'::jsonb,'MACHINE_ENTAILED','ROUTINE','TEST',repeat('2',64),'APPROVED');
INSERT INTO release_lot(lot_id,bundle_sha256,frozen_at,registry_snapshot_at,status)
VALUES ('lot:concb',repeat('b',64),now(),now(),'CANDIDATE');
INSERT INTO release_lot_claim(lot_id,claim_revision_id,content_hash)
VALUES ('lot:concb','pharm:claimrev:rl_concb',repeat('2',64));
SQL

psqlq > /tmp/rl-withdraw-first.log 2>&1 <<'SQL' &
BEGIN;
UPDATE claim_revision SET status='SUPPORTED' WHERE claim_revision_id='pharm:claimrev:rl_concb';
SELECT pg_sleep(2);
COMMIT;
SQL
withdraw_first_pid=$!
sleep 0.35
if psqlq > /tmp/rl-freeze-second.log 2>&1 -c "UPDATE release_lot SET status='FROZEN' WHERE lot_id='lot:concb'"; then
  echo 'RL-CONC-B FAIL: freeze unexpectedly succeeded after approval withdrawal' >&2
  wait "$withdraw_first_pid"
  exit 1
else
  grep -q 'release lot cannot freeze or release with non-approved, stale, or recalled claim revision' /tmp/rl-freeze-second.log || {
    echo 'RL-CONC-B FAIL: wrong freeze rejection' >&2
    cat /tmp/rl-freeze-second.log >&2
    wait "$withdraw_first_pid"
    exit 1
  }
fi
wait "$withdraw_first_pid"

status_b=$(psqlq -Atc "SELECT status FROM release_lot WHERE lot_id='lot:concb'")
claim_b=$(psqlq -Atc "SELECT status FROM claim_revision WHERE claim_revision_id='pharm:claimrev:rl_concb'")
[[ "$status_b" == "CANDIDATE" && "$claim_b" == "SUPPORTED" ]] || {
  echo "RL-CONC-B FAIL: lot=$status_b claim=$claim_b" >&2
  exit 1
}
echo 'RL-CONC-B PASS: withdrawal-first => freeze rejected'

echo 'PACK_BUILDER_RELEASE_LOT_CONCURRENCY: PASS'
