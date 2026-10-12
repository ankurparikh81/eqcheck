# eqcheck: C++ Semantic Equivalence Checker via LLVM IR

`eqcheck` determines whether two C++ source files (`reference.cpp` and `implementation.cpp`) are semantically equivalent. Rather than performing a superficial text diff, `eqcheck` compiles the source files using Clang into LLVM Intermediate Representation (IR), optimizes them to canonical SSA form with inlining enabled, aligns and compares every matching function structurally, and traces any discrepancies directly to the C++ source lines that caused them.

## Features

- **Noise Invariance**: Ignores formatting, indentation, comments, typedefs, and non-functional syntax variations.
- **SSA Canonicalization**: Abstract away temporary variables, parameter names, and stack allocations.
- **Whole Translation Unit Auditing (Default)**: Automatically checks and proves equivalence for every matching function in the `.cpp` files without requiring an entrypoint.
- **Inlining-Enabled Refactoring (Default)**: Inlining is enabled by default so extracting, inlining, or restructuring internal helper functions within the file does not cause false mismatches.
- **Unmatched Functions as Warnings**: Functions that are added, unused, or unmatched (and do not affect the execution of any matched function) are reported as warnings rather than failing equivalence.
- **Optional `--top` Scoping**: Specify `--top <func>` to narrow comparison to a specific function and its reachable call graph.
- **4-Tier Function Matching**:
  1. **Tier 1 (Explicit User Match File)**: Match pairs defined in `--match-file matches.json` (highest priority).
  2. **Tier 2 (Name & Identical Signature)**: Automatic pairing of functions with matching names and type signatures.
  3. **Tier 3 (Heuristic Call-Site & Signature)**: Automatic alignment of renamed helpers based on call topology and unique type signatures.
  4. **Tier 4 (Unmatched Functions)**: Unmatched functions are reported as warnings if they don't break matched functions.
- **Build System Integration (`compile_commands.json`)**: Extract include directories, macro definitions, and language standards automatically via `-p / --compile-commands <path_or_dir>`.
- **Native Git Revision Auditing**: Compare working tree files against past revisions (`-r HEAD~1`), compare ranges (`-r main..feature`), or specify Git object targets (`HEAD~1:src/foo.cpp`).
- **Automatic Sibling Header Resolution**: Extracted Git files automatically retain local header lookup (`-iquote`) for sibling headers.
- **Flexible Header & Flag Configuration**: Supply `-I`, `-isystem`, `--cxxflags`, or trailing `-- <flags>` directly on the command line.
- **Source-Level Diagnostics**: On mismatch, `eqcheck` maps the diverging LLVM instructions back to the exact C++ source code lines and displays them with a `>` marker.
- **DAG Instruction Scheduling**: Normalizes independent operations within basic blocks to prevent false mismatches caused by syntactic evaluation order.
- **Rich Terminal & JSON Output**: Colored side-by-side diagnostic diffs and `--json` format for CI/CD pipelines.

## Requirements

- Python 3.9+
- Clang / Clang++ (with LLVM `opt` and `c++filt`)

## Installation / Setup

From repository root:
```bash
# Direct execution
./bin/eqcheck --help

# Or install in editable mode
pip install -e .
```

## Usage

```bash
# Basic check: audits entire translation unit
eqcheck ref.cpp impl.cpp

# Compare working tree file against a previous Git commit
eqcheck src/matrix.cpp -r HEAD~1

# Compare across Git branches or revisions
eqcheck src/matrix.cpp -r main..feature
eqcheck HEAD~2:src/matrix.cpp HEAD~1:src/matrix.cpp

# Include directories & compiler definitions
eqcheck ref.cpp impl.cpp -I include/ -isystem /opt/lib/include --cxxflags "-DFEATURE=1 -std=c++20"

# Pass trailing compiler flags directly
eqcheck ref.cpp impl.cpp -- -DFEATURE=1 -std=c++20

# Automatically extract include paths & macros from compile_commands.json
eqcheck src/foo.cpp -r HEAD~1 -p build/

# Focus on a specific entrypoint function
eqcheck ref.cpp impl.cpp --top compute_total

# With explicit function matches for renamed routines
eqcheck ref.cpp impl.cpp --match-file matches.json

# Strict function boundary check (disable compiler inlining)
eqcheck ref.cpp impl.cpp --no-inline

# Verbose output with detailed LLVM IR instruction diffs
eqcheck ref.cpp impl.cpp --verbose

# Machine-readable JSON output
eqcheck ref.cpp impl.cpp --json
```

### Match File Format (`matches.json`)

```json
{
  "reference_helper_name": "implementation_helper_name",
  "compute_step_v1": "fast_step_impl"
}
```

## Running Tests

```bash
python3 -m unittest discover -s tests -v
```
