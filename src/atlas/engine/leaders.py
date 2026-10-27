"""Leaders: traits that bite, and governments that fall.

The nation table had ``stability`` and ``legitimacy`` moving every turn with nobody's name
attached to them. A coup was a number going down. This layer puts somebody in the chair,
gives them a handful of mechanical traits, and lets the numbers remove them.

Three things, in the order they matter:

**Traits are multipliers on levers that already existed.** No trait invents a mechanic. A
``bellicose`` government advances further on an offensive because it spends men it should
not; a ``technocrat`` gets more out of a public work; a ``hardliner`` holds occupied ground
down harder. Every one of those is a number some other module was already computing, and
:func:`modifier` is the single door they come through -- one function, one table, so a
trait cannot mean one thing in combat and another in the treasury.

**A government can be removed.** Each turn, every nation rolls against a risk built from
its own stability and legitimacy, modified by its leader's traits and by how the *last*
government came in -- a state that has had one coup is measurably likelier to have another,
which is the single most robust finding in the coup literature and also the thing that makes
a run of instability feel like a spiral rather than a sequence of unrelated dice. The roll is
seeded on ``(world, turn, nation)``, never on call order, so a world replays identically.

**Nothing here is proposed by a model.** The engine assesses and the engine writes, through
ordinary diffs citing one event, so a coup rewinds like anything else. A model may *read*
its leader in the briefing -- that is the whole point of the agenda -- but cannot appoint,
remove or re-trait one.

**A world with no leaders is unaffected.** Saves written before schema 13 have an empty
``leader`` table; :func:`modifier` returns ``1.0`` for a nation with no government and
:func:`assess_successions` takes no rolls, so an old world resolves on exactly the numbers
it always did.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from ..sim.rng import derive
from ..state.apply import apply_batch, record_event
from ..state.diff import Diff, DiffBatch
from ..state.models import DiffOp, EventKind, EventOrigin, Role, Visibility
from .constituencies import FRICTION_FROM, support_of
from .constituencies import RESTING as CIVIC_RESTING
from .pacing import compounded, span_of

__all__ = [
    "LEVERS",
    "TRAITS",
    "Leader",
    "ShockReport",
    "SuccessionReport",
    "accountability_of",
    "assess_successions",
    "coup_risk",
    "leader_of",
    "modifier",
    "removal_channel",
    "seat",
    "assess_shocks",
    "shock_assassination",
]


#: The levers a trait may pull. Deliberately few and deliberately named after the thing
#: they scale, so adding a trait is a table edit and adding a *lever* is a decision that
#: has to be made somewhere a reader will see it.
#:
#: * ``advance``   -- how far an offensive gets, multiplying :mod:`atlas.sim.resolve`.
#: * ``development`` -- what a public work is worth, multiplying the growth it buys.
#: * ``unrest``   -- the drag held foreign ground puts on stability. **Below 1.0 is good**
#:   for the holder: a hard government garrisons better.
#: * ``coup_risk`` -- the chance this government is removed. Below 1.0 is safer.
#: * ``gambit``   -- appetite for an undertaking with an uncertain outcome.
LEVERS: tuple[str, ...] = ("advance", "development", "unrest", "coup_risk", "gambit")


#: Every trait, as multipliers on the levers it touches. Absent lever means 1.0.
#:
#: **Each trait costs something.** There is no trait in this table that is only good, which
#: is what stops the layer becoming a free bonus for whoever happens to be seeded well: a
#: ``bellicose`` government wins ground and invites its own overthrow; a ``technocrat``
#: builds well and holds conquered ground badly; ``cautious`` is safe and slow. The numbers
#: are modest on purpose -- a leader should tilt a country, not replace it.
TRAITS: dict[str, dict[str, float]] = {
    "bellicose": {"advance": 1.14, "gambit": 1.25, "coup_risk": 1.15},
    "cautious": {"advance": 0.93, "gambit": 0.70, "coup_risk": 0.85},
    "technocrat": {"development": 1.25, "unrest": 1.05, "gambit": 0.90},
    "reformer": {"development": 1.15, "coup_risk": 1.20, "unrest": 0.95},
    "populist": {"coup_risk": 0.85, "unrest": 1.10, "gambit": 1.10},
    "hardliner": {"unrest": 0.80, "coup_risk": 0.90, "development": 0.95},
    "ideologue": {"advance": 1.08, "development": 0.90, "coup_risk": 1.10},
    "pragmatist": {"coup_risk": 0.85, "advance": 0.97},
    "dealmaker": {"gambit": 1.20, "advance": 0.95, "coup_risk": 0.95},
    "isolationist": {"advance": 0.90, "development": 1.08, "gambit": 0.80},
}

#: Base per-year chance a government falls, before anything modifies it. This is the rate
#: for a *settled* country, and it is the whole story for most of the map: measured on the
#: 2026 seed, **226 of 243 nations sit at exactly the same stability and legitimacy**, so
#: the quadratic term below is zero for all of them and this constant alone decides. Set
#: at the observed real-world rate for countries that are not in trouble -- roughly one
#: government in two hundred per year -- which puts about eight changes a decade somewhere
#: on the map. Any larger and a world of identical minors becomes a lottery; any smaller
#: and a settled country can never surprise anyone.
_BASE_RISK = 0.005

#: Where danger starts. Stability and legitimacy both run 0-100 and both seed at 50, so a
#: curve that charges for every point below 100 charges *the whole map, every turn*.
#: **Measured on the 2026 map before this threshold existed: 408 governments fell in ten
#: years across 243 nations, and the ones that fell averaged 57.0 against the survivors'
#: 57.4** -- which is not a simulation of instability, it is a lottery with extra steps.
#: Only the bottom half of the scale is dangerous; above it a country is merely governed.
_DANGER_BELOW = 50.0

#: How much the bottom half adds, at its worst. **Quadratic in the shortfall**, so risk
#: climbs slowly as a state starts to slip and sharply once it is genuinely coming apart --
#: which is both the shape the historical record has and the shape that gives a player time
#: to notice and answer. Weighted toward legitimacy, because a government can be unpopular
#: and survive far longer than it can be seen as having no right to the job.
_RISK_FROM_INSTABILITY = 0.10
_RISK_FROM_ILLEGITIMACY = 0.18

#: **Where a constituency starts being dangerous.** Not `_DANGER_BELOW`: support seeds near
#: the middle of the scale for most of the map, and a curve that charged for every point
#: below 50 would make a coup risk out of an officer corps that is merely unenthusiastic --
#: the exact lottery-with-extra-steps defect `_DANGER_BELOW` was introduced to fix. This is
#: the top of the *restive* band, so the charge begins where the layer's own vocabulary says
#: a group has turned against its government.
_ANGER_BELOW = FRICTION_FROM

#: What a mutinous officer corps and a government's own establishment add, at their worst.
#: **Quadratic in the shortfall, like the two above**, so a grumbling army is nothing and a
#: hostile one is the largest single term in the formula.
#:
#: The army is weighted heaviest because it is the answer to a question stability and
#: legitimacy never asked: those two say a country is in trouble, and this says who is going
#: to do something about it. The establishment is real but quieter -- a state whose officials
#: and courts have given up on a government removes it with a signature more often than with
#: a tank, and either way it happens.
_RISK_FROM_MUTINY = 0.14
_RISK_FROM_DISOWNED = 0.07

#: And what an army that would rather keep this government takes off the whole risk. **The
#: half that makes the officer corps worth courting rather than merely worth fearing**: a
#: government the army is devoted to is not immune, but the people best placed to remove it
#: are not going to.
_LOYALTY_RELIEF = 0.35

#: What having taken power by force does to the odds of losing it the same way. The
#: single most robust regularity in the literature, and mechanically it is what turns a bad
#: run into a spiral instead of a sequence of unrelated dice.
_COUP_TRAP = 1.9

#: No government may exceed this chance of falling in one year, however bad things get.
#: A collapsing state should be a mounting risk the player can answer -- stabilise,
#: legitimise, or hand something back -- never a coin flip that removes them without
#: warning.
_MAX_RISK = 0.30

#: What a coup does to the country it happens in, over and above changing the name. A new
#: government arrives with a mandate it has not earned.
#:
#: **Clamped against what the country actually has.** The obvious version -- a flat
#: subtraction -- takes a state at stability 4 to -4, and rule ECON-003 then refuses the
#: whole batch, so the government fell in the report and did not fall in the database. The
#: worse states are exactly the ones this fires on, so that was not an edge case: it was
#: the common case.
_COUP_STABILITY = -8.0
_COUP_LEGITIMACY = -12.0

#: What a coup cannot take a country below. Not zero: a state pinned at exactly zero has
#: nothing left for the *next* shock to bite on, and the point of a spiral is that each
#: turn of it still costs something.
_COUP_FLOOR = 1.0

# ------------------------------------------------------- and who gets to do the removing

#: What a country's own people add to the odds, and it is smaller than the army's for a
#: reason that is not sentimental: a hostile population is the *commonest* thing in this
#: table and the slowest-acting. Crowds bring governments down, and they do it after years
#: of the thing the army does in an afternoon.
_RISK_FROM_STREETS = 0.09

#: **THE ACCOUNTABILITY PROXY, AND IT IS A GUESS ALEX SHOULD OVERRULE IF IT READS WRONG.**
#:
#: The question C2 asks is *which* group can remove this government, and the honest answer
#: is that nothing in the schema knows. There is no government-type column, and the seeded
#: worlds are no help: every one of the 74, 148 and 242 governments on the three shipped
#: maps has ``came_to_power = 'seeded'``, and 226 of 243 nations share one stability and one
#: legitimacy, so neither of those discriminates either.
#:
#: What the scenarios *do* author is a **title**, for the powers a player actually deals
#: with -- President, Prime Minister, Bundeskanzler on one side; Fuehrer, Duce, General
#: Secretary, Supreme Leader, Generalissimo on the other. That is a real authored signal, it
#: is already in the save, it needs no new schema, and overruling it is one line in the two
#: tuples below.
#:
#: **It covers the great powers and nobody else**, and what happens to the rest is the other
#: half of the decision. 63 of 74 governments in 1936 are titled "the government", which is
#: the scenario declining to say -- and a state with no stated way of changing its government
#: changes it by force, so those default to the army.
#:
#: Not to "whoever is angrier", which was tried and measured first: the people's seeded
#: standing is derived from stability, and stability is what makes a government fall, so the
#: populace of a collapsing state is angry *by construction* and the electoral channel won
#: nearly every ambiguous case. That is one number counted twice, and it would have flipped
#: the default behaviour of some two hundred nations per world on a coincidence of the seed.
#:
#: An untitled country can still be swept away -- `_CHANNEL_TILT` leaves the popular channel
#: at a third of the army's weight rather than at zero, so a populace three times angrier
#: than the officer corps still carries it. That is a revolution, and it should take that.
#:
#: The alternative considered and rejected: an additive ``nation.accountability`` column
#: defaulted per scenario. It is a better *store* and a worse *answer* -- somebody would have
#: had to invent a government type for 243 countries, which is a regime system arriving by
#: the back door in a batch that was told not to build one.
_ELECTED_TITLES: tuple[str, ...] = (
    "president", "prime minister", "chancellor", "bundeskanzler", "taoiseach",
    "premier", "president du conseil", "président du conseil", "presidente del consiglio",
    "chief minister", "governor-general",
)
_UNELECTED_TITLES: tuple[str, ...] = (
    "fuhrer", "führer", "duce", "general secretary", "first secretary", "supreme leader",
    "generalissimo", "marshal", "king", "queen", "emperor", "empress", "shah",
    "crown prince", "sultan", "chairman", "caudillo", "regent", "dictator", "khan",
)

#: What a scenario that did not say gets. Same value as a named autocracy, because the two
#: are the same claim about the world: nothing here says this government can be voted out.
_UNSTATED = 0.0

#: How far accountability may tilt the contest between the channels. At 1.0 an electoral
#: state weighs popular anger three times what it weighs the army's, and an autocracy the
#: reverse -- but neither ever *silences* the other, because a democracy can still be couped
#: and a junta can still be swept away.
_CHANNEL_TILT = 0.5

#: **What it takes for the street to beat the barracks**, over and above being angrier.
#: Removing a government from below needs mass mobilisation and removing it from within needs
#: a colonel, and the asymmetry is not a detail -- without it the popular channel wins nearly
#: every ambiguous case, because the people's seeded standing is derived from stability and
#: stability is what makes governments fall. Measured: a state at 20/15 has a populace at 38
#: against an officer corps at 42.8, and fell to a rising it had not earned.
_STREET_MARGIN = 0.15

#: What being voted out does, against what being deposed does. **A country that removes a
#: government the way its own rules provide for is not damaged by it** -- the system worked,
#: which is close to the definition of legitimacy -- so this costs a little stability, in the
#: churn of a change of government, and *pays* legitimacy rather than charging it. That
#: asymmetry is the whole reason the two channels are worth telling apart.
_ELECTION_STABILITY = -2.0
_ELECTION_LEGITIMACY = 4.0

#: And what a rising does, which is worse than a coup. A coup replaces a government; a
#: revolution rejects the order that produced it, and the country has to rebuild the claim
#: that anybody has a right to govern it at all.
_REVOLUTION_STABILITY = -12.0
_REVOLUTION_LEGITIMACY = -16.0


@dataclass(frozen=True, slots=True)
class Leader:
    """One government, as the engine sees it."""

    id: str
    nation_id: str
    name: str
    title: str
    traits: tuple[str, ...]
    agenda: str
    took_power_turn: int
    came_to_power: str

    @property
    def described(self) -> str:
        """How to refer to this government in prose.

        An unnamed government is not a missing one -- see
        :mod:`atlas.scenarios.leaders` for why the modern table has no names -- so it
        needs a form of words that reads as deliberate rather than as a bug.
        """
        if self.name:
            return f"{self.name}, {self.title}" if self.title else self.name
        return self.title or "the government"


@dataclass(slots=True)
class SuccessionReport:
    assessed: int = 0
    #: Governments removed this turn, in the words the report shows.
    fell: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _row_to_leader(row: sqlite3.Row) -> Leader:
    try:
        traits = tuple(str(t) for t in json.loads(row["traits_json"] or "[]"))
    except (ValueError, TypeError):
        # A malformed traits list is flavour that failed to parse, not a reason to lose
        # the government. Same tolerance `tech.effects` has.
        traits = ()
    return Leader(
        id=str(row["id"]),
        nation_id=str(row["nation_id"]),
        name=str(row["name"]),
        title=str(row["title"]),
        traits=traits,
        agenda=str(row["agenda"]),
        took_power_turn=int(row["took_power_turn"]),
        came_to_power=str(row["came_to_power"]),
    )


def leader_of(conn: sqlite3.Connection, nation_id: str) -> Leader | None:
    """The current government of one nation, or ``None`` if it has no leader row.

    No ``world_id``: ``nation.id`` is a global primary key, so scoping by world would add
    nothing but an opportunity to pass the wrong one. That matters here because this is
    called from deep inside the adjudicator, where a `Contest` knows who is fighting and
    has no reason to know which world it is in.

    ``None`` is the honest answer for a world written before schema 13, and every caller
    treats it as "no modifiers, no succession" rather than as an error.
    """
    try:
        row = conn.execute(
            "SELECT id, nation_id, name, title, traits_json, agenda, took_power_turn,"
            "       came_to_power"
            "  FROM leader WHERE nation_id = ? AND is_current = 1",
            (nation_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        # The table does not exist: a pre-migration connection. Not an error either.
        return None
    return _row_to_leader(row) if row else None


def modifier(conn: sqlite3.Connection, nation_id: str, lever: str) -> float:
    """How this nation's government scales one lever. ``1.0`` when it has none.

    Traits compose multiplicatively, which is the only composition that stays sane as
    traits are added: three traits that each touch ``advance`` give a product, not a sum
    that can pass 2.0 or go negative. A trait name the table does not know is ignored,
    so a scenario may carry flavour ahead of the mechanic for it.
    """
    if lever not in LEVERS:
        raise ValueError(f"unknown leader lever: {lever!r}")
    leader = leader_of(conn, nation_id)
    if leader is None:
        return 1.0
    scale = 1.0
    for trait in leader.traits:
        scale *= TRAITS.get(trait, {}).get(lever, 1.0)
    return scale


def seat(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    nation_id: str,
    name: str,
    title: str,
    traits: tuple[str, ...],
    agenda: str,
    turn: int,
    event_id: int,
    came_to_power: str = "seeded",
) -> str:
    """Put a government in the chair, standing down whatever was there.

    **Written as diffs, because a rewind has to be able to put the old government back.**
    This was direct SQL, on the reasoning that "who sits in the chair is restored by the
    rewind replaying that same turn" -- and a rewind does not replay. PT31's laboratory
    caught it in a live run and the isolated case is stark: a coup on turn 3, rewound to
    turn 2, left the junta in the chair. `leader` has been in `_FINGERPRINT_TABLES` since
    decision 101 for exactly this reason, so the digest moved and the world did not.

    ``event_id`` is required rather than optional: CORE-001 refuses a diff that cites
    nothing, and an optional citation would mean an optional rewind.

    The two writes are one batch and their order matters -- a partial unique index allows
    one current government per nation, so the outgoing row must be stood down before the
    incoming one is inserted. Diffs apply in sequence, which is what makes that safe.
    """
    standing = conn.execute(
        "SELECT id FROM leader WHERE world_id = ? AND nation_id = ? AND is_current = 1",
        (world_id, nation_id),
    ).fetchone()

    leader_id = f"ldr:{nation_id}:{turn}"
    # A nation that changes government twice on the same turn would collide. Vanishingly
    # rare and not worth a sequence table, but it must not raise: suffix and move on.
    existing = {
        str(r["id"])
        for r in conn.execute(
            "SELECT id FROM leader WHERE world_id = ? AND nation_id = ?",
            (world_id, nation_id),
        )
    }
    if leader_id in existing:
        suffix = 2
        while f"{leader_id}:{suffix}" in existing:
            suffix += 1
        leader_id = f"{leader_id}:{suffix}"
    diffs: list[Diff] = []
    if standing is not None:
        diffs += [
            Diff("leader", str(standing["id"]), "is_current", DiffOp.SET, 0,
                 reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),
            Diff("leader", str(standing["id"]), "left_power_turn", DiffOp.SET, turn,
                 reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),
        ]
    diffs.append(
        Diff("leader", leader_id, "__insert__", DiffOp.INSERT,
             {
                 "world_id": world_id, "nation_id": nation_id, "name": name,
                 "title": title, "traits_json": json.dumps(list(traits)),
                 "agenda": agenda, "took_power_turn": turn,
                 "came_to_power": came_to_power, "is_current": 1,
             },
             reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
    )
    outcome = apply_batch(
        conn,
        DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                  proposer_model=None, diffs=tuple(diffs)),
    )
    if not outcome.applied:
        raise RuntimeError(f"a government could not be seated: {outcome.reason}")
    return leader_id


def coup_risk(
    conn: sqlite3.Connection, nation_id: str, *,
    stability: float, legitimacy: float,
) -> float:
    """The per-year chance this government falls. ``0.0`` for a nation with no leader.

    Pure enough to test directly, which matters: this is the number that decides whether
    the layer is drama or noise, and it should be arguable without running a world.

    **Stability and legitimacy say a country is in trouble; they never say who is going to
    do something about it.** So the officer corps and the establishment are read here too --
    the two groups that actually remove a government -- and a devoted army takes something
    off, because the people best placed to mount a coup are also the people who can decline
    to. Same formula, same cap, two more terms: a second coup mechanism beside this one
    would be a second set of odds to drift out of step.
    """
    leader = leader_of(conn, nation_id)
    if leader is None:
        return 0.0
    shaky = max(0.0, (_DANGER_BELOW - min(_DANGER_BELOW, stability)) / _DANGER_BELOW)
    lawless = max(0.0, (_DANGER_BELOW - min(_DANGER_BELOW, legitimacy)) / _DANGER_BELOW)
    army = support_of(conn, nation_id, "army")
    mutinous = _fed_up(army)
    disowned = _fed_up(support_of(conn, nation_id, "establishment"))
    streets = _fed_up(support_of(conn, nation_id, "people"))
    risk = (
        _BASE_RISK
        + _RISK_FROM_INSTABILITY * shaky * shaky
        + _RISK_FROM_ILLEGITIMACY * lawless * lawless
        + _RISK_FROM_MUTINY * mutinous * mutinous
        + _RISK_FROM_DISOWNED * disowned * disowned
        + _RISK_FROM_STREETS * streets * streets
    )
    # Applied to the whole risk rather than subtracted from it, so a loyal army protects a
    # government that is in trouble for other reasons -- which is the historically usual
    # case and the one worth playing for.
    risk *= 1.0 - _LOYALTY_RELIEF * max(0.0, (army - CIVIC_RESTING) / 50.0)
    risk *= modifier(conn, nation_id, "coup_risk")
    if leader.came_to_power in ("coup", "revolution"):
        risk *= _COUP_TRAP
    return min(_MAX_RISK, max(0.0, risk))


def _fed_up(support: float) -> float:
    """How far past the restive line a constituency has gone, 0 to 1."""
    return max(0.0, (_ANGER_BELOW - min(_ANGER_BELOW, support)) / _ANGER_BELOW)


def accountability_of(conn: sqlite3.Connection, nation_id: str) -> float:
    """Whether this government can be removed at the ballot box. See `_ELECTED_TITLES`.

    ``1.0`` it answers to the people, ``0.0`` it answers to whoever holds the guns. A
    scenario that named no office gets ``0.0`` -- see the note on `_ELECTED_TITLES` for why
    the unstated case defaults to force rather than to a contest.
    """
    leader = leader_of(conn, nation_id)
    if leader is None:
        return _UNSTATED
    title = leader.title.strip().lower()
    if any(word in title for word in _UNELECTED_TITLES):
        return 0.0
    if any(word in title for word in _ELECTED_TITLES):
        return 1.0
    return _UNSTATED


def removal_channel(conn: sqlite3.Connection, nation_id: str) -> str:
    """How this government would fall if it fell today.

    **Which group, not whether.** :func:`coup_risk` says a government is in danger; this says
    who from, and what the country's own rules make of that. Two questions, three answers:

    * the people are decisively the angrier, and there is a ballot box -- ``election``
    * the people are decisively the angrier, and there is not -- ``revolution``
    * anything else -- ``coup``

    The third is the default rather than a fallback. A government removed for no stated
    reason is removed by the people best placed to remove it, which the officer corps has
    always been; and the popular channel has to clear `_STREET_MARGIN` to beat them, because
    a crowd is not a colonel.

    **A revolution is not an election with worse manners.** It is what the popular channel
    becomes where the polity offers no other way of using it, and it costs the country more
    than a coup does -- see `_REVOLUTION_LEGITIMACY`. It is also the answer to the obvious
    objection to a title-based proxy: a country nobody titled is not thereby immune to its
    own people.
    """
    army = _fed_up(support_of(conn, nation_id, "army"))
    streets = _fed_up(support_of(conn, nation_id, "people"))
    if streets <= 0:
        return "coup"
    accountable = accountability_of(conn, nation_id)
    by_people = streets * (1.0 - _CHANNEL_TILT + 2 * _CHANNEL_TILT * accountable)
    by_army = army * (1.0 + _CHANNEL_TILT - 2 * _CHANNEL_TILT * accountable)
    if by_people <= by_army + _STREET_MARGIN:
        return "coup"
    return "election" if accountable > 0.0 else "revolution"


def _successor(leader: Leader) -> tuple[tuple[str, ...], str]:
    """Who comes next: harder than who went, and with something to prove.

    A coup does not produce a random government. It produces the faction that was able to
    mount one, which in practice means the army or the security apparatus -- so the
    successor is reliably harder-line than its predecessor and keeps whatever else that
    predecessor was. Keeping the rest is what makes a country still feel like itself
    three governments in.
    """
    traits = [t for t in leader.traits if t not in ("reformer", "cautious", "dealmaker")]
    if "hardliner" not in traits:
        traits.append("hardliner")
    if len(traits) < 2:
        traits.append("pragmatist")
    return tuple(traits[:3]), "restore order and settle accounts"


def _voted_in(leader: Leader) -> tuple[tuple[str, ...], str]:
    """And who comes next when the country was asked.

    The mirror of `_successor`, and deliberately so: an electorate that has just thrown a
    government out does not replace it with a harder version of itself. It returns the
    faction that campaigned against whatever was wrong, which in practice means somebody
    promising to spend on the people who voted -- and the country keeps the rest of its
    character, for the same reason a coup does.
    """
    traits = [t for t in leader.traits if t not in ("hardliner", "bellicose", "ideologue")]
    if "populist" not in traits:
        traits.append("populist")
    if len(traits) < 2:
        traits.append("reformer")
    return tuple(traits[:3]), "answer the people who put us here"


def assess_successions(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    turn: int,
    date: str,
    seed: int,
    days: int = 30,
) -> SuccessionReport:
    """Let shaky governments fall, once per nation per turn.

    The risk is a *rate*, so a season-long turn carries the several months of exposure it
    replaces rather than one month -- otherwise a player could outrun their own
    instability by taking longer turns, which is the exact pacing bug
    :mod:`atlas.engine.pacing` exists to prevent. Over the **continuous** span, not a
    rounded count of rounds: see the comment on the draw.

    One seeded stream per nation per turn, keyed on where and when rather than on call
    order, so the same world replays identically however the loop is scheduled.
    """
    report = SuccessionReport()
    try:
        nations = conn.execute(
            "SELECT n.id AS id, n.name AS name, n.stability AS stability,"
            "       n.legitimacy AS legitimacy"
            "  FROM nation n JOIN leader l ON l.nation_id = n.id AND l.is_current = 1"
            " WHERE n.world_id = ? AND n.is_alive = 1 ORDER BY n.id",
            (world_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return report
    if not nations:
        return report

    span = span_of(days)
    fallen: list[tuple[str, str, Leader, float, float]] = []
    for nation in nations:
        report.assessed += 1
        nation_id = str(nation["id"])
        risk = coup_risk(
            conn, nation_id,
            stability=float(nation["stability"]),
            legitimacy=float(nation["legitimacy"]),
        )
        if risk <= 0.0:
            continue
        rng = derive(seed, world_id, turn, f"succession:{nation_id}")
        # **The span is continuous; `rounds` is not.** This used to draw once per
        # *engine round*, and `rounds` answers a different question -- how many discrete
        # months of fighting to grind out -- by rounding the span to a whole number. The
        # fractional remainder was simply lost. A 75-day turn is 2.5 months and drew
        # twice, so a government got a sixth of a year of instability for free; a
        # seven-day turn is 0.233 months and drew *once*, charging a week four months of
        # risk. Long turns undercounted and short ones overcounted, from the same line.
        #
        # `compounded` is the exact statement of "this monthly chance, for this many
        # months", and it is correct for a fractional span in both directions. One draw
        # against it is distributionally identical to N monthly draws and does not need N
        # to be a whole number.
        monthly = 1.0 - (1.0 - risk) ** (1.0 / 12.0)
        if rng.random() >= compounded(monthly, span):
            continue
        leader = leader_of(conn, nation_id)
        if leader is not None:
            fallen.append((nation_id, str(nation["name"]), leader,
                           float(nation["stability"]), float(nation["legitimacy"])))

    if not fallen:
        return report

    diffs: list[Diff] = []
    for nation_id, nation_name, leader, stability, legitimacy in fallen:
        # **How it falls, decided before anything is written.** Same risk, same roll, same
        # machinery -- this only chooses which door the government leaves by, and whether
        # the country is damaged by its going.
        channel = removal_channel(conn, nation_id)
        voted_out = channel == "election"
        risen = channel == "revolution"
        event_id = record_event(
            conn, world_id=world_id, turn=turn, date=date,
            kind={"election": EventKind.ELECTION,
                  "revolution": EventKind.REVOLUTION}.get(channel, EventKind.COUP),
            origin=EventOrigin.ENGINE,
            text={
                "election": f"{leader.described} was voted out in {nation_name}.",
                "revolution": (f"{leader.described} was swept away in {nation_name}: "
                               "the country would not have it any longer."),
            }.get(channel, f"{leader.described} no longer governs {nation_name}."),
            visibility=Visibility.PUBLIC,
        )
        traits, agenda = (
            _voted_in(leader) if voted_out or risen else _successor(leader))
        seat(
            conn, world_id, nation_id=nation_id, name="", title=leader.title,
            traits=traits, agenda=agenda, turn=turn, event_id=event_id,
            came_to_power=channel,
        )
        # Never below the floor: see `_COUP_FLOOR`. A country cannot lose legitimacy it
        # does not have, and a batch that tried would be refused whole -- leaving a
        # government that fell in the report and did not fall in the database.
        if risen:
            lost_stability = -min(
                -_REVOLUTION_STABILITY, max(0.0, stability - _COUP_FLOOR))
            lost_legitimacy = -min(
                -_REVOLUTION_LEGITIMACY, max(0.0, legitimacy - _COUP_FLOOR))
        elif voted_out:
            lost_stability = -min(
                -_ELECTION_STABILITY, max(0.0, stability - _COUP_FLOOR))
            # Charged against the ceiling for the same reason a coup is charged against the
            # floor: ECON-003 holds legitimacy inside 0-100 and condemns the whole batch,
            # and this is the only diff in the game that pushes it *up* on a government
            # falling.
            lost_legitimacy = min(_ELECTION_LEGITIMACY, max(0.0, 100.0 - legitimacy))
        else:
            lost_stability = -min(-_COUP_STABILITY, max(0.0, stability - _COUP_FLOOR))
            lost_legitimacy = -min(-_COUP_LEGITIMACY, max(0.0, legitimacy - _COUP_FLOOR))
        diffs += [
            Diff("nation", nation_id, "stability", DiffOp.ADJUST, lost_stability,
                 reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),
            Diff("nation", nation_id, "legitimacy", DiffOp.ADJUST, lost_legitimacy,
                 reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),
        ]
        report.fell.append(
            f"{nation_name}: {leader.described} was "
            + {"election": "voted out", "revolution": "swept away"}.get(
                channel, "removed"))

    outcome = apply_batch(
        conn,
        DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                  proposer_model=None, diffs=tuple(diffs)),
    )
    if not outcome.applied:
        report.problems.append(f"succession could not be applied: {outcome.reason}")
    return report


# ------------------------------------------------------------------ a killing, on demand

#: What a killing costs the state, against an ordinary coup's -8 and -12.
#:
#: **Roughly double, and the reasoning is that the two removals are not the same event.**
#: A coup is the end of a crisis the country has been living through: by the time the
#: colonels move, everybody knew. An assassination is the *start* of one, and it arrives
#: with no warning, no successor prepared and no story anybody has agreed on yet. The
#: numbers are a first setting to be tuned against a played turn, not a claim.
#:
#: Clamped against what the country actually has, exactly as `_COUP_STABILITY` is, and for
#: the same reason: ECON-003 holds legitimacy inside 0-100 and condemns the whole batch, so
#: an unclamped hit on a shaky state means a government that fell in the report and did not
#: fall in the database. The worse states are the interesting ones, so that is the common
#: case and not an edge.
_SHOCK_STABILITY = -16.0
_SHOCK_LEGITIMACY = -20.0

#: What the army feels. **The one effect with no precedent in the fall path**, and the
#: thing that makes this a military shock rather than only a political one: the succession
#: layer has never touched a formation. A flat knock on every formation the nation has,
#: floored at zero.
_SHOCK_MORALE = -10.0

#: What a suspected sponsor loses in the victim's eyes. Below DIP-002's 25-point per-batch
#: cap on purpose -- this is one move among possibly several in the turn, and a shock that
#: spent the whole allowance would refuse whatever else the turn wanted to say.
_SHOCK_BLAME = -20.0


@dataclass(slots=True)
class ShockReport:
    """What one killing did, in the words the turn report shows."""

    happened: bool = False
    nation_id: str = ""
    #: One sentence, already written for a reader.
    text: str = ""
    #: Aims the dead government had declared, which die with it.
    aims_cleared: int = 0
    formations_shaken: int = 0
    mobilisation_said: str = ""
    problems: list[str] = field(default_factory=list)

    def describe(self) -> str:
        return self.text or "nothing happened"


def shock_assassination(  # noqa: PLR0913 - a shock is named by all of these
    conn: sqlite3.Connection,
    world_id: str,
    *,
    nation_id: str,
    turn: int,
    date: str,
    sponsor: str | None = None,
    mobilise: bool = True,
) -> ShockReport:
    """Kill a government, from a cause its own numbers did not predict.

    **Why this exists at all.** Everything else in this module removes a government the
    country's own stability and legitimacy said was removable: a fall is the last step of a
    spiral the world can see coming. Nothing here could make something happen *to* a nation
    that the nation had not been earning. That gap is a missing game feature -- the thing
    every alternate-history sandbox wants -- and a missing instrument: every EVE match so
    far has compared model families on decisions they chose, and this is the one way to
    compare them on a decision forced on them.

    **It is not a model's to fire, and that is structural rather than checked.** There is
    deliberately no order type and no batch field that reaches this function. It is called
    by the engine, from :meth:`TurnEngine.jump`'s ``shocks`` argument or from a scenario's
    own opt-in rate, both of which are filled in by the harness or the CLI and never by a
    parsed answer. The alternative -- registering an ``assassinate`` handler beside
    ``raise_units`` -- would put it one schema field away from every tracked agent in the
    game, and every match would become a murder contest, which is a different game.

    **Everything it does goes through a door that already exists**: :func:`seat` for the
    chair, diffs for stability, legitimacy, morale and the blame, ``DiffOp.DELETE`` for the
    aims, and ``mobilisation.set_level`` for the footing. Nothing writes SQL directly, which
    is what keeps a rewind honest -- `seat` was converted from raw SQL to diffs precisely
    because a rewound coup used to leave the junta in the chair.

    ``sponsor`` is optional and is never inferred. An unattributed killing is a different
    game from one everybody blames on Washington, and attribution is a whole mechanic
    elsewhere (:mod:`atlas.engine.attribution`); guessing here would be a second, worse copy
    of it. Naming one costs that sponsor standing in the victim's eyes and nothing else --
    no war is declared, because who declares what is the players' business.
    """
    report = ShockReport(nation_id=nation_id)
    nation = conn.execute(
        "SELECT name, stability, legitimacy, is_alive FROM nation"
        " WHERE world_id = ? AND id = ?",
        (world_id, nation_id),
    ).fetchone()
    if nation is None or not int(nation["is_alive"]):
        report.problems.append(f"there is no living nation called {nation_id!r} to strike")
        return report
    leader = leader_of(conn, nation_id)
    if leader is None:
        # A world seeded before schema 13 has no governments at all. Saying so beats
        # raising, because the caller is a study harness that should carry on.
        report.problems.append(f"{nation['name']} has no government to remove")
        return report

    nation_name = str(nation["name"])
    stability = float(nation["stability"])
    legitimacy = float(nation["legitimacy"])
    blamed = ""
    if sponsor:
        row = conn.execute(
            "SELECT name FROM nation WHERE world_id = ? AND id = ?", (world_id, sponsor)
        ).fetchone()
        blamed = str(row["name"]) if row is not None else ""

    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        # **`leader_change`, not `coup`.** Both describe a government going, and `coup` is
        # what the fall path writes -- but `coup` is a GATING_EVENT_KIND, which means it
        # unlocks privileged mutations for anything that cites it. A shock has no business
        # handing out that key, and `leader_change` says what happened without it.
        kind=EventKind.LEADER_CHANGE,
        origin=EventOrigin.ENGINE,
        text=(f"{leader.described} was assassinated in {nation_name}"
              + (f"; {blamed} is blamed." if blamed else ".")),
        actor_nation_id=sponsor if blamed else None,
        visibility=Visibility.PUBLIC,
    )

    # `came_to_power='coup'` rather than a new value: the schema's CHECK is a closed list
    # and a fourth entry is a migration. The event text above carries the distinction, and
    # if the chronicle later wants to query on it, that is when the migration earns itself.
    traits, agenda = _successor(leader)
    seat(
        conn, world_id, nation_id=nation_id, name="", title=leader.title,
        traits=traits, agenda=agenda, turn=turn, event_id=event_id,
        came_to_power="coup",
    )

    diffs: list[Diff] = [
        Diff("nation", nation_id, "stability", DiffOp.ADJUST,
             -min(-_SHOCK_STABILITY, max(0.0, stability - _COUP_FLOOR)),
             reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),
        Diff("nation", nation_id, "legitimacy", DiffOp.ADJUST,
             -min(-_SHOCK_LEGITIMACY, max(0.0, legitimacy - _COUP_FLOOR)),
             reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),
    ]

    for unit in conn.execute(
        "SELECT id, morale FROM unit WHERE world_id = ? AND nation_id = ?",
        (world_id, nation_id),
    ).fetchall():
        knock = -min(-_SHOCK_MORALE, max(0.0, float(unit["morale"])))
        if knock >= 0.0:
            continue
        diffs.append(
            Diff("unit", str(unit["id"]), "morale", DiffOp.ADJUST, knock,
                 reason_event_id=event_id, visibility_scope=Visibility.PUBLIC))
        report.formations_shaken += 1

    # **The aims die with the government that declared them.** They are that leader's
    # statement of what the war is for, and the successor has not made one. A bloc whose
    # aims vanish must restate them or fight without a stated purpose, and which of those
    # it does is the most legible single thing this instrument measures.
    for aim in conn.execute(
        "SELECT id FROM war_aim WHERE world_id = ? AND nation_id = ?", (world_id, nation_id)
    ).fetchall():
        diffs.append(
            Diff("war_aim", str(aim["id"]), "__delete__", DiffOp.DELETE, None,
                 reason_event_id=event_id, visibility_scope=Visibility.PUBLIC))
        report.aims_cleared += 1

    if blamed and sponsor:
        for side_a, side_b in ((nation_id, sponsor), (sponsor, nation_id)):
            row = conn.execute(
                "SELECT score FROM relation"
                " WHERE world_id = ? AND nation_a = ? AND nation_b = ?",
                (world_id, side_a, side_b),
            ).fetchone()
            if row is None:
                continue
            # Clamped against the floor the relation table keeps, so a pair already at the
            # bottom does not condemn the batch the way an unclamped hit would.
            drop = -min(-_SHOCK_BLAME, max(0.0, float(row["score"]) + 100.0))
            if drop < 0.0:
                diffs.append(
                    Diff("relation", f"{side_a}|{side_b}", "score", DiffOp.ADJUST, drop,
                         reason_event_id=event_id, visibility_scope=Visibility.PUBLIC))

    outcome = apply_batch(
        conn,
        DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                  proposer_model=None, diffs=tuple(diffs)),
    )
    if not outcome.applied:
        report.problems.append(f"an assassination could not be applied: {outcome.reason}")
        return report

    if mobilise:
        # One step, through the ordinary door, which is why this is a separate call: a
        # state that has just lost its head of government reaches for the call-up, and
        # `set_level` moves one step per turn whatever is asked for.
        from .mobilisation import set_level

        _level, said = set_level(
            conn, world_id, nation_id=nation_id, wanted=4, turn=turn, date=date,
            reason_event_id=event_id)
        report.mobilisation_said = said

    report.happened = True
    report.text = (
        f"{nation_name}: {leader.described} was assassinated"
        + (f", and {blamed} is blamed" if blamed else "")
        + (f"; {report.aims_cleared} war aim(s) died with them"
           if report.aims_cleared else "")
        + "."
    )
    return report


#: How much likelier a killing is in a country that is fighting somebody. **A multiplier on
#: the era's rate rather than a rate of its own**, so an era that opted out stays opted out
#: whatever its wars are doing -- the zero has to survive every modifier or the opt-in is
#: not really an opt-in.
#:
#: The direction is not in doubt and the size is a guess; open decision 4 of the proposal
#: is where the owner settles what else should move it.
_SHOCK_AT_WAR = 2.0


def assess_shocks(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    turn: int,
    date: str,
    seed: int,
    days: int = 30,
) -> list[ShockReport]:
    """Let an era that asked for it lose a government to something nobody saw coming.

    **Nothing happens unless the scenario opts in, and "nothing" means no draw.** The rate
    is read from :attr:`Scenario.assassination_rate` through ``world.preset_id``, the same
    join :mod:`atlas.engine.research` and :mod:`atlas.engine.decolonisation` use. At the
    default of zero this returns before touching :func:`derive`, so every other seeded
    stream in the world is exactly where it was -- which is the difference between an era
    being unaffected and an era being *byte-identical*, and only the second one leaves the
    cold-war cassette alone.

    One stream per nation per turn, keyed on where and when rather than on call order, so
    the same world replays identically however the loop is scheduled -- the rule
    :func:`assess_successions` follows and for the same reason.
    """
    reports: list[ShockReport] = []
    row = conn.execute(
        "SELECT preset_id FROM world WHERE id = ?", (world_id,)).fetchone()
    if row is None or not row["preset_id"]:
        return reports
    from ..scenarios.registry import scenario_for

    scenario = scenario_for(str(row["preset_id"]))
    rate = float(getattr(scenario, "assassination_rate", 0.0) or 0.0) if scenario else 0.0
    if rate <= 0.0:
        return reports

    span = span_of(days)
    try:
        nations = conn.execute(
            "SELECT n.id AS id FROM nation n"
            "  JOIN leader l ON l.nation_id = n.id AND l.is_current = 1"
            " WHERE n.world_id = ? AND n.is_alive = 1 ORDER BY n.id",
            (world_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return reports

    struck: list[str] = []
    for nation in nations:
        nation_id = str(nation["id"])
        at_war = conn.execute(
            "SELECT 1 FROM relation WHERE world_id = ? AND nation_a = ? AND at_war = 1 LIMIT 1",
            (world_id, nation_id),
        ).fetchone() is not None
        yearly = min(1.0, rate * (_SHOCK_AT_WAR if at_war else 1.0))
        monthly = 1.0 - (1.0 - yearly) ** (1.0 / 12.0)
        rng = derive(seed, world_id, turn, f"assassination:{nation_id}")
        if rng.random() < compounded(monthly, span):
            struck.append(nation_id)

    # Drawn for every nation first, then applied. A killing changes stability, war aims and
    # mobilisation, and applying one mid-loop would let it be read by a later nation's
    # query -- the draws are keyed so that cannot move them, but the *at war* lookup above
    # is not a draw, and an order-dependent input is how a replay quietly stops matching.
    for nation_id in struck:
        reports.append(shock_assassination(
            conn, world_id, nation_id=nation_id, turn=turn, date=date))
    return reports
