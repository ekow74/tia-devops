#!/usr/bin/env python3
"""Evaluate KSP reconciliation policy without performing side effects.

Safety order is deliberately asymmetric:
1. verified revocation of *running* content outranks every other control;
2. revocation freshness is monotonic and fail-static;
3. desired-state/build authority must verify;
4. a valid freeze stops repair but never blocks quarantine;
5. replay, clock, expiry, rollback and flap controls run before drift repair.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def load(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def parse_time(value: str, field: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include timezone")
    return parsed.astimezone(timezone.utc)


def now_utc(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    return parse_time(value, "--now")


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

        running_digests = set(runtime.get("active_digests", []))
        revoked = set(revocations.get("revoked_digests", []))

        # m1: quarantine verified recalled running content before freeze or desired-state authority checks.
        if args.revocation_signature_verified and running_digests & revoked:
            return emit("QUARANTINE", "running state references a verified revoked digest")

        if not args.revocation_signature_verified:
            return emit("FAIL_STATIC", "revocation list unavailable or unverified; keep current verified state and alert")

        revocation_seq = revocations.get("sequence")
        last_seen_revocation = runtime.get("last_seen_revocation_sequence", 0)
        if not isinstance(revocation_seq, int) or isinstance(revocation_seq, bool):
            return emit("FAIL_STATIC", "invalid revocation sequence; keep current verified state and alert")
        if not isinstance(last_seen_revocation, int) or last_seen_revocation < 0:
            return emit("REFUSE", "invalid persisted last_seen_revocation_sequence", 2)
        if revocation_seq < last_seen_revocation:
            return emit("FAIL_STATIC", "revocation replay detected; sequence is older than last seen")
        if revocation_seq < desired.get("revocation_min_sequence", 0):
            return emit("FAIL_STATIC", "revocation sequence below desired-state minimum")

        if not args.promotion_signature_verified:
            return emit("REFUSE", "promotion signature not verified", 2)
        if not args.build_signature_verified:
            return emit("REFUSE", "release manifest build signature not verified", 2)

        if args.freeze_present:
            if not args.freeze_signature_verified:
                return emit("REFUSE", "freeze marker present but signature is not verified", 2)
            return emit("FREEZE", "valid local signed freeze stops repair; verified revocation quarantine already evaluated")

        # m2: desired-state channel replay protection is independent of target-release rollback.
        last_applied = runtime.get("last_applied_sequence", 0)
        desired_sequence = desired.get("sequence", 0)
        if not isinstance(last_applied, int) or last_applied < 0:
            return emit("REFUSE", "invalid persisted last_applied_sequence", 2)
        if not isinstance(desired_sequence, int) or desired_sequence < 1:
            return emit("REFUSE", "invalid desired-state sequence", 2)
        if desired_sequence < last_applied:
            return emit("REFUSE", "desired-state replay detected; sequence is older than last applied", 2)

        # m6: time freshness is fail-static. NTP health is a host-observed runtime property.
        if runtime.get("ntp_synchronized") is not True:
            return emit("FAIL_STATIC", "host time is not NTP-synchronized; keep current verified state and alert")
        issued_at = parse_time(desired["issued_at"], "issued_at")
        expires_at = parse_time(desired["expires_at"], "expires_at")
        tolerance = int(policy.get("clock_skew_tolerance_seconds", 300))
        max_validity = int(policy.get("max_desired_state_validity_seconds", 172800))
        if issued_at > current_time + timedelta(seconds=tolerance):
            return emit("FAIL_STATIC", "desired state issued_at is in the future beyond clock-skew tolerance")
        if (expires_at - issued_at).total_seconds() > max_validity:
            return emit("FAIL_STATIC", "desired-state validity window exceeds policy maximum")
        if current_time >= expires_at:
            return emit("FAIL_STATIC", "desired state expired; keep current verified state and alert")

        desired_digests = {
            desired["release_manifest_digest"],
            desired["compiled_learner_bundle_digest"],
            desired["qualification_bundle_digest"],
            *desired["knowledge"].values(),
        }
        desired_digests.update(ref.split("@", 1)[1] for ref in desired["software"].values())
        if desired_digests & revoked:
            return emit("QUARANTINE", "promotion-signed desired state references a revoked digest")

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
