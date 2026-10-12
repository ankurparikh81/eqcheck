"""
4-Tier Function Matching Engine:
  Tier 1: Explicit user match file (JSON)
  Tier 2: Automatic name and identical signature matching
  Tier 3: Heuristic topology and signature matching
  Tier 4: Unmatched functions
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from eqcheck.callgraph import CallGraphSlice
from eqcheck.ir_parser import LLVMFunction


@dataclass
class FunctionMatch:
    ref_fn: LLVMFunction
    impl_fn: LLVMFunction
    tier: str


@dataclass
class FunctionMapping:
    matches: list[FunctionMatch] = field(default_factory=list)
    ref_to_impl: dict[str, str] = field(default_factory=dict)
    impl_to_ref: dict[str, str] = field(default_factory=dict)
    tier_by_ref: dict[str, str] = field(default_factory=dict)
    unmatched_ref: list[LLVMFunction] = field(default_factory=list)
    unmatched_impl: list[LLVMFunction] = field(default_factory=list)

    def add_match(self, ref_fn: LLVMFunction, impl_fn: LLVMFunction, tier: str) -> None:
        self.matches.append(FunctionMatch(ref_fn=ref_fn, impl_fn=impl_fn, tier=tier))
        self.ref_to_impl[ref_fn.mangled_name] = impl_fn.mangled_name
        self.impl_to_ref[impl_fn.mangled_name] = ref_fn.mangled_name
        self.tier_by_ref[ref_fn.mangled_name] = tier

    def is_ref_matched(self, mangled_name: str) -> bool:
        return mangled_name in self.ref_to_impl

    def is_impl_matched(self, mangled_name: str) -> bool:
        return mangled_name in self.impl_to_ref


def parse_match_file(match_file: Path | str) -> dict[str, str]:
    """
    Parse a JSON match file.
    Supports:
      1. {"ref_func": "impl_func", ...}
      2. {"matches": {"ref_func": "impl_func", ...}}
      3. [{"ref": "ref_func", "impl": "impl_func"}, ...]
    """
    path = Path(match_file)
    if not path.exists():
        raise FileNotFoundError(f"Match file not found: {path}")

    text = path.read_text(encoding="utf-8")
    data = json.loads(text)

    result: dict[str, str] = {}
    if isinstance(data, dict):
        if "matches" in data and isinstance(data["matches"], dict):
            for k, v in data["matches"].items():
                result[str(k)] = str(v)
        elif "matches" in data and isinstance(data["matches"], list):
            for item in data["matches"]:
                if isinstance(item, dict) and "ref" in item and "impl" in item:
                    result[str(item["ref"])] = str(item["impl"])
        else:
            for k, v in data.items():
                result[str(k)] = str(v)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and "ref" in item and "impl" in item:
                result[str(item["ref"])] = str(item["impl"])

    return result


def find_function_in_slice(slice_obj: CallGraphSlice, name: str) -> Optional[LLVMFunction]:
    """Find a function in reachable functions (or fallback to all in module)."""
    # 1. Exact mangled
    if name in slice_obj.reachable_functions:
        return slice_obj.reachable_functions[name]
    name_clean = name.lstrip("@")
    if name_clean in slice_obj.reachable_functions:
        return slice_obj.reachable_functions[name_clean]

    # 2. Demangled
    for fn in slice_obj.reachable_functions.values():
        if fn.demangled_name == name:
            return fn

    # 3. Base name
    matches = [fn for fn in slice_obj.reachable_functions.values() if fn.base_name == name]
    if len(matches) == 1:
        return matches[0]

    return None


def signatures_compatible(fn1: LLVMFunction, fn2: LLVMFunction) -> bool:
    """Check if two functions have identical return type and parameter types."""
    if fn1.return_type != fn2.return_type:
        return False
    if len(fn1.param_types) != len(fn2.param_types):
        return False
    return all(t1 == t2 for t1, t2 in zip(fn1.param_types, fn2.param_types))


def match_functions(
    ref_slice: CallGraphSlice,
    impl_slice: CallGraphSlice,
    match_file: Optional[Path | str] = None,
) -> FunctionMapping:
    """
    Perform 4-tier function matching between reference and implementation slices.
    """
    mapping = FunctionMapping()

    # Tier 1: User-specified match file (highest precedence)
    user_matches: dict[str, str] = {}
    if match_file:
        user_matches = parse_match_file(match_file)
        for ref_spec, impl_spec in user_matches.items():
            ref_fn = find_function_in_slice(ref_slice, ref_spec)
            impl_fn = find_function_in_slice(impl_slice, impl_spec)
            if ref_fn and impl_fn:
                if not mapping.is_ref_matched(ref_fn.mangled_name) and not mapping.is_impl_matched(impl_fn.mangled_name):
                    mapping.add_match(ref_fn, impl_fn, "Tier 1 (User Match File)")

    # Ensure top function is matched if not already (when top is specified)
    top_ref = ref_slice.top_function
    top_impl = impl_slice.top_function
    if top_ref and top_impl:
        if not mapping.is_ref_matched(top_ref.mangled_name) and not mapping.is_impl_matched(top_impl.mangled_name):
            tier_label = "Tier 2 (Top Name & Signature)" if top_ref.base_name == top_impl.base_name else "Tier 2 (Top Function)"
            mapping.add_match(top_ref, top_impl, tier_label)

    # Tier 2: Automatic Name and Identical Signature Matching
    for ref_name, ref_fn in ref_slice.reachable_functions.items():
        if mapping.is_ref_matched(ref_name):
            continue

        candidates = [
            impl_fn for impl_fn in impl_slice.reachable_functions.values()
            if not mapping.is_impl_matched(impl_fn.mangled_name)
            and (impl_fn.base_name == ref_fn.base_name or impl_fn.demangled_name == ref_fn.demangled_name)
            and signatures_compatible(ref_fn, impl_fn)
        ]

        if len(candidates) == 1:
            mapping.add_match(ref_fn, candidates[0], "Tier 2 (Name & Signature)")

    # Tier 3: Heuristic Matcher for remaining active functions
    # Strategy 3A: Callee matching from already-matched callers
    for match in list(mapping.matches):
        p_ref = match.ref_fn
        p_impl = match.impl_fn

        # Look at callees in call order
        callees_ref = [c for c in p_ref.callees if c in ref_slice.reachable_functions and not mapping.is_ref_matched(c)]
        callees_impl = [c for c in p_impl.callees if c in impl_slice.reachable_functions and not mapping.is_impl_matched(c)]

        if len(callees_ref) == 1 and len(callees_impl) == 1:
            c_ref_fn = ref_slice.reachable_functions[callees_ref[0]]
            c_impl_fn = impl_slice.reachable_functions[callees_impl[0]]
            if signatures_compatible(c_ref_fn, c_impl_fn):
                mapping.add_match(c_ref_fn, c_impl_fn, "Tier 3 (Heuristic Call-Site)")

    # Strategy 3B: Unique signature matching among remaining active functions
    unmatched_ref_active = [
        fn for name, fn in ref_slice.reachable_functions.items()
        if not mapping.is_ref_matched(name)
    ]
    unmatched_impl_active = [
        fn for name, fn in impl_slice.reachable_functions.items()
        if not mapping.is_impl_matched(name)
    ]

    for ref_fn in list(unmatched_ref_active):
        if mapping.is_ref_matched(ref_fn.mangled_name):
            continue
        compatible_impl = [
            impl_fn for impl_fn in unmatched_impl_active
            if not mapping.is_impl_matched(impl_fn.mangled_name)
            and signatures_compatible(ref_fn, impl_fn)
        ]
        if len(compatible_impl) == 1:
            mapping.add_match(ref_fn, compatible_impl[0], "Tier 3 (Heuristic Unique Signature)")

    # Tier 4: Remaining Unmatched Functions
    for name, fn in ref_slice.reachable_functions.items():
        if not mapping.is_ref_matched(name):
            mapping.unmatched_ref.append(fn)

    for name, fn in impl_slice.reachable_functions.items():
        if not mapping.is_impl_matched(name):
            mapping.unmatched_impl.append(fn)

    return mapping
