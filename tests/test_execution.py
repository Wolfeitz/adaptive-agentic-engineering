from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
from typing import Any
import unittest

from aae.accounting import build_agent_skill_accounting
from aae.cli import accounting_repository, init_repository
from aae.execution import (
    _codex_result_schema,
    _validate_result_against_packet,
    build_context_packet,
    execute_governed_task,
    governed_run_digest_is_valid,
    load_execution_configuration,
)


class GovernedExecutionTests(unittest.TestCase):
    def _project(self, root: Path, *, denied: bool = False) -> Path:
        init_repository(root)
        evidence = root / "evidence.txt"
        evidence.write_text("measured engineering evidence\n", encoding="utf-8")
        executable = root / "codex"
        executable.write_text(
            """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

if sys.argv[1:] == [\"--version\"]:
    print(\"fake-codex 1.0\")
    raise SystemExit(0)
Path(__file__).with_name(\"fake-codex-ran\").write_text(\"ran\\n\", encoding=\"utf-8\")
output = Path(sys.argv[sys.argv.index(\"--output-last-message\") + 1])
if sys.argv[-1] != \"-\":
    raise SystemExit(\"prompt must be streamed on stdin\")
prompt = sys.stdin.read()
role = \"reviewer\" if \"ROLE\\nreviewer\" in prompt else \"executor\"
schema_path = Path(sys.argv[sys.argv.index(\"--output-schema\") + 1])
schema = json.loads(schema_path.read_text(encoding=\"utf-8\"))
expected_verdicts = [\"not-applicable\"] if role == \"executor\" else [\"approved\", \"changes-required\", \"blocked\"]
if schema[\"properties\"][\"role\"][\"enum\"] != [role]:
    raise SystemExit(\"result schema must bind the invocation role\")
if schema[\"properties\"][\"review_verdict\"][\"enum\"] != expected_verdicts:
    raise SystemExit(\"result schema must bind role-valid review verdicts\")
packet = json.loads(prompt.split(\"BOUNDED EVIDENCE PACKET\\n\", 1)[1])
criterion_status = \"passed\"
if role == \"executor\" and \"failed criterion outcome\" in packet[\"task\"]:
    criterion_status = \"failed\"
if role == \"executor\" and \"blocked criterion outcome\" in packet[\"task\"]:
    criterion_status = \"blocked\"
reported_outcome = {\"passed\": \"succeeded\", \"failed\": \"failed\", \"blocked\": \"blocked\"}[criterion_status]
if role == \"executor\" and \"contradictory executor outcome\" in packet[\"task\"]:
    reported_outcome = \"failed\"
if role == \"reviewer\" and \"contradictory reviewer outcome\" in packet[\"task\"]:
    reported_outcome = \"failed\"
result = {
    \"role\": role,
    \"outcome\": reported_outcome,
    \"review_verdict\": \"approved\" if role == \"reviewer\" else \"not-applicable\",
    \"summary\": \"bounded fixture completed\",
    \"findings\": [{\"severity\": \"info\", \"statement\": \"fixture\", \"evidence_refs\": [\"evidence.txt\"]}],
    \"verification\": [
        {\"criterion\": criterion, \"status\": criterion_status, \"evidence_refs\": [\"evidence.txt\"]}
        for criterion in packet[\"acceptance_criteria\"]
    ],
}
output.write_text(json.dumps(result), encoding=\"utf-8\")
print(json.dumps({\"type\": \"thread.started\", \"thread_id\": f\"thread-{os.getpid()}\"}))
print(json.dumps({\"type\": \"turn.completed\", \"usage\": {\"input_tokens\": 12, \"output_tokens\": 4}}))
""",
            encoding="utf-8",
        )
        executable.chmod(0o700)

        primary_manifest = root / ".aae/skills/engineering-qualify/skill.json"
        primary_manifest.parent.mkdir(parents=True)
        primary_manifest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "name": "engineering-qualify",
                    "version": "1.0.0",
                    "description": "Qualify a real engineering path from bounded evidence.",
                    "capabilities": ["engineering-qualification"],
                    "triggers": ["engineering qualification"],
                    "applicable_when": ["engineering-only"],
                    "inputs": ["bounded-evidence"],
                    "produces": ["qualification-verdict"],
                    "requires": ["repository-evidence"],
                    "may_recommend": ["independent-review"],
                    "cost": {"context": "low", "reasoning": "medium"},
                    "independence_required": False,
                    "lifecycle": "project",
                    "execution": {
                        "mode": "hybrid",
                        "side_effects": "workspace-write" if denied else "read-only",
                    },
                    "requirements": {
                        "tools": ["filesystem-read"],
                        "model_capabilities": ["reasoning"],
                        "platforms": ["any"],
                        "network": "none",
                        "data_classifications": ["internal"],
                    },
                    "procedure": "SKILL.md",
                }
            ),
            encoding="utf-8",
        )
        primary_manifest.with_name("SKILL.md").write_text(
            "# Engineering qualification\n\nUse only supplied evidence.\n",
            encoding="utf-8",
        )
        policy = {
            "schema_version": 1,
            "minimum_trust": "declared",
            "required_source_approval": "approved",
            "allow_advisory_contracts": False,
            "allowed_side_effects": ["read-only"],
            "approval_required_for": ["external-write", "destructive"],
            "allowed_tools": ["filesystem-read"],
            "network_allowed": False,
            "allowed_data_classifications": ["internal"],
            "model_authorizations": [
                {
                    "provider": "fake-codex",
                    "models": ["fake-primary", "fake-review"],
                    "capabilities": ["reasoning"],
                    "data_classifications": ["internal"],
                }
            ],
        }
        (root / ".aae/skill-policy.json").write_text(
            json.dumps(policy), encoding="utf-8"
        )
        executor = {
            "adapter": "codex-cli",
            "command": str(executable),
            "command_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
            "model": "fake-primary",
            "provider": "fake-codex",
            "sandbox": "read-only",
            "timeout_seconds": 30,
            "available_tools": ["filesystem-read"],
            "model_capabilities": ["reasoning"],
            "data_classifications": ["internal"],
        }
        configuration = {
            "schema_version": 1,
            "context_limits": {
                "max_items": 12,
                "max_files": 6,
                "max_bytes": 65536,
                "max_estimated_tokens": 16384,
            },
            "evidence_paths": {
                "allowed_prefixes": ["evidence.txt", ".aae/runtime/executions"],
                "denied_prefixes": [".secret"],
            },
            "primary_executor": executor,
            "review": {
                "required": True,
                "skill": "project:independent-review",
                "executor": {**executor, "model": "fake-review"},
            },
            "accounting_directory": ".aae/state/governed-runs",
        }
        (root / ".aae/execution.json").write_text(
            json.dumps(configuration), encoding="utf-8"
        )
        return evidence

    def test_real_subprocess_path_records_separate_review_and_accounting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = self._project(root)
            run = execute_governed_task(
                root,
                task_id="engineering-fixture-v1",
                task="Qualify the engineering fixture and independently review it.",
                explicit_skill="project:engineering-qualify",
                capabilities=("engineering-qualification",),
                acceptance_criteria=("Evidence is bounded.",),
                evidence_paths=(evidence,),
            )
            self.assertEqual(run["status"], "succeeded")
            self.assertTrue(run["review"]["thread_distinct"])
            self.assertNotEqual(
                run["primary"]["invocation_id"], run["review"]["invocation_id"]
            )
            self.assertEqual(run["primary"]["changed_project_paths"], [])
            stored = json.loads((root / run["accounting_path"]).read_text(encoding="utf-8"))
            self.assertEqual(stored, {key: value for key, value in run.items() if key != "accounting_path"})
            self.assertTrue(governed_run_digest_is_valid(stored))
            accounting, errors, _ = build_agent_skill_accounting(root)
            self.assertEqual(errors, [])
            self.assertEqual(accounting["runtime_evidence"]["governed_run_count"], 1)
            self.assertEqual(
                accounting["runtime_evidence"]["governed_runs"][0]["run_id"],
                run["run_id"],
            )
            review_packet_path = root / ".aae/runtime/context-packets" / (
                run["review"]["context_packet_sha256"] + ".json"
            )
            review_packet = json.loads(
                review_packet_path.read_text(encoding="utf-8")
            )
            self.assertIn("ORIGINAL TASK", review_packet["task"])
            self.assertIn("Evidence is bounded.", review_packet["task"])

    def test_deterministic_outcome_contract_and_role_schemas(self) -> None:
        packet = {"acceptance_criteria": ["criterion-a", "criterion-b"]}

        def result(statuses: tuple[str, str], outcome: str) -> dict[str, Any]:
            return {
                "role": "executor",
                "outcome": outcome,
                "review_verdict": "not-applicable",
                "summary": "fixture",
                "findings": [],
                "verification": [
                    {
                        "criterion": criterion,
                        "status": status,
                        "evidence_refs": ["evidence.txt"],
                    }
                    for criterion, status in zip(
                        packet["acceptance_criteria"], statuses, strict=True
                    )
                ],
            }

        self.assertEqual(
            _validate_result_against_packet(
                result(("passed", "passed"), "succeeded"), packet
            ),
            "succeeded",
        )
        self.assertEqual(
            _validate_result_against_packet(
                result(("passed", "failed"), "failed"), packet
            ),
            "failed",
        )
        self.assertEqual(
            _validate_result_against_packet(
                result(("passed", "blocked"), "blocked"), packet
            ),
            "blocked",
        )
        self.assertEqual(
            _validate_result_against_packet(
                result(("blocked", "failed"), "failed"), packet
            ),
            "failed",
        )
        with self.assertRaisesRegex(ValueError, "reported=failed, derived=succeeded"):
            _validate_result_against_packet(
                result(("passed", "passed"), "failed"), packet
            )
        with self.assertRaisesRegex(ValueError, "reported=succeeded, derived=failed"):
            _validate_result_against_packet(
                result(("passed", "failed"), "succeeded"), packet
            )
        missing = result(("passed", "passed"), "succeeded")
        missing["verification"] = missing["verification"][:1]
        with self.assertRaisesRegex(ValueError, "exactly once"):
            _validate_result_against_packet(missing, packet)
        malformed = result(("passed", "passed"), "succeeded")
        malformed["verification"][1]["criterion"] = "criterion-a"
        with self.assertRaisesRegex(ValueError, "exactly once"):
            _validate_result_against_packet(malformed, packet)

        executor_schema = _codex_result_schema("executor")
        reviewer_schema = _codex_result_schema("reviewer")
        self.assertEqual(
            executor_schema["properties"]["review_verdict"]["enum"],
            ["not-applicable"],
        )
        self.assertEqual(
            reviewer_schema["properties"]["review_verdict"]["enum"],
            ["approved", "changes-required", "blocked"],
        )

    def test_human_accounting_renders_preselection_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = self._project(root)
            configuration_path = root / ".aae/execution.json"
            configuration = json.loads(configuration_path.read_text(encoding="utf-8"))
            configuration["primary_executor"]["model"] = None
            configuration_path.write_text(json.dumps(configuration), encoding="utf-8")
            run = execute_governed_task(
                root,
                task_id="preselection-failure-v1",
                task="Exercise a failure before skill selection.",
                explicit_skill="project:engineering-qualify",
                capabilities=("engineering-qualification",),
                acceptance_criteria=("Evidence is bounded.",),
                evidence_paths=(evidence,),
            )
            self.assertEqual(run["failure"]["phase"], "control-plane-prepublication")

            output = io.StringIO()
            with redirect_stdout(output):
                result = accounting_repository(root, False)

            self.assertEqual(result, 0)
            rendered = output.getvalue()
            self.assertIn(run["run_id"], rendered)
            self.assertIn("no skill selected; executor not started", rendered)

            json_output = io.StringIO()
            with redirect_stdout(json_output):
                result = accounting_repository(root, True)
            self.assertEqual(result, 0)
            accounting = json.loads(json_output.getvalue())
            self.assertIsNone(
                accounting["runtime_evidence"]["governed_runs"][0][
                    "selected_skill"
                ]
            )

    def test_invalid_executor_output_preserves_attempt_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = self._project(root)
            run = execute_governed_task(
                root,
                task_id="invalid-executor-v1",
                task="Exercise a contradictory executor outcome.",
                explicit_skill="project:engineering-qualify",
                capabilities=("engineering-qualification",),
                acceptance_criteria=("Evidence is bounded.",),
                evidence_paths=(evidence,),
            )

            self.assertEqual(run["status"], "failed")
            self.assertEqual(run["failure"]["phase"], "primary-invalid-output")
            self.assertEqual(run["primary"]["execution_disposition"], "invalid-output")
            self.assertEqual(run["primary"]["authoritative_outcome"], None)
            self.assertIsInstance(run["primary"]["thread_id"], str)
            self.assertEqual(run["primary"]["usage"]["input_tokens"], 12)
            self.assertRegex(run["primary"]["raw_output_sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(run["primary"]["parsed_output_sha256"], r"^[0-9a-f]{64}$")
            self.assertIn(
                "reported=failed, derived=succeeded",
                run["primary"]["validation_failure"]["message"],
            )
            execution_path = root / ".aae/runtime/executions" / (
                run["primary"]["execution_id"] + ".json"
            )
            execution = json.loads(execution_path.read_text(encoding="utf-8"))
            self.assertIsNone(execution["result"])
            self.assertNotIn("raw_output", execution)
            accounting, errors, _ = build_agent_skill_accounting(root)
            self.assertEqual(errors, [])
            self.assertEqual(accounting["runtime_evidence"]["governed_run_count"], 1)

    def test_contradictory_reviewer_output_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = self._project(root)
            run = execute_governed_task(
                root,
                task_id="invalid-reviewer-v1",
                task="Exercise a contradictory reviewer outcome.",
                explicit_skill="project:engineering-qualify",
                capabilities=("engineering-qualification",),
                acceptance_criteria=("Evidence is bounded.",),
                evidence_paths=(evidence,),
            )

            self.assertEqual(run["status"], "failed")
            self.assertEqual(run["failure"]["phase"], "review-invalid-output")
            self.assertEqual(run["primary"]["authoritative_outcome"], "succeeded")
            self.assertEqual(run["review"]["execution_disposition"], "invalid-output")
            self.assertIsInstance(run["review"]["thread_id"], str)
            self.assertIsNotNone(run["review"]["validation_failure"])
            accounting, errors, _ = build_agent_skill_accounting(root)
            self.assertEqual(errors, [])
            self.assertEqual(accounting["runtime_evidence"]["governed_run_count"], 1)

    def test_non_successful_primary_outcome_does_not_launch_reviewer(self) -> None:
        for status in ("failed", "blocked"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                evidence = self._project(root)
                run = execute_governed_task(
                    root,
                    task_id=f"{status}-primary-v1",
                    task=f"Exercise a {status} criterion outcome.",
                    explicit_skill="project:engineering-qualify",
                    capabilities=("engineering-qualification",),
                    acceptance_criteria=("Evidence is bounded.",),
                    evidence_paths=(evidence,),
                )

                self.assertEqual(run["status"], "failed")
                self.assertEqual(
                    run["failure"]["phase"], "primary-governed-outcome"
                )
                self.assertEqual(run["primary"]["authoritative_outcome"], status)
                self.assertIsNone(run["review"]["invocation_id"])
                invocation_paths = list(
                    (root / ".aae/runtime/invocations").glob("*.json")
                )
                self.assertEqual(len(invocation_paths), 1)

    def test_large_bounded_prompt_is_streamed_over_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = self._project(root)
            evidence.write_text("x" * 180_000, encoding="utf-8")
            config_path = root / ".aae/execution.json"
            configuration = json.loads(config_path.read_text(encoding="utf-8"))
            configuration["context_limits"]["max_bytes"] = 300_000
            configuration["context_limits"]["max_estimated_tokens"] = 300_000
            config_path.write_text(json.dumps(configuration), encoding="utf-8")

            run = execute_governed_task(
                root,
                task_id="large-stdin-fixture-v1",
                task="Qualify the large bounded engineering fixture.",
                explicit_skill="project:engineering-qualify",
                capabilities=("engineering-qualification",),
                acceptance_criteria=("Large evidence remains bounded.",),
                evidence_paths=(evidence,),
            )

            self.assertEqual(run["status"], "succeeded")
            self.assertGreater(run["primary"]["context_packet"]["measured"]["bytes"], 131_072)

    def test_denied_policy_never_launches_executor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = self._project(root, denied=True)
            marker = root / "fake-codex-ran"
            run = execute_governed_task(
                root,
                task_id="denied-fixture-v1",
                task="Attempt a denied engineering invocation.",
                explicit_skill="project:engineering-qualify",
                capabilities=("engineering-qualification",),
                acceptance_criteria=("Policy must deny.",),
                evidence_paths=(evidence,),
            )
            self.assertEqual(run["status"], "failed")
            self.assertEqual(run["failure"]["phase"], "primary-policy")
            self.assertTrue((root / run["accounting_path"]).is_file())
            self.assertFalse(marker.exists())

    def test_context_packet_limits_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = root / "large.txt"
            evidence.write_text("x" * 20, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "exceeds policy"):
                build_context_packet(
                    root,
                    task_id="bounded-v1",
                    task="bounded task",
                    acceptance_criteria=("Packet remains bounded.",),
                    evidence_paths=(evidence,),
                    limits={
                        "max_items": 3,
                        "max_files": 1,
                        "max_bytes": 10,
                        "max_estimated_tokens": 100,
                    },
                )

    def test_portable_configuration_allows_local_model_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._project(root)
            configuration_path = root / ".aae/execution.json"
            configuration = json.loads(configuration_path.read_text(encoding="utf-8"))
            configuration["primary_executor"]["model"] = None
            configuration["review"]["executor"]["model"] = None
            configuration_path.write_text(json.dumps(configuration), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "model must be explicit"):
                load_execution_configuration(root)
            portable = load_execution_configuration(
                root, require_effective_executor=False
            )
            self.assertIsNone(portable["primary_executor"]["model"])

    def test_executor_failure_is_durably_accounted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = self._project(root)
            executable = root / "codex"
            executable.write_text(
                """#!/usr/bin/env python3
import sys
if sys.argv[1:] == ["--version"]:
    print("fake-codex 2.0")
    raise SystemExit(0)
print("executor failed", file=sys.stderr)
raise SystemExit(7)
""",
                encoding="utf-8",
            )
            executable.chmod(0o700)
            configuration_path = root / ".aae/execution.json"
            configuration = json.loads(configuration_path.read_text(encoding="utf-8"))
            command_sha256 = hashlib.sha256(executable.read_bytes()).hexdigest()
            configuration["primary_executor"]["command_sha256"] = command_sha256
            configuration["review"]["executor"]["command_sha256"] = command_sha256
            configuration_path.write_text(json.dumps(configuration), encoding="utf-8")
            run = execute_governed_task(
                root,
                task_id="failed-fixture-v1",
                task="Exercise a failing governed executor.",
                explicit_skill="project:engineering-qualify",
                capabilities=("engineering-qualification",),
                acceptance_criteria=("Failure is accounted.",),
                evidence_paths=(evidence,),
            )
            self.assertEqual(run["status"], "failed")
            self.assertEqual(run["failure"]["phase"], "primary-execution")
            stored = json.loads(
                (root / run["accounting_path"]).read_text(encoding="utf-8")
            )
            self.assertTrue(governed_run_digest_is_valid(stored))
            accounting, errors, _ = build_agent_skill_accounting(root)
            self.assertEqual(errors, [])
            self.assertEqual(accounting["runtime_evidence"]["governed_run_count"], 1)

    def test_accounting_uses_configured_governed_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = self._project(root)
            configuration_path = root / ".aae/execution.json"
            configuration = json.loads(configuration_path.read_text(encoding="utf-8"))
            configuration["accounting_directory"] = ".aae/state/custom-runs"
            configuration_path.write_text(json.dumps(configuration), encoding="utf-8")
            run = execute_governed_task(
                root,
                task_id="custom-accounting-v1",
                task="Record a governed run in the configured directory.",
                explicit_skill="project:engineering-qualify",
                capabilities=("engineering-qualification",),
                acceptance_criteria=("Evidence is bounded.",),
                evidence_paths=(evidence,),
            )
            self.assertTrue(run["accounting_path"].startswith(".aae/state/custom-runs/"))
            accounting, errors, _ = build_agent_skill_accounting(root)
            self.assertEqual(errors, [])
            self.assertEqual(accounting["runtime_evidence"]["governed_run_count"], 1)

    def test_command_digest_drift_fails_before_semantic_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = self._project(root)
            configuration_path = root / ".aae/execution.json"
            configuration = json.loads(configuration_path.read_text(encoding="utf-8"))
            configuration["primary_executor"]["command_sha256"] = "0" * 64
            configuration_path.write_text(json.dumps(configuration), encoding="utf-8")
            run = execute_governed_task(
                root,
                task_id="command-drift-v1",
                task="Reject a drifted executor binary.",
                explicit_skill="project:engineering-qualify",
                capabilities=("engineering-qualification",),
                acceptance_criteria=("The executor digest matches.",),
                evidence_paths=(evidence,),
            )
            self.assertEqual(run["status"], "failed")
            self.assertEqual(run["failure"]["phase"], "control-plane-prepublication")
            self.assertFalse((root / "fake-codex-ran").exists())

    def test_denied_evidence_path_fails_before_semantic_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._project(root)
            denied = root / ".secret/evidence.txt"
            denied.parent.mkdir()
            denied.write_text("protected\n", encoding="utf-8")
            run = execute_governed_task(
                root,
                task_id="denied-evidence-v1",
                task="Attempt to load denied evidence.",
                explicit_skill="project:engineering-qualify",
                capabilities=("engineering-qualification",),
                acceptance_criteria=("Denied evidence stays unavailable.",),
                evidence_paths=(denied,),
            )
            self.assertEqual(run["status"], "failed")
            self.assertIsNone(run["primary"]["invocation_id"])
            self.assertFalse((root / "fake-codex-ran").exists())


if __name__ == "__main__":
    unittest.main()
