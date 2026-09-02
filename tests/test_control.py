from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from aae.cli import init_repository
from aae.control import invoke_skill, record_invocation_outcome
from aae.skills import build_skill_registry, load_skill_instructions


class SkillInvocationTests(unittest.TestCase):
    def test_best_matching_advertisement_is_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            registry, errors, _ = build_skill_registry(root)
            self.assertEqual(errors, [])
            record, procedure, invocation_errors = invoke_skill(
                root,
                registry,
                task="inspect an unfamiliar repository and find project authority",
                runtime_profile={
                    "available_tools": ["filesystem-search", "version-control-read"]
                },
            )
            self.assertEqual(invocation_errors, [])
            self.assertEqual(record["selected_skill"]["registry_id"], "project:repo-recon")
            self.assertEqual(record["selection_reason"], "best advertisement match")
            self.assertIn("# Repository Reconnaissance", str(procedure))

    def test_missing_required_tools_denies_loading(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            registry, errors, _ = build_skill_registry(root)
            self.assertEqual(errors, [])
            record, procedure, _ = invoke_skill(
                root,
                registry,
                task="verify the change",
                explicit_skill="project:acceptance-verify",
                runtime_profile={"available_tools": []},
            )
            self.assertIsNone(procedure)
            self.assertEqual(record["status"], "denied")
            self.assertIn("missing-tools:test-execution", record["safety"]["rejection_reasons"])

    def test_independent_review_requires_fresh_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            registry, errors, _ = build_skill_registry(root)
            self.assertEqual(errors, [])
            record, procedure, _ = invoke_skill(
                root,
                registry,
                task="independently review the change",
                explicit_skill="project:independent-review",
                runtime_profile={"available_tools": ["filesystem-read"]},
            )
            self.assertIsNone(procedure)
            self.assertIn("fresh-context-required", record["safety"]["rejection_reasons"])

    def test_destructive_skill_requires_explicit_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            manifest_path = root / ".aae/skills/acceptance-verify/skill.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["destructive"] = True
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            registry, errors, _ = build_skill_registry(root)
            self.assertEqual(errors, [])
            denied, procedure, _ = invoke_skill(
                root,
                registry,
                task="verify the change",
                explicit_skill="project:acceptance-verify",
                runtime_profile={"available_tools": ["test-execution"]},
            )
            self.assertIsNone(procedure)
            self.assertIn("destructive-approval-required", denied["safety"]["rejection_reasons"])
            allowed, procedure, _ = invoke_skill(
                root,
                registry,
                task="verify the change",
                explicit_skill="project:acceptance-verify",
                runtime_profile={
                    "available_tools": ["test-execution"],
                    "approvals": ["destructive"],
                },
            )
            self.assertEqual(allowed["status"], "procedure-loaded")
            self.assertIsNotNone(procedure)

    def test_loader_rejects_a_stale_or_missing_decision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            registry, errors, _ = build_skill_registry(root)
            self.assertEqual(errors, [])
            _, procedure, error = load_skill_instructions(registry, "project:repo-recon")
            self.assertIsNone(procedure)
            self.assertIn("allowed invocation decision", str(error))
            skill = next(item for item in registry["skills"] if item["name"] == "repo-recon")
            _, procedure, error = load_skill_instructions(
                registry,
                "project:repo-recon",
                authorization={
                    "decision": "allowed",
                    "registry_content_sha256": "0" * 64,
                    "skill_content_sha256": skill["skill_content_sha256"],
                },
            )
            self.assertIsNone(procedure)
            self.assertIn("registry digest", str(error))

    def test_allowed_invocation_and_outcome_are_durable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            registry, errors, _ = build_skill_registry(root)
            self.assertEqual(errors, [])
            record, procedure, invocation_errors = invoke_skill(
                root,
                registry,
                task="verify the completed change",
                explicit_skill="project:acceptance-verify",
                task_id="task-1",
                runtime_profile={"available_tools": ["test-execution"]},
            )
            self.assertEqual(invocation_errors, [])
            self.assertIsNotNone(procedure)
            self.assertIsNone(
                record_invocation_outcome(
                    root,
                    record["invocation_id"],
                    outcome="succeeded",
                    verification="tests passed",
                    evidence="report.json",
                    context_tokens=123,
                    execution_cost=0.5,
                )
            )
            saved = json.loads(
                (root / ".aae/runtime/invocations" / f"{record['invocation_id']}.json").read_text()
            )
            self.assertEqual(saved["status"], "completed")
            self.assertEqual(saved["outcome"]["context_tokens"], 123)


if __name__ == "__main__":
    unittest.main()
