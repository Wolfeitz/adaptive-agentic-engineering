from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from aae.accounting import build_agent_skill_accounting
from aae.cli import init_repository
from aae.execution import (
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
prompt = sys.argv[-1]
role = \"reviewer\" if \"ROLE\\nreviewer\" in prompt else \"executor\"
packet = json.loads(prompt.split(\"BOUNDED EVIDENCE PACKET\\n\", 1)[1])
result = {
    \"role\": role,
    \"outcome\": \"succeeded\",
    \"review_verdict\": \"approved\" if role == \"reviewer\" else \"not-applicable\",
    \"summary\": \"bounded fixture completed\",
    \"findings\": [{\"severity\": \"info\", \"statement\": \"fixture\", \"evidence_refs\": [\"evidence.txt\"]}],
    \"verification\": [
        {\"criterion\": criterion, \"status\": \"passed\", \"evidence_refs\": [\"evidence.txt\"]}
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
