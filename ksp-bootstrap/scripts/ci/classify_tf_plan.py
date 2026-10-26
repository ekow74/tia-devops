#!/usr/bin/env python3
"""Fail closed on destructive Terraform changes to protected resources.

Protection identity is optional_module_path.type.name. Instance keys in either the
resource display address or module_address are deliberately ignored. `forget` is
classified as destructive too.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

REINSTALL_SENSITIVE = {"image_id", "ssh_keys", "default_user", "user_data"}
MODULE_INSTANCE_RE = re.compile(r"\[(?:\d+|\"(?:[^\"\\]|\\.)*\")\]")


def normalize_module_address(value: str) -> str:
    return MODULE_INSTANCE_RE.sub("", value.strip())


def identity(resource: dict) -> str:
    rtype = str(resource.get("type", "")).strip()
    name = str(resource.get("name", "")).strip()
    module = normalize_module_address(str(resource.get("module_address", "")))
    if not rtype or not name:
        return ""
    base = f"{rtype}.{name}"
    return f"{module}.{base}" if module else base


def is_protected(resource_identity: str, protected: set[str]) -> bool:
    for spec in protected:
        spec = normalize_module_address(spec.strip()) if spec.strip().startswith("module.") else spec.strip()
        if not spec:
            continue
        if spec.startswith("module."):
            if resource_identity == spec:
                return True
        elif resource_identity == spec or resource_identity.endswith("." + spec):
            return True
    return False


def classify(plan: dict, protected: set[str]) -> dict:
    reasons: list[str] = []
    for resource in plan.get("resource_changes", []):
        if not isinstance(resource, dict):
            continue
        rid = identity(resource)
        if not rid or not is_protected(rid, protected):
            continue
        display = str(resource.get("address", rid))
        change = resource.get("change") or {}
        actions = {str(x) for x in change.get("actions", [])}
        if actions & {"delete", "forget"}:
            reasons.append(f"protected resource delete/replace/forget: {display}")
        before = change.get("before") or {}
        after = change.get("after") or {}
        if isinstance(before, dict) and isinstance(after, dict):
            for key in sorted(REINSTALL_SENSITIVE):
                if (key in before or key in after) and before.get(key) != after.get(key):
                    reasons.append(f"reinstall-sensitive change {display}.{key}")
    return {"classification": "DESTRUCTIVE" if reasons else "NON_DESTRUCTIVE", "reasons": reasons}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("plan_json", type=Path)
    p.add_argument("protected_json", type=Path)
    args = p.parse_args()
    plan = json.loads(args.plan_json.read_text(encoding="utf-8"))
    config = json.loads(args.protected_json.read_text(encoding="utf-8"))
    protected = {str(x) for x in config.get("addresses", [])}
    result = classify(plan, protected)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 42 if result["classification"] == "DESTRUCTIVE" else 0


if __name__ == "__main__":
    raise SystemExit(main())
