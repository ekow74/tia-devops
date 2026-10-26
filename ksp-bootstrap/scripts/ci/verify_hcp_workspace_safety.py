#!/usr/bin/env python3
"""Verify the credentialed HCP Terraform workspace cannot run PR code.

The KSP baseline is deliberately stronger than merely toggling PR speculation off:
- speculative-enabled == false
- execution-mode == local
- vcs-repo == null/empty
- auto-apply == false

Token is read from TFC_TOKEN and is never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def fail(msg: str) -> int:
    print(f"hcp-workspace-safety: FAIL: {msg}", file=sys.stderr)
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--organization", required=True)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--hostname", default="app.terraform.io")
    args = ap.parse_args()

    token = os.environ.get("TFC_TOKEN", "")
    if not token:
        return fail("TFC_TOKEN is required")

    org = urllib.parse.quote(args.organization, safe="")
    ws = urllib.parse.quote(args.workspace, safe="")
    url = f"https://{args.hostname}/api/v2/organizations/{org}/workspaces/{ws}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/vnd.api+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        return fail(f"HCP API returned HTTP {exc.code}")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return fail(f"HCP API readback failed: {type(exc).__name__}")

    data = payload.get("data")
    if not isinstance(data, dict):
        return fail("workspace response missing data object")
    attrs = data.get("attributes")
    if not isinstance(attrs, dict):
        return fail("workspace response missing attributes")

    problems: list[str] = []
    if attrs.get("speculative-enabled") is not False:
        problems.append("speculative-enabled must be false")
    if attrs.get("execution-mode") != "local":
        problems.append("execution-mode must be local")
    vcs_repo = attrs.get("vcs-repo")
    if vcs_repo not in (None, {}, ""):
        problems.append("vcs-repo must be disconnected for credentialed workspace")
    if attrs.get("auto-apply") is not False:
        problems.append("auto-apply must be false")

    if problems:
        print("hcp-workspace-safety: FAIL", file=sys.stderr)
        for p in problems:
            print(f" - {p}", file=sys.stderr)
        return 1

    workspace_id = str(data.get("id", "<unknown>"))
    print(
        "hcp-workspace-safety: PASS "
        f"workspace_id={workspace_id} speculative_enabled=false "
        "execution_mode=local vcs_repo=none auto_apply=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
