"""A fresh, isolated directory per model call.

Every call gets its own empty directory under the runs root, and it is removed afterwards.
No call ever runs in the repository, the user's home, or any other project -- enforced by
:func:`atlas.config.paths.assert_isolated`, which is an allowlist rather than a list of
places we remembered to forbid.

**Two Windows details that are easy to get wrong.**

A directory cannot be deleted while any live process has it as their working directory,
so teardown waits for the child to exit first and then retries with backoff -- antivirus
and OneDrive both hold brief handles on freshly written files. A sandbox that still
cannot be removed is renamed into an ``.orphans`` folder rather than raising, because
losing a turn to a directory-removal race would be absurd.

**The Antigravity caveat.** ``agy`` ignores the working directory it is given and runs in
its own scratch folder regardless. For that adapter the sandbox is not the boundary --
what constrains it is which directories are passed to ``--add-dir``. The sandbox is still
created (the prompt file lives there), but Diagnostics is honest that isolation for agy
means something different than for the other two.
"""

from __future__ import annotations

import logging
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..config.paths import assert_isolated, runs_root

__all__ = ["SandboxDir", "sweep_orphans"]

log = logging.getLogger(__name__)

_PREFIX = "atlas-run-"
_ORPHANS = ".orphans"


@dataclass(slots=True)
class SandboxDir:
    """A per-call working directory, used as a context manager."""

    path: Path
    keep: bool = False

    @classmethod
    def create(cls, *, keep: bool = False) -> SandboxDir:
        root = runs_root()
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{_PREFIX}{uuid.uuid4().hex[:12]}"
        path.mkdir(parents=False, exist_ok=False)
        # The allowlist check happens after creation so it validates the real, resolved
        # path rather than an intention.
        assert_isolated(path)
        return cls(path=path, keep=keep)

    def write(self, name: str, content: str) -> Path:
        target = self.path / name
        target.write_text(content, encoding="utf-8")
        return target

    def read(self, name: str) -> str | None:
        target = self.path / name
        try:
            return target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def cleanup(self) -> None:
        if self.keep:
            log.debug("keeping sandbox %s (debug mode)", self.path)
            return
        for attempt in range(3):
            try:
                shutil.rmtree(self.path)
                return
            except OSError:
                if attempt == 2:
                    break
                import time

                time.sleep(0.15 * (attempt + 1))

        # Still locked. Move it aside rather than failing the call: a turn must not be
        # lost to a directory that antivirus happened to be scanning.
        try:
            orphans = runs_root() / _ORPHANS
            orphans.mkdir(parents=True, exist_ok=True)
            self.path.rename(orphans / self.path.name)
            log.warning("sandbox %s could not be removed; moved to %s", self.path, orphans)
        except OSError:
            log.warning("sandbox %s could not be removed or moved; leaving it", self.path)

    def __enter__(self) -> SandboxDir:
        return self

    def __exit__(self, *_exc) -> None:
        self.cleanup()


def sweep_orphans(*, max_age_s: float = 3600.0) -> int:
    """Delete sandboxes left behind by a crash or a hard kill. Called at startup."""
    import time

    root = runs_root()
    if not root.exists():
        return 0

    removed = 0
    cutoff = time.time() - max_age_s
    candidates = list(root.glob(f"{_PREFIX}*"))
    orphans = root / _ORPHANS
    if orphans.exists():
        candidates.extend(orphans.iterdir())

    for path in candidates:
        try:
            if path.stat().st_mtime > cutoff:
                continue
            shutil.rmtree(path, ignore_errors=True)
            removed += not path.exists()
        except OSError:
            continue
    return removed
