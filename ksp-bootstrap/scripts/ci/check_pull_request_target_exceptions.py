#!/usr/bin/env python3
"""Validate reviewed GitHub workflow trigger and zizmor exceptions with real YAML decoding."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])pull_request_target(?![A-Za-z0-9_])")
ZIZMOR_IGNORE_RE = re.compile(r"(?i)\bzizmor\s*:\s*ignore\[([^\]]+)\]")
RULE_RE = re.compile(r"[A-Za-z0-9_-]+")
ZIZMOR_CONFIG_PATHS = (
    "zizmor.yml",
    "zizmor.yaml",
    ".github/zizmor.yml",
    ".github/zizmor.yaml",
)


def active_text(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def _parsed_pull_request_target(text: str) -> bool:
    if yaml is None:
        raise ValueError("PyYAML is required for workflow trigger validation")
    try:
        document = yaml.load(text, Loader=yaml.BaseLoader)
    except yaml.YAMLError as exc:
        raise ValueError(f"workflow YAML parse failed: {exc}") from exc
    if document is None:
        return False
    if not isinstance(document, dict):
        raise ValueError("workflow YAML root must be a mapping")
    trigger = document.get("on")
    if isinstance(trigger, str):
        return trigger == "pull_request_target"
    if isinstance(trigger, list):
        return any(item == "pull_request_target" for item in trigger)
    if isinstance(trigger, dict):
        return "pull_request_target" in trigger
    if trigger is None:
        return False
    raise ValueError("workflow top-level on value must be a string, sequence, or mapping")


def uses_pull_request_target(text: str) -> bool:
    return _parsed_pull_request_target(text) or bool(TOKEN_RE.search(active_text(text)))


def zizmor_ignore_rules(text: str) -> set[str]:
    rules: set[str] = set()
    for match in ZIZMOR_IGNORE_RE.finditer(text):
        parts = [part.strip() for part in match.group(1).split(",")]
        if not parts:
            rules.add("__malformed__")
            continue
        for part in parts:
            rules.add(part.lower() if RULE_RE.fullmatch(part) else "__malformed__")
    return rules


def has_zizmor_ignore_comment(text: str) -> bool:
    return bool(zizmor_ignore_rules(text))


def load_allowlist(path: Path) -> tuple[list[tuple[str, str]], list[str]]:
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
            errors.append(f"workflow exception line {lineno} must be '<path> <rule-id>'")
            continue
        workflow, rule = fields
        if rule != "pull_request_target" and not rule.startswith("zizmor:"):
            errors.append(f"workflow exception line {lineno} has unsupported rule id: {rule}")
            continue
        if rule.startswith("zizmor:"):
            rule_name = rule.split(":", 1)[1]
            if not RULE_RE.fullmatch(rule_name):
                errors.append(f"workflow exception line {lineno} has malformed zizmor rule id: {rule}")
                continue
            rule = f"zizmor:{rule_name.lower()}"
        entries.append((workflow, rule))
    return entries, errors


def workflow_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in {".yml", ".yaml"}
    )


def validate(repo_root: Path, workflows_dir: Path, allowlist_path: Path) -> list[str]:
    errors: list[str] = []
    allowlist, parse_errors = load_allowlist(allowlist_path)
    errors.extend(parse_errors)
    if len(allowlist) != len(set(allowlist)):
        errors.append("workflow exception list contains duplicate path/rule entries")

    for config_path in ZIZMOR_CONFIG_PATHS:
        candidate = repo_root / config_path
        if candidate.exists() or candidate.is_symlink():
            errors.append(f"zizmor configuration file is prohibited: {config_path}")

    allowed = set(allowlist)
    detected: list[str] = []
    suppressed: dict[str, set[str]] = {}
    for workflow in workflow_files(workflows_dir):
        relative = workflow.relative_to(repo_root).as_posix()
        text = workflow.read_text()
        try:
            if uses_pull_request_target(text):
                detected.append(relative)
        except ValueError as exc:
            errors.append(f"workflow trigger validation failed: {relative}: {exc}")
        rules = zizmor_ignore_rules(text)
        if rules:
            suppressed[relative] = rules

    if (detected or suppressed) and not allowlist_path.exists():
        errors.append("workflow exceptions require a reviewed exact-path/rule allowlist")

    for workflow in detected:
        if (workflow, "pull_request_target") not in allowed:
            errors.append(f"pull_request_target workflow lacks reviewed exact-path/rule exception: {workflow}")

    for workflow, rules in suppressed.items():
        for rule in sorted(rules):
            if (workflow, f"zizmor:{rule}") not in allowed:
                errors.append(f"zizmor inline ignore lacks reviewed exact path/rule exception: {workflow} {rule}")

    for exception, rule in allowlist:
        target = repo_root / exception
        if not target.is_file():
            errors.append(f"stale workflow exception points to missing workflow: {exception} {rule}")
            continue
        text = target.read_text()
        if rule == "pull_request_target":
            try:
                present = uses_pull_request_target(text)
            except ValueError as exc:
                errors.append(f"workflow trigger validation failed: {exception}: {exc}")
                continue
            if not present:
                errors.append(f"stale workflow exception points to workflow without pull_request_target: {exception}")
            continue
        rule_name = rule.split(":", 1)[1]
        if rule_name not in zizmor_ignore_rules(text):
            errors.append(f"stale workflow exception points to workflow without matching zizmor ignore: {exception} {rule_name}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--workflows-dir", default=".github/workflows")
    parser.add_argument("--allowlist", default="governance/workflow-exceptions/pull-request-target.txt")
    args = parser.parse_args()
    root = Path(args.repo_root).resolve()
    errors = validate(root, root / args.workflows_dir, root / args.allowlist)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("pull_request_target/zizmor exception validation PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
