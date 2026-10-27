"""Reads scoped to what one nation may legitimately know.

**Every function here takes a viewer.** There is deliberately no ``get_all_events`` or
``get_all_nations`` -- not because such a function would be hard to write, but because its
existence is the leak. Once an unscoped reader exists, some future call site uses it "just
for a moment" and the asymmetry guarantee quietly stops holding.

The world simulator genuinely does need omniscience: it is the referee, not a player.
That path lives in :mod:`atlas.state.referee`, in its own module, so the two are never
confused and an import of the omniscient one is visible in review.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..engine.reference import label
from ..engine.tainted import Tainted
from .tables import json_list

__all__ = [
    "DEFAULT_SALIENCE",
    "FRONT_ENEMIES",
    "FRONT_REGIONS",
    "FrontRegion",
    "NationBrief",
    "SALIENCE",
    "by_salience",
    "fronts_of",
    "known_events",
    "nation_self",
    "other_nations",
    "own_regions",
    "relations_of",
    "salience_of",
    "standing_commitments",
]


@dataclass(frozen=True, slots=True)
class NationBrief:
    """What one nation knows about another.

    Estimates rather than figures: a rival's exact economy is not observable, and handing
    over the true number would be a leak dressed as a convenience.
    """

    nation_id: str
    name: str
    relation_score: float
    treaty_status: str
    at_war: bool
    military_estimate: str
    is_neighbour: bool


def nation_self(conn: sqlite3.Connection, viewer: str) -> dict:
    """A nation's own attributes. It knows these exactly."""
    row = conn.execute(
        """
        SELECT id, name, personality_blurb, standing_goals_json, military_strength,
               economy, stability, legitimacy, treasury, manpower, capital_region_id
          FROM nation WHERE id = ?
        """,
        (viewer,),
    ).fetchone()
    if row is None:
        return {}
    return {
        "id": row["id"],
        "name": row["name"],
        "character": row["personality_blurb"],
        "standing_goals": json_list(row["standing_goals_json"]),
        "military_strength": round(row["military_strength"], 1),
        "economy": round(row["economy"], 1),
        "stability": round(row["stability"], 1),
        "legitimacy": round(row["legitimacy"], 1),
        "treasury": round(row["treasury"], 1),
        "manpower": round(row["manpower"], 1),
        "capital": row["capital_region_id"],
    }


def _band(value: float, reference: float) -> str:
    """Describe a rival's strength relative to your own, in words.

    A ratio is deliberately coarse. Publishing the exact figure would let a model compute
    with information no foreign ministry would actually have.

    **``value`` is an estimate, not the truth, and that was a real hole until it was.** This
    used to be handed the rival's actual ``military_strength``: coarse, but exact, identical
    for every observer, and free -- a landlocked minor with no attaches abroad read Berlin
    exactly as well as Britain did. It is now fed the midpoint of what the viewer's own
    service believes, so the bucket a nation lands in depends on who is looking. See
    :mod:`atlas.engine.intelligence`.
    """
    if reference <= 0:
        return "unknown"
    ratio = value / reference
    if ratio < 0.4:
        return "far weaker"
    if ratio < 0.8:
        return "weaker"
    if ratio < 1.25:
        return "comparable"
    if ratio < 2.5:
        return "stronger"
    return "far stronger"


def other_nations(conn: sqlite3.Connection, viewer: str) -> list[NationBrief]:
    """Every other living nation, as this one perceives it.

    *Perceives*, and the word is load-bearing: a rival's strength arrives here through its
    estimate rather than its row. A viewer with a good service in one capital and none in
    another sees the first clearly and the second badly, which is the ordinary case and was
    not previously expressible.
    """

    own = conn.execute(
        "SELECT military_strength, world_id FROM nation WHERE id = ?", (viewer,)
    ).fetchone()
    reference = float(own["military_strength"]) if own else 0.0
    world_id = str(own["world_id"]) if own else ""
    clock = conn.execute(
        "SELECT turn_number FROM world WHERE id = ?", (world_id,)).fetchone()
    turn = int(clock["turn_number"]) if clock else 0

    rows = conn.execute(
        """
        SELECT n.id, n.name, n.military_strength,
               COALESCE(r.score, 0)              AS score,
               COALESCE(r.treaty_status, 'none') AS treaty_status,
               COALESCE(r.at_war, 0)             AS at_war,
               EXISTS (
                   SELECT 1
                     FROM region_adjacency a
                     JOIN region ra ON ra.id = a.region_a
                     JOIN region rb ON rb.id = a.region_b
                    WHERE (ra.owner_nation_id = ? AND rb.owner_nation_id = n.id)
                       OR (rb.owner_nation_id = ? AND ra.owner_nation_id = n.id)
               ) AS is_neighbour
          FROM nation n
          LEFT JOIN relation r ON r.nation_a = ? AND r.nation_b = n.id
         WHERE n.world_id = (SELECT world_id FROM nation WHERE id = ?)
           AND n.id <> ? AND n.is_alive = 1
           -- An international body is a nation row with no territory, so without this it
           -- appears in every power roster as a country with zero of everything and
           -- invites a staff to declare war on the United Nations.
           AND NOT EXISTS (SELECT 1 FROM organisation o WHERE o.nation_id = n.id)
         ORDER BY n.name
        """,
        (viewer, viewer, viewer, viewer, viewer),
    ).fetchall()

    return [
        NationBrief(
            nation_id=row["id"],
            name=row["name"],
            relation_score=round(row["score"], 1),
            treaty_status=row["treaty_status"],
            at_war=bool(row["at_war"]),
            # The midpoint of the estimate, never the row. Two viewers looking at the same
            # country from different services get different words for it, which is the
            # whole of the intelligence layer showing up in the oldest sentence in the
            # briefing.
            military_estimate=_band(
                _believed(conn, world_id, viewer, str(row["id"]),
                          float(row["military_strength"]), turn),
                reference),
            is_neighbour=bool(row["is_neighbour"]),
        )
        for row in rows
    ]


def _believed(
    conn: sqlite3.Connection, world_id: str, viewer: str, target: str,
    value: float, turn: int,
) -> float:
    """What the viewer's service thinks a figure is: the middle of the band it reports.

    Deliberately the *reported* band's midpoint rather than a separate draw, so the coarse
    word in the power roster and the numeric range in the intelligence section can never
    disagree about the same country.
    """
    from ..engine.intelligence import estimate_of

    guess = estimate_of(conn, world_id, viewer=viewer, target=target,
                        subject="army", value=value, turn=turn)
    return (guess.low + guess.high) / 2.0


def own_regions(conn: sqlite3.Connection, viewer: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, name, terrain, kind, population, development, controller_nation_id
          FROM region WHERE owner_nation_id = ? ORDER BY name
        """,
        (viewer,),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "terrain": row["terrain"],
            "population": round(row["population"], 1),
            "development": round(row["development"], 1),
            "occupied_by": row["controller_nation_id"]
            if row["controller_nation_id"] and row["controller_nation_id"] != viewer
            else None,
        }
        for row in rows
    ]


#: How many enemies a front summary describes, and how many provinces per enemy.
#:
#: Both are hard budgets rather than guidelines, and the reason is measured rather than
#: aesthetic. A promoted power holds a dozen provinces and borders several more; listing
#: them produced thousands of characters of ``Baden-Wurttemberg: quiet`` that crowded out
#: the handful the turn was actually about, and the smaller models answered worse for it.
#: What a staff needs is where the fighting is, not an order of battle.
FRONT_ENEMIES = 3
FRONT_REGIONS = 5


@dataclass(frozen=True, slots=True)
class FrontRegion:
    """One piece of ground on a front, and who is winning it."""

    region_id: str
    name: str
    #: True when the viewer holds it and is defending; False when the enemy holds it.
    mine: bool
    #: Share of the way to changing hands, and who is pushing. ``None`` when quiet.
    pressure: float | None = None
    pressed_by_me: bool = False

    def describe(self) -> str:
        held = "you hold it" if self.mine else "they hold it"
        if self.pressure is None:
            return f"{label(self.name, self.region_id)} -- {held}, quiet"
        who = "you are" if self.pressed_by_me else "they are"
        return (
            f"{label(self.name, self.region_id)} -- {held}, "
            f"{who} {self.pressure:.0%} of the way to taking it"
        )


def fronts_of(
    conn: sqlite3.Connection,
    viewer: str,
    *,
    max_enemies: int = FRONT_ENEMIES,
    max_regions: int = FRONT_REGIONS,
) -> list[dict]:
    """Where this nation's wars actually are, summarised to a fixed budget.

    A front is the ground either side of a border with a power the viewer is at war with.
    Contested provinces come first and quiet ones fill whatever room is left, because a
    staff deciding what to do with its army needs to know where the line is moving before
    it needs a complete list of what it owns.

    Scoped like everything else in this module: only wars this nation is in, and only the
    ground it holds or borders.
    """
    enemies = [
        row["nation_b"]
        for row in conn.execute(
            "SELECT nation_b FROM relation WHERE nation_a = ? AND at_war = 1"
            " ORDER BY nation_b",
            (viewer,),
        )
    ]
    if not enemies:
        return []

    holder = {
        row["id"]: (row["holder"], row["name"])
        for row in conn.execute(
            "SELECT id, name, COALESCE(controller_nation_id, owner_nation_id) AS holder"
            "  FROM region"
        )
    }
    borders: list[tuple[str, str]] = [
        (row["region_a"], row["region_b"])
        for row in conn.execute("SELECT region_a, region_b FROM region_adjacency")
    ]
    sieges: dict[str, tuple[str, float]] = {}
    for row in conn.execute(
        "SELECT region_id, attacker_id, pressure FROM contest ORDER BY region_id, attacker_id"
    ):
        current = sieges.get(row["region_id"])
        if current is None or float(row["pressure"]) > current[1]:
            sieges[row["region_id"]] = (row["attacker_id"], float(row["pressure"]))

    names = {
        row["id"]: row["name"] for row in conn.execute("SELECT id, name FROM nation")
    }

    found: list[dict] = []
    for enemy in enemies:
        touching: dict[str, bool] = {}
        for a, b in borders:
            for near, far in ((a, b), (b, a)):
                if holder.get(near, (None, ""))[0] != viewer:
                    continue
                if holder.get(far, (None, ""))[0] != enemy:
                    continue
                touching[near] = True
                touching[far] = False
        if not touching:
            continue

        regions: list[FrontRegion] = []
        for region_id, mine in touching.items():
            siege = sieges.get(region_id)
            regions.append(
                FrontRegion(
                    region_id=region_id,
                    name=holder[region_id][1],
                    mine=mine,
                    pressure=siege[1] if siege else None,
                    pressed_by_me=bool(siege and siege[0] == viewer),
                )
            )
        # Contested first, then by how far along, then by name so the order is stable.
        regions.sort(key=lambda r: (r.pressure is None, -(r.pressure or 0.0), r.name))
        found.append(
            {
                "nation_id": enemy,
                "name": names.get(enemy, enemy),
                "regions": regions[:max_regions],
                "quiet_omitted": max(0, len(regions) - max_regions),
                "contested": sum(1 for r in regions if r.pressure is not None),
            }
        )

    # The busiest wars first: a front with fighting on it outranks one that is only a
    # border, which is the same judgement Focus Mode makes about which nations matter.
    found.sort(key=lambda f: (-f["contested"], -len(f["regions"]), f["nation_id"]))
    return found[:max_enemies]


#: Other nations' routine public works: the events a briefing summarises as a count rather
#: than listing. Written once and used both ways -- excluded from the window by
#: :func:`known_events`, counted by :func:`routine_construction` -- so the two can never
#: come to disagree about what "routine" means. Takes the viewer as its one parameter.
_ROUTINE_CONSTRUCTION = (
    "(e.kind = 'construction'"
    " AND (e.actor_nation_id IS NULL OR e.actor_nation_id <> ?))"
)


#: **How much a piece of news is worth the space it takes.** Higher sorts first.
#:
#: The feed was pure recency, and recency is the wrong axis when one nation's rail
#: extension and another's coup arrive in the same turn. `autonomous_investment` develops
#: every unattended nation every turn, so construction is the most numerous kind on the map
#: by a wide margin -- the window's cap already had to exclude *other* nations' works to
#: stop them crowding out the wars, and that exclusion is a blunt version of this.
#:
#: Four bands, and the boundaries are about what a staff must not miss rather than about
#: drama. **Three** is somebody using force or changing what a state is: a launch, a coup,
#: a declaration of war, ground changing hands. **Two** is a commitment made or broken --
#: the things that decide who fights beside whom next turn. **One** is everything else that
#: happened to somebody. **Zero** is the tick: a workshop finished, a reform passed.
#:
#: Nothing is dropped. A zero still prints, still counts against the cap, and is still
#: citable -- the ordering decides what a truncated window keeps, not what exists.
SALIENCE: dict[str, int] = {
    # Force, and the end of a government.
    "war_declared": 3, "coup": 3, "revolution": 3, "territory_transfer": 3,
    "battle": 3, "siege": 3, "peace_signed": 3, "ceasefire": 3, "disaster": 3,
    # Who will be fighting beside whom.
    "treaty_signed": 2, "treaty_broken": 2, "alliance_formed": 2, "alliance_broken": 2,
    "commitment_made": 2, "commitment_broken": 2, "casus_belli": 2, "ultimatum": 2,
    "leader_change": 2, "election": 2, "sabotage": 2, "espionage": 2,
    # Everything else that happened to somebody.
    "unrest": 1, "famine": 1, "plague": 1, "discovery": 1, "unit_disbanded": 1,
    "diplomatic_message": 1, "envoy_dispatched": 1, "commitment_fulfilled": 1,
    "trade_agreement": 1, "narrative": 1, "move": 1, "genesis": 1,
    # The tick.
    "construction": 0, "economic_reform": 0, "unit_raised": 0,
    "order_issued": 0, "order_rejected": 0,
}

#: What an unrecognised kind is worth. **One, not zero**: a kind nobody has ranked is a
#: kind somebody added, and burying it under a completed workshop is the failure mode a
#: default of zero guarantees the day the enum grows.
DEFAULT_SALIENCE = 1

#: **Shooting through a truce is not ordinary news.** `treaty_broken` sits at 2 with the
#: rest of the diplomatic traffic, which was right when the only thing that could be broken
#: was a pact nobody was shooting over. An armistice is different in kind: the guns had
#: stopped, and somebody started them again -- and the same is true of a broken peace. Both
#: are `treaty_broken` events, and both belong with the fighting rather than under six
#: completed workshops. Matching the engine's own words, for the reason `_GRAVE_WORDS` gives.
_BROKEN_TRUCE = ("through a ceasefire", "ceasefire it had agreed", "broke the peace")

#: A launch is a `narrative` event by kind -- :mod:`atlas.engine.nuclear` writes it there
#: because the event enum is closed and adding to it is save-invalidating -- so the kind
#: alone cannot rank it. These phrases are what the engine itself writes; matching its own
#: words is not clever, it is the only handle the schema leaves.
_GRAVE_WORDS = ("nuclear", "warhead", "capital fell", "capital was taken", "capital has fallen")


def salience_of(event: dict) -> int:
    """How high this event sorts. See :data:`SALIENCE`.

    Takes the *rendered* event dict rather than a row, so the briefing and the window rank
    identically off the same shape.
    """
    rank = SALIENCE.get(str(event.get("kind") or ""), DEFAULT_SALIENCE)
    text = str(event.get("text") or "").lower()
    if any(word in text for word in _GRAVE_WORDS) or any(
        phrase in text for phrase in _BROKEN_TRUCE
    ):
        return max(rank, 3)
    return rank


def by_salience(events: list[dict]) -> list[dict]:
    """The same events, gravest first, ties broken by recency then by id.

    A stable, total order: two events of equal weight in the same turn keep the order they
    were written in, so a rewind and replay renders the identical feed.
    """
    return sorted(
        events, key=lambda e: (-salience_of(e), -int(e["turn"]), -int(e["id"])))


def _ranked_window(
    conn: sqlite3.Connection,
    viewer: str,
    since_turn: int,
    limit: int,
    clause: str,
    routine: tuple,
    until_turn: int | None = None,
) -> list[dict]:
    """The ``limit`` gravest events this viewer knows of, returned chronologically.

    **Ranked in Python rather than in SQL**, deliberately: :func:`salience_of` reads the
    event's *text* as well as its kind -- a nuclear launch is a ``narrative`` row, because
    the event enum is closed -- and a CASE expression that had to duplicate that would be a
    second copy of the ranking to keep in step with the first.

    The pre-filter is a generous multiple of the cap rather than the whole log, so a world
    with fifty thousand events does not build fifty thousand dicts to print twenty-five.
    A grave event older than that many rows is older than the briefing window means to
    reach anyway.
    """
    wide = _rows(conn, viewer, since_turn, max(limit * 8, 200), clause, routine, until_turn)
    kept = by_salience(wide)[:limit]
    return sorted(kept, key=lambda e: (int(e["turn"]), int(e["id"])))


def known_events(
    conn: sqlite3.Connection,
    viewer: str,
    *,
    since_turn: int = 0,
    until_turn: int | None = None,
    limit: int = 60,
    without_routine_construction: bool = False,
    rank_by_salience: bool = False,
) -> list[dict]:
    """Events this nation witnessed.

    Public events are visible to everyone without a row per nation; anything else needs
    an explicit ``event_visibility`` entry. A nation's own actions are always visible to
    it, which is why the actor clause is there.

    ``without_routine_construction`` drops *other* nations' public works from the window
    itself. **The briefing used to drop them after the cap, which does not work**:
    `production.autonomous_investment` develops every unattended nation every turn, those
    events are written late and so hold the high ids, and the window is ordered by recency.
    So the cap filled with construction, the thinning deleted it, and the briefing showed
    what was left — while the wars and treaties sat outside the window entirely. Measured on
    the recorded runs, **every** briefing filled its cap and none showed a full one: 4.1
    events of an allowed 25 on gathering-storm, with 1314 displacements of in-run news
    including *"Germany repudiates the Rhineland demilitarisation provisions of Locarno"*
    falling out of Austria's briefing. Excluding them here spends the cap on what will
    actually be shown.

    ``until_turn`` closes the window at the top, for a caller asking about a stretch that is
    no longer the recent past. The cap is spent newest-first, so without an upper bound a
    request for an old stretch of a busy world returns the events that came *after* it --
    which is how a five-turn digest came to be written over an empty list.
    """
    clause = f" AND NOT {_ROUTINE_CONSTRUCTION}" if without_routine_construction else ""
    routine = (viewer,) if without_routine_construction else ()
    if rank_by_salience:
        # **The cap is spent by weight, not by clock.** Ordering by recency alone meant a
        # turn in which six nations each finished a workshop could push a coup out of the
        # window entirely -- the events exist, they are citable, and the staff never sees
        # them. Recency still breaks ties, so within a band the feed is the newest news.
        return _ranked_window(conn, viewer, since_turn, limit, clause, routine, until_turn)
    return list(reversed(_rows(conn, viewer, since_turn, limit, clause, routine, until_turn)))


def _rows(
    conn: sqlite3.Connection,
    viewer: str,
    since_turn: int,
    limit: int,
    clause: str,
    routine: tuple,
    until_turn: int | None = None,
) -> list[dict]:
    """The window, newest first. One query, two callers, no second copy of the visibility
    predicate -- which is the part of this that must never differ between them."""
    bounded = " AND e.turn <= ?" if until_turn is not None else ""
    upper = (until_turn,) if until_turn is not None else ()
    rows = conn.execute(
        f"""
        SELECT e.id, e.turn, e.date, e.kind, e.text, e.actor_nation_id, e.visibility
          FROM event e
         WHERE e.world_id = (SELECT world_id FROM nation WHERE id = ?)
           AND e.turn >= ?{bounded}
           AND (
                e.visibility = 'public'
             OR e.actor_nation_id = ?
             OR EXISTS (SELECT 1 FROM event_visibility v
                         WHERE v.event_id = e.id AND v.nation_id = ?)
           ){clause}
         ORDER BY e.turn DESC, e.id DESC
         LIMIT ?
        """,
        (viewer, since_turn, *upper, viewer, viewer, *routine, limit),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "turn": row["turn"],
            "date": row["date"],
            "kind": row["kind"],
            "actor": row["actor_nation_id"],
            "text": row["text"],
            # The event's own scope, so a nation citing it as the cause of a relation change
            # can match that change's visibility to it (rule INFO-002). Without it the model
            # cites an event it can see but cannot tell how widely, and guesses wider.
            "visibility": row["visibility"],
        }
        for row in rows
    ]


def routine_construction(
    conn: sqlite3.Connection, viewer: str, *, since_turn: int = 0
) -> int:
    """How many of other nations' public works the window left out, over the span it covers.

    Counted rather than carried, because the footnote only ever needed the number and the
    events themselves were what crowded the briefing out. ``since_turn`` is the oldest turn
    the window reached, so the count describes the period the briefing describes rather than
    the whole game.
    """
    return int(conn.execute(
        f"""
        SELECT COUNT(*)
          FROM event e
         WHERE e.world_id = (SELECT world_id FROM nation WHERE id = ?)
           AND e.turn >= ?
           AND (
                e.visibility = 'public'
             OR e.actor_nation_id = ?
             OR EXISTS (SELECT 1 FROM event_visibility v
                         WHERE v.event_id = e.id AND v.nation_id = ?)
           )
           AND {_ROUTINE_CONSTRUCTION}
        """,
        (viewer, since_turn, viewer, viewer, viewer),
    ).fetchone()[0])


def relations_of(conn: sqlite3.Connection, viewer: str, other: str) -> dict:
    """The bilateral picture between two nations, from one side's perspective."""
    row = conn.execute(
        """
        SELECT score, treaty_status, at_war FROM relation
         WHERE nation_a = ? AND nation_b = ?
        """,
        (viewer, other),
    ).fetchone()
    if row is None:
        return {"score": 0.0, "treaty_status": "none", "at_war": False}
    return {
        "score": round(row["score"], 1),
        "treaty_status": row["treaty_status"],
        "at_war": bool(row["at_war"]),
    }


def standing_commitments(conn: sqlite3.Connection, viewer: str) -> list[dict]:
    """Commitments this nation is a party to. Others' private pacts are not visible."""
    rows = conn.execute(
        """
        SELECT c.id, c.kind, c.status, c.condition_text, c.created_turn,
               GROUP_CONCAT(cn2.nation_id) AS parties
          FROM commitment c
          JOIN commitment_nation cn  ON cn.commitment_id = c.id AND cn.nation_id = ?
          JOIN commitment_nation cn2 ON cn2.commitment_id = c.id
         WHERE c.status IN ('pending', 'active')
         GROUP BY c.id
         ORDER BY c.created_turn DESC
        """,
        (viewer,),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "kind": row["kind"],
            "status": row["status"],
            "condition": row["condition_text"],
            "since_turn": row["created_turn"],
            "parties": (row["parties"] or "").split(","),
        }
        for row in rows
    ]


def secret_intent(conn: sqlite3.Connection, nation_id: str) -> Tainted[str]:
    """A nation's private intentions, tainted so they cannot reach anyone else.

    Kept as an example of the pattern as much as for its own sake: anything genuinely
    hidden is wrapped at the point it leaves the database, and :func:`atlas.engine.
    tainted.render` refuses to serialise it for the wrong viewer.
    """
    row = conn.execute(
        "SELECT standing_goals_json FROM nation WHERE id = ?", (nation_id,)
    ).fetchone()
    goals = json_list(row["standing_goals_json"]) if row else []
    return Tainted(
        value="; ".join(goals),
        owners=frozenset({nation_id}),
        label=f"{nation_id}'s private objectives",
    )
