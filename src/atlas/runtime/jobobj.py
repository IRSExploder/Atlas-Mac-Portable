"""Windows Job Objects, for killing a process tree deterministically.

Cancelling a jump has to actually stop the work. The obvious approaches both fail:

``proc.terminate()`` calls ``TerminateProcess``, which kills exactly one process and
ignores its children. ``CREATE_NEW_PROCESS_GROUP`` does not help -- process groups govern
console signal delivery, not termination.

``taskkill /T`` walks the *live* parent chain to find descendants. If an intermediate
process has already exited -- a launcher that spawned the real worker and returned -- the
grandchildren are orphaned and keep running, holding the sandbox directory open.

A Job Object solves it properly. The child is assigned to a job at creation, children it
spawns are assigned automatically, and ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` makes the
kernel terminate every member the moment the handle closes. Nothing can escape it and
nothing needs to be enumerated.

On non-Windows this degrades to a no-op wrapper; POSIX process groups already give the
same guarantee through ``killpg``.
"""

from __future__ import annotations

import os
from typing import Any

__all__ = ["JobObject", "supported"]

_IS_WINDOWS = os.name == "nt"

if _IS_WINDOWS:  # pragma: no branch
    import ctypes
    from ctypes import wintypes

    _JobObjectExtendedLimitInformation = 9
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _PROCESS_SET_QUOTA = 0x0100
    _PROCESS_TERMINATE = 0x0001

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.POINTER(wintypes.ULONG)),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]


def supported() -> bool:
    return _IS_WINDOWS


class JobObject:
    """A kill-on-close job. Assign a pid, close the handle, the tree dies."""

    __slots__ = ("_handle", "_kernel32", "_assigned")

    def __init__(self) -> None:
        self._handle: Any = None
        self._assigned = False
        self._kernel32 = None
        if not _IS_WINDOWS:
            return

        import ctypes

        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = self._kernel32.CreateJobObjectW(None, None)
        if not handle:
            return

        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = self._kernel32.SetInformationJobObject(
            handle,
            _JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            self._kernel32.CloseHandle(handle)
            return
        self._handle = handle

    @property
    def active(self) -> bool:
        return self._handle is not None

    def assign(self, pid: int) -> bool:
        """Put a running process into the job. Returns whether it worked."""
        if self._handle is None or self._kernel32 is None:
            return False
        process = self._kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
        if not process:
            return False
        try:
            self._assigned = bool(self._kernel32.AssignProcessToJobObject(self._handle, process))
            return self._assigned
        finally:
            self._kernel32.CloseHandle(process)

    def kill(self) -> None:
        """Terminate every process in the job."""
        if self._handle is None or self._kernel32 is None:
            return
        self._kernel32.TerminateJobObject(self._handle, 1)

    def close(self) -> None:
        if self._handle is not None and self._kernel32 is not None:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self) -> JobObject:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
