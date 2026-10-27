"""Where the player left off, kept beside the save and never inside it.

**Two kinds of thing look alike and must not be stored alike.** What a nation holds is
*world state*: it is in the database, it is in the fingerprint, a rewind undoes it and a
replay reproduces it. Which provinces the player has pinned, and what they had half-typed
when they closed the window, are neither -- they are facts about a person using a program.

Putting them in the database would make both untrue at once. The fingerprint would move
because somebody clicked a pin, two runs of the same seeded world would stop comparing
equal, and a rewind would throw away a note the player made about what to watch. So they
live in a JSON file **next to** the world file, which makes the guarantee structural rather
than careful: there is no code path from a preference to a digest, because they are not in
the same file.

A world with no file -- ``:memory:``, which is every test and the map editor -- has no
workspace and says so by returning nothing. Nothing here raises on a missing, unreadable or
corrupt file: a preference that fails to load is a preference the player sets again, and
crashing a strategy game over a malformed pin list would be absurd.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

__all__ = [
    "SUFFIX",
    "forget",
    "recall",
    "remember",
    "workspace_path",
]

#: What the sidecar is called. Beside the save and obviously derived from it, so a player
#: looking at the folder can tell what it belongs to and delete it without wondering.
SUFFIX = ".workspace.json"

#: Bumped only if the shape of the file changes incompatibly. An older or newer number is
#: read as "no preferences", which is the safe answer for a file nobody must depend on.
VERSION = 1


def workspace_path(conn: sqlite3.Connection) -> Path | None:
    """The sidecar for this connection's world, or ``None`` if it has no file.

    Read off the connection rather than passed in, so a caller cannot accidentally point
    two worlds at one workspace by forgetting to thread a path through.
    """
    try:
        rows = conn.execute("PRAGMA database_list").fetchall()
    except sqlite3.Error:
        return None
    for row in rows:
        # (seq, name, file). "main" is the world; attached databases are not ours.
        if str(row[1]) == "main" and str(row[2] or ""):
            return Path(str(row[2])).with_suffix(Path(str(row[2])).suffix + SUFFIX)
    return None


def _load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != VERSION:
        return {}
    kept = data.get("kept")
    return kept if isinstance(kept, dict) else {}


def recall(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    """What was remembered under ``key``, or ``default``.

    ``default`` is returned for a world with no file, an absent file, an unreadable one and
    an unparseable one, deliberately without distinguishing them: every one of those means
    *there is nothing to restore*, and a caller that treated them differently would be
    treating a corrupt file as an error worth showing somebody.
    """
    path = workspace_path(conn)
    if path is None or not path.exists():
        return default
    found = _load(path).get(key, default)
    return found


def remember(conn: sqlite3.Connection, key: str, value: Any) -> bool:
    """Keep ``value`` under ``key``. ``False`` if this world has nowhere to keep it.

    Written whole and atomically -- a temporary file in the same directory, then a replace
    -- so a crash mid-write leaves the previous preferences rather than half of the new
    ones. The whole file is small by construction: it holds what a person clicked.
    """
    path = workspace_path(conn)
    if path is None:
        return False
    kept = _load(path)
    kept[key] = value
    payload = json.dumps({"version": VERSION, "kept": kept}, indent=1, sort_keys=True)
    # Named for the file it will become and made in the same directory, so the replace
    # below is a rename within one filesystem and therefore atomic.
    beside = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        beside.write_text(payload, encoding="utf-8")
        os.replace(beside, path)
    except OSError:
        beside.unlink(missing_ok=True)
        return False
    return True


def forget(conn: sqlite3.Connection, key: str) -> bool:
    """Drop one key. Absent is not an error -- it is the state being asked for."""
    return remember(conn, key, None)
