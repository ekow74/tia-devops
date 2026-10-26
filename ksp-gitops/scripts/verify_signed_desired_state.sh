#!/usr/bin/env bash
set -euo pipefail

# One release authority: the promotion-signed desired-state document.
# Git is audit-only and is never read by this verifier or the reconciler.
# Usage: verify_signed_desired_state.sh desired.json promotion.sigstore.json promotion.pub build.pub

if [[ $# -ne 4 ]]; then
  echo "usage: $0 DESIRED_JSON PROMOTION_BUNDLE PROMOTION_PUBLIC_KEY BUILD_PUBLIC_KEY" >&2
  exit 64
fi

DESIRED_JSON=$1
PROMOTION_BUNDLE=$2
PROMOTION_PUBLIC_KEY=$3
BUILD_PUBLIC_KEY=$4

for f in "$DESIRED_JSON" "$PROMOTION_BUNDLE" "$PROMOTION_PUBLIC_KEY" "$BUILD_PUBLIC_KEY"; do
  [[ -r "$f" ]] || { echo "authority-verification: FAIL unreadable file: $f" >&2; exit 1; }
done

command -v cosign >/dev/null 2>&1 || {
  echo "authority-verification: FAIL cosign is required" >&2
  exit 1
}

# F1-T01/F1-T02: only the pinned promotion key may authorize the desired-state blob.
cosign verify-blob \
  --bundle "$PROMOTION_BUNDLE" \
  --key "$PROMOTION_PUBLIC_KEY" \
  "$DESIRED_JSON" >/dev/null

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

# The release manifest itself must independently verify under the pinned build key.
cosign verify --key "$BUILD_PUBLIC_KEY" "$RELEASE_REF" >/dev/null

echo "authority-verification: PASS promotion-signature + build-signature"
