from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

from aae.cli import init_repository
from aae.hooks import (
    load_hook_config,
    normalize_native_hook,
    parse_payload_values,
    process_event,
    process_native_hook,
)


class HookEventTests(unittest.TestCase):
    def test_init_installs_native_hook_adapters_without_enabling_actions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            codex = json.loads((root / ".codex/hooks.json").read_text())
            copilot = json.loads((root / ".github/hooks/aae.json").read_text())
            self.assertEqual(
                codex["hooks"]["PostToolUse"][0]["hooks"][0]["command"],
                "aae native-hook codex",
            )
            self.assertEqual(
                copilot["hooks"]["postToolUse"][0]["bash"],
                "aae native-hook copilot --event PostToolUse",
            )
            config, errors = load_hook_config(root)
            self.assertEqual(errors, [])
            self.assertTrue(all(not rule["enabled"] for rule in config["rules"]))

    def test_seeded_hooks_are_valid_and_disabled_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            config, errors = load_hook_config(root)
            self.assertEqual(errors, [])
            self.assertTrue(all(not rule["enabled"] for rule in config["rules"]))
            record, procedures, event_errors = process_event(
                root, event="test-failed", payload={"suite": "integration"}
            )
            self.assertEqual(event_errors, [])
            self.assertEqual(record["status"], "no-match")
            self.assertEqual(procedures, {})

    def test_event_requests_one_skill_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            hooks_path = root / ".aae/hooks.json"
            config = json.loads(hooks_path.read_text(encoding="utf-8"))
            config["rules"][0]["enabled"] = True
            hooks_path.write_text(json.dumps(config), encoding="utf-8")
            profile = {"available_tools": ["runtime-read"]}
            record, procedures, errors = process_event(
                root,
                event="test-failed",
                payload={"suite": "integration"},
                runtime_profile=profile,
                idempotency_key="ci-run-42",
            )
            self.assertEqual(errors, [])
            self.assertEqual(record["status"], "skill-requested")
            self.assertEqual(record["matched_rules"], ["diagnose-repeated-test-failure"])
            self.assertEqual(len(procedures), 1)
            invocation_id = record["actions"][0]["invocation_id"]
            invocation = json.loads(
                (root / ".aae/runtime/invocations" / f"{invocation_id}.json").read_text()
            )
            self.assertEqual(invocation["trigger_provenance"]["event_id"], record["event_id"])
            repeated, repeated_procedures, repeated_errors = process_event(
                root,
                event="test-failed",
                payload={"suite": "integration"},
                runtime_profile=profile,
                idempotency_key="ci-run-42",
            )
            self.assertEqual(repeated_errors, [])
            self.assertTrue(repeated["duplicate_delivery"])
            self.assertEqual(repeated_procedures, {})
            self.assertEqual(len(list((root / ".aae/runtime/invocations").glob("*.json"))), 1)

    def test_files_changed_path_runs_configured_check_directly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            config = {
                "schema_version": 1,
                "rules": [
                    {
                        "id": "check-python",
                        "on": "files-changed",
                        "paths": ["src/**/*.py"],
                        "run_check": [sys.executable, "-c", "raise SystemExit(0)"],
                    }
                ],
            }
            (root / ".aae/hooks.json").write_text(json.dumps(config), encoding="utf-8")
            ignored, _, errors = process_event(
                root, event="files-changed", payload={"paths": ["README.md"]}
            )
            self.assertEqual(errors, [])
            self.assertEqual(ignored["status"], "no-match")
            completed, _, errors = process_event(
                root, event="files-changed", payload={"paths": ["src/aae/cli.py"]}
            )
            self.assertEqual(errors, [])
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(completed["actions"][0]["status"], "passed")

    def test_destructive_check_requires_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            config = {
                "schema_version": 1,
                "rules": [
                    {
                        "id": "destructive-check",
                        "on": "deployment-requested",
                        "run_check": [sys.executable, "-c", "raise SystemExit(0)"],
                        "destructive": True,
                    }
                ],
            }
            (root / ".aae/hooks.json").write_text(json.dumps(config), encoding="utf-8")
            denied, _, errors = process_event(
                root, event="deployment-requested", payload={}
            )
            self.assertEqual(errors, [])
            self.assertEqual(denied["status"], "denied")
            allowed, _, errors = process_event(
                root,
                event="deployment-requested",
                payload={},
                runtime_profile={"approvals": ["destructive"]},
            )
            self.assertEqual(errors, [])
            self.assertEqual(allowed["actions"][0]["status"], "passed")

    def test_payload_parser_and_invalid_configuration_fail_closed(self) -> None:
        payload, errors = parse_payload_values(["paths=[\"src/a.py\"]", "failed=true"])
        self.assertEqual(errors, [])
        self.assertEqual(payload, {"paths": ["src/a.py"], "failed": True})
        _, errors = parse_payload_values(["missing-separator", "x=1", "x=2"])
        self.assertEqual(len(errors), 2)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            (root / ".aae/hooks.json").write_text(
                '{"schema_version": 1, "rules": [{"id": "bad", "on": "test-failed"}]}',
                encoding="utf-8",
            )
            record, procedures, event_errors = process_event(
                root, event="test-failed", payload={}
            )
            self.assertEqual(record["status"], "configuration-invalid")
            self.assertEqual(procedures, {})
            self.assertTrue(event_errors)

    def test_codex_apply_patch_normalizes_to_files_changed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.mkdir(exist_ok=True)
            event, payload, key, errors = normalize_native_hook(
                root,
                "codex",
                {
                    "hook_event_name": "PostToolUse",
                    "session_id": "session-1",
                    "turn_id": "turn-1",
                    "tool_use_id": "tool-1",
                    "tool_name": "apply_patch",
                    "tool_input": {
                        "command": "*** Begin Patch\n*** Update File: src/aae/cli.py\n"
                    },
                    "tool_response": "sensitive content that must not persist",
                },
            )
            self.assertEqual(errors, [])
            self.assertEqual(event, "files-changed")
            self.assertEqual(payload["paths"], ["src/aae/cli.py"])
            self.assertEqual(payload["session_id"], "session-1")
            self.assertNotIn("tool_response", payload)
            self.assertIsNotNone(key)

    def test_native_delivery_persists_only_normalized_facts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            config = {
                "schema_version": 1,
                "rules": [
                    {
                        "id": "check-python",
                        "on": "files-changed",
                        "paths": ["src/**/*.py"],
                        "run_check": [sys.executable, "-c", "raise SystemExit(0)"],
                    }
                ],
            }
            (root / ".aae/hooks.json").write_text(json.dumps(config), encoding="utf-8")
            secret = "do-not-store-this-tool-response"
            record, output, errors = process_native_hook(
                root,
                "copilot",
                {
                    "session_id": "session-2",
                    "tool_name": "write_file",
                    "tool_input": {"file_path": "src/aae/hooks.py"},
                    "tool_response": secret,
                },
                native_event_override="PostToolUse",
            )
            self.assertEqual(errors, [])
            assert record is not None
            self.assertEqual(record["status"], "completed")
            self.assertIsNone(output)
            saved = next((root / ".aae/runtime/hook-events").glob("*.json")).read_text()
            self.assertNotIn(secret, saved)
            self.assertNotIn("tool_response", saved)
            self.assertIn("native_payload_sha256", saved)

    def test_native_no_match_creates_no_event_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            record, output, errors = process_native_hook(
                root,
                "codex",
                {
                    "hook_event_name": "PostToolUse",
                    "session_id": "session-3",
                    "tool_name": "Read",
                    "tool_input": {"path": "README.md"},
                },
            )
            self.assertEqual(errors, [])
            assert record is not None
            self.assertEqual(record["status"], "no-match")
            self.assertIsNone(output)
            self.assertFalse((root / ".aae/runtime/hook-events").exists())

    def test_native_skill_request_returns_host_specific_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            config = {
                "schema_version": 1,
                "rules": [
                    {
                        "id": "review-python-change",
                        "on": "files-changed",
                        "paths": ["src/**/*.py"],
                        "request_skill": "project:review-lesson-extractor",
                    }
                ],
            }
            (root / ".aae/hooks.json").write_text(json.dumps(config), encoding="utf-8")
            native = {
                "hook_event_name": "PostToolUse",
                "session_id": "session-4",
                "tool_name": "Write",
                "tool_input": {"file_path": "src/aae/new.py"},
            }
            codex_record, codex_output, codex_errors = process_native_hook(
                root, "codex", native
            )
            self.assertEqual(codex_errors, [])
            assert codex_record is not None and codex_output is not None
            self.assertEqual(codex_record["status"], "skill-requested")
            self.assertEqual(
                codex_output["hookSpecificOutput"]["hookEventName"], "PostToolUse"
            )
            self.assertIn(
                "Review Lesson Extractor",
                codex_output["hookSpecificOutput"]["additionalContext"],
            )

            native["session_id"] = "session-5"
            _, copilot_output, copilot_errors = process_native_hook(
                root, "copilot", native
            )
            self.assertEqual(copilot_errors, [])
            assert copilot_output is not None
            self.assertIn("additionalContext", copilot_output)


if __name__ == "__main__":
    unittest.main()
