"""Desktop application entry point.

Startup order matters:

1. logging, so any failure below is written to ``logs/pyfix360.log``
2. persisted settings are loaded into the runtime config *before* the window is
   built, so the saved theme and layout are the ones that appear
3. the window is constructed, which applies the stylesheet
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

from pyfix360 import APP_NAME, __version__
from pyfix360.config import get_config

logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    cfg = get_config()
    level = getattr(logging, str(cfg.log_level).upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(cfg.logs_dir / "pyfix360.log", encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )


def _prune_logs(retention_days: int) -> None:
    """Delete log files older than the retention window.

    Only files this application wrote are touched: the log directory is
    PYFIX's own, but a user may keep notes there.
    """
    if retention_days <= 0:
        return
    logs_dir = get_config().logs_dir
    cutoff = time.time() - retention_days * 86400
    for path in logs_dir.glob("*.log"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            logger.debug("could not remove the old log file %s", path)


def _icon_path() -> Path | None:
    """Locate the bundled icon in both a source checkout and a frozen build."""
    from pyfix360.branding import icon_path

    return icon_path()


def _start_update_check(ctx, tray) -> None:
    """Look for a new version in the background, if the policy allows it.

    Checking is a remote call, so it happens without a click only when the
    access policy says the application may phone home on its own. Otherwise
    the user asks for it in Settings → Data & Backup.
    """
    from pyfix360.models import AccessLevel
    from pyfix360.services import settings_service, update_service
    from pyfix360.ui.workers import run_async

    try:
        with ctx.session() as session:
            level = settings_service.access_level(session, update_service.CAPABILITY)
            auto_install = settings_service.get_settings(session).update_auto_install
    except Exception:  # pragma: no cover - a broken database must not stop the app
        logger.debug("could not read the update policy", exc_info=True)
        return
    if level is not AccessLevel.FULL:
        logger.info("no automatic update check: the policy is %s", level.value)
        return

    def work():
        with ctx.session() as session:
            return update_service.check(session, confirmed=True)

    def done(info):
        if info is None:
            return
        if not auto_install:
            tray.message(
                f"{APP_NAME} {info.version} is available",
                "Open Settings → Data & Backup to install it.")
            ctx.status(f"Version {info.version} is available.")
            return

        def install_work():
            path = update_service.download(info)
            with ctx.session() as session:
                update_service.start_install(session, info, path)
            return path

        def installed(_path) -> None:
            ctx.status("The update is installing; the application will restart.")
            from PySide6.QtCore import QTimer
            from PySide6.QtWidgets import QApplication

            # The installer finds the running application through the Windows
            # Restart Manager, closes it and starts it again afterwards. It has
            # to see the running copy, so the quit is delayed a little.
            QTimer.singleShot(2500, QApplication.instance().quit)

        def failed(message: str) -> None:
            logger.warning("the automatic update failed: %s", message)
            ctx.status("The automatic update failed; the installed version is "
                       "unchanged.")

        run_async(install_work, on_done=installed, on_error=failed)

    def failed(message: str) -> None:
        logger.warning("the update check failed: %s", message)

    run_async(work, on_done=done, on_error=failed)


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    cfg = get_config()
    logger.info(
        "%s %s starting (data_dir=%s, database=%s)",
        APP_NAME,
        __version__,
        cfg.data_dir,
        cfg.db_path,
    )
    try:
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QApplication
    except ImportError:  # pragma: no cover
        print(
            "PySide6 is required for the desktop interface.\n"
            "Install it with:  pip install 'pyfix360[ui]'\n"
            "Or use the CLI:   pyfix360 --help",
            file=sys.stderr,
        )
        return 2

    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("PYFIX 360")

    icon = _icon_path()
    if icon is not None:
        app.setWindowIcon(QIcon(str(icon)))

    # Bring the schema up to date before any screen queries it. This is additive
    # only (missing tables and columns), never destructive, so an existing
    # install upgrades in place instead of failing with "no such column".
    from pyfix360.db import init_db, session_scope

    init_db()

    # Load persisted settings into the runtime config before the window exists.
    from pyfix360.services import settings_service

    with session_scope() as s:
        settings_service.apply_from_config(s)
        _prune_logs(settings_service.get_settings(s).log_retention_days)

    from pyfix360.ui.context import AppContext
    from pyfix360.ui.main_window import MainWindow

    ctx = AppContext()
    window = MainWindow(ctx)
    if icon is not None:
        window.setWindowIcon(QIcon(str(icon)))

    # Closing the window hides it while the tray icon stays as the way back and the
    # only Quit — so the last-window rule must not be what ends the app. When the
    # desktop has no notification area, or the user turned the icon off, hiding is
    # refused and the ordinary quit-on-last-window behaviour is left in place,
    # because an app with no window and no icon is one nobody can stop.
    from pyfix360.ui.tray import TrayController

    tray = TrayController(ctx, icon_path=icon)
    window.set_tray(tray)
    app.setQuitOnLastWindowClosed(not tray.should_hide_on_close)
    shown = tray.show()
    if not shown and ctx.config.tray_enabled:
        logger.info("this desktop has no notification area; closing the window "
                    "quits the app")

    window.show()
    _start_update_check(ctx, tray)
    logger.info("main window shown (tray=%s, quit on last window closed=%s); "
                "entering event loop", shown, not tray.should_hide_on_close)
    code = app.exec()
    logger.info("event loop finished with code %s", code)
    return code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
