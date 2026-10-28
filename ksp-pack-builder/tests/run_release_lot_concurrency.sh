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
  local deadline=$((SECONDS + 15))
  while (( SECONDS < deadline )); do
    if [[ $(psqlq -Atc "
      SELECT count(*)
      FROM pg_stat_activity
      WHERE application_name='$app_name'
        AND state='idle in transaction'
        AND query LIKE 'SELECT claim_revision_id FROM claim_revision%'
    ") == 1 ]]; then
      return 0
    fi
    sleep 0.05
  done
  echo "Timed out waiting for $app_name to hold a claim-revision SHARE lock" >&2
  diagnostics
  return 1
}

wait_for_revision_wait() {
  local waiter=$1
  local blocker=$2
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
    if ! kill -0 "$waiter_pid" 2>/dev/null; then
      echo "$waiter exited before a verified revision-row wait" >&2
      diagnostics
      return 1
    fi
    sleep 0.05
  done
  echo "Timed out proving $waiter waits on $blocker's claim-revision row lock" >&2
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

start_revision_blocker() {
  blocker_app=$1
  blocker_dir=$(mktemp -d /tmp/rl-row-blocker.XXXXXX)
  blocker_fifo="$blocker_dir/input"
  blocker_log="/tmp/${blocker_app}.log"
  mkfifo "$blocker_fifo"
  PGAPPNAME="$blocker_app" psqlq <"$blocker_fifo" >"$blocker_log" 2>&1 &
  blocker_pid=$!
  exec 9<>"$blocker_fifo"
  printf "BEGIN;\nSELECT claim_revision_id FROM claim_revision WHERE claim_revision_id='pharm:claimrev:rl_conce' FOR SHARE;\n" >&9
  wait_for_revision_share "$blocker_app"
}

start_revision_waiter() {
  waiter_app=$1
  waiter_sql=$2
  waiter_log="/tmp/${waiter_app}.log"
  PGAPPNAME="$waiter_app" psqlq --set=VERBOSITY=verbose -c "$waiter_sql" >"$waiter_log" 2>&1 &
  waiter_pid=$!
  wait_for_revision_wait "$waiter_app" "$blocker_app"
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
  ('pharm:claim:rl_conce','PHARM','pharm:concept:rl_concurrency');
INSERT INTO claim_revision(
  claim_revision_id,claim_id,revision_number,claim_type,claim_text,context_frame,
  verification_mode,safety_class,authority_type,content_hash,status
) VALUES
  ('pharm:claimrev:rl_conca','pharm:claim:rl_conca',1,'MECHANISM','concurrency a','{"scope":"UNIVERSAL_DEFINITION"}','MACHINE_ENTAILED','ROUTINE','TEST',repeat('1',64),'APPROVED'),
  ('pharm:claimrev:rl_concb','pharm:claim:rl_concb',1,'MECHANISM','concurrency b','{"scope":"UNIVERSAL_DEFINITION"}','MACHINE_ENTAILED','ROUTINE','TEST',repeat('2',64),'APPROVED'),
  ('pharm:claimrev:rl_concc','pharm:claim:rl_concc',1,'MECHANISM','concurrency c','{"scope":"UNIVERSAL_DEFINITION"}','MACHINE_ENTAILED','ROUTINE','TEST',repeat('3',64),'APPROVED'),
  ('pharm:claimrev:rl_concd','pharm:claim:rl_concd',1,'MECHANISM','concurrency d','{"scope":"UNIVERSAL_DEFINITION"}','MACHINE_ENTAILED','ROUTINE','TEST',repeat('4',64),'APPROVED'),
  ('pharm:claimrev:rl_conce','pharm:claim:rl_conce',1,'MECHANISM','concurrency e','{"scope":"UNIVERSAL_DEFINITION"}','MACHINE_ENTAILED','ROUTINE','TEST',repeat('5',64),'APPROVED');
INSERT INTO release_lot(lot_id,bundle_sha256,frozen_at,registry_snapshot_at,status) VALUES
  ('lot:conca',repeat('a',64),NULL,clock_timestamp(),'CANDIDATE'),
  ('lot:concb',repeat('b',64),NULL,clock_timestamp(),'CANDIDATE'),
  ('lot:concc',repeat('c',64),NULL,clock_timestamp(),'CANDIDATE'),
  ('lot:concd',repeat('d',64),NULL,clock_timestamp(),'CANDIDATE');
INSERT INTO release_lot_claim(lot_id,claim_revision_id,content_hash) VALUES
  ('lot:conca','pharm:claimrev:rl_conca',repeat('1',64)),
  ('lot:concb','pharm:claimrev:rl_concb',repeat('2',64)),
  ('lot:concc','pharm:claimrev:rl_concc',repeat('3',64)),
  ('lot:concd','pharm:claimrev:rl_concd',repeat('4',64));
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

# The RecallEvent trigger's FOR NO KEY UPDATE lock conflicts with a revision SHARE lock.
start_revision_blocker rl-e-share
start_revision_waiter rl-e-recall \
  "INSERT INTO recall_event(claim_id,claim_revision_id,content_hash,reason) VALUES ('pharm:claim:rl_conce','pharm:claimrev:rl_conce',repeat('5',64),'RL-CONC-E')"
finish_blocker
finish_waiter_success
[[ $(psqlq -Atc "SELECT count(*) FROM recall_event WHERE claim_revision_id='pharm:claimrev:rl_conce'") == 1 ]]
echo 'RL-CONC-E PASS: verified RecallEvent revision-row wait on FOR SHARE blocker'

echo 'PACK_BUILDER_RELEASE_LOT_CONCURRENCY: PASS'
