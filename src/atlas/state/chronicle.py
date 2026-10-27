"""A branch as a tree, a branch renamed, and a branch written out as a readable history.

Three small things over the lineage `state.branch` already records, and none of them adds
state: the tree is `branches()` arranged by the parent each one already names, a rename is a
directory rename plus the children pointed at the new name, and a chronicle is the world's
own `event` table read in order.

**Read-only where it can be.** `tree` and `chronicle` open nothing for writing and change no
fingerprint. `rename` is the one that touches disk, and it touches names rather than worlds:
no row of any world's state is edited except the lineage pointers that would otherwise point
at a directory that no longer exists.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from ..config.paths import worlds_root
from .branch import Branch, branches

__all__ = ["ChronicleEntry", "Node", "chronicle", "rename_branch", "tree", "render_tree"]


@dataclass(slots=True)
class Node:
    """One branch and the branches taken from it."""

    branch: Branch
    children: list[Node] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.branch.name


def tree(root: Path | None = None) -> list[Node]:
    """Every world on disk, arranged by what it was forked from. Roots first.

    **A flat list stopped being readable the moment forking was worth doing.** Six lines
    off one afternoon read as six unrelated worlds; the lineage that says which came from
    which was already stored and was shown as a sentence per row.

    A fork whose parent has been deleted or renamed away is returned as a root rather than
    dropped: it is still a playable line, and hiding it because its ancestry is broken
    would lose somebody's world.
    """
    found = branches(root)
    nodes = {branch.name: Node(branch=branch) for branch in found}
    roots: list[Node] = []
    for node in nodes.values():
        parent = nodes.get(node.branch.forked_from)
        if parent is None or parent is node:
            roots.append(node)
        else:
            parent.children.append(node)
    for node in nodes.values():
        node.children.sort(key=lambda child: (child.branch.forked_at, child.name))
    roots.sort(key=lambda node: node.name)
    return roots


def render_tree(roots: list[Node]) -> list[str]:
    """The tree as indented lines, for a terminal or a panel.

    Each line says what the branch is and, for a fork, the turn it left its parent -- which
    is the one fact that makes a tree more useful than an indented list.
    """
    lines: list[str] = []

    def walk(node: Node, depth: int) -> None:
        lead = "  " * depth + ("+- " if depth else "")
        where = (f"  (from {node.branch.forked_from} at turn {node.branch.forked_at})"
                 if node.branch.is_fork else "")
        lines.append(f"{lead}{node.name}  turn {node.branch.turn}  {node.branch.date}"
                     f"{where}")
        for child in node.children:
            walk(child, depth + 1)

    for root in roots:
        walk(root, 0)
    return lines


def rename_branch(old: str, new: str, *, root: Path | None = None) -> Branch:
    """Give a fork a name somebody chose. Returns the branch under its new name.

    **Names are how a player tells their lines apart**, and a fork is created with whatever
    was typed in a hurry -- often before the player knows what the line turned out to be.

    Refuses rather than overwrites when the new name is taken, for the reason `fork_world`
    refuses: quietly replacing somebody else's line is the one failure this feature must not
    have. Children are re-pointed at the new name in the same pass, so the tree above does
    not lose a subtree to a rename.
    """
    base = Path(root) if root is not None else worlds_root()
    cleaned = new.strip()
    if not cleaned or any(character in cleaned for character in '\\/:*?"<>|'):
        raise ValueError(f"{new!r} is not a usable name for a world")
    source, destination = base / old, base / cleaned
    if not (source / "world.db").exists():
        raise FileNotFoundError(f"there is no world called {old!r}")
    if destination.exists():
        raise FileExistsError(f"there is already a world called {cleaned!r}")

    source.rename(destination)
    # Anything that called the old name its parent would otherwise point at a directory
    # that is no longer there, and `tree` would show it as a root.
    for branch in branches(base):
        if branch.forked_from != old:
            continue
        # Closed explicitly: ``with sqlite3.connect(...)`` commits on exit but does *not*
        # close, and a handle left open holds the file on Windows until it is collected.
        child = sqlite3.connect(branch.path)
        try:
            child.execute(
                "UPDATE world_lineage SET forked_from = ? WHERE forked_from = ?",
                (cleaned, old))
            child.commit()
        finally:
            child.close()

    from .branch import _describe

    described = _describe(destination / "world.db", cleaned)
    if described is None:  # pragma: no cover - it was readable a moment ago
        raise OSError(f"the world at {destination} could not be read back")
    return described


@dataclass(frozen=True, slots=True)
class ChronicleEntry:
    """One turn of a line, as somebody would read it back."""

    turn: int
    date: str
    headlines: list[str]


def chronicle(
    conn: sqlite3.Connection, world_id: str, *, viewer: str | None = None,
    per_turn: int = 12,
) -> list[ChronicleEntry]:
    """The line's history, turn by turn, in the words the engine wrote at the time.

    **Nothing is composed here.** Every line is an `event.text` exactly as recorded, which is
    what makes this a chronicle rather than a summary: a reader can check any sentence in it
    against the event that produced it, and the inspector will show what caused that.

    ``viewer`` scopes it to what one nation was entitled to see, using the same predicate
    `state.visibility` uses -- so a campaign written from a player's seat contains their war
    and not everybody's secrets. ``None`` is the omniscient read, which is what a finished
    campaign is usually exported as.
    """
    rows = conn.execute(
        """
        SELECT e.turn AS turn, e.date AS date, e.text AS text
          FROM event e
         WHERE e.world_id = ?
           AND (? IS NULL
                OR e.visibility = 'public'
                OR e.actor_nation_id = ?
                OR EXISTS (SELECT 1 FROM event_visibility v
                            WHERE v.event_id = e.id AND v.nation_id = ?))
         ORDER BY e.turn, e.id
        """,
        (world_id, viewer, viewer, viewer),
    ).fetchall()

    by_turn: dict[int, ChronicleEntry] = {}
    for row in rows:
        turn = int(row["turn"])
        entry = by_turn.get(turn)
        if entry is None:
            entry = ChronicleEntry(turn=turn, date=str(row["date"] or ""), headlines=[])
            by_turn[turn] = entry
        if len(entry.headlines) < per_turn:
            entry.headlines.append(str(row["text"]))
    return [by_turn[turn] for turn in sorted(by_turn)]


def render_chronicle(
    entries: list[ChronicleEntry], *, title: str, lineage: str = ""
) -> str:
    """The chronicle as markdown, ready to be written to a file and read by a person."""
    lines = [f"# {title}", ""]
    if lineage:
        lines += [f"*{lineage}*", ""]
    if not entries:
        lines += ["Nothing has happened in this line yet.", ""]
        return "\n".join(lines)
    for entry in entries:
        lines.append(f"## Turn {entry.turn} — {entry.date}")
        lines += [f"- {headline}" for headline in entry.headlines]
        lines.append("")
    return "\n".join(lines)
