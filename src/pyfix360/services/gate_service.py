"""Quality gates.

A gate is a declarative rule that a scan result must satisfy — for example
"no more than zero critical findings" or "ruff and bandit must both have run".
Gates make the definition of *done* explicit and machine-checkable rather
than a feeling.

Design notes:

* Gates are evaluated against a completed scan and always produce a written
  explanation together with the offending finding ids. A failing gate is never
  just ``False``; it says which findings caused it.
* ``blocking`` gates decide the overall pass/fail. Non-blocking gates are
  advisory and are reported separately, so a team can adopt a rule gradually.
* Evaluation is pure with respect to the project: nothing is written back.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from pyfix360.db.models import FindingRow, QualityGateRow, ScanRow
from pyfix360.models import (
    GateReport,
    GateResult,
    Project,
    QualityGate,
    Severity,
)
from pyfix360.services import audit, settings_service

#: metric -> human description shown in the UI
METRICS: dict[str, str] = {
    "max_severity_count": "count of findings at or above a severity",
    "max_total_findings": "total number of findings",
    "max_critical": "number of critical findings",
    "required_tools": "tools that must have run successfully",
    "test_outcome": "the most recent test run for this project",
    "no_new_findings": "findings that did not exist in the previous scan",
}

OPERATORS = ("<=", "<", "==", ">=", ">")

DEFAULT_GATES: tuple[dict, ...] = (
    {"name": "No critical findings", "metric": "max_critical", "operator": "<=",
     "threshold": 0, "blocking": True},
    {"name": "All requested tools ran", "metric": "required_tools",
     "operator": "==", "threshold": 0, "blocking": False,
     "params": {"tools": ["ruff"]}},
    {"name": "No new critical findings", "metric": "no_new_findings",
     "operator": "<=", "threshold": 0, "severity": "critical", "blocking": False},
)


# --------------------------------------------------------------------------------------
# CRUD
# --------------------------------------------------------------------------------------


def create_gate(session: Session, gate: QualityGate, *,
                confirmed: bool = False) -> QualityGateRow:
    """Add a quality gate.

    Gates decide whether a run is allowed to pass, so editing them is gated by
    the ``edit_quality_gates`` capability at the service boundary.
    """
    settings_service.check_access(session, "edit_quality_gates", confirmed=confirmed)
    row = QualityGateRow(
        project_id=gate.project_id,
        name=gate.name or "unnamed gate",
        metric=gate.metric,
        operator=gate.operator,
        threshold=gate.threshold,
        severity=gate.severity.value,
        params=dict(gate.params),
        enabled=gate.enabled,
        blocking=gate.blocking,
    )
    session.add(row)
    session.flush()
    audit.record(session, "gate.create", target=f"gate#{row.id}",
                 project_id=gate.project_id, actor="user",
                 details=f"{row.name} metric={row.metric}")
    return row


def list_gates(session: Session, project_id: int | None = None) -> list[QualityGateRow]:
    stmt = select(QualityGateRow).order_by(QualityGateRow.id)
    rows = list(session.scalars(stmt))
    if project_id is None:
        return rows
    # project-specific gates plus the global ones (project_id is NULL)
    return [r for r in rows if r.project_id in (None, project_id)]


def to_model(row: QualityGateRow) -> QualityGate:
    return QualityGate(
        id=row.id, project_id=row.project_id, name=row.name, metric=row.metric,
        operator=row.operator, threshold=row.threshold,
        severity=Severity(row.severity) if row.severity in
        {s.value for s in Severity} else Severity.CRITICAL,
        params=dict(row.params or {}), enabled=row.enabled, blocking=row.blocking,
        created_at=row.created_at,
    )


def update_gate(session: Session, gate_id: int, *,
                confirmed: bool = False, **changes) -> QualityGateRow | None:
    settings_service.check_access(session, "edit_quality_gates", confirmed=confirmed)
    row = session.get(QualityGateRow, gate_id)
    if row is None:
        return None
    for key, value in changes.items():
        if key == "severity" and isinstance(value, Severity):
            value = value.value
        if hasattr(row, key):
            setattr(row, key, value)
    audit.record(session, "gate.update", target=f"gate#{gate_id}", actor="user",
                 details=",".join(sorted(changes)))
    session.flush()
    return row


def delete_gate(session: Session, gate_id: int, *, confirmed: bool = False) -> None:
    settings_service.check_access(session, "edit_quality_gates", confirmed=confirmed)
    row = session.get(QualityGateRow, gate_id)
    if row is None:
        return
    session.delete(row)
    audit.record(session, "gate.delete", target=f"gate#{gate_id}", actor="user",
                 risk="warn")
    session.flush()


def seed_defaults(session: Session, project_id: int | None = None, *,
                  confirmed: bool = False) -> list[QualityGateRow]:
    """Install the starter gates for a project (idempotent by name)."""
    existing = {r.name for r in list_gates(session, project_id)}
    created = []
    for spec in DEFAULT_GATES:
        if spec["name"] in existing:
            continue
        gate = QualityGate(
            project_id=project_id,
            name=spec["name"],
            metric=spec["metric"],
            operator=spec["operator"],
            threshold=spec["threshold"],
            severity=Severity(spec.get("severity", "critical")),
            params=spec.get("params", {}),
            blocking=spec.get("blocking", True),
        )
        created.append(create_gate(session, gate, confirmed=confirmed))
    return created


# --------------------------------------------------------------------------------------
# evaluation
# --------------------------------------------------------------------------------------


def _compare(observed: int, operator: str, threshold: int) -> bool:
    return {
        "<=": observed <= threshold,
        "<": observed < threshold,
        "==": observed == threshold,
        ">=": observed >= threshold,
        ">": observed > threshold,
    }.get(operator, False)


def _severity_at_or_above(session: Session, scan_id: int, severity: Severity) -> list[FindingRow]:
    order = ["info", "low", "medium", "high", "critical"]
    floor = order.index(severity.value)
    allowed = set(order[floor:])
    return [
        f for f in session.scalars(
            select(FindingRow).where(FindingRow.scan_id == scan_id)
        ) if f.severity in allowed
    ]


def _evaluate_one(session: Session, gate: QualityGateRow, scan: ScanRow,
                  previous_scan_id: int | None) -> GateResult:
    metric = gate.metric
    severity = Severity(gate.severity) if gate.severity in {s.value for s in Severity} \
        else Severity.CRITICAL

    if metric in ("max_critical", "max_severity_count"):
        sev = Severity.CRITICAL if metric == "max_critical" else severity
        findings = _severity_at_or_above(session, scan.id, sev)
        observed = len(findings)
        passed = _compare(observed, gate.operator, gate.threshold)
        return GateResult(
            gate_id=gate.id, name=gate.name, passed=passed, metric=metric,
            observed=str(observed),
            expected=f"{gate.operator} {gate.threshold}",
            blocking=gate.blocking,
            detail=f"{observed} finding(s) at or above {sev.value}",
            finding_ids=[f.id for f in findings][:200],
        )

    if metric == "max_total_findings":
        observed = scan.findings_count
        passed = _compare(observed, gate.operator, gate.threshold)
        return GateResult(
            gate_id=gate.id, name=gate.name, passed=passed, metric=metric,
            observed=str(observed), expected=f"{gate.operator} {gate.threshold}",
            blocking=gate.blocking,
            detail=f"scan recorded {observed} findings",
        )

    if metric == "required_tools":
        required = [t for t in (gate.params or {}).get("tools", []) if t]
        ran = set(scan.tools_ran or [])
        missing = [t for t in required if t not in ran]
        passed = not missing
        return GateResult(
            gate_id=gate.id, name=gate.name, passed=passed, metric=metric,
            observed=", ".join(sorted(ran)) or "(none)",
            expected=", ".join(required) or "(none required)",
            blocking=gate.blocking,
            detail="all required tools ran" if passed
            else f"missing: {', '.join(missing)} (scan status {scan.status})",
        )

    if metric == "test_outcome":
        from pyfix360.db.models import TestRunRow

        row = session.scalars(
            select(TestRunRow)
            .where(TestRunRow.project_id == scan.project_id)
            .order_by(TestRunRow.id.desc())
        ).first()
        expected = (gate.params or {}).get("clause", "passed")
        if row is None:
            return GateResult(
                gate_id=gate.id, name=gate.name, passed=False, metric=metric,
                observed="no test run", expected=expected, blocking=gate.blocking,
                detail="no test run recorded for this project yet",
            )
        passed = row.outcome == expected
        return GateResult(
            gate_id=gate.id, name=gate.name, passed=passed, metric=metric,
            observed=row.outcome, expected=expected, blocking=gate.blocking,
            detail=f"test run #{row.id} ({row.phase}) outcome {row.outcome}",
        )

    if metric == "no_new_findings":
        if previous_scan_id is None:
            return GateResult(
                gate_id=gate.id, name=gate.name, passed=True, metric=metric,
                observed="no previous scan", expected=f"{gate.operator} {gate.threshold}",
                blocking=gate.blocking,
                detail="first scan for this project — nothing to compare against",
            )
        previous_keys = {
            f.stable_key for f in session.scalars(
                select(FindingRow).where(FindingRow.scan_id == previous_scan_id))
        }
        current = _severity_at_or_above(session, scan.id, severity)
        new = [f for f in current if f.stable_key not in previous_keys]
        observed = len(new)
        passed = _compare(observed, gate.operator, gate.threshold)
        return GateResult(
            gate_id=gate.id, name=gate.name, passed=passed, metric=metric,
            observed=str(observed), expected=f"{gate.operator} {gate.threshold}",
            blocking=gate.blocking,
            detail=f"{observed} new {severity.value}+ finding(s) since scan "
                   f"#{previous_scan_id}",
            finding_ids=[f.id for f in new][:200],
        )

    return GateResult(
        gate_id=gate.id, name=gate.name, passed=False, metric=metric,
        observed="?", expected="unknown metric", blocking=gate.blocking,
        detail=f"unsupported metric '{metric}' — edit the gate",
    )


def evaluate(session: Session, project_id: int, scan_id: int | None = None) -> GateReport:
    """Evaluate every enabled gate against a scan."""
    scan = None
    if scan_id is not None:
        scan = session.get(ScanRow, scan_id)
    if scan is None:
        scan = session.scalars(
            select(ScanRow).where(ScanRow.project_id == project_id)
            .order_by(ScanRow.id.desc())
        ).first()
    if scan is None:
        return GateReport(project_id=project_id, scan_id=None, results=[])

    previous = session.scalars(
        select(ScanRow)
        .where(ScanRow.project_id == project_id, ScanRow.id < scan.id)
        .order_by(ScanRow.id.desc())
    ).first()

    results = [
        _evaluate_one(session, gate, scan, previous.id if previous else None)
        for gate in list_gates(session, project_id) if gate.enabled
    ]
    return GateReport(project_id=project_id, scan_id=scan.id, results=results)


def summary_line(report: GateReport) -> str:
    if not report.results:
        return "no gates configured"
    failed = report.failed_blocking
    if not failed:
        advisory = [r for r in report.results if not r.blocking and not r.passed]
        base = f"{len(report.results)} gate(s) passed"
        return base + (f"; {len(advisory)} advisory warning(s)" if advisory else "")
    return (f"{len(failed)} blocking gate(s) FAILED: "
            + ", ".join(r.name for r in failed))


def project_summary(session: Session, project: Project) -> str:
    if project.id is None:
        return "no project"
    try:
        return summary_line(evaluate(session, project.id))
    except Exception as exc:  # pragma: no cover - defensive
        return f"gate evaluation error: {exc}"
