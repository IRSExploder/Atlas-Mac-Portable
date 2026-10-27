"""What it costs to hold ground that is not yours.

The peace layer made annexation possible; this makes it *expensive*. A province whose holder
is not its home nation is restive -- occupied enemy core during a war, or foreign ground
kept at a peace -- and restive ground drags on the state that holds it. Without this,
conquering the whole map is free: a power could annex a continent at the peace table and
carry it at no cost, which turns a grand-strategy map into a land-grab.

The drag scales with **how much** foreign core a nation holds and how **developed** it is --
a densely populated industrial province resents occupation more loudly than an empty one --
and is eased two ways: by the holder's own **stability** (a settled, confident state garrisons
and governs restive ground better than a shaky one) and by the **troops it stations there**
(an army sitting on a restive province holds it down directly -- the guns-on-the-ground half
of occupation). So the cost is real but answerable: a conqueror who invests in stability, or
parks armies on what he took, can hold more; one who overreaches while shaky and thin on the
ground watches the occupation eat the very stability it needs to hold it. Garrison is a
finite lever -- you cannot station a division on every province of a continent -- so the
tension is where to put the troops you have. Imperial overstretch, priced.

Engine-authored every turn, like the budget: the holder never proposes this, the engine
assesses the map and writes the drag itself, citing one event so the whole thing rewinds.
``home_nation_id`` never changes here -- unrest is a standing condition of held foreign
ground, not something a turn resolves.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..sim.resolve import uncertain
from ..state.apply import apply_batch, record_event
from ..state.diff import Diff, DiffBatch
from ..state.models import DiffOp, EventKind, EventOrigin, Role, Visibility
from .bounds import absorbable
from .leaders import modifier as leader_modifier
from .pacing import compounded, over, span_of

__all__ = [
    "PartisanReport",
    "UnrestReport",
    "assess_partisans",
    "assess_unrest",
    "held_foreign_provinces",
    "nation_unrest",
]

#: A flat cost per restive province, before development weights it. Even an empty province
#: under occupation needs holding down.
_UNREST_PER_PROVINCE = 0.8

#: How much a province's development adds to the load. Developed ground has the people,
#: the presses and the railways a resistance runs on.
_UNREST_FROM_DEVELOPMENT = 0.25

#: How much each point of the holder's unit strength stationed in a restive province eases
#: that province's load. Land divisions run ~4-7 strength, so a single division parked on a
#: province takes roughly a whole province's base load off -- one army holds one province
#: down. A province's load is floored at zero: a garrison can neutralise unrest but never
#: turn occupied ground into a stability *bonus*.
_GARRISON_RELIEF_PER_STRENGTH = 0.3

#: How much a nation's stability can *mitigate* its unrest, at most. A settled state
#: garrisons and governs restive ground far better than a shaky one -- but never perfectly:
#: even a stable empire pays something for every occupied province, because a few years is
#: not long enough to make a conquered people forget whose flag it was. Calibrated so a
#: great power holding a continent (~15 provinces) bleeds a couple of stability a turn it
#: must actively make back, and one that overreaches while shaky bleeds faster.
_MITIGATION_MAX = 0.6

#: Share of the unmitigated load taken off stability each turn.
_DRAG_RATE = 0.2

#: No single turn's unrest may cost more stability than this, so overstretch is a bleed a
#: player can react to rather than a cliff that zeroes a conqueror in one quarter.
_MAX_DRAG = 5.0

#: How a province's residual unrest (after garrison and stability) becomes a per-turn chance
#: that partisans rise and wrest it back. A restive, ungarrisoned, developed province held by
#: a shaky state faces a real uprising risk; a garrisoned or well-governed one faces almost
#: none. Tuned low: an uprising is a bleed, not a certainty, so holding foreign ground is a
#: gamble that gets worse the longer and worse you hold it -- not an instant reversal.
_PARTISAN_CHANCE_PER_LOAD = 0.05

#: No single province may exceed this uprising chance in a turn, so even the worst-held ground
#: is a mounting risk the holder can answer (garrison it, stabilise, or hand it back), never a
#: coin flip that erases a conquest without warning.
_PARTISAN_MAX_CHANCE = 0.25


@dataclass(slots=True)
class UnrestReport:
    assessed: int = 0
    #: Nations that took a stability hit from restive ground this turn, worst first.
    restive: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PartisanReport:
    assessed: int = 0
    #: Provinces that rose against their occupier this turn and reverted to home control.
    flipped: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def nation_unrest(conn: sqlite3.Connection, world_id: str, nation_id: str) -> float:
    """The unrest load a nation carries from foreign core it holds, before stability eases it.

    Each restive province contributes ``_UNREST_PER_PROVINCE + _UNREST_FROM_DEVELOPMENT *
    development``, less the relief of the holder's own troops stationed there, floored at zero.
    ``0`` for a nation sitting on nothing but its own ground -- which is every nation at the
    start of a world, since home is seeded to the owner.
    """
    row = conn.execute(
        """
        SELECT COALESCE(SUM(
                   MAX(0.0, (? + ? * r.development) - ? * COALESCE(g.strength, 0))
               ), 0) AS load
          FROM region r
          LEFT JOIN (
                   SELECT region_id, SUM(strength) AS strength
                     FROM unit
                    WHERE world_id = ? AND nation_id = ?
                    GROUP BY region_id
               ) g ON g.region_id = r.id
         WHERE r.world_id = ?
           AND r.kind <> 'ocean'
           AND r.home_nation_id IS NOT NULL
           AND COALESCE(r.controller_nation_id, r.owner_nation_id) = ?
           AND r.home_nation_id <> ?
        """,
        (_UNREST_PER_PROVINCE, _UNREST_FROM_DEVELOPMENT, _GARRISON_RELIEF_PER_STRENGTH,
         world_id, nation_id, world_id, nation_id, nation_id),
    ).fetchone()
    held = float(row["load"]) if row else 0.0

    # **And what its colonies are doing.** A dependency's home is NULL, so the query above
    # cannot see one -- which is why an empire of a hundred and forty-five territories cost
    # its holder nothing at all until `atlas.engine.decolonisation` existed. Added here,
    # in the same unit, so an empire in trouble and an occupation in trouble cost the same
    # kind of thing and a holder cannot be in one kind of trouble twice.
    from .decolonisation import unrest_load

    return held + unrest_load(conn, world_id, nation_id)


def held_foreign_provinces(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> list[dict]:
    """The restive provinces a nation holds, with what it would take to keep them.

    One row per foreign-core province the nation controls, each carrying the province's
    residual unrest (after the garrison it currently has) and whether that garrison is
    holding it down. Read by the briefing views so the decision-maker can *see* the drag and
    the revolt risk and answer it -- the unrest layer is invisible until stability craters
    unless the person deciding is shown where it is bleeding. Ordered worst-held first.
    """
    return [
        {
            "region_id": str(r["region_id"]),
            "name": str(r["region_name"]),
            "home_nation_id": str(r["home_id"]),
            "residual": float(r["residual"]),
            "garrison": float(r["garrison"]),
            "garrisoned": float(r["residual"]) <= 0.0,
        }
        for r in conn.execute(
            """
            SELECT r.id AS region_id, r.name AS region_name, r.home_nation_id AS home_id,
                   COALESCE(g.strength, 0) AS garrison,
                   MAX(0.0, (? + ? * r.development) - ? * COALESCE(g.strength, 0)) AS residual
              FROM region r
              LEFT JOIN (
                       SELECT region_id, nation_id, SUM(strength) AS strength
                         FROM unit WHERE world_id = ?
                        GROUP BY region_id, nation_id
                   ) g ON g.region_id = r.id AND g.nation_id = ?
             WHERE r.world_id = ?
               AND r.kind <> 'ocean'
               AND r.home_nation_id IS NOT NULL
               AND COALESCE(r.controller_nation_id, r.owner_nation_id) = ?
               AND r.home_nation_id <> ?
             ORDER BY residual DESC, r.id
            """,
            (_UNREST_PER_PROVINCE, _UNREST_FROM_DEVELOPMENT, _GARRISON_RELIEF_PER_STRENGTH,
             world_id, nation_id, world_id, nation_id, nation_id),
        )
    ]


def assess_unrest(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str, days: int = 30
) -> UnrestReport:
    """Charge every holder of foreign core for the trouble it is sitting on.

    The drag is a *flow* -- a year of occupation costs twelve months of stability, and
    the per-turn ceiling scales with it or a long turn would be cheaper than the twelve
    short ones it replaces. See :mod:`atlas.engine.pacing`.
    """
    span = span_of(days)
    report = UnrestReport()
    nations = conn.execute(
        "SELECT id, name, stability FROM nation "
        "WHERE world_id = ? AND is_alive = 1 ORDER BY id",
        (world_id,),
    ).fetchall()
    if not nations:
        return report

    charged: list[tuple[str, str, float]] = []
    for nation in nations:
        report.assessed += 1
        load = nation_unrest(conn, world_id, nation["id"])
        if load <= 0:
            continue
        stability = float(nation["stability"])
        # A settled state mitigates the unrest, but never all of it -- some always leaks.
        mitigation = _MITIGATION_MAX * max(0.0, min(stability, 100.0)) / 100.0
        # **And so does the government's own disposition.** A hard one garrisons occupied
        # ground more effectively and a populist one less, which is the standing cost of
        # who is in charge rather than of what they did this turn. It scales the load
        # before mitigation, so it compounds with stability rather than replacing it.
        load *= leader_modifier(conn, str(nation["id"]), "unrest")
        # **Charged against what there is.** ECON-003 holds stability inside 0-100 and
        # names no diff, so it condemns the **whole batch** -- and this batch charges every
        # occupier in the world. An occupier down to 0.2 taking a 0.5 drag therefore cost
        # every *other* occupier its drag as well, and the only thing that happened was a
        # problem line. Reproduced. Exactly the failure the breach path had, in a place
        # where it is worse because the batch is world-wide.
        drag = -absorbable(conn, "nation", str(nation["id"]), "stability", -min(
            over(_MAX_DRAG, span),
            round(over(load * _DRAG_RATE * (1.0 - mitigation), span), 2),
        ))
        if drag >= 0.05:
            charged.append((str(nation["id"]), str(nation["name"]), drag))

    if not charged:
        return report

    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.NARRATIVE, origin=EventOrigin.ENGINE,
        text="Occupied provinces chafe under the flags now flying over them.",
        visibility=Visibility.PUBLIC,
    )
    diffs = [
        Diff("nation", nation_id, "stability", DiffOp.ADJUST, -drag,
             reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
        for nation_id, _name, drag in charged
    ]
    outcome = apply_batch(
        conn,
        DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                  proposer_model=None, diffs=tuple(diffs)),
    )
    if not outcome.applied:
        report.problems.append(f"unrest could not be applied: {outcome.reason}")
        return report
    report.restive = [name for _id, name, _drag in sorted(
        charged, key=lambda c: c[2], reverse=True)]
    return report


def _partisan_key(region_id: str, round_index: int) -> str:
    """The RNG coordinates of one province's rising, in one round of a turn.

    **The round is part of the key, because this is a draw and it runs once per round.**
    The stream is keyed on ``(world, turn, call_key)`` so that no other call can shift
    anybody's luck -- and the price of that is that two calls with the same key on the same
    turn *are the same call*. Without the round, a year-long jump rolled the same number
    twelve times and a province rose about as often in a year as in a month (decision 104
    is the same failure in succession). Round 0 carries no suffix, so a one-round turn --
    every monthly turn -- draws exactly what it always drew. Spelled the way
    ``resolution._stream_key`` spells it for battles.
    """
    return f"partisan:{region_id}" + (f"#r{round_index}" if round_index else "")


def assess_partisans(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str, seed: int,
    days: int = 30, round_index: int = 0,
) -> PartisanReport:
    """Let badly-held ground rise against its occupier and revert to its home nation.

    Where :func:`assess_unrest` turns held foreign core into a *stability* cost, this turns
    it into a *map* cost. Each restive province the occupier holds carries a residual load --
    its base unrest, less the troops stationed on it, less what a stable state suppresses --
    and that residual is a per-turn chance the population rises and takes the province back.
    A garrisoned or well-governed province is almost safe; a developed one held thin and shaky
    is a standing risk. When partisans win, control (not ownership) reverts to the home nation,
    exactly as an occupation is control taken by force -- ownership still changes only at a
    peace. One seeded roll per province per round (``round_index``, see :func:`_partisan_key`),
    so the same world replays identically and it rewinds as ordinary region diffs. Only living home nations rise: a people whose state was wholly
    conquered has no banner left to rally to -- reviving the dead is conquest's business, not
    a partisan's.
    """
    report = PartisanReport()
    rows = conn.execute(
        """
        SELECT r.id AS region_id, r.name AS region_name, r.home_nation_id AS home_id,
               COALESCE(r.controller_nation_id, r.owner_nation_id) AS holder_id,
               n.stability AS stability,
               MAX(0.0, (? + ? * r.development) - ? * COALESCE(g.strength, 0)) AS residual
          FROM region r
          JOIN nation n ON n.id = COALESCE(r.controller_nation_id, r.owner_nation_id)
                       AND n.world_id = r.world_id
          JOIN nation home ON home.id = r.home_nation_id AND home.world_id = r.world_id
                          AND home.is_alive = 1
          LEFT JOIN (
                   SELECT region_id, nation_id, SUM(strength) AS strength
                     FROM unit WHERE world_id = ?
                    GROUP BY region_id, nation_id
               ) g ON g.region_id = r.id
                  AND g.nation_id = COALESCE(r.controller_nation_id, r.owner_nation_id)
         WHERE r.world_id = ?
           AND r.kind <> 'ocean'
           AND r.home_nation_id IS NOT NULL
           AND r.home_nation_id <> COALESCE(r.controller_nation_id, r.owner_nation_id)
         ORDER BY r.id
        """,
        (_UNREST_PER_PROVINCE, _UNREST_FROM_DEVELOPMENT, _GARRISON_RELIEF_PER_STRENGTH,
         world_id, world_id),
    ).fetchall()

    risen: list[tuple[str, str, str]] = []  # (region_id, region_name, home_id)
    for row in rows:
        report.assessed += 1
        residual = float(row["residual"])
        if residual <= 0:
            continue
        stability = max(0.0, min(float(row["stability"]), 100.0))
        mitigation = _MITIGATION_MAX * stability / 100.0
        # A *probability*, so a longer turn compounds it rather than multiplying: twelve
        # months at the 0.25 ceiling is a 0.97 chance of a rising, not a meaningless 3.0.
        chance = compounded(
            min(
                _PARTISAN_MAX_CHANCE,
                residual * _PARTISAN_CHANCE_PER_LOAD * (1.0 - mitigation),
            ),
            span_of(days),
        )
        if chance <= 0:
            continue
        if uncertain(seed, world_id, turn, _partisan_key(row["region_id"], round_index),
                     chance):
            risen.append((str(row["region_id"]), str(row["region_name"]), str(row["home_id"])))

    if not risen:
        return report

    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.NARRATIVE, origin=EventOrigin.ENGINE,
        text="Partisans rose in occupied territory and drove the occupier's flag back down.",
        visibility=Visibility.PUBLIC,
    )
    diffs = [
        Diff("region", region_id, "controller_nation_id", DiffOp.SET, home_id,
             reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
        for region_id, _name, home_id in risen
    ]
    outcome = apply_batch(
        conn,
        DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                  proposer_model=None, diffs=tuple(diffs)),
    )
    if not outcome.applied:
        report.problems.append(f"partisan uprisings could not be applied: {outcome.reason}")
        return report
    report.flipped = [name for _id, name, _home in risen]
    return report
