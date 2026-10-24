"""CLI end-to-end tests (the headless acceptance path)."""

import json

import pytest

from pyfix360.cli import main


@pytest.fixture()
def cli(data_dir, sample_project, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: "y")
    return sample_project


def test_cli_full_workflow(cli, capsys, tmp_path):
    root = str(cli)
    assert main(["import", root]) == 0
    out = capsys.readouterr().out
    assert "Imported project #1" in out

    assert main(["scan", "1"]) == 0
    out = capsys.readouterr().out
    assert "Scan #1 finished" in out

    assert main(["findings", "1"]) == 0
    out = capsys.readouterr().out
    assert "py-bare-except" in out

    # find the finding id from the DB
    from pyfix360.db import session_scope
    from pyfix360.db.models import FindingRow

    with session_scope() as s:
        f = s.query(FindingRow).filter_by(rule_id="py-bare-except").one()
        fid = f.id

    assert main(["show", str(fid)]) == 0
    out = capsys.readouterr().out
    assert "evidence" in out.lower()

    assert main(["plan", str(fid)]) == 0
    out = capsys.readouterr().out
    assert "Repair plan #1" in out

    assert main(["repair", "1", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "verification=passed" in out
    assert "except Exception:" in out

    report_path = tmp_path / "report.md"
    assert main(["report", "1", "--format", "markdown", "--out", str(report_path)]) == 0
    text = report_path.read_text(encoding="utf-8")
    assert "Audit Report" in text

    assert main(["history", "1"]) == 0
    out = capsys.readouterr().out
    assert "repair.execute" in out

    assert main(["patches", "1"]) == 0
    out = capsys.readouterr().out
    assert "verified" in out


def test_cli_projects_listing(cli, capsys):
    main(["import", str(cli)])
    capsys.readouterr()
    assert main(["projects"]) == 0
    out = capsys.readouterr().out
    assert "sample_proj" in out


def test_cli_settings_show_and_set(cli, capsys):
    capsys.readouterr()
    assert main(["settings", "show"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["isolate_execution"] is True

    assert main(["settings", "set", "execution_timeout_s", "123"]) == 0
    capsys.readouterr()
    assert main(["settings", "show"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["execution_timeout_s"] == 123


def test_cli_settings_unknown_key(cli, capsys, capsys_err=None):
    capsys.readouterr()
    assert main(["settings", "set", "bogus_key", "1"]) == 1


def test_cli_features(cli, capsys):
    main(["import", str(cli)])
    main(["scan", "1"])
    capsys.readouterr()
    assert main(["features", "1"]) == 0
    out = capsys.readouterr().out
    assert "feature" in out.lower()


def test_cli_providers_lifecycle(cli, capsys, no_keyring):
    capsys.readouterr()
    assert main(["providers", "add", "ollama", "--type", "local_runtime",
                 "--url", "http://localhost:11434/v1", "--model", "qwen"]) == 0
    assert main(["providers", "list"]) == 0
    out = capsys.readouterr().out
    assert "ollama" in out
    assert main(["providers", "remove", "1"]) == 0


def test_cli_error_handling(cli, capsys):
    capsys.readouterr()
    assert main(["show", "9999"]) == 1
    err = capsys.readouterr()
    assert "not found" in err.err
