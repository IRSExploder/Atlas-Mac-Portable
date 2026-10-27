"""An ally that does not come, and what that costs.

**Article 5 is prompted rather than automatic, and that was right and incomplete.**
:func:`atlas.engine.resolution._invoke_defensive_pacts` is emphatic about the first half: a
hard auto-join would drag five powers into a war nobody chose, so attacking a pact member
writes each ally an engine-authored casus belli -- which makes entering the war legitimate
and free -- and collapses the allies' regard for the aggressor. The decision to actually
fight stays with each power.

What was missing is the other side of a promise. **Nothing at all happened to an ally that
held a pact and stayed home.** The call was free to make and free to ignore, so a defensive
pact was a benefit with no obligation attached, and "collective security" could not mean
anything mechanically because there was nothing to be reliable *about*.

So an unanswered call is now a thing that happened:

* **The window is world time.** :data:`ANSWER_MONTHS` from the day the pact was invoked, so
  a country has the same season to make up its mind whether the player is watching a month
  or a year at a time -- the rule armistices, sanctions and doctrine shifts already keep.
* **It is derivable, not asserted.** The invocation is an engine-authored `casus_belli`
  event naming the ally, the aggressor and the victim; whether the ally came is
  `relation.at_war`. **No model can claim it answered when it did not, or hide that it
  did not come** -- the same discipline `treaties.assess_breaches` uses to detect a breach.
* **It is charged once.** The charge event cites the call it answers, so a call that has
  been settled is one that has an effect citing it. No schema, no flag, and a rewind takes
  the charge back with the turn that made it.

**What it costs is regard with the injured party and legitimacy at home**, and both are
deliberately smaller than tearing up the signature outright: not coming when called is a
lesser thing than repudiating the pact, and a country that does it can still be argued
with. The pact itself is left standing -- ending it is the injured party's decision to
make, and the engine does not make it for them.

Scaled by the ally's own alignment doctrine, if it has one. A power built on collective
security has told everybody its promises are real and is judged accordingly; an armed
neutral never pretended otherwise.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..state.apply import apply_batch, record_event
from ..state.diff import Diff, DiffBatch
from ..state.models import DiffOp, EventKind, EventOrigin, Role, Visibility
from .bounds import absorbable
from .pacing import days_between

__all__ = [
    "ANSWER_MONTHS",
    "LEGITIMACY_COST",
    "REGARD_COST",
    "REGARD_FOR_ANSWERING",
    "UnansweredCall",
    "assess_unanswered_calls",
    "outstanding_calls",
]

#: **Balance number, tunable.** How long an ally has to decide, in world time. Long enough
#: that a government has to actually refuse rather than merely be slow, and short enough
#: that a war is still going on when the answer is due.
ANSWER_MONTHS = 3.0

#: **Balance number, tunable.** What the injured party thinks of an ally that did not come.
#: Smaller than the -35 for tearing up the agreement outright: not coming is a lesser thing
#: than repudiating, and it leaves something to argue about.
REGARD_COST = -18.0

#: ...and at home. Smaller again, for the reason `treaties._LEGITIMACY_COST` is smaller than
#: its regard cost: the domestic audience is not the injured party.
LEGITIMACY_COST = -3.0

#: **Balance number, tunable.** What the injured party thinks of an ally that did come.
#: Smaller than what not coming costs, deliberately: a promise kept is expected and a
#: promise broken is news, and a mechanic that paid more for turning up than it charged for
#: staying home would make signing pacts a way to farm regard.
REGARD_FOR_ANSWERING = 12.0

#: How many days count as the window. `after_months` is not used because the comparison is
#: "has enough time passed", not "what date does this fall on".
_DAYS_PER_MONTH = 30.44


@dataclass(frozen=True, slots=True)
class UnansweredCall:
    """One ally that was called and has not come."""

    event_id: int
    ally: str
    aggressor: str
    victim: str
    called_on: str
    days_waiting: float


@dataclass
class AnswerReport:
    """What the turn made of everybody's promises."""

    #: One line per ally charged this turn.
    unanswered: list[str] = field(default_factory=list)
    #: One line per ally that came, for the report -- keeping a promise should be visible.
    answered: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def outstanding_calls(
    conn: sqlite3.Connection, world_id: str, date: str
) -> list[UnansweredCall]:
    """Every pact invocation still waiting for an answer, oldest first.

    A call is outstanding while nothing cites it as settled. The engine writes exactly one
    settling event per call -- either *they came* or *they did not* -- so this is the whole
    of the bookkeeping and none of it is stored.
    """
    try:
        rows = conn.execute(
            """
            SELECT e.id AS event_id, e.date AS called_on,
                   ally.nation_id     AS ally,
                   against.nation_id  AS aggressor,
                   hurt.nation_id     AS victim
              FROM event e
              JOIN event_participant ally
                ON ally.event_id = e.id AND ally.role = 'actor'
              JOIN event_participant against
                ON against.event_id = e.id AND against.role = 'target'
              JOIN event_participant hurt
                ON hurt.event_id = e.id AND hurt.role = 'aggrieved'
             WHERE e.world_id = ? AND e.kind = 'casus_belli' AND e.origin = 'ENGINE'
               -- **Settled means something cites it.** The engine writes exactly one
               -- narrative per call, by the ally it was made to, saying either that they
               -- came or that they did not -- so a call with such an event behind it has
               -- been answered for, once. No flag, no column, and a rewind takes the
               -- settlement back with the turn that wrote it.
               AND NOT EXISTS (
                     SELECT 1 FROM event_cause c JOIN event settled ON settled.id = c.event_id
                      WHERE c.cause_id = e.id
                        AND settled.kind = 'narrative' AND settled.origin = 'ENGINE'
                        AND settled.actor_nation_id = ally.nation_id)
             ORDER BY e.id
            """,
            (world_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []

    out: list[UnansweredCall] = []
    for row in rows:
        waited = days_between(str(row["called_on"] or ""), date)
        # `None` means a date nobody can read, which is not the same as no time having
        # passed: an unreadable call waits rather than being settled by accident.
        if waited is None:
            continue
        out.append(UnansweredCall(
            event_id=int(row["event_id"]), ally=str(row["ally"]),
            aggressor=str(row["aggressor"]), victim=str(row["victim"]),
            called_on=str(row["called_on"]), days_waiting=waited))
    return out


#: What `relation.score` may hold. A SQLite CHECK rather than a rule, so overshooting it is
#: an `IntegrityError` that takes the whole batch rather than a severed diff -- a different
#: failure from the bounded nation columns and a worse one, because nothing is written at all.
_REGARD_RANGE = (-100.0, 100.0)


def _regard_room(
    conn: sqlite3.Connection, world_id: str, of: str, toward: str, amount: float
) -> float:
    """As much of ``amount`` as this relation can take without leaving its range.

    `bounds.absorbable` covers the nation columns and is keyed on a single `id`; a relation
    is keyed on a pair, so this is the same idea for the one column that needs it.
    """
    row = conn.execute(
        "SELECT score FROM relation WHERE world_id = ? AND nation_a = ? AND nation_b = ?",
        (world_id, of, toward)).fetchone()
    if row is None:
        return round(amount, 2)
    low, high = _REGARD_RANGE
    current = float(row["score"] or 0.0)
    if amount < 0:
        return -round(min(-amount, max(0.0, current - low)), 2)
    return round(min(amount, max(0.0, high - current)), 2)


def _at_war(conn: sqlite3.Connection, world_id: str, a: str, b: str) -> bool:
    row = conn.execute(
        "SELECT at_war FROM relation WHERE world_id = ? AND nation_a = ? AND nation_b = ?",
        (world_id, a, b)).fetchone()
    return bool(row and int(row["at_war"] or 0))


def assess_unanswered_calls(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str
) -> AnswerReport:
    """Settle every call whose window has run out. Charges the no-shows, credits the rest.

    Run after the fighting resolves, so an ally that entered the war *this* turn counts as
    having come -- which is the generous reading and the correct one: it did come.
    """
    from .doctrines import factor

    report = AnswerReport()
    calls = outstanding_calls(conn, world_id, date)
    if not calls:
        return report

    names = {str(r["id"]): str(r["name"]) for r in conn.execute(
        "SELECT id, name FROM nation WHERE world_id = ?", (world_id,))}
    diffs: list[Diff] = []

    for call in calls:
        came = _at_war(conn, world_id, call.ally, call.aggressor)
        if not came and call.days_waiting < ANSWER_MONTHS * _DAYS_PER_MONTH:
            continue  # still thinking, and entitled to

        ally = names.get(call.ally, call.ally)
        victim = names.get(call.victim, call.victim)
        if came:
            # **Coming is worth something, and it was not before.** An ally that honoured a
            # pact got exactly what an ally that ignored one got, which is why collective
            # security had nothing to be built on. Scaled by the alignment doctrine for the
            # same reason the charge is: a power that says its promises are real is
            # believed more when it keeps one.
            credit = REGARD_FOR_ANSWERING * factor(
                conn, world_id, call.ally, "alignment", "regard_for_answering")
            event_id = record_event(
                conn, world_id=world_id, turn=turn, date=date,
                kind=EventKind.NARRATIVE, origin=EventOrigin.ENGINE,
                actor_nation_id=call.ally,
                text=f"{ally} answered the call and stood with {victim}."[:600],
                visibility=Visibility.PUBLIC, causes=(call.event_id,),
                participants=(("actor", call.ally), ("beneficiary", call.victim)))
            diffs.append(Diff("relation", f"{call.victim}|{call.ally}", "score",
                              DiffOp.ADJUST,
                              _regard_room(conn, world_id, call.victim, call.ally, credit),
                              reason_event_id=event_id, visibility_scope=Visibility.PUBLIC))
            report.answered.append(f"{ally} answered the call for {victim}")
            continue

        # **Scaled by what this country said it was.** A power built on collective security
        # has been telling everybody its promises are real; an armed neutral never
        # pretended otherwise, and is judged less harshly for the same silence.
        weight = factor(conn, world_id, call.ally, "alignment", "unanswered_call")
        event_id = record_event(
            conn, world_id=world_id, turn=turn, date=date,
            kind=EventKind.NARRATIVE, origin=EventOrigin.ENGINE,
            actor_nation_id=call.ally,
            text=(f"{ally} did not answer the call. {victim} was attacked and its ally "
                  f"stayed home.")[:600],
            visibility=Visibility.PUBLIC, causes=(call.event_id,),
            participants=(("actor", call.ally), ("aggrieved", call.victim)))
        # **Both charges are bounded, and an unbounded one is a severable one.** An ally
        # already at nothing had its legitimacy charge severed while the regard charge and
        # the settling event went through, so the penalty half-happened and reported
        # success. Same shape as the doctrine shift, same fix: charge what there is.
        diffs.append(Diff("relation", f"{call.victim}|{call.ally}", "score", DiffOp.ADJUST,
                          _regard_room(conn, world_id, call.victim, call.ally,
                                       REGARD_COST * weight),
                          reason_event_id=event_id, visibility_scope=Visibility.PUBLIC))
        diffs.append(Diff("nation", call.ally, "legitimacy", DiffOp.ADJUST,
                          absorbable(conn, "nation", call.ally, "legitimacy",
                                     round(LEGITIMACY_COST * weight, 2)),
                          reason_event_id=event_id, visibility_scope=Visibility.PUBLIC))
        report.unanswered.append(
            f"{ally} did not come when {victim} was attacked")

    if diffs:
        outcome = apply_batch(conn, DiffBatch(
            world_id=world_id, turn=turn, proposer_role=Role.ENGINE, proposer_model=None,
            diffs=tuple(diffs)))
        if not outcome.applied:
            report.problems.append(
                f"an unanswered call could not be charged: {outcome.reason}")
    return report
