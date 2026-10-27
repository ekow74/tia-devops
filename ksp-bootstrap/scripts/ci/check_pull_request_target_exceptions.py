#!/usr/bin/env python3
"""Validate reviewed pull_request_target and zizmor exceptions without a YAML dependency."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])pull_request_target(?![A-Za-z0-9_])")
ZIZMOR_IGNORE_RE = re.compile(r"(?i)\bzizmor\s*:\s*ignore\[([^\]]+)\]")
RULE_RE = re.compile(r"[A-Za-z0-9_-]+")


def active_text(text: str) -> str:
    """Drop only full-line YAML comments; keep inline text fail-closed."""
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def has_double_quoted_escape(text: str) -> bool:
    """Reject quoted escapes conservatively because YAML may decode hidden trigger keys."""
    in_double = False
    for ch in active_text(text):
        if in_double:
            if ch == "\\":
                return True
            if ch == '"':
                in_double = False
        elif ch == '"':
            in_double = True
    return False


def uses_pull_request_target(text: str) -> bool:
    """Fail closed for literal trigger tokens or YAML double-quoted escapes."""
    body = active_text(text)
    return bool(TOKEN_RE.search(body)) or has_double_quoted_escape(body)


def zizmor_ignore_rules(text: str) -> set[str]:
    """Return every inline ignore rule seen in raw text; over-detection is intentional."""
    rules: set[str] = set()
    for match in ZIZMOR_IGNORE_RE.finditer(text):
        body = match.group(1)
        parts = [part.strip() for part in body.split(",")]
        if not parts:
            rules.add("__malformed__")
            continue
        for part in parts:
            if RULE_RE.fullmatch(part):
                rules.add(part.lower())
            else:
                rules.add("__malformed__")
    return rules


def has_zizmor_ignore_comment(text: str) -> bool:
    """Compatibility helper used by regression tests."""
    return bool(zizmor_ignore_rules(text))


def load_allowlist(path: Path) -> tuple[list[tuple[str, str]], list[str]]:
    """Load exact (workflow path, rule) exceptions and report malformed entries."""
    if not path.exists():
        return [], []
    entries: list[tuple[str, str]] = []
    errors: list[str] = []
    for lineno, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) != 2:
            errors.append(
                f"workflow exception line {lineno} must be '<path> <rule-id>'"
            )
            continue
        workflow, rule = fields
        if rule != "pull_request_target" and not rule.startswith("zizmor:"):
            errors.append(
                f"workflow exception line {lineno} has unsupported rule id: {rule}"
            )
            continue
        if rule.startswith("zizmor:"):
            rule_name = rule.split(":", 1)[1]
            if not RULE_RE.fullmatch(rule_name):
                errors.append(
                    f"workflow exception line {lineno} has malformed zizmor rule id: {rule}"
                )
                continue
            rule = f"zizmor:{rule_name.lower()}"
        entries.append((workflow, rule))
    return entries, errors


def workflow_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(
        [p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in {".yml", ".yaml"}]
    )


def validate(repo_root: Path, workflows_dir: Path, allowlist_path: Path) -> list[str]:
    errors: list[str] = []
    allowlist, parse_errors = load_allowlist(allowlist_path)
    errors.extend(parse_errors)
    if len(allowlist) != len(set(allowlist)):
        errors.append("workflow exception list contains duplicate path/rule entries")

    allowed = set(allowlist)
    detected: list[str] = []
    suppressed: dict[str, set[str]] = {}
    for workflow in workflow_files(workflows_dir):
        relative = workflow.relative_to(repo_root).as_posix()
        text = workflow.read_text()
        if uses_pull_request_target(text):
            detected.append(relative)
        rules = zizmor_ignore_rules(text)
        if rules:
            suppressed[relative] = rules

    if (detected or suppressed) and not allowlist_path.exists():
        errors.append(
            "pull_request_target/zizmor exceptions are prohibited unless each workflow/rule has a reviewed exact-path exception"
        )

    for workflow in detected:
        if (workflow, "pull_request_target") not in allowed:
            errors.append(
                f"pull_request_target workflow lacks reviewed exact-path/rule exception: {workflow}"
            )

    for workflow, rules in suppressed.items():
        for rule in sorted(rules):
            key = (workflow, f"zizmor:{rule}")
            if key not in allowed:
                errors.append(
                    f"zizmor inline ignore lacks reviewed exact path/rule exception: {workflow} {rule}"
                )

    for exception, rule in allowlist:
        target = repo_root / exception
        if not target.is_file():
            errors.append(f"stale workflow exception points to missing workflow: {exception} {rule}")
            continue
        text = target.read_text()
        if rule == "pull_request_target":
            if not uses_pull_request_target(text):
                errors.append(
                    f"stale workflow exception points to workflow without pull_request_target: {exception}"
                )
            continue
        rule_name = rule.split(":", 1)[1]
        if rule_name not in zizmor_ignore_rules(text):
            errors.append(
                f"stale workflow exception points to workflow without matching zizmor ignore: {exception} {rule_name}"
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
