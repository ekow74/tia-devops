#!/usr/bin/env bash
set -euo pipefail

# One runtime authority: promotion-signed desired-state document.
# Git is audit-only and is never read by this verifier or reconciler.
# Private baseline: project-key signatures are not uploaded to public Rekor.
# Freshness/replay are enforced separately from signatures by signed timestamps and
# monotonic persisted sequence counters.
# Usage: verify_signed_desired_state.sh desired.json promotion.bundle promotion.pub build.pub

if [[ $# -ne 4 ]]; then
  echo "usage: $0 DESIRED_JSON PROMOTION_BUNDLE PROMOTION_PUBLIC_KEY BUILD_PUBLIC_KEY" >&2
  exit 64
fi

DESIRED_JSON=$1
PROMOTION_BUNDLE=$2
PROMOTION_PUBLIC_KEY=$3
BUILD_PUBLIC_KEY=$4
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

for f in "$DESIRED_JSON" "$PROMOTION_BUNDLE" "$PROMOTION_PUBLIC_KEY" "$BUILD_PUBLIC_KEY"; do
  [[ -r "$f" ]] || { echo "authority-verification: FAIL unreadable file: $f" >&2; exit 1; }
done
command -v cosign >/dev/null 2>&1 || { echo "authority-verification: FAIL cosign is required" >&2; exit 1; }

# F1-T01/F1-T02/F1-T03: desired-state authority is the dedicated promotion key.
"${SCRIPT_DIR}/verify_promotion_signature.sh" \
  "$DESIRED_JSON" "$PROMOTION_BUNDLE" "$PROMOTION_PUBLIC_KEY" >/dev/null

RELEASE_REF=$(python3 - "$DESIRED_JSON" <<'PY'
import json, sys
with open(sys.argv[1], encoding='utf-8') as f:
    d=json.load(f)
ref=d.get('release_manifest_ref')
if not isinstance(ref, str) or '@sha256:' not in ref:
    raise SystemExit(2)
print(ref)
PY
)

# Independently verify the referenced ReleaseManifest under the pinned build key.
# The private baseline intentionally does not require a public transparency log.
cosign verify \
  --insecure-ignore-tlog=true \
  --key "$BUILD_PUBLIC_KEY" \
  "$RELEASE_REF" >/dev/null

echo "authority-verification: PASS promotion-signature + build-signature"
