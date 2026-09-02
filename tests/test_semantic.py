from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from aae.cli import init_repository
from aae.semantic import (
    build_impact_graph,
    build_impact_delta,
    build_task_packets,
    canonical_digest,
    compile_with_provider,
    export_tracker_items,
    load_active_task_packet,
    publish_semantic_document,
    rollback_release,
    unresolved_material_items,
    validate_semantic_document,
)
from aae.skills import build_skill_registry


class FakeProvider:
    name = "fake"

    def __init__(self, document: dict[str, object]) -> None:
        self.document = document

    def compile(self, request: object) -> dict[str, object]:
        return self.document


class SemanticCompilerTests(unittest.TestCase):
    def make_project(self, root: Path) -> tuple[dict[str, object], dict[str, object]]:
        init_repository(root)
        source = root / ".aae/intent/project.md"
        source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
        document: dict[str, object] = {
            "schema_version": 1,
            "project": {"name": "Example"},
            "compiler": {"provider": "test-fixture", "model": "none"},
            "statements": [
                {
                    "id": "project-purpose",
                    "kind": "fact",
                    "text": "The project demonstrates semantic publication.",
                    "sources": [
                        {"path": ".aae/intent/project.md", "sha256": source_digest}
                    ],
                }
            ],
            "capabilities": [
                {
                    "id": "verified-change",
                    "description": "Deliver a verified change.",
                    "inputs": ["task"],
                    "outputs": ["implementation"],
                    "evidence": ["acceptance results"],
                    "statement_ids": ["project-purpose"],
                    "skill_capabilities": ["acceptance-verification"],
                }
            ],
            "tasks": [
                {
                    "id": "implement-change",
                    "title": "Implement the change",
                    "description": "Produce and verify one bounded outcome.",
                    "capabilities": ["verified-change"],
                    "depends_on": [],
                    "consequence": "high",
                    "evidence_gap": "medium",
                    "risks": ["compatibility"],
                    "acceptance": ["The behavior is exercised end to end."],
                    "selected_skills": ["project:acceptance-verify"],
                }
            ],
            "conflicts": [],
            "questions": [],
            "artifacts": [
                {
                    "id": "implementation",
                    "path": "src/example.py",
                    "task_ids": ["implement-change"],
                }
            ],
        }
        registry, errors, warnings = build_skill_registry(root)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])
        return document, registry

    def test_semantic_validation_impact_and_provider_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            document, registry = self.make_project(Path(directory))
            errors, warnings = validate_semantic_document(document, registry)
            self.assertEqual(errors, [])
            self.assertEqual(warnings, [])
            graph = build_impact_graph(document)
            self.assertRegex(graph["graph_sha256"], r"^[0-9a-f]{64}$")
            relationships = {edge["relationship"] for edge in graph["edges"]}
            self.assertEqual(
                relationships, {"motivates", "required-by", "selected-for", "produces"}
            )
            self.assertEqual(compile_with_provider(FakeProvider(document), {}), document)
            delta = build_impact_delta(None, document)
            self.assertIn("task:implement-change", delta["added"])
            self.assertEqual(delta["added"], delta["impacted"])

    def test_material_question_blocks_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document, registry = self.make_project(root)
            document["questions"] = [
                {
                    "id": "deployment-owner",
                    "question": "Who approves production deployment?",
                    "material": True,
                }
            ]
            errors, warnings = validate_semantic_document(document, registry)
            self.assertEqual(errors, [])
            self.assertIn("material question remains unanswered", warnings[0])
            self.assertEqual(
                unresolved_material_items(document), ["question:deployment-owner"]
            )
            with self.assertRaisesRegex(ValueError, "unresolved material items"):
                publish_semantic_document(root, document)

    def test_task_packets_preserve_skill_and_risk_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            document, registry = self.make_project(Path(directory))
            packets, errors = build_task_packets(document, registry)
            self.assertEqual(errors, [])
            self.assertEqual(len(packets), 1)
            packet = packets[0]
            self.assertTrue(
                packet["execution_classification"]["independent_review_required"]
            )
            invocation = packet["selected_skill_invocations"][0]
            self.assertEqual(invocation["registry_id"], "project:acceptance-verify")
            self.assertRegex(invocation["procedure_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(packet["packet_sha256"], canonical_digest({
                key: value for key, value in packet.items() if key != "packet_sha256"
            }))

    def test_atomic_publication_idempotence_rollback_and_exports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_document, _ = self.make_project(root)
            first = publish_semantic_document(root, first_document)
            self.assertTrue(first.created)
            self.assertFalse(publish_semantic_document(root, first_document).created)
            packet = load_active_task_packet(root, "implement-change")
            self.assertEqual(packet["task_id"], "implement-change")
            review = first.release_path / "review-packets/implement-change.json"
            self.assertTrue(review.is_file())
            for provider in ("azure", "github", "jira"):
                exported = export_tracker_items(root, provider)
                self.assertEqual(len(exported), 1)
                self.assertEqual(exported[0]["provider"], provider)

            second_document = copy.deepcopy(first_document)
            second_document["project"] = {"name": "Example Two"}
            statements = second_document["statements"]
            assert isinstance(statements, list)
            first_statement = statements[0]
            assert isinstance(first_statement, dict)
            first_statement["text"] = "The project demonstrates changed publication."
            second = publish_semantic_document(root, second_document)
            self.assertNotEqual(first.release_id, second.release_id)
            delta = json.loads((second.release_path / "impact-delta.json").read_text())
            self.assertEqual(delta["changed"], ["statement:project-purpose"])
            self.assertIn("capability:verified-change", delta["impacted"])
            self.assertIn("task:implement-change", delta["impacted"])
            self.assertEqual(rollback_release(root), first.release_id)
            active = json.loads(
                (root / ".aae/generated/active-release.json").read_text()
            )
            self.assertEqual(active["release_id"], first.release_id)

    def test_path_traversal_and_invalid_provider_output_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            document, registry = self.make_project(Path(directory))
            document["artifacts"] = [
                {"id": "escape", "path": "../escape", "task_ids": ["implement-change"]}
            ]
            errors, _ = validate_semantic_document(document, registry)
            self.assertTrue(any("safe relative path" in error for error in errors))
            with self.assertRaisesRegex(ValueError, "provider returned invalid"):
                compile_with_provider(FakeProvider({"schema_version": 1}), {})

    def test_publication_verifies_declared_source_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document, _ = self.make_project(root)
            source = root / ".aae/intent/project.md"
            source.write_text(source.read_text(encoding="utf-8") + "\nchanged\n")
            with self.assertRaisesRegex(ValueError, "does not match"):
                publish_semantic_document(root, document)


if __name__ == "__main__":
    unittest.main()
