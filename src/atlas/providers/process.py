"""Running a CLI as a subprocess, correctly.

Four details here are the difference between working and appearing to work:

**The stream limit.** ``asyncio``'s default ``StreamReader`` limit is 64 KiB per line. A
single JSON response from a world simulation is routinely larger, and exceeding it raises
``LimitOverrunError`` mid-turn -- which looks like a provider fault and is not one. The
limit is raised to 8 MiB.

**Partial output on timeout.** ``asyncio.wait_for(proc.communicate(), ...)`` discards
everything the process wrote when it is cancelled, so a timeout yields no diagnostics at
exactly the moment they are wanted, and a truncated response becomes indistinguishable
from a hang. Explicit reader tasks keep what arrived.

**Killing the tree.** ``proc.kill()`` terminates one process. A Job Object terminates
everything the call spawned, deterministically. See :mod:`atlas.runtime.jobobj`. On POSIX
the child is started as the leader of its own session, so everything it spawns shares its
process group, and a timeout or cancel kills the group with ``killpg``.

**Closing stdin.** Codex prints "Reading additional input from stdin..." and waits forever
if stdin stays open. Feeding the prompt is not enough -- the pipe has to be closed to
signal end of input.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path

from ..runtime.jobobj import JobObject

__all__ = ["ProcessOutcome", "run_cli"]

log = logging.getLogger(__name__)

#: 8 MiB. A world-sim response is far larger than the 64 KiB asyncio default.
_STREAM_LIMIT = 8 * 1024 * 1024

#: How long to wait for a killed process to actually die before giving up on it.
_REAP_TIMEOUT = 5.0

#: POSIX kills by process group; Windows keeps its Job Object and nothing else changes.
_POSIX_GROUPS = os.name != "nt"


def _kill_group(pgid: int) -> None:
    """SIGKILL every process in the call's group, including any whose parent has exited."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, signal.SIGKILL)


@dataclass(slots=True)
class ProcessOutcome:
    argv: tuple[str, ...]
    exit_code: int | None
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False
    cancelled: bool = False
    #: Set when the executable could not be started at all.
    launch_error: str | None = None

    @property
    def ok_exit(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and self.launch_error is None


async def _drain(stream: asyncio.StreamReader | None, buffer: bytearray) -> None:
    """Read a pipe to EOF, keeping whatever arrives."""
    if stream is None:
        return
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            return
        buffer.extend(chunk)


async def run_cli(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    stdin_payload: str | None = None,
    timeout_s: float = 300.0,
) -> ProcessOutcome:
    """Launch a CLI, feed it stdin, and collect its output within a deadline.

    Never raises for an ordinary failure -- a timeout, a crash and a bad exit code are all
    returned as data, because the caller has to classify them anyway.
    """
    started = time.monotonic()
    argv_tuple = tuple(argv)
    stdout_buf = bytearray()
    stderr_buf = bytearray()

    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd),
            env=env,
            limit=_STREAM_LIMIT,
            # Its own session, so its pid is the group id of everything it spawns and the
            # whole tree can be killed at once. Without it a killed CLI orphans its workers.
            **({"start_new_session": True} if _POSIX_GROUPS else {}),
        )
    except (OSError, ValueError) as exc:
        return ProcessOutcome(
            argv=argv_tuple,
            exit_code=None,
            stdout="",
            stderr="",
            duration_s=time.monotonic() - started,
            launch_error=f"{type(exc).__name__}: {exc}",
        )

    job = JobObject()
    if job.active and process.pid:
        job.assign(process.pid)

    async def feed() -> None:
        """Write the prompt and close the pipe.

        Closing matters as much as writing: codex blocks indefinitely waiting for more
        input if stdin stays open, and a blocked child looks exactly like a slow model.
        """
        if process.stdin is None:
            return
        try:
            if stdin_payload:
                process.stdin.write(stdin_payload.encode("utf-8"))
                await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # the CLI may not read stdin at all; that is not an error
        finally:
            with contextlib.suppress(BrokenPipeError, OSError):
                process.stdin.close()

    timed_out = False
    cancelled = False
    tasks = [
        asyncio.create_task(feed()),
        asyncio.create_task(_drain(process.stdout, stdout_buf)),
        asyncio.create_task(_drain(process.stderr, stderr_buf)),
        asyncio.create_task(process.wait()),
    ]

    try:
        done, pending = await asyncio.wait(tasks, timeout=timeout_s)
        if pending:
            timed_out = True
    except asyncio.CancelledError:
        cancelled = True
        raise
    finally:
        if _POSIX_GROUPS and (timed_out or cancelled or process.returncode is None):
            # Even when the leader has already exited: a grandchild still holding the
            # pipes is exactly what makes the readers time out, and it is still in the
            # group. The group outlives its leader for as long as it has members.
            _kill_group(process.pid)
        if process.returncode is None:
            # Kill the whole tree, not just the process we can see.
            job.kill()
            if job.active is False:  # pragma: no cover - non-Windows fallback
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
            try:
                await asyncio.wait_for(process.wait(), timeout=_REAP_TIMEOUT)
            except TimeoutError:  # pragma: no cover
                log.warning("process %s did not exit after being killed", process.pid)

        for task in tasks:
            if not task.done():
                task.cancel()
        # Let the cancellations settle so no reader is still holding the pipes when the
        # sandbox directory is removed.
        await asyncio.gather(*tasks, return_exceptions=True)
        job.close()

    return ProcessOutcome(
        argv=argv_tuple,
        exit_code=process.returncode,
        stdout=stdout_buf.decode("utf-8", errors="replace"),
        stderr=stderr_buf.decode("utf-8", errors="replace"),
        duration_s=time.monotonic() - started,
        timed_out=timed_out,
        cancelled=cancelled,
    )
