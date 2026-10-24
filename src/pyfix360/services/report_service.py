"""Report generation: audit reports in Markdown, JSON and HTML.

Reports include project facts, scan coverage (including tools that could NOT
run), findings with evidence, repair history and honest verification status.
"""

from __future__ import annotations

import html
import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from pyfix360.db.models import (
    AuditEventRow,
    FindingRow,
    PatchRow,
    ProjectRow,
    ScanRow,
    TestRunRow,
)
from pyfix360.services import audit, settings_service

_SEV_ORDER = ["critical", "high", "medium", "low", "info"]


def _findings(session: Session, scan_id: int) -> list[FindingRow]:
    return list(session.scalars(
        select(FindingRow).where(FindingRow.scan_id == scan_id)
        .order_by(FindingRow.severity, FindingRow.file_path, FindingRow.line_start)
    ))


def _by_severity(findings: list[FindingRow]) -> dict[str, int]:
    counts = dict.fromkeys(_SEV_ORDER, 0)
    for f in findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1
    return counts


def generate_report(
    session: Session,
    project_id: int,
    *,
    fmt: str = "markdown",
    out_path: Path | None = None,
    confirmed: bool = False,
) -> Path:
    """Write a report for a project and return the file it was written to.

    A report carries findings, code evidence and audit history, so writing one
    is an export and is gated by the ``export_reports`` capability. A caller
    holding the user's agreement passes ``confirmed=True``.
    """
    settings_service.check_access(session, "export_reports", confirmed=confirmed)
    project = session.get(ProjectRow, project_id)
    if project is None:
        raise ValueError(f"project {project_id} not found")
    scans = list(session.scalars(
        select(ScanRow).where(ScanRow.project_id == project_id).order_by(ScanRow.id)
    ))
    if not scans:
        raise ValueError("project has no scans; run a scan before reporting")
    latest = scans[-1]
    findings = _findings(session, latest.id)
    patches = list(session.scalars(
        select(PatchRow).where(PatchRow.project_id == project_id).order_by(PatchRow.id)
    ))
    runs = list(session.scalars(
        select(TestRunRow).where(TestRunRow.project_id == project_id).order_by(TestRunRow.id)
    ))
    events = list(session.scalars(
        select(AuditEventRow).where(AuditEventRow.project_id == project_id)
        .order_by(AuditEventRow.id.desc()).limit(200)
    ))

    data = {
        "generated_at": datetime.now(UTC).isoformat(),
        "project": {
            "id": project.id, "name": project.name, "root_path": project.root_path,
            "origin": project.origin, "file_count": project.file_count,
            "facts": project.facts or {},
        },
        "scan": {
            "id": latest.id, "status": latest.status,
            "started_at": str(latest.started_at), "finished_at": str(latest.finished_at),
            "files_scanned": latest.files_scanned, "files_skipped": latest.files_skipped,
            "tools_ran": latest.tools_ran, "tools_skipped": latest.tools_skipped,
            "findings_count": latest.findings_count,
        },
        "severity_counts": _by_severity(findings),
        "findings": [
            {
                "id": f.id, "stable_key": f.stable_key, "rule_id": f.rule_id,
                "category": f.category, "severity": f.severity, "confidence": f.confidence,
                "status": f.status, "title": f.title, "description": f.description,
                "file_path": f.file_path, "line_start": f.line_start, "line_end": f.line_end,
                "symbol": f.symbol, "tool": f.tool, "dedup_count": f.dedup_count,
                "evidence": [
                    {"kind": e.kind, "file_path": e.file_path, "line_start": e.line_start,
                     "line_end": e.line_end, "snippet": e.snippet, "excerpt": e.excerpt}
                    for e in f.evidence
                ],
            }
            for f in findings
        ],
        "patches": [
            {"id": p.id, "status": p.status, "file_path": p.file_path,
             "risk": p.risk, "origin": p.origin, "explanation": p.explanation,
             "added_lines": p.added_lines, "removed_lines": p.removed_lines, "diff": p.diff}
            for p in patches
        ],
        "test_runs": [
            {"id": r.id, "phase": r.phase, "command": r.command, "outcome": r.outcome,
             "passed": r.passed, "failed": r.failed, "errors": r.errors,
             "skipped": r.skipped, "duration_s": r.duration_s}
            for r in runs
        ],
        "audit_events": [
            {"id": e.id, "timestamp": str(e.timestamp), "actor": e.actor,
             "action": e.action, "target": e.target, "details": e.details, "risk": e.risk}
            for e in events
        ],
    }

    cfg_reports = Path(out_path) if out_path else _default_out(project, latest, fmt)
    cfg_reports.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "json":
        cfg_reports.write_text(json.dumps(data, indent=2), encoding="utf-8")
    elif fmt == "html":
        cfg_reports.write_text(_render_html(data), encoding="utf-8")
    else:
        cfg_reports.write_text(_render_markdown(data), encoding="utf-8")

    audit.record(session, "report.generate", project_id=project_id,
                 target=str(cfg_reports), details=f"format={fmt} scan={latest.id}",
                 actor="user")
    return cfg_reports


def _default_out(project: ProjectRow, scan: ScanRow, fmt: str) -> Path:
    from pyfix360.config import get_config

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    ext = {"json": "json", "html": "html"}.get(fmt, "md")
    return get_config().reports_dir / f"{project.name}_scan{scan.id}_{stamp}.{ext}"


def _render_markdown(d: dict) -> str:
    p, s = d["project"], d["scan"]
    facts = p["facts"]
    lines = [
        f"# PYFIX 360 Audit Report — {p['name']}",
        f"_Generated {d['generated_at']}_",
        "",
        "## 1. Project",
        f"- Path: `{p['root_path']}` (origin: {p['origin']})",
        f"- Files registered: {p['file_count']}",
        f"- Languages: {', '.join(facts.get('languages', [])) or 'none detected'}",
        f"- Frameworks: {', '.join(facts.get('frameworks', [])) or 'none detected'}",
        f"- Build systems: {', '.join(facts.get('build_systems', [])) or 'none detected'}",
        f"- Entry points: {', '.join(facts.get('entry_points', [])) or 'none detected'}",
        f"- Test harnesses: {', '.join(facts.get('test_harnesses', [])) or 'none detected'}",
        "",
        "## 2. Scan Coverage",
        f"- Scan #{s['id']} — status: **{s['status']}**",
        f"- Files scanned: {s['files_scanned']} (skipped: {s['files_skipped']})",
        f"- Tools ran: {', '.join(s['tools_ran']) or 'builtin only'}",
    ]
    if s["tools_skipped"]:
        lines.append("- Tools NOT available (findings incomplete):")
        lines += [f"  - {t}" for t in s["tools_skipped"]]
    lines += ["", "## 3. Findings by Severity"]
    for sev in _SEV_ORDER:
        lines.append(f"- {sev}: {d['severity_counts'].get(sev, 0)}")
    lines += ["", "## 4. Findings"]
    if not d["findings"]:
        lines.append("_No findings._")
    for f in d["findings"]:
        lines += [
            f"### [{f['severity'].upper()}] {f['title']} <sub>{f['rule_id']}</sub>",
            f"- Location: `{f['file_path']}:{f['line_start']}` "
            f"(symbol: {f['symbol'] or 'module'}, tool: {f['tool']}, "
            f"confidence: {f['confidence']}, status: {f['status']})",
            f"- {f['description']}",
        ]
        if f["dedup_count"] > 1:
            lines.append(f"- Deduplicated occurrences: {f['dedup_count']}")
        for e in f["evidence"]:
            lines.append(f"- Evidence ({e['kind']}) `{e['file_path']}` "
                         f"L{e['line_start']}-L{e['line_end']}:")
            if e["snippet"]:
                lines.append("  ```")
                lines += ["  " + ln for ln in e["snippet"].splitlines()[:8]]
                lines.append("  ```")
        lines.append("")
    lines += ["## 5. Repairs & Verification"]
    if not d["patches"]:
        lines.append("_No repairs attempted._")
    for pt in d["patches"]:
        lines.append(f"- Patch #{pt['id']} [{pt['status']}] `{pt['file_path']}` "
                     f"(+{pt['added_lines']}/-{pt['removed_lines']}, risk: {pt['risk']})")
        lines.append(f"  - {pt['explanation']}")
        lines.append("  - Diff:")
        lines.append("    ```diff")
        lines += ["    " + ln for ln in pt["diff"].splitlines()[:60]]
        lines.append("    ```")
    lines += ["", "## 6. Test Runs"]
    if not d["test_runs"]:
        lines.append("_No test runs recorded._")
    for r in d["test_runs"]:
        lines.append(f"- Run #{r['id']} ({r['phase']}) [{r['outcome']}] "
                     f"passed={r['passed']} failed={r['failed']} errors={r['errors']} "
                     f"skipped={r['skipped']} — `{r['command'] or 'no harness'}`")
    lines += ["", "## 7. Audit Trail (last 50 events)"]
    for e in d["audit_events"][:50]:
        lines.append(f"- {e['timestamp']} [{e['actor']}/{e['risk']}] {e['action']} "
                     f"{e['target'] or ''} — {e['details']}")
    lines += ["", "## 8. Limitations", ""]
    lines.append("- Tools marked NOT available above were not run; their findings are absent.")
    lines.append("- Patches labelled `applied` but not `verified` have NOT passed tests.")
    lines.append("- Sandbox network isolation is not enforced at the OS level; see Settings.")
    return "\n".join(lines) + "\n"


def _render_html(d: dict) -> str:
    md_like = _render_markdown(d)
    body = html.escape(md_like)
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>PYFIX 360 report — {html.escape(d['project']['name'])}</title>"
        "<style>body{font-family:Consolas,monospace;white-space:pre-wrap;"
        "max-width:1000px;margin:2em auto;}</style></head><body>"
        f"{body}</body></html>"
    )
