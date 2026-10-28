#!/usr/bin/env python3
"""Offline Phase-0 reviewer gate scaffolding; no provider integration is enabled."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Protocol

WORKFLOW_PATH = ".github/workflows/phase0-review-gate.yml"
POLICY_PATH = "ksp-bootstrap/policies/protected-paths.txt"
PAGE_SIZE = 100
MAX_FILES = 3000
MAX_PAGES = MAX_FILES // PAGE_SIZE
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
REPOSITORY_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
VERDICT_KEYS = {
    "schema_version",
    "repository",
    "pr_number",
    "head_sha",
    "base_sha",
    "policy_sha",
    "reviewer_identity",
    "verdict",
    "artifact_reference",
    "workflow_version",
    "run_id",
    "reviewed",
}


class GateError(ValueError):
    pass


class ReviewerAdapter(Protocol):
    identity: str

    def review(self, untrusted_input: Any) -> tuple[str, bool]: ...


class OfflineTestAdapter:
    """Deterministic adapter used by tests only; it is not a live reviewer."""

    identity = "offline-test-adapter"

    def review(self, _untrusted_input: Any) -> tuple[str, bool]:
        return "APPROVED", True


class UnconfiguredAdapter:
    """Fail-closed production placeholder until the owner authorizes a provider."""

    identity = "provider-unconfigured"

    def review(self, _untrusted_input: Any) -> tuple[str, bool]:
        return "UNAVAILABLE", False


def parse_patterns(text: str) -> list[str]:
    patterns = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not patterns:
        raise GateError("trusted protected-path policy is empty")
    if any(pattern.startswith("/") or "\x00" in pattern for pattern in patterns):
        raise GateError("trusted protected-path policy contains an invalid pattern")
    return patterns


def _validate_path(path: Any) -> str:
    if (
        not isinstance(path, str)
        or not path
        or path.startswith("/")
        or "\x00" in path
        or "\\" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        raise GateError("file listing contains an invalid repository path")
    return path


def paths_from_file_listing(files: list[dict[str, Any]]) -> list[str]:
    paths: set[str] = set()
    supported_statuses = {"added", "modified", "removed", "renamed", "copied", "changed"}
    for item in files:
        if not isinstance(item, dict):
            raise GateError("file listing contains a malformed entry")
        status = item.get("status")
        if not isinstance(status, str) or status not in supported_statuses:
            raise GateError("file listing contains an unsupported or missing status")
        paths.add(_validate_path(item.get("filename")))
        if status == "renamed":
            paths.add(_validate_path(item.get("previous_filename")))
    return sorted(paths)


def path_is_protected(path: str, patterns: list[str]) -> bool:
    from fnmatch import fnmatch

    return any(
        path.startswith(pattern) if pattern.endswith("/") else fnmatch(path, pattern)
        for pattern in patterns
    )


def classify_files(files: list[dict[str, Any]], patterns: list[str]) -> list[str]:
    return [
        path
        for path in paths_from_file_listing(files)
        if path_is_protected(path, patterns)
    ]


def collect_paginated_files(
    fetch_page: Callable[[int], tuple[list[dict[str, Any]], str | None]],
    expected_count: int,
) -> list[dict[str, Any]]:
    if type(expected_count) is not int or not 0 <= expected_count <= MAX_FILES:
        raise GateError("pull-request file count is outside the API completeness bound")
    files: list[dict[str, Any]] = []
    seen_filenames: set[str] = set()
    for page in range(1, MAX_PAGES + 1):
        rows, _link_header = fetch_page(page)
        if not isinstance(rows, list) or len(rows) > PAGE_SIZE:
            raise GateError("file listing page is malformed")
        for item in rows:
            if not isinstance(item, dict):
                raise GateError("file listing contains a malformed entry")
            filename = _validate_path(item.get("filename"))
            if filename in seen_filenames:
                raise GateError("file listing contains a duplicate filename")
            seen_filenames.add(filename)
        files.extend(rows)
        if len(rows) < PAGE_SIZE:
            if len(files) != expected_count:
                raise GateError("complete file listing disagrees with pull-request metadata")
            return files
    if expected_count == MAX_FILES and len(files) == MAX_FILES:
        return files
    raise GateError("file listing exceeds the API's complete-pagination bound")


def make_verdict(
    *,
    repository: str,
    pr_number: int,
    head_sha: str,
    base_sha: str,
    policy_sha: str,
    reviewer_identity: str,
    verdict: str,
    artifact_reference: str,
    workflow_version: str,
    run_id: int,
    reviewed: bool,
) -> dict[str, Any]:
    result = {
        "schema_version": "1",
        "repository": repository,
        "pr_number": pr_number,
        "head_sha": head_sha,
        "base_sha": base_sha,
        "policy_sha": policy_sha,
        "reviewer_identity": reviewer_identity,
        "verdict": verdict,
        "artifact_reference": artifact_reference,
        "workflow_version": workflow_version,
        "run_id": run_id,
        "reviewed": reviewed,
    }
    validate_verdict(result)
    return result


def validate_verdict(
    value: Any,
    *,
    expected: dict[str, Any] | None = None,
    protected: bool | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != VERDICT_KEYS:
        raise GateError("review verdict does not match the strict schema")
    if value["schema_version"] != "1":
        raise GateError("review verdict schema version is unsupported")
    if not isinstance(value["repository"], str) or not REPOSITORY_RE.fullmatch(
        value["repository"]
    ):
        raise GateError("review verdict repository is invalid")
    if type(value["pr_number"]) is not int or value["pr_number"] < 1:
        raise GateError("review verdict PR number is invalid")
    if not all(
        isinstance(value[key], str) and SHA_RE.fullmatch(value[key])
        for key in ("head_sha", "base_sha", "policy_sha")
    ):
        raise GateError("review verdict commit binding is invalid")
    if not isinstance(value["reviewer_identity"], str) or value[
        "reviewer_identity"
    ] not in {
        "not-reviewed",
        "provider-unconfigured",
        "offline-test-adapter",
    }:
        raise GateError("review verdict reviewer identity is invalid")
    if not isinstance(value["verdict"], str) or value["verdict"] not in {
        "NOT_REQUIRED",
        "UNAVAILABLE",
        "APPROVED",
        "REQUEST_CHANGES",
    }:
        raise GateError("review verdict classification is invalid")
    if not isinstance(value["artifact_reference"], str) or not value[
        "artifact_reference"
    ].startswith("run:https://"):
        raise GateError("review verdict artifact reference is invalid")
    artifact_url = urllib.parse.urlsplit(value["artifact_reference"][4:])
    if (
        artifact_url.scheme != "https"
        or not artifact_url.netloc
        or artifact_url.username
        or artifact_url.password
        or artifact_url.query
        or artifact_url.fragment
        or artifact_url.path
        != f"/{value['repository']}/actions/runs/{value['run_id']}"
    ):
        raise GateError("review verdict artifact reference is not bound to this run")
    if not isinstance(value["workflow_version"], str) or not re.fullmatch(
        r"phase0-review/v0\+[0-9a-f]{40}", value["workflow_version"]
    ):
        raise GateError("review verdict workflow version is invalid")
    if type(value["run_id"]) is not int or value["run_id"] < 1:
        raise GateError("review verdict run ID is invalid")
    if type(value["reviewed"]) is not bool:
        raise GateError("review verdict reviewed flag is invalid")

    if value["verdict"] == "NOT_REQUIRED":
        if value["reviewer_identity"] != "not-reviewed" or value["reviewed"]:
            raise GateError("NOT_REQUIRED must not claim a reviewer or review")
    elif value["verdict"] == "UNAVAILABLE":
        if value["reviewer_identity"] != "provider-unconfigured" or value["reviewed"]:
            raise GateError("UNAVAILABLE must not claim a reviewer or review")
    elif value["reviewer_identity"] != "offline-test-adapter" or not value["reviewed"]:
        raise GateError("only the offline test adapter may issue a reviewed test verdict")

    if protected is True and value["verdict"] == "NOT_REQUIRED":
        raise GateError("protected change cannot be classified NOT_REQUIRED")
    if protected is False and value["verdict"] != "NOT_REQUIRED":
        raise GateError("unprotected change must be NOT_REQUIRED, not reviewed")
    if expected is not None:
        for key, expected_value in expected.items():
            if value.get(key) != expected_value:
                raise GateError(f"review verdict binding mismatch: {key}")
    return value


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _api_get(api_root: str, route: str, token: str) -> tuple[Any, str | None]:
    root = urllib.parse.urlsplit(api_root)
    if (
        root.scheme != "https"
        or not root.netloc
        or root.username
        or root.password
        or root.query
        or root.fragment
    ):
        raise GateError("GitHub API URL must be an HTTPS origin")
    url = api_root.rstrip("/") + route
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + token,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        opener = urllib.request.build_opener(_RejectRedirects())
        with opener.open(request, timeout=10) as response:
            payload = json.loads(response.read())
            return payload, response.headers.get("Link")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise GateError("bounded GitHub read request failed") from exc


def _api_file_listing(
    api_root: str, repository: str, pr_number: int, token: str, expected_count: int
) -> list[dict[str, Any]]:
    route = f"/repos/{urllib.parse.quote(repository, safe='/')}/pulls/{pr_number}/files"

    def fetch(page: int) -> tuple[list[dict[str, Any]], str | None]:
        query = urllib.parse.urlencode({"per_page": PAGE_SIZE, "page": page})
        payload, header = _api_get(api_root, f"{route}?{query}", token)
        if not isinstance(payload, list):
            raise GateError("GitHub file-list response is malformed")
        return payload, header

    return collect_paginated_files(fetch, expected_count)


def _load_json_object(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise GateError("GitHub pull-request response is malformed")
    return payload


def _current_pr(
    api_root: str, repository: str, pr_number: int, token: str
) -> dict[str, Any]:
    route = f"/repos/{urllib.parse.quote(repository, safe='/')}/pulls/{pr_number}"
    payload, _ = _api_get(api_root, route, token)
    return _load_json_object(payload)


def _pr_changed_files(pr: dict[str, Any]) -> int:
    count = pr.get("changed_files")
    if type(count) is not int or count < 0 or count > MAX_FILES:
        raise GateError("pull-request changed_files count is invalid or exceeds 3000")
    return count


def _match_pr_binding(
    pr: dict[str, Any], *, head_sha: str, base_sha: str
) -> int:
    try:
        actual_head = pr["head"]["sha"]
        actual_base = pr["base"]["sha"]
    except (KeyError, TypeError) as exc:
        raise GateError("GitHub pull-request binding is incomplete") from exc
    if actual_head != head_sha or actual_base != base_sha:
        raise GateError("pull request head or base changed during review")
    return _pr_changed_files(pr)


def _workflow_version(trusted_sha: str, root: Path) -> str:
    try:
        blob = subprocess.check_output(
            ["git", "rev-parse", f"{trusted_sha}:{WORKFLOW_PATH}"],
            cwd=root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except subprocess.CalledProcessError as exc:
        raise GateError("trusted reviewer workflow is missing from policy commit") from exc
    if not re.fullmatch(r"[0-9a-f]{40}", blob):
        raise GateError("trusted reviewer workflow blob ID is invalid")
    return f"phase0-review/v0+{blob}"


def _verify_trusted_base(trusted_sha: str, root: Path) -> None:
    checked_out = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()
    if checked_out != trusted_sha:
        raise GateError("trusted checkout does not match the workflow source SHA")


def _write_summary(verdict: dict[str, Any]) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write(
                f"Phase-0 reviewer result: **{verdict['verdict']}**"
                + (
                    " (not reviewed)"
                    if verdict["verdict"] == "NOT_REQUIRED"
                    else " (provider integration unqualified)"
                    if verdict["verdict"] == "UNAVAILABLE"
                    else ""
                )
                + "\n\n"
                + f"Run evidence: `{verdict['artifact_reference']}`\n\n"
                + f"Head SHA: `{verdict['head_sha']}`\n"
            )


def run_gate(
    env: dict[str, str] | None = None,
    reviewer_adapter: ReviewerAdapter | None = None,
    *,
    trusted_root: Path | None = None,
) -> dict[str, Any]:
    env = dict(os.environ if env is None else env)
    required = (
        "GITHUB_API_URL",
        "GITHUB_EVENT_PATH",
        "GITHUB_REPOSITORY",
        "GITHUB_RUN_ID",
        "GITHUB_SERVER_URL",
        "GITHUB_TOKEN",
        "KSP_BASE_SHA",
        "KSP_DEFAULT_BRANCH",
        "KSP_EVENT_BASE_REF",
        "KSP_EVENT_HEAD_SHA",
        "KSP_PR_NUMBER",
        "KSP_TRUSTED_SHA",
        "GITHUB_WORKFLOW_SHA",
    )
    if any(not env.get(name) for name in required):
        raise GateError("required trusted workflow context is missing")

    repository = env["GITHUB_REPOSITORY"]
    if not REPOSITORY_RE.fullmatch(repository):
        raise GateError("workflow repository binding is invalid")
    pr_number = int(env["KSP_PR_NUMBER"])
    run_id = int(env["GITHUB_RUN_ID"])
    if pr_number < 1 or run_id < 1:
        raise GateError("workflow PR or run ID is invalid")

    with open(env["GITHUB_EVENT_PATH"], encoding="utf-8") as event_file:
        event = _load_json_object(json.load(event_file))
    try:
        event_pr = event["pull_request"]
        event_repository = event["repository"]["full_name"]
        event_number = event["number"]
        head_sha = event_pr["head"]["sha"]
        base_sha = event_pr["base"]["sha"]
        base_ref = event_pr["base"]["ref"]
    except (KeyError, TypeError) as exc:
        raise GateError("workflow event is missing pull-request bindings") from exc
    if (
        event_repository != repository
        or type(event_number) is not int
        or event_number != pr_number
        or head_sha != env["KSP_EVENT_HEAD_SHA"]
        or base_sha != env["KSP_BASE_SHA"]
        or base_ref != env["KSP_EVENT_BASE_REF"]
        or base_ref != env["KSP_DEFAULT_BRANCH"]
        or not SHA_RE.fullmatch(head_sha)
        or not SHA_RE.fullmatch(base_sha)
        or env["KSP_TRUSTED_SHA"] != env["GITHUB_WORKFLOW_SHA"]
        or not SHA_RE.fullmatch(env["GITHUB_WORKFLOW_SHA"])
    ):
        raise GateError("workflow event binding is invalid or not based on default branch")

    root = (trusted_root or Path(__file__).resolve().parents[3]).resolve()
    policy_sha = env["GITHUB_WORKFLOW_SHA"]
    _verify_trusted_base(policy_sha, root)
    workflow_version = _workflow_version(policy_sha, root)
    api_root = env["GITHUB_API_URL"]
    token = env["GITHUB_TOKEN"]
    before_count = _match_pr_binding(
        _current_pr(api_root, repository, pr_number, token),
        head_sha=head_sha,
        base_sha=base_sha,
    )
    files = _api_file_listing(
        api_root, repository, pr_number, token, expected_count=before_count
    )
    after_count = _match_pr_binding(
        _current_pr(api_root, repository, pr_number, token),
        head_sha=head_sha,
        base_sha=base_sha,
    )
    if after_count != before_count:
        raise GateError("pull-request file count changed during classification")
    try:
        pattern_text = subprocess.check_output(
            ["git", "show", f"{policy_sha}:{POLICY_PATH}"],
            cwd=root,
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError as exc:
        raise GateError("trusted protected-path policy is missing from policy commit") from exc
    patterns = parse_patterns(pattern_text)
    protected_paths = classify_files(files, patterns)
    if protected_paths:
        adapter = reviewer_adapter or UnconfiguredAdapter()
        verdict_value, reviewed = adapter.review(protected_paths)
        if not isinstance(verdict_value, str) or type(reviewed) is not bool:
            raise GateError("reviewer adapter returned a malformed classification")
        identity = adapter.identity
    else:
        verdict_value, reviewed = "NOT_REQUIRED", False
        identity = "not-reviewed"

    run_reference = (
        f"run:{env['GITHUB_SERVER_URL'].rstrip('/')}/{repository}/actions/runs/{run_id}"
    )
    verdict = make_verdict(
        repository=repository,
        pr_number=pr_number,
        head_sha=head_sha,
        base_sha=base_sha,
        policy_sha=policy_sha,
        reviewer_identity=identity,
        verdict=verdict_value,
        artifact_reference=run_reference,
        workflow_version=workflow_version,
        run_id=run_id,
        reviewed=reviewed,
    )
    validate_verdict(
        verdict,
        protected=bool(protected_paths),
        expected={
            "repository": repository,
            "pr_number": pr_number,
            "head_sha": head_sha,
            "base_sha": base_sha,
            "policy_sha": policy_sha,
            "reviewer_identity": identity,
            "verdict": verdict_value,
            "artifact_reference": run_reference,
            "workflow_version": workflow_version,
            "run_id": run_id,
            "reviewed": reviewed,
        },
    )
    _write_summary(verdict)
    print(json.dumps(verdict, sort_keys=True))
    if protected_paths:
        raise GateError("protected change has no authorized independent reviewer")
    return verdict


def main() -> int:
    try:
        run_gate()
    except Exception as exc:
        print(f"phase0-review-gate: FAIL ({type(exc).__name__})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
