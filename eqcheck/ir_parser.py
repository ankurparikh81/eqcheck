"""
LLVM IR Parser for extracting functions, basic blocks, instructions, and call graphs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from eqcheck.compiler import demangle


@dataclass
class Instruction:
    raw: str
    result_reg: Optional[str] = None
    opcode: str = ""
    operands_text: str = ""
    callee: Optional[str] = None
    is_phi: bool = False
    is_terminator: bool = False
    source_line: Optional[int] = None
    source_col: Optional[int] = None

    @property
    def is_call(self) -> bool:
        return self.opcode in ("call", "invoke") or self.callee is not None


@dataclass
class BasicBlock:
    name: str
    instructions: list[Instruction] = field(default_factory=list)
    successors: list[str] = field(default_factory=list)


@dataclass
class LLVMFunction:
    mangled_name: str
    demangled_name: str
    base_name: str
    return_type: str
    param_types: list[str] = field(default_factory=list)
    param_names: list[str] = field(default_factory=list)
    is_declaration: bool = False
    blocks: list[BasicBlock] = field(default_factory=list)
    callees: set[str] = field(default_factory=set)
    source_file: Optional[str] = None

    @property
    def signature_str(self) -> str:
        params = ", ".join(self.param_types)
        return f"{self.return_type} ({params})"


@dataclass
class LLVMModule:
    functions: dict[str, LLVMFunction] = field(default_factory=dict)
    declarations: dict[str, LLVMFunction] = field(default_factory=dict)
    raw_ir: str = ""
    source_file_path: Optional[str] = None

    def find_function(self, name: str) -> Optional[LLVMFunction]:
        """
        Find a function by mangled name, demangled name, or base name.
        """
        # Exact mangled match
        if name in self.functions:
            return self.functions[name]
        symbol_with_at = name.lstrip("@")
        if symbol_with_at in self.functions:
            return self.functions[symbol_with_at]

        # Exact demangled match
        for fn in self.functions.values():
            if fn.demangled_name == name:
                return fn

        # Base name match
        matches = [fn for fn in self.functions.values() if fn.base_name == name]
        if len(matches) == 1:
            return matches[0]
        elif len(matches) > 1:
            # If multiple overloads exist, prefer one with matching demangled or first
            return matches[0]

        return None


def extract_base_name(demangled: str) -> str:
    """Extract base function name from demangled representation."""
    s = demangled.strip()
    idx = s.rfind("(")
    if idx != -1:
        s = s[:idx].strip()
    parts = s.split("::")
    base = parts[-1].strip()
    # Remove template args if present
    t_idx = base.find("<")
    if t_idx != -1:
        base = base[:t_idx].strip()
    return base


_CALL_PATTERN = re.compile(r'(?:call|invoke)\s+.*?\s+@([a-zA-Z0-9_$.]+)\s*\(')
_BR_COND_PATTERN = re.compile(r'br\s+i1\s+.*,\s+label\s+%([a-zA-Z0-9_$.]+),\s+label\s+%([a-zA-Z0-9_$.]+)')
_BR_UNCOND_PATTERN = re.compile(r'br\s+label\s+%([a-zA-Z0-9_$.]+)')
_SWITCH_TARGETS_PATTERN = re.compile(r'label\s+%([a-zA-Z0-9_$.]+)')


def parse_instruction(
    raw_line: str,
    loc_map: Optional[dict[str, tuple[int, int]]] = None
) -> Instruction:
    line = raw_line.strip()
    # Extract source location before stripping comments/metadata
    source_line = None
    source_col = None
    if loc_map:
        m_dbg = re.search(r'!dbg\s+!(\d+)', line)
        if m_dbg and m_dbg.group(1) in loc_map:
            source_line, source_col = loc_map[m_dbg.group(1)]

    # Strip comments from instruction line
    # Note: comments in LLVM IR start with ';'
    comment_idx = line.find(";")
    if comment_idx != -1:
        line = line[:comment_idx].strip()

    result_reg = None
    rest = line

    if line.startswith("%") and "=" in line:
        parts = line.split("=", 1)
        result_reg = parts[0].strip()
        rest = parts[1].strip()

    tokens = rest.split()
    opcode = tokens[0] if tokens else ""

    # Check for tail call / musttail call / notail call
    if opcode in ("tail", "musttail", "notail"):
        if len(tokens) > 1:
            opcode = tokens[1]

    # Find callee if call/invoke
    callee = None
    if opcode in ("call", "invoke") or "call " in line or "invoke " in line:
        m = _CALL_PATTERN.search(line)
        if m:
            callee = m.group(1)

    is_phi = (opcode == "phi")
    is_terminator = opcode in ("ret", "br", "switch", "indirectbr", "invoke", "resume", "unreachable")

    return Instruction(
        raw=raw_line.strip(),
        result_reg=result_reg,
        opcode=opcode,
        operands_text=rest,
        callee=callee,
        is_phi=is_phi,
        is_terminator=is_terminator,
        source_line=source_line,
        source_col=source_col
    )


def extract_successors(instr: Instruction) -> list[str]:
    raw = instr.raw
    if instr.opcode == "br":
        m_cond = _BR_COND_PATTERN.search(raw)
        if m_cond:
            return [m_cond.group(1), m_cond.group(2)]
        m_uncond = _BR_UNCOND_PATTERN.search(raw)
        if m_uncond:
            return [m_uncond.group(1)]
    elif instr.opcode == "switch":
        return _SWITCH_TARGETS_PATTERN.findall(raw)
    return []


def parse_llvm_ir(ir_text: str, source_file_path: Optional[str] = None) -> LLVMModule:
    """
    Parse LLVM IR text into a structured LLVMModule.
    """
    module = LLVMModule(raw_ir=ir_text, source_file_path=source_file_path)

    # Pre-parse DILocation metadata for mapping IR instructions to source lines
    diloc_pattern = re.compile(r'!(\d+)\s*=\s*!DILocation\(line:\s*(\d+)(?:,\s*column:\s*(\d+))?')
    loc_map: dict[str, tuple[int, int]] = {}
    for m in diloc_pattern.finditer(ir_text):
        dbg_id = m.group(1)
        line_no = int(m.group(2))
        col_no = int(m.group(3)) if m.group(3) else 0
        loc_map[dbg_id] = (line_no, col_no)

    lines = ir_text.splitlines()

    i = 0
    num_lines = len(lines)

    while i < num_lines:
        line = lines[i].strip()

        # Check for function definition
        if line.startswith("define ") and "{" in line:
            fn_header_lines = [line]
            while "{" not in fn_header_lines[-1] and i + 1 < num_lines:
                i += 1
                fn_header_lines.append(lines[i].strip())
            header_full = " ".join(fn_header_lines)

            # Match function name and arguments
            # e.g. define ... <ret_type> @<name>(<args>) ... {
            m = re.search(r'@([a-zA-Z0-9_$.]+)\s*\((.*?)\)', header_full)
            if not m:
                i += 1
                continue

            mangled_name = m.group(1)
            args_str = m.group(2)
            demangled_name = demangle(mangled_name)
            base_name = extract_base_name(demangled_name)

            # Extract return type (preceding @<name>)
            before_at = header_full[:m.start()].strip()
            tokens = before_at.split()
            ret_type = tokens[-1] if tokens else "void"

            # Parse parameter types and names
            param_types = []
            param_names = []
            if args_str.strip():
                raw_args = [a.strip() for a in args_str.split(",") if a.strip()]
                for arg in raw_args:
                    arg_tokens = arg.split()
                    p_type = arg_tokens[0]
                    p_name = ""
                    for tok in arg_tokens:
                        if tok.startswith("%"):
                            p_name = tok
                            break
                    param_types.append(p_type)
                    param_names.append(p_name)

            fn = LLVMFunction(
                mangled_name=mangled_name,
                demangled_name=demangled_name,
                base_name=base_name,
                return_type=ret_type,
                param_types=param_types,
                param_names=param_names,
                is_declaration=False,
                source_file=source_file_path
            )

            # Read function body blocks
            i += 1
            current_block: Optional[BasicBlock] = None

            while i < num_lines:
                curr_line = lines[i]
                stripped = curr_line.strip()

                if stripped == "}":
                    if current_block is not None:
                        fn.blocks.append(current_block)
                    break

                if not stripped or stripped.startswith(";"):
                    i += 1
                    continue

                label_match = re.match(r'^([a-zA-Z0-9_$.]+):', stripped)
                if label_match:
                    if current_block is not None:
                        fn.blocks.append(current_block)
                    block_name = label_match.group(1)
                    current_block = BasicBlock(name=block_name)
                    i += 1
                    continue

                if current_block is None:
                    current_block = BasicBlock(name="entry")

                instr = parse_instruction(stripped, loc_map=loc_map)
                if instr.callee:
                    fn.callees.add(instr.callee)
                succs = extract_successors(instr)
                if succs:
                    current_block.successors.extend(succs)
                current_block.instructions.append(instr)
                i += 1

            module.functions[mangled_name] = fn

        elif line.startswith("declare "):
            # Function declaration
            m = re.search(r'@([a-zA-Z0-9_$.]+)\s*\((.*?)\)', line)
            if m:
                mangled_name = m.group(1)
                demangled_name = demangle(mangled_name)
                base_name = extract_base_name(demangled_name)
                fn = LLVMFunction(
                    mangled_name=mangled_name,
                    demangled_name=demangled_name,
                    base_name=base_name,
                    return_type="void",
                    is_declaration=True
                )
                module.declarations[mangled_name] = fn
            i += 1
        else:
            i += 1

    return module
