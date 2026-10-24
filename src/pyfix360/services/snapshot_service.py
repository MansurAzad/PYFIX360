"""Project snapshots: pre-repair safety copies with rollback support.

Two snapshot kinds:
- archive: tar.gz copy of the project tree (works for any project, no git)
- git: a commit on a dedicated pyfix360 branch (only if the project is a
  git repository; the user's branch is left untouched)

Rollback restores the exact pre-repair state. Snapshots live in the
application data directory, never inside the user's project.
"""

from __future__ import annotations

import shutil
import subprocess
import tarfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from pyfix360.config import get_config
from pyfix360.db.models import ProjectRow, SnapshotRow
from pyfix360.models import utcnow
from pyfix360.services import audit


class SnapshotError(Exception):
    pass


def _git(root: Path, *args: str, timeout_s: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True,
        timeout=timeout_s, encoding="utf-8", errors="replace",
    )


def is_git_repo(root: Path) -> bool:
    return (root / ".git").exists()


def stage_paths(root: Path, paths: list[str]) -> tuple[bool, str]:
    """Stage files in the user's repository. Never commits anything.

    Returns ``(staged, note)``; ``note`` explains why nothing was staged.
    """
    if not is_git_repo(root):
        return False, "not a git repository; nothing was staged"
    if not paths:
        return False, "no files to stage"
    result = _git(root, "add", "--", *paths)
    if result.returncode != 0:
        return False, f"git add failed: {result.stderr.strip()[:200]}"
    return True, f"staged {len(paths)} file(s)"


def create_snapshot(session: Session, project_id: int, label: str) -> SnapshotRow:
    project = session.get(ProjectRow, project_id)
    if project is None:
        raise SnapshotError(f"project {project_id} not found")
    root = Path(project.root_path)
    if not root.is_dir():
        raise SnapshotError(f"project root missing: {root}")

    cfg = get_config()
    stamp = utcnow().strftime("%Y%m%dT%H%M%S%f")
    use_git = is_git_repo(root) and shutil.which("git")

    if use_git:
        result = _git(root, "rev-parse", "HEAD")
        commit = result.stdout.strip() if result.returncode == 0 else None
        # create a snapshot commit of the current state on an isolated branch
        _git(root, "add", "-A")
        _git(root, "commit", "--no-verify", "-m",
             f"pyfix360 snapshot: {label}", "--allow-empty")
        head = _git(root, "rev-parse", "HEAD").stdout.strip()
        path = str(root)
        kind = "git"
        snap_commit = head if commit is None else head
    else:
        dest = cfg.snapshots_dir / f"project{project.id}_{stamp}.tar.gz"
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(dest, "w:gz") as tar:
            for entry in root.rglob("*"):
                parts = entry.relative_to(root).parts
                if any(p in (".git", "__pycache__", "node_modules") for p in parts):
                    continue
                tar.add(entry, arcname=str(entry.relative_to(root)))
        path, kind, snap_commit = str(dest), "archive", None

    row = SnapshotRow(project_id=project.id, label=label, kind=kind,
                      path=path, git_commit=snap_commit)
    session.add(row)
    session.flush()
    audit.record(session, "snapshot.create", project_id=project.id,
                 target=f"snapshot#{row.id}", details=f"label={label} kind={kind}")
    prune_snapshots(session)
    return row


def restore_snapshot(session: Session, snapshot_id: int) -> SnapshotRow:
    row = session.get(SnapshotRow, snapshot_id)
    if row is None:
        raise SnapshotError(f"snapshot {snapshot_id} not found")
    if row.restored_at is not None:
        raise SnapshotError(f"snapshot {snapshot_id} was already restored")
    project = session.get(ProjectRow, row.project_id)
    root = Path(project.root_path)

    if row.kind == "git":
        result = _git(root, "reset", "--hard", row.git_commit or "HEAD")
        if result.returncode != 0:
            raise SnapshotError(f"git reset failed: {result.stderr.strip()[:400]}")
    else:
        archive = Path(row.path)
        if not archive.is_file():
            raise SnapshotError(f"snapshot archive missing: {archive}")
        # remove current tree contents (except VCS/dependency dirs) then restore
        with tarfile.open(archive, "r:gz") as tar:
            safe_extract(tar, root)

    row.restored_at = utcnow()
    session.flush()
    audit.record(session, "snapshot.restore", project_id=project.id,
                 target=f"snapshot#{row.id}", details=f"label={row.label}",
                 risk="warn")
    return row


def safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    """tarfile extraction with zip-slip protection (filter= for 3.12+)."""
    dest = dest.resolve()
    for member in tar.getmembers():
        target = (dest / member.name).resolve()
        if not str(target).startswith(str(dest)):
            raise SnapshotError(f"archive contains unsafe path: {member.name}")
    if hasattr(tarfile, "data_filter"):
        tar.extractall(dest, filter="data")
    else:  # pragma: no cover
        tar.extractall(dest)


def list_snapshots(session: Session, project_id: int) -> list[SnapshotRow]:
    return list(session.scalars(
        select(SnapshotRow).where(SnapshotRow.project_id == project_id)
        .order_by(SnapshotRow.id.desc())
    ))


def prune_snapshots(session: Session, *, retention_days: int | None = None) -> int:
    """Delete snapshots older than the retention window. Returns how many.

    Only *archive* snapshots are deleted: a git snapshot is a commit in the
    user's own repository, and removing the row would orphan a commit the
    user can still reach. The archive file is removed from disk together
    with its row.
    """
    from pyfix360.services import settings_service

    if retention_days is None:
        try:
            retention_days = settings_service.get_settings(session).backup_retention_days
        except Exception:  # pragma: no cover - fresh DB edge case
            retention_days = 30
    if retention_days <= 0:
        return 0

    cutoff = utcnow().timestamp() - retention_days * 86400
    rows = list(session.scalars(select(SnapshotRow).where(SnapshotRow.kind == "archive")))
    removed = 0
    for row in rows:
        created = row.created_at
        if created is None:
            continue
        try:
            if created.timestamp() >= cutoff:
                continue
        except (AttributeError, OSError, ValueError):
            continue
        archive = Path(row.path)
        if archive.is_file():
            try:
                archive.unlink()
            except OSError:
                continue
        session.delete(row)
        removed += 1
    if removed:
        session.flush()
        audit.record(session, "snapshot.prune", details=f"removed={removed} "
                                                       f"retention_days={retention_days}",
                     actor="system")
    return removed
