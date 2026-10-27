"""SQLite connection policy for ATLAS.

The live database never lives in the project folder. That folder is under OneDrive, and
SQLite in a synced tree corrupts in a particularly nasty way: OneDrive replicates the
``-wal`` and ``-shm`` sidecars independently of the main file, so a sync conflict or a
rehydrated placeholder yields *stale pages* rather than an honest ``SQLITE_BUSY``. Silent
wrong data beats loud failure only in the sense that you find out later. :func:`connect`
therefore refuses any path under a sync root outright.

Saving a world into the project folder is an explicit export (:func:`export_world`),
which produces a single quiescent file with no sidecars -- safe to sync.
"""

from __future__ import annotations

import os
import sqlite3
import time
from importlib import resources
from pathlib import Path, PurePath

from ..config.paths import worlds_root
from .migrations import SCHEMA_VERSION, current_version, migrate, rebuilds_pending, stamp

__all__ = [
    "SyncedPathError",
    "connect",
    "create_world_db",
    "export_world",
    "world_db_path",
]

_SYNC_MARKERS = ("onedrive", "dropbox", "google drive", "icloud drive", "box sync")


class SyncedPathError(RuntimeError):
    """Refused to open a database inside a file-sync root."""


def _is_synced(path: Path) -> str | None:
    """Return the offending path component if ``path`` sits inside a sync root."""
    lowered = [part.lower() for part in path.resolve().parts]
    for part in lowered:
        for marker in _SYNC_MARKERS:
            if marker in part:
                return part
    return None


def world_db_path(world_id: str) -> Path:
    return worlds_root() / world_id / "world.db"


def connect(path: Path | str, *, allow_synced: bool = False) -> sqlite3.Connection:
    """Open a world database with the pragmas ATLAS depends on.

    ``allow_synced`` exists only for reading an exported file the user deliberately
    placed in a synced folder. It is never used for the live database.
    """
    target = Path(path)
    if not allow_synced and target != Path(":memory:") and (marker := _is_synced(target)):
        raise SyncedPathError(
            f"refusing to open a live world database inside a sync root ({marker!r}): {target}. "
            f"Live worlds belong under {worlds_root()}; use export_world() to save a copy."
        )

    if target != Path(":memory:"):
        target.parent.mkdir(parents=True, exist_ok=True)

    # The connection is genuinely used from two threads: the UI reads on the Qt thread
    # while the turn engine runs on the asyncio loop thread (a world simulation takes
    # minutes and cannot block the map). Python's default guard forbids that outright,
    # but it is conservative rather than a real limitation -- `sqlite3.threadsafety == 3`
    # means the underlying library is in serialized mode and does its own locking. With
    # WAL on top, concurrent readers and a single writer are safe, and only one turn ever
    # runs at a time so there is no writer contention to begin with.
    if sqlite3.threadsafety < 3:  # pragma: no cover - depends on the SQLite build
        raise RuntimeError(
            "this SQLite build is not serialized (threadsafety < 3), so the database "
            "cannot be shared between the UI and the turn engine. ATLAS needs a "
            "serialized build."
        )

    conn = sqlite3.connect(target, isolation_level=None, timeout=5.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = FULL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    # Rules and views join across event/diff/region constantly; letting SQLite keep more
    # of the index in memory is the cheapest win available.
    conn.execute("PRAGMA cache_size = -20000")

    # Bring an existing world up to the current schema. Skipped for a database that has
    # no schema yet (create_world_db is about to lay it down) and for a deliberately
    # read-only look at an exported file, which must never be written to.
    if not allow_synced and _has_schema(conn):
        if target != Path(":memory:") and rebuilds_pending(conn):
            _back_up_before_rebuild(conn, target)
        migrate(conn)
    return conn


def _back_up_before_rebuild(conn: sqlite3.Connection, target: Path) -> Path:
    """Keep a copy of a save before a migration rebuilds one of its tables.

    Additive migrations cannot lose anything and are not worth a copy of the world. A
    rebuild copies every row of a table across and drops the original, and one of them
    once emptied every pact's parties on the way (see ``migrations._apply_rebuild``), so
    the save as it stood is kept beside it as ``world.db.schema-<N>.bak``.

    Beside it, not anywhere else: :func:`connect` has already refused a synced folder, so
    the directory the live database sits in is by construction one that may hold a
    database. An older copy of the same name is replaced, not kept: a failed migration
    rolls back whole, so the file being opened *is* the save as it stood, and a copy left
    over from a different world once replaced at this path would not be.
    """
    backup = target.with_name(f"{target.name}.schema-{current_version(conn)}.bak")
    return export_world(conn, backup)


def _has_schema(conn: sqlite3.Connection) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'world'"
    ).fetchone() is not None


def _schema_sql() -> str:
    return resources.files("atlas.state").joinpath("schema.sql").read_text(encoding="utf-8")


def create_world_db(path: Path | str, *, allow_synced: bool = False) -> sqlite3.Connection:
    """Create and initialise a fresh world database.

    There is no migration framework by design, so this applies the whole schema once and
    stamps ``schema_version``. Opening an existing database with a different version is
    an error the caller must handle by starting a new world.
    """
    conn = connect(path, allow_synced=allow_synced)
    conn.executescript(_schema_sql())
    # schema.sql *is* the current schema, so a fresh world starts stamped and no
    # migration ever runs against it. A test asserts this converges with the migrated
    # path, which is the only thing keeping the two definitions honest.
    stamp(conn, SCHEMA_VERSION)
    return conn


def export_world(conn: sqlite3.Connection, destination: Path | str) -> Path:
    """Write a quiescent single-file copy of the world to ``destination``.

    ``VACUUM INTO`` refuses to overwrite an existing file, so this vacuums to a temporary
    name and then replaces atomically. The replace is retried because OneDrive routinely
    holds a freshly written file open for a moment, surfacing as ``WinError 32``.
    """
    dest = Path(destination)
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = dest.with_suffix(dest.suffix + ".tmp")
    staging.unlink(missing_ok=True)

    conn.execute("VACUUM INTO ?", (str(staging),))

    last: OSError | None = None
    for attempt in range(4):
        try:
            os.replace(staging, dest)
            return dest
        except OSError as exc:  # pragma: no cover - timing dependent
            last = exc
            time.sleep(0.25 * (attempt + 1))
    staging.unlink(missing_ok=True)
    raise OSError(f"could not replace {dest} after 4 attempts") from last


def sqlite_uri(path: PurePath) -> str:
    """``path``, which must be absolute, as a ``file:`` URI SQLite reads back unchanged.

    **Not ``f"file:{path}"``.** SQLite decodes the URI it is given, so a ``#`` in the path
    ends it (``C:\\Users\\C#Dev\\...`` opens ``C:\\Users\\C``) and ``%XX`` is decoded into
    a different name. ``#`` is legal in a Windows user name, so a whole install could sit
    under such a path. ``as_uri`` percent-encodes both and writes a drive path as
    ``file:///C:/...``, the form SQLite documents for Windows.

    A UNC path comes out of ``as_uri`` as ``file://server/share/...``, and SQLite refuses a
    URI authority unless built with ``SQLITE_ALLOW_URI_AUTHORITY``; four slashes put the
    host back in the path, where SQLite hands it to Windows as ``\\\\server\\share\\...``.
    """
    uri = path.as_uri()
    if not uri.startswith("file:///"):
        uri = "file:////" + uri[len("file://"):]
    return uri


def read_only_uri(path: Path | str) -> str:
    """The URI that opens ``path`` read-only: ``sqlite3.connect(read_only_uri(p), uri=True)``.

    Resolved first because a URI has no relative form that SQLite and ``as_uri`` agree on.
    """
    return sqlite_uri(Path(path).resolve()) + "?mode=ro"
