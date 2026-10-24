"""Unified diff helpers."""

from __future__ import annotations

import difflib
from dataclasses import dataclass


def unified_diff(rel_path: str, old: str, new: str, *, context: int = 3) -> str:
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{rel_path}", tofile=f"b/{rel_path}", n=context,
    )
    return "".join(diff)


def diff_stats(diff: str) -> tuple[int, int]:
    added = sum(1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---"))
    return added, removed


@dataclass
class DiffApplyError(Exception):
    message: str

    def __str__(self) -> str:  # pragma: no cover
        return self.message


def apply_unified_diff(text: str, diff: str) -> str:
    """Apply a unified diff to text.

    Supports standard single/multi-file diffs where all hunks refer to the
    same original content (as produced by :func:`unified_diff`). Raises
    DiffApplyError when context does not match — the caller must treat that
    as a failed patch, never force-apply.
    """
    src_lines = text.splitlines(keepends=True)
    out: list[str] = []
    src_idx = 0  # 0-based index into src_lines
    hunks = _parse_hunks(diff)
    if not hunks:
        raise DiffApplyError("no hunks found in diff")
    for old_start, _old_count, _new_count, ops in hunks:
        # copy unchanged lines up to the hunk start
        target = max(old_start - 1, 0)
        if target < src_idx:
            raise DiffApplyError(f"hunk out of order at line {old_start}")
        out.extend(src_lines[src_idx:target])
        src_idx = target
        for op, line in ops:
            if op == " ":
                if src_idx >= len(src_lines) or \
                        src_lines[src_idx].rstrip("\r\n") != line.rstrip("\r\n"):
                    raise DiffApplyError(
                        f"context mismatch at line {src_idx + 1}: expected {line!r}"
                    )
                out.append(src_lines[src_idx])
                src_idx += 1
            elif op == "-":
                if src_idx >= len(src_lines) or \
                        src_lines[src_idx].rstrip("\r\n") != line.rstrip("\r\n"):
                    raise DiffApplyError(
                        f"removal mismatch at line {src_idx + 1}: expected {line!r}"
                    )
                src_idx += 1
            elif op == "+":
                out.append(line + "\n" if not line.endswith("\n") else line)
    out.extend(src_lines[src_idx:])
    return "".join(out)


def _parse_hunks(diff: str) -> list[tuple[int, int, int, list[tuple[str, str]]]]:
    hunks: list[tuple[int, int, int, list[tuple[str, str]]]] = []
    current: tuple[int, int, int, list[tuple[str, str]]] | None = None
    for raw in diff.splitlines():
        if raw.startswith("---") or raw.startswith("+++"):
            continue
        if raw.startswith("@@"):
            header = raw.split("@@")[1].strip()  # "-a,b +c,d"
            old_part, new_part = header.split(" ")
            old_start = int(old_part.lstrip("-").split(",")[0])
            old_count = int((old_part.split(",") + ["1"])[1]) if "," in old_part else \
                int(old_part.lstrip("-") or 1)
            new_start = int(new_part.lstrip("+").split(",")[0])
            new_count = int((new_part.split(",") + ["1"])[1]) if "," in new_part else 1
            if new_start < 1 or new_count < 0:
                raise DiffApplyError(f"malformed hunk header: {raw!r}")
            current = (old_start, old_count, new_count, [])
            hunks.append(current)
            continue
        if current is None:
            continue
        if raw.startswith("\\"):  # "\ No newline at end of file"
            continue
        if raw.startswith(("+", "-", " ")):
            current[3].append((raw[0], raw[1:]))
    return hunks
