"""Forking a world at a turn, so a what-if does not cost you the line you were playing.

**A branch is a file, not a flag.** A world lives at ``worlds_root()/<name>/world.db`` and
its id appears in thirty of the thirty-seven tables, so giving a fork a new *id* would mean
rewriting almost every row in the database and every foreign key between them. Giving it a
new *directory* costs nothing, needs no schema change at all, and leaves both branches as
ordinary self-contained worlds that every existing tool already knows how to open.

The fork itself is two things the engine could already do:

* :func:`atlas.state.db.export_world` writes a quiescent copy -- the same call the export
  button uses, so a branch is exactly as sound as a saved game;
* :func:`atlas.state.replay.rewind_to` puts that copy back to the chosen turn, exactly, by
  undoing diffs newest-first.

So a fork is *copy, then rewind the copy*. The original is never opened for writing and
never touched: what makes the branch safe is that the rewind happens somewhere else.

**Lineage lives in its own table, and that placement is the load-bearing detail.** The
promise that makes a fork worth having is that the copy *is* the original as it stood, by
fingerprint -- and ``world`` is a fingerprinted table, so recording the lineage there made
every fork differ from its own origin by exactly the two fields saying it was one. It is
:table:`world_lineage` instead, which the digest does not read: where a file came from is a
fact about the file, not about the world inside it.
"""

from __future__ import annotations

import sqlite3
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from ..config.paths import worlds_root
from .db import connect, export_world, read_only_uri, world_db_path

__all__ = ["Branch", "branches", "fork_world", "lineage_of"]


@dataclass(frozen=True, slots=True)
class Branch:
    """One playable line, and where it came from."""

    name: str
    path: Path
    world_id: str
    turn: int
    date: str
    #: The branch this one was forked from, or ``""`` for an original line.
    forked_from: str = ""
    #: The turn it was forked at. ``0`` for an original line.
    forked_at: int = 0

    @property
    def is_fork(self) -> bool:
        return bool(self.forked_from)


def _describe(path: Path, name: str) -> Branch | None:
    """Read one world's identity without disturbing it."""
    # **Closed, not merely committed.** `with sqlite3.connect(...)` scopes the
    # *transaction* and leaves the handle open until it is collected -- and on Windows an
    # open handle on a world file makes its directory unrenamable, so listing the branches
    # was enough to stop one being renamed afterwards. Found by the rename below failing
    # with a bare PermissionError.
    probe: sqlite3.Connection | None = None
    try:
        probe = sqlite3.connect(read_only_uri(path), uri=True)
        probe.row_factory = sqlite3.Row
        row = probe.execute(
            "SELECT id, turn_number, world_date FROM world LIMIT 1").fetchone()
        if row is None:
            return None
        # The lineage columns are read defensively: a world written before migration 24
        # simply has none, and is an original line by definition.
        # Read defensively: a world written before migration 24 has no such table,
        # and a world with no row in it is an original line -- which is what every
        # world was before branching existed.
        try:
            extra = probe.execute(
                "SELECT forked_from, forked_at_turn FROM world_lineage"
                " WHERE world_id = ?", (row["id"],)).fetchone()
            came_from = str(extra["forked_from"]) if extra else ""
            came_at = int(extra["forked_at_turn"]) if extra else 0
        except sqlite3.OperationalError:
            came_from, came_at = "", 0
        return Branch(
            name=name, path=path, world_id=str(row["id"]),
            turn=int(row["turn_number"]), date=str(row["world_date"] or ""),
            forked_from=came_from, forked_at=came_at)
    except sqlite3.Error:
        return None
    finally:
        if probe is not None:
            probe.close()


def branches(root: Path | None = None) -> list[Branch]:
    """Every playable world on disk, originals and forks alike, oldest name first."""
    base = Path(root) if root is not None else worlds_root()
    if not base.exists():
        return []
    found = [
        described
        for entry in sorted(base.iterdir())
        if entry.is_dir() and (entry / "world.db").exists()
        for described in (_describe(entry / "world.db", entry.name),)
        if described is not None
    ]
    return found


def lineage_of(conn: sqlite3.Connection, world_id: str) -> tuple[str, int]:
    """``(forked_from, forked_at_turn)`` for an open world. ``("", 0)`` for an original."""
    try:
        row = conn.execute(
            "SELECT forked_from, forked_at_turn FROM world_lineage WHERE world_id = ?",
            (world_id,)).fetchone()
    except sqlite3.OperationalError:
        return "", 0
    if row is None:
        return "", 0
    return str(row["forked_from"]), int(row["forked_at_turn"])


def fork_world(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    at_turn: int,
    name: str,
    root: Path | None = None,
) -> Branch:
    """Copy this world and put the copy back to ``at_turn``. Returns the new branch.

    **The original is not touched.** It is read to make the copy and never written, so a
    fork cannot cost a player the line they were on -- which is the entire point, and is
    asserted against the original's fingerprint rather than assumed.

    Raises rather than guesses when the branch name is taken: silently overwriting somebody
    else's line is the one failure this feature must not have.
    """
    from .replay import can_rewind, rewind_to

    base = Path(root) if root is not None else worlds_root()
    destination = (base / name / "world.db") if root is not None else world_db_path(name)
    if destination.exists():
        raise FileExistsError(f"there is already a world called {name!r}")

    allowed, reason = can_rewind(conn, world_id, at_turn)
    if not allowed:
        raise ValueError(f"cannot fork at turn {at_turn}: {reason}")

    export_world(conn, destination)

    branch = connect(destination)
    try:
        report = rewind_to(branch, world_id, at_turn)
        if report.problems:
            raise ValueError("; ".join(report.problems))
        # Written after the rewind, not before: the rewind removes rows for turns beyond
        # the target, and lineage written first would go with them.
        branch.execute(
            "INSERT OR REPLACE INTO world_lineage"
            " (world_id, forked_from, forked_at_turn, forked_at) VALUES (?, ?, ?, ?)",
            (world_id, _own_name(conn, world_id), at_turn,
             _now(branch, world_id)))
        branch.commit()
    except Exception:
        branch.close()
        _discard_fork(destination)
        raise
    branch.close()

    described = _describe(destination, name)
    if described is None:
        # **Not left behind.** A fork that cannot be read back is a world the branch list
        # will never show and the name is taken by it all the same -- which is what a `#`
        # in the path did before `read_only_uri` (bug scan S6).
        _discard_fork(destination)
        raise OSError(f"the fork at {destination} could not be read back")
    return described


def _discard_fork(destination: Path) -> None:
    """Remove a fork that did not come to exist: its file, SQLite's sidecars, and its
    directory if that is now empty. Nothing else in the directory is touched."""
    for suffix in ("", "-wal", "-shm", "-journal"):
        destination.with_name(destination.name + suffix).unlink(missing_ok=True)
    with suppress(OSError):
        destination.parent.rmdir()


def _now(conn: sqlite3.Connection, world_id: str) -> str:
    """The in-world date the branch was taken at -- read back after the rewind, so it is
    the date the fork actually starts from rather than the one it was cut from."""
    row = conn.execute(
        "SELECT world_date FROM world WHERE id = ?", (world_id,)).fetchone()
    return str(row["world_date"] or "") if row else ""


def _own_name(conn: sqlite3.Connection, world_id: str) -> str:
    """What to record as the parent: the directory a world lives in, or its id.

    An in-memory world has no directory, and a test that forked one would otherwise record
    an empty parent and read as an original line.
    """
    row = conn.execute("PRAGMA database_list").fetchone()
    path = str(row[2]) if row and len(row) > 2 else ""
    return Path(path).parent.name if path else world_id
