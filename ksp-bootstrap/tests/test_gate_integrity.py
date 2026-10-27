from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
SCRIPT = ROOT / "scripts/ci/check_gate_integrity.py"
READBACK_SCRIPT = ROOT / "scripts/ci/verify_gatechange_readback.py"

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


def approval(gid: str, approval_id: str, scope: list[str], *, head: str = HEAD, threshold: str = "protected-path merge authorization") -> dict:
    return {
        "decision": "APPROVED",
        "approval_id": approval_id,
        "approved_gate_change_id": gid,
        "approved_scope": scope,
        "approved_threshold": threshold,
        "review_reference": "CLAUDE-TEST",
        "review_revision_id": "REV-1",
        "approved_head_sha": head,
    }


class GateIntegrityTests(unittest.TestCase):
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

    def test_a1_t07_workflow_materializes_checker_and_policy_from_base(self):
        workflow = (REPO_ROOT / ".github/workflows/ksp-safety-gates.yml").read_text()
        self.assertIn('git show "${BASE_SHA}:${CHECKER}" > "${CHECKER}"', workflow)
        self.assertIn('git show "${BASE_SHA}:${POLICY}" > "${POLICY}"', workflow)
        self.assertIn('gate-integrity-source: trusted base ${BASE_SHA}', workflow)
        self.assertIn("bootstrap head exception for PR #2", workflow)
        self.assertIn("b9a7a3fa8c787a57ec862f4150c156691928ff05", workflow)


if __name__ == "__main__":
    unittest.main()
