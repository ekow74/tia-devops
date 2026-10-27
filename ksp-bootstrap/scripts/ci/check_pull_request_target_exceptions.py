#!/usr/bin/env python3
"""Validate reviewed pull_request_target and zizmor exceptions without a YAML dependency."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])pull_request_target(?![A-Za-z0-9_])")
ZIZMOR_IGNORE_RE = re.compile(r"(?i)\bzizmor\s*:\s*ignore\[")


def split_yaml_comment(line: str) -> tuple[str, str]:
    """Split code from a YAML comment while preserving # inside quoted strings."""
    out: list[str] = []
    single = False
    double = False
    escaped = False
    for index, ch in enumerate(line):
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
            return "".join(out), line[index + 1 :]
        out.append(ch)
    return "".join(out), ""


def strip_comment(line: str) -> str:
    return split_yaml_comment(line)[0]


def uses_pull_request_target(text: str) -> bool:
    """Fail closed: any non-comment token requires an exact reviewed exception."""
    stripped = "\n".join(strip_comment(line) for line in text.splitlines())
    return bool(TOKEN_RE.search(stripped))


def has_zizmor_ignore_comment(text: str) -> bool:
    """Return True for an actual YAML comment carrying a zizmor inline ignore."""
    for line in text.splitlines():
        _, comment = split_yaml_comment(line)
        if comment and ZIZMOR_IGNORE_RE.search(comment):
            return True
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

    allowed = set(allowlist)
    detected: list[str] = []
    suppressed: list[str] = []
    for workflow in workflow_files(workflows_dir):
        relative = workflow.relative_to(repo_root).as_posix()
        text = workflow.read_text()
        if uses_pull_request_target(text):
            detected.append(relative)
        if has_zizmor_ignore_comment(text):
            suppressed.append(relative)

    if (detected or suppressed) and not allowlist_path.exists():
        errors.append(
            "pull_request_target/zizmor exceptions are prohibited unless each workflow has a reviewed exact-path exception"
        )

    for workflow in detected:
        if workflow not in allowed:
            errors.append(f"pull_request_target workflow lacks reviewed exact-path exception: {workflow}")

    for workflow in suppressed:
        if workflow not in allowed:
            errors.append(f"zizmor inline ignore lacks reviewed exact-path exception: {workflow}")

    for exception in allowlist:
        target = repo_root / exception
        if not target.is_file():
            errors.append(f"stale pull_request_target exception points to missing workflow: {exception}")
            continue
        text = target.read_text()
        if not uses_pull_request_target(text) and not has_zizmor_ignore_comment(text):
            errors.append(
                f"stale pull_request_target exception points to workflow without pull_request_target or zizmor ignore: {exception}"
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
    print("pull_request_target/zizmor exception validation PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
