"""How a country is built to fight, spend and stand — engine-owned, and everyone has one.

Two nations with identical numbers played identically. Everything that made France France and
the Soviet Union the Soviet Union lived in a prose blurb the models read and the arithmetic
never did, so a hundred and forty-seven countries ran one set of equations and differed only in
their inputs. A doctrine is the missing half: **a standing commitment about method that changes
the calculation itself**.

**Not a directive.** :mod:`atlas.engine.directives` is the player's private note to themselves,
kept in the workspace, reordering advice and never acting. This is the opposite in every
respect and deliberately so:

===================  ==============================  ==============================
                     directives                      doctrines
===================  ==============================  ==============================
stored in            the workspace sidecar           a fingerprinted table
rewind               untouched                       undone like any other state
who has one          the player                      **every nation**
what it does         reorders advice                 changes the arithmetic
who writes it        the player, free and instant    proposed, then priced
===================  ==============================  ==============================

The word for one is *doctrine* and the word for the other is *policy*, everywhere, so a
briefing that mentions both is readable.

**Absence is the default and means today's behaviour.** A nation with no row for an axis has no
stance, and every multiplier below is exactly 1.0. That is the arrangement `arsenal` has, and it
is what lets this ship without moving any world that does not seed it: a save written before
the table migrates to one with no doctrines and behaves precisely as it did.

**A shift empties the stance rather than setting a third one.** While a country is
reorganising it has no doctrine at all, so it holds neither stance's advantages -- and that dip
*is* the cost, self-balancing in a way a flat charge is not: a nation that keeps changing its
mind never holds a bonus at all. There is no in-transition flag to forget to check, because the
absence of a stance already means "no modifiers".

**Nothing here decides an outcome.** The columns are engine-only, a shift is checked and priced
before it is written, and every effect is a multiplier applied by the system that owns the
number -- not a write into somebody else's table.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from ..state.apply import apply_batch, record_event, unrecord_events
from ..state.diff import Diff, DiffBatch
from ..state.models import DiffOp, EventKind, EventOrigin, Role, Visibility
from .bounds import absorbable
from .causality import Refusal
from .pacing import after_months, days_between

__all__ = [
    "AXES",
    "AXIS_IDS",
    "EFFECTS",
    "LEVER_IS_A_COST",
    "helps",
    "hurts",
    "SHIFT_AT_WAR",
    "SHIFT_MONTHS",
    "SHIFT_STABILITY",
    "SHIFT_TREASURY_SHARE",
    "Axis",
    "Doctrine",
    "ShiftOutcome",
    "Stance",
    "adopt",
    "all_of",
    "as_lines",
    "axis_named",
    "axis_of_stance",
    "offered_to",
    "stance_ids",
    "doctrine_id_for",
    "factor",
    "seed_doctrines",
    "settle_shifts",
    "stance_of",
]


# ------------------------------------------------------------------- the catalogue


@dataclass(frozen=True, slots=True)
class Stance:
    """One pole of an axis: what a country has decided, and what it implies."""

    id: str
    label: str
    #: One clause a player reads.
    blurb: str
    #: What it implies about what to *do*, handed to a nation's own staff. The part that
    #: does the work in a briefing: a doctrine nobody is told the consequences of is a
    #: label, and a label changes nobody's mind.
    implies: str


@dataclass(frozen=True, slots=True)
class Axis:
    """One question every country has answered, whether or not it ever said so."""

    id: str
    label: str
    blurb: str
    stances: tuple[Stance, ...]
    #: Whether shifting is a domestic event as well as an expensive one. See
    #: :data:`SHIFT_STABILITY`.
    political: bool = False
    #: Whether this axis means anything for this nation. A landlocked power has no naval
    #: doctrine and a non-nuclear one has no posture, in the same way that a nation with no
    #: warheads has no `arsenal` row -- absence rather than a stance meaning "not
    #: applicable". Absent means the axis always applies.
    applies: Callable[[sqlite3.Connection, str, str], bool] | None = None

    def stance_named(self, stance_id: str) -> Stance | None:
        for stance in self.stances:
            if stance.id == stance_id:
                return stance
        return None


def _has_arsenal(conn: sqlite3.Connection, world_id: str, nation_id: str) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM arsenal WHERE world_id = ? AND nation_id = ? AND warheads > 0",
            (world_id, nation_id)).fetchone()
    except sqlite3.OperationalError:
        return False
    return row is not None


#: Every axis, and every stance on it. **One declaration point**, in the manner of
#: `engine.capabilities`: the schema's CHECK, the briefing, the player's menu and the
#: effect table are all read from here, so an axis cannot exist in one and not the others.
AXES: tuple[Axis, ...] = (
    Axis(
        id="mobilisation",
        label="How the army is raised",
        blurb="Whether this country fields the many or the few.",
        stances=(
            Stance(
                id="mass",
                label="mass mobilisation",
                blurb="Everyone serves. The army is large, cheap per head, and made of "
                      "conscripts.",
                implies="You can replace losses that would end another country's war, so "
                        "press attacks a smaller army could not afford. Your formations are "
                        "individually worse than a professional enemy's, and casualties are "
                        "felt at home more sharply than they would be elsewhere.",
            ),
            Stance(
                id="professional",
                label="a professional army",
                blurb="Few, long-service and well-found. Expensive per head and hard to "
                      "replace.",
                implies="Your formations beat their weight, so seek battles where quality "
                        "decides. **You cannot replace what you lose** -- your manpower pool "
                        "is small and refills slowly, so a war of attrition is a war you lose "
                        "even while winning every engagement.",
            ),
        ),
    ),
    Axis(
        id="nuclear_posture",
        label="What the warheads are for",
        blurb="Whether this country's arsenal is meant to answer everything or only the "
              "last thing.",
        stances=(
            Stance(
                id="retaliation",
                label="massive retaliation",
                blurb="A large arsenal, committed in force, answering any serious attack.",
                implies="Your deterrent is hard to discount and your second strike would end "
                        "the other country. It costs you every month, and the day you use it "
                        "the world will not forget who did.",
            ),
            Stance(
                id="minimum",
                label="minimum deterrence",
                blurb="Enough to be unacceptable to attack, and not one warhead more.",
                implies="Your warheads are for deterring and not for winning. Do not reach "
                        "for them to settle a war you are losing conventionally; what they "
                        "buy you is that nobody marches on your capital.",
            ),
        ),
        applies=_has_arsenal,
    ),
    Axis(
        id="alignment",
        label="Who this country stands with",
        blurb="Whether it answers a call, or keeps out of other people's wars.",
        political=True,
        stances=(
            Stance(
                id="collective_security",
                label="collective security",
                blurb="Pacts are real promises, and are answered.",
                implies="Honour the pacts you hold: an ally you come for is worth more than "
                        "the war costs, and one you leave standing alone costs you at home "
                        "and abroad.",
            ),
            Stance(
                id="neutrality",
                label="armed neutrality",
                blurb="No blocs, no obligations, and trade with whoever is buying.",
                implies="Do not sign pacts and do not be drawn into other people's wars. You "
                        "keep selling to both sides and your people are content -- and when "
                        "somebody comes for you, nobody will come for you.",
            ),
        ),
    ),
)

AXIS_IDS: tuple[str, ...] = tuple(axis.id for axis in AXES)


def axis_named(axis_id: str) -> Axis | None:
    for axis in AXES:
        if axis.id == axis_id:
            return axis
    return None


def axis_of_stance(stance_id: str) -> Axis | None:
    """Which question this answer answers, or ``None`` if nothing does.

    **Stance ids are unique across every axis, and a test holds them to it.** That is what
    lets a player and a staff name a doctrine in one word -- "go professional", "adopt
    minimum deterrence" -- instead of naming an axis and a stance and getting the pairing
    wrong. The axis is derivable, so it is derived rather than asked for.
    """
    for axis in AXES:
        if axis.stance_named(stance_id) is not None:
            return axis
    return None


def stance_ids() -> tuple[str, ...]:
    """Every stance a nation could be asked to adopt. Feeds the parser and the menu."""
    return tuple(stance.id for axis in AXES for stance in axis.stances)


def offered_to(conn: sqlite3.Connection, world_id: str, nation_id: str) -> list[Stance]:
    """The stances this nation could actually move to, right now.

    Skips the axis it already holds, the one it is mid-shift on, and any axis that is not a
    question for this country -- so a menu built from this never offers something that
    would be refused, which is the standing rule for every list the player is shown.
    """
    held = all_of(conn, world_id, nation_id)
    if any(d.shifting for d in held.values()):
        return []
    out: list[Stance] = []
    for axis in AXES:
        if axis.applies is not None and not axis.applies(conn, world_id, nation_id):
            continue
        current = held.get(axis.id)
        for stance in axis.stances:
            if current is not None and current.stance == stance.id:
                continue
            out.append(stance)
    return out


# --------------------------------------------------------------------- the effects

#: **Every multiplier a doctrine applies, in one table.** Keyed axis -> stance -> lever.
#: A lever absent from a stance is 1.0, and a nation with no stance reads 1.0 for every
#: lever -- so a system asking this question never has to special-case "no doctrine".
#:
#: **All of these are balance numbers and every one is tunable.** What is *not* negotiable
#: is the shape, and a test holds it: **every stance must move at least two levers in
#: opposite directions**, so no stance is strictly better than its opposite. The magnitudes
#: follow one rule with a reason behind it -- at most 15% on anything that reaches
#: `sim.resolve.compute_pressure`, because those compound through every contest of every
#: turn, and at most 30% on stocks, flows and accumulators a player can see coming and plan
#: around. `casualty_grief` is in the second group and not the first: war exhaustion is a
#: number a player watches climb and decides about, not a term in anybody's odds.
EFFECTS: dict[str, dict[str, dict[str, float]]] = {
    "mobilisation": {
        "mass": {
            # A third again as many people under arms, refilling half again as fast, at a
            # sixth less per formation -- and each of those formations is a tenth worse and
            # its losses are felt a quarter more keenly at home.
            "manpower_ceiling": 1.30,
            # **1.30 and not the 1.50 this was first written at.** The cap on a stock or a
            # flow is 30%, and a rule that is relaxed the first time it is inconvenient is
            # not a rule. The axis loses nothing: a higher ceiling and a faster approach to
            # it compound, so mass mobilisation still refills far more than a third faster
            # in practice -- which the budget test measures rather than assumes.
            "manpower_recovery": 1.30,
            "land_upkeep": 0.85,
            "raised_strength": 0.90,
            "casualty_grief": 1.25,
        },
        "professional": {
            # The 0.75 is the whole downside and it is meant to be severe: a professional
            # army that is destroyed is not rebuilt, and that is what the doctrine costs.
            "manpower_ceiling": 0.75,
            "manpower_recovery": 0.80,
            "land_upkeep": 1.25,
            "raised_strength": 1.12,
            "casualty_grief": 0.85,
        },
    },
    "nuclear_posture": {
        "retaliation": {
            # A deterrent that is harder to discount, harder to disarm, and answers in
            # force -- paid for every month, and remembered by everybody the day it is used.
            "deterrence": 1.15,
            "survivability": 1.15,
            "warheads_committed": 1.30,
            "arsenal_upkeep": 1.30,
            "outrage": 1.25,
        },
        "minimum": {
            # Most of the deterrent for a fraction of the money, and a first strike that
            # might actually find it.
            "deterrence": 0.88,
            "survivability": 0.85,
            "warheads_committed": 0.75,
            "arsenal_upkeep": 0.75,
            "outrage": 0.80,
        },
    },
    "alignment": {
        "collective_security": {
            # Promises that are worth something because they are kept -- and a country
            # that has said so is judged hardest when it is not, which is the whole of
            # what makes the promise worth anything.
            "unanswered_call": 1.30,
            "regard_for_answering": 1.30,
            # Promises kept in a state to be called on, and paid for accordingly.
            "pact_upkeep": 1.25,
        },
        "neutrality": {
            # No blocs, no obligations, and a public that likes it that way -- and nobody
            # coming when somebody comes for you, which is the part no multiplier says.
            # That downside is a refusal rather than a number: a neutral may not sign a
            # defensive pact or an alliance at all, so it has no allies to be let down by.
            "unanswered_call": 0.75,
            "regard_for_answering": 0.80,
            # A neutral that holds a pact at all holds it lightly -- and mostly does not
            # hold one, because it may not sign.
            "pact_upkeep": 0.75,
            "peacetime_legitimacy": 1.20,
        },
    },
}

#: **Levers where a larger multiplier is worse for whoever holds it.** Upkeep, grief,
#: outrage: paying more, minding your dead more, being hated more.
#:
#: Without this the no-free-lunch rule cannot be stated at all. It was first written as
#: "every stance moves at least one number up and one down", which silently assumed higher
#: is better -- and the nuclear axis broke it immediately, because massive retaliation
#: raises *five* numbers of which two are bills. The rule is about whether a stance helps
#: and hurts, and that needs to know which way each lever points.
LEVER_IS_A_COST: frozenset[str] = frozenset({
    "land_upkeep", "casualty_grief", "arsenal_upkeep", "outrage",
    "unanswered_call", "pact_upkeep",
})


def helps(lever: str, value: float) -> bool:
    """Whether this multiplier is good news for the nation that has it."""
    return value < 1.0 if lever in LEVER_IS_A_COST else value > 1.0


def hurts(lever: str, value: float) -> bool:
    return value > 1.0 if lever in LEVER_IS_A_COST else value < 1.0


# ------------------------------------------------------------------- the transition

#: **Balance number, tunable.** Months of *world time* a shift takes to bite. Not turns: a
#: doctrine shift is half a year of elapsed history whether the player is watching a month
#: or a year at a time, which is the rule armistices, sanctions and cooldowns already keep.
SHIFT_MONTHS = 6.0

#: **Balance number, tunable.** What reorganising during a war multiplies the dip by. It is
#: allowed, because it is exactly when a country would want to, and it is ruinous, because
#: it is exactly when it can least afford to be without either stance.
SHIFT_AT_WAR = 2.0

#: **Balance number, tunable.** Share of the treasury a shift costs on the day it starts.
SHIFT_TREASURY_SHARE = 0.15

#: **Balance number, tunable.** Stability paid on the politically charged axes only.
#: Reorganising your army is expensive; announcing that you are abandoning your allies is a
#: domestic event.
SHIFT_STABILITY = 2.0


@dataclass(frozen=True, slots=True)
class Doctrine:
    """One nation's answer on one axis, and whether it is in the middle of changing it."""

    nation_id: str
    axis: str
    #: What is in force. ``""`` while a shift is in flight -- see the module docstring.
    stance: str
    adopted_on: str = ""
    adopted_turn: int = 0
    shifting_to: str = ""
    shift_ends_on: str = ""

    @property
    def shifting(self) -> bool:
        return bool(self.shifting_to)

    def described(self) -> str:
        axis = axis_named(self.axis)
        if axis is None:
            return self.axis
        if self.shifting:
            to = axis.stance_named(self.shifting_to)
            return f"changing to {to.label if to else self.shifting_to}"
        held = axis.stance_named(self.stance)
        return held.label if held else self.stance or "none"


def doctrine_id_for(nation_id: str, axis: str) -> str:
    """Derived, not autoincremented, so a doctrine keeps its identity across a rewind.

    The same argument `military_objective`, `war_aim` and `war_effort` make: a rewind does
    not replay, so a row the engine rewrites next turn has to be addressable by what
    identifies it rather than by whatever number it happened to get.
    """
    return f"d_{nation_id}_{axis}"


# --------------------------------------------------------------------------- reading


def _read(row) -> Doctrine:
    return Doctrine(
        nation_id=str(row["nation_id"]), axis=str(row["axis"]), stance=str(row["stance"]),
        adopted_on=str(row["adopted_on"] or ""), adopted_turn=int(row["adopted_turn"] or 0),
        shifting_to=str(row["shifting_to"] or ""),
        shift_ends_on=str(row["shift_ends_on"] or ""))


def all_of(conn: sqlite3.Connection, world_id: str, nation_id: str) -> dict[str, Doctrine]:
    """Every axis this nation has answered, by axis id. ``{}`` for a world without them."""
    try:
        rows = conn.execute(
            "SELECT nation_id, axis, stance, adopted_on, adopted_turn, shifting_to,"
            "       shift_ends_on FROM doctrine_stance WHERE world_id = ? AND nation_id = ?",
            (world_id, nation_id)).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {str(row["axis"]): _read(row) for row in rows}


def stance_of(
    conn: sqlite3.Connection, world_id: str, nation_id: str, axis: str
) -> str:
    """What is in force on this axis for this nation. ``""`` for none, and while shifting.

    The one function every effect goes through, and it answers ``""`` for four different
    situations that all mean the same thing to a multiplier: no table, no row, an empty
    stance, and a shift in flight.
    """
    try:
        row = conn.execute(
            "SELECT stance FROM doctrine_stance WHERE world_id = ? AND nation_id = ? AND axis = ?",
            (world_id, nation_id, axis)).fetchone()
    except sqlite3.OperationalError:
        return ""
    return str(row["stance"] or "") if row else ""


def factor(
    conn: sqlite3.Connection, world_id: str, nation_id: str, axis: str, lever: str
) -> float:
    """The multiplier this nation's doctrine puts on ``lever``. Exactly ``1.0`` for none.

    **The whole interface between doctrines and everything else.** A system that wants to
    know what a doctrine does to its number asks for a multiplier and gets 1.0 when there is
    no answer -- so no caller needs a branch for "this world has no doctrines", "this nation
    has none", or "this one is mid-shift".
    """
    stance = stance_of(conn, world_id, nation_id, axis)
    if not stance:
        return 1.0
    return EFFECTS.get(axis, {}).get(stance, {}).get(lever, 1.0)


def as_lines(conn: sqlite3.Connection, world_id: str, nation_id: str) -> list[str]:
    """What this country is built to do, for a briefing. ``[]`` when it has no doctrine.

    Says the stance **and what it implies**, because a doctrine a staff is told the name of
    and not the consequences of is a label, and a label changes nobody's mind.
    """
    held = all_of(conn, world_id, nation_id)
    lines: list[str] = []
    for axis in AXES:
        doctrine = held.get(axis.id)
        if doctrine is None:
            continue
        if doctrine.shifting:
            to = axis.stance_named(doctrine.shifting_to)
            lines.append(
                f"- **{axis.label}:** changing to {to.label if to else doctrine.shifting_to}."
                " Until that is done you have the advantages of neither.")
            continue
        stance = axis.stance_named(doctrine.stance)
        if stance is None:
            continue
        lines.append(f"- **{axis.label}: {stance.label}.** {stance.implies}")
    return lines


# --------------------------------------------------------------------------- writing


@dataclass(frozen=True, slots=True)
class ShiftOutcome:
    """What happened when a country tried to change its mind."""

    ok: bool
    #: The refusal, when there is one. Carries what to write instead where one exists.
    refusal: Refusal | None = None
    #: What was started, for a report.
    described: str = ""
    #: What it cost, for a report.
    cost: float = 0.0
    #: The world date the new stance takes effect.
    ends_on: str = ""

    def rendered(self) -> str:
        if self.refusal is not None:
            return self.refusal.rendered()
        return self.described


def _at_war(conn: sqlite3.Connection, world_id: str, nation_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM relation WHERE world_id = ? AND nation_a = ? AND at_war = 1 LIMIT 1",
        (world_id, nation_id)).fetchone()
    return row is not None


def _refuse(conn, world_id: str, nation_id: str, axis: Axis | None,
            axis_id: str, stance_id: str) -> Refusal | None:
    """Every reason a shift is turned down, in one place, each naming its own gate."""
    if axis is None:
        return Refusal(
            "doctrine-axis",
            f"there is no doctrine called {axis_id!r}",
            instead="one of " + ", ".join(AXIS_IDS))
    if axis.applies is not None and not axis.applies(conn, world_id, nation_id):
        available = [stance.id for stance in offered_to(conn, world_id, nation_id)]
        return Refusal(
            "doctrine-applies",
            f"{axis.label.lower()} is not a question for this country",
            instead="the doctrines it can change: " + ", ".join(available)
                    if available else "")
    if axis.stance_named(stance_id) is None:
        return Refusal(
            "doctrine-stance",
            f"{stance_id!r} is not a stance on {axis.id}",
            instead="one of " + ", ".join(s.id for s in axis.stances))

    held = all_of(conn, world_id, nation_id)
    current = held.get(axis.id)
    if current is not None and current.shifting:
        return Refusal(
            "doctrine-settled",
            f"you are already changing {axis.label.lower()}, and it is not settled",
            instead=f"wait until {current.shift_ends_on}, then change it again if you still "
                    "want to" if current.shift_ends_on else "wait for it to settle")
    if current is not None and current.stance == stance_id:
        other = [s.id for s in axis.stances if s.id != stance_id]
        return Refusal(
            "doctrine-different",
            f"{axis.label.lower()} is already {stance_id}",
            instead=f"{other[0]} is the other answer to that question" if other else "")
    # **One axis at a time, across all of them.** A country reorganising its army and its
    # alliances in the same season is a country doing neither.
    elsewhere = [d for d in held.values() if d.shifting and d.axis != axis.id]
    if elsewhere:
        busy = elsewhere[0]
        return Refusal(
            "doctrine-one-at-a-time",
            f"you are already changing {busy.axis.replace('_', ' ')}; "
            "a country reorganises one thing at a time",
            instead=f"that one settles on {busy.shift_ends_on}; this can be started after"
                    if busy.shift_ends_on else "finish the change already under way first")
    return None


def adopt(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    nation_id: str,
    axis: str,
    stance: str,
    turn: int,
    date: str,
) -> ShiftOutcome:
    """Begin changing what this country is built to do, if it may and can pay.

    Checked before it is written and priced by the engine, so the *decision* belongs to
    whoever asked and nothing else does -- the same division `military_objective` keeps.
    """
    found = axis_named(axis)
    refusal = _refuse(conn, world_id, nation_id, found, axis, stance)
    if refusal is not None:
        return ShiftOutcome(False, refusal=refusal)
    assert found is not None  # _refuse returns a refusal when it is None

    row = conn.execute(
        "SELECT name, treasury, stability FROM nation WHERE id = ? AND world_id = ?",
        (nation_id, world_id)).fetchone()
    if row is None:
        return ShiftOutcome(False, refusal=Refusal(
            "doctrine-nation", f"there is no nation {nation_id!r} in this world",
            instead="name a power that exists in this world"))

    treasury = float(row["treasury"] or 0.0)
    if treasury <= 0:
        return ShiftOutcome(False, refusal=Refusal(
            "doctrine-afford",
            "an empty treasury cannot pay for a reorganisation",
            instead="wait until the books are in order"))


    months = SHIFT_MONTHS * (SHIFT_AT_WAR if _at_war(conn, world_id, nation_id) else 1.0)
    ends_on = after_months(date, months)
    charge = round(treasury * SHIFT_TREASURY_SHARE, 2)
    to = found.stance_named(stance)
    label = to.label if to else stance

    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.ORDER_ISSUED, origin=EventOrigin.ENGINE, actor_nation_id=nation_id,
        # Public: a country reorganising its army is not a secret, and the whole point of
        # the axis is that others can see what they are dealing with.
        text=(f"{row['name']} begins changing {found.label.lower()} to {label}. "
              f"Until {ends_on} it has the advantages of neither.")[:600],
        visibility=Visibility.PUBLIC,
    )

    current = all_of(conn, world_id, nation_id).get(axis)
    doctrine_id = doctrine_id_for(nation_id, axis)
    diffs: list[Diff] = []
    if current is None:
        diffs.append(Diff(
            "doctrine_stance", doctrine_id, "__insert__", DiffOp.INSERT,
            {"id": doctrine_id, "world_id": world_id, "nation_id": nation_id, "axis": axis,
             "stance": "", "adopted_on": "", "adopted_turn": 0,
             "shifting_to": stance, "shift_ends_on": ends_on},
            reason_event_id=event_id, visibility_scope=Visibility.PUBLIC))
    else:
        # **The stance is emptied now, not when the shift lands.** That is the dip, and
        # putting it here is what makes it cost something from the first day.
        diffs.append(Diff("doctrine_stance", doctrine_id, "stance", DiffOp.SET, "",
                          reason_event_id=event_id, visibility_scope=Visibility.PUBLIC))
        diffs.append(Diff("doctrine_stance", doctrine_id, "shifting_to", DiffOp.SET, stance,
                          reason_event_id=event_id, visibility_scope=Visibility.PUBLIC))
        diffs.append(Diff("doctrine_stance", doctrine_id, "shift_ends_on", DiffOp.SET, ends_on,
                          reason_event_id=event_id, visibility_scope=Visibility.PUBLIC))
    diffs.append(Diff("nation", nation_id, "treasury", DiffOp.ADJUST, -charge,
                      reason_event_id=event_id, visibility_scope=Visibility.PUBLIC))
    if found.political:
        # **Charged to the floor, because an unbounded charge is a severable one.**
        # `ECON-003` holds stability inside 0-100 and names the diff it condemns, so a
        # government at 1.0 taking a 2.0 charge had *that diff severed and the rest of the
        # batch accepted*: the treasury paid, the doctrine changed, and the political cost
        # of changing it silently did not happen. Reproduced by Codex, and it is the ninth
        # writer `bounds.absorbable` was written for -- its own docstring lists the other
        # eight. Charging what there is is also the truer reading: a government already at
        # nothing cannot be shaken further by announcing one more thing.
        diffs.append(Diff("nation", nation_id, "stability", DiffOp.ADJUST,
                          absorbable(conn, "nation", nation_id, "stability",
                                     -SHIFT_STABILITY),
                          reason_event_id=event_id, visibility_scope=Visibility.PUBLIC))

    outcome = apply_batch(conn, DiffBatch(
        world_id=world_id, turn=turn, proposer_role=Role.ENGINE, proposer_model=None,
        diffs=tuple(diffs)))
    if not outcome.applied:
        # **And the event goes with it.** The event is written before the batch, because a
        # diff has to cite a cause that already exists -- so a refused batch would otherwise
        # leave a public record of a reorganisation that did not begin, which the briefing
        # would then narrate. Retracted rather than left, the same way a refused proposal is.
        unrecord_events(conn, (event_id,))
        return ShiftOutcome(False, refusal=Refusal(
            "doctrine-record", f"the change could not be recorded: {outcome.reason}",
            # Nothing was written, so the honest answer is that it can simply be asked for
            # again -- a half-applied shift is the one thing this branch guarantees against.
            instead="nothing was changed; the order can be given again"))
    # **Severed is not applied, and reporting success on a severed cost is the bug this
    # whole fix is about.** `apply_batch` may drop a minority of diffs and accept the rest;
    # every charge above is clamped so that cannot happen to one, and this says so if it
    # ever does rather than quietly returning a shift nobody paid for.
    if outcome.validation.violations:
        return ShiftOutcome(False, refusal=Refusal(
            "doctrine-cost",
            "the change could not be charged for in full, so it was not made",
            instead="nothing was changed; the order can be given again"))
    return ShiftOutcome(
        True,
        described=f"{row['name']} is changing {found.label.lower()} to {label}, "
                  f"in force from {ends_on}",
        cost=charge, ends_on=ends_on)


def settle_shifts(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str
) -> list[str]:
    """Land every shift whose date has arrived. One line per country that finished.

    **Run every turn, and by date rather than by a countdown**, so a year-long jump lands
    the shifts that came due inside it and a monthly one lands only what actually matured.
    """
    try:
        rows = conn.execute(
            "SELECT id, nation_id, axis, shifting_to, shift_ends_on FROM doctrine_stance"
            " WHERE world_id = ? AND shifting_to <> ''", (world_id,)).fetchall()
    except sqlite3.OperationalError:
        return []

    due = []
    for row in rows:
        remaining = days_between(date, str(row["shift_ends_on"] or ""))
        # `None` means the date is unreadable, which is not the same as arrived: a row
        # nobody can date stays in transition rather than settling by accident.
        if remaining is not None and remaining <= 0:
            due.append(row)
    if not due:
        return []

    names = {str(r["id"]): str(r["name"]) for r in conn.execute(
        "SELECT id, name FROM nation WHERE world_id = ?", (world_id,))}
    landed: list[str] = []
    diffs: list[Diff] = []
    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date, kind=EventKind.ORDER_ISSUED,
        origin=EventOrigin.ENGINE,
        text=f"{len(due)} reorganisation(s) complete.", visibility=Visibility.PUBLIC)

    for row in due:
        axis = axis_named(str(row["axis"]))
        stance = axis.stance_named(str(row["shifting_to"])) if axis else None
        for field, value in (("stance", str(row["shifting_to"])),
                             ("shifting_to", ""), ("shift_ends_on", ""),
                             ("adopted_on", date), ("adopted_turn", turn)):
            diffs.append(Diff("doctrine_stance", str(row["id"]), field, DiffOp.SET, value,
                              reason_event_id=event_id, visibility_scope=Visibility.PUBLIC))
        landed.append(
            f"{names.get(str(row['nation_id']), row['nation_id'])}: "
            f"{axis.label.lower() if axis else row['axis']} is now "
            f"{stance.label if stance else row['shifting_to']}")

    apply_batch(conn, DiffBatch(
        world_id=world_id, turn=turn, proposer_role=Role.ENGINE, proposer_model=None,
        diffs=tuple(diffs)))
    return landed


def seed_doctrines(
    conn: sqlite3.Connection, world_id: str, doctrines: dict[str, dict[str, str]]
) -> int:
    """Write a scenario's historical doctrines. Returns how many rows were written.

    Direct SQL and not diffs, like every other thing `apply_politics` writes: this is world
    *creation*, before there is a turn to undo, and there is nothing behind turn zero for a
    rewind to restore. Anything written after that goes through :func:`adopt`.

    Silently skips an axis or stance the catalogue does not know, because a scenario naming
    one is a typo that should fail the audit rather than crash world creation.
    """
    written = 0
    for nation_id, per_axis in doctrines.items():
        for axis_id, stance_id in per_axis.items():
            axis = axis_named(axis_id)
            if axis is None or axis.stance_named(stance_id) is None:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO doctrine_stance (id, world_id, nation_id, axis, stance,"
                "  adopted_on, adopted_turn, shifting_to, shift_ends_on)"
                " VALUES (?, ?, ?, ?, ?, '', 0, '', '')",
                (doctrine_id_for(nation_id, axis_id), world_id, nation_id, axis_id,
                 stance_id))
            written += 1
    return written
