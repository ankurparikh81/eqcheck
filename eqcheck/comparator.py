"""
Structural CFG & Instruction Comparator:
  - Compares matched function pairs structurally.
  - Verifies CFG topology, block successors, and instruction semantics.
  - Handles independent instruction reordering within basic blocks.
  - Implements unmatched function semantics (unmatched functions do not cause
    non-equivalence unless they affect the observable execution path from top).
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Optional

from eqcheck.callgraph import CallGraphSlice
from eqcheck.matcher import FunctionMapping
from eqcheck.normalizer import NormalizedBlock, NormalizedFunction, normalize_function


from pathlib import Path


@dataclass
class SourceMismatch:
    ref_file: Optional[str]
    ref_line: Optional[int]
    ref_snippet: str
    impl_file: Optional[str]
    impl_line: Optional[int]
    impl_snippet: str


def get_source_snippet(file_path: Optional[str], line_no: Optional[int], context_lines: int = 2) -> str:
    """Extract a formatted source snippet with line numbers centered around line_no."""
    if not file_path or line_no is None:
        return ""
    p = Path(file_path)
    if not p.exists():
        return ""
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
        start = max(0, line_no - 1 - context_lines)
        end = min(len(lines), line_no + context_lines)
        out = []
        for idx in range(start, end):
            curr_line = idx + 1
            marker = ">" if curr_line == line_no else " "
            out.append(f"{marker} {curr_line:4d} | {lines[idx]}")
        return "\n".join(out)
    except Exception:
        return ""


@dataclass
class FunctionDiff:
    ref_name: str
    impl_name: str
    tier: str
    is_match: bool
    diff_text: str = ""
    block_mismatches: list[str] = field(default_factory=list)
    source_mismatches: list[SourceMismatch] = field(default_factory=list)


@dataclass
class ComparisonResult:
    is_equivalent: bool
    verdict: str  # "EQUIVALENT" or "DIFFERENT"
    top_function_ref: str
    top_function_impl: str
    ref_display_name: str = ""
    impl_display_name: str = ""
    function_diffs: list[FunctionDiff] = field(default_factory=list)
    unmatched_affecting: list[str] = field(default_factory=list)
    unmatched_redundant: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error_message: Optional[str] = None


def compare_instruction_lists(
    ref_insts: list[str],
    impl_insts: list[str]
) -> tuple[bool, str]:
    """
    Compare instructions within a basic block.
    Returns (is_match, diff_message).
    """
    if ref_insts == impl_insts:
        return True, ""

    # Check multiset equality (e.g. if independent instructions were emitted in different order)
    if len(ref_insts) == len(impl_insts) and len(ref_insts) > 0:
        if ref_insts[-1] == impl_insts[-1]:
            non_term_ref = sorted(ref_insts[:-1])
            non_term_impl = sorted(impl_insts[:-1])
            if non_term_ref == non_term_impl:
                return True, ""

    diff = "\n".join(difflib.unified_diff(
        ref_insts,
        impl_insts,
        fromfile="reference",
        tofile="implementation",
        lineterm=""
    ))
    return False, diff


def compare_blocks(
    ref_block: NormalizedBlock,
    impl_block: NormalizedBlock,
    ref_file: Optional[str] = None,
    impl_file: Optional[str] = None,
    ref_display_file: Optional[str] = None,
    impl_display_file: Optional[str] = None,
) -> tuple[bool, str, Optional[SourceMismatch]]:
    """Compare two normalized basic blocks and extract source mismatch if any."""
    if ref_block.successors != impl_block.successors:
        return False, f"Successor mismatch: {ref_block.successors} vs {impl_block.successors}", None

    is_match, diff = compare_instruction_lists(ref_block.instructions, impl_block.instructions)
    if is_match:
        return True, "", None

    # Find the index of the first instruction mismatch
    first_diff_idx = 0
    min_len = min(len(ref_block.instructions), len(impl_block.instructions))
    for i in range(min_len):
        if ref_block.instructions[i] != impl_block.instructions[i]:
            first_diff_idx = i
            break
    else:
        first_diff_idx = min_len

    ref_line = (
        ref_block.instruction_source_lines[first_diff_idx]
        if first_diff_idx < len(ref_block.instruction_source_lines)
        else None
    )
    impl_line = (
        impl_block.instruction_source_lines[first_diff_idx]
        if first_diff_idx < len(impl_block.instruction_source_lines)
        else None
    )

    src_mismatch = SourceMismatch(
        ref_file=ref_display_file or ref_file,
        ref_line=ref_line,
        ref_snippet=get_source_snippet(ref_file, ref_line),
        impl_file=impl_display_file or impl_file,
        impl_line=impl_line,
        impl_snippet=get_source_snippet(impl_file, impl_line),
    )

    return False, diff, src_mismatch


def compare_modules(
    ref_slice: CallGraphSlice,
    impl_slice: CallGraphSlice,
    mapping: FunctionMapping,
    ref_display_name: str = "",
    impl_display_name: str = "",
) -> ComparisonResult:
    """
    Compare reference and implementation call-graph slices according to mapping.
    """
    top_ref = ref_slice.top_function
    top_impl = impl_slice.top_function

    ref_file = top_ref.source_file if top_ref else None
    impl_file = top_impl.source_file if top_impl else None

    # Build canonical callee maps
    ref_callee_map: dict[str, str] = {}
    impl_callee_map: dict[str, str] = {}

    for idx, match in enumerate(mapping.matches):
        canon_name = f"@__fn_pair_{idx}"
        ref_callee_map[match.ref_fn.mangled_name] = canon_name
        impl_callee_map[match.impl_fn.mangled_name] = canon_name

    function_diffs: list[FunctionDiff] = []
    all_matched_fns_equal = True

    for idx, match in enumerate(mapping.matches):
        canon_name = f"@__fn_pair_{idx}"
        norm_ref = normalize_function(match.ref_fn, canon_name, ref_callee_map)
        norm_impl = normalize_function(match.impl_fn, canon_name, impl_callee_map)

        # Check identical normalized text
        if norm_ref.normalized_text == norm_impl.normalized_text:
            function_diffs.append(
                FunctionDiff(
                    ref_name=match.ref_fn.demangled_name,
                    impl_name=match.impl_fn.demangled_name,
                    tier=match.tier,
                    is_match=True
                )
            )
            continue

        # Check block by block
        is_fn_match = True
        mismatches: list[str] = []
        source_mismatches: list[SourceMismatch] = []

        cur_ref_file = match.ref_fn.source_file or ref_file
        cur_impl_file = match.impl_fn.source_file or impl_file

        if len(norm_ref.blocks) != len(norm_impl.blocks):
            is_fn_match = False
            mismatches.append(
                f"Block count mismatch: {len(norm_ref.blocks)} blocks in ref vs {len(norm_impl.blocks)} in impl"
            )
        else:
            for b_idx, (b_ref, b_impl) in enumerate(zip(norm_ref.blocks, norm_impl.blocks)):
                b_match, b_msg, src_mismatch = compare_blocks(
                    b_ref,
                    b_impl,
                    ref_file=cur_ref_file,
                    impl_file=cur_impl_file,
                    ref_display_file=ref_display_name or cur_ref_file,
                    impl_display_file=impl_display_name or cur_impl_file,
                )
                if not b_match:
                    is_fn_match = False
                    mismatches.append(f"Block {b_ref.canonical_label}: {b_msg}")
                    if src_mismatch and (src_mismatch.ref_snippet or src_mismatch.impl_snippet):
                        source_mismatches.append(src_mismatch)

        diff_text = "\n".join(difflib.unified_diff(
            norm_ref.normalized_text.splitlines(),
            norm_impl.normalized_text.splitlines(),
            fromfile=f"ref: {match.ref_fn.demangled_name}",
            tofile=f"impl: {match.impl_fn.demangled_name}",
            lineterm=""
        ))

        if not is_fn_match:
            all_matched_fns_equal = False

        function_diffs.append(
            FunctionDiff(
                ref_name=match.ref_fn.demangled_name,
                impl_name=match.impl_fn.demangled_name,
                tier=match.tier,
                is_match=is_fn_match,
                diff_text=diff_text,
                block_mismatches=mismatches,
                source_mismatches=source_mismatches
            )
        )

    # Analyze unmatched functions
    # An unmatched function only affects output if it is called within a matched function
    unmatched_affecting: list[str] = []
    unmatched_redundant: list[str] = []
    warnings: list[str] = []

    # Active unmatched functions in ref or impl
    for fn in mapping.unmatched_ref:
        called = any(fn.mangled_name in m.ref_fn.callees for m in mapping.matches)
        if called:
            unmatched_affecting.append(f"Reference: {fn.demangled_name}")
        else:
            unmatched_redundant.append(f"Reference: {fn.demangled_name}")
            warnings.append(f"Unmatched function in reference: '{fn.demangled_name}' (did not affect matched functions)")

    for fn in mapping.unmatched_impl:
        called = any(fn.mangled_name in m.impl_fn.callees for m in mapping.matches)
        if called:
            unmatched_affecting.append(f"Implementation: {fn.demangled_name}")
        else:
            unmatched_redundant.append(f"Implementation: {fn.demangled_name}")
            warnings.append(f"Unmatched function in implementation: '{fn.demangled_name}' (did not affect matched functions)")

    # Also list unreachable functions from modules as warnings if any
    for fn in ref_slice.unreachable_functions.values():
        unmatched_redundant.append(f"Reference (unreachable): {fn.demangled_name}")
        warnings.append(f"Unreachable function in reference: '{fn.demangled_name}'")
    for fn in impl_slice.unreachable_functions.values():
        unmatched_redundant.append(f"Implementation (unreachable): {fn.demangled_name}")
        warnings.append(f"Unreachable function in implementation: '{fn.demangled_name}'")

    # Equivalence determination:
    # Must have at least one matched function, all matched functions must be identical,
    # and no calls to affecting unmatched functions.
    is_equivalent = all_matched_fns_equal and len(unmatched_affecting) == 0 and len(mapping.matches) > 0

    return ComparisonResult(
        is_equivalent=is_equivalent,
        verdict="EQUIVALENT" if is_equivalent else "DIFFERENT",
        top_function_ref=top_ref.demangled_name if top_ref else "(all functions)",
        top_function_impl=top_impl.demangled_name if top_impl else "(all functions)",
        ref_display_name=ref_display_name or (ref_file or ""),
        impl_display_name=impl_display_name or (impl_file or ""),
        function_diffs=function_diffs,
        unmatched_affecting=unmatched_affecting,
        unmatched_redundant=unmatched_redundant,
        warnings=warnings
    )
