#!/usr/bin/env python3
"""Validate reviewed pull_request_target exceptions without a YAML dependency."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])pull_request_target(?![A-Za-z0-9_])")
ON_RE = re.compile(r"^(?P<indent>[ \t]*)on\s*:\s*(?P<rest>.*)$")


def strip_comment(line: str) -> str:
    """Strip YAML comments while preserving # inside quoted strings."""
    out: list[str] = []
    single = False
    double = False
    escaped = False
    for ch in line:
        if escaped:
            out.append(ch)
            escaped = False
            continue
        if double and ch == "\\":
            out.append(ch)
            escaped = True
            continue
        if not double and ch == "'":
            single = not single
            out.append(ch)
            continue
        if not single and ch == '"':
            double = not double
            out.append(ch)
            continue
        if ch == "#" and not single and not double:
            break
        out.append(ch)
    return "".join(out)


def indent_width(prefix: str) -> int:
    return len(prefix.expandtabs(8))


def uses_pull_request_target(text: str) -> bool:
    lines = text.splitlines()
    for i, raw in enumerate(lines):
        line = strip_comment(raw).rstrip()
        if not line.strip():
            continue
        match = ON_RE.match(line)
        if not match:
            continue

        base_indent = indent_width(match.group("indent"))
        rest = match.group("rest").strip()
        if rest:
            return bool(TOKEN_RE.search(rest))

        for nested_raw in lines[i + 1 :]:
            nested = strip_comment(nested_raw).rstrip()
            if not nested.strip():
                continue
            leading = nested[: len(nested) - len(nested.lstrip(" \t"))]
            if indent_width(leading) <= base_indent:
                break
            if TOKEN_RE.search(nested.strip()):
                return True
        return False
    return False


def load_allowlist(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def workflow_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(
        [p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in {".yml", ".yaml"}]
    )


def validate(repo_root: Path, workflows_dir: Path, allowlist_path: Path) -> list[str]:
    errors: list[str] = []
    allowlist = load_allowlist(allowlist_path)
    if len(allowlist) != len(set(allowlist)):
        errors.append("pull_request_target exception list contains duplicate paths")

    detected: list[str] = []
    for workflow in workflow_files(workflows_dir):
        if uses_pull_request_target(workflow.read_text()):
            detected.append(workflow.relative_to(repo_root).as_posix())

    if detected and not allowlist_path.exists():
        errors.append("pull_request_target is prohibited unless each workflow has a reviewed exact-path exception")

    allowed = set(allowlist)
    for workflow in detected:
        if workflow not in allowed:
            errors.append(f"pull_request_target workflow lacks reviewed exact-path exception: {workflow}")

    for exception in allowlist:
        target = repo_root / exception
        if not target.is_file():
            errors.append(f"stale pull_request_target exception points to missing workflow: {exception}")
            continue
        if not uses_pull_request_target(target.read_text()):
            errors.append(
                f"stale pull_request_target exception points to workflow without pull_request_target: {exception}"
            )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--workflows-dir", default=".github/workflows")
    parser.add_argument(
        "--allowlist", default="governance/workflow-exceptions/pull-request-target.txt"
    )
    args = parser.parse_args()

    root = Path(args.repo_root).resolve()
    workflows = root / args.workflows_dir
    allowlist = root / args.allowlist
    errors = validate(root, workflows, allowlist)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("pull_request_target exception validation PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
