# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for PYFIX 360 — onedir build.

Build with:

    pyinstaller packaging/pyfix360.spec --noconfirm --clean

Why onedir (a folder) rather than onefile (a single .exe):

* **startup time** — a onefile build unpacks the whole bundle to a temp
  directory on every launch. For a Qt app that is several hundred milliseconds
  of pure waste.
* **antivirus** — self-extracting single executables are a common false
  positive. A plain folder with a small launcher is unremarkable.
* **inspectability** — the user can see exactly what is inside, which suits an
  offline-first tool whose whole pitch is "nothing hidden".

The installer (see ``installer.iss``) packages this folder, so the end user
still gets a single setup program.
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# spec files are executed by PyInstaller, not imported, so __file__ works but
# there is no package context to import from.
SPEC_DIR = Path(SPECPATH).resolve()
ROOT = SPEC_DIR.parent
SRC = ROOT / "src"
ICON = SPEC_DIR / "pyfix360.ico"

block_cipher = None

# --- data files ------------------------------------------------------------
# The schema is created from the SQLAlchemy metadata at runtime, so no
# migration or template files must ship. We only bundle the icon so the
# window/taskbar logo is available even before the installer registers it.
datas = [(str(ICON), ".")]

# Alembic ships ini/config files that pyfix360 imports lazily when the user
# runs the CLI migration commands; include them if present.
for package in ("alembic",):
    try:
        datas += collect_data_files(package)
    except Exception:
        pass

# --- hidden imports --------------------------------------------------------
# SQLAlchemy and Alembic load dialects/plugins by string name, so PyInstaller's
# static analysis cannot see them.
hiddenimports = [
    "sqlalchemy.dialects.sqlite",
    "sqlalchemy.sql.default_comparator",
    "alembic.ddl.sqlite",
]

# Qt binds a lot of things by name (platform plugins, image formats). Pulling
# every pyfix360 submodule in guarantees nothing imported lazily is missed.
hiddenimports += collect_submodules("pyfix360")

# Optional credential backend — imported inside a try/except at runtime.
hiddenimports += ["keyring", "keyring.backends.Windows",
                  "keyring.backends.SecretService",
                  "keyring.backends.macOS"]

# --- excluded modules ------------------------------------------------------
# Keep the bundle lean and avoid dragging in tooling the app never uses. These
# are *optional analysers* that PYFIX shells out to: they must remain external
# commands, never frozen into the app, or version reporting would lie.
excludes = [
    "bandit",
    "pip_audit",
    "pytest",
    "IPython",
    "matplotlib",
    "numpy",
    "pandas",
    "tkinter",
    "PyQt5",
    "PyQt6",
    "PySide2",
]

a = Analysis(
    [str(SRC / "pyfix360" / "app.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# A second analysis for the CLI: it is a different entry script, so it needs its
# own scripts list. Pure modules are shared (see MERGE/COLLECT below), so this
# does not duplicate the library body.
a_cli = Analysis(
    [str(SRC / "pyfix360" / "cli_entry.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
pyz_cli = PYZ(a_cli.pure, a_cli.zipped_data, cipher=block_cipher)

# Two launchers, one shared runtime. Both EXEs are placed in a single COLLECT so
# there is exactly one copy of the Python runtime and Qt libraries, and both .exe
# files end up side by side in the bundle root.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PYFIX360",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,           # GUI app: no console window on Windows
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON),
)

cli = EXE(
    pyz_cli,
    a_cli.scripts,
    [],
    exclude_binaries=True,
    name="PYFIX360-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,            # CLI keeps its console for piping and --help
    icon=str(ICON),
)

coll = COLLECT(
    exe,                     # the GUI launcher
    cli,                     # the headless launcher
    a.binaries,              # one shared copy of the runtime + Qt
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PYFIX360",
)
