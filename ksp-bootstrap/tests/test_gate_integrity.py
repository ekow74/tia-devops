from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/ci/check_gate_integrity.py"
spec = importlib.util.spec_from_file_location("check_gate_integrity", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


def approved(gid: str, approval_id: str, scope: list[str]) -> dict:
    return {
        "gate_change_id": gid,
        "decision_log_id": "D-0020",
        "reason": "test",
        "reviewer_kind": "CLAUDE",
        "review_status": "APPROVED",
        "review_reference": "CLAUDE-TEST",
        "scope": scope,
        "approval": {
            "approval_id": approval_id,
            "approved_gate_change_id": gid,
            "approved_scope": scope,
            "approved_threshold": "protected-path merge authorization",
            "review_revision_id": "REV-1",
        },
    }


class GateIntegrityTests(unittest.TestCase):
    def test_a1_t05e_replayed_unrelated_approval_rejected(self):
        record = approved("GC-0002", "APP-1", ["ksp-bootstrap/scripts/ci/**"])
        record["approval"]["approved_gate_change_id"] = "GC-9999"
        with self.assertRaises(ValueError):
            mod.validate_gatechange_records([record], ["ksp-bootstrap/scripts/ci/x.py"])

    def test_a1_t05f_reused_approval_rejected(self):
        base = approved("GC-0001", "APP-1", ["ksp-bootstrap/policies/**"])
        current = approved("GC-0002", "APP-1", ["ksp-bootstrap/scripts/ci/**"])
        with self.assertRaises(ValueError):
            mod.validate_gatechange_records([current], ["ksp-bootstrap/scripts/ci/x.py"], base_records=[base])

    def test_scope_must_cover_changed_protected_path(self):
        record = approved("GC-0002", "APP-2", ["ksp-bootstrap/policies/**"])
        with self.assertRaises(ValueError):
            mod.validate_gatechange_records([record], ["ksp-bootstrap/scripts/ci/x.py"])

    def test_bound_single_use_approval_passes(self):
        record = approved("GC-0002", "APP-2", ["ksp-bootstrap/scripts/ci/**"])
        mod.validate_gatechange_records([record], ["ksp-bootstrap/scripts/ci/x.py"])

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


if __name__ == "__main__":
    unittest.main()
