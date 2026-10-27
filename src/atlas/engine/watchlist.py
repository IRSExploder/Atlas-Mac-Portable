"""The handful of things the player has decided to keep an eye on.

**A pin is a bookmark, and what it points at goes on living.** So a pin stores an identity
and nothing else -- kind and target, four bytes of intent -- and every line on the screen is
resolved from the world at the moment it is shown. A pinned province that changes hands says
so; a pinned power that declares war on you says so; a pinned objective that has been met
and pruned says *that*, rather than quietly vanishing and leaving the player wondering
whether they imagined pinning it.

**Fog applies, and it applies as staleness rather than as absence.** A province the player
knew about last month and cannot see today is still on the list, marked, because the fact
they were watching it is theirs and only what is *currently true about it* is hidden. A list
that silently dropped it would be a list the player could not trust, which is worse than one
that admits it has lost sight of something.

Nothing here writes to the world. Where the pins themselves live -- beside the save, never
inside it -- is :mod:`atlas.state.workspace`'s business, and the reason is there.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

__all__ = ["PIN_KINDS", "Pin", "WatchRow", "objectives_to_offer", "resolve", "toggled"]

#: What can be pinned. The first four are exactly what the search box returns, so anything
#: found is something that can be kept; the fifth is an intention rather than a thing, and
#: points at the ground it is about.
PIN_KINDS: tuple[str, ...] = ("region", "nation", "event", "treaty", "objective")

#: How many pins are a watchlist and not a second inbox. Small on purpose: the value of this
#: screen is that everything on it fits in one glance.
PIN_LIMIT = 24


@dataclass(frozen=True, slots=True)
class Pin:
    kind: str
    target: str

    def as_pair(self) -> list[str]:
        """The form kept on disk. A list rather than a tuple, because JSON has no tuples
        and a round trip that changed the type would make equality quietly stop working."""
        return [self.kind, self.target]


@dataclass(frozen=True, slots=True)
class WatchRow:
    """One pinned thing, as it stands right now."""

    kind: str
    target: str
    label: str
    #: What has become of it, in a phrase. The whole reason to resolve rather than store.
    detail: str = ""
    #: True when the world can no longer answer for this: a pruned objective, a deleted
    #: event, or ground that has gone back under the fog.
    stale: bool = False


def toggled(pins: list[Pin], pin: Pin) -> list[Pin]:
    """Add or remove, whichever the player meant. Newest first, capped.

    One function for both directions because a pin control is one control: the player
    clicks the same thing to start and to stop watching, and two entry points would let the
    two disagree about what counts as the same pin.
    """
    if pin.kind not in PIN_KINDS:
        return pins
    kept = [existing for existing in pins if existing != pin]
    if len(kept) == len(pins):
        kept.insert(0, pin)
    return kept[:PIN_LIMIT]


def resolve(
    conn: sqlite3.Connection, world_id: str, pins: list[Pin], *, viewer: str | None = None
) -> list[WatchRow]:
    """What each pin points at, as the world stands. Pure read."""
    return [_row(conn, world_id, pin, viewer) for pin in pins]


def _row(
    conn: sqlite3.Connection, world_id: str, pin: Pin, viewer: str | None
) -> WatchRow:
    reader = {
        "region": _region, "nation": _nation, "event": _event,
        "treaty": _treaty, "objective": _objective,
    }.get(pin.kind)
    if reader is None:
        return WatchRow(kind=pin.kind, target=pin.target, label=pin.target,
                        detail="no longer a kind of thing this can show", stale=True)
    return reader(conn, world_id, pin.target, viewer)


def _region(
    conn: sqlite3.Connection, world_id: str, target: str, viewer: str | None
) -> WatchRow:
    from ..map.query import known_regions

    row = conn.execute(
        "SELECT r.name, COALESCE(r.controller_nation_id, r.owner_nation_id) AS holder,"
        "       (SELECT n.name FROM nation n WHERE n.id ="
        "          COALESCE(r.controller_nation_id, r.owner_nation_id)) AS holder_name,"
        "       r.controller_nation_id, r.owner_nation_id"
        "  FROM region r WHERE r.world_id = ? AND r.id = ?", (world_id, target)).fetchone()
    if row is None:
        return WatchRow("region", target, target, "no longer on the map", stale=True)
    name = str(row["name"])
    if viewer is not None and target not in known_regions(conn, world_id, viewer):
        # Kept, and marked. The fact that they were watching it is the player's; only what
        # is currently true about it is not.
        return WatchRow("region", target, name, "you have lost sight of it", stale=True)
    holder = str(row["holder_name"] or "unclaimed")
    occupied = (row["controller_nation_id"]
                and row["controller_nation_id"] != row["owner_nation_id"])
    return WatchRow("region", target, name,
                    f"held by {holder}" + (" — occupied" if occupied else ""))


def _nation(
    conn: sqlite3.Connection, world_id: str, target: str, viewer: str | None
) -> WatchRow:
    row = conn.execute(
        "SELECT name, is_alive FROM nation WHERE world_id = ? AND id = ?",
        (world_id, target)).fetchone()
    if row is None:
        return WatchRow("nation", target, target, "no such power", stale=True)
    name = str(row["name"])
    if not row["is_alive"]:
        return WatchRow("nation", target, name, "no longer exists", stale=True)
    if viewer is None or viewer == target:
        return WatchRow("nation", target, name, "")
    standing = conn.execute(
        "SELECT score, at_war FROM relation WHERE world_id = ? AND nation_a = ?"
        "   AND nation_b = ?", (world_id, target, viewer)).fetchone()
    if standing is None:
        return WatchRow("nation", target, name, "no dealings with you")
    from ..map.render import attitude_word

    said = f"regards you: {attitude_word(float(standing['score'] or 0.0))}"
    return WatchRow("nation", target, name,
                    said + (" — at war with you" if standing["at_war"] else ""))


def _event(
    conn: sqlite3.Connection, world_id: str, target: str, viewer: str | None
) -> WatchRow:
    row = conn.execute(
        "SELECT e.id, e.turn, e.date, e.text FROM event e"
        " WHERE e.world_id = ? AND e.id = ?"
        "   AND (? IS NULL OR e.visibility = 'public' OR e.actor_nation_id = ?"
        "        OR EXISTS (SELECT 1 FROM event_visibility v"
        "                    WHERE v.event_id = e.id AND v.nation_id = ?))",
        (world_id, target, viewer, viewer, viewer)).fetchone()
    if row is None:
        # A rewind discards events, so a pin can outlive the thing it points at. Said
        # plainly rather than dropped: the player pinned something and is owed an answer.
        return WatchRow("event", target, f"event {target}",
                        "gone — rewound away, or never yours to see", stale=True)
    text = str(row["text"])
    return WatchRow("event", target,
                    text if len(text) <= 90 else text[:87] + "...",
                    f"turn {row['turn']} — {row['date']}")


def _treaty(
    conn: sqlite3.Connection, world_id: str, target: str, viewer: str | None
) -> WatchRow:
    from .search import _EVENT_VISIBLE

    # **The same gate search uses, because a pin is a search you saved.** An agreement is
    # visible through the event that recorded it; without the gate, a pin whose id a rewind
    # handed to somebody else's private agreement would read that agreement out by name.
    row = conn.execute(
        "SELECT c.kind, c.status,"
        "       (SELECT GROUP_CONCAT(n.name, ' and ') FROM commitment_nation cn"
        "          JOIN nation n ON n.id = cn.nation_id"
        "         WHERE cn.commitment_id = c.id) AS parties"
        "  FROM commitment c LEFT JOIN event e ON e.id = c.source_event_id"
        " WHERE c.world_id = ? AND c.id = ?"
        f"   AND (c.source_event_id IS NULL OR {_EVENT_VISIBLE})",
        (world_id, target, viewer, viewer, viewer)).fetchone()
    if row is None:
        return WatchRow("treaty", target, f"agreement {target}",
                        "gone — rewound away, or never yours to see", stale=True)
    label = str(row["kind"]).replace("_", " ")
    if row["parties"]:
        label += f": {row['parties']}"
    return WatchRow("treaty", target, label, str(row["status"]),
                    stale=str(row["status"]) in ("broken", "expired"))


def _objective(
    conn: sqlite3.Connection, world_id: str, target: str, viewer: str | None
) -> WatchRow:
    """A pinned intention, shown against the ground it is about.

    Stale rather than absent when the objective has been met, dropped or pruned. An
    intention that disappeared from the screen would leave the player wondering whether
    they had imagined pinning it, which is the failure this whole module is arranged
    against.
    """
    ground = _region(conn, world_id, target, viewer)
    if viewer is None:
        return WatchRow("objective", target, ground.label,
                        "no nation is intending anything", stale=True)
    row = conn.execute(
        "SELECT kind FROM military_objective WHERE world_id = ? AND nation_id = ?"
        "   AND region_id = ?", (world_id, viewer, target)).fetchone()
    if row is None:
        return WatchRow("objective", target, ground.label,
                        "no longer one of your objectives", stale=True)
    return WatchRow("objective", target, ground.label,
                    f"{str(row['kind']).replace('_', ' ')} — {ground.detail}",
                    stale=ground.stale)


def objectives_to_offer(
    conn: sqlite3.Connection, world_id: str, viewer: str | None
) -> list[WatchRow]:
    """What the player currently intends, so the screen has something to pin *from*.

    A watchlist you can only add to from somewhere else is a watchlist most players never
    put anything on, and objectives are a small enumerable set -- unlike provinces, of which
    there are four thousand.
    """
    if viewer is None:
        return []
    from .objectives import objectives_of

    found = []
    for kind, regions in objectives_of(conn, world_id, viewer).items():
        for region_id in regions:
            row = _region(conn, world_id, region_id, viewer)
            found.append(WatchRow("objective", region_id, row.label,
                                  f"{kind.replace('_', ' ')} — {row.detail}"))
    return found
