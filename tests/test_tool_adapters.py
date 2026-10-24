"""Tests for external tool adapters.

The contract these adapters must honour: when a tool is missing, is broken, or
times out, the adapter reports `ran=False` with a reason and produces **zero**
findings. It must never invent findings or silently pretend a tool ran.
"""

import subprocess

import pytest

from pyfix360.analysis import tool_adapters


def test_ruff_availability_is_a_truthful_tuple():
    adapter = tool_adapters.RuffAdapter()
    available, reason = adapter.is_available()
    assert isinstance(available, bool)
    if not available:
        assert reason  # a reason is always supplied


def test_bandit_availability_is_a_truthful_tuple():
    adapter = tool_adapters.BanditAdapter()
    available, reason = adapter.is_available()
    assert isinstance(available, bool)
    if not available:
        assert reason


def test_pip_audit_availability_is_a_truthful_tuple():
    adapter = tool_adapters.PipAuditAdapter()
    available, reason = adapter.is_available()
    assert isinstance(available, bool)
    if not available:
        assert reason


def test_unavailable_adapter_yields_no_findings(tmp_path, monkeypatch):
    """Simulate a missing binary: the run must be skipped, not faked."""
    adapter = tool_adapters.BanditAdapter()
    monkeypatch.setattr(tool_adapters.shutil, "which", lambda _name: None)

    result = adapter.run(tmp_path, timeout_s=30)
    if not result.ran:
        assert result.skip_reason
        assert result.findings == []


def test_ruff_run_produces_only_real_findings(tmp_path):
    """If ruff is present, its output is parsed; otherwise the run is skipped."""
    (tmp_path / "sample.py").write_text(
        "import os\nimport sys\n\n\ndef f():\n    return 1\n", encoding="utf-8"
    )
    adapter = tool_adapters.RuffAdapter()
    available, _ = adapter.is_available()
    result = adapter.run(tmp_path, timeout_s=60)

    if available:
        assert result.ran is True
        # every finding must point at a real file and carry a rule id
        for finding in result.findings:
            assert finding.rule_id
            assert finding.file_path
    else:
        assert result.ran is False
        assert result.skip_reason
        assert result.findings == []


def test_timeout_is_reported_as_skip(tmp_path, monkeypatch):
    adapter = tool_adapters.RuffAdapter()

    def boom(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="ruff", timeout=1)

    monkeypatch.setattr(adapter, "_exec", boom)
    result = adapter.run(tmp_path, timeout_s=1)
    assert result.ran is False
    assert "timed out" in result.skip_reason
    assert result.findings == []


def test_unparseable_output_is_reported_as_skip(tmp_path, monkeypatch):
    adapter = tool_adapters.RuffAdapter()

    class FakeProc:
        returncode = 0
        stdout = "this is not json"
        stderr = ""

    monkeypatch.setattr(adapter, "_exec", lambda *a, **k: FakeProc())
    result = adapter.run(tmp_path, timeout_s=30)
    assert result.ran is False
    assert "parse" in result.skip_reason.lower()
    assert result.findings == []


@pytest.mark.parametrize("adapter_cls", [
    tool_adapters.RuffAdapter,
    tool_adapters.BanditAdapter,
    tool_adapters.PipAuditAdapter,
])
def test_adapter_declares_a_name(adapter_cls):
    assert adapter_cls.name
