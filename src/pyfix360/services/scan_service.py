"""Scan orchestration.

Runs the built-in analyzer for every registered Python file plus any requested
external tool adapters, deduplicates findings, correlates them into root-cause
groups, and persists everything with progress reporting. Incremental scans
skip files whose SHA-256 has not changed since the previous completed scan.
"""

from __future__ import annotations

import fnmatch
import logging
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from pyfix360.analysis.python_analyzer import analyze_python_file
from pyfix360.analysis.tool_adapters import ToolAdapter, default_adapters
from pyfix360.db.models import FindingRow, ModuleRow, ProjectFileRow, ProjectRow, ScanRow
from pyfix360.models import ScanStatus, utcnow
from pyfix360.services import audit

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[float, str], None]


def matches_any(rel_path: str, patterns: list[str]) -> bool:
    """True when the path matches one of the glob patterns.

    ``fnmatch`` is used because the patterns are written the way people write
    them (``**/vendor/**``, ``*.min.js``). A leading ``**/`` is also tried
    without the leading segment so that ``**/vendor/**`` matches
    ``vendor/lib.py`` as well as ``src/vendor/lib.py``.
    """
    name = (rel_path or "").replace("\\", "/")
    for pattern in patterns or []:
        pattern = (pattern or "").strip().replace("\\", "/")
        if not pattern:
            continue
        if fnmatch.fnmatch(name, pattern):
            return True
        if fnmatch.fnmatch(name.rsplit("/", 1)[-1], pattern):
            return True
        if pattern.startswith("**/") and fnmatch.fnmatch(name, pattern[3:]):
            return True
    return False


def _dedup_key(f) -> tuple[str, str, str, str]:
    return (f.rule_id, (f.file_path or "").replace("\\", "/"), f.symbol or "", f.title)


def deduplicate(findings: list) -> list:
    """Collapse identical findings; keep evidence and count repeats."""
    seen: dict[tuple[str, str, str, str], object] = {}
    for f in findings:
        key = _dedup_key(f)
        if key in seen:
            existing = seen[key]
            existing.dedup_count += 1
            if f.evidence and not existing.evidence:
                existing.evidence = f.evidence
            continue
        seen[key] = f
    return list(seen.values())


def correlate(findings: list) -> None:
    """Assign root-cause groups: same rule in same file+symbol => one cause."""
    groups: dict[str, list] = {}
    for f in findings:
        f.root_cause_group = f.root_cause_group or f.stable_key[:16]
        groups.setdefault(f.root_cause_group, []).append(f)


def create_scan(session: Session, project_id: int, tools: list[str] | None = None) -> ScanRow:
    scan = ScanRow(project_id=project_id, status=ScanStatus.PENDING.value,
                   tools_requested=tools or ["builtin"])
    session.add(scan)
    session.flush()
    return scan


def run_scan(
    session: Session,
    scan_id: int,
    *,
    incremental: bool | None = None,
    timeout_s: int | None = None,
    adapters: list[ToolAdapter] | None = None,
    progress: ProgressCallback | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> ScanRow:
    """Execute a scan to completion (or partial completion).

    ``incremental``, ``timeout_s`` and the adapter set default to whatever the
    user configured in the Settings Center, so the UI options are actually
    honoured rather than decorative.
    """
    from pyfix360.services import settings_service

    try:
        settings = settings_service.get_settings(session)
    except Exception:  # pragma: no cover - fresh DB edge case
        settings = None

    if settings is not None:
        if incremental is None:
            incremental = settings.scan_incremental
        if timeout_s is None:
            timeout_s = settings.execution_timeout_s
        enabled = set(settings.enabled_analyzers or [])
        include_globs = list(settings.scan_include_globs or [])
        exclude_globs = list(settings.scan_exclude_globs or [])
        skip_generated = settings.scan_skip_generated
        max_file_bytes = max(1, settings.scan_max_file_kb) * 1024
        max_files = max(1, settings.scan_max_files)
        workers = max(0, settings.scan_parallel_workers)
        tool_paths = dict(settings.custom_tool_paths or {})
    else:
        if incremental is None:
            incremental = True
        if timeout_s is None:
            timeout_s = 300
        enabled = set()
        include_globs, exclude_globs = [], []
        skip_generated = True
        max_file_bytes = _LARGE_FILE_BYTES
        max_files = 20000
        workers = 0
        tool_paths = {}

    scan = session.get(ScanRow, scan_id)
    if scan is None:
        raise ValueError(f"Scan {scan_id} not found")
    project = session.get(ProjectRow, scan.project_id)
    if project is None:
        raise ValueError(f"Project {scan.project_id} not found")
    root = Path(project.root_path)
    if not root.is_dir():
        scan.status = ScanStatus.FAILED.value
        scan.message = f"Project root missing: {root}"
        session.flush()
        return scan

    # Respect the user's analyzer selection unless the caller overrode it.
    if adapters is None and enabled:
        adapters = [a for a in default_adapters() if a.name in enabled]

    scan.status = ScanStatus.RUNNING.value
    scan.started_at = utcnow()
    session.flush()

    # file registry (fresh + incremental baseline)
    files = list(session.scalars(select(ProjectFileRow).where(ProjectFileRow.project_id == project.id)))

    findings: list = []
    modules: list[ModuleRow] = []
    scanned = skipped = carried = 0
    total = max(len(files), 1)

    try:
        for pf in files:
            if cancel_check and cancel_check():
                scan.status = ScanStatus.CANCELLED.value
                scan.message = "cancelled by user"
                break
            # --- scope: the user's include/exclude globs ---------------
            if include_globs and not matches_any(pf.rel_path, include_globs):
                skipped += 1
                continue
            if exclude_globs and matches_any(pf.rel_path, exclude_globs):
                skipped += 1
                continue
            # --- size limit: large files are not analysed at all ------
            if pf.size_bytes and pf.size_bytes > max_file_bytes:
                skipped += 1
                continue
            if skip_generated and pf.is_generated:
                skipped += 1
                continue
            # --- file count cap -----------------------------------------
            # The cap counts every file the scan looks at, not only the ones
            # the built-in analyser reads, so "maximum files per scan" means
            # what it says and the reported files_scanned can never exceed it.
            if scanned >= max_files:
                skipped += 1
                continue
            # incremental: unchanged files keep their previous findings
            if (incremental and pf.last_scan_id and pf.last_scan_hash == pf.sha256
                    and pf.sha256):
                old_rows = session.scalars(
                    select(FindingRow).where(FindingRow.scan_id == pf.last_scan_id,
                                             FindingRow.file_path == pf.rel_path)
                ).all()
                for row in old_rows:
                    findings.append(_row_to_finding_model(row))
                carried += 1
                scanned += 1
                continue
            if pf.language == "python" and not pf.is_generated:
                try:
                    fnds, syms = analyze_python_file(root, pf.rel_path)
                    findings.extend(fnds)
                    for s in syms:
                        modules.append(ModuleRow(
                            project_id=project.id, rel_path=pf.rel_path,
                            kind=s["kind"], name=s["name"],
                            qualified_name=f"{pf.rel_path}::{s['name']}",
                            line_start=s.get("line_start"), line_end=s.get("line_end"),
                        ))
                except Exception as exc:  # analyzer bug must not kill the scan
                    logger.exception("analyzer failed on %s", pf.rel_path)
                    scan.tools_skipped.append(f"builtin:{pf.rel_path}: {exc}")
                pf.last_scan_hash = pf.sha256
                pf.last_scan_id = scan.id
                scanned += 1
            elif pf.language not in (None, "python"):
                scanned += 1  # tracked, but only external tools analyze it
            scan.progress = round(min(scanned / total, 0.99), 3)
            if progress and scanned % 25 == 0:
                progress(scan.progress, f"analyzed {scanned}/{total} files")
            session.flush()

        # --- builtin analyzer marker ---------------------------------------
        # The builtin AST analyzer always runs for Python files, but it is not
        # an external adapter. Record it explicitly so "tools ran" and any
        # required_tools gate reflect what actually happened.
        if "builtin" not in scan.tools_ran:
            scan.tools_ran.append("builtin")

        # --- external tools ------------------------------------------------
        if scan.status != ScanStatus.CANCELLED.value:
            requested = set(scan.tools_requested or [])
            adapters = adapters if adapters is not None else default_adapters()
            for adapter, result, reason in _run_adapters(adapters, root, timeout_s,
                                                         tool_paths, workers):
                if cancel_check and cancel_check():
                    scan.status = ScanStatus.CANCELLED.value
                    break
                if requested and "builtin" not in requested and adapter.name not in requested:
                    continue
                if result is None:
                    scan.tools_skipped.append(f"{adapter.name}: {reason}")
                    continue
                if result.ran:
                    scan.tools_ran.append(adapter.name)
                    findings.extend(result.findings)
                else:
                    scan.tools_skipped.append(f"{adapter.name}: {result.skip_reason}")
                session.flush()

        # --- persist findings ----------------------------------------------
        if scan.status != ScanStatus.CANCELLED.value:
            scan.status = ScanStatus.COMPLETED.value
        findings = deduplicate(findings)
        correlate(findings)
        for f in findings:
            row = FindingRow(
                scan_id=scan.id, project_id=project.id,
                stable_key=f.stable_key, rule_id=f.rule_id, category=f.category.value,
                severity=f.severity.value, confidence=f.confidence.value,
                status="detected", title=f.title, description=f.description,
                file_path=f.file_path, line_start=f.line_start, line_end=f.line_end,
                symbol=f.symbol, tool=f.tool, root_cause_group=f.root_cause_group,
                dedup_count=f.dedup_count,
            )
            session.add(row)
            session.flush()
            for ev in f.evidence:
                session.add(_evidence_row(row.id, ev))
        for m in modules:
            session.add(m)
        scan.findings_count = len(findings)
        scan.files_scanned = scanned
        scan.files_skipped = skipped
        if carried:
            scan.message = (scan.message + f" carried_forward={carried}").strip()
        if scan.tools_skipped and scan.status == ScanStatus.COMPLETED.value:
            scan.status = ScanStatus.PARTIAL.value
        project.last_scan_id = scan.id
        # auto-acknowledge informational findings, if the user asked for it
        if settings is not None and settings.auto_acknowledge_info \
                and scan.status == ScanStatus.COMPLETED.value:
            from pyfix360.services import findings_service

            acked = findings_service.acknowledge_info(session, scan.id)
            if acked:
                audit.record(session, "scan.auto_acknowledge", project_id=project.id,
                             target=f"scan#{scan.id}", details=f"acknowledged={acked}",
                             actor="system")
    except Exception as exc:
        logger.exception("scan %s failed", scan_id)
        scan.status = ScanStatus.FAILED.value
        scan.message = f"{type(exc).__name__}: {exc}"
    scan.finished_at = utcnow()
    scan.progress = 1.0
    audit.record(
        session, "scan.complete", project_id=project.id, target=f"scan#{scan.id}",
        details=f"status={scan.status} findings={scan.findings_count} "
                f"tools_ran={scan.tools_ran} tools_skipped={scan.tools_skipped}",
        risk="warn" if scan.status in (ScanStatus.FAILED.value, ScanStatus.PARTIAL.value) else "info",
    )
    session.flush()
    return scan


_LARGE_FILE_BYTES = 1_000_000


def _auto_workers(usable: int) -> int:
    """How many tools to run at once when the setting says "auto".

    The adapters are separate processes doing CPU- and IO-bound work, so a small
    multiple of the core count helps without oversubscribing the machine.
    """
    if usable <= 1:
        return 1
    return max(2, min(usable, (os.cpu_count() or 2) * 2))


def _run_adapters(
    adapters: list[ToolAdapter],
    root: Path,
    timeout_s: int,
    tool_paths: dict[str, str],
    workers: int,
) -> list[tuple[ToolAdapter, object | None, str]]:
    """Run the external analyzers and report what each one did.

    Returns ``(adapter, result, reason)`` per adapter; ``result`` is ``None``
    when the tool was not available, and ``reason`` then says why. With more
    than one worker the tools run concurrently — they are separate processes,
    so the wall-clock saving is real.

    ``workers`` of 0 means "auto": the pool is sized from the core count.
    """
    usable: list[ToolAdapter] = []
    out: list[tuple[ToolAdapter, object | None, str]] = []
    for adapter in adapters:
        try:
            ok, reason = adapter.is_available(tool_paths)
        except Exception as exc:  # a broken adapter must not kill the scan
            out.append((adapter, None, f"availability check failed: {exc}"))
            continue
        if ok:
            usable.append(adapter)
        else:
            out.append((adapter, None, reason))

    def run_one(adapter: ToolAdapter):
        try:
            return adapter.run(root, timeout_s=timeout_s, tool_paths=tool_paths)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("adapter %s failed", adapter.name)
            return exc

    pool_size = _auto_workers(len(usable)) if workers <= 0 else workers
    if pool_size > 1 and len(usable) > 1:
        with ThreadPoolExecutor(max_workers=min(pool_size, len(usable))) as pool:
            results = list(pool.map(run_one, usable))
    else:
        results = [run_one(adapter) for adapter in usable]

    for adapter, result in zip(usable, results, strict=True):
        if isinstance(result, Exception):
            out.append((adapter, None, f"{type(result).__name__}: {result}"))
        else:
            out.append((adapter, result, ""))
    return out


def _evidence_row(finding_id: int, ev) -> object:
    from pyfix360.db.models import EvidenceRow

    return EvidenceRow(
        finding_id=finding_id, kind=ev.kind, file_path=ev.file_path,
        line_start=ev.line_start, line_end=ev.line_end,
        snippet=ev.snippet, excerpt=ev.excerpt,
    )


def _row_to_finding_model(row: FindingRow):
    """Clone a persisted finding row into a detached domain model."""
    from pyfix360.models import Confidence, Evidence, Finding, FindingCategory, Severity

    return Finding(
        stable_key=row.stable_key,
        rule_id=row.rule_id,
        category=FindingCategory(row.category),
        severity=Severity(row.severity),
        confidence=Confidence(row.confidence),
        title=row.title,
        description=row.description,
        file_path=row.file_path,
        line_start=row.line_start,
        line_end=row.line_end,
        symbol=row.symbol,
        tool=row.tool,
        root_cause_group=row.root_cause_group,
        dedup_count=row.dedup_count,
        evidence=[
            Evidence(kind=ev.kind, file_path=ev.file_path, line_start=ev.line_start,
                     line_end=ev.line_end, snippet=ev.snippet, excerpt=ev.excerpt)
            for ev in row.evidence
        ],
    )
