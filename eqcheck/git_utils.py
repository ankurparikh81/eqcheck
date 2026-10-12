"""
Git integration utilities for extracting file revisions and resolving targets.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Optional, Tuple


class GitError(Exception):
    """Raised when a Git operation fails."""
    pass


@dataclass
class ResolvedSource:
    """Represents a resolved C++ source file ready for compilation."""
    path: Path
    display_name: str
    original_repo_path: Path
    is_temporary: bool = False
    _temp_dir: Optional[tempfile.TemporaryDirectory] = None

    def cleanup(self) -> None:
        """Clean up temporary resources if any."""
        if self._temp_dir:
            self._temp_dir.cleanup()
            self._temp_dir = None


def is_git_spec(target: str) -> bool:
    """
    Check if a target string represents a Git object specifier (e.g., 'HEAD~1:foo.cpp').
    Excludes Windows drive letters like 'C:\\path' or 'D:/path'.
    """
    if ":" not in target:
        return False
    parts = target.split(":", 1)
    # Check for Windows drive letter
    if len(parts[0]) == 1 and parts[0].isalpha():
        return False
    return True


def get_git_repo_root(cwd: Path | str | None = None) -> Path:
    """Find the root directory of the current Git repository."""
    work_dir = Path(cwd).resolve() if cwd else Path.cwd().resolve()
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            raise GitError(f"Not a git repository (or any of the parent directories): {work_dir}")
        return Path(res.stdout.strip()).resolve()
    except FileNotFoundError:
        raise GitError("Git executable ('git') was not found on PATH.")


def find_repo_root_for_path(target_path: Path | str | None = None) -> Path:
    """Find Git repo root, checking target_path directory first if provided."""
    if target_path:
        p = Path(target_path)
        candidate = p if p.is_dir() else p.parent
        if candidate.exists():
            try:
                return get_git_repo_root(candidate)
            except GitError:
                pass
    return get_git_repo_root(Path.cwd())


def get_repo_relative_path(file_path: Path | str, repo_root: Path) -> Path:
    """
    Convert a file path to a path relative to the Git repository root.
    Handles absolute paths, CWD-relative paths, and repo-relative paths.
    """
    p = Path(file_path)

    # 1. If absolute path, relative_to repo_root
    if p.is_absolute():
        abs_p = p.resolve()
        try:
            return abs_p.relative_to(repo_root)
        except ValueError:
            raise GitError(f"File path '{abs_p}' is outside Git repository root '{repo_root}'")

    # 2. If relative, check if resolving against CWD is within repo_root
    cwd_resolved = (Path.cwd() / p).resolve()
    try:
        if cwd_resolved.exists():
            return cwd_resolved.relative_to(repo_root)
    except ValueError:
        pass

    # 3. Check if path directly relative to repo_root exists
    repo_resolved = (repo_root / p).resolve()
    if repo_resolved.exists():
        try:
            return repo_resolved.relative_to(repo_root)
        except ValueError:
            pass

    # 4. Fallback: try cwd_resolved first if inside repo_root, else repo_resolved
    try:
        return cwd_resolved.relative_to(repo_root)
    except ValueError:
        try:
            return repo_resolved.relative_to(repo_root)
        except ValueError:
            raise GitError(f"File path '{file_path}' cannot be resolved inside Git repo '{repo_root}'")


def extract_git_blob_to_temp(
    revision: str,
    repo_rel_path: Path,
    repo_root: Path,
    display_name: str,
) -> ResolvedSource:
    """
    Extract a file revision from Git into a temporary file.
    """
    git_spec = f"{revision}:{repo_rel_path.as_posix()}"
    try:
        # Verify object exists
        cat_res = subprocess.run(
            ["git", "cat-file", "-e", git_spec],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
        if cat_res.returncode != 0:
            raise GitError(
                f"Path '{repo_rel_path}' does not exist at revision '{revision}' in Git repository."
            )

        # Extract content
        show_res = subprocess.run(
            ["git", "show", git_spec],
            cwd=str(repo_root),
            capture_output=True,
            text=False,  # read raw bytes in case of encodings
            check=False,
        )
        if show_res.returncode != 0:
            err = show_res.stderr.decode("utf-8", errors="replace") if show_res.stderr else ""
            raise GitError(f"Failed to extract '{git_spec}' from Git: {err}")

        # Write to temp file with original file extension
        suffix = repo_rel_path.suffix or ".cpp"
        tmp_dir = tempfile.TemporaryDirectory()
        tmp_path = Path(tmp_dir.name) / f"git_extracted_{repo_rel_path.stem}{suffix}"
        tmp_path.write_bytes(show_res.stdout)

        original_abs = (repo_root / repo_rel_path).resolve()
        return ResolvedSource(
            path=tmp_path,
            display_name=display_name,
            original_repo_path=original_abs,
            is_temporary=True,
            _temp_dir=tmp_dir,
        )
    except Exception:
        raise


def resolve_source_target(
    target: str | Path,
    git_rev: Optional[str] = None,
    repo_root: Optional[Path] = None,
) -> ResolvedSource:
    """
    Resolve a source target string or path into a ResolvedSource.

    Handles:
      - git_rev provided explicitly (e.g. git_rev='HEAD~1', target='foo.cpp')
      - target with colon git spec (e.g. target='HEAD~1:foo.cpp')
      - standard local filesystem path
    """
    target_str = str(target)

    # Determine if this target involves Git
    if git_rev is not None:
        if repo_root is None:
            repo_root = find_repo_root_for_path(target_str)
        repo_rel = get_repo_relative_path(target_str, repo_root)
        display = f"{git_rev}:{target_str}"
        return extract_git_blob_to_temp(git_rev, repo_rel, repo_root, display)

    if is_git_spec(target_str):
        rev_part, path_part = target_str.split(":", 1)
        if repo_root is None:
            repo_root = find_repo_root_for_path(path_part)
        repo_rel = get_repo_relative_path(path_part, repo_root)
        display = target_str
        return extract_git_blob_to_temp(rev_part, repo_rel, repo_root, display)

    # Standard local file
    local_path = Path(target_str).resolve()
    return ResolvedSource(
        path=local_path,
        display_name=target_str,
        original_repo_path=local_path,
        is_temporary=False,
    )


def resolve_file_pair(
    ref_arg: str,
    impl_arg: Optional[str] = None,
    rev: Optional[str] = None,
    ref_rev: Optional[str] = None,
    impl_rev: Optional[str] = None,
    repo_root: Optional[Path] = None,
) -> Tuple[ResolvedSource, ResolvedSource]:
    """
    Resolve user CLI arguments into reference and implementation ResolvedSources.

    Supported patterns:
      1. Single file with -r REV (e.g. eqcheck foo.cpp -r HEAD~1):
         ref = HEAD~1:foo.cpp, impl = foo.cpp (working tree)
      2. Single file with -r REV1..REV2 (e.g. eqcheck foo.cpp -r HEAD~2..HEAD~1):
         ref = HEAD~2:foo.cpp, impl = HEAD~1:foo.cpp
      3. Single file with --ref-rev / --impl-rev:
         ref = ref_rev:foo.cpp, impl = impl_rev:foo.cpp (or working tree)
      4. Two files with or without git specs (e.g. eqcheck HEAD~1:foo.cpp foo.cpp):
         ref = resolve(ref_arg), impl = resolve(impl_arg)
    """
    if repo_root is None:
        # Infer repo root from ref_arg or impl_arg
        target_hint = ref_arg.split(":", 1)[1] if is_git_spec(ref_arg) else ref_arg
        try:
            repo_root = find_repo_root_for_path(target_hint)
        except GitError:
            repo_root = None

    if impl_arg is None:
        # 1-file mode
        if rev:
            if ".." in rev:
                r1, r2 = rev.split("..", 1)
                ref_src = resolve_source_target(ref_arg, git_rev=r1, repo_root=repo_root)
                impl_src = resolve_source_target(ref_arg, git_rev=r2, repo_root=repo_root)
                return ref_src, impl_src
            else:
                ref_src = resolve_source_target(ref_arg, git_rev=rev, repo_root=repo_root)
                impl_src = resolve_source_target(ref_arg, repo_root=repo_root)
                return ref_src, impl_src
        elif ref_rev or impl_rev:
            ref_src = resolve_source_target(ref_arg, git_rev=ref_rev, repo_root=repo_root)
            impl_src = resolve_source_target(ref_arg, git_rev=impl_rev, repo_root=repo_root)
            return ref_src, impl_src
        else:
            raise ValueError(
                f"Single file '{ref_arg}' specified without Git revision. "
                "Provide a second implementation file or specify -r / --rev <commit>."
            )

    # 2-file mode
    ref_src = resolve_source_target(ref_arg, git_rev=ref_rev, repo_root=repo_root)
    impl_src = resolve_source_target(impl_arg, git_rev=impl_rev, repo_root=repo_root)
    return ref_src, impl_src
