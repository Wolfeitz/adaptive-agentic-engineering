from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

from aae.cli import init_repository
from aae.hooks import load_hook_config, parse_payload_values, process_event


class HookEventTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
