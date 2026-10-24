# PYFIX 360 — Data Model & Lifecycle Reference

Reference for the persisted schema and the finding/repair lifecycle. Application code
should use the Pydantic models in `pyfix360.models`; ORM rows never cross the service
boundary into the UI or CLI.

---

## 1. Entity overview

```
Project
 ├── ProjectFile        (registered files, language, flags, last_scan_id/hash)
 ├── Module             (parsed symbols: module/class/function)
 ├── Dependency         (declared deps from requirements/pyproject/package.json)
 ├── Scan               (one analysis pass: status, tools ran/skipped, counts)
 │    └── Finding
 │         ├── Evidence (file, line range, snippet, why-flagged)
 │         └── (root_cause_group links Findings)
 ├── Feature
 │    └── FeatureCheck  (code/test/docs/criteria evidence)
 ├── RepairPlan         (steps, risk, rationale, finding ids)
 │    └── Patch         (diff, status, risk, origin, snapshot link)
 ├── TestRun            (phase, outcome, counts, output excerpt)
 │    └── TestCase      (node id, outcome, message)
 ├── Snapshot           (recoverable project copy, restored_at)
 └── AuditEvent         (actor, risk, action, target, details)

AIProvider ── AIRequest   (provider, purpose, prompt_sha256, redactions, remote_allowed)
           ── AIChat      (redacted-only conversation history)
QualityGate              (metric, operator, threshold, severity, params, blocking)
Setting                  (key/value application settings)
```

---

## 2. Tables (20)

| Table | Row class | Purpose |
|---|---|---|
| `projects` | `ProjectRow` | Imported project, root path, origin, facts, file/scan counters. |
| `project_files` | `ProjectFileRow` | One row per tracked file; `last_scan_id` + `last_scan_hash` drive incremental scans. |
| `modules` | `ModuleRow` | Parsed symbols with kind and line range. |
| `dependencies` | `DependencyRow` | Declared dependency with ecosystem and source file. |
| `scans` | `ScanRow` | A scan pass: status, tools_ran, tools_skipped, counts, message. |
| `findings` | `FindingRow` | A finding with stable key, severity, confidence, status, dedup count, root-cause group. |
| `finding_evidence` | `EvidenceRow` | Evidence attached to a finding. |
| `features` | `FeatureRow` | A detected/required feature and its status. |
| `feature_checks` | `FeatureCheckRow` | Individual evidence check for a feature. |
| `repair_plans` | `RepairPlanRow` | Planned repair: steps, risk, rationale, finding ids. |
| `patches` | `PatchRow` | A diff with status, risk, origin, snapshot link, line counts. |
| `test_runs` | `TestRunRow` | A test execution: phase, outcome, counts, duration, output excerpt. |
| `test_cases` | `TestCaseRow` | A single test case within a run. |
| `snapshots` | `SnapshotRow` | Recoverable copy of the project; `restored_at` when used. |
| `audit_events` | `AuditEventRow` | Immutable audit trail entry. |
| `ai_providers` | `AIProviderRow` | Provider config: type, endpoint, model, privacy mode, `catalog_key` provenance, `discovered_models`, verification state. No API key — that lives in the credential store. |
| `ai_requests` | `AIRequestRow` | AI request **metadata only** (prompt hash, redaction count, remote flag, status). |
| `ai_chats` | `AIChatRow` | AI conversation history — **redacted text only**, with the prompt SHA-256. |
| `quality_gates` | `QualityGateRow` | A declarative pass/fail rule: metric, operator, threshold, severity/params, blocking flag. Project-scoped (`project_id`) or global (`NULL`). |
| `settings` | `SettingRow` | Key/value application settings. |

### Schema upgrades

`db.session.init_db()` creates missing tables **and** adds missing columns, so an
existing install upgrades in place. It is additive only — it never drops or retypes a
column — and is safe to run on every startup. It also **stamps** the Alembic revision
table at the current head, so `alembic upgrade head` afterwards is a no-op instead of
a "table already exists" failure. The Alembic migrations live in
`src/pyfix360/migrations/versions/`.

### Settings

`settings` is a key/value table. The keys, their types and their defaults are
declared once, in `services/settings_service.DEFAULTS`, and the declared type decides
how a raw string is parsed — so a list setting is stored and read as a JSON list, a
boolean as a boolean, an enum as its enum.

One key is **locked**: `redact_before_ai`. The AI gateway redacts unconditionally, so
a stored `false` would be a value the code could not honour. The store pins it to
`true` and refuses a write that would disable it, rather than accepting a setting
that lies.

---

## 3. Finding lifecycle

```
detected ──► planned ──► fix_proposed ──► fix_applied ──► verified
                                             │
                                             ├──► failed
                                             └──► rolled_back
```

| Status | Meaning |
|---|---|
| `detected` | Found by an analyzer; nothing done yet. |
| `planned` | A repair plan exists for it. |
| `fix_proposed` | A patch has been generated but not applied. |
| `fix_applied` | The patch was applied in the workspace. |
| `verified` | Baseline + post-repair tests both passed. **The only terminal success.** |
| `failed` | Verification failed; the change was not kept. |
| `rolled_back` | A previously applied change was reverted. |

The status is displayed everywhere (UI tables, CLI listings, reports) so the
difference between *detected*, *proposed*, *applied*, *verified* and *unresolved* is
never blurred.

---

## 4. Stable keys and correlation

- **stable key** = `sha256(rule_id | rel_path | symbol | normalized_snippet)`
  - normalised snippet: whitespace collapsed, truncated, so cosmetic reformatting does
    not create a new finding, while the identity survives line drift.
- **root-cause group** = 16-char hash of `(rule_id, rel_path, symbol)`
  - groups recurrences of the same underlying problem across a file.

Deduplication collapses findings that share a stable key within a scan and increments
`dedup_count`.

---

## 5. Incremental scanning

`project_files` carries:

- `last_scan_id` — the scan that last analysed this file,
- `last_scan_hash` — content hash at that time.

On a new scan:

1. If the file is unchanged (`last_scan_hash` matches current), the prior findings for
   that file are **carried forward** — the file is not re-parsed.
2. If the file changed (or is new), it is re-parsed and its findings regenerated.

A full rescan ignores both fields. Cancel a scan at any time; findings from a cancelled
scan are not promoted to the project's `last_scan_id`.

---

## 6. Verification semantics

`compare_runs(baseline, post)` returns one of:

| Result | Condition |
|---|---|
| `passed` | Baseline passed, post passed, no errors. |
| `regression` | At least one baseline-passing test fails post-repair. |
| `improved_not_clean` | Fewer failures post-repair, but still failing. |
| `failed` | No improvement, or new failures. |
| `inconclusive` | No harness detected, or counts cannot be compared. |

Only `passed` sets a patch to `verified` and makes it eligible for
`--apply`.

---

## 7. Snapshots and rollback

- A snapshot is taken **before** any change is applied to the original project.
- Snapshot directory lives under `data/snapshots/`.
- `rollback_patch(session, patch_id)` looks up the snapshot linked to the patch,
  restores the whole project tree, marks the patch `rolled_back`, and records
  `snapshots.restored_at` plus an audit event.
- Rollback is **whole-project**, not per-file.

---

## 8. Audit events

Every mutation records an `AuditEventRow`:

| Field | Example |
|---|---|
| `actor` | `user`, `system` |
| `risk` | `info`, `warn`, `critical` |
| `action` | `project.import`, `scan.run`, `repair.plan`, `repair.execute`, `patch.rollback`, `settings.save` |
| `target` | project/file/patch identifier |
| `details` | human-readable summary |

The trail is append-only in normal operation and is included in every report.

---

## 9. AI request metadata

`ai_requests` stores **metadata only**:

| Field | Meaning |
|---|---|
| `provider_id` | Which provider was used. |
| `purpose` | `explain` / `propose_repair` / `summarize`. |
| `model` | Model id. |
| `prompt_sha256` | Hash of the redacted prompt. |
| `redactions_applied` | Number of redactions performed. |
| `remote_allowed` | Whether the request was permitted to leave the machine. |
| `status` | `ok` / `error` / `refused`. |
| `latency_ms` | Round-trip time. |

No prompt text and no secret values are ever stored.

This table is a local audit trail of AI calls, not usage telemetry. The
application has no telemetry of any kind: nothing about how it is used is
counted, aggregated or transmitted.
