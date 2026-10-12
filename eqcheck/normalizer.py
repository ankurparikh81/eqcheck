"""
IR Normalizer:
  - Alpha-renaming of function arguments, local registers, and basic block labels.
  - Commutative operand canonicalization.
  - Phi-node incoming branch sorting.
  - Stripping non-semantic metadata and attributes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from eqcheck.ir_parser import BasicBlock, Instruction, LLVMFunction
from eqcheck.matcher import FunctionMapping


@dataclass
class NormalizedBlock:
    canonical_label: str
    instructions: list[str] = field(default_factory=list)
    instruction_source_lines: list[Optional[int]] = field(default_factory=list)
    successors: list[str] = field(default_factory=list)


@dataclass
class NormalizedFunction:
    original_function: LLVMFunction
    canonical_name: str
    signature_str: str
    blocks: list[NormalizedBlock] = field(default_factory=list)
    normalized_text: str = ""


# Commutative integer/float binary ops
COMMUTATIVE_OPS = {"add", "fadd", "mul", "fmul", "and", "or", "xor"}
# Commutative icmp predicates
COMMUTATIVE_ICMP = {"eq", "ne"}


def clean_instruction_text(text: str) -> str:
    """Strip compiler metadata, attributes, and alignment decorations from instruction text."""
    # Strip comments
    c_idx = text.find(";")
    if c_idx != -1:
        text = text[:c_idx].strip()

    # Strip tail/musttail/notail
    text = re.sub(r'\b(tail|musttail|notail)\s+', '', text)

    # Strip metadata annotations like !range !0, !dbg !12, !tbaa !14, !noundef !12
    text = re.sub(r',?\s*![a-zA-Z0-9_$.]+\s+![0-9]+', '', text)
    text = re.sub(r',?\s*![0-9]+', '', text)

    # Strip attributes like noundef, nonnull, readonly, etc.
    text = re.sub(r'\b(noundef|nonnull|signext|zeroext|local_unnamed_addr|exact|nuw|nsw)\s+', '', text)

    # Strip align 4, align 8
    text = re.sub(r',?\s*align\s+\d+', '', text)

    # Clean multi-spaces
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def normalize_phi_node(line: str, block_renames: dict[str, str], value_renames: dict[str, str]) -> str:
    """Canonicalize a phi node by sorting its incoming [val, label] branches."""
    # Format: %v = phi type [ val1, label1 ], [ val2, label2 ]
    eq_idx = line.find("=")
    if eq_idx == -1:
        return line
    lhs = line[:eq_idx].strip()
    rhs = line[eq_idx + 1:].strip()

    tokens = rhs.split(None, 2)
    if len(tokens) < 3 or tokens[0] != "phi":
        return line

    phi_type = tokens[1]
    branches_str = tokens[2]

    # Find [ val, label ] pairs
    pairs = re.findall(r'\[\s*(.*?)\s*,\s*(%?[a-zA-Z0-9_$.]+)\s*\]', branches_str)
    if not pairs:
        return line

    norm_pairs = []
    for val, lbl in pairs:
        val = val.strip()
        lbl = lbl.strip().lstrip("%")
        canon_lbl = block_renames.get(lbl, block_renames.get(f"%{lbl}", lbl))
        canon_val = value_renames.get(val, val)
        norm_pairs.append((canon_lbl, canon_val))

    # Sort deterministically by canonical block label
    norm_pairs.sort(key=lambda p: p[0])

    pairs_str = ", ".join(f"[ {val}, %{lbl} ]" for lbl, val in norm_pairs)
    return f"{lhs} = phi {phi_type} {pairs_str}"


def canonicalize_commutative(line: str) -> str:
    """Sort operands for commutative instructions."""
    # Pattern 1: %v = opcode type op1, op2
    m = re.match(r'^(%\w+\s*=\s*)([a-z]+)\s+([a-zA-Z0-9_$.]+)\s+(%?[a-zA-Z0-9_$.]+),\s*(%?[a-zA-Z0-9_$.]+)$', line)
    if m:
        prefix = m.group(1)
        opcode = m.group(2)
        v_type = m.group(3)
        op1 = m.group(4)
        op2 = m.group(5)
        if opcode in COMMUTATIVE_OPS:
            args = sorted([op1, op2])
            return f"{prefix}{opcode} {v_type} {args[0]}, {args[1]}"

    # Pattern 2: %v = icmp eq/ne type op1, op2
    m_icmp = re.match(r'^(%\w+\s*=\s*icmp\s+)(eq|ne)\s+([a-zA-Z0-9_$.]+)\s+(%?[a-zA-Z0-9_$.]+),\s*(%?[a-zA-Z0-9_$.]+)$', line)
    if m_icmp:
        prefix = m_icmp.group(1)
        pred = m_icmp.group(2)
        v_type = m_icmp.group(3)
        op1 = m_icmp.group(4)
        op2 = m_icmp.group(5)
        args = sorted([op1, op2])
        return f"{prefix}{pred} {v_type} {args[0]}, {args[1]}"

    return line


def touches_memory(inst: Instruction) -> bool:
    """Check if instruction accesses memory or may have side effects."""
    if inst.opcode in ("load", "store", "fence", "atomicrmw", "cmpxchg"):
        return True
    if inst.is_call:
        # LLVM intrinsics like lifetime, smax etc. might be pure or readnone
        if inst.callee and (inst.callee.startswith("llvm.smax") or inst.callee.startswith("llvm.smin")
                            or inst.callee.startswith("llvm.abs")):
            return False
        return True
    return False


def schedule_block(instructions: list[Instruction], param_map: dict[str, str]) -> list[Instruction]:
    """
    Canonically schedule instructions in a basic block.
    Uses topological sorting on the data-dependency DAG with a deterministic
    tie-breaker to ensure independent operations are ordered consistently.
    """
    phis = [i for i in instructions if i.is_phi]
    terms = [i for i in instructions if i.is_terminator]
    middle = [i for i in instructions if not i.is_phi and not i.is_terminator]

    if len(middle) <= 1:
        return phis + middle + terms

    def_to_idx: dict[str, int] = {}
    for idx, inst in enumerate(middle):
        if inst.result_reg:
            def_to_idx[inst.result_reg] = idx

    def get_norm_key(inst: Instruction) -> tuple[str, str]:
        txt = clean_instruction_text(inst.raw)
        for p_from, p_to in param_map.items():
            txt = re.sub(r'\b' + re.escape(p_from) + r'\b', p_to, txt)
        txt = canonicalize_commutative(txt)
        if "=" in txt:
            txt = txt.split("=", 1)[1].strip()
        return (inst.opcode, txt)

    dep_count = [0] * len(middle)
    dependents: list[list[int]] = [[] for _ in range(len(middle))]
    last_memory_idx: Optional[int] = None

    for idx, inst in enumerate(middle):
        used_regs = set(re.findall(r'%[a-zA-Z0-9_$.]+', inst.operands_text))
        deps = {def_to_idx[r] for r in used_regs if r in def_to_idx and def_to_idx[r] != idx}

        # Preserve memory order
        if touches_memory(inst):
            if last_memory_idx is not None:
                deps.add(last_memory_idx)
            last_memory_idx = idx

        dep_count[idx] = len(deps)
        for d in deps:
            dependents[d].append(idx)

    ready = [idx for idx, c in enumerate(dep_count) if c == 0]
    scheduled: list[Instruction] = []

    while ready:
        ready.sort(key=lambda idx: get_norm_key(middle[idx]))
        curr = ready.pop(0)
        scheduled.append(middle[curr])
        for nxt in dependents[curr]:
            dep_count[nxt] -= 1
            if dep_count[nxt] == 0:
                ready.append(nxt)

    # In the unlikely event of remaining cycles, append them
    if len(scheduled) < len(middle):
        scheduled_set = set(id(i) for i in scheduled)
        for i in middle:
            if id(i) not in scheduled_set:
                scheduled.append(i)

    return phis + scheduled + terms


def normalize_function(
    fn: LLVMFunction,
    canonical_name: str,
    callee_name_map: dict[str, str],
) -> NormalizedFunction:
    """
    Normalize an LLVM function to canonical alpha-form.

    Args:
        fn: The original LLVMFunction.
        canonical_name: e.g. "@__fn_pair_0".
        callee_name_map: Map of original callee mangled symbols to canonical names.
    """
    # 1. Map parameters to %arg_0, %arg_1, ...
    value_renames: dict[str, str] = {}
    param_map: dict[str, str] = {}
    for idx, p_name in enumerate(fn.param_names):
        if p_name:
            value_renames[p_name] = f"%arg_{idx}"
            param_map[p_name] = f"%arg_{idx}"

    # 2. Canonicalize basic block labels in order
    block_renames: dict[str, str] = {}
    for idx, bb in enumerate(fn.blocks):
        clean_name = bb.name.lstrip("%")
        block_renames[clean_name] = f"bb_{idx}"
        block_renames[f"%{clean_name}"] = f"bb_{idx}"

    # 3. Schedule instructions in each block canonically
    scheduled_blocks: list[list[Instruction]] = []
    for bb in fn.blocks:
        scheduled_insts = schedule_block(bb.instructions, param_map)
        scheduled_blocks.append(scheduled_insts)

    # 4. Assign canonical register names %v_0, %v_1, ...
    reg_counter = 0
    for inst_list in scheduled_blocks:
        for instr in inst_list:
            if instr.result_reg and instr.result_reg not in value_renames:
                value_renames[instr.result_reg] = f"%v_{reg_counter}"
                reg_counter += 1

    # 5. Normalize instructions and substitute identifiers
    normalized_blocks: list[NormalizedBlock] = []

    for idx, (bb, inst_list) in enumerate(zip(fn.blocks, scheduled_blocks)):
        canon_label = f"bb_{idx}"
        norm_instructions: list[str] = []

        for instr in inst_list:
            cleaned = clean_instruction_text(instr.raw)

            # Substitute callee function names if call/invoke
            if instr.callee:
                canon_callee = callee_name_map.get(instr.callee, None)
                if canon_callee:
                    cleaned = re.sub(
                        r'@' + re.escape(instr.callee) + r'\b',
                        canon_callee,
                        cleaned
                    )

            # Handle phi node ordering
            if instr.is_phi:
                cleaned = normalize_phi_node(cleaned, block_renames, value_renames)

            # Substitute block labels: label %orig -> label %bb_X
            def replace_block_lbl(match: re.Match) -> str:
                lbl = match.group(1).lstrip("%")
                canon = block_renames.get(lbl, lbl)
                return f"label %{canon}"

            cleaned = re.sub(r'label\s+%([a-zA-Z0-9_$.]+)', replace_block_lbl, cleaned)

            # Substitute register / value names: %orig -> %v_X or %arg_X
            def replace_val(match: re.Match) -> str:
                v = match.group(0)
                return value_renames.get(v, v)

            # Match % followed by alphanumeric identifier
            cleaned = re.sub(r'%[a-zA-Z0-9_$.]+', replace_val, cleaned)

            # Canonicalize commutative operations
            cleaned = canonicalize_commutative(cleaned)

            norm_instructions.append(cleaned)

        # Normalize successors
        canon_succs = []
        for s in bb.successors:
            s_clean = s.lstrip("%")
            canon_succs.append(block_renames.get(s_clean, s_clean))

        normalized_blocks.append(
            NormalizedBlock(
                canonical_label=canon_label,
                instructions=norm_instructions,
                instruction_source_lines=[instr.source_line for instr in inst_list],
                successors=canon_succs
            )
        )

    # Format normalized function text
    text_lines = [
        f"define {fn.return_type} {canonical_name}({', '.join(fn.param_types)}) {{"
    ]
    for b in normalized_blocks:
        text_lines.append(f"{b.canonical_label}:")
        for inst in b.instructions:
            text_lines.append(f"  {inst}")
    text_lines.append("}")

    return NormalizedFunction(
        original_function=fn,
        canonical_name=canonical_name,
        signature_str=fn.signature_str,
        blocks=normalized_blocks,
        normalized_text="\n".join(text_lines)
    )
