"""Deciding contested outcomes. The model narrates; this decides.

The distinction is easy to state and easy to lose. A design where the model returns
``{"contested": true, "pressure": 0.62}`` and the engine compares 0.62 against a threshold
has not moved the decision anywhere -- the only free variable is still model-authored. The
seeded RNG would be constructed and never drawn from, and "the model never decides" would
be false while looking true.

So the model may return **evidence flags only**: enumerated, citation-bearing observations
about the situation. It cannot express a probability, a winner, or an outcome, because the
response schema has no field for one. Python turns those flags plus real state -- force
ratios, terrain, supply, difficulty -- into a pressure value, draws from the world's seeded
stream, and constructs the resulting diffs itself under the ENGINE role.

That is also what makes the result reproducible: the same seed and the same inputs give the
same battle, every time, which is what rewind depends on.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import StrEnum

from ..state.models import DIFFICULTY_PROFILES, Difficulty, DifficultyProfile
from .rng import chance, derive, jitter

__all__ = [
    "BASELINE_ADVANCE",
    "_UNSUPPLIED_ADVANCE",
    "CAPTURE_THRESHOLD",
    "IDLE_DECAY",
    "REPULSE",
    "Contest",
    "ContestKind",
    "EvidenceFlag",
    "Outcome",
    "compute_advance",
    "defender_resilience",
    "resolve_contest",
]


class ContestKind(StrEnum):
    BATTLE = "battle"
    SIEGE = "siege"
    COUP = "coup"
    UPRISING = "uprising"
    NEGOTIATION = "negotiation"


class EvidenceFlag(StrEnum):
    """What a model is permitted to observe about a contest.

    A closed set, and none of these is an outcome. The model reports circumstances; the
    weighting of those circumstances lives here, in code the player can read.
    """

    DEFENDER_FORTIFIED = "defender_fortified"
    ATTACKER_SURPRISE = "attacker_surprise"
    DIFFICULT_TERRAIN = "difficult_terrain"
    ATTACKER_OVEREXTENDED = "attacker_overextended"
    DEFENDER_ISOLATED = "defender_isolated"
    WINTER_CONDITIONS = "winter_conditions"
    POPULAR_SUPPORT = "popular_support"
    FOREIGN_BACKING = "foreign_backing"
    LOW_MORALE = "low_morale"
    SUPPLY_SECURE = "supply_secure"


#: How much each flag shifts the attacker's odds. Visible, tunable, and auditable --
#: unlike a number a model invented.
_FLAG_WEIGHTS: dict[EvidenceFlag, float] = {
    EvidenceFlag.DEFENDER_FORTIFIED: -0.15,
    EvidenceFlag.ATTACKER_SURPRISE: +0.15,
    EvidenceFlag.DIFFICULT_TERRAIN: -0.10,
    EvidenceFlag.ATTACKER_OVEREXTENDED: -0.18,
    EvidenceFlag.DEFENDER_ISOLATED: +0.14,
    EvidenceFlag.WINTER_CONDITIONS: -0.08,
    EvidenceFlag.POPULAR_SUPPORT: +0.12,
    EvidenceFlag.FOREIGN_BACKING: +0.10,
    EvidenceFlag.LOW_MORALE: -0.12,
    EvidenceFlag.SUPPLY_SECURE: +0.08,
}


@dataclass(frozen=True, slots=True)
class Contest:
    """A clash awaiting resolution. Carries no outcome and no probability."""

    kind: ContestKind
    attacker: str
    defender: str
    region_id: str
    #: Only enumerated observations, never a number the model chose.
    flags: tuple[EvidenceFlag, ...] = ()
    reason_event_id: int | None = None
    #: Stable identifier for the RNG stream. Must describe *where* the contest is, never
    #: what order it was resolved in -- an ordinal key reintroduces call-order dependence.
    key: str = ""

    def stream_key(self) -> str:
        return self.key or f"{self.kind.value}:{self.region_id}:{self.attacker}-vs-{self.defender}"


@dataclass(frozen=True, slots=True)
class Outcome:
    contest: Contest
    attacker_wins: bool
    pressure: float
    roll: float
    attacker_losses: float
    defender_losses: float
    decisive: bool
    narrative_hint: str
    #: How far the front moved this turn, as a share of :data:`CAPTURE_THRESHOLD`.
    #: Positive when the attacker gained ground, negative when it was thrown back. The
    #: region changes hands only once accumulated pressure crosses the threshold, which
    #: is what turns a battle into a campaign.
    advance: float = 0.0

    def describe(self) -> str:
        winner = self.contest.attacker if self.attacker_wins else self.contest.defender
        scale = "decisively" if self.decisive else "narrowly"
        return f"{winner} prevails {scale} at {self.contest.region_id}"


#: What a formation in a bordering region contributes. Less than one standing on the
#: ground, more than nothing: it is arriving, not dug in.
_ADJACENT_WEIGHT = 0.6


#: What a carrier a sea away is worth, against the 0.6 anything else gets for being
#: nearby. Aircraft are why a carrier is in the fight at all when its own guns are not,
#: and giving them reach is the one place the model says something specific about the
#: argument the 1930s naval staffs were having.
_CARRIER_REACH = 1.0


def _strength(conn: sqlite3.Connection, nation_id: str, region_id: str) -> float:
    """What a nation can actually bring to bear at ``region_id``.

    Units in bordering regions count, at a discount. Without them an attacker's strength
    is almost always zero -- an invader's army is by definition *next to* the region it
    is invading, not already inside it -- so every war was decided by a tenth of national
    strength against the defender's garrison, and a massed army on the border counted for
    nothing at all.

    **At sea only ships count, and there is no reserve.** A fleet action is fought by
    what is floating: a nation's abstract military strength stands for its army, and
    letting a tenth of it decide the North Sea would let a landlocked power contest water
    it cannot reach. Ships in a neighbouring sea or in a port on that sea count at the
    usual discount, and carriers at full weight, because their aircraft are already there.
    """
    kind = conn.execute(
        "SELECT kind FROM region WHERE id = ?", (region_id,)
    ).fetchone()
    at_sea = bool(kind) and kind["kind"] == "ocean"
    # **Aircraft are not counted here, in either domain.** They used to be: the test was
    # `<> 'sea'`, so an air wing was summed into a land battle exactly like an infantry
    # division -- at full weight, with no range, and with nothing air-like about it at
    # all. What an air force contributes is the sky, which is fought for separately and
    # arrives as a term in `compute_pressure` below. Counting it twice would make an
    # aeroplane the most efficient infantryman in the game, which is what it was.
    domain = "= 'sea'" if at_sea else "= 'land'"

    local = conn.execute(
        f"""
        SELECT COALESCE(SUM(u.strength * (u.morale / 50.0)), 0) AS s FROM unit u
          LEFT JOIN unit_template t ON t.world_id = u.world_id AND t.kind = u.kind
         WHERE u.nation_id = ? AND u.region_id = ?
           AND COALESCE(t.domain, 'land') {domain}
        """,  # noqa: S608 - domain is one of two literals chosen above
        (nation_id, region_id),
    ).fetchone()

    nearby = conn.execute(
        f"""
        SELECT COALESCE(SUM(u.strength * (u.morale / 50.0)
                            * CASE WHEN u.kind = 'carrier' THEN ? ELSE ? END), 0) AS s
          FROM unit u
          LEFT JOIN unit_template t ON t.world_id = u.world_id AND t.kind = u.kind
         WHERE u.nation_id = ?
           AND COALESCE(t.domain, 'land') {domain}
           AND u.region_id IN (
                SELECT CASE WHEN a.region_a = ? THEN a.region_b ELSE a.region_a END
                  FROM region_adjacency a
                 WHERE a.region_a = ? OR a.region_b = ?
           )
        """,  # noqa: S608 - domain is one of two literals chosen above
        (_CARRIER_REACH, _ADJACENT_WEIGHT, nation_id, region_id, region_id, region_id),
    ).fetchone()

    total = (float(local["s"]) if local else 0.0) + (float(nearby["s"]) if nearby else 0.0)
    if at_sea:
        return total
    total += _from_the_sea(conn, nation_id, region_id)

    # A power with no units anywhere near still brings something to bear -- a fraction of
    # its national strength -- otherwise an undefended region is an automatic loss
    # regardless of who owns it.
    national = conn.execute(
        "SELECT military_strength FROM nation WHERE id = ?", (nation_id,)
    ).fetchone()
    return total + (float(national["military_strength"]) * 0.1 if national else 0.0)


#: What an army coming over a beach is worth against one coming over a border. An opposed
#: landing arrives piecemeal, without its artillery, on ground the defender chose.
_AMPHIBIOUS_WEIGHT = 0.45

#: How much fighting strength one transport puts ashore. The cap this imposes is the point
#: of a transport existing: a landing is bounded by the shipping behind it, not by the size
#: of the army waiting in the ports.
_LIFT_STRENGTH = 6.0


def _crossings(conn: sqlite3.Connection, nation_id: str, region_id: str) -> list[str]:
    """The seas touching ``region_id`` that this nation could actually mount a landing across.

    **Both halves of the question, through the one function that answers it.** The filter
    here used to be a single SQL clause, ``sea.controller_nation_id IS NULL``, and it was
    wrong in both directions at once: it refused a landing across a power's *own* sea --
    winning the water you were about to cross closed the crossing -- and it admitted one
    straight through a sea holding an enemy battle fleet, because a fleet in being writes
    no controller anywhere. See :func:`atlas.engine.naval.crossable`.
    """
    from ..engine.naval import crossable

    world = conn.execute(
        "SELECT world_id FROM region WHERE id = ?", (region_id,)
    ).fetchone()
    if world is None:
        return []
    world_id = str(world["world_id"])
    enemies = {
        str(row["nation_b"])
        for row in conn.execute(
            "SELECT nation_b FROM relation WHERE world_id = ? AND nation_a = ? AND at_war = 1",
            (world_id, nation_id),
        )
    }
    seas = [
        str(row["id"])
        for row in conn.execute(
            """
            SELECT r.id FROM region_adjacency a
              JOIN region r ON r.id = CASE WHEN a.region_a = ? THEN a.region_b
                                           ELSE a.region_a END
             WHERE a.world_id = ? AND (a.region_a = ? OR a.region_b = ?)
               AND r.kind = 'ocean'
             ORDER BY r.id
            """,
            (region_id, world_id, region_id, region_id),
        )
    ]
    return [sea for sea in seas if crossable(conn, nation_id, enemies, sea)]


def _from_the_sea(conn: sqlite3.Connection, nation_id: str, region_id: str) -> float:
    """What this nation could put ashore here, across one sea.

    Two regions away rather than one, and only ever with water in between and shipping on
    it -- which is the whole of what makes an island reachable. The result is capped by
    the transports available, so a fleet of one merchantman does not land an army group;
    that cap is why a transport is worth building and worth sinking.

    **Only troops go ashore.** The sum used to take every unit in the embarkation ports
    whatever its domain, so a battle fleet in harbour landed itself on the beach, an air
    wing waded in beside it, and a transport counted as its own cargo -- a power could
    take a defended coast with no army at all, provided it had a navy. Aircraft are
    excluded for the same reason they are excluded from every gatherer in
    :mod:`atlas.engine.resolution`: what an air force contributes is the sky.

    **And only troops that are not already in the battle.** A province touching both the
    objective and the sea between was counted twice -- once by the ``nearby`` term above
    at :data:`_ADJACENT_WEIGHT` for marching over the border, and again here at
    :data:`_AMPHIBIOUS_WEIGHT` for landing on the same ground it was already standing next
    to. It cannot do both, and the border is the one it would use.
    """
    from ..engine.naval import LIFT_PER_TRANSPORT, lift_hulls

    seas = _crossings(conn, nation_id, region_id)
    if not seas:
        return 0.0

    # **The shipping is counted by the naval layer, not here.** This used to count it in
    # the query below, among the units in the land regions across the water -- so a
    # transport that had put to sea stopped being lift, and `_march_to_the_front` sails
    # them out unprompted onto the seas washing an enemy coast, which is exactly where a
    # landing crosses. The `far` set is built by joining *away* from each crossing sea, so
    # a hull floating in that sea can never be a member of it and no filter here could
    # admit one. Measured on 1936: after one ordinary march, every naval power's transports
    # were at sea and this term fell to zero for fifteen war pairs out of fifteen, while
    # `amphibious_reach` went on offering the same coasts -- an assault that stayed
    # declarable forever and could never be made. `naval.lift_hulls` is the same reader
    # `amphibious_reach` and `resolution._units_shipped_to` already use.
    world = conn.execute(
        "SELECT world_id FROM region WHERE id = ?", (region_id,)
    ).fetchone()
    if world is None:
        return 0.0
    lift = len(
        lift_hulls(conn, str(world["world_id"]), nation_id, seas)
    ) * LIFT_PER_TRANSPORT
    if lift < 1:
        return 0.0

    placeholders = ", ".join("?" for _ in seas)
    row = conn.execute(
        f"""
        SELECT
          COALESCE(SUM(CASE WHEN COALESCE(t.domain, 'land') = 'land'
                            THEN u.strength * (u.morale / 50.0) ELSE 0 END), 0) AS ashore
          FROM unit u
          LEFT JOIN unit_template t ON t.world_id = u.world_id AND t.kind = u.kind
         WHERE u.nation_id = ?
           AND u.strength > 0
           AND u.region_id IN (
                SELECT far.id FROM region_adjacency onward
                  JOIN region far ON far.id = CASE WHEN onward.region_a IN ({placeholders})
                                                   THEN onward.region_b ELSE onward.region_a END
                 WHERE (onward.region_a IN ({placeholders})
                     OR onward.region_b IN ({placeholders}))
                   AND far.kind <> 'ocean'
                   AND far.id <> ?
                   AND far.id NOT IN (
                        SELECT CASE WHEN beside.region_a = ? THEN beside.region_b
                                    ELSE beside.region_a END
                          FROM region_adjacency beside
                         WHERE beside.region_a = ? OR beside.region_b = ?
                   )
           )
        """,  # noqa: S608 - the placeholders are generated, the values are bound
        (nation_id, *seas, *seas, *seas, region_id,
         region_id, region_id, region_id),
    ).fetchone()
    if row is None:
        return 0.0

    # Land strength is everything that is not the shipping itself, capped by what there is
    # to carry it in -- the cap is why a transport is worth building and worth sinking.
    return min(float(row["ashore"] or 0.0), lift * _LIFT_STRENGTH) * _AMPHIBIOUS_WEIGHT


def _terrain_modifier(conn: sqlite3.Connection, region_id: str) -> float:
    row = conn.execute("SELECT terrain FROM region WHERE id = ?", (region_id,)).fetchone()
    terrain = row["terrain"] if row else "plains"
    return {
        "mountains": -0.15,
        "hills": -0.08,
        "forest": -0.05,
        "marsh": -0.10,
        "desert": -0.05,
        "plains": 0.0,
    }.get(terrain, 0.0)


#: The per-turn advance potential at force parity, on open ground, against a defender of
#: ordinary morale.
#:
#: The pace follows from this: expected ground per turn is
#: ``BASELINE_ADVANCE * advantage / resilience * odds``, where ``advantage`` is 1.0 at
#: parity and ``odds`` is the attacker's chance of winning the turn's fighting -- also 0.5
#: at parity. Attrition then erodes the attacker mid-campaign, so the measured pace runs a
#: turn or two longer than that arithmetic alone predicts. Against a single region:
#:
#:     force ratio  0.90 (broken)      2 turns
#:                  0.63               2 turns
#:                  0.52 (parity)      2 turns
#:                  0.42               4 turns
#:                  0.30 (strong)      6 turns
#:                  0.29 (dug in,      8 turns
#:                        mountains)
#:                  0.18 (very)       19 turns
#:
#: **This is set from a whole campaign, not from one siege.** At 0.65 -- the country-level
#: figure, tuned when overrunning a power meant taking one region -- France needed thirty
#: turns to overrun a Germany of ten provinces, and the same arithmetic would have made
#: Yugoslavia's twelve worse. Measuring the promoted map showed why: France meets Germany
#: along Alsace-Lorraine and nothing else, so the front is two or three regions wide
#: against a country five deep, and a conquest is far more sequential than the region
#: count suggests. Eleven turns is what a comparable-power conquest costs now.
#:
#: 1.8 rather than more because that is where the curve flattens. Past it the campaign is
#: bounded by the country's depth and by the cap below, which holds any single region to
#: at least two turns however lopsided the fight -- so a larger figure buys no speed and
#: only flattens the defender-driven gradient above, which is the thing worth keeping.
#:
#: The smoothness matters and was learned the hard way. Gating the advance on *winning*
#: the engagement -- advance on a win, give ground on a loss -- produced a cliff rather
#: than a curve: every force ratio below about 0.6 stalled for eighteen turns and
#: everything above it fell in two, because a near-even attacker random-walks while both
#: armies bleed. Letting the margin of the roll scale the advance continuously gives a
#: defender-driven gradient instead.
#: **What an offensive is worth when it has run past its own supply.**
#:
#: Being cut off already cost an attacker 0.054 of its odds -- `_SUPPLY_SWING` times the
#: 0.45 gap in `supply_multiplier` -- and a play-test measured what that is worth in
#: practice: Russia finished with **thirteen of thirty-one land formations out of supply**
#: and took ground in every seed anyway. Five points of odds is not a logistics constraint,
#: it is a rounding error.
#:
#: **On pace rather than on odds, and that is the whole of the claim.** An army without
#: fuel and shells can still hold the ground it is standing on and can still win the day's
#: fighting -- what it cannot do is *keep going*. So this multiplies the advance, next to
#: air interdiction and the leader's temperament, and leaves `compute_pressure` alone. An
#: offensive that outruns its supply stalls where it stands rather than losing the battle
#: it is in, which is what actually happened to the real advance in 2022.
#:
#: A quarter, not zero: a stalled offensive still gains a little ground on a good day, and
#: an absolute halt would make one cut road as decisive as an encirclement.
_UNSUPPLIED_ADVANCE = 0.25

BASELINE_ADVANCE = 1.8

#: Accumulated pressure at which the region changes hands.
CAPTURE_THRESHOLD = 1.0

#: What a turn's fighting costs, as a share of the force engaged. Deliberately small,
#: and the reason is a feedback loop rather than realism: losses change the force ratio,
#: the force ratio sets the advance, so heavy per-turn losses compound. At the original
#: 0.15/0.30 a *weaker* attacker overran a stronger defender in four turns -- the first
#: casualties tipped the ratio, which sped up the advance, which inflicted more
#: casualties. Halving them keeps the outcome driven by the armies the two sides started
#: with rather than by how quickly the first exchange went.
#:
#: **Halved again when the maps went to provinces.** These were set when most countries were
#: one region and a war lasted two turns, so a month's fighting decided a country and its
#: losses were incidental. Fought province by province the same war is fifteen or twenty
#: months, an attacker fights a battle and a counterattack most of them, and at the old rate
#: both armies were bled out long before either side was beaten -- a play-test watched
#: Turkey's army go from 54 to nothing in Iran while taking three provinces of eleven. A
#: province is a smaller fight than a country, and its month costs proportionately less.
_LOSS_DECISIVE = 0.035
_LOSS_CLOSE = 0.07

#: Ground given up when a turn's fighting goes against the attacker. Much smaller than a
#: turn's gain, so a grinding offensive that loses an exchange is set back rather than
#: undone -- fronts should move, not oscillate.
REPULSE = 0.05

#: What an unpressed contest loses per turn. Without this an attacker could push a region
#: to the brink, walk away for twenty turns, and come back to find it still at 90%.
IDLE_DECAY = 0.15


def defender_resilience(conn: sqlite3.Connection, contest: Contest) -> float:
    """How much harder this defender is to shift than an average one. Never below 1.

    Terrain and dug-in morale slow an advance without changing who is likely to win the
    day's fighting, which is the distinction between this and :func:`compute_pressure`:
    that decides whether the attacker gains ground, this decides how much.
    """
    # _terrain_modifier is negative for hard ground. A -0.15 mountain becomes a 1.6x
    # multiplier on how long it takes to clear.
    resilience = 1.0 - _terrain_modifier(conn, contest.region_id) * 4.0

    row = conn.execute(
        "SELECT AVG(morale) AS m FROM unit WHERE nation_id = ? AND region_id = ?",
        (contest.defender, contest.region_id),
    ).fetchone()
    morale = float(row["m"]) if row and row["m"] is not None else 50.0
    # Fortified troops (morale is raised by digging in, and is engine-only) hold longer.
    resilience *= 1.0 + max(-0.2, (morale - 50.0) / 100.0)

    # **Time in place, which is the one thing an attacker cannot also have.** Keyed on how
    # long these formations have held this ground rather than on whose ground it is: an
    # aggressor who took the province last month is defending it too, and paying him for
    # that was measured and reverted. Zero at the start of every game and zero for anybody
    # who has just arrived, so this is a defensive mechanic rather than a global brake on
    # the pace of conquest.
    from ..engine.entrench import entrenchment_bonus

    resilience *= 1.0 + entrenchment_bonus(conn, contest.defender, contest.region_id)

    return max(1.0, resilience)


def _world_of(conn: sqlite3.Connection, nation_id: str) -> str:
    """Which world a nation is in.

    A `Contest` names who is fighting and where, and has never needed to know which world
    it is in -- every other query it makes is keyed on a globally unique nation or region
    id. The supply walk is the first thing here that is scoped per world, so it looks the
    id up rather than changing the shape of a contest for one caller.
    """
    row = conn.execute(
        "SELECT world_id FROM nation WHERE id = ?", (nation_id,)
    ).fetchone()
    return str(row["world_id"]) if row else ""


def compute_advance(
    conn: sqlite3.Connection,
    contest: Contest,
    profile: DifficultyProfile,
    *,
    player_nation: str | None = None,
) -> float:
    """How far the attacker would push this region in one good turn.

    **Pace is set primarily by the defender.** A country is not overrun in a fixed number
    of turns; it is overrun as fast as its army and its ground allow. A comparable
    defender costs a couple of turns per region, and a strong army dug into mountains can
    hold one for a dozen. That is the whole point of the accumulate-and-threshold model
    over a single roll.
    """
    attack = _strength(conn, contest.attacker, contest.region_id)
    defend = _strength(conn, contest.defender, contest.region_id)
    total = attack + defend
    if total <= 0 or attack <= 0:
        return 0.0

    # Scaled so parity is exactly 1.0: an evenly matched attacker advances at the
    # baseline, and the ratio moves it from there.
    advantage = 2.0 * (attack / total)

    advance = BASELINE_ADVANCE * advantage / defender_resilience(conn, contest)

    # **Interdiction.** The other half of air power, and the half that belongs on pace
    # rather than on odds: an attacker who owns the sky is exploiting against a defender
    # who cannot move reserves by day. It multiplies rather than adds, because it makes a
    # breakthrough go further and does nothing at all for an attack that is not moving.
    from ..engine.air import advance_multiplier

    advance *= advance_multiplier(conn, contest.region_id, contest.attacker)

    # **An offensive that has outrun its supply stalls.** See `_UNSUPPLIED_ADVANCE`: the
    # odds term alone was worth five points and did not slow a Russian advance with
    # thirteen formations cut off behind it. Read through the same door the odds use, so
    # there is one definition of "supplied at this front" and not two.
    from ..engine.logistics import supplied_at_front

    if not supplied_at_front(conn, _world_of(conn, contest.attacker),
                             contest.attacker, contest.region_id):
        advance *= _UNSUPPLIED_ADVANCE

    # **Who is giving the orders.** A bellicose government pushes further than the
    # arithmetic says it should, because it spends men it should not; a cautious one
    # stops short of what it could have taken. On pace rather than on odds, deliberately:
    # a leader changes how hard a country is willing to press an attack, not whether the
    # attack was a good idea. See :mod:`atlas.engine.leaders`.
    from ..engine.leaders import modifier as leader_modifier

    advance *= leader_modifier(conn, contest.attacker, "advance")

    # **Whether the men have anything to advance with.** An attacker whose thread back to
    # its depots has been cut does not stop fighting -- it stops *exploiting*, which is
    # what pace measures. On advance rather than on odds for the same reason interdiction
    # is: a cut-off army can still hold a line, and what it cannot do is go anywhere.
    from ..engine.logistics import supply_multiplier

    advance *= supply_multiplier(
        conn, _world_of(conn, contest.attacker), contest.attacker, contest.region_id
    )

    if player_nation is not None:
        if contest.attacker == player_nation:
            advance *= profile.player_fortune
        elif contest.defender == player_nation:
            advance /= max(0.1, profile.player_fortune)

    return max(0.0, advance)


#: How much being in supply is worth on the odds, per side. Modest next to the force ratio:
#: supply decides whether an army can *move*, and only tilts whether it wins where it
#: stands. The full swing between a supplied attacker facing a cut-off defender and the
#: reverse is twice this.
_SUPPLY_SWING = 0.12


def compute_pressure(
    conn: sqlite3.Connection,
    contest: Contest,
    profile: DifficultyProfile,
    *,
    player_nation: str | None = None,
) -> float:
    """Turn real state plus evidence flags into the attacker's odds.

    Every term is computed here from the database or from the flag table. Nothing in this
    function comes from a model.
    """
    attack = _strength(conn, contest.attacker, contest.region_id)
    defend = _strength(conn, contest.defender, contest.region_id)

    # A logistic-ish curve on the force ratio: overwhelming numbers matter, but never
    # guarantee the result, which is what keeps upsets possible.
    total = attack + defend
    base = 0.5 if total <= 0 else attack / total
    pressure = 0.5 + (base - 0.5) * 1.4

    pressure += _terrain_modifier(conn, contest.region_id)

    # **Being cut off tells on the odds too, both ways.** An unsupplied attacker presses a
    # weaker attack and an unsupplied defender holds a weaker line, so a pocket is easier
    # to reduce than the same troops in their own country -- which is what makes cutting a
    # line worth doing rather than merely worth watching.
    from ..engine.logistics import supply_multiplier

    world_id = _world_of(conn, contest.attacker)
    attacker_supply = supply_multiplier(conn, world_id, contest.attacker, contest.region_id)
    defender_supply = supply_multiplier(conn, world_id, contest.defender, contest.region_id)
    pressure += _SUPPLY_SWING * (attacker_supply - defender_supply)
    for flag in contest.flags:
        pressure += _FLAG_WEIGHTS.get(flag, 0.0)

    # **A survivable nuclear arsenal makes the homeland it guards harder to take, before
    # anybody launches.** This is where most of the value of the nuclear layer is, and it
    # costs one query: invading a nuclear power is a different proposition from invading
    # anybody else, and the map should say so without a warhead ever going off.
    #
    # Imported inside the function on purpose. This module is the adjudicator and knows
    # about force ratios and terrain; nuclear weapons are *content* built on top of it,
    # and a module-level import here would invert that. It is a dict lookup after the
    # first call. Exactly 0.0 on any map with no arsenal rows -- which is every 1936
    # world and every world written before schema 10.
    from ..engine.nuclear import deterrence, tactical_edge

    pressure -= deterrence(conn, contest.defender, contest.region_id)
    # **And the other half of the same arsenal, pulling the other way.** Strategic weapons
    # make ground harder to take and battlefield weapons make it easier to fight over, so a
    # power holding both appears on both sides of this line. Both sides of a contest can
    # hold them, and the term is the difference -- two armies with tactical weapons are back
    # where they started, which is what happened.
    pressure += (tactical_edge(conn, contest.attacker, contest.region_id)
                 - tactical_edge(conn, contest.defender, contest.region_id))

    # **And who owns the sky above it.** Same layering argument as the import above: this
    # module knows about force ratios, and air power is content built on top of it. Zero
    # for a region no aircraft have ever met over, which is every region of every world
    # written before schema 12 and most regions of every world since.
    from ..engine.air import superiority_term

    pressure += superiority_term(
        conn, contest.region_id, contest.attacker, contest.defender
    )

    # **And who is paying for it.** A proxy war is fought by the client and decided partly
    # by the patron, and until this term existed the shipments were invisible to the only
    # arithmetic that could have shown them. Same layering argument as the two imports
    # above: this module knows about force ratios, and who is arming whom is content built
    # on top of that. Exactly 0.0 on a world with no `support` table, which is every world
    # written before schema 22.
    from ..engine.support import combat_bonus

    pressure += combat_bonus(conn, world_id, contest.attacker)
    pressure -= combat_bonus(conn, world_id, contest.defender)

    # Difficulty tilts the dice as well as the prose. Prompt-only difficulty is a
    # suggestion a model can ignore; numbers-only difficulty tells the same story with
    # different results. Both halves, always.
    if player_nation is not None:
        if contest.attacker == player_nation:
            pressure *= profile.player_fortune
        elif contest.defender == player_nation:
            pressure /= max(0.1, profile.player_fortune)

    return min(0.95, max(0.05, pressure))


def resolve_contest(
    conn: sqlite3.Connection,
    contest: Contest,
    *,
    world_id: str,
    turn: int,
    seed: int,
    difficulty: Difficulty = Difficulty.NORMAL,
    player_nation: str | None = None,
    span: float = 1.0,
) -> Outcome:
    """Decide a contest. The engine draws; the model is not consulted.

    ``span`` is how many reference months this round of fighting covers. It is ``1.0`` for
    an ordinary monthly round -- which is every round of a monthly, seasonal or yearly
    jump, because those are split into months upstream -- and a fraction for a jump
    shorter than a month.
    """
    profile = DIFFICULTY_PROFILES[difficulty]
    pressure = compute_pressure(conn, contest, profile, player_nation=player_nation)

    rng = derive(seed, world_id, turn, contest.stream_key())
    roll = rng.random()
    attacker_wins = roll < pressure

    # Margin drives losses: a close-run thing is bloody for both sides, a rout is not.
    margin = abs(roll - pressure)
    decisive = margin > 0.30

    attack = _strength(conn, contest.attacker, contest.region_id)
    defend = _strength(conn, contest.defender, contest.region_id)
    # Ground gained and blood spent are both *flows*, so both scale with how much time
    # this round of fighting covers -- and they scale by the same factor, which is what
    # keeps the attrition feedback loop (losses shift the force ratio, which shifts the
    # advance) at exactly the tuning it was given. A round shorter than a month inches;
    # a round is never longer than a month, because the caller splits a long jump into
    # months rather than taking one enormous step. See :mod:`atlas.engine.pacing`.
    intensity = (_LOSS_DECISIVE if decisive else _LOSS_CLOSE) * span

    if attacker_wins:
        attacker_losses = jitter(rng, attack * intensity * 0.6, 0.25)
        defender_losses = jitter(rng, defend * intensity * 1.4, 0.25)
    else:
        attacker_losses = jitter(rng, attack * intensity * 1.4, 0.25)
        defender_losses = jitter(rng, defend * intensity * 0.6, 0.25)

    # How far the line moves. The same roll that decided the day scales the advance, so a
    # narrow win inches forward and a rout covers ground -- and a bad enough day gives
    # ground back. No second draw is taken: the margin is the noise, which keeps the
    # advance exactly as reproducible as the outcome that produced it.
    #
    # The cap scales too. It exists to hold any single region to at least two rounds
    # however lopsided the fight; leaving it fixed while the advance shrank would let a
    # weekly round take three quarters of a province, which is the opposite of the
    # guarantee.
    swing = 0.5 + (pressure - roll)
    advance = max(
        -REPULSE * span,
        min(
            CAPTURE_THRESHOLD * 0.75 * span,
            compute_advance(conn, contest, profile, player_nation=player_nation)
            * swing
            * span,
        ),
    )

    return Outcome(
        contest=contest,
        attacker_wins=attacker_wins,
        pressure=pressure,
        roll=roll,
        attacker_losses=round(max(0.0, attacker_losses), 2),
        defender_losses=round(max(0.0, defender_losses), 2),
        decisive=decisive,
        narrative_hint=(
            f"{'decisive' if decisive else 'narrow'} "
            f"{'attacker' if attacker_wins else 'defender'} victory"
        ),
        advance=round(advance, 4),
    )


def morale_shift(rng_seed: int, world_id: str, turn: int, nation_id: str, base: float) -> float:
    """A small seeded wobble, so identical situations do not produce identical numbers."""
    rng = derive(rng_seed, world_id, turn, f"morale:{nation_id}")
    return round(jitter(rng, base, 0.15), 2)


def uncertain(
    rng_seed: int, world_id: str, turn: int, key: str, probability: float
) -> bool:
    """A one-off weighted decision on its own stream, for engine-side coin flips."""
    return chance(derive(rng_seed, world_id, turn, key), probability)
