"""Background task helpers for the UI.

Long operations (scans, test runs, repairs) run on a worker thread so the
interface stays responsive. Results are delivered back on the GUI thread.
"""

from __future__ import annotations

import logging
import traceback
from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

logger = logging.getLogger(__name__)


class WorkerSignals(QObject):
    finished = Signal(object)
    error = Signal(str)
    progress = Signal(float, str)


class Worker(QRunnable):
    def __init__(self, fn: Callable, *args, **kwargs) -> None:
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()
        self.setAutoDelete(True)

    def run(self) -> None:  # pragma: no cover - exercised via Qt event loop
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as exc:
            logger.exception("background task failed")
            self.signals.error.emit(f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=4)}")
            return
        self.signals.finished.emit(result)


#: Workers whose result is still on its way. ``setAutoDelete(True)`` lets Qt free
#: the runnable the moment ``run()`` returns, and with it the ``WorkerSignals``
#: object that carries the connections — so a caller that does not keep the
#: reference silently loses the callback. Verified: without this set, emitting
#: and then ``processEvents()`` delivers nothing at all. Forgetting the worker is
#: the normal way screens use this helper, so the helper remembers for them.
_inflight: set[Worker] = set()


def run_async(fn: Callable, *, on_done: Callable | None = None,
              on_error: Callable | None = None,
              on_progress: Callable | None = None) -> Worker:
    worker = Worker(fn)
    if on_done:
        worker.signals.finished.connect(on_done)
    if on_error:
        worker.signals.error.connect(on_error)
    if on_progress:
        worker.signals.progress.connect(on_progress)

    def _release(*_ignored) -> None:
        _inflight.discard(worker)

    worker.signals.finished.connect(_release)
    worker.signals.error.connect(_release)
    _inflight.add(worker)
    QThreadPool.globalInstance().start(worker)
    return worker
