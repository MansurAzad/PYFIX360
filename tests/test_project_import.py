"""Tests for project import (folder and zip)."""

import zipfile

import pytest
from sqlalchemy import select

from pyfix360.db.models import DependencyRow, ProjectFileRow, ProjectRow
from pyfix360.models import ProjectOrigin
from pyfix360.services import audit, project_service


def test_import_folder(session, sample_project):
    project = project_service.import_project(session, str(sample_project),
                                             origin=ProjectOrigin.FOLDER)
    assert project.id is not None
    assert project.file_count >= 4
    files = session.scalars(
        select(ProjectFileRow).where(ProjectFileRow.project_id == project.id)
    ).all()
    rels = {f.rel_path.replace("\\", "/") for f in files}
    assert "app.py" in rels
    assert "tests/test_app.py" in rels
    app_file = next(f for f in files if f.rel_path.endswith("app.py"))
    assert app_file.language == "python"
    assert app_file.sha256
    test_file = next(f for f in files if "test_app.py" in f.rel_path)
    assert test_file.is_test


def test_import_captures_facts(session, sample_project):
    project = project_service.import_project(session, str(sample_project))
    facts = project.facts
    assert "python" in facts["languages"]
    assert "flask" in facts["frameworks"]
    assert "pytest" in facts["test_harnesses"]


def test_import_registers_dependencies(session, sample_project):
    project = project_service.import_project(session, str(sample_project))
    deps = session.scalars(
        select(DependencyRow).where(DependencyRow.project_id == project.id)
    ).all()
    assert any(d.name == "flask" and d.declared_version == "==3.0.0" for d in deps)


def test_import_parses_pyproject_dependencies(session, tmp_path):
    root = tmp_path / "pep621"
    root.mkdir()
    (root / "main.py").write_text("x = 1\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        "[project]\n"
        'name = "demo"\n'
        'dependencies = [\n'
        '  "pydantic>=2.7",\n'
        '  "SQLAlchemy>=2.0.30",\n'
        '  "requests; python_version < \'3.12\'",\n'
        "]\n"
        "[project.optional-dependencies]\n"
        'dev = ["pytest>=8"]\n',
        encoding="utf-8",
    )
    project = project_service.import_project(session, str(root))
    deps = {d.name: d for d in session.scalars(
        select(DependencyRow).where(DependencyRow.project_id == project.id)).all()}
    assert "pydantic" in deps and deps["pydantic"].declared_version == ">=2.7"
    assert "SQLAlchemy" in deps
    assert "requests" in deps  # environment marker stripped
    assert "pytest" in deps
    assert deps["pytest"].source_file.endswith("dev")


def test_import_parses_npm_dependencies(session, tmp_path):
    root = tmp_path / "web"
    root.mkdir()
    (root / "package.json").write_text(
        '{"dependencies": {"react": "^18.2.0"}, '
        '"devDependencies": {"jest": "^29.0.0"}}',
        encoding="utf-8",
    )
    project = project_service.import_project(session, str(root))
    deps = {d.name: d for d in session.scalars(
        select(DependencyRow).where(DependencyRow.project_id == project.id)).all()}
    assert deps["react"].ecosystem == "npm"
    assert deps["react"].declared_version == "^18.2.0"
    assert "jest" in deps


def test_import_creates_audit_event(session, sample_project):
    project = project_service.import_project(session, str(sample_project))
    events = session.query(audit.AuditEventRow).filter_by(
        action="project.import", project_id=project.id).all()
    assert len(events) == 1


def test_import_zip(tmp_path, session):
    src = tmp_path / "archive_src"
    (src / "inner").mkdir(parents=True)
    (src / "inner" / "main.py").write_text("print('hi')\n")
    zpath = tmp_path / "proj.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.write(src / "inner" / "main.py", "inner/main.py")

    project = project_service.import_project(session, str(zpath), origin=ProjectOrigin.ZIP)
    files = session.scalars(
        select(ProjectFileRow).where(ProjectFileRow.project_id == project.id)
    ).all()
    # single top-level dir is unwrapped
    assert any(f.rel_path.replace("\\", "/") == "main.py" for f in files)


def test_zip_slip_rejected(tmp_path, session):
    zpath = tmp_path / "evil.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("../../evil.txt", "boom")
    with pytest.raises(project_service.ImportError_, match="unsafe path"):
        project_service.import_project(session, str(zpath), origin=ProjectOrigin.ZIP)


def test_unknown_source_rejected(session, tmp_path):
    with pytest.raises(project_service.ImportError_):
        project_service.import_project(session, str(tmp_path / "nope.txt"))


def test_git_import_with_local_repo(tmp_path, session):
    import subprocess
    from pathlib import Path

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "code.py").write_text("x = 1\n")
    for cmd in (["git", "init"], ["git", "add", "-A"],
                ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "i"]):
        subprocess.run(cmd, cwd=repo, capture_output=True, timeout=60)

    project = project_service.import_project(session, repo.as_uri(),
                                             origin=ProjectOrigin.GIT, name="cloned")
    fetched = session.get(ProjectRow, project.id)
    assert Path(fetched.root_path, "code.py").exists()
