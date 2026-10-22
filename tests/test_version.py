from contextlib import redirect_stdout
from importlib.metadata import version
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from aae import __version__
from aae.cli import doctor, init_repository


class VersionTests(unittest.TestCase):
    def test_distribution_and_runtime_versions_match(self) -> None:
        self.assertEqual(version("adaptive-agentic-engineering"), __version__)
        self.assertEqual(__version__, "0.29.0")

    def test_doctor_reports_runtime_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(StringIO()) as output:
            root = Path(directory)
            init_repository(root)
            self.assertEqual(doctor(root), 0)
            self.assertIn(f"AAE version: {__version__}\n", output.getvalue())
