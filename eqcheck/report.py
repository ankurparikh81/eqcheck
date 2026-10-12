"""
Terminal formatting and JSON reporting for eqcheck results.
"""

from __future__ import annotations

import json
import sys
from typing import TextIO

from eqcheck.comparator import ComparisonResult


# ANSI color codes
RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
GRAY = "\033[90m"


def format_text_report(result: ComparisonResult, verbose: bool = False, use_color: bool = True) -> str:
    lines = []

    def c(code: str, text: str) -> str:
        return f"{code}{text}{RESET}" if use_color else text

    # Header / Verdict banner
    if result.is_equivalent:
        verdict_badge = c(BOLD + GREEN, "✔ EQUIVALENT")
    else:
        verdict_badge = c(BOLD + RED, "✖ DIFFERENT")

    lines.append("=" * 64)
    lines.append(f"  eqcheck result: {verdict_badge}")
    lines.append("=" * 64)
    if result.ref_display_name or result.impl_display_name:
        lines.append(f"  Reference:             {c(CYAN, result.ref_display_name)}")
        lines.append(f"  Implementation:        {c(CYAN, result.impl_display_name)}")
    if result.top_function_ref != "(all functions)":
        lines.append(f"  Top (Reference):      {c(CYAN, result.top_function_ref)}")
        lines.append(f"  Top (Implementation): {c(CYAN, result.top_function_impl)}")
    else:
        lines.append(f"  Scope:                 {c(CYAN, 'Entire Translation Unit (all functions)')}")
    lines.append("-" * 64)

    # Function comparison summary
    lines.append(c(BOLD, "Function Alignment & Comparison:"))
    for fd in result.function_diffs:
        status_str = c(GREEN, "[MATCH]") if fd.is_match else c(RED, "[MISMATCH]")
        tier_str = c(GRAY, f"({fd.tier})")
        lines.append(f"  {status_str} {fd.ref_name} <-> {fd.impl_name} {tier_str}")

    # Unmatched active functions causing non-equivalence
    if result.unmatched_affecting:
        lines.append("")
        lines.append(c(BOLD + RED, "Unmatched Active Functions (cause non-equivalence):"))
        for u in result.unmatched_affecting:
            lines.append(f"  {c(RED, '✖')} {u}")

    # Warnings for unmatched / unused functions
    if result.warnings:
        lines.append("")
        lines.append(c(BOLD + YELLOW, "Warnings (unmatched functions that did not affect matched functions):"))
        for w in result.warnings:
            lines.append(f"  {c(YELLOW, '⚠')} {w}")

    # Detailed differences
    if not result.is_equivalent:
        lines.append("")
        lines.append(c(BOLD + RED, "Detailed Mismatches:"))
        for fd in result.function_diffs:
            if not fd.is_match:
                lines.append(f"\n--- {c(BOLD, fd.ref_name)} vs {c(BOLD, fd.impl_name)} ---")

                # High-level C++ source code differences
                if fd.source_mismatches:
                    lines.append(c(BOLD + YELLOW, "  Source Code Location:"))
                    for sm in fd.source_mismatches:
                        ref_loc = f"{sm.ref_file}:{sm.ref_line}" if sm.ref_line else f"{sm.ref_file}:(line unknown)"
                        impl_loc = f"{sm.impl_file}:{sm.impl_line}" if sm.impl_line else f"{sm.impl_file}:(line unknown)"

                        lines.append(f"\n  {c(CYAN, 'Reference')} ({ref_loc}):")
                        for s_line in sm.ref_snippet.splitlines():
                            lines.append(f"    {s_line}")

                        lines.append(f"\n  {c(CYAN, 'Implementation')} ({impl_loc}):")
                        for s_line in sm.impl_snippet.splitlines():
                            lines.append(f"    {s_line}")

                if verbose:
                    lines.append(c(BOLD + GRAY, "\n  IR Instruction Diffs:"))
                    for m in fd.block_mismatches:
                        lines.append(f"    {c(YELLOW, '!')} {m}")
                    if fd.diff_text:
                        lines.append("\n" + fd.diff_text)
                else:
                    lines.append(c(GRAY, "\n  (Use --verbose to see raw LLVM IR instruction diffs)"))

    elif verbose:
        # If equivalent and verbose requested, show matched functions
        lines.append("")
        lines.append(c(BOLD + GRAY, "All reachable functions verified structurally identical."))

    lines.append("=" * 64)
    return "\n".join(lines)


def format_json_report(result: ComparisonResult) -> str:
    data = {
        "verdict": result.verdict,
        "is_equivalent": result.is_equivalent,
        "reference": result.ref_display_name,
        "implementation": result.impl_display_name,
        "top_function": {
            "ref": result.top_function_ref,
            "impl": result.top_function_impl,
        },
        "matches": [
            {
                "ref": fd.ref_name,
                "impl": fd.impl_name,
                "tier": fd.tier,
                "is_match": fd.is_match,
                "mismatches": fd.block_mismatches,
                "source_mismatches": [
                    {
                        "ref_file": sm.ref_file,
                        "ref_line": sm.ref_line,
                        "ref_snippet": sm.ref_snippet,
                        "impl_file": sm.impl_file,
                        "impl_line": sm.impl_line,
                        "impl_snippet": sm.impl_snippet,
                    }
                    for sm in fd.source_mismatches
                ],
            }
            for fd in result.function_diffs
        ],
        "unmatched_affecting": result.unmatched_affecting,
        "unmatched_redundant": result.unmatched_redundant,
        "warnings": result.warnings,
    }
    return json.dumps(data, indent=2)
