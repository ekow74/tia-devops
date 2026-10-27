#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Mapping

REINSTALL_SENSITIVE = {"image_id", "ssh_keys", "default_user", "user_data"}


def identity(resource: Mapping[str, Any]) -> tuple[str, str, str]:
    module = re.sub(r"\[[^\]]+\]", "", str(resource.get("module_address") or "").strip())
    typ = str(resource.get("type") or "").strip()
    name = str(resource.get("name") or "").strip()
    address = str(resource.get("address") or "").strip()
    base = f"{typ}.{name}" if typ and name else re.sub(r"\[[^\]]+\]", "", address).split(".")[-2:]
    if isinstance(base, list):
        base = ".".join(base)
    return module, str(base), address


def classify(plan: Mapping[str, Any], protected: set[str]) -> dict[str, Any]:
    reasons: list[str] = []
    for item in plan.get("resource_changes", []):
        if not isinstance(item, Mapping):
            continue
        module, base, address = identity(item)
        qualified = f"{module}.{base}" if module else base
        if base not in protected and qualified not in protected:
            continue
        change = item.get("change") or {}
        actions = {str(x) for x in change.get("actions", [])}
        if actions.intersection({"delete", "forget"}):
            reasons.append(f"protected resource delete/forget: {address}")
        before = change.get("before") or {}
        after = change.get("after") or {}
        if isinstance(before, Mapping) and isinstance(after, Mapping):
            for key in REINSTALL_SENSITIVE:
                if before.get(key) != after.get(key) and (key in before or key in after):
                    reasons.append(f"reinstall-sensitive change: {address}.{key}")
    return {"classification": "DESTRUCTIVE" if reasons else "NON_DESTRUCTIVE", "reasons": reasons}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("plan", type=Path)
    ap.add_argument("--protected", action="append", required=True)
    ns = ap.parse_args()
    result = classify(json.loads(ns.plan.read_text()), set(ns.protected))
    print(json.dumps(result, sort_keys=True))
    return 0 if result["classification"] == "DESTRUCTIVE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
