from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
SCRIPT = ROOT / "scripts/ci/check_gate_integrity.py"
READBACK_SCRIPT = ROOT / "scripts/ci/verify_gatechange_readback.py"
WORKFLOW = REPO_ROOT / ".github/workflows/ksp-safety-gates.yml"
BOOTSTRAP_ANCHOR = "b9a7a3fa8c787a57ec862f4150c156691928ff05"

spec = importlib.util.spec_from_file_location("check_gate_integrity", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)

readback_spec = importlib.util.spec_from_file_location("verify_gatechange_readback", READBACK_SCRIPT)
readback_mod = importlib.util.module_from_spec(readback_spec)
assert readback_spec and readback_spec.loader
readback_spec.loader.exec_module(readback_mod)

HEAD = "a" * 40


def gate(gid: str, scope: list[str], threshold: str = "protected-path merge authorization") -> dict:
    return {
        "gate_change_id": gid,
        "decision_log_id": "D-0020",
        "reason": "test",
        "reviewer_kind": "CLAUDE",
        "review_reference": "CLAUDE-TEST",
        "approval_threshold": threshold,
        "scope": scope,
    }


def approval(
    gid: str,
    approval_id: str,
    scope: list[str],
    *,
    head: str = HEAD,
    threshold: str = "protected-path merge authorization",
    review_reference: str = "CLAUDE-TEST",
) -> dict:
    return {
        "decision": "APPROVED",
        "approval_id": approval_id,
        "approved_gate_change_id": gid,
        "approved_scope": scope,
        "approved_threshold": threshold,
        "review_reference": review_reference,
        "review_revision_id": "REV-1",
        "approved_head_sha": head,
    }


def git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True, stderr=subprocess.STDOUT).strip()


class GateIntegrityTests(unittest.TestCase):
    def setUp(self):
        mod.configure_repo(REPO_ROOT)

    def test_a1_t05d_exact_external_readback_required(self):
        record = gate("GC-0002", ["ksp-bootstrap/scripts/ci/**"])
        line = (
            "GateChange approval: APPROVED | approval_id=APP-2 | gate_change_id=GC-0002 | "
            f"approved_head_sha={HEAD} | approved_scope=ksp-bootstrap/scripts/ci/** | "
            "approved_threshold=protected-path merge authorization | review_reference=CLAUDE-TEST"
        )
        receipt = readback_mod.build_receipt(
            record,
            line,
            head=HEAD,
            approval_id="APP-2",
            review_revision_id="REV-2",
        )
        self.assertEqual(receipt["approved_head_sha"], HEAD)
        self.assertEqual(receipt["approved_gate_change_id"], "GC-0002")
        self.assertEqual(receipt["approval_consumed_by"], "GC-0002")

    def test_a1_t05d_absent_or_wrong_head_readback_rejected(self):
        record = gate("GC-0002", ["ksp-bootstrap/scripts/ci/**"])
        with self.assertRaises(ValueError):
            readback_mod.build_receipt(
                record,
                "no approval line here",
                head=HEAD,
                approval_id="APP-2",
                review_revision_id="REV-2",
            )
        wrong = (
            "GateChange approval: APPROVED | approval_id=APP-2 | gate_change_id=GC-0002 | "
            f"approved_head_sha={'b' * 40} | approved_scope=ksp-bootstrap/scripts/ci/** | "
            "approved_threshold=protected-path merge authorization | review_reference=CLAUDE-TEST"
        )
        with self.assertRaises(ValueError):
            readback_mod.build_receipt(
                record,
                wrong,
                head=HEAD,
                approval_id="APP-2",
                review_revision_id="REV-2",
            )

    def test_a1_t05e_replayed_unrelated_approval_rejected(self):
        record = gate("GC-0002", ["ksp-bootstrap/scripts/ci/**"])
        wrong = approval("GC-9999", "APP-1", ["ksp-bootstrap/scripts/ci/**"])
        with self.assertRaises(ValueError):
            mod.validate_gatechange_records([record], ["ksp-bootstrap/scripts/ci/x.py"], head_sha=HEAD, approvals=[wrong])

    def test_a1_t05f_reused_approval_rejected(self):
        records = [
            gate("GC-0001", ["ksp-bootstrap/policies/**"]),
            gate("GC-0002", ["ksp-bootstrap/scripts/ci/**"]),
        ]
        approvals = [
            approval("GC-0001", "APP-1", ["ksp-bootstrap/policies/**"]),
            approval("GC-0002", "APP-1", ["ksp-bootstrap/scripts/ci/**"]),
        ]
        with self.assertRaises(ValueError):
            mod.validate_gatechange_records(records, ["ksp-bootstrap/policies/x", "ksp-bootstrap/scripts/ci/x.py"], head_sha=HEAD, approvals=approvals)

    def test_scope_must_cover_changed_protected_path(self):
        record = gate("GC-0002", ["ksp-bootstrap/policies/**"])
        receipt = approval("GC-0002", "APP-2", ["ksp-bootstrap/policies/**"])
        with self.assertRaises(ValueError):
            mod.validate_gatechange_records([record], ["ksp-bootstrap/scripts/ci/x.py"], head_sha=HEAD, approvals=[receipt])

    def test_bound_single_use_approval_passes(self):
        record = gate("GC-0002", ["ksp-bootstrap/scripts/ci/**"])
        receipt = approval("GC-0002", "APP-2", ["ksp-bootstrap/scripts/ci/**"])
        mod.validate_gatechange_records([record], ["ksp-bootstrap/scripts/ci/x.py"], head_sha=HEAD, approvals=[receipt])

    def test_head_mismatch_rejected(self):
        record = gate("GC-0002", ["ksp-bootstrap/scripts/ci/**"])
        receipt = approval("GC-0002", "APP-2", ["ksp-bootstrap/scripts/ci/**"], head="b" * 40)
        with self.assertRaises(ValueError):
            mod.validate_gatechange_records([record], ["ksp-bootstrap/scripts/ci/x.py"], head_sha=HEAD, approvals=[receipt])

    def test_m1_scope_requires_exact_ordered_equality(self):
        record = gate("GC-0002", ["ksp-bootstrap/scripts/ci/**"])
        broader = approval("GC-0002", "APP-2", ["ksp-bootstrap/**"])
        with self.assertRaisesRegex(ValueError, "exactly equal"):
            mod.validate_gatechange_records([record], ["ksp-bootstrap/scripts/ci/x.py"], head_sha=HEAD, approvals=[broader])

    def test_a1_t06_validator_surfaces_are_protected(self):
        pats = mod.patterns()
        required = [
            "src/ksp_knowledge/safety.py",
            "src/ksp_knowledge/quantity.py",
            "src/ksp_knowledge/governance.py",
            "src/ksp_knowledge/revocation.py",
            "src/ksp_knowledge/pack.py",
            "src/ksp_knowledge/release.py",
            "src/ksp_knowledge/platform.py",
            "tests/test_s03_controls.py",
            "tests/fixtures/sample.json",
        ]
        for path in required:
            self.assertTrue(any(mod.path_matches(path, p) for p in pats), path)

    def test_a1_t07_gate_job_is_separate_base_checked_out_and_isolated(self):
        workflow = WORKFLOW.read_text()
        self.assertIn("  gate-integrity:\n", workflow)
        gate_job = workflow.split("  gate-integrity:\n", 1)[1]
        self.assertIn("Checkout trusted base only", gate_job)
        self.assertIn("ref: ${{ github.event.pull_request.base.sha }}", gate_job)
        self.assertIn('git fetch --no-tags --depth=1 origin "${HEAD_SHA}"', gate_job)
        self.assertIn('git show "${BASE_SHA}:${CHECKER}" > "${TMP_CHECKER}"', gate_job)
        self.assertIn('python3 -I "${TMP_CHECKER}"', gate_job)
        self.assertIn('--bootstrap-anchor "${BOOTSTRAP_ANCHOR}"', gate_job)
        self.assertNotIn("unittest discover", gate_job)
        self.assertNotIn("check_terraform_static.py", gate_job)
        self.assertIn("text = git_show(head, path)", SCRIPT.read_text())

    def test_n1_bootstrap_receipts_only_base_passes_and_other_change_fails(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            git(repo, "init", "-q")
            git(repo, "config", "user.email", "test@example.invalid")
            git(repo, "config", "user.name", "KSP Test")
            (repo / ".keep").write_text("anchor\n")
            git(repo, "add", ".keep")
            git(repo, "commit", "-q", "-m", "anchor")
            anchor = git(repo, "rev-parse", "HEAD")

            git(repo, "checkout", "-q", "-b", "head")
            (repo / "ksp-bootstrap/policies").mkdir(parents=True)
            (repo / "ksp-bootstrap/governance/gate-changes").mkdir(parents=True)
            (repo / "x").mkdir()
            (repo / "ksp-bootstrap/policies/protected-paths.txt").write_text("x/**\n")
            gate_record = {
                "gate_change_id": "GC-0001",
                "decision_log_id": "D-0023",
                "reason": "synthetic bootstrap test",
                "reviewer_kind": "CLAUDE",
                "review_reference": "REVIEW-v3",
                "scope": ["x/**"],
                "approval_threshold": "bootstrap",
            }
            (repo / "ksp-bootstrap/governance/gate-changes/GC-0001.json").write_text(json.dumps(gate_record))
            (repo / "x/change.txt").write_text("protected\n")
            git(repo, "add", ".")
            git(repo, "commit", "-q", "-m", "head change")
            head = git(repo, "rev-parse", "HEAD")

            git(repo, "checkout", "-q", "-b", "base", anchor)
            (repo / "ksp-bootstrap/governance/approvals").mkdir(parents=True)
            receipt = approval(
                "GC-0001",
                "APP-BOOT",
                ["x/**"],
                head=head,
                threshold="bootstrap",
                review_reference="REVIEW-v3",
            )
            (repo / "ksp-bootstrap/governance/approvals/GC-0001.json").write_text(json.dumps(receipt))
            git(repo, "add", ".")
            git(repo, "commit", "-q", "-m", "receipt only")
            base = git(repo, "rev-parse", "HEAD")

            cmd = [
                sys.executable,
                "-I",
                str(SCRIPT),
                "--repo-root",
                str(repo),
                "--base",
                base,
                "--head",
                head,
                "--policy-ref",
                head,
                "--bootstrap-anchor",
                anchor,
            ]
            passed = subprocess.run(cmd, cwd=repo, text=True, capture_output=True)
            self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
            self.assertIn("gate-integrity: PASS", passed.stdout)

            (repo / "not-a-receipt.txt").write_text("disallowed base change\n")
            git(repo, "add", ".")
            git(repo, "commit", "-q", "-m", "bad base change")
            bad_base = git(repo, "rev-parse", "HEAD")
            failed = subprocess.run(
                [*cmd[:6], bad_base, *cmd[7:]], cwd=repo, text=True, capture_output=True
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("bootstrap base is not the pinned anchor plus approval-ledger receipts only", failed.stdout)

    def test_n3_python_isolated_mode_blocks_json_shadowing(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            checker = d / "check_gate_integrity.py"
            shutil.copyfile(SCRIPT, checker)
            (d / "json.py").write_text(
                "print('gate-integrity: PASS (HEAD-SHADOW-RAN)')\nraise SystemExit(0)\n"
            )
            probe = subprocess.run(
                [sys.executable, "-I", str(checker), "--help"],
                cwd=d,
                text=True,
                capture_output=True,
            )
            self.assertEqual(probe.returncode, 0, probe.stdout + probe.stderr)
            self.assertNotIn("HEAD-SHADOW-RAN", probe.stdout + probe.stderr)
            self.assertIn("--bootstrap-anchor", probe.stdout)

    def test_n2_real_pr_diff_scope_coverage(self):
        head = os.environ.get("KSP_PR_HEAD_SHA")
        base = os.environ.get("KSP_PR_BASE_SHA")
        if not head or not base:
            self.skipTest("real PR diff assertion runs in pull_request CI")
        try:
            git(REPO_ROOT, "cat-file", "-e", f"{head}^{{commit}}")
            git(REPO_ROOT, "cat-file", "-e", f"{BOOTSTRAP_ANCHOR}^{{commit}}")
        except subprocess.CalledProcessError as exc:
            self.fail(f"required PR objects missing: {exc.output}")

        mod.configure_repo(REPO_ROOT, head)
        records = mod.current_gate_records(BOOTSTRAP_ANCHOR, head)
        self.assertEqual({r["gate_change_id"] for r in records}, {"GC-0001", "GC-0002"})
        protected_paths = [
            p
            for _, p in mod.changed_files(BOOTSTRAP_ANCHOR, head)
            if mod.protected(p, head) and not p.startswith(mod.GATE_DIR + "/")
        ]
        receipts = [
            approval(
                r["gate_change_id"],
                "APP-" + r["gate_change_id"],
                list(r["scope"]),
                head=head,
                threshold=r["approval_threshold"],
                review_reference=r["review_reference"],
            )
            for r in records
        ]
        mod.validate_gatechange_records(records, protected_paths, head_sha=head, approvals=receipts)

        narrowed_records = [dict(r) for r in records]
        gc1 = next(r for r in narrowed_records if r["gate_change_id"] == "GC-0001")
        gc1["scope"] = [x for x in gc1["scope"] if x != ".github/workflows/ci.yml"]
        narrowed_receipts = [
            approval(
                r["gate_change_id"],
                "APP-NARROW-" + r["gate_change_id"],
                list(r["scope"]),
                head=head,
                threshold=r["approval_threshold"],
                review_reference=r["review_reference"],
            )
            for r in narrowed_records
        ]
        with self.assertRaisesRegex(ValueError, r"\.github/workflows/ci\.yml"):
            mod.validate_gatechange_records(narrowed_records, protected_paths, head_sha=head, approvals=narrowed_receipts)


if __name__ == "__main__":
    unittest.main()
