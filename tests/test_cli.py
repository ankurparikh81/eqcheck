"""
CLI and integration tests for eqcheck.
"""

import json
from pathlib import Path
import subprocess
import sys
import unittest

BIN_EQCHECK = Path(__file__).parent.parent / "bin" / "eqcheck"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestCLI(unittest.TestCase):

    def run_cli(self, args: list[str]) -> subprocess.CompletedProcess:
        cmd = [sys.executable, str(BIN_EQCHECK)] + args
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_cli_help(self):
        res = self.run_cli(["--help"])
        self.assertEqual(res.returncode, 0)
        self.assertIn("Determine semantic equivalence", res.stdout)
        self.assertIn("--top", res.stdout)
        self.assertIn("--match-file", res.stdout)
        self.assertIn("--no-inline", res.stdout)

    def test_cli_whole_tu_default(self):
        ref = str(FIXTURES_DIR / "formatting" / "ref.cpp")
        impl = str(FIXTURES_DIR / "formatting" / "impl.cpp")
        res = self.run_cli([ref, impl])
        self.assertEqual(res.returncode, 0)
        self.assertIn("EQUIVALENT", res.stdout)
        self.assertIn("Entire Translation Unit", res.stdout)

    def test_cli_equivalent_with_top(self):
        ref = str(FIXTURES_DIR / "formatting" / "ref.cpp")
        impl = str(FIXTURES_DIR / "formatting" / "impl.cpp")
        res = self.run_cli([ref, impl, "--top", "compute_dot"])
        self.assertEqual(res.returncode, 0)
        self.assertIn("EQUIVALENT", res.stdout)

    def test_cli_different_exit_code(self):
        ref = str(FIXTURES_DIR / "semantic_diff" / "ref.cpp")
        impl = str(FIXTURES_DIR / "semantic_diff" / "impl.cpp")
        res = self.run_cli([ref, impl])
        self.assertEqual(res.returncode, 1)
        self.assertIn("DIFFERENT", res.stdout)

    def test_cli_json_output(self):
        ref = str(FIXTURES_DIR / "formatting" / "ref.cpp")
        impl = str(FIXTURES_DIR / "formatting" / "impl.cpp")
        res = self.run_cli([ref, impl, "--json"])
        self.assertEqual(res.returncode, 0)
        data = json.loads(res.stdout)
        self.assertTrue(data["is_equivalent"])
        self.assertEqual(data["verdict"], "EQUIVALENT")
        self.assertEqual(len(data["matches"]), 1)
        self.assertIn("warnings", data)

    def test_cli_missing_top_error(self):
        ref = str(FIXTURES_DIR / "formatting" / "ref.cpp")
        impl = str(FIXTURES_DIR / "formatting" / "impl.cpp")
        res = self.run_cli([ref, impl, "--top", "invalid_entry"])
        self.assertEqual(res.returncode, 1)
        self.assertIn("not found", res.stderr + res.stdout)


if __name__ == "__main__":
    unittest.main()
