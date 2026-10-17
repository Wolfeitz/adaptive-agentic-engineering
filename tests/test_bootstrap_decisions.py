import unittest
from aae.bootstrap_decisions import assess_decisions
from aae.bootstrap_acceptance import REQUIRED


class DecisionLedgerTests(unittest.TestCase):
    def record(self):
        record = {"requested_operation": "project-bootstrap",
                "original_request": "Full bootstrap",
                "outcomes": {name: {"decision": "not_applicable", "reason": "assessed and outside scope",
                                    "evidence": "project assessment"} for name in REQUIRED}}
        record["outcomes"]["original_scope_reconciliation"] = {
            "decision": "retain", "reason": "All exclusions reviewed against the full request",
            "evidence": "Scope review confirms no required capability was dropped",
            "verification": "verified",
        }
        return record

    def test_assessment_without_artifact_creation_can_complete(self):
        self.assertEqual(assess_decisions(self.record())["state"], "COMPLETE")

    def test_all_not_applicable_is_not_complete(self):
        r = self.record()
        r["outcomes"]["original_scope_reconciliation"] = {
            "decision": "not_applicable", "reason": "outside scope",
            "evidence": "assessment", "verification": "verified",
        }
        result = assess_decisions(r)
        self.assertEqual(result["state"], "PARTIAL")
        self.assertIn("original_scope_reconciliation: scope reconciliation cannot be not applicable",
                      result["errors"])

    def test_scope_reconciliation_requires_verification(self):
        r = self.record()
        del r["outcomes"]["original_scope_reconciliation"]["verification"]
        self.assertEqual(assess_decisions(r)["pending"], ["original_scope_reconciliation"])

    def test_normal_verified_completion(self):
        r = self.record()
        for item in r["outcomes"].values():
            item.update(decision="retain", verification="verified")
        self.assertEqual(assess_decisions(r)["state"], "COMPLETE")

    def test_scoped_exclusion_requires_reason_and_evidence(self):
        for field in ("reason", "evidence"):
            with self.subTest(field=field):
                r = self.record()
                del r["outcomes"]["mcp_assessment"][field]
                self.assertEqual(assess_decisions(r)["state"], "PARTIAL")

    def test_every_category_needs_decision(self):
        r = self.record()
        del r["outcomes"]["agent_assessment"]
        self.assertEqual(assess_decisions(r)["state"], "PARTIAL")
        self.assertTrue(any("agent_assessment" in e for e in assess_decisions(r)["errors"]))

    def test_unverified_adopted_capability_remains_pending(self):
        r = self.record()
        r["outcomes"]["hook_assessment"] = {"decision": "create", "reason": "block wrong worktree",
            "evidence": "existing runtime capability"}
        self.assertIn("hook_assessment", assess_decisions(r)["pending"])

    def test_blocker_does_not_erase_other_decisions(self):
        r = self.record()
        r["outcomes"]["mcp_assessment"] = {"decision": "blocked", "reason": "permission pending",
            "evidence": "access denied", "next_action": "Ask owner"}
        result = assess_decisions(r)
        self.assertEqual(result["pending"], ["mcp_assessment"])
        self.assertEqual(result["errors"], [])

    def test_unsupported_decision_rejected(self):
        r = self.record()
        r["outcomes"]["skill_assessment"]["decision"] = "automatically_install_everything"
        self.assertEqual(assess_decisions(r)["state"], "PARTIAL")

    def test_do_not_narrow_full_bootstrap_to_setup(self):
        r = self.record()
        r["requested_operation"] = "developer-setup"
        self.assertEqual(assess_decisions(r)["state"], "NOT_FULL_BOOTSTRAP")


if __name__ == "__main__":
    unittest.main()
