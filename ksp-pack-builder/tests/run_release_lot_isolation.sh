#!/usr/bin/env bash
set -euo pipefail
: "${DATABASE_URL:?Set DATABASE_URL to a disposable PostgreSQL 18 database}"

psqlq() { psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -X "$@"; }

psqlq <<'SQL'
INSERT INTO concept(concept_id,domain,canonical_label,status)
VALUES ('pharm:concept:rl_isolation','PHARM','RL isolation','ACTIVE');
INSERT INTO claim(claim_id,domain,concept_id)
VALUES ('pharm:claim:rl_isolation','PHARM','pharm:concept:rl_isolation');
INSERT INTO claim_revision(
  claim_revision_id,claim_id,revision_number,claim_type,claim_text,context_frame,
  verification_mode,safety_class,authority_type,content_hash,status
) VALUES (
  'pharm:claimrev:rl_isolation','pharm:claim:rl_isolation',1,'MECHANISM',
  'isolation text','{"scope":"UNIVERSAL_DEFINITION"}','MACHINE_ENTAILED',
  'ROUTINE','TEST',repeat('9',64),'APPROVED'
);
INSERT INTO release_lot(lot_id,bundle_sha256,frozen_at,registry_snapshot_at,status)
VALUES ('lot:rl_isolation',repeat('9',64),NULL,clock_timestamp(),'CANDIDATE');
INSERT INTO release_lot_claim(lot_id,claim_revision_id,content_hash)
VALUES ('lot:rl_isolation','pharm:claimrev:rl_isolation',repeat('9',64));
SQL

if psqlq --set=VERBOSITY=verbose -c "BEGIN ISOLATION LEVEL REPEATABLE READ; UPDATE release_lot SET status='FROZEN' WHERE lot_id='lot:rl_isolation'; COMMIT;" >/tmp/rl-isolation.log 2>&1; then
  echo 'stronger isolation unexpectedly permitted a release-lot mutation' >&2
  exit 1
fi
grep -Fqx 'ERROR:  P0001: release-lot mutations require READ COMMITTED isolation' /tmp/rl-isolation.log || {
  echo 'stronger-isolation rejection returned an unexpected error' >&2
  cat /tmp/rl-isolation.log >&2
  exit 1
}
[[ $(psqlq -Atc "SELECT status FROM release_lot WHERE lot_id='lot:rl_isolation'") == CANDIDATE ]]
echo 'PACK_BUILDER_RELEASE_LOT_ISOLATION: PASS (stronger isolation fails closed)'
