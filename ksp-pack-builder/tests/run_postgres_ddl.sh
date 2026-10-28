#!/usr/bin/env bash
set -euo pipefail
: "${DATABASE_URL:?Set DATABASE_URL to a disposable PostgreSQL 18 database}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CANONICAL="$ROOT/sql/phase0_schema.sql"
EXPECTED_SHA256="b9c596a900df6ff3b29e1675cfc2fca321a8d1a4867063a89888a12ee3893fac"

cat "$ROOT"/sql/phase0_schema.parts/*.part "$ROOT"/sql/release_lot_controls.sql > "$CANONICAL"
actual=$(sha256sum "$CANONICAL" | awk '{print $1}')
if [[ "$actual" != "$EXPECTED_SHA256" ]]; then
  echo "phase0_schema.sql SHA mismatch: expected=$EXPECTED_SHA256 actual=$actual" >&2
  exit 1
fi

echo "PHASE0_SCHEMA_SHA256: $actual"
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -X -f "$CANONICAL"
echo 'PHASE0_SCHEMA_LOAD: PASS'
