"""SQLAlchemy ORM models.

One table per concept listed in the master specification. Rows are converted
to Pydantic domain models at service boundaries — the ORM layer never leaks
into the UI or CLI.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# JSON columns hold lists/dicts that services mutate in place (e.g.
# ``scan.tools_ran.append(...)``). A bare ``JSON`` column is not change-tracked
# for in-place mutation, so those edits would be silently dropped on commit.
# Wrapping the type in a Mutable* variant makes SQLAlchemy detect them.
JSONList = MutableList.as_mutable(JSON)
JSONDict = MutableDict.as_mutable(JSON)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class ProjectRow(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    root_path: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(String(32), default="folder")
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_scan_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    facts: Mapped[dict] = mapped_column(JSONDict, default=dict)
    file_count: Mapped[int] = mapped_column(Integer, default=0)

    files: Mapped[list[ProjectFileRow]] = relationship(back_populates="project", cascade="all, delete-orphan")
    scans: Mapped[list[ScanRow]] = relationship(back_populates="project", cascade="all, delete-orphan")


class ProjectFileRow(Base):
    __tablename__ = "project_files"
    __table_args__ = (UniqueConstraint("project_id", "rel_path", name="uq_file_path"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    rel_path: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    is_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    last_scan_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_scan_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    project: Mapped[ProjectRow] = relationship(back_populates="files")


class ScanRow(Base):
    __tablename__ = "scans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tools_requested: Mapped[list] = mapped_column(JSONList, default=list)
    tools_ran: Mapped[list] = mapped_column(JSONList, default=list)
    tools_skipped: Mapped[list] = mapped_column(JSONList, default=list)
    files_scanned: Mapped[int] = mapped_column(Integer, default=0)
    files_skipped: Mapped[int] = mapped_column(Integer, default=0)
    findings_count: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")
    progress: Mapped[float] = mapped_column(Float, default=0.0)

    project: Mapped[ProjectRow] = relationship(back_populates="scans")
    findings: Mapped[list[FindingRow]] = relationship(back_populates="scan", cascade="all, delete-orphan")


class FindingRow(Base):
    __tablename__ = "findings"
    __table_args__ = (UniqueConstraint("scan_id", "stable_key", name="uq_finding_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("scans.id", ondelete="CASCADE"))
    project_id: Mapped[int] = mapped_column(Integer)
    stable_key: Mapped[str] = mapped_column(String(64), index=True)
    rule_id: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16), index=True)
    confidence: Mapped[str] = mapped_column(String(16), default="medium")
    status: Mapped[str] = mapped_column(String(32), default="detected", index=True)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    file_path: Mapped[str | None] = mapped_column(Text)
    line_start: Mapped[int | None] = mapped_column(Integer)
    line_end: Mapped[int | None] = mapped_column(Integer)
    symbol: Mapped[str | None] = mapped_column(Text)
    tool: Mapped[str] = mapped_column(String(64), default="pyfix360")
    root_cause_group: Mapped[str | None] = mapped_column(String(64), index=True)
    dedup_count: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    scan: Mapped[ScanRow] = relationship(back_populates="findings")
    evidence: Mapped[list[EvidenceRow]] = relationship(back_populates="finding", cascade="all, delete-orphan")


class EvidenceRow(Base):
    __tablename__ = "finding_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    finding_id: Mapped[int] = mapped_column(ForeignKey("findings.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32), default="source")
    file_path: Mapped[str | None] = mapped_column(Text)
    line_start: Mapped[int | None] = mapped_column(Integer)
    line_end: Mapped[int | None] = mapped_column(Integer)
    snippet: Mapped[str | None] = mapped_column(Text)
    excerpt: Mapped[str | None] = mapped_column(Text)

    finding: Mapped[FindingRow] = relationship(back_populates="evidence")


class DependencyRow(Base):
    __tablename__ = "dependencies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(256))
    declared_version: Mapped[str | None] = mapped_column(String(128))
    ecosystem: Mapped[str] = mapped_column(String(32), default="pypi")
    source_file: Mapped[str | None] = mapped_column(Text)


class ModuleRow(Base):
    __tablename__ = "modules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    rel_path: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(32), default="module")
    name: Mapped[str] = mapped_column(Text, default="")
    qualified_name: Mapped[str] = mapped_column(Text, default="")
    line_start: Mapped[int | None] = mapped_column(Integer)
    line_end: Mapped[int | None] = mapped_column(Integer)


class FeatureRow(Base):
    __tablename__ = "features"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="unknown")  # complete|partial|missing|unknown
    evidence_paths: Mapped[list] = mapped_column(JSONList, default=list)
    confidence: Mapped[str] = mapped_column(String(16), default="low")


class FeatureCheckRow(Base):
    __tablename__ = "feature_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    feature_id: Mapped[int] = mapped_column(ForeignKey("features.id", ondelete="CASCADE"))
    check_kind: Mapped[str] = mapped_column(String(32))   # code|test|docs|criteria
    description: Mapped[str] = mapped_column(Text)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    detail: Mapped[str] = mapped_column(Text, default="")


class RepairPlanRow(Base):
    __tablename__ = "repair_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    scan_id: Mapped[int | None] = mapped_column(Integer)
    finding_ids: Mapped[list] = mapped_column(JSONList, default=list)
    title: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(Text, default="")
    steps: Mapped[list] = mapped_column(JSONList, default=list)
    estimated_risk: Mapped[str] = mapped_column(String(16), default="low")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PatchRow(Base):
    __tablename__ = "patches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repair_plan_id: Mapped[int | None] = mapped_column(ForeignKey("repair_plans.id", ondelete="SET NULL"))
    project_id: Mapped[int] = mapped_column(Integer)
    finding_id: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="proposed", index=True)
    file_path: Mapped[str] = mapped_column(Text)
    diff: Mapped[str] = mapped_column(Text, default="")
    added_lines: Mapped[int] = mapped_column(Integer, default=0)
    removed_lines: Mapped[int] = mapped_column(Integer, default=0)
    risk: Mapped[str] = mapped_column(String(16), default="low")
    origin: Mapped[str] = mapped_column(String(32), default="builtin")
    explanation: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TestCaseRow(Base):
    __tablename__ = "test_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    test_run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"))
    node_id: Mapped[str] = mapped_column(Text, default="")
    outcome: Mapped[str] = mapped_column(String(16), default="not_run")
    duration_s: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str | None] = mapped_column(Text)

    run: Mapped[TestRunRow] = relationship(back_populates="results")


class TestRunRow(Base):
    __tablename__ = "test_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer)
    scan_id: Mapped[int | None] = mapped_column(Integer)
    patch_id: Mapped[int | None] = mapped_column(Integer)
    phase: Mapped[str] = mapped_column(String(16), default="baseline")
    command: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    passed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    duration_s: Mapped[float] = mapped_column(Float, default=0.0)
    outcome: Mapped[str] = mapped_column(String(16), default="not_run")
    log_excerpt: Mapped[str] = mapped_column(Text, default="")

    results: Mapped[list[TestCaseRow]] = relationship(back_populates="run", cascade="all, delete-orphan")


class SnapshotRow(Base):
    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    label: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16), default="archive")
    path: Mapped[str] = mapped_column(Text)
    git_commit: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    restored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEventRow(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(Integer, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor: Mapped[str] = mapped_column(String(16), default="system")
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str | None] = mapped_column(Text)
    details: Mapped[str] = mapped_column(Text, default="")
    risk: Mapped[str] = mapped_column(String(16), default="info")


class AIProviderRow(Base):
    __tablename__ = "ai_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    provider_type: Mapped[str] = mapped_column(String(32), default="none")
    base_url: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(Text, default="")
    timeout_s: Mapped[int] = mapped_column(Integer, default=60)
    retry_limit: Mapped[int] = mapped_column(Integer, default=2)
    privacy_mode: Mapped[str] = mapped_column(String(32), default="local_only")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    #: which catalogue preset this row came from ("" for hand-made rows)
    catalog_key: Mapped[str] = mapped_column(String(64), default="")
    #: model ids the provider itself reported via /models (JSON list)
    discovered_models: Mapped[list] = mapped_column(JSONList, default=list)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_verify_ok: Mapped[bool | None] = mapped_column(Boolean)


class AIRequestRow(Base):
    __tablename__ = "ai_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_id: Mapped[int | None] = mapped_column(Integer)
    purpose: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(Text, default="")
    prompt_sha256: Mapped[str] = mapped_column(String(64), default="")
    redactions_applied: Mapped[int] = mapped_column(Integer, default=0)
    remote_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(32), default="")
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SettingRow(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class QualityGateRow(Base):
    """A project- or global-scope rule that a scan result must satisfy.

    Gates are declarative: the scan service evaluates them against the latest
    scan and reports pass/fail with the exact offending findings, so a failure
    is always explainable rather than a bare boolean.
    """

    __tablename__ = "quality_gates"
    __table_args__ = (Index("ix_quality_gates_project", "project_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    name: Mapped[str] = mapped_column(String(128))
    metric: Mapped[str] = mapped_column(String(32), default="max_severity_count")
    # metric vocabulary:
    #   max_severity_count  -> threshold = allowed count of `severity`
    #   max_total_findings  -> threshold = allowed total
    #   max_critical        -> threshold = allowed critical count
    #   required_tools      -> threshold unused; `params` lists tool names
    #   test_outcome        -> params.clause = passed
    operator: Mapped[str] = mapped_column(String(8), default="<=")
    threshold: Mapped[int] = mapped_column(Integer, default=0)
    severity: Mapped[str] = mapped_column(String(16), default="critical")
    params: Mapped[dict] = mapped_column(JSONDict, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    blocking: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AIChatRow(Base):
    """One turn of the optional AI helper conversation.

    Only the *redacted* text is ever stored, and the raw prompt is never
    persisted — ``prompt_sha256`` is recorded for the audit trail instead.
    """

    __tablename__ = "ai_chats"
    __table_args__ = (Index("ix_ai_chats_project", "project_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(Integer)
    provider_id: Mapped[int | None] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16), default="user")
    purpose: Mapped[str] = mapped_column(String(32), default="chat")
    content: Mapped[str] = mapped_column(Text, default="")
    prompt_sha256: Mapped[str] = mapped_column(String(64), default="")
    redactions_applied: Mapped[int] = mapped_column(Integer, default=0)
    remote: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BackgroundJobRow(Base):
    """One unit of work handed to a process that outlives the window.

    The row is the *only* channel between the GUI and the background host: they
    are different processes, sometimes different Windows sessions, so nothing is
    passed by handle. That is why the operator's answers travel as columns
    (``resume_action``, ``cancel_requested``) rather than as method calls, and
    why ``updated_at`` doubles as a heartbeat — a crashed host must not leave a
    job looking alive forever.

    ``steps`` is a snapshot of the autopilot queue for readers only. The host
    owns the real state machine; the table never drives it.
    """

    __tablename__ = "background_jobs"
    __table_args__ = (Index("ix_background_jobs_status", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), default="autopilot")
    # queued | running | waiting_approval | done | failed | cancelled | interrupted
    status: Mapped[str] = mapped_column(String(24), default="queued")
    detail: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    steps: Mapped[list] = mapped_column(JSONList, default=list)
    #: The operator already confirmed the ``autopilot`` capability when this was
    #: queued. Without it the host refuses to start an ASK-gated run — a daemon
    #: must not satisfy a confirmation simply by having nobody around to ask.
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    # "" | approve | skip — the out-of-band answer to a parked approval gate
    resume_action: Mapped[str] = mapped_column(String(8), default="")
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    host: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: stamped by the host on every poll while the job is its own
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
