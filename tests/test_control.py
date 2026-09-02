from __future__ import annotations

import json
import copy
from pathlib import Path
import tempfile
import unittest

from aae.cli import init_repository
from aae.control import (
    build_candidate_set,
    build_capability_demand,
    invoke_skill,
    load_invocation_policy,
    record_invocation_outcome,
    select_candidate,
)
from aae.skills import build_skill_registry
from aae.skills import load_skill_instructions


class CapabilityControlTests(unittest.TestCase):
    def test_demand_selection_is_deterministic_and_provenance_bearing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            registry, errors, _ = build_skill_registry(root)
            self.assertEqual(errors, [])
            demand = build_capability_demand(
                task="verify the completed change",
                explicit_capabilities=["acceptance-verification"],
                risks=["regression"],
                task_id="task-1",
            )
            candidate_set = build_candidate_set(registry, demand)
            selection = select_candidate(
                candidate_set, "project:acceptance-verify"
            )
            self.assertEqual(
                selection["selected_registry_id"], "project:acceptance-verify"
            )
            self.assertRegex(selection["selection_decision_sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue(demand["provenance"]["acceptance-verification"])

    def test_policy_denies_missing_runtime_capabilities_without_loading(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            registry, errors, _ = build_skill_registry(root)
            self.assertEqual(errors, [])
            record, procedure, policy_errors = invoke_skill(
                root,
                registry,
                task="verify the completed change",
                explicit_skill="project:acceptance-verify",
                explicit_capabilities=["acceptance-verification"],
                runtime_profile={
                    "provider": "local",
                    "model": "unit-test",
                    "available_tools": [],
                    "model_capabilities": [],
                    "model_data_classifications": ["internal"],
                    "data_classification": "internal",
                },
            )
            self.assertEqual(policy_errors, [])
            self.assertIsNone(procedure)
            self.assertEqual(record["status"], "denied")
            self.assertIn("tools", record["invocation_plan"]["policy"]["rejection_reasons"])
            self.assertFalse(record["procedure_loaded"])

    def test_independent_review_requires_fresh_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            registry, errors, _ = build_skill_registry(root)
            self.assertEqual(errors, [])
            record, procedure, _ = invoke_skill(
                root,
                registry,
                task="perform an independent review",
                explicit_skill="project:independent-review",
                explicit_capabilities=["independent-review"],
                runtime_profile={
                    "provider": "local",
                    "model": "unit-test",
                    "fresh_context": False,
                    "available_tools": ["filesystem-read"],
                    "model_capabilities": ["reasoning"],
                    "model_data_classifications": ["internal"],
                    "data_classification": "internal",
                },
            )
            self.assertIsNone(procedure)
            self.assertIn(
                "independence", record["invocation_plan"]["policy"]["rejection_reasons"]
            )
            self.assertFalse(record["procedure_loaded"])

    def test_destructive_side_effect_requires_explicit_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            manifest_path = root / ".aae/skills/acceptance-verify/skill.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["execution"]["side_effects"] = "destructive"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            registry, errors, _ = build_skill_registry(root)
            self.assertEqual(errors, [])
            record, procedure, _ = invoke_skill(
                root,
                registry,
                task="verify the completed change",
                explicit_skill="project:acceptance-verify",
                explicit_capabilities=["acceptance-verification"],
                runtime_profile={
                    "provider": "local",
                    "model": "unit-test",
                    "available_tools": ["test-execution"],
                    "model_capabilities": ["reasoning"],
                    "model_data_classifications": ["internal"],
                    "data_classification": "internal",
                    "approvals": [],
                },
            )
            self.assertIsNone(procedure)
            self.assertIn(
                "side-effect-approval",
                record["invocation_plan"]["policy"]["rejection_reasons"],
            )

    def test_model_authorization_is_required_for_controlled_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            registry, errors, _ = build_skill_registry(root)
            self.assertEqual(errors, [])
            record, procedure, _ = invoke_skill(
                root,
                registry,
                task="verify the completed change",
                explicit_skill="project:acceptance-verify",
                explicit_capabilities=["acceptance-verification"],
                runtime_profile={
                    "provider": "local",
                    "model": "unit-test",
                    "available_tools": ["test-execution"],
                    "model_capabilities": ["reasoning"],
                    "model_data_classifications": ["internal"],
                    "data_classification": "controlled",
                },
            )
            self.assertIsNone(procedure)
            self.assertIn(
                "model-data-classification",
                record["invocation_plan"]["policy"]["rejection_reasons"],
            )

    def test_runtime_tool_claim_does_not_override_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            policy_path = root / ".aae/skill-policy.json"
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
            policy["allowed_tools"] = []
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            registry, errors, _ = build_skill_registry(root)
            self.assertEqual(errors, [])
            record, procedure, _ = invoke_skill(
                root,
                registry,
                task="verify the completed change",
                explicit_skill="project:acceptance-verify",
                explicit_capabilities=["acceptance-verification"],
                runtime_profile={
                    "provider": "local",
                    "model": "unit-test",
                    "available_tools": ["test-execution"],
                    "model_capabilities": ["reasoning"],
                    "model_data_classifications": ["internal"],
                    "data_classification": "internal",
                },
            )
            self.assertIsNone(procedure)
            self.assertIn(
                "policy-tools", record["invocation_plan"]["policy"]["rejection_reasons"]
            )

    def test_unauthorized_provider_and_source_capability_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            registry, errors, _ = build_skill_registry(root)
            self.assertEqual(errors, [])
            selected = next(
                skill for skill in registry["skills"] if skill["name"] == "acceptance-verify"
            )
            selected["source"]["capability_allowlist"] = []
            record, procedure, _ = invoke_skill(
                root,
                registry,
                task="verify the completed change",
                explicit_skill="project:acceptance-verify",
                explicit_capabilities=["acceptance-verification"],
                runtime_profile={
                    "provider": "unauthorized-cloud",
                    "model": "unknown",
                    "available_tools": ["test-execution"],
                    "model_capabilities": ["reasoning"],
                    "model_data_classifications": ["internal"],
                    "data_classification": "internal",
                },
            )
            self.assertIsNone(procedure)
            reasons = record["invocation_plan"]["policy"]["rejection_reasons"]
            self.assertIn("source-capabilities", reasons)
            self.assertIn("model-authorization", reasons)

    def test_tampered_invocation_plan_cannot_load_a_procedure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            registry, errors, _ = build_skill_registry(root)
            self.assertEqual(errors, [])
            record, procedure, _ = invoke_skill(
                root,
                registry,
                task="verify the completed change",
                explicit_skill="project:acceptance-verify",
                explicit_capabilities=["acceptance-verification"],
                runtime_profile={
                    "provider": "local",
                    "model": "unit-test",
                    "available_tools": ["test-execution"],
                    "model_capabilities": ["reasoning"],
                    "model_data_classifications": ["internal"],
                    "data_classification": "internal",
                },
            )
            self.assertIsNotNone(procedure)
            tampered = copy.deepcopy(record["invocation_plan"])
            tampered["binding"]["side_effects"] = "destructive"
            _, loaded, error = load_skill_instructions(
                registry,
                "project:acceptance-verify",
                authorization=tampered,
            )
            self.assertIsNone(loaded)
            self.assertIn("digest is invalid", str(error))

    def test_allowed_invocation_and_outcome_are_durable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            registry, errors, _ = build_skill_registry(root)
            self.assertEqual(errors, [])
            policy, policy_errors = load_invocation_policy(root)
            self.assertEqual(policy_errors, [])
            self.assertEqual(policy["minimum_trust"], "declared")
            record, procedure, invoke_errors = invoke_skill(
                root,
                registry,
                task="verify the completed change",
                explicit_skill="project:acceptance-verify",
                explicit_capabilities=["acceptance-verification"],
                task_id="task-1",
                context_evidence_sha256="a" * 64,
                runtime_profile={
                    "provider": "local",
                    "model": "unit-test",
                    "available_tools": ["test-execution"],
                    "model_capabilities": ["reasoning"],
                    "model_data_classifications": ["internal"],
                    "data_classification": "internal",
                    "approvals": [],
                    "network_available": False,
                },
            )
            self.assertEqual(invoke_errors, [])
            self.assertEqual(record["status"], "procedure-loaded")
            self.assertIn("# Acceptance Verification", str(procedure))
            invocation_id = record["invocation_id"]
            self.assertIsNone(
                record_invocation_outcome(
                    root,
                    invocation_id,
                    outcome="succeeded",
                    verification="tests passed",
                    evidence="report.json",
                    context_tokens=123,
                    execution_cost=0.5,
                )
            )
            saved = json.loads(
                (root / ".aae/runtime/invocations" / f"{invocation_id}.json").read_text()
            )
            self.assertEqual(saved["status"], "completed")
            self.assertEqual(saved["outcome"]["context_tokens"], 123)

    def test_invalid_policy_and_outcome_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            policy_path = root / ".aae/skill-policy.json"
            policy_path.write_text('{"schema_version": 1, "minimum_trust": "magic"}')
            _, errors = load_invocation_policy(root)
            self.assertTrue(any("minimum_trust" in error for error in errors))
            error = record_invocation_outcome(
                root,
                "missing",
                outcome="succeeded",
                verification=None,
                evidence=None,
                context_tokens=None,
                execution_cost=None,
            )
            assert error is not None
            self.assertIn(
                "not found",
                error,
            )


if __name__ == "__main__":
    unittest.main()
