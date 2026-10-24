# PYFIX 360 — Operating Manual

This is the day-to-day usage guide. For architecture and principles see
[`../README.md`](../README.md).

---

## 1. First run

```bash
pip install -e ".[ui,credentials]"
pyfix360 init
```

`init` creates the database, the Alembic revision table, and the data directories
(`snapshots/`, `workspaces/`, `reports/`, `logs/`). It is safe to run repeatedly.

`init` and Alembic are reconciled, not two competing ways of building the schema.
`init` creates the tables from the ORM metadata, adds any missing column to a table
that already existed, and then **stamps** the Alembic revision table at the current
head. Running `alembic upgrade head` afterwards is therefore a no-op instead of a
"table already exists" failure. Nothing is ever dropped or retyped, so an existing
install upgrades in place.

Then launch the desktop app:

```bash
pyfix360-gui
```

or drive everything from the CLI (`pyfix360 --help`).

---

## 2. Importing a project

| Source | Command | Notes |
|---|---|---|
| Folder | `pyfix360 import ./proj --origin folder` | Analysed **in place**. PYFIX 360 reads the folder; it does not copy or modify it. |
| ZIP | `pyfix360 import ./proj.zip --origin zip` | Extracted into the data directory. Unsafe archive paths are rejected. |
| Git | `pyfix360 import https://host/repo.git --origin git` | Shallow clone (`--depth 1`) into the data directory. |

After import, PYFIX 360 records:

- every tracked file with its language, size and flags (`test`, `generated`),
- parsed symbols (modules, classes, functions) for Python files,
- declared dependencies from `requirements.txt`, `pyproject.toml` (PEP 621 and Poetry)
  and `package.json`,
- detected frameworks, build systems, test harnesses, entry points and package
  managers.

Select the active project in the top bar of the desktop app, or pass its id to CLI
commands.

---

## 3. Scanning

```bash
pyfix360 scan 1                 # full scan, incremental by default
pyfix360 scan 1 --full          # force a full rescan
pyfix360 scan 1 --tools builtin # built-in analyzer only
```

The desktop **Inspection Center** offers the same choices.

### What a scan does

1. Iterates the tracked files.
2. For **unchanged** files (same content hash as last scan), carries previous findings
   forward without re-parsing.
3. For **changed Python** files, runs the built-in AST analyzer.
4. For each available external tool, runs its adapter; unavailable tools are recorded
   in `tools_skipped` with a reason.
5. Deduplicates findings by stable key, correlates them into root-cause groups, and
   persists them.

### Scan status

| Status | Meaning |
|---|---|
| `completed` | All configured tools ran. |
| `partial` | One or more tools were unavailable; coverage is narrower. The skipped tools are always listed. |
| `failed` | The scan could not complete (see the message). |
| `cancelled` | The scan was cancelled by the user. |

**Never treat a `partial` scan as complete security coverage.** The report states the
skipped tools explicitly.

---

## 4. Reading findings

```bash
pyfix360 findings 1            # latest scan
pyfix360 show 42               # one finding with all evidence
```

In the desktop app, the **Inspection Center** lists findings with severity colour
coding and shows detail + evidence below. Use the severity and status filters to
narrow the list.

Each finding exposes:

- **stable key** — survives line drift within the file,
- **category / severity / confidence**,
- **status** — where it is in the repair lifecycle,
- **root-cause group** — links recurrences of the same problem,
- **evidence** — file, line range, snippet and *why it was flagged*.

### The list follows the Findings settings

The list you see is not the raw database table. `findings_service.visible_findings()`
applies the settings in Settings → Findings, in both the desktop app and the CLI:

| Setting | Effect on the list |
|---|---|
| `min_severity` | Findings below the floor are hidden. |
| `show_false_positive_rules` | Findings already marked as false positives are hidden unless this is on. |
| `group_by_root_cause` | The list shows one row per root-cause group, with the number of recurrences in the title, instead of one row per occurrence. |

`auto_acknowledge_info` acts on the other side: after a scan completes, `info`-level
findings that are still `detected` are flipped to `acknowledged` automatically, so a
long tail of informational noise does not sit in the queue pretending to be work.

---

## 5. Repairing

```bash
pyfix360 plan 42               # create a repair plan for finding #42
pyfix360 repair 1 --yes        # execute plan #1 in isolation
pyfix360 repair 1 --yes --apply   # opt-in: copy verified changes back
```

In the desktop app use the **Repair Planning Center**: pick a finding, *Create plan*,
review the steps and risk, then *Execute plan*. Tick "apply verified changes to the
original project" only if you want the original tree updated.

### What happens during a repair

1. A snapshot of the project is taken (recoverable).
2. The project is copied into `data/workspaces/proj<id>_plan<id>`.
3. The pristine workspace runs the test suite → **baseline**.
4. The built-in fixer (if one exists) proposes a patch and a diff is generated.
5. The patch is applied **inside the workspace only**.
6. The test suite runs again → **post-repair**.
7. The runs are compared. Only `passed` promotes the patch to **verified**.
8. If `--apply` was given *and* the patch is verified, the change is copied to
   the original project.

If verification is anything other than `passed`, the change stays in the workspace and
the patch is marked `failed`/`rolled_back`. **The original project is untouched.**

### Approvals are asked for, per category

Repair is the one place where several different risks can apply at once, so the
approval switches are per *category* rather than one global "confirm" box:

| Category | Applies to |
|---|---|
| `destructive` | The patch removes more lines than it adds. |
| `security` | The finding is a security rule. |
| `dependency` | The finding touches a dependency. |
| `migration` | The finding touches a schema or data migration. |
| `production` | The finding touches production configuration or deployment. |

Each category has its own switch in Settings → Repair & Safety
(`require_approval_<category>`). A category whose switch is **on** must be approved
before the repair proceeds. The desktop app shows one dialog naming every category
that applies; the CLI asks on the terminal. Approval is granted **per repair** and is
never remembered across runs.

The `destructive` category is only knowable once the diff exists, so it is evaluated
after the patch is generated and **before anything is written** — including before the
copy back to the original project.

The approval switches are a second channel, separate from the access matrix in
Permissions. The access matrix decides *whether a capability may be used at all*; the
approval switches decide *whether this particular change needs your say-so*. Both
apply, and both are enforced by the service, not the UI.

### Isolation is not optional

If Settings → Repair & Safety → `isolate_execution` is off, a repair and a test run
are both **refused** rather than run against the project directly. Running a project's
own test suite in the project itself is how a broken test suite destroys the working
tree, so the setting is a guard rail and not a preference.

### Manual-only rules

Most rules have no automated fixer. For those, the plan contains manual guidance steps
and the patch is never fabricated. This is deliberate: an unverifiable auto-fix is
worse than an honest instruction.

---

## 6. Rolling back

```bash
pyfix360 rollback 3            # restore the snapshot taken before patch #3
```

The desktop **Repair History & Rollback Center** does the same with one click.
Rollback restores the **entire** project to the state at snapshot time — any change
made after that snapshot is reverted.

---

## 7. Testing and verification

The desktop **Testing & Verification Center** can:

- detect the test harness in a chosen workspace,
- run the suite in the project root (read-only) or any isolated workspace,
- show pass/fail/error/skip counts, duration and the (capped) output,
- list individual test cases.

Verification is always a **comparison**: a patch is verified only when the baseline
passed and the post-repair run passes too. In the comparison vocabulary:

- `passed` — clean before and after.
- `regression` — a previously passing test now fails.
- `improved_not_clean` — progress, but the suite still fails.
- `failed` — no improvement or new failures.
- `inconclusive` — no harness or incomparable counts.

**No harness → no verification.** PYFIX 360 says so rather than guessing.

---

## 8. Feature completeness

```bash
pyfix360 features 1
```

Enter acceptance criteria (one per line) in the desktop **Feature Completeness
Center**, then click *Analyze features*. Each feature is graded against four evidence
kinds: **code**, **test**, **docs**, and **criteria**.

Statuses: `complete`, `partial`, `missing`, `unknown`.

> **Honest caveat:** `complete` means code exists *and* a test references it. It does
> **not** prove the behaviour is correct, and documentation-only mentions are flagged
> as `missing` when no code implements them.

---

## 9. Reports

```bash
pyfix360 report 1 --format markdown --out ./audit.md
pyfix360 report 1 --format json     --out ./audit.json
pyfix360 report 1 --format html     --out ./audit.html
```

Reports contain findings (with evidence), repair history, test results, the audit
trail, and a section listing **what could not be checked** — missing tools, unparsed
languages, and sandbox limitations. Export or preview from the desktop **Reports &
Export Center**.

---

## 10. AI providers and models

Configure in the desktop **AI Provider Manager**, or:

```bash
pyfix360 providers presets                 # list the curated catalogue
pyfix360 providers add ollama --type local_runtime --url http://localhost:11434/v1 --model qwen2.5-coder
pyfix360 providers list
pyfix360 providers test 1                  # a real call; verifies only on success
pyfix360 providers remove 1
```

### Choosing a provider

Start from a **preset** rather than typing a URL from memory. Local presets
(Ollama, LM Studio, llama.cpp, vLLM) need no key and never touch the network. Remote
presets (OpenRouter, Groq, Cerebras, Google, Mistral, DeepSeek, Together) need a key
from the provider's `signup_url`, which the app shows you.

### Choosing a model

The catalogue records a cost label (`free` / `cheap` / `paid`) and a note for each
model. It is a **reviewed starting point** (see `CATALOG_REVIEWED`), not a guarantee —
model ids change often. When in doubt, use **Discover models**, which asks *your*
endpoint for its current id list via a single `GET {base_url}/models` call and merges
that with the catalogue. Discovery sends no source and no prompt, runs only when you
press the button, and never invents prices. See [`AI.md`](AI.md) for the models the
project ships entries for and the honest caveats about them.

### Verification state

`verified_live` is `False` for every provider until a real call actually succeeds.
"Configured but key absent" is stated plainly — the app never pretends a model
answered when none did.

See the README's [AI providers, models and privacy](../README.md#ai-providers-models-and-privacy)
section and [`AI.md`](AI.md) for the full model. In short:

- AI is **disabled by default** and never required for local analysis.
- Remote endpoints require an explicit privacy mode of `FULL_REMOTE_APPROVED`.
- Credentials are redacted **before** any prompt is built.
- Only a prompt **hash** is logged, never the prompt text; chat history is redacted text.
- An unknown host is treated as **remote**, so a misconfiguration earns the stricter checks.
- AI output is **advisory** — it is never the authority for verification.

---

## 11. Quality gates

Gates encode "this project must not ship unless…" as data, evaluated against a scan.
Manage them on the **Quality Gates** screen or from the Settings Center.

| Metric | Meaning |
|---|---|
| `max_severity_count` | Findings at a severity, compared with an operator and threshold. |
| `tool_available` | A named analyzer actually ran (so a `PARTIAL` scan can fail the gate). |
| `tests_passing` | The latest verification run passed. |
| `no_new_findings` | No regression versus the previous scan. |

Each gate is **blocking** or advisory, and project-scoped or global. A **Seed
defaults** button offers three sensible starting gates — no criticals, no highs, and
all required tools must have run — but gates are **opt-in**: PYFIX 360 does not create
blocking rules you did not ask for. Evaluation produces a verdict per gate plus a
one-line summary; a pass is never implied — it is computed.

---

## 12. Settings Center

The desktop **Settings Center** is a single module fronting every option, organised
into twelve tabs. Nothing is reachable only by editing a file.

| Tab | Covers |
|---|---|
| Appearance | Theme, accent, font, density, radius, motion, sidebar width, start screen, status bar. |
| Scanning | Include/exclude globs, size and file-count limits, generated-file skipping, enabled analyzers, parallel workers, incremental vs full. |
| Findings | Minimum severity, root-cause grouping, auto-acknowledgement, false-positive visibility. |
| AI | Behaviour, prompt cap, repository-content switch, default provider. |
| Privacy | Privacy mode, and the redaction guarantee (always on). There is no telemetry. |
| Repair & Safety | Snapshot-before-write, per-category approvals, isolation, timeout, memory, network, retry limit, backup retention, write guard, gesture window. |
| Access Control | The access matrix (below). |
| Quality Gates | Manage gates in place. |
| Tools & Commands | Test command override; custom tool paths. |
| Notifications | Completion toasts, log level and retention. |
| Background | Tray icon, close-to-tray, the background host's interval and what is installed right now. |
| Data & Backup | Export/import the settings file, inspect the data directory, reset, and the application updates. |

From the CLI:

```bash
pyfix360 settings show
pyfix360 settings get theme
pyfix360 settings set execution_timeout_s 600
pyfix360 settings set memory_limit_mb 4096
pyfix360 settings export ./settings.json
pyfix360 settings import ./settings.json
```

### Access levels are enforced, not just stored

Every capability (`apply_to_original`, `rollback`, `export_reports`, `purge_history`,
`autopilot`, `danger_zone`, …) carries one of:

| Level | Effect |
|---|---|
| `full` | The action runs silently. |
| `ask` | Refused unless the caller confirms. |
| `blocked` | Refused even when confirmed. |

`check_access()` raises `AccessDenied` at the **service** boundary, so bypassing the UI
does not bypass the policy. An unknown capability defaults to `ask` — never to `full`.
The Danger Zone buttons grey out while `danger_zone` is `blocked`, and the refusal is
explained in a tooltip before you click.

### Common settings

| Setting | Default | Effect |
|---|---|---|
| `isolate_execution` | `true` | Require isolation for test execution. When off, repairs and test runs are refused. |
| `execution_timeout_s` | `300` | Hard timeout (process-tree kill) for any sandboxed command. |
| `memory_limit_mb` | `2048` | Best-effort memory cap (weaker on Windows). |
| `allow_network_in_sandbox` | `false` | Whether sandboxed commands may reach the network. |
| `max_repair_retries` | `2` | Retry limit for repair attempts. |
| `require_snapshot_before_write` | `true` | Snapshot before a write. When off, the repair is audited as `repair.snapshot_skipped`. |
| `require_approval_*` | on | Per-category approval before a repair (see §5). |
| `block_write_outside_project` | `true` | Refuse a copy back to a path outside the project root. |
| `dry_run_by_default` | `true` | The Repair screen starts with "apply to the original" unticked. |
| `redact_before_ai` | `true` | Redact credentials before building any prompt. **Locked on** — it cannot be switched off. |
| `privacy_mode` | `local_only` | Default privacy mode for providers. |
| `theme` | `dark` | One of the five themes. |
| `access_danger_zone` | `blocked` | Danger-zone policy. |
| `access_autopilot` | `ask` | Whether starting an unattended run needs confirmation. |
| `autopilot_gesture_window_ms` | `500` | Grace window for the double/triple Enter gesture, measured from the first Enter. |
| `auto_commit_verified` | `false` | Stage/commit verified changes automatically (opt-in). |
| `backup_retention_days` | `30` | How long archive snapshots are kept. Git snapshots are never pruned by age. |
| `scan_include_globs` / `scan_exclude_globs` | — | Which files a scan looks at. |
| `scan_max_file_kb` | `2048` | Files larger than this are skipped, not analysed. |
| `scan_max_files` | `20000` | Upper bound on the files one scan reads. |
| `scan_parallel_workers` | `0` (auto) | How many external tools run at once. `0` means auto; `1` means one at a time. |
| `min_severity` | `info` | The severity floor of the finding list. |
| `log_retention_days` | `14` | How long `pyfix360.log` is kept. |

### Redaction cannot be turned off

`redact_before_ai` is shown in the Settings Center as a **disabled** switch with the
explanation next to it, and the settings store **refuses** a write that would set it
to `false`:

```bash
pyfix360 settings set redact_before_ai false
# 'redact_before_ai' is locked on: redaction before AI is a hard guarantee of the
# privacy model and cannot be switched off.
# exit code 1
```

This is deliberate. The gateway redacts unconditionally, so a stored `false` would be a
setting the code could not honour — a promise the app would silently break. There is
no state in which secrets are sent unredacted.

### Themes

Five themes ship — Studio Dark, Midnight Indigo, Deep Forest, Daylight, High Contrast —
plus an accent override. Contrast is **checkable**: body text is ≥ 7:1 (WCAG AAA),
muted text ≥ 4.5:1 (AA), and button labels are derived from the accent's own luminance
so no accent — including one you type in — can make a label unreadable.

### Application updates

The **Data & Backup** tab has an *Application updates* group:

| Control | What it does |
|---|---|
| *Check for updates* | Asks the repository that publishes the releases whether a newer version exists. |
| *Install update* | Downloads it, verifies the checksum and installs it. |
| *install a new version as soon as it is found* | Only takes effect when the access policy allows an automatic check. |

From the terminal:

```bash
pyfix360 update --check     # report what is available, change nothing
pyfix360 update --yes       # download, verify and install
```

**What the check does and does not do.** It talks to one host — the GitHub
repository that publishes the releases — and sends nothing about your machine.
The download is verified against the SHA-256 the release published; a file that
does not match is **deleted, not installed**.

**Whether it happens on its own** is decided by the access matrix, not by the
app:

| `access_check_for_updates` | Effect |
|---|---|
| `ask` (the default) | Nothing leaves the machine until you press *Check for updates*. |
| `full` | The application checks at startup, and installs automatically if the checkbox above is on. |
| `blocked` | The application never contacts GitHub at all. |

Installing means the installer replaces the installed folder and the shortcuts.
The Windows Restart Manager closes the running application first and starts the
new version again afterwards — so the application restarts itself.

---

## 13. Audit trail

```bash
pyfix360 history 1
```

Every import, scan, plan, patch application, test run, rollback and settings change is
recorded with timestamp, actor, risk level, action, target and details. The Settings
Center shows the most recent events; the Overview screen shows the global trail.

---

## 14. Platform playbooks

Seven AI app builders are catalogued with the operating knowledge needed to drive
them: **Lovable**, **Trickle**, **Replit**, **Emergent**, **v0**, **Builder.io** and
**Google AI Studio**.

Open **Integrations & CLIs → Platform Playbooks**. Filter by category, search, then
select a platform to read its playbook in full:

| Section of a playbook | What it tells you |
|---|---|
| Summary | What the platform is, what it is good at, what it is bad at. |
| Verification | When the entry was last reviewed, and the standing warning. |
| Authentication | Where to obtain the API key, and what it can do. |
| API | The endpoint, the capabilities, and whether a prompt can be sent at all. |
| CLI | The command-line tool, if the vendor ships one. |
| Deep link | A URL that opens the platform with a prompt pre-filled. |
| How to give commands | The dialect the platform understands. |
| Prompt patterns | Templates that work, with the reason each is shaped that way. |
| Questions and answers | The clarifying questions it asks and the answer that unblocks it. |
| Situations | What to do when the build stalls, the preview breaks, credits run out, or a secret leaks. |
| Pitfalls | The failure modes specific to that platform. |

### Read the verification notice first

Every platform entry carries `reviewed_on` (the date the vendor's documentation was
last read) and `verified_live = false`. These are recorded in the same style as the AI
provider catalogue: the prompt dialect, the API surface and the credit rules of every
one of these vendors change without notice.

> **PYFIX has never driven this platform from inside this build.** Confirm the entry
> against the vendor's own documentation before you rely on it. The catalogue is a
> reviewed starting point, not a guarantee.

The notice is repeated at the top of every playbook in the UI, not buried in a
footnote, because a stale entry that looks authoritative is worse than no entry.

### Operating a platform

**Integrations & CLIs → Operate a platform** sends a real prompt. There are two paths,
and the readiness line tells you which one a platform supports before you try.

#### Path A — API (the stable one)

| Platform | Surface | Notes |
|---|---|---|
| v0 | `https://api.v0.dev/v1` | Create a chat from a prompt, stream messages, read the preview URL, deploy. |
| Lovable | REST | Lists, reads and deploys **existing** projects. It cannot build an app from a prompt. |
| Replit | GraphQL | Creates a Repl from a prompt and posts agent messages. Operation names are community-documented — verify them live. |
| Builder.io | Content API | The key travels in the `apiKey` query parameter, per the vendor's own docs. |
| AI Studio | Generative Language API | OpenAI-compatible chat completions and a model list. |

The clients live in `pyfix360/integrations/api_clients.py` and use only the standard
library, so no extra dependency is needed. They raise `UnsupportedOperation` rather
than pretending to succeed when a platform has no API for what you asked.

#### Path B — browser automation (the fallback)

Trickle and Emergent publish no API, so the only way to drive them is a real signed-in
browser session. The same path also works for prompting on Lovable, where the API
cannot build.

```bash
pip install -e ".[automation]"
python -m playwright install chromium
```

- Selectors live in `pyfix360/integrations/browser.py` as data, separate from the
  code, because **a vendor redesign breaks them**.
- They can be overridden per platform without touching the code.
- A failed operation writes a screenshot (`automation-<label>.png`) into the log
  directory so the breakage can be diagnosed instead of guessed at.
- Where a deep link exists (`lovable`, `ai-studio`), the prompt is pre-filled by the
  URL and only the submit step is automated.

#### The workflow

1. Pick the platform. The status line shows what is missing (no API client, no key,
   `[automation]` not installed).
2. Save the API key. It goes into the **OS keyring** under `platform-<key>` — never
   into the database, and never into the exported settings file.
3. Type the prompt, then press **Preview**. The preview shows the *exact* text that
   would be sent, after redaction, so you can see what was stripped.
4. **Send via API** or **Send via browser**.
5. Paste the returned handle into **Job handle** and press **Read status** to follow
   the build, or answer a question the platform asked with **Send follow-up**.

### The gates that apply

Operating a platform is a remote action, so it passes through the same policy as the
AI assistant:

- The **`remote_ai`** capability in Settings → Access Control decides whether a call is
  allowed and whether it needs confirmation. Browser automation uses
  **`plugin_tools`**.
- The global privacy mode must be **`full_remote_approved`** — every app builder is a
  remote service, so `local_only` and `redacted_remote` both refuse.
- The prompt is **redacted before it leaves the machine**, and the redaction count is
  recorded.
- Every call is written to the **audit trail** as `platform.prompt.submit`,
  `platform.prompt.followup` or `platform.status`, with the platform, the handle and
  the redaction count.

A refusal is always explained in plain language ("no API key is stored for 'v0'",
"change the privacy mode to 'full_remote_approved'"), never reported as a
connectivity error.

---

## 15. Autopilot

Autopilot runs the pipeline for the active project without supervision, and is built
around one promise: **you can stop it, and it cannot surprise you.**

### Start a run

1. Choose the project in the top-bar selector.
2. Open **Autopilot** in the sidebar (second entry). While idle, the screen previews the
   queue and says, per step, whether it `runs unattended`, `asks you first`, or `would be
   refused`.
3. Press **Start unattended run**. The *Start an unattended autopilot run* row in
   Settings → Access Control ships as *ask*, so you are shown the plan and asked to
   confirm it first.

The shipped queue is: scan → run tests → apply the newest repair plan to the original →
evaluate gates → export a report. The third step is gated on `apply_to_original` (the
*Apply a verified patch to the original project* row), which ships as *ask*, so an
unattended run always stops before it writes to your project.

### Drive it with Enter

| Keys | Effect |
|---|---|
| Double Enter | Pause the run. |
| Triple Enter | Resume it. |
| Enter at an approval | Approve that step, continue. |
| Esc at an approval | Skip that step only; the rest of the queue still runs. |
| **Stop now** | Abandon the run and discard the queue (audited as a warning). |

The gesture works from any screen, because it is caught by an application-level event
filter rather than a shortcut. Three rules stop it from eating your typing:

- In a text field, Enter counts only when the line under the cursor is blank.
- A held-down Enter is one press, not a burst.
- Any other key between two Enters — or any key inside a dialog — voids the gesture.

### Why a triple never pauses first

The pause is not performed on the second Enter. Two Enters open a **grace window**
(default 500 ms) that is measured from the *first* Enter of the gesture, not rolling
between presses. A third Enter inside the window resolves it as a triple and cancels the
pending pause. If the window closes with only two Enters in it, the gesture was a double
and the pause happens then — a `QTimer` settles the expiry, so you never have to press a
third key to get the pause you asked for.

Slow Enters are not a triple: three presses spread over more than the window read as a
double plus a stray Enter, which is the safe interpretation. Widen or narrow the window in
**Settings → Repair & Safety → Autopilot gesture window** (150–2000 ms).

### Approvals

When a step reaches a capability set to *ask*, the run pauses with reason `approval`, the
toolbar badge turns red and reads `press Enter to approve`, and the Autopilot screen shows
a **Waiting for you** card naming the step and the capability. Approving confirms that one
step; denying skips that one step. A capability set to *blocked* is refused in place,
audited as a warning, and the run continues — "skipped" and "not allowed" are different
facts and are reported differently.

### What is recorded

Every transition is written to the audit trail with `actor=autopilot`:
`autopilot.start`, `autopilot.pause`, `autopilot.resume`, `autopilot.approve`,
`autopilot.deny`, `autopilot.step.start`, `autopilot.step.done`, `autopilot.step.failed`,
`autopilot.step.skipped`, `autopilot.step.refused`, `autopilot.finish` and
`autopilot.stop`. The screen's session log mirrors them, newest last.

### Limits, stated plainly

- Autopilot is **not a daemon**. It needs the window open; closing the app ends the run —
  unless you hide it to the tray or queue the job for the background host instead, which is
  §16.
- A step that is dispatched to a worker thread cannot be interrupted mid-step — the
  gesture takes effect at the next queue boundary. Long test suites therefore pause late.
- The queue is fixed by `pipeline_steps()`. Custom queues are not editable from the UI yet.

---

## 16. Background and headless operation

Everything in §15 needs a window and a keyboard. The background host runs the same
pipeline with neither: it is the same `Autopilot` state machine, the same steps, the same
capability gates and the same audit chain, driven by a process that works off a queue
stored in the database.

The queue is not in the daemon's memory. A job is a row in `background_jobs`, and that
row is the *only* channel between the process that asks for work and the process that
does it — which is why you can queue a job from the GUI, watch it from a console, and
answer its approval gate from either.

### The commands

```powershell
pyfix360 service probe                  # what this machine allows, and what each mode costs
pyfix360 service install                # pick the strongest mode available
pyfix360 service install --mode task    # or name one
pyfix360 service install --mode task-account --username ops --password-ask
pyfix360 service start / stop / status
pyfix360 service stop --force           # end it now, even mid-step
pyfix360 service uninstall              # remove the registration, keep the job history

pyfix360 service enqueue 1 --confirmed  # queue a run for project 1
pyfix360 service jobs --open            # the queue as the host sees it
pyfix360 service approve 4              # answer a parked gate
pyfix360 service skip 4                 # decline it; the rest of the queue still runs
pyfix360 service cancel 4               # abandon a job

pyfix360 service run --once --polls 3   # drive the loop in this console, install nothing
pyfix360 service logs -n 40             # tail of the host log
```

`status` answers two different questions and reports both: what the *launcher* (the
service control manager, the task engine) thinks is running, and what the host's own pid
file proves is actually here. They disagree whenever a host was killed or is still
starting. The exit code of `service status` is the running answer, so a script can ask
without parsing prose.

### Five modes, and what each one costs

`install` with no `--mode` takes the most persistent mode this machine will allow and
says which it chose. Naming a mode the machine refuses is an error with the reason in it —
never a silent downgrade, because surviving your logoff is exactly what was asked for.

| Mode | Needs admin | Survives logoff | Starts at | What it is |
|---|---|---|---|---|
| `service` | yes | yes | boot | A real Windows service in session 0, hosted by pywin32's `pythonservice.exe`. No window, no icon, runs as a machine account. |
| `nssm` | yes | yes | boot | The same, wrapped by NSSM, for a machine without pywin32. `nssm.exe` must be on `PATH`. |
| `task-account` | no | yes | boot | `schtasks /sc onstart /ru … /rp …` — your own identity, with the password stored by Windows so the task can run without you. |
| `task` | no | no | logon | `schtasks /sc onlogon`. No rights needed; Task Scheduler ends it when you log off. |
| `process` | no | no | when you run it | A detached `pythonw.exe` child. Nothing is registered, so nothing restarts it. |

`install` writes the command it ran, the mode it used, that mode's trade-off, and the log
path into its report. It also writes a short launcher (`service/run_host.py`) instead of a
long inline command, because `schtasks /tr` truncates silently and a truncated command is
a task that reports itself registered and never starts anything.

### Autostart, and "it must keep running after I log off"

Only three of the five modes do that, and they differ in what they need:

- **`service` / `nssm`** start at boot with nobody logged on. They need an elevated prompt,
  because registering a service writes under `HKLM\System\CurrentControlSet`. A `service`
  install also drops a copy of the install record in `%PROGRAMDATA%\PYFIX360\host.json`: a
  session-0 service sees a *different* `%LOCALAPPDATA%` than you do, and without the
  handover it would open its own empty database and quietly run against nothing.
- **`task-account`** starts at boot and keeps your identity. It needs no admin rights, but
  Windows has to store your password for the task to run without a session — that is the
  price, and the report says so. The password is only accepted from a prompt
  (`--password-ask`), never from the command line, because argv is kept by the shell
  history and visible in the process list.
- **`task`** starts at your logon and ends at your logoff. It is the right answer for
  "no window, no taskbar entry, nothing installed machine-wide", and the wrong answer for
  "keep working when I'm gone". The install says which one it gave you.

`service probe` prints this machine's actual answer — rights, pywin32, `schtasks`,
`nssm` — before you commit to anything.

### What happens at an approval gate

This is the part a daemon must not guess about.

- **Starting a run** whose `access_autopilot` row is *ask* is **refused**, unless the job
  was queued with `--confirmed`. The refusal names both ways to fix it: set the row to
  *full* in Settings → Access Control, or queue the job confirmed. A daemon that quietly
  confirmed its own gated step would turn the access matrix into decoration.
- **A gate reached mid-queue** (the `apply_to_original` step, say) **parks** the job: the
  row goes to `waiting_approval`, the prompt is written into the row and the log with the
  exact command that answers it, and the host goes back to heartbeating.

```
'Apply the newest repair plan to the original' needs apply_to_original, which is set to
ASK — answer with `pyfix360 service approve 4` or `pyfix360 service skip 4`
```

`approve` and `skip` record your answer; the host applies it on its next poll, so it is
not instant. `skip` declines *that step* and the rest of the queue still runs, exactly as
Esc does at the desk. The Autopilot screen's **Background queue** card offers the same two
answers as buttons — enabled only for a row that is actually parked — so you never need the
console to answer a gate. The tray menu shows how many jobs are open and how many wait for
you, and starts or stops the host, but it does not answer gates: a summary menu is where
you notice a job is waiting, not where you read what it is asking.

### Where to look when it is not doing what you asked

```
%LOCALAPPDATA%\PYFIX360\logs\service.log     rotating, 2 MB × 5, one file for every host
%LOCALAPPDATA%\PYFIX360\service\host.pid     who is running, started when, which data dir
%LOCALAPPDATA%\PYFIX360\service\stop.request a stop asked for, and which pid it is aimed at
%LOCALAPPDATA%\PYFIX360\service\host.json    how the host was installed (mode, paths)
```

`pyfix360 service logs -n 40` reads the log from the end. The desktop app keeps writing
`pyfix360.log` beside it: a background run is the thing you read *after* the fact, so the
two never share a file.

A stop is a **request**, not a kill. `service stop` writes the marker naming the live pid
and the host drains its current step before exiting; that is one poll cycle (2 s by
default — Settings → Background). The marker names the pid it is aimed at on purpose: a
stop asked for while a host was still starting would otherwise be swallowed by that host's
own startup, and a stop meant for the process being replaced would land on its replacement.
`install` and `start` clear a leftover request, because asking for a running host and
leaving "stop" in the inbox are contradictory instructions.

### Limits, stated rather than worked around

- **The host is a visible process.** No window and no taskbar entry is what "background"
  means here; it still appears in Task Manager and in `tasklist`. Leaving the process list
  would need process hiding, which is a rootkit technique, not a feature. Hiding the tray
  icon does not hide the app either.
- **A service runs as a machine account, so it cannot unlock your credentials.** The OS
  keyring of a service account is a different one, and the encrypted-file fallback is keyed
  to your user, so a secret you saved simply is not there. A step that needs one then sends
  no key and fails at the provider's refusal — the job neither runs as you nor invents a
  credential. Use `task-account` to keep your identity without admin rights.
- **A step already running cannot be interrupted.** Cancellation and stopping land at the
  next queue boundary; a long test suite therefore finishes before it obeys.
- **A job whose host died is `interrupted`, not `failed`.** Nobody can prove whether a
  repair was half-applied, so the row says that instead of guessing. The reaper only makes
  that call after a stale heartbeat **and** a dead pid, so a host grinding through a
  forty-second suite is left alone, and a second host on the same machine can safely clean
  up after a crashed one.
- **Nothing is registered with Windows by this app without you typing the command.** The
  GUI only reads and reports host state; a service or scheduled task is a machine-wide
  action, so Settings → Background and the Autopilot card tell you the command instead of
  running it for you. The one thing a click does start is the tray's *Start the background
  host* — a detached process, which registers nothing and ends with your session.

---

## 17. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Scan status is `partial` | One or more external tools are missing. Install them (`pip install ruff bandit pip-audit`) or accept the reduced coverage. |
| Repairs never reach `verified` | No test harness detected, or the suite fails at baseline. Add tests; PYFIX 360 will not verify without them. |
| `pip-audit` reports unavailable | pip-audit is not installed, or the network is unreachable. It is optional. |
| Provider test fails | Check the endpoint URL and that the local runtime is running. Keys are read from the credential store, not the DB. |
| Rollback restored too much | Rollback restores the whole snapshot; it is not a per-file undo. Make snapshots at the right granularity. |
| UI shows "No project selected" | Import a project, then choose it in the top-bar selector. |
| Platform call refused before it left the machine | Policy, not connectivity. Check the `remote_ai` access level and the privacy mode (must be `full_remote_approved`). |
| "no public API — drive the signed-in web session" | Expected for Trickle and Emergent. Install the `[automation]` extra and use **Send via browser**. |
| Browser step fails after a vendor redesign | The selectors in `integrations/browser.py` are stale. Override them for that platform; the failure screenshot in the log directory shows what changed. |
| The Replit call returns a GraphQL error | The operation names are community-documented, not official. Verify them against a live account before depending on them. |
| Double Enter does not pause | You are inside a modal dialog (its button owns the key), typing on a non-blank line, or the presses were further apart than the grace window. The badge and session log say what the machine saw. |
| Autopilot pauses at "Apply the newest repair plan" | Correct behaviour: `apply_to_original` ships as *ask*. Approve it, press Esc to skip that step, or change the level in Settings → Access Control. |
| Autopilot refuses to start | The *Start an unattended autopilot run* access row is *blocked*, or no project is selected. The refusal states which. |
| A resumed run looks stuck on one step | That step is on a worker thread and cannot be interrupted mid-step; the queue advances when it reports back. The badge shows `running` and the log shows `autopilot.step.start`. |
| Lovable accepts the prompt but builds nothing | The Lovable API manages existing projects; it cannot create one from a prompt. Use the browser path. |
| `service install` refuses the mode you named | That mode needs something this machine is not giving it — admin rights for `service`/`nssm`, `nssm.exe` on `PATH`, `schtasks.exe` for the task modes. The refusal names the missing piece and lists the modes that are open; `service probe` says the same before you try. |
| Installed as `task`, but the host is gone after you log off | Correct behaviour, and the install report said it: a logon task lives and dies with your session. `task-account` or `service` is the mode that survives logoff. |
| `status` says "not running" right after `install --mode task` | The task was registered, not started — `onlogon` fires at the next logon. `pyfix360 service start` runs the launcher now. |
| `status` gives two different answers | Deliberate. One is what the launcher (service control manager or Task Scheduler) believes, the other is what the host's own pid file proves. They disagree while a host is still starting, or after one was killed rather than stopped. Trust the pid file for "is work happening". |
| `service stop` took a few seconds | A stop is a request the host reads at its next heartbeat, so the running step finishes and the job records its true state. That is one poll interval (2 s by default, Settings → Background). `service stop --force` ends it immediately and leaves the job `interrupted`. |
| A job sits at `waiting_approval` and nothing happens | Expected: nobody is there to answer. `pyfix360 service jobs --open` shows the parked prompt with the exact command, and `service approve`/`skip` — or the Approve/Skip buttons on the Autopilot screen's Background queue card — answer it. The tray only counts them; it has no answers to give. |
| `service approve 4` says "not waiting for approval" | Either that job is not at a gate, or your first answer has not been polled yet — a parked job holds one answer at a time, so a double-click cannot change what the run is allowed to do. |
| A queued job failed immediately with "cannot answer for you" | `access_autopilot` is *ask* and the job was queued without `--confirmed`. A daemon does not answer for you. Set the row to *full*, or queue it confirmed. |
| The service runs but sees no projects | It opened its own empty database. A session-0 service reads `%PROGRAMDATA%\PYFIX360\host.json` to find your data directory; a per-user install cannot write that file, so re-run `service install --mode service` from an elevated prompt. |
| `service enqueue 99` queues nothing | That project id does not exist in the database the CLI is reading. Check `pyfix360 projects` — and note the pid file's `data_dir` if you installed against a `PYFIX360_DATA_DIR` override. |
| A repair refuses with "execution isolation is disabled" | Settings → Repair & Safety → `isolate_execution` is off. A repair or a test run in the project itself is refused, not performed. Turn isolation back on. |
| A repair refuses with "needs approval" | A category in Settings → Repair & Safety is on and this change falls under it (`destructive`, `security`, `dependency`, `migration`, `production`). Approve it in the dialog, or turn that category off if you do not want to be asked. |
| `settings set redact_before_ai false` is refused | Correct. Redaction is a hard guarantee of the privacy model, so the key is locked on and cannot be written to `false`. |
| `settings set scan_include_globs "*.py"` is refused | List and dict settings take JSON: `settings set scan_include_globs '["**/*.py"]'`. A plain string is not a list and is rejected rather than silently stored. |
| `pyfix360 gates` prints a table | That is the list form of the command. `pyfix360 gates eval <project>` evaluates the gates against a scan. |
| `alembic upgrade head` says the table already exists | `pyfix360 init` stamps the revision table at the head, so the upgrade is a no-op. Do not stamp by hand to "fix" it. |
