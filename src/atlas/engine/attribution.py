"""Who a victim believes did it, and how sure it is allowed to be.

A covert act has an author. Until now that was binary and it was the *sight* of the event
that decided it: either a nation could read the event, in which case it knew everything, or
it could not, in which case the act might as well not have happened to it. There was no way
for a country to be *fairly sure* who had burned its shipyard.

**Three tiers, and each one buys something different.**

* *suspected* -- the relationship sours. Nothing else: a suspicion is not a claim.
* *likely* -- a grievance the diplomacy layer can raise and price. Still not a war.
* *proven* -- a casus belli, and a fact the world takes for granted afterwards.

**The engine owns the tier, and that is the whole guarantee.** A model may accuse anybody it
likes -- the diplomacy channel has always let it, and always will -- but an accusation
carries exactly the mechanical weight the computed tier gives it, which for an unfounded one
is none. There is no channel through which a power can assert guilt and be believed by the
rules. `attribution` is `ENGINE_ONLY` for the same reason `intel_network` is: a power that
could write its own attribution would manufacture a casus belli by saying so.

**Proven is not the same as exposed.** Patient counterintelligence reaches *proven*
privately: the victim knows, acts on it, and the world reads nothing. What makes an
operation a public scandal is exposure, which is its own layer and deliberately independent
of this one -- so a quiet certainty and a public humiliation are different things that can
happen separately, in either order, or not at all.

**Patience is literal.** Evidence accumulates per turn at a rate set by how far the victim's
counterintelligence outmatches the actor's tradecraft, so a clumsy operation against a
watchful state is proven in a few turns and a superb one against a careless state is
suspected for ever and proven never. That is the shape the layer is for.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..state.apply import apply_batch, record_event
from ..state.diff import Diff, DiffBatch
from ..state.models import DiffOp, EventKind, EventOrigin, Role, Visibility
from .intelligence import network_between
from .pacing import span_of

__all__ = [
    "EARNED",
    "NO_TIER",
    "TIERS",
    "Attribution",
    "assess_attribution",
    "attribution_id_for",
    "attributions_for",
    "counterintel_of",
    "tier_of",
    "tradecraft_of",
]

#: **What a tier is worth, worst to best.** ``none`` is a real stored state rather than the
#: absence of one: an investigation accumulates evidence for turns before it is worth
#: anything, and a row that could not exist until it crossed the first threshold would throw
#: that evidence away every turn and never cross it. The schema's CHECK is exactly this list.
NO_TIER = "none"
TIERS: tuple[str, ...] = (NO_TIER, "suspected", "likely", "proven")

#: The three that actually permit something. `TIERS` is what may be stored; this is what a
#: reader means by "the victim has a view".
EARNED: tuple[str, ...] = ("suspected", "likely", "proven")

#: The event kinds a covert undertaking is written as. Read from the gambit catalogue rather
#: than repeated, so a new covert kind is attributable the day it is added.
def _covert_kinds() -> frozenset[str]:
    from .gambit import KINDS

    return frozenset(kind.event_kind.value for kind in KINDS if kind.covert)


# --------------------------------------------------------------------------- the dials

#: Where each tier begins, on a confidence that runs 0-100. **All three are TUNABLE.**
#: Spaced so the middle tier is a real place to sit rather than a moment on the way past:
#: a grievance a country holds for a while without being able to act on it is the state
#: most of this layer's interesting decisions happen in.
SUSPECTED_AT = 25.0
LIKELY_AT = 55.0
PROVEN_AT = 85.0

#: How fast evidence accumulates, per point of margin per month. TUNABLE. At 0.5 a victim
#: outmatching an operator by twenty points proves it in about nine turns.
_EVIDENCE_RATE = 0.5

#: **What accrues even when the operator is better.** Without this a superb service would be
#: not merely unprovable but permanently unsuspected, and a country whose shipyards keep
#: burning would have no opinion at all about who was doing it. Small enough that a good
#: operator is suspected and effectively never proven: at 2.0 a point of margin nobody has,
#: reaching *proven* would take eighty-five turns of continuous operating.
_ALWAYS_SOMETHING = 2.0

#: What each tier does to the relationship when it is first reached. Charged once, on the
#: step, so a long investigation does not bleed regard every turn it continues.
_REGARD_ON_TIER: dict[str, float] = {
    "suspected": -6.0,
    "likely": -10.0,
    "proven": -16.0,
}

# ------------------------------------------------------------------ the two capabilities

#: What a nation can see of foreign operatives on its own soil without working at it.
#: **The passive floor.** I2 added the active half exactly where this note said it would --
#: as a term in this function -- so everything that reads counterintelligence picked it up
#: without changing.
_PASSIVE_BASE = 20.0

#: What being well governed is worth to a police service, per point of stability above the
#: middle. A state that has lost its own establishment does not catch spies.
_FROM_ORDER = 0.30

#: What sheer size is worth, against the operator's. A great power watching a great power is
#: a different problem from a great power watching a minor.
_FROM_SCALE = 8.0

#: And what being at war with the operator, or bordering it, does to how hard you look.
_FROM_WAR = 15.0
_FROM_NEIGHBOUR = 8.0


def counterintel_of(
    conn: sqlite3.Connection, world_id: str, nation_id: str, *, against: str
) -> float:
    """How well ``nation_id`` polices ``against``'s people inside its own borders, 0-100.

    **Passive plus active.** The passive floor is what any state gets from being governed at
    all; the active term is `intel_network.counter_intel`, which a government has to order
    and keep ordering. Per-pair, because a service that has swept its ministries for one
    country's agents has not swept them for another's.

    The active half is what closes the gap I3 shipped with. Passively this tops out near 66
    against a tradecraft that reaches 100, so an elite operator was unprovable however
    watchful the victim; with a sweep running, a state that invests can outmatch anybody --
    which is the whole reason counterintelligence is a decision rather than a stat.
    """
    from .intelligence import network_between

    active = network_between(conn, world_id, against, nation_id).counter_intel
    row = conn.execute(
        """
        SELECT (SELECT stability FROM nation WHERE id = ?)          AS order_,
               (SELECT economy FROM nation WHERE id = ?)            AS mine,
               (SELECT economy FROM nation WHERE id = ?)            AS theirs,
               COALESCE((SELECT at_war FROM relation
                          WHERE world_id = ? AND nation_a = ? AND nation_b = ?), 0) AS war,
               EXISTS (SELECT 1 FROM region_adjacency a
                         JOIN region ra ON ra.id = a.region_a
                         JOIN region rb ON rb.id = a.region_b
                        WHERE (ra.owner_nation_id = ? AND rb.owner_nation_id = ?)
                           OR (rb.owner_nation_id = ? AND ra.owner_nation_id = ?))
                                                                    AS neighbour
        """,
        (nation_id, nation_id, against, world_id, nation_id, against,
         nation_id, against, against, nation_id)).fetchone()
    if row is None:
        return min(100.0, _PASSIVE_BASE + active)
    strength = _PASSIVE_BASE + active
    strength += (float(row["order_"] or 50.0) - 50.0) * _FROM_ORDER
    strength += _FROM_SCALE * min(
        2.0, float(row["mine"] or 0.0) / max(1.0, float(row["theirs"] or 0.0))) / 2.0
    if int(row["war"] or 0):
        strength += _FROM_WAR
    if int(row["neighbour"] or 0):
        strength += _FROM_NEIGHBOUR
    return round(max(0.0, min(100.0, strength)), 3)


def tradecraft_of(
    conn: sqlite3.Connection, world_id: str, nation_id: str, *, against: str
) -> float:
    """How well ``nation_id`` can actually operate inside ``against``, 0-100.

    The network it has there, which is the same number `intelligence` already uses for what
    it can *see* -- deliberately, because the people who can find a secret out are the people
    who can do something quietly, and two separate numbers for one service would drift.

    **Effective, not raw**: what a sweep takes away is the ability to work, not the names on
    a ledger. So a service the other side is hunting is worse at covering its tracks, and
    counterintelligence tells on an operator twice over -- it degrades what they can do and
    it puts more police on the case. Those are two real things rather than one counted
    twice, and together they are what makes investment decisive against an elite operator
    that passive policing could never touch.
    """
    return network_between(conn, world_id, nation_id, against).effective


def tier_of(confidence: float) -> str:
    """The tier a confidence buys. ``NO_TIER`` while the file is still thin."""
    if confidence >= PROVEN_AT:
        return "proven"
    if confidence >= LIKELY_AT:
        return "likely"
    if confidence >= SUSPECTED_AT:
        return "suspected"
    return NO_TIER


def attribution_id_for(event_id: int, victim_id: str) -> str:
    """Derived, so an attribution survives a rewind with its identity."""
    return f"att_{event_id}_{victim_id}"


@dataclass(frozen=True, slots=True)
class Attribution:
    """What one victim has worked out about one covert act."""

    event_id: int
    actor_id: str
    victim_id: str
    confidence: float
    tier: str
    opened_turn: int = 0
    last_turn: int = 0

    @property
    def believed(self) -> bool:
        """Whether the victim has any view worth acting on at all."""
        return self.tier in EARNED

    @property
    def permits_demand(self) -> bool:
        """Whether the diplomacy layer may raise this as a grievance."""
        return self.tier in ("likely", "proven")

    @property
    def permits_war(self) -> bool:
        return self.tier == "proven"


def attributions_for(
    conn: sqlite3.Connection, world_id: str, victim_id: str
) -> list[Attribution]:
    """Everything this nation believes about who has been acting against it, gravest first.

    **Scoped to the victim by construction.** There is deliberately no reader for "every
    attribution in the world": what a country has worked out is its own, and an unscoped
    reader is the leak, which is the argument `state.visibility` makes about itself.
    """
    return [
        Attribution(
            event_id=int(row["event_id"]), actor_id=str(row["actor_nation_id"]),
            victim_id=victim_id, confidence=float(row["confidence"]),
            tier=str(row["tier"]), opened_turn=int(row["opened_turn"]),
            last_turn=int(row["last_turn"]))
        for row in conn.execute(
            "SELECT event_id, actor_nation_id, confidence, tier, opened_turn, last_turn"
            "  FROM attribution WHERE world_id = ? AND victim_nation_id = ?"
            " ORDER BY confidence DESC, event_id", (world_id, victim_id))
    ]


# ------------------------------------------------------------------------ the assessment


@dataclass(slots=True)
class AttributionReport:
    """What moved, and what crossed a tier -- which is the only part worth reporting."""

    raised: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def assess_attribution(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str, days: int = 30
) -> AttributionReport:
    """Let every victim get a little closer to knowing who did it.

    Runs over the covert acts still open against each victim and adds a turn's evidence to
    each. Written as diffs at the victim's own scope, so a rewind puts the investigation
    back exactly where it stood and nobody else ever reads it.
    """
    report = AttributionReport()
    acts = _open_acts(conn, world_id, turn)
    if not acts:
        return report

    known = {
        (int(row["event_id"]), str(row["victim_nation_id"])): (
            float(row["confidence"]), str(row["tier"]))
        for row in conn.execute(
            "SELECT event_id, victim_nation_id, confidence, tier FROM attribution"
            " WHERE world_id = ?", (world_id,))
    }
    span = span_of(days)
    for event_id, actor, victim, opened, _opened_on in acts:
        was, old_tier = known.get((event_id, victim), (0.0, NO_TIER))
        margin = (counterintel_of(conn, world_id, victim, against=actor)
                  - tradecraft_of(conn, world_id, actor, against=victim))
        gained = max(_ALWAYS_SOMETHING, margin) * _EVIDENCE_RATE * span
        now = round(min(100.0, was + gained), 3)
        tier = tier_of(now)
        if tier == old_tier and abs(now - was) < 0.01:
            continue
        _record(conn, world_id, turn=turn, date=date, event_id=event_id, actor=actor,
                victim=victim, confidence=now, tier=tier, was=old_tier, opened=opened,
                fresh=(event_id, victim) not in known, report=report)
    return report


def _open_acts(
    conn: sqlite3.Connection, world_id: str, turn: int
) -> list[tuple[int, str, str, int, str]]:
    """``(event_id, actor, victim, opened_turn, opened_on)`` for every covert act.

    The date comes along because how long a trail stays warm is a fact about the world's
    calendar rather than about how the player spends their turns, and the event already
    knows when it happened.

    **A covert act is a secret event with an actor and a named target.** That triple is the
    discriminator and each part of it earns its place: the `espionage` and `unrest` kinds are
    also written by paths that are nobody's operation -- a service filing its own report, a
    colonial administration taking the temperature -- and those name no target, so they are
    not attributable to anybody and never appear here.

    **Covert backing is the second shape** (I5), and it is recognised by carrying a
    `beneficiary` as well: a patron, the client it is arming, and every enemy of that client
    as a target. Nothing else in the engine writes actor, beneficiary and target together on
    a secret event, so no kind list has to be widened to let deniability be worn down --
    which is what "at some point the other side stops pretending" needed to become
    mechanical rather than flavour.
    """
    kinds = _covert_kinds()
    if not kinds:
        return []
    placeholders = ", ".join("?" for _ in kinds)
    return [
        (int(row["id"]), str(row["actor"]), str(row["victim"]), int(row["turn"]),
         str(row["date"] or ""))
        for row in conn.execute(
            f"""
            SELECT e.id, e.turn, e.date, e.actor_nation_id AS actor,
                   p.nation_id AS victim
              FROM event e
              JOIN event_participant p ON p.event_id = e.id AND p.role = 'target'
             WHERE e.world_id = ? AND e.turn <= ? AND e.visibility <> 'public'
               AND e.actor_nation_id IS NOT NULL AND p.nation_id IS NOT NULL
               AND p.nation_id <> e.actor_nation_id
               AND (e.kind IN ({placeholders})
                    OR EXISTS (SELECT 1 FROM event_participant b
                                WHERE b.event_id = e.id AND b.role = 'beneficiary'))
             ORDER BY e.id, p.nation_id
            """,
            (world_id, turn, *sorted(kinds)))
    ]


def _record(  # noqa: PLR0913 - an attribution is who, to whom, about what, and how sure
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str, event_id: int,
    actor: str, victim: str, confidence: float, tier: str, was: str, opened: int,
    fresh: bool, report: AttributionReport,
) -> None:
    """Write one step of an investigation, and whatever the new tier has earned."""
    names = _names(conn, world_id)
    stepped = tier != was
    told = (f"{names.get(victim, victim)}'s people are now {tier} of "
            f"{names.get(actor, actor)}"
            if tier != NO_TIER else
            f"{names.get(victim, victim)}'s people are still working on it")
    event_id_own = record_event(
        conn, world_id=world_id, turn=turn, date=date, kind=EventKind.ESPIONAGE,
        origin=EventOrigin.ENGINE, actor_nation_id=victim,
        text=told if stepped else
             f"{names.get(victim, victim)}'s investigation continues.",
        # **The victim's private knowledge.** Attribution is not exposure: what a country
        # has worked out is its own until somebody blows the operation, which is a different
        # layer entirely. Secret, and visible to one nation.
        visibility=Visibility.SECRET,
        visible_to=(victim,))

    row_id = attribution_id_for(event_id, victim)
    diffs: list[Diff] = []
    if fresh:
        diffs.append(Diff(
            "attribution", row_id, "__insert__", DiffOp.INSERT,
            {
                "world_id": world_id, "event_id": event_id, "actor_nation_id": actor,
                "victim_nation_id": victim, "confidence": confidence, "tier": tier,
                "opened_turn": opened, "last_turn": turn,
            },
            reason_event_id=event_id_own, visibility_scope=Visibility.SECRET))
    else:
        diffs.append(Diff("attribution", row_id, "confidence", DiffOp.SET, confidence,
                          reason_event_id=event_id_own,
                          visibility_scope=Visibility.SECRET))
        if stepped:
            diffs.append(Diff("attribution", row_id, "tier", DiffOp.SET, tier,
                              reason_event_id=event_id_own,
                              visibility_scope=Visibility.SECRET))
        diffs.append(Diff("attribution", row_id, "last_turn", DiffOp.SET, turn,
                          reason_event_id=event_id_own,
                          visibility_scope=Visibility.SECRET))

    if stepped and tier != NO_TIER:
        # **The relationship sours, at the victim's own scope.** A regard drop everybody
        # could read would announce the investigation, which is the leak INFO-002 exists to
        # refuse -- and it would announce it to the operator, who is the one person who must
        # not know how close this is getting.
        diffs.append(Diff(
            "relation", f"{victim}|{actor}", "score", DiffOp.ADJUST,
            _REGARD_ON_TIER.get(tier, -6.0), reason_event_id=event_id_own,
            visibility_scope=Visibility.SECRET))

    outcome = apply_batch(
        conn,
        DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                  proposer_model=None, diffs=tuple(diffs)))
    if not outcome.applied:
        report.problems.append(f"an attribution could not be written: {outcome.reason}")
        return
    if stepped and tier != NO_TIER:
        report.raised.append(told)
    if tier == "proven" and was != "proven":
        _prove(conn, world_id, turn=turn, date=date, actor=actor, victim=victim,
               cause=event_id_own, names=names, report=report)


def _prove(  # noqa: PLR0913 - proving is who, to whom, when, and citing what
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str, actor: str,
    victim: str, cause: int, names: dict[str, str], report: AttributionReport,
) -> None:
    """What *proven* buys: a casus belli the victim may act on, and a fact that outlives it.

    **The casus belli is secret and still works.** `war_declaration_justified` reads
    engine-authored `casus_belli` events by aggressor and turn, and does not filter on
    visibility -- so a victim that has proved authorship privately may declare war on it
    without the world having been told why. That is exactly what "proven is independent of
    exposure" has to mean mechanically, and it falls out of machinery that already existed.
    """
    from .facts import record_fact

    record_event(
        conn, world_id=world_id, turn=turn, date=date, kind=EventKind.CASUS_BELLI,
        origin=EventOrigin.ENGINE, actor_nation_id=victim,
        text=(f"{names.get(victim, victim)} holds {names.get(actor, actor)} responsible "
              "for a covert act against it, and can prove it."),
        visibility=Visibility.SECRET, visible_to=(victim,), causes=(cause,),
        # **Who the grievance is against.** `war_declaration_justified` reads this to
        # decide which war this licenses; without it the event said only that the victim
        # had a grievance, and a grievance against nobody in particular excused a war
        # against anybody at all.
        participants=(("actor", victim), ("target", actor)))

    # A fact the world takes for granted afterwards -- scoped to what the world may know,
    # which until an exposure is the victim alone. `record_fact` takes the scope of its
    # cause, which is the rule that keeps this honest.
    try:
        minted = record_fact(
            conn, world_id, subject_kind="nation", subject_id=actor,
            predicate="was_behind", object_id=victim,
            turn=turn, source_event_id=cause, visibility=Visibility.SECRET)
        if minted.refused:
            report.problems.extend(minted.refused)
    except Exception as exc:  # noqa: BLE001 - a fact that cannot be minted is a finding
        report.problems.append(f"the fact could not be minted: {exc}")


def _names(conn: sqlite3.Connection, world_id: str) -> dict[str, str]:
    return {
        str(row["id"]): str(row["name"])
        for row in conn.execute(
            "SELECT id, name FROM nation WHERE world_id = ?", (world_id,))
    }
