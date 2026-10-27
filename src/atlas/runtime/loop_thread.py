"""Bridging asyncio and Qt without either one blocking the other.

A world-simulation call takes up to fifteen minutes. The map must stay pannable
throughout, so the provider layer cannot run on the GUI thread. The constraints pull in
opposite directions:

* Qt insists on owning the main thread and its own event loop.
* ``asyncio.create_subprocess_exec`` on Windows requires a **Proactor** loop. A selector
  loop raises ``NotImplementedError`` the moment a subprocess is spawned.

**qasync and friends are rejected for exactly that reason.** They drive asyncio *from* the
Qt loop, and on Windows that means a selector-based loop -- so every provider call fails,
and it fails at the point where the whole design depends on subprocesses. It is the single
most likely thing to stall this build, and it is invisible until the first real call.

So the two loops are never mixed. Qt keeps the main thread untouched; a daemon thread owns
an explicit ``ProactorEventLoop``; results cross back as Qt queued signals. The only thing
lost is ``add_signal_handler``, which a GUI never wanted.

**The strong reference is not optional.** ``TaskHandle`` is a ``QObject`` constructed on
the main thread, and the runtime holds it until its terminal signal has been delivered. If
it were only referenced by the calling frame, a garbage collection on the main thread while
the loop thread is mid-``emit`` is a hard crash, not an exception.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from collections.abc import Coroutine
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

__all__ = ["AsyncRuntime", "TaskHandle"]

log = logging.getLogger(__name__)


class _ReplaySignal:
    """A signal that also delivers to whoever connects *after* it has already fired.

    **The bug this exists for.** ``submit`` hands the coroutine to the loop thread before
    it returns, and every caller connects afterwards -- five sites in the main window do
    exactly that. A coroutine that finishes before the connection is made emits into an
    empty receiver list, and Qt resolves receivers *at emit time*, so the signal is not
    late, it is gone. A provider that raised before its first ``await`` left the interface
    waiting on a reply that had already failed.

    So the handle remembers its terminal outcome, and connecting to a signal that has
    already carried one schedules that outcome for the new slot.

    **Exactly once, and the lock is what guarantees it.** The replay is aimed at the slot
    that just connected rather than re-emitted to everyone, so a subscriber that connected
    in time cannot be served twice. The narrow race -- connecting between the moment the
    outcome is recorded and the moment it is emitted -- is closed by taking the handle's
    lock on both paths: either the connection is in place before the emit resolves its
    receivers (one delivery, no replay, because the outcome was not yet recorded when
    ``connect`` looked), or it is not (one delivery, by replay).

    **Deferred, never called inside ``connect``.** The slot goes through
    ``QTimer.singleShot`` with the handle as its context object, so it runs on the thread
    the handle lives on -- the GUI thread -- on a later turn of the event loop, exactly as
    a queued delivery would. Calling it inline would run a UI callback halfway through
    somebody's ``connect`` statement and on whatever thread they happened to be on.

    The closure holds the handle, which is what keeps ``runtime.submit(x).finished.connect
    (f)`` safe: the temporary handle in that expression stays alive until the replay has
    been delivered.

    One honest limitation: a slot disconnected between connecting and the next turn of the
    event loop still receives its replay, because the replay is a call to that slot rather
    than a trip through the signal. Nothing in this codebase does that, and the alternative
    is a second connection to keep in step with the first.
    """

    __slots__ = ("_handle", "_signal", "_state")

    def __init__(self, handle: TaskHandle, signal, state: str) -> None:
        self._handle = handle
        self._signal = signal
        self._state = state

    def connect(self, slot, *args, **kwargs):
        handle = self._handle
        with handle._lock:
            result = self._signal.connect(slot, *args, **kwargs)
            settled = handle._outcome
            if settled is None or settled[0] != self._state:
                return result
            value = settled[1]

        def replay() -> None:
            # `handle` is captured deliberately: see the class docstring.
            if handle is not None:
                slot() if value is _NOTHING else slot(value)

        QTimer.singleShot(0, handle, replay)
        return result

    def __getattr__(self, name: str):
        """`emit`, `disconnect` and the rest go straight to the real signal."""
        return getattr(self._signal, name)


#: The absence of a value, for a terminal state that carries none. ``None`` will not do:
#: a coroutine returning ``None`` is a perfectly ordinary success.
_NOTHING = object()


class TaskHandle(QObject):
    """One in-flight call. Lives on the main thread; signals arrive queued.

    **The terminal signal is a promise, not a broadcast.** Connect whenever you like: if
    the call has already finished, failed or been cancelled, the outcome is replayed to
    you on the GUI thread. See :class:`_ReplaySignal`.
    """

    _finished = Signal(object)
    _failed = Signal(object)
    _cancelled = Signal()

    def __init__(self, label: str = "", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.label = label
        self._future: asyncio.Future | None = None
        self._done = False
        # Re-entrant because a *direct* connection runs its slot inside `emit`, and a slot
        # that connects something else to the same handle would otherwise deadlock on a
        # plain lock. Cross-thread connections are queued and never reach that path.
        self._lock = threading.RLock()
        #: ``(state, value)`` once terminal. Read under the lock by every connection.
        self._outcome: tuple[str, Any] | None = None
        self.finished = _ReplaySignal(self, self._finished, "finished")
        self.failed = _ReplaySignal(self, self._failed, "failed")
        self.cancelled = _ReplaySignal(self, self._cancelled, "cancelled")

    @property
    def done(self) -> bool:
        return self._done

    def _settle(self, state: str, value: Any = _NOTHING) -> None:
        """Record the terminal outcome and emit it. Called on the loop thread.

        Recording and emitting happen under one lock so that a connection cannot slip
        between them and be served twice -- see :class:`_ReplaySignal`.
        """
        with self._lock:
            self._done = True
            self._outcome = (state, value)
            signal = getattr(self, f"_{state}")
            signal.emit() if value is _NOTHING else signal.emit(value)

    def cancel(self) -> None:
        """Ask the running coroutine to stop.

        Cancellation is cooperative and crosses a thread boundary, so this returns
        immediately; ``cancelled`` fires once the loop thread has actually unwound.
        """
        future = self._future
        if future is not None and not future.done():
            future.cancel()


def _make_loop() -> asyncio.AbstractEventLoop:
    """Create a loop that can actually spawn subprocesses on this platform."""
    if sys.platform == "win32":
        return asyncio.ProactorEventLoop()
    return asyncio.new_event_loop()


class AsyncRuntime:
    """Owns the background event loop and marshals results back to Qt."""

    __slots__ = ("_loop", "_thread", "_ready", "_live")

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        # Strong references to in-flight handles. See the module docstring.
        self._live: set[TaskHandle] = set()

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return

        def run() -> None:
            loop = _make_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            self._ready.set()
            try:
                loop.run_forever()
            finally:
                try:
                    loop.run_until_complete(loop.shutdown_asyncgens())
                finally:
                    loop.close()

        self._thread = threading.Thread(target=run, name="atlas-async", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5.0)

        if self._loop is not None and sys.platform == "win32":
            assert isinstance(self._loop, asyncio.ProactorEventLoop), (
                "the provider loop must be a ProactorEventLoop; a selector loop cannot "
                "spawn subprocesses on Windows"
            )

    def stop(self, *, timeout: float = 5.0) -> None:
        loop, thread = self._loop, self._thread
        if loop is None or thread is None:
            return
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=timeout)
        self._loop = None
        self._thread = None
        self._ready.clear()
        self._live.clear()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- submission --------------------------------------------------------

    def submit(self, coro: Coroutine[Any, Any, Any], *, label: str = "") -> TaskHandle:
        """Run ``coro`` on the background loop and return a handle to its result.

        The handle must be constructed here, on the calling (main) thread, so that Qt
        gives its connections queued delivery automatically.
        """
        if self._loop is None:
            self.start()
        loop = self._loop
        if loop is None:  # pragma: no cover - start() failed
            raise RuntimeError("the async runtime could not be started")

        handle = TaskHandle(label=label)
        self._live.add(handle)

        future = asyncio.run_coroutine_threadsafe(coro, loop)
        handle._future = future

        def on_done(completed) -> None:
            # Runs on the loop thread. Emitting is safe because the handle lives on the
            # main thread, so the connection is queued.
            try:
                if completed.cancelled():
                    handle._settle("cancelled")
                else:
                    error = completed.exception()
                    if error is not None:
                        handle._settle("failed", error)
                    else:
                        handle._settle("finished", completed.result())
            except Exception:  # noqa: BLE001 - a listener must never kill the loop thread
                log.exception("error delivering result for %s", label or "task")
            finally:
                # Released only after the terminal signal has been posted.
                self._live.discard(handle)

        future.add_done_callback(on_done)
        return handle

    def run_sync(self, coro: Coroutine[Any, Any, Any], *, timeout: float | None = None) -> Any:
        """Block until ``coro`` finishes. For the CLI and tests, never for the GUI."""
        if self._loop is None:
            self.start()
        loop = self._loop
        if loop is None:  # pragma: no cover
            raise RuntimeError("the async runtime could not be started")
        return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout)
