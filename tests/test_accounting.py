from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from aae.accounting import build_agent_skill_accounting
from aae.cli import init_repository


class AccountingTests(unittest.TestCase):
    def test_seeded_project_has_skills_but_no_permanent_agent_cast(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            accounting, errors, warnings = build_agent_skill_accounting(root)
            self.assertEqual(errors, [])
            self.assertEqual(warnings, [])
            self.assertEqual(accounting["agent_model"]["persistent_named_agents"], 0)
            self.assertTrue(accounting["agent_model"]["runtime_agents_are_ephemeral"])
            self.assertEqual(accounting["skill_fabric"]["skill_count"], 8)
            self.assertGreater(accounting["skill_fabric"]["capability_count"], 30)
            self.assertEqual(accounting["runtime_evidence"]["invocation_count"], 0)
            self.assertEqual(
                accounting["extension_points"]["configured_model_profiles"], 0
            )
            self.assertTrue(
                all(
                    component["is_agent"] is False
                    for component in accounting["component_accounting"]
                )
            )
            roles = {role["role"] for role in accounting["agent_model"]["roles"]}
            self.assertEqual(
                roles,
                {"current-agent", "independent-reviewer", "deterministic-control-plane"},
            )


if __name__ == "__main__":
    unittest.main()
