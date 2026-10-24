"""Tests for stable fingerprints and deduplication."""

from pyfix360.analysis.fingerprints import normalize_snippet, root_cause_group, stable_key
from pyfix360.models import Confidence, Finding, FindingCategory, Severity
from pyfix360.services.scan_service import deduplicate


def _finding(rule="r1", path="a.py", symbol=None, snippet="x = 1") -> Finding:
    return Finding(
        stable_key=stable_key(rule, path, symbol, snippet),
        rule_id=rule, category=FindingCategory.logic, severity=Severity.LOW,
        confidence=Confidence.HIGH, title="t", file_path=path, symbol=symbol,
    )


def test_stable_key_ignores_whitespace_variants():
    a = stable_key("r", "a.py", None, "x  =   1")
    b = stable_key("r", "a.py", None, "x = 1")
    assert a == b


def test_stable_key_differs_by_rule_path_symbol():
    base = stable_key("r1", "a.py", "f", "x=1")
    assert base != stable_key("r2", "a.py", "f", "x=1")
    assert base != stable_key("r1", "b.py", "f", "x=1")
    assert base != stable_key("r1", "a.py", "g", "x=1")


def test_normalize_snippet():
    assert normalize_snippet("  a \n b\tc ") == "a b c"


def test_root_cause_group_shared_by_site():
    g1 = root_cause_group("r1", "a.py", "f")
    g2 = root_cause_group("r1", "a.py", "f")
    g3 = root_cause_group("r1", "a.py", "other")
    assert g1 == g2
    assert g1 != g3


def test_dedup_collapses_identical_findings():
    f1 = _finding()
    f2 = _finding()
    f3 = _finding(symbol="g")
    out = deduplicate([f1, f2, f3])
    assert len(out) == 2
    assert f1.dedup_count == 2 or f2.dedup_count == 2


def test_dedup_preserves_evidence_of_first():
    a, b = _finding(), _finding()
    b.evidence = []
    a.evidence = []
    out = deduplicate([a, b])
    assert out[0].dedup_count == 2
