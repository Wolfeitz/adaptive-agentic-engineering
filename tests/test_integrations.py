from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
from typing import Mapping
import unittest

from aae.cli import init_repository
from aae.integrations import build_tracker_request_specs, submit_tracker_items
from aae.semantic import publish_semantic_document


def published_project(root: Path) -> None:
    init_repository(root)
    source = root / ".aae/intent/project.md"
    source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    document = {
        "schema_version": 1,
        "project": {"name": "Tracker fixture"},
        "statements": [
            {
                "id": "purpose",
                "kind": "fact",
                "text": "Exercise tracker submission.",
                "sources": [
                    {"path": ".aae/intent/project.md", "sha256": source_digest}
                ],
            }
        ],
        "capabilities": [
            {
                "id": "deliver",
                "description": "Deliver work.",
                "inputs": ["task"],
                "outputs": ["result"],
                "evidence": ["tests"],
                "statement_ids": ["purpose"],
            }
        ],
        "tasks": [
            {
                "id": "tracker-task",
                "title": "Tracker task",
                "description": "Exercise one adapter.",
                "capabilities": ["deliver"],
                "depends_on": [],
                "consequence": "low",
                "evidence_gap": "low",
                "risks": [],
                "acceptance": ["The request is canonical."],
                "selected_skills": [],
            }
        ],
        "conflicts": [],
        "questions": [],
        "artifacts": [],
    }
    publish_semantic_document(root, document)


class RecordingTransport:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, str], bytes]] = []

    def send(
        self, url: str, headers: Mapping[str, str], body: bytes
    ) -> dict[str, object]:
        header_dict = dict(headers)
        self.requests.append((url, header_dict, body))
        return {"status": 201, "id": "fixture"}


class TrackerIntegrationTests(unittest.TestCase):
    def test_submission_is_confirmation_gated_and_does_not_persist_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            published_project(root)
            with self.assertRaisesRegex(ValueError, "explicit confirmation"):
                submit_tracker_items(
                    root,
                    "github",
                    "https://api.example.invalid/issues",
                    "secret-token",
                    confirm_external_write=False,
                )
            transport = RecordingTransport()
            result = submit_tracker_items(
                root,
                "github",
                "https://api.example.invalid/issues",
                "secret-token",
                confirm_external_write=True,
                transport=transport,
            )
            self.assertEqual(result["submitted"], 1)
            self.assertEqual(transport.requests[0][1]["Authorization"], "Bearer secret-token")
            self.assertNotIn("secret-token", str(result))

    def test_azure_conversion_is_deterministic_and_endpoint_is_https(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            published_project(root)
            first = build_tracker_request_specs(
                root, "azure", "https://dev.azure.example/workitems"
            )
            second = build_tracker_request_specs(
                root, "azure", "https://dev.azure.example/workitems"
            )
            self.assertEqual(first[0]["request_sha256"], second[0]["request_sha256"])
            self.assertIsInstance(first[0]["payload"], list)
            with self.assertRaisesRegex(ValueError, "HTTPS"):
                build_tracker_request_specs(
                    root, "azure", "http://remote.example/workitems"
                )

    def test_jira_requires_explicit_project_and_issue_type_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            published_project(root)
            with self.assertRaisesRegex(ValueError, "fields.project"):
                build_tracker_request_specs(
                    root, "jira", "https://jira.example/rest/api/3/issue"
                )
            specs = build_tracker_request_specs(
                root,
                "jira",
                "https://jira.example/rest/api/3/issue",
                payload_defaults={
                    "fields": {
                        "project": {"key": "AAE"},
                        "issuetype": {"name": "Task"},
                    }
                },
            )
            self.assertEqual(specs[0]["payload"]["fields"]["project"]["key"], "AAE")
            self.assertNotIn("authorization", str(specs).lower())
