"""What a country earns each turn, and what it costs to keep what it has.

Found by playing France through sixteen turns: the treasury was a one-shot budget. France
opens 1936 with eighty, and after a housing programme and a research programme it had
twenty-five and could never afford anything again. Every economic decision in the game
happened before turn four. There was no income anywhere in the engine -- ``treasury`` only
ever went down.

So a turn now has a budget. **Revenue** comes from the size of the economy and the
development of the ground a nation holds, which is what makes a public works programme pay
for itself eventually and what makes losing provinces hurt beyond the map. **Upkeep** is
charged per formation, and a fleet costs several times what a division does, because it
did: a battleship in commission was a permanent line in a naval estimate and the reason
half the world's capital ships spent the thirties in reserve.

The two together are the tension the peaceful layer needs. Income alone is free money;
upkeep alone is a slow loss. Together, an army you are not using is an army you are paying
for instead of building something.

**A country that cannot pay does not go into debt.** It pays what it can, the treasury
stops at nothing, and the government takes the blame -- which is a far more legible failure
than a negative number, and keeps the ``UNIT-001`` overdrawn check meaning what it says.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..state.apply import apply_batch, record_event
from ..state.diff import Diff, DiffBatch
from ..state.models import DiffOp, EventKind, EventOrigin, Role, Visibility
from .bounds import absorbable
from .constituencies import labour_drag
from .doctrines import factor
from .mobilisation import (
    ceiling_factor,
    recovery_factor,
    revenue_drag,
)
from .pacing import compounded, over, span_of
from .wareffort import manpower_ceiling

__all__ = [
    "STRAIN_THRESHOLD",
    "WAR_ECONOMY_BONUS",
    "arsenal_upkeep",
    "strain_pressure",
    "upkeep_strain",
    "pact_upkeep",
    "BudgetReport",
    "supportable_economy",
    "collect_revenue",
    "erode_development",
    "ground_value",
    "ground_values",
    "nation_budget",
]

#: Share of the economy that reaches the treasury each turn.
#:
#: **Calibrated against the real 1936 orders of battle, twice.** The first pass gave France
#: nine a turn against fifteen of upkeep, so she went bankrupt on turn five and bled
#: stability for the rest of the game -- which merely replaced "you can never build
#: anything" with "you can never build anything and it is your fault". These figures put a
#: great power at peace six to eight a turn ahead: a housing programme is three or four
#: turns of saving, a battleship is most of a year, and Britain's twenty-seven warships
#: leave her barely in the black, which is exactly the position the Royal Navy actually put
#: the Treasury in.
#:
#: **Lowered from 0.25 when development stopped being a constant.** The two coefficients
#: are one calibration: with every region seeded at 1.0 the development term was paying a
#: flat 0.25 per province, so the totals below were a quarter of the economy plus a
#: province count. Now that a nation's ground totals ``DEV_PER_ECONOMY`` (0.55) of its
#: economy, the same great powers land within a point of where they were -- Britain 20.5
#: to 20.6, Germany 19.0 to 18.9 -- while the powers that were being paid for owning large
#: amounts of poor land are corrected downward, which was the point.
_REVENUE_FROM_ECONOMY = 0.17

#: What developed ground is worth on top. **Two fifths of a great power's income**, which
#: is a number the playtest chose rather than the design. At the first calibration ground
#: was a quarter of revenue, and taking Silesia, the Saar and Berlin off Germany -- 53% of
#: its industrial ground -- cost it 12% of its income. A conquest that removes half a
#: country's industry and is felt as a rounding error is a conquest the economy did not
#: notice, so the split was moved until it was. The pair still sums to the same coefficient
#: against economy at seed, so nobody's turn-one budget moved.
_REVENUE_FROM_DEVELOPMENT = 0.21

#: What ground you hold but do not own yields, as a share. Conquest is not immediately
#: profitable: the works are wrecked, the workforce is hostile and the administration is
#: yours to rebuild. It is the reason taking a rich province is worth doing *and* worth
#: investing in afterwards rather than simply banking.
_OCCUPIED_YIELD = 0.4

#: How fast restive ground loses its development, per point of unrest residual per month.
#: Occupation degrades what it holds -- and because the residual is what is left *after*
#: garrison relief, a province with troops sitting on it does not decay at all. The same
#: number therefore prices both halves of holding foreign ground.
_UNREST_EROSION = 0.06

#: And what a sanctions regime does to the ground it is aimed at, per month per point of
#: severity. Small: sanctions bite an economy and a treasury directly already, and a third
#: large term on the same cause would be the same punishment counted three times.
_SANCTION_EROSION = 0.02

#: Development never erodes below this. Same reasoning as the seed floor and the strike
#: floor: a province at zero is one no later mechanic can reason about.
_DEVELOPMENT_FLOOR = 0.25

#: Per-turn upkeep by domain. A capital ship is not a division, and pretending otherwise
#: is why every naval power in a strategy game builds nothing else.
#:
#: **Cut from 0.18/0.35/0.6, because at those rates nobody could afford a war.** Measured
#: across all three eras at seed: upkeep took 80-90% of the revenue of every militarised
#: power -- Russia 90%, Britain 81%, the United States 83% -- and the Cold War's United
#: States and the Gathering Storm's Japan were *structurally insolvent before a shot was
#: fired*, netting -2% and -11% a month. A power with no discretionary income cannot build,
#: cannot reinforce and cannot respond to anything, so every economic mechanic in the engine
#: was reaching for money that was not there.
#:
#: **The land rate moved proportionally the most, and that is deliberate.** Two checks say
#: it was the wrong one. Against build cost these rates were backwards -- a hull costing 130
#: to lay down paid 0.6 a month, 0.46%, while a division costing 9 paid 0.18, a full 2% --
#: so an army was four times dearer to keep than a fleet, per unit of what it cost to make.
#: And modelled on paper first, cutting the *naval* rate alone was regressive in exactly the
#: way the reinforcement pricing had already been: navies are two thirds of a great power's
#: bill, so it hands the money to the United States, Japan and Britain and gives a country
#: fighting a land war with no fleet precisely nothing.
_UPKEEP: dict[str, float] = {"land": 0.12, "air": 0.30, "sea": 0.50}

#: What a country at war raises on top of its ordinary revenue: taxes, borrowing, and
#: production directed by a government that has stopped asking. **Bounded and reversible**
#: -- it lasts exactly as long as the war does, and it is a share of what a country already
#: earns, so it makes a big economy bigger and cannot conjure one out of a small one.
#:
#: This exists because the upkeep cut alone does not reach the case that matters. A power's
#: revenue *falls* as it loses ground while its upkeep does not, so a country being beaten
#: is squeezed from both ends and arrives at zero discretionary income exactly when it most
#: needs to replace what it is losing -- which is the death spiral the Ukraine play-test
#: measured, revenue falling 3.54 to 2.29 against a flat 2.19 of upkeep.
#:
#: 30% and not more: it must not make war *pay*. A war still costs a country its ground,
#: its formations and the yield of everything under occupation, and this is meant to let a
#: government fight rather than to reward it for starting one. **Balance number, tunable.**
WAR_ECONOMY_BONUS = 0.30

#: **What a deterrent costs to hold, per month, by how it would be delivered.**
#:
#: Nothing charged anything for warheads before this: an arsenal was free to keep and free
#: to grow, so a nuclear power paid for its deterrent exactly once, in the scenario that
#: seeded it. That is the wrong shape for the one military asset whose whole cost is
#: standing readiness.
#:
#: **By tier and not by count, mostly, because that is where the money went.** A deterrent
#: is a *programme* -- silos, boats, crews, alert bombers -- and a triad is three programmes
#: running at once. The marginal cost of one more warhead on an existing missile is small,
#: and :data:`_ARSENAL_PER_WARHEAD` is deliberately small to match.
#:
#: **Balance numbers, tunable, and sized against the shipped eras rather than invented.**
#: Measured before they were chosen, and re-measured when Britain turned out to be paying
#: a full programme for four boats. See `scratchpad/shot_nuclear.py`, which prints the
#: before and after for every armed nation in all three eras.
_ARSENAL_UPKEEP: dict[str, float] = {
    # **Sea came down from 0.22 after measuring what it did to Britain.** Four Polaris
    # boats are the cheapest credible second strike anybody ever built, and pricing them
    # two thirds of a triad was pricing the *idea* of a boat force rather than that one.
    "none": 0.0, "aircraft": 0.08, "missile": 0.15, "sea": 0.18, "triad": 0.30,
}

#: **What share of a tier's programme a token force pays**, and how large a force pays all
#: of it. Britain's four warheads and America's forty-six were charged nearly the same
#: programme, differing only by the tier -- so a squadron cost what an establishment did.
#:
#: A deterrent has real fixed costs whatever its size, which is why the floor is a quarter
#: and not zero: you need the boats, the crews and the alert state before you need the
#: second warhead. **Balance numbers, tunable**, and chosen by measuring every armed nation
#: in both eras rather than by argument -- see `scratchpad/shot_nuclear.py`.
_ARSENAL_SMALL_FORCE = 0.25
_ARSENAL_FULL_FORCE = 24.0

#: The marginal cost of one more warhead on a delivery system somebody is already paying
#: for. **Balance number, tunable.**
#:
#: **Down from 0.004, and the old value contradicted the design it was written under.** At
#: forty-six warheads the count came to 0.184 against a triad programme of 0.35 -- more
#: than half, on a charge whose whole premise is that the money is in the silos and the
#: boats and not in the warhead on top. It also made the largest arsenal on a bare missile
#: force cost more in warheads than in programme, which is the shape this is not.
#:
#: Found by adding the pact charge on top: the 1972 United States has 0.714 a month of
#: margin and the arsenal was taking 0.694 of it, so a superpower could not afford one more
#: bill of any size. It keeps 0.26 now, and pays a real deterrent bill and a real alliance
#: bill out of it.
_ARSENAL_PER_WARHEAD = 0.0015


#: **What one standing promise to fight for somebody costs to keep, per month.**
#:
#: A pact was free. A country could sign every defensive agreement on offer and pay nothing
#: for any of them, so an alliance web was pure benefit and "less entangled" could not be
#: said in the only language a budget speaks. What a commitment actually costs is readiness
#: somebody else's crisis decides the timing of: forces held where a partner might need
#: them, plans kept current, exercises nobody would otherwise run.
#:
#: Only the kinds that promise force. A trade agreement or a non-aggression pact costs
#: nothing to hold, because neither asks you to be ready for anything.
#:
#: **Balance number, tunable, and small on purpose.** The most-allied power on any shipped
#: map holds four, so this is bounded by construction -- and it is sized against the 1972
#: United States, which has the thinnest margin of any great power in any era.
_PACT_UPKEEP = 0.03

#: Which agreements are a standing commitment of force rather than a piece of paper.
_ENTANGLING: tuple[str, ...] = ("defensive_pact", "alliance")


#: Manpower recovers toward what the population can sustain. Slow: a war fought to the
#: bottom of the manpower pool is not won back in a season.
_MANPOWER_RECOVERY = 0.04

#: An economy grows toward the size its industrial base can support, and no further. This
#: is the compounding heart of the peaceful game: found by playing twenty turns of Italy
#: where economy, industry and legitimacy never moved once. A steelworks is not a one-off
#: bump any more -- it raises the ceiling the economy then climbs toward for years, so a
#: builder watches their country grow every quarter and a hoarder watches it sit still.
#: The multiplier sits just above the seed's industry-to-economy ratio (industry defaults
#: to 0.6 of economy), so a nation that builds nothing drifts up gently and one that
#: invests pulls away.
#:
#: **Lowered from 1.8 when development joined it.** The ceiling used to be industry alone,
#: which made the two halves of the economic game strangers: building factories compounded
#: and developing provinces did not, so "develop your land" was a cash lever with no growth
#: behind it and no reason to prefer a rich province to a poor one beyond the immediate
#: revenue. Now both feed it, and the pair is calibrated to leave the seed equilibrium
#: exactly where it was -- industry seeds at 0.6 of economy and ground at 0.55 of it, so
#: ``0.6 * 1.4 + 0.55 * 0.43`` reproduces the 1.08 the old ``0.6 * 1.8`` gave. Nothing
#: about turn one changes; everything about what a turn *can do* does.
#:
#: The consequence worth naming: **losing provinces now shrinks your economy**, slowly,
#: rather than only your income. Losing the Ruhr is not a line in a budget, it is a
#: country that is smaller in two years' time.
_ECONOMY_PER_INDUSTRY = 1.4
_ECONOMY_FROM_DEVELOPMENT = 0.43
_ECONOMY_GROWTH_RATE = 0.06

#: **Where industrialising stops paying proportionally.** BOTH CONSTANTS ARE TUNABLE and
#: this note is the argument for the shape rather than for the numbers.
#:
#: The drift above is a converging pull toward a ceiling and was never the problem. What
#: compounded was the *ceiling*: it is linear in industry and development, both of which are
#: bought with a treasury that the economy feeds, so the loop had no damping term anywhere
#: in it. Measured over 110 turns of gathering-storm with no models steering: world economy
#: 957 -> 8615 (9.0x) and industry 574 -> 6970 (12.1x), about 27% compound annual growth,
#: with the per-decade increments still rising at the end.
#:
#: **The knee is set above every nation on every shipped map, deliberately.** The largest
#: raw target at seed is the United States at 103.3 in 1936 and 107.7 in 2026, and the
#: economy meter is drawn against a ceiling of 100 -- so at 120 this transform is the
#: *identity* for every country in every era on the day the world is built, and the early
#: game is not merely "materially unchanged" but bit-identical. It only begins to bite once
#: a country has grown past anything the historical map contains, which is exactly where a
#: century of unchecked compounding was taking it.
#:
#: Above the knee the return is Michaelis-Menten: each further unit of raw industrial base
#: buys less economy than the last, and the whole thing asymptotes at
#: ``_ECONOMY_KNEE * (1 + _ECONOMY_SATURATION)`` -- 192 here, about twice the largest
#: economy the game has ever shipped. That is a plateau a builder takes decades to
#: approach, not a wall they hit.
_ECONOMY_KNEE = 120.0
_ECONOMY_SATURATION = 0.6


def supportable_economy(raw: float) -> float:
    """What an industrial base of ``raw`` can actually hold up.

    Identity below :data:`_ECONOMY_KNEE`, saturating above it. Separate and named because
    two things need to agree about it -- the drift that moves an economy and any test that
    argues about the shape -- and because a curve worth arguing with should be somewhere a
    reader can find.
    """
    if raw <= _ECONOMY_KNEE:
        return raw
    over = raw - _ECONOMY_KNEE
    return _ECONOMY_KNEE + over / (1.0 + over / (_ECONOMY_KNEE * _ECONOMY_SATURATION))

#: **What a quiet decade puts back.** BOTH CONSTANTS ARE TUNABLE.
#:
#: Every writer of `stability` in the engine was a charge -- arrears, coups, colonial
#: unrest, a nuclear strike, the disruption of building something -- and nothing anywhere
#: restored it. So peace did not heal; it only failed to hurt, and a country at peace for a
#: century still arrived at the bottom of the scale by ordinary accumulation. Measured over
#: 110 turns of gathering-storm with no models steering, no wars, no occupied ground and
#: nobody bankrupt: founders' average 57.8 -> 37.2.
#:
#: **One-directional, and that is the safety property.** This only ever pulls *up*, toward
#: `_STABILITY_RESTING`, so a well-governed country at 70 is not dragged down to 50 and the
#: number does not become a magnet that makes governing pointless. There is no upward
#: runaway available: the drift is a share of the remaining distance and stops at the
#: resting point.
#:
#: **And it is gated on actually being at peace**, which is what makes it a reward rather
#: than a floor. A country at war, in arrears, or with foreign troops on its own soil heals
#: nothing at all and degrades exactly as it did before.
#: **Above `leaders._DANGER_BELOW`, and that is the whole choice.** At 50 -- the danger
#: threshold itself -- the equilibrium a peaceful country settles at lands *at or just
#: below* the line where coup risk starts, so a century of peace converged on permanent,
#: mild danger: measured at 48.4 average with coup risk tripled from 0.46% to 1.48%. A
#: resting point has to be somewhere a country can rest.
#:
#: 55 rather than higher because it is still below the seeded average of 57.8: this puts
#: nothing back that a scenario did not start with, and a well-governed country is on its
#: own above it.
_STABILITY_RESTING = 55.0
_STABILITY_RECOVERY = 0.04

#: Legitimacy tracks how well the country is actually governed. A state kept stable earns
#: the benefit of the doubt; a shaky one loses it, however loved it once was. Deliberately
#: *gentle*: this is background weather, not the main lever. The player's own legitimacy
#: choices -- public health, schools, land reform, a freeform appeal -- should be what
#: actually moves the number; the pull toward stability only nudges it over a long run.
#: (First set at 0.05, which closed ~70% of the gap in two game-years and read as a laggy
#: copy of stability. Halved so autopilot matters less than what the government decides.)
_LEGITIMACY_DRIFT_RATE = 0.03

#: What failing to pay the bills costs a government, per turn, in standing.
_ARREARS_COST = 1.5

#: The smallest drift worth writing a diff for, per month. Below it the change is noise
#: and the diff is churn in the history nobody can read.
#:
#: **Scaled by the turn's length like everything else here, and for a sharper reason than
#: tidiness.** As a fixed floor it silently deleted a short turn's growth: a week's share
#: of a 0.06 monthly rate is 0.014, so once a nation came within a couple of points of its
#: industrial ceiling every weekly drift rounded under the floor and was dropped, and a
#: player on weekly turns watched their economy stop climbing for no reason they could
#: see. Twenty-four months of weekly play gained 0.5 where monthly gained 2.8.
#:
#: **Then set to the grain the column can actually record, because a floor on a
#: gap-closing rate is a dead zone.** Every drift here is ``gap * rate``, so the drift
#: shrinks as the value approaches its target and the floor eventually catches it *for
#: good*: the value stops at ``threshold / rate`` short of where it is supposed to settle
#: and stays there forever. At 0.05 that stranded manpower **1.5 below its ceiling** --
#: identically at 7, 30, 90 and 365 days, which is what makes it a dead zone rather than
#: the span bug it was first read as. On a ten-point pool that is 15% of a nation's
#: manpower permanently missing, with nothing in the game saying why.
#:
#: 0.01 is not a tuning choice. Diff values here are ``round(v, 2)``, so a drift below
#: 0.005 records as ``0.00`` -- a floor at the rounding grain suppresses exactly the diffs
#: that would write nothing and no others. The strand distance falls to 0.25 on manpower
#: (2.5%), 0.17 on economy and 0.33 on legitimacy, and the drifts still converge and stop:
#: a world at equilibrium pays no diffs, which was the original point.
_NOTICEABLE = 0.01


@dataclass(slots=True)
class BudgetReport:
    collected: int = 0
    #: Nations that could not meet their upkeep this turn.
    in_arrears: list[str] = field(default_factory=list)
    #: ``name: share`` for nations whose populace cost them revenue this turn. Surfaced
    #: rather than merely charged: a player who is poorer and cannot see why has been given
    #: a bug, not a mechanic.
    on_strike: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def ground_value(conn: sqlite3.Connection, world_id: str, nation_id: str) -> float:
    """What the ground a nation holds is worth to it, discounted for occupation.

    One function, because two callers need it and they must not drift: revenue reads it,
    and so does the ceiling the economy grows toward. A province you hold but do not own
    yields :data:`_OCCUPIED_YIELD` of its development in both.
    """
    row = conn.execute(
        """
        SELECT COALESCE(SUM(r.development
                            * CASE WHEN r.owner_nation_id = ? THEN 1.0 ELSE ? END), 0) AS v
          FROM region r
         WHERE r.world_id = ? AND r.kind <> 'ocean'
           AND COALESCE(r.controller_nation_id, r.owner_nation_id) = ?
        """,
        (nation_id, _OCCUPIED_YIELD, world_id, nation_id),
    ).fetchone()
    return float(row["v"]) if row else 0.0


def ground_values(conn: sqlite3.Connection, world_id: str) -> dict[str, float]:
    """:func:`ground_value` for every nation in the world, in one statement.

    The same arithmetic, grouped rather than asked 204 times. The group key is the
    holder -- controller if there is one, owner otherwise -- and the discount compares
    the owner against that same key, which is exactly what the per-nation query does when
    it is handed that nation's id.
    """
    return {
        str(row["nid"]): float(row["v"])
        for row in conn.execute(
            """
            SELECT COALESCE(r.controller_nation_id, r.owner_nation_id) AS nid,
                   COALESCE(SUM(r.development
                                * CASE WHEN r.owner_nation_id
                                          = COALESCE(r.controller_nation_id,
                                                     r.owner_nation_id)
                                       THEN 1.0 ELSE ? END), 0) AS v
              FROM region r
             WHERE r.world_id = ? AND r.kind <> 'ocean'
             GROUP BY nid
            """,
            (_OCCUPIED_YIELD, world_id),
        )
        if row["nid"] is not None
    }


def _at_peace(conn: sqlite3.Connection, world_id: str, nation_id: str) -> bool:
    """Nothing to recover from: no war, nobody standing on your ground, and the bills paid.

    Deliberately all three. A country that is technically not at war but has an enemy in
    two of its provinces is not at peace in any sense a recovery term should reward, and one
    that cannot meet its own payroll is being told something by the arrears charge that a
    healing term would quietly undo.
    """
    row = conn.execute(
        """
        SELECT (SELECT COUNT(*) FROM relation
                 WHERE world_id = ? AND at_war = 1
                   AND (nation_a = ? OR nation_b = ?))                       AS wars,
               (SELECT COUNT(*) FROM region
                 WHERE world_id = ? AND owner_nation_id = ?
                   AND controller_nation_id IS NOT NULL
                   AND controller_nation_id <> ?)                            AS occupied,
               (SELECT treasury FROM nation WHERE id = ?)                    AS treasury
        """,
        (world_id, nation_id, nation_id, world_id, nation_id, nation_id, nation_id),
    ).fetchone()
    if row is None:
        return False
    return (not int(row["wars"] or 0) and not int(row["occupied"] or 0)
            and float(row["treasury"] or 0.0) > 0.0)


def _fighting(conn: sqlite3.Connection, world_id: str, nation_id: str) -> bool:
    """Whether this nation is in a war right now.

    **Not the negation of `_at_peace`.** That one asks a harder question for a different
    purpose -- it also refuses a nation with an enemy standing on its ground or a treasury
    it cannot balance, because a *recovery* term must not reward either. A war footing turns
    on the war and nothing else: a government that is losing and broke is precisely the one
    that mobilises hardest, and reading `not _at_peace` here would hand the bonus to every
    bankrupt country in the world whether or not anybody was shooting at it.
    """
    row = conn.execute(
        "SELECT COUNT(*) AS wars FROM relation"
        " WHERE world_id = ? AND at_war = 1 AND (nation_a = ? OR nation_b = ?)",
        (world_id, nation_id, nation_id),
    ).fetchone()
    return bool(row and int(row["wars"] or 0))


def arsenal_upkeep(conn: sqlite3.Connection, world_id: str, nation_id: str) -> float:
    """What holding a deterrent costs this nation per month. ``0.0`` for everybody else.

    Zero on any world with no `arsenal` table and on every nation without a row in it,
    which is every country of every 1936 world -- so an era with no warheads in it is
    charged exactly as it always was.

    Scaled by the nation's nuclear posture, if it has one: massive retaliation keeps more
    of the force on alert and pays for it, and minimum deterrence is cheaper to hold
    precisely because it is smaller and less ready. 1.0 for a nation with no doctrine.
    """
    try:
        row = conn.execute(
            "SELECT warheads, delivery FROM arsenal WHERE world_id = ? AND nation_id = ?",
            (world_id, nation_id)).fetchone()
    except sqlite3.OperationalError:
        return 0.0
    if row is None or int(row["warheads"] or 0) <= 0:
        return 0.0

    # **The programme scales with how much of it there is.** See `_ARSENAL_SMALL_FORCE`:
    # a token deterrent pays a quarter of its tier and a full one pays all of it, because
    # a squadron of boats is not the same undertaking as a navy of them.
    warheads = int(row["warheads"])
    scale = _ARSENAL_SMALL_FORCE + (1.0 - _ARSENAL_SMALL_FORCE) * min(
        1.0, warheads / _ARSENAL_FULL_FORCE)
    charge = (_ARSENAL_UPKEEP.get(str(row["delivery"]), 0.0) * scale
              + warheads * _ARSENAL_PER_WARHEAD)
    return round(charge * factor(conn, world_id, nation_id, "nuclear_posture",
                                 "arsenal_upkeep"), 4)


def pact_upkeep(conn: sqlite3.Connection, world_id: str, nation_id: str) -> float:
    """What this nation's standing promises cost it per month. ``0.0`` for an unaligned one.

    **Derived from the commitments themselves, with no schema at all.** The agreements are
    already rows and already have a status; what was missing was anybody charging for them.
    A pact that lapses or is broken stops costing in the same turn it stops binding, for
    free, because this counts what is active rather than what was ever signed.

    Scaled by the nation's alignment doctrine. A power built on collective security keeps
    its promises in a state to be called on and pays for that; an armed neutral that holds
    a pact at all holds it lightly. **The larger effect is not the multiplier**: a neutral
    may not sign one in the first place, so its bill is usually zero because the count is.

    **A promise to nobody costs nothing.** An agreement counts only while some *other*
    party to it is still alive: an alliance whose partner has been wiped off the map stays
    `active` in the table (nothing retires it at elimination) and used to be billed every
    month for the rest of the game.
    """
    try:
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS n FROM commitment c
              JOIN commitment_nation cn ON cn.commitment_id = c.id
             WHERE c.world_id = ? AND c.status = 'active' AND cn.nation_id = ?
               AND c.kind IN ({", ".join("?" for _ in _ENTANGLING)})
               AND EXISTS (SELECT 1 FROM commitment_nation other
                             JOIN nation n ON n.id = other.nation_id AND n.world_id = c.world_id
                            WHERE other.commitment_id = c.id
                              AND other.nation_id <> cn.nation_id AND n.is_alive = 1)
            """,
            (world_id, nation_id, *_ENTANGLING)).fetchone()
    except sqlite3.OperationalError:
        return 0.0
    held = int(row["n"]) if row else 0
    if held <= 0:
        return 0.0
    return round(held * _PACT_UPKEEP * factor(conn, world_id, nation_id, "alignment",
                                              "pact_upkeep"), 4)


#: Where a budget stops being comfortable and starts being a strain, as the share of
#: revenue the establishments eat.
#:
#: **The graded signal that replaced the bankruptcy flag.** `in_arrears` only fires when a
#: government cannot pay at all, which across five thirty-turn play-test games happened
#: *zero* times -- so anything hung off it is a mechanic that never runs. What does vary is
#: this ratio: the 2026 map sits between 0.80 and 0.86, so the penalty starts here and
#: reaches its full weight at 1.0, where a country's whole income goes on its forces.
STRAIN_THRESHOLD = 0.85


def upkeep_strain(conn: sqlite3.Connection, world_id: str, nation_id: str) -> float:
    """How much of this nation's revenue its establishments eat, ``0.0`` upward.

    One copy, because two layers read it -- `morale.drift_morale` and
    `nuclear.drift_readiness` -- and a second implementation would drift from this one the
    first time the revenue formula changed.
    """
    revenue, upkeep = nation_budget(conn, world_id, nation_id)
    if revenue <= 0:
        return 1.0 if upkeep > 0 else 0.0
    return upkeep / revenue


def strain_pressure(conn: sqlite3.Connection, world_id: str, nation_id: str) -> float:
    """The strain above :data:`STRAIN_THRESHOLD`, as ``0.0`` to ``1.0``.

    Graded rather than a cliff: everybody under the line is at zero, and a country
    spending its entire revenue on its forces is at one.
    """
    strain = upkeep_strain(conn, world_id, nation_id)
    return max(0.0, min(1.0, (strain - STRAIN_THRESHOLD)
                        / max(1e-9, 1.0 - STRAIN_THRESHOLD)))


def nation_budget(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> tuple[float, float]:
    """``(revenue, upkeep)`` for one nation this turn, before anything is written."""
    row = conn.execute(
        "SELECT economy FROM nation WHERE id = ? AND world_id = ?", (nation_id, world_id)
    ).fetchone()
    if row is None:
        return 0.0, 0.0

    revenue = (
        float(row["economy"]) * _REVENUE_FROM_ECONOMY
        + ground_value(conn, world_id, nation_id) * _REVENUE_FROM_DEVELOPMENT
    )
    # **And what the country actually turned up for.** A populace below its resting point
    # strikes, goes slow and stays home, and the whole of that lands here rather than as a
    # charge beside the budget: one account of what a country earned, which is the only way
    # the number a player is shown and the number the treasury receives stay the same. Zero
    # for every nation nobody has angered, which is most of them.
    revenue *= 1.0 - labour_drag(conn, nation_id)

    # **A country at war puts its economy on a war footing.** Applied after the labour drag
    # rather than before it, so a government whose own people have downed tools does not
    # mobilise its way out of a strike it caused: the war footing raises what a working
    # country can be made to yield, and multiplies whatever is actually being produced.
    if _fighting(conn, world_id, nation_id):
        revenue *= 1.0 + WAR_ECONOMY_BONUS

    # **And the hands that are no longer in the factories.** A call-up takes people out of
    # the economy, and this is where that is felt: beside the strike drag, in the one number
    # the whole economy is computed from, so what a player is shown and what the treasury
    # receives stay the same figure.
    revenue *= 1.0 - revenue_drag(conn, world_id, nation_id)

    upkeep = 0.0
    for entry in conn.execute(
        """
        SELECT COALESCE(t.domain, 'land') AS domain, COUNT(*) AS n FROM unit u
          LEFT JOIN unit_template t ON t.world_id = u.world_id AND t.kind = u.kind
         WHERE u.world_id = ? AND u.nation_id = ? GROUP BY COALESCE(t.domain, 'land')
        """,
        (world_id, nation_id),
    ):
        domain = str(entry["domain"])
        # **A conscript division costs less to keep than a professional one**, and that is
        # the other half of what mass mobilisation buys: more of them, cheaper each. Land
        # only -- a doctrine about how the army is raised says nothing about a battleship.
        rate = _UPKEEP.get(domain, _UPKEEP["land"])
        if domain == "land":
            rate *= factor(conn, world_id, nation_id, "mobilisation", "land_upkeep")
        upkeep += rate * int(entry["n"])

    # **And the deterrent.** Charged beside the formations rather than as a separate line,
    # because it is the same kind of thing -- what standing still costs -- and one account
    # of upkeep is the only way the number a player is shown and the number the treasury
    # pays stay the same.
    upkeep += arsenal_upkeep(conn, world_id, nation_id)

    # **And the promises.** Same account as the formations and the deterrent, for the same
    # reason: what standing still costs, in one number a player and the treasury agree on.
    upkeep += pact_upkeep(conn, world_id, nation_id)

    return round(revenue, 2), round(upkeep, 2)


def collect_revenue(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str,
    days: int = 30,
) -> BudgetReport:
    """Run the whole world's finances for the turn, as one engine-authored batch.

    ``days`` is how long the turn covered. Money is a *flow*, so a year collects twelve
    months of revenue and pays twelve of upkeep; the drifts below are *rates* and
    compound instead. See :mod:`atlas.engine.pacing`.
    """
    span = span_of(days)
    report = BudgetReport()
    nations = conn.execute(
        "SELECT id, name, treasury, manpower, population,"
        "       economy, industry, stability, legitimacy FROM ("
        "  SELECT n.id, n.name, n.treasury, n.manpower,"
        "         n.economy, n.industry, n.stability, n.legitimacy,"
        "         COALESCE((SELECT SUM(r.population) FROM region r"
        "                    WHERE r.world_id = n.world_id"
        "                      AND COALESCE(r.controller_nation_id, r.owner_nation_id) = n.id"
        "                      AND r.kind <> 'ocean'), 0) AS population"
        "    FROM nation n WHERE n.world_id = ? AND n.is_alive = 1"
        ") ORDER BY id",
        (world_id,),
    ).fetchall()
    if not nations:
        return report

    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.ECONOMIC_REFORM, origin=EventOrigin.ENGINE,
        text="The quarter's revenues are collected and the establishments are paid.",
        visibility=Visibility.PUBLIC,
    )

    diffs: list[Diff] = []
    for nation in nations:
        revenue, upkeep = nation_budget(conn, world_id, nation["id"])
        drag = labour_drag(conn, str(nation["id"]))
        if drag > 0:
            report.on_strike.append(
                f"{nation['name']} lost {drag * 100:.0f}% of its revenue to labour unrest")
        treasury = float(nation["treasury"])
        net = over(revenue - upkeep, span)
        # Decided once, here, because `_at_peace` below reads the treasury as it stood
        # *before* this batch: a nation that falls into arrears this turn still had money
        # when the healing term asked, and was healed in the same batch as its charge.
        fell_into_arrears = treasury + net < 0

        if fell_into_arrears:
            # Pay what there is. A government in arrears is a legible failure; a negative
            # treasury is a number nobody can act on.
            diffs.append(
                Diff("nation", nation["id"], "treasury", DiffOp.SET, 0.0,
                     reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
            )
            diffs.append(
                Diff("nation", nation["id"], "stability", DiffOp.ADJUST,
                     # **Charged against what there is**, for the reason the breach path
                     # learned and this one did not: ECON-003 holds stability inside 0-100
                     # and names no diff, so it condemns the *whole batch* -- and this
                     # batch is every nation's revenue and upkeep for the turn. Reproduced:
                     # one bankrupt minor a single point short of the charge, and **not one
                     # treasury in the world moved**. A government at zero stability cannot
                     # be shaken further by missing one more payroll; it is already there.
                     absorbable(conn, "nation", str(nation["id"]), "stability",
                                -round(over(_ARREARS_COST, span), 2)),
                     reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
            )
            report.in_arrears.append(str(nation["name"]))
        elif net:
            diffs.append(
                Diff("nation", nation["id"], "treasury", DiffOp.ADJUST, round(net, 2),
                     reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
            )

        # Manpower recovers toward what the ground can sustain, and never past it.
        #
        # **A share of the gap, not of the ceiling.** The rate was applied to the ceiling,
        # which is not what "recovers toward" means and is not what the two drifts below
        # it do -- economy and legitimacy both close a fraction of the *distance* to their
        # target. Against a ceiling the recovery is flat all the way up: a nation at 1% of
        # its manpower and one at 99% both regained 4% of the ceiling a month, so the last
        # 4% of the pool refilled as fast as the first. Twelve times too fast at the top,
        # and the clamp on the line below hid it -- the `min` looked like a ceiling guard
        # and was in fact the only thing keeping the number finite.
        # **Called, not restated.** `wareffort.manpower_ceiling` says of itself that it is
        # "identical to the ceiling `budget` refills toward -- one copy, so the two cannot
        # drift", and this line was a second copy of the same arithmetic that happened to
        # agree. Found by a coverage pass: the function kept to prevent drift was not used
        # by the layer it was kept for.
        # **And what this country is built to raise.** Both halves move together: a nation
        # on mass mobilisation has a larger pool *and* refills it faster, and a professional
        # one has a smaller pool it refills slowly -- which is what makes losses it cannot
        # replace the price of the doctrine rather than an inconvenience. Exactly 1.0 for a
        # nation with no doctrine, so this reads as it always did.
        # **Doctrine and footing multiply together**, because they answer different
        # questions: *how* this country raises an army, and *how hard it is digging right
        # now*. A nation built on mass mobilisation that is also at a total-war footing is
        # doing both, and should reach further than either alone.
        ceiling = manpower_ceiling(
            float(nation["population"]),
            factor(conn, world_id, str(nation["id"]), "mobilisation", "manpower_ceiling")
            * ceiling_factor(conn, world_id, str(nation["id"])))
        manpower = float(nation["manpower"])
        if manpower < ceiling:
            recovery = (ceiling - manpower) * compounded(
                _MANPOWER_RECOVERY
                * factor(conn, world_id, str(nation["id"]), "mobilisation",
                         "manpower_recovery")
                * recovery_factor(conn, world_id, str(nation["id"])),
                span)
            if recovery >= _NOTICEABLE * span:
                diffs.append(
                    Diff("nation", nation["id"], "manpower", DiffOp.ADJUST,
                         round(recovery, 2), reason_event_id=event_id,
                         visibility_scope=Visibility.PUBLIC)
                )

        # The economy climbs toward what the industrial base can support. This is what
        # turns a factory from a one-line bump into years of quiet growth, and what a
        # peaceful builder actually watches move.
        economy = float(nation["economy"])
        target = supportable_economy(
            float(nation["industry"]) * _ECONOMY_PER_INDUSTRY
            + ground_value(conn, world_id, str(nation["id"])) * _ECONOMY_FROM_DEVELOPMENT
        )
        drift = round((target - economy) * compounded(_ECONOMY_GROWTH_RATE, span), 2)
        if abs(drift) >= _NOTICEABLE * span:
            diffs.append(
                Diff("nation", nation["id"], "economy", DiffOp.ADJUST, drift,
                     reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
            )

        # **Peace puts something back.** Only upward, only toward the resting point, and
        # only for a country that is genuinely quiet -- see the constants above.
        stability = float(nation["stability"])
        # **Not for a nation that just missed its payroll**, which the arrears charge above
        # has just told something the healing term would quietly undo.
        if (stability < _STABILITY_RESTING and not fell_into_arrears
                and _at_peace(conn, world_id, str(nation["id"]))):
            healing = round(
                (_STABILITY_RESTING - stability)
                * compounded(_STABILITY_RECOVERY, span), 2)
            if healing >= _NOTICEABLE * span:
                diffs.append(
                    Diff("nation", nation["id"], "stability", DiffOp.ADJUST, healing,
                         reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
                )

        # Legitimacy follows how stable the country is kept, slowly.
        legitimacy = float(nation["legitimacy"])
        # **And a country whose whole policy is staying out of other people's wars is
        # easier to govern while it does.** The one lever armed neutrality has that is not
        # about somebody else's promise: an unentangled public settles toward its
        # government faster. Only while the drift is upward -- a doctrine cannot make a
        # collapse more comfortable -- and exactly 1.0 for a nation with no doctrine.
        drift_rate = _LEGITIMACY_DRIFT_RATE
        if float(nation["stability"]) > legitimacy:
            drift_rate *= factor(conn, world_id, str(nation["id"]), "alignment",
                                 "peacetime_legitimacy")
        leg_drift = round(
            (float(nation["stability"]) - legitimacy) * compounded(drift_rate, span),
            2,
        )
        if abs(leg_drift) >= _NOTICEABLE * span:
            diffs.append(
                Diff("nation", nation["id"], "legitimacy", DiffOp.ADJUST, leg_drift,
                     reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
            )
        report.collected += 1

    if not diffs:
        return report
    outcome = apply_batch(
        conn,
        DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                  proposer_model=None, diffs=tuple(diffs)),
    )
    if not outcome.applied:
        report.problems.append(f"the budget could not be applied: {outcome.reason}")
    return report


@dataclass(slots=True)
class ErosionReport:
    #: Provinces whose development fell this turn, and by how much in total.
    provinces: int = 0
    lost: float = 0.0
    headlines: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def erode_development(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str, days: int = 30
) -> ErosionReport:
    """Ground that is fought over, held down or shut out of the world gets poorer.

    Development had exactly one direction before this: a public work raised it and nothing
    ever lowered it, so a province was a ratchet and "develop your land" was a strategy
    with no downside to weigh against. Three things push the other way now, and all three
    are read from state something else already computes rather than from a new column:

    * **Occupation and unrest**, from :func:`~atlas.engine.unrest.held_foreign_provinces`
      -- the same per-province residual that decides what holding foreign ground costs in
      stability. It is the residual *after* garrison relief, so a province with troops on
      it does not decay: garrisoning restive ground protects its value as well as your
      own, which is the correct incentive and needed no second mechanic to express.
    * **Sanctions**, from the ``sanction`` table the Security Council writes.
    * **Bombing**, which is not here: :mod:`atlas.engine.air` writes those diffs at the
      moment the raid lands, and :mod:`atlas.engine.nuclear` at the moment the warhead
      does. This function is the slow half.

    Engine-authored, one event, ordinary diffs -- so a rewind puts back exactly the ground
    value the turn started with.
    """
    from .organisation import sanction_drag
    from .unrest import held_foreign_provinces

    span = span_of(days)
    report = ErosionReport()

    losses: dict[str, float] = {}
    holders = [
        str(row["id"])
        for row in conn.execute(
            "SELECT id FROM nation WHERE world_id = ? AND is_alive = 1 ORDER BY id",
            (world_id,),
        )
    ]
    for holder in holders:
        for province in held_foreign_provinces(conn, world_id, holder):
            residual = float(province.get("residual") or 0.0)
            if residual > 0:
                region_id = str(province["region_id"])
                losses[region_id] = losses.get(region_id, 0.0) + residual * _UNREST_EROSION

        severity = sanction_drag(conn, world_id, holder, turn=turn, date=date)
        if severity > 0:
            for row in conn.execute(
                "SELECT id FROM region WHERE world_id = ? AND kind <> 'ocean'"
                "   AND COALESCE(controller_nation_id, owner_nation_id) = ?"
                " ORDER BY id",
                (world_id, holder),
            ):
                region_id = str(row["id"])
                losses[region_id] = (
                    losses.get(region_id, 0.0) + severity * _SANCTION_EROSION
                )

    if not losses:
        return report

    diffs: list[Diff] = []
    event_id: int | None = None
    for region_id in sorted(losses):
        row = conn.execute(
            "SELECT name, development FROM region WHERE id = ?", (region_id,)
        ).fetchone()
        if row is None:
            continue
        current = float(row["development"])
        # SET of a computed target rather than ADJUST of a delta, for the reason the
        # sanction drag learned the hard way: an adjust of a rounded delta walks through
        # a floor by floating-point dust after enough turns.
        target = max(_DEVELOPMENT_FLOOR, current - over(losses[region_id], span))
        if current - target < 0.005:
            continue
        if event_id is None:
            event_id = record_event(
                conn, world_id=world_id, turn=turn, date=date,
                kind=EventKind.ECONOMIC_REFORM, origin=EventOrigin.ENGINE,
                text="Ground held down, fought over or shut out of trade loses its worth.",
                visibility=Visibility.PUBLIC,
            )
        report.provinces += 1
        report.lost += current - target
        diffs.append(
            Diff("region", region_id, "development", DiffOp.SET, round(target, 2),
                 reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
        )

    if not diffs:
        return report
    outcome = apply_batch(
        conn,
        DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                  proposer_model=None, diffs=tuple(diffs)),
    )
    if outcome.applied:
        report.headlines.append(
            f"{report.provinces} provinces are worth less than they were "
            f"({report.lost:.1f} of development lost)."
        )
    else:
        report.problems.append(f"development could not be eroded: {outcome.reason}")
    return report
