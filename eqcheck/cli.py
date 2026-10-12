"""
Command Line Interface for eqcheck.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import sys
from typing import Optional

from eqcheck.callgraph import FunctionNotFoundError, slice_all_functions, slice_callgraph
from eqcheck.compdb import CompilationDatabase, CompilationDatabaseError
from eqcheck.compiler import CompilerError, compile_to_llvm_ir
from eqcheck.comparator import ComparisonResult, compare_modules
from eqcheck.git_utils import GitError, ResolvedSource, resolve_file_pair
from eqcheck.ir_parser import parse_llvm_ir
from eqcheck.matcher import match_functions, parse_match_file
from eqcheck.report import format_json_report, format_text_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eqcheck",
        description="Determine semantic equivalence between C++ sources via LLVM IR (supports Git revisions and compile_commands.json)."
    )
    parser.add_argument(
        "ref_file",
        help="Reference C++ source file or Git revision target (e.g. HEAD~1:foo.cpp)"
    )
    parser.add_argument(
        "impl_file",
        nargs="?",
        default=None,
        help="Implementation C++ source file (optional if --rev / -r is specified)"
    )
    parser.add_argument(
        "--top",
        required=False,
        default=None,
        help="Optional name of the top function to check. If omitted, all matching functions in both files are checked."
    )
    parser.add_argument(
        "--match-file",
        help="Path to JSON file specifying explicit {ref_name: impl_name} function matches."
    )
    parser.add_argument(
        "--no-inline",
        action="store_true",
        default=False,
        help="Disable compiler inlining (default is inlining enabled for whole-TU refactoring)."
    )
    parser.add_argument(
        "-I", "--include-dir",
        action="append",
        dest="include_dirs",
        default=[],
        help="Add directory to include search path (can be specified multiple times)."
    )
    parser.add_argument(
        "-isystem",
        action="append",
        dest="isystem_dirs",
        default=[],
        help="Add directory to system include search path (can be specified multiple times)."
    )
    parser.add_argument(
        "--cxxflags",
        type=str,
        default="",
        help="Additional compiler flags string (e.g. '-DFOO=1 -std=c++20')."
    )
    parser.add_argument(
        "-p", "--compile-commands",
        type=str,
        default=None,
        help="Path to compile_commands.json or build directory containing it."
    )
    parser.add_argument(
        "-r", "--rev",
        type=str,
        default=None,
        help="Git revision for reference file when checking single file (e.g. -r HEAD~1 or range -r HEAD~1..HEAD)."
    )
    parser.add_argument(
        "--ref-rev",
        type=str,
        default=None,
        help="Explicit Git revision for reference file."
    )
    parser.add_argument(
        "--impl-rev",
        type=str,
        default=None,
        help="Explicit Git revision for implementation file."
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed normalized IR diffs and progress information."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output structured JSON verdict instead of human-readable text."
    )
    return parser


def parse_cli_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    """Parse CLI arguments, separating flags before and after '--' pass-through."""
    if argv is None:
        argv = sys.argv[1:]
    if "--" in argv:
        dash_idx = argv.index("--")
        eqcheck_args = argv[:dash_idx]
        trailing_cxxflags = argv[dash_idx + 1:]
    else:
        eqcheck_args = argv
        trailing_cxxflags = []

    # Handle --cxxflags where the next argument may start with '-' (e.g. -DMAGIC=42)
    # argparse treats -X as another flag unless passed as --cxxflags=...
    normalized_args = []
    i = 0
    while i < len(eqcheck_args):
        tok = eqcheck_args[i]
        if tok == "--cxxflags" and i + 1 < len(eqcheck_args):
            normalized_args.append(f"--cxxflags={eqcheck_args[i + 1]}")
            i += 2
            continue
        normalized_args.append(tok)
        i += 1

    parser = build_parser()
    args = parser.parse_args(normalized_args)
    return args, trailing_cxxflags


def run_equivalence_check(
    ref_file: str | Path | ResolvedSource,
    impl_file: str | Path | ResolvedSource | None = None,
    top: str | None = None,
    match_file: str | Path | None = None,
    opt_level: str = "-O2",
    no_inline: bool = False,
    include_dirs: list[str] | None = None,
    isystem_dirs: list[str] | None = None,
    extra_flags: list[str] | None = None,
    compile_commands: str | Path | None = None,
    rev: str | None = None,
    ref_rev: str | None = None,
    impl_rev: str | None = None,
) -> ComparisonResult:
    """Run full equivalence check pipeline and return ComparisonResult."""
    # 1. Resolve source files (local disk or Git object)
    if isinstance(ref_file, ResolvedSource) and isinstance(impl_file, ResolvedSource):
        ref_source = ref_file
        impl_source = impl_file
    else:
        ref_arg = str(ref_file.path) if isinstance(ref_file, ResolvedSource) else str(ref_file)
        impl_arg = None
        if impl_file is not None:
            impl_arg = str(impl_file.path) if isinstance(impl_file, ResolvedSource) else str(impl_file)

        ref_source, impl_source = resolve_file_pair(
            ref_arg=ref_arg,
            impl_arg=impl_arg,
            rev=rev,
            ref_rev=ref_rev,
            impl_rev=impl_rev,
        )

    try:
        # 2. Extract flags from compilation database if available
        ref_db_flags: list[str] = []
        impl_db_flags: list[str] = []
        cdb: Optional[CompilationDatabase] = None
        try:
            cdb = CompilationDatabase.discover(compile_commands)
        except Exception:
            if compile_commands is not None:
                raise

        if cdb is not None:
            ref_db_flags = cdb.extract_flags_for_file(ref_source.original_repo_path)
            impl_db_flags = cdb.extract_flags_for_file(impl_source.original_repo_path)
            # If one file has flags and other doesn't (e.g. refactored temp/out-of-tree file), borrow flags
            if not ref_db_flags and impl_db_flags:
                ref_db_flags = list(impl_db_flags)
            elif not impl_db_flags and ref_db_flags:
                impl_db_flags = list(ref_db_flags)

        # 3. Handle sibling includes for temporary Git extractions
        ref_iquote: list[str] = []
        impl_iquote: list[str] = []
        if ref_source.is_temporary:
            ref_iquote.append(str(ref_source.original_repo_path.parent))
        if impl_source.is_temporary:
            impl_iquote.append(str(impl_source.original_repo_path.parent))

        # 4. Compile both files to LLVM IR
        ref_all_extra = ref_db_flags + (extra_flags or [])
        impl_all_extra = impl_db_flags + (extra_flags or [])

        ref_ir = compile_to_llvm_ir(
            ref_source.path,
            opt_level=opt_level,
            no_inline=no_inline,
            include_dirs=include_dirs,
            isystem_dirs=isystem_dirs,
            iquote_dirs=ref_iquote,
            extra_flags=ref_all_extra,
        )
        impl_ir = compile_to_llvm_ir(
            impl_source.path,
            opt_level=opt_level,
            no_inline=no_inline,
            include_dirs=include_dirs,
            isystem_dirs=isystem_dirs,
            iquote_dirs=impl_iquote,
            extra_flags=impl_all_extra,
        )

        # 5. Parse LLVM IR with actual source file paths on disk for snippet extraction
        ref_module = parse_llvm_ir(ref_ir, source_file_path=str(ref_source.path))
        impl_module = parse_llvm_ir(impl_ir, source_file_path=str(impl_source.path))

        # 6. Construct call-graph slices (scoped to top or all functions)
        if top:
            top_ref_name = top
            top_impl_name = top
            if match_file:
                user_matches = parse_match_file(match_file)
                if top_ref_name in user_matches:
                    top_impl_name = user_matches[top_ref_name]

            ref_slice = slice_callgraph(ref_module, top_ref_name)
            impl_slice = slice_callgraph(impl_module, top_impl_name)
        else:
            ref_slice = slice_all_functions(ref_module)
            impl_slice = slice_all_functions(impl_module)

        # 7. Run 4-tier function matching
        mapping = match_functions(ref_slice, impl_slice, match_file=match_file)

        # 8. Compare modules with display names
        return compare_modules(
            ref_slice,
            impl_slice,
            mapping,
            ref_display_name=ref_source.display_name,
            impl_display_name=impl_source.display_name,
        )

    finally:
        # Clean up temporary Git extractions
        ref_source.cleanup()
        impl_source.cleanup()


def main(argv: list[str] | None = None) -> int:
    try:
        args, trailing_cxxflags = parse_cli_args(argv)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 1

    extra_flags = []
    if args.cxxflags:
        extra_flags.extend(shlex.split(args.cxxflags))
    if trailing_cxxflags:
        extra_flags.extend(trailing_cxxflags)

    try:
        result = run_equivalence_check(
            ref_file=args.ref_file,
            impl_file=args.impl_file,
            top=args.top,
            match_file=args.match_file,
            no_inline=args.no_inline,
            include_dirs=args.include_dirs,
            isystem_dirs=args.isystem_dirs,
            extra_flags=extra_flags,
            compile_commands=args.compile_commands,
            rev=args.rev,
            ref_rev=args.ref_rev,
            impl_rev=args.impl_rev,
        )

        use_color = sys.stdout.isatty() and not args.json
        if args.json:
            print(format_json_report(result))
        else:
            print(format_text_report(result, verbose=args.verbose, use_color=use_color))

        return 0 if result.is_equivalent else 1

    except (CompilerError, FunctionNotFoundError, FileNotFoundError, GitError, CompilationDatabaseError, ValueError) as e:
        if args.json:
            print(json.dumps({"error": str(e), "is_equivalent": False, "verdict": "ERROR"}))
        else:
            print(f"\033[31mError: {e}\033[0m", file=sys.stderr)
        return 1
    except Exception as e:
        if args.json:
            print(json.dumps({"error": str(e), "is_equivalent": False, "verdict": "ERROR"}))
        else:
            print(f"\033[31mUnexpected error: {e}\033[0m", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
