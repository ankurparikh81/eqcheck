"""
Call-graph construction and reachability analysis rooted at the top function.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from eqcheck.ir_parser import LLVMFunction, LLVMModule


class FunctionNotFoundError(Exception):
    """Raised when the specified top function cannot be found in a module."""
    pass


@dataclass
class CallGraphSlice:
    top_function: Optional[LLVMFunction] = None
    reachable_functions: dict[str, LLVMFunction] = field(default_factory=dict)
    unreachable_functions: dict[str, LLVMFunction] = field(default_factory=dict)
    edges: dict[str, set[str]] = field(default_factory=dict)

    def is_reachable(self, mangled_name: str) -> bool:
        return mangled_name in self.reachable_functions


def slice_all_functions(module: LLVMModule) -> CallGraphSlice:
    """
    Construct a call-graph slice containing all user-defined functions in the module.
    Used when evaluating the entire translation unit without a designated top function.
    """
    reachable = dict(module.functions)
    edges: dict[str, set[str]] = {}
    for name, fn in reachable.items():
        edges[name] = set()
        for callee_symbol in fn.callees:
            callee_fn = module.find_function(callee_symbol)
            if callee_fn is not None and not callee_fn.is_declaration:
                edges[name].add(callee_fn.mangled_name)

    return CallGraphSlice(
        top_function=None,
        reachable_functions=reachable,
        unreachable_functions={},
        edges=edges
    )


def slice_callgraph(module: LLVMModule, top_name: str) -> CallGraphSlice:
    """
    Construct a call-graph slice rooted at top_name.

    Traverses all outgoing direct and indirect calls from the top function.
    Any function in the module that is not reachable from top is marked unreachable.
    """
    top_fn = module.find_function(top_name)
    if top_fn is None:
        available = [fn.base_name for fn in module.functions.values()]
        raise FunctionNotFoundError(
            f"Top function '{top_name}' was not found in module. "
            f"Available functions: {', '.join(available) if available else '(none)'}"
        )

    reachable: dict[str, LLVMFunction] = {top_fn.mangled_name: top_fn}
    edges: dict[str, set[str]] = {}

    queue = deque([top_fn])

    while queue:
        curr_fn = queue.popleft()
        curr_name = curr_fn.mangled_name
        edges.setdefault(curr_name, set())

        for callee_symbol in curr_fn.callees:
            # Check if callee is a user-defined function in this module
            callee_fn = module.find_function(callee_symbol)
            if callee_fn is not None and not callee_fn.is_declaration:
                edges[curr_name].add(callee_fn.mangled_name)
                if callee_fn.mangled_name not in reachable:
                    reachable[callee_fn.mangled_name] = callee_fn
                    queue.append(callee_fn)

    unreachable: dict[str, LLVMFunction] = {}
    for name, fn in module.functions.items():
        if name not in reachable:
            unreachable[name] = fn

    return CallGraphSlice(
        top_function=top_fn,
        reachable_functions=reachable,
        unreachable_functions=unreachable,
        edges=edges
    )
