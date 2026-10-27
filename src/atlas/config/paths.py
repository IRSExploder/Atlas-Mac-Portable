"""Where ATLAS is allowed to put things, and where model calls are allowed to run.

Two rules carry the weight here, and they are deliberately redundant:

**1. Mutable state never lives in a synced folder.** The repo sits under OneDrive.
SQLite in a synced tree is the classic corruption path -- OneDrive replicates the
``-wal`` and ``-shm`` sidecars independently of the main database, and a rehydrated
placeholder or a sync conflict yields stale pages rather than a clean ``SQLITE_BUSY``.
So the live database, the geometry packs, the run sandboxes and the venv all live under
``%LOCALAPPDATA%``. Saving into the project folder is an explicit *export*, never the
working copy.

**Containment is decided by filesystem identity, not by spelling.** Windows packaged-app
virtualization can give one directory two names, so both checks below compare with
:func:`_contains` rather than with a string prefix. See there for the diagnosis; the rule
is unchanged and only the way it is *evaluated* moved.

**2. Model calls run in an allowlist, not merely outside a denylist.**
:func:`assert_isolated` requires the target to be *inside* the runs root. A denylist
answers "is this one of the paths I remembered to forbid?", which fails the moment a
repository moves or a new one appears. The allowlist answers "is this a directory I
created for this purpose?", which cannot fail that way. :data:`FORBIDDEN_ROOTS` is kept
as a second layer, checked as well, because defence in depth is cheap here.

**The Antigravity asymmetry.** ``agy`` ignores the subprocess working directory
entirely -- it operates in its own fixed scratch directory regardless of what ``cwd``
the parent sets (verified against agy 1.1.4). So for that provider ``cwd`` isolation is
not weak containment, it is *no* containment, and the meaningful lever is which
directories are handed to ``--add-dir``. :func:`assert_grantable` is the check that
matters for agy; :func:`assert_isolated` is the one that matters for claude and codex.
Do not collapse them into one function -- they guard different mechanisms.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = [
    "FORBIDDEN_ROOTS",
    "IsolationError",
    "app_data_root",
    "assert_grantable",
    "assert_isolated",
    "is_forbidden",
    "packs_root",
    "project_root",
    "runs_root",
    "worlds_root",
]


class IsolationError(RuntimeError):
    """A path failed the isolation policy. Never downgrade this to a warning."""


def _resolve(path: Path | str) -> Path:
    return Path(path).expanduser().resolve()


def _local_app_data() -> Path:
    raw = os.environ.get("LOCALAPPDATA")
    if raw:
        return Path(raw)
    return Path.home() / "AppData" / "Local"


def app_data_root() -> Path:
    """Root for everything mutable. Deliberately outside the synced project tree."""
    return _local_app_data() / "Atlas"


def worlds_root() -> Path:
    return app_data_root() / "worlds"


def packs_root() -> Path:
    return app_data_root() / "packs"


def runs_root() -> Path:
    """Parent of every per-call sandbox. The allowlist in :func:`assert_isolated`."""
    return app_data_root() / "runs"


def project_root() -> Path:
    """The ATLAS source tree itself (three levels up from this file)."""
    return Path(__file__).resolve().parents[3]


def _default_forbidden() -> tuple[Path, ...]:
    """Paths a model call must never be pointed at.

    The Quartz and Council trees are named explicitly because the user's standing
    instruction is that no agent ever runs inside them. Home is included so a
    misconfiguration cannot hand an agent the whole user profile.
    """
    home = Path.home()
    desktop = home / "OneDrive" / "Desktop"
    named = (
        desktop / "Chess Engine competition" / "quartz-engine",
        desktop / "Chess Engine competition" / "arena" / "quartz_reference",
        desktop / "Multi-AI app" / "council",
        desktop / "Atlas Game",
        home,
    )
    # **ATLAS's own tree, found rather than spelled.** The entry above names it by the
    # absolute path it happens to sit at today, so moving the checkout -- or cloning it to
    # review a branch -- silently stopped the source tree being a forbidden root, while
    # `project_root()`, which computes it correctly, sat exported and unused. That is the
    # same "by spelling alone" mistake this module refuses for the MSIX redirect, pointing
    # the other way. Deduplicated, because on the machine this was written for the two are
    # the same directory and a list that names it twice reads like two rules.
    here = project_root()
    if any(_resolve(root) == _resolve(here) for root in named):
        return named
    return (*named[:-1], here, named[-1])


FORBIDDEN_ROOTS: tuple[Path, ...] = _default_forbidden()


def _contains(allowed: Path, target: Path) -> bool:
    r"""Whether ``target`` is inside ``allowed``, by identity when the names disagree.

    **The names can disagree about the same directory, and on this machine they do.**
    Under Windows packaged-app (MSIX) filesystem virtualization, writes beneath
    ``%LOCALAPPDATA%`` are redirected into the package's private store, so a sandbox
    created at::

        C:\...\AppData\Local\Atlas\runs\atlas-run-c109b5e0

    reports ``Path.resolve()`` as::

        C:\...\AppData\Local\Packages\<pkg>\LocalCache\Local\Atlas\runs\atlas-run-c109b5e0

    while ``runs_root()`` -- an older directory that predates the redirect -- resolves to
    itself. The two are **the same place**, and a string comparison says they are not: the
    redirect appears on newly created entries and not on the ancestors they were created
    under, so the check compared a redirected child against an unredirected parent and
    refused every legitimate sandbox this environment could produce.

    Fixed by asking the filesystem instead of the string. If plain containment fails, walk
    up from ``target`` and ask ``os.path.samefile`` -- which compares volume serial and
    file index, not text -- whether any ancestor *is* ``allowed``. That accepts whatever
    redirection the OS is actually performing without this module having to know MSIX's
    layout, and it is **not a loosening**: a directory in the wrong place has a different
    file identity and still fails, and a path merely *shaped* like the virtualized form
    matches nothing because no such directory exists.

    Deliberately not done by rewriting ``Packages\<pkg>\LocalCache\Local`` back to
    ``Local`` textually. That would accept *any* package's private store as equivalent to
    ours -- a different physical directory, passed by spelling alone -- which is exactly
    the class of hole an allowlist exists to close.
    """
    if target == allowed or target.is_relative_to(allowed):
        return True
    if not allowed.exists():
        return False
    probe = target
    while True:
        if probe.exists():
            try:
                if os.path.samefile(probe, allowed):
                    return True
            except OSError:
                # Unreadable or vanished mid-check. Refuse rather than guess.
                return False
        if probe.parent == probe:
            return False
        probe = probe.parent


def is_forbidden(path: Path | str) -> Path | None:
    """Return the forbidden root that ``path`` falls inside, or ``None``.

    ``home`` is matched exactly rather than recursively: everything interesting lives
    under it, including ``%LOCALAPPDATA%``, so a recursive match would forbid the runs
    root itself. The recursive project/repo entries above are what actually keep an
    agent out of a source tree.
    """
    target = _resolve(path)
    home = _resolve(Path.home())
    for root in FORBIDDEN_ROOTS:
        resolved = _resolve(root)
        if resolved == home:
            if target == home:
                return resolved
            continue
        if target == resolved or target.is_relative_to(resolved):
            return resolved
    return None


def assert_isolated(path: Path | str) -> Path:
    """Assert ``path`` is a legitimate per-call sandbox (the ``cwd`` for claude/codex).

    Allowlist first: the path must live under :func:`runs_root`. That single check is
    what makes it structurally impossible to run a call inside Quartz, Council or the
    ATLAS repo, regardless of whether those paths were remembered.

    **Under, and not equal to.** The runs root itself passed, because `_contains` accepts
    identity -- and a call whose working directory is the root can read *every other
    sandbox in it*: another nation's briefing, another role's prompt, another turn's
    answer. The whole architecture rests on models never seeing each other's output, so
    handing one the drawer that holds all of it is the sharpest version of the thing this
    function exists to prevent. Nothing does it today (`SandboxDir.create` always makes a
    child) and the guard should refuse it anyway.
    """
    target = _resolve(path)
    allowed = _resolve(runs_root())
    if target == allowed:
        raise IsolationError(
            f"refusing to run a model call in {target}: that is the sandbox root itself, "
            "which holds every other call's working directory"
        )
    if not _contains(allowed, target):
        raise IsolationError(
            f"refusing to run a model call in {target}: sandboxes must live under {allowed}"
        )
    if (hit := is_forbidden(target)) is not None:
        raise IsolationError(f"refusing to run a model call in {target}: inside forbidden root {hit}")
    return target


def assert_grantable(path: Path | str) -> Path:
    """Assert ``path`` may be handed to ``agy --add-dir``.

    This is the check that actually constrains Antigravity, because ``agy`` ignores
    ``cwd``. A granted directory must be a sandbox we created, never a source tree.

    **And never the sandbox root.** For agy this is the only containment there is, so
    granting the root would hand it every other call's working directory in one argument.
    """
    target = _resolve(path)
    allowed = _resolve(runs_root())
    if target == allowed:
        raise IsolationError(
            f"refusing to grant {target} to agy --add-dir: that is the sandbox root "
            "itself, which holds every other call's working directory"
        )
    if not _contains(allowed, target):
        raise IsolationError(
            f"refusing to grant {target} to agy --add-dir: only sandboxes under {allowed} may be granted"
        )
    if (hit := is_forbidden(target)) is not None:
        raise IsolationError(f"refusing to grant {target} to agy --add-dir: inside forbidden root {hit}")
    return target
