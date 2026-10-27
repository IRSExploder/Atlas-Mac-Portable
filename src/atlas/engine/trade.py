"""Strategic goods, the trade that moves them, and the three ways it stops.

Everything a country needed was one abstract number. This gives it three things it can run
out of, and three ways somebody else can make that happen.

**A shortage does not make a country poorer, it makes it slower to build.** The shortfall
scales the month each yard works in :func:`atlas.engine.production.advance_construction` --
an existing door and not a new mechanic, and the failure mode stays legible: a blockaded
country still collects its taxes and its battleships come off the slipway late. (Until
2026-09-18 it also cut :func:`~atlas.engine.production.industrial_capacity`, the count of
concurrent projects; see there for why it no longer does.) Resources feeding ``economy`` was the obvious
alternative and was rejected because economy has four inputs already and a fifth would make
the number mean nothing at all.

**The three disruption levers are three existing systems being asked a question they could
already answer**, which is the strongest evidence this design fits the engine rather than
being bolted to it:

* **sanction** -- the organisation layer already writes `sanction` rows;
* **blockade** -- a cargo crosses water exactly as supply does, and
  :func:`atlas.engine.naval.crossable` decides whether it gets through. The same function
  that decides whether a landing happens and whether an army eats;
* **war** -- belligerents do not trade.

None of the three needed a line of code saying "embargo".

**Stockpiles are the reason a blockade is a campaign rather than a switch.** A nation stores
several months of its own production, so being cut off is a mounting problem its owner has
time to answer -- by opening another route, breaking the blockade, or building less.

**And since schema 46, a cargo is paid for.** For a long while it was not: a seller shipped
oil every month for the life of a treaty and no treasury in the world moved by a point, so
a blockade could starve a country of steel and could not cost it a penny. What a shipment
goes for is settled by :mod:`atlas.engine.pricing` -- from both sides' supply, from who else
the buyer could go to, and from the leverage each has over the other -- and the money moves
in the same batch as the goods, so a rewind cannot restore one without the other. A route
that has never been priced still barters, which is what every route in a world written
before this did, and what every route does when the layer is switched off.

**And since the refusal prototype, a seller may decline.** For as long as there was a price
there was no way to say no to one: this function shipped what the seller had at whatever the
terms were, and 10-15% of measured route-turns sat pinned at the price floor shipping a real
cargo for the least the engine would name. :mod:`atlas.engine.refusal` decides, from the
standing terms and the seller's own position, whether the cargo goes -- behind its own flag,
and off by default like the pricing it sits on.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..state.apply import apply_batch, record_event
from ..state.diff import Diff, DiffBatch
from ..state.models import DiffOp, EventKind, EventOrigin, Role, Visibility
from .pacing import REFERENCE_DAYS, compounded, days_between, over, span_of


def _called(conn: sqlite3.Connection, world_id: str, nation_id: str) -> str:
    """A nation's name for a line a player reads; its id only if it has none."""
    row = conn.execute("SELECT name FROM nation WHERE world_id = ? AND id = ?",
                       (world_id, nation_id)).fetchone()
    return str(row[0]) if row else str(nation_id)


if TYPE_CHECKING:  # `consent` and `pricing` both import this module at runtime.
    from .consent import Band
    from .pricing import Terms

__all__ = [
    "GOODS",
    "IMPORT_BUDGET_SHARE",
    "REPRICE_EVERY_MONTHS",
    "SETTLEMENT_UNITS",
    "TradeReport",
    "assess_trade",
    "consent_columns_present",
    "demand_of",
    "is_unreachable",
    "next_repricing",
    "pricing_columns_present",
    "route_is_open",
    "sellable",
    "settled_price",
    "shortfall_of",
    "supply_ratio",
    "worth_selling",
]

#: The three goods. The schema's CHECK constraints name the same three, which is what
#: actually holds the set closed.
GOODS: tuple[str, ...] = ("oil", "steel", "rare")

#: How much of each good a point of industry burns per month. One coefficient rather than
#: three, because a good a country needs less of is expressed by giving it *more* in
#: :mod:`atlas.scenarios.resources` -- one table to argue with instead of two.
_DEMAND_PER_INDUSTRY = 0.11

#: The least a nation ever demands, so a country with no industry at all still needs
#: something and can still be squeezed.
_MIN_DEMAND = 0.5

#: What a nation with none of a good it needs can still build, as a share of its normal
#: pace: each yard works this fraction of every month.
#: Not zero: an army with no fuel still has an army, and a production system that can be
#: driven to a standstill by one severed route is a system where one lever decides the game.
_STARVED_CAPACITY = 0.35

#: Most a stockpile may grow to, in months of that nation's own demand. Without a ceiling a
#: peacetime surplus accumulates forever and the blockade lever stops meaning anything by
#: about year five.
_MAX_MONTHS_STORED = 12.0

#: Most of its treasury a country will hand over for imports, **per month**.
#:
#: **A country that spent everything on oil could not pay its army**, and the first version
#: let it: the affordability cap was the whole treasury, so a desperate buyer facing a
#: monopolist emptied itself in one turn and went into arrears on upkeep the same turn --
#: two separate failures reported as one bankruptcy, and the player could not tell which had
#: caused which. Half is the discipline: a government under real pressure will spend down
#: most of its reserves for the fuel its factories need, and no government spends the
#: payroll. The unaffordable remainder is simply not shipped, which is the honest outcome --
#: the seller keeps its cargo.
#:
#: **Per month, not per call, and that is the fix for a turn-length bug.** The allowance was
#: a flat share of the treasury however much time the call covered, while the cargo it was
#: weighed against was already ``over(volume, span)``. A weekly turn therefore bought a
#: quarter-month of cargo out of a full month's allowance and the cap could not bind at all,
#: 4.3x looser than the monthly game it was calibrated on.
#:
#: **A rate, not a flow, and :mod:`atlas.engine.pacing` is explicit about the difference.**
#: This is "hand over this fraction of what you are holding", which is the same shape as
#: economy growth and manpower recovery: it **compounds**, and
#: :func:`~atlas.engine.pacing.compounded` is the engine's answer for it. Multiplying by
#: ``span`` instead looks right and is not -- at a 360-day call it grants six times a
#: treasury, and the first draft of this fix duly settled a 40-point purse at *minus 200*.
#: Compounding is bounded below 1.0 by construction, so a buyer can spend down towards its
#: reserves and never past them however long the call.
#:
#: Both readings are the identity at the 30-day turn every recorded figure on this branch
#: was taken at, so nothing measured moves.
IMPORT_BUDGET_SHARE = 0.5

#: How often a standing route's terms are looked at again, **in months of world time**, and
#: how the routes are spread across those occasions so they do not all renegotiate at once.
#: Terms that could only ever be set at signature would be a world where the 1936 oil price
#: still stood in 1945; terms recomputed every turn are 648 sea walks a turn for a number
#: that has not moved. A route is reconsidered on the turns where
#: ``(turn + stagger) % period == 0``, and the stagger is derived from the route id so it is
#: stable across a rewind and a replay.
#:
#: **Months, because the constant used to be turns and a turn is not a duration.** Six turns
#: is six months on the monthly game this was calibrated against, six *weeks* on a weekly
#: one and **six years** on a yearly one -- so the yearly game was exactly the world the
#: paragraph above says this exists to prevent, with a price settled in 1936 standing
#: untouched until 1942. The period is now derived per call from how long the turn actually
#: is; see :func:`_reprice_period`.
REPRICE_EVERY_MONTHS = 6.0

#: How close to :data:`atlas.engine.pricing.EMBARGO_PRICE` a stored price counts as being
#: at it. The column holds a rounded number and the threshold is a product of floats; a
#: bare `>=` between the two shipped a cargo that had been priced out.
_PRICE_EPSILON = 1e-6

#: The grain a payment is settled in: every bill is a whole number of ``1 /
#: SETTLEMENT_UNITS``, and so is every treasury adjustment the settlement writes.
#:
#: **This exists because trade was creating money, and it is the only shape of fix that
#: does not.** A bill used to be applied to the two sides *separately* and each nation's
#: netted position rounded to 2dp on its own, so the buyer's cent and the seller's cent were
#: rounded by different amounts and the batch stopped adding up to nothing. Worse, a nation
#: whose whole netted position came to under a cent was dropped from the settlement
#: outright, and the cargo it had just received became free. Neither is visible on one route
#: between two countries, and both scale with the number of routes: measured over 36 monthly
#: turns of the modern-day map, **+10.66 was created out of nothing against 423.74 of cargo
#: actually traded** -- 2.5% of the turnover, every settlement leaking, all of it one way.
#:
#: The fix is to compute each payment **once**, as an integer, and hand the identical
#: integer to both sides. Integers cancel exactly; two independently rounded floats do not.
#:
#: **Why a power of two, and not a hundredth or a thousandth.** The diffs still have to
#: carry floats, so the grain has to survive the trip back: ``units / 2**20`` is exact in
#: binary floating point, every settled amount is therefore an exact multiple of the same
#: power of two, and their sum is exact too -- ``sum()`` of the batch is ``0.0`` on the
#: nose, not ``1e-17``. A decimal grain would be exact in the integers and inexact the
#: moment it was divided, which is the same class of bug one layer further down.
#:
#: 2**-20 is about a millionth, which is four orders of magnitude finer than the cent the
#: interface displays and two orders finer than the smallest bill the engine can produce
#: (``_shipment`` floors a cargo at 0.001 and ``pricing.FLOOR_PRICE`` at 0.027). It leaves
#: room for a batch of eight billion before a partial sum could stop being exact.
#:
#: **Rounding here is not presentation rounding.** Nothing is rounded *for display* and
#: nothing is rounded *after* the fact: this is the unit the payment is denominated in
#: before either party is touched, so both parties are touched by the same number. What used
#: to happen -- settle a fractional amount, then round each nation's copy of it separately
#: -- is exactly what this replaces.
SETTLEMENT_UNITS = 1 << 20


@dataclass(slots=True)
class TradeReport:
    assessed: int = 0
    #: Routes that failed to deliver this turn, and why.
    interrupted: list[str] = field(default_factory=list)
    #: Nations that could not meet their industry's demand this turn.
    short: list[str] = field(default_factory=list)
    #: Routes whose terms the engine settled or re-settled this turn. Kept apart from
    #: `interrupted` because a renegotiation is a thing that *happened*, not a failure.
    repriced: list[str] = field(default_factory=list)
    #: Routes whose *seller* declined this turn's shipment. Its own list, and not folded
    #: into `interrupted`, for the reason the priced-out line is its own sentence: a lane
    #: nobody is stopping and a lane a fleet is stopping are different facts about the
    #: world, and only one of them can be fixed by a navy. Empty unless
    #: :mod:`atlas.engine.refusal` is switched on.
    withdrawn: list[str] = field(default_factory=list)
    #: Routes whose *buyer* stopped taking the cargo this turn, because the standing price
    #: is above what it will pay. The other half of `withdrawn`, kept as its own list for
    #: the same reason `withdrawn` is kept apart from `interrupted`: a lane nobody is
    #: stopping, a lane a fleet is stopping, a seller that walked away and a buyer that
    #: walked away are four different facts about the world, and only one of them can be
    #: fixed by a navy. Empty unless :mod:`atlas.engine.consent` is switched on.
    refused: list[str] = field(default_factory=list)
    #: Routes one government has deliberately stopped selling on, and what refusing to
    #: ship is costing it. **Its own list, beside the four above and not folded into any of
    #: them, and that separation is the entire argument of `atlas.engine.embargo`**: a
    #: seller that walked away because the price stopped working and a seller that is
    #: accepting a loss in order to coerce somebody are not the same fact, they do not have
    #: the same remedy, and a player told only that "the oil stopped" cannot tell a market
    #: from an ultimatum. Empty unless :mod:`atlas.engine.embargo` is switched on.
    embargoed: list[str] = field(default_factory=list)
    #: Routes that had stopped and are moving again, because the terms came back inside
    #: what both sides will accept. **Its own list because a resumption is news** -- a
    #: player who was told the oil stopped has to be told when it starts, or the world
    #: only ever reports getting worse.
    resumed: list[str] = field(default_factory=list)
    #: What each nation paid out or took in for cargo this turn, netted. Empty when the
    #: world has no priced routes -- which is every world before schema 46 and every world
    #: played with `atlas.engine.pricing` switched off.
    #:
    #: **These sum to exactly zero**, and a reader may rely on that: trade moves money
    #: between countries and never makes or burns any. See :data:`SETTLEMENT_UNITS` for what
    #: it took to make that true and what it was costing while it was not. The figures are
    #: the settled amounts and not display figures -- round them where they are shown, never
    #: before.
    settled: dict[str, float] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def demand_of(conn: sqlite3.Connection, nation_id: str) -> float:
    """How much of each good this nation's industry burns per month."""
    row = conn.execute(
        "SELECT industry, economy FROM nation WHERE id = ?", (nation_id,)
    ).fetchone()
    if row is None:
        return 0.0
    industry = float(row["industry"] or 0.0) or float(row["economy"] or 0.0) * 0.6
    return max(_MIN_DEMAND, industry * _DEMAND_PER_INDUSTRY)


def _stock(conn: sqlite3.Connection, world_id: str, nation_id: str) -> dict[str, float]:
    try:
        rows = conn.execute(
            "SELECT good, produced, stockpile FROM resource"
            " WHERE world_id = ? AND nation_id = ?", (world_id, nation_id),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {str(r["good"]): float(r["stockpile"]) for r in rows}


def _still_standing_at_world_clock() -> str:
    """`organisation.still_standing`, rendered against the joined `world` row.

    Imported lazily and through the shared renderer rather than copied: the whole reason
    the trade layer and the economic layer disagreed about which measures had lapsed is
    that this predicate existed in one place and was written out longhand in another.
    """
    from .organisation import still_standing

    return still_standing("w.world_date", "w.turn_number")


def route_is_open(
    conn: sqlite3.Connection, world_id: str, seller: str, buyer: str
) -> tuple[bool, str]:
    """Whether goods can actually move between two nations, and why not if they cannot.

    Three tests, and each is a question some other subsystem already knew the answer to.
    Ordered cheapest first: war and sanctions are single queries, the sea walk is not.
    """
    at_war = conn.execute(
        "SELECT at_war FROM relation WHERE world_id = ? AND nation_a = ? AND nation_b = ?",
        (world_id, seller, buyer),
    ).fetchone()
    if at_war is not None and int(at_war["at_war"]):
        return False, "at war"

    # **A measure that has lapsed is not a measure.** Sanction rows are deliberately never
    # deleted -- `organisation.sanction_drag` says why: a row that expires without anybody
    # writing anything is a row a rewind never has to put back. That makes "a row exists"
    # the wrong question, and it was the one asked here: since there is no lifting
    # mechanic anywhere in the engine, one resolution that passed on turn 3 shut this pair
    # of countries out of each other's markets for the rest of the campaign, while the
    # economic drag it was actually voted for stopped after eight months.
    #
    # The clock is read off the `world` row in the same statement rather than passed in,
    # because `route_is_open` is reached from `pricing.price_route` through frames that
    # carry no date -- see `organisation.still_standing`. It costs no extra round trip,
    # which matters: this query runs some thousands of times a run.
    #
    # **That clock lags by one turn, deliberately.** `jump` writes `world.world_date` once,
    # at the end (turn_engine.py:1853), so a read taken mid-turn sees the date the turn
    # started -- and since per-round dating the rounds walk forward past it. A measure due
    # to lapse during turn T therefore goes on closing the lane for the rest of T and opens
    # at T+1. That is one turn late rather than the previous forever, and it errs towards
    # the measure standing, which is the safe direction for a lane nobody can reopen by
    # hand. Tightening it means giving `route_is_open` an optional date and passing
    # `assess_trade`'s `round_date`; the cost is that trade and pricing would then answer
    # differently about a measure lapsing this turn, so it is left as one answer.
    try:
        sanctioned = conn.execute(
            f"""
            SELECT 1 FROM sanction s
              JOIN organisation_member m ON m.organisation_id = s.organisation_id
              JOIN world w ON w.id = s.world_id
             WHERE s.world_id = ? AND s.target_nation_id IN (?, ?)
               AND m.nation_id IN (?, ?)
               AND m.nation_id <> s.target_nation_id
               AND {_still_standing_at_world_clock()}
             LIMIT 1
            """,  # noqa: S608 - the fragment is a literal, the values are bound
            (world_id, seller, buyer, seller, buyer),
        ).fetchone()
    except sqlite3.OperationalError:
        sanctioned = None
    if sanctioned is not None:
        return False, "under sanction"

    return _lane_open(conn, world_id, seller, buyer)


def _lane_open(
    conn: sqlite3.Connection, world_id: str, seller: str, buyer: str
) -> tuple[bool, str]:
    """Whether a cargo can physically get from one country to the other.

    **This was first written to reuse `logistics.supply_map`, and that was wrong in a way
    worth recording.** Military reach and commercial reach are not the same thing: supply
    is deliberately bounded at three hops, because an army is fed from depots behind it, and
    a merchant ship is not. Borrowing the supply walk made *every one of the 648 seeded
    routes* read as cut on turn one -- Saudi oil could not reach Japan because Japan is more
    than three provinces from Riyadh, which is true and has nothing to do with trade.

    So the walk here has **no distance limit** and crosses a *neutral's* land freely --
    commerce does not need to control the ground it passes over. What it cannot do is cross
    water a hostile fleet commands or that somebody else has taken, and that test is
    :func:`atlas.engine.naval.crossable`, exactly as in the supply layer. At peace, across
    water nobody has claimed, every sea passes and the world trades.

    **The one country whose land it does not cross is one at war with an end of the lane.**
    The sentence above was written about neutrals and is right about them; the code applied
    it to belligerents too, so a cargo rolled across the territory of a nation shooting at
    it -- Germany to Persia through a hostile USSR. The test is the same war table the sea
    half already reads, and from both ends for the same reason: a lane has two owners, and
    either one's enemies can sit on the road. Ground the enemy merely *holds* counts,
    because ``COALESCE(controller, owner)`` is who is standing on it.

    **This is worth almost nothing in play, and that is worth knowing.** It was proposed as
    the land half of the embargo-does-not-bite finding and it is not. Same walk, three land
    policies, world at war with one power, third-party lanes only:

    =============================  ===============  ===========
    land policy                    gathering-storm  modern-day
    =============================  ===============  ===========
    free (before this)             134 of 134 open  365 of 373
    refuse a belligerent's ground  134 of 134       365 of 373
    only the two ends' own ground   80 of 134       206 of 373
    =============================  ===============  ===========

    A belligerent's ground is never a chokepoint on either map: Eurasia-Africa is one dense
    landmass, so denying one country's roads closes nothing, because there is always a
    neutral's road beside it. Blockade is toothless against a continental power because
    this walk has **no distance limit and asks nobody's permission** -- not because it
    crosses enemies. The lever that would change that is transit consent or an overland
    cost, and the bottom row is what such a rule is worth at its strictest.
    """
    from .air import _neighbours
    from .naval import crossable

    buyer_ground = {
        str(row["id"])
        for row in conn.execute(
            "SELECT id FROM region WHERE world_id = ? AND kind <> 'ocean'"
            "   AND COALESCE(controller_nation_id, owner_nation_id) = ?",
            (world_id, buyer),
        )
    }
    if not buyer_ground:
        return False, "the buyer holds no ground"

    # **A blockade is not one-sided, and this asked only the seller's half.** A power at
    # war with the *buyer*, with a battle fleet astride the only lane into it, did not
    # close that lane -- because the seller was at peace with it and the seller was the
    # only side consulted. Blockading an enemy's imports is most of what a navy is for,
    # and it worked on exactly the cargoes the enemy happened to be exporting.
    enemies = {
        str(row["other"])
        for row in conn.execute(
            "SELECT nation_b AS other FROM relation"
            " WHERE world_id = ? AND nation_a IN (?, ?) AND at_war = 1",
            (world_id, seller, buyer),
        )
    }
    graph = _neighbours(conn, world_id)
    oceans = {
        str(row["id"])
        for row in conn.execute(
            "SELECT id FROM region WHERE world_id = ? AND kind = 'ocean'", (world_id,)
        )
    }
    origins = [
        str(row["id"])
        for row in conn.execute(
            "SELECT id FROM region WHERE world_id = ? AND kind <> 'ocean'"
            "   AND COALESCE(controller_nation_id, owner_nation_id) = ?",
            (world_id, seller),
        )
    ]
    if not origins:
        return False, "the seller holds no ground"

    # Land a belligerent is standing on. One query rather than a lookup per step, because
    # the walk visits most of a continent and the map has not moved since the world was
    # seeded. Empty at peace, which is when this costs nothing.
    shut: set[str] = set()
    if enemies:
        marks = ",".join("?" * len(enemies))
        shut = {
            str(row["id"])
            for row in conn.execute(
                f"SELECT id FROM region WHERE world_id = ? AND kind <> 'ocean'"
                f"   AND COALESCE(controller_nation_id, owner_nation_id) IN ({marks})",
                (world_id, *sorted(enemies)),
            )
        }

    seen = set(origins)
    frontier = list(origins)
    passable: dict[str, bool] = {}
    while frontier:
        nxt: list[str] = []
        for region in frontier:
            if region in buyer_ground:
                return True, ""
            for neighbour in graph.get(region, ()):
                if neighbour in seen:
                    continue
                if neighbour in oceans:
                    if neighbour not in passable:
                        # Either end may be the one that gets the cargo through: a lane
                        # has two owners, and a convoy escorted by whichever of them has
                        # the stronger fleet here is a convoy that arrives. `or` rather
                        # than `and` for the same reason -- requiring both would shut a
                        # buyer out of water it holds itself, which is not a blockade,
                        # it is a country refusing to unload its own docks.
                        passable[neighbour] = crossable(
                            conn, seller, enemies, neighbour
                        ) or crossable(conn, buyer, enemies, neighbour)
                    if not passable[neighbour]:
                        continue
                elif neighbour in shut:
                    # A belligerent's road. Not a distance and not a price -- a country at
                    # war with one end of this lane does not pass its cargo, for the same
                    # reason its navy does not escort it.
                    continue
                seen.add(neighbour)
                nxt.append(neighbour)
        frontier = nxt
    return False, "the lane is cut"



def pricing_columns_present(conn: sqlite3.Connection) -> bool:
    """Whether this world's schema carries the pricing columns at all.

    **The pre-migration guarantee, asked of the database rather than assumed.** A world
    written before schema 46 has no ``trade_route.unit_price``, so there is nothing to read,
    nothing to write and no money to move -- it barters, exactly as every world did before
    this existed. That is a different thing from the flag being down, and both have to be
    true for the layer to be silent, so both are checked.
    """
    return "unit_price" in {
        str(row["name"]) for row in conn.execute("PRAGMA table_info(trade_route)")
    }


def consent_columns_present(conn: sqlite3.Connection) -> bool:
    """Whether this world's schema can *record* a commercial suspension.

    The schema-47 twin of :func:`pricing_columns_present`, and separate from it because the
    two migrations are separate: a world at 46 has prices and nowhere to write down that a
    buyer refused one. Such a world still gets the decision -- the cargo stops, the report
    says so -- it simply cannot carry the fact across to the next turn, which is the honest
    behaviour for a save that predates the column and is better than refusing to run.
    """
    return "suspended_turn" in {
        str(row["name"]) for row in conn.execute("PRAGMA table_info(trade_route)")
    }


def _still_dwelling(  # noqa: PLR0913 - a reopening is a fact about all of this
    conn: sqlite3.Connection, world_id: str, seller: str, buyer: str, good: str, route,
    *, date: str, dwelling: bool, unit_price: float,
    lanes, alternatives_cache: dict,
) -> tuple[bool, str]:
    """Whether a suspended lane whose terms now overlap must nonetheless stay stopped.

    Returns ``(held, why)``, and ``why`` is a sentence either way -- the reason it is still
    stopped, or what it took to reopen it. **The resumption says what it cost**, because a
    hysteresis nobody can read is a second invisible mechanism, which is the defect this was
    added to fix rather than a second instance of it.

    Two clauses, both durations. See :data:`atlas.engine.consent.RECOVERY_MARGIN_DAYS` and
    :data:`atlas.engine.consent.DWELL_DAYS` for why each is sized the way it is and why
    neither is a count of turns.
    """
    from . import consent  # local: `consent` imports this module, and a cycle helps nobody

    # **The buyer's half only, and the restriction is the honest scope.** Both clauses are
    # derived from `consent.approach`, which solves the crossing of the *buyer's* ceiling
    # against the price; a seller's withdrawal is `refusal.seller_declines`, a sign test on
    # a different quantity that this diagnosis did not measure. Holding a withdrawn lane
    # shut against a margin computed from the buyer's regard would be applying a number to a
    # decision it says nothing about -- and it showed: it kept a lane closed for a turn after
    # the seller's own position had turned around, which
    # `test_the_route_survives_a_refusal_and_ships_again_when_the_terms_do` is there to
    # forbid. Whether the seller's side wants the same treatment is a real question and it
    # wants its own measurement first.
    #
    # **That measurement has now been taken, and the answer is no.** Classifying all 212
    # in-play suspensions across the play battery -- `scratchpad/CLIFF_DIAGNOSIS.md` -- 95%
    # are seller-side, so this is the majority case going unhysteresised and it was worth
    # checking. It does not need it: of 70 close/reopen cycles the **median lane stayed shut
    # 180 days**, exactly one repricing interval, and only **one cycle in 70** was shorter
    # than `DWELL_DAYS`. A dwell here would bind on 1.4% of cycles and change nothing about
    # the rest, because the stagger already imposes a floor on the suspension length three
    # times longer than the dwell would. The failure `RECOVERY_MARGIN_DAYS` was sized against
    # was 31 flips in 31 turns; the seller's side is not within two orders of magnitude of
    # it, and damping a signal that is not chattering would only hide it.
    if str(route["suspended_by"] or "") != "buyer":
        return False, "the terms are back inside what both will accept"

    served = ""
    if dwelling:
        stopped_on = str(route["suspended_date"] or "")
        if stopped_on:
            waited = days_between(stopped_on, date)
            # `None` is an unparseable pair of dates, not a dwell of zero. A clock that
            # cannot be read does not get to hold a lane shut.
            if waited is not None:
                if waited < consent.DWELL_DAYS:
                    return True, (
                        f"a suspension stands {consent.DWELL_DAYS:.0f} days before it may "
                        f"lift and this one is {waited:.0f} days old")
                served = f"{waited:.0f} days"

    approach = consent.approach(
        conn, world_id, seller, buyer, good, standing_price=unit_price,
        lanes=lanes, alternatives_cache=alternatives_cache,
    )
    # **No crossing on the buyer's ledger means nothing to be clear of.** The lane did not
    # stop because the reader's opinion fell -- it stopped because the price rose -- so a
    # margin denominated in the reader's regard has nothing to hold it shut with, and the
    # dwell above is the only clause that applies. Inventing a margin here would keep a lane
    # closed against a number that had no part in closing it.
    if approach is None or not approach.on_the_regard_axis:
        return False, (f"the terms are back inside what both will accept{_after(served)}")
    margin = consent.RECOVERY_MARGIN_DAYS * consent.drift_per_day(approach.threshold)
    if approach.headroom < margin:
        return True, (
            f"it wants {margin:.0f} points of regard clear of the {approach.threshold:.0f} "
            f"at which it stops, and has {approach.headroom:.0f}")
    return False, (
        f"it took until {approach.regard:.0f}, {approach.headroom:.0f} points clear of the "
        f"{approach.threshold:.0f} at which it stops{_after(served)}, before the two would "
        "sign again")


def _after(served: str) -> str:
    return f", and {served}" if served else ""


def dwell_column_present(conn: sqlite3.Connection) -> bool:
    """Whether this world can record *when* a lane was suspended, not merely on which turn.

    The schema-49 twin of :func:`consent_columns_present`, separate for the same reason: a
    world at 47 or 48 can record a suspension and has no date to measure a dwell against.
    Such a world keeps the recovery margin -- which is a position and needs no clock -- and
    serves no dwell, which is the honest behaviour for a save that predates the column and
    is better than locking every lane it already has suspended shut for ever.
    """
    return "suspended_date" in {
        str(row["name"]) for row in conn.execute("PRAGMA table_info(trade_route)")
    }


def _reprice_period(turn_span: float) -> int:
    """How many *turns* apart two repricings of the same route should fall.

    :data:`REPRICE_EVERY_MONTHS` of world time, converted into the currency the stagger is
    actually counted in. At the 30-day turn every figure on this branch was measured at,
    ``turn_span`` is 1.0 and this returns 6 -- the constant it replaced, unchanged.

    **Never less than one, because a turn is the finest grain there is.** A route's terms
    are reconsidered at most once per turn by construction, so a yearly turn cannot honour a
    six-month period however it is asked to: every turn is the closest it can get, and it is
    the right direction to fail in -- a year of world time is well past the point where two
    governments would reopen the file.
    """
    if turn_span <= 0:
        return 1
    return max(1, round(REPRICE_EVERY_MONTHS / turn_span))


def _stagger(route_id: str, period: int) -> int:
    """Which of the repricing turns this route falls on. Derived from the id and nothing
    else, so a rewind and a replay put it back on the same turn -- a stagger that used the
    RNG would make the same replay reprice on a different turn and the digest would move.

    ``period`` is passed rather than read off a module constant because it now depends on
    the turn length: a world played in years spreads its routes over a different number of
    turns than one played in weeks, and a stagger keyed to the wrong one would park routes
    on turns that never come round.
    """
    return sum(route_id.encode()) % max(1, period)


def next_repricing(
    route_id: str, turn: int, *, turn_span: float = 1.0, priced_turn: int = 0,
) -> tuple[int, float]:
    """When this route's terms next come up, as ``(turn, days from now)``.

    **The same arithmetic :func:`assess_trade` reprices on, asked forwards.** The stagger and
    the period are already derived from the route id and the turn length and nothing else, so
    the answer is a pure function of them -- which is what lets a briefing print a *date*
    rather than "some time in the next six months", and lets it survive a rewind without
    being stored anywhere.

    The clause reproduced is::

        due = priced_turn < 0 or (priced_turn != turn and (turn + stagger) % period == 0)

    so a route the engine has never priced is due now, and any other route is due on the next
    turn the modulus comes round. ``priced_turn`` defaults to ``0`` rather than to ``-1``
    because the overwhelmingly common caller is a briefing about a route that *has* a price;
    a caller that means "never priced" says so.

    Returns days as well as the turn because a turn number is not a duration and the whole
    point of the surface this feeds is to state a deadline in the units the other deadlines
    on the page are stated in.
    """
    period = _reprice_period(turn_span)
    if priced_turn < 0:
        return turn, 0.0
    stagger = _stagger(route_id, period)
    ahead = 1
    # Bounded by `period` because the modulus has period `period`: one full cycle always
    # contains a solution, and a loop that could not terminate has no business in a briefing.
    while ahead <= period and (turn + ahead + stagger) % period != 0:
        ahead += 1
    if ahead > period:
        return turn + period, period * turn_span * REFERENCE_DAYS
    return turn + ahead, ahead * turn_span * REFERENCE_DAYS


def settled_price(conn: sqlite3.Connection, route_id: str) -> tuple[float, int]:
    """What a route's terms currently stand at: ``(unit_price, priced_turn)``.

    ``priced_turn`` of ``-1`` means unpriced -- barter. Public because the UI and the tests
    both want to ask it without knowing whether the columns exist.
    """
    if not pricing_columns_present(conn):
        return 0.0, -1
    row = conn.execute(
        "SELECT unit_price, priced_turn FROM trade_route WHERE id = ?", (route_id,)
    ).fetchone()
    if row is None:
        return 0.0, -1
    return float(row["unit_price"] or 0.0), int(row["priced_turn"])


def _treasuries(conn: sqlite3.Connection, world_id: str) -> dict[str, float]:
    return {
        str(row["id"]): float(row["treasury"] or 0.0)
        for row in conn.execute(
            "SELECT id, treasury FROM nation WHERE world_id = ? ORDER BY id", (world_id,)
        )
    }

def worth_selling(
    conn: sqlite3.Connection, world_id: str, seller: str, buyer: str
) -> tuple[str, float] | None:
    """The good this seller could most usefully send this buyer, and how much a month.

    **What a negotiated trade agreement actually moves, decided by the engine rather than
    by whoever drafted it.** The same discipline a nuclear launch takes no effect verbs
    under: the parties agree to trade, and what that is worth is read off what one of them
    digs up and the other cannot get enough of.

    Returns ``None`` when there is nothing to send -- a seller with no surplus, a buyer with
    no shortfall, or a world from before schema 14 where nobody has resources at all. An
    agreement between two such powers is a diplomatic fact and moves no cargo, which is
    honest: there was nothing to ship.
    """
    try:
        rows = conn.execute(
            "SELECT nation_id, good, produced FROM resource"
            " WHERE world_id = ? AND nation_id IN (?, ?)",
            (world_id, seller, buyer),
        ).fetchall()
    except sqlite3.OperationalError:
        return None
    produced = {(str(r["nation_id"]), str(r["good"])): float(r["produced"]) for r in rows}
    if not produced:
        return None

    seller_demand = demand_of(conn, seller)
    buyer_demand = demand_of(conn, buyer)
    best: tuple[str, float] | None = None
    best_gap = 0.0
    # Sorted, because two goods can tie and a dict's order must not decide what a treaty
    # says. Determinism here is what makes the same negotiation replay the same way.
    for good in GOODS:
        surplus = produced.get((seller, good), 0.0) - seller_demand
        gap = buyer_demand - produced.get((buyer, good), 0.0)
        if surplus <= 0 or gap <= 0:
            continue
        if gap > best_gap:
            best_gap = gap
            best = (good, _shipment(surplus, gap))
    return best if best and best[1] > 0 else None


def _shipment(surplus: float, gap: float) -> float:
    """Half the seller's surplus, and never more than the buyer is short of.

    A treaty that emptied a country's own supply would be a treaty nobody would sign, and
    one that shipped more than the buyer burns would just fill a stockpile the ceiling
    already caps. Extracted so :func:`sellable` and :func:`worth_selling` cannot drift:
    an agreement and an intention that opened the same lane at different volumes would be
    two rules for one question.
    """
    return round(min(surplus * 0.5, gap), 3)


def sellable(
    conn: sqlite3.Connection, world_id: str, seller: str, buyer: str, good: str
) -> float:
    """How much of *this named good* this seller could usefully send this buyer, a month.

    :func:`worth_selling` answers "what should these two trade", which is the question a
    treaty asks -- it picks the good and cannot be told one. :mod:`atlas.engine.intent`
    asks the other question: a government that has said it wants oil wants oil, and the
    engine's job there is the volume rather than the choice. ``0.0`` when there is nothing
    to ship, which is what the caller checks.
    """
    try:
        rows = conn.execute(
            "SELECT nation_id, produced FROM resource"
            " WHERE world_id = ? AND good = ? AND nation_id IN (?, ?)",
            (world_id, good, seller, buyer),
        ).fetchall()
    except sqlite3.OperationalError:
        return 0.0
    produced = {str(r["nation_id"]): float(r["produced"]) for r in rows}
    if not produced:
        return 0.0
    surplus = produced.get(seller, 0.0) - demand_of(conn, seller)
    gap = demand_of(conn, buyer) - produced.get(buyer, 0.0)
    if surplus <= 0 or gap <= 0:
        return 0.0
    return max(0.0, _shipment(surplus, gap))


def supply_ratio(
    conn: sqlite3.Connection, world_id: str, nation_id: str, good: str
) -> float:
    """What share of its need for ``good`` this nation can meet. ``1.0`` when comfortable.

    ``1.0`` for a world written before schema 14, where no nation has a resource row --
    so an old save builds at exactly the rate it always did.
    """
    stock = _stock(conn, world_id, nation_id)
    if good not in stock:
        return 1.0
    demand = demand_of(conn, nation_id)
    if demand <= 0:
        return 1.0
    return max(0.0, min(1.0, stock[good] / demand))


def is_unreachable(conn: sqlite3.Connection, world_id: str, nation_id: str) -> bool:
    """Whether this nation's ground is disconnected from the map entirely.

    **A map-data gap, handled openly rather than silently.** 35 land regions on the 2026
    map carry no adjacency edges at all -- Aruba, Anguilla, American Samoa and the like are
    single-province islands the pack never joined to anything. Nothing can reach them, so
    every trade route to them reads as cut, and without this they would sit permanently at
    the starved-capacity floor and appear in every turn report as short.

    That would be punishing a country for a hole in the geometry. Nobody blockaded Aruba:
    a nation nothing can reach also cannot be *cut off* by anyone, so it is exempt. If the
    packs ever gain those edges this becomes dead code, and it will be dead in the honest
    way -- returning False for everybody rather than quietly still firing.
    """
    row = conn.execute(
        """
        SELECT 1 FROM region r
         WHERE r.world_id = ? AND r.kind <> 'ocean'
           AND COALESCE(r.controller_nation_id, r.owner_nation_id) = ?
           AND EXISTS (SELECT 1 FROM region_adjacency a
                        WHERE a.world_id = r.world_id
                          AND (a.region_a = r.id OR a.region_b = r.id))
         LIMIT 1
        """,
        (world_id, nation_id),
    ).fetchone()
    return row is None


def shortfall_of(conn: sqlite3.Connection, world_id: str, nation_id: str) -> float:
    """What this nation's industry can actually run at, 0..1.

    The **worst** of the three goods, not the average: an aircraft factory with steel and
    no fuel is an aircraft factory that is not running, and averaging would let a surplus
    of one good paper over the absence of another. Floored at
    :data:`_STARVED_CAPACITY` -- see there for why not zero.
    """
    stock = _stock(conn, world_id, nation_id)
    if not stock or is_unreachable(conn, world_id, nation_id):
        return 1.0
    worst = min(supply_ratio(conn, world_id, nation_id, good) for good in stock)
    return _STARVED_CAPACITY + (1.0 - _STARVED_CAPACITY) * worst


@dataclass(frozen=True, slots=True)
class LaneOutcome:
    """What one standing route does this round: the terms, the cargo and the bill.

    **The one derivation, read by both the settlement and the panel that reports it.**
    :func:`assess_trade` decides what a lane does and then writes it; the dependency lens
    has to say the same thing before the turn is spent, and for a while it said it by
    walking a second copy of this loop "in the same order, with the same tests". That copy
    drifted, because a copy always does. It never grew the staggered repricing, it never
    grew the renegotiate-before-suspend clause, and it never grew the dwell. The visible
    result was a panel reporting a lane **suspended at a bill of zero** where the engine
    repriced it and shipped it -- a player budgeting against a number nothing enforces,
    which is the exact failure `test_the_lens_that_shows_who_supplies_you` exists to
    prevent.

    So the decision lives here now, once, and the two callers differ only in what they do
    with it: the engine writes diffs and report lines, the lens formats sentences.

    **Where the two still part, and why that part is safe.** The route-is-open test and the
    embargo test stay in the callers. Both are terminal -- a lane that fails either ships
    nothing and bills nothing, which both callers already agreed on -- and the engine's
    embargo branch carries an accounting of what the refusal cost its proposer that a
    read-only panel must not compute. Everything downstream of those two, which is
    everything that can produce a number, is decided here.
    """

    #: What the lane is doing: ``open``, ``suspended``, ``priced_out`` or ``barter``.
    status: str
    #: What the lane is settled or tested at this round. ``0.0`` is barter.
    unit_price: float
    #: What it carried *into* the round, before anything here reconsidered it.
    price_before: float
    shipped: float
    #: What the cargo costs, **in whole :data:`SETTLEMENT_UNITS`** -- the integer the
    #: settlement actually moves, and the reason this field is an ``int``.
    #:
    #: The bill is decided *here* and not in :func:`assess_trade`, which is the same
    #: argument the rest of this class makes one level up. Two callers derive the lane;
    #: only one of them writes it; and a bill computed twice is a bill that can come apart
    #: -- which is exactly what :data:`SETTLEMENT_UNITS` exists to stop happening between
    #: the two *sides* of one payment. The lens reads :attr:`bill` and the engine reads
    #: this, and they are the same number by construction rather than by review.
    bill_units: int = 0
    #: A price the engine would write to the route, or ``None`` where the terms stand.
    settle_price: float | None = None
    #: Terms reconsidered and left where they were: a ``priced_turn`` stamp, no change.
    considered: bool = False
    #: The deadband was bypassed because one side was otherwise about to walk.
    reopened: bool = False
    #: The pricing layer's own object, so a caller can print the sentence it wants.
    terms: Terms | None = None
    state: Band | None = None
    #: Whether the lane came into this round already stopped.
    was_stopped: bool = False
    #: What the dwell clause said, either way. Empty where it was never consulted.
    dwell: str = ""
    #: Set where the lane is open on its terms and something else held the cargo back.
    blocked: str = ""

    @property
    def bill(self) -> float:
        """What the cargo costs, in money -- :attr:`bill_units` over the grain.

        Exact: the grain is a power of two, so this division is exact in binary floating
        point and a reader that adds these up gets the same answer the treasury does.
        Round it where it is *shown*, never before -- see :data:`SETTLEMENT_UNITS`.
        """
        return self.bill_units / SETTLEMENT_UNITS

    @property
    def moving(self) -> bool:
        return self.shipped > 0


def settle_lane(  # noqa: PLR0913 - a lane is the world, the route, the round and the caches
    conn: sqlite3.Connection, world_id: str, route, *, turn: int, date: str,
    available: float, volume: float, budget: float, priced: bool, recordable: bool,
    dwelling: bool, reprice_period: int,
    lanes: dict, alternatives_cache: dict, distress_cache: dict,
    considered_this_turn: set[str], storable: bool = True,
) -> LaneOutcome:
    """Decide one lane, reading the world and writing nothing.

    ``considered_this_turn`` is the route ids this walk has already settled terms for --
    the engine's ``route_terms`` and ``looked_at`` together. It is what caps a route at one
    reconsideration per turn however many rounds the turn is ground into, and a fresh
    preview passes an empty set because it is modelling the next round rather than
    continuing this one.

    Callers must already have answered the two questions above this one: that the lane is
    open at all, and that nobody is embargoing it.

    ``storable`` is *does the buyer have a* ``resource`` *row for this good to receive it
    into*, which the caller knows because it has already loaded them all and this function
    would otherwise read the table once per lane. It defaults to true because a nation
    seeded by :mod:`atlas.state.bootstrap` has a row for every good, so the only way to be
    without one is to be a ``nation`` row created afterwards for something that is not a
    country -- an organisation, which :mod:`atlas.scenarios.seeder` writes with no
    endowment at all. Such a buyer used to be billed at the end of ``assess_trade`` while
    step 3 skipped its missing row and dropped the cargo on the floor. The *money* still
    balanced -- the payment reached the seller, which is why the conservation work did not
    catch this -- but the goods it paid for stopped existing, so the lane ran a cargo out of
    the world every round. It is refused here instead, where the lens sees the same refusal
    for free.
    """
    from . import consent, pricing  # local: both import this module, and a cycle helps nobody

    route_id = str(route["id"])
    seller, buyer = str(route["seller_id"]), str(route["buyer_id"])
    good = str(route["good"])
    shipped = min(volume, available)

    if not priced:
        return LaneOutcome(status="barter", unit_price=0.0, price_before=0.0,
                           shipped=shipped)

    stored = float(route["unit_price"] or 0.0)
    priced_turn = int(route["priced_turn"])
    settle_price: float | None = None
    considered = False
    terms: Terms | None = None

    # 1. Terms are settled the first time the engine looks at a route and looked at again
    #    on a stagger. In between, the standing price stands.
    due = priced_turn < 0 or (
        priced_turn != turn
        and (turn + _stagger(route_id, reprice_period)) % reprice_period == 0
    )
    if due:
        terms = pricing.price_route(
            conn, world_id, seller, buyer, good,
            lanes=lanes, alternatives_cache=alternatives_cache)
        fresh = terms.unit_price
        if priced_turn < 0 or abs(fresh - stored) > pricing.REPRICE_DEADBAND * max(
                stored, pricing.FLOOR_PRICE):
            settle_price = fresh
            unit_price = fresh
        else:
            # **Inside the deadband the file is not reopened, and the cargo is still
            # billed at what it is worth today.** The deadband exists so a *stored* price
            # does not chase the world -- that is what keeps a government's terms stable
            # and what `settle_price` staying `None` below preserves. It was never an
            # argument for invoicing at a number the engine has just recomputed and knows
            # to be stale.
            #
            # Billing at `stored` made the final world depend on execution order.
            # `scratchpad/DECISION_MEASUREMENTS.md` measured it: across 3,547 lanes whose
            # deadband held a stale price -- up to 12% stale -- the buyer's ceiling and the
            # seller's reserve returned the same verdict at both numbers **every single
            # time**, so nothing was accepted or refused differently. What differed was the
            # money. Whichever price a walk happened to be holding was the price it
            # charged, the integer settlement grain absorbed most of it, and occasionally a
            # bill landed on the other side of a unit boundary: by turn 30 that had moved
            # 13 treasuries in cold-war -- netting exactly zero, a pure redistribution --
            # and 101 in modern-day, where it had propagated into `unit`, because a
            # government with a marginally different purse builds marginally differently.
            #
            # `fresh` is derived from the world as it stands and carries no memory of when
            # this route was last repriced, so billing at it removes the dependence at its
            # source. The review is left exactly as it was, which is the cheaper of the two
            # fixes that note put up; the other was making the whole review atomic.
            considered = True
            unit_price = fresh
            terms = None
    else:
        unit_price = stored

    # **No early return on a zero price, and the absence is deliberate.** `price_route`
    # floors at `pricing.FLOOR_PRICE`, and an unpriced route always falls through `due`
    # above, so a priced world reaching here at zero takes an external write to arrange --
    # which makes this the corner nobody would ever test and exactly the sort of place two
    # copies of a loop diverge. The settlement consults the band at whatever number it is
    # holding, so this does too; the guards below are the settlement's own.

    # 2. And either side may decline.
    state = consent.band(
        conn, world_id, seller, buyer, good, unit_price, shipped=shipped,
        lanes=lanes, alternatives_cache=alternatives_cache,
        distress_cache=distress_cache)

    # 3. A route that falls outside the band is renegotiated before it is stopped. The
    #    buyer's side only and at most once a turn; the reasons are on `assess_trade`.
    reopened = False
    #: **Including the decision this call has just made, which a snapshot cannot see.**
    #: `assess_trade` read `route_terms` and `looked_at` *live*, so a route repriced or
    #: deliberately left alone in step 1 above was already in one of them by the time this
    #: test ran, and did not renegotiate. `considered_this_turn` is taken before the call
    #: and can only carry the *other* routes, so the two local flags stand in for this
    #: route's own entry. Without them the walk reopens a file it settled four lines ago,
    #: which moved two lanes' stored prices on the cold-war tape and reddened the gate.
    decided_here = settle_price is not None or considered
    if (state.ended_by == "buyer" and priced_turn != turn
            and route_id not in considered_this_turn and not decided_here):
        again = pricing.price_route(
            conn, world_id, seller, buyer, good,
            lanes=lanes, alternatives_cache=alternatives_cache)
        if again.unit_price != unit_price:
            settle_price = again.unit_price
            considered = False
            reopened = True
            terms = again
            unit_price = again.unit_price
            state = consent.band(
                conn, world_id, seller, buyer, good, unit_price, shipped=shipped,
                lanes=lanes, alternatives_cache=alternatives_cache,
                distress_cache=distress_cache)

    was_stopped = int(route["suspended_turn"]) >= 0 if recordable else False
    if not state.overlaps:
        return LaneOutcome(status="suspended", unit_price=unit_price, price_before=stored,
                           shipped=0.0, settle_price=settle_price,
                           considered=considered, reopened=reopened, terms=terms,
                           state=state, was_stopped=was_stopped)

    if unit_price >= pricing.EMBARGO_PRICE - _PRICE_EPSILON:
        return LaneOutcome(status="priced_out", unit_price=unit_price,
                           price_before=stored, shipped=0.0,
                           settle_price=settle_price, considered=considered,
                           reopened=reopened, terms=terms, state=state,
                           was_stopped=was_stopped)

    # 4. A lane that has been stopped does not reopen the moment the numbers touch again.
    dwell = ""
    if was_stopped:
        held, dwell = _still_dwelling(
            conn, world_id, seller, buyer, good, route, date=date, dwelling=dwelling,
            unit_price=unit_price, lanes=lanes,
            alternatives_cache=alternatives_cache)
        if held:
            return LaneOutcome(status="suspended", unit_price=unit_price,
                               price_before=stored, shipped=0.0,
                               settle_price=settle_price, considered=considered,
                               reopened=reopened, terms=terms, state=state,
                               was_stopped=True, dwell=dwell, blocked="dwell")

    # 5. And a buyer that has nowhere to put a good does not buy it. Before the money,
    #    because the alternative is a payment for a cargo that cannot land.
    if not storable:
        return LaneOutcome(status="open", unit_price=unit_price, price_before=stored,
                           shipped=0.0, settle_price=settle_price,
                           considered=considered, reopened=reopened, terms=terms,
                           state=state, was_stopped=was_stopped, dwell=dwell,
                           blocked="unstorable")

    # 6. And a buyer ships home what it can pay for. Guarded exactly as `assess_trade`
    #    guards it: a lane carrying no price charges nothing and is capped by nothing.
    if unit_price > 0:
        shipped = min(shipped, budget / unit_price)
    if shipped <= 0:
        return LaneOutcome(status="open", unit_price=unit_price, price_before=stored,
                           shipped=0.0, settle_price=settle_price,
                           considered=considered, reopened=reopened, terms=terms,
                           state=state, was_stopped=was_stopped, dwell=dwell,
                           blocked="budget" if available > 0 else "stock")
    # **What this cargo costs, decided once, before it moves.** One integer, in whole
    # :data:`SETTLEMENT_UNITS`, and the same integer reaches both parties in
    # :func:`assess_trade` -- its negation and nothing else -- so there is no arithmetic
    # left in which the two halves of one payment could come apart. It is also the number
    # the dependency lens reports, so the panel and the treasury cannot come apart either.
    #
    # **Priced before the cargo moves, and that ordering is the point.** A shipment too
    # small to bill at all does not happen: it is not waived, not rounded away, and not
    # quietly given to the buyer. There is no cargo in this engine that can reach here and
    # land on zero -- the smallest a route can ship is 0.001 and the least the pricing
    # layer will name is `FLOOR_PRICE`, which is 28 units -- so this is a floor under an
    # impossibility rather than a case that fires. It is written down because the
    # alternative to refusing the shipment is making it free, and free cargo below a
    # threshold is precisely the defect that constant exists to remove.
    bill_units = round(shipped * unit_price * SETTLEMENT_UNITS) if unit_price > 0 else 0
    if unit_price > 0 and bill_units <= 0:
        return LaneOutcome(status="open", unit_price=unit_price, price_before=stored,
                           shipped=0.0, settle_price=settle_price,
                           considered=considered, reopened=reopened, terms=terms,
                           state=state, was_stopped=was_stopped, dwell=dwell,
                           blocked="unbillable")
    return LaneOutcome(status="open", unit_price=unit_price, price_before=stored,
                       shipped=shipped, bill_units=bill_units,
                       settle_price=settle_price, considered=considered,
                       reopened=reopened, terms=terms, state=state,
                       was_stopped=was_stopped, dwell=dwell)


def assess_trade(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str, days: float = 30,
    turn_days: float | None = None,
) -> TradeReport:
    """Produce, ship and burn, once per round.

    Engine-authored, written as ordinary diffs on `resource.stockpile` citing one event, so
    it rewinds. A *flow*, so a season produces and burns a season's worth.

    Order matters and is the order the world works in: a country digs its own goods, then
    receives what it bought from anyone still able to ship, then its industry burns what it
    needs. A route that cannot deliver simply does not, and the buyer draws down its store.

    **Two different lengths of time, and the layer needs both.** ``days`` is this *round* --
    :class:`~atlas.engine.turn_engine.TurnEngine` grinds a long jump out as a sequence of
    roughly monthly rounds and calls this once per round, so it is what every flow here is
    scaled by. ``turn_days`` is the whole *turn*, and only one thing wants it: how often a
    route's terms are reconsidered, which is capped at once per turn and therefore counts in
    turns rather than in rounds. It defaults to ``days``, which is the truth for every
    caller that does not split a jump into rounds -- a test, a harness, or a monthly game,
    where the round and the turn are the same month.
    """
    span = span_of(days)
    turn_span = span_of(days if turn_days is None else turn_days)
    reprice_period = _reprice_period(turn_span)
    report = TradeReport()

    try:
        rows = conn.execute(
            "SELECT nation_id, good, produced, stockpile FROM resource"
            " WHERE world_id = ? ORDER BY nation_id, good", (world_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return report
    if not rows:
        return report

    stock = {(str(r["nation_id"]), str(r["good"])): float(r["stockpile"]) for r in rows}
    produced = {(str(r["nation_id"]), str(r["good"])): float(r["produced"]) for r in rows}
    nations = sorted({nation for nation, _good in stock})
    report.assessed = len(nations)

    moved: dict[tuple[str, str], float] = {key: 0.0 for key in stock}

    # 1. What each country digs up.
    for key, rate in produced.items():
        moved[key] += over(rate, span)

    # 2. What it ships and receives -- and, since schema 46, what it is paid for it. Each
    #    route's viability is decided once, and the same cache answers every question the
    #    pricing layer asks about the same pair.
    from . import consent, embargo, pricing, refusal

    priced = pricing.enabled() and pricing_columns_present(conn)
    #: Whether a suspension can be *carried* to the next turn. See
    #: :func:`consent_columns_present`: a schema-46 world still makes the decision and still
    #: reports it, it simply has nowhere to write it down.
    recordable = priced and consent_columns_present(conn)
    columns = "id, seller_id, buyer_id, good, volume"
    if priced:
        columns += ", unit_price, priced_turn"
    if recordable:
        columns += ", suspended_turn, suspended_by"
    #: Whether the dwell clause can bind at all. See :func:`dwell_column_present`.
    dwelling = recordable and dwell_column_present(conn)
    if dwelling:
        columns += ", suspended_date"
    routes = conn.execute(
        f"SELECT {columns} FROM trade_route"  # noqa: S608 - `columns` is a literal
        " WHERE world_id = ? ORDER BY id", (world_id,),
    ).fetchall()
    #: Every standing export embargo, fetched once. **A dictionary rather than a query per
    #: route**, for the reason the lane cache below exists: a world carries hundreds of
    #: routes and a handful of embargoes, and asking the table once per route is the same
    #: answer fetched hundreds of times. Empty -- and free -- whenever
    #: `atlas.engine.embargo` is down, which is the default.
    embargoes = embargo.bites(conn, world_id)
    #: What each standing embargo cost its proposer this round, by embargo id. Written as
    #: `ADJUST` diffs in the same batch as the cargo it suppressed, so a rewind that puts
    #: the shipment back also takes the sacrifice off the books.
    forgone: dict[str, float] = {}
    open_between: dict[tuple[str, str], tuple[bool, str]] = {}
    #: Shared with the pricing layer so a world's routes do not walk the same sea twice.
    alternatives_cache: dict[tuple[str, str, str], int] = {}
    #: And the same for the seller's side of it, which `atlas.engine.refusal` asks of every
    #: floor-pinned route: how much of this seller's business is still moving.
    distress_cache: dict[tuple[str, str], float] = {}
    #: What each nation may still spend on imports this round, drawn down in route-id order
    #: so that a buyer which spent its money on the first cargo cannot pay for the second.
    #:
    #: A share of what it held at the *start* of the round, not of what is left, or the cap
    #: would be a slow leak rather than a limit -- and deliberately not credited with what
    #: the same nation earns selling this round, so the order routes are settled in cannot
    #: change what anybody can afford.
    #:
    #: **Compounded over the round's span**, because the cargo on the other side of the
    #: comparison is a flow and this is not. See :data:`IMPORT_BUDGET_SHARE`: half a
    #: treasury a *month*, so a round covering a quarter of a month buys a quarter-month of
    #: imports out of a quarter-month's allowance, and the cap binds at the same tightness
    #: whatever length of turn the player chose. Compounding rather than multiplying is what
    #: keeps it below the treasury itself at long spans. At the monthly turn it is the
    #: identity, and every recorded figure stands.
    allowance = compounded(IMPORT_BUDGET_SHARE, span)
    budget_left = {
        nation: max(0.0, treasury) * allowance
        for nation, treasury in (_treasuries(conn, world_id) if priced else {}).items()
    }
    #: What each nation owes or is owed for cargo this round, **in whole
    #: :data:`SETTLEMENT_UNITS` and not in money**. Integers because the two sides of a
    #: payment have to cancel, and only integers cancel exactly; see the constant for what
    #: settling this in floats was costing.
    owed: dict[str, int] = {}
    route_terms: dict[str, float] = {}
    #: Routes whose commercial state changed this turn: ``route_id -> (turn, side)``, with
    #: ``(-1, "")`` for one that resumed. Written as diffs at the end beside the prices, so
    #: the record of a stoppage rewinds with the shipment it stopped.
    suspension: dict[str, tuple[int, str, str]] = {}
    #: Routes whose terms were reconsidered and left where they were. They get a
    #: `priced_turn` stamp and no price change.
    looked_at: set[str] = set()

    for route in routes:
        route_id = str(route["id"])
        seller, buyer = str(route["seller_id"]), str(route["buyer_id"])
        good, volume = str(route["good"]), over(float(route["volume"]), span)
        pair = (seller, buyer)
        if pair not in open_between:
            open_between[pair] = route_is_open(conn, world_id, seller, buyer)
        ok, why = open_between[pair]
        if not ok:
            if not is_unreachable(conn, world_id, buyer):
                report.interrupted.append(
                    f"{_called(conn, world_id, seller)} to {_called(conn, world_id, buyer)} "
                    f"({good.replace('_', ' ')}): {why}")
            continue

        # A seller ships what it has. It cannot ship a cargo it does not possess.
        available = max(0.0, stock.get((seller, good), 0.0) + moved.get((seller, good), 0.0))
        shipped = min(volume, available)

        # **And a seller may have decided not to, whatever it has and whatever the terms
        # are.** Before the pricing block and before the consent band, deliberately, on two
        # grounds. The cheap one: a route nobody is going to ship on has no business paying
        # for a sea walk and a fresh price every stagger. The load-bearing one: an embargo
        # is not a disagreement about terms, so putting it *after* the band would let a
        # route the two sides could not agree on anyway be reported as an act of coercion,
        # or the reverse -- and telling those two apart is the whole reason this layer
        # exists. An embargo answers first because it is the decision; everything below it
        # is the market.
        stopper = embargo.stopped_by(embargoes, seller, buyer, good) if priced else ""
        if stopper:
            # What the refusal costs its proposer: the cargo it would have moved, at the
            # terms standing on the route. Priced once here where the engine has never
            # looked at this route, because reporting a sacrifice of zero for a lane that
            # was never priced would understate the decision to nothing at all.
            #
            # **The buyer's import allowance is deliberately not applied.** Whether this
            # buyer could have afforded the cargo depends on which other routes drew its
            # budget down first, and those routes are settled in a world where this
            # shipment did not happen. Asking the counterfactual honestly would mean
            # settling a second world; what is reported instead is the plain thing a
            # ministry would say -- this is what the lane was earning.
            forgone_price = float(route["unit_price"] or 0.0)
            if int(route["priced_turn"]) < 0 or forgone_price <= 0:
                forgone_price = pricing.price_route(
                    conn, world_id, seller, buyer, good,
                    lanes=open_between, alternatives_cache=alternatives_cache,
                ).unit_price
            # **And the band is consulted anyway, purely to price the sacrifice.** This
            # reverses the cheap half of the ordering argument above, and it has to.
            #
            # The embargo still answers *first* for why the cargo stopped: that is a
            # decision, and it takes precedence over a market accident. But what it *cost*
            # is a different question, and the honest answer is the **marginal** one. If
            # the buyer's ceiling had already closed this lane, nothing was moving, nobody
            # was being paid, and refusing to ship costs the proposer exactly nothing.
            #
            # Measured in `scratchpad/forgone_probe.py`, and it is not a corner case: with
            # `atlas.engine.consent` at its shipped default the band closes a hostile lane
            # between -28 and -35 regard, and no government wants to embargo somebody it is
            # *warmer* with than that. So the regime where an embargo is wanted is very
            # nearly the regime where the ceiling has already shut the lane -- and booking
            # the standing price there recorded 2.8 to 3.6 a month of sacrifice against a
            # lane that had actually paid 0.00. The entire claim that an embargo costs its
            # proposer something rests on this number, so it is the marginal one or the
            # mechanic is free and the column says otherwise.
            #
            # The extra `band` call is paid only on routes that are actually embargoed,
            # which is a handful in any world.
            would_have_moved = consent.band(
                conn, world_id, seller, buyer, good, forgone_price,
                shipped=shipped, lanes=open_between,
                alternatives_cache=alternatives_cache, distress_cache=distress_cache,
            ).overlaps
            lost = round(shipped * forgone_price, 3) if would_have_moved else 0.0
            if lost > 0:
                forgone[stopper] = forgone.get(stopper, 0.0) + lost
            if not is_unreachable(conn, world_id, buyer):
                # **"This round", not "a month".** `shipped` is `over(volume, span)` --
                # the cargo this *round* would have carried -- so `lost` is a round's
                # sacrifice and saying "a month" was only true at the 30-day round. It is
                # the accumulator into `revenue_forgone` that spans the game, and that one
                # is right: round quantities summed over rounds.
                report.embargoed.append(
                    f"{seller} -> {buyer} ({good}): embargoed -- {seller} is giving up "
                    f"{lost:.2f} this round rather than ship it" if would_have_moved
                    else f"{seller} -> {buyer} ({good}): embargoed -- but the terms had "
                         f"already stopped this cargo, so it costs {seller} nothing to "
                         "refuse it"
                )
            continue

        # **One decision, made in one place.** See :func:`settle_lane`: the terms, the
        # two consents, the renegotiation, the dwell and the affordability cap are all
        # decided there, because the dependency lens has to be able to say what this
        # round will do before it happens and a second copy of this loop drifted from it.
        # What stays here is everything the settlement *writes* -- the price, the
        # suspension record, and the sentences a player is told -- which a read-only panel
        # must not do.
        outcome = settle_lane(
            conn, world_id, route, turn=turn, date=date, available=available,
            volume=volume, budget=budget_left.get(buyer, 0.0), priced=priced,
            recordable=recordable, dwelling=dwelling, reprice_period=reprice_period,
            lanes=open_between, alternatives_cache=alternatives_cache,
            distress_cache=distress_cache,
            considered_this_turn=route_terms.keys() | looked_at,
            storable=(buyer, good) in stock)
        shipped, unit_price = outcome.shipped, outcome.unit_price
        stored, state, was_stopped = (outcome.price_before, outcome.state,
                                      outcome.was_stopped)

        if outcome.settle_price is not None:
            route_terms[route_id] = outcome.settle_price
            looked_at.discard(route_id)
            if outcome.terms is not None:
                report.repriced.append(
                    f"{seller} -> {buyer} ({good}): "
                    f"{pricing.explain(outcome.terms, good)}"
                    + (" (reopened early: the standing terms had moved past what one "
                       "side would accept)" if outcome.reopened else "")
                )
        elif outcome.considered:
            # Looked at and left alone. `priced_turn` still moves -- it records when the
            # engine last *considered* the terms, which is what stops the remaining rounds
            # of this turn considering them again.
            looked_at.add(route_id)

        if outcome.status == "suspended" and outcome.blocked != "dwell":
            # **The agreement is untouched.** Nothing here writes `commitment`, so a month
            # nobody could agree a price on costs no breach, no regard, no casus belli and
            # no injured party. The route row stays where it is and the cargo moves again
            # the turn the two numbers overlap.
            if recordable and not was_stopped:
                suspension[route_id] = (turn, state.ended_by, date)
            if not is_unreachable(conn, world_id, buyer):
                # **And *why*, which is the one place in the engine that can say it
                # exactly.** `explain` names who stopped and at what number; it cannot say
                # whether anybody changed their mind, because that is a question about two
                # values of a column that only ever holds one. Here both are in hand --
                # `stored` is the price this route carried into the turn and `unit_price`
                # is what it was tested at -- so the sentence can state which of the three
                # things happened.
                cause = consent.why_it_stopped(
                    state,
                    consent.headroom(
                        conn, world_id, seller, buyer, good,
                        standing_price=unit_price, route_id=route_id, turn=turn,
                        turn_span=turn_span, lanes=open_between,
                        alternatives_cache=alternatives_cache,
                        distress_cache=distress_cache,
                    ),
                    good, price_before=stored,
                )
                if state.withdrawal is not None:
                    report.withdrawn.append(
                        f"{seller} -> {buyer} ({good}): "
                        f"{refusal.explain(state.withdrawal, good)} {cause}"
                    )
                else:
                    report.refused.append(
                        f"{seller} -> {buyer} ({good}): "
                        f"{consent.explain(state, good)} {cause}"
                    )
            continue

        if outcome.status == "priced_out":
            # The lane is open and the ships could sail. Nobody is buying at that number,
            # which is a different fact from a blockade and says so.
            if not is_unreachable(conn, world_id, buyer):
                report.interrupted.append(
                    f"{_called(conn, world_id, seller)} to {_called(conn, world_id, buyer)} "
                    f"({good.replace('_', ' ')}): priced out at {unit_price:.2f}"
                )
            continue

        if outcome.blocked == "dwell":
            if not is_unreachable(conn, world_id, buyer):
                report.refused.append(
                    f"{seller} -> {buyer} ({good}): the terms overlap again, but "
                    f"shipments stay stopped -- {outcome.dwell}"
                )
            continue

        if was_stopped:
            suspension[route_id] = (-1, "", "")
            if not is_unreachable(conn, world_id, buyer):
                report.resumed.append(
                    f"{seller} -> {buyer} ({good}): shipments resume at "
                    f"{unit_price:.2f} a unit -- {outcome.dwell}, and the agreement never "
                    "lapsed"
                )

        if shipped <= 0:
            continue

        # **What this cargo costs was decided in :func:`settle_lane`, as one integer, and
        # this reads it rather than working it out again.** That is the whole of the
        # settlement rule and the whole of the one-derivation rule meeting in one line: the
        # bill is a whole number of :data:`SETTLEMENT_UNITS`, the buyer's side is its
        # negation and nothing else, and the dependency lens reports the same integer off
        # the same call. Recomputing it here from `shipped * unit_price` would work today
        # and is exactly the shape of copy that drifted last time.
        #
        # A cargo too small to bill never reaches here: `settle_lane` refuses it and
        # reports `blocked="unbillable"`, so it is not waived, not rounded away and not
        # given to the buyer.
        bill_units = outcome.bill_units

        moved[(seller, good)] = moved.get((seller, good), 0.0) - shipped
        moved[(buyer, good)] = moved.get((buyer, good), 0.0) + shipped

        if bill_units:
            budget_left[buyer] = max(
                0.0, budget_left.get(buyer, 0.0) - outcome.bill)
            owed[buyer] = owed.get(buyer, 0) - bill_units
            owed[seller] = owed.get(seller, 0) + bill_units

    # 3. What industry burns, and the ceiling on what may be stored.
    diffs: list[Diff] = []
    for nation_id in nations:
        demand = over(demand_of(conn, nation_id), span)
        ceiling = demand_of(conn, nation_id) * _MAX_MONTHS_STORED
        short_goods: list[str] = []
        for good in GOODS:
            key = (nation_id, good)
            if key not in stock:
                continue
            after = stock[key] + moved.get(key, 0.0) - demand
            if after < 0:
                short_goods.append(good)
                after = 0.0
            after = min(after, ceiling)
            # **Written whenever the figure moves, and not only when it moves by a lot.**
            #
            # This used to be `abs(round(after - stock[key], 3)) >= 0.01`, and the write was
            # a `SET` of the whole new figure -- so a round whose net came to less than a
            # hundredth had its production, its consumption *and* its cargo discarded
            # together, and the next round started again from the old number.
            #
            # For a nation-good that moves slowly that is not a rounding artefact, it is a
            # **freeze**: the change is under the threshold in *every* round, so it is
            # dropped in every round and the stockpile never moves at all. Measured on the
            # shipped cold-war map, Italy's oil and rare sat at 7.92 for twelve turns while
            # this line computed -0.004 and threw it away twelve times running; 11 of 441
            # nation-goods were frozen for the whole game, and 2.5-2.7% of them in every era
            # tried. A country whose trade is small is exactly the country the trade layer
            # exists to model, and it was the one being held still.
            #
            # The gate is now "would the stored figure actually change", which is the only
            # thing a write is for. `round(..., 3)` on both sides is the engine's existing
            # stockpile grain -- `_shipment` already quantises a cargo to the same place --
            # so this writes when the number a player reads changes and stays quiet when it
            # does not. It costs about 1.3-1.5x the resource diffs per turn, measured, which
            # is the price of the column meaning what it says.
            settled = round(after, 3)
            if settled != round(stock[key], 3):
                diffs.append(Diff("resource", f"{nation_id}|{good}", "stockpile",
                                  DiffOp.SET, settled))
        if short_goods and not is_unreachable(conn, world_id, nation_id):
            report.short.append(f"{nation_id}: short of {', '.join(short_goods)}")

    # 4. And what the cargo cost. **In the same batch as the goods it paid for**, which is
    #    the whole settlement argument: a rewind that put the oil back and left the money
    #    spent, or took the money back and left the oil delivered, would be a rewind to a
    #    world that never existed. One batch, one event, one diff path -- so the pair moves
    #    together or neither moves.
    #
    #    `ADJUST` rather than `SET`, as the budget layer does for the same column: two
    #    subsystems that both settle a treasury in one turn must not each write the total
    #    they believe it should be, or the second silently discards the first.
    #    **And the batch adds up to nothing, exactly.** Every nation's position is a sum of
    #    the same integers the other side of each payment subtracted, so the integers cancel
    #    without a remainder; dividing by a power of two is exact in binary floating point,
    #    so the amounts that leave here cancel without a remainder too. `sum` over this loop
    #    is `0.0` and not `1e-17`, which is the property `test_a_settlement_creates_no_money`
    #    asserts with no tolerance at all.
    #
    #    A nation is skipped only when it owes and is owed *precisely the same amount* --
    #    an exporter that spent its receipts on the same round. Skipping that is not a
    #    threshold and loses nothing: the adjustment would be zero. The old rule dropped
    #    anything under a cent, which is where the free cargo came from.
    for nation_id in sorted(owed):
        units = owed[nation_id]
        if units == 0:
            continue
        amount = units / SETTLEMENT_UNITS
        report.settled[nation_id] = amount
        diffs.append(Diff("nation", nation_id, "treasury", DiffOp.ADJUST, amount))
    for route_id in sorted(route_terms):
        diffs.append(
            Diff("trade_route", route_id, "unit_price", DiffOp.SET,
                 round(route_terms[route_id], 4))
        )
    for route_id in sorted(set(route_terms) | looked_at):
        diffs.append(Diff("trade_route", route_id, "priced_turn", DiffOp.SET, turn))
    # **In this batch and not another one**, for the argument the settlement makes above: a
    # rewind that put the cargo back and left the route marked stopped -- or cleared the
    # mark and left the month's shipment missing -- would be a rewind to a world that never
    # existed.
    # And what the embargoes cost the governments holding them. **In this batch**, for the
    # settlement's own argument: a rewind that restored the cargo and left the sacrifice
    # recorded -- or cleared the sacrifice and left the month's shipment missing -- would be
    # a rewind to a world that never existed.
    diffs.extend(embargo.forgone_diffs(forgone))
    for route_id in sorted(suspension):
        stopped_turn, side, stopped_on = suspension[route_id]
        diffs.append(
            Diff("trade_route", route_id, "suspended_turn", DiffOp.SET, stopped_turn))
        diffs.append(Diff("trade_route", route_id, "suspended_by", DiffOp.SET, side))
        # **In the same batch as the turn number**, so the dwell clock and the fact it
        # times rewind together. A rewind that restored a suspension and lost the date it
        # started from would leave a lane that had already served a dwell it never began.
        if dwelling:
            diffs.append(
                Diff("trade_route", route_id, "suspended_date", DiffOp.SET, stopped_on))

    if not diffs:
        return report

    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.TRADE_AGREEMENT, origin=EventOrigin.ENGINE,
        text="Cargoes move, and the furnaces burn what arrives.",
        visibility=Visibility.PUBLIC,
    )
    outcome = apply_batch(
        conn,
        DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                  proposer_model=None,
                  diffs=tuple(
                      Diff(d.target_table, d.target_id, d.field, d.op, d.new_value,
                           reason_event_id=event_id,
                           visibility_scope=Visibility.PUBLIC)
                      for d in diffs
                  )),
    )
    if not outcome.applied:
        report.problems.append(f"trade could not be applied: {outcome.reason}")
    return report
