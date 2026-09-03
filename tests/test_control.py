from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

from aae.cli import init_repository
from aae.control import invoke_skill, record_invocation_outcome
from aae.criteria import (
    DETERMINISTIC_CONTROL,
    SEMANTIC_EXECUTOR,
    combine_criteria,
    validate_criteria,
)
from aae.hooks import process_event
from aae.skills import build_skill_registry, load_skill_instructions


class SkillInvocationTests(unittest.TestCase):
    def _configured_control_rule(self, root: Path, *, succeeds: bool = True) -> dict:
        hooks_path = root / ".aae/hooks.json"
        config = json.loads(hooks_path.read_text(encoding="utf-8"))
        rule = config["rules"][1]
        rule["enabled"] = True
        rule["criterion"] = "The configured verification check passes."
        rule["run_check"] = [
            sys.executable,
            "-c",
            "raise SystemExit(0)" if succeeds else "raise SystemExit(7)",
        ]
        hooks_path.write_text(json.dumps(config), encoding="utf-8")
        return rule

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

    def test_semantic_and_hook_control_results_are_combined(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            rule = self._configured_control_rule(root)
            registry, errors, _ = build_skill_registry(root)
            self.assertEqual(errors, [])
            criteria = combine_criteria(["The implementation matches the task."], [rule])
            record, procedure, invocation_errors = invoke_skill(
                root,
                registry,
                task="verify the completed change",
                explicit_skill="project:acceptance-verify",
                runtime_profile={"available_tools": ["test-execution"]},
                criteria=criteria,
            )
            self.assertEqual(invocation_errors, [])
            self.assertIsNotNone(procedure)
            self.assertEqual(
                {item["authority"] for item in record["executor_criteria"]},
                {SEMANTIC_EXECUTOR},
            )
            self.assertEqual(len(record["executor_criteria"]), 1)
            event, _, event_errors = process_event(
                root,
                event="files-changed",
                payload={"paths": ["src/aae/control.py"]},
                for_invocation_id=record["invocation_id"],
            )
            self.assertEqual(event_errors, [])
            control_result = event["actions"][0]["criterion_result"]
            self.assertEqual(control_result["authority"], DETERMINISTIC_CONTROL)
            self.assertEqual(control_result["result"], "passed")
            evidence_path = root / "verification.json"
            evidence_path.write_text('{"status":"passed"}', encoding="utf-8")
            self.assertIsNone(
                record_invocation_outcome(
                    root,
                    record["invocation_id"],
                    outcome="succeeded",
                    verification="passed",
                    evidence="verification.json",
                    context_tokens=None,
                    execution_cost=None,
                    semantic_results={"The implementation matches the task.": "passed"},
                    control_event_ids=[event["event_id"]],
                )
            )
            saved = json.loads(
                (root / ".aae/runtime/invocations" / f"{record['invocation_id']}.json")
                .read_text(encoding="utf-8")
            )
            self.assertEqual(saved["schema_version"], 2)
            self.assertEqual(saved["outcome"]["combined_result"], "succeeded")
            self.assertEqual(len(saved["outcome"]["criterion_results"]), 2)
            for result in saved["outcome"]["criterion_results"]:
                self.assertEqual(len(result["criterion_id"]), 64)
                self.assertEqual(len(result["supporting_evidence_sha256"]), 64)
                self.assertIn("responsible_identity", result)

    def test_missing_control_proof_is_blocked_and_contradiction_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            rule = self._configured_control_rule(root)
            registry, errors, _ = build_skill_registry(root)
            self.assertEqual(errors, [])
            criteria = combine_criteria(["The implementation matches the task."], [rule])
            record, _, _ = invoke_skill(
                root,
                registry,
                task="verify the completed change",
                explicit_skill="project:acceptance-verify",
                runtime_profile={"available_tools": ["test-execution"]},
                criteria=criteria,
            )
            evidence_path = root / "verification.json"
            evidence_path.write_text('{"status":"passed"}', encoding="utf-8")
            stale_event, _, _ = process_event(
                root,
                event="files-changed",
                payload={"paths": ["src/aae/control.py"]},
                for_invocation_id="different-invocation",
            )
            crossing_error = record_invocation_outcome(
                root,
                record["invocation_id"],
                outcome="succeeded",
                verification="passed",
                evidence="verification.json",
                context_tokens=None,
                execution_cost=None,
                semantic_results={
                    "The implementation matches the task.": "passed",
                    "The configured verification check passes.": "passed",
                },
                control_event_ids=[stale_event["event_id"]],
            )
            self.assertIn("unexpected", str(crossing_error))
            error = record_invocation_outcome(
                root,
                record["invocation_id"],
                outcome="succeeded",
                verification="blocked",
                evidence="verification.json",
                context_tokens=None,
                execution_cost=None,
                semantic_results={"The implementation matches the task.": "passed"},
                control_event_ids=[stale_event["event_id"]],
            )
            self.assertIn("contradicts criterion result", str(error))
            self.assertIsNone(
                record_invocation_outcome(
                    root,
                    record["invocation_id"],
                    outcome="blocked",
                    verification="blocked",
                    evidence="verification.json",
                    context_tokens=None,
                    execution_cost=None,
                    semantic_results={"The implementation matches the task.": "passed"},
                    control_event_ids=[stale_event["event_id"]],
                )
            )
            saved = json.loads(
                (root / ".aae/runtime/invocations" / f"{record['invocation_id']}.json")
                .read_text(encoding="utf-8")
            )
            self.assertEqual(saved["status"], "blocked")
            self.assertEqual(saved["outcome"]["combined_result"], "blocked")

    def test_unknown_control_evaluator_is_rejected(self) -> None:
        criterion = {
            "statement": "Something deterministic happens.",
            "authority": DETERMINISTIC_CONTROL,
            "evaluator": "trust-me-v1",
        }
        criterion["criterion_id"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "unsupported deterministic evaluator"):
            validate_criteria([criterion])

    def test_failed_control_proof_overrides_semantic_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            rule = self._configured_control_rule(root, succeeds=False)
            registry, errors, _ = build_skill_registry(root)
            self.assertEqual(errors, [])
            criteria = combine_criteria(["The implementation matches the task."], [rule])
            record, _, _ = invoke_skill(
                root,
                registry,
                task="verify the completed change",
                explicit_skill="project:acceptance-verify",
                runtime_profile={"available_tools": ["test-execution"]},
                criteria=criteria,
            )
            event, _, _ = process_event(
                root,
                event="files-changed",
                payload={"paths": ["src/aae/control.py"]},
                for_invocation_id=record["invocation_id"],
            )
            (root / "verification.json").write_text("{}", encoding="utf-8")
            self.assertIsNone(
                record_invocation_outcome(
                    root,
                    record["invocation_id"],
                    outcome="failed",
                    verification="failed",
                    evidence="verification.json",
                    context_tokens=None,
                    execution_cost=None,
                    semantic_results={"The implementation matches the task.": "passed"},
                    control_event_ids=[event["event_id"]],
                )
            )
            saved = json.loads(
                (root / ".aae/runtime/invocations" / f"{record['invocation_id']}.json")
                .read_text(encoding="utf-8")
            )
            self.assertEqual(saved["outcome"]["combined_result"], "failed")

    def test_independent_review_requires_a_succeeded_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            registry, errors, _ = build_skill_registry(root)
            self.assertEqual(errors, [])
            primary, _, _ = invoke_skill(
                root,
                registry,
                task="implement the change",
                explicit_skill="project:implementation-preflight",
                runtime_profile={
                    "available_tools": [
                        "filesystem-search",
                        "version-control-read",
                    ]
                },
            )
            denied, procedure, _ = invoke_skill(
                root,
                registry,
                task="independently review the change",
                explicit_skill="project:independent-review",
                runtime_profile={
                    "available_tools": ["filesystem-read"],
                    "fresh_context": True,
                },
                review_of_invocation_id=primary["invocation_id"],
            )
            self.assertIsNone(procedure)
            self.assertIn(
                "review-target-not-succeeded", denied["safety"]["rejection_reasons"]
            )
            self.assertIsNone(
                record_invocation_outcome(
                    root,
                    primary["invocation_id"],
                    outcome="succeeded",
                    verification="passed",
                    evidence="report.json",
                    context_tokens=None,
                    execution_cost=None,
                )
            )
            allowed, procedure, invocation_errors = invoke_skill(
                root,
                registry,
                task="independently review the change",
                explicit_skill="project:independent-review",
                runtime_profile={
                    "available_tools": ["filesystem-read"],
                    "fresh_context": True,
                },
                review_of_invocation_id=primary["invocation_id"],
            )
            self.assertEqual(invocation_errors, [])
            self.assertIsNotNone(procedure)
            self.assertEqual(allowed["status"], "procedure-loaded")
            self.assertEqual(
                allowed["review_of_invocation_id"], primary["invocation_id"]
            )


if __name__ == "__main__":
    unittest.main()
