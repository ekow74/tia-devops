#!/usr/bin/env bash
set -euo pipefail
: "${DATABASE_URL:?Set DATABASE_URL to a disposable PostgreSQL 18 database}"

psqlq() { psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -X "$@"; }
TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/rl-isolation.XXXXXX")
trap 'rm -rf "$TMP_DIR"' EXIT

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

assert_mutation_rejected() {
  local isolation=$1
  local target=$2
  local statement=$3
  local log="$TMP_DIR/${isolation}-${target}.log"
  if psqlq --set=VERBOSITY=verbose -c \
    "BEGIN ISOLATION LEVEL $isolation; SET LOCAL lock_timeout='3s'; SET LOCAL statement_timeout='10s'; $statement; COMMIT;" \
    >"$log" 2>&1; then
    echo "$isolation unexpectedly permitted $target mutation" >&2
    exit 1
  fi
  grep -Fqx 'ERROR:  P0001: release-lot mutations require READ COMMITTED isolation' "$log" || {
    echo "$isolation $target returned an unexpected error" >&2
    cat "$log" >&2
    exit 1
  }
}

for isolation in 'REPEATABLE READ' SERIALIZABLE; do
  assert_mutation_rejected "$isolation" claim_revision \
    "UPDATE claim_revision SET status='APPROVED' WHERE claim_revision_id='pharm:claimrev:rl_isolation'"
  assert_mutation_rejected "$isolation" recall_event \
    "INSERT INTO recall_event(claim_id,claim_revision_id,content_hash,reason) VALUES ('pharm:claim:rl_isolation','pharm:claimrev:rl_isolation',repeat('9',64),'isolation test')"
  assert_mutation_rejected "$isolation" release_lot_claim \
    "UPDATE release_lot_claim SET content_hash=content_hash WHERE lot_id='lot:rl_isolation'"
  assert_mutation_rejected "$isolation" release_lot \
    "UPDATE release_lot SET status='FROZEN' WHERE lot_id='lot:rl_isolation'"
done

[[ $(psqlq -Atc "SELECT status FROM release_lot WHERE lot_id='lot:rl_isolation'") == CANDIDATE ]]
[[ $(psqlq -Atc "SELECT status FROM claim_revision WHERE claim_revision_id='pharm:claimrev:rl_isolation'") == APPROVED ]]
[[ $(psqlq -Atc "SELECT count(*) FROM recall_event WHERE claim_id='pharm:claim:rl_isolation'") == 0 ]]
echo 'PACK_BUILDER_RELEASE_LOT_ISOLATION: PASS (REPEATABLE READ and SERIALIZABLE mutations fail closed across all guarded tables)'
