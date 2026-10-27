"""A colony wanting to stop being one, and the two ways that ends.

**An empire was free ground with no politics in it.** The unrest layer charges a holder for
foreign *core* -- `held_foreign_provinces` requires ``home_nation_id IS NOT NULL`` -- and a
dependency's home is nobody, deliberately, because there is no nation for British India and
inventing one would be inventing a state. So the ninety regions Britain holds in 1936 and the
thirty-three France holds cost their holders exactly nothing and could never want anything.
That is the largest silent hole the sovereignty map opened, and this closes it.

**Momentum, not a die roll per turn.** A movement accumulates: it grows every turn from how
many people the territory has and how far it is from the metropole, is slowed by the
garrison sitting on it, and is spent by nothing except independence itself. That shape is
doing real work. A per-turn chance would make decolonisation a surprise, and it was not a
surprise to anybody -- it was visible for decades and arrived anyway. Momentum is also
*legible*: a holder can read the number, see it rising, and decide.

**What drives it was wrong, and PT39 proved it live.** The first version drove momentum off
*development*, and development is the one colonial statistic that runs backwards: it made
Puerto Rico, Guam and American Samoa the vanguard of decolonisation while India, Nigeria
and Indochina sat still for twenty-two turns. Population drives it now, development is a
small negative, and the 1936 ordering that follows is India, Indonesia, Nigeria, Indochina,
Burma -- with the American Pacific territories ranked 153rd to 156th of 164. See
:data:`MOMENTUM_PER_POPULATION` for the argument and
:func:`_colonial_population` for why the seeded ``region.population`` column could not be
used for it.

**Two ends, and the engine only builds one of them.**

*Negotiated decolonisation* is here in full: the holder agrees, the ground becomes a
sovereign state, and the transition is a set of diffs like anything else. It costs the holder
the province and buys it the regard of everyone watching.

*The other end is a war of independence*, and it is deliberately **not** modelled as a
separate mechanic. A movement past the threshold makes the ground restive -- it drags the
holder's stability through the existing unrest path -- and if the holder will not let go, that
drag is the whole cost. Building a rebellion that raises its own armies would need a nation to
raise them for, which is the thing this module exists not to invent until the moment of
independence. Flagged rather than hidden.

**Every number here is a balance call and every one is named.** On the 1936 map an
ungarrisoned dependency reaches a movement between turn 17 (India) and turn 136 (a Pacific
atoll), and independence between turn 47 and turn 399; a garrison of any weight pushes both
out, and a heavy one holds the ground indefinitely.

**A staggered wave, which is the second thing PT40 had to fix.** Getting the ordering right
still left the whole table arriving at once: 158 of 164 dependencies stirred and 21 reached
independence inside 40 turns, because a base rate of 0.9 that everybody got equally
compressed the spread between India and an atoll to barely threefold. Independence now
lands across three hundred turns instead of forty, and **8 of 164 complete inside a hundred
turns** where before it was 158 -- so an empire loses its largest possessions during a long
game and keeps the rest, rather than dissolving entirely in three years.

**Still faster than history, and that is the remaining judgement.** India was independent in
1947, which is 132 monthly turns from 1936, not 47. Going the rest of the way would put the
first independence beyond most games, which is a decision about what a player should get to
see rather than a fact about the century. :data:`MOMENTUM_PER_TURN` and
:data:`MOMENTUM_PER_POPULATION` are the dials, in that order.

**Deterministic through the pipeline.** The one random element -- whether a given turn's
growth is a good year or a bad one for the movement -- draws from
:func:`atlas.sim.rng.derive` with a ``call_key`` naming the *region*, never an ordinal, so
adding a colony elsewhere on the map cannot shift this one's history.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field

from ..sim.rng import derive
from ..state.apply import apply_batch, record_event, unrecord_events
from ..state.diff import Diff, DiffBatch
from ..state.models import DiffOp, EventKind, EventOrigin, Role, Visibility

__all__ = [
    "GARRISON_RELIEF",
    "INDEPENDENCE_THRESHOLD",
    "MOMENTUM_IF_SEPARATED",
    "MOMENTUM_PER_DEVELOPMENT",
    "MOMENTUM_PER_POPULATION",
    "MOMENTUM_PER_TURN",
    "MOVEMENT_THRESHOLD",
    "SMALL_TERRITORY_POPULATION",
    "DecolonisationReport",
    "assess_independence",
    "decolonise",
    "dependencies_of",
    "movements_of",
    "restive_dependencies",
]

#: **Balance number, and the pacing dial.** Base momentum a dependency gains per turn,
#: before everything else. A turn is a month by default, so this is per month.
#:
#: **Small on purpose, because it is the term everybody gets equally.** A large base rate
#: is a floor under the whole table, and a floor compresses it: at 0.9 the spread between
#: India and a Pacific atoll was barely threefold, so once the first colony moved they all
#: moved, and PT40 saw 158 of 164 dependencies stir and 21 reach independence inside 40
#: monthly turns. An empire is not supposed to come apart in three years.
#:
#: At 0.12 the base is a background hum and :data:`MOMENTUM_PER_POPULATION` does the
#: separating, which widens the spread to roughly eightfold and turns the flood into a
#: staggered wave: on the 1936 map independence now arrives between turn 47 (India) and
#: turn 399 (the smallest territories), with **8 of 164 inside a hundred turns** where
#: before it was 158.
MOMENTUM_PER_TURN = 0.12

#: **Balance number, and a tunable design default.** Momentum per unit of *population
#: weight* -- see :func:`_population_weight` for what that unit is.
#:
#: **Population is the primary driver, and this is the decision to argue with.** The first
#: version drove momentum off the province's *development*, on the reasoning that a rich,
#: literate, connected colony organises faster than a poor one. PT39 ran that live for 22
#: turns and it produced the century backwards: every crossing into a movement, in strict
#: order, was American at development 4.6, then Japanese at 1.7, then Australian at 1.1,
#: then Dutch at 0.8 -- Puerto Rico, Guam and American Samoa stirring while India, Nigeria
#: and Indochina did nothing at all. Britain held 72 of the map's 164 dependencies and
#: France 32; not one of them stirred in 22 turns, because British dependencies seed at a
#: median development of 0.33 and would have taken until roughly turn 69 to reach
#: independence.
#:
#: Large populations sustain independence movements. That is the direction the twentieth
#: century actually ran in: India, Indonesia, Indochina, Nigeria -- the populous colonies
#: went first and the small dependencies stayed dependencies, several of them to this day.
#:
#: If a run shows empires dissolving too fast, this is the dial before
#: :data:`MOMENTUM_PER_TURN`.
MOMENTUM_PER_POPULATION = 0.5

#: **Balance number, and a tunable design default.** Momentum per point of development --
#: **negative, and deliberately small.**
#:
#: Development is demoted rather than removed. A wealthier, better-integrated dependency is
#: marginally *less* restive: the metropole has more to offer it and more invested in it,
#: and the local elite has more to lose. But it is kept small enough that it cannot order
#: the table on its own in either direction, which was the whole failure of the first
#: version. Across the whole 1936 spread of colonial development (0.3 to 4.6) it is worth
#: under a tenth of a point of momentum per turn, against the one-and-a-quarter points that
#: separate India from an atoll on population.
MOMENTUM_PER_DEVELOPMENT = -0.02

#: **Balance number, and a tunable design default.** Momentum for a dependency with no land
#: border onto its holder's own home soil.
#:
#: Distance and difficulty of control: ground the metropole cannot walk to is ground it
#: cannot reinforce quickly, police cheaply, or settle. It is a small term because in 1936
#: it separates almost nothing -- 162 of the map's 164 dependencies are overseas, and the
#: two that are not are Japanese Korea and South African Namibia, which is historically
#: exactly right. It earns its place in the eras and empires where land is held by land.
#:
#: Scaled down with the rest of the table when the pace was slowed, so it keeps the same
#: weight relative to population that it was chosen to have.
MOMENTUM_IF_SEPARATED = 0.10

#: What a dependency's population is taken to be when the demography table has no entry for
#: it, in millions. **The absence is the signal**: the table carries the world's populous
#: countries, so a territory missing from it is small by construction -- Anguilla, Aruba,
#: Norfolk Island. Set low deliberately, and low enough that it is the floor rather than a
#: middle value that would flatten the ordering it exists to produce.
SMALL_TERRITORY_POPULATION = 0.3

#: **Balance number.** Momentum removed per point of garrison strength standing on it. It
#: **slows and never reverses** -- a garrison buys years, not consent, and a mechanic where
#: enough troops make a colony want to stay is a mechanic that gets the century backwards.
#:
#: Scaled down with the rest of the table when the pace was slowed, and the ratio was chosen
#: against the two cases that matter rather than kept proportional: a single battalion must
#: slow a colony without stopping it, and the garrisons Italy actually had on Eritrea and
#: Somaliland in 1936 -- 5.0 and 10.1 -- must still hold them indefinitely. Both hold, and
#: the same six dependencies are pinned as before.
GARRISON_RELIEF = 0.06

#: **Balance number.** Where it stops being grumbling and becomes a movement: an event, a
#: line in the holder's briefing, and the point at which the ground starts dragging on the
#: holder's stability through the ordinary unrest path.
MOVEMENT_THRESHOLD = 25.0

#: **Balance number.** Where the holder is out of time: from here, keeping it costs more
#: every turn and a negotiated exit is the only ending that is not a defeat. Roughly six to
#: eight years of turns for an ungarrisoned developed colony, two to three times that for a
#: heavily held one.
INDEPENDENCE_THRESHOLD = 70.0

#: How much a movement past the threshold drags on the holder, per turn, as unrest load.
#: Deliberately expressed in the same unit the occupation drag uses, so an empire in trouble
#: and an occupation in trouble cost the same kind of thing.
UNREST_PER_MOVEMENT = 1.2

#: The good-year/bad-year band. ±25%, so a colony's history is not a straight line and two
#: identical colonies do not arrive on the same turn -- and small enough that the outcome is
#: never a surprise, which is the whole point of momentum over a die roll.
_VARIANCE = 0.25


@dataclass(slots=True)
class DecolonisationReport:
    assessed: int = 0
    #: One line per territory that crossed into being a movement this turn.
    stirred: list[str] = field(default_factory=list)
    #: ...and per territory that reached the point where holding it is only a delay.
    ripe: list[str] = field(default_factory=list)
    freed: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def dependencies_of(
    conn: sqlite3.Connection, world_id: str, holder_id: str | None = None
) -> list[dict]:
    """Ground held by a power that is nobody's core. One row per dependency.

    **The definition is exactly the one the sovereignty map writes**: ``home_nation_id IS
    NULL`` on land somebody holds. A colony, a mandate, a trust territory -- everything the
    dependency mechanism produces and nothing else. An occupied province has a home and is
    the unrest layer's business, not this one.
    """
    sql = """
        SELECT r.id AS region_id, r.name AS name, r.development AS development,
               COALESCE(r.controller_nation_id, r.owner_nation_id) AS holder,
               COALESCE(g.strength, 0) AS garrison
          FROM region r
          LEFT JOIN (SELECT region_id, nation_id, SUM(strength) AS strength
                       FROM unit WHERE world_id = ? GROUP BY region_id, nation_id) g
            ON g.region_id = r.id
           AND g.nation_id = COALESCE(r.controller_nation_id, r.owner_nation_id)
         WHERE r.world_id = ? AND r.kind <> 'ocean' AND r.home_nation_id IS NULL
           AND COALESCE(r.controller_nation_id, r.owner_nation_id) IS NOT NULL
    """
    args: list = [world_id, world_id]
    if holder_id is not None:
        sql += " AND COALESCE(r.controller_nation_id, r.owner_nation_id) = ?"
        args.append(holder_id)
    return [
        {
            "region_id": str(row["region_id"]),
            "name": str(row["name"]),
            "holder": str(row["holder"]),
            "development": float(row["development"]),
            "garrison": float(row["garrison"]),
        }
        for row in conn.execute(sql + " ORDER BY r.id", args)
    ]


def _population_weight(millions: float) -> float:
    """Population as momentum sees it: ``log10(1 + millions)``.

    **Logarithmic, because the effect is not linear.** India had roughly twenty times the
    people of the Gold Coast and did not want independence twenty times as much; the step
    from one million to ten is worth far more than the step from three hundred to three
    hundred and fifty. Linear weighting would also let one giant swamp the table exactly
    the way development did, which is the failure being fixed.

    The 1936 spread it produces: India 2.55, Indonesia 1.79, Nigeria 1.32, Philippines
    1.20, Kenya 0.70, and a small island floor of 0.11.
    """
    return math.log10(1.0 + max(0.0, millions))


def _colonial_population(conn: sqlite3.Connection, world_id: str) -> dict[str, float]:
    """``region -> the population of the territory itself``, in millions.

    **Not what ``region.population`` says, and for two separate reasons.**

    It was fiction: the seed shared a holder's people out by development weight, so British
    India read 0.85 and American Samoa read 24.52 on the strength of belonging to the United
    States. That is now fixed at the seed (:func:`atlas.scenarios.demography.assign_population`)
    and per-territory population is real.

    It is still not *comparable*. Fixing it had to leave every nation's total untouched --
    ``budget`` caps manpower at twice the population a nation holds, so any drift there is a
    combat rebalance of the whole map -- which means each holder's territories are scaled to
    that holder's existing pool rather than to the world. Britain's colonies are scaled by
    about 0.21 and America's by about 1.93, so Puerto Rico reads 3.28 against Ceylon's 1.26
    although Ceylon had three times the people. A movement compares colonies across empires,
    so reading that column would put the cross-empire half of the old bug straight back.

    So this asks the demography tables the same question the seed asked, through the same
    two functions, and gets real millions: they cannot disagree about which territory this
    is, and there is exactly one place to correct a figure.
    """
    from ..scenarios.demography import _territory_code, _territory_population
    from ..scenarios.registry import scenario_for

    preset = conn.execute(
        "SELECT preset_id FROM world WHERE id = ?", (world_id,)
    ).fetchone()
    scenario = scenario_for(str(preset["preset_id"]) if preset else None)
    if scenario is None:
        return {}

    resolved: dict[str, str | None] = {}
    for row in conn.execute(
        "SELECT id, COALESCE(controller_nation_id, owner_nation_id) AS holder"
        "  FROM region WHERE world_id = ? AND home_nation_id IS NULL"
        "   AND COALESCE(controller_nation_id, owner_nation_id) IS NOT NULL",
        (world_id,),
    ):
        resolved[str(row["id"])] = _territory_code(
            str(row["id"]), str(row["holder"]), scenario.era)

    # **A province is not the whole country.** The map splits Italian Eritrea into six and
    # Italian Somaliland into thirteen, and all of them resolve to one territory code, so
    # handing each the territory's whole population would say Eritrea has six Eritreas in
    # it. The seed divides the same pool the same way; giving each province the full figure
    # was enough on its own to lift four garrisoned Italian provinces off the floor and out
    # from under garrisons that had been holding them.
    shared: dict[str, int] = {}
    for code in resolved.values():
        if code:
            shared[code] = shared.get(code, 0) + 1

    return {
        region: (_territory_population(code, era=scenario.era) / shared[code] if code
                 else SMALL_TERRITORY_POPULATION)
        for region, code in resolved.items()
    }


def _separated(conn: sqlite3.Connection, world_id: str) -> set[str]:
    """Dependencies with no *land* border onto their own holder's home soil.

    One query per turn rather than a search per colony: the question is only whether the
    metropole can walk there, not how far it is. A sea crossing does not count, which is
    what makes Korea separated from Japan and Namibia contiguous with South Africa.
    """
    touching = {
        str(row["id"])
        for row in conn.execute(
            """
            SELECT r.id AS id
              FROM region r
              JOIN region_adjacency a
                ON a.world_id = r.world_id AND a.crossing = 'land'
               AND (a.region_a = r.id OR a.region_b = r.id)
              JOIN region h
                ON h.world_id = r.world_id
               AND h.id = CASE WHEN a.region_a = r.id THEN a.region_b ELSE a.region_a END
               AND h.home_nation_id = COALESCE(r.controller_nation_id, r.owner_nation_id)
             WHERE r.world_id = ? AND r.home_nation_id IS NULL
            """,
            (world_id,),
        )
    }
    return {
        str(row["id"])
        for row in conn.execute(
            "SELECT id FROM region WHERE world_id = ? AND home_nation_id IS NULL",
            (world_id,),
        )
    } - touching


def movements_of(
    conn: sqlite3.Connection, world_id: str, holder_id: str | None = None
) -> dict[str, float]:
    """``region -> momentum``, for everything with any at all."""
    sql = "SELECT region_id, momentum FROM independence WHERE world_id = ?"
    args: list = [world_id]
    if holder_id is not None:
        sql += " AND holder_id = ?"
        args.append(holder_id)
    try:
        return {
            str(row["region_id"]): float(row["momentum"])
            for row in conn.execute(sql + " ORDER BY region_id", args)
        }
    except sqlite3.OperationalError:
        # A world written before schema 23. Nothing is stirring, which is what it was.
        return {}


def restive_dependencies(
    conn: sqlite3.Connection, world_id: str, holder_id: str
) -> list[dict]:
    """What this holder should be worried about, worst first. For its own briefing."""
    momentum = movements_of(conn, world_id, holder_id)
    out = [
        {**entry, "momentum": momentum.get(entry["region_id"], 0.0)}
        for entry in dependencies_of(conn, world_id, holder_id)
        if momentum.get(entry["region_id"], 0.0) >= MOVEMENT_THRESHOLD
    ]
    out.sort(key=lambda e: (-e["momentum"], e["region_id"]))
    return out


def unrest_load(conn: sqlite3.Connection, world_id: str, nation_id: str) -> float:
    """What this holder's independence movements cost it, in the unrest layer's own unit.

    Zero for everybody with no restive colonies, and exactly zero on a world with no
    ``independence`` table -- the shape every one of these terms has.
    """
    return round(
        UNREST_PER_MOVEMENT * len(restive_dependencies(conn, world_id, nation_id)), 3)


def assess_independence(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str, seed: int,
    span: float = 1.0, round_index: int = 0,
) -> DecolonisationReport:
    """Advance every colony's movement by one turn, and say what crossed a line.

    Idempotent in the sense that matters: it is a function of the world and the turn, so
    replaying a turn after a rewind produces the same momentum -- the ``call_key`` names the
    region, the turn and the round, and nothing about the order colonies were walked in.
    """
    report = DecolonisationReport()
    holdings = dependencies_of(conn, world_id)
    if not holdings:
        return report
    current = movements_of(conn, world_id)
    people = _colonial_population(conn, world_id)
    separated = _separated(conn, world_id)

    diffs: list[Diff] = []
    crossed: list[tuple[str, str, str, float]] = []
    for entry in holdings:
        report.assessed += 1
        region_id = entry["region_id"]
        was = current.get(region_id, 0.0)

        gain = (
            MOMENTUM_PER_TURN
            + MOMENTUM_PER_POPULATION * _population_weight(
                people.get(region_id, SMALL_TERRITORY_POPULATION))
            + (MOMENTUM_IF_SEPARATED if region_id in separated else 0.0)
            # Negative, and small enough not to order the table. See the constant.
            + MOMENTUM_PER_DEVELOPMENT * entry["development"]
            - GARRISON_RELIEF * entry["garrison"]
        )
        # **A garrison slows and never reverses.** Enough troops must not make a colony want
        # to stay; they buy years, and that is all they ever bought.
        gain = max(0.0, gain) * span
        if gain > 0:
            # **The round is in the key**, or every round of a multi-round turn reuses
            # round 0's jitter and twelve draws are one draw scaled by twelve. Round 0 has
            # no suffix, so a monthly (one-round) turn draws what it always drew. The same
            # spelling as ``resolution._stream_key``; see ``unrest._partisan_key``.
            rng = derive(seed, world_id, turn, f"independence:{region_id}"
                         + (f"#r{round_index}" if round_index else ""))
            gain *= 1.0 + rng.uniform(-_VARIANCE, _VARIANCE)
        now = round(was + gain, 3)
        if abs(now - was) < 1e-6:
            continue

        row_id = f"{world_id}|{region_id}"
        if was == 0.0 and region_id not in current:
            diffs.append(Diff("independence", row_id, "momentum", DiffOp.INSERT,
                              {"holder_id": entry["holder"], "momentum": now,
                               "opened_turn": turn}))
        else:
            diffs.append(Diff("independence", row_id, "momentum", DiffOp.SET, now))

        for threshold, bucket in ((MOVEMENT_THRESHOLD, "stirred"),
                                  (INDEPENDENCE_THRESHOLD, "ripe")):
            if was < threshold <= now:
                crossed.append((bucket, region_id, entry["name"], entry["holder"]))

    if not diffs:
        return report

    # One event for the turn's growth, because momentum rising is not news; the crossings
    # are, and each gets its own so it can be cited on its own.
    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date, kind=EventKind.UNREST,
        origin=EventOrigin.ENGINE,
        text="Colonial administrations report on the state of opinion in the territories.",
        # **Secret.** How restive somebody else's empire is, is not something the world
        # reads in the newspapers; the *movement* below is public, because a general strike
        # is not deniable.
        visibility=Visibility.SECRET,
    )
    outcome = apply_batch(
        conn,
        DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                  proposer_model=None,
                  diffs=tuple(
                      Diff(d.target_table, d.target_id, d.field, d.op, d.new_value,
                           reason_event_id=event_id, visibility_scope=Visibility.SECRET)
                      for d in diffs)),
    )
    if not outcome.applied:
        unrecord_events(conn, (event_id,))
        report.problems.append(f"colonial opinion could not be recorded: {outcome.reason}")
        return report

    for bucket, region_id, name, holder in crossed:
        told = (f"An independence movement takes shape in {name}."
                if bucket == "stirred" else
                f"The independence movement in {name} is now the government of the "
                "territory in all but name.")
        record_event(
            conn, world_id=world_id, turn=turn, date=date, kind=EventKind.UNREST,
            origin=EventOrigin.ENGINE, actor_nation_id=holder, text=told,
            visibility=Visibility.PUBLIC,
        )
        getattr(report, bucket).append(f"{name} [{region_id}]: {told}")
    conn.commit()

    _settle_the_inevitable(conn, world_id, turn=turn, date=date, report=report)
    return report


def _settle_the_inevitable(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str,
    report: DecolonisationReport,
) -> None:
    """Free every dependency whose movement has passed the point of being stoppable.

    **Because otherwise decolonisation accrues and never completes.** ``grant_independence``
    is a tracked-agent channel, and Focus Mode tracks five nations a turn -- a hard ceiling
    with a database trigger behind it. PT39 ran 22 turns of 1936 in which the empires whose
    colonies were stirring were `usa`, `jpn`, `m_nld` and `m_aus`, and the tracked roster
    was `eth, fra, gbr, ger, ita` for 17 of them. The United States ranked 8.0 against
    Germany's 68 and was never tracked once. So the only empires that could act were the
    ones with nothing to act on, the exit was unreachable by any amount of playing on, and
    not a single colony was freed.

    Past :data:`INDEPENDENCE_THRESHOLD` the outcome is not really the holder's any more.
    The negotiated exit is what a holder buys by acting *early*: from 25 to 70 it can let a
    colony go on its own terms, take the regard for it, and stop paying the unrest. What
    happens here is what happens when it does not -- the same identity-correct, rewindable
    transition, arrived at rather than chosen.

    Deterministic: it walks :func:`dependencies_of`, which is ordered by region id, and
    depends on nothing but the world and the threshold.
    """
    momentum = movements_of(conn, world_id)
    ripe = [
        entry for entry in dependencies_of(conn, world_id)
        if momentum.get(entry["region_id"], 0.0) >= INDEPENDENCE_THRESHOLD
    ]
    for entry in ripe:
        # Nobody asked: past the threshold the holder's consent is no longer the question,
        # which is the one case ``granted_by=None`` exists for.
        new_id, problem = decolonise(
            conn, world_id, region_id=entry["region_id"], granted_by=None,
            turn=turn, date=date)
        if new_id is None:
            report.problems.append(
                f"{entry['name']} is past holding and could not be let go: {problem}")
            continue
        report.freed.append(
            f"{entry['name']} [{entry['region_id']}]: {entry['holder']} is no longer able "
            f"to hold it; it becomes {new_id}")


def decolonise(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    region_id: str,
    granted_by: str | None,
    turn: int,
    date: str,
    nation_id: str | None = None,
    name: str | None = None,
) -> tuple[str | None, str]:
    """Let a dependency go. Returns ``(new nation id, why not)``.

    **The whole transition is diffs**, so a rewind past it puts the empire back: the nation
    row appears by INSERT, the region's owner and home are SET, the movement row is deleted.
    Nothing here writes SQL directly, which is the defect PT31 caught in the leader and
    commitment tables.

    The three identity columns land where the sovereign/controller/core split says they
    should, and getting any of them wrong is a different bug:

    * ``owner_nation_id`` becomes the new state -- it is its own ground now;
    * ``home_nation_id`` becomes the new state too, which is the column that says *this is
      somebody's country*. A dependency's home was NULL precisely because it was nobody's,
      and independence is the moment that stops being true;
    * ``controller_nation_id`` is cleared, so nobody is occupying it.

    Refuses rather than guesses when the ground is not a dependency, which keeps this from
    being a general-purpose way to mint nations out of provinces.

    **``granted_by`` is who is letting it go, and it is required so no caller can forget
    it.** ``nation_id`` is the id of the *new* state, and for a long time it was the only
    nation this function was told about -- so `grant_independence` from any tracked power
    freed whatever region it named, and Germany could recognise Anguilla on Britain's
    behalf, with no war and no cost. A named requester must be the holder (the controller
    if the ground is occupied, the owner otherwise), which is what the schema has always
    promised: "Only ground you hold". ``None`` means nobody asked -- the engine settling a
    movement past :data:`INDEPENDENCE_THRESHOLD` -- and is the only way to skip the check.
    """
    row = conn.execute(
        "SELECT name, home_nation_id AS home, owner_nation_id AS owner,"
        "       controller_nation_id AS controller"
        "  FROM region WHERE world_id = ? AND id = ?",
        (world_id, region_id),
    ).fetchone()
    if row is None:
        return None, f"there is no region called {region_id!r} on this map"
    if row["home"] is not None:
        return None, (
            f"{region_id} is {row['home']}'s own ground, not a dependency; ending an "
            "occupation is a peace settlement, not a decolonisation"
        )
    holder = row["controller"] or row["owner"]
    if holder is None:
        return None, f"{region_id} is held by nobody, so there is nobody to let it go"
    if granted_by is not None and holder != granted_by:
        return None, (
            f"you do not hold {region_id}, so you cannot grant it independence -- "
            f"{holder} does. Name only dependencies you hold; your briefing lists them"
        )

    seat = conn.execute(
        "SELECT id FROM nation WHERE world_id = ? AND capital_region_id = ?",
        (world_id, region_id),
    ).fetchone()
    if seat is not None:
        # Rule CORE-005 would refuse the batch anyway; refusing here says why in a sentence
        # rather than as a rule code, and it is a real constraint: a government cannot grant
        # independence to the ground it is sitting in.
        return None, (
            f"{region_id} is where {seat['id']} governs from; a state cannot grant "
            "independence to its own seat of government"
        )

    new_id = nation_id or f"m_{region_id.removeprefix('r_')}"
    if conn.execute("SELECT 1 FROM nation WHERE id = ?", (new_id,)).fetchone():
        return None, f"there is already a nation called {new_id!r}"

    new_name = name or str(row["name"])
    holder_name = conn.execute(
        "SELECT name FROM nation WHERE id = ?", (holder,)).fetchone()
    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date, kind=EventKind.TERRITORY_TRANSFER,
        origin=EventOrigin.ENGINE, actor_nation_id=holder,
        text=f"{holder_name['name'] if holder_name else holder} recognises the "
             f"independence of {new_name}.",
        visibility=Visibility.PUBLIC,
    )
    diffs = [
        Diff("nation", new_id, "id", DiffOp.INSERT,
             {"world_id": world_id, "name": new_name,
              # Its own colour would need a palette this module has no business owning;
              # the neutral grey every generic minor gets is honest and is what the seeder
              # would have given it.
              "colour": "#8892A6", "is_player": 0, "controller": "engine",
              "capital_region_id": region_id},
             reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),
        Diff("region", region_id, "owner_nation_id", DiffOp.SET, new_id,
             reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),
        Diff("region", region_id, "home_nation_id", DiffOp.SET, new_id,
             reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),
        Diff("region", region_id, "controller_nation_id", DiffOp.SET, None,
             reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),
    ]
    if conn.execute(
        "SELECT 1 FROM independence WHERE world_id = ? AND region_id = ?",
        (world_id, region_id),
    ).fetchone():
        diffs.append(
            Diff("independence", f"{world_id}|{region_id}", "momentum", DiffOp.DELETE, None,
                 reason_event_id=event_id, visibility_scope=Visibility.PUBLIC))

    outcome = apply_batch(
        conn,
        DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                  proposer_model=None, diffs=tuple(diffs)),
    )
    if not outcome.applied:
        unrecord_events(conn, (event_id,))
        return None, f"the independence could not be recorded: {outcome.reason}"
    conn.commit()
    return new_id, ""


def colonial_lines(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> list[str]:
    """What this holder's empire is doing, for its own briefing. Empty if it has none.

    **It used to be empty for a quiet empire too, and that was the wrong silence.**
    `decolonise` refuses ground that is not a dependency and asks nothing about momentum,
    so letting a *calm* territory go has always been legal -- but a holder whose colonies
    were not yet stirring was shown nothing, and so was never told the option existed at
    all. Recognising somebody before they have to fight for it is the one version of this
    decision that is a policy rather than a concession, and it was the one case the briefing
    could not reach.
    """
    restive = restive_dependencies(conn, world_id, nation_id)
    if not restive:
        quiet = conn.execute(
            "SELECT id, name FROM region"
            " WHERE world_id = ? AND owner_nation_id = ? AND home_nation_id IS NULL"
            "   AND kind <> 'ocean' ORDER BY id LIMIT 8",
            (world_id, nation_id)).fetchall()
        if not quiet:
            return []
        return [
            "**Territories you hold that are nobody's country:** "
            + "; ".join(f"{r['name']} [{r['id']}]" for r in quiet)
            + ". None of them is stirring yet. You may recognise any of them as independent "
              "by naming its region id in `grant_independence` -- doing it now is a policy, "
              "and doing it later is a concession."
        ]
    ripe = [e for e in restive if e["momentum"] >= INDEPENDENCE_THRESHOLD]
    lines = [
        "**Independence movements in territories you hold:** "
        + "; ".join(f"{e['name']} [{e['region_id']}] at {e['momentum']:.0f}"
                    for e in restive[:8])
        + f". A movement begins at {MOVEMENT_THRESHOLD:.0f}. Garrisons slow it and nothing "
        "reverses it."
    ]
    if ripe:
        lines.append(
            "**Past the point where holding them is only a delay:** "
            + ", ".join(e["name"] for e in ripe)
            + ". You may recognise their independence by naming the region ids in "
            "`grant_independence`, which costs you the territory and is the only ending "
            "here that is not a defeat. **Naming the field matters**: the sentence used to "
            "say only that recognition was possible, and across a hundred and fifty turns "
            "of play-test no holder ever recognised anything -- a staff that has to guess "
            "which of thirteen channels carries a decision does not take it."
        )
    return lines
