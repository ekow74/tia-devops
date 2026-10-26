#!/usr/bin/env bash
set -euo pipefail
: "${DATABASE_URL:?Set DATABASE_URL to a disposable PostgreSQL 18 database}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CANONICAL="$ROOT/sql/phase0_schema.sql"
EXPECTED_SHA256="369f1bb6544761840dd2502d65d66ada4d2da4bd8391129642550fe0fadd83f1"

cat "$ROOT"/sql/phase0_schema.parts/*.part > "$CANONICAL"
actual=$(sha256sum "$CANONICAL" | awk '{print $1}')
if [[ "$actual" != "$EXPECTED_SHA256" ]]; then
  echo "phase0_schema.sql SHA mismatch: expected=$EXPECTED_SHA256 actual=$actual" >&2
  exit 1
fi

echo "PHASE0_SCHEMA_SHA256: $actual"
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -X -f "$CANONICAL"
echo 'PHASE0_SCHEMA_LOAD: PASS'
