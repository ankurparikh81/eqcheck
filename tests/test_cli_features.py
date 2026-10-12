"""
Integration tests for CLI features:
  - Header resolution (-I, -isystem)
  - Compiler flags (--cxxflags, trailing --)
  - Compilation database (-p / --compile-commands)
  - Git revision workflows (eqcheck foo.cpp -r HEAD~1, <rev>:<path>)
  - Sibling header resolution in Git-extracted files
"""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from eqcheck.cli import main


class TestCLIFeatures(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_include_directories_flag(self):
        # Create external header directory
        inc_dir = self.root / "ext_include"
        inc_dir.mkdir()
        (inc_dir / "custom_header.h").write_text("int compute_base() { return 100; }\n")

        ref_file = self.root / "ref.cpp"
        impl_file = self.root / "impl.cpp"

        ref_file.write_text(
            '#include "custom_header.h"\n'
            'int run() { return compute_base() + 5; }\n'
        )
        impl_file.write_text(
            '#include "custom_header.h"\n'
            'int run() { return 5 + compute_base(); }\n'
        )

        # 1. Without -I, compilation must fail
        ret = main([str(ref_file), str(impl_file)])
        self.assertEqual(ret, 1)

        # 2. With -I, compilation succeeds and equivalence passes
        ret_ok = main([str(ref_file), str(impl_file), "-I", str(inc_dir)])
        self.assertEqual(ret_ok, 0)

    def test_isystem_flag(self):
        sys_inc_dir = self.root / "sys_inc"
        sys_inc_dir.mkdir()
        (sys_inc_dir / "sys_config.h").write_text("#define MULTIPLIER 3\n")

        ref_file = self.root / "ref.cpp"
        impl_file = self.root / "impl.cpp"

        ref_file.write_text(
            '#include <sys_config.h>\n'
            'int run(int x) { return x * MULTIPLIER; }\n'
        )
        impl_file.write_text(
            '#include <sys_config.h>\n'
            'int run(int x) { return MULTIPLIER * x; }\n'
        )

        ret = main([str(ref_file), str(impl_file), "-isystem", str(sys_inc_dir)])
        self.assertEqual(ret, 0)

    def test_cxxflags_and_trailing_flags(self):
        ref_file = self.root / "ref.cpp"
        impl_file = self.root / "impl.cpp"

        code = (
            "#if MAGIC == 42\n"
            "int run() { return 1; }\n"
            "#else\n"
            "int run() { return 0; }\n"
            "#endif\n"
        )
        ref_file.write_text(code)
        impl_file.write_text(code)

        # Test with --cxxflags
        ret_cxxflags = main([str(ref_file), str(impl_file), "--cxxflags", "-DMAGIC=42"])
        self.assertEqual(ret_cxxflags, 0)

        # Test with trailing -- pass-through
        ret_trailing = main([str(ref_file), str(impl_file), "--", "-DMAGIC=42"])
        self.assertEqual(ret_trailing, 0)

    def test_compilation_database_integration(self):
        build_dir = self.root / "build"
        inc_dir = self.root / "include"
        src_dir = self.root / "src"
        build_dir.mkdir()
        inc_dir.mkdir()
        src_dir.mkdir()

        (inc_dir / "common.h").write_text("#define SCALE 10\n")

        ref_file = src_dir / "module.cpp"
        ref_file.write_text(
            '#include "common.h"\n'
            'int scale(int x) { return x * SCALE; }\n'
        )

        impl_file = src_dir / "module_refactored.cpp"
        impl_file.write_text(
            '#include "common.h"\n'
            'int scale(int x) { return SCALE * x; }\n'
        )

        # Create compile_commands.json with relative -I../include relative to build_dir
        compdb = [
            {
                "directory": str(build_dir),
                "command": f"clang++ -I../include -c {ref_file} -o module.o",
                "file": str(ref_file),
            }
        ]
        db_file = build_dir / "compile_commands.json"
        db_file.write_text(json.dumps(compdb))

        # Run with -p pointing to build directory
        ret = main([str(ref_file), str(impl_file), "-p", str(build_dir)])
        self.assertEqual(ret, 0)

    def test_git_workflow_single_file_and_sibling_headers(self):
        repo_dir = self.root / "my_repo"
        repo_dir.mkdir()

        # Initialize git repo
        subprocess.run(["git", "init"], cwd=str(repo_dir), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo_dir), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(repo_dir), check=True, capture_output=True)

        src_dir = repo_dir / "src"
        src_dir.mkdir()

        # Create a sibling header in the same directory
        sibling_header = src_dir / "sibling.h"
        sibling_header.write_text("int offset() { return 7; }\n")

        foo_cpp = src_dir / "foo.cpp"
        # Commit 1: ref version
        foo_cpp.write_text(
            '#include "sibling.h"\n'
            'int calc(int a) { return a + offset(); }\n'
        )
        subprocess.run(["git", "add", "."], cwd=str(repo_dir), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Commit 1"], cwd=str(repo_dir), check=True, capture_output=True)

        # Working tree: impl version (equivalent refactoring)
        foo_cpp.write_text(
            '#include "sibling.h"\n'
            'int calc(int a) { return offset() + a; }\n'
        )

        # Test 1: Single file shorthand `eqcheck foo.cpp -r HEAD`
        ret_rev = main([str(foo_cpp), "-r", "HEAD"])
        self.assertEqual(ret_rev, 0)

        # Test 2: Git object spec `eqcheck HEAD:src/foo.cpp src/foo.cpp`
        # Run from repo_dir
        orig_cwd = Path.cwd()
        try:
            import os
            os.chdir(repo_dir)
            ret_spec = main(["HEAD:src/foo.cpp", "src/foo.cpp"])
            self.assertEqual(ret_spec, 0)

            # Test 3: Modify working tree to be DIFFERENT
            foo_cpp.write_text(
                '#include "sibling.h"\n'
                'int calc(int a) { return a - offset(); }\n'
            )
            ret_diff = main(["src/foo.cpp", "-r", "HEAD"])
            self.assertEqual(ret_diff, 1)

        finally:
            import os
            os.chdir(orig_cwd)


if __name__ == "__main__":
    unittest.main()
