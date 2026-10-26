#!/usr/bin/env python3
"""Evaluate KSP reconciliation policy without performing side effects.

The side-effecting reconciler must call this policy evaluator after cryptographic
verification. Policy is deliberately asymmetric: revocation/freeze outrank desired
state; unknown, host, secret, database and volume drift are never auto-repaired.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def load(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def now_utc(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("--now must include timezone")
    return parsed.astimezone(timezone.utc)


def emit(action: str, reason: str, exit_code: int = 0) -> int:
    print(json.dumps({"action": action, "reason": reason}, sort_keys=True))
    return exit_code


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("desired", type=Path)
    p.add_argument("runtime", type=Path)
    p.add_argument("revocations", type=Path)
    p.add_argument("drift_policy", type=Path)
    p.add_argument("--drift-class", required=True)
    p.add_argument("--promotion-signature-verified", action="store_true")
    p.add_argument("--build-signature-verified", action="store_true")
    p.add_argument("--revocation-signature-verified", action="store_true")
    p.add_argument("--rollback-record-verified", action="store_true")
    p.add_argument("--freeze-present", action="store_true")
    p.add_argument("--freeze-signature-verified", action="store_true")
    p.add_argument("--now")
    args = p.parse_args()

    try:
        desired = load(args.desired)
        runtime = load(args.runtime)
        revocations = load(args.revocations)
        policy = load(args.drift_policy)
        current_time = now_utc(args.now)

        if not args.promotion_signature_verified:
            return emit("REFUSE", "promotion signature not verified", 2)
        if not args.build_signature_verified:
            return emit("REFUSE", "release manifest build signature not verified", 2)

        if args.freeze_present:
            if not args.freeze_signature_verified:
                return emit("REFUSE", "freeze marker present but signature is not verified", 2)
            return emit("FREEZE", "valid local signed freeze overrides desired state")

        if not args.revocation_signature_verified:
            return emit("FAIL_STATIC", "revocation list unavailable or unverified; keep current verified state")

        revocation_seq = revocations.get("sequence")
        if not isinstance(revocation_seq, int) or revocation_seq < desired.get("revocation_min_sequence", 0):
            return emit("FAIL_STATIC", "revocation sequence below desired-state minimum")

        expires_at = datetime.fromisoformat(desired["expires_at"].replace("Z", "+00:00")).astimezone(timezone.utc)
        if current_time >= expires_at:
            running_digests = set(runtime.get("active_digests", []))
            revoked = set(revocations.get("revoked_digests", []))
            if running_digests & revoked:
                return emit("QUARANTINE", "desired state expired and running state contains revoked digest")
            return emit("FAIL_STATIC", "desired state expired; keep current verified state and alert")

        desired_digests = {
            desired["release_manifest_digest"],
            desired["compiled_learner_bundle_digest"],
            desired["qualification_bundle_digest"],
            desired["revocation_list_digest"],
            *desired["knowledge"].values(),
        }
        desired_digests.update(ref.split("@", 1)[1] for ref in desired["software"].values())
        revoked = set(revocations.get("revoked_digests", []))
        if desired_digests & revoked:
            return emit("QUARANTINE", "promotion-signed desired state references a revoked digest")

        running_digests = set(runtime.get("active_digests", []))
        if running_digests & revoked:
            return emit("QUARANTINE", "running state references a revoked digest")

        current_target = runtime.get("target_release_sequence", 0)
        desired_target = desired.get("target_release_sequence", 0)
        if desired_target < current_target and not args.rollback_record_verified:
            return emit("REFUSE", "lower target release requires verified rollback record", 2)

        failures = runtime.get("consecutive_failed_applies", 0)
        flap_limit = policy.get("flap_limit", 3)
        if not isinstance(failures, int) or failures < 0:
            return emit("REFUSE", "invalid failure counter", 2)
        if failures >= flap_limit:
            return emit("STOP_ESCALATE", "flap limit reached; keep or restore last healthy release")

        classes = policy.get("classes", {})
        rule = classes.get(args.drift_class)
        if not isinstance(rule, dict):
            return emit("REPORT_ONLY", "unknown drift class; fail closed and do not mutate")
        action = rule.get("action")
        if action not in {
            "AUTO_REPAIR",
            "QUARANTINE",
            "ALERT_CONTAIN",
            "REPORT_ONLY",
            "FREEZE_DEPLOYS",
        }:
            return emit("REFUSE", "drift policy contains unknown action", 2)

        if action == "AUTO_REPAIR":
            return emit("AUTO_REPAIR", "known-safe drift after full verification")
        return emit(action, rule.get("notes", "policy action"))
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return emit("REFUSE", f"policy evaluation error: {exc}", 2)


if __name__ == "__main__":
    raise SystemExit(main())
