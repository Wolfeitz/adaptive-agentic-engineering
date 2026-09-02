from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from aae.adaptive import (
    build_ci_policy,
    build_historical_use_graph,
    build_otel_genai_trace_export,
    build_promotion_proposal,
    evaluate_skill_lifecycle,
    rerank_with_retriever,
    route_model,
)
from aae.cli import init_repository
from aae.control import invoke_skill, record_invocation_outcome
from aae.skills import build_skill_registry, record_skill_event


class FakeRetriever:
    def rank(self, request: object, candidates: object) -> list[dict[str, object]]:
        del request, candidates
        return [
            {"registry_id": "project:repo-recon", "score": 0.8},
            {"registry_id": "project:runtime-diagnosis", "score": 0.9},
        ]


class BadRetriever:
    def rank(self, request: object, candidates: object) -> list[dict[str, object]]:
        del request, candidates
        return [{"registry_id": "project:not-a-candidate", "score": 1.0}]


class AdaptiveTests(unittest.TestCase):
    def test_model_routing_is_deterministic_and_policy_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            profiles = {
                "schema_version": 1,
                "profiles": [
                    {
                        "id": "cloud-cheap",
                        "provider": "example",
                        "model": "cloud",
                        "location": "cloud",
                        "capabilities": ["reasoning"],
                        "data_classifications": ["public"],
                        "network": "required",
                        "available": True,
                        "preference": 1,
                        "cost_rank": 1,
                        "fallback_to": [],
                    },
                    {
                        "id": "local-safe",
                        "provider": "local",
                        "model": "local",
                        "location": "local",
                        "capabilities": ["reasoning", "structured-output"],
                        "data_classifications": ["public", "internal"],
                        "network": "none",
                        "available": True,
                        "preference": 2,
                        "cost_rank": 2,
                        "fallback_to": [],
                    },
                ],
            }
            (root / ".aae/model-profiles.json").write_text(
                json.dumps(profiles), encoding="utf-8"
            )
            route = route_model(
                root,
                capabilities=["reasoning"],
                data_classification="internal",
                network_available=False,
            )
            self.assertEqual(route["selected"]["id"], "local-safe")
            self.assertEqual(
                route["rejected"][0]["reasons"],
                ["data-classification", "network"],
            )
            repeated = route_model(
                root,
                capabilities=["reasoning"],
                data_classification="internal",
                network_available=False,
            )
            self.assertEqual(route["route_sha256"], repeated["route_sha256"])

    def test_semantic_retrieval_cannot_escape_bounded_candidate_set(self) -> None:
        candidates = [
            {"registry_id": "project:repo-recon", "score": 1},
            {"registry_id": "project:runtime-diagnosis", "score": 2},
        ]
        result = rerank_with_retriever(FakeRetriever(), {}, candidates, limit=1)
        self.assertEqual(
            result["shortlist"][0]["registry_id"], "project:runtime-diagnosis"
        )
        with self.assertRaisesRegex(ValueError, "out-of-candidate"):
            rerank_with_retriever(BadRetriever(), {}, candidates, limit=1)

    def test_lifecycle_evaluation_is_advisory_and_never_mutates_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            registry, errors, _ = build_skill_registry(root)
            self.assertEqual(errors, [])
            manifest = root / ".aae/skills/repo-recon/skill.json"
            original = manifest.read_bytes()
            for index in range(5):
                record_skill_event(root, registry, "project:repo-recon", "selected")
                record_skill_event(
                    root,
                    registry,
                    "project:repo-recon",
                    "succeeded" if index < 4 else "failed",
                    evidence=f"evidence-{index}",
                )
            evaluation = evaluate_skill_lifecycle(root, "project:repo-recon")
            self.assertTrue(evaluation["eligible_for_proposal"])
            proposal = build_promotion_proposal(
                root, "project:repo-recon", "validated"
            )
            self.assertEqual(proposal["decision"], "proposal-only-not-applied")
            with self.assertRaisesRegex(ValueError, "exactly one"):
                build_promotion_proposal(
                    root, "project:repo-recon", "deprecated"
                )
            self.assertEqual(manifest.read_bytes(), original)

    def test_history_trace_and_ci_exports_are_deterministic_and_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            registry, errors, _ = build_skill_registry(root)
            self.assertEqual(errors, [])
            record, _, invocation_errors = invoke_skill(
                root,
                registry,
                task="inspect the repository",
                explicit_skill="project:repo-recon",
                runtime_profile={
                    "provider": "local",
                    "model": "fixture-model",
                    "available_tools": ["filesystem-search", "version-control-read"],
                    "model_capabilities": ["reasoning"],
                    "model_data_classifications": ["public", "internal"],
                    "data_classification": "internal",
                    "platform": "linux",
                    "network_available": False,
                    "fresh_context": False,
                },
            )
            self.assertEqual(invocation_errors, [])
            self.assertIsNone(
                record_invocation_outcome(
                    root,
                    record["invocation_id"],
                    outcome="succeeded",
                    evidence="tests",
                    verification="passed",
                    context_tokens=10,
                    execution_cost=0.0,
                )
            )
            graph = build_historical_use_graph(root)
            self.assertEqual(len(graph["edges"]), 1)
            traces = build_otel_genai_trace_export(root)
            serialized = json.dumps(traces)
            self.assertIn("gen_ai.operation.name", serialized)
            self.assertNotIn("inspect the repository", serialized)
            self.assertEqual(
                build_ci_policy("github")["payload_sha256"],
                build_ci_policy("github")["payload_sha256"],
            )


if __name__ == "__main__":
    unittest.main()
