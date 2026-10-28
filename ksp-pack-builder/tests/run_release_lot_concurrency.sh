#!/usr/bin/env bash
set -euo pipefail
: "${DATABASE_URL:?Set DATABASE_URL to a disposable PostgreSQL 18 database}"

psqlq() { psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -X "$@"; }

diagnostics() {
  psqlq -x -c "
    SELECT pid, application_name, state, wait_event_type, wait_event,
           pg_blocking_pids(pid) AS blockers
    FROM pg_stat_activity
    WHERE application_name LIKE 'rl-%'
    ORDER BY application_name;
    SELECT locktype, pid, granted, classid, objid, objsubid, mode
    FROM pg_locks
    WHERE pid IN (
      SELECT pid FROM pg_stat_activity WHERE application_name LIKE 'rl-%'
    )
    ORDER BY pid, locktype, granted;
  " >&2 || true
  for log in /tmp/rl-*.log; do
    [[ -f "$log" ]] && { echo "===== $log =====" >&2; cat "$log" >&2; }
  done
}

wait_for_gate() {
  local app_name=$1
  local deadline=$((SECONDS + 15))
  while (( SECONDS < deadline )); do
    if [[ $(psqlq -Atc "
      SELECT count(*)
      FROM pg_locks l
      JOIN pg_stat_activity a USING (pid)
      WHERE a.application_name='$app_name'
        AND l.locktype='advisory'
        AND l.granted
        AND l.classid=1263749968::oid
        AND l.objid=1::oid
        AND l.objsubid=2
    ") == 1 ]]; then
      return 0
    fi
    sleep 0.05
  done
  echo "Timed out waiting for $app_name to hold the release-lot gate" >&2
  diagnostics
  return 1
}

wait_for_revision_share() {
  local app_name=$1
  local revision_id=$2
  local lock_mode=$3
  local deadline=$((SECONDS + 15))
  while (( SECONDS < deadline )); do
    if [[ $(psqlq -Atc "
      SELECT count(*)
      FROM pg_stat_activity
      WHERE application_name='$app_name'
        AND state='idle in transaction'
        AND query LIKE '%claim_revision_id=''$revision_id''%'
        AND query LIKE '%$lock_mode%'
        AND EXISTS (
          SELECT 1 FROM pg_locks l
          WHERE l.pid=pg_stat_activity.pid
            AND l.locktype='transactionid' AND l.granted
            AND l.mode='ExclusiveLock'
        )
    ") == 1 ]]; then
      return 0
    fi
    sleep 0.05
  done
  echo "Timed out waiting for $app_name to hold a claim-revision $lock_mode lock" >&2
  diagnostics
  return 1
}

wait_for_revision_wait() {
  local waiter=$1
  local blocker=$2
  local revision_id=$3
  local lock_mode=$4
  local deadline=$((SECONDS + 15))
  while (( SECONDS < deadline )); do
    if [[ $(psqlq -Atc "
      SELECT count(*)
      FROM pg_stat_activity w
      JOIN LATERAL unnest(pg_blocking_pids(w.pid)) p(pid) ON true
      JOIN pg_stat_activity b ON b.pid=p.pid
      JOIN pg_locks wl ON wl.pid=w.pid AND wl.locktype='transactionid'
                       AND NOT wl.granted
      JOIN pg_locks bl ON bl.pid=b.pid AND bl.locktype='transactionid'
                       AND bl.granted AND bl.transactionid=wl.transactionid
      WHERE w.application_name='$waiter'
        AND b.application_name='$blocker'
        AND b.state='idle in transaction'
        AND b.query LIKE '%claim_revision_id=''$revision_id''%'
        AND b.query LIKE '%$lock_mode%'
        AND w.wait_event_type='Lock'
        AND w.wait_event='transactionid'
        AND EXISTS (
          SELECT 1 FROM pg_locks gl
          WHERE gl.pid=w.pid AND gl.locktype='advisory' AND gl.granted
            AND gl.classid=1263749968::oid AND gl.objid=1::oid AND gl.objsubid=2
        )
    ") == 1 ]]; then
      return 0
    fi
    waiter_state=$(ps -o stat= -p "$waiter_pid" 2>/dev/null | tr -d ' ' || true)
    if [[ -z "$waiter_state" || "$waiter_state" == Z* ]]; then
      echo "$waiter exited before a verified revision-row wait" >&2
      diagnostics
      return 1
    fi
    sleep 0.05
  done
  echo "Timed out proving $waiter waits on $blocker's $lock_mode lock for $revision_id" >&2
  diagnostics
  return 1
}

start_blocker() {
  blocker_app=$1
  blocker_sql=$2
  blocker_dir=$(mktemp -d /tmp/rl-blocker.XXXXXX)
  blocker_fifo="$blocker_dir/input"
  blocker_log="/tmp/${blocker_app}.log"
  mkfifo "$blocker_fifo"
  PGAPPNAME="$blocker_app" psqlq <"$blocker_fifo" >"$blocker_log" 2>&1 &
  blocker_pid=$!
  exec 9<>"$blocker_fifo"
  printf 'BEGIN;\n%s\n' "$blocker_sql" >&9
  wait_for_gate "$blocker_app"
}

start_waiter() {
  waiter_app=$1
  waiter_sql=$2
  blocker_app=$3
  waiter_log="/tmp/${waiter_app}.log"
  PGAPPNAME="$waiter_app" psqlq --set=VERBOSITY=verbose -c "$waiter_sql" >"$waiter_log" 2>&1 &
  waiter_pid=$!

  local deadline=$((SECONDS + 15))
  while (( SECONDS < deadline )); do
    if [[ $(psqlq -Atc "
      SELECT count(*)
      FROM pg_stat_activity w
      JOIN LATERAL unnest(pg_blocking_pids(w.pid)) b(pid) ON true
      JOIN pg_stat_activity blocker ON blocker.pid=b.pid
      JOIN pg_locks wl ON wl.pid=w.pid AND wl.locktype='advisory' AND NOT wl.granted
      JOIN pg_locks bl ON bl.pid=blocker.pid AND bl.locktype='advisory'
                       AND bl.granted AND bl.classid=wl.classid
                       AND bl.objid=wl.objid AND bl.objsubid=wl.objsubid
      WHERE w.application_name='$waiter_app'
        AND blocker.application_name='$blocker_app'
        AND w.wait_event_type='Lock'
    ") == 1 ]]; then
      return 0
    fi
    if ! kill -0 "$waiter_pid" 2>/dev/null; then
      echo "$waiter_app exited before a verified advisory-lock wait" >&2
      diagnostics
      return 1
    fi
    sleep 0.05
  done
  echo "Timed out proving $waiter_app waits on $blocker_app's advisory lock" >&2
  diagnostics
  return 1
}

start_revision_share_blocker() {
  blocker_app=$1
  local revision_id=$2
  blocker_dir=$(mktemp -d /tmp/rl-row-blocker.XXXXXX)
  blocker_fifo="$blocker_dir/input"
  blocker_log="/tmp/${blocker_app}.log"
  mkfifo "$blocker_fifo"
  PGAPPNAME="$blocker_app" psqlq <"$blocker_fifo" >"$blocker_log" 2>&1 &
  blocker_pid=$!
  exec 9<>"$blocker_fifo"
  printf "BEGIN;\nSELECT claim_revision_id FROM claim_revision WHERE claim_revision_id='%s' FOR SHARE;\n" "$revision_id" >&9
  wait_for_revision_share "$blocker_app" "$revision_id" 'FOR SHARE'
}

start_revision_no_key_update_blocker() {
  blocker_app=$1
  blocker_dir=$(mktemp -d /tmp/rl-row-blocker.XXXXXX)
  blocker_fifo="$blocker_dir/input"
  blocker_log="/tmp/${blocker_app}.log"
  mkfifo "$blocker_fifo"
  PGAPPNAME="$blocker_app" psqlq <"$blocker_fifo" >"$blocker_log" 2>&1 &
  blocker_pid=$!
  exec 9<>"$blocker_fifo"
  printf "BEGIN;\nSELECT claim_revision_id FROM claim_revision WHERE claim_revision_id='pharm:claimrev:rl_concf' FOR NO KEY UPDATE;\n" >&9
  wait_for_revision_share "$blocker_app" pharm:claimrev:rl_concf 'FOR NO KEY UPDATE'
}

start_revision_waiter() {
  waiter_app=$1
  waiter_sql=$2
  local revision_id=$3
  local lock_mode=$4
  waiter_log="/tmp/${waiter_app}.log"
  PGAPPNAME="$waiter_app" psqlq --set=VERBOSITY=verbose -c "$waiter_sql" >"$waiter_log" 2>&1 &
  waiter_pid=$!
  wait_for_revision_wait "$waiter_app" "$blocker_app" "$revision_id" "$lock_mode"
}

start_freeze_revision_waiter() {
  waiter_app=$1
  waiter_sql=$2
  waiter_log="/tmp/${waiter_app}.log"
  PGAPPNAME="$waiter_app" psqlq --set=VERBOSITY=verbose -c "$waiter_sql" >"$waiter_log" 2>&1 &
  waiter_pid=$!
  wait_for_revision_wait "$waiter_app" "$blocker_app" pharm:claimrev:rl_concf 'FOR NO KEY UPDATE'
}

finish_blocker() {
  printf 'COMMIT;\n' >&9
  exec 9>&-
  if ! wait "$blocker_pid"; then
    cat "$blocker_log" >&2
    return 1
  fi
}

finish_waiter_success() {
  if ! wait "$waiter_pid"; then
    cat "$waiter_log" >&2
    return 1
  fi
}

finish_waiter_error() {
  local expected=$1
  if wait "$waiter_pid"; then
    echo "Expected rejected statement to fail: $waiter_app" >&2
    return 1
  fi
  grep -Fqx "ERROR:  P0001: $expected" "$waiter_log" || {
    echo "Unexpected error for $waiter_app; expected exact message: $expected" >&2
    cat "$waiter_log" >&2
    return 1
  }
}

psqlq <<'SQL'
INSERT INTO concept(concept_id,domain,canonical_label,status)
VALUES ('pharm:concept:rl_concurrency','PHARM','RL concurrency','ACTIVE');
INSERT INTO claim(claim_id,domain,concept_id) VALUES
  ('pharm:claim:rl_conca','PHARM','pharm:concept:rl_concurrency'),
  ('pharm:claim:rl_concb','PHARM','pharm:concept:rl_concurrency'),
  ('pharm:claim:rl_concc','PHARM','pharm:concept:rl_concurrency'),
  ('pharm:claim:rl_concd','PHARM','pharm:concept:rl_concurrency'),
  ('pharm:claim:rl_conce','PHARM','pharm:concept:rl_concurrency'),
  ('pharm:claim:rl_concf','PHARM','pharm:concept:rl_concurrency'),
  ('pharm:claim:rl_conch','PHARM','pharm:concept:rl_concurrency'),
  ('pharm:claim:rl_conci','PHARM','pharm:concept:rl_concurrency');
INSERT INTO claim_revision(
  claim_revision_id,claim_id,revision_number,claim_type,claim_text,context_frame,
  verification_mode,safety_class,authority_type,content_hash,status
) VALUES
  ('pharm:claimrev:rl_conca','pharm:claim:rl_conca',1,'MECHANISM','concurrency a','{"scope":"UNIVERSAL_DEFINITION"}','MACHINE_ENTAILED','ROUTINE','TEST',repeat('1',64),'APPROVED'),
  ('pharm:claimrev:rl_concb','pharm:claim:rl_concb',1,'MECHANISM','concurrency b','{"scope":"UNIVERSAL_DEFINITION"}','MACHINE_ENTAILED','ROUTINE','TEST',repeat('2',64),'APPROVED'),
  ('pharm:claimrev:rl_concc','pharm:claim:rl_concc',1,'MECHANISM','concurrency c','{"scope":"UNIVERSAL_DEFINITION"}','MACHINE_ENTAILED','ROUTINE','TEST',repeat('3',64),'APPROVED'),
  ('pharm:claimrev:rl_concd','pharm:claim:rl_concd',1,'MECHANISM','concurrency d','{"scope":"UNIVERSAL_DEFINITION"}','MACHINE_ENTAILED','ROUTINE','TEST',repeat('4',64),'APPROVED'),
  ('pharm:claimrev:rl_conce','pharm:claim:rl_conce',1,'MECHANISM','concurrency e','{"scope":"UNIVERSAL_DEFINITION"}','MACHINE_ENTAILED','ROUTINE','TEST',repeat('5',64),'APPROVED'),
  ('pharm:claimrev:rl_concf','pharm:claim:rl_concf',1,'MECHANISM','concurrency f','{"scope":"UNIVERSAL_DEFINITION"}','MACHINE_ENTAILED','ROUTINE','TEST',repeat('6',64),'APPROVED'),
  ('pharm:claimrev:rl_conch','pharm:claim:rl_conch',1,'MECHANISM','concurrency human dual','{"scope":"UNIVERSAL_DEFINITION"}','HUMAN_DUAL','SAFETY_CRITICAL','TEST',repeat('8',64),'SUPPORTED'),
  ('pharm:claimrev:rl_conci','pharm:claim:rl_conci',1,'MECHANISM','concurrency i','{"scope":"UNIVERSAL_DEFINITION"}','MACHINE_ENTAILED','ROUTINE','TEST',repeat('9',64),'APPROVED');
INSERT INTO human_dual_review(
  claim_revision_id,claim_id,content_hash,abstractor_id,verifier_id,
  abstractor_decision,verifier_decision,abstractor_reviewed_at,verifier_reviewed_at,
  qualifier_checklist,source_version,source_locator,effective_date
) VALUES (
  'pharm:claimrev:rl_conch','pharm:claim:rl_conch',repeat('8',64),'reviewer-a','reviewer-b',
  'SUPPORT','CONFIRM',clock_timestamp(),clock_timestamp(),
  jsonb_build_object(
    'population','CHECKED','phenotype_or_stage','CHECKED','setting','CHECKED',
    'condition_context','CHECKED','threshold_or_trigger','CHECKED','exclusion_or_exception','CHECKED',
    'organ_function','CHECKED','timing_or_duration','CHECKED','therapy_status','CHECKED',
    'recommendation_strength_or_class','CHECKED','evidence_level_if_stated','CHECKED',
    'off_label_status','CHECKED','jurisdiction_applicability','CHECKED',
    'source_version','CHECKED','effective_date','CHECKED'
  ),
  'test-version','test-locator',CURRENT_DATE
);
UPDATE claim_revision SET status='APPROVED' WHERE claim_revision_id='pharm:claimrev:rl_conch';
INSERT INTO release_lot(lot_id,bundle_sha256,frozen_at,registry_snapshot_at,status) VALUES
  ('lot:conca',repeat('a',64),NULL,clock_timestamp(),'CANDIDATE'),
  ('lot:concb',repeat('b',64),NULL,clock_timestamp(),'CANDIDATE'),
  ('lot:concc',repeat('c',64),NULL,clock_timestamp(),'CANDIDATE'),
  ('lot:concd',repeat('d',64),NULL,clock_timestamp(),'CANDIDATE'),
  ('lot:concf',repeat('f',64),NULL,clock_timestamp(),'CANDIDATE'),
  ('lot:conch',repeat('8',64),NULL,clock_timestamp(),'CANDIDATE'),
  ('lot:conci',repeat('9',64),NULL,clock_timestamp(),'CANDIDATE');
INSERT INTO release_lot_claim(lot_id,claim_revision_id,content_hash) VALUES
  ('lot:conca','pharm:claimrev:rl_conca',repeat('1',64)),
  ('lot:concb','pharm:claimrev:rl_concb',repeat('2',64)),
  ('lot:concc','pharm:claimrev:rl_concc',repeat('3',64)),
  ('lot:concd','pharm:claimrev:rl_concd',repeat('4',64)),
  ('lot:concf','pharm:claimrev:rl_concf',repeat('6',64)),
  ('lot:conch','pharm:claimrev:rl_conch',repeat('8',64)),
  ('lot:conci','pharm:claimrev:rl_conci',repeat('9',64));
SQL

# RL-CONC-A: freeze first; approval withdrawal is proven waiting, then quarantines.
start_blocker rl-a-freeze "UPDATE release_lot SET status='FROZEN' WHERE lot_id='lot:conca';"
start_waiter rl-a-withdraw \
  "UPDATE claim_revision SET status='SUPPORTED' WHERE claim_revision_id='pharm:claimrev:rl_conca'" \
  rl-a-freeze
finish_blocker
finish_waiter_success
[[ $(psqlq -Atc "SELECT status FROM release_lot WHERE lot_id='lot:conca'") == QUARANTINED ]]
echo 'RL-CONC-A PASS: verified withdrawal wait; freeze-first => QUARANTINED'

# RL-CONC-B: withdrawal first; freeze waits, then rejects the no-longer-approved revision.
start_blocker rl-b-withdraw \
  "UPDATE claim_revision SET status='SUPPORTED' WHERE claim_revision_id='pharm:claimrev:rl_concb';"
start_waiter rl-b-freeze \
  "UPDATE release_lot SET status='FROZEN' WHERE lot_id='lot:concb'" \
  rl-b-withdraw
finish_blocker
finish_waiter_error 'release lot cannot freeze or release with non-approved, stale, or recalled claim revision'
[[ $(psqlq -Atc "SELECT status FROM release_lot WHERE lot_id='lot:concb'") == CANDIDATE ]]
echo 'RL-CONC-B PASS: verified freeze wait; withdrawal-first => freeze rejected'

# RL-CONC-C: RecallEvent first; freeze waits and sees the committed event at READ COMMITTED.
start_blocker rl-c-recall \
  "INSERT INTO recall_event(claim_id,claim_revision_id,content_hash,reason) VALUES ('pharm:claim:rl_concc','pharm:claimrev:rl_concc',repeat('3',64),'RL-CONC-C');"
start_waiter rl-c-freeze \
  "UPDATE release_lot SET status='FROZEN' WHERE lot_id='lot:concc'" \
  rl-c-recall
finish_blocker
finish_waiter_error 'release lot cannot freeze or release with non-approved, stale, or recalled claim revision'
[[ $(psqlq -Atc "SELECT status FROM release_lot WHERE lot_id='lot:concc'") == CANDIDATE ]]
[[ $(psqlq -Atc "SELECT status FROM claim_revision WHERE claim_revision_id='pharm:claimrev:rl_concc'") == APPROVED ]]
echo 'RL-CONC-C PASS: verified freeze wait; RecallEvent-first => freeze rejected'

# RL-CONC-D: freeze first; RecallEvent waits, then quarantines in its own transaction.
start_blocker rl-d-freeze "UPDATE release_lot SET status='FROZEN' WHERE lot_id='lot:concd';"
start_waiter rl-d-recall \
  "INSERT INTO recall_event(claim_id,claim_revision_id,content_hash,reason) VALUES ('pharm:claim:rl_concd','pharm:claimrev:rl_concd',repeat('4',64),'RL-CONC-D')" \
  rl-d-freeze
finish_blocker
finish_waiter_success
[[ $(psqlq -Atc "SELECT status FROM release_lot WHERE lot_id='lot:concd'") == QUARANTINED ]]
echo 'RL-CONC-D PASS: verified RecallEvent wait; freeze-first => QUARANTINED'

# RL-CONC-E: the RecallEvent trigger's FOR NO KEY UPDATE lock conflicts with a revision SHARE lock.
start_revision_share_blocker rl-e-share pharm:claimrev:rl_conce
start_revision_waiter rl-e-recall \
  "INSERT INTO recall_event(claim_id,claim_revision_id,content_hash,reason) VALUES ('pharm:claim:rl_conce','pharm:claimrev:rl_conce',repeat('5',64),'RL-CONC-E')" \
  pharm:claimrev:rl_conce 'FOR SHARE'
finish_blocker
finish_waiter_success
[[ $(psqlq -Atc "SELECT count(*) FROM recall_event WHERE claim_revision_id='pharm:claimrev:rl_conce'") == 1 ]]
echo 'RL-CONC-E PASS: verified RecallEvent revision-row wait on FOR SHARE blocker'

# RL-CONC-F: the human-dual review write takes the common gate before its row
# trigger locks the revision; freeze waits on the gate while reapproval completes.
psqlq -c "UPDATE claim_revision SET status='SUPPORTED' WHERE claim_revision_id='pharm:claimrev:rl_conch'"
start_blocker rl-f-human-review \
  "UPDATE human_dual_review SET source_locator=source_locator || '#rl-conc-f' WHERE claim_revision_id='pharm:claimrev:rl_conch';"
start_waiter rl-f-freeze \
  "UPDATE release_lot SET status='FROZEN' WHERE lot_id='lot:conch'" \
  rl-f-human-review
printf "UPDATE claim_revision SET status='APPROVED' WHERE claim_revision_id='pharm:claimrev:rl_conch';\nCOMMIT;\n" >&9
exec 9>&-
if ! wait "$blocker_pid"; then
  cat "$blocker_log" >&2
  exit 1
fi
finish_waiter_success
[[ $(psqlq -Atc "SELECT status FROM release_lot WHERE lot_id='lot:conch'") == FROZEN ]]
[[ $(psqlq -Atc "SELECT status FROM claim_revision WHERE claim_revision_id='pharm:claimrev:rl_conch'") == APPROVED ]]
echo 'RL-CONC-F PASS: human-review gate serializes review-row lock, reapproval, and freeze without deadlock'

# RL-CONC-G: prove freeze waits on an ungated FOR NO KEY UPDATE holder of the
# exact pinned revision, then succeeds after that row holder commits.
start_revision_no_key_update_blocker rl-g-revision-holder
start_freeze_revision_waiter rl-g-freeze \
  "UPDATE release_lot SET status='FROZEN' WHERE lot_id='lot:concf'"
finish_blocker
finish_waiter_success
[[ $(psqlq -Atc "SELECT status FROM release_lot WHERE lot_id='lot:concf'") == FROZEN ]]
echo 'RL-CONC-G PASS: observed freeze wait on exact pinned revision row holder'

# RL-CONC-H: a recall holds the global gate while waiting on a revision reader;
# an unrelated lot write is observed waiting behind that recall and then proceeds.
start_revision_share_blocker rl-h-reader pharm:claimrev:rl_conci
start_revision_waiter rl-h-recall \
  "INSERT INTO recall_event(claim_id,claim_revision_id,content_hash,reason) VALUES ('pharm:claim:rl_conci','pharm:claimrev:rl_conci',repeat('9',64),'RL-CONC-H')" \
  pharm:claimrev:rl_conci 'FOR SHARE'
recall_pid=$waiter_pid
recall_log=$waiter_log
start_waiter rl-h-unrelated-lot-write \
  "UPDATE release_lot SET registry_snapshot_at=registry_snapshot_at WHERE lot_id='lot:concf'" \
  rl-h-recall
finish_blocker
finish_waiter_success
if ! wait "$recall_pid"; then
  cat "$recall_log" >&2
  exit 1
fi
[[ $(psqlq -Atc "SELECT count(*) FROM recall_event WHERE claim_revision_id='pharm:claimrev:rl_conci'") == 1 ]]
[[ $(psqlq -Atc "SELECT status FROM release_lot WHERE lot_id='lot:conci'") == CANDIDATE ]]
echo 'RL-CONC-H PASS: unrelated lot mutation waited behind a recall blocked by an ungated revision reader'

echo 'PACK_BUILDER_RELEASE_LOT_CONCURRENCY: PASS'
