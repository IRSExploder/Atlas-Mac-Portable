"""Omniscient reads. For the world simulator only.

The world simulator is a referee, not a participant. It resolves what happens everywhere,
so it must see everything -- including the secret pacts and hidden intentions that no
individual nation may know. That is correct and deliberate.

**This module is quarantined on purpose.** The danger is not that the referee sees too
much; it is that an omniscient reader, once it exists, gets reused somewhere it should not
be. So it lives apart from :mod:`atlas.state.visibility`, every function is named
``all_*``, and a test asserts that nothing outside the world-sim view imports it. An
unexpected import shows up immediately in review rather than hiding inside a diff.

The corresponding hazard is the referee writing a secret into publicly observable state --
a relation shift everyone can see, caused by a pact only two powers know about. That is
not solved here; rule INFO-002 refuses any diff whose visibility is broader than its cause.
"""

from __future__ import annotations

import sqlite3

from .tables import json_list

__all__ = [
    "all_commitments",
    "all_events",
    "all_nations",
    "all_regions",
    "all_relations",
    "world_summary",
]


def world_summary(conn: sqlite3.Connection, world_id: str) -> dict:
    row = conn.execute(
        "SELECT id, name, world_date, turn_number, difficulty, mode FROM world WHERE id = ?",
        (world_id,),
    ).fetchone()
    if row is None:
        return {}
    return {
        "world": row["name"],
        "date": row["world_date"],
        "turn": row["turn_number"],
        "difficulty": row["difficulty"],
        "mode": row["mode"],
    }


def all_nations(conn: sqlite3.Connection, world_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, name, is_player, personality_blurb, standing_goals_json,
               military_strength, economy, stability, legitimacy, treasury, manpower,
               is_tracked, is_alive
          FROM nation WHERE world_id = ? AND is_alive = 1 ORDER BY name
        """,
        (world_id,),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "is_player": bool(row["is_player"]),
            "character": row["personality_blurb"],
            "goals": json_list(row["standing_goals_json"]),
            "military": round(row["military_strength"], 1),
            "economy": round(row["economy"], 1),
            "stability": round(row["stability"], 1),
            "legitimacy": round(row["legitimacy"], 1),
            "treasury": round(row["treasury"], 1),
            "manpower": round(row["manpower"], 1),
            "tracked": bool(row["is_tracked"]),
        }
        for row in rows
    ]


def all_regions(conn: sqlite3.Connection, world_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, name, kind, terrain, owner_nation_id, controller_nation_id,
               population, development
          FROM region WHERE world_id = ? AND kind <> 'ocean' ORDER BY name
        """,
        (world_id,),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "owner": row["owner_nation_id"],
            "occupied_by": row["controller_nation_id"]
            if row["controller_nation_id"] != row["owner_nation_id"]
            else None,
            "terrain": row["terrain"],
            "population": round(row["population"], 1),
        }
        for row in rows
    ]


def all_relations(conn: sqlite3.Connection, world_id: str, *, only_notable: bool = True) -> list[dict]:
    """Relations between nations.

    ``only_notable`` drops the neutral middle by default. In a 242-nation world the full
    matrix is 58,000 rows, which would swamp the context with pairs that have never
    interacted and tell the simulator nothing.
    """
    sql = """
        SELECT nation_a, nation_b, score, treaty_status, at_war
          FROM relation WHERE world_id = ?
    """
    if only_notable:
        sql += " AND (at_war = 1 OR treaty_status <> 'none' OR ABS(score) >= 20)"
    sql += " ORDER BY at_war DESC, ABS(score) DESC"

    return [
        {
            "a": row["nation_a"],
            "b": row["nation_b"],
            "score": round(row["score"], 1),
            "treaty": row["treaty_status"],
            "at_war": bool(row["at_war"]),
        }
        for row in conn.execute(sql, (world_id,))
    ]


#: A nation's routine public works, from the referee's seat.
#:
#: **Not the same predicate as `visibility._ROUTINE_CONSTRUCTION`, and deliberately not
#: shared with it.** That one asks "construction belonging to somebody other than the
#: viewer", which in SQL needs `actor_nation_id <> ?` -- and passing NULL for a refereethat
#: has no nation of its own inverts it silently, because `'usa' <> NULL` is NULL rather than
#: true. The referee's question is simply "construction belonging to any nation", which the
#: aggregate `N project(s) advance in the yards` line (no actor) is not.
_A_NATIONS_CONSTRUCTION = "(kind = 'construction' AND actor_nation_id IS NOT NULL)"


def routine_construction(
    conn: sqlite3.Connection, world_id: str, *, since_turn: int = 0
) -> int:
    """How many of the nations' public works the referee's window left out."""
    return int(conn.execute(
        f"SELECT COUNT(*) FROM event WHERE world_id = ? AND turn >= ?"
        f"   AND {_A_NATIONS_CONSTRUCTION}",
        (world_id, since_turn),
    ).fetchone()[0])


def all_events(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    since_turn: int = 0,
    until_turn: int | None = None,
    limit: int = 120,
    without_routine_construction: bool = False,
) -> list[dict]:
    """Every event, including secret ones. Referee only.

    ``without_routine_construction`` drops the nations' public works from the window itself.
    The referee has no nation, so *all* of it was elsewhere to it and was being discarded
    after the cap — which spent 32 of 120 slots on pt34e and 40 on pt34g fetching events
    that were then deleted, while the cap bound exactly. Excluded here instead, so the
    window is spent on what the referee will actually read.

    ``until_turn`` closes the window at the top. Without it the only bound is recency, so a
    caller asking about an *old* stretch of a busy world gets the newest ``limit`` rows --
    which on a map that records more than ``limit`` events after that stretch contains none
    of it. A caller that then filters the result down to its stretch is left with nothing;
    see :func:`atlas.engine.digest.consolidate`, which did exactly that.
    """
    bounded = " AND turn <= ?" if until_turn is not None else ""
    upper = (until_turn,) if until_turn is not None else ()
    clause = f" AND NOT {_A_NATIONS_CONSTRUCTION}" if without_routine_construction else ""
    rows = conn.execute(
        f"""
        SELECT id, turn, date, kind, origin, visibility, text, actor_nation_id
          FROM event
         WHERE world_id = ? AND turn >= ?{bounded}{clause}
         ORDER BY turn DESC, id DESC LIMIT ?
        """,
        (world_id, since_turn, *upper, limit),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "turn": row["turn"],
            "date": row["date"],
            "kind": row["kind"],
            "actor": row["actor_nation_id"],
            "visibility": row["visibility"],
            "text": row["text"],
        }
        for row in reversed(rows)
    ]


def all_commitments(conn: sqlite3.Connection, world_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT c.id, c.kind, c.status, c.condition_text, c.created_turn,
               GROUP_CONCAT(cn.nation_id) AS parties
          FROM commitment c
          LEFT JOIN commitment_nation cn ON cn.commitment_id = c.id
         WHERE c.world_id = ? AND c.status IN ('pending', 'active')
         GROUP BY c.id ORDER BY c.created_turn DESC
        """,
        (world_id,),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "kind": row["kind"],
            "status": row["status"],
            "condition": row["condition_text"],
            "parties": (row["parties"] or "").split(","),
        }
        for row in rows
    ]
