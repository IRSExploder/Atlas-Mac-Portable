"""When ATLAS breaks, leave evidence and tell the player -- never just vanish.

Before this, nothing configured logging and nothing caught an exception: every
``log.warning`` in the codebase went to a stderr that a windowed executable does not have,
and an unhandled exception in a frozen ``ATLAS.exe`` closed the window with no message and
no file. A player could not report a crash, and a tester could not tell one from a quit.

:func:`install` sets up four things, all under the per-user logs folder:

* **A rotating log** (``atlas.log``, 1 MB x 5) that every module's logger reaches, plus
  Qt's own warnings routed into it.
* **faulthandler**, writing native crashes (an access violation inside Qt, a deadlock
  dumped on a signal) to ``faulthandler.log`` -- the one kind of crash Python code never
  sees.
* **Exception hooks** for the main thread and every other thread, including the asyncio
  loop thread the turns run on. Each unhandled exception becomes a ``crash-*.txt`` report
  with the traceback, the version and the platform.
* **A dialog**, if a QApplication is running and the platform has a screen, saying the game
  hit an error and where the report is.

**The hooks never raise.** A crash handler that crashes hides the original crash, so every
step is guarded and the worst outcome is the old behaviour: the default hook's stderr.

Not installed by tests or by headless subcommands; the window commands and the frozen
entry point call it.
"""

from __future__ import annotations

import contextlib
import faulthandler
import logging
import logging.handlers
import os
import platform
import sys
import threading
import time
import traceback
from pathlib import Path

__all__ = ["install", "logs_root", "write_report"]

log = logging.getLogger(__name__)

_state: dict = {}


def logs_root() -> Path:
    from ..config.paths import logs_root as _root

    return _root()


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("atlas")
    except Exception:  # noqa: BLE001 -- frozen builds may carry no metadata
        return "unknown"


def write_report(exc_type, exc, tb, *, thread: str = "main") -> Path | None:
    """Write one crash report and return its path, or ``None`` if even that failed."""
    try:
        folder = _state.get("folder") or logs_root()
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = folder / f"crash-{stamp}-{os.getpid()}.txt"
        body = "".join(traceback.format_exception(exc_type, exc, tb))
        path.write_text(
            f"ATLAS {_version()} crashed on thread {thread!r} at "
            f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"python {sys.version.split()[0]}, {platform.platform()}, "
            f"frozen={bool(getattr(sys, 'frozen', False))}\n\n{body}",
            encoding="utf-8")
        return path
    except Exception:  # noqa: BLE001
        return None


def _can_show_dialog() -> bool:
    if os.environ.get("ATLAS_NO_CRASH_DIALOG"):
        return False
    try:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        return app is not None and app.platformName() not in ("offscreen", "minimal")
    except Exception:  # noqa: BLE001
        return False


def _show_dialog(report: Path | None, exc) -> None:
    if not _can_show_dialog():
        return
    try:
        from PySide6.QtWidgets import QMessageBox

        where = (f"A report was saved to:\n{report}" if report
                 else f"The log is in:\n{_state.get('folder')}")
        QMessageBox.critical(
            None, "ATLAS hit an error",
            f"Something went wrong: {type(exc).__name__}: {exc}\n\n{where}\n\n"
            "Your world is saved after every turn. If the game keeps working you can "
            "carry on; otherwise restart it and use Continue.")
    except Exception:  # noqa: BLE001
        pass


def _handle(exc_type, exc, tb, *, thread: str) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        _state["previous_hook"](exc_type, exc, tb)
        return
    with contextlib.suppress(Exception):
        log.critical("unhandled exception on %s", thread, exc_info=(exc_type, exc, tb))
    report = write_report(exc_type, exc, tb, thread=thread)
    with contextlib.suppress(Exception):
        _state["previous_hook"](exc_type, exc, tb)
    if thread == "main":
        _show_dialog(report, exc)
    else:
        # Off the GUI thread a dialog would crash Qt; hand it to the GUI thread instead.
        _queue_dialog(report, exc)


def _queue_dialog(report: Path | None, exc) -> None:
    if not _can_show_dialog():
        return
    try:
        from PySide6.QtCore import QTimer

        QTimer.singleShot(0, lambda: _show_dialog(report, exc))
    except Exception:  # noqa: BLE001
        pass


def _qt_messages(mode, context, message) -> None:
    try:
        from PySide6.QtCore import QtMsgType

        level = {QtMsgType.QtDebugMsg: logging.DEBUG, QtMsgType.QtInfoMsg: logging.INFO,
                 QtMsgType.QtWarningMsg: logging.WARNING,
                 QtMsgType.QtCriticalMsg: logging.ERROR,
                 QtMsgType.QtFatalMsg: logging.CRITICAL}.get(mode, logging.WARNING)
        logging.getLogger("qt").log(level, "%s", message)
    except Exception:  # noqa: BLE001
        pass


def install(folder: Path | None = None, *, qt: bool = True) -> Path:
    """Install logging, faulthandler and the exception hooks. Idempotent; returns the folder."""
    if _state.get("installed"):
        return _state["folder"]
    folder = Path(folder) if folder is not None else logs_root()
    folder.mkdir(parents=True, exist_ok=True)
    _state["folder"] = folder

    handler = logging.handlers.RotatingFileHandler(
        folder / "atlas.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(threadName)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
    _state["handler"] = handler

    try:
        stream = (folder / "faulthandler.log").open("a", encoding="utf-8")
        stream.write(f"\n--- session {time.strftime('%Y-%m-%d %H:%M:%S')} pid {os.getpid()}\n")
        stream.flush()
        faulthandler.enable(stream, all_threads=True)
        _state["fault_stream"] = stream
    except Exception:  # noqa: BLE001
        log.warning("faulthandler could not be enabled", exc_info=True)

    _state["previous_hook"] = sys.excepthook
    _state["previous_thread_hook"] = threading.excepthook
    sys.excepthook = lambda t, e, tb: _handle(t, e, tb, thread="main")
    threading.excepthook = lambda args: _handle(
        args.exc_type, args.exc_value, args.exc_traceback,
        thread=getattr(args.thread, "name", "thread"))

    if qt:
        try:
            from PySide6.QtCore import qInstallMessageHandler

            _state["previous_qt"] = qInstallMessageHandler(_qt_messages)
        except Exception:  # noqa: BLE001
            pass

    _state["installed"] = True
    log.info("ATLAS %s starting; python %s; %s; frozen=%s", _version(),
             sys.version.split()[0], platform.platform(), bool(getattr(sys, "frozen", False)))
    return folder


def uninstall() -> None:
    """Put everything back. For tests."""
    if not _state.get("installed"):
        return
    sys.excepthook = _state.pop("previous_hook")
    threading.excepthook = _state.pop("previous_thread_hook")
    if "previous_qt" in _state:
        try:
            from PySide6.QtCore import qInstallMessageHandler

            qInstallMessageHandler(_state.pop("previous_qt"))
        except Exception:  # noqa: BLE001
            pass
    handler = _state.pop("handler", None)
    if handler is not None:
        logging.getLogger().removeHandler(handler)
        handler.close()
    stream = _state.pop("fault_stream", None)
    if stream is not None:
        faulthandler.disable()
        stream.close()
    _state.clear()
