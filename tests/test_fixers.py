"""Tests for builtin fixers."""

from pyfix360.analysis.python_analyzer import analyze_python_source
from pyfix360.models import Finding
from pyfix360.patching import fixers


def _finding_for(source: str, rule: str) -> Finding:
    findings, _ = analyze_python_source("t.py", source)
    for f in findings:
        if f.rule_id == rule:
            return f
    raise AssertionError(f"rule {rule} not found in {source!r}")


def test_bare_except_fix():
    src = "try:\n    x = 1\nexcept:\n    pass\n"
    f = _finding_for(src, "py-bare-except")
    out = fixers.propose_fix(src, f)
    assert out is not None
    assert "except Exception:" in out.new_source
    assert "except:\n" not in out.new_source
    compile(out.new_source, "<t>", "exec")


def test_yaml_load_fix():
    src = "import yaml\n\ndef f(d):\n    return yaml.load(d)\n"
    f = _finding_for(src, "sec-yaml-load")
    out = fixers.propose_fix(src, f)
    assert out is not None
    assert "yaml.safe_load(d)" in out.new_source


def test_yaml_load_skipped_when_loader_kwarg_present():
    src = "import yaml\n\ndef f(d):\n    return yaml.load(d, Loader=yaml.Loader)\n"
    f = _finding_for(src, "sec-yaml-load")
    assert fixers.propose_fix(src, f) is None


def test_weak_hash_fix():
    src = "import hashlib\n\ndef h(d):\n    return hashlib.md5(d).hexdigest()\n"
    f = _finding_for(src, "sec-weak-hash")
    out = fixers.propose_fix(src, f)
    assert out is not None
    assert "usedforsecurity=False" in out.new_source
    compile(out.new_source, "<t>", "exec")


def test_weak_hash_skipped_when_keywords_present():
    src = "import hashlib\n\ndef h(d):\n    return hashlib.md5(d, usedforsecurity=True).hexdigest()\n"
    f = _finding_for(src, "sec-weak-hash")
    assert fixers.propose_fix(src, f) is None


def test_no_fixer_returns_none():
    src = "def f(a=[]):\n    return a\n"
    f = _finding_for(src, "py-mutable-default")
    assert fixers.propose_fix(src, f) is None


def test_fixer_never_returns_broken_syntax():
    # fixer must abort when result would not compile
    src = "try:\n    pass\nexcept:\n    pass\n"
    f = _finding_for(src, "py-bare-except")
    out = fixers.propose_fix(src, f)
    compile(out.new_source, "<t>", "exec")


def test_can_auto_fix_flags():
    assert fixers.can_auto_fix("py-bare-except")
    assert not fixers.can_auto_fix("py-mutable-default")


def test_plan_steps_include_snapshot_and_tests():
    src = "try:\n    pass\nexcept:\n    pass\n"
    f = _finding_for(src, "py-bare-except")
    steps = fixers.build_plan_steps(f)
    actions = [s["action"] for s in steps]
    assert "snapshot" in actions
    assert "run_tests" in actions
    assert "compare_baseline" in actions


def test_plan_steps_manual_guidance_for_unfixable():
    src = "def f(a=[]):\n    return a\n"
    f = _finding_for(src, "py-mutable-default")
    steps = fixers.build_plan_steps(f)
    assert steps[0]["action"] == "manual_guidance"
    assert "None" in steps[0]["detail"]


def test_propose_fix_survives_garbled_finding():
    f = Finding(stable_key="x", rule_id="py-bare-except", category="logic",
                severity="medium", confidence="high", title="t",
                file_path="t.py", line_start=999)
    assert fixers.propose_fix("try:\n    pass\nexcept:\n    pass\n", f) is None
