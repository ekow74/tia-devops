from __future__ import annotations

import importlib.util
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import urllib.error
import urllib.parse
import urllib.request

import yaml

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
MODULE_PATH = ROOT / "scripts/ci/phase0_review_gate.py"
AUTHORITY = ROOT / "scripts/ci/check_gate_integrity.py"
AUTHORITY_WORKFLOW = REPO_ROOT / ".github/workflows/ksp-gate-authority.yml"
CI_WORKFLOW = REPO_ROOT / ".github/workflows/ci.yml"
WORKFLOW = REPO_ROOT / ".github/workflows/phase0-review-gate.yml"
ALLOWLIST = REPO_ROOT / "governance/workflow-exceptions/pull-request-target.txt"
AUTHORITY_POLICY = ROOT / "policies/gate-authority.json"
FIXTURES = ROOT / "tests/fixtures/reviewer"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


review = load_module("phase0_review_gate", MODULE_PATH)
authority = load_module("phase0_gate_authority", AUTHORITY)
POLICY = json.loads(AUTHORITY_POLICY.read_text())
SHA = "a" * 40
BASE = "b" * 40
POLICY_SHA = "d" * 40


def envelope(verdict: str, identity: str, reviewed: bool) -> dict:
    return review.make_verdict(
        repository="ekow74/tia-devops",
        pr_number=11,
        head_sha=SHA,
        base_sha=BASE,
        policy_sha=POLICY_SHA,
        reviewer_identity=identity,
        verdict=verdict,
        artifact_reference="run:https://github.com/ekow74/tia-devops/actions/runs/123",
        workflow_version="phase0-review/v0+" + "c" * 40,
        run_id=123,
        reviewed=reviewed,
    )


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=root, text=True, stderr=subprocess.STDOUT
    ).strip()


def exercise_run_gate(
    files: list[dict],
    *,
    changed_counts: tuple[int, int] | None = None,
    post_head_sha: str = SHA,
    post_base_sha: str = BASE,
    base_ref: str = "main",
    event_head_sha: str = SHA,
    workflow_sha_override: str | None = None,
    trusted_env_sha: str | None = None,
    advance_checkout: bool = False,
) -> tuple[dict | None, Exception | None, str, str, str]:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        git(root, "init", "-q")
        git(root, "config", "user.email", "test@example.invalid")
        git(root, "config", "user.name", "Phase0 test")
        workflow = root / ".github/workflows/phase0-review-gate.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text(
            "on: pull_request_target\npermissions: {contents: read}\n"
            "jobs: {review: {runs-on: ubuntu-latest}}\n"
        )
        policy = root / "ksp-bootstrap/policies/protected-paths.txt"
        policy.parent.mkdir(parents=True)
        policy.write_text("protected/\n")
        git(root, "add", ".")
        git(root, "commit", "-q", "-m", "trusted workflow source")
        trusted_sha = git(root, "rev-parse", "HEAD")
        expected_workflow_version = (
            "phase0-review/v0+"
            + git(root, "rev-parse", f"{trusted_sha}:{review.WORKFLOW_PATH}")
        )
        if advance_checkout:
            git(root, "commit", "--allow-empty", "-q", "-m", "checkout mismatch")

        base_ref_sha = BASE
        pr_snapshots = [
            {
                "head": {"sha": SHA},
                "base": {"sha": base_ref_sha},
                "changed_files": (
                    changed_counts[0] if changed_counts is not None else len(files)
                ),
            },
            {
                "head": {"sha": post_head_sha},
                "base": {"sha": post_base_sha},
                "changed_files": (
                    changed_counts[1] if changed_counts is not None else len(files)
                ),
            },
        ]
        event_path = root / "event.json"
        event_path.write_text(
            json.dumps(
                {
                    "number": 11,
                    "repository": {"full_name": "ekow74/tia-devops"},
                    "pull_request": {
                        "head": {"sha": event_head_sha},
                        "base": {"sha": base_ref_sha, "ref": base_ref},
                    },
                }
            )
        )
        env = {
            "GITHUB_API_URL": "https://api.github.test",
            "GITHUB_EVENT_PATH": str(event_path),
            "GITHUB_REPOSITORY": "ekow74/tia-devops",
            "GITHUB_RUN_ID": "123",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_TOKEN": "test-token",
            "GITHUB_WORKFLOW_SHA": workflow_sha_override or trusted_sha,
            "KSP_TRUSTED_SHA": trusted_env_sha or workflow_sha_override or trusted_sha,
            "KSP_BASE_SHA": base_ref_sha,
            "KSP_DEFAULT_BRANCH": "main",
            "KSP_EVENT_BASE_REF": base_ref,
            "KSP_EVENT_HEAD_SHA": SHA,
            "KSP_PR_NUMBER": "11",
        }
        pr_index = 0

        def fake_api_get(_api_root: str, route: str, _token: str):
            nonlocal pr_index
            parsed = urllib.parse.urlsplit(route)
            if parsed.path.endswith("/pulls/11"):
                result = pr_snapshots[pr_index]
                pr_index += 1
                return result, None
            page = int(urllib.parse.parse_qs(parsed.query)["page"][0])
            start = (page - 1) * review.PAGE_SIZE
            page_files = files[start : start + review.PAGE_SIZE]
            link = (
                "<https://api.github.test/repositories/123/pulls/11/files?"
                f"per_page=100&page={page + 1}>; rel=\"next\""
                if len(page_files) == review.PAGE_SIZE
                else None
            )
            return page_files, link

        output = io.StringIO()
        try:
            with mock.patch.object(review, "_api_get", side_effect=fake_api_get):
                with contextlib.redirect_stdout(output):
                    result = review.run_gate(env, trusted_root=root)
            return result, None, output.getvalue(), trusted_sha, expected_workflow_version
        except Exception as exc:
            return None, exc, output.getvalue(), trusted_sha, expected_workflow_version


class Phase0ReviewGateTests(unittest.TestCase):
    def test_base_policy_classifies_renames_and_deletions(self):
        patterns = review.parse_patterns(
            "protected/**\nother/\n# ignored\n"
        )
        files = [
            {
                "filename": "unprotected/new.txt",
                "previous_filename": "protected/renamed.txt",
                "status": "renamed",
            },
            {"filename": "other/deleted.txt", "status": "removed"},
            {"filename": "unprotected/edit.txt", "status": "modified"},
        ]
        self.assertEqual(
            review.classify_files(files, patterns),
            ["other/deleted.txt", "protected/renamed.txt"],
        )

    def test_unknown_status_and_invalid_paths_fail_closed(self):
        for item in (
            {"filename": "new.txt", "status": "unknown"},
            {"filename": "../protected/file", "status": "removed"},
            {
                "filename": "new.txt",
                "previous_filename": "../protected/file",
                "status": "renamed",
            },
        ):
            with self.subTest(item=item), self.assertRaises(review.GateError):
                review.paths_from_file_listing([item])

    def test_pagination_requires_consistent_complete_pages(self):
        pages = {
            1: ([{"filename": f"file-{i}", "status": "added"} for i in range(100)],
                '<https://api.github.com/repositories/123/pulls/11/files?per_page=100&page=2>; rel="next"'),
            2: ([{"filename": "last", "status": "added"}], None),
        }
        result = review.collect_paginated_files(lambda page: pages.get(page, ([], None)), 101)
        self.assertEqual(len(result), 101)

        for count in (99, 100, 101, 200, 3000):
            entries = [
                {"filename": f"file-{index}", "status": "added"}
                for index in range(count)
            ]
            requested_pages: list[int] = []

            def fetch(page: int):
                requested_pages.append(page)
                start = (page - 1) * review.PAGE_SIZE
                return entries[start : start + review.PAGE_SIZE], (
                    '<https://api.github.com/repositories/123/pulls/11/files?'
                    f'per_page=100&page={page + 1}>; rel="next"'
                    if start + review.PAGE_SIZE < count
                    else None
                )

            self.assertEqual(
                review.collect_paginated_files(fetch, count), entries
            )
            self.assertEqual(requested_pages[-1], (count + 99) // 100 + (count % 100 == 0 and count < 3000))

    def test_pagination_count_and_duplicate_entries_fail_closed(self):
        def full_page(_page):
            return (
                [{"filename": "file.txt", "status": "added"}] * 100,
                '<https://api.github.com/next?page=2>; rel="next"',
            )

        with self.assertRaisesRegex(review.GateError, "outside the API completeness bound"):
            review.collect_paginated_files(full_page, 3001)
        with self.assertRaisesRegex(review.GateError, "duplicate filename"):
            review.collect_paginated_files(
                lambda _page: (
                    [{"filename": "same.txt", "status": "added"}] * 2,
                    None,
                ),
                2,
            )
        with self.assertRaisesRegex(review.GateError, "disagrees with pull-request metadata"):
            review.collect_paginated_files(
                lambda _page: ([{"filename": "one.txt", "status": "added"}], None),
                2,
            )

    def test_api_listing_uses_local_pages_and_ignores_link_targets(self):
        requests: list[str] = []

        def fake_api_get(_api_root: str, route: str, _token: str):
            requests.append(route)
            parsed = urllib.parse.urlsplit(route)
            page = int(urllib.parse.parse_qs(parsed.query)["page"][0])
            if page == 1:
                return (
                    [{"filename": f"file-{index}", "status": "added"} for index in range(100)],
                    '<https://api.github.com/repositories/123/pulls/11/files?per_page=100&page=2>; rel="next"',
                )
            return ([{"filename": "last", "status": "added"}], None)

        with mock.patch.object(review, "_api_get", side_effect=fake_api_get):
            files = review._api_file_listing(
                "https://api.github.com", "ekow74/tia-devops", 11, "test-token", 101
            )
        self.assertEqual(len(files), 101)
        self.assertEqual(len(requests), 2)
        self.assertIn("page=1", requests[0])
        self.assertIn("page=2", requests[1])

    def test_api_transport_refuses_redirects(self):
        handler = review._RejectRedirects()
        request = urllib.request.Request("https://api.github.com/test")
        self.assertIsNone(
            handler.redirect_request(request, None, 302, "Found", {}, "https://evil.invalid")
        )
        opener = mock.Mock()
        opener.open.side_effect = urllib.error.HTTPError(
            "https://api.github.com/test", 302, "Found", {}, None
        )
        with mock.patch.object(
            review.urllib.request, "build_opener", return_value=opener
        ) as build_opener:
            with self.assertRaises(review.GateError):
                review._api_get("https://api.github.com", "/test", "test-token")
        self.assertIsInstance(build_opener.call_args.args[0], review._RejectRedirects)
        opener.open.assert_called_once()

    def test_strict_verdict_schema_and_exact_bindings(self):
        value = envelope("UNAVAILABLE", "provider-unconfigured", False)
        expected = {
            "repository": "ekow74/tia-devops",
            "pr_number": 11,
            "head_sha": SHA,
            "base_sha": BASE,
            "policy_sha": POLICY_SHA,
            "run_id": 123,
        }
        self.assertEqual(
            review.validate_verdict(value, expected=expected, protected=True), value
        )
        for mutate in (
            lambda v: v.update(extra="field"),
            lambda v: v.update(head_sha="HEAD"),
            lambda v: v.update(policy_sha="bad"),
            lambda v: v.update(run_id=124),
            lambda v: v.update(verdict="NOT_REQUIRED", reviewer_identity="not-reviewed"),
            lambda v: v.update(
                artifact_reference="run:https://github.com/ekow74/tia-devops/actions/runs/999"
            ),
            lambda v: v.update(workflow_version="phase0-review/v0+unknown"),
        ):
            changed = dict(value)
            mutate(changed)
            with self.subTest(changed=changed), self.assertRaises(review.GateError):
                review.validate_verdict(changed, expected=expected, protected=True)

    def test_not_required_is_explicitly_not_reviewed(self):
        value = envelope("NOT_REQUIRED", "not-reviewed", False)
        self.assertEqual(
            review.validate_verdict(value, protected=False)["verdict"],
            "NOT_REQUIRED",
        )
        with self.assertRaisesRegex(review.GateError, "must not claim"):
            review.validate_verdict(
                envelope("NOT_REQUIRED", "offline-test-adapter", True),
                protected=False,
            )

    def test_run_gate_uses_workflow_source_sha_separate_from_pr_base(self):
        result, error, output, trusted_sha, workflow_version = exercise_run_gate(
            [{"filename": "docs/readme.md", "status": "modified"}]
        )
        self.assertIsNone(error)
        self.assertEqual(result["verdict"], "NOT_REQUIRED")
        self.assertFalse(result["reviewed"])
        self.assertEqual(result["base_sha"], BASE)
        self.assertEqual(result["policy_sha"], trusted_sha)
        self.assertNotEqual(result["policy_sha"], result["base_sha"])
        self.assertEqual(result["workflow_version"], workflow_version)
        self.assertIn('"policy_sha":', output)

    def test_run_gate_rejects_context_checkout_and_pr_races(self):
        cases = (
            (
                "event head mismatch",
                {"event_head_sha": "e" * 40},
                "workflow event binding is invalid",
            ),
            (
                "workflow source context mismatch",
                {"trusted_env_sha": "c" * 40},
                "workflow event binding is invalid",
            ),
            (
                "non-default base",
                {"base_ref": "release"},
                "workflow event binding is invalid",
            ),
            (
                "checkout mismatch",
                {"advance_checkout": True},
                "trusted checkout does not match the workflow source SHA",
            ),
            (
                "head changed after listing",
                {"post_head_sha": "f" * 40},
                "pull request head or base changed during review",
            ),
            (
                "base changed after listing",
                {"post_base_sha": "f" * 40},
                "pull request head or base changed during review",
            ),
            (
                "metadata file count changed",
                {"changed_counts": (1, 2)},
                "pull-request file count changed during classification",
            ),
        )
        for label, kwargs, expected_error in cases:
            with self.subTest(case=label):
                _, error, _, _, _ = exercise_run_gate(
                    [{"filename": "docs/readme.md", "status": "modified"}],
                    **kwargs,
                )
                self.assertIsInstance(error, review.GateError)
                self.assertIn(expected_error, str(error))

    def test_run_gate_fails_closed_for_incomplete_and_protected_listings(self):
        _, error, _, _, _ = exercise_run_gate(
            [{"filename": "docs/readme.md", "status": "modified"}],
            changed_counts=(2, 2),
        )
        self.assertIsInstance(error, review.GateError)
        self.assertIn("disagrees with pull-request metadata", str(error))
        _, error, _, _, _ = exercise_run_gate(
            [{"filename": "docs/readme.md", "status": "modified"}],
            changed_counts=(3001, 3001),
        )
        self.assertIsInstance(error, review.GateError)
        self.assertIn("exceeds 3000", str(error))

        _, error, output, _, _ = exercise_run_gate(
            [{"filename": "protected/file.txt", "status": "modified"}]
        )
        self.assertIsInstance(error, review.GateError)
        self.assertIn("no authorized independent reviewer", str(error))
        self.assertEqual(json.loads(output)["verdict"], "UNAVAILABLE")
        self.assertEqual(json.loads(output)["reviewer_identity"], "provider-unconfigured")

    def test_test_adapter_does_not_follow_injected_instructions(self):
        fixture = json.loads((FIXTURES / "prompt-injection.json").read_text())
        verdict, reviewed = review.OfflineTestAdapter().review(fixture)
        self.assertEqual((verdict, reviewed), ("APPROVED", True))
        value = envelope(verdict, "offline-test-adapter", reviewed)
        self.assertEqual(
            review.validate_verdict(value, protected=True)["reviewer_identity"],
            "offline-test-adapter",
        )
        self.assertNotIn("GITHUB_TOKEN", json.dumps(value))

    def test_unavailable_adapter_fails_closed_for_protected_paths(self):
        verdict, reviewed = review.UnconfiguredAdapter().review(["ksp-bootstrap/tests/x"])
        self.assertEqual((verdict, reviewed), ("UNAVAILABLE", False))
        value = envelope(verdict, "provider-unconfigured", reviewed)
        self.assertEqual(
            review.validate_verdict(value, protected=True)["verdict"],
            "UNAVAILABLE",
        )

    def test_permission_policy_blocks_untrusted_check_and_status_writers(self):
        for filename in (
            "permission-checks-write.yml",
            "permission-block-scalar.yml",
            "permission-statuses-write-head.yml",
            "permission-alias-key.yml",
            "permission-explicit-key.yml",
            "permission-line-continuation.yml",
        ):
            with self.subTest(fixture=filename), self.assertRaises(ValueError):
                authority.validate_workflow_permissions(
                    ".github/workflows/untrusted.yml",
                    (FIXTURES / filename).read_text(),
                    POLICY,
                )

        reproductions = (
            (
                "flow root mapping",
                "{name: test, on: push, permissions: {statuses: write}, "
                "jobs: {test: {runs-on: ubuntu-latest, steps: [{run: echo test}]}}}",
            ),
            (
                "job flow permission without explicit workflow permission",
                "on: push\n"
                "jobs: {test: {permissions: {statuses: write}, "
                "runs-on: ubuntu-latest, steps: [{run: echo test}]}}",
            ),
            (
                "flow job permission mapping",
                "on: push\npermissions: {contents: read}\n"
                "jobs: {test: {permissions: {statuses: write}, "
                "runs-on: ubuntu-latest, steps: [{run: echo test}]}}",
            ),
        )
        for label, workflow in reproductions:
            expected_error = (
                "workflow-level permissions must be explicit"
                if label.startswith("job flow permission without")
                else "outside reviewed authority paths"
            )
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, expected_error):
                authority.validate_workflow_permissions(
                    ".github/workflows/untrusted.yml", workflow, POLICY
                )

        for scope in ("checks", "statuses"):
            with self.subTest(scope=scope), self.assertRaisesRegex(
                ValueError, "outside reviewed authority paths"
            ):
                authority.validate_workflow_permissions(
                    ".github/workflows/untrusted.yml",
                    f"on: push\npermissions: {{contents: read}}\n"
                    f"jobs: {{test: {{permissions: {{{scope}: write}}}}}}",
                    POLICY,
                )

        for trigger, scope in (
            ("push", "actions"),
            ("pull_request_review", "issues"),
            ("pull_request_review_comment", "id-token"),
            ("merge_group", "security-events"),
            ("workflow_dispatch", "actions"),
        ):
            workflow = (
                f"on: {trigger}\npermissions: {{contents: read}}\n"
                f"jobs:\n  test:\n    permissions:\n      {scope}: write\n"
                "    runs-on: ubuntu-latest\n"
            )
            with self.subTest(trigger=trigger, scope=scope), self.assertRaisesRegex(
                ValueError, "write permission"
            ):
                authority.validate_workflow_permissions(
                    ".github/workflows/untrusted.yml", workflow, POLICY
                )

        with self.assertRaisesRegex(ValueError, "exact reviewed exception"):
            authority.validate_workflow_permissions(
                ".github/workflows/security-deep.yml",
                "on: [schedule, workflow_dispatch]\n"
                "permissions: {contents: read}\n"
                "jobs: {scorecard: {permissions: {contents: read, id-token: write, "
                "security-events: write, actions: write}, runs-on: ubuntu-latest}}",
                POLICY,
            )

        for workflow in (
            "on: pull_request_target\npermissions: {checks: write}\n"
            "jobs: {review: {runs-on: ubuntu-latest}}",
            "on: pull_request_target\npermissions: {statuses: write}\n"
            "jobs: {review: {permissions: {contents: read, statuses: write}}}",
        ):
            self.assertIsNone(
                authority.validate_workflow_permissions(
                    ".github/workflows/phase0-review-gate.yml", workflow, POLICY
                )
            )

        for workflow in (
            "on: {pull_request_target: {}, workflow_dispatch: {}}\n"
            "permissions: {checks: write}\njobs: {review: {runs-on: ubuntu-latest}}",
            "on: {pull_request_target: {}, push: {}}\n"
            "permissions: {statuses: write}\njobs: {review: {runs-on: ubuntu-latest}}",
            "on: {pull_request_target: {}, workflow_dispatch: {}}\n"
            "permissions: {contents: read}\n"
            "jobs: {review: {permissions: {checks: write}, runs-on: ubuntu-latest}}",
        ):
            with self.subTest(workflow=workflow), self.assertRaisesRegex(
                ValueError, "requires pull_request_target only"
            ):
                authority.validate_workflow_permissions(
                    ".github/workflows/phase0-review-gate.yml", workflow, POLICY
                )

        read_only = (
            "on: [push, pull_request]\npermissions: {contents: read}\n"
            "jobs: {build: {runs-on: ubuntu-latest}, "
            "test: {permissions: read-all, runs-on: ubuntu-latest}}"
        )
        self.assertIsNone(
            authority.validate_workflow_permissions(
                ".github/workflows/untrusted.yml", read_only, POLICY
            )
        )
        with self.assertRaisesRegex(ValueError, "repository defaults are unverified"):
            authority.validate_workflow_permissions(
                ".github/workflows/untrusted.yml",
                "on: push\njobs: {test: {runs-on: ubuntu-latest}}",
                POLICY,
            )

        with self.assertRaisesRegex(ValueError, "outside reviewed authority paths"):
            authority.validate_workflow_permissions(
                ".github/workflows/untrusted.yml",
                "on: push\npermissions: {statuses: write}\n"
                "jobs: {test: {permissions: {statuses: write}}}",
                POLICY,
            )
        self.assertIsNone(
            authority.validate_workflow_permissions(
                ".github/workflows/phase0-review-gate.yml",
                "on: pull_request_target\npermissions: {statuses: write}\n"
                "jobs: {review: {permissions: {contents: read}}}",
                POLICY,
            )
        )
        self.assertIsNone(
            authority.validate_workflow_permissions(
                ".github/workflows/untrusted.yml",
                "on: push\npermissions: write-all\n"
                "jobs: {build: {permissions: {contents: read}}}",
                POLICY,
            )
        )

    def test_reserved_read_only_check_names_require_trusted_target_path(self):
        for filename in (
            "reserved-phase0-name.yml",
            "reserved-independent-review-name.yml",
            "reserved-authority-name.yml",
        ):
            with self.subTest(fixture=filename), self.assertRaisesRegex(
                ValueError, "reserved check name outside safe authority context"
            ):
                authority.validate_workflow_permissions(
                    ".github/workflows/untrusted.yml",
                    (FIXTURES / filename).read_text(),
                    POLICY,
                )

        authorized = (
            "on: pull_request_target\npermissions: {contents: read}\n"
            "jobs: {review: {name: 'Phase-0 reviewer scaffold (not required)', "
            "runs-on: ubuntu-latest}}"
        )
        self.assertIsNone(
            authority.validate_workflow_permissions(
                ".github/workflows/phase0-review-gate.yml", authorized, POLICY
            )
        )
        for trigger in ("pull_request", "workflow_dispatch"):
            unsafe = authorized.replace("pull_request_target", trigger)
            with self.subTest(trigger=trigger), self.assertRaisesRegex(
                ValueError, "reserved check name outside safe authority context"
            ):
                authority.validate_workflow_permissions(
                    ".github/workflows/phase0-review-gate.yml", unsafe, POLICY
                )

        computed = (
            "on: push\npermissions: {contents: read}\n"
            "jobs: {test: {name: '${{ matrix.name }}', runs-on: ubuntu-latest}}"
        )
        with self.assertRaisesRegex(ValueError, "computed or invalid check name"):
            authority.validate_workflow_permissions(
                ".github/workflows/untrusted.yml", computed, POLICY
            )

    def test_authority_policy_rejects_false_trigger_unknown_scope_and_unexpected_files(self):
        for workflow in (
            "off: push\npermissions: {contents: read}\n"
            "jobs: {test: {runs-on: ubuntu-latest}}",
            "on: push\npermissions: {models: write}\n"
            "jobs: {test: {runs-on: ubuntu-latest}}",
        ):
            with self.subTest(workflow=workflow), self.assertRaises(ValueError):
                authority.validate_workflow_permissions(
                    ".github/workflows/untrusted.yml", workflow, POLICY
                )

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            git(repo, "init", "-q")
            git(repo, "config", "user.email", "test@example.invalid")
            git(repo, "config", "user.name", "Phase0 test")
            policy_path = repo / "ksp-bootstrap/policies/gate-authority.json"
            policy_path.parent.mkdir(parents=True)
            policy_path.write_text(AUTHORITY_POLICY.read_text())
            workflow = repo / ".github/workflows/safe.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "on: push\npermissions: {contents: read}\n"
                "jobs: {test: {runs-on: ubuntu-latest}}\n"
            )
            git(repo, "add", ".")
            git(repo, "commit", "-q", "-m", "trusted base")
            base = git(repo, "rev-parse", "HEAD")
            (repo / ".github/workflows/not-a-workflow.txt").write_text("data")
            (repo / ".github/workflows/uppercase.YML").write_text("data")
            git(repo, "add", ".")
            git(repo, "commit", "-q", "-m", "unexpected workflow entry")
            head = git(repo, "rev-parse", "HEAD")
            authority.configure_repo(repo)
            try:
                with self.assertRaisesRegex(ValueError, "unsupported workflow entry"):
                    authority.validate_head_workflow_permissions(base, head)
            finally:
                authority.configure_repo(REPO_ROOT)

    def test_permission_decoder_handles_aliases_merges_and_write_all(self):
        merged_permissions = (
            "on: pull_request_target\n"
            "read_permissions: &read_permissions {contents: read}\n"
            "write_permission: &write_permission {checks: write}\n"
            "permissions:\n  <<: [*read_permissions, *write_permission]\n"
            "jobs: {review: {runs-on: ubuntu-latest}}"
        )
        self.assertIsNone(
            authority.validate_workflow_permissions(
                ".github/workflows/phase0-review-gate.yml",
                merged_permissions,
                POLICY,
            )
        )

        for permissions in (
            "write-all",
            "{checks: write}",
            '{\"chec\\u006bs\": write}',
            '{checks: \"writ\\u0065\"}',
        ):
            workflow = (
                "on: push\npermissions: "
                + permissions
                + "\njobs: {test: {runs-on: ubuntu-latest}}"
            )
            with self.subTest(permissions=permissions), self.assertRaises(ValueError):
                authority.validate_workflow_permissions(
                    ".github/workflows/untrusted.yml", workflow, POLICY
                )

        malformed = (
            "on: push\npermissions: {checks: [write]}\n"
            "jobs: {test: {runs-on: ubuntu-latest}}",
            "on: push\npermissions: {checks: read, checks: write}\n"
            "jobs: {test: {runs-on: ubuntu-latest}}",
            "on: push\npermissions: {unknown-scope: write}\n"
            "jobs: {test: {runs-on: ubuntu-latest}}",
        )
        for workflow in malformed:
            with self.subTest(workflow=workflow), self.assertRaises(ValueError):
                authority.validate_workflow_permissions(
                    ".github/workflows/untrusted.yml", workflow, POLICY
                )

        mixed_triggers = (
            "on: [pull_request_target, pull_request]\n"
            "permissions: {checks: write}\n"
            "jobs: {review: {runs-on: ubuntu-latest}}"
        )
        with self.assertRaisesRegex(ValueError, "requires pull_request_target only"):
            authority.validate_workflow_permissions(
                ".github/workflows/phase0-review-gate.yml",
                mixed_triggers,
                POLICY,
            )

    def test_authority_permission_scan_uses_base_policy_on_head_tree(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            git(repo, "init", "-q")
            git(repo, "config", "user.email", "test@example.invalid")
            git(repo, "config", "user.name", "Phase0 test")
            policy_path = repo / "ksp-bootstrap/policies/gate-authority.json"
            policy_path.parent.mkdir(parents=True)
            policy_path.write_text(AUTHORITY_POLICY.read_text())
            workflow = repo / ".github/workflows/ksp-gate-authority.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "on:\n  pull_request_target:\n"
                "permissions:\n  contents: read\n"
                "jobs:\n  authority:\n    runs-on: ubuntu-latest\n"
            )
            git(repo, "add", ".")
            git(repo, "commit", "-q", "-m", "trusted base")
            base = git(repo, "rev-parse", "HEAD")

            spoof = repo / ".github/workflows/reviewer-workflow-spoof.yml"
            spoof.write_text(
                "on:\n  pull_request_target:\n"
                "permissions:\n  statuses: write\n"
                "jobs:\n  test:\n    runs-on: ubuntu-latest\n"
            )
            head_policy = json.loads(AUTHORITY_POLICY.read_text())
            head_policy["reviewed_authority_paths"] = [
                ".github/workflows/reviewer-workflow-spoof.yml"
            ]
            head_policy["reserved_check_names"] = ["head-controlled nonreserved name"]
            policy_path.write_text(json.dumps(head_policy))
            git(repo, "add", ".")
            git(repo, "commit", "-q", "-m", "untrusted workflow and policy")
            head = git(repo, "rev-parse", "HEAD")
            authority.configure_repo(repo)
            try:
                with self.assertRaisesRegex(
                    ValueError,
                    "checks/statuses write is outside reviewed authority paths",
                ):
                    authority.validate_head_workflow_permissions(base, head)

                spoof.write_text(
                    "on:\n  pull_request:\n"
                    "permissions:\n  contents: read\n"
                    "jobs:\n  counterfeit:\n"
                    "    name: KSP Independent Review\n"
                    "    runs-on: ubuntu-latest\n"
                    "    steps:\n      - run: exit 0\n"
                )
                git(repo, "add", ".")
                git(repo, "commit", "-q", "-m", "read-only check-name spoof")
                read_only_head = git(repo, "rev-parse", "HEAD")
                with self.assertRaisesRegex(
                    ValueError,
                    "reserved check name outside safe authority context",
                ):
                    authority.validate_head_workflow_permissions(base, read_only_head)
            finally:
                authority.configure_repo(REPO_ROOT)

    def test_current_reviewer_workflow_is_base_trusted_and_read_only(self):
        text = WORKFLOW.read_text()
        self.assertIn("pull_request_target:", text)
        self.assertIn(
            "types: [opened, synchronize, reopened, ready_for_review, edited]", text
        )
        self.assertIn("cancel-in-progress: true", text)
        self.assertIn("KSP_BASE_SHA: ${{ github.event.pull_request.base.sha }}", text)
        self.assertIn("KSP_TRUSTED_SHA: ${{ github.workflow_sha }}", text)
        self.assertIn("ref: ${{ github.workflow_sha }}", text)
        self.assertIn("persist-credentials: false", text)
        self.assertNotIn("checks: write", text)
        self.assertNotIn("statuses: write", text)
        self.assertNotIn("permissions: write-all", text)
        self.assertNotIn(
            "ref: ${{ github.event.pull_request.head.sha }}", text
        )
        self.assertNotIn("git checkout", text)
        self.assertEqual(text.count("${{ secrets.GITHUB_TOKEN }}"), 1)

    def test_authority_installs_existing_hash_pinned_yaml_parser(self):
        text = AUTHORITY_WORKFLOW.read_text()
        self.assertIn("Install hash-pinned YAML parser", text)
        self.assertIn("--require-hashes -r ksp-bootstrap/requirements-ci.txt", text)
        self.assertIn("--only-binary=:all:", text)
        self.assertIn("--no-deps", text)
        ci = CI_WORKFLOW.read_text()
        self.assertIn("  pull_request:", ci)
        self.assertIn(
            "--require-hashes -r ksp-bootstrap/requirements-ci.txt", ci
        )
        self.assertIn("bash scripts/ci/validate_devops_umbrella.sh", ci)

    def test_all_current_workflows_have_decodable_permissions(self):
        for path in sorted((REPO_ROOT / ".github/workflows").glob("*.y*ml")):
            with self.subTest(path=path.name):
                authority.validate_workflow_permissions(
                    path.relative_to(REPO_ROOT).as_posix(),
                    path.read_text(),
                    POLICY,
                )

    def test_exact_target_exception_and_authority_policy_are_present(self):
        entries = {
            tuple(line.split())
            for line in ALLOWLIST.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertIn(
            (".github/workflows/phase0-review-gate.yml", "pull_request_target"),
            entries,
        )
        self.assertIn(
            (
                ".github/workflows/phase0-review-gate.yml",
                "zizmor:dangerous-triggers",
            ),
            entries,
        )
        self.assertEqual(POLICY["blocked_permissions"], ["checks", "statuses"])
        self.assertEqual(POLICY["reviewed_write_permissions"], ["checks", "statuses"])
        self.assertEqual(
            POLICY["reviewed_write_exceptions"],
            [
                {
                    "path": ".github/workflows/security-deep.yml",
                    "job_id": "scorecard",
                    "triggers": ["schedule", "workflow_dispatch"],
                    "permissions": ["id-token", "security-events"],
                }
            ],
        )
        self.assertEqual(
            POLICY["reserved_check_names"],
            [
                "Phase-0 reviewer scaffold (not required)",
                "KSP Independent Review",
                "KSP trusted gate authority",
            ],
        )


if __name__ == "__main__":
    unittest.main()
