#!/usr/bin/env bash
set -euo pipefail

# Private baseline: project-key signatures are not uploaded to public Rekor.
# Freshness/replay are enforced by signed issued_at/expires_at and monotonic host state.
if [[ $# -ne 3 ]]; then
  echo "usage: $0 DESIRED_JSON SIGNATURE_BUNDLE PROMOTION_PUBLIC_KEY" >&2
  exit 64
fi

DESIRED_JSON=$1
SIGNATURE_BUNDLE=$2
PROMOTION_PUBLIC_KEY=$3

for f in "$DESIRED_JSON" "$SIGNATURE_BUNDLE" "$PROMOTION_PUBLIC_KEY"; do
  [[ -r "$f" ]] || { echo "promotion-signature: FAIL unreadable file: $f" >&2; exit 1; }
done
command -v cosign >/dev/null 2>&1 || { echo "promotion-signature: FAIL cosign required" >&2; exit 1; }

cosign verify-blob \
  --insecure-ignore-tlog=true \
  --bundle "$SIGNATURE_BUNDLE" \
  --key "$PROMOTION_PUBLIC_KEY" \
  "$DESIRED_JSON" >/dev/null

echo "promotion-signature: PASS"
