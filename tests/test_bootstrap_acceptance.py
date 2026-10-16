import unittest
from aae.bootstrap_acceptance import REQUIRED, assess

class BootstrapAcceptanceTests(unittest.TestCase):
    def full(self):
        return {"requested_operation": "project-bootstrap",
                "original_request": "Establish full engineering environment",
                "outcomes": {name: {"status": "verified", "evidence": "test"} for name in REQUIRED}}

    def test_complete_with_evidence(self):
        self.assertEqual(assess(self.full())["state"], "COMPLETE")

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
