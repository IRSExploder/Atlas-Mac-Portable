"""Brokering somebody else's war.

**Every way a war could end went through one of its own belligerents.** A power outside a
war could send it equipment, take a side in it, or watch it; it had no sentence that meant
*stop, and here are the terms*. That is the marquee thing a power leading the international
order does, and the concert route was built without it -- so the only diplomatic route to
victory was to be liked, and never to be useful.

Three claims this layer has to make good on, and each one is a way it could have gone wrong:

* **A broker is not a belligerent.** A power at war with either side is a party to the
  fight, and its "mediation" is a demand. Refused structurally, not scored down.
* **It has to succeed where a bilateral offer would fail**, or it is a longer way of saying
  something the layer could already say and no power would ever choose it. Good offices --
  see :data:`GOOD_OFFICES_MAX` -- widen what both sides will bear, scaled by how far the
  broker actually stands above the field. A nobody's mediation lands only on wars that were
  going to end anyway; a genuine leader's can end one that was not.
* **And it cannot decide the war.** Both belligerents run the ordinary
  :func:`atlas.engine.peace.acceptance` test, in both directions -- which is *stricter* than
  a bilateral peace, where only the side being offered to has to agree. A broker persuades;
  it does not sign for anybody.

What it earns is the point: a successful mediation is the one thing in the game that feeds
the leadership index without being a promise kept or a seat inherited from the scenario.
See :data:`atlas.engine.concert.MEDIATION_WEIGHT`.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..state.apply import record_event
from ..state.models import EventKind, EventOrigin, Visibility
from .pacing import days_between

__all__ = [
    "GOOD_OFFICES_MAX",
    "HEARD_ABOVE",
    "MEDIATION_COOLDOWN_DAYS",
    "STANDING_TO_BE_HEARD",
    "Brokerage",
    "Verdict",
    "brokerable_wars",
    "may_broker",
    "mediate",
    "mediate_war",
    "mediations_by",
]

#: How far above the field's median leadership a power must stand before anybody at war
#: will hear it out.
#:
#: **Relative, and it has to be**: the index is built on commitment-*turns*, so it climbs
#: all game for everybody. A fixed bar of 0.5 refuses every power on turn one and admits
#: every power by turn forty, which is a statement about the calendar rather than about who
#: leads. Measured on the shipped cold-war map at turn one: the field sits at 0.25 and the
#: great powers at 0.40, so an edge of 0.05 admits the powers anybody would actually ask and
#: refuses the other hundred and forty.
STANDING_TO_BE_HEARD = 0.05

#: Regard below which a belligerent will not sit at a table this power set. Not a demand to
#: be *liked* -- the shipped map has almost every relation at zero and mediation between
#: strangers is ordinary -- but a power you regard at -40 is not an honest broker, it is
#: your enemy's friend.
HEARD_ABOVE = -20.0

#: The most good offices can add to what a belligerent will bear, and what it is scaled by.
#:
#: **This is the whole reason the action exists.** Without it a mediated peace is exactly a
#: bilateral peace with an extra name on it, so it could only ever end wars that were about
#: to end, and no power would spend an answer on it. With it, a broker standing well clear
#: of the field can carry a settlement across a gap of a quarter -- against
#: `peace.TOLERANCE_FLOOR` of 0.08 that is substantial, and it is still nothing like
#: decisive: a power in a commanding position expects concessions on the
#: `EXPECTATION_FROM_LEVERAGE` scale, and no amount of good offices talks it out of a war it
#: is winning.
#:
#: Scaled by the edge over the field rather than by the raw index, for the reason the gate
#: above is relative.
GOOD_OFFICES_MAX = 0.25
GOOD_OFFICES_PER_EDGE = 0.80

#: How long before the same power may try the same war again. A refused mediation is a
#: refusal, not an invitation to ask every month until the dice come up -- which is what an
#: uncooled channel would become the moment anything automated it.
#:
#: Days rather than turns, like every other duration in the engine: eight turns is eight
#: months at a monthly pace and eight years at a yearly one, and the clock registry is right
#: to refuse that.
MEDIATION_COOLDOWN_DAYS = 365.0

#: What both sides think of the broker afterwards, and where it settles. Deliberately much
#: smaller than what leading the order pays: this is one act, not a record.
GRATITUDE = 6.0
GRATITUDE_CEILING = 60.0


@dataclass
class Brokerage:
    """What happened when a power offered to broker a war."""

    signed: bool = False
    refused: list[str] = field(default_factory=list)
    applied: list[str] = field(default_factory=list)
    commitment_id: int | None = None
    #: What each side said, so a refusal can be read rather than guessed at.
    verdicts: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.signed


@dataclass(frozen=True, slots=True)
class Verdict:
    """Whether this power may broker this war at all, and why not."""

    allowed: bool
    because: str = ""
    #: How far above the field it stands, which is what good offices are worth.
    edge: float = 0.0

    @property
    def good_offices(self) -> float:
        return min(GOOD_OFFICES_MAX, max(0.0, self.edge) * GOOD_OFFICES_PER_EDGE)


def may_broker(
    conn: sqlite3.Connection, world_id: str, *, mediator: str, between: tuple[str, str],
    date: str, turn: int,
) -> Verdict:
    """Whether ``mediator`` may broker the war between these two, and what it is worth.

    Read-only and draws no RNG, so a briefing can offer exactly what the engine will accept
    -- an undisclosed bound costs a whole proposal, which is the lesson this codebase keeps
    relearning.
    """
    from .concert import leadership_index

    a, b = between
    if a == b:
        return Verdict(False, "a power is not at war with itself")
    if mediator in (a, b):
        return Verdict(
            False, "you are in this war; ending it is a peace offer, not a mediation")
    if not _at_war(conn, world_id, a, b):
        return Verdict(False, f"{a} and {b} are not at war")
    for side in (a, b):
        if _at_war(conn, world_id, mediator, side):
            return Verdict(
                False,
                f"you are at war with {side}; a belligerent's terms are a demand, not "
                "mediation")

    for side in (a, b):
        regard = _regard(conn, world_id, side, mediator)
        if regard < HEARD_ABOVE:
            return Verdict(
                False,
                f"{side} regards you at {regard:.0f} and will not sit at a table you set")

    last = _last_attempt(conn, world_id, mediator=mediator, between=(a, b))
    if last and days_between(last, date) < MEDIATION_COOLDOWN_DAYS:
        return Verdict(
            False,
            f"you last put terms to this war on {last}; leave it a year before trying again")

    edge = (leadership_index(conn, world_id, mediator, date=date, turn=turn)
            - _field_median(conn, world_id, date=date, turn=turn))
    if edge < STANDING_TO_BE_HEARD:
        return Verdict(
            False,
            f"you stand {edge:+.2f} against an ordinary power on the international order; "
            f"{STANDING_TO_BE_HEARD:.2f} is what it takes to be heard at somebody else's "
            "war",
            edge)
    return Verdict(True, "", edge)


def brokerable_wars(
    conn: sqlite3.Connection, world_id: str, *, mediator: str, date: str, turn: int,
    limit: int = 5,
) -> list[tuple[str, str, float]]:
    """The wars this power could offer to broker, worst first, with what it brings to each.

    Ordered by how much blood is in them, because that is the one a leader should be asked
    about first and because a list in id order is a list nobody reads to the bottom of.
    """
    seen: set[tuple[str, ...]] = set()
    out: list[tuple[str, str, float]] = []
    for row in conn.execute(
        "SELECT nation_a AS a, nation_b AS b FROM relation"
        " WHERE world_id = ? AND at_war = 1 ORDER BY nation_a, nation_b", (world_id,)):
        pair = tuple(sorted((str(row["a"]), str(row["b"]))))
        if pair in seen:
            continue
        seen.add(pair)
        verdict = may_broker(conn, world_id, mediator=mediator,
                             between=(pair[0], pair[1]), date=date, turn=turn)
        if verdict.allowed:
            out.append((pair[0], pair[1], verdict.good_offices))
    out.sort(key=lambda entry: (-_blood(conn, world_id, entry[0], entry[1]),
                                entry[0], entry[1]))
    return out[:limit]


def mediate(  # noqa: PLR0913 - a brokerage is its broker, its parties, its terms, its clock
    conn: sqlite3.Connection,
    world_id: str,
    *,
    mediator: str,
    between: tuple[str, str],
    terms,
    turn: int,
    date: str,
) -> Brokerage:
    """Put terms to somebody else's war, and settle it if both sides will sign.

    **Both sides, in both directions.** A bilateral offer asks one power whether it accepts
    what the other proposed; a mediation has no proposer among the belligerents, so each is
    asked whether it would take this from the other. That is a higher bar on purpose, and it
    is what stops a broker handing one side a victory it had not won.
    """
    from .peace import acceptance, settle

    out = Brokerage()
    a, b = between
    verdict = may_broker(conn, world_id, mediator=mediator, between=(a, b),
                         date=date, turn=turn)
    if not verdict.allowed:
        out.refused.append(verdict.because)
        return out

    offices = verdict.good_offices
    for side, other in ((a, b), (b, a)):
        said = acceptance(conn, world_id, offered_to=side, offered_by=other,
                          terms=terms, good_offices=offices)
        out.verdicts[side] = said.because
        if not said.accepted:
            out.refused.append(f"{side} will not sign: {said.because}")

    # **The attempt is recorded whether or not it lands**, which is what the cooldown reads
    # and what stops a refused mediation being re-offered every month until the war moves
    # under it. A refusal is public: a power that tried to end a war and was rebuffed is a
    # thing the world saw.
    if out.refused:
        record_event(
            conn, world_id=world_id, turn=turn, date=date, kind=EventKind.ENVOY_DISPATCHED,
            origin=EventOrigin.ENGINE, actor_nation_id=mediator,
            text=f"{mediator} put terms to the war between {a} and {b}, and was refused",
            visibility=Visibility.PUBLIC,
            participants=(("mediator", mediator), ("actor", a), ("target", b)))
        return out

    # Both have already agreed, so the settlement itself does not ask again -- asking twice
    # would let a term that passed the mediation test fail the bilateral one and leave the
    # war open with the mediation recorded, which is a state nothing could explain.
    settled = settle(conn, world_id, proposer=a, target=b, terms=terms,
                     turn=turn, date=date, check_acceptance=False, mediator=mediator)
    out.refused.extend(settled.refused)
    out.applied.extend(settled.applied)
    out.commitment_id = settled.commitment_id
    out.signed = settled.signed
    if settled.signed and settled.event_id is not None:
        _thank(conn, world_id, mediator=mediator, between=(a, b),
               event_id=settled.event_id, turn=turn)
    return out


def mediate_war(
    conn: sqlite3.Connection, world_id: str, *, nation_id: str, declared,
    turn: int, date: str,
) -> Brokerage:
    """A power's offer to broker, as its own answer wrote it.

    Intent, checked and then written, exactly like the peace-offer and war-aims channels:
    names are resolved the way every other channel resolves them, the engine reviews every
    term, both belligerents decide for themselves, and a refusal comes back with a reason
    the power is told.
    """
    from . import reference
    from .peace import TERM_KINDS, ProposedTerm, terms_from

    out = Brokerage()
    if not isinstance(declared, dict) or not declared:
        return out

    named = declared.get("between") or []
    if not isinstance(named, (list, tuple)) or len(named) != 2:
        out.refused.append("a mediation names the two powers at war, and only those two")
        return out
    sides: list[str] = []
    for raw in named:
        found = reference.resolve(conn, world_id, "nation", raw)
        if found.ambiguous:
            out.refused.append(found.problem or f"{raw!r} is nobody the map knows")
            return out
        sides.append(found.value or str(raw or ""))
    a, b = sides

    # Resolved before the terms are, so a mediation naming an unknown power is refused for
    # that rather than for whatever its first clause happened to say.
    verdict = may_broker(conn, world_id, mediator=nation_id, between=(a, b),
                         date=date, turn=turn)
    if not verdict.allowed:
        out.refused.append(verdict.because)
        return out

    # `terms_from` is written for a bilateral offer, where "me" and "them" mean the two
    # sides. Here the broker is neither, so the two belligerents take those places.
    resolved: list[ProposedTerm] = []
    for term in terms_from(declared.get("terms"), me=a, them=b):
        subject = term.subject
        if subject:
            spec = TERM_KINDS.get(term.kind)
            kind = "nation" if spec and spec.subject == "nation" else "region"
            found = reference.resolve(conn, world_id, kind, subject)
            if found.ambiguous:
                out.refused.append(found.problem or f"{subject!r} is unclear")
                return out
            subject = found.value or subject
        resolved.append(ProposedTerm(term.kind, term.obliged, term.beneficiary,
                                     subject, term.value))

    return mediate(conn, world_id, mediator=nation_id, between=(a, b), terms=resolved,
                   turn=turn, date=date)


def mediations_by(conn: sqlite3.Connection, world_id: str, nation_id: str) -> int:
    """How many wars this power has actually brought to an end that were not its own.

    Read from the log, because the log is where it is written and a rewind takes it. There
    is no counter to keep in step and none to farm.
    """
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM event e"
        "  JOIN event_participant m ON m.event_id = e.id AND m.role = 'mediator'"
        " WHERE e.world_id = ? AND m.nation_id = ? AND e.kind = ?",
        (world_id, nation_id, EventKind.PEACE_SIGNED.value)).fetchone()
    return int(row["c"]) if row else 0


# --------------------------------------------------------------------------- internals


def _at_war(conn: sqlite3.Connection, world_id: str, a: str, b: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM relation WHERE world_id = ? AND nation_a = ? AND nation_b = ?"
        "   AND at_war = 1", (world_id, a, b)).fetchone() is not None


def _regard(conn: sqlite3.Connection, world_id: str, of: str, toward: str) -> float:
    row = conn.execute(
        "SELECT score FROM relation WHERE world_id = ? AND nation_a = ? AND nation_b = ?",
        (world_id, of, toward)).fetchone()
    return float(row["score"]) if row else 0.0


def _blood(conn: sqlite3.Connection, world_id: str, a: str, b: str) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(casualties), 0) AS c FROM war_effort"
        " WHERE world_id = ? AND nation_id IN (?, ?)", (world_id, a, b)).fetchone()
    return float(row["c"]) if row else 0.0


def _field_median(
    conn: sqlite3.Connection, world_id: str, *, date: str, turn: int
) -> float:
    """Where an ordinary power stands on the leadership index right now.

    Over the living field, so a world that has lost half its powers is measured against the
    half that is left rather than against a ghost.
    """
    from .concert import leadership_index

    values = sorted(
        leadership_index(conn, world_id, str(row["id"]), date=date, turn=turn)
        for row in conn.execute(
            "SELECT id FROM nation WHERE world_id = ? AND is_alive = 1 ORDER BY id",
            (world_id,)))
    if not values:
        return 0.0
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2.0


def _last_attempt(
    conn: sqlite3.Connection, world_id: str, *, mediator: str, between: tuple[str, str]
) -> str:
    """When this power last put terms to this war, as a world date.

    Read from the log, which is the only place it is written -- there is no mediation table
    and there should not be one: an attempt is an event, and events rewind.
    """
    a, b = sorted(between)
    row = conn.execute(
        "SELECT e.date AS date FROM event e"
        "  JOIN event_participant m ON m.event_id = e.id AND m.role = 'mediator'"
        " WHERE e.world_id = ? AND m.nation_id = ?"
        "   AND EXISTS (SELECT 1 FROM event_participant p WHERE p.event_id = e.id"
        "                 AND p.role IN ('actor', 'target') AND p.nation_id = ?)"
        "   AND EXISTS (SELECT 1 FROM event_participant q WHERE q.event_id = e.id"
        "                 AND q.role IN ('actor', 'target') AND q.nation_id = ?)"
        " ORDER BY e.id DESC LIMIT 1", (world_id, mediator, a, b)).fetchone()
    return str(row["date"]) if row else ""


def _thank(
    conn: sqlite3.Connection, world_id: str, *, mediator: str, between: tuple[str, str],
    event_id: int, turn: int,
) -> None:
    """Both belligerents warm to the power that got them out of it.

    Small, and bounded by a ceiling rather than added for ever, on the argument
    `concert.REGARD_CEILING` makes at length: a quantity that only adds stops being a
    measure. A relation already above the ceiling keeps what it has.

    **Through the diff pipeline, like every other engine nudge.** The first draft wrote the
    two rows with a plain UPDATE, which is fingerprinted state changed outside the only
    path a rewind can invert -- so a rewound world would have kept the gratitude for a
    peace that no longer happened. Caught by `test_no_new_module_writes_fingerprinted_
    state_outside_the_diff_pipeline`, which exists for exactly this and has now earned its
    keep twice.
    """
    from ..state.apply import apply_batch
    from ..state.diff import Diff, DiffBatch, DiffOp
    from ..state.models import Role

    diffs: list[Diff] = []
    for side in between:
        row = conn.execute(
            "SELECT score FROM relation WHERE world_id = ? AND nation_a = ?"
            "   AND nation_b = ?", (world_id, side, mediator)).fetchone()
        if row is None:
            continue
        gain = min(GRATITUDE, max(0.0, GRATITUDE_CEILING - float(row["score"])))
        if gain <= 0.0:
            continue
        diffs.append(
            Diff("relation", f"{side}|{mediator}", "score", DiffOp.ADJUST, gain,
                 reason_event_id=event_id, visibility_scope=Visibility.PUBLIC))
    if diffs:
        apply_batch(conn, DiffBatch(world_id=world_id, turn=turn,
                                    proposer_role=Role.ENGINE, diffs=tuple(diffs)))
