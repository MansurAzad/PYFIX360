"""Shared enums and Pydantic domain models used across the application.

These models are the transport layer between the engine, services, UI and CLI.
Database rows are mapped into these models at service boundaries.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------------------


class Severity(str, enum.Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @property
    def rank(self) -> int:
        return {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}[self.value]


class FindingCategory(str, enum.Enum):
    SYNTAX = "syntax"
    runtime = "runtime"
    logic = "logic"
    security = "security"
    performance = "performance"
    architecture = "architecture"
    dependency = "dependency"
    style = "style"
    completeness = "completeness"
    test = "test"


class Confidence(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FindingStatus(str, enum.Enum):
    DETECTED = "detected"          # issue confirmed present
    ACKNOWLEDGED = "acknowledged"  # user has seen / accepted
    PLANNED = "planned"            # included in a repair plan
    FIX_PROPOSED = "fix_proposed"  # a patch exists awaiting review
    FIX_APPLIED = "fix_applied"    # patch applied, verification pending
    VERIFIED = "verified"          # applied AND verified by checks/tests
    FAILED = "failed"              # attempted repair failed validation/tests
    ROLLED_BACK = "rolled_back"    # repair was reverted
    UNRESOLVED = "unresolved"      # could not be fixed automatically
    FALSE_POSITIVE = "false_positive"


class ScanStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"      # finished but some tools/files were skipped
    FAILED = "failed"
    CANCELLED = "cancelled"


class PatchStatus(str, enum.Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    APPLIED = "applied"
    VERIFIED = "verified"
    REJECTED = "rejected"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class TestOutcome(str, enum.Enum):
    __test__ = False  # not a pytest test class

    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"
    NOT_RUN = "not_run"


class ProjectOrigin(str, enum.Enum):
    FOLDER = "folder"
    ZIP = "zip"
    GIT = "git"


class ProviderType(str, enum.Enum):
    NONE = "none"            # local-only mode, no remote AI
    LOCAL_RUNTIME = "local_runtime"  # Ollama / llama.cpp / LM Studio (OpenAI-compatible local endpoint)
    OPENROUTER = "openrouter"
    CUSTOM_OPENAI_COMPAT = "custom_openai_compat"


class ThemeKey(str, enum.Enum):
    DARK = "dark"
    MIDNIGHT = "midnight"
    FOREST = "forest"
    LIGHT = "light"
    HIGH_CONTRAST = "high_contrast"


class Density(str, enum.Enum):
    COMPACT = "compact"
    COMFORTABLE = "comfortable"
    SPACIOUS = "spacious"


class AccessLevel(str, enum.Enum):
    """Coarse permission level for a capability.

    These are enforced at the service boundary, not merely stored — the UI
    reads them to grey out actions, and the services refuse the operation.
    """

    FULL = "full"            # allowed without prompting
    ASK = "ask"              # allowed only after an explicit confirmation
    BLOCKED = "blocked"      # refused outright


class AIBehavior(str, enum.Enum):
    OFF = "off"                        # AI never used automatically
    SUGGEST = "suggest"                # AI may propose; user always decides
    EXPLAIN_ONLY = "explain_only"      # AI may explain but not propose patches



class PrivacyMode(str, enum.Enum):
    LOCAL_ONLY = "local_only"          # never transmit code
    REDACTED_REMOTE = "redacted_remote"  # transmit only redacted snippets with consent
    FULL_REMOTE_APPROVED = "full_remote_approved"  # user approved full remote processing


class JobStatus(str, enum.Enum):
    """Where a background job stands, as seen by a process that did not run it.

    ``WAITING_APPROVAL`` is the only state a job can hold indefinitely: a step
    reached a capability set to ASK and the daemon will not answer for the
    operator. ``INTERRUPTED`` means the host stopped reporting — it is deliberately
    not called "failed", because nobody knows whether the work was half-applied.
    """

    __test__ = False  # not a pytest test class

    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


# --------------------------------------------------------------------------------------
# Core domain models
# --------------------------------------------------------------------------------------


class Model(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Evidence(Model):
    id: int | None = None
    finding_id: int | None = None
    kind: str = "source"          # source | tool_output | test_output | doc
    file_path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    snippet: str | None = None
    excerpt: str | None = None    # human-readable explanation of why this is evidence


class Finding(Model):
    id: int | None = None
    scan_id: int | None = None
    project_id: int | None = None
    stable_key: str               # stable deduplicated identifier
    rule_id: str
    category: FindingCategory
    severity: Severity
    confidence: Confidence
    status: FindingStatus = FindingStatus.DETECTED
    title: str
    description: str = ""
    file_path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    symbol: str | None = None     # enclosing function/class
    tool: str = "pyfix360"
    root_cause_group: str | None = None
    dedup_count: int = 1
    evidence: list[Evidence] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)


class ProjectFile(Model):
    id: int | None = None
    project_id: int | None = None
    rel_path: str
    language: str | None = None
    size_bytes: int = 0
    sha256: str = ""
    is_test: bool = False
    is_generated: bool = False


class Dependency(Model):
    id: int | None = None
    project_id: int | None = None
    name: str
    declared_version: str | None = None
    ecosystem: str = "pypi"      # pypi | npm | maven | go | cargo | other
    source_file: str | None = None


class ModuleInfo(Model):
    id: int | None = None
    project_id: int | None = None
    rel_path: str
    kind: str = "module"          # module | package | class | function
    name: str = ""
    qualified_name: str = ""
    line_start: int | None = None
    line_end: int | None = None


class ProjectFacts(Model):
    """Detected facts about an imported project."""

    languages: list[str] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)
    build_systems: list[str] = Field(default_factory=list)
    entry_points: list[str] = Field(default_factory=list)
    package_managers: list[str] = Field(default_factory=list)
    test_harnesses: list[str] = Field(default_factory=list)


class Project(Model):
    id: int | None = None
    name: str
    root_path: str
    origin: ProjectOrigin = ProjectOrigin.FOLDER
    imported_at: datetime = Field(default_factory=utcnow)
    last_scan_id: int | None = None
    facts: ProjectFacts = Field(default_factory=ProjectFacts)
    file_count: int = 0


class Scan(Model):
    id: int | None = None
    project_id: int | None = None
    status: ScanStatus = ScanStatus.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    tools_requested: list[str] = Field(default_factory=list)
    tools_ran: list[str] = Field(default_factory=list)
    tools_skipped: list[str] = Field(default_factory=list)  # "tool: reason"
    files_scanned: int = 0
    files_skipped: int = 0
    findings_count: int = 0
    message: str = ""
    progress: float = 0.0


class RepairPlanStep(Model):
    order: int = 0
    action: str
    detail: str = ""
    risk: str = "low"            # low | medium | high
    requires_approval: bool = False


class RepairPlan(Model):
    id: int | None = None
    project_id: int | None = None
    scan_id: int | None = None
    finding_ids: list[int] = Field(default_factory=list)
    title: str
    rationale: str = ""
    steps: list[RepairPlanStep] = Field(default_factory=list)
    estimated_risk: str = "low"
    created_at: datetime = Field(default_factory=utcnow)


class Patch(Model):
    id: int | None = None
    repair_plan_id: int | None = None
    project_id: int | None = None
    finding_id: int | None = None
    status: PatchStatus = PatchStatus.PROPOSED
    file_path: str                # project-relative path the patch modifies
    diff: str = ""                # unified diff
    added_lines: int = 0
    removed_lines: int = 0
    risk: str = "low"
    origin: str = "builtin"       # builtin | ruff | ai
    explanation: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class TestCaseResult(Model):
    node_id: str = ""
    outcome: TestOutcome = TestOutcome.NOT_RUN
    duration_s: float = 0.0
    message: str | None = None


class TestRun(Model):
    id: int | None = None
    project_id: int | None = None
    scan_id: int | None = None
    patch_id: int | None = None
    phase: str = "baseline"       # baseline | post_patch | adhoc
    command: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    duration_s: float = 0.0
    outcome: TestOutcome = TestOutcome.NOT_RUN
    log_excerpt: str = ""
    results: list[TestCaseResult] = Field(default_factory=list)


class Snapshot(Model):
    id: int | None = None
    project_id: int | None = None
    label: str
    kind: str = "archive"         # archive | git
    path: str = ""
    git_commit: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    restored_at: datetime | None = None


class RepairResult(Model):
    """Final outcome of executing a repair plan."""

    plan: RepairPlan
    patch: Patch | None = None
    snapshot: Snapshot | None = None
    baseline_test_run: TestRun | None = None
    post_test_run: TestRun | None = None
    status: PatchStatus = PatchStatus.PROPOSED
    verification: str = "not_run"   # not_run | passed | failed | inconclusive
    message: str = ""
    audit_event_id: int | None = None


class AuditEvent(Model):
    id: int | None = None
    project_id: int | None = None
    timestamp: datetime = Field(default_factory=utcnow)
    actor: str = "system"          # user | system | ai
    action: str
    target: str | None = None
    details: str = ""
    risk: str = "info"


class AIProviderConfig(Model):
    id: int | None = None
    name: str
    provider_type: ProviderType = ProviderType.NONE
    base_url: str = ""
    model: str = ""
    timeout_s: int = 60
    retry_limit: int = 2
    privacy_mode: PrivacyMode = PrivacyMode.LOCAL_ONLY
    enabled: bool = False
    has_api_key: bool = False      # actual key lives in the credential store
    catalog_key: str = ""          # catalogue preset this came from
    discovered_models: list[str] = Field(default_factory=list)
    last_verify_ok: bool | None = None
    last_verified_at: datetime | None = None


class QualityGate(Model):
    """A declarative rule the latest scan must satisfy."""

    id: int | None = None
    project_id: int | None = None      # None = applies to every project
    name: str = ""
    metric: str = "max_severity_count"
    operator: str = "<="
    threshold: int = 0
    severity: Severity = Severity.CRITICAL
    params: dict = Field(default_factory=dict)
    enabled: bool = True
    blocking: bool = True
    created_at: datetime = Field(default_factory=utcnow)


class GateResult(Model):
    """Outcome of evaluating one gate against one scan."""

    gate_id: int | None = None
    name: str = ""
    passed: bool = True
    metric: str = ""
    observed: str = ""
    expected: str = ""
    blocking: bool = True
    detail: str = ""
    finding_ids: list[int] = Field(default_factory=list)


class GateReport(Model):
    project_id: int | None = None
    scan_id: int | None = None
    results: list[GateResult] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results if r.blocking)

    @property
    def failed_blocking(self) -> list[GateResult]:
        return [r for r in self.results if r.blocking and not r.passed]


class AIChatTurn(Model):
    id: int | None = None
    project_id: int | None = None
    provider_id: int | None = None
    role: str = "user"
    purpose: str = "chat"
    content: str = ""
    prompt_sha256: str = ""
    redactions_applied: int = 0
    remote: bool = False
    latency_ms: int | None = None
    created_at: datetime = Field(default_factory=utcnow)


class DiscoveredModelInfo(Model):
    """A model id a provider itself reported, with our annotation."""

    id: str
    owned_by: str = ""
    context_k: int = 0
    annotation: str = ""



class AIRequestMeta(Model):
    id: int | None = None
    provider_id: int | None = None
    purpose: str = ""              # explain | propose_repair | summarize
    model: str = ""
    prompt_sha256: str = ""
    redactions_applied: int = 0
    remote_allowed: bool = False
    status: str = ""
    latency_ms: int | None = None
    created_at: datetime = Field(default_factory=utcnow)


class AppSettings(Model):
    """Everything the user can configure, in one place.

    Grouped to mirror the Settings Center's tabs. The model is deliberately
    flat so it maps cleanly onto the key/value settings table.
    """

    # --- execution & sandbox ---
    isolate_execution: bool = True
    execution_timeout_s: int = 300
    memory_limit_mb: int = 2048
    allow_network_in_sandbox: bool = False
    max_repair_retries: int = 2
    auto_commit_verified: bool = False

    # --- scanning ---
    scan_include_globs: list[str] = Field(default_factory=list)
    scan_exclude_globs: list[str] = Field(default_factory=list)
    scan_skip_generated: bool = True
    scan_max_file_kb: int = 2048
    scan_max_files: int = 20000
    scan_parallel_workers: int = 0          # 0 = auto
    scan_incremental: bool = True
    enabled_analyzers: list[str] = Field(
        default_factory=lambda: ["builtin", "ruff", "bandit", "pip-audit"])

    # --- findings & display ---
    min_severity: Severity = Severity.INFO
    auto_acknowledge_info: bool = False
    group_by_root_cause: bool = True
    show_false_positive_rules: bool = False

    # --- AI ---
    redact_before_ai: bool = True
    privacy_mode: PrivacyMode = PrivacyMode.LOCAL_ONLY
    ai_behavior: AIBehavior = AIBehavior.SUGGEST
    ai_default_provider_id: int | None = None
    ai_auto_explain_findings: bool = False
    ai_max_prompt_chars: int = 24000
    ai_allow_repo_content: bool = False     # extra gate before source is sent

    # --- repair & safety ---
    require_snapshot_before_write: bool = True
    require_approval_destructive: bool = True
    require_approval_security: bool = True
    require_approval_dependency: bool = True
    require_approval_migration: bool = True
    require_approval_production: bool = True
    block_write_outside_project: bool = True
    backup_retention_days: int = 30
    dry_run_by_default: bool = True

    # --- access control (enforced in services) ---
    access_apply_to_original: AccessLevel = AccessLevel.ASK
    access_rollback: AccessLevel = AccessLevel.FULL
    access_delete_project: AccessLevel = AccessLevel.ASK
    access_purge_history: AccessLevel = AccessLevel.BLOCKED
    access_run_tests: AccessLevel = AccessLevel.FULL
    access_edit_quality_gates: AccessLevel = AccessLevel.FULL
    access_remote_ai: AccessLevel = AccessLevel.ASK
    access_export_reports: AccessLevel = AccessLevel.FULL
    access_plugin_tools: AccessLevel = AccessLevel.ASK
    access_autopilot: AccessLevel = AccessLevel.ASK
    access_danger_zone: AccessLevel = AccessLevel.BLOCKED
    access_check_for_updates: AccessLevel = AccessLevel.ASK

    # --- autopilot ---
    autopilot_gesture_window_ms: int = 500

    # --- custom commands ---
    test_command_override: str = ""
    custom_tool_paths: dict[str, str] = Field(default_factory=dict)

    # --- appearance ---
    theme: ThemeKey = ThemeKey.DARK
    accent_override: str = ""               # "" = use the theme's own accent
    font_size: int = 13
    font_family: str = ""
    density: Density = Density.COMFORTABLE
    corner_radius: int = 6
    motion_enabled: bool = True
    show_status_bar: bool = True
    sidebar_width: int = 210
    start_screen: str = "Overview"

    # --- notifications & logs ---
    notify_on_scan_complete: bool = True
    notify_on_repair_verified: bool = True
    log_level: str = "INFO"
    log_retention_days: int = 14

    # --- updates ---
    update_auto_install: bool = False       # install a new version without asking

    # --- background / headless ---
    tray_enabled: bool = True              # notification-area icon while running
    close_hides_to_tray: bool = True       # the close button hides instead of quitting
    service_poll_ms: int = 2000            # how often the background host checks for work

    # --- runtime-managed ---
    data_dir: str = ""


class BackgroundJob(Model):
    """One queued unit of work for the process that outlives the window.

    Read by the GUI and the CLI to report what a *different* process is doing, so
    everything here is a snapshot rather than a live handle. ``steps`` is the
    autopilot queue rendered for readers; the host that owns the job is the only
    writer of it.
    """

    id: int | None = None
    project_id: int | None = None
    kind: str = "autopilot"
    status: JobStatus = JobStatus.QUEUED
    detail: str = ""
    error: str = ""
    steps: list[dict] = Field(default_factory=list)
    confirmed: bool = False
    cancel_requested: bool = False
    resume_action: str = ""
    pid: int | None = None
    host: str = ""
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def progress(self) -> tuple[int, int]:
        """Steps closed and steps total — the in-flight one counts as outstanding."""
        total = len(self.steps)
        done = sum(1 for s in self.steps if s.get("status") in
                   ("done", "failed", "skipped", "refused"))
        return done, total

    @property
    def open(self) -> bool:
        """True while a host may still be working on it."""
        return self.status in (JobStatus.QUEUED, JobStatus.RUNNING,
                               JobStatus.WAITING_APPROVAL)

    def heartbeat_age_s(self, now: datetime | None = None) -> float | None:
        """Seconds since the owner last reported — ``None`` if it never ran."""
        if self.updated_at is None:
            return None
        now = now or utcnow()
        stamp = self.updated_at
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        return (now - stamp).total_seconds()

    def summary_line(self) -> str:
        done, total = self.progress
        who = f"project #{self.project_id}" if self.project_id else "no project"
        bits = [f"#{self.id} {self.kind} {self.status.value} ({done}/{total})", who]
        if self.status is JobStatus.WAITING_APPROVAL:
            bits.append("waiting for you")
        elif self.cancel_requested and self.open:
            bits.append("cancel requested")
        if self.detail:
            bits.append(self.detail)
        return " — ".join(bits)

