import unittest
from aae.bootstrap_acceptance import REQUIRED, assess

class BootstrapAcceptanceTests(unittest.TestCase):
    def full(self):
        return {"requested_operation": "project-bootstrap",
                "original_request": "Establish full engineering environment",
                "outcomes": {name: {"status": "verified", "evidence": "test"} for name in REQUIRED}}

    def test_complete_with_evidence(self):
        self.assertEqual(assess(self.full())["state"], "COMPLETE")

    def test_all_not_applicable_is_not_complete(self):
        r = self.full()
        for item in r["outcomes"].values():
            item.update(status="not_applicable", reason="assessed and outside scope")
        self.assertEqual(assess(r)["state"], "PARTIAL")

    def test_scope_reconciliation_alone_cannot_be_excluded(self):
        r = self.full()
        r["outcomes"]["original_scope_reconciliation"].update(
            status="not_applicable", reason="limited pilot")
        result = assess(r)
        self.assertEqual(result["state"], "PARTIAL")
        self.assertIn("original_scope_reconciliation: scope reconciliation cannot be not applicable",
                      result["errors"])

    def test_reasoned_scoped_exclusions_can_complete(self):
        r = self.full()
        for name in ("mcp_assessment", "native_invocation_verification"):
            r["outcomes"][name] = {"status": "not_applicable",
                "reason": "Assessment found no suitable MCP or new native integration",
                "evidence": "Project capability review"}
        self.assertEqual(assess(r)["state"], "COMPLETE")

    def test_scope_reconciliation_requires_evidence(self):
        r = self.full()
        del r["outcomes"]["original_scope_reconciliation"]["evidence"]
        self.assertEqual(assess(r)["state"], "PARTIAL")

    def test_missing_specialist_assessment_is_not_complete(self):
        r = self.full()
        del r["outcomes"]["agent_assessment"]
        self.assertEqual(assess(r)["state"], "PARTIAL")

    def test_unverified_native_invocation_is_not_complete(self):
        r = self.full()
        r["outcomes"]["native_invocation_verification"] = {"status": "blocked"}
        self.assertIn("native_invocation_verification", assess(r)["outstanding"])

    def test_na_without_reason_is_not_complete(self):
        r = self.full()
        r["outcomes"]["mcp_assessment"] = {"status": "not_applicable"}
        self.assertEqual(assess(r)["state"], "PARTIAL")

    def test_developer_setup_is_not_full_bootstrap(self):
        r = self.full()
        r["requested_operation"] = "developer-setup"
        self.assertEqual(assess(r)["state"], "NOT_FULL_BOOTSTRAP")

if __name__ == "__main__":
    unittest.main()
