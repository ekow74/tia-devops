#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
cat > "$TMP/main.tf" <<'TF'
terraform {
  required_version = ">= 1.9.0, < 2.0.0"
}
variable "keep" { type = bool }
resource "terraform_data" "probe" {
  count = var.keep ? 1 : 0
  input = "real-terraform-show-json-probe"
}
TF
terraform -chdir="$TMP" init -backend=false -input=false >/dev/null
terraform -chdir="$TMP" apply -auto-approve -input=false -var='keep=true' >/dev/null
terraform -chdir="$TMP" plan -input=false -var='keep=false' -out=tfplan >/dev/null
terraform -chdir="$TMP" show -json tfplan > "$TMP/tfplan.json"
python3 - "$TMP/tfplan.json" <<'PY'
import json, sys
from pathlib import Path
p=Path(sys.argv[1]); plan=json.loads(p.read_text())
if not any(x.get('address') == 'terraform_data.probe[0]' for x in plan.get('resource_changes', [])):
    raise SystemExit('indexed address not emitted by real terraform show -json')
PY
python3 "$ROOT/ksp-bootstrap/scripts/ci/classify_tf_plan.py" "$TMP/tfplan.json" --protected terraform_data.probe
mkdir -p "$ROOT/ksp-bootstrap/evidence"
cp "$TMP/tfplan.json" "$ROOT/ksp-bootstrap/evidence/terraform-real-plan-probe.json"
echo 'terraform-real-plan-probe: PASS'
