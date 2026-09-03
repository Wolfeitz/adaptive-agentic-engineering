from __future__ import annotations

import json
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

    def test_accounting_reconciles_v1_and_criterion_v2_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            invocation_directory = root / ".aae/runtime/invocations"
            invocation_directory.mkdir(parents=True)
            (invocation_directory / "old.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "status": "completed",
                        "outcome": {"result": "succeeded"},
                    }
                ),
                encoding="utf-8",
            )
            (invocation_directory / "new.json").write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "status": "blocked",
                        "criteria": [
                            {"authority": "semantic-executor"},
                            {"authority": "deterministic-control"},
                        ],
                        "outcome": {
                            "result": "blocked",
                            "combined_result": "blocked",
                            "criterion_results": [
                                {"result": "passed"},
                                {"result": "blocked"},
                            ],
                        },
                    }
                ),
                encoding="utf-8",
            )
            accounting, errors, warnings = build_agent_skill_accounting(root)
            self.assertEqual(errors, [])
            self.assertEqual(warnings, [])
            evidence = accounting["runtime_evidence"]
            self.assertEqual(evidence["invocation_schema_counts"], {"1": 1, "2": 1})
            self.assertEqual(
                evidence["criterion_authority_counts"],
                {"deterministic-control": 1, "semantic-executor": 1},
            )
            self.assertEqual(
                evidence["criterion_result_counts"], {"blocked": 1, "passed": 1}
            )
            self.assertEqual(evidence["combined_result_counts"], {"blocked": 1})


if __name__ == "__main__":
    unittest.main()
