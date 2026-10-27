"""When an operation is blown, and how much of a scandal it is.

Attribution is what a *victim* privately works out. This is the other thing that can happen
to a covert act: the world finds out. The two are deliberately independent -- a state can be
certain who burned its shipyard and never be able to say so publicly, and an operation can be
splashed across every front page before anybody has proved whose it was.

**The fallout is computed, not gated.** The obvious design is a switch: exposed, therefore
casus belli. That makes every covert act a coin flip between nothing and a war, and it prices
a bribe exactly like a plot against a head of state. So the magnitude is a product of three
things, and it scales what it costs continuously:

* **importance** -- what was being done, and to whom. Wrecking something in a nuclear power
  is not passing money to a minister.
* **how heavily guarded it was** -- the victim's own investment in keeping people out.
  Ripping open a well-defended secret is a bigger betrayal than walking into an open house.
* **the mismatch** -- a clumsy operation caught by a good service is more damning than a
  careful one barely caught. Getting away with it badly is its own indictment.

A **product**, so the casus belli falls out of the shape rather than out of a second rule: a
major operation nobody was guarding does not reach it, and neither does a trivial one against
a fortress. Only something that is both important *and* well-defended gets there, which is
what "not a flat gate" has to mean.

**A fact is always minted.** However small the magnitude, the world now knows this happened
and remembers it -- that is what separates exposure from every other consequence here, which
can be shrugged off.

**Nothing a model says touches any of it.** It cannot fake an exposure, avoid one by
protesting innocence, or manufacture a casus belli. The roll is seeded on the operation and
the fallout is arithmetic over state.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..sim.rng import derive
from ..state.apply import apply_batch, record_event
from ..state.diff import Diff, DiffBatch
from ..state.models import DiffOp, EventKind, EventOrigin, Role, Visibility
from .attribution import counterintel_of
from .pacing import compounded, days_between, span_of

__all__ = [
    "CASUS_BELLI_AT",
    "CASUS_BELLI_NEEDS_IMPORTANCE",
    "Exposure",
    "assess_exposure",
    "exposure_chance",
    "exposure_id_for",
    "fallout_of",
    "guardedness_of",
    "importance_of",
]

# --------------------------------------------------------------------------- the dials

#: **How likely a live operation is to surface in one month**, at the extremes. TUNABLE.
#: A careful operation against a service that is not looking almost never does; a clumsy one
#: against an invested service usually does within a year.
_CHANCE_FLOOR = 0.01
_CHANCE_CEILING = 0.35

#: **How long a trail stays warm, in days of world time.** TUNABLE. After this an operation
#: is history rather than news, and rolling for ever would mean everything is eventually
#: exposed -- which would make tradecraft worthless and the whole layer a clock.
#:
#: Days rather than turns, because how long a thing stays newsworthy is a fact about the
#: world and not about how much detail the player asked to watch. It was twelve turns: a
#: year at monthly pacing, a season at weekly, and twelve *years* at yearly. 365 is what
#: twelve monthly turns were worth, so nothing about the default game moves.
COLD_AFTER_DAYS = 365.0

#: What each kind of covert act is worth on the importance scale, before the target is
#: weighed. **TUNABLE, and the crudest thing in this module** -- see `importance_of`.
_KIND_IMPORTANCE: dict[str, float] = {
    "sabotage": 0.75,
    "unrest": 0.60,
    "espionage": 0.25,
}

#: **What arming somebody's enemy is worth**, when that is what came out (I5). Its own
#: number rather than the `narrative` kind's default, because a covert backing is not a
#: narrative -- it is the single most consequential thing on this scale short of wrecking
#: something, and a patron caught running a war it denied should be in war-grade territory
#: against a victim that was genuinely watching.
_BACKING_IMPORTANCE = 0.80

#: What being a serious country does to the importance of an operation against it. A power
#: with warheads is the case Alex named: wrecking something there is not the same act as
#: wrecking something in a minor.
_ARSENAL_WEIGHT = 0.25
_GREAT_POWER_WEIGHT = 0.15

#: Floors, so no single factor annihilates the product. Without them an operation against a
#: country with no counterintelligence at all would be a free scandal of magnitude zero, and
#: "always mints a fact" would be minting a fact about nothing.
_GUARD_FLOOR = 0.25
_MISMATCH_FLOOR = 0.30

#: What a magnitude of 100 costs. TUNABLE, and the pair to argue about first: these set how
#: dangerous covert play is relative to everything else a country can do to its standing.
REGARD_AT_FULL = -45.0
LEGITIMACY_AT_FULL = -18.0

#: **Where a scandal becomes a war.** TUNABLE, and the single number Alex asked to see
#: measured before it locked. Because the magnitude is a *product*, this is not a fourth
#: rule: it is the point past which only something important and well-guarded can reach.
CASUS_BELLI_AT = 42.0

#: **And what can never be a casus belli however badly it goes.** TUNABLE.
#:
#: Measured first without it, and the curve said no: a *bribe* against a well-guarded nuclear
#: power came out at magnitude 55 and bought a war. Buying a minister is the shrug end of the
#: scale by construction, and no amount of embarrassment should turn stealing secrets into
#: grounds for invasion -- so the importance floor is a statement about *kinds of act*, which
#: is the half of "important AND heavily guarded" that a single magnitude cannot express.
#:
#: At 0.70 the whole espionage family is structurally excluded: bribes and stings top out at
#: 0.65 even against a nuclear great power. Sabotage and incitement can reach it.
CASUS_BELLI_NEEDS_IMPORTANCE = 0.70


def exposure_id_for(event_id: int) -> str:
    """Derived, so an exposure survives a rewind with its identity."""
    return f"exp_{event_id}"


@dataclass(frozen=True, slots=True)
class Exposure:
    """What an operation cost when it came out."""

    event_id: int
    actor_id: str
    victim_id: str
    importance: float
    guardedness: float
    mismatch: float
    magnitude: float
    casus_belli: bool

    def describe(self) -> str:
        return (f"magnitude {self.magnitude:.0f} "
                f"(importance {self.importance:.2f} x guarded {self.guardedness:.2f} "
                f"x mismatch {self.mismatch:.2f})"
                + (" -- casus belli" if self.casus_belli else ""))


@dataclass(slots=True)
class ExposureReport:
    blown: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


# ------------------------------------------------------------------------ the three factors


def importance_of(
    conn: sqlite3.Connection, world_id: str, *, kind: str, victim: str,
    backing: bool = False,
) -> float:
    """How big a thing this was to do, 0..1.

    **The crudest thing in this module, and worth knowing why.** The sharpest signal would be
    the *stake* the actor committed -- `gambit.STAKES` grades every undertaking minor,
    serious or grave, and prices it accordingly -- but the stake is charged to the treasury
    and never recorded on the event, so at exposure time it is gone. What survives is the
    event's kind and who it was done to, so that is what this reads.

    Recording the stake on the event would make this sharper for one line of change, and it
    is flagged rather than done because it means touching the gambit writer again.
    """
    weight = _BACKING_IMPORTANCE if backing else _KIND_IMPORTANCE.get(kind, 0.35)
    row = conn.execute(
        """
        SELECT COALESCE((SELECT warheads FROM arsenal
                          WHERE world_id = ? AND nation_id = ?), 0)      AS warheads,
               (SELECT economy FROM nation WHERE id = ?)                 AS economy,
               (SELECT MAX(economy) FROM nation WHERE world_id = ?)      AS biggest
        """,
        (world_id, victim, victim, world_id)).fetchone()
    if row is None:
        return round(min(1.0, weight), 4)
    if int(row["warheads"] or 0) > 0:
        weight += _ARSENAL_WEIGHT
    biggest = float(row["biggest"] or 0.0)
    if biggest > 0 and float(row["economy"] or 0.0) / biggest >= 0.5:
        weight += _GREAT_POWER_WEIGHT
    return round(min(1.0, weight), 4)


def guardedness_of(
    conn: sqlite3.Connection, world_id: str, *, actor: str, victim: str
) -> float:
    """How hard the victim was working to keep this power's people out, 0..1.

    The counterintelligence investment itself, which is what makes exposing a well-defended
    secret a bigger betrayal than walking into an open house.
    """
    from .intelligence import network_between

    return round(
        min(1.0, network_between(conn, world_id, actor, victim).counter_intel / 100.0), 4)


def _mismatch(conn: sqlite3.Connection, world_id: str, *, actor: str, victim: str) -> float:
    """How badly outclassed the operation was, 0..1. Zero when the operator was the better.

    **Against the operator's *raw* service, not its effective one, and the difference matters
    here in a way it does not in `attribution`.** Effective tradecraft is raw minus the
    sweep, so measuring against it would make the sweep drive this factor *and* the
    guardedness factor -- and a product of two numbers that are the same number is not a
    product of two factors, it is one factor squared. Measured on what the operator actually
    brought, this asks a different question from guardedness: not "how hard were they
    trying to stop you" but "how far out of your depth were you".
    """
    police = counterintel_of(conn, world_id, victim, against=actor)
    from .intelligence import network_between

    craft = network_between(conn, world_id, actor, victim).strength
    return round(max(0.0, min(1.0, (police - craft) / 100.0)), 4)


def exposure_chance(
    conn: sqlite3.Connection, world_id: str, *, actor: str, victim: str
) -> float:
    """The chance this operation surfaces in one month.

    Rises with what the victim is doing about it and falls with how well the operation was
    run -- the same contest attribution reads, asked about the public rather than the police.
    """
    return round(
        _CHANCE_FLOOR + (_CHANCE_CEILING - _CHANCE_FLOOR)
        * _mismatch(conn, world_id, actor=actor, victim=victim), 4)


def fallout_of(  # noqa: PLR0913 - a fallout is the act, its author, and its victim
    conn: sqlite3.Connection, world_id: str, *, event_id: int, kind: str, actor: str,
    victim: str, backing: bool = False,
) -> Exposure:
    """What this operation costs if it comes out. Pure arithmetic over state."""
    importance = importance_of(conn, world_id, kind=kind, victim=victim, backing=backing)
    guarded = guardedness_of(conn, world_id, actor=actor, victim=victim)
    mismatch = _mismatch(conn, world_id, actor=actor, victim=victim)
    magnitude = round(
        100.0
        * importance
        * (_GUARD_FLOOR + (1.0 - _GUARD_FLOOR) * guarded)
        * (_MISMATCH_FLOOR + (1.0 - _MISMATCH_FLOOR) * mismatch), 3)
    return Exposure(
        event_id=event_id, actor_id=actor, victim_id=victim, importance=importance,
        guardedness=guarded, mismatch=mismatch, magnitude=magnitude,
        casus_belli=(magnitude >= CASUS_BELLI_AT
                     and importance >= CASUS_BELLI_NEEDS_IMPORTANCE))


# ------------------------------------------------------------------------ the assessment


def assess_exposure(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str, seed: int = 0,
    days: float = 30.0,
) -> ExposureReport:
    """Roll every still-warm operation for whether this is the month it comes out.

    **Or the year, if that is what the turn was.** `exposure_chance` says of itself that it
    is the chance in one *month*, and it was rolled once per turn however long the turn was:
    a covert operation running through a year-long jump got a single month's chance of
    surfacing. Secrets kept better the faster you played, which is not a property anybody
    chose. Compounded, per :func:`atlas.engine.pacing.compounded` -- a 0.08 monthly chance
    is 0.63 over a year, not 0.08 and not 0.96.
    """
    report = ExposureReport()
    span = span_of(days)
    from .attribution import _open_acts

    already = {
        int(row["event_id"]) for row in conn.execute(
            "SELECT event_id FROM exposure WHERE world_id = ?", (world_id,))
    }
    if seed == 0:
        row = conn.execute(
            "SELECT rng_seed FROM world WHERE id = ?", (world_id,)).fetchone()
        seed = int(row["rng_seed"]) if row else 0

    for event_id, actor, victim, opened, opened_on in _open_acts(conn, world_id, turn):
        if event_id in already:
            continue
        # How long ago it happened, in world time. A save whose event carries no readable
        # date falls back to the turn count at the reference length, which is what that
        # save was played at anyway.
        cold = days_between(opened_on, date) if opened_on else None
        if cold is None:
            cold = float(turn - opened) * 30.0
        if cold > COLD_AFTER_DAYS:
            continue
        chance = compounded(
            exposure_chance(conn, world_id, actor=actor, victim=victim), span)
        rng = derive(seed, world_id, turn, f"exposure:{event_id}")
        if rng.random() >= chance:
            continue
        kind = str(conn.execute(
            "SELECT kind FROM event WHERE id = ?", (event_id,)).fetchone()["kind"])
        # A covert backing is the shape with a beneficiary on it -- see
        # `attribution._open_acts`, which recognises the same triple.
        backing = conn.execute(
            "SELECT 1 FROM event_participant WHERE event_id = ? AND role = 'beneficiary'",
            (event_id,)).fetchone() is not None
        blown = fallout_of(conn, world_id, event_id=event_id, kind=kind, actor=actor,
                           victim=victim, backing=backing)
        # **Marked before publishing, not after the pass.** `already` was read once above
        # and never added to, so an operation `_open_acts` yields twice in the same pass --
        # a covert act with more than one participant on it will -- was published twice, and
        # the second insert hit `UNIQUE (world_id, event_id)` and lost the whole batch to
        # `[CORE-000] IntegrityError`. Worse, `_publish` records its event *before* the
        # batch, so the refused turn still left a scandal in the log that nothing backed.
        #
        # Found by the laboratory on a recorded run, turn 9. Latent for as long as the table
        # has existed and reachable only once covert backing became common, which is what
        # made the patronage briefing surface it.
        already.add(event_id)
        _publish(conn, world_id, turn=turn, date=date, blown=blown, report=report)
    return report


def _publish(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str, blown: Exposure,
    report: ExposureReport,
) -> None:
    """Write the scandal, charge it, and give the world something to remember."""
    from .facts import record_fact

    names = {
        str(row["id"]): str(row["name"])
        for row in conn.execute(
            "SELECT id, name FROM nation WHERE world_id = ?", (world_id,))
    }
    actor = names.get(blown.actor_id, blown.actor_id)
    victim = names.get(blown.victim_id, blown.victim_id)
    told = (f"{actor}'s hand in a covert operation against {victim} is now public "
            f"knowledge.")
    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date, kind=EventKind.ESPIONAGE,
        origin=EventOrigin.ENGINE, actor_nation_id=blown.actor_id, text=told,
        # **Public, and that is the whole difference from attribution.** What a victim works
        # out privately is its own; this is the thing everybody reads.
        visibility=Visibility.PUBLIC,
        participants=(("actor", blown.actor_id), ("aggrieved", blown.victim_id)))

    share = blown.magnitude / 100.0
    diffs: list[Diff] = [
        Diff("exposure", exposure_id_for(blown.event_id), "__insert__", DiffOp.INSERT,
             {
                 "world_id": world_id, "event_id": blown.event_id,
                 "actor_nation_id": blown.actor_id, "victim_nation_id": blown.victim_id,
                 "importance": blown.importance, "guardedness": blown.guardedness,
                 "mismatch": blown.mismatch, "magnitude": blown.magnitude,
                 "casus_belli": 1 if blown.casus_belli else 0, "turn": turn,
             },
             reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),
        Diff("relation", f"{blown.victim_id}|{blown.actor_id}", "score", DiffOp.ADJUST,
             round(REGARD_AT_FULL * share, 2), reason_event_id=event_id,
             visibility_scope=Visibility.PUBLIC),
    ]
    # Charged against what there is, for the reason the breach path learned: ECON-003 holds
    # legitimacy inside 0-100 and names no diff, so an overshoot loses the whole batch.
    from .bounds import absorbable

    diffs.append(Diff(
        "nation", blown.actor_id, "legitimacy", DiffOp.ADJUST,
        absorbable(conn, "nation", blown.actor_id, "legitimacy",
                   round(LEGITIMACY_AT_FULL * share, 2)),
        reason_event_id=event_id, visibility_scope=Visibility.PUBLIC))

    outcome = apply_batch(
        conn,
        DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                  proposer_model=None, diffs=tuple(diffs)))
    if not outcome.applied:
        report.problems.append(f"an exposure could not be written: {outcome.reason}")
        return

    # **Always, whatever the magnitude.** The world now knows, and that is the one
    # consequence of exposure nobody can shrug off.
    minted = record_fact(
        conn, world_id, subject_kind="nation", subject_id=blown.actor_id,
        predicate="was_behind", object_id=blown.victim_id, turn=turn,
        source_event_id=event_id, visibility=Visibility.PUBLIC)
    report.problems.extend(minted.refused)

    if blown.casus_belli:
        record_event(
            conn, world_id=world_id, turn=turn, date=date, kind=EventKind.CASUS_BELLI,
            origin=EventOrigin.ENGINE, actor_nation_id=blown.victim_id,
            text=(f"{victim} has been shown what {actor} was doing to it, and the world "
                  "has seen it too."),
            visibility=Visibility.PUBLIC, causes=(event_id,),
            # Who it is against -- see the note in `attribution._prove`. WAR-001 reads it.
            participants=(("actor", blown.victim_id), ("target", blown.actor_id)))
    report.blown.append(f"{told} {blown.describe()}")
