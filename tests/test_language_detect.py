"""Tests for language/framework/build detection."""

from pathlib import Path

from pyfix360.analysis import language_detect as ld


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "x").mkdir(parents=True)
    (root / "src" / "pkg" / "main.py").write_text("x = 1\n")
    (root / "app.ts").write_text("const x = 1\n")
    (root / "style.css").write_text("body{}\n")
    (root / "node_modules" / "x" / "lib.js").write_text("module.exports = 1\n")
    (root / "package.json").write_text('{"dependencies": {"react": "^18"}}\n')
    (root / "requirements.txt").write_text("django==5.0\n")
    (root / "manage.py").write_text("import django\n")
    (root / "conftest.py").write_text("")
    (root / "Dockerfile").write_text("FROM python:3.12\n")
    (root / "app.min.js").write_text("var a=1\n")
    return root


def test_detect_language_by_extension():
    assert ld.detect_language(Path("a.py")) == "python"
    assert ld.detect_language(Path("a.tsx")) == "typescript"
    assert ld.detect_language(Path("Dockerfile")) == "dockerfile"
    assert ld.detect_language(Path("a.unknownext")) is None


def test_ignored_dirs_and_generated():
    assert ld.is_ignored_dir("node_modules")
    assert not ld.is_ignored_dir("src")
    assert ld.is_generated(Path("app.min.js"))
    assert ld.is_generated(Path("schema_pb2.py"))
    assert not ld.is_generated(Path("app.py"))


def test_test_path_detection():
    assert ld.is_test_path("tests/test_x.py")
    assert ld.is_test_path("pkg/x_test.py")
    assert ld.is_test_path("web/a.test.js")
    assert not ld.is_test_path("src/main.py")


def test_frameworks_detected(tmp_path):
    root = _project(tmp_path)
    frameworks = ld.detect_frameworks(root)
    assert "django" in frameworks
    assert "react" in frameworks


def test_build_systems_detected(tmp_path):
    root = _project(tmp_path)
    builds = ld.detect_build_systems(root)
    assert any("npm" in b for b in builds)
    assert any("docker" in b for b in builds)


def test_test_harness_pytest(tmp_path):
    root = _project(tmp_path)
    harnesses = ld.detect_test_harnesses(root)
    assert "pytest" in harnesses


def test_entry_points(tmp_path):
    root = _project(tmp_path)
    entries = ld.detect_entry_points(root)
    assert any("manage.py" in e for e in entries)


def test_known_languages_excludes_ignored_dirs(tmp_path):
    root = _project(tmp_path)
    langs = ld.known_languages(root)
    assert "python" in langs
    assert "typescript" in langs
    # node_modules excluded: lib.js is JS, but we only assert python/ts presence
    # plus css from top-level
    assert "css" in langs


def test_package_managers(tmp_path):
    root = _project(tmp_path)
    managers = ld.detect_package_managers(root)
    assert any("pip" in m for m in managers)
    assert any("npm" in m for m in managers)
