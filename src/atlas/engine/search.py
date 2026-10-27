"""One box that finds anything in the world, and never finds what the player cannot see.

**A search is a view, so it obeys the same rules every other view does.** A box that matched
across the whole `event` table would hand over every secret in the game to anybody who
guessed a word -- the treaty nobody told you about, the coup being planned against you, the
covert backing you are supposed to be denying. So events are filtered by exactly the
predicate `state.visibility` uses, provinces by the same presence-based knowledge the map's
fog draws, and a nation whose ground you have never seen is still findable by name because a
nation's existence is not a secret.

**Ranked by how well the query matched, not by what kind of thing it is.** A player typing
*ver* wants Verdun before it wants a treaty whose text happens to contain the word, and a
player typing an exact id wants that thing first. Exact, prefix, word-start, contained -- four
tiers, and within a tier the shorter name wins, because a shorter name containing your query
is a better match for it.

Nothing here writes. It is a `SELECT` with a viewer on it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

__all__ = ["HIT_KINDS", "Hit", "search"]

#: What a hit can be, and what the caller should do with each. The UI switches on this, so
#: adding a kind means adding a way to reach it -- a hit that leads nowhere is worse than no
#: hit at all.
HIT_KINDS: tuple[str, ...] = ("region", "nation", "event", "treaty")

#: How many of each kind to offer before the list stops being a list and starts being a
#: dump. Per kind rather than overall, so a query matching four hundred provinces still
#: leaves room for the treaty it also matched.
PER_KIND = 8


@dataclass(frozen=True, slots=True)
class Hit:
    """One thing the player could jump to."""

    kind: str
    #: What to hand back to the caller: a region id, a nation id, an event id as text, or a
    #: commitment id as text.
    target: str
    label: str
    #: One line of context, so two provinces with the same name are told apart.
    detail: str = ""
    #: Lower is better. Exposed so a caller can merge several searches and re-sort.
    rank: float = 0.0


def _score(query: str, candidate: str) -> float | None:
    """How well ``candidate`` answers ``query``, or ``None`` if it does not.

    Four tiers, and the length tiebreak inside them is the part that matters in practice:
    on a full map *ind* matches India, Indiana and the Independence of a dozen colonies, and
    the one the player meant is almost always the shortest thing that contains it.
    """
    text = candidate.casefold().strip()
    if not text:
        return None
    if text == query:
        return 0.0 + len(text) / 1000.0
    if text.startswith(query):
        return 1.0 + len(text) / 1000.0
    if any(word.startswith(query) for word in text.replace("_", " ").split()):
        return 2.0 + len(text) / 1000.0
    if query in text:
        return 3.0 + len(text) / 1000.0
    return None


def _best(query: str, *candidates: str) -> float | None:
    """The best score any of a thing's names earns. An id and a display name are two ways of
    asking for the same province, and either should find it."""
    found = [score for score in (_score(query, c) for c in candidates) if score is not None]
    return min(found) if found else None


def search(
    conn: sqlite3.Connection,
    world_id: str,
    query: str,
    *,
    viewer: str | None = None,
    limit: int = PER_KIND,
) -> list[Hit]:
    """Everything matching ``query`` that ``viewer`` is entitled to find.

    ``viewer`` of ``None`` is the omniscient case -- the map editor and EVE spectating --
    exactly as it is for :func:`atlas.map.query.load_map_view`. In an ordinary game it is
    the player's nation, and a search cannot see round the fog.
    """
    wanted = query.casefold().strip()
    if len(wanted) < 2:
        # One letter matches most of the world and answers nothing. Two is where a query
        # starts being a question.
        return []

    found = [
        *_regions(conn, world_id, wanted, viewer, limit),
        *_nations(conn, world_id, wanted, limit),
        *_events(conn, world_id, wanted, viewer, limit),
        *_treaties(conn, world_id, wanted, viewer, limit),
    ]
    return sorted(found, key=lambda hit: (hit.rank, hit.kind, hit.label))


def _regions(
    conn: sqlite3.Connection, world_id: str, query: str, viewer: str | None, limit: int
) -> list[Hit]:
    """Provinces the viewer knows about, by name or by id.

    Knowledge is the map's own: what you hold, what you occupy, what borders it, and where
    your forces are standing. Reusing that function rather than writing the predicate again
    is the whole point -- a search that could find a province the fog is hiding would be a
    hole in the fog with a text box in front of it.
    """
    from ..map.query import known_regions

    known = known_regions(conn, world_id, viewer)
    names = {
        str(row["id"]): str(row["name"])
        for row in conn.execute(
            "SELECT id, name FROM nation WHERE world_id = ?", (world_id,))
    }
    hits = []
    for row in conn.execute(
        "SELECT id, name, kind, owner_nation_id, controller_nation_id FROM region"
        " WHERE world_id = ? AND kind <> 'ocean'", (world_id,)
    ):
        region_id = str(row["id"])
        if region_id not in known:
            continue
        rank = _best(query, str(row["name"]), region_id)
        if rank is None:
            continue
        holder = row["controller_nation_id"] or row["owner_nation_id"] or ""
        hits.append(Hit(
            kind="region", target=region_id, label=str(row["name"]), rank=rank,
            detail=names.get(str(holder), "unclaimed")))
    return sorted(hits, key=lambda hit: hit.rank)[:limit]


def _nations(
    conn: sqlite3.Connection, world_id: str, query: str, limit: int
) -> list[Hit]:
    """Living powers, by name or by id.

    **Not gated on knowing their ground**, and that is deliberate rather than an oversight.
    A nation's existence is not a secret -- every briefing this program writes names the
    other powers, and it always has. What is hidden is where they are and what they hold,
    and a hit here reveals neither: it jumps to a capital the viewer can only see if the
    fog already lets it.
    """
    hits = []
    for row in conn.execute(
        "SELECT id, name, capital_region_id FROM nation"
        " WHERE world_id = ? AND is_alive = 1", (world_id,)
    ):
        rank = _best(query, str(row["name"]), str(row["id"]))
        if rank is None:
            continue
        hits.append(Hit(
            kind="nation", target=str(row["id"]), label=str(row["name"]), rank=rank,
            detail=str(row["capital_region_id"] or "")))
    return sorted(hits, key=lambda hit: hit.rank)[:limit]


#: The visibility predicate, in exactly the words `state.visibility._rows` uses. Written out
#: rather than imported because that function returns a window rather than a clause -- but if
#: one of the two ever changes, `test_a_search_cannot_see_round_the_fog` fails.
_EVENT_VISIBLE = """
    (? IS NULL
     OR e.visibility = 'public'
     OR e.actor_nation_id = ?
     OR EXISTS (SELECT 1 FROM event_visibility v
                 WHERE v.event_id = e.id AND v.nation_id = ?))
"""


def _events(
    conn: sqlite3.Connection, world_id: str, query: str, viewer: str | None, limit: int
) -> list[Hit]:
    """What happened, in the words it was recorded in -- and only what the viewer saw."""
    hits = []
    for row in conn.execute(
        f"SELECT e.id, e.turn, e.date, e.kind, e.text FROM event e"
        f" WHERE e.world_id = ? AND {_EVENT_VISIBLE} ORDER BY e.id DESC LIMIT 4000",
        (world_id, viewer, viewer, viewer)
    ):
        rank = _best(query, str(row["text"]), str(row["kind"]))
        if rank is None:
            continue
        text = str(row["text"])
        hits.append(Hit(
            kind="event", target=str(row["id"]), rank=rank,
            label=text if len(text) <= 90 else text[:87] + "...",
            detail=f"turn {row['turn']} — {row['date']}"))
    return sorted(hits, key=lambda hit: hit.rank)[:limit]


def _treaties(
    conn: sqlite3.Connection, world_id: str, query: str, viewer: str | None, limit: int
) -> list[Hit]:
    """Agreements, by what they are and who signed them.

    Visible through the event that recorded them, which is the honest gate: an agreement
    two other powers reached in private is not something a third can search for, and the
    event carrying it is exactly where that was already decided.
    """
    names = {
        str(row["id"]): str(row["name"])
        for row in conn.execute(
            "SELECT id, name FROM nation WHERE world_id = ?", (world_id,))
    }
    hits = []
    for row in conn.execute(
        f"""
        SELECT c.id, c.kind, c.status, c.source_event_id, c.condition_text,
               (SELECT GROUP_CONCAT(cn.nation_id, ',') FROM commitment_nation cn
                 WHERE cn.commitment_id = c.id) AS parties
          FROM commitment c
          LEFT JOIN event e ON e.id = c.source_event_id
         WHERE c.world_id = ?
           AND (c.source_event_id IS NULL OR {_EVENT_VISIBLE})
         ORDER BY c.id DESC
        """,
        (world_id, viewer, viewer, viewer)
    ):
        parties = [p for p in str(row["parties"] or "").split(",") if p]
        signatories = [names.get(p, p) for p in parties]
        rank = _best(query, str(row["kind"]), str(row["condition_text"] or ""), *signatories)
        if rank is None:
            continue
        hits.append(Hit(
            kind="treaty", target=str(row["id"]), rank=rank,
            label=str(row["kind"]).replace("_", " ")
            + (f": {' and '.join(signatories)}" if signatories else ""),
            detail=str(row["status"])))
    return sorted(hits, key=lambda hit: hit.rank)[:limit]
