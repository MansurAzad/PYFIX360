"""Tests for unified diff generation and application."""

import pytest

from pyfix360.patching.diff import DiffApplyError, apply_unified_diff, diff_stats, unified_diff

OLD = "line1\nline2\nline3\nline4\nline5\n"
NEW = "line1\nline2 changed\nline3\nline4\nline5\nnew line6\n"


def test_unified_diff_shape():
    d = unified_diff("f.py", OLD, NEW)
    assert d.startswith("--- a/f.py")
    assert "+++ b/f.py" in d
    assert "-line2\n" in d
    assert "+line2 changed\n" in d


def test_diff_stats():
    added, removed = diff_stats(unified_diff("f.py", OLD, NEW))
    assert (added, removed) == (2, 1)


def test_apply_roundtrip():
    d = unified_diff("f.py", OLD, NEW)
    assert apply_unified_diff(OLD, d) == NEW


def test_apply_multiple_hunks():
    old = "\n".join(f"l{i}" for i in range(1, 21)) + "\n"
    new = old.replace("l3", "L3").replace("l17", "L17")
    d = unified_diff("f.py", old, new, context=2)
    assert apply_unified_diff(old, d) == new


def test_apply_rejects_context_mismatch():
    d = unified_diff("f.py", OLD, NEW)
    tampered = OLD.replace("line3", "different")
    with pytest.raises(DiffApplyError):
        apply_unified_diff(tampered, d)


def test_apply_rejects_garbage_diff():
    with pytest.raises(DiffApplyError):
        apply_unified_diff(OLD, "not a diff at all")


def test_empty_diff_rejected():
    with pytest.raises(DiffApplyError):
        apply_unified_diff(OLD, "--- a/f.py\n+++ b/f.py\n")
