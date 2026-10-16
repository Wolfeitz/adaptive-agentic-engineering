import unittest
from aae.bootstrap_decisions import assess_decisions
from aae.bootstrap_acceptance import REQUIRED


class DecisionLedgerTests(unittest.TestCase):
    def record(self):
        return {"requested_operation": "project-bootstrap",
                "original_request": "Full bootstrap",
                "outcomes": {name: {"decision": "not_applicable", "reason": "assessed and outside scope",
                                    "evidence": "project assessment"} for name in REQUIRED}}

    def test_assessment_without_artifact_creation_can_complete(self):
        self.assertEqual(assess_decisions(self.record())["state"], "COMPLETE")

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
