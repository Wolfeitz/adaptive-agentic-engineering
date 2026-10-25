import unittest
from aae.requirements_coverage import check

class CoverageTests(unittest.TestCase):
    def good(self):
        return {"original_request": "Review applicable architecture and coding rules",
                "kind": "pr-review", "repository": "TransportNet/Mediation", "pr": 42,
                "head_sha": "abc", "current_head_sha": "abc",
                "applicable_requirements": ["checklist-details", "architecture-conformance"],
                "generated_tasks": [{"name": "read overview", "status": "done"}],
                "results": {r: {"status": "verified", "source": "wiki/revision",
                                "reason": "applies to changed code",
                                "change_evidence": "diff lines", "assessment_evidence": "review result"}
                            for r in ["checklist-details", "architecture-conformance"]}}
    def test_complete(self):
        self.assertEqual(check(self.good())["coverage"], "complete")
    def test_mediation_all_generated_todos_done_but_missing_architecture(self):
        a=self.good()
        del a["results"]["architecture-conformance"]
        self.assertEqual(check(a)["coverage"], "partial")
    def test_reading_overview_not_assessment(self):
        a=self.good()
        del a["results"]["checklist-details"]["assessment_evidence"]
        self.assertEqual(check(a)["coverage"], "partial")
    def test_unreachable_source(self):
        a=self.good()
        a["results"]["architecture-conformance"]["status"]="blocked"
        self.assertEqual(check(a)["coverage"], "partial")
    def test_approved_deferral(self):
        a=self.good()
        a["results"]["architecture-conformance"]={"status":"deferred","source":"wiki","reason":"approved exception","approval":"owner approval id"}
        self.assertEqual(check(a)["coverage"], "complete")
    def test_unsupported_waiver(self):
        a=self.good()
        a["results"]["architecture-conformance"]={"status":"deferred","source":"wiki","reason":"later"}
        self.assertEqual(check(a)["coverage"], "partial")
    def test_stale_head(self):
        a=self.good()
        a["current_head_sha"]="def"
        self.assertEqual(check(a)["coverage"], "partial")
    def test_review_complete_can_find_violations(self):
        a=self.good()
        a["results"]["architecture-conformance"]["status"]="violated"
        self.assertEqual(check(a)["coverage"], "complete")
    def test_missing_original_request(self):
        a=self.good()
        a.pop("original_request")
        self.assertEqual(check(a)["coverage"], "partial")
