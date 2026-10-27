"""What a tracked power is trying to do with its army, and what the engine does about it.

This is the military half of per-country agency. A tracked nation already had its own
call, its own goals and its own reading of events -- and no way at all to say what it
wanted done with its army, because every column that could express one is engine-only.
So a power with a dedicated agent fought its wars by heuristic: reserves walked toward
whichever frontier happened to be nearest, and "Germany is going for the Saar" was
something the player could infer from the map and the nation itself could never mean.

**Intent, never a write path.** The channel names ground and nothing else. It carries no
outcome, no odds, no schedule and no claim about anyone else, and it reaches no territory
column. What it does is:

``push``
    becomes a contest against whoever holds that ground -- the same channel the world
    simulator reports clashes through, resolved by the same seeded draw. The engine
    refuses one against a power it is not at war with, and one on ground the nation
    already holds. Enemy ground the front has not reached yet is *kept* and pressed
    when it becomes reachable, which is what lets one declaration drive a campaign
    through a province and into the one behind it.

``hold``
    becomes where that nation's reserves march, instead of the nearest frontier.

``withdraw_from``
    empties the ground of that nation's formations and abandons any push it was making
    from there.

**Standing until changed.** A staff that decided last month to take the Saar has not
stopped wanting it because it was not asked again this turn, and a channel that had to be
restated every turn would make silence mean surrender. Declaring anything for a region
replaces whatever that nation had declared for it before.

**Rewind-safe.** Objectives are written as diffs against a real table, so rewinding to
turn N restores what each staff was trying to do at turn N. Recomputing them on load
instead would replay the campaign with different orders and quietly land on a different
war.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field

from ..state.apply import apply_batch, record_event, unrecord_events
from ..state.diff import OBJECTIVE_LIMIT, Diff, DiffBatch
from ..state.models import DiffOp, EventKind, EventOrigin, Role, Visibility
from . import reference
from .alternatives import MOST, phrase
from .causality import Refusal
from .naval import amphibious_reach

__all__ = [
    "OBJECTIVE_KINDS",
    "pact_partners",
    "ObjectiveReport",
    "declare_objectives",
    "objective_id_for",
    "objectives_of",
    "prune_objectives",
    "pushes_as_contests",
]

#: The three things a staff can say about a piece of ground. Deliberately not more: each
#: one has to mean something mechanical, and a kind the engine cannot act on is flavour
#: the nation will waste its answer on.
OBJECTIVE_KINDS = ("push", "hold", "withdraw_from")


@dataclass(slots=True)
class ObjectiveReport:
    """What was accepted, and what was refused with a reason the nation can act on."""

    nation_id: str
    accepted: dict[str, tuple[str, ...]] = field(default_factory=dict)
    refused: list[str] = field(default_factory=list)
    #: Accepted, but not actionable yet: enemy ground the front has not reached. Kept
    #: apart from ``refused`` because the staff gets to keep wanting it -- and apart
    #: from silence because a staff that thinks its army is already there is exactly
    #: the confusion the refusal messages exist to prevent.
    deferred: list[str] = field(default_factory=list)
    #: Occupied ground handed back to its owner by a `withdraw_from` with nobody there to
    #: withdraw -- see :func:`_hand_back`. Empty unless ``ATLAS_VOLUNTARY_RETURN`` is on.
    returned: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(len(regions) for regions in self.accepted.values())


def objective_id_for(nation_id: str, region_id: str) -> str:
    """Derived, not autoincremented, so an objective survives a rewind with its identity."""
    return f"o_{nation_id}_{region_id}"


def objectives_of(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> dict[str, tuple[str, ...]]:
    """Everything ``nation_id`` currently intends, grouped by kind."""
    found: dict[str, list[str]] = {kind: [] for kind in OBJECTIVE_KINDS}
    for row in conn.execute(
        "SELECT kind, region_id FROM military_objective"
        " WHERE world_id = ? AND nation_id = ? ORDER BY kind, region_id",
        (world_id, nation_id),
    ):
        found.setdefault(row["kind"], []).append(row["region_id"])
    return {kind: tuple(regions) for kind, regions in found.items() if regions}


def declare_objectives(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    nation_id: str,
    declared: dict,
    turn: int,
    date: str,
) -> ObjectiveReport:
    """Check a power's declared objectives against the map, and record what survives.

    Every refusal is reported rather than dropped. A staff that named ground it cannot
    attack has made a mistake worth telling it about; silently ignoring it produces a
    nation that believes it is attacking somewhere it is not.

    Enemy ground that is merely *not reachable yet* is a third case, and it lands in
    :attr:`ObjectiveReport.deferred`: accepted, reported, and pressed as soon as the
    front arrives.
    """
    report = ObjectiveReport(nation_id=nation_id)
    if not declared:
        return report

    holdings = _held_by(conn, world_id, nation_id)
    at_war = _enemies_of(conn, world_id, nation_id)
    allies = pact_partners(conn, world_id, nation_id)
    # Ground across open water counts as within reach: an island is not somewhere a war
    # cannot go, it is somewhere a war needs ships to go. See atlas.engine.naval.
    reachable = _reachable_from(conn, world_id, holdings) | set(
        amphibious_reach(conn, world_id, nation_id)
    )
    holder = _holders(conn, world_id)
    oceans = {
        str(row["id"])
        for row in conn.execute(
            "SELECT id FROM region WHERE world_id = ? AND kind = 'ocean'", (world_id,)
        )
    }

    accepted: dict[str, list[str]] = {}
    seen: set[str] = set()

    for kind in OBJECTIVE_KINDS:
        raw = declared.get(kind) or ()
        if not isinstance(raw, (list, tuple)):
            report.refused.append(f"{kind} was not a list of region ids")
            continue
        if len(raw) > OBJECTIVE_LIMIT:
            report.refused.append(
                f"only the first {OBJECTIVE_LIMIT} of {len(raw)} {kind} objectives were kept"
            )
        for value in list(raw)[:OBJECTIVE_LIMIT]:
            # **What was written, resolved to what it names.** Briefings show ground as
            # `Northmark [r_north]`, and live agents write back `Northmark` -- which an
            # exact id lookup refuses, so a staff was told the place it had just been
            # shown is not on the map. The same resolver the order pipeline already uses
            # accepts either form; a genuinely unknown place still resolves to nothing and
            # is still refused below, by its original spelling so the message names what
            # the staff actually wrote.
            found = reference.resolve(conn, world_id, "region", value)
            if found.ambiguous:
                # **Ambiguity is a different answer from ignorance, and the useful one:**
                # the vocabulary was right and the phrase was not specific enough. Told
                # "there is no such region" a staff goes looking for a spelling mistake it
                # did not make; told the candidates, it knows what to write.
                report.refused.append(found.problem or f"{value} is unclear")
                continue
            region_id = found.value or str(value)
            if region_id in seen:
                report.refused.append(
                    f"{region_id} was named twice; an objective is one kind per region"
                )
                continue
            problem = _refusal(
                kind, region_id, holdings, reachable, holder, at_war, oceans, allies
            )
            if problem:
                report.refused.append(problem.rendered())
                continue
            if kind == "push" and region_id not in reachable:
                report.deferred.append(
                    f"{region_id} borders none of your territory yet; the objective "
                    "stands and your forces will press it once the front reaches it"
                )
            seen.add(region_id)
            accepted.setdefault(kind, []).append(region_id)

    report.accepted = {kind: tuple(sorted(regions)) for kind, regions in accepted.items()}

    # Everything refused means the staff still wants what it wanted. Replacing the
    # standing set with an empty one here would make a bad declaration a stand-down
    # order: France, having just lost the ground it declared it would hold, restated the
    # same plan, had every line of it refused, and came out of the turn with no
    # objectives at all -- which is not what it said and not what it meant.
    if not report.accepted and report.refused:
        return report

    diffs = _diffs_for(conn, world_id, nation_id, report.accepted, turn)
    if not diffs:
        return report

    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.ORDER_ISSUED, origin=EventOrigin.ENGINE, actor_nation_id=nation_id,
        text=_describe(conn, nation_id, report.accepted),
        # Public because an army concentrating is not a secret. What a staff *intends*
        # would be, but what this records is where the troops are going.
        visibility=Visibility.PUBLIC,
    )
    outcome = apply_batch(
        conn,
        DiffBatch(
            world_id=world_id, turn=turn, proposer_role=Role.ENGINE, proposer_model=None,
            diffs=tuple(
                Diff(d.target_table, d.target_id, d.field, d.op, d.new_value,
                     reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
                for d in diffs
            ),
        ),
    )
    if not outcome.applied:
        report.refused.append(f"the objectives could not be recorded: {outcome.reason}")
        report.accepted = {}
        return report
    if voluntary_return_enabled():
        for region_id in report.accepted.get("withdraw_from", ()):
            _hand_back(conn, world_id, nation_id, region_id, turn=turn, date=date,
                       report=report)
    return report


def _voluntary_return_from_env() -> bool:
    """``ATLAS_VOLUNTARY_RETURN=on`` lets an occupier hand back ground it has no troops on.

    **Off by default: it changes what a world does**, and the eras and cassettes were
    recorded without it. Read once at import; tests and study harnesses move it through
    :func:`set_voluntary_return`.
    """
    return os.environ.get("ATLAS_VOLUNTARY_RETURN", "off").strip().lower() in {
        "1", "on", "true", "yes"}


_VOLUNTARY_RETURN: bool = _voluntary_return_from_env()


def voluntary_return_enabled() -> bool:
    return _VOLUNTARY_RETURN


def set_voluntary_return(value: bool) -> bool:
    """Move the flag, returning what it was."""
    global _VOLUNTARY_RETURN
    was, _VOLUNTARY_RETURN = _VOLUNTARY_RETURN, bool(value)
    return was


def _hand_back(  # noqa: PLR0913 - a hand-back is named by all of these
    conn: sqlite3.Connection, world_id: str, nation_id: str, region_id: str, *,
    turn: int, date: str, report: ObjectiveReport,
) -> None:
    """Give occupied ground back to its owner when the occupier has nobody on it.

    **A withdrawal moves troops, and an occupation with no troops had no way to end.** Seen
    in betrayal-only run 2 (2026-09-22): Laos ordered its garrison out of North Vietnam's
    Thanh Hoa in thirteen consecutive turns, and nothing happened -- it had no soldier
    there, so `withdraw_from` had nothing to move, and control changed only when a revolt
    took the province at turn 14. The decision to give ground back was made every turn and
    had no door.

    Only for ground the nation *controls but does not own*, and only with **no land
    formation of its own** there: an occupier with troops present withdraws them the
    ordinary way, and the province stays contested until they have gone. Recorded as an
    engine-authored territory transfer, so the hand-back is causal history like any other.
    """
    row = conn.execute(
        "SELECT name, owner_nation_id AS owner, controller_nation_id AS controller"
        "  FROM region WHERE world_id = ? AND id = ?", (world_id, region_id)).fetchone()
    if row is None or row["controller"] != nation_id or not row["owner"]:
        return
    if row["owner"] == nation_id:
        return
    garrisoned = conn.execute(
        "SELECT COUNT(*) FROM unit u LEFT JOIN unit_template t"
        "    ON t.world_id = u.world_id AND t.kind = u.kind"
        " WHERE u.world_id = ? AND u.nation_id = ? AND u.region_id = ?"
        "   AND COALESCE(t.domain, 'land') = 'land'", (world_id, nation_id, region_id)
    ).fetchone()[0]
    if garrisoned:
        return
    names = {str(r["id"]): str(r["name"]) for r in conn.execute(
        "SELECT id, name FROM nation WHERE world_id = ? AND id IN (?, ?)",
        (world_id, nation_id, row["owner"]))}
    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.TERRITORY_TRANSFER, origin=EventOrigin.ENGINE,
        actor_nation_id=nation_id,
        text=(f"{names.get(nation_id, nation_id)} hands {row['name']} back to "
              f"{names.get(row['owner'], row['owner'])}, withdrawing an occupation it "
              "no longer holds with troops."),
        visibility=Visibility.PUBLIC,
        participants=(("actor", nation_id), ("target", str(row["owner"]))),
    )
    outcome = apply_batch(conn, DiffBatch(
        world_id=world_id, turn=turn, proposer_role=Role.ENGINE, proposer_model=None,
        diffs=(Diff("region", region_id, "controller_nation_id", DiffOp.SET,
                    str(row["owner"]), reason_event_id=event_id,
                    visibility_scope=Visibility.PUBLIC),)))
    if outcome.applied:
        report.returned.append(region_id)
    else:
        # As in `production.order_construction`: "hands back" was written so the diff could
        # cite it, and with the batch refused it is news of a withdrawal that did not happen.
        unrecord_events(conn, [event_id])
        conn.commit()
        report.refused.append(f"{region_id} could not be handed back: {outcome.reason}")


def pushes_as_contests(
    conn: sqlite3.Connection, world_id: str, *, turn: int
) -> list[dict]:
    """Every standing ``push`` that is still a war, as contests for the resolver.

    Re-derived each turn rather than remembered from the turn it was declared: ground
    changes hands, wars end, and an objective that was a battle last month may be a
    province this power already holds. The resolver receives these exactly as it receives
    the world simulator's clashes, and cannot tell them apart -- which is the point. A
    tracked power's war is fought under the same rules as everyone else's.
    """
    holder = _holders(conn, world_id)
    found: list[dict] = []
    for row in conn.execute(
        "SELECT nation_id, region_id FROM military_objective"
        " WHERE world_id = ? AND kind = 'push' ORDER BY nation_id, region_id",
        (world_id,),
    ):
        nation_id, region_id = row["nation_id"], row["region_id"]
        defender = holder.get(region_id)
        if not defender or defender == nation_id:
            continue
        if defender not in _enemies_of(conn, world_id, nation_id):
            continue
        holdings = _held_by(conn, world_id, nation_id)
        reachable = _reachable_from(conn, world_id, holdings) | set(
            amphibious_reach(conn, world_id, nation_id)
        )
        if region_id not in reachable:
            continue
        found.append(
            {
                "kind": "battle",
                "attacker": nation_id,
                "defender": defender,
                "region_id": region_id,
                "flags": [],
                "reason_event_id": None,
            }
        )
    return found


# --------------------------------------------------------------------------- internals



def prune_objectives(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str
) -> list[str]:
    """Drop every standing objective the map has overtaken. Returns what was dropped.

    **An objective is a statement about ground, and ground moves.** A `hold` on a province
    that has been taken from you is not an order any longer, and a `push` into one you now
    own is an order that has already succeeded. Left standing, they are shown back to the
    staff in its next briefing, faithfully restated, and refused -- every turn, for as long
    as the war lasts.

    PT20 produced both halves in one turn. Ukraine retook Sumy; Russia's `hold` on it and
    Ukraine's `push` into it were both refused the following month, neither through any
    fault of the model. They had been declared against a map that then changed, and nothing
    told either staff so.

    Run after resolution, when the map has settled for the turn, and written as diffs so a
    rewind puts the objectives back exactly as they stood.
    """
    standing = list(conn.execute(
        "SELECT nation_id, region_id, kind FROM military_objective"
        " WHERE world_id = ? ORDER BY nation_id, region_id",
        (world_id,),
    ))
    if not standing:
        return []

    holder = _holders(conn, world_id)
    partners: dict[str, set[str]] = {}
    stale: list[tuple[str, str, str, str]] = []
    for row in standing:
        nation_id, region_id, kind = row["nation_id"], row["region_id"], row["kind"]
        held = holder.get(region_id) == nation_id
        if kind == "hold" and not held:
            # **An ally's ground is still held.** `declare_objectives` accepts a `hold`
            # over a pact partner's province -- that is how a patron defends an ally --
            # so the same test decides when it lapses, or it is dropped the turn after
            # it is declared with a false "no longer holds".
            if nation_id not in partners:
                partners[nation_id] = pact_partners(conn, world_id, nation_id)
            held = holder.get(region_id) in partners[nation_id]
        if kind == "push" and held:
            stale.append((nation_id, region_id, kind, "taken"))
        elif kind != "push" and not held:
            stale.append((nation_id, region_id, kind, "lost"))
    if not stale:
        return []

    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.ORDER_ISSUED, origin=EventOrigin.ENGINE,
        text=(
            f"{len(stale)} standing objective(s) were overtaken by events and lapsed."
        ),
        visibility=Visibility.PUBLIC,
    )
    outcome = apply_batch(
        conn,
        DiffBatch(
            world_id=world_id, turn=turn, proposer_role=Role.ENGINE, proposer_model=None,
            diffs=tuple(
                Diff(
                    "military_objective", objective_id_for(nation_id, region_id),
                    "__delete__", DiffOp.DELETE, None,
                    reason_event_id=event_id, visibility_scope=Visibility.PUBLIC,
                )
                for nation_id, region_id, _kind, _why in stale
            ),
        ),
    )
    if not outcome.applied:
        return []
    return [
        f"{nation_id} no longer {'holds' if why == 'lost' else 'needs'} {region_id}; "
        f"its {kind} objective there has lapsed"
        for nation_id, region_id, kind, why in stale
    ]


def _refusal(  # noqa: PLR0913 - one call site, and every set is a separate question
    kind: str,
    region_id: str,
    holdings: set[str],
    reachable: set[str],
    holder: dict[str, str | None],
    at_war: set[str],
    oceans: set[str],
    allies: frozenset[str] | set[str] = frozenset(),
) -> Refusal | None:
    """``None`` if the objective stands, otherwise why not and what to declare instead.

    **A refusal that only says no is a refusal the staff will make again next turn.** Every
    branch here names the gate it failed and, where there is a legal equivalent, says what
    to write -- a patron told "you are not at war with whoever holds r_south" went looking
    for a war to declare on its own ally.
    """
    if region_id not in holder:
        return Refusal(
            "known_region", f"{region_id} is not a region on this map.",
            # `reachable` is the set this function already gates on two branches below.
            phrase(sorted(reachable)[:MOST],
                   lead="ground within reach of yours includes"))
    if region_id in oceans:
        # **A push into open water was accepted, stored, and did nothing.** No sea has a
        # holder, so the "you are not at war with whoever holds it" test below passed
        # vacuously; the objective went into `military_objective`, was restated in every
        # briefing afterwards, and `pushes_as_contests` dropped it every round because a
        # region nobody holds has no defender. A staff could order the North Sea taken
        # for the rest of the game and never be told otherwise.
        #
        # Refused with a reason rather than made to work: sea control today follows from
        # where the fleets are, and nothing anywhere lets an *order* open a contest
        # against unheld water. Building that is a design decision about whether sea
        # control becomes player-orderable at all, which is Alex's to make; saying plainly
        # that it is not orderable today is not.
        return Refusal(
            "over_ground",
            f"{region_id} is open sea; objectives are declared over ground.",
            "sea control follows from where your fleet is -- name the coast you want, and "
            "your ships will contest the water on the way to it.",
        )
    if kind == "push":
        if region_id in holdings:
            return Refusal(
                "not_already_yours",
                f"you already hold {region_id}; there is nothing there to push into.",
                f"declare `hold` over {region_id} if you mean to defend it.",
            )
        occupant = holder.get(region_id)
        if occupant in allies:
            return Refusal(
                "not_an_allys_ground",
                f"{region_id} is held by {occupant}, who you are sworn to defend by an "
                "active defensive pact -- you cannot seize what you are guaranteeing.",
                f"declare `hold` over {region_id}: that is the defend objective, and it "
                "marches your reserves toward it.",
            )
        if occupant and occupant not in at_war:
            return Refusal(
                "at_war",
                f"you are not at war with {occupant}, who holds {region_id}.",
                f"declare war on {occupant} first; an objective cannot open a war.",
            )
        if region_id not in reachable:
            # **Kept, not refused, when it is enemy ground.** Reachability is checked
            # again every round by `pushes_as_contests`, so a province two deep into a
            # country this power is already fighting is a perfectly good standing
            # objective -- it simply waits for the front to arrive. Refusing it made a
            # long jump strictly worse at prosecuting a war than twelve short ones: a
            # staff that said "take Ukraine" once a year got the border provinces and
            # then stood still, because the ground behind them had been thrown away at
            # declaration time and there was no second call in which to name it.
            #
            # Ground held by nobody this power is fighting is still refused above, so
            # "attack somewhere unreachable and neutral" is as wrong as it ever was.
            return None
    elif kind == "hold" and holder.get(region_id) in allies:
        # **Defending an ally is holding its ground, not taking it.** A patron had no way
        # to say this at all: `push` is refused because it is not at war with the ally, and
        # `hold` was refused because it does not hold the ground -- so the only sentence
        # available for "reinforce Poland" was one the engine would turn down. Every order
        # a patron spent on its client's territory was wasted.
        #
        # No new kind and no widened CHECK: `hold` already means *this is where my reserves
        # march*, and marching toward an ally under attack is exactly what it should mean.
        # What it cannot mean is garrisoning ground somebody else holds -- see
        # `resolution.reserve_destinations`, which masses the army on this nation's own side
        # of the border, which is what a relief force actually does.
        return None
    elif region_id not in holdings:
        occupant = holder.get(region_id)
        return Refusal(
            "you_hold_it",
            f"you do not hold {region_id}, so you cannot {kind.replace('_', ' ')} it.",
            (f"declare `push` on it -- you are at war with {occupant}."
             if occupant in at_war else
             f"a `hold` is only open over your own ground or an ally's, and {region_id} "
             "is neither."),
        )
    return None


def _diffs_for(
    conn: sqlite3.Connection,
    world_id: str,
    nation_id: str,
    accepted: dict[str, tuple[str, ...]],
    turn: int,
) -> list[Diff]:
    """Replace this nation's objectives with the accepted set, as insert and delete diffs.

    Declaring nothing for a region it previously named is how a staff drops an objective,
    so the standing set is replaced wholesale rather than added to. A row whose kind is
    unchanged is left alone: rewriting it would churn the diff log every turn and make a
    war look busier on paper than it was.
    """
    current = {
        row["region_id"]: row["kind"]
        for row in conn.execute(
            "SELECT region_id, kind FROM military_objective"
            " WHERE world_id = ? AND nation_id = ?",
            (world_id, nation_id),
        )
    }
    wanted = {
        region_id: kind for kind, regions in accepted.items() for region_id in regions
    }

    diffs: list[Diff] = []
    for region_id in sorted(set(current) - set(wanted)):
        diffs.append(
            Diff("military_objective", objective_id_for(nation_id, region_id),
                 "__delete__", DiffOp.DELETE, None)
        )
    for region_id in sorted(wanted):
        kind = wanted[region_id]
        if current.get(region_id) == kind:
            continue
        objective_id = objective_id_for(nation_id, region_id)
        if region_id in current:
            diffs.append(
                Diff("military_objective", objective_id, "kind", DiffOp.SET, kind)
            )
        else:
            diffs.append(
                Diff(
                    "military_objective", objective_id, "__insert__", DiffOp.INSERT,
                    {
                        "world_id": world_id,
                        "nation_id": nation_id,
                        "region_id": region_id,
                        "kind": kind,
                        "declared_turn": turn,
                    },
                )
            )
    return diffs


def _describe(
    conn: sqlite3.Connection, nation_id: str, accepted: dict[str, tuple[str, ...]]
) -> str:
    row = conn.execute("SELECT name FROM nation WHERE id = ?", (nation_id,)).fetchone()
    name = row["name"] if row else nation_id
    if not accepted:
        return f"{name} set no military objectives."

    names = {
        r["id"]: r["name"]
        for r in conn.execute("SELECT id, name FROM region")
    }

    def listing(kind: str) -> str:
        return ", ".join(names.get(r, r) for r in accepted.get(kind, ()))

    parts = []
    if accepted.get("push"):
        parts.append(f"pushing into {listing('push')}")
    if accepted.get("hold"):
        parts.append(f"holding {listing('hold')}")
    if accepted.get("withdraw_from"):
        parts.append(f"withdrawing from {listing('withdraw_from')}")
    return f"{name}'s staff set its objectives: {'; '.join(parts)}."


def _held_by(conn: sqlite3.Connection, world_id: str, nation_id: str) -> set[str]:
    """The **ground** this nation holds. See `resolution._held_by` for the other half.

    Holdings are what `_reachable_from` walks out of, so a sea counted as a holding made
    every coast it touches reachable by land -- an army crossing open water on foot, with
    no shipping and no lift cap.
    """
    return {
        row["id"]
        for row in conn.execute(
            "SELECT id FROM region WHERE world_id = ? AND kind <> 'ocean'"
            " AND COALESCE(controller_nation_id, owner_nation_id) = ?",
            (world_id, nation_id),
        )
    }


def _holders(conn: sqlite3.Connection, world_id: str) -> dict[str, str | None]:
    return {
        row["id"]: row["holder"]
        for row in conn.execute(
            "SELECT id, COALESCE(controller_nation_id, owner_nation_id) AS holder"
            "  FROM region WHERE world_id = ?",
            (world_id,),
        )
    }


def pact_partners(conn: sqlite3.Connection, world_id: str, nation_id: str) -> set[str]:
    """Everyone this nation is in an active defensive pact with, and their dependencies.

    **The ground a patron cannot take and is supposed to defend.** Poland's ground is not
    Germany's to seize, and neither is the ground Poland holds without owning -- a colony,
    a mandate, an occupied province. `COALESCE(controller, owner)` is what decides who a
    region answers to everywhere else in the engine, so it decides it here too.

    Dependencies matter more than they look: 1936's British Empire is ninety regions that
    Britain holds and no ally would ever be right to march into, and every one of them
    reads as "held by an ally" only through this coalesce.
    """
    return {
        str(row["ally"])
        for row in conn.execute(
            """
            SELECT DISTINCT other.nation_id AS ally
              FROM commitment c
              JOIN commitment_nation mine  ON mine.commitment_id = c.id
              JOIN commitment_nation other ON other.commitment_id = c.id
              JOIN nation n ON n.id = other.nation_id
             WHERE c.world_id = ? AND c.kind = 'defensive_pact' AND c.status = 'active'
               AND mine.nation_id = ? AND other.nation_id <> ? AND n.is_alive = 1
            """,
            (world_id, nation_id, nation_id),
        )
    }


def _enemies_of(conn: sqlite3.Connection, world_id: str, nation_id: str) -> set[str]:
    return {
        row["nation_b"]
        for row in conn.execute(
            "SELECT nation_b FROM relation WHERE world_id = ? AND nation_a = ? AND at_war = 1",
            (world_id, nation_id),
        )
    }


def _reachable_from(
    conn: sqlite3.Connection, world_id: str, holdings: set[str]
) -> set[str]:
    """Ground bordering anything the nation holds. An army marches; it does not appear.

    **Ground.** An ocean beside a coast this power holds is not somewhere an army can be
    sent, and returning one made an open-sea `push` a legal objective that then sat inert:
    accepted, restated in every briefing, and never resolvable into anything.
    """
    if not holdings:
        return set()
    oceans = {
        str(row["id"])
        for row in conn.execute(
            "SELECT id FROM region WHERE world_id = ? AND kind = 'ocean'", (world_id,)
        )
    }
    found: set[str] = set()
    for row in conn.execute(
        "SELECT region_a, region_b FROM region_adjacency WHERE world_id = ?", (world_id,)
    ):
        a, b = row["region_a"], row["region_b"]
        if a in holdings:
            found.add(b)
        if b in holdings:
            found.add(a)
    return found - holdings - oceans
