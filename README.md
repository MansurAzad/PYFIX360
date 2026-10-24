# PYFIX 360

<p align="center">
  <img src="packaging/pyfix360_icon_preview.png" alt="PYFIX 360" width="128" height="128">
</p>

**Offline-first code inspection, audit, repair and project-completion platform.**

PYFIX 360 inspects a codebase, records evidence-backed findings, plans safe repairs,
executes those repairs in an isolated copy of the project, verifies them against the
project's own test suite, and produces an audit report. **Everything works with no
network access.** Cloud AI is optional, user-configured, and off by default.

---

## Table of contents

1. [What it does](#what-it-does)
2. [Core principles](#core-principles)
3. [Installation](#installation)
4. [Quick start (CLI)](#quick-start-cli)
5. [Desktop interface](#desktop-interface)
6. [The Settings Center — 360° of control](#the-settings-center--360-of-control)
7. [Quality gates](#quality-gates)
8. [Themes and accessibility](#themes-and-accessibility)
9. [The repair contract](#the-repair-contract)
10. [AI providers, models and privacy](#ai-providers-models-and-privacy)
11. [AI app builders — platform playbooks](#ai-app-builders--platform-playbooks)
12. [Autopilot — unattended runs you can interrupt](#autopilot--unattended-runs-you-can-interrupt)
13. [Background and headless operation](#background-and-headless-operation)
14. [Packaging and distribution](#packaging-and-distribution)
15. [Architecture](#architecture)
16. [Supported languages — an honest matrix](#supported-languages--an-honest-matrix)
17. [Built-in analysis rules](#built-in-analysis-rules)
18. [Safety model and known limitations](#safety-model-and-known-limitations)
19. [Database and migrations](#database-and-migrations)
20. [Development](#development)

Further documentation:

- [`docs/OPERATING.md`](docs/OPERATING.md) — day-to-day usage.
- [`docs/AI.md`](docs/AI.md) — providers, the free/low-cost model catalogue, discovery, privacy guarantees.
- [`docs/PACKAGING.md`](docs/PACKAGING.md) — building the `.exe`, the icon and the installer.
- [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) — schema and lifecycle.

---

## What it does

| Capability | Detail |
|---|---|
| **Import** | Folder (analysed in place), ZIP archive, or `git clone`. The imported tree is never modified by analysis. |
| **Inspect** | Built-in Python AST analyzer (always available) plus optional external tools (Ruff, Bandit, pip-audit) that report *skipped + reason* when absent. |
| **Findings** | Stable identifiers, category, severity, evidence, confidence, status, deduplication and root-cause correlation. |
| **Feature completeness** | Builds a feature matrix from code evidence, tests, documentation and your acceptance criteria. |
| **Repair** | Plans a repair, applies built-in fixers **inside an isolated workspace copy**, runs baseline + post-repair tests, and only promotes a patch to *verified* when the tests confirm it. |
| **Rollback** | A recoverable snapshot is taken before every change; any applied patch can be reverted to the exact pre-repair state. |
| **Report** | Markdown / JSON / HTML audit report including findings, repair history, test results, the audit trail, and an explicit statement of what could **not** be checked. |
| **Quality gates** | Declarative pass/fail rules (severity counts, tool availability, test status) evaluated per project, with a blocking/non-blocking verdict. |
| **Autopilot (optional)** | An unattended pipeline run — scan, test, apply, gate, report — that pauses on a double Enter, resumes on a triple, and stops at any capability set to *ask* until you approve it. |
| **Background host (optional)** | The same autopilot machine, in a process with no window, working off a queue stored in the database. Installable as a Windows service, a scheduled task, a tray-resident desktop app or a plain detached process. |
| **AI assistant (optional)** | Chat, explain, and propose-a-repair over redacted, user-approved context — never a substitute for the test suite. |
| **Platform playbooks (optional)** | Operating knowledge for seven AI app builders — prompt patterns, command dialect, question handling and mid-build situations — plus gated API and browser paths to actually drive them. |
| **Audit trail** | Every import, scan, plan, patch, test run, rollback and settings change is recorded with actor, risk level and details. |

---

## Core principles

- **Offline-first.** The full local workflow needs no network. External tools that
  require network (e.g. `pip-audit` vulnerability feeds) are optional and disclose
  their absence.
- **No silent success.** If a tool is unavailable, the scan status becomes `PARTIAL`
  and the skipped tools are listed. Insights are never invented.
- **Evidence over assertion.** Every finding carries a file, line range and a code
  snippet. Every repair carries a diff.
- **Never label an untested patch as verified.** Verification requires a passing
  baseline and a passing post-repair test run. No test harness → no verification.
- **Recoverable changes only.** The original project is never overwritten without a
  snapshot that can be restored.
- **AI proposes; it does not verify.** AI output is advisory. The test suite and the
  static analyzers remain the authority.

---

## Installation

Requires **Python 3.11+**.

```bash
# 1. create and activate a virtual environment
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

# 2. install PYFIX 360 with the desktop UI and credential-store extras
pip install -e ".[ui,credentials]"
```

Minimal (headless) install without the desktop UI:

```bash
pip install -e .
```

To register the background host as a real Windows service (starts at boot, runs
whether or not anybody is logged on), add the `service` extra — it is only needed
for that one mode, and installing the service still takes an elevated prompt:

```bash
pip install -e ".[service]"
```

Optional external analyzers — install whichever you have available; PYFIX 360
detects them at runtime and reports the ones it cannot use:

```bash
pip install ruff bandit pip-audit
```

Initialize the database and data directories:

```bash
pyfix360 init
```

By default everything is stored under a per-user data directory:

| Platform | Location |
|---|---|
| Windows | `%LOCALAPPDATA%\PYFIX360` |
| macOS | `~/Library/Application Support/PYFIX360` |
| Linux | `~/.local/share/pyfix360` |

Override it with the `PYFIX360_DATA_DIR` environment variable.

---

## Quick start (CLI)

```bash
# import a project (folder, .zip or git URL)
pyfix360 import ./my-project --origin folder

# run a scan (PHP/JS/etc. files are inventoried; Python gets full AST analysis)
pyfix360 scan 1

# list findings from the latest scan
pyfix360 findings 1

# inspect one finding with its evidence
pyfix360 show 42

# create a repair plan for a finding
pyfix360 plan 42

# execute the plan in an isolated workspace (runs baseline + post tests)
pyfix360 repair 1 --yes

# copy verified changes back onto the original project (opt-in)
pyfix360 repair 1 --yes --apply

# review patches and roll one back if needed
pyfix360 patches 1
pyfix360 rollback 3

# reports and history
pyfix360 report 1 --format markdown --out ./audit.md
pyfix360 history 1

# feature completeness
pyfix360 features 1

# AI providers and settings
pyfix360 providers presets                 # the curated catalogue of endpoints
pyfix360 providers preset ollama           # create one from a preset (starts disabled)
pyfix360 providers add ollama --type local_runtime --url http://localhost:11434/v1 --model qwen2.5-coder
pyfix360 providers list
pyfix360 providers test <id>               # real call; marks the row verified only on success
pyfix360 providers discover <id>           # ask the provider for its live model list

pyfix360 settings show
pyfix360 settings get theme
pyfix360 settings set execution_timeout_s 600
pyfix360 settings export ./settings.json   # never includes credentials
pyfix360 settings import ./settings.json
pyfix360 settings reset

# quality gates
pyfix360 gates list
pyfix360 gates eval 1                      # exit code 1 if a blocking gate fails
```

All commands accept `--help`. The CLI is the headless driver for the whole
workflow; the desktop app uses the same service layer.

---

## Desktop interface

Launch the graphical application:

```bash
pyfix360-gui
# or
python -m pyfix360.app
```

The window has a collapsible sidebar with twenty screens, all wired to the service
layer. Every screen reflows for narrow and wide windows, and the whole UI is
recoloured live when you switch theme:

1. **Overview** — project/file/finding counts, gate status and the recent audit trail.
2. **Autopilot** — start an unattended pipeline run, watch the queue, answer approval gates.
3. **Project Manager** — import folder/ZIP/git, select the active project.
4. **Project Explorer** — file tree with languages, flags and the parsed symbol inventory; source preview.
5. **Inspection Center** — run scans, filter by severity/status, read finding detail and evidence.
6. **Security Audit** — security and dependency findings, secret-exposure subset, declared dependencies.
7. **API & Database** — detected HTTP routes, ORM/DB models and raw SQL usage.
8. **Website & UI** — interaction inventory, accessibility risks, responsive-layout signals, client-side API calls.
9. **Feature Completeness** — feature matrix built from code/test/doc evidence and acceptance criteria.
10. **Integrations & CLIs** — tool catalogue, project detection, platform playbooks and gated operation.
11. **Quality Gates** — create and evaluate pass/fail gates over the current scan.
12. **AI Providers** — the Custom AI Settings Center: browse the catalogue, configure an endpoint, test connectivity, discover the provider's live model list.
13. **AI Assistant** — chat and per-finding explain/propose, constrained by the privacy policy and redaction.
14. **Repair Planning** — create a plan for a finding, review steps and risk, execute in isolation.
15. **Patch & Diff Viewer** — line-by-line review of every proposed/applied patch, with rollback.
16. **Testing & Verification** — detect the harness, run tests in a chosen workspace, inspect results.
17. **Dependency Manager** — declared dependencies and known-vulnerability findings; on-demand `pip-audit`.
18. **Reports & Export** — generate and export audit reports.
19. **Repair History** — full repair history with snapshots and one-click rollback.
20. **Settings Center** — the 360° control surface described below.

The toolbar carries three live badges — privacy mode, gate verdict and autopilot state.
The autopilot badge is the one that matters while a run is going: it names the keypress
the machine wants *now* (`press Enter to approve`) so an unattended run can be answered
from any screen.

Long operations (scans, test runs, repairs, reports) run on a worker thread, so the
interface stays responsive.

---

## The Settings Center — 360° of control

A single module (`ui/screens/settings.py`) fronts every option in the application, so
nothing is reachable only by editing a file. It is organised into twelve tabs:

| Tab | What it controls |
|---|---|
| **Appearance** | Theme, accent override, font family/size, density, corner radius, motion, sidebar width, start screen, status bar. |
| **Scanning** | Include/exclude globs, size and file-count limits, generated-file skipping, which analyzers are enabled, parallel workers, incremental vs full. |
| **Findings** | Minimum severity, root-cause grouping, auto-acknowledgement of `info` findings, false-positive visibility. |
| **AI** | Behaviour (off / suggest / explain-only / chat), prompt size cap, whether repository content may be sent, default provider. |
| **Privacy** | Privacy mode, and the redaction guarantee — always on, and the switch is locked. There is no telemetry of any kind. |
| **Repair & Safety** | Snapshot-before-write, per-category approvals, isolation, timeout, memory cap, network-in-sandbox, retry limit, backup retention, write-outside-project guard, autopilot gesture window. |
| **Access Control** | The access matrix (see below). |
| **Quality Gates** | Manage gates without leaving settings. |
| **Tools & Commands** | Override the test command and point at custom tool executables. |
| **Notifications** | Completion toasts, log level and log retention. |
| **Background** | Tray icon, close-to-tray, the background host's poll interval and what is installed right now. |
| **Data & Backup** | Export/import the settings file, inspect the data directory, reset. |

**Access levels are enforced, not just stored.** Every capability (`apply_to_original`,
`rollback`, `export_reports`, `purge_history`, `autopilot`, `danger_zone`, …) carries one of:

| Level | Effect |
|---|---|
| `full` | The action runs silently. |
| `ask` | The action is refused unless the caller passes `confirmed=True`. |
| `blocked` | The action is refused even when confirmed. |

`settings_service.check_access()` raises `AccessDenied` at the **service** boundary, so
bypassing the UI does not bypass the policy. An unknown capability defaults to `ask` —
never to `full`.

---

## Quality gates

Gates encode "this project must not ship unless…" as data, evaluated against a scan:

| Metric | Meaning |
|---|---|
| `max_severity_count` | Number of findings at a given severity (with operator + threshold). |
| `tool_available` | A named analyzer actually ran (so a `PARTIAL` scan can fail the gate). |
| `tests_passing` | The latest verification run passed. |
| `no_new_findings` | No regression versus the previous scan. |

Each gate is blocking or advisory, project-scoped or global, and the report includes
the evaluated verdict so a pass is never implied. Gates are **opt-in**: a **Seed
defaults** button offers three sensible starting rules (no criticals, no highs, tools
must have run), but PYFIX 360 does not create blocking gates you did not ask for.

---

## Themes and accessibility

Five themes ship — **Studio Dark**, **Midnight Indigo**, **Deep Forest**, **Daylight**
and **High Contrast** — plus an accent override. The UI is written against semantic
tokens, so a switch recolours everything without touching a widget.

The colour choices are **checkable**, not just asserted:

- Body text is at or above **7:1** contrast on its surface (WCAG AAA); muted text at or
  above **4.5:1** (AA); the high-contrast theme exceeds 15:1.
- Button label colour is **derived from the accent's own luminance**, so no accent —
  including a custom one you type in — can drop its label below AA.
- `theme.contrast_ratio()` is the same formula WCAG uses, and
  `tests/test_theme.py` fails if any pairing regresses.

---

## The repair contract

Every repair follows the same thirteen steps, enforced by the service layer:

1. Select a finding.
2. Create a repair plan (steps, risk assessment, rationale).
3. Take a recoverable snapshot of the project.
4. Copy the project into an isolated workspace (`data/workspaces/proj<id>_plan<id>`).
5. Propose a patch (built-in fixer, or manual guidance when no fixer exists).
6. Produce a clear, reviewable diff.
7. Run the project's test suite on the pristine workspace → **baseline**.
8. Apply the patch to the workspace copy.
9. Run the test suite again → **post-repair**.
10. Compare the two runs.
11. Promote the patch to **verified** only when the comparison is `passed`.
12. Optionally copy verified changes back to the original project (explicit opt-in).
13. Record everything in the audit trail and keep the snapshot for rollback.

Verification outcomes:

| Outcome | Meaning |
|---|---|
| `passed` | Post-repair run passes and shows no regression → patch becomes **verified**. |
| `regression` | A test that passed at baseline now fails. |
| `improved_not_clean` | Some tests fixed, but the suite is still failing. |
| `failed` | No improvement, or new failures. |
| `inconclusive` | No harness, or counts cannot be compared. |

Built-in auto-fixers currently exist for three rules:

| Rule | Fix |
|---|---|
| `py-bare-except` | `except:` → `except Exception:` |
| `sec-yaml-load` | `yaml.load(...)` → `yaml.safe_load(...)` |
| `sec-weak-hash` | `hashlib.md5/sha1(...)` → `hashlib.sha256(...)` |

Every other rule produces a **plan with manual guidance** — PYFIX 360 will not
fabricate a fix it cannot validate.

---

## AI providers, models and privacy

AI is optional, disabled by default, and configured through the **AI Provider
Manager**. No source code leaves the machine unless you:

1. explicitly **enable** a provider,
2. set its privacy mode to permit remote processing, and
3. the gateway confirms the mode is `FULL_REMOTE_APPROVED` for non-local endpoints.

### The provider catalogue

A curated catalogue (`ai/catalog.py`) ships a dozen presets so you do not have to
remember base URLs and model ids:

| Preset | Endpoint | Key required |
|---|---|---|
| Ollama, LM Studio, llama.cpp, vLLM / text-generation-webui | `http://localhost:…` | no — fully offline |
| OpenRouter | `https://openrouter.ai/api/v1` | yes |
| Groq, Cerebras | fast free tiers | yes |
| Google AI Studio (Gemini) | free tier | yes |
| Mistral La Plateforme | free tier | yes |
| DeepSeek Platform | very low cost | yes |
| Together AI | open models | yes |
| Custom OpenAI-compatible | anything you type | depends |

### Models you asked for

The catalogue carries specific entries for the models requested, marked with a
cost label (`free` / `cheap` / `paid`) and a note about availability:

| Model | Catalogue entry | Where it usually lives | Cost label |
|---|---|---|---|
| **Big Pickle** | `big-pickle` (OpenRouter / custom) | Aggregated or self-hosted gateway | `free` |
| **GLM Flash 5.3** | `z-ai/glm-4.6`, `z-ai/glm-4.5-air` | OpenRouter, Z.ai direct | `cheap` / `free` |
| **LongCat 2.5 Preview** | `meituan/longcat` | Meituan → usually via an aggregator | `free` |
| **MiniMax 3.1 Flash** | `minimax/minimax-m2` | MiniMax, OpenRouter | `cheap` |
| **MiMo V2.6** | `xiaomi/mimo` | Xiaomi → usually via an aggregator | `free` |

> **An honest caveat.** Several of these are newer or vendor-specific models whose
> exact ids change without notice, and some (LongCat from Meituan, MiMo from Xiaomi)
> typically reach developers through an aggregator or a self-hosted gateway rather
> than a first-party public API. The catalogue states this rather than pretending the
> ids are stable. When in doubt, use **Discover models** in the provider screen: it
> asks *your* endpoint which ids it currently serves and merges that with the
> catalogue, without inventing prices.
>
> The catalogue is a **reviewed starting point** (`CATALOG_REVIEWED = "2026-10-21"`),
> not a guarantee. `verified_live` is `False` for every entry until PYFIX actually
> completes a call to that endpoint with your key.

### Live discovery (opt-in, metadata-only)

Pressing **Discover models** issues exactly one request — `GET {base_url}/models`,
the documented OpenAI-compatible metadata route. It sends no source code and no
prompt; only your credential travels, to the endpoint you configured. Nothing here
runs on startup, on a timer, or in the background.

Privacy modes:

| Mode | Behaviour |
|---|---|
| `LOCAL_ONLY` | Only local endpoints (Ollama, LM Studio, llama.cpp, `localhost`). Remote providers are refused. |
| `REDACTED_REMOTE` | Remote allowed **only** when combined with explicit full approval; secrets are always redacted first. |
| `FULL_REMOTE_APPROVED` | Remote source transmission explicitly approved by the user. |

Additional guarantees:

- **Redaction before send.** Credentials, API keys, tokens, private keys, connection
  strings and high-entropy secrets are replaced with a `[REDACTED:…]` placeholder
  naming the kind of secret *before* a prompt is built. This applies to finding
  context and to diffs. Redaction is **unconditional**: the `redact_before_ai` setting
  is locked on, the UI shows the switch disabled, and the settings store refuses a
  write that would turn it off.
- **Metadata-only logging.** AI request rows store a prompt **SHA-256**, never the
  prompt text or the secret values. Chat history stores **redacted text only**.
- **Untrusted input.** Repository content is treated as untrusted data, never as
  instructions to the model.
- **Keys are never in SQLite.** API keys live in the OS credential store (keyring) or,
  as a fallback, an encrypted file vault. The fallback's weakness is surfaced in the UI.
- **Fails safe.** A provider pointing at an unknown host is treated as **remote**, so a
  misconfiguration earns the stricter checks, never a free pass. The host is compared
  **exactly**: `localhost` inside a URL is not the host `localhost`, so
  `http://localhost.evil.com/v1` is remote, not local.
- **Discovery is a remote call.** *Discover models* obeys the same policy as sending a
  prompt — it is refused unless the privacy mode is `FULL_REMOTE_APPROVED` and the
  `remote_ai` capability permits it.
- **No telemetry.** Nothing about how you use the app is counted or transmitted.

---

## AI app builders — platform playbooks

Seven AI app builders and prompt workbenches are catalogued with the knowledge
needed to drive them:

| Platform | Entry | How it can be driven |
|---|---|---|
| Lovable | `lovable` | API (list / read / deploy) + browser for prompting |
| Trickle | `trickle` | browser only — no public API |
| Replit | `replit` | GraphQL API + browser |
| Emergent | `emergent` | browser only — no public API |
| v0 | `v0` | REST API (`api.v0.dev`) + browser |
| Builder.io | `builder` | Content API + the `builder` CLI + browser |
| Google AI Studio | `ai-studio` | Generative Language API + browser |

Each entry carries a **playbook**, not just a name:

- **Prompt patterns** — the templates that work on that platform, with the reason
  each one is shaped the way it is.
- **Command dialect** — how that platform expects to be instructed, and what makes
  a request land badly.
- **Questions and answers** — the clarifying questions each platform asks, with the
  answer that unblocks it fastest.
- **Situations** — what to do when the build stalls, when the preview breaks, when
  the credits run out, when a secret leaks into the generated code.
- **Verification state** — `reviewed_on` records when the vendor's own
  documentation was last read, and `verified_live` is `false` for every platform
  because PYFIX has never driven them from inside this build.

Open **Integrations & CLIs → Platform Playbooks** to browse them.

### Operating a platform

The **Operate a platform** tab sends a real prompt. There are two paths:

- **API** — the stable one. v0, Lovable, Replit, Builder.io and AI Studio have REST
  surfaces. The client lives in `pyfix360/integrations/api_clients.py` and uses only
  the standard library, so nothing extra has to be installed. The API key is kept in
  the OS keyring, never in the database.
- **Browser** — the fallback, for the platforms that publish no API. It drives a real
  signed-in browser session through Playwright:

  ```bash
  pip install -e ".[automation]"
  python -m playwright install chromium
  ```

  Selectors live in `pyfix360/integrations/browser.py` and can be overridden per
  platform, because a vendor redesign breaks them. A failed operation writes a
  screenshot into the log directory so the breakage can be diagnosed.

The flow is the same for both paths: pick the platform, save the API key (it goes
into the OS keyring, never the database), type the prompt, press **Preview what
will be sent** to see the exact redacted text, then **Send via API** or **Send via
browser**. The returned job handle goes into the **Job handle** field, where
**Read status** follows the build and **Send follow-up** answers the questions the
platform asks back.

Both paths are gated exactly like the AI assistant: the `remote_ai` capability in
Settings → Access Control decides whether the call is allowed and whether it needs
confirmation, the privacy mode must be `full_remote_approved`, the text is redacted
before it leaves the machine, and every call is written to the audit trail.

**Honest limitations:**

- Nothing in the platform catalogue has been driven live from this build. Treat every
  entry as a starting point to verify against the vendor's own documentation.
- The Replit GraphQL operation names are community-documented, not published in the
  official Agent docs. Verify them against a live account before depending on them.
- The Lovable public API manages and deploys existing projects; it cannot build an
  app from a prompt. Prompting there needs the browser.
- Browser automation drives a page, not an API. It breaks when the UI changes, and it
  is the only way to reach Trickle and Emergent.

---

## Autopilot — unattended runs you can interrupt

Autopilot runs the pipeline for the active project without you clicking through it, and
stops the moment you disagree. The queue that ships with the app is deliberately ordered
so it fails cheapest:

1. **Scan the project** — ungated, always runs.
2. **Run the test suite** — gated on the `run_tests` capability.
3. **Apply the newest repair plan to the original** — gated on `apply_to_original`, which
   ships as *ask*. This is the step that can write to your project, so an unattended run
   cannot get past it silently.
4. **Evaluate quality gates** — ungated.
5. **Export a report** — gated on `export_reports`.

The machine is `IDLE → RUNNING → PAUSED → RUNNING`, driven by a `QTimer`, and every
transition, refusal, approval and failure is written to the audit trail with
`actor=autopilot`.

### The Enter gesture

| Keys | Meaning |
|---|---|
| **Double Enter** | Pause the run. |
| **Triple Enter** | Resume it. |
| **Enter** at an approval gate | Approve that one step and continue. |
| **Esc** at an approval gate | Skip that one step; the rest of the queue still runs. |

**The grace window is the whole trick.** A triple *contains* a double, so acting on the
second Enter would make every resume pause first. The second Enter therefore never acts on
its own: it opens a window (500 ms by default, measured from the **first** Enter of the
gesture, not rolling between presses). A third Enter inside that window resolves as a triple
and cancels the pending pause; if the window closes with only two Enters in it, the gesture
was a double and the pause happens then. A `QTimer` resolves the expiry, so a lone double
still pauses without a third keypress. Tune the window in **Settings → Repair & Safety**.

### Why an event filter, not a shortcut

A `QShortcut` for `Return` never fires while a `QPlainTextEdit` has focus — the text widget
consumes the key first, and that is exactly the widget you are looking at when you want to
interrupt a run. The gesture is caught by an application-level `QEventFilter` installed on
`QApplication`, which runs *ahead* of the focused widget and never consumes the key. Three
rules keep it from corrupting ordinary typing, and each is covered by a test:

- Inside a text widget, Enter counts only on a **blank line** — two Enters in a row is what
  a blank line looks like, so that is the signal. An Enter that inserts a newline after real
  prose belongs to the prose.
- **Auto-repeat is dropped**: one long-held Enter is not a burst of presses.
- Any other keystroke between two Enters, and any Enter inside a **modal dialog**, voids the
  gesture. The dialog's default button owns that key.

### Approvals are not gestures

When a step reaches a capability set to *ask*, the run pauses with reason `approval` and the
toolbar badge says `press Enter to approve`. From there one Enter — or the **Approve**
button on the Autopilot screen, for anyone who cannot see the prompt — confirms that step
and nothing else. Esc skips that step and lets the rest of the queue run. A *blocked*
capability is refused in place, audited as a warning, and the run continues, because
"skipped" and "not allowed" are different facts.

> **Not a daemon.** Autopilot is an in-app, attended-by-default run: it needs the window
> open and it stops when the app closes. The background host below is the piece that runs
> with no window at all. See
> [`docs/OPERATING.md`](docs/OPERATING.md#15-autopilot) for the operating procedure.

---

## Background and headless operation

The desktop run needs a keyboard. The background host is the same state machine —
same steps, same capabilities, same audit chain — driven by a process with no window
that works off a queue stored in the database.

```powershell
pyfix360 service probe                 # what this machine allows, and what each mode costs
pyfix360 service install --mode task   # register it so it starts by itself
pyfix360 service enqueue 1 --confirmed # put a run on the queue
pyfix360 service status                # what is installed, and whether it is running
pyfix360 service logs -n 40            # the tail of logs/service.log
pyfix360 service stop                  # ask it to finish the current step, then exit
```

### Five modes, because "install a service" is not one thing

`service install` with no `--mode` picks the strongest mode this machine will actually
allow, and says which it picked. Naming a mode the machine refuses is an error with the
reason in it — never a silent downgrade, because surviving your logoff is exactly what
was asked for.

| Mode | Needs admin | Survives logoff | Starts at |
|---|---|---|---|
| `service` | yes | yes | boot — a real service in session 0, as a machine account |
| `nssm` | yes | yes | boot — same, for hosts without pywin32 |
| `task-account` | no | yes | boot — a scheduled task with stored credentials, as you |
| `task` | no | no | logon — no rights needed, and Windows ends it at logoff |
| `process` | no | no | when you run it — a detached child; nothing is installed |

Every report from `install` names the mode it used and what that mode cannot do, in the
same message, before you have to ask.

### What an unattended run does at an approval gate

Nothing it was not told to. A capability set to *ask* pauses the desktop run behind an
"press Enter to approve" prompt; a daemon has no Enter to press. Starting an
`ASK`-gated *run* from the background host is therefore **refused outright** unless the
job was queued with `--confirmed`, and a gate reached mid-queue **parks** the job
(`waiting_approval`) with the exact command that answers it written into the row and the
log:

```
pyfix360 service approve 4          # or: service skip 4
```

That is the access matrix keeping its meaning after the human leaves the room, and it is
the reason the queue lives in the database rather than in a daemon's memory: the row is
the only channel between the process that asks for work and the process that does it.

### Two limits, stated rather than worked around

- **The process is visible in Task Manager.** A windowless host with no taskbar entry is
  what "background" means here. Leaving the process list would need process hiding,
  which is a rootkit technique, not a feature.
- **A service runs as a machine account, so it cannot unlock the credentials you stored.**
  A service account has its own keyring, and the encrypted-file fallback is keyed to your
  user, so a secret you saved simply is not reachable. A step that needs one then sends no
  key and fails at the provider's refusal — the job neither runs as you nor invents a
  credential. `task-account` is the mode that keeps your identity without administrator
  rights.

A cancelled or crashed job is reported as `interrupted`, never as `failed`: nobody can
claim a repair was applied and rolled back when the process that made it is gone. The
reaper only calls a job abandoned after a stale heartbeat *and* a dead pid, so a host
grinding through a forty-second test suite is left alone.

### The desktop half

`Settings → Background` holds the tray icon and the "hide to tray instead of quitting"
switch, and the Autopilot screen grew a **Background queue** card that lists jobs, queues
a run, and answers or cancels a parked one — the same rows the CLI reads, so the app and
the console never disagree about what is running. Closing the window hides the app while
a host is working; the tray menu shows the host's state and the queue's depth, and the
same panel states plainly that hiding a window is not hiding a process.

Full procedure, trade-offs and troubleshooting:
[`docs/OPERATING.md` §16](docs/OPERATING.md#16-background-and-headless-operation).

---

## Packaging and distribution

Ships as a self-contained **onedir** Windows build plus an installer. No network
access is needed at runtime, and no packed analyser is faked — external tools
stay external.

```powershell
# from the repository root
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

`build.ps1` regenerates the icon, runs the test suite, builds the bundle, smoke-tests
the frozen CLI, and compiles the installer when Inno Setup is installed. The result:

| Artefact | Path |
|---|---|
| Application | `dist\PYFIX360\PYFIX360.exe` |
| Headless CLI | `dist\PYFIX360\PYFIX360-cli.exe` |
| Installer (if ISCC present) | `packaging\Output\Setup_PYFIX360_v0.1.0.exe` |

| File | Purpose |
|---|---|
| `packaging/logo_master.png` | The committed source of truth for the logo (512×512 RGBA). |
| `packaging/make_icon.py` | Deterministically turns the master into `pyfix360.ico` (7 resolutions, 16→256) with Pillow — no design tool needed. |
| `packaging/pyfix360.spec` | PyInstaller onedir spec (GUI + CLI in one folder). |
| `packaging/build.ps1` | One-command build + checks. |
| `packaging/installer.iss` | Inno Setup script: per-user install, optional desktop/PATH integration, keeps your data on uninstall unless you say otherwise. |

Why **onedir** rather than onefile: a onefile build unpacks several hundred MB to a
temp folder on every launch, and self-extracting single executables are a common
antivirus false positive. The installer still gives the end user a single setup
program. `bandit`, `pip-audit`, `pytest` and the GUI toolkits are excluded from the
bundle on purpose — they must remain external commands so tool availability reporting
stays truthful.

### Releases and the auto-update

Every push to `main` on GitHub runs `.github/workflows/release.yml`: it builds on
`windows-latest`, runs the test suite (a failing test stops the release), compiles
the installer and publishes a tagged release. The version is the project version plus
the run number — `0.1.0.142`, `0.1.0.143`, … — so each push has its own version and
the order of the releases is the order of the commits.

Each release carries the installer, a portable zip, `checksums.txt` and
`latest.json` — the manifest the updater reads.

The installed copy updates itself:

```
Settings → Data & Backup → Application updates → Check for updates
pyfix360 update --check      # look, report, change nothing
pyfix360 update --yes        # look, download, verify, install
```

The download is verified against the published SHA-256 and a file that does not
match is deleted instead of installed. The installer then closes the running
application, replaces the installed folder and starts the new version.

A check is a remote call, so it goes through the access matrix like every other
remote action. `access_check_for_updates` defaults to **ask** — nothing leaves the
machine until you ask for it. **full** lets the app check on its own at startup;
**blocked** means it never contacts GitHub.

---

## Architecture

Strict layering — the UI never talks to the database directly, and the ORM never
leaks into the UI or CLI.

```
src/pyfix360/
├── models.py                 Pydantic domain models + enums (the service boundary types)
├── config.py                 AppConfig, data directories, runtime limits
├── cli.py / __main__.py      Headless command-line driver
├── service_host.py           The windowless host process: pid + stop markers, generated
│                             launcher, session-0 data-dir handover, service class
├── app.py                    Desktop entry point (loads settings, applies theme, sets icon)
├── db/
│   ├── models.py             SQLAlchemy ORM rows (~22 tables)
│   └── session.py            Engine, session_scope, init_db
├── analysis/
│   ├── language_detect.py    Language / framework / build / harness / package-manager detection
│   ├── python_analyzer.py    Built-in AST rule engine
│   ├── tool_adapters.py      Ruff / Bandit / pip-audit adapters (skip-with-reason)
│   └── fingerprints.py       Stable finding keys and root-cause grouping
├── services/
│   ├── project_service.py    Import (folder/ZIP/git), file registration, dependency parsing
│   ├── scan_service.py       Scan orchestration, dedup, correlation, incremental carry-forward
│   ├── findings_service.py   The finding list: severity floor, false-positive hiding,
│   │                         root-cause grouping, auto-acknowledgement
│   ├── repair_service.py     The 13-step repair contract, workspace isolation, rollback,
│   │                         per-category approvals
│   ├── test_runner.py        Harness detection, run, result parsing, baseline comparison
│   ├── snapshot_service.py   Recoverable snapshots
│   ├── feature_service.py    Feature completeness matrix
│   ├── report_service.py     Markdown / JSON / HTML reports
│   ├── settings_service.py   Settings persistence, access-control enforcement, config sync
│   ├── gate_service.py       Quality-gate CRUD and evaluation
│   ├── ai_provider_service.py Catalogue-driven provider CRUD, credentials, discovery, verification
│   ├── ai_chat_service.py    Policy-guarded chat/explain/propose + redacted history
│   ├── integration_service.py Tool knowledge, project detection, playbook rendering
│   ├── platform_service.py    Access/privacy gates + audit for operating a platform
│   ├── autopilot_service.py  Qt-free run machine: grace-window gestures, approval gates, audit
│   ├── job_service.py        The persisted queue: claims, heartbeats, answers, cancelling,
│   │                         and reaping a job whose host went quiet *and* died
│   ├── background_service.py  The same machine with no window: inline steps, parked gates
│   ├── host_service.py        Install/start/stop/status as a decision tree over injected
│   │                         commands (service, nssm, scheduled task, detached process)
│   └── audit.py              Audit-trail recording
├── patching/
│   ├── diff.py               Unified diff generation, parsing and application
│   └── fixers.py             Built-in fixers + manual guidance map
├── sandbox/executor.py       Isolated command execution (timeout, env scrub, output caps)
├── ai/
│   ├── catalog.py            Curated provider/model catalogue + presets (data only, no I/O)
│   ├── discovery.py          Opt-in, metadata-only `GET /models` discovery
│   ├── gateway.py            Privacy enforcement (check_permission), redaction-then-send
│   └── redaction.py          Secret/credential redaction and diff redaction
├── integrations/
│   ├── catalog.py            Tool + platform catalogue (playbooks, prompt patterns, Q&A flows)
│   ├── api_clients.py        REST clients for v0, Lovable, Replit, Builder.io, AI Studio
│   └── browser.py            Playwright automation for the platforms that publish no API
├── credentials/store.py      Keyring-preferred credential store with encrypted-file fallback
└── ui/
    ├── theme.py              Theme engine: 5 themes, colour theory, contrast checks, stylesheet()
    ├── workers.py            Background task helper (holds the worker until it reports back)
    ├── context.py            Shared application context — owns the one autopilot controller
    ├── autopilot.py          App-level Enter gesture filter, the QTimer ticker, worker dispatch
    ├── widgets.py            Reusable, theme-aware, responsive widgets
    ├── tray.py               Optional tray icon: the host's state, the queue's depth, and
    │                         start/stop for the process mode
    ├── main_window.py        Collapsible sidebar + stacked screens (20), live theme switch
    │                         and hide-to-tray on close
    └── screens/              One module per screen
```

**Finding identity.** A finding's stable key is a SHA-256 of
`(rule_id, relative_path, symbol, normalized_snippet)`, so keys survive line drift.
The **root-cause group** is a shorter hash of `(rule, file, symbol)` and links
recurrences of the same underlying problem.

**Incremental scanning.** Each tracked file stores the scan id and content hash of its
last analysis. Unchanged files carry their previous findings forward; only changed
files are re-parsed. A full rescan is available at any time.

---

## Supported languages — an honest matrix

PYFIX 360 does **not** claim universal language support. Depth is explicit:

| Language | Inventory / detect | Full rule analysis | Auto-fix | Static UI audit |
|---|---|---|---|---|
| **Python** | ✅ | ✅ AST analyzer (~18 rules) + optional Ruff/Bandit | ✅ (3 rules) | ❌ |
| JavaScript / TypeScript | ✅ | ❌ (inventory + frontend audits only) | ❌ | ✅ (JSX/DOM patterns, client API calls) |
| HTML | ✅ | ❌ | ❌ | ✅ (forms, buttons, links, a11y, viewport) |
| CSS / SCSS / LESS | ✅ | ❌ | ❌ | ✅ (`@media` responsive signals) |
| Vue / Svelte | ✅ | ❌ | ❌ | ✅ (static markup inspection) |
| Java, Kotlin, C/C++, C#, Go, Rust, Ruby, PHP, Swift, Scala, Shell, PowerShell, SQL, Dockerfile | ✅ | ❌ | ❌ | ❌ |
| Config: JSON, YAML, TOML, INI, XML, Markdown, RST | ✅ (inventory, deps, docs evidence) | ❌ | ❌ | ❌ |

- "Inventory / detect" means the file is registered, its language recorded, and its
  dependencies / documentation harvested where applicable.
- **Full rule analysis** currently means: built-in Python AST rules plus adapters for
  Ruff (Python), Bandit (Python security) and pip-audit (Python dependency vulns).
  Files in other languages are **not** silently analysed — they are listed and the
  limitation is stated in the report.
- The API/Database Inspector and the Website/UI Inspector parse **Python, HTML and
  JSX/TSX-style markup statically**. Runtime interaction flows (real browser clicks,
  rendered accessibility trees) require Playwright, which is not configured.

---

## Built-in analysis rules

The always-available Python analyzer emits these rule ids:

| Rule id | Category | Severity | What it flags |
|---|---|---|---|
| `py-syntax-error` | bug | high | File does not parse (analysis of that file stops). |
| `py-mutable-default` | bug | medium | Mutable default argument (`def f(x=[])`). |
| `py-bare-except` | bug | medium | Bare `except:` clause. |
| `py-swallowed-exception` | bug | low | `except` body that only `pass`es. |
| `hardcoded-secret` | security | critical | Literal credential assigned to a credential-looking name. |
| `hardcoded-aws-key` | security | critical | `AKIA…` access key literal. |
| `hardcoded-private-key` | security | critical | Embedded PEM private key. |
| `hardcoded-api-token` | security | high | `sk-`/`pk-`/`rk-` style token literal. |
| `hardcoded-url-creds` | security | high | Password embedded in a connection URL. |
| `sec-dynamic-exec` | security | high | `eval` / `exec` / `compile` / `__import__`. |
| `sec-assert-auth` | security | high | `assert` used for an authentication/authorization check. |
| `sec-subprocess-shell` | security | high | `subprocess.*` with `shell=True`. |
| `sec-os-system` | security | high | `os.system(...)`. |
| `sec-pickle` | security | high | `pickle.load(s)` on untrusted data. |
| `sec-marshal` | security | medium | `marshal.load`. |
| `sec-yaml-load` | security | high | `yaml.load` without a safe loader. |
| `sec-insecure-temp` | security | medium | `tempfile.mktemp`. |
| `sec-weak-hash` | security | low | `hashlib.md5` / `hashlib.sha1`. |
| `sec-weak-random` | security | low | `random.*` used for tokens/salts/keys. |
| `cmp-todo-marker` | completeness | info | Unresolved TODO/FIXME. |
| `io-unreadable` | bug | low | File could not be read (encoding/permissions). |

Findings carry a **confidence** (`high`/`medium`/`low`) and a **status**
(`detected` → `planned` → `fix_proposed` → `fix_applied` → `verified` / `failed` /
`rolled_back`). This makes the difference between *detected*, *proposed*, *applied*,
*verified* and *unresolved* explicit everywhere in the UI and reports.

---

## Safety model and known limitations

**Enforced:**

- Projects are never executed on the host without explicit approval; tests run in an
  isolated workspace copy with a hard timeout and process-tree termination. Isolation
  is not optional — turning it off **refuses** the repair and the test run.
- Every applied change has a recoverable snapshot and a clear diff.
- Destructive operations (production changes, DB migrations, security changes, major
  dependency upgrades) require explicit approval, per category.
- An untested patch is never labelled verified.
- Credentials are redacted before any prompt is built; API keys are never stored in
  the database. Redaction cannot be switched off.
- The access policy is enforced at the **service** boundary, so bypassing the UI does
  not bypass the policy. An unknown capability defaults to *ask*, never to *full*.
- The DB stores a prompt **hash**, never the prompt text, and never a secret value.

**Not enforced — stated honestly, not hidden:**

- **Network isolation is NOT enforced at OS level.** The sandbox limits the working
  directory and scrubs the environment, but a test process can still reach the
  network. Run untrusted projects in a container or VM.
- **Memory limits are best-effort on Windows** (no Job Object helper is installed);
  the timeout is the primary hard stop.
- **Feature completeness detection is heuristic.** `complete` requires only a test
  reference; it does not prove the behaviour is correct.
- **External tools are optional.** Without Bandit and pip-audit installed, security
  and dependency coverage is narrower, and the scan is marked `PARTIAL` with the
  skipped tools listed.
- **Non-Python analysis is inventory-only** unless a matching adapter is available.
- **A background host is a visible process.** It has no window and no taskbar entry, but
  it is in Task Manager and in `tasklist`. Leaving the process list would need process
  hiding, which is a rootkit technique rather than a feature, so this app does not try.
- **A service cannot unlock your credentials.** As a machine account it has a keyring of
  its own, and the encrypted-file fallback is keyed to your user, so a secret you saved is
  not reachable — the step sends no key and the provider refuses it, rather than the job
  running as you. `task-account` is the mode that keeps your identity without admin rights.
- **A running step cannot be interrupted.** Cancellation and stopping land between steps;
  a job whose host died is recorded `interrupted`, not `failed`, because nobody can prove
  whether a repair was half-applied.

---

## Database and migrations

SQLite via SQLAlchemy, with Alembic migrations.

```bash
pyfix360 init                                   # create the DB at the current revision
alembic upgrade head                            # apply migrations manually
alembic revision --autogenerate -m "message"    # create a new migration
alembic downgrade -1                            # step back one revision
```

`pyfix360 init` and Alembic are **reconciled, not competing**. `init` creates the
tables from the ORM metadata, adds any missing column to a table that already existed,
and then stamps the revision table at the current head — so `alembic upgrade head`
afterwards is a no-op instead of a "table already exists" failure. Nothing is dropped
or retyped, so an existing install upgrades in place. The migration scripts live in
`src/pyfix360/migrations/versions/`.

The initial schema creates 18 tables covering projects, files, modules, scans,
findings, evidence, dependencies, repair plans, patches, test runs, test cases,
snapshots, features and checks, settings, AI providers, AI requests and the audit
trail. A follow-up revision adds **quality gates** and **AI chat history** and
extends `ai_providers` with catalogue/verification columns; both upgrade and
downgrade are supported on SQLite (via `batch_alter_table`). See
[`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) for the full entity map and the
finding/repair lifecycle.

---

## Development

```bash
pip install -e ".[ui,credentials,dev]"

# run the test suite
python -m pytest -q

# lint
ruff check src tests

# build the distributable (see Packaging and distribution)
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

The repository has **411** unit and integration tests. Coverage includes
fingerprinting, diff generation and application, fixers, redaction and AI privacy
enforcement, live model discovery, the credential store, language detection, the
Python analyzer, project import (folder, ZIP, git, PEP 621 and npm dependency
parsing), the scan service, the full repair workflow (happy path, apply + rollback,
failed-verification rollback, audit coverage), the test runner and baseline
comparison, feature analysis, reports, the sandbox executor, the settings store and
access-control policy, quality gates, the AI catalogue, the platform playbooks and
their API/browser operation paths, the autopilot state machine (including every
grace-window case), its Enter gesture filter and the desktop wiring that makes them
work, the theme engine and its colour-contrast contract, and the CLI acceptance path.

### Test conventions

- Tests use the `data_dir`, `session`, `sample_project`, `no_keyring` and `qapp`
  fixtures from `tests/conftest.py`.
- Anything that needs real widgets — the gesture filter, the autopilot screen, the
  toolbar badge — takes `qapp`, and is skipped where no display server is reachable.
- UI tests take `data_dir` rather than `session`: the widgets open and commit their
  own database sessions, so borrowing the fixture session would nest transactions.
- The Python analyzer returns **only** a `py-syntax-error` finding for a file that
  does not parse, so tests that seed several defects place the syntax error in a
  separate file.
- Bandit and pip-audit are genuinely optional; scans in a bare environment are
  `PARTIAL` and tests assert that the skipped tools are disclosed.

---

## License

Proprietary. © PYFIX 360 Team.
