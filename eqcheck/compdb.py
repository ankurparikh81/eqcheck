"""
Compilation Database (compile_commands.json) parser and compiler flag extractor.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
from typing import Optional


class CompilationDatabaseError(Exception):
    """Raised when compilation database cannot be loaded or parsed."""
    pass


class CompilationDatabase:
    """Manages loading and querying compile_commands.json."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path).resolve()
        if not self.db_path.exists():
            raise FileNotFoundError(f"Compilation database not found: {self.db_path}")

        try:
            with open(self.db_path, "r", encoding="utf-8") as f:
                self.entries: list[dict] = json.load(f)
        except json.JSONDecodeError as e:
            raise CompilationDatabaseError(f"Invalid JSON in {self.db_path}: {e}")
        except Exception as e:
            raise CompilationDatabaseError(f"Failed to read {self.db_path}: {e}")

        # Index entries by canonical absolute file path
        self._file_to_entry: dict[str, dict] = {}
        for entry in self.entries:
            file_str = entry.get("file")
            if not file_str:
                continue
            entry_dir = Path(entry.get("directory", self.db_path.parent)).resolve()
            entry_file = Path(file_str)
            if not entry_file.is_absolute():
                entry_file = (entry_dir / entry_file).resolve()
            else:
                entry_file = entry_file.resolve()
            self._file_to_entry[str(entry_file)] = entry

    @classmethod
    def discover(cls, search_path: Path | str | None = None) -> Optional[CompilationDatabase]:
        """
        Discover and load compile_commands.json.

        If search_path is given:
          - If it's a file, load it.
          - If it's a directory, look for search_path / "compile_commands.json".
          - If not found, raise FileNotFoundError.
        If search_path is None:
          - Check ./compile_commands.json, ./build/compile_commands.json, ../build/compile_commands.json.
          - Return None if not found.
        """
        if search_path is not None:
            p = Path(search_path).resolve()
            if p.is_file():
                return cls(p)
            elif p.is_dir():
                target = p / "compile_commands.json"
                if target.is_file():
                    return cls(target)
                raise FileNotFoundError(f"No compile_commands.json found in directory: {p}")
            else:
                raise FileNotFoundError(f"Specified compilation database path does not exist: {p}")

        # Auto-discovery
        cwd = Path.cwd().resolve()
        candidates = [
            cwd / "compile_commands.json",
            cwd / "build" / "compile_commands.json",
            cwd.parent / "build" / "compile_commands.json",
        ]
        for cand in candidates:
            if cand.is_file():
                return cls(cand)
        return None

    def get_entry(self, source_file: Path | str) -> Optional[dict]:
        """Look up compilation database entry for a given source file."""
        abs_target = str(Path(source_file).resolve())
        return self._file_to_entry.get(abs_target)

    def extract_flags_for_file(self, source_file: Path | str) -> list[str]:
        """
        Extract and canonicalize relevant compiler flags for a given source file.
        Returns a list of flags (e.g. ['-I/abs/path', '-DFOO=1', '-std=c++17']).
        """
        entry = self.get_entry(source_file)
        if not entry:
            return []

        entry_dir = Path(entry.get("directory", self.db_path.parent)).resolve()

        if "arguments" in entry and isinstance(entry["arguments"], list):
            args = list(entry["arguments"])
        elif "command" in entry and isinstance(entry["command"], str):
            args = shlex.split(entry["command"])
        else:
            return []

        if not args:
            return []

        # Skip compiler binary
        tokens = args[1:]
        result_flags: list[str] = []

        skip_next = False
        source_target_abs = str(Path(source_file).resolve())

        i = 0
        while i < len(tokens):
            tok = tokens[i]
            i += 1

            # Check if this token is an output flag (-o out)
            if tok == "-o":
                i += 1  # skip destination
                continue
            if tok.startswith("-o") and tok != "-o":
                continue

            # Skip compilation actions
            if tok in ("-c", "-S", "-E"):
                continue

            # Skip debug info and optimization level (handled canonically by eqcheck)
            if tok.startswith("-g"):
                continue
            if tok.startswith("-O") and len(tok) >= 2:
                continue

            # Skip warnings (especially -Werror so external warnings don't fail IR generation)
            if tok.startswith("-W") or tok == "-w":
                continue

            # Check if token is the source file itself
            try:
                tok_path = Path(tok)
                if not tok_path.is_absolute():
                    tok_abs = str((entry_dir / tok_path).resolve())
                else:
                    tok_abs = str(tok_path.resolve())
                if tok_abs == source_target_abs or tok_path.suffix.lower() in (".cpp", ".c", ".cc", ".cxx", ".cp", ".c++"):
                    continue
            except Exception:
                pass

            # Handle include directories
            # -I <dir> or -I<dir>
            if tok == "-I":
                if i < len(tokens):
                    inc_path = Path(tokens[i])
                    i += 1
                    if not inc_path.is_absolute():
                        inc_path = (entry_dir / inc_path).resolve()
                    result_flags.extend(["-I", str(inc_path)])
                continue
            elif tok.startswith("-I") and len(tok) > 2:
                inc_part = tok[2:]
                inc_path = Path(inc_part)
                if not inc_path.is_absolute():
                    inc_path = (entry_dir / inc_path).resolve()
                result_flags.extend(["-I", str(inc_path)])
                continue

            # -isystem <dir> or -isystem<dir>
            if tok == "-isystem":
                if i < len(tokens):
                    inc_path = Path(tokens[i])
                    i += 1
                    if not inc_path.is_absolute():
                        inc_path = (entry_dir / inc_path).resolve()
                    result_flags.extend(["-isystem", str(inc_path)])
                continue
            elif tok.startswith("-isystem") and len(tok) > 8:
                inc_part = tok[8:]
                inc_path = Path(inc_part)
                if not inc_path.is_absolute():
                    inc_path = (entry_dir / inc_path).resolve()
                result_flags.extend(["-isystem", str(inc_path)])
                continue

            # -iquote <dir> or -iquote<dir>
            if tok == "-iquote":
                if i < len(tokens):
                    inc_path = Path(tokens[i])
                    i += 1
                    if not inc_path.is_absolute():
                        inc_path = (entry_dir / inc_path).resolve()
                    result_flags.extend(["-iquote", str(inc_path)])
                continue
            elif tok.startswith("-iquote") and len(tok) > 7:
                inc_part = tok[7:]
                inc_path = Path(inc_part)
                if not inc_path.is_absolute():
                    inc_path = (entry_dir / inc_path).resolve()
                result_flags.extend(["-iquote", str(inc_path)])
                continue

            # -idirafter <dir>
            if tok == "-idirafter":
                if i < len(tokens):
                    inc_path = Path(tokens[i])
                    i += 1
                    if not inc_path.is_absolute():
                        inc_path = (entry_dir / inc_path).resolve()
                    result_flags.extend(["-idirafter", str(inc_path)])
                continue
            elif tok.startswith("-idirafter") and len(tok) > 10:
                inc_part = tok[10:]
                inc_path = Path(inc_part)
                if not inc_path.is_absolute():
                    inc_path = (entry_dir / inc_path).resolve()
                result_flags.extend(["-idirafter", str(inc_path)])
                continue

            # Defines and Undefines
            if tok in ("-D", "-U"):
                if i < len(tokens):
                    result_flags.extend([tok, tokens[i]])
                    i += 1
                continue
            if tok.startswith("-D") or tok.startswith("-U"):
                result_flags.append(tok)
                continue

            # Standard and Language dialects
            if tok.startswith("-std="):
                result_flags.append(tok)
                continue

            # Architecture, Target, Sysroot, and Dialect options
            if (
                tok.startswith("-m")
                or tok.startswith("-f")
                or tok.startswith("--target=")
                or tok.startswith("--sysroot=")
                or tok.startswith("-stdlib=")
                or tok in (
                    "-nostdinc++",
                    "-nostdinc",
                    "-pthread",
                    "-fopenmp",
                )
            ):
                result_flags.append(tok)
                continue

            if tok in ("-target", "--target", "-sysroot", "--sysroot", "-isysroot"):
                if i < len(tokens):
                    result_flags.extend([tok, tokens[i]])
                    i += 1
                continue

            # For unknown flags that don't look like files, pass them through
            if tok.startswith("-"):
                result_flags.append(tok)

        return result_flags
