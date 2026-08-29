from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from aae.cli import compile_repository, discover_intent, init_repository, validate_repository


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
            request = (root / ".aae/runtime/compiler-request.md").read_text()
            self.assertIn("open-ended", request)
            self.assertIn("novel-concern.md", request)

    def test_seeded_project_validates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init_repository(root)
            self.assertEqual(validate_repository(root), 0)


if __name__ == "__main__":
    unittest.main()
