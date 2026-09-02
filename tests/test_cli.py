from __future__ import annotations

import json
import io
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from concurrent.futures import ThreadPoolExecutor

from aae.cli import (
    compile_repository,
    discover_intent,
    init_repository,
    registry_repository,
    validate_repository,
    watched_state,
    write_json,
)
from aae.skills import (
    build_skill_registry,
    discover_skills,
    load_skill_instructions,
    portable_path,
    record_skill_event,
    resolve_skill_metadata,
    summarize_skill_events,
)


class AaeCliTests(unittest.TestCase):
    def test_init_preserves_existing_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            existing = root / "AGENTS.md"
            existing.write_text("keep me\n", encoding="utf-8")
            self.assertEqual(init_repository(root), 0)
            self.assertEqual(existing.read_text(encoding="utf-8"), "keep me\n")
            self.assertTrue((root / ".aae/intent/project.md").exists())

    def test_local_overlay_follows_shared_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            intent = root / ".aae/intent"
            intent.mkdir(parents=True)
            (intent / "environment.local.md").write_text("# Local\n", encoding="utf-8")
            (intent / "environment.md").write_text("# Shared\n", encoding="utf-8")
            sources = discover_intent(root)
            self.assertEqual([item["local"] for item in sources], [False, True])
            self.assertEqual(sources[1]["overlay_of"], ".aae/intent/environment.md")

    def test_local_example_is_not_compiled_as_intent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            intent = root / ".aae/intent"
            intent.mkdir(parents=True)
            (intent / "models.md").write_text("# Shared\n", encoding="utf-8")
            (intent / "models.local.example.md").write_text("# Example\n", encoding="utf-8")
            sources = discover_intent(root)
            self.assertEqual([item["path"] for item in sources], [".aae/intent/models.md"])

    def test_compile_writes_manifest_and_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            intent = root / ".aae/intent"
            intent.mkdir(parents=True)
            (intent / "novel-concern.md").write_text("# Novel\nTreat this as intent.\n", encoding="utf-8")
            self.assertEqual(compile_repository(root, quiet=True), 0)
            manifest = json.loads((root / ".aae/runtime/effective-sources.json").read_text())
            self.assertEqual(manifest["source_count"], 1)
            self.assertEqual(manifest["schema_version"], 2)
            self.assertEqual(manifest["skill_registry"]["skill_count"], 0)
            request = (root / ".aae/runtime/compiler-request.md").read_text()
            self.assertIn("open-ended", request)
            self.assertIn("novel-concern.md", request)
            self.assertIn("Registered skills: 0", request)

    def test_seeded_project_validates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            testing = (root / ".aae/intent/testing.md").read_text(encoding="utf-8")
            self.assertIn("**Automated test creation:** on", testing)
            self.assertTrue((root / ".aae/intent/testing.local.example.md").exists())
            self.assertTrue((root / ".aae/intent/environment.local.example.md").exists())
            self.assertTrue((root / ".aae/skill-sources.json").exists())
            self.assertTrue((root / ".aae/skill-sources.local.example.json").exists())
            self.assertTrue((root / ".aae/skills/repo-recon/skill.json").exists())
            schemas = sorted((root / ".aae/schemas").glob("*.schema.json"))
            self.assertEqual(len(schemas), 9)
            for schema_path in schemas:
                schema = json.loads(schema_path.read_text(encoding="utf-8"))
                self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
            sources = discover_intent(root)
            self.assertFalse(any(str(item["path"]).endswith(".local.example.md") for item in sources))
            self.assertEqual(validate_repository(root), 0)

    def test_seeded_registry_is_metadata_only_and_direct_procedure_load_is_denied(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            registry, errors, warnings = build_skill_registry(root)
            self.assertEqual(errors, [])
            self.assertEqual(warnings, [])
            self.assertEqual(registry["skill_count"], 8)
            serialized = json.dumps(registry)
            self.assertNotIn("Stop when the next decision is supported", serialized)
            skill, instructions, error = load_skill_instructions(registry, "project:repo-recon")
            self.assertIsNotNone(skill)
            self.assertIsNone(instructions)
            self.assertIn("allowed InvocationPlan", str(error))
            assert skill is not None
            self.assertEqual(skill["name"], "repo-recon")

    def test_portable_content_identity_excludes_install_location(self) -> None:
        with tempfile.TemporaryDirectory() as first_directory, tempfile.TemporaryDirectory() as second_directory:
            first_root = Path(first_directory)
            second_root = Path(second_directory)
            init_repository(first_root)
            init_repository(second_root)
            first, first_errors, _ = build_skill_registry(first_root)
            second, second_errors, _ = build_skill_registry(second_root)
            self.assertEqual(first_errors, [])
            self.assertEqual(second_errors, [])
            self.assertEqual(
                first["registry_content_sha256"], second["registry_content_sha256"]
            )
            self.assertEqual(
                [skill["skill_content_sha256"] for skill in first["skills"]],
                [skill["skill_content_sha256"] for skill in second["skills"]],
            )
            self.assertNotEqual(
                first["runtime_instance"]["runtime_instance_id"],
                second["runtime_instance"]["runtime_instance_id"],
            )

    def test_windows_style_relative_procedure_path_is_portable(self) -> None:
        self.assertEqual(portable_path(r"nested\SKILL.md"), "nested/SKILL.md")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            manifest_path = root / ".aae/skills/repo-recon/skill.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            procedure = root / ".aae/skills/repo-recon/SKILL.md"
            nested = procedure.parent / "nested"
            nested.mkdir()
            procedure.replace(nested / procedure.name)
            manifest["procedure"] = r"nested\SKILL.md"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            registry, errors, _ = build_skill_registry(root)
            self.assertEqual(errors, [])
            skill = next(
                item for item in registry["skills"] if item["name"] == "repo-recon"
            )
            self.assertEqual(skill["procedure_path"].split("/")[-2:], ["nested", "SKILL.md"])

    def test_procedure_change_changes_skill_and_registry_content_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            before, errors, _ = build_skill_registry(root)
            self.assertEqual(errors, [])
            before_skill = next(
                item for item in before["skills"] if item["name"] == "repo-recon"
            )
            procedure = root / ".aae/skills/repo-recon/SKILL.md"
            procedure.write_text(
                procedure.read_text(encoding="utf-8") + "\nPortable identity change.\n",
                encoding="utf-8",
            )
            after, errors, _ = build_skill_registry(root)
            self.assertEqual(errors, [])
            after_skill = next(
                item for item in after["skills"] if item["name"] == "repo-recon"
            )
            self.assertNotEqual(
                before_skill["skill_content_sha256"], after_skill["skill_content_sha256"]
            )
            self.assertNotEqual(
                before["registry_content_sha256"], after["registry_content_sha256"]
            )

    def test_metadata_only_does_not_read_procedure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            registry, errors, _ = build_skill_registry(root)
            self.assertEqual(errors, [])
            procedure = root / ".aae/skills/repo-recon/SKILL.md"
            procedure.unlink()
            skill, error = resolve_skill_metadata(registry, "project:repo-recon")
            self.assertIsNone(error)
            assert skill is not None
            self.assertEqual(skill["name"], "repo-recon")
            _, instructions, load_error = load_skill_instructions(
                registry, "project:repo-recon"
            )
            self.assertIsNone(instructions)
            self.assertIn("allowed InvocationPlan", str(load_error))

    def test_watched_state_hashes_content_not_only_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            intent = root / ".aae/intent"
            intent.mkdir(parents=True)
            path = intent / "project.md"
            path.write_text("alpha\n", encoding="utf-8")
            first = watched_state(root)
            stat = path.stat()
            path.write_text("bravo\n", encoding="utf-8")
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            second = watched_state(root)
            self.assertNotEqual(first, second)

    def test_write_json_is_canonical_at_publication_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested/value.json"
            write_json(path, {"value": 1})
            self.assertEqual(json.loads(path.read_text()), {"value": 1})
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_discovery_filters_lifecycle_and_bounds_context(self) -> None:
        skills = []
        for index in range(30):
            skills.append(
                {
                    "registry_id": f"project:security-{index}",
                    "name": f"security-{index}",
                    "version": "1.0.0",
                    "description": "Review authentication security boundaries",
                    "capabilities": ["security-analysis"],
                    "triggers": ["authentication change"],
                    "applicable_when": ["security risk"],
                    "inputs": ["change"],
                    "produces": ["security findings"],
                    "requires": [],
                    "may_recommend": [],
                    "cost": {"context": "low", "reasoning": "medium"},
                    "independence_required": False,
                    "lifecycle": "retired" if index == 0 else "validated",
                    "execution": {"mode": "agentic", "side_effects": "read-only"},
                    "source": {"id": "project", "scope": "project", "adapter": "aae-json", "path": ".aae/skills"},
                }
            )
        result = discover_skills(
            {"skills": skills},
            task="change authentication behavior",
            capabilities=["security-analysis"],
            facts=["security risk"],
            architecture=["shared authentication service"],
            environment=["python asyncio"],
            risks=["credential exposure"],
            evidence_gaps=["consumer compatibility unknown"],
            candidate_limit=18,
            limit=4,
        )
        self.assertEqual(result["registry_skill_count"], 30)
        self.assertEqual(result["eligible_skill_count"], 29)
        self.assertEqual(result["metadata_candidate_count"], 29)
        self.assertEqual(len(result["shortlist"]), 4)
        self.assertEqual(result["clues"]["environment"], ["python asyncio"])
        self.assertNotIn("project:security-0", {item["registry_id"] for item in result["shortlist"]})

    def test_skill_md_adapter_normalizes_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            external = root / "runtime-skills" / "contract-check"
            external.mkdir(parents=True)
            (external / "SKILL.md").write_text(
                "---\n"
                "name: contract-check\n"
                "description: Check public contract compatibility\n"
                "capabilities: [compatibility-analysis, api-analysis]\n"
                "---\n"
                "# Full runtime instructions\n",
                encoding="utf-8",
            )
            aae = root / ".aae"
            aae.mkdir()
            (aae / "skill-sources.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "sources": [
                            {
                                "id": "runtime",
                                "scope": "runtime",
                                "adapter": "skill-md",
                                "path": str(root / "runtime-skills"),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            registry, errors, warnings = build_skill_registry(root)
            self.assertEqual(errors, [])
            self.assertEqual(warnings, [])
            self.assertEqual(registry["skill_count"], 1)
            skill = registry["skills"][0]
            self.assertEqual(skill["registry_id"], "runtime:contract-check")
            self.assertEqual(skill["capabilities"], ["compatibility-analysis", "api-analysis"])
            self.assertTrue(skill["adapted"])

    def test_invalid_manifest_fails_registry_and_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            intent = root / ".aae/intent"
            intent.mkdir(parents=True)
            (intent / "project.md").write_text("# Project\n", encoding="utf-8")
            skill_dir = root / ".aae/skills/broken"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Procedure\n", encoding="utf-8")
            (skill_dir / "skill.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "name": "Broken Name",
                        "version": "not-a-version",
                        "description": "broken",
                    }
                ),
                encoding="utf-8",
            )
            registry, errors, _ = build_skill_registry(root)
            self.assertEqual(registry["skill_count"], 0)
            self.assertTrue(any("kebab-case" in error for error in errors))
            with redirect_stdout(io.StringIO()):
                self.assertEqual(validate_repository(root), 1)

    def test_registry_command_writes_digest_bearing_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(registry_repository(root), 0)
            index = json.loads((root / ".aae/runtime/skill-registry.json").read_text())
            self.assertEqual(index["skill_count"], 8)
            self.assertRegex(index["registry_sha256"], r"^[0-9a-f]{64}$")

    def test_skill_events_track_versioned_selection_outcome_and_cost(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            registry, errors, _ = build_skill_registry(root)
            self.assertEqual(errors, [])
            self.assertIsNone(
                record_skill_event(root, registry, "project:repo-recon", "considered")
            )
            self.assertIsNone(
                record_skill_event(root, registry, "project:repo-recon", "selected")
            )
            self.assertIsNone(
                record_skill_event(
                    root,
                    registry,
                    "project:repo-recon",
                    "succeeded",
                    context_tokens=1200,
                    execution_cost=0.25,
                    evidence=".aae/specs/example/tasks.md",
                )
            )
            summaries, warnings = summarize_skill_events(root, registry)
            self.assertEqual(warnings, [])
            summary = summaries["project:repo-recon@0.1.0"]
            self.assertEqual(summary["considered"], 1)
            self.assertEqual(summary["selected"], 1)
            self.assertEqual(summary["succeeded"], 1)
            self.assertEqual(summary["context_tokens"], 1200)
            self.assertEqual(summary["execution_cost"], 0.25)

    def test_concurrent_skill_events_publish_without_corrupting_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            registry, errors, _ = build_skill_registry(root)
            self.assertEqual(errors, [])
            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(
                    executor.map(
                        lambda _: record_skill_event(
                            root, registry, "project:repo-recon", "considered"
                        ),
                        range(64),
                    )
                )
            self.assertEqual(results, [None] * 64)
            summaries, warnings = summarize_skill_events(root, registry)
            self.assertEqual(warnings, [])
            self.assertEqual(summaries["project:repo-recon@0.1.0"]["considered"], 64)


if __name__ == "__main__":
    unittest.main()
