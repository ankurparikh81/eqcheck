"""
Unit tests for Git revision resolution and extraction.
"""

from pathlib import Path
import subprocess
import tempfile
import unittest

from eqcheck.git_utils import (
    GitError,
    extract_git_blob_to_temp,
    get_git_repo_root,
    is_git_spec,
    resolve_file_pair,
    resolve_source_target,
)


class TestGitUtils(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.tmpdir.name)

        # Initialize git repo with config
        subprocess.run(["git", "init"], cwd=str(self.repo_dir), check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Tester"],
            cwd=str(self.repo_dir),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "tester@test.com"],
            cwd=str(self.repo_dir),
            check=True,
            capture_output=True,
        )

        # Create first commit (v1)
        self.src_file = self.repo_dir / "src" / "compute.cpp"
        self.src_file.parent.mkdir(parents=True)
        self.src_file.write_text("int run() { return 10; }\n")
        subprocess.run(["git", "add", "."], cwd=str(self.repo_dir), check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Commit 1"],
            cwd=str(self.repo_dir),
            check=True,
            capture_output=True,
        )

        # Create second commit (v2)
        self.src_file.write_text("int run() { return 20; }\n")
        subprocess.run(["git", "commit", "-am", "Commit 2"], cwd=str(self.repo_dir), check=True, capture_output=True)

        # Working tree modification (v3)
        self.src_file.write_text("int run() { return 30; }\n")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_is_git_spec(self):
        self.assertTrue(is_git_spec("HEAD:src/compute.cpp"))
        self.assertTrue(is_git_spec("HEAD~1:compute.cpp"))
        self.assertTrue(is_git_spec("v1.0:test.cpp"))
        self.assertTrue(is_git_spec(":0:test.cpp"))
        self.assertFalse(is_git_spec("src/compute.cpp"))
        self.assertFalse(is_git_spec("C:\\Users\\test\\compute.cpp"))
        self.assertFalse(is_git_spec("D:/foo/bar.cpp"))

    def test_get_git_repo_root(self):
        root = get_git_repo_root(cwd=self.repo_dir / "src")
        self.assertEqual(root, self.repo_dir.resolve())

    def test_resolve_file_pair_single_file_rev(self):
        # Single file with -r HEAD~1
        ref_src, impl_src = resolve_file_pair(
            ref_arg=str(self.src_file),
            rev="HEAD~1",
        )
        try:
            self.assertTrue(ref_src.is_temporary)
            self.assertEqual(ref_src.path.read_text(), "int run() { return 10; }\n")
            self.assertEqual(ref_src.display_name, f"HEAD~1:{self.src_file}")

            self.assertFalse(impl_src.is_temporary)
            self.assertEqual(impl_src.path.read_text(), "int run() { return 30; }\n")
        finally:
            ref_src.cleanup()
            impl_src.cleanup()

    def test_resolve_file_pair_rev_range(self):
        # Single file with -r HEAD~1..HEAD
        ref_src, impl_src = resolve_file_pair(
            ref_arg=str(self.src_file),
            rev="HEAD~1..HEAD",
        )
        try:
            self.assertTrue(ref_src.is_temporary)
            self.assertEqual(ref_src.path.read_text(), "int run() { return 10; }\n")

            self.assertTrue(impl_src.is_temporary)
            self.assertEqual(impl_src.path.read_text(), "int run() { return 20; }\n")
        finally:
            ref_src.cleanup()
            impl_src.cleanup()

    def test_resolve_file_pair_git_specs(self):
        # Two files with explicit <rev>:<path>
        ref_spec = f"HEAD~1:{self.src_file}"
        impl_spec = f"HEAD:{self.src_file}"

        ref_src, impl_src = resolve_file_pair(ref_arg=ref_spec, impl_arg=impl_spec)
        try:
            self.assertTrue(ref_src.is_temporary)
            self.assertEqual(ref_src.path.read_text(), "int run() { return 10; }\n")

            self.assertTrue(impl_src.is_temporary)
            self.assertEqual(impl_src.path.read_text(), "int run() { return 20; }\n")
        finally:
            ref_src.cleanup()
            impl_src.cleanup()

    def test_missing_git_path_error(self):
        with self.assertRaises(GitError):
            resolve_source_target(f"HEAD~1:nonexistent.cpp")


if __name__ == "__main__":
    unittest.main()
