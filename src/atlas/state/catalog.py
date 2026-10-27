"""The saved worlds on this machine, for the main menu's Continue and Load.

Read-only and forgiving. Every world is opened with a read-only URI and closed at once,
and a world that cannot be read -- written by a newer build, half-copied, not a database
at all -- is still listed, with the reason, rather than hidden: a save that silently
vanished from the list reads exactly like a save that was lost.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

__all__ = ["SavedWorld", "list_worlds", "most_recent"]


@dataclass(frozen=True, slots=True)
class SavedWorld:
    world_id: str
    path: Path
    #: When the file last changed: what "most recent" means to a player.
    modified: datetime
    name: str = ""
    preset_id: str | None = None
    world_date: str = ""
    turn: int = 0
    #: Why it cannot be opened, or ``""`` when it can.
    problem: str = ""

    @property
    def playable(self) -> bool:
        return not self.problem

    def describe(self) -> str:
        if self.problem:
            return f"{self.world_id} -- cannot open: {self.problem}"
        era = self.preset_id or "custom"
        return f"{self.name or self.world_id} -- {era}, {self.world_date}, turn {self.turn}"


def _read(world_id: str, path: Path) -> SavedWorld:
    from .db import read_only_uri
    from .migrations import SCHEMA_VERSION

    modified = datetime.fromtimestamp(path.stat().st_mtime)
    try:
        with sqlite3.connect(read_only_uri(path), uri=True) as probe:
            # The schema authority is the pragma, not `world.schema_version` -- see
            # `state.migrations`; it is what `migrate` refuses on.
            version = int(probe.execute("PRAGMA user_version").fetchone()[0])
            row = probe.execute(
                "SELECT name, preset_id, world_date, turn_number "
                "FROM world WHERE id = ?", (world_id,)).fetchone()
    except sqlite3.Error as exc:
        return SavedWorld(world_id, path, modified, problem=f"unreadable ({exc})")
    if row is None:
        return SavedWorld(world_id, path, modified, problem="no world of that name inside")
    name, preset_id, world_date, turn = row
    problem = ""
    if version > SCHEMA_VERSION:
        problem = f"made by a newer version of ATLAS (save format {version})"
    return SavedWorld(world_id, path, modified, name=str(name or ""), preset_id=preset_id,
                      world_date=str(world_date or ""), turn=int(turn or 0), problem=problem)


def list_worlds(root: Path | None = None) -> list[SavedWorld]:
    """Every ``<root>/<world_id>/world.db``, most recently played first."""
    from ..config.paths import worlds_root

    base = root if root is not None else worlds_root()
    if not base.is_dir():
        return []
    found = [_read(entry.name, entry / "world.db")
             for entry in base.iterdir() if (entry / "world.db").is_file()]
    return sorted(found, key=lambda world: world.modified, reverse=True)


def most_recent(root: Path | None = None) -> SavedWorld | None:
    """What Continue opens: the newest world that can actually be opened."""
    return next((world for world in list_worlds(root) if world.playable), None)
