"""Tests for the built-in Python AST analyzer."""

from pyfix360.analysis.python_analyzer import analyze_python_file, analyze_python_source


def _rules(source: str) -> dict[str, list]:
    findings, _ = analyze_python_source("t.py", source)
    out: dict[str, list] = {}
    for f in findings:
        out.setdefault(f.rule_id, []).append(f)
    return out


def test_syntax_error_is_critical():
    findings, symbols = analyze_python_source("t.py", "def f(:\n")
    assert len(findings) == 1
    f = findings[0]
    assert f.rule_id == "py-syntax-error"
    assert f.severity.value == "critical"
    assert f.line_start == 1
    assert symbols == []


def test_utf8_bom_is_not_a_syntax_error(tmp_path):
    """A BOM (common on Windows) must not be reported as a syntax error."""
    (tmp_path / "bom.py").write_bytes("\ufeffimport os\n\n\ndef f():\n    return 1\n".encode())
    findings, symbols = analyze_python_file(tmp_path, "bom.py")
    assert not any(f.rule_id == "py-syntax-error" for f in findings)
    assert any(s["name"] == "f" for s in symbols)


def test_bare_except_detected_with_symbol():
    src = "def cleanup():\n    try:\n        pass\n    except:\n        pass\n"
    rules = _rules(src)
    f = rules["py-bare-except"][0]
    assert f.symbol == "cleanup"
    assert f.evidence and f.evidence[0].snippet.strip().startswith("except:")


def test_swallowed_exception_detected():
    src = "def f():\n    try:\n        pass\n    except Exception:\n        pass\n"
    assert "py-swallowed-exception" in _rules(src)


def test_mutable_default_detected():
    src = "def f(items=[]):\n    return items\n"
    f = _rules(src)["py-mutable-default"][0]
    assert f.severity.value == "medium"


def test_subprocess_shell_flagged_only_with_shell_true():
    flagged = _rules("import subprocess\nsubprocess.run('ls', shell=True)\n")
    assert "sec-subprocess-shell" in flagged
    clean = _rules("import subprocess\nsubprocess.run(['ls'])\n")
    assert "sec-subprocess-shell" not in clean


def test_dynamic_exec_flagged():
    assert "sec-dynamic-exec" in _rules("eval(user_input)\n")
    assert "sec-dynamic-exec" in _rules("exec(code)\n")


def test_yaml_load_flagged():
    src = "import yaml\ndata = yaml.load(stream)\n"
    assert "sec-yaml-load" in _rules(src)


def test_weak_hash_flagged():
    src = "import hashlib\nhashlib.md5(b'x')\n"
    assert "sec-weak-hash" in _rules(src)


def test_hardcoded_secret_flagged():
    src = 'API_KEY = "sk-abcdef1234567890abcdef"\n'
    rules = _rules(src)
    assert any(k.startswith("hardcoded") for k in rules)


def test_secret_finding_never_contains_secret():
    src = 'API_KEY = "sk-abcdef1234567890abcdef"\n'
    findings, _ = analyze_python_source("t.py", src)
    secret_finding = next(f for f in findings if f.rule_id.startswith("hardcoded"))
    assert "abcdef" not in secret_finding.evidence[0].snippet


def test_todo_marker_recorded():
    src = "# TODO: implement batching\ndef f():\n    pass\n"
    rules = _rules(src)
    assert "cmp-todo-marker" in rules
    assert "implement batching" in rules["cmp-todo-marker"][0].description


def test_assert_auth_gate_flagged():
    src = "def h():\n    assert user.is_admin, 'permission denied'\n"
    assert "sec-assert-auth" in _rules(src)


def test_symbol_inventory():
    src = "class A:\n    def m(self):\n        pass\n\ndef top():\n    pass\n"
    _, symbols = analyze_python_source("t.py", src)
    kinds = {(s["kind"], s["name"]) for s in symbols}
    assert ("class", "A") in kinds
    assert ("function", "m") in kinds
    assert ("function", "top") in kinds
    assert ("module", "t.py") in kinds


def test_unreadable_file_reports_finding(tmp_path):
    findings, _ = analyze_python_file(tmp_path, "missing.py")
    assert findings[0].rule_id == "io-unreadable"


def test_stable_key_is_position_stable():
    src1 = "def f():\n    pass\n\ndef g():\n    try:\n        pass\n    except:\n        pass\n"
    src2 = "def f():\n    pass\n\ndef f2():\n    pass\n\ndef g():\n    try:\n        pass\n    except:\n        pass\n"
    a = _rules(src1)["py-bare-except"][0]
    b = _rules(src2)["py-bare-except"][0]
    assert a.stable_key == b.stable_key  # unaffected by unrelated inserted lines
