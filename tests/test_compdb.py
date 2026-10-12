"""
Unit tests for CompilationDatabase parsing and flag extraction.
"""

import json
from pathlib import Path
import tempfile
import unittest

from eqcheck.compdb import CompilationDatabase, CompilationDatabaseError


class TestCompilationDatabase(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.src_file = self.root / "src" / "foo.cpp"
        self.src_file.parent.mkdir(parents=True)
        self.src_file.write_text("int foo() { return 1; }\n")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_extract_flags_from_command(self):
        build_dir = self.root / "build"
        build_dir.mkdir()
        db_file = build_dir / "compile_commands.json"

        data = [
            {
                "directory": str(build_dir),
                "command": f"/usr/bin/c++ -I../include -isystem /usr/include/eigen3 -DDEBUG=1 -O3 -Wall -Werror -g -std=c++17 -c {self.src_file} -o foo.o",
                "file": str(self.src_file),
            }
        ]
        db_file.write_text(json.dumps(data))

        cdb = CompilationDatabase(db_file)
        flags = cdb.extract_flags_for_file(self.src_file)

        # -I../include should resolve relative to build_dir
        expected_inc = str((build_dir / "../include").resolve())
        self.assertIn("-I", flags)
        idx = flags.index("-I")
        self.assertEqual(flags[idx + 1], expected_inc)

        self.assertIn("-isystem", flags)
        idx_sys = flags.index("-isystem")
        self.assertEqual(flags[idx_sys + 1], "/usr/include/eigen3")

        self.assertIn("-DDEBUG=1", flags)
        self.assertIn("-std=c++17", flags)

        # Verify stripped flags
        self.assertNotIn("-O3", flags)
        self.assertNotIn("-Wall", flags)
        self.assertNotIn("-Werror", flags)
        self.assertNotIn("-g", flags)
        self.assertNotIn("-c", flags)
        self.assertNotIn("-o", flags)
        self.assertNotIn("foo.o", flags)
        self.assertNotIn("/usr/bin/c++", flags)

    def test_extract_flags_from_arguments(self):
        build_dir = self.root / "build"
        build_dir.mkdir()
        db_file = build_dir / "compile_commands.json"

        data = [
            {
                "directory": str(build_dir),
                "arguments": [
                    "clang++",
                    "-I", "include",
                    "-mavx2",
                    "-DFOO=BAR",
                    "-c", str(self.src_file),
                    "-o", "out.o"
                ],
                "file": str(self.src_file),
            }
        ]
        db_file.write_text(json.dumps(data))

        cdb = CompilationDatabase(db_file)
        flags = cdb.extract_flags_for_file(self.src_file)

        expected_inc = str((build_dir / "include").resolve())
        self.assertIn("-I", flags)
        self.assertIn(expected_inc, flags)
        self.assertIn("-mavx2", flags)
        self.assertIn("-DFOO=BAR", flags)
        self.assertNotIn("clang++", flags)
        self.assertNotIn("-c", flags)

    def test_discover_directory(self):
        build_dir = self.root / "build"
        build_dir.mkdir()
        db_file = build_dir / "compile_commands.json"
        db_file.write_text("[]")

        cdb = CompilationDatabase.discover(build_dir)
        self.assertIsNotNone(cdb)
        self.assertEqual(cdb.db_path, db_file.resolve())


if __name__ == "__main__":
    unittest.main()
