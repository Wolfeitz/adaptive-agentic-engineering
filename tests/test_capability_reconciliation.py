import unittest
from aae.capability_reconciliation import reconcile

class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.req = [{"id":"planning-agent","category":"agents"}]
        self.existing = [{"id":"hve-planner","category":"agents","owner":"hve","scope":"user"}]

    def test_reuse_hve_with_native_evidence(self):
        d=[{"requirement_id":"planning-agent","action":"reuse","target_id":"hve-planner",
            "reason":"HVE already plans","discovery_evidence":"native catalog"}]
        self.assertEqual(reconcile(self.req,self.existing,d)["state"],"COVERED")

    def test_unassessed_agent_gap_cannot_disappear(self):
        self.assertEqual(reconcile(self.req,self.existing,[])["state"],"PARTIAL")

    def test_hve_replace_needs_approval(self):
        d=[{"requirement_id":"planning-agent","action":"replace","target_id":"hve-planner",
            "reason":"project wants another","implementation_evidence":"files"}]
        self.assertEqual(reconcile(self.req,self.existing,d)["state"],"PARTIAL")

    def test_creation_requires_implementation_evidence(self):
        d=[{"requirement_id":"planning-agent","action":"create",
            "reason":"No appropriate native specialist"}]
        self.assertIn("planning-agent",reconcile(self.req,[],d)["gaps"])

    def test_wrong_type_is_not_coverage(self):
        d=[{"requirement_id":"planning-agent","action":"reuse","target_id":"hve-planner",
            "reason":"it exists","discovery_evidence":"catalog"}]
        self.assertEqual(reconcile(self.req,[{"id":"hve-planner","category":"skills"}],d)["state"],"PARTIAL")

if __name__=="__main__": unittest.main()
