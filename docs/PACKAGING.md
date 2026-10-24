# Packaging PYFIX 360

PYFIX 360 ships as a self-contained Windows application: a **onedir** bundle that runs
with no Python installed, plus an optional **Inno Setup** installer. Nothing is
downloaded at runtime, and no analyser is faked.

---

## 1. What you produce

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

| Artefact | Path |
|---|---|
| Application | `dist\PYFIX360\PYFIX360.exe` |
| Headless CLI | `dist\PYFIX360\PYFIX360-cli.exe` |
| Icon | `packaging\pyfix360.ico` |
| Installer (if ISCC is present) | `packaging\Output\Setup_PYFIX360_v0.1.0.exe` |

The two executables share **one** copy of the Python runtime and Qt libraries (~500 MB
saved versus two independent builds).

---

## 2. The build script

`packaging/build.ps1` runs, in order:

1. checks Python is usable;
2. installs `pyinstaller`, `pillow`, `PySide6`, `keyring`;
3. regenerates the icon (`make_icon.py`) — deterministic, no design tool needed;
4. **runs the full test suite** — a broken build fails here, not on the user's machine;
5. runs a clean onedir build from `packaging/pyfix360.spec`;
6. smoke-tests the frozen CLI (`PYFIX360-cli.exe --version`);
7. compiles the installer if `ISCC.exe` is on `PATH` (or in the default Inno Setup 6
   location).

Useful flags:

| Flag | Effect |
|---|---|
| `-SkipTests` | Skip step 4 (use only when you have just run the suite). |
| `-SkipInstaller` | Build the folder only, no installer. |
| `-Python <path>` | Use a specific interpreter. |
| `-Version <x.y.z>` | Stamp a version into the installer (`/DMyAppVersion=…`). |

The script finds `ISCC.exe` on `PATH`, in `C:\Program Files (x86)\Inno Setup 6`,
in `C:\Program Files\Inno Setup 6`, and in the per-user location
`%LOCALAPPDATA%\Programs\Inno Setup 6`.

---

## 3. Why onedir, not onefile

| Concern | onefile | onedir (chosen) |
|---|---|---|
| Startup | Unpacks several hundred MB to a temp dir **every launch** | Files already on disk; fast start |
| Antivirus | Self-extracting single executables are a common false positive | A plain folder with a small launcher is unremarkable |
| Inspectability | Opaque blob | The user can see exactly what is inside — fitting for an offline-first tool whose pitch is "nothing hidden" |

The installer still delivers a single setup program to the end user.

---

## 4. What the bundle contains

* `PYFIX360.exe` — GUI launcher, built with `console=False` (no console window).
* `PYFIX360-cli.exe` — headless launcher, built with `console=True` so piping and
  `--help` behave normally.
* `_internal\` — the Python runtime, Qt libraries and Qt plugins.
* `pyfix360.ico` — the window/taskbar logo, available even before the installer
  registers an icon.

The database schema is created from the SQLAlchemy metadata at runtime, so **no
migration or template files need to ship**. The Alembic ini/data files are included
only if present, for users who run the migration commands from the CLI.

---

## 5. What the bundle deliberately omits

These are in the spec's `excludes` list **on purpose**:

| Excluded | Why |
|---|---|
| `bandit`, `pip_audit` | They are *optional external analysers*. Freezing them would make tool-availability reporting lie. |
| `pytest` | The user's own projects bring their own test runner. |
| `numpy`, `pandas`, `matplotlib` | Not used by the app; large. |
| `tkinter`, `PyQt5`, `PyQt6`, `PySide2` | Other GUI toolkits; only PySide6 is used. |

---

## 6. The icon

`packaging/logo_master.png` is the source of truth: a 512×512 transparent PNG of the
mark — a dark rounded tile with a navy rim, a blue **orbit ring with an arrowhead**
(the 360° rotation) and a white **check** (the verified repair).

`packaging/make_icon.py` turns that master into the multi-resolution `.ico` that
Windows uses for the executable, the shortcuts, the taskbar and the notification
area. The `.ico` carries **16, 24, 32, 48, 64, 128 and 256 px** frames; the small
frames get a light unsharp mask, because downscaling averages the blue ring and
the dark tile together and turns the edge muddy below 48 px.

The script also writes `pyfix360_icon_preview.png` (512 px) and
`pyfix360_icon_sizes.png` (a strip of the small sizes) for the README and for
verifying legibility.

Everything is deterministic and needs nothing but Pillow — no design tool, no
image model — so anyone who checks out the repository rebuilds the icon byte for
byte.

To regenerate:

```powershell
python packaging\make_icon.py
```

---

## 7. The installer

`packaging/installer.iss` is an Inno Setup 6 script:

* **Per-user install** (`PrivilegesRequired=lowest`) — no UAC prompt, no admin rights.
  A dialog override is allowed if an admin wants a machine-wide install.
* Optional **desktop shortcut** (checked once by default) and optional **PATH
  integration** for the CLI.
* `SetupIconFile` uses the generated `.ico`; the uninstall entry uses the app icon.
* **Uninstall keeps your data.** The data directory
  (`%LOCALAPPDATA%\PYFIX360`) holds imported projects, findings, audit history,
  snapshots and reports. On uninstall the script asks explicitly, defaulting to
  **keep**; choosing *No* deletes it permanently.
* Fails early with a clear message if the bundle was not built first.

Compile manually:

```powershell
ISCC.exe /DMyDistDir="C:\path\to\dist\PYFIX360" packaging\installer.iss
```

---

## 8. The release pipeline (GitHub Actions)

`.github/workflows/release.yml` builds and publishes on **every push to `main`**,
on a `v*` tag, and on demand from the Actions tab.

| Trigger | Version published | Tag |
|---|---|---|
| Push to `main` | `0.1.0.<run number>` — e.g. `0.1.0.142` | `v0.1.0.142` |
| Push of `v1.2.3` | `1.2.3` | `v1.2.3` |
| *Actions → Run workflow* | `0.1.0.<run number>` | `v0.1.0.<run number>` |

The run number comes from GitHub and only ever grows, so the order of the
releases is the order of the commits that produced them.

The job runs on `windows-latest` and does exactly what `build.ps1` does, plus the
release bookkeeping:

1. check out, set up Python 3.12, install the dependencies;
2. compute the version and write it into `src/pyfix360/__init__.py`;
3. run the test suite — **a failing test stops the release**;
4. regenerate the icon, run PyInstaller, smoke-test the frozen CLI;
5. `choco install innosetup` and compile the installer;
6. collect the artefacts and publish a GitHub release.

Every release carries four assets:

| Asset | Purpose |
|---|---|
| `Setup_PYFIX360_v<version>.exe` | the installer (per-user, no admin rights needed) |
| `PYFIX360-v<version>-portable.zip` | the application folder, no install |
| `checksums.txt` | SHA-256 of the installer |
| `latest.json` | the manifest the in-app updater reads |

`latest.json` is the contract between the pipeline and the updater:

```json
{
  "version": "0.1.0.142",
  "installer": "Setup_PYFIX360_v0.1.0.142.exe",
  "url": "https://github.com/MansurAzad/PYFIX360/releases/download/v0.1.0.142/Setup_PYFIX360_v0.1.0.142.exe",
  "sha256": "…",
  "size": 91234567
}
```

The file name deliberately contains no space: it travels in a URL.

---

## 9. The auto-update

`src/pyfix360/services/update_service.py` makes the installed copy update itself.

```
Settings → Check for updates        →  manual check
pyfix360 update --check              →  the same, from the terminal
pyfix360 update --yes                →  check, download and install
```

**What it does**

1. reads the newest published release from the GitHub API and fetches its
   `latest.json`;
2. compares the version with the running one — if it is not newer, it stops
   there;
3. downloads the installer and verifies it against the published SHA-256. A file
   that does not match is **deleted, not installed**;
4. runs the installer with `/VERYSILENT /NORESTART /CLOSEAPPLICATIONS
   /RESTARTAPPLICATIONS`. The Windows Restart Manager closes the running
   application, the installer replaces the installed folder and the shortcuts,
   and the new version is started again.

**What it will not do**

A check is a remote call, so it goes through the access matrix like every other
remote action:

| `access_check_for_updates` | Behaviour |
|---|---|
| `ask` (default) | Nothing leaves the machine until you press *Check for updates*. |
| `full` | The app checks on its own at startup, and may install automatically if `update_auto_install` is on. |
| `blocked` | The app never contacts GitHub at all. |

The repository it reads is `MansurAzad/PYFIX360`; a fork can point elsewhere
with the `PYFIX360_UPDATE_REPO` environment variable.

---

## 10. Running without installing

The bundle is portable: copy `dist\PYFIX360\` anywhere and run `PYFIX360.exe`. Data
still lives under `%LOCALAPPDATA%\PYFIX360` unless you set `PYFIX360_DATA_DIR`:

```powershell
$env:PYFIX360_DATA_DIR = "D:\pyfix-data"
.\PYFIX360.exe
```

---

## 11. Troubleshooting the build

| Symptom | Likely cause / fix |
|---|---|
| `Invalid argument type for COLLECT` | An EXE/COLLECT nesting error in the spec. Both launchers must go into a **single** `COLLECT`. |
| Missing Qt platform plugin | Ensure `collect_submodules("pyfix360")` and the Qt hooks ran; a clean rebuild (`--clean`) fixes a stale cache. |
| Antivirus quarantines the launcher | Build onedir (already the default); add a signing step if you distribute widely. |
| `ISCC.exe not found` | Install Inno Setup 6, or pass `-SkipInstaller`. The folder build is complete and runnable without it. |
| Frozen CLI prints nothing | Check it was built with `console=True`; the GUI exe intentionally has no console. |

---

## See also

* [`../README.md`](../README.md) — *Packaging and distribution*.
* [`AI.md`](AI.md) — the frozen build preserves every AI privacy guarantee.
