"""Project import service.

Supports three origins:
- folder: analyze in place (read-only); the original is never modified here
- zip: extracted into the managed data directory
- git: cloned into the managed data directory (uses the git executable)

Import registers every tracked file with a SHA-256 fingerprint and detected
language, and captures project facts (frameworks, build systems, entry points).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

from sqlalchemy.orm import Session

from pyfix360.analysis import language_detect as ld
from pyfix360.config import get_config
from pyfix360.db.models import DependencyRow, ProjectFileRow, ProjectRow
from pyfix360.models import Project, ProjectFacts, ProjectOrigin
from pyfix360.services import audit

_MAX_FILE_BYTES = 5_000_000  # files larger than this are registered but not analyzed


class ImportError_(Exception):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _managed_dir(kind: str, name: str) -> Path:
    cfg = get_config()
    base = cfg.data_dir / "imports" / kind
    base.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:80] or "project"
    target = base / safe
    counter = 1
    while target.exists():
        target = base / f"{safe}_{counter}"
        counter += 1
    return target


def _import_git(url: str, dest: Path, *, timeout_s: int = 600) -> None:
    if not shutil.which("git"):
        raise ImportError_("git executable not found on PATH; cannot import from git.")
    try:
        proc = subprocess.run(
            ["git", "clone", "--depth", "1", url, str(dest)],
            capture_output=True, text=True, timeout=timeout_s, encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        raise ImportError_(f"git clone timed out after {timeout_s}s") from exc
    if proc.returncode != 0:
        raise ImportError_(f"git clone failed: {proc.stderr.strip()[:500]}")


def _import_zip(zip_path: Path, dest: Path) -> None:
    if not zipfile.is_zipfile(zip_path):
        raise ImportError_(f"Not a valid ZIP archive: {zip_path}")
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = info.filename
            # zip-slip protection
            target = (dest / name).resolve()
            if not str(target).startswith(str(dest.resolve())):
                raise ImportError_(f"Archive contains an unsafe path: {name}")
        zf.extractall(dest)
    # unwrap a single top-level directory (common for repo archives)
    entries = list(dest.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        inner = entries[0]
        for child in inner.iterdir():
            shutil.move(str(child), dest / child.name)
        inner.rmdir()


_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[.*?\])?\s*([=<>!~].*)?$")
_PEP508 = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(.*)$")


def _dep_from_pep508(spec: str, source: str) -> DependencyRow | None:
    """Parse a PEP 508 requirement string like 'pydantic>=2.7'."""
    spec = spec.split(";", 1)[0].strip()  # drop environment markers
    if not spec or spec.startswith("#"):
        return None
    m = _PEP508.match(spec)
    if not m:
        return None
    name, version = m.group(1), (m.group(2) or "").strip()
    # strip extras and whitespace from the version specifier
    return DependencyRow(name=name, declared_version=version or None, ecosystem="pypi",
                         source_file=source)


def _parse_pyproject(root: Path) -> list[DependencyRow]:
    """Read PEP 621 [project] dependencies and optional-dependencies."""
    path = root / "pyproject.toml"
    if not path.is_file():
        return []
    try:
        import tomllib
    except ImportError:  # pragma: no cover - py3.11+ guarantees tomllib
        return []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
    except Exception:
        return []

    deps: list[DependencyRow] = []
    project = data.get("project") or {}
    for spec in project.get("dependencies") or []:
        dep = _dep_from_pep508(str(spec), "pyproject.toml:project.dependencies")
        if dep:
            deps.append(dep)
    for extra, specs in (project.get("optional-dependencies") or {}).items():
        for spec in specs or []:
            dep = _dep_from_pep508(str(spec),
                                   f"pyproject.toml:project.optional-dependencies.{extra}")
            if dep:
                deps.append(dep)
    # poetry-style
    poetry = (data.get("tool") or {}).get("poetry") or {}
    for name, spec in (poetry.get("dependencies") or {}).items():
        if name.lower() == "python":
            continue
        version = spec if isinstance(spec, str) else (spec or {}).get("version", "")
        deps.append(DependencyRow(name=name, declared_version=version or None,
                                  ecosystem="pypi", source_file="pyproject.toml:poetry"))
    return deps


def _parse_requirements(root: Path) -> list[DependencyRow]:
    deps: list[DependencyRow] = []
    req = root / "requirements.txt"
    if not req.is_file():
        return deps
    for raw in req.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "-")):
            continue
        m = _REQ_LINE.match(line)
        if m:
            deps.append(DependencyRow(
                name=m.group(1), declared_version=(m.group(2) or "").strip() or None,
                ecosystem="pypi", source_file="requirements.txt",
            ))
    return deps


def _parse_package_json(root: Path) -> list[DependencyRow]:
    deps: list[DependencyRow] = []
    pj = root / "package.json"
    if not pj.is_file():
        return deps
    try:
        data = json.loads(pj.read_text(encoding="utf-8-sig", errors="replace"))
    except json.JSONDecodeError:
        return deps
    for section in ("dependencies", "devDependencies"):
        for name, version in (data.get(section) or {}).items():
            deps.append(DependencyRow(
                name=name, declared_version=str(version), ecosystem="npm",
                source_file=f"package.json:{section}",
            ))
    return deps


def import_project(
    session: Session,
    source: str,
    *,
    origin: ProjectOrigin | None = None,
    name: str | None = None,
) -> ProjectRow:
    """Import a project from a folder path, ZIP archive or git URL."""
    src = Path(source).expanduser()

    if origin is None:
        if re.match(r"^(https?|git|ssh|git@|file)://", source) or source.endswith(".git"):
            origin = ProjectOrigin.GIT
        elif src.is_file() and src.suffix.lower() == ".zip":
            origin = ProjectOrigin.ZIP
        elif src.is_dir():
            origin = ProjectOrigin.FOLDER
        else:
            raise ImportError_(
                f"Cannot determine import type for '{source}'. "
                "Pass origin=folder|zip|git explicitly."
            )

    proj_name = name or (src.stem if origin == ProjectOrigin.ZIP else src.name or "project")
    if origin == ProjectOrigin.FOLDER:
        if not src.is_dir():
            raise ImportError_(f"Folder does not exist: {src}")
        root = src.resolve()
    elif origin == ProjectOrigin.ZIP:
        if not src.is_file():
            raise ImportError_(f"ZIP file does not exist: {src}")
        root = _managed_dir("zip", proj_name)
        _import_zip(src.resolve(), root)
    else:
        root = _managed_dir("git", proj_name)
        _import_git(source, root)

    project = ProjectRow(name=proj_name, root_path=str(root), origin=origin.value)
    session.add(project)
    session.flush()

    # --- register files ------------------------------------------------
    file_count = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(root).parts
        if any(ld.is_ignored_dir(p) for p in rel_parts):
            continue
        rel = str(path.relative_to(root))
        try:
            size = path.stat().st_size
            digest = "" if size > _MAX_FILE_BYTES else _sha256(path)
        except OSError:
            continue
        session.add(ProjectFileRow(
            project_id=project.id,
            rel_path=rel,
            language=ld.detect_language(path),
            size_bytes=size,
            sha256=digest,
            is_test=ld.is_test_path(rel),
            is_generated=ld.is_generated(path),
        ))
        file_count += 1
    project.file_count = file_count

    # --- project facts --------------------------------------------------
    facts = ProjectFacts(
        languages=ld.known_languages(root),
        frameworks=ld.detect_frameworks(root),
        build_systems=ld.detect_build_systems(root),
        entry_points=ld.detect_entry_points(root),
        package_managers=ld.detect_package_managers(root),
        test_harnesses=ld.detect_test_harnesses(root),
    )
    project.facts = facts.model_dump()

    # --- dependencies ---------------------------------------------------
    deps = (_parse_requirements(root) + _parse_pyproject(root)
            + _parse_package_json(root))
    for dep in deps:
        dep.project_id = project.id
        session.add(dep)

    audit.record(
        session, "project.import", project_id=project.id,
        target=str(root), details=f"origin={origin.value} files={file_count}",
    )
    session.flush()
    return project


def project_to_model(row: ProjectRow) -> Project:
    return Project(
        id=row.id,
        name=row.name,
        root_path=row.root_path,
        origin=ProjectOrigin(row.origin),
        imported_at=row.imported_at,
        last_scan_id=row.last_scan_id,
        facts=ProjectFacts(**(row.facts or {})),
        file_count=row.file_count,
    )


def delete_project(session: Session, project_id: int, *,
                   confirmed: bool = False) -> bool:
    """Forget a project: its rows go, the files on disk stay.

    Removing a project takes its scans, findings, patches and history with it,
    so it is gated by the ``delete_project`` capability — *ask* by default, and
    a caller with the user's agreement passes ``confirmed=True``. The files in
    the project folder are never touched: this is a record, not a directory.
    """
    from pyfix360.services import settings_service

    settings_service.check_access(session, "delete_project", confirmed=confirmed)
    row = session.get(ProjectRow, project_id)
    if row is None:
        return False
    root = row.root_path
    session.delete(row)
    session.flush()
    audit.record(session, "project.delete", target=f"project#{project_id}",
                 details=f"root={root}", actor="user", risk="warn")
    session.flush()
    return True
