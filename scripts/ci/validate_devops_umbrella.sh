#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

failures=0

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  failures=$((failures + 1))
}

note() {
  printf '==> %s\n' "$*"
}

note "Validate required governance files"
for required in KSP_DEVOPS_UMBRELLA.md catalog/repositories.yaml; do
  [[ -f "$required" ]] || fail "missing required file: $required"
done

note "Validate repository catalog shape"
if [[ -f catalog/repositories.yaml ]]; then
  mapfile -t repo_names < <(sed -nE 's/^  - name: (.+)$/\1/p' catalog/repositories.yaml)
  if (( ${#repo_names[@]} == 0 )); then
    fail "catalog contains no repositories"
  fi

  duplicates="$(printf '%s\n' "${repo_names[@]}" | sort | uniq -d || true)"
  [[ -z "$duplicates" ]] || fail "duplicate repository names in catalog: $duplicates"

  invalid_lifecycle="$(sed -nE 's/^    lifecycle: (.+)$/\1/p' catalog/repositories.yaml | grep -Ev '^(candidate-extract|reference|archive)$' || true)"
  [[ -z "$invalid_lifecycle" ]] || fail "invalid lifecycle values: $invalid_lifecycle"

  name_count="${#repo_names[@]}"
  lifecycle_count="$(grep -Ec '^    lifecycle: ' catalog/repositories.yaml || true)"
  domain_count="$(grep -Ec '^    domain: ' catalog/repositories.yaml || true)"
  disposition_count="$(grep -Ec '^    disposition: ' catalog/repositories.yaml || true)"
  [[ "$name_count" -eq "$lifecycle_count" ]] || fail "each repo needs lifecycle: names=$name_count lifecycle=$lifecycle_count"
  [[ "$name_count" -eq "$domain_count" ]] || fail "each repo needs domain: names=$name_count domain=$domain_count"
  [[ "$name_count" -eq "$disposition_count" ]] || fail "each repo needs disposition: names=$name_count disposition=$disposition_count"
fi

note "Enforce GitHub Actions supply-chain rules"
if [[ -d .github/workflows ]]; then
  while IFS= read -r use_ref; do
    [[ -z "$use_ref" ]] && continue
    if [[ "$use_ref" == ./* ]]; then
      continue
    fi
    if [[ ! "$use_ref" =~ @[0-9a-fA-F]{40}$ ]]; then
      fail "GitHub Action is not pinned to a full commit SHA: $use_ref"
    fi
  done < <(grep -RhoE 'uses:[[:space:]]*[^#[:space:]]+' .github/workflows 2>/dev/null | sed -E 's/^uses:[[:space:]]*//' || true)

  pr_target_allowlist="governance/workflow-exceptions/pull-request-target.txt"
  mapfile -t pr_target_files < <(
    grep -RlE '^[[:space:]]*pull_request_target:' .github/workflows 2>/dev/null | sort || true
  )
  if (( ${#pr_target_files[@]} > 0 )); then
    if [[ ! -f "$pr_target_allowlist" ]]; then
      printf '%s\n' "${pr_target_files[@]}" >&2
      fail "pull_request_target is prohibited unless each workflow has a reviewed exact-path exception"
    else
      for workflow in "${pr_target_files[@]}"; do
        if ! grep -Fxq "$workflow" "$pr_target_allowlist"; then
          printf '%s\n' "$workflow" >&2
          fail "pull_request_target workflow lacks reviewed exact-path exception: $workflow"
        fi
      done
    fi
  fi

  if [[ -f "$pr_target_allowlist" ]]; then
    while IFS= read -r exception || [[ -n "$exception" ]]; do
      [[ -z "$exception" || "$exception" == \#* ]] && continue
      if [[ ! -f "$exception" ]]; then
        fail "stale pull_request_target exception points to missing workflow: $exception"
        continue
      fi
      if ! grep -qE '^[[:space:]]*pull_request_target:' "$exception"; then
        fail "stale pull_request_target exception points to workflow without pull_request_target: $exception"
      fi
    done < "$pr_target_allowlist"
  fi

  if grep -RInE 'permissions:[[:space:]]*write-all' .github/workflows >/tmp/ksp-write-all.txt 2>/dev/null; then
    cat /tmp/ksp-write-all.txt >&2
    fail "permissions: write-all is prohibited"
  fi

  if grep -RInE 'persist-credentials:[[:space:]]*true' .github/workflows >/tmp/ksp-persist-creds.txt 2>/dev/null; then
    cat /tmp/ksp-persist-creds.txt >&2
    fail "actions/checkout must not persist credentials"
  fi
fi

note "Basic credential signature scan for control-plane files"
scan_targets=(.github scripts catalog governance)
existing_targets=()
for target in "${scan_targets[@]}"; do
  [[ -e "$target" ]] && existing_targets+=("$target")
done
if (( ${#existing_targets[@]} > 0 )); then
  if grep -RInE --exclude='validate_devops_umbrella.sh' '(AKIA[0-9A-Z]{16}|-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9_]{20,})' "${existing_targets[@]}" >/tmp/ksp-secret-signatures.txt 2>/dev/null; then
    cat /tmp/ksp-secret-signatures.txt >&2
    fail "possible committed credential/private key signature detected"
  fi
fi

note "Validate shell syntax"
while IFS= read -r -d '' script; do
  bash -n "$script" || fail "shell syntax invalid: $script"
done < <(find scripts -type f -name '*.sh' -print0 2>/dev/null || true)

if (( failures > 0 )); then
  printf '\nDevOps umbrella validation FAILED with %d issue(s).\n' "$failures" >&2
  exit 1
fi

printf '\nDevOps umbrella validation PASSED.\n'
