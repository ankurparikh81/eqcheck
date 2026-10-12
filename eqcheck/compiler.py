"""
Compiler interface for invoking Clang and LLVM optimization passes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


class CompilerError(Exception):
    """Raised when compilation or optimization fails."""
    pass


def find_tool(name: str, fallback_candidates: list[str]) -> str:
    """Find a tool binary from PATH or fallback candidates."""
    found = shutil.which(name)
    if found:
        return found
    for cand in fallback_candidates:
        cand_path = shutil.which(cand)
        if cand_path:
            return cand_path
        if os.path.exists(cand) and os.access(cand, os.X_OK):
            return cand
    raise FileNotFoundError(f"Required tool '{name}' could not be found.")


def get_clangxx() -> str:
    return find_tool("clang++", ["clang++-21", "/usr/lib/llvm-21/bin/clang++", "g++"])


def get_opt() -> str:
    return find_tool("opt-21", ["opt", "/usr/lib/llvm-21/bin/opt", "/usr/bin/opt"])


def get_cxxfilt() -> Optional[str]:
    try:
        return find_tool("c++filt", ["llvm-cxxfilt-21", "llvm-cxxfilt", "/usr/lib/llvm-21/bin/llvm-cxxfilt"])
    except FileNotFoundError:
        return None


def get_llvm_diff() -> Optional[str]:
    try:
        return find_tool("llvm-diff-21", ["llvm-diff", "/usr/lib/llvm-21/bin/llvm-diff"])
    except FileNotFoundError:
        return None


_DEMANGLE_CACHE: dict[str, str] = {}


def demangle(symbol: str) -> str:
    """Demangle a C++ symbol using c++filt if available."""
    if not symbol.startswith("_Z"):
        return symbol
    if symbol in _DEMANGLE_CACHE:
        return _DEMANGLE_CACHE[symbol]
    tool = get_cxxfilt()
    if not tool:
        return symbol
    try:
        res = subprocess.run(
            [tool, symbol],
            capture_output=True,
            text=True,
            check=True
        )
        demangled = res.stdout.strip()
        _DEMANGLE_CACHE[symbol] = demangled
        return demangled
    except Exception:
        return symbol


def compile_to_llvm_ir(
    source_file: Path | str,
    opt_level: str = "-O2",
    no_inline: bool = True,
    include_dirs: Optional[list[str]] = None,
    isystem_dirs: Optional[list[str]] = None,
    iquote_dirs: Optional[list[str]] = None,
    extra_flags: Optional[list[str]] = None,
) -> str:
    """
    Compile a C++ source file to canonical LLVM IR text.

    Args:
        source_file: Path to C++ source file.
        opt_level: Optimization level flag, e.g. '-O0', '-O1', '-O2', '-O3'.
        no_inline: If True, adds -fno-inline so helper functions are preserved in call graph.
        include_dirs: Directories to add to include search path (-I).
        isystem_dirs: Directories to add to system include search path (-isystem).
        iquote_dirs: Directories to add for quoted includes (-iquote).
        extra_flags: Additional compiler flags.

    Returns:
        The generated LLVM IR as a string.
    """
    source_path = Path(source_file).resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"Source file not found: {source_path}")

    clangxx = get_clangxx()
    opt = get_opt()

    with tempfile.TemporaryDirectory() as tmpdir:
        bc_out = Path(tmpdir) / "module.bc"
        ll_out = Path(tmpdir) / "module.ll"

        clang_cmd = [
            clangxx,
            "-c",
            "-emit-llvm",
            opt_level,
            "-gline-tables-only",
            "-fdiscard-value-names",
        ]
        if no_inline:
            clang_cmd.append("-fno-inline")
        if iquote_dirs:
            for d in iquote_dirs:
                clang_cmd.extend(["-iquote", str(d)])
        if isystem_dirs:
            for d in isystem_dirs:
                clang_cmd.extend(["-isystem", str(d)])
        if include_dirs:
            for d in include_dirs:
                clang_cmd.extend(["-I", str(d)])
        if extra_flags:
            clang_cmd.extend(extra_flags)

        clang_cmd.extend([str(source_path), "-o", str(bc_out)])

        try:
            res = subprocess.run(
                clang_cmd,
                capture_output=True,
                text=True,
                check=False
            )
            if res.returncode != 0:
                raise CompilerError(f"Clang compilation failed:\n{res.stderr}")
        except FileNotFoundError as e:
            raise CompilerError(f"Failed to execute compiler: {e}")

        # Run opt canonicalization passes
        # passes: instcombine, simplifycfg, reassociate, dce
        opt_passes = "instcombine,simplifycfg,reassociate,dce"
        opt_cmd = [
            opt,
            f"-passes={opt_passes}",
            str(bc_out),
            "-S",
            "-o",
            str(ll_out)
        ]

        try:
            res_opt = subprocess.run(
                opt_cmd,
                capture_output=True,
                text=True,
                check=False
            )
            if res_opt.returncode != 0:
                raise CompilerError(f"opt optimization passes failed:\n{res_opt.stderr}")
        except FileNotFoundError as e:
            raise CompilerError(f"Failed to execute opt: {e}")

        return ll_out.read_text(encoding="utf-8")
