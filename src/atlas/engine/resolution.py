"""Turning reported clashes into things that actually happened.

This is the half of the loop that decides. The simulator observes and reports; nothing it
returns can move a border, cost a division, or put two powers at war. Those follow from
here, under the ENGINE role, from a seeded draw against real force ratios.

Without this module the game has no verbs. A player can order an invasion, the parser will
accept it, an ``order_issued`` event will be written -- and then nothing, because every
column that would record a consequence is engine-only and no engine code was writing them.
The map froze on turn one and orders to take ground were silently ignored, which reads as
the models refusing rather than as a missing phase.

**Every write here is engine-authored and cites an engine-authored event.** A model cannot
reach these columns, cannot forge the event, and cannot influence the draw beyond the
enumerated evidence flags whose weights live in :mod:`atlas.sim.resolve`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..sim.resolve import (
    CAPTURE_THRESHOLD,
    IDLE_DECAY,
    Contest,
    ContestKind,
    EvidenceFlag,
    resolve_contest,
)
from ..sim.rng import derive, pick
from ..state.apply import apply_batch, record_event, unrecord_events
from ..state.diff import Diff, DiffBatch
from ..state.models import (
    Difficulty,
    DiffOp,
    EventKind,
    EventOrigin,
    Role,
    Visibility,
)
from .air import resolve_air_war
from .armistice import break_armistice, paused_pairs
from .bounds import absorbable
from .naming import resolve_nation, resolve_region
from .naval import amphibious_reach, sea_lift
from .objectives import objectives_of, pushes_as_contests
from .wareffort import casualty_diffs

__all__ = [
    "reconcile_self_sieges",
    "DeclarationCost",
    "ResolutionReport",
    "declaration_cost",
    "defensive_pact_allies",
    "resolve_turn",
    "settleable_wars",
]

#: What an unprovoked declaration costs the aggressor. Rule WAR-001 refuses a war
#: declared at no cost at all, and a refused declaration is a war that never starts.
_UNPROVOKED_WAR_COST = 8.0


@dataclass(frozen=True, slots=True)
class DeclarationCost:
    """What WAR-001 will make of a declaration, asked before anybody makes it.

    Exists so that the gate a government is shown and the rule it meets are the same
    arithmetic rather than two readings of the same intent -- see
    :func:`declaration_cost`.
    """

    #: A grievance against *this* target. What decides whether a charge is authored at all.
    provoked: bool
    #: A grievance WAR-001 itself would accept, which is the looser test -- the rule does
    #: not ask whom a recorded casus belli was against. Kept apart from ``provoked`` on
    #: purpose: the first decides what the engine charges, the second decides whether the
    #: rule would refuse the batch, and conflating them would either overcharge a
    #: declaration or hide a target that would in fact have gone through.
    accepted: bool
    #: Legitimacy actually chargeable, as a positive magnitude, already clamped to what the
    #: aggressor has left by :func:`atlas.engine.bounds.absorbable`.
    cost: float

    @property
    def refused(self) -> bool:
        """Whether WAR-001 would refuse this declaration outright.

        No grievance the rule accepts and nothing left to pay: the clamp yields zero, the
        rule sees no cost, and the batch is refused whole.
        """
        return not self.accepted and self.cost <= 0.0


def declaration_cost(
    conn: sqlite3.Connection, world_id: str, *, aggressor: str, target: str, turn: int
) -> DeclarationCost:
    """What opening this war would cost, and whether the rule would allow it at all.

    **One reading of WAR-001, used by the two places that need it.** `_declare_war` uses it
    to decide what to charge; `warpath.may_declare` uses it to decide whether to offer the
    target. They disagreed, and the disagreement was invisible: warpath advertised a
    grievance the world could see, WAR-001 asked for a grievance the *engine* had recorded,
    and a government with the first and neither the second nor any legitimacy left was
    handed a target that came back "the declaration of war was refused".

    **Standing on your own country is a grievance.** ``home_nation_id`` ground held by the
    target is warpath's strongest case and was costed here as unprovoked aggression -- a
    country retaking its own provinces paid the same eight legitimacy as one invading a
    neighbour that had done nothing. It is engine-recognised now, in this function and in
    the rule, so the two cannot drift apart again.
    """
    from .warpath import occupied_homeland

    reclaiming = occupied_homeland(conn, world_id, aggressor, target) > 0
    return DeclarationCost(
        provoked=reclaiming or _has_casus_belli(
            conn, world_id, aggressor, turn, against=target),
        accepted=reclaiming or _has_casus_belli(conn, world_id, aggressor, turn),
        cost=-absorbable(conn, "nation", aggressor, "legitimacy", -_UNPROVOKED_WAR_COST),
    )


def settleable_wars(conn: sqlite3.Connection, world_id: str) -> list[dict]:
    """Wars a peace could now settle: an occupier holding an enemy's ground while still at war.

    A province occupied *between two belligerents* is exactly what a cession may transfer
    (this reproduces the validity test in :func:`_apply_cessions`): the province is held by
    someone other than its owner, and its owner and holder are the two powers at war. When
    that state persists, the war has reached what real wars stall in -- ground held, not
    owned, and nothing converting one into the other -- which is the gap the peace layer
    exists to close but nothing prompts anyone to reach for. Surfaced to the referee so a
    settled occupation is nudged toward a peace that cedes the held ground, rather than
    grinding untouched (the observed failure: an occupier held a capital for turns and no
    one ever proposed terms).

    Returns one entry per ``(occupier, victim)`` pair, each listing the provinces the
    occupier holds and could annex at the table. A pure read of current state, so it costs
    nothing and rewinds with the turn.
    """
    rows = conn.execute(
        """
        SELECT r.id AS region_id, r.name AS region_name,
               r.controller_nation_id AS occupier_id, occ.name AS occupier_name,
               r.owner_nation_id AS victim_id, vic.name AS victim_name
          FROM region r
          JOIN nation occ ON occ.id = r.controller_nation_id AND occ.world_id = r.world_id
          JOIN nation vic ON vic.id = r.owner_nation_id AND vic.world_id = r.world_id
          JOIN relation rel ON rel.world_id = r.world_id
                           AND rel.nation_a = r.controller_nation_id
                           AND rel.nation_b = r.owner_nation_id
                           AND rel.at_war = 1
         WHERE r.world_id = ?
           AND r.kind <> 'ocean'
           AND r.controller_nation_id IS NOT NULL
           AND r.controller_nation_id <> r.owner_nation_id
           AND occ.is_alive = 1
           AND vic.is_alive = 1
         ORDER BY r.controller_nation_id, r.owner_nation_id, r.id
        """,
        (world_id,),
    ).fetchall()

    grouped: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (str(row["occupier_id"]), str(row["victim_id"]))
        entry = grouped.get(key)
        if entry is None:
            entry = {
                "occupier_id": str(row["occupier_id"]),
                "occupier_name": str(row["occupier_name"]),
                "victim_id": str(row["victim_id"]),
                "victim_name": str(row["victim_name"]),
                "provinces": [],
            }
            grouped[key] = entry
        entry["provinces"].append(
            {"region_id": str(row["region_id"]), "name": str(row["region_name"])}
        )
    return list(grouped.values())


@dataclass(slots=True)
class ResolutionReport:
    """What the resolution phase did, for the turn summary and the tests."""

    contests: int = 0
    battles_won: int = 0
    regions_occupied: int = 0
    #: Contested regions where the line moved without changing hands. A war that is
    #: being fought but has taken no ground yet is progress, and the report should
    #: not read as though nothing happened.
    fronts_moved: int = 0
    #: Formations moved between regions the nation already held. Not a battle, and the
    #: thing that decides whether there will be one worth fighting.
    redeployed: int = 0
    #: Standing objectives a tracked power's own agent declared that became fighting this
    #: turn. Counted separately from the simulator's clashes so per-country agency is
    #: visible in the turn report rather than only in the diff log.
    objectives_pursued: int = 0
    #: Formations that got out of ground that fell, and formations that did not.
    retreated: int = 0
    encircled: int = 0
    wars_declared: int = 0
    wars_ended: int = 0
    #: Provinces that changed hands at a peace table rather than on the battlefield. The
    #: point of the whole settlement layer: a war can now end with a border redrawn, and
    #: the report says which ground was recovered by its own people and which was annexed.
    provinces_ceded: int = 0
    #: Powers handed a casus belli this turn because a defensive pact they belong to was
    #: invoked. Named rather than counted: which alliance woke up is the news.
    pacts_invoked: list[str] = field(default_factory=list)
    #: Skies contested this turn, and the ones that changed hands. An air campaign is
    #: fought whether or not anybody ordered it, so the report is the only place a player
    #: finds out that the war above the war is going badly.
    air_battles: int = 0
    skies_taken: int = 0
    #: **Which** skies changed hands, as ``(region_id, winner)``, and how many were let go
    #: of. The counts above have been computed since the layer shipped and read by nothing
    #: but a probe script -- an air campaign is fought whether or not anybody ordered it,
    #: and a turn report that does not say so is the reason a player reads a supply corridor
    #: collapsing as the engine being broken. The pair is kept rather than the count because
    #: *where* is what a summary line has to name.
    skies: list[tuple[str, str]] = field(default_factory=list)
    skies_lapsed: int = 0
    problems: list[str] = field(default_factory=list)
    headlines: list[str] = field(default_factory=list)

    @property
    def acted(self) -> bool:
        return bool(
            self.contests
            or self.wars_declared
            or self.wars_ended
            or self.redeployed
            or self.air_battles
        )


def resolve_turn(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    turn: int,
    date: str,
    seed: int,
    contests: list[dict],
    actions: list[dict],
    declarations: list[dict] | None = None,
    difficulty: Difficulty = Difficulty.NORMAL,
    player_nation: str | None = None,
    rounds: int = 1,
    span: float = 1.0,
) -> ResolutionReport:
    """Resolve every clash and declaration this turn produced.

    ``contests`` are what the world simulator reported. ``actions`` are the player's
    parsed orders, which are read for two things the simulator has no standing to decide
    on the player's behalf: a declaration of war, and a movement into someone else's
    territory that the simulator failed to report as a clash.

    ``rounds`` is how many months of fighting this jump covers and ``span`` how long each
    round is, both from :func:`atlas.engine.pacing.rounds`. The defaults are one ordinary
    month, which is what every caller wanted before turn length meant anything.
    """
    report = ResolutionReport()

    # Declarations first: a war that starts this turn is the cause the battle cites.
    for action in actions:
        if str(action.get("type")) == "declare_war":
            _declare_war(conn, world_id, turn=turn, date=date, aggressor=player_nation,
                         target_value=action.get("target_nation"), report=report)

    # And the same for every other power. Before this channel existed the player was the
    # only nation on the map that could enter a state of war, and no war could ever end.
    for entry in declarations or ():
        action = str(entry.get("action") or "")
        if action == "declare_war":
            _declare_war(
                conn, world_id, turn=turn, date=date,
                aggressor=resolve_nation(conn, world_id, entry.get("actor")),
                target_value=entry.get("target"), report=report,
                justification=str(entry.get("justification") or ""),
            )
        elif action == "peace":
            # **Through the settlement layer, not the old shortcut.** `_settle_war` clears
            # two flags and moves occupied provinces: no agreement, no acceptance check,
            # nothing the other side could refuse. So the simulator could end any war on
            # any terms by asserting it, which is the one thing the layer exists to stop.
            _settle_through_terms(
                conn, world_id, turn=turn, date=date, entry=entry, report=report)
        elif action == "ceasefire":
            # **A truce, not a peace.** The old path cleared `at_war` for a ceasefire,
            # which made it a settlement that happened to carry no terms -- the war was
            # over, the ledger stopped, the aims lapsed, and nothing could break it later.
            # An armistice halts the fighting and leaves the war exactly where it was.
            _pause_the_fighting(
                conn, world_id, turn=turn, date=date, entry=entry, report=report)
        else:
            report.problems.append(f"a declaration named an unknown action {action!r}")

    # Which of the player's formations they gave orders to, read before those orders move
    # anything: the march below is a default, and a default must never overrule a player
    # who said what they wanted.
    spoken_for = _under_orders(conn, world_id, actions, player_nation)

    # Redeployment before the fighting: an army ordered to the frontier this month is at
    # the frontier when the month's fighting happens.
    if player_nation:
        _redeploy(conn, world_id, turn=turn, date=date, nation=player_nation,
                  actions=actions, report=report)

    # **Deduped by what each report resolves to, not by what it says.** See
    # :func:`_canonical`: an unresolvable report keeps its place in the queue so the
    # player is still told which name matched nothing.
    reported: list[dict] = []
    seen: set[tuple] = set()
    for reported_clash in contests:
        key = _canonical(conn, world_id, reported_clash)
        if key is not None and key in seen:
            continue
        if key is not None:
            seen.add(key)
        reported.append(reported_clash)

    # A movement into foreign territory is a clash whether or not the simulator said so.
    # Relying on it to notice is how an invasion becomes a no-op.
    for inferred in _contests_from_actions(conn, world_id, actions, player_nation):
        # The two refusals are reported rather than silently dropped: a player whose
        # invasion simply did not happen deserves to be told which of the two reasons
        # it was.
        if "__unreachable__" in inferred:
            name = _region_name(conn, inferred["__unreachable__"])
            report.problems.append(
                f"your forces cannot reach {name}: it borders none of your territory, "
                "and you have no sea route to it either -- that needs transports on a "
                "sea nobody else controls"
            )
            continue
        if "__friendly__" in inferred:
            names = _names(conn, (inferred["__holder__"],))
            report.problems.append(
                f"{names[inferred['__holder__']]} is bound to you by treaty; moving in was "
                "not treated as an invasion. Declare war first if that is what you meant."
            )
            continue
        # The simulator may already have reported the clash this order implies; fighting
        # it twice in one round is the same battle counted twice.
        key = _canonical(conn, world_id, inferred)
        if key is not None and key in seen:
            continue
        if key is not None:
            seen.add(key)
        reported.append(inferred)

    # **The air war is fought first, and once, whatever the jump length.**
    #
    # Before it, because the sky over a battle has to be decided before the battle reads
    # it; once, because an air campaign is the background condition of a month of war
    # rather than a thing that happens per engagement, and re-rolling it every round of a
    # seasonal jump would let a season of fighting change hands in the air four times.
    # Nobody orders this and nobody can decline it: it is what owning an air force means.
    # **The whole turn's span**, because the air war is fought once for the jump rather
    # than once per round -- and the sky that nobody flew over lapses by how much time went
    # by, not by how many times the player pressed the button. `span` here is one round's
    # worth; `rounds` is how many of them this jump covers.
    air = resolve_air_war(conn, world_id, turn=turn, date=date, seed=seed,
                          span=span * max(1, rounds))
    report.air_battles = air.battles
    report.skies_taken = len(air.taken)
    report.skies = list(air.taken)
    report.skies_lapsed = air.lapsed
    report.headlines.extend(air.headlines[:3])
    report.problems.extend(air.problems)

    # **The fighting runs once per round; everything above ran once for the jump.**
    #
    # A declaration of war is made once however long the turn is, and so is a player's
    # redeployment -- those are decisions. The fighting is not: a year of contact is a
    # year of contact, and resolving it as one enormous roll would settle a campaign on a
    # single draw. So a long jump is split into monthly rounds upstream and each round
    # fights an ordinary month. See :mod:`atlas.engine.pacing`.
    #
    # The reported and inferred contests belong to the round the *model* proposed them
    # for, so they are fought once, in the first round. Standing objectives are re-derived
    # every round against the map as it then stands, which is what lets a front advance
    # into the province behind the one it just took rather than stalling at the first.
    for index in range(max(1, rounds)):
        resolved: set[tuple] = set()
        combined: list[dict] = []
        if index == 0:
            for raw in reported:
                key = _canonical(conn, world_id, raw)
                if key is not None:
                    resolved.add(key)
                combined.append(raw)
        for pushed in pushes_as_contests(conn, world_id, turn=turn):
            key = _canonical(conn, world_id, pushed)
            if key is not None and key in resolved:
                continue
            if key is not None:
                resolved.add(key)
            combined.append(pushed)
            report.objectives_pursued += 1

        # **A standing truce holds the fire, and the first shot through it is the break.**
        # Filtered here rather than inside `_resolve_one` because this is the one place
        # every contest passes through -- the simulator's, the player's inferred moves and
        # the standing objectives alike -- and a truce that only stopped one of the three
        # would be a truce a staff could walk around.
        combined = _through_the_truce(
            conn, world_id, combined, turn=turn, date=date, report=report)

        for raw in combined:
            try:
                _resolve_one(
                    conn, world_id, raw, turn=turn, date=date, seed=seed,
                    difficulty=difficulty, player_nation=player_nation, report=report,
                    span=span, round_index=index,
                )
            except (KeyError, ValueError, TypeError, sqlite3.Error) as exc:
                # A malformed contest costs its own resolution, never the turn.
                report.problems.append(f"could not resolve a reported clash: {exc}")

        _march_to_the_front(conn, world_id, turn=turn, date=date,
                            spoken_for=spoken_for, report=report)

    # Once, at the end: a front nobody stood on all turn is a front that has been let go
    # of, and running this between rounds would decay a contest the next round revives.
    _decay_unpressed(conn, world_id, turn=turn, date=date, report=report)
    return report


def _decay_unpressed(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    turn: int,
    date: str,
    report: ResolutionReport,
) -> None:
    """Let go of every front nobody stood on this turn, and of every siege of one's own
    ground -- **including one that only became self-besieging during this turn.**

    `decay_idle_contests` has always dropped a self-besieging contest whoever pressed it and
    whenever it was last pressed; this caller asked the wrong question before calling it.
    The gate counted *idle* contests alone, so ground that changed hands inside the turn it
    was being fought over left a contest whose attacker now held it, pressed this turn,
    therefore not idle, therefore not looked at until the next pass. It self-corrected a
    turn later, which is why it never stuck -- and for that one turn the invariant was
    right that the world contained a province besieging itself, and the briefing rendered
    it as *"you hold it, you are 55% of the way to taking it"*, which is a sentence a staff
    acts on. PT43, PT45, PT46 and PT48 all caught it live on South Vietnam at Quang Tri.

    So the reconciliation runs against post-transfer ownership in the same pass that moved
    the ground. **Nothing about the resolution changed** -- this is the ordering of an
    existing cleanup.

    **What this is not, and the earlier version of this docstring said it was.** It is the
    end of the *resolution*, not the end of the *turn*. Three other passes move provinces
    after it -- partisans, peace terms, decolonisation -- and each can make a live contest
    self-besieging with nothing left to look again. That is why the finding survived this
    fix and appeared in PT51 anyway. :func:`reconcile_self_sieges` is the end-of-turn pass
    that closes it; this one stays because reconciling inside the resolution is still the
    right place to catch resolution's own captures.
    """
    pending = conn.execute(
        """
        SELECT COUNT(*) FROM contest c JOIN region r ON r.id = c.region_id
         WHERE c.world_id = ?
           AND (c.last_turn < ?
                OR COALESCE(r.controller_nation_id, r.owner_nation_id) = c.attacker_id)
        """,
        (world_id, turn),
    ).fetchone()[0]
    if not pending:
        return

    # **The sentence has to be true of what actually happened.** A turn whose only finding
    # is a siege that ate its own ground did not have a front go quiet, and the old text
    # said so -- which is why the alternative exists rather than a reworded single line:
    # every turn that *does* have idle fronts still reads exactly as it did.
    idle = conn.execute(
        "SELECT COUNT(*) FROM contest WHERE world_id = ? AND last_turn < ?",
        (world_id, turn)).fetchone()[0]
    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.NARRATIVE, origin=EventOrigin.ENGINE,
        text=("Fronts left unpressed this season lose their momentum." if idle
              else "A siege of ground its besieger now holds is called off."),
        visibility=Visibility.PUBLIC,
    )
    diffs = decay_idle_contests(conn, world_id, turn=turn, event_id=event_id)
    if not diffs:
        return
    outcome = apply_batch(
        conn,
        DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                  proposer_model=None, diffs=tuple(diffs)),
    )
    if not outcome.applied:
        report.problems.append(f"idle fronts could not be updated: {outcome.reason}")


def _redeploy(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    turn: int,
    date: str,
    nation: str,
    actions: list[dict],
    report: ResolutionReport,
) -> None:
    """Carry out movement orders between regions the nation already holds.

    Until provinces existed this was a question nobody could ask: a nation was one region,
    so "move the army to the frontier" had no frontier to move to and no interior to move
    from. Now it decides wars. France opens 1936 with thirteen divisions and one of them
    on the German border, and without redeployment the other twelve watch the campaign
    from Brittany while the war is fought and lost by whoever happened to be standing in
    the right place in January.

    Moving into somebody else's ground is not this. That is an invasion, and
    :func:`_contests_from_actions` reads it as one.
    """
    moves: list[tuple[str, str]] = []

    for action in actions:
        if str(action.get("type")) != "move_units":
            continue
        source = resolve_region(conn, world_id, action.get("from_region"))
        target = resolve_region(conn, world_id, action.get("to_region"))
        if not source or not target or source == target:
            continue

        row = conn.execute(
            "SELECT COALESCE(controller_nation_id, owner_nation_id) AS holder FROM region"
            " WHERE id = ? AND world_id = ?",
            (target, world_id),
        ).fetchone()
        if row is None or row["holder"] != nation:
            continue

        # An army marches; it does not appear. One region per turn, the same reach that
        # makes an invasion of a bordering region the only invasion anyone can attempt.
        if not _adjacent(conn, world_id, source, target):
            # Naming where they *can* go, because otherwise the refusal is unanswerable:
            # the player is told two provinces do not border and has no way to find out
            # which ones do. Found by playing -- an order to reinforce Alsace-Lorraine
            # from Paris was refused three times with nothing to act on.
            report.problems.append(
                f"your forces in {_region_name(conn, source)} cannot reach "
                f"{_region_name(conn, target)} in one move: the two do not border. "
                + _reachable_from(conn, world_id, nation, source)
            )
            continue

        available = conn.execute(
            "SELECT id FROM unit WHERE world_id = ? AND nation_id = ? AND region_id = ?"
            " ORDER BY id",
            (world_id, nation, source),
        ).fetchall()
        if not available:
            continue
        quantity = action.get("quantity")
        try:
            wanted = max(1, int(quantity)) if quantity is not None else len(available)
        except (TypeError, ValueError):
            wanted = len(available)

        moves.extend((unit["id"], target) for unit in available[:wanted])

    if not moves:
        return

    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.MOVE, origin=EventOrigin.ENGINE, actor_nation_id=nation,
        text=f"{len(moves)} formation(s) redeployed within {_names(conn, (nation,))[nation]}.",
        visibility=Visibility.PUBLIC,
    )
    outcome = apply_batch(
        conn,
        DiffBatch(
            world_id=world_id, turn=turn, proposer_role=Role.ENGINE, proposer_model=None,
            diffs=tuple(
                Diff("unit", unit_id, "region_id", DiffOp.SET, target,
                     reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
                for unit_id, target in moves
            ),
        ),
    )
    if outcome.applied:
        report.redeployed += len(moves)
    else:
        report.problems.append(f"a redeployment could not be written: {outcome.reason}")


def _under_orders(
    conn: sqlite3.Connection,
    world_id: str,
    actions: list[dict],
    nation: str | None,
) -> set[str]:
    """The player's formations that were given an order this turn, by unit id.

    A formation is spoken for when the player named the region it is standing in as the
    origin of a movement -- whether that movement was a redeployment or an invasion. The
    march below fills in for everything else and leaves these alone.
    """
    if not nation:
        return set()

    origins = {
        source
        for action in actions
        if str(action.get("type")) == "move_units"
        and (source := resolve_region(conn, world_id, action.get("from_region")))
    }
    if not origins:
        return set()

    placeholders = ",".join("?" * len(origins))
    return {
        row["id"]
        for row in conn.execute(
            "SELECT id FROM unit WHERE world_id = ? AND nation_id = ?"  # noqa: S608
            f" AND region_id IN ({placeholders})",
            (world_id, nation, *sorted(origins)),
        )
    }


#: How many formations a power tries to leave standing on each of its own threatened
#: provinces before it sends anything onward to the attack.
#:
#: **One, because the point is that somebody is there at all.** Every defensive mechanic in
#: the engine -- terrain, entrenchment, resilience, the supply swing -- is a multiplier
#: applied to a defender, and in a province with no defender in it they all multiply
#: nothing. Measured before this existed: across a twenty-four power map only half of all
#: threatened provinces had anybody standing on them, and on the Ukraine front all thirteen
#: of the defender's formations were stacked in a single oblast while five others stood
#: open. A higher number would be a real posture setting; one is the difference between a
#: front line and a walk-in, which is the thing that was missing.
GARRISON_TARGET = 1


def _threatened(
    conn: sqlite3.Connection, world_id: str, nation: str, held: set[str], enemies: set[str]
) -> set[str]:
    """Provinces this power holds that border ground an enemy holds.

    The same definition ``_distance_to_objectives`` falls back to, lifted out so that the
    march and the destination graph cannot drift apart about what the front is.
    """
    if not enemies:
        return set()
    holder = {
        row["id"]: row["holder"]
        for row in conn.execute(
            "SELECT id, COALESCE(controller_nation_id, owner_nation_id) AS holder"
            "  FROM region WHERE world_id = ?", (world_id,),
        )
    }
    return {
        region for region in held
        if any(holder.get(neighbour) in enemies
               for neighbour in _neighbours(conn, world_id, region))
    }


def _garrisons(
    conn: sqlite3.Connection, world_id: str, nation: str
) -> dict[str, int]:
    """How many land formations this power has standing on each of its regions."""
    return {
        str(row["region_id"]): int(row["n"])
        for row in conn.execute(
            "SELECT u.region_id, COUNT(*) AS n FROM unit u"
            "  JOIN region r ON r.id = u.region_id"
            " WHERE u.world_id = ? AND u.nation_id = ? AND r.kind <> 'ocean'"
            " GROUP BY u.region_id", (world_id, nation),
        )
    }


def _march_to_the_front(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    turn: int,
    date: str,
    spoken_for: set[str],
    report: ResolutionReport,
) -> None:
    """Every power at war sends its rear formations one region closer to its frontier.

    A nation at war has a general staff. Without one, promotion made the outcome of every
    war a property of where the scenario happened to put the divisions in January:
    Germany fought France with the one division standing in Saarland while six sat in
    Berlin, four provinces behind, for the whole campaign.

    **The player's staff too, and it is a default rather than a command.** Leaving the
    player out meant they redeployed a dozen divisions by hand every turn while every
    engine power had it done for free -- a tax on playing, not a decision. So the march
    covers everyone, and skips any formation the player gave an order to this turn:
    ``spoken_for`` is read before those orders are carried out, so an army sent somewhere
    deliberately stays sent.

    **A power that declared objectives marches toward those instead of toward whatever
    frontier is nearest.** That is most of what makes a declared objective mean anything:
    without it a staff could say it was going for the Saar while its reserves walked
    toward Poland, and the channel would be flavour.

    **But it leaves a garrison behind, which it did not used to.** Declaring an offensive
    silently converted a power's entire army into an assault force: the destination set
    replaced the frontier rather than joining it, so every formation in the country walked
    toward the one place the staff had named and every other province emptied. Measured, it
    was not a slow drift -- the defender ended with all thirteen of its formations stacked
    in one oblast and five of its own provinces standing open, and the attacker did the same
    thing with fourteen and fifteen. Ground changed hands with nobody on it, which is why
    every defensive mechanic in the engine measured as inert: they are all multipliers on a
    defender, and there was no defender.

    So the frontier is now always part of where an army wants to be, and a province that is
    threatened keeps ``GARRISON_TARGET`` formations before anything is released to march.
    A province that already has its garrison stops being a destination, so the surplus
    flows onward to the next one that has none instead of piling up where it started.
    """
    moves: list[tuple[str, str, str]] = []

    for nation in _nations_at_war(conn, world_id):
        held = _held_by(conn, world_id, nation)
        if len(held) < 2:
            continue
        # The frontier is where the war is: ground this power holds that touches ground
        # an enemy holds. Contest rows are not enough -- a front nobody pressed this turn
        # is still the place reinforcements are needed.
        declared = objectives_of(conn, world_id, nation)
        abandoned = set(declared.get("withdraw_from", ()))
        enemies = {
            str(row["nation_b"]) for row in conn.execute(
                "SELECT nation_b FROM relation"
                " WHERE world_id = ? AND nation_a = ? AND at_war = 1",
                (world_id, nation))
        }
        standing = _garrisons(conn, world_id, nation)
        # Ground the staff has given up on is not garrisoned: a withdrawal is a decision,
        # and leaving a division behind to honour a quota would quietly countermand it.
        needs_holding = {
            region for region in _threatened(conn, world_id, nation, held, enemies)
            if region not in abandoned
        }
        thin = {region for region in needs_holding
                if standing.get(region, 0) < GARRISON_TARGET}
        ashore = _distance_to_objectives(
            conn, world_id, nation, held, declared, reinforce=thin)
        # **Man the line, then attack.** A second graph, over the unheld ground only, and
        # it is consulted first for any formation that is not itself a garrison. Without it
        # the union above is not enough: a `push` makes the province the assault is staged
        # from a destination in its own right, every formation standing there is already at
        # distance zero, and so the whole army sits in one oblast while five others stand
        # open -- which is exactly the stack this was measured to fix and did not.
        toward_thin = _distance_to_objectives(
            conn, world_id, nation, held,
            {"withdraw_from": declared.get("withdraw_from", ())}, reinforce=thin,
        ) if thin else {}
        afloat = _distance_at_sea(conn, world_id, nation, held, declared)
        if not ashore and not afloat:
            continue
        #: How many formations have already been kept back on each province this turn.
        #: Counted as the loop runs, in unit-id order, so which formation stays is settled
        #: by the same rule twice and a rewind lands on the world that was played.
        retained: dict[str, int] = {}

        for unit in conn.execute(
            """
            SELECT u.id, u.region_id, COALESCE(t.domain, 'land') AS domain FROM unit u
              LEFT JOIN unit_template t ON t.world_id = u.world_id AND t.kind = u.kind
             WHERE u.world_id = ? AND u.nation_id = ? ORDER BY u.id
            """,
            (world_id, nation),
        ):
            region_id = unit["region_id"]
            if unit["id"] in spoken_for:
                continue
            # An army and a fleet are moving through different worlds. Marching a
            # battleship toward the front with the infantry puts it in Bavaria, UNIT-002
            # refuses the batch whole, and *nobody* is reinforced -- which is how adding
            # ships silently stalled a land campaign at four provinces.
            distance = afloat if unit["domain"] == "sea" else ashore
            if not distance:
                continue

            # **The garrison, taken off the top.** Checked before the distance rather than
            # after it, because a formation standing on threatened ground that no longer
            # counts as a destination has a distance of one or more and would otherwise be
            # released to march with all the others -- which is the emptying this exists to
            # stop. Land only: a squadron in harbour is not holding the province.
            if (unit["domain"] != "sea" and region_id in needs_holding
                    and retained.get(region_id, 0) < GARRISON_TARGET):
                retained[region_id] = retained.get(region_id, 0) + 1
                continue

            if region_id in abandoned:
                # Standing on ground the staff has given up on. Leave for whichever of
                # this power's remaining regions is closest to where it does want to be.
                out = sorted(
                    (distance[neighbour], neighbour)
                    for neighbour in _neighbours(conn, world_id, region_id)
                    if neighbour in distance
                )
                if out:
                    moves.append((nation, unit["id"], out[0][1]))
                continue

            # A formation surplus to its own province's garrison goes to whichever part of
            # the line has nobody on it, if any part of it has nobody on it. Only once the
            # frontier is covered does the rest of the army go where the staff pointed.
            if unit["domain"] != "sea" and toward_thin.get(region_id):
                distance = toward_thin

            here = distance.get(region_id)
            if here is None or here == 0:
                continue
            forward = sorted(
                neighbour
                for neighbour in _neighbours(conn, world_id, region_id)
                if distance.get(neighbour, here) == here - 1
            )
            if forward:
                moves.append((nation, unit["id"], forward[0]))

    if not moves:
        return

    for nation in sorted({nation for nation, _, _ in moves}):
        theirs = [(unit_id, target) for owner, unit_id, target in moves if owner == nation]
        event_id = record_event(
            conn, world_id=world_id, turn=turn, date=date,
            kind=EventKind.MOVE, origin=EventOrigin.ENGINE, actor_nation_id=nation,
            text=(
                f"{_names(conn, (nation,))[nation]} moved {len(theirs)} formation(s) "
                "toward the fighting."
            ),
            visibility=Visibility.PUBLIC,
        )
        outcome = apply_batch(
            conn,
            DiffBatch(
                world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                proposer_model=None,
                diffs=tuple(
                    Diff("unit", unit_id, "region_id", DiffOp.SET, target,
                         reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
                    for unit_id, target in theirs
                ),
            ),
        )
        if outcome.applied:
            report.redeployed += len(theirs)
        else:
            report.problems.append(f"reinforcements could not be moved: {outcome.reason}")


def _distance_at_sea(
    conn: sqlite3.Connection,
    world_id: str,
    nation: str,
    held: set[str],
    declared: dict[str, tuple[str, ...]],
) -> dict[str, int]:
    """How far a fleet is from the water it is wanted in.

    A fleet moves through a different map from the army: every sea is open to it, and the
    only land it may touch is its own coast. Where it is wanted is whatever water its
    staff declared, and failing that the seas washing an enemy's coast -- which is where a
    blockade sits, where a landing is covered from, and where the other fleet has to be
    met.
    """
    kinds = {
        row["id"]: (row["kind"], row["holder"])
        for row in conn.execute(
            "SELECT id, kind, COALESCE(controller_nation_id, owner_nation_id) AS holder"
            "  FROM region WHERE world_id = ?",
            (world_id,),
        )
    }
    enemies = {
        row["nation_b"]
        for row in conn.execute(
            "SELECT nation_b FROM relation WHERE world_id = ? AND nation_a = ? AND at_war = 1",
            (world_id, nation),
        )
    }
    if not enemies:
        return {}

    abandoned = set(declared.get("withdraw_from", ()))
    passable = {
        region
        for region, (kind, _holder) in kinds.items()
        if (kind == "ocean" or (region in held and kind == "coastal"))
        and region not in abandoned
    }
    if not passable:
        return {}

    wanted = {r for r in declared.get("push", ()) + declared.get("hold", ()) if r in passable}
    if not wanted:
        wanted = {
            region
            for region in passable
            if kinds[region][0] == "ocean"
            and any(
                kinds.get(neighbour, ("", None))[1] in enemies
                for neighbour in _neighbours(conn, world_id, region)
            )
        }
    if not wanted:
        return {}

    distance = {region: 0 for region in sorted(wanted)}
    queue = sorted(wanted)
    while queue:
        current = queue.pop(0)
        for neighbour in _neighbours(conn, world_id, current):
            if neighbour in passable and neighbour not in distance:
                distance[neighbour] = distance[current] + 1
                queue.append(neighbour)
    return distance


def _nations_at_war(conn: sqlite3.Connection, world_id: str) -> list[str]:
    return [
        row["nation_a"]
        for row in conn.execute(
            "SELECT DISTINCT nation_a FROM relation WHERE world_id = ? AND at_war = 1"
            " ORDER BY nation_a",
            (world_id,),
        )
    ]


def _held_by(conn: sqlite3.Connection, world_id: str, nation: str) -> set[str]:
    """The **ground** this nation holds. A sea it has taken is not ground.

    This feeds `_distance_to_objectives`, which is the graph an army marches through, and
    an ocean in that graph is a province a division walks into. Rule UNIT-002 then refuses
    the whole reinforcement batch, so *nobody* is reinforced -- and the more successful a
    power's navy is, the more of its own land war it freezes. A fleet has its own graph in
    `_distance_at_sea`, which reads oceans directly and does not need them here.
    """
    return {
        row["id"]
        for row in conn.execute(
            "SELECT id FROM region WHERE world_id = ? AND kind <> 'ocean'"
            " AND COALESCE(controller_nation_id, owner_nation_id) = ?",
            (world_id, nation),
        )
    }


def _neighbours(conn: sqlite3.Connection, world_id: str, region_id: str) -> list[str]:
    return [
        row[0]
        for row in conn.execute(
            "SELECT CASE WHEN region_a = ? THEN region_b ELSE region_a END"
            "  FROM region_adjacency WHERE world_id = ? AND (region_a = ? OR region_b = ?)",
            (region_id, world_id, region_id, region_id),
        )
    ]


def _distance_to_objectives(
    conn: sqlite3.Connection,
    world_id: str,
    nation: str,
    held: set[str],
    declared: dict[str, tuple[str, ...]],
    reinforce: set[str] | None = None,
) -> dict[str, int]:
    """How many regions of its own a power must cross to reach where it wants to be.

    Where it wants to be is what its staff declared, if it declared anything: the ground
    it means to hold, and its own ground next to the ground it means to take. Otherwise
    it is the whole frontier -- every region the power holds that touches an enemy --
    which is the answer for a nation simulated in bulk and had been the answer for
    everyone.

    Zero for a destination. Absent for a region no march can reach, which is how an
    island garrison stays where it is instead of walking into the sea. Ground the staff
    declared it is withdrawing from is left out of the graph entirely, so nothing marches
    through territory that power is giving up.

    ``reinforce`` is the power's own threatened ground that has nobody standing on it, and
    it is **unioned with what the staff declared rather than replacing it or being replaced
    by it.** It used to be neither: the frontier was a fallback used only when nothing had
    been declared, so naming a single offensive deleted a power's entire defensive line from
    the graph and the whole army walked off it. A province that already has its garrison is
    not in this set, which is what lets the surplus flow past it to the next one that has
    none instead of stopping at the first.
    """
    enemies = {
        row["nation_b"]
        for row in conn.execute(
            "SELECT nation_b FROM relation WHERE world_id = ? AND nation_a = ? AND at_war = 1",
            (world_id, nation),
        )
    }
    if not enemies:
        return {}

    passable = held - set(declared.get("withdraw_from", ()))
    if not passable:
        return {}

    edges: dict[str, list[str]] = {}
    for region in sorted(passable):
        edges[region] = [
            neighbour
            for neighbour in _neighbours(conn, world_id, region)
            if neighbour in passable
        ]

    holder = {
        row["id"]: row["holder"]
        for row in conn.execute(
            "SELECT id, COALESCE(controller_nation_id, owner_nation_id) AS holder"
            "  FROM region WHERE world_id = ?",
            (world_id,),
        )
    }

    wanted = set(declared.get("hold", ())) & passable
    # **A hold over ground this power does not hold is the defend-an-ally objective**, and
    # it behaves like a push target rather than being dropped: the army masses on its own
    # side of the border. That is what a relief force is -- nobody teleports a corps into
    # Poland -- and it is the same arithmetic, so the two share the loop.
    onward = list(declared.get("push", ())) + [
        region for region in declared.get("hold", ()) if region not in passable
    ]
    for target in onward:
        # A power cannot march onto ground it does not hold; it masses on its own side of
        # the border and the fighting carries it the rest of the way.
        wanted |= {
            neighbour
            for neighbour in _neighbours(conn, world_id, target)
            if neighbour in passable
        }

    wanted |= set(reinforce or ()) & passable

    if not wanted:
        wanted = {
            region
            for region in passable
            if any(
                holder.get(neighbour) in enemies
                for neighbour in _neighbours(conn, world_id, region)
            )
        }

    frontier = sorted(wanted)
    distance = {region: 0 for region in frontier}
    queue = list(frontier)
    while queue:
        current = queue.pop(0)
        for neighbour in edges.get(current, ()):
            if neighbour not in distance:
                distance[neighbour] = distance[current] + 1
                queue.append(neighbour)
    return distance


def _reachable_from(
    conn: sqlite3.Connection, world_id: str, nation: str, source: str, limit: int = 6
) -> str:
    """A sentence naming the neighbours of ``source`` this nation could march to.

    A refusal a player cannot act on is barely better than no refusal. The map knows the
    answer; the player has never been shown it.
    """
    rows = conn.execute(
        """
        SELECT r.name FROM region_adjacency a
          JOIN region r ON r.id = CASE WHEN a.region_a = ? THEN a.region_b
                                       ELSE a.region_a END
         WHERE a.world_id = ? AND (a.region_a = ? OR a.region_b = ?)
           AND COALESCE(r.controller_nation_id, r.owner_nation_id) = ?
           AND r.kind <> 'ocean'
         ORDER BY r.name
        """,
        (source, world_id, source, source, nation),
    ).fetchall()
    if not rows:
        return "It borders nothing else you hold."
    names = [str(row["name"]) for row in rows]
    listed = ", ".join(names[:limit])
    more = f", and {len(names) - limit} more" if len(names) > limit else ""
    return f"From there you can reach {listed}{more}."


def _adjacent(conn: sqlite3.Connection, world_id: str, a: str, b: str) -> bool:
    lo, hi = sorted((a, b))
    row = conn.execute(
        "SELECT 1 FROM region_adjacency WHERE world_id = ? AND region_a = ? AND region_b = ?",
        (world_id, lo, hi),
    ).fetchone()
    return row is not None


def _canonical(conn: sqlite3.Connection, world_id: str, raw: dict) -> tuple | None:
    """The clash a reported clash *is*, as ids, or ``None`` if it names nothing real.

    **The dedup key has to be the resolved one.** It was built from the raw labels the
    reporter wrote, and a model writes what it likes: ``"Germany"`` and ``"ger"`` are the
    same power, ``"Alsace-Lorraine"`` and ``"r_alsace"`` the same ground. Two reports of
    one battle under two spellings were two different keys, so both survived dedup and
    both were resolved -- and since :func:`_resolve_one` keys the *draw* by the resolved
    tuple, both drew the same stream and applied the same advance twice. One month of
    fighting moved the front by two months' worth, which is the per-round cap being
    quietly doubled.

    Exact duplicates went through for a simpler reason: the first-round loop added every
    reported clash to the seen-set and appended it regardless. A simulator that listed the
    same battle twice fought it twice.

    ``None`` for anything unresolvable, and an unresolvable clash is deliberately not
    deduped: :func:`_resolve_one` is the one place that explains to the player which name
    matched nothing, and collapsing two bad reports into one would silence half of that.
    """
    attacker = resolve_nation(conn, world_id, raw.get("attacker"))
    defender = resolve_nation(conn, world_id, raw.get("defender"))
    region_id = resolve_region(conn, world_id, raw.get("region_id"))
    if not attacker or not defender or not region_id:
        return None
    return (_kind(raw.get("kind")).value, attacker, defender, region_id)


def _stream_key(canonical: tuple, round_index: int) -> str:
    """The RNG coordinates of one battle, from the same tuple that deduped it.

    Written once so the two can never disagree: if the key that decides *whether* a clash
    is fought a second time is not the key that decides *how* it is fought, a duplicate
    slips through dedup and then draws the identical result.
    """
    kind, attacker, defender, region_id = canonical
    return (
        f"{kind}:{region_id}:{attacker}-vs-{defender}"
        + (f"#r{round_index}" if round_index else "")
    )


def _contests_from_actions(
    conn: sqlite3.Connection,
    world_id: str,
    actions: list[dict],
    player_nation: str | None,
) -> list[dict]:
    """Read the player's movement orders for clashes the simulator did not report."""
    if not player_nation:
        return []

    found: list[dict] = []
    for action in actions:
        # Movement only. Espionage names a country without attacking it, and inferring a
        # battle from it invaded Austria on an order to "quietly prepare intelligence and
        # plans concerning Austria" -- an invasion the player never wrote and could not
        # undo. Anything short of moving forces is not an attack.
        if str(action.get("type")) != "move_units":
            continue
        destination = action.get("to_region")
        if not destination:
            continue
        region_id = resolve_region(conn, world_id, destination)
        if region_id is None:
            continue
        row = conn.execute(
            "SELECT owner_nation_id, controller_nation_id FROM region WHERE id = ?",
            (region_id,),
        ).fetchone()
        if row is None:
            continue
        holder = row["controller_nation_id"] or row["owner_nation_id"]
        if not holder or holder == player_nation:
            continue

        # An army cannot appear anywhere on Earth. Without this, "move units to Japan"
        # conquered Japan from Berlin: the order named a region, the region had an owner,
        # and that was the whole test. Reachability is what makes a front a front.
        if not _adjacent_to_holdings(conn, world_id, player_nation, region_id):
            found.append({"__unreachable__": region_id})
            continue

        # Walking into an ally is not an invasion. Treating it as one occupied a friend's
        # territory with no declaration and no war -- from an order the player would have
        # read as reinforcing them.
        if _is_friendly(conn, world_id, player_nation, holder):
            found.append({"__friendly__": region_id, "__holder__": holder})
            continue

        found.append(
            {
                "kind": ContestKind.BATTLE.value,
                "attacker": player_nation,
                "defender": holder,
                "region_id": region_id,
                "flags": [],
                "reason_event_id": None,
            }
        )
    return found


def _adjacent_to_holdings(
    conn: sqlite3.Connection, world_id: str, nation_id: str, region_id: str
) -> bool:
    """Whether the nation holds, borders, or could land on the region it is moving into.

    The third is what makes an island a place a war can reach. See
    :mod:`atlas.engine.naval`: it is still a one-region reach, across water nobody else
    holds, with ships present to make the crossing.

    **Bordering a sea it holds is not bordering the far shore.** Without ``kind <>
    'ocean'`` a power that had taken the water could march an army straight across it onto
    any coast the sea touched -- no transports, no lift cap, none of what makes an
    amphibious assault a decision. Winning a sea gave the army the sea's adjacency, which
    is precisely the reach `amphibious_reach` exists to ration.
    """
    row = conn.execute(
        """
        SELECT COUNT(*) FROM region_adjacency a
          JOIN region mine ON mine.id = CASE WHEN a.region_a = ? THEN a.region_b
                                             ELSE a.region_a END
         WHERE a.world_id = ? AND (a.region_a = ? OR a.region_b = ?)
           AND mine.kind <> 'ocean'
           AND COALESCE(mine.controller_nation_id, mine.owner_nation_id) = ?
        """,
        (region_id, world_id, region_id, region_id, nation_id),
    ).fetchone()
    if row and row[0]:
        return True
    return region_id in amphibious_reach(conn, world_id, nation_id)


def _is_friendly(
    conn: sqlite3.Connection, world_id: str, nation_id: str, other: str
) -> bool:
    """Whether the two are allied or bound by a pact, and not already at war."""
    row = conn.execute(
        "SELECT treaty_status, at_war FROM relation"
        " WHERE world_id = ? AND nation_a = ? AND nation_b = ?",
        (world_id, nation_id, other),
    ).fetchone()
    if row is None or row["at_war"]:
        return False
    return str(row["treaty_status"]) in {"alliance", "defensive", "non_aggression"}


def _declare_war(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    turn: int,
    date: str,
    aggressor: str | None,
    target_value,
    report: ResolutionReport,
    justification: str = "",
) -> None:
    """Put two powers at war. Engine-only, because a model must not be able to start one."""
    target = resolve_nation(conn, world_id, target_value)
    if not aggressor or not target or target == aggressor:
        report.problems.append(
            f"a declaration of war named {target_value!r}, which matched no nation"
        )
        return
    # An international body holds no ground, so no contest could ever open against it and
    # the war would be a flag on a relation that nothing could ever resolve. Refused
    # rather than allowed-and-inert, because an inert war is one a staff keeps reasoning
    # from.
    for party in (aggressor, target):
        if conn.execute(
            "SELECT 1 FROM organisation WHERE nation_id = ?", (party,)
        ).fetchone():
            report.problems.append(
                f"{party} is an international body, not a country: it holds no ground and "
                "cannot be at war"
            )
            return
    if _at_war(conn, aggressor, target):
        return

    names = _names(conn, (aggressor, target))
    event_id = record_event(
        conn,
        world_id=world_id,
        turn=turn,
        date=date,
        kind=EventKind.WAR_DECLARED,
        origin=EventOrigin.ENGINE,
        text=(
            f"{names[aggressor]} declares war on {names[target]}."
            + (f" {justification.strip()}" if justification else "")
        ),
        actor_nation_id=aggressor,
        visibility=Visibility.PUBLIC,
        # **Whom the war is against, as a fact rather than as prose.** Without it the treaty
        # layer had to infer the aggressor from whichever declaration was most recent, and
        # charged an unrelated war to the wrong signatory.
        participants=(("actor", aggressor), ("target", target)),
    )

    #: What the player is told, if this declaration turns out to have happened.
    pending: list[str] = []

    # Both directions: relations are directed, and a war is not a one-sided condition.
    diffs = [
        Diff(
            target_table="relation",
            target_id=f"{a}|{b}",
            field="at_war",
            op=DiffOp.SET,
            new_value=1,
            reason_event_id=event_id,
            visibility_scope=Visibility.PUBLIC,
        )
        for a, b in ((aggressor, target), (target, aggressor))
    ]

    # WAR-001: war is available to anyone, but not for free. Without an engine-recognised
    # casus belli the declaration must cost legitimacy in the same batch, or the rule
    # refuses it -- and a refused declaration is a war that never starts.
    charge = declaration_cost(
        conn, world_id, aggressor=aggressor, target=target, turn=turn)
    if not charge.provoked:
        diffs.append(
            Diff(
                target_table="nation",
                target_id=aggressor,
                field="legitimacy",
                op=DiffOp.ADJUST,
                # **Charged against what there is**, like every other bounded charge --
                # see :mod:`atlas.engine.bounds`. A government with 3.0 legitimacy left
                # pays 3.0 for its unprovoked war rather than authoring a charge of 8.0
                # that cannot be paid. It matters more here than elsewhere: ECON-003 now
                # names the diff it condemns, so an unpayable charge would be *severed*
                # and the declaration would half-land -- the relation at war, nobody
                # charged, and no `war_declared` event to read it by.
                #
                # A nation with nothing left to pay still cannot declare: the clamp yields
                # zero, WAR-001 sees no cost, and the declaration is refused whole. That is
                # the rule doing its job rather than an overshoot doing it by accident.
                new_value=-charge.cost,
                reason_event_id=event_id,
                visibility_scope=Visibility.PUBLIC,
            )
        )
        # Staged, not written. A headline is the player's account of what happened, and
        # nothing has happened yet -- the batch below decides that.
        pending.append(
            f"{names[aggressor]} declared war with no provocation; the world noticed."
        )

    outcome = apply_batch(
        conn,
        DiffBatch(
            world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
            proposer_model=None, diffs=tuple(diffs),
        ),
    )
    if outcome.applied:
        report.wars_declared += 1
        report.headlines.extend(pending)
        report.headlines.append(f"{names[aggressor]} declared war on {names[target]}.")
        _invoke_defensive_pacts(
            conn, world_id, turn=turn, date=date, aggressor=aggressor, victim=target,
            report=report, cause_event_id=event_id,
        )
        return

    # **A war that did not start must not leave a declaration behind.** The event has to
    # be written before the batch, because the batch's diffs cite it -- so when the batch
    # is refused the news of a war is already on the record with no war under it.
    #
    # This one is load-bearing beyond the newspaper. `war_declared` carries
    # `participants` naming the aggressor and the target as *facts*, and the treaty layer
    # reads them to decide which signatory broke which pact; the legitimacy path reads
    # the same events to decide who has been starting wars. A phantom declaration
    # therefore attributes a breach to a power that never declared anything and charges
    # it for a war that never happened -- and, because `_at_war` stays false, the same
    # declaration is made again next turn and leaves a second one.
    unrecord_events(conn, [event_id])
    report.problems.append(f"the declaration of war was refused: {outcome.reason}")


#: What a pact member's regard for the aggressor loses when the treaty is invoked. Larger
#: than the +-25 per-turn swing a *model* may propose, because this is the engine writing
#: a consequence rather than a diplomat arguing for one -- the same arrangement the
#: nuclear layer's `outrage` has.
_PACT_BETRAYAL = 30.0


def defensive_pact_allies(
    conn: sqlite3.Connection, world_id: str, victim: str, aggressor: str
) -> list[str]:
    """Living powers bound by an active defensive pact to ``victim``, minus the aggressor.

    Reads ``commitment`` / ``commitment_nation`` for ``kind = 'defensive_pact'`` and
    ``status = 'active'`` -- which Modern Day has seeded since the era was written, with
    Article 5's text in the condition line, and which **nothing has ever read**. Before
    this the enum member ``CommitmentKind.DEFENSIVE_PACT`` appeared exactly once in the
    codebase: its own definition.

    A member of the same pact as the aggressor is excluded rather than treated as a
    guarantor of both sides. When NATO attacks NATO the treaty has already failed, and
    the honest answer is that the remaining members are freed to act, not that they owe
    the attacker anything.
    """
    rows = conn.execute(
        """
        SELECT DISTINCT other.nation_id AS ally
          FROM commitment c
          JOIN commitment_nation mine  ON mine.commitment_id = c.id
          JOIN commitment_nation other ON other.commitment_id = c.id
          JOIN nation n ON n.id = other.nation_id
         WHERE c.world_id = ?
           AND c.kind = 'defensive_pact'
           AND c.status = 'active'
           AND mine.nation_id = ?
           AND other.nation_id <> ?
           AND other.nation_id <> ?
           AND n.is_alive = 1
         ORDER BY other.nation_id
        """,
        (world_id, victim, victim, aggressor),
    ).fetchall()
    return [str(row["ally"]) for row in rows]


def _invoke_defensive_pacts(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    turn: int,
    date: str,
    aggressor: str,
    victim: str,
    report: ResolutionReport,
    cause_event_id: int,
) -> None:
    """Article 5: attacking a pact member gives every other member a free hand.

    **Prompted, not automatic, and the distinction is the design.** A hard auto-join would
    make one declaration drag five powers into a war nobody chose, which is not what a
    defensive pact is and not what happened the one time Article 5 was invoked -- members
    decided, and decided differently. So the engine does not declare anybody's war. What
    it does is make joining *legitimate and free*, and make not joining a visible choice:

    * **An engine-authored ``casus_belli`` for each ally.** That is load-bearing state, not
      flavour: ``_has_casus_belli`` exempts its holder from the ``_UNPROVOKED_WAR_COST``
      legitimacy hit for five turns, and rule DIP-003 reads the same record. An ally that
      enters this war pays nothing for it, while the aggressor has already paid.
    * **The allies' regard for the aggressor collapses**, engine-written, so the pact has a
      mechanical consequence even if no ally ever acts.

    Reuses ``EventKind.CASUS_BELLI``, which the closed CHECK enum already carries -- no
    schema change and nothing save-invalidating.

    The decision to actually fight stays where every other decision to fight is: with the
    referee and with each tracked power's own staff, reading a briefing that now states
    the consequence up front.
    """
    allies = defensive_pact_allies(conn, world_id, victim, aggressor)
    if not allies:
        return

    names = _names(conn, (aggressor, victim, *allies))
    diffs: list[Diff] = []
    invoked: list[str] = []

    for ally in allies:
        # An ally already fighting the aggressor needs no further excuse.
        if _at_war(conn, ally, aggressor):
            continue
        ally_event = record_event(
            conn,
            world_id=world_id,
            turn=turn,
            date=date,
            kind=EventKind.CASUS_BELLI,
            origin=EventOrigin.ENGINE,
            actor_nation_id=ally,
            text=(
                f"{names[aggressor]} attacked {names[victim]}, to whom "
                f"{names.get(ally, ally)} is bound by a standing defensive pact. "
                f"{names.get(ally, ally)} may enter this war without reproach."
            )[:600],
            visibility=Visibility.PUBLIC,
            causes=(cause_event_id,),
            # The grievance is the ally's, and it is against the aggressor specifically --
            # not a free licence to attack anybody for five turns.
            #
            # **And the victim, who is the whole reason there is a call to answer.** Named
            # as `aggrieved` so `engine.answering` can tell whose regard an ally loses by
            # staying home -- derived from the event rather than stored anywhere, which is
            # what keeps a model from claiming it came when it did not.
            participants=(("actor", ally), ("target", aggressor),
                          ("aggrieved", victim)),
        )
        diffs.append(
            Diff(
                target_table="relation",
                target_id=f"{ally}|{aggressor}",
                field="score",
                op=DiffOp.ADJUST,
                new_value=-_PACT_BETRAYAL,
                reason_event_id=ally_event,
                visibility_scope=Visibility.PUBLIC,
            )
        )
        invoked.append(names.get(ally, ally))

    if not diffs:
        return

    outcome = apply_batch(
        conn,
        DiffBatch(
            world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
            proposer_model=None, diffs=tuple(diffs),
        ),
    )
    if outcome.applied:
        report.pacts_invoked.extend(invoked)
        report.headlines.append(
            f"A defensive pact binds {', '.join(invoked)} to {names[victim]}; each now "
            f"has cause against {names[aggressor]}."
        )
    else:
        report.problems.append(
            f"a defensive pact could not be invoked: {outcome.reason}"
        )


def _pause_the_fighting(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    turn: int,
    date: str,
    entry: dict,
    report: ResolutionReport,
) -> None:
    """The simulator's ceasefire, through the armistice layer like anybody else's."""
    from .armistice import offer_ceasefire

    actor = resolve_nation(conn, world_id, entry.get("actor"))
    target = resolve_nation(conn, world_id, entry.get("target"))
    if not actor or not target or actor == target:
        report.problems.append(
            f"a ceasefire named {entry.get('actor')!r} and {entry.get('target')!r}, "
            "which matched no nations")
        return
    from .armistice import ARMISTICE_MONTHS

    outcome = offer_ceasefire(
        conn, world_id, nation_id=actor, enemy_id=target,
        months=int(entry.get("months") or 0) or ARMISTICE_MONTHS, turn=turn, date=date)
    if not outcome.agreed:
        report.problems.extend(
            line for line in outcome.refused[:2] if "are not at war" not in line)
        return
    names = _names(conn, (actor, target))
    report.headlines.append(
        f"{names[actor]} and {names[target]} agreed a ceasefire until "
        f"{outcome.expires_on or f'turn {outcome.expires_turn}'}. The war is not over.")


def _through_the_truce(
    conn: sqlite3.Connection,
    world_id: str,
    contests: list[dict],
    *,
    turn: int,
    date: str,
    report: ResolutionReport,
) -> list[dict]:
    """Drop the fighting a ceasefire has stopped, and break the ones somebody shot through.

    Two different things happen to a contest whose pair has a truce, and which one depends
    on where it came from. A contest the *world* produced -- a standing objective still
    pressing a front the guns have gone quiet on -- is simply not fought: nobody decided to
    break anything. A contest somebody *ordered* is an attack, and an attack through a
    truce is a breach: it is charged as one and then it happens, because it did.

    The engine cannot see intent, so it uses the only honest proxy it has: an objective
    that was standing before the truce is not an order given after it. A push declared into
    a paused front is left standing and unfought, which is also what a front looks like when
    the shooting stops.
    """
    paused = paused_pairs(conn, world_id)
    if not paused:
        return contests
    kept: list[dict] = []
    for raw in contests:
        attacker = resolve_nation(conn, world_id, raw.get("attacker"))
        defender = resolve_nation(conn, world_id, raw.get("defender"))
        if not attacker or not defender or frozenset((attacker, defender)) not in paused:
            kept.append(raw)
            continue
        if raw.get("reason_event_id") is None and not raw.get("flags"):
            # The world pressing an old objective into a quiet front. Not an act.
            report.problems.append(
                f"the ceasefire held: {attacker} did not press {defender}")
            continue
        line = break_armistice(
            conn, world_id, breaker=attacker, victim=defender, turn=turn, date=date)
        if line:
            report.headlines.append(line)
        kept.append(raw)
    return kept


def _settle_through_terms(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    turn: int,
    date: str,
    entry: dict,
    report: ResolutionReport,
) -> None:
    """The simulator's peace, put to the settlement layer like anybody else's.

    Terms are reviewed against the map and the other side accepts or refuses according to
    how the war has gone -- so the simulator proposes a settlement and does not impose one.
    Legacy ``cessions`` are read as ``cede_region`` terms, so a tape recorded before terms
    existed still means what it meant.
    """
    from .peace import offer_peace

    actor = resolve_nation(conn, world_id, entry.get("actor"))
    target = resolve_nation(conn, world_id, entry.get("target"))
    if not actor or not target or actor == target:
        report.problems.append(
            f"a settlement named {entry.get('actor')!r} and {entry.get('target')!r}, "
            "which matched no nations")
        return

    terms: list[dict] = []
    for raw in entry.get("terms") or ():
        if not isinstance(raw, dict):
            continue
        side = str(raw.get("from") or "target").strip().lower()
        terms.append({**raw, "from": "me" if side == "actor" else "them"})
    for raw in entry.get("cessions") or ():
        if not isinstance(raw, dict):
            continue
        to_id = resolve_nation(conn, world_id, raw.get("to"))
        terms.append({
            "kind": "cede_region",
            "from": "me" if to_id == target else "them",
            "region": str(raw.get("region") or ""),
        })

    outcome = offer_peace(
        conn, world_id, nation_id=actor,
        declared={"against": target, "terms": terms}, turn=turn, date=date)
    if not outcome.signed:
        # A settlement between two powers who are not fighting is a no-op rather than a
        # complaint -- the shortcut this replaced returned silently, and the simulator
        # proposes peace speculatively often enough that the noise would drown the report.
        report.problems.extend(
            line for line in outcome.refused[:3] if "are not at war" not in line)
        return
    report.wars_ended += 1
    report.provinces_ceded += len(outcome.headlines)
    names = _names(conn, (actor, target))
    report.headlines.append(f"{names[actor]} and {names[target]} stopped fighting.")
    report.headlines.extend(outcome.headlines)


def _settle_war(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    turn: int,
    date: str,
    actor_value,
    target_value,
    ceasefire: bool,
    report: ResolutionReport,
    justification: str = "",
    cessions: list[dict] | None = None,
) -> None:
    """End a war, and redraw the border if the peace does.

    Rule WAR-002 requires the diff that clears ``at_war`` to cite a ``peace_signed`` or
    ``ceasefire`` event, so the event is written first and both directions cite it.

    A *peace* may also cede provinces (a *ceasefire* only stops the shooting, so it carries
    no territory). Each cession names a province and which of the two belligerents it goes
    to, and the engine writes ``owner`` and ``controller`` together in the same batch as the
    war's end -- the "ratified cession" path the permission model reserves for exactly this.
    A province's ``home_nation_id`` is left untouched, so a settlement that hands a people
    back their own capital reads as recovery and one that keeps a conquest reads as
    annexation, and the report can tell them apart.
    """
    actor = resolve_nation(conn, world_id, actor_value)
    target = resolve_nation(conn, world_id, target_value)
    if not actor or not target or actor == target:
        report.problems.append(
            f"a settlement named {actor_value!r} and {target_value!r}, which matched no nations"
        )
        return
    if not _at_war(conn, actor, target):
        return

    names = _names(conn, (actor, target))
    kind = EventKind.CEASEFIRE if ceasefire else EventKind.PEACE_SIGNED
    word = "agree a ceasefire" if ceasefire else "sign a peace"
    event_id = record_event(
        conn,
        world_id=world_id,
        turn=turn,
        date=date,
        kind=kind,
        origin=EventOrigin.ENGINE,
        text=(
            f"{names[actor]} and {names[target]} {word}."
            + (f" {justification.strip()}" if justification else "")
        ),
        actor_nation_id=actor,
        visibility=Visibility.PUBLIC,
    )

    diffs = [
        Diff(
            target_table="relation",
            target_id=f"{a}|{b}",
            field="at_war",
            op=DiffOp.SET,
            new_value=0,
            reason_event_id=event_id,
            visibility_scope=Visibility.PUBLIC,
        )
        for a, b in ((actor, target), (target, actor))
    ]

    transfers: list[tuple[str, str, bool, str]] = []
    if not ceasefire:
        transfers = _cession_diffs(
            conn, world_id, belligerents=(actor, target), cessions=cessions or (),
            event_id=event_id, diffs=diffs, report=report,
        )

    outcome = apply_batch(
        conn,
        DiffBatch(
            world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
            proposer_model=None, diffs=tuple(diffs),
        ),
    )
    if outcome.applied:
        report.wars_ended += 1
        report.headlines.append(
            f"{names[actor]} and {names[target]} stopped fighting."
        )
        for region_name, to_id, recovered, region_id in transfers:
            report.provinces_ceded += 1
            verb = "recovered" if recovered else "annexed"
            report.headlines.append(f"{names.get(to_id, to_id)} {verb} {region_name}.")
            # **A record, not just a headline.** Ground changing hands at a peace table is
            # one of the three things the Security Council convenes about, and it reads
            # `territory_transfer` events -- which nothing had ever written, so the trigger
            # was dead code from the day it was added. A live Modern Day run found it the
            # hard way: Russia annexed Kiev, Donets'k, Mykolayiv and Sumy in one
            # settlement, the single most Council-worthy act of the game, and no session
            # convened. Recovering your own core is not annexation and does not file one.
            if not recovered:
                record_event(
                    conn, world_id=world_id, turn=turn, date=date,
                    kind=EventKind.TERRITORY_TRANSFER, origin=EventOrigin.ENGINE,
                    actor_nation_id=to_id,
                    text=f"{names.get(to_id, to_id)} annexed {region_name}.",
                    visibility=Visibility.PUBLIC,
                    # Who gained the ground and which ground, as facts. The Council reads
                    # these events, and "who benefited" was only ever in the sentence.
                    participants=(("actor", to_id), ("beneficiary", to_id)),
                    region_participants=(("target", region_id),),
                )
    else:
        report.problems.append(f"the settlement was refused: {outcome.reason}")


def _cession_diffs(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    belligerents: tuple[str, str],
    cessions,
    event_id: int,
    diffs: list[Diff],
    report: ResolutionReport,
) -> list[tuple[str, str, bool]]:
    """Validate each ceded province and append its owner/controller diffs.

    A cession is legal only if it moves ground that is genuinely *between these two
    belligerents*: the province's owner and its holder are the two at the table, and it is
    going to one of them. That is what stops a peace from signing away a bystander's land
    or conjuring a province out of a war it was never part of -- the same "name ground the
    map already knows" discipline the objectives channel uses.

    Two passes, because losing a capital is legal but has to be recorded as a capital move
    in the same batch (rule CORE-005). The first pass validates and gathers every province
    changing hands; the second emits the diffs and, when a nation is ceding its capital,
    relocates it to a province that nation still keeps -- refusing outright to dissolve a
    nation at the peace table, which is conquest's business, not a negotiator's.
    """
    valid: list[tuple[str, str, str, str, bool]] = []
    for entry in cessions:
        region_id = resolve_region(conn, world_id, entry.get("region"))
        to_id = resolve_nation(conn, world_id, entry.get("to"))
        if region_id is None or to_id is None:
            report.problems.append(
                f"a peace term named region {entry.get('region')!r} to {entry.get('to')!r}, "
                "which matched nothing"
            )
            continue
        if to_id not in belligerents:
            report.problems.append(
                f"a peace term ceded ground to {to_id}, who is not a party to this war"
            )
            continue
        row = conn.execute(
            "SELECT name, kind, owner_nation_id, controller_nation_id, home_nation_id "
            "FROM region WHERE id = ? AND world_id = ?",
            (region_id, world_id),
        ).fetchone()
        if row is None or row["kind"] == "ocean":
            report.problems.append(f"a peace term named {region_id}, which cannot be ceded")
            continue
        owner, controller = row["owner_nation_id"], row["controller_nation_id"]
        # The safety line for the whole feature: a peace may only ratify an occupation the
        # fighting already produced. The province must be occupied right now (someone else's
        # army standing on someone's ground), both parties to that occupation must be the two
        # at the table, and it can only go to one of them -- the occupier keeps it, or hands
        # it back. A model cannot cede peaceful, un-occupied land, so it cannot decide a
        # territorial outcome the battlefield never reached.
        if controller is None or controller == owner:
            report.problems.append(
                f"a peace term named {row['name']}, which no one is occupying -- a peace can "
                "only settle ground the fighting reached"
            )
            continue
        if {owner, controller} != set(belligerents) or to_id not in (owner, controller):
            report.problems.append(
                f"a peace term named {row['name']}, which is not occupied between these two powers"
            )
            continue
        valid.append(
            (region_id, to_id, owner, str(row["name"]), row["home_nation_id"] == to_id)
        )

    ceded = {v[0] for v in valid}
    capitals = {
        r["id"]: r["capital_region_id"]
        for r in conn.execute(
            "SELECT id, capital_region_id FROM nation WHERE world_id = ?", (world_id,)
        )
    }
    relocated: set[str] = set()
    transfers: list[tuple[str, str, bool, str]] = []
    for region_id, to_id, loser, name, recovered in valid:
        if capitals.get(loser) == region_id and loser not in relocated:
            fallback = _capital_fallback(conn, world_id, loser, ceded)
            if fallback is None:
                report.problems.append(
                    f"a peace term would have taken {name}, the last province of a nation; "
                    "a state cannot be dissolved at the peace table, only conquered"
                )
                continue
            diffs.append(
                Diff("nation", loser, "capital_region_id", DiffOp.SET, fallback,
                     reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
            )
            relocated.add(loser)
        diffs.append(
            Diff("region", region_id, "owner_nation_id", DiffOp.SET, to_id,
                 reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
        )
        diffs.append(
            Diff("region", region_id, "controller_nation_id", DiffOp.SET, to_id,
                 reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
        )
        transfers.append((name, to_id, recovered, region_id))
    return transfers


def _capital_fallback(
    conn: sqlite3.Connection, world_id: str, nation_id: str, ceded: set[str]
) -> str | None:
    """A province the nation keeps, to move its capital to. ``None`` if it keeps none."""
    for row in conn.execute(
        "SELECT id FROM region WHERE world_id = ? AND owner_nation_id = ? "
        "AND kind <> 'ocean' ORDER BY development DESC, id",
        (world_id, nation_id),
    ):
        if row["id"] not in ceded:
            return str(row["id"])
    return None


def _at_war(conn: sqlite3.Connection, a: str, b: str) -> bool:
    row = conn.execute(
        "SELECT at_war FROM relation WHERE nation_a = ? AND nation_b = ?", (a, b)
    ).fetchone()
    return bool(row and row["at_war"])


def _has_casus_belli(
    conn: sqlite3.Connection, world_id: str, nation_id: str, turn: int,
    against: str | None = None,
) -> bool:
    """Whether the engine already recorded a grievance this power can point at.

    Engine-authored only, and within five turns: a nation cannot mint its own grievance
    and cite it, and a decade-old one is not a reason for a war today.

    **And, when the target is known, a grievance *against that power*.** A casus belli
    recorded its holder and not whom it was against, so any grievance inside five turns
    licensed a war against anybody: Karthos wronged by Veyra could invade Aurelia for free.
    A grievance is a specific complaint about a specific country, and treating it as a
    general permit is the difference between a casus belli and a blank cheque.

    A grievance with no recorded target still counts, because every one written before
    schema 17 has none -- so an old save behaves exactly as it did, and this only ever gets
    *stricter* as new grievances are written.
    """
    if against is None:
        row = conn.execute(
            """
            SELECT COUNT(*) FROM event
             WHERE world_id = ? AND kind = ? AND origin = 'ENGINE'
               AND actor_nation_id = ? AND turn >= ?
            """,
            (world_id, EventKind.CASUS_BELLI.value, nation_id, max(0, turn - 5)),
        ).fetchone()
        return bool(row and row[0])

    row = conn.execute(
        """
        SELECT COUNT(*) FROM event e
         WHERE e.world_id = ? AND e.kind = ? AND e.origin = 'ENGINE'
           AND e.actor_nation_id = ? AND e.turn >= ?
           AND (EXISTS (SELECT 1 FROM event_participant p
                         WHERE p.event_id = e.id AND p.role = 'target'
                           AND p.nation_id = ?)
             OR NOT EXISTS (SELECT 1 FROM event_participant p
                             WHERE p.event_id = e.id AND p.role = 'target'))
        """,
        (world_id, EventKind.CASUS_BELLI.value, nation_id, max(0, turn - 5), against),
    ).fetchone()
    return bool(row and row[0])


def _resolve_one(
    conn: sqlite3.Connection,
    world_id: str,
    raw: dict,
    *,
    turn: int,
    date: str,
    seed: int,
    difficulty: Difficulty,
    player_nation: str | None,
    report: ResolutionReport,
    span: float = 1.0,
    round_index: int = 0,
) -> None:
    attacker = resolve_nation(conn, world_id, raw.get("attacker"))
    defender = resolve_nation(conn, world_id, raw.get("defender"))
    region_id = resolve_region(conn, world_id, raw.get("region_id")) or ""
    if attacker and attacker == defender:
        # A power garrisoning its own ground is not a battle. The simulator reports this
        # for orders like "reoccupy the Rhineland", which happen entirely inside one
        # nation, and it is nothing to resolve rather than something that went wrong.
        return
    if not attacker or not defender:
        missing = [
            repr(raw.get(side))
            for side, resolved in (("attacker", attacker), ("defender", defender))
            if not resolved
        ]
        report.problems.append(
            f"a clash named {' and '.join(missing)}, which matched no nation on the map"
        )
        return

    region = conn.execute(
        "SELECT id, name, owner_nation_id, controller_nation_id FROM region"
        " WHERE id = ? AND world_id = ?",
        (region_id, world_id),
    ).fetchone()
    if region is None:
        report.problems.append(f"a clash named region {region_id!r}, which does not exist")
        return

    # **You cannot besiege ground you are standing on**, and the two paths into this
    # resolver disagreed about that. `objectives.pushes_as_contests` drops a push whose
    # defender is the pushing power itself; a clash *reported by the world simulation* was
    # not checked, so one could open against a province the attacker already controlled.
    #
    # It then never closed: `captured` requires `held != attacker`, so the contest sat
    # accumulating pressure toward a capture that could not happen. PT34d found it running
    # for nine turns -- Russia's briefing read **"Donets'k -- you hold it, you are 55% of
    # the way to taking it"** every turn, its staff duly ordered a push into its own
    # province, and the objectives channel refused it with *there is nothing there to push
    # into*. Two parts of the engine describing the same province in contradictory terms,
    # which is what a seam is; the laboratory's `impossible-objectives` detector is what
    # noticed, six refusals in.
    holder = region["controller_nation_id"] or region["owner_nation_id"]
    if holder == attacker:
        report.problems.append(
            f"a clash named {region['name']}, which {attacker} already holds; "
            "there is nothing there to take"
        )
        return

    kind = _kind(raw.get("kind"))
    # The draw is keyed by the decision point, never by call order -- and a second
    # month of fighting over the same ground is a *different* decision point. Without
    # the round in the key every round of a yearly turn would draw the same number and
    # the year would resolve as one month repeated twelve times.
    contest = Contest(
        kind=kind,
        attacker=attacker,
        defender=defender,
        region_id=region_id,
        flags=_flags(raw.get("flags")),
        reason_event_id=_int_or_none(raw.get("reason_event_id")),
        key=_stream_key((kind.value, attacker, defender, region_id), round_index),
    )
    outcome = resolve_contest(
        conn, contest, world_id=world_id, turn=turn, seed=seed,
        difficulty=difficulty, player_nation=player_nation, span=span,
    )
    # **Everything below describes an outcome that has not been written yet.** Staged
    # into a report of its own and merged only once `apply_batch` says the world took it
    # -- see the merge at the foot of this function.
    staged = ResolutionReport()
    staged.contests = 1
    if outcome.attacker_wins:
        staged.battles_won = 1

    # Ground does not change hands on one roll. The day's fighting moves a front, and the
    # region falls only when accumulated pressure crosses the threshold -- so overrunning
    # a country is a campaign whose length is set by how hard its army is to shift.
    #
    # **Computed before the event rather than after it**, because the event has to say so.
    # See `_progress_sentence`: who won the exchange and how far the campaign has got are
    # two different facts, and the record carried only the first.
    held = region["controller_nation_id"] or region["owner_nation_id"]
    capture_seq: int | None = None
    contest_id, existing = _contest_state(conn, world_id, region_id, attacker)
    opening = existing is None
    pressure_after = max(
        0.0, min(CAPTURE_THRESHOLD, (existing or 0.0) + outcome.advance)
    )
    captured = pressure_after >= CAPTURE_THRESHOLD and held != attacker

    names = _names(conn, (attacker, defender))
    event_id = record_event(
        conn,
        world_id=world_id,
        turn=turn,
        date=date,
        kind=EventKind.BATTLE,
        origin=EventOrigin.ENGINE,
        text=(
            f"{names[attacker]} attacked {names[defender]} in {region['name']}. "
            f"{names[attacker] if outcome.attacker_wins else names[defender]} prevailed "
            f"{'decisively' if outcome.decisive else 'narrowly'}. "
            + _progress_sentence(
                attacker=names[attacker],
                region_name=str(region["name"]),
                before=existing or 0.0,
                after=pressure_after,
            )
        ),
        actor_nation_id=attacker,
        visibility=Visibility.PUBLIC,
        # **Who fought and who came out ahead, structurally.** The event named the attacker
        # and left the rest to a sentence, so who lost a battle could only be recovered by
        # matching a nation's name inside a string -- which "Germany" and "West Germany"
        # break outright. `beneficiary` is the side that prevailed; the role vocabulary is a
        # closed CHECK and this is the member that already means what is needed.
        participants=(
            ("actor", attacker),
            ("defender", defender),
            ("beneficiary", attacker if outcome.attacker_wins else defender),
        ),
    )
    staged.headlines.append(
        f"{names[attacker] if outcome.attacker_wins else names[defender]} "
        f"prevailed in {region['name']}."
    )

    # The attacker's formations are usually not *in* the region they are attacking -- they
    # are next door, which is what made the attack possible. Charging their losses only to
    # units already standing in the contested region means an invader never bleeds.
    forbidden = _forbidden_domain(conn, region_id)
    attacking_units = (
        _units_in(conn, attacker, region_id, forbidden=forbidden)
        or _units_staged_near(conn, world_id, attacker, region_id, forbidden=forbidden)
        # Nobody is standing next to an island. The troops for a landing are in the ports
        # across the water, and without this they take no losses and none of them ever
        # goes ashore -- the region changes hands and is held by nobody.
        or _units_shipped_to(conn, world_id, attacker, region_id, forbidden=forbidden)
    )
    # **At sea a loss concentrates, because a ship is a hull and not a pool.** See
    # `_loss_diffs`: the same total, cut into salvos and aimed by a seeded draw, so a hull
    # can reach zero and go down instead of the whole squadron eroding together forever.
    # Scoped by region kind, which is exactly equivalent to unit domain here -- `forbidden`
    # already means only sea formations are engaged in a sea contest -- and stated this way
    # because *where the fighting is* is what makes concentration right.
    #
    # One stream per side, keyed by the contest rather than by call order: the contest key
    # already carries kind, attacker, defender, region and round index, so a replay sinks
    # the same ship. A second month over the same water is a different decision point and
    # gets a different draw, exactly as the exchange itself does.
    at_sea = forbidden == "land"
    seed_for = (
        (lambda side: derive(seed, world_id, turn, f"losses:{contest.stream_key()}:{side}"))
        if at_sea
        else (lambda side: None)  # noqa: ARG005 - the flat branch takes no stream
    )

    attacker_toll = _loss_diffs(
        attacking_units, outcome.attacker_losses, event_id, rng=seed_for("attacker")
    )
    defender_toll = _loss_diffs(
        _units_in(conn, defender, region_id, forbidden=forbidden),
        outcome.defender_losses,
        event_id,
        rng=seed_for("defender"),
    )
    diffs = attacker_toll.diffs + defender_toll.diffs

    # **What the fighting cost, added up.** The strength diffs above are the losses
    # themselves; nothing had ever totalled them, so no layer could ask what a war had
    # cost -- which is the question a peace turns on. Charged here, in the same batch, so a
    # battle that is refused costs nobody a casualty they did not take. A pair that is not
    # at war gets no ledger: undeclared fighting stays outside the war loop by decision.
    #
    # **The diffs, not `outcome.*_losses`.** The two differ when a side is asked for more
    # than it has: the flat distribution can only take what is on the map, so a defender
    # with 0.96 strength left "lost" 2.03 by the resolver's arithmetic and 0.96 in fact.
    # Charging the nominal figure put a casualty on the ledger that no formation paid.
    diffs += casualty_diffs(
        conn, world_id,
        losses={attacker: _taken(conn, attacker_toll),
                defender: _taken(conn, defender_toll)},
        enemy_of={attacker: defender, defender: attacker},
        turn=turn, event_id=event_id,
    )

    # **Nothing may touch a formation that has just been removed.** `_apply_one` refuses
    # an update to a row that is not there, and it refuses by raising, which loses the
    # whole batch rather than the one diff. Two call sites below would otherwise do it:
    # `_advance_diffs` moves surviving attackers onto the ground they took, and
    # `_evict_diffs` reads the defender's garrison straight out of a database the batch has
    # not been applied to yet, so a sunk hull is still sitting in both.
    #
    # Filtering, never re-gathering: `_advance_diffs` takes a *prefix* of this list, and
    # `test_resolution_ordering` guards that every query feeding it says which rows come
    # first. A filter preserves that order; a fresh unordered `SELECT` would not.
    sunk = set(attacker_toll.sunk) | set(defender_toll.sunk)
    if attacker_toll.sunk:
        attacking_units = [u for u in attacking_units if u["id"] not in sunk]

    # **A ship going down is news, so it gets an event of its own.** The battle event is
    # already written by here and describes the exchange; this is the thing a briefing
    # should carry and a player should remember, and folding it into a sentence about who
    # prevailed would bury it. Unrecorded alongside the battle if the batch is refused --
    # a sinking that could not be written did not happen either.
    sunk_event: int | None = None
    if sunk:
        toll = " and ".join(
            f"{names[side]} lost {len(ids)} {'ship' if len(ids) == 1 else 'ships'}"
            for side, ids in (
                (attacker, attacker_toll.sunk), (defender, defender_toll.sunk)
            )
            if ids
        )
        sentence = f"{toll} in the fighting in {region['name']}."
        sunk_event = record_event(
            conn, world_id=world_id, turn=turn, date=date,
            kind=EventKind.BATTLE, origin=EventOrigin.ENGINE, actor_nation_id=attacker,
            text=sentence, visibility=Visibility.PUBLIC,
            # The same three roles the land battle carries. At sea "who prevailed" is who
            # lost fewer hulls, and an even exchange names the defender -- a raid that
            # traded evenly did not achieve what it set out to.
            participants=(
                ("actor", attacker), ("defender", defender),
                ("beneficiary",
                 attacker if len(attacker_toll.sunk) < len(defender_toll.sunk)
                 else defender),
            ),
        )
        staged.headlines.append(sentence)

    if captured:
        # The fight for this ground is over. Leaving the winner's row behind at zero
        # would show a siege of a region its besieger already holds.
        diffs.append(
            Diff("contest", contest_id, "__delete__", DiffOp.DELETE, None,
                 reason_event_id=event_id)
        )
    else:
        diffs += _contest_diffs(
            conn,
            world_id=world_id,
            contest_id=contest_id,
            region_id=region_id,
            attacker=attacker,
            defender=defender,
            pressure=pressure_after,
            turn=turn,
            event_id=event_id,
            opening=opening,
        )

    if not captured:
        staged.fronts_moved += 1
        # **The percentage on the losing line too.** A repulse said only that the attacker
        # was thrown back, so a front that had been pushed to 80% and gave up 5 of it read
        # exactly like one that had never moved -- and a player watching two powers trade
        # exchanges over the same ground saw a run of wins and losses with no way to tell
        # which of them was closer to taking it.
        staged.headlines.append(
            f"{names[attacker]} pressed {names[defender]} in {region['name']} "
            f"({pressure_after:.0%} of the way to taking it)."
            if outcome.advance > 0
            else f"{names[defender]} threw {names[attacker]} back in {region['name']} "
                 f"({pressure_after:.0%} of the way to taking it)."
        )

    # Occupation, not annexation. Taking ground puts you in control of it; ownership
    # changes only through a settlement, which is a diplomatic act rather than a battle.
    if captured:
        diffs.append(
            Diff(
                target_table="region",
                target_id=region_id,
                field="controller_nation_id",
                op=DiffOp.SET,
                new_value=attacker,
                reason_event_id=event_id,
                visibility_scope=Visibility.PUBLIC,
            )
        )
        # The seq of the diff that *is* the capture, so the merge below can tell a
        # refused capture from a granted one. `apply_batch` numbers diffs densely from
        # zero in the order they are given, so the index is the seq.
        capture_seq = len(diffs) - 1
        staged.regions_occupied = 1
        # **The owner, not the defender.** They are the same nation the first time ground
        # changes hands, which is why five real-model runs read correctly -- and different
        # the moment somebody takes their own province back, which PT20 was the first run
        # ever to do. Ukraine liberating Sumy was reported as "Ukraine now occupies Sumy,
        # which Russia still owns": the state was right and the sentence describing it
        # named the beaten occupier as the landlord.
        owner = region["owner_nation_id"]
        if owner == attacker:
            staged.headlines.append(
                f"{names[attacker]} has retaken {region['name']}."
            )
        else:
            owner_name = _names(conn, (owner,)).get(owner, owner) if owner else None
            staged.headlines.append(
                f"{names[attacker]} now occupies {region['name']}, "
                f"which {owner_name} still owns."
                if owner_name
                else f"{names[attacker]} now occupies {region['name']}."
            )
        # Everyone else pressing this region loses their accumulated progress: the ground
        # they were fighting over is now held by somebody new, under different defenders,
        # and carrying the old pressure forward would let a bystander inherit a siege it
        # did not fight.
        diffs += _clear_rival_contests(
            conn, world_id, region_id, keep_attacker=attacker, event_id=event_id
        )
        # The army that took the ground moves onto it. An occupation held by nobody is a
        # colour on the map that the next reserve walks straight back through.
        diffs += list(_advance_diffs(attacking_units, region_id, event_id))
        # And the army that lost it gets out, or does not get out.
        diffs += _evict_diffs(
            conn, world_id, region_id, defender=defender, event_id=event_id,
            names=names, report=staged, gone=sunk,
        )

    if not diffs:
        # The fighting produced nothing to write. It still happened, and the event that
        # says so is the whole of the record -- there is nothing here to refuse.
        _merge(report, staged)
        return

    applied = apply_batch(
        conn,
        DiffBatch(
            world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
            proposer_model=None, diffs=tuple(diffs),
        ),
    )
    if not applied.applied:
        # **A battle that could not be written did not happen.** Before this, the counters
        # and the headlines went up regardless: a refused batch reported a province
        # captured, a front moved and divisions taken prisoner, while the map showed none
        # of it -- and the `battle` event stayed behind for every later briefing to quote
        # as history. The refusal is now the only thing the turn reports.
        unrecord_events(conn, [event_id] + ([sunk_event] if sunk_event else []))
        report.problems.append(f"a resolved battle could not be written: {applied.reason}")
        return

    if capture_seq is not None and capture_seq in set(applied.severed):
        # Severing let the rest of the batch stand and took the one diff that *is* the
        # capture with it. The losses and the retreats are real; the province is not.
        staged.regions_occupied = 0
        staged.headlines = [
            line for line in staged.headlines
            if not line.startswith(f"{names[attacker]} now occupies")
            and not line.startswith(f"{names[attacker]} has retaken")
        ]
        report.problems.append(
            f"{region['name']} was fought over and did not change hands: the capture was "
            f"refused ({applied.reason})"
        )
    _merge(report, staged)


def _progress_sentence(
    *, attacker: str, region_name: str, before: float, after: float
) -> str:
    """Where the campaign for this ground stands, for the battle event to carry.

    **Winning the exchange and taking the ground are two different facts, and the record
    held only the first.** ``<winner> prevailed narrowly`` is what a `battle` event said,
    and it is the sentence every later briefing quotes -- so a naval campaign in which one
    power won both surface actions while the *other* accumulated the pressure and took the
    water read as a flat contradiction. The exchange is a day; the pressure is the
    campaign, and a reader given only the day cannot tell a stalled offensive from one two
    turns from carrying the position.

    It goes in the event text rather than into a new field on purpose, which is the whole
    of why this costs nothing. The diff that moves ``contest.pressure`` already cites this
    event -- that is rule CORE-001 -- so the sentence *is* the citation the causal layer
    reads, in exactly the shape :class:`atlas.engine.causality.Contribution` calls a
    *because*: the engine's own words for why a number moved, never a category invented
    beside it. Every nation-facing view that prints events carries it for free, and there
    is no second copy to fall out of step with the pressure after a rewind.

    **The delta is measured, not restated.** ``Outcome.advance`` is what the fighting
    would have moved; the pressure is clamped into ``[0, CAPTURE_THRESHOLD]``, so an
    attack that arrives at 0.9 with 0.5 of advance moves the campaign by 0.1 and not by
    0.5. Reporting the raw advance would print a number that does not agree with the two
    percentages either side of it.

    **No claim is made about the ground changing hands.** A capture is a diff, and a diff
    can be severed by a rule after this event has been written -- `_resolve_one` handles
    exactly that case, strips the occupation headlines and reports the refusal. A sentence
    here saying the region had been taken would survive the severing as false history,
    which is the failure `unrecord_events` exists to prevent. The pressure reaching 100%
    is true either way.
    """
    moved = after - before
    return (
        f"{attacker} is {after:.0%} of the way to taking {region_name} "
        f"({moved:+.0%} this month)."
    )


def _merge(report: ResolutionReport, staged: ResolutionReport) -> None:
    """Fold a resolution that actually landed into the turn's report.

    Counters add and lists extend, because one turn resolves many clashes into one
    report. Nothing here decides *whether* to merge -- that is the caller's judgement,
    and it is the whole point of staging.
    """
    report.contests += staged.contests
    report.battles_won += staged.battles_won
    report.regions_occupied += staged.regions_occupied
    report.fronts_moved += staged.fronts_moved
    report.retreated += staged.retreated
    report.encircled += staged.encircled
    report.headlines.extend(staged.headlines)
    report.problems.extend(staged.problems)


def _units_shipped_to(
    conn: sqlite3.Connection,
    world_id: str,
    nation_id: str,
    region_id: str,
    *,
    forbidden: str = "sea",
) -> list:
    """The formations a landing here would be mounted with, waiting in ports across the sea.

    Ordered so the same troops are picked every time, and capped at what the shipping can
    actually carry -- the transports are the reason a landing is a decision rather than a
    formality, and a list that ignored them would put an army group ashore behind one
    requisitioned merchantman.
    """
    route = amphibious_reach(conn, world_id, nation_id).get(region_id)
    if route is None:
        return []
    lift = sea_lift(conn, world_id, nation_id, route)
    if lift < 1:
        return []
    return conn.execute(
        """
        SELECT u.id, u.strength FROM unit u
          JOIN region r ON r.id = u.region_id
          LEFT JOIN unit_template t ON t.world_id = u.world_id AND t.kind = u.kind
         WHERE u.world_id = ? AND u.nation_id = ? AND u.strength > 0
           AND COALESCE(t.domain, 'land') <> ?
           AND COALESCE(t.domain, 'land') <> 'air'
           AND COALESCE(r.controller_nation_id, r.owner_nation_id) = u.nation_id
           AND EXISTS (SELECT 1 FROM region_adjacency a
                        WHERE a.world_id = u.world_id
                          AND ((a.region_a = r.id AND a.region_b = ?)
                            OR (a.region_b = r.id AND a.region_a = ?)))
         ORDER BY u.strength DESC, u.id
         LIMIT ?
        """,
        (world_id, nation_id, forbidden, route, route, lift),
    ).fetchall()


def _forbidden_domain(conn: sqlite3.Connection, region_id: str) -> str:
    """The unit domain that cannot legally stand in this region.

    Rule UNIT-002 refuses a fleet on land and an army at sea. Everything that gathers the
    formations *fighting* for a region has to agree with it in advance, because the ones
    that win move onto the ground -- and a batch that marches a battleship into Bavaria is
    refused whole, which silently turns a won battle into a war that never advances. That
    is exactly what happened the turn ships were added: France stalled at four German
    provinces for thirty-six turns and nothing said why.

    Aircraft are excluded from all three gatherers separately, and for a different
    reason: they are not forbidden from the region, they are simply not part of the
    assault. An air wing does not take losses in a tank battle and does not walk onto the
    ground it helped win -- what it contributes is the sky, in :mod:`atlas.engine.air`.
    """
    row = conn.execute("SELECT kind FROM region WHERE id = ?", (region_id,)).fetchone()
    return "land" if row and row["kind"] == "ocean" else "sea"


def _units_in(
    conn: sqlite3.Connection, nation_id: str, region_id: str, *, forbidden: str = "sea"
) -> list:
    """The nation's formations standing in the region.

    **Ordered, like every other gatherer here**, because the caller takes a *prefix* of
    this list -- :func:`_advance_diffs` moves half the army onto the ground it took. An
    unordered ``SELECT`` returns rows in whatever order the storage layer finds them,
    which is insertion order today and something else the moment a row is deleted and its
    slot reused. Replaying a turn after that would advance a different half of the army,
    move different units, and break the one guarantee this project makes: that a rewind
    and a replay reproduce the world byte for byte.
    """
    return conn.execute(
        """
        SELECT u.id, u.strength FROM unit u
          LEFT JOIN unit_template t ON t.world_id = u.world_id AND t.kind = u.kind
         WHERE u.nation_id = ? AND u.region_id = ?
           AND COALESCE(t.domain, 'land') <> ?
           AND COALESCE(t.domain, 'land') <> 'air'
         ORDER BY u.id
        """,
        (nation_id, region_id, forbidden),
    ).fetchall()


def _units_staged_near(
    conn: sqlite3.Connection,
    world_id: str,
    nation_id: str,
    region_id: str,
    *,
    forbidden: str = "sea",
) -> list:
    """The attacker's formations in territory bordering the contested region.

    An attack comes from somewhere. Without this the invader's units sit untouched at
    home while the defender bleeds, and war costs the aggressor nothing at all.

    Only formations that could stand on the objective: a destroyer in the next sea is
    part of the campaign but it is not part of the assault, and it must not be counted
    among the troops that walk onto the beach.

    Ordered for the reason given in :func:`_units_in`: the caller slices a prefix off it.
    """
    return conn.execute(
        """
        SELECT u.id, u.strength FROM unit u
          LEFT JOIN unit_template t ON t.world_id = u.world_id AND t.kind = u.kind
         WHERE u.world_id = ? AND u.nation_id = ?
           AND COALESCE(t.domain, 'land') <> ?
           AND COALESCE(t.domain, 'land') <> 'air'
           AND u.region_id IN (
                SELECT CASE WHEN a.region_a = ? THEN a.region_b ELSE a.region_a END
                  FROM region_adjacency a
                 WHERE a.region_a = ? OR a.region_b = ?
           )
         ORDER BY u.id
        """,
        (world_id, nation_id, forbidden, region_id, region_id, region_id),
    ).fetchall()


def contest_id_for(region_id: str, attacker: str) -> str:
    """Derived, not autoincremented, so a contest keeps its identity across a rewind."""
    return f"k_{region_id}_{attacker}"


def _contest_state(
    conn: sqlite3.Connection, world_id: str, region_id: str, attacker: str
) -> tuple[str, float | None]:
    """``(id, pressure)``. Pressure is ``None`` when no contest is open here yet."""
    contest_id = contest_id_for(region_id, attacker)
    row = conn.execute(
        "SELECT pressure FROM contest WHERE id = ? AND world_id = ?", (contest_id, world_id)
    ).fetchone()
    return contest_id, (float(row["pressure"]) if row else None)


def _contest_diffs(
    conn: sqlite3.Connection,
    *,
    world_id: str,
    contest_id: str,
    region_id: str,
    attacker: str,
    defender: str,
    pressure: float,
    turn: int,
    event_id: int,
    opening: bool,
) -> list[Diff]:
    """Open or update a contest. Written as diffs so rewind undoes a campaign exactly."""
    if opening:
        return [
            Diff(
                target_table="contest",
                target_id=contest_id,
                field="__insert__",
                op=DiffOp.INSERT,
                new_value={
                    "world_id": world_id,
                    "region_id": region_id,
                    "attacker_id": attacker,
                    "defender_id": defender,
                    "pressure": round(pressure, 4),
                    "opened_turn": turn,
                    "last_turn": turn,
                },
                reason_event_id=event_id,
                visibility_scope=Visibility.PUBLIC,
            )
        ]
    return [
        Diff("contest", contest_id, "pressure", DiffOp.SET, round(pressure, 4),
             reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),
        Diff("contest", contest_id, "last_turn", DiffOp.SET, turn,
             reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),
        # The holder can change under a contest that is still open, and the losses fall
        # on whoever is actually standing there.
        Diff("contest", contest_id, "defender_id", DiffOp.SET, defender,
             reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),
    ]


def _clear_rival_contests(
    conn: sqlite3.Connection, world_id: str, region_id: str, *, keep_attacker: str, event_id: int
) -> list[Diff]:
    rows = conn.execute(
        "SELECT id FROM contest WHERE world_id = ? AND region_id = ? AND attacker_id <> ? "
        "ORDER BY id",
        (world_id, region_id, keep_attacker),
    ).fetchall()
    return [
        Diff("contest", row["id"], "__delete__", DiffOp.DELETE, None, reason_event_id=event_id)
        for row in rows
    ]


def reconcile_self_sieges(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str
) -> list[str]:
    """Drop every contest whose attacker now holds the ground. One line per drop.

    **The same cleanup as the self-siege branch of :func:`decay_idle_contests`, asked at
    the end of the TURN rather than at the end of the RESOLUTION** -- and the difference
    between those two is the whole of this function's reason to exist.

    Resolution is not the only thing that moves a province. Four passes do, and three of
    them run *after* the fighting is resolved:

    * partisans rising and putting the flag back up (`unrest.assess_partisans`),
    * a peace term handing an occupied province home (`peace.honour_peace_terms`),
    * a colony becoming a country (`decolonisation.decolonise`, reachable from a tracked
      nation's own turn, which is the last thing in the jump),
    * and resolution's own captures, which the pass inside it already covers.

    So a country fighting to retake its own core -- South Vietnam at Quang Tri, in five
    separate recorded runs -- had its siege reconciled against the map as resolution left
    it, and then partisans handed the province back inside the same turn and nothing looked
    again. The earlier fix moved the reconciliation to the end of the resolution and its own
    docstring called that "the end of the turn"; it was not, and that gap is what kept the
    finding alive through PT42, PT45, PT46, PT48 and PT51.

    Why it matters is not a stale row. ``captured = pressure >= THRESHOLD and held !=
    attacker`` means such a contest can never end, and the briefing renders it as *"you hold
    it, you are 55% of the way to taking it"* -- a sentence a staff acts on, and did, by
    ordering a push into its own province every turn against an objectives channel that
    refused each one.

    Engine-authored diffs, ordered by contest id, so a replay is identical every run.
    """
    stuck = conn.execute(
        """
        SELECT c.id, r.name FROM contest c JOIN region r ON r.id = c.region_id
         WHERE c.world_id = ?
           AND COALESCE(r.controller_nation_id, r.owner_nation_id) = c.attacker_id
         ORDER BY c.id
        """,
        (world_id,),
    ).fetchall()
    if not stuck:
        return []

    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date, kind=EventKind.NARRATIVE,
        origin=EventOrigin.ENGINE,
        text=("The fighting stopped where the ground had already changed hands: "
              + ", ".join(sorted(str(row["name"]) for row in stuck)))[:600],
        visibility=Visibility.PUBLIC,
    )
    outcome = apply_batch(
        conn,
        DiffBatch(
            world_id=world_id, turn=turn, proposer_role=Role.ENGINE, proposer_model=None,
            diffs=tuple(
                Diff("contest", str(row["id"]), "__delete__", DiffOp.DELETE, None,
                     reason_event_id=event_id)
                for row in stuck),
        ),
    )
    if not outcome.applied:
        return [f"a siege of one's own ground could not be dropped: {outcome.reason}"]
    return [f"the siege of {row['name']} ended: it is already held" for row in stuck]


def decay_idle_contests(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    turn: int,
    event_id: int,
) -> list[Diff]:
    """Fade every contest that was not pressed this turn, close the spent ones, and drop
    the ones the map has made meaningless.

    Without the fading an attacker could push a region to the brink, walk away for twenty
    turns, and come back to find the siege exactly where it was left. A front that nobody
    is standing on is not a front.

    **And a siege of your own ground is dropped outright, whoever pressed it.** `_resolve_one`
    refuses to *open* one -- PT34d found what that costs -- but nothing closed one that
    became self-besieging afterwards, and the ground can change hands by half a dozen routes
    that never touch this resolver: a recapture, a ceded province going home in a peace, a
    colony taken back, an armistice restoring what it restores. PT43 caught it live: South
    Vietnam held Quang Tri and besieged it for two turns at 70% and 55%, and
    ``captured = pressure >= THRESHOLD and held != attacker`` means that contest could
    never have ended. What it costs is not a stale row -- the briefing renders it as *"you
    hold it, you are 55% of the way to taking it"*, which is a sentence a staff acts on.

    Ordered by id so the diffs -- and therefore the replay -- are identical every run.
    """
    rows = conn.execute(
        """
        SELECT c.id, c.pressure, c.last_turn,
               COALESCE(r.controller_nation_id, r.owner_nation_id) AS holder,
               c.attacker_id
          FROM contest c JOIN region r ON r.id = c.region_id
         WHERE c.world_id = ? ORDER BY c.id
        """,
        (world_id,),
    ).fetchall()

    diffs: list[Diff] = []
    for row in rows:
        if str(row["holder"] or "") == str(row["attacker_id"]):
            diffs.append(
                Diff("contest", row["id"], "__delete__", DiffOp.DELETE, None,
                     reason_event_id=event_id)
            )
            continue
        if int(row["last_turn"]) >= turn:
            continue
        faded = float(row["pressure"]) - IDLE_DECAY
        if faded <= 0.0:
            diffs.append(
                Diff("contest", row["id"], "__delete__", DiffOp.DELETE, None,
                     reason_event_id=event_id)
            )
        else:
            diffs.append(
                Diff("contest", row["id"], "pressure", DiffOp.SET, round(faded, 4),
                     reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
            )
    return diffs


def _evict_diffs(
    conn: sqlite3.Connection,
    world_id: str,
    region_id: str,
    *,
    defender: str,
    event_id: int,
    names: dict[str, str],
    report: ResolutionReport,
    gone: set[str] | None = None,
) -> list[Diff]:
    """Get the losing garrison out of ground it no longer holds, or take it prisoner.

    Before this, a province changed hands and the defenders stayed standing in it. That
    was invisible while a nation was one region -- there was nowhere to withdraw *to* --
    and became plainly wrong the moment a front could move: France lost Alsace-Lorraine
    and its thirteen divisions carried on sitting inside German-held ground, counting
    toward the defence of a region they did not hold and toward attacks launched from it.

    A garrison with anywhere to go withdraws to it, preferring ground the same attacker
    is not already pressing -- retreating into the next province about to fall is not a
    retreat. A garrison with nowhere to go is encircled, and an encircled formation is
    captured with the ground it was standing on. Both are ordinary diffs, so a rewind puts
    the divisions back where they were and un-captures the ones that were lost.

    ``gone`` is what the same batch has already removed. **This reads the database, and
    the batch it is contributing to has not been applied yet**, so a hull sunk in this
    very exchange is still sitting in the region as far as this query is concerned -- and
    withdrawing or capturing a ship that is being deleted three diffs earlier raises out
    of `_apply_one`, which refuses the whole batch rather than the one diff. A wreck does
    not retreat and is not taken prisoner.
    """
    already = gone or set()
    trapped = [
        row
        for row in conn.execute(
            """
            SELECT u.id, COALESCE(t.domain, 'land') AS domain FROM unit u
              LEFT JOIN unit_template t ON t.world_id = u.world_id AND t.kind = u.kind
             WHERE u.world_id = ? AND u.nation_id = ? AND u.region_id = ? ORDER BY u.id
            """,
            (world_id, defender, region_id),
        ).fetchall()
        if str(row["id"]) not in already
    ]
    if not trapped:
        return []

    besieged = {
        row["region_id"]
        for row in conn.execute(
            "SELECT region_id FROM contest WHERE world_id = ? AND defender_id = ?",
            (world_id, defender),
        )
    }
    # An army falls back onto its own ground; a fleet puts to sea, and any sea will do --
    # a beaten squadron does not need permission to leave harbour, and open water is the
    # one direction never closed to it.
    neighbours = conn.execute(
        """
        SELECT r.id, r.kind, COALESCE(r.controller_nation_id, r.owner_nation_id) AS holder
          FROM region_adjacency a
          JOIN region r ON r.id = CASE WHEN a.region_a = ? THEN a.region_b
                                       ELSE a.region_a END
         WHERE a.world_id = ? AND (a.region_a = ? OR a.region_b = ?)
        """,
        (region_id, world_id, region_id, region_id),
    ).fetchall()
    routes = {
        "sea": sorted(
            row["id"]
            for row in neighbours
            if row["kind"] == "ocean" or (row["holder"] == defender and row["kind"] == "coastal")
        ),
        "land": sorted(
            row["id"]
            for row in neighbours
            if row["holder"] == defender and row["kind"] != "ocean"
        ),
    }

    diffs: list[Diff] = []
    #: Where the garrison went, for the sentence naming the provinces.
    withdrew: set[str] = set()
    #: **Which formations got out**, which is a different question and the one the
    #: encirclement sentence is about. It used to compare the count of *destinations* to
    #: the count of units, so a garrison that escaped intact down one road -- three
    #: divisions, one province behind them -- read as one that got out and two that were
    #: taken prisoner. Every retreat where the survivors share a road narrated a
    #: non-existent encirclement, and the state was right the whole time.
    escaped: set[str] = set()
    for unit in trapped:
        escapes = routes["sea" if unit["domain"] == "sea" else "land"]
        # Somewhere quiet first, and anywhere at all rather than nowhere.
        ordered = [region for region in escapes if region not in besieged] or escapes
        if ordered:
            withdrew.add(ordered[0])
            escaped.add(str(unit["id"]))
            report.retreated += 1
            diffs.append(
                Diff("unit", unit["id"], "region_id", DiffOp.SET, ordered[0],
                     reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
            )
        else:
            report.encircled += 1
            diffs.append(
                Diff("unit", unit["id"], "__delete__", DiffOp.DELETE, None,
                     reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
            )

    where = _region_name(conn, region_id)
    if withdrew:
        report.headlines.append(
            f"{names.get(defender, defender)}'s forces withdrew from {where} to "
            + ", ".join(_region_name(conn, region) for region in sorted(withdrew))
            + "."
        )
    if len(escaped) < len(trapped):
        report.headlines.append(
            f"{names.get(defender, defender)}'s remaining forces in {where} were cut off "
            "and taken with it."
        )
    return diffs


def _advance_diffs(units: list, region_id: str, event_id: int):
    """Move the surviving attackers onto the ground they just took.

    Half of them, not all: an army that wins a battle does not abandon the territory it
    came from, and leaving the whole force in one region makes the next counterattack a
    single decisive roll.
    """
    for unit in units[: max(1, len(units) // 2)]:
        yield Diff(
            target_table="unit",
            target_id=unit["id"],
            field="region_id",
            op=DiffOp.SET,
            new_value=region_id,
            reason_event_id=event_id,
            visibility_scope=Visibility.PUBLIC,
        )


#: How many salvos a concentrated loss is cut into, given the formations engaged: half of
#: them, rounded up.
#:
#: **This is the one knob on how hard losses concentrate, and it is a balance choice.**
#: Fewer salvos concentrates harder -- at one, the whole of a turn's loss lands on a single
#: hull, which sinks ships fast and reads as a lucky magazine hit every single month. More
#: approaches the flat model, and at one salvo per formation is barely distinguishable from
#: it. At half, no more than half a fleet is damaged in any one exchange and the heaviest
#: hull takes roughly twice the flat share, which is what makes a hull wear down toward
#: zero across a campaign instead of the whole squadron sinking together at the end of it.
def _salvo_count(engaged: int) -> int:
    return max(1, (engaged + 1) // 2)


@dataclass(slots=True)
class _Losses:
    """What a side's casualties come to, as diffs, plus what did not survive them."""

    diffs: list[Diff] = field(default_factory=list)
    #: Unit ids removed from the map. The caller must not touch these again in the same
    #: batch: `_apply_one` refuses an update to a row that is not there, and a raise
    #: refuses the whole batch rather than the diff.
    sunk: list[str] = field(default_factory=list)


def _taken(conn: sqlite3.Connection, toll: _Losses) -> float:
    """What a side's losses actually came to on the map, for the war ledger.

    The strength adjustments plus whatever the hulls that went down were still worth. Read
    from the diffs rather than from the resolver's figure so the ledger and the map cannot
    disagree -- see the note at the call site.
    """
    total = sum(abs(float(diff.new_value or 0.0)) for diff in toll.diffs
                if diff.op is DiffOp.ADJUST and diff.field == "strength")
    for unit_id in toll.sunk:
        row = conn.execute(
            "SELECT strength FROM unit WHERE id = ?", (unit_id,)).fetchone()
        if row is not None:
            total += float(row["strength"])
    return round(total, 3)


def _loss_diffs(
    units: list, losses: float, event_id: int, *, rng=None
) -> _Losses:
    """Charge a side's losses to the formations that were engaged.

    Two distributions, and the caller chooses. Both spend **exactly the same total**: the
    figures come from :func:`~atlas.sim.resolve.resolve_contest` and are not touched here,
    so the attrition feedback loop that `_LOSS_DECISIVE` and `_LOSS_CLOSE` were halved to
    tame -- losses shift the force ratio, which shifts the advance -- keeps the tuning it
    was given whichever branch runs.

    **Flat (``rng is None``), which is the land war.** Proportional across every formation:
    losing a tenth of your strength costs a large army more than a small one. A division's
    strength stands for men and materiel, so uniform erosion is the honest model for it,
    and losing a *formation* ashore is supposed to come from being encircled -- which
    :func:`_evict_diffs` does -- rather than from attrition. :data:`atlas.engine.air.
    _UNIT_FLOOR` already holds that line for the air layer, in as many words.

    **Concentrated (``rng`` given), which is the fleet action.** A ship is a hull: it
    floats or it does not. Under the flat model a fleet of four hulls losing 8% of its
    strength lost 8% of every hull, so no hull could reach zero before the whole squadron
    did and nothing removed one at zero -- seven turns of a real naval war sank nothing,
    and a *"ship goes down"* moment was not rare but arithmetically unreachable.

    So the total is cut into :func:`_salvo_count` salvos and each is aimed by a seeded
    draw. **Aimed uniformly among what is still afloat, not weighted by strength**, which
    is the one thing here that had to be decided rather than copied: weighting by strength
    spares a cripple, and sparing cripples is exactly backwards for the mechanic being
    built. A salvo larger than what its target has left sinks it and the surplus spills to
    the next, so a side never loses more than the arithmetic said and never less.

    ``rng`` is a stream derived from the contest's own key, so the same fleet action sinks
    the same ship on a replay. It is passed in rather than derived here because the key
    that identifies the decision belongs to the caller, not to the allocator.
    """
    out = _Losses()
    if losses <= 0:
        return out
    total = sum(float(u["strength"]) for u in units)
    if total <= 0:
        return out

    if rng is None:
        share = min(1.0, losses / total)
        charged = {str(u["id"]): float(u["strength"]) * share for u in units}
    else:
        charged = _aim_salvos(rng, units, min(losses, total))

    for unit in units:
        unit_id = str(unit["id"])
        lost = round(charged.get(unit_id, 0.0), 2)
        if lost <= 0:
            continue
        # **Only the concentrated branch may remove a formation, and the guard is
        # `rng is not None` rather than the arithmetic.** The flat branch can reach a full
        # charge on its own -- `share` is `min(1.0, losses / total)`, and a side whose
        # engaged list is one small unit while its `_strength` counts a national fallback
        # and half the neighbouring province can be charged more than that unit has. Left
        # to the arithmetic, this would have started deleting land formations, which is
        # exactly the scope this item said it was not touching. The flat branch is
        # bit-for-bit what it was.
        #
        # **Rounded before the decision, not after.** The diff carries two decimals, so a
        # hull charged 9.997 of its 10.0 is written down to exactly 0.0 -- a ship still on
        # the map, still counted as a formation, worth nothing. Whether it survives is
        # settled by the same number the diff will carry.
        if rng is not None and round(float(unit["strength"]) - lost, 2) <= 0:
            out.sunk.append(unit_id)
            out.diffs.append(
                Diff("unit", unit_id, "__delete__", DiffOp.DELETE, None,
                     reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
            )
            continue
        out.diffs.append(
            Diff(
                target_table="unit",
                target_id=unit_id,
                field="strength",
                op=DiffOp.ADJUST,
                new_value=-lost,
                reason_event_id=event_id,
                visibility_scope=Visibility.PUBLIC,
            )
        )
    return out


def _aim_salvos(rng, units: list, budget: float) -> dict[str, float]:
    """Split ``budget`` into salvos and aim each one, spilling what a target cannot take.

    Kept apart from :func:`_loss_diffs` so the allocation can be read and tested as
    arithmetic: it spends the budget exactly, never charges a formation more than it has,
    and depends on nothing but the stream and the units it is given.
    """
    remaining = {str(u["id"]): float(u["strength"]) for u in units}
    charged: dict[str, float] = {}
    # Ordered by id by every gatherer in this module, and `pick` indexes into the list, so
    # the draw would follow row order if this did not. It is already sorted; sorting again
    # states the requirement where a reader can see it.
    afloat = sorted(remaining)

    salvos = _salvo_count(len(units))
    per_salvo = budget / salvos
    for _ in range(salvos):
        weight = per_salvo
        # A salvo that overshoots its target is not wasted: the surplus is re-aimed, which
        # is what keeps the side's total loss exactly the figure the contest produced.
        while weight > 1e-9 and afloat:
            target = pick(rng, afloat)
            take = min(weight, remaining[target])
            charged[target] = charged.get(target, 0.0) + take
            remaining[target] -= take
            weight -= take
            if remaining[target] <= 1e-9:
                afloat.remove(target)
    return charged


def _kind(value) -> ContestKind:
    try:
        return ContestKind(str(value))
    except ValueError:
        return ContestKind.BATTLE


def _flags(value) -> tuple[EvidenceFlag, ...]:
    """Keep only flags the engine recognises. An invented one is dropped, not honoured."""
    out: list[EvidenceFlag] = []
    for entry in value or ():
        try:
            out.append(EvidenceFlag(str(entry)))
        except ValueError:
            continue
    return tuple(out)


def _int_or_none(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _names(conn: sqlite3.Connection, ids) -> dict[str, str]:
    found = {}
    for nation_id in ids:
        row = conn.execute("SELECT name FROM nation WHERE id = ?", (nation_id,)).fetchone()
        found[nation_id] = row["name"] if row else nation_id
    return found


def _region_name(conn: sqlite3.Connection, region_id: str) -> str:
    row = conn.execute("SELECT name FROM region WHERE id = ?", (region_id,)).fetchone()
    return row["name"] if row else region_id
