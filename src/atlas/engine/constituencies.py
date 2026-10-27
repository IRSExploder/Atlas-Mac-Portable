"""Who a government has to keep, and what it costs to disappoint them.

**A leader has had nobody to govern.** Stability and legitimacy are two numbers that move
for reasons the country never has an opinion about: a battleship, a land reform, a
humiliating peace and a colony let go all read as the same arithmetic, and the only
political question a player was ever asked was *how low is the coup risk*. So decisions were
numerically different and never politically different, which is the thing this layer is for.

Five constituencies, and five is a decision. A dozen sliders is a spreadsheet nobody reads,
and one number is what we already have. Five is enough that *different* decisions anger
*different* people -- which is the entire mechanism -- and few enough that a briefing can
name them all and a player can hold them in their head.

**Engine-derived, and the model has no lever on it.** A power cannot set its own support any
more than it can set its own exhaustion: reactions are read from the events the engine
already wrote, which is why the mapping is a table of event kinds rather than a channel.
What a government can do is govern, and live with it.

**And it has teeth.** A hostile populace strikes and a hostile industry drags its feet, both
applied *inside* the revenue and the build days the engine was already computing rather than
as a penalty beside them -- a separate strike mechanic would be a second economy to keep in
step with the first. Who a government has lost decides how it falls, in
:mod:`atlas.engine.leaders`, and how much a beaten country will sign away, in
:mod:`atlas.engine.peace`. None of that is authored anywhere but here.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..state.apply import apply_batch, record_event
from ..state.diff import Diff, DiffBatch
from ..state.models import DiffOp, EventKind, EventOrigin, Role, Visibility
from .pacing import compounded, over, span_of

__all__ = [
    "BANDS",
    "CONSTITUENCIES",
    "OBSTRUCTION_MAX",
    "REACTIONS",
    "STRIKE_DRAG_MAX",
    "Constituency",
    "HomeFront",
    "Standing",
    "assess_constituencies",
    "band_of",
    "constituency_id_for",
    "home_front",
    "industrial_throughput",
    "labour_drag",
    "standing_of",
    "support_of",
]


@dataclass(frozen=True, slots=True)
class Constituency:
    """One group a government has to keep, and what it wants."""

    kind: str
    name: str
    wants: str
    #: What its support starts at, before the nation's own state adjusts it.
    seeded: float = 50.0


#: **Five, universal, and the universality is a decision worth arguing with.**
#:
#: The alternative -- a different set per regime type -- needs a regime taxonomy the schema
#: does not have, and a set that varies by era makes a world unreplayable across eras. These
#: five exist in every state that has ever been governed; what differs between a 1936
#: dictatorship and a 2026 democracy is *which of them can remove you*, which is the
#: consequences layer's question rather than this one's.
#:
#: Reformers are deliberately not a sixth. A reforming impulse is the tension between the
#: people and the establishment, and modelling it as a group of its own produces a
#: constituency that is pleased by everything the other two are split on.
CONSTITUENCIES: tuple[Constituency, ...] = (
    Constituency(
        "army", "the officer corps",
        "arms, budgets, a war fought to a finish, and never a settlement that wastes what "
        "was paid for it"),
    Constituency(
        "industry", "industry and finance",
        "orders, factories, open trade routes and a treasury that pays its bills"),
    Constituency(
        "people", "the people",
        "bread, housing, schools, and their sons not being conscripted for somebody "
        "else's frontier"),
    Constituency(
        "patriots", "the nationalists",
        "ground, prestige, and never one province signed away at a table"),
    Constituency(
        "establishment", "the establishment",
        "continuity, legality, and a government that keeps the agreements it signs"),
)

CONSTITUENCY_KINDS: tuple[str, ...] = tuple(c.kind for c in CONSTITUENCIES)

#: The five bands, by upper bound. Names rather than numbers, for the same reason war
#: weariness has them: "restive" is what a briefing should say and 38.2 is not.
BANDS: tuple[tuple[float, str], ...] = (
    (20.0, "hostile"),
    (40.0, "restive"),
    (60.0, "uneasy"),
    (80.0, "content"),
    (100.1, "devoted"),
)

# --------------------------------------------------------------------------- the dials

#: Where support settles when nothing is happening. Not 50: a government that does nothing
#: is tolerated rather than loved, and a resting point at the middle of the scale means a
#: quiet decade heals every grievance completely.
RESTING = 50.0

#: How much of the distance to the resting point a month closes. **Slow on purpose.** A
#: grievance that fades in two turns is a grievance nobody has to govern around; at 0.06 a
#: shock is still being felt a year later and mostly gone in three.
INERTIA = 0.06

#: How far a **month's** reactions may move a constituency, however much happened. A country
#: that raised six divisions in a month has done one thing, not six.
#:
#: Per month, and scaled by the turn's span at the one place it is applied -- because the
#: reasoning above is monthly and a country that fought for a year, lost its capital and
#: changed its government has not done one thing either. Held flat it was the binding
#: constraint on every long jump, and domestic politics quietly stopped mattering at the
#: pace most of a century is played at.
PER_TURN_CAP = 12.0


# ------------------------------------------------------------------------- the friction

#: **What a country that has stopped cooperating costs its government.** Support below
#: `RESTING` buys friction, linearly, to these ceilings at zero.
#:
#: Friction and not a penalty layer: the drag is applied inside the revenue the budget was
#: already going to collect and inside the days the yards were already going to advance, so
#: there is nothing new to rewind and no second account of what a country produced. A
#: separate "strike" mechanic would be a second economy to keep in step with the first.
#:
#: Bounded well short of paralysis on purpose. At its worst a general strike costs a third of
#: revenue and a hostile industry takes half again as long to build anything -- which is a
#: bad year a player can dig out of, not a death spiral they watch. The floor matters more
#: than the ceiling here: a country that cannot pay is already charged for that by the budget,
#: and charging it twice was the mistake the arrears mapping avoided.
STRIKE_DRAG_MAX = 0.30
OBSTRUCTION_MAX = 0.40

#: **Where friction starts: the top of the *restive* band, not the resting point.**
#:
#: Measured before it was set. Against `RESTING`, 232 of the 242 nations on the modern map
#: had their yards slowed on the day the world was built -- because the seeded industry
#: standing is a shade under 50 for any country with a small treasury, and a threshold at
#: the resting point turns "nobody has done anything for them lately" into a permanent
#: industrial penalty on almost every nation in the game. That is not friction, it is a
#: recalibration of the world economy arriving through the back door.
#:
#: So a group costs you something when it has actually turned against you, and the number is
#: `leaders._ANGER_BELOW` deliberately: the band where an officer corps starts thinking about
#: a coup is the band where a workforce starts staying home, and two different answers to
#: "when has this group had enough" would be two vocabularies for one idea.
FRICTION_FROM = 40.0


def _friction(support: float, ceiling: float) -> float:
    """How far below the resting point, as a share of the way to zero, times the ceiling."""
    if support >= FRICTION_FROM:
        return 0.0
    return round(ceiling * (FRICTION_FROM - support) / FRICTION_FROM, 4)


def labour_drag(conn: sqlite3.Connection, nation_id: str) -> float:
    """The share of this turn's revenue that strikes, absenteeism and go-slows cost.

    Read by :func:`atlas.engine.budget.nation_budget`, so it lands in the one number the
    whole economy is computed from rather than in a charge beside it.
    """
    return _friction(support_of(conn, nation_id, "people"), STRIKE_DRAG_MAX)


def industrial_throughput(conn: sqlite3.Connection, nation_id: str) -> float:
    """What fraction of a day's work the yards actually do, 0.6 to 1.0.

    Read by :func:`atlas.engine.production.advance_construction`. A project does not stop
    -- an obstructed yard is slow, not shut -- which is why this scales the days rather
    than cancelling the row.
    """
    return round(1.0 - _friction(support_of(conn, nation_id, "industry"), OBSTRUCTION_MAX), 4)


def band_of(support: float) -> str:
    for ceiling, name in BANDS:
        if support < ceiling:
            return name
    return BANDS[-1][1]


@dataclass(frozen=True, slots=True)
class Standing:
    """Where one constituency stands, and why it last moved."""

    nation_id: str
    kind: str
    support: float = RESTING
    last_turn: int = 0
    because: str = ""

    @property
    def band(self) -> str:
        return band_of(self.support)

    @property
    def spec(self) -> Constituency:
        return next(c for c in CONSTITUENCIES if c.kind == self.kind)

    def describe(self) -> str:
        return (f"{self.spec.name}: **{self.band}** ({self.support:.0f}/100)"
                + (f" -- {self.because}" if self.because else ""))


# ------------------------------------------------------------------------ the mapping


@dataclass(frozen=True, slots=True)
class Reaction:
    """What one thing that happened does to the five, and how it reads."""

    #: ``{constituency: points}``. Absent means indifferent, which is as much of the
    #: mapping as the presence of a number: an army that does not care about a land reform
    #: is a fact about armies.
    moves: dict[str, float]
    because: str
    note: str = ""


#: **Who reacts to what.** The heart of the layer: this is what makes a battleship and a
#: land reform politically different rather than numerically different.
#:
#: Keyed by the engine's own event kinds, because the events are already written, already
#: attributed to an actor and already the causal spine -- a mapping keyed on anything else
#: would be a second account of what happened. A kind absent from this table moves nobody,
#: which is the honest default: most of what happens in a month is not political.
#:
#: The arguable ones were held back for one round and are now here, each carrying the reading
#: it was given in its own `note` -- the third field of `Reaction`, which exists for exactly
#: that and holds a decision rather than a number. Five of the ten have one. Any of them can
#: be overruled by editing one line.
REACTIONS: dict[str, Reaction] = {
    "unit_raised": Reaction(
        {"army": 2.5, "people": -1.5, "industry": 1.0},
        "the army was enlarged",
        "Mobilisation is the clearest case in the whole layer: the officers want it, the "
        "factories are paid for it, and it is somebody's sons."),
    "unit_disbanded": Reaction(
        {"army": -3.0, "people": 1.5},
        "formations were stood down"),
    "war_declared": Reaction(
        {"patriots": 4.0, "army": 2.0, "people": -3.0, "establishment": -1.0},
        "a war was declared",
        "The rally is real and so is the dread. Which one lasts is what exhaustion "
        "decides, later."),
    "peace_signed": Reaction(
        {"people": 4.0, "army": -2.0, "patriots": -2.0, "establishment": 1.0},
        "a war was ended",
        "Peace is popular and the officer corps is not consulted. A peace that gives "
        "ground away is worse than this -- see `_territorial` below, which reads the "
        "treaty's own terms rather than guessing from the kind."),
    "ceasefire": Reaction(
        {"people": 2.0, "army": -1.0},
        "the fighting stopped, for now"),
    "construction": Reaction(
        {"industry": 1.5},
        "something was built",
        "Which work it was decides who else cares -- see `_public_works`."),
    "treaty_signed": Reaction(
        {"establishment": 2.0, "people": 1.0, "patriots": -1.5},
        "an agreement was signed",
        "The establishment likes a signature. The nationalists read every treaty as a "
        "constraint on what the country may do next, and they are not wrong."),
    "treaty_broken": Reaction(
        {"establishment": -5.0, "patriots": 1.0},
        "an agreement was torn up"),
    "coup": Reaction(
        {"establishment": -8.0, "people": -3.0, "army": 2.0},
        "the government was overthrown"),
    "unrest": Reaction(
        {"people": -2.0, "establishment": -1.0},
        "there was trouble at home"),
}

#: Ground changing hands is read from the event's own participants rather than from its
#: kind, because the same kind means opposite things to the two sides.
_GAINED = Reaction(
    {"patriots": 5.0, "army": 2.0}, "ground was gained")
_LOST = Reaction(
    {"patriots": -6.0, "army": -3.0, "establishment": -1.5}, "ground was lost")

# -------------------------------------------------- the readings that were arguable
#
# **Every entry below is a judgement, not a measurement**, which is why they are gathered
# here rather than scattered through the code that fires them. Each says what was chosen and
# what the other reading would have been, so overruling one is a one-line edit and not an
# archaeology exercise.

#: **Money in.** The only deliberate economic act a government can take in this game is to
#: invest, and there is no austerity *policy* to map -- so "austerity" is mapped as the thing
#: that does exist: arrears, below. Spending pleases the people who receive it and worries
#: the people who keep the books, which is the reading in every direction.
_INVESTED = Reaction(
    {"industry": 2.5, "army": 1.0, "establishment": -1.0},
    "public money went into industry and rearmament")

#: **Money out, because somebody else decided so.** The rally is the choice here, and it is
#: the finding the sanctions literature is most confident about: sanctions consolidate a
#: population behind the government far more reliably than they turn it. Industry, which
#: actually pays, is not consoled. The other reading -- hardship turns the people against
#: their government -- is what `unrest` already does, so mapping it here too would charge the
#: same thing twice.
_SANCTIONED = Reaction(
    {"industry": -2.5, "patriots": 2.0, "people": -1.0},
    "sanctions are biting")

#: **The bills were not paid.** Not austerity -- a government choosing to cut is a policy
#: this game has no lever for -- but the failure state the budget already models. Everyone
#: who is owed is angry, and nobody is pleased: an unpaid army is not fiscal prudence.
_ARREARS = Reaction(
    {"army": -2.5, "industry": -3.0, "establishment": -2.0, "people": -1.0},
    "the government could not pay its bills")

#: **A battle lost, and a battle won.** Small on purpose: battles are the most frequent event
#: in a war and a month of them must add up to a bad month, not to a revolution. Losing is
#: worth more than winning because a war of victories is what an army is *for*, and the
#: asymmetry is the whole reason the numbers are not mirrored.
#:
#: The defender's loss is heavier than the attacker's: it was fought on ground they were
#: holding, and somebody at home noticed.
#: Worded for every domain rather than for the land war, because air, naval and nuclear
#: events now carry the same three roles. "Ground was lost in the fighting" is a true
#: sentence about a province and a false one about a squadron over the Channel, and the
#: mapping cannot tell which it is looking at -- so it says the thing that is true of both.
_ATTACK_FAILED = Reaction({"army": -1.0, "people": -0.5}, "an attack was thrown back")
_DEFENCE_FAILED = Reaction(
    {"army": -1.2, "people": -0.8, "patriots": -0.6}, "the fighting went against us")
_PREVAILED = Reaction({"army": 1.0, "patriots": 0.8}, "the fighting went our way")

#: **Rearmament with no war to justify it**, over and above what raising a unit already does.
#: The nationalists are delighted -- this is the country standing up -- and the cost lands on
#: the household and on whoever has to find the money. The alternative reading, that a
#: peacetime army is simply cheaper politically than a wartime one, gets the sign right and
#: the mechanism wrong: what makes conscription bearable is a war, not the absence of one.
_ARMS_RACE = Reaction(
    {"patriots": 1.5, "people": -1.0, "establishment": -1.0},
    "the country is arming with nobody to fight")

#: **Holding somebody else's ground.** Per turn while it lasts, because occupation is a
#: condition and not an event -- it does not happen, it continues. Small, so that the
#: equilibrium it settles at is *content* rather than *devoted*: a long occupation is a
#: standing source of national pride and not a permanent holiday.
_OCCUPYING = Reaction({"patriots": 1.0, "army": 0.5}, "the army is holding foreign ground")

#: **And having your own ground held.** Deliberately heavier than the reverse, because
#: losing is not the negative of taking: a country notices an enemy in its provinces in a way
#: it never notices its own soldiers in somebody else's. Sustained, this settles a homeland
#: under occupation into *hostile*, which is the intended reading.
_OCCUPIED = Reaction(
    {"patriots": -2.0, "people": -1.5, "army": -1.0, "establishment": -1.0},
    "foreign troops are on home soil")

#: **Tearing up your own ceasefire**, on top of the generic charge for breaking an agreement.
#: The judgement is that the hardliners are pleased: they wanted the war back and now they
#: have it. The people are not -- they had stopped burying their sons for three months. The
#: alternative reading, that a country tells itself the other side broke it first, is real but
#: is a *narrative* claim, and this layer reads the ledger, which knows who moved.
_BROKE_THE_TRUCE = Reaction(
    {"people": -3.0, "establishment": -2.0, "patriots": 1.5},
    "the government tore up a ceasefire it had agreed")

#: How many provinces' worth of occupation counts. Beyond this the country is as angry as it
#: is going to get from this cause alone, and the rest is what everything else is doing.
_OCCUPATION_CAP = 3

#: What a civil work says to the people, over and above `construction`'s nod to industry.
_PUBLIC_WORKS: dict[str, dict[str, float]] = {
    "housing": {"people": 3.0}, "health": {"people": 3.0}, "schools": {"people": 2.5},
    "land_reform": {"people": 4.0, "industry": -3.0, "establishment": -2.0},
    "steelworks": {"industry": 2.0, "army": 1.0}, "shipyards": {"industry": 2.0,
                                                                "army": 1.0},
    "workshops": {"industry": 2.0}, "rail": {"industry": 1.5, "army": 1.0},
}


def constituency_id_for(nation_id: str, kind: str) -> str:
    """Derived, not autoincremented, so a constituency survives a rewind with its identity."""
    return f"c_{nation_id}_{kind}"


# --------------------------------------------------------------------------- reading


def standing_of(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> list[Standing]:
    """Where all five stand. **A nation with no rows yet reads its seeded defaults.**

    Nothing is written until something actually moves, so a world that has never had a
    turn does not carry five rows per nation for no reason -- and the answer is the same
    either way, which is what makes the lazy seeding invisible.
    """
    found = {
        str(row["kind"]): Standing(
            nation_id=nation_id, kind=str(row["kind"]), support=float(row["support"]),
            last_turn=int(row["last_turn"]), because=str(row["because"] or ""))
        for row in conn.execute(
            "SELECT kind, support, last_turn, because FROM constituency"
            " WHERE world_id = ? AND nation_id = ?", (world_id, nation_id))
    }
    seeded = _seed_for(conn, world_id, nation_id)
    return [
        found.get(spec.kind,
                  Standing(nation_id=nation_id, kind=spec.kind,
                           support=seeded.get(spec.kind, spec.seeded)))
        for spec in CONSTITUENCIES
    ]


def support_of(conn: sqlite3.Connection, nation_id: str, kind: str) -> float:
    """One constituency's standing, keyed by nation alone.

    **No ``world_id``, for the reason `leader_of` gives:** ``nation.id`` is a global primary
    key, so scoping by world would add nothing but an opportunity to pass the wrong one --
    and this is called from inside the settlement and succession machinery, which knows who
    is involved and has no reason to know which world it is in.

    Answers with the seeded default for a nation that has never had a constituency move, so
    a consequence reading this never has to care whether the row exists yet.
    """
    row = conn.execute(
        "SELECT support FROM constituency WHERE nation_id = ? AND kind = ?",
        (nation_id, kind)).fetchone()
    if row is not None:
        return float(row["support"])
    spec = next((c for c in CONSTITUENCIES if c.kind == kind), None)
    return _seed_for(conn, None, nation_id).get(kind, spec.seeded if spec else RESTING)


def _seed_for(
    conn: sqlite3.Connection, world_id: str | None, nation_id: str
) -> dict[str, float]:
    """What the five start at, derived from the country rather than assigned.

    A well-governed country starts with a contented establishment; a heavily armed one
    with a pleased officer corps; a poor one with a restive population. Derived rather
    than seeded per scenario so every era and every mod gets it for nothing, and so the
    starting politics of a nation follow from what the scenario already said about it.
    """
    row = conn.execute(
        "SELECT stability, legitimacy, military_strength, economy, treasury"
        "  FROM nation WHERE id = ?" + ("" if world_id is None else " AND world_id = ?"),
        (nation_id,) if world_id is None else (nation_id, world_id)).fetchone()
    if row is None:
        return {}
    stability = float(row["stability"])
    legitimacy = float(row["legitimacy"])
    military = float(row["military_strength"])
    economy = max(1.0, float(row["economy"]))
    return {
        "army": _clamp(RESTING + (military / economy - 1.0) * 12.0),
        "industry": _clamp(RESTING + (float(row["treasury"]) / 20.0) - 2.5),
        "people": _clamp(RESTING + (stability - 50.0) * 0.4),
        "patriots": _clamp(RESTING + (military - 20.0) * 0.2),
        "establishment": _clamp(RESTING + (legitimacy - 50.0) * 0.5),
    }


# ------------------------------------------------------------------------ the assessment


@dataclass(slots=True)
class ConstituencyReport:
    """What moved, and what crossed a band -- which is the only part worth reporting."""

    moved: int = 0
    news: list[str] = field(default_factory=list)


def assess_constituencies(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str, days: int = 30
) -> list[str]:
    """Move every constituency by what this turn's events did to it, then let it settle.

    Reads the events of the turn that has just been resolved, so a reaction is always to
    something that actually happened and can be traced to it. Written as diffs, so a rewind
    puts the politics back exactly as they stood.
    """
    events = list(conn.execute(
        "SELECT e.id, e.kind, e.text, e.visibility, e.actor_nation_id AS actor FROM event e"
        " WHERE e.world_id = ? AND e.turn = ? ORDER BY e.id", (world_id, turn)))
    nations = [
        str(row["id"]) for row in conn.execute(
            "SELECT id FROM nation WHERE world_id = ? AND is_alive = 1 ORDER BY id",
            (world_id,))
    ]
    if not nations:
        return []

    span = span_of(days)
    #: ``nation -> constituency -> [points, why, scope]``
    pending: dict[str, dict[str, list]] = {}
    reacting = [
        (nation_id, reaction, str(event["visibility"]))
        for event in events
        for nation_id, reaction in _reactions_to(conn, world_id, event, nations)
    ]
    # A condition is the state of the map, which everyone can see.
    #
    # **And it is a flow, so it is charged for as many months as the turn lasted.** An
    # event happened once and is counted once however long the turn was; a condition
    # *continues* -- that is the distinction this function's own docstring draws, and it is
    # the difference between a country angry about the month its provinces fell and one
    # angry for as long as they are held. Charged once per turn, a year of occupation
    # angered a population exactly as much as a month of it, so the anger of a long war was
    # something a player could skip past by choosing a longer turn.
    reacting += [(nation_id, _scaled(reaction, span), Visibility.PUBLIC.value)
                 for nation_id, reaction in _conditions(conn, world_id, turn, nations)]
    for nation_id, reaction, scope in reacting:
        for kind, points in reaction.moves.items():
            slot = pending.setdefault(nation_id, {}).setdefault(
                kind, [0.0, "", Visibility.PUBLIC.value])
            slot[0] += points
            if abs(points) >= abs(slot[0]) - abs(points) or not slot[1]:
                slot[1] = reaction.because
            slot[2] = _narrower(str(slot[2]), scope)

    written = {
        str(row["id"]) for row in conn.execute(
            "SELECT id FROM constituency WHERE world_id = ?", (world_id,))
    }
    # **The cap is a month's worth, so a longer turn gets more of them.** Its own reasoning
    # is monthly -- "a country that raised six divisions *in a month* has done one thing,
    # not six" -- and a country that fought for a year, lost its capital and changed
    # government has not done one thing either. Held flat, it was the binding constraint on
    # every long jump: a year of catastrophe moved a constituency 12 points while the
    # settling pull, which does compound, dragged it a third of the way back to resting.
    # Domestic politics quietly stopped mattering at the pace most of a century is played
    # at. `over` is what the pacing doctrine calls this, and 12 is unchanged at a month.
    cap = over(PER_TURN_CAP, span)
    #: ``scope -> diffs``, because a movement caused in secret may not be written in public.
    staged: dict[str, list[Diff]] = {}
    #: ``scope -> nations`` moved at that scope. Carried rather than parsed back out of the
    #: row id, which cannot be done: a minor's id has underscores of its own (``m_afg_...``).
    moved: dict[str, set[str]] = {}
    news: list[str] = []
    for nation_id in nations:
        current = {s.kind: s for s in standing_of(conn, world_id, nation_id)}
        for kind, standing in current.items():
            points, because, scope = pending.get(nation_id, {}).get(
                kind, [0.0, "", Visibility.PUBLIC.value])
            points = max(-cap, min(cap, points))
            # Compounded over the span rather than multiplied by it, for the reason
            # `intelligence.assess_networks` carries in full: a converging pull written
            # linearly overshoots what it converges toward once the span passes
            # `1 / INERTIA`. At a three-year jump a standing of 90 settled to 2.4, straight
            # through a resting point of 50. Identical at span 1.
            settling = (RESTING - standing.support) * compounded(INERTIA, span)
            after = _clamp(standing.support + points + settling)
            if abs(after - standing.support) < 0.01:
                continue
            staged.setdefault(str(scope), []).append(
                _write(world_id, nation_id, kind, after, turn, because,
                       exists=constituency_id_for(nation_id, kind) in written))
            moved.setdefault(str(scope), set()).add(nation_id)
            # **Only a publicly-caused change is news.** A band crossing that happened
            # because of a covert operation is exactly the sentence that would give the
            # operation away.
            if band_of(after) != band_of(standing.support) and (
                    str(scope) == Visibility.PUBLIC.value):
                names = _names(conn, world_id)
                spec = next(c for c in CONSTITUENCIES if c.kind == kind)
                news.append(
                    f"{names.get(nation_id, nation_id)}: {spec.name} are now "
                    f"{band_of(after)}" + (f" -- {because}" if because else ""))
    if not staged:
        return []

    applied = False
    for scope, diffs in sorted(staged.items()):
        public = scope == Visibility.PUBLIC.value
        # A secret cause gets a secret event, visible only to the country it happened in --
        # which is the same treatment the facts layer had to be given, and for the same
        # reason. The countries are named because a batch may cover several.
        subjects = sorted(moved.get(scope, ()))
        event_id = record_event(
            conn, world_id=world_id, turn=turn, date=date, kind=EventKind.NARRATIVE,
            origin=EventOrigin.ENGINE,
            text=(news[0] if public and len(news) == 1 else
                  f"{len(news)} constituency(ies) changed their mind about their government."
                  if public and news else
                  "The country took a view on what its government had done."),
            visibility=Visibility(scope),
            visible_to=() if public else tuple(subjects))
        outcome = apply_batch(
            conn,
            DiffBatch(
                world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                proposer_model=None,
                diffs=tuple(
                    Diff(d.target_table, d.target_id, d.field, d.op, d.new_value,
                         reason_event_id=event_id, visibility_scope=Visibility(scope))
                    for d in diffs)))
        applied = applied or outcome.applied
    return news if applied else []


#: Narrowest first. A movement with two causes is written at the tighter of them, which is
#: the only safe rule: a public reason does not license publishing a secret one.
_SCOPES: tuple[str, ...] = (
    Visibility.SECRET.value, Visibility.BILATERAL.value, Visibility.PUBLIC.value)


def _narrower(one: str, other: str) -> str:
    return next(scope for scope in _SCOPES if scope in (one, other))


def _reactions_to(
    conn: sqlite3.Connection, world_id: str, event, nations: list[str]
) -> list[tuple[str, Reaction]]:
    """Who this event moves, and how. One event may move both sides of a war differently."""
    kind = str(event["kind"])
    actor = str(event["actor"] or "")
    found: list[tuple[str, Reaction]] = []

    if kind == "territory_transfer":
        for nation_id, reaction in _territorial(conn, world_id, event, nations):
            found.append((nation_id, reaction))
        return found

    if kind == "battle":
        return _fighting(conn, event, actor, nations)

    if kind == "economic_reform":
        return _economic(conn, event, actor, nations)

    if kind == "construction" and actor:
        works = _public_works(str(event["text"] or ""))
        if works:
            found.append((actor, Reaction(works, "a public work was finished")))

    if kind == "unit_raised" and actor in nations and not _at_war(conn, world_id, actor):
        found.append((actor, _ARMS_RACE))

    reaction = REACTIONS.get(kind)
    if reaction is None or not actor or actor not in nations:
        return found

    found.append((actor, reaction))
    # A peace or a ceasefire is signed *by* two powers, and the engine names one as actor.
    # The other side's people are just as relieved.
    if kind in {"peace_signed", "ceasefire"}:
        for other in _counterparties(conn, event["id"], actor):
            if other in nations:
                found.append((other, reaction))
    if kind == "peace_signed":
        found.extend(_ceded(conn, world_id, event, nations))
    return found


def _fighting(
    conn: sqlite3.Connection, event, actor: str, nations: list[str]
) -> list[tuple[str, Reaction]]:
    """Who lost a battle, read from the event's participants rather than from its prose.

    **Battles carried no participants at all until this layer needed them.** The event named
    the attacker and said in a sentence who prevailed, so the defender was not recoverable
    and the winner only by matching a name inside a string -- which two nations called
    "Germany" and "West Germany" break. They now name the defender and, as ``beneficiary``,
    the side that came out ahead: structural, and no closed CHECK had to be widened for it.
    """
    roles = {
        str(row["role"]): str(row["nation_id"] or "")
        for row in conn.execute(
            "SELECT role, nation_id FROM event_participant WHERE event_id = ?"
            "   AND role IN ('defender', 'beneficiary')", (event["id"],))
    }
    defender, won = roles.get("defender", ""), roles.get("beneficiary", "")
    if not defender or not won:
        # An air or naval action, or an older event. Silence is the honest answer: a
        # mapping that guessed would attribute somebody else's defeat.
        return []
    found: list[tuple[str, Reaction]] = []
    for side, lost_it in ((actor, _ATTACK_FAILED), (defender, _DEFENCE_FAILED)):
        if side and side in nations:
            found.append((side, _PREVAILED if side == won else lost_it))
    return found


def _economic(
    conn: sqlite3.Connection, event, actor: str, nations: list[str]
) -> list[tuple[str, Reaction]]:
    """Investment, sanctions and arrears all arrive as `economic_reform`, so read the money.

    **Which way it moved, on the diffs the event itself caused.** Three unrelated things wear
    the same kind -- a government putting funds into industry, an organisation's sanctions
    biting, and the quarter's accounts -- and telling them apart by their prose would be a
    mapping that breaks when somebody rewords a sentence. The ledger says who gained and who
    paid, and it cannot be reworded.
    """
    found: list[tuple[str, Reaction]] = []
    for row in conn.execute(
        "SELECT target_id AS who, field, SUM(CAST(new_json AS REAL)) AS moved"
        "  FROM diff WHERE reason_event_id = ? AND target_table = 'nation'"
        "   AND op = 'adjust' AND field IN ('economy', 'stability')"
        " GROUP BY target_id, field ORDER BY target_id, field", (event["id"],)
    ):
        who, moved = str(row["who"]), float(row["moved"] or 0.0)
        if who not in nations or abs(moved) < 0.001:
            continue
        if str(row["field"]) == "economy":
            found.append((who, _INVESTED if moved > 0 else _SANCTIONED))
        elif moved < 0 and not actor:
            # The budget writes one world-wide event with no actor and charges the
            # governments that could not pay. Only those governments react.
            found.append((who, _ARREARS))
    return found


def _conditions(
    conn: sqlite3.Connection, world_id: str, turn: int, nations: list[str]
) -> list[tuple[str, Reaction]]:
    """What is *true* rather than what happened, for the three cases that are states.

    An occupation does not occur, it continues; so does an occupation of somebody else's
    ground. Reading them from the map each turn is the difference between a country that is
    angry about the month its provinces fell and one that is angry for as long as they are
    held -- and the second is the one every history of an occupation describes.
    """
    living = set(nations)
    found: list[tuple[str, Reaction]] = []
    for row in conn.execute(
        """
        SELECT owner_nation_id AS owner, controller_nation_id AS holder, COUNT(*) AS n
          FROM region
         WHERE world_id = ? AND kind <> 'ocean' AND owner_nation_id IS NOT NULL
           AND controller_nation_id IS NOT NULL AND controller_nation_id <> owner_nation_id
         GROUP BY owner_nation_id, controller_nation_id
         ORDER BY owner_nation_id, controller_nation_id
        """, (world_id,)
    ):
        owner, holder, count = str(row["owner"]), str(row["holder"]), int(row["n"])
        weight = min(_OCCUPATION_CAP, count)
        if owner in living:
            found.append((owner, _scaled(_OCCUPIED, weight)))
        if holder in living:
            found.append((holder, _scaled(_OCCUPYING, weight)))

    for row in conn.execute(
        "SELECT breached_by_nation_id AS who FROM commitment"
        " WHERE world_id = ? AND kind = 'ceasefire' AND breached_turn = ?"
        "   AND breached_by_nation_id IS NOT NULL"
        " ORDER BY breached_by_nation_id", (world_id, turn)
    ):
        if str(row["who"]) in living:
            found.append((str(row["who"]), _BROKE_THE_TRUCE))
    return found


def _scaled(reaction: Reaction, weight: float) -> Reaction:
    """Weigh a reaction: by how many provinces it is about, or by how long the turn was.

    ``== 1`` rather than ``<= 1``, because the second caller passes a span and a weekly turn
    is a weight of 0.23. The first caller passes a province count, which is never below one,
    so nothing there changes.
    """
    if weight == 1:
        return reaction
    return Reaction({kind: points * weight for kind, points in reaction.moves.items()},
                    reaction.because, reaction.note)


def _at_war(conn: sqlite3.Connection, world_id: str, nation_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM relation WHERE world_id = ? AND at_war = 1"
        "   AND (nation_a = ? OR nation_b = ?) LIMIT 1",
        (world_id, nation_id, nation_id)).fetchone() is not None


def _territorial(
    conn: sqlite3.Connection, world_id: str, event, nations: list[str]
) -> list[tuple[str, Reaction]]:
    """Ground changing hands: gained for one, lost for the other. Read from participants."""
    gained = str(event["actor"] or "")
    found: list[tuple[str, Reaction]] = []
    if gained in nations:
        found.append((gained, _GAINED))
    for row in conn.execute(
        "SELECT r.home_nation_id AS home FROM event_participant p"
        "  JOIN region r ON r.id = p.nation_id"
        " WHERE p.event_id = ? AND p.role = 'target'", (event["id"],)
    ):
        home = str(row["home"] or "")
        if home and home != gained and home in nations:
            found.append((home, _LOST))
    return found


def _ceded(
    conn: sqlite3.Connection, world_id: str, event, nations: list[str]
) -> list[tuple[str, Reaction]]:
    """**A peace that signs ground away is a different peace**, and the terms say so.

    Read from `peace_term` rather than guessed from the event kind: the nationalists do not
    mind a war ending, they mind a province going with it, and the difference between those
    two is the whole reason the settlement layer records what it agreed.
    """
    found: list[tuple[str, Reaction]] = []
    for row in conn.execute(
        "SELECT p.obliged_id AS who, COUNT(*) AS n FROM peace_term p"
        "  JOIN commitment c ON c.id = p.commitment_id"
        " WHERE p.world_id = ? AND c.source_event_id = ? AND p.kind = 'cede_region'"
        " GROUP BY p.obliged_id ORDER BY p.obliged_id", (world_id, event["id"])
    ):
        who = str(row["who"])
        if who in nations:
            lost = int(row["n"])
            found.append((who, Reaction(
                {"patriots": -4.0 * min(3, lost), "army": -2.0,
                 "establishment": -1.0 * min(3, lost)},
                f"{lost} province(s) were signed away at the table")))
    return found


def _public_works(text: str) -> dict[str, float]:
    lowered = text.lower()
    for project, moves in _PUBLIC_WORKS.items():
        if project.replace("_", " ") in lowered or project in lowered:
            return moves
    return {}


def _counterparties(conn: sqlite3.Connection, event_id, actor: str) -> list[str]:
    return [
        str(row["nation_id"]) for row in conn.execute(
            "SELECT nation_id FROM event_participant WHERE event_id = ?"
            "   AND role IN ('target', 'defender') AND nation_id <> ?",
            (event_id, actor))
    ]


def _write(  # noqa: PLR0913 - a row is its identity, its new value and why it moved
    world_id: str, nation_id: str, kind: str, after: float, turn: int, because: str,
    *, exists: bool,
) -> Diff:
    """Insert the row the first time this constituency moves; set it thereafter.

    **Lazily, so a world that has never had a turn carries no rows at all** -- and reads
    the same, because `standing_of` answers with the seeded default for a nation that has
    none. Five rows per nation across 243 nations is twelve hundred rows of nothing
    happening, and a table that only holds what has actually moved is also a table a
    reader can scan.
    """
    row_id = constituency_id_for(nation_id, kind)
    if not exists:
        return Diff(
            "constituency", row_id, "__insert__", DiffOp.INSERT,
            {
                "world_id": world_id, "nation_id": nation_id, "kind": kind,
                "support": round(after, 3), "last_turn": turn, "because": because[:120],
            })
    return Diff("constituency", row_id, "support", DiffOp.SET, round(after, 3))


# ------------------------------------------------------- everything about one home front


@dataclass(frozen=True, slots=True)
class HomeFront:
    """One nation's politics, gathered for a reader. **Computed, never stored.**

    Every field here is derived from state some other module owns -- the constituency rows,
    the succession odds, the revenue, the yards, the acceptance model -- and gathered in one
    place so a screen showing it cannot disagree with the engine acting on it. A panel that
    recomputed a strike drag its own way would be a second answer to a question the budget
    has already settled.
    """

    nation_id: str
    standings: tuple[Standing, ...]
    #: ``kind -> lines``: what this group is doing to the government right now.
    consequences: dict[str, tuple[str, ...]]
    #: ``kind -> lines``: what moved it lately, read back through the same mapping the
    #: engine applies, so the reason shown is the reason charged.
    lately: dict[str, tuple[str, ...]]
    #: How this government would fall today, and whether there is a ballot box.
    channel: str = "coup"
    accountable: float = 0.0
    #: Its chance of falling within the year, as `leaders.coup_risk` computes it.
    risk: float = 0.0

    @property
    def worst(self) -> Standing:
        return min(self.standings, key=lambda s: s.support)

    @property
    def unhappy(self) -> tuple[Standing, ...]:
        return tuple(s for s in self.standings if s.support < RESTING)


def home_front(
    conn: sqlite3.Connection, world_id: str, nation_id: str, *, turn: int, back: int = 3
) -> HomeFront:
    """Where all five stand, what each is costing, and what moved it lately.

    The consequence lines are the *live* ones: each is generated from the same function the
    engine consults, so a group listed as costing nothing is costing nothing.
    """
    from .leaders import accountability_of, coup_risk, removal_channel
    from .peace import patriot_stiffening

    standings = tuple(standing_of(conn, world_id, nation_id))
    row = conn.execute(
        "SELECT stability, legitimacy FROM nation WHERE world_id = ? AND id = ?",
        (world_id, nation_id)).fetchone()
    channel = removal_channel(conn, nation_id)
    risk = coup_risk(
        conn, nation_id,
        stability=float(row["stability"]) if row else 50.0,
        legitimacy=float(row["legitimacy"]) if row else 50.0)

    told: dict[str, list[str]] = {spec.kind: [] for spec in CONSTITUENCIES}
    drag = labour_drag(conn, nation_id)
    if drag > 0:
        told["people"].append(
            f"strikes and go-slows are costing you {drag * 100:.0f}% of your revenue")
    throughput = industrial_throughput(conn, nation_id)
    if throughput < 1.0:
        told["industry"].append(
            f"the yards are doing {throughput * 100:.0f}% of a month's work in a month")
    # **The odds go on the group that would actually do it**, not on the army by default.
    # A government at 11% in a country that would vote it out is not being told something
    # about its officer corps, and a line under the wrong row is worse than no line.
    if risk > 0.02:
        told["army" if channel == "coup" else "people"].append(
            "would remove this government, and it stands about a "
            f"{risk * 100:.0f}% chance of falling this year")
    # But everyone past the line is *raising* those odds whether or not they would be the
    # ones to act, and a hostile officer corps reading "costing you nothing" is the screen
    # contradicting the formula. See `leaders._RISK_FROM_MUTINY` and `_RISK_FROM_DISOWNED`.
    for kind in ("army", "establishment", "people"):
        if support_of(conn, nation_id, kind) < FRICTION_FROM and not told[kind]:
            told[kind].append("is raising the odds this government falls")
    # Measured at a settlement made entirely of ground, which is what a nationalist minds:
    # anything less humiliating stiffens less, so this is the worst case rather than a
    # prediction about a particular treaty.
    stiffening = patriot_stiffening(conn, nation_id, position=0.0, humiliation=1.0)
    if stiffening > 0.01:
        told["patriots"].append(
            f"a peace that ceded ground would have to be {stiffening * 100:.0f}% easier "
            "before they would wear it")
    elif stiffening < -0.01:
        told["patriots"].append("they would swallow a harder peace than the position needs")

    return HomeFront(
        nation_id=nation_id, standings=standings,
        consequences={kind: tuple(lines) for kind, lines in told.items()},
        lately=_lately(conn, world_id, nation_id, turn=turn, back=back),
        channel=channel, accountable=accountability_of(conn, nation_id), risk=risk)


def _lately(
    conn: sqlite3.Connection, world_id: str, nation_id: str, *, turn: int, back: int
) -> dict[str, tuple[str, ...]]:
    """What has been moving each group, through the same mapping that moved it.

    **Not a second account.** The reasons a panel shows are produced by re-running
    `_reactions_to` over the recent events rather than by a parallel description table, so
    there is no way for the screen to name a cause the engine did not charge.
    """
    nations = [
        str(row["id"]) for row in conn.execute(
            "SELECT id FROM nation WHERE world_id = ? AND is_alive = 1", (world_id,))
    ]
    seen: dict[str, list[tuple[float, str]]] = {spec.kind: [] for spec in CONSTITUENCIES}
    events = conn.execute(
        "SELECT id, kind, text, actor_nation_id AS actor FROM event"
        " WHERE world_id = ? AND turn > ? AND turn <= ? ORDER BY id DESC",
        (world_id, turn - back, turn))
    reacting = [
        pair for event in events
        for pair in _reactions_to(conn, world_id, event, nations)
    ]
    reacting += _conditions(conn, world_id, turn, nations)
    for who, reaction in reacting:
        if who != nation_id:
            continue
        for kind, points in reaction.moves.items():
            line = f"{'+' if points > 0 else ''}{points:.1f}  {reaction.because}"
            if line not in [text for _p, text in seen[kind]]:
                seen[kind].append((abs(points), line))
    return {
        kind: tuple(line for _weight, line in sorted(found, key=lambda x: -x[0])[:4])
        for kind, found in seen.items()
    }


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


def _names(conn: sqlite3.Connection, world_id: str) -> dict[str, str]:
    return {
        str(row["id"]): str(row["name"])
        for row in conn.execute(
            "SELECT id, name FROM nation WHERE world_id = ?", (world_id,))
    }
