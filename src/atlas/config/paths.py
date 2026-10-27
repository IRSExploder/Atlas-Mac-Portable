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
import sys
from pathlib import Path

__all__ = [
    "FORBIDDEN_ROOTS",
    "LAYOUT",
    "IsolationError",
    "app_data_root",
    "assert_grantable",
    "assert_isolated",
    "bundled_packs_root",
    "is_frozen",
    "logs_root",
    "is_forbidden",
    "missing_pack_advice",
    "pack_file",
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
    legacy = Path.home() / "AppData" / "Local"
    # **A Mac keeps an app's data in Application Support.** The Windows layout is kept only
    # where a Mac already has it -- the first Mac builds wrote there -- so nobody's saves
    # move out from under them.
    if sys.platform == "darwin" and not (legacy / "Atlas").exists():
        return Path.home() / "Library" / "Application Support"
    return legacy


def app_data_root() -> Path:
    """Root for everything mutable. Deliberately outside the synced project tree.

    ``%LOCALAPPDATA%/Atlas`` -- per user, per machine, never roamed by Windows. Steam Cloud
    syncs the parts of it :data:`LAYOUT` marks as cloud, and nothing else.
    ``ATLAS_DATA_DIR`` moves the whole root: a portable install, a test, or a second
    profile on one machine.
    """
    override = os.environ.get("ATLAS_DATA_DIR")
    if override:
        return Path(override)
    return _local_app_data() / "Atlas"


#: Every folder and file under :func:`app_data_root`, and whether it belongs to the player
#: (``cloud``: saves and the player's own settings and scenarios -- what Steam Cloud should
#: carry between machines) or to this machine (``local``: logs, call sandboxes, download
#: caches and developer-baked packs, which are either disposable or machine-specific).
#: One table, so the Steam Cloud configuration (A16) is generated from it rather than
#: remembered.
LAYOUT: dict[str, str] = {
    "worlds/": "cloud",
    "presets/": "cloud",
    "workshop/": "cloud",
    "preferences.json": "cloud",
    "logs/": "local",
    "runs/": "local",
    "cache/": "local",
    "packs/": "local",
}


def worlds_root() -> Path:
    return app_data_root() / "worlds"


def packs_root() -> Path:
    """Where `atlas map bake` writes. A developer's packs; a player's install has none here."""
    return app_data_root() / "packs"


def is_frozen() -> bool:
    """Whether this is the installed, frozen build rather than a source checkout."""
    return bool(getattr(sys, "frozen", False))


def bundled_packs_root() -> Path | None:
    """The packs shipped with the install, or ``None`` in a source checkout.

    **A player never bakes.** Baking downloads Natural Earth from GitHub and needs the
    `[bake]` geometry stack (shapely, topojson); none of that belongs on a player's
    machine. The release build copies the packs `packs.lock.json` pins into a ``packs``
    folder beside the executable, read-only like the rest of the install -- a pack is a
    read-only memory map, so a Program Files folder is fine. ``ATLAS_BUNDLED_PACKS``
    points a source checkout at such a folder, which is how the shipped layout is tested
    without freezing anything.
    """
    raw = os.environ.get("ATLAS_BUNDLED_PACKS")
    if raw:
        return Path(raw)
    if is_frozen():
        here = Path(sys.executable).resolve().parent
        # In a macOS app bundle the executable is in Contents/MacOS and data belongs in
        # Contents/Resources: code signing seals the two differently.
        resources = here.parent / "Resources" / "packs"
        if sys.platform == "darwin" and resources.is_dir():
            return resources
        return here / "packs"
    return None


def pack_file(source: str) -> Path:
    """The pack for one geometry source: the shipped one if there is one, else the baked one.

    Shipped wins because it is the copy the release was verified against; a stale bake
    left in app data by an older developer install must not shadow it. When neither
    exists this returns the baked path, which is what the error messages name.
    """
    bundled = bundled_packs_root()
    if bundled is not None:
        shipped = bundled / f"{source}.pack"
        if shipped.exists():
            return shipped
    return packs_root() / f"{source}.pack"


def missing_pack_advice(path: Path) -> str:
    """What to tell someone whose pack is missing -- which differs for a player."""
    if bundled_packs_root() is not None:
        return (f"the map data file {path.name} is missing from this installation; "
                "verify or reinstall the game")
    return f"no map pack at {path.name}; run `atlas map bake --source {path.stem}`"


def logs_root() -> Path:
    """The rotating log, faulthandler output and crash reports. See `runtime.crash`."""
    return app_data_root() / "logs"


def runs_root() -> Path:
    """Parent of every per-call sandbox. The allowlist in :func:`assert_isolated`."""
    return app_data_root() / "runs"


def project_root() -> Path:
    """The ATLAS source tree itself (three levels up from this file)."""
    return Path(__file__).resolve().parents[3]


#: The developer's own forbidden folders, kept out of the code (A7). Read only from a source
#: checkout; see the file itself.
DEV_FORBIDDEN_FILE = "config/dev_forbidden_roots.txt"


def _dev_roots() -> tuple[Path, ...]:
    """The checkout's dev list plus ``ATLAS_FORBIDDEN_ROOTS``; nothing in a frozen build.

    **Moved out of the code, not dropped.** The Quartz and Council trees are forbidden
    because the user's standing instruction is that no agent ever runs inside them, and on
    the machine this is developed on that is exactly as true as it was. What changed is
    where it is written: a shipped build has no business carrying one developer's folder
    names, and a player's machine has none of those folders.
    """
    roots: list[Path] = []
    listed = project_root() / DEV_FORBIDDEN_FILE
    if not is_frozen() and listed.is_file():
        for line in listed.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                roots.append(Path(line).expanduser())
    for raw in os.environ.get("ATLAS_FORBIDDEN_ROOTS", "").split(os.pathsep):
        if raw.strip():
            roots.append(Path(raw.strip()).expanduser())
    return tuple(roots)


def _default_forbidden() -> tuple[Path, ...]:
    """Paths a model call must never be pointed at.

    Always the home folder -- so a misconfiguration cannot hand an agent the whole user
    profile -- and ATLAS's own tree; plus the developer's list (:func:`_dev_roots`). Home
    goes last: it is matched exactly rather than recursively (see :func:`is_forbidden`).
    """
    home = Path.home()
    named = _dev_roots()
    # **ATLAS's own tree, found rather than spelled.** Naming it by the absolute path it
    # happens to sit at today meant moving the checkout -- or cloning it to review a
    # branch -- silently stopped the source tree being a forbidden root. Deduplicated,
    # because on the development machine the dev list names the same directory and a list
    # that names it twice reads like two rules.
    here = project_root()
    if not any(_resolve(root) == _resolve(here) for root in named):
        named = (*named, here)
    return (*named, home)


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
