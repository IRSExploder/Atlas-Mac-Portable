"""What a cargo costs, settled by the engine from supply, demand and leverage.

Trade shipped as **barter**. A standing agreement moved oil from a seller to a buyer every
month for the life of the treaty, the buyer's furnaces burned it, and no treasury anywhere
in the world moved by a single point. ``grep treasury src/atlas/engine/trade.py`` returned
nothing. A blockade could starve a country of steel and could not cost it a penny, and the
oil states -- whose entire position in the world is that everybody else needs what is under
their ground -- were, economically, indistinguishable from countries that export nothing.

**The propose/decide split is the same one the rest of the layer already keeps.** A model
proposes that two powers trade; :func:`atlas.engine.trade.worth_selling` decides *what* and
*how much*, because a model that could name the volume could write itself a hundred barrels
a month. This module is the third answer in that sequence, and it is the engine's for
exactly the same reason: a model that could name the price could buy a war's worth of oil
for nothing. ``trade_route.unit_price`` is in
:data:`~atlas.state.permissions.ENGINE_ONLY` beside the volume, so the refusal is a property
of the wiring rather than of a prompt.

**Five terms, each a question a trade delegation would actually ask**, and each read off
state some other subsystem already maintains:

``scarcity``
    How badly the buyer needs this good against how comfortable the seller is in it.
    Straight from :func:`atlas.engine.trade.supply_ratio`, which is the same number the
    shortfall already scales production by -- so the thing that makes a country bid up is
    the thing that is about to stop its factories.
``squeeze``
    How many *other* sellers could reach this buyer with this good. A buyer with three
    alternatives is not squeezable and a buyer with none pays what it is asked. This is the
    "dependent buyer" lever named explicitly, and it is the one that makes a supply
    relationship a position rather than a line item.
``distress``
    Whether the seller has anywhere else to put the cargo. A seller whose other lanes are
    cut, or who is somebody's sanctions target, is selling into a market that has shrunk to
    the buyers still willing -- and it takes what it is offered. **This is the term that
    makes a blockade cost the blockaded party money and not only goods.**
``pressure``
    How badly the two of them are getting on, and whether the buyer is at war. This is the
    term that moves a price *toward embargo*: a deteriorating relationship does not cut a
    lane, it makes what crosses it dearer, until at :data:`EMBARGO_PRICE` nothing crosses
    at all. Trade stopping because it stopped being worth doing is a softer and more
    frequent thing than trade stopping because a fleet is in the way, and the engine had
    only the second.
``affordability`` -- not here, but in the settlement
    A buyer ships home what it can pay for. See :func:`atlas.engine.trade.assess_trade`; a
    country whose treasury is empty cannot import, which is a consequence worth having and
    one nothing in the engine could previously express.

**Pure derivation.** Nothing here writes and nothing here remembers: two worlds in the same
state price the same route identically, and asking costs nothing but queries. The *stored*
price on ``trade_route`` is a cache of this answer with a deadband, so that a route whose
terms have not really moved does not write a diff every turn -- see
:data:`REPRICE_DEADBAND`.

**The flag.** ``ATLAS_TRADE_PRICING=off`` is the kill switch and it is honest about being
one: with it off, no price is computed, no price column is written and no treasury moves,
so a world plays exactly as it did before this module existed. It **defaults on** -- see
:func:`_flag_from_env` for what settled that and for what else it brings up with it -- and
it is off automatically for a world whose schema predates the pricing columns -- see
:func:`atlas.engine.trade.pricing_columns_present` -- because that is not a choice anybody
made, it is a save from before the feature.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field

from .trade import (
    GOODS,
    _still_standing_at_world_clock,
    demand_of,
    route_is_open,
    supply_ratio,
)

__all__ = [
    "BASE_PRICE",
    "COMMERCIAL_CEILING",
    "EMBARGO_PRICE",
    "FLOOR_PRICE",
    "REPRICE_DEADBAND",
    "Terms",
    "alternatives",
    "at_war_with_anybody",
    "enabled",
    "hostility",
    "pressure_at_hostility",
    "price_route",
    "regard_at_hostility",
    "seller_distress",
    "set_enabled",
]


def _flag_from_env() -> bool:
    """``ATLAS_TRADE_PRICING=off`` puts the engine back to barter.

    **Default on**, and the parse is inverted with the default, for the reason
    :mod:`atlas.engine.refusal` and :mod:`atlas.engine.consent` give for their own: once a
    layer ships, the reading that has to work is the *negative* one, so a typo leaves the
    layer on rather than quietly shipping a different game.

    **It was staged for a calibration decision, and the decision has been made on
    measurement.** The layer was default-off while it was the first large *unearned* inflow
    in the engine with no term damping a country simply accumulating cash. What settled it:

    * the long games in `scratchpad/TRADE_PRICING_VALIDATION.md` and
      `scratchpad/TRADE_AGENCY.md`;
    * `scratchpad/CEILING_FIXES.md`, which closed the two defects that were gating this --
      the ceiling reading the wrong government's ledger, and a suspension arriving with no
      notice -- and re-measured the whole stack as a *paired* run, one world id per era,
      under the four validity guards `scratchpad/PAIRED_RUN_VALIDITY.md` sets out;
    * and the money check inside it: what one treasury loses another gains, read off the
      settled receipts rather than off a standing price, to within the engine's own rounding
      grain.

    **What this default does and does not turn on.** It gates the whole priced branch of
    :func:`atlas.engine.trade.assess_trade`, so flipping it also brings up
    :mod:`atlas.engine.refusal`, :mod:`atlas.engine.consent` and :mod:`atlas.engine.intent`,
    which have always defaulted on *inside* that branch and never ran outside it. It does
    **not** turn on :mod:`atlas.engine.embargo`, which is behind its own flag, is still
    default-off, and is a separate decision.

    Read once, at import, and never again: a coefficient that could change between two
    turns of the same game is a coefficient that breaks replay, and the environment is
    exactly that kind of thing. Tests move the flag through :func:`set_enabled` instead,
    which is a deliberate act inside one process rather than ambient state.
    """
    raw = os.environ.get("ATLAS_TRADE_PRICING", "on").strip().lower()
    return raw not in {"0", "off", "false", "no"}


#: Whether the engine prices trade at all. See the module docstring; :func:`set_enabled`
#: is the supported way to move it.
_ENABLED: bool = _flag_from_env()


def enabled() -> bool:
    return _ENABLED


def set_enabled(value: bool) -> bool:
    """Move the flag, returning what it was. A function rather than a bare global so that
    every reader goes through one door and a test can restore what it found."""
    global _ENABLED
    was, _ENABLED = _ENABLED, bool(value)
    return was


#: What one unit of a good, delivered for one month, costs between two powers who need each
#: other equally and neither of whom has any hold over the other.
#:
#: **Measured against three seeded eras, not reasoned about.** The first draft put this at
#: 0.45 on the argument that the seeded routes move "one to five units a month", and that
#: argument was simply wrong: `scratchpad/pricing_scale.py` on the real packs shows the
#: seeded volumes are an order of magnitude larger than that, and at 0.45 the United States
#: earned 98% of its revenue again from trade on turn one, Saudi Arabia 662% of its, and
#: Zaire 456% of its. That is not a strategic-resource layer, it is a second and larger
#: economy running beside the calibrated one.
#:
#: At 0.09 the same measurement puts the great powers in the range a strategic export
#: *should* be worth -- see the recorded figures in `scratchpad/TRADE_PRICING.md`. The
#: petrostates still clear a substantial multiple of their non-oil revenue, and that is
#: kept deliberately: a country whose entire position in the world is what is under its
#: ground is supposed to look like one, and flattening that to a few percent would be
#: building the mechanic and then hiding it.
BASE_PRICE = 0.09

#: The least a cargo ever goes for. A seller with nowhere else to sell still does not pay
#: somebody to take it: a negative price is a number no player can act on, and the floor is
#: where "worse terms" stops being terms.
FLOOR_PRICE = BASE_PRICE * 0.3

#: The most that *commerce alone* can bid a cargo up to -- scarcity, dependence and a
#: seller's distress, with nobody bearing anybody a grudge.
#:
#: **Four times base, raised from three because three was quietly cancelling the squeeze.**
#: A buyer entirely out of a good reaches 2.1 on scarcity, and having no alternative
#: supplier is worth 1.8 on top: 3.78, above a ceiling of 3. So the captive buyer and the
#: buyer with three suppliers to choose between were charged 3.0 and 2.52 -- a 19% spread
#: standing in for the difference between total dependence and none, because the clamp was
#: eating the lever the layer exists to model.
#: `test_a_supplier_price_squeezes_a_dependent_buyer` is what found it.
#:
#: **This is separate from :data:`EMBARGO_PRICE` and the separation is the design.** The
#: first draft clamped everything to one ceiling, and the consequence was that an ordinary
#: commercial squeeze -- a desperate buyer and a monopolist, which is a perfectly normal
#: thing for a world to contain -- hit the same number that means "nobody is trading", so
#: the most interesting trade relationship in the game read as an embargo. Being expensive
#: and being off are different facts. Commerce can only make a cargo dear; it takes
#: *pressure* to make it not happen.
COMMERCIAL_CEILING = BASE_PRICE * 4.0

#: And the price at which the trade stops happening. Not a cut lane -- the ships could
#: sail; nobody is willing at that number. Reported separately from a blockade for exactly
#: that reason, because they are different facts about the world and only one of them can
#: be fixed by a navy.
#:
#: At eight times base it is only reachable when a dependency meets a bad relationship: the
#: commercial ceiling is four, so the pressure term has to roughly double a price that is
#: already at the top of its commercial range. **A power the buyer does not need cannot
#: embargo it however much the two of them hate each other**, which is the correct answer
#: and is the whole reason the two ceilings are different numbers.
EMBARGO_PRICE = BASE_PRICE * 8.0

#: How far the standing price must be from what the world now says before the engine writes
#: a new one. **Without a band this writes a diff for every route every turn**: the terms
#: move by fractions of a percent as stockpiles breathe, 648 seeded routes each produce a
#: `trade_route.unit_price` diff a turn, and the diff log -- which is what a player reads to
#: find out what changed -- fills with noise that means nothing. At 12% a repricing is an
#: event: something moved enough to renegotiate.
REPRICE_DEADBAND = 0.12

#: How hard the supply gap pulls. At 1.1 a buyer that is completely out of a good, dealing
#: with a seller that is comfortable in it, pays a little over twice what a comfortable
#: buyer would -- which is the shape of every real commodity squeeze and is bounded, unlike
#: the exponential the first draft used.
_SCARCITY_GAIN = 1.1

#: What being the only game in town is worth. A monopolist adds 80%; a fourth alternative
#: takes that to 20%. **The curve is 1/(1+k) rather than linear** because the first
#: alternative is the one that matters -- going from one supplier to two changes a country's
#: strategic position, and going from four to five changes a spreadsheet.
_SQUEEZE_GAIN = 0.8

#: And what having nowhere else to sell costs. Capped below 1 by construction: a seller
#: whose every other lane is cut and who is under sanction on top of that gets 45% off the
#: terms it would otherwise have had, which is severe and is not confiscation.
_DISTRESS_GAIN = 0.45

#: How far a bad relationship can push a price. 1.6 is enough to carry an otherwise ordinary
#: trade past :data:`EMBARGO_PRICE` -- which is the point: two powers who loathe each other
#: stop trading without anybody having to blockade anything.
_PRESSURE_GAIN = 1.6

#: What a buyer at war pays on top, as a share. A country fighting a war needs the oil more
#: than it needs the money and everybody selling to it knows.
_WAR_PREMIUM = 0.35

#: Relation score below which a pair counts as hostile at all, and the span over which
#: hostility runs to its maximum. `relation.score` is -100..100 and the seeded world sits
#: near zero, so a pair has to be actively bad rather than merely not friendly.
_PRESSURE_ONSET = -10.0
_PRESSURE_FULL = -70.0


@dataclass(frozen=True, slots=True)
class Terms:
    """What the engine says this cargo goes for, and why it says so.

    ``because`` is not decoration. The whole risk in an engine-settled price is that it
    reads as a number the game made up, so every term that moved it by more than a rounding
    error names itself -- and the same strings are what a player is shown when they ask why
    the oil got expensive.
    """

    unit_price: float
    #: Each multiplier, so a caller can see which one did the work.
    scarcity: float = 1.0
    squeeze: float = 1.0
    distress: float = 1.0
    pressure: float = 1.0
    #: How many other sellers of this good could reach the buyer.
    alternatives: int = 0
    #: Priced out: the lane is open, the ships could sail, and nobody is willing at this
    #: number. **Recorded rather than recomputed from the price**, because the price is
    #: clamped *to* the threshold and `>=` on two floats that a rounding step apart is a
    #: coin toss -- the first version shipped a cargo that had been priced out, at exactly
    #: the embargo price, because `1.17 >= 1.1700000000000002` is False.
    embargoed: bool = False
    because: tuple[str, ...] = field(default_factory=tuple)


def _surplus(conn: sqlite3.Connection, world_id: str, nation_id: str, good: str) -> float:
    """What this nation digs up beyond what it burns, per month. Negative when short."""
    row = conn.execute(
        "SELECT produced FROM resource WHERE world_id = ? AND nation_id = ? AND good = ?",
        (world_id, nation_id, good),
    ).fetchone()
    if row is None:
        return 0.0
    return float(row["produced"]) - demand_of(conn, nation_id)


#: The shape of the lane cache threaded through this module: ``(seller, buyer)`` to the
#: answer :func:`atlas.engine.trade.route_is_open` gave. Pricing a world's routes asks the
#: same question of the same pairs many times over -- every buyer's alternatives overlap
#: heavily -- and the sea walk is by far the most expensive thing here.
Lanes = dict


def _open(
    conn: sqlite3.Connection, world_id: str, seller: str, buyer: str, lanes: Lanes | None
) -> bool:
    if lanes is None:
        return route_is_open(conn, world_id, seller, buyer)[0]
    key = (seller, buyer)
    if key not in lanes:
        lanes[key] = route_is_open(conn, world_id, seller, buyer)
    return lanes[key][0]


def _alternatives(
    conn: sqlite3.Connection, world_id: str, buyer: str, seller: str, good: str,
    lanes: Lanes | None = None,
) -> int:
    """How many powers other than this seller could actually supply this buyer.

    **Reachability is asked, not assumed.** A surplus on the far side of a sea somebody
    else's fleet commands is not an alternative, and counting it would make a blockaded
    buyer read as well-supplied at exactly the moment it is not. That is
    :func:`atlas.engine.trade.route_is_open`, the same walk the cargo itself takes.

    Capped at :data:`_ALTERNATIVES_CAP` candidates examined, because the walk is the
    expensive part of this module and the multiplier is flat past three anyway -- a buyer
    with four suppliers and a buyer with forty are in the same position.
    """
    candidates = [
        str(row["nation_id"])
        for row in conn.execute(
            # Ordered by how much they have spare, so the cap keeps the sellers that
            # actually matter, and by id after that so the answer is deterministic when
            # two powers have identical surpluses.
            "SELECT nation_id, produced FROM resource"
            "  WHERE world_id = ? AND good = ? AND nation_id NOT IN (?, ?)"
            "  ORDER BY produced DESC, nation_id",
            (world_id, good, seller, buyer),
        )
    ]
    found = 0
    looked = 0
    for other in candidates:
        if _surplus(conn, world_id, other, good) <= 0:
            continue
        looked += 1
        if looked > _ALTERNATIVES_CAP:
            break
        if _open(conn, world_id, other, buyer, lanes):
            found += 1
            if found >= _ALTERNATIVES_ENOUGH:
                break
    return found


def alternatives(
    conn: sqlite3.Connection, world_id: str, buyer: str, seller: str, good: str,
    *, lanes: Lanes | None = None, alternatives_cache: dict | None = None,
) -> int:
    """How many powers other than this seller could supply this buyer, cache and all.

    **The public door onto the same walk :func:`price_route` takes**, extracted for
    :mod:`atlas.engine.consent`: the buyer's ceiling rises with captivity at exactly the
    rate the price's squeeze term does, and that equality is only true if both read the
    same count. Two implementations of "who else could reach this buyer" that drifted
    apart would be a captive buyer charged a monopolist's price and permitted to refuse it.

    The cache key is ``(buyer, good, seller)`` -- who else can reach a given buyer with a
    given good is the same question for every route into it, and a seeded world has one
    buyer at the end of several.
    """
    key = (buyer, good, seller)
    if alternatives_cache is None:
        return _alternatives(conn, world_id, buyer, seller, good, lanes)
    if key not in alternatives_cache:
        alternatives_cache[key] = _alternatives(conn, world_id, buyer, seller, good, lanes)
    return alternatives_cache[key]


#: How many surplus-holding powers are worth walking a sea route for, and how many found
#: alternatives is already "plenty". Both exist to bound the cost: the multiplier at three
#: alternatives is 1.2 and its limit is 1.0, so the fourth answer cannot change a price by
#: more than five percent of base.
_ALTERNATIVES_CAP = 6
_ALTERNATIVES_ENOUGH = 3


def _blocked_share(
    conn: sqlite3.Connection, world_id: str, seller: str, buyer: str,
    lanes: Lanes | None = None,
) -> float:
    """The share of this seller's *other* standing routes that cannot currently deliver.

    Excluding the lane being priced, which is the whole point: a seller is in distress
    because of the outlets it has lost, and counting the one that still works among the
    ones that do not would make every seller look half-blockaded.
    """
    rows = conn.execute(
        "SELECT DISTINCT buyer_id FROM trade_route"
        "  WHERE world_id = ? AND seller_id = ? AND buyer_id <> ? ORDER BY buyer_id",
        (world_id, seller, buyer),
    ).fetchall()
    if not rows:
        return 0.0
    cut = sum(
        0 if _open(conn, world_id, seller, str(row["buyer_id"]), lanes) else 1
        for row in rows
    )
    return cut / len(rows)


def _is_sanctioned(conn: sqlite3.Connection, world_id: str, nation_id: str) -> bool:
    """Whether any body's measure currently names this nation. Not who imposed it: a
    pariah sells cheap to whoever is still buying, which is the position sanctions are
    meant to put a country in and the one the engine could not previously express."""
    # Standing, not merely recorded -- the same correction `trade.route_is_open` needed and
    # for the same reason: rows are never deleted, so a lapsed measure went on discounting
    # a country's goods forever. The clock comes off the joined `world` row because nothing
    # on the path into `price_route` carries a date.
    try:
        row = conn.execute(
            f"SELECT 1 FROM sanction s JOIN world w ON w.id = s.world_id"
            f" WHERE s.world_id = ? AND s.target_nation_id = ?"
            f"   AND {_still_standing_at_world_clock()} LIMIT 1",  # noqa: S608 - literal fragment
            (world_id, nation_id),
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    return row is not None


def _relation(conn: sqlite3.Connection, world_id: str, a: str, b: str) -> tuple[float, bool]:
    """``(score, at_war)`` for an ordered pair. Rows are directed and written both ways by
    the bootstrap, so one lookup is the whole answer."""
    row = conn.execute(
        "SELECT score, at_war FROM relation"
        "  WHERE world_id = ? AND nation_a = ? AND nation_b = ?",
        (world_id, a, b),
    ).fetchone()
    if row is None:
        return 0.0, False
    return float(row["score"] or 0.0), bool(int(row["at_war"] or 0))


def at_war_with_anybody(conn: sqlite3.Connection, world_id: str, nation_id: str) -> bool:
    """Whether this nation is fighting anybody at all.

    Public because :mod:`atlas.engine.consent` charges a buyer's ceiling the same war
    premium the price charges it, and two readings of "is this country at war" that could
    drift apart would be a buyer paying a premium it was not allowed to concede.
    """
    row = conn.execute(
        "SELECT 1 FROM relation WHERE world_id = ? AND nation_a = ? AND at_war = 1 LIMIT 1",
        (world_id, nation_id),
    ).fetchone()
    return row is not None


def seller_distress(
    conn: sqlite3.Connection, world_id: str, seller: str, buyer: str,
    lanes: Lanes | None = None,
) -> float:
    """How badly this seller needs *this* sale, 1.0 for a seller in no trouble at all.

    Extracted from :func:`price_route` rather than copied, because
    :mod:`atlas.engine.refusal` asks the same question to decide whether a seller may
    walk away, and two definitions of "distress" that drifted apart would be a seller
    that took the pariah discount and then declined the sale for having options.
    """
    blocked = _blocked_share(conn, world_id, seller, buyer, lanes)
    pariah = 1.0 if _is_sanctioned(conn, world_id, seller) else 0.0
    return _distress_from(blocked, pariah)


def _distress_from(blocked: float, pariah: float) -> float:
    """The discount itself, given the two readings. Split out so :func:`price_route` --
    which needs ``blocked`` for its own explanation anyway -- does not walk the seller's
    other lanes twice to get the same number."""
    # Averaged rather than summed: a seller that is both cut off and sanctioned is in the
    # worst position there is, and that position is one discount and not two stacked.
    return 1.0 - _DISTRESS_GAIN * max(blocked, pariah) * (0.65 + 0.35 * min(blocked, pariah))


def hostility(conn: sqlite3.Connection, world_id: str, a: str, b: str) -> float:
    """How badly two powers are getting on, 0.0 at ordinary terms and 1.0 at the worst.

    The same reading the pressure term is built on, extracted for the same reason
    :func:`seller_distress` is.
    """
    score, _at_war = _relation(conn, world_id, a, b)
    span = _PRESSURE_ONSET - _PRESSURE_FULL
    return max(0.0, min(1.0, (_PRESSURE_ONSET - score) / span))


def pressure_at_hostility(h: float) -> float:
    """The pressure multiplier at hostility ``h``, **war premium excluded**.

    Public for :mod:`atlas.engine.consent`, which needs the two halves of
    :attr:`Terms.pressure` apart: the term carries a relationship and a war multiplied
    together, they have entirely different levers -- one is earned back over years, the other
    ends when the fighting does -- and a warning that offered "improve relations" to a
    government paying a war premium would be naming a lever that does not reach.

    Extracted rather than reimplemented for the reason :func:`seller_distress` and
    :func:`at_war_with_anybody` were: two readings of one coefficient that drifted apart
    would put a factor's name on the wrong half of a price.
    """
    return 1.0 + _PRESSURE_GAIN * max(0.0, min(1.0, h))


def regard_at_hostility(h: float) -> float:
    """The relation score at which :func:`hostility` reads ``h``. Its inverse.

    For a caller that has solved a band for the hostility at which it closes and wants to
    print the answer in the units a player acts in -- a *regard*, the number diplomacy
    moves -- rather than as a ratio of two prices. See
    :func:`atlas.engine.consent.approach`.

    **Clamped to the live window**, ``[-70, -10]``, because outside it the function this
    inverts is flat: every score above :data:`_PRESSURE_ONSET` gives the same zero and every
    score below :data:`_PRESSURE_FULL` the same one, so there is no score to recover and the
    honest answer is the edge of the range where the quantity still moves.
    """
    h = max(0.0, min(1.0, h))
    return _PRESSURE_ONSET - h * (_PRESSURE_ONSET - _PRESSURE_FULL)


def price_route(
    conn: sqlite3.Connection, world_id: str, seller: str, buyer: str, good: str,
    *, lanes: Lanes | None = None, alternatives_cache: dict | None = None,
) -> Terms:
    """What this cargo goes for, per unit per month, and the reasons it goes for that.

    Called with the pair and the good rather than a route row, because the answer does not
    depend on the volume: a price is a rate, and doubling the shipment doubles the bill
    rather than the terms. That also means this is answerable *before* a route exists,
    which is what lets the terms be settled at the moment an agreement is honoured.
    """
    because: list[str] = []

    # 1. Scarcity. The buyer's tightness against the seller's, both from the number the
    #    production layer already scales industry by.
    buyer_need = 1.0 - supply_ratio(conn, world_id, buyer, good)
    seller_need = 1.0 - supply_ratio(conn, world_id, seller, good)
    scarcity = max(0.25, 1.0 + _SCARCITY_GAIN * (buyer_need - seller_need))
    if buyer_need - seller_need > 0.25:
        because.append(f"the buyer is short of {good} and the seller is not")
    elif seller_need - buyer_need > 0.25:
        because.append(f"the seller needs its own {good} more than the buyer does")

    # 2. Squeeze. Who else could sell this buyer the same thing.
    reachable = alternatives(
        conn, world_id, buyer, seller, good,
        lanes=lanes, alternatives_cache=alternatives_cache)
    squeeze = 1.0 + _SQUEEZE_GAIN / (1.0 + reachable)
    if reachable == 0:
        because.append("the buyer has no other supplier it can reach")
    elif reachable >= _ALTERNATIVES_ENOUGH:
        because.append("the buyer has other suppliers to go to")

    # 3. Distress. Whether the seller has anywhere else to put it.
    blocked = _blocked_share(conn, world_id, seller, buyer, lanes)
    pariah = 1.0 if _is_sanctioned(conn, world_id, seller) else 0.0
    distress = _distress_from(blocked, pariah)
    if blocked > 0.4:
        because.append("the seller's other lanes are cut and the cargo has nowhere else to go")
    if pariah:
        because.append("the seller is under sanction and sells to whoever still will")

    # 4. Pressure. How badly they are getting on, and whether the buyer is fighting.
    hostile = hostility(conn, world_id, seller, buyer)
    pressure = 1.0 + _PRESSURE_GAIN * hostile
    if hostile > 0.25:
        because.append("relations between the two are bad enough to be priced in")
    if at_war_with_anybody(conn, world_id, buyer):
        pressure *= 1.0 + _WAR_PREMIUM
        because.append("the buyer is at war and needs it more than it needs the money")

    commercial = min(COMMERCIAL_CEILING, BASE_PRICE * scarcity * squeeze * distress)
    price = commercial * pressure
    embargoed = price >= EMBARGO_PRICE
    price = max(FLOOR_PRICE, min(EMBARGO_PRICE, price))
    return Terms(
        unit_price=round(price, 4),
        scarcity=round(scarcity, 4),
        squeeze=round(squeeze, 4),
        distress=round(distress, 4),
        pressure=round(pressure, 4),
        alternatives=reachable,
        embargoed=embargoed,
        because=tuple(because),
    )


def explain(terms: Terms, good: str) -> str:
    """One line a player can read. Tucked away by default -- the price is a column on a
    route, not a screen -- and this is what the route says when it is asked."""
    head = f"{terms.unit_price:.2f} a unit of {good} a month"
    if terms.embargoed:
        head += " -- priced out; nobody is buying at that"
    if not terms.because:
        return head + ", which is about what it is worth to both of them"
    return head + ": " + "; ".join(terms.because)


def priceable_goods() -> tuple[str, ...]:
    """The goods this module knows how to price. One list, and it is trade's."""
    return GOODS
