"""A buyer that can say *that is too much*, and the band the two answers make together.

**There was a seller's floor and no buyer's ceiling, so half a negotiation was missing.**
:mod:`atlas.engine.refusal` gave a seller a reserve price and the right to walk away from
terms beneath it. Nothing gave the other side anything. A price walked up by
:mod:`atlas.engine.pricing` -- by a squeeze, by a distress premium, above all by *pressure*,
the term that exists to move a price toward embargo -- was simply paid, out of the buyer's
treasury, every month, until the number crossed :data:`~atlas.engine.pricing.EMBARGO_PRICE`
and the engine declared that nobody was trading. That is not a negotiation between two
governments. That is an automatic treasury effect with a cliff at the end of it, and a
player watching their money drain into a rival's exchequer could not tell you *who* had
decided that, because nobody had.

So the buyer gets the answer the seller already had.

**The band.** Each side of a standing route now names a number, and the cargo moves only
where the two overlap:

``seller_min``
    The least the seller will ship for. Not new -- it is
    :data:`atlas.engine.refusal.RESERVE_PRICE` raised by hostility, and a seller in distress
    has no reserve at all because it is in no position to hold out. Expressed here as the
    bottom of an interval so that the two halves can be reasoned about as one object.
``buyer_max``
    The most the buyer will pay, and this module is mostly about deriving it. Four
    readings, each of which a real purchasing ministry would actually put in a memo, and
    each read off state some other subsystem already maintains -- **need**, **dependency**,
    **alternatives** and **affordability**.

A route whose standing price sits inside ``[seller_min, buyer_max]`` ships. A route whose
price falls outside it **suspends**, from whichever side is out of range -- and the
agreement behind it is untouched. That last part is the design, not a detail: an agreement
ending is a *diplomatic* fact with a breach, a regard charge, a casus belli and an injured
party attached to it, and a month where nobody could agree a price is not any of those
things. The cargo stops. The signatures stand. When the ranges overlap again the cargo
resumes, and neither government had to sign anything twice.

**The four readings behind ``buyer_max``.**

``need``
    How short the buyer actually is, from :func:`atlas.engine.trade.supply_ratio` -- the
    same number the production layer scales industry by and the same one the price's own
    scarcity term is built from. A country whose furnaces stop without this cargo will pay
    a great deal for it; a country with a full store will not.
``dependency``
    Whether the buyer is *structurally* an importer of this good: what share of its monthly
    demand its own ground cannot cover. Distinct from ``need``, which is about this month's
    store and moves as stockpiles breathe. A country that produces none of a good is
    dependent whatever its warehouse says, and it concedes that when it bargains.
``alternatives``
    How many other sellers could reach it, from :func:`atlas.engine.pricing.alternatives`
    -- **the same count, on the same walk, that the price's squeeze term reads.** That
    sharing is deliberate and it is the most important calibration decision in this module:
    the ceiling rises with captivity at exactly the rate the price rises with it, so
    *being squeezed by a monopolist never, on its own, suspends a route*. A buyer with no
    alternatives is in a bad position and knows it, and it pays. What suspends a route is
    a price driven by something the buyer does not accept as its own weakness.
``affordability``
    And the purse. A government will not keep paying above what it can pay, whatever it
    would otherwise concede -- see :data:`AFFORDABILITY_SLACK` for why this is a wide
    backstop rather than a tight budget line.

**What actually ends a route, therefore, is hostility -- and that is the point.** Pressure
multiplies the price by up to 2.6 and :data:`HOSTILITY_BITE` cuts the ceiling by up to half,
so two powers whose relations are deteriorating stop trading around a relation score of
**-33**, from the *buyer's* side, with a named reason, instead of at the -64 or so where the
engine used to declare an embargo on its own account. The trade stops earlier, it stops
because somebody decided, and the briefing can say who and why.

**Pure derivation, and nothing here writes.** Like :mod:`atlas.engine.pricing` and
:mod:`atlas.engine.refusal`, this module answers questions and remembers nothing: two worlds
in the same state produce the same band. The *stored* suspension on ``trade_route`` is a
record of what this answered, written by :func:`atlas.engine.trade.assess_trade` through the
diff pipeline like everything else, so a rewind restores it and a replay re-derives it.

**The flag.** ``ATLAS_TRADE_CONSENT=off`` takes the buyer's voice away again, and it
defaults **on** for the reason :mod:`atlas.engine.refusal` gives for its own: the two have
to be separable to be measured apart, and a priced world in which only one side of a
bargain may decline is a shape nobody should ship by accident. It runs only inside the
priced branch of :func:`~atlas.engine.trade.assess_trade`, so a world with
:mod:`atlas.engine.pricing` down -- which is any world that has asked for barter -- never
reaches it.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field

from . import pricing, refusal
from .trade import GOODS, IMPORT_BUDGET_SHARE, demand_of, supply_ratio

__all__ = [
    "AFFORDABILITY_SLACK",
    "CAPTIVITY_GAIN",
    "CEILING_HEADROOM",
    "DEPENDENCY_GAIN",
    "DWELL_DAYS",
    "ESCALATION_DAYS",
    "HOSTILITY_BITE",
    "NEED_GAIN",
    "RECOVERY_MARGIN_DAYS",
    "ESCALATION_RATIO",
    "WARNING_DAYS",
    "WARNING_RATIO",
    "WAR_URGENCY",
    "MARGIN_ESCALATION",
    "MARGIN_WARNING",
    "Approach",
    "Band",
    "Ceiling",
    "Driver",
    "Headroom",
    "approach",
    "band",
    "drift_per_day",
    "briefing_lines",
    "buyer_maximum",
    "enabled",
    "explain",
    "headroom",
    "headroom_lines",
    "seller_minimum",
    "set_enabled",
    "turn_and_span",
    "warning_line",
    "what_you_could_do",
    "why_it_stopped",
]


def _flag_from_env() -> bool:
    """``ATLAS_TRADE_CONSENT=off`` leaves the buyer paying whatever it is asked.

    **Default on**, and the parse is inverted for the reason :mod:`atlas.engine.refusal`
    gives: the negative reading is the one that has to work, so a typo leaves the layer on
    rather than quietly shipping a different game. Read once, at import, because a
    coefficient that could change between two turns of the same game breaks replay. Tests
    move it through :func:`set_enabled`.
    """
    raw = os.environ.get("ATLAS_TRADE_CONSENT", "on").strip().lower()
    return raw not in {"0", "off", "false", "no"}


_ENABLED: bool = _flag_from_env()


def enabled() -> bool:
    return _ENABLED


def set_enabled(value: bool) -> bool:
    """Move the flag, returning what it was."""
    global _ENABLED
    was, _ENABLED = _ENABLED, bool(value)
    return was


#: How far above what the terms say is fair a buyer will still pay, because a shipment that
#: arrives is worth more than a good price.
#:
#: **This is the slack that keeps ordinary routes off a knife edge, and it is why it is a
#: constant rather than nothing.** Without it the ceiling and the price are two products of
#: overlapping terms that land within a few percent of each other on an ordinary route, and
#: a stockpile breathing by one unit would flip a route between shipping and suspended every
#: month.
#:
#: **Measured, in `scratchpad/consent_shape.py`.** At 1.25 the median seeded route sits at
#: **0.58 of its buyer's ceiling** in all three eras (0.577 / 0.575 / 0.574 under `large`),
#: and the distribution is so tightly clustered that the *ninetieth percentile is the same
#: number* -- the board is a dense band of ordinary routes a long way under the line, with a
#: thin tail above it. A 42% margin on a distribution that flat is not a knife edge.
#:
#: `scratchpad/consent_sensitivity.py` puts 1.5 at a 0.48 median and costs three of the
#: fourteen routes the layer stops across the six measured arms. 1.25 is kept because the
#: margin at 1.25 is already wide enough that nothing but hostility crosses it -- see
#: :data:`HOSTILITY_BITE` -- and widening it further only removes pairs the layer should
#: reach.
CEILING_HEADROOM = 1.25

#: What being short of the good is worth to a buyer's willingness. Slightly above the
#: price's own scarcity gain of 1.1, and the difference is not a thumb on the scale:
#: scarcity is a *difference* -- the buyer's shortage against the seller's -- while this is
#: the buyer's own shortage alone. Where the seller is comfortable the two readings coincide
#: and the gap is the deliberate 0.2 of give a bargainer has.
NEED_GAIN = 1.3

#: What being structurally unable to make the thing is worth on top. A country that produces
#: none of a good concedes 50% more than one that produces most of what it burns and is
#: topping up.
#:
#: **Kept well below :data:`NEED_GAIN` on purpose.** Dependency is the slow fact and need is
#: the fast one; a buyer that has built a stockpile has genuinely improved its position for
#: this negotiation even though its mines have not moved, and weighting the structural term
#: above the immediate one would say the opposite.
DEPENDENCY_GAIN = 0.5

#: What having nowhere else to go is worth. **Exactly the pricing layer's squeeze gain, and
#: the equality is load-bearing** -- see the module docstring. The price's squeeze term and
#: the ceiling's captivity term read the same alternatives count on the same curve, so they
#: move together and a monopoly on its own can never push a price through a ceiling. A buyer
#: with no alternatives pays a monopolist's price because it *is* captive, which is the fact
#: the pricing layer exists to model; it does not additionally get to refuse.
CAPTIVITY_GAIN = 0.8

#: What a buyer at war will pay on top. **The same 0.35 the price charges it**, for the
#: reason the captivity gain matches the squeeze gain: a country at war needs the oil more
#: than it needs the money, everybody selling to it knows, and it should not be able to
#: invoke its own war as a reason to refuse the premium its war caused.
WAR_URGENCY = 0.35

#: How much a total collapse in relations takes *off* what a buyer will pay.
#:
#: **This is the whole mechanic and it is the one number here that is a decision.** The
#: pricing layer's pressure term multiplies a cargo by up to 2.6 as relations fail; this
#: cuts what the buyer will pay by up to half over the same span. The two together mean a
#: deteriorating relationship ends a trade at around a relation score of -33 -- roughly half
#: way to the -64 where the price alone would have reached
#: :data:`atlas.engine.pricing.EMBARGO_PRICE` -- and ends it as a decision the buyer made
#: and can be told about, rather than as a threshold the engine crossed.
#:
#: **It sits at the top of a plateau rather than on a cliff.**
#: `scratchpad/consent_sensitivity.py` runs 0.35, 0.5 and 0.65 against all three eras and
#: both spread modes: **0.5 and 0.65 give the identical board in five of the six arms** and
#: differ by one route in the sixth, so the setting is not balanced on an edge and a pack
#: edit that shifts a relation by a few points will not swing it. 0.35 is below the plateau
#: -- it loses about half of what the layer reaches in cold-war and modern-day -- which is
#: the direction that matters, because the cost of this constant is entirely in what it
#: fails to stop.
#:
#: And what it reaches is legible rather than statistical. Every route it stops on turn one
#: is a pair a player would name unprompted: the United States and Cuba, North Vietnam, East
#: Germany and North Korea in the cold war, the Soviet Union and Britain, Russia and Germany
#: and Poland, China and Japan over rare earths, China and the United States, and India and
#: Pakistan in 2026. **None of them was stopped before**: the price-only layer embargoed
#: zero routes in every era, so this is the whole of the difference between a pressure term
#: that costs money and one that ends a trade.
#:
#: **Gathering Storm stops nothing at this setting, and that is a finding rather than a
#: fault.** Its sharpest pairs sit at hostility 0.25 to 0.50 while being 71-96% dependent on
#: the supplier they dislike, so they pay up -- which is what a country that cannot make its
#: own oil does. The 1936 story this looks like it should produce, the American embargo of
#: Japan, was a *seller* refusing to sell; nothing in this engine can yet express that, and
#: the branch report records why.
HOSTILITY_BITE = 0.5

#: How far past what it can comfortably afford a buyer is allowed to go before the purse
#: itself becomes the ceiling: the price at which its monthly import allowance would buy
#: this many months of the good its industry actually burns.
#:
#: **A wide backstop, not a budget line, and the width is the point.**
#: :func:`atlas.engine.trade.assess_trade` already handles the ordinary case of a buyer that
#: cannot pay for everything: it ships home what it can afford and leaves the rest, which is
#: a partial cargo and the honest outcome. Suspending the *route* is a much larger event and
#: is reserved for a buyer that could not be a customer at this price at all.
#:
#: **Weighed against demand, deliberately, and not against the order book.** The first
#: draft divided the allowance by everything the buyer had contracted for, and that is a
#: quantity judgement wearing a price judgement's clothes: it made a large standing order a
#: reason to refuse a small one, and it suspended the route in
#: `test_a_buyer_never_spends_more_than_its_import_budget` -- where a buyer holding 40 had
#: contracted for 150 months of its own demand -- instead of letting the import cap ship it
#: what it could pay for. Willingness per unit is a fact about a treasury and a country's
#: needs. How much of a large contract it can actually lift is the cap's question, and the
#: cap already answers it well.
AFFORDABILITY_SLACK = 3.0

#: The stored price is rounded and the ceiling is a product of floats. The same trap
#: :mod:`atlas.engine.refusal` records, avoided the same way.
_PRICE_EPSILON = 1e-6

#: The smallest the hostility term is allowed to be when it is divided back out of a
#: ceiling. :data:`HOSTILITY_BITE` is 0.5 and `hostile` is clamped to 1.0, so the true floor
#: is 0.5 and this never binds -- it is here so that raising the bite above 1.0 some day
#: turns a wrong answer into a bounded one rather than a division by zero.
_HOSTILITY_FLOOR = 1e-3

#: Where "badly short", "produces none of its own" and "has somewhere else to go" start, for
#: the sentences alone. Nothing is computed from these -- they decide only whether a reading
#: is worth a line in a briefing, and a reading that moved the ceiling by a rounding error
#: should not claim credit for it.
_NEED_WORTH_SAYING = 0.5
_DEPENDENCY_WORTH_SAYING = 0.8
_HOSTILITY_WORTH_SAYING = 0.25
_ALTERNATIVES_WORTH_SAYING = 2


@dataclass(frozen=True, slots=True)
class Ceiling:
    """The most this buyer will pay for this cargo, and what each reading contributed."""

    #: The answer, per unit per month: what this buyer will pay, purse included.
    price: float
    #: The same answer with the purse left out -- what it would pay if money were no
    #: object. **This, not :attr:`price`, is what suspends a standing route**; see
    #: :data:`AFFORDABILITY_SLACK`.
    willing: float = 0.0
    #: How short of the good the buyer is now, 0.0..1.0.
    need: float = 0.0
    #: How much of its monthly demand its own ground cannot cover, 0.0..1.0.
    dependency: float = 0.0
    #: How many other sellers could reach it.
    alternatives: int = 0
    #: What its purse alone would allow, or ``0.0`` when it has no imports to weigh.
    affordable: float = 0.0
    #: What this buyer would pay if it thought nothing in particular of its supplier --
    #: :attr:`willing` with the hostility term left out. Carried because it is the one
    #: quantity :func:`approach` needs and the only one that cannot be recovered from the
    #: others: dividing :attr:`willing` back out by the bite loses a digit at every rounding
    #: and the answer is a *regard* printed to a player, which must agree with the engine's
    #: own decision to the point.
    willing_at_ease: float = 0.0
    #: **The buyer's** regard for the seller, as :func:`atlas.engine.pricing.hostility`
    #: reads it: 0.0 at ordinary terms and 1.0 at the worst. Carried rather than left to be
    #: recovered from :attr:`because`, so :func:`what_you_could_do` can name the ledger this
    #: ceiling was actually cut by without matching on the wording of a sentence.
    hostile: float = 0.0
    #: Whether the purse, rather than willingness, is what set the ceiling.
    purse_bound: bool = False
    because: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class Band:
    """The interval both governments would accept, and where the standing price sits in it.

    ``ended_by`` is ``""`` while the cargo moves, and names the side that stopped it
    otherwise. It is recorded rather than recomputed from the three floats for the reason
    :attr:`atlas.engine.pricing.Terms.embargoed` is: a comparison between a rounded column
    and a product of floats one step apart is a coin toss, and the first version of that
    mistake shipped a cargo that had been priced out.
    """

    #: What the terms actually stand at.
    price: float
    #: The least the seller will ship for. ``0.0`` for a seller in no position to refuse.
    seller_min: float
    #: The most the buyer will pay.
    buyer_max: float
    #: ``""``, ``"seller"`` or ``"buyer"``.
    ended_by: str = ""
    because: tuple[str, ...] = field(default_factory=tuple)
    #: The seller's own answer, when it is the one that stopped. Carried so the caller can
    #: keep reporting a withdrawal in the words :mod:`atlas.engine.refusal` already chose.
    withdrawal: refusal.Withdrawal | None = None
    #: And the buyer's, for the same reason and for the briefing.
    ceiling: Ceiling | None = None

    @property
    def overlaps(self) -> bool:
        """Whether the cargo moves. Not ``seller_min <= price <= buyer_max`` recomputed --
        see the class docstring."""
        return not self.ended_by


def _produced(conn: sqlite3.Connection, world_id: str, nation_id: str, good: str) -> float:
    row = conn.execute(
        "SELECT produced FROM resource WHERE world_id = ? AND nation_id = ? AND good = ?",
        (world_id, nation_id, good),
    ).fetchone()
    return 0.0 if row is None else float(row["produced"] or 0.0)


def _treasury(conn: sqlite3.Connection, world_id: str, nation_id: str) -> float:
    row = conn.execute(
        "SELECT treasury FROM nation WHERE world_id = ? AND id = ?", (world_id, nation_id)
    ).fetchone()
    return 0.0 if row is None else float(row["treasury"] or 0.0)


def buyer_maximum(
    conn: sqlite3.Connection, world_id: str, seller: str, buyer: str, good: str,
    *, lanes: pricing.Lanes | None = None, alternatives_cache: dict | None = None,
) -> Ceiling:
    """The most this buyer will pay for this cargo, per unit per month.

    Symmetric to :data:`atlas.engine.refusal.RESERVE_PRICE` in the role it plays and not in
    its shape, because the two sides of a bargain are not shaped alike: a seller's floor is
    a near-constant that hostility raises, while a buyer's ceiling is mostly a statement
    about how much trouble the buyer is in. See the module docstring for the four readings.

    Nothing here reads the route table: every one of the four readings is a fact about the
    buyer, so the answer does not depend on which route is being asked about and therefore
    not on the order routes are settled in.
    """
    need = max(0.0, 1.0 - supply_ratio(conn, world_id, buyer, good))
    demand = demand_of(conn, buyer)
    own = _produced(conn, world_id, buyer, good)
    dependency = 1.0 if demand <= 0 else max(0.0, min(1.0, 1.0 - own / demand))
    alternatives = pricing.alternatives(
        conn, world_id, buyer, seller, good,
        lanes=lanes, alternatives_cache=alternatives_cache,
    )
    # **The buyer's regard for the seller, and that direction is the whole point.**
    # `relation` is directed -- `relation(a, b).score` is *a's* regard for *b*, the reading
    # `scoring.world_regard` and every engine-authored regard write already agree on -- and
    # this line read `(seller, buyer)` until it was measured: the most a government would
    # pay for its oil was cut by *its supplier's* opinion of *it*, while its own opinion of
    # the supplier moved the ceiling by nothing at all, anywhere in the +-100 range, on
    # every lane swept. The reason the layer printed for that, "relations are bad enough
    # that it would rather do without", attributes a sentiment to the buyer and computed it
    # from the seller's ledger.
    #
    # The swap, not `max(both)`. A ceiling is a statement about what *this* government will
    # accept, and every other reading behind it -- need, dependency, alternatives, the purse
    # -- is a fact about the buyer; a term that let the seller's mood lower the buyer's
    # willingness would be the only one that was not. `max(both)` -- "a bargain fails if
    # either side has soured" -- describes a real thing, but the engine already has the
    # seller's half of it in `seller_minimum`, which reads `(seller, buyer)` for the
    # defensible reason that a seller holds out longer against a government it dislikes.
    # Reading the seller's regard on both sides of the band would be counting one ledger
    # twice and leaving the other unread, which is the defect, not the fix.
    #
    # `pricing.price_route` keeps `(seller, buyer)`: a seller *charges* more to a government
    # it dislikes. So the two curves now move on two ledgers, which is why
    # `what_you_could_do` names them apart.
    hostile = pricing.hostility(conn, world_id, buyer, seller)

    # **The stronger of the two readings, not the momentary one alone.** `need` is this
    # month's store and `dependency` is what the country's own ground can cover; they are
    # two readings of one pressure on two clocks, and a buyer concedes whichever is worse.
    #
    # Taking the max rather than the live value is what stops the ceiling oscillating with
    # the stockpile -- and the oscillation was not cosmetic. A route that delivers fills the
    # buyer's store, which drops `need` to zero, which drops the ceiling under a price that
    # was struck when the buyer was desperate: **the route suspends itself for working.**
    # `test_the_same_route_is_reconsidered_at_the_same_rate_in_any_length_of_turn` caught
    # the tail of it, where weekly turns made the store breathe fast enough to force two
    # extra renegotiations in two years and the six-month cadence became turn-length
    # dependent.
    #
    # It is also the better reading. A country that produces none of a good is short of it
    # in the only sense that lasts, and a full warehouse is not an argument against that --
    # the warehouse is full *because of the route being negotiated*.
    pressed = max(need, dependency)
    because: list[str] = []
    willing = (
        pricing.BASE_PRICE
        * CEILING_HEADROOM
        * (1.0 + NEED_GAIN * pressed)
        * (1.0 + DEPENDENCY_GAIN * dependency)
        * (1.0 + CAPTIVITY_GAIN / (1.0 + alternatives))
    )
    if pricing.at_war_with_anybody(conn, world_id, buyer):
        willing *= 1.0 + WAR_URGENCY
        because.append("it is at war and will pay for what its factories need")
    if need > _NEED_WORTH_SAYING:
        because.append(f"it is badly short of {good}")
    if dependency > _DEPENDENCY_WORTH_SAYING:
        because.append(f"it produces almost no {good} of its own")
    if alternatives == 0:
        because.append("it has no other supplier it can reach")
    elif alternatives >= _ALTERNATIVES_WORTH_SAYING:
        because.append("it has other suppliers and need not accept these terms")
    # Applied whatever the reading; only *said* when it is worth saying, which is the same
    # rule `atlas.engine.pricing.price_route` keeps for its own reasons.
    willing *= max(0.0, 1.0 - HOSTILITY_BITE * hostile)
    if hostile > _HOSTILITY_WORTH_SAYING:
        because.append(
            "it thinks badly enough of this supplier that it would rather do without")

    unbounded = willing
    at_ease = unbounded / max(_HOSTILITY_FLOOR, 1.0 - HOSTILITY_BITE * hostile)
    affordable = 0.0
    purse_bound = False
    if demand > 0:
        allowance = max(0.0, _treasury(conn, world_id, buyer)) * IMPORT_BUDGET_SHARE
        affordable = AFFORDABILITY_SLACK * allowance / demand
        if affordable < willing:
            willing = affordable
            purse_bound = True
            because.append("its treasury will not stretch to imports at this price")

    return Ceiling(
        price=round(willing, 4),
        willing=round(unbounded, 4),
        need=round(need, 4),
        dependency=round(dependency, 4),
        alternatives=alternatives,
        affordable=round(affordable, 4),
        willing_at_ease=round(at_ease, 4),
        hostile=round(hostile, 4),
        purse_bound=purse_bound,
        because=tuple(because),
    )


def seller_minimum(
    conn: sqlite3.Connection, world_id: str, seller: str, buyer: str,
    *, lanes: pricing.Lanes | None = None, distress_cache: dict | None = None,
) -> float:
    """The least this seller will ship for, ``0.0`` when it is in no position to refuse.

    **Not a second definition of the reserve.** The number and both of its conditions are
    :mod:`atlas.engine.refusal`'s -- the hostility-raised reserve, and the distress floor
    below which a seller with nowhere else to put the cargo takes what it is offered. This
    only expresses them as the bottom of an interval, so that the two halves of a bargain
    are one object a briefing can print.
    """
    if not refusal.enabled():
        return 0.0
    key = (seller, buyer)
    if distress_cache is None:
        distress = pricing.seller_distress(conn, world_id, seller, buyer, lanes)
    else:
        if key not in distress_cache:
            distress_cache[key] = pricing.seller_distress(
                conn, world_id, seller, buyer, lanes)
        distress = distress_cache[key]
    if distress < refusal.DISTRESS_FLOOR:
        return 0.0
    hostile = pricing.hostility(conn, world_id, seller, buyer)
    return refusal.RESERVE_PRICE * (1.0 + refusal.RESERVE_HOSTILITY_GAIN * hostile)


def band(  # noqa: PLR0913 - the caller has already read all of this
    conn: sqlite3.Connection, world_id: str, seller: str, buyer: str, good: str,
    unit_price: float, *, shipped: float = 1.0,
    lanes: pricing.Lanes | None = None, alternatives_cache: dict | None = None,
    distress_cache: dict | None = None,
) -> Band:
    """Where the standing price sits between what the two of them will accept.

    Derived from the *standing* price rather than from fresh
    :class:`~atlas.engine.pricing.Terms`, for the reason
    :func:`atlas.engine.refusal.seller_declines` gives: terms are only reconsidered on a
    stagger, and a consent test keyed to a repricing would suspend one turn in six and ship
    the other five.

    **The seller is asked first, and it is asked in its own words.** The seller's half of
    this band is not reimplemented here -- it is :func:`atlas.engine.refusal.seller_declines`
    called and carried, so that a withdrawal keeps the reasons, the report line and the
    behaviour it already had, and this module adds the buyer's half rather than replacing
    the seller's.

    ``shipped`` guards only the seller's side, because
    :mod:`atlas.engine.refusal` uses it to avoid announcing that a seller with nothing in
    the ground "withdrew" -- a choice nobody made. A buyer's ceiling is a statement about a
    price and is true whether or not there is any cargo.
    """
    if unit_price <= 0:
        # An unpriced route is a barter route, and barter has no terms to disagree about.
        return Band(price=unit_price, seller_min=0.0, buyer_max=0.0)

    withdrawal = refusal.seller_declines(
        conn, world_id, seller, buyer, good, unit_price,
        lanes=lanes, distress_cache=distress_cache,
    ) if shipped > 0 else None
    seller_min = seller_minimum(
        conn, world_id, seller, buyer, lanes=lanes, distress_cache=distress_cache)
    if withdrawal is not None:
        return Band(
            price=round(unit_price, 4), seller_min=round(seller_min, 4),
            buyer_max=0.0, ended_by="seller", because=withdrawal.because,
            withdrawal=withdrawal,
        )

    if not enabled():
        # **The flag takes the buyer's voice away and leaves the seller's.** It has to be
        # here rather than at the top: with this module off, a route is still subject to
        # `atlas.engine.refusal` exactly as it was before any of this existed, and a guard
        # that returned early would have quietly repealed the seller's reserve as a side
        # effect of switching off the buyer's ceiling.
        return Band(price=round(unit_price, 4), seller_min=round(seller_min, 4),
                    buyer_max=0.0)

    ceiling = buyer_maximum(
        conn, world_id, seller, buyer, good,
        lanes=lanes, alternatives_cache=alternatives_cache,
    )
    # **Weighed against willingness, not against the purse, and the difference is a
    # deliberate line between two kinds of decision.** Affordability belongs in the ceiling
    # -- a briefing should say when a treasury cannot carry a bill, and
    # :mod:`atlas.engine.intent` must not open a route a government cannot pay for. But a
    # purse is a *stock* that drains inside a turn, and gating a standing shipment on it
    # makes the outcome depend on how the world time was sliced: twelve monthly calls
    # stopped buying with 0.31 left where one yearly call ran the same purse down to 0.01,
    # which `test_the_same_world_time_spends_the_same_money_however_it_is_divided` pins as
    # a violation of the rule `atlas.engine.pacing` exists to keep.
    #
    # And it is unnecessary. :func:`atlas.engine.trade.assess_trade` already answers "it
    # cannot pay for all of this" the honest way -- it ships home what the import budget
    # covers and leaves the rest, which is a *rate* and therefore survives being divided.
    # So: affordability decides whether a government takes on a new commitment; it does not
    # tear up one it already has.
    if unit_price > ceiling.willing + _PRICE_EPSILON:
        because = (
            f"the terms are above what the buyer will pay for {good}", *ceiling.because)
        return Band(
            price=round(unit_price, 4), seller_min=round(seller_min, 4),
            buyer_max=ceiling.willing, ended_by="buyer", because=because, ceiling=ceiling,
        )
    return Band(
        price=round(unit_price, 4), seller_min=round(seller_min, 4),
        buyer_max=ceiling.price, ceiling=ceiling,
    )


#: How much notice the warning is meant to give, in **days of world time**.
#:
#: One repricing interval. :data:`atlas.engine.trade.REPRICE_EVERY_MONTHS` is six months of
#: world time and the stored price stands between those, so a lane that is going to stop
#: before its terms are next reopened will do it as a *step* -- flat for six months and then
#: a jump of at least :data:`atlas.engine.pricing.REPRICE_DEADBAND` straight through the
#: crossing. Measured over twenty arms of turn length x deterioration rate, the stored ratio
#: the turn before the stop and the ratio at the stop were 0.98 and 1.22, 0.69 and 1.39,
#: 0.60 and 1.07: in **every** arm the lane went from below any plausible band to stopped in
#: one step. So the warning cannot be a band on the price. It has to be sized to the clock
#: that makes the price a staircase.
WARNING_DAYS = 180.0

#: And when it stops being a warning and becomes a deadline: this lane stops before its
#: terms are next reopened. Two reference months, the same duration
#: :data:`RECOVERY_MARGIN_DAYS` gives a lane climbing back out.
ESCALATION_DAYS = 60.0

#: How far a suspended lane's regard has to recover past the crossing before it reopens, in
#: **days** of the engine's own drift -- and it is a duration rather than a count of points
#: for exactly the reason :data:`atlas.engine.mediation.MEDIATION_COOLDOWN_DAYS` is one.
#:
#: Sixty days is about five points at the drift below, and five is the smallest integer
#: above both numbers that matter: **2.56**, one turn of concert's regard drift near the
#: crossing, below which a relationship hovering on the threshold flips every single turn
#: (31 flips in 31 turns, measured, collapsing to 1 the moment the margin clears the drift
#: step); and **4.3**, :data:`atlas.engine.pricing.REPRICE_DEADBAND` converted into points
#: of regard at the 2.8%-per-point slope, below which a stale stored price can reopen a lane
#: that the next repricing immediately re-stops.
RECOVERY_MARGIN_DAYS = 60.0

#: And how long a suspension stands before it may lift at all, whatever the regard says.
#:
#: **The margin alone is a position, and a long turn crosses a position in one step.**
#: :func:`atlas.engine.trade.assess_trade` also runs once per *round* -- the turn engine
#: calls it inside ``for _round in range(round_count)`` with the turn fixed and the date
#: advancing -- so a 365-day turn re-tests the band twelve times against a relationship that
#: model-authored diffs rewrite once, and the stockpile channel behind
#: ``pressed = max(need, dependency)`` is free to flap it between rounds. A dwell is the only
#: clause that binds there. At 28-day turns it is about two turns; at 365-day turns it is
#: satisfied at the first turn boundary, which is correct -- a year has passed and of course
#: the file reopens.
DWELL_DAYS = 60.0

#: The second trigger, and it is second on purpose: how close the price may get to the
#: ceiling before the lane is worth a warning whatever the regard is doing.
#:
#: **The headroom trigger is one-dimensional and this is the other dimension.** A crossing is
#: solved for the hostility in the *buyer's* ceiling with the price held at today's terms --
#: so it answers "how far can my regard for this supplier fall", and it is blind to a lane
#: that stops because the *price* moved instead: the seller's regard for the buyer, a
#: shortage, a seller's distress. Measured over a 40-turn stressed run of all three eras,
#: that blindness was most of what was left: the suspensions with no warning in front of them
#: were lanes whose crossing was not on the buyer's axis at all, or whose price jumped past a
#: regard that had 61 points in hand the turn before.
#:
#: **0.90, and it is nearly silent on a healthy world.** Across all 1043 seeded routes in the
#: three eras the price/ceiling ratio clusters at 0.577 with a 90th percentile of 0.577; the
#: only lanes at or above 0.90 are the 9 that have already stopped. So this fires on a lane
#: in trouble and on essentially nothing else. It is second rather than first because a ratio
#: is a poor proxy for *time* -- no band on it clears a repricing interval in even half the
#: arms, which is the whole reason the headroom trigger exists -- but a warning on the turn
#: before is better than the nothing this case had.
WARNING_RATIO = 0.90

#: And the ratio at which it is a deadline rather than a warning: within about one point of
#: regard of the crossing, at the 2.8%-per-point slope the diagnosis measured.
ESCALATION_RATIO = 0.97

#: The floor under the drift used to convert points of regard into days, per reference
#: month. Concert's drift is ``(REGARD_CEILING - score) * REGARD_RATE``, which is fastest far
#: below the ceiling and would be zero at it; a lane whose crossing sits at or above +45 has
#: no drift toward it and the conversion would divide by nothing.
_MIN_DRIFT_PER_MONTH = 0.05


def drift_per_day(score: float) -> float:
    """How fast regard moves on its own at ``score``, in points per day of world time.

    **The engine's own fastest *continuous* mover, and very nearly the only one.**
    :data:`atlas.engine.concert.REGARD_RATE` closes the gap to
    :data:`~atlas.engine.concert.REGARD_CEILING` every turn for every pair in a concert;
    everything else that writes ``relation.score`` -- :mod:`atlas.engine.answering`,
    :mod:`atlas.engine.embargo`, :mod:`atlas.engine.treaties`,
    :mod:`atlas.engine.exposure`, a diplomacy model -- is a discrete event of -45 to +15 that
    no threshold can anticipate. Measured over 24 turns of a free deterministic game, regard
    moved twice in 8304 pair-turns, so the drift is not merely the fastest continuous mover
    but nearly the only movement there is.

    **A yardstick, not a forecast.** It converts a distance in points into a duration so the
    warning can be stated in the units the repricing clock is measured in. It does not
    predict which way a relationship will go, and the briefing says "at the rate relations
    drift" rather than claiming to know.
    """
    from .concert import REGARD_CEILING, REGARD_RATE  # local: concert reads pacing, not this
    from .pacing import REFERENCE_DAYS

    per_month = max(_MIN_DRIFT_PER_MONTH, (REGARD_CEILING - score) * REGARD_RATE)
    return per_month / REFERENCE_DAYS


@dataclass(frozen=True, slots=True)
class Approach:
    """How close a shipping lane is to the regard at which the buyer stops taking it.

    **The transition this exists to announce is a sign test on a smooth quantity.**
    :attr:`Band.overlaps` is ``not ended_by``: a lane at a price/ceiling ratio of 0.997 and
    one at 1.024 are, to every consumer of that object, "shipping" and "stopped", and nothing
    carries the ratio. Over the sixty-point window in which hostility moves at all, that
    ratio travels 2.8% per point of regard -- so one point decides whether a nation's oil
    arrives, and in a seeded world 59-72% of all priced routes cross inside the same
    one-point bin, because the whole captivity/squeeze half of the calculation cancels by
    construction and the only free variable left is a dependency the seeder hands out at
    exactly 0.20.

    Nothing here spreads that cohort, and spreading it is not this object's job: moving the
    crossing moves all of them together, and the constants that would move it were calibrated
    against the pairs they stop. What this does is make the crossing **legible ahead of
    time**, in the units a player acts in.
    """

    #: How much of what the buyer will pay the price is already asking for. Always present,
    #: because it is the reading that survives when the crossing does not exist.
    ratio: float
    #: What the buyer's regard for the seller is today.
    regard: float
    #: The regard at which this lane stops, on :attr:`whose` ledger -- or ``None`` when no
    #: regard in :func:`~atlas.engine.pricing.hostility`'s live window closes it. **Not a
    #: rare case**: after the direction fix the price and the ceiling move on two ledgers, so
    #: 17-31% of seeded routes cannot be closed by the buyer's opinion alone, and printing a
    #: clamped -70 for those would be naming a number that decides nothing.
    threshold: float | None = None
    #: :attr:`regard` minus :attr:`threshold`, or ``None`` with the threshold.
    headroom: float | None = None
    #: :attr:`headroom` converted to days at :func:`drift_per_day`. ``0.0`` when the lane is
    #: already past its crossing, ``None`` when there is no crossing.
    days: float | None = None
    #: Which ledger the threshold is read off, in words, because the whole point of the
    #: direction fix in :func:`buyer_maximum` is that it is not the obvious one.
    whose: str = "the buyer's regard for the seller"

    @property
    def on_the_regard_axis(self) -> bool:
        """Whether the buyer's own opinion can close this lane at all."""
        return self.threshold is not None

    @property
    def warned(self) -> bool:
        """Whether this lane is worth a line. Either trigger, and they answer two different
        questions -- see :data:`WARNING_DAYS` and :data:`WARNING_RATIO`."""
        near_in_time = self.days is not None and 0.0 <= self.days < WARNING_DAYS
        return near_in_time or self.ratio >= WARNING_RATIO

    @property
    def urgent(self) -> bool:
        """Whether it stops before its terms are next reopened."""
        near_in_time = self.days is not None and 0.0 <= self.days < ESCALATION_DAYS
        return near_in_time or self.ratio >= ESCALATION_RATIO


def approach(  # noqa: PLR0913 - a crossing is a fact about all of this
    conn: sqlite3.Connection, world_id: str, seller: str, buyer: str, good: str,
    *, standing_price: float = 0.0,
    lanes: pricing.Lanes | None = None, alternatives_cache: dict | None = None,
) -> Approach | None:
    """How much regard this lane has in hand before the buyer stops taking it.

    ``None`` when the question does not arise: the layer is off, the route is barter, or the
    ceiling sits high enough above the price that no regard in the live window closes it.

    **Off the fresh terms, not the stored price, and that is the design.**
    :func:`atlas.engine.pricing.price_route` is already called on every briefing for the
    "Why:" line, so the number is in hand; the stored price is frozen between repricings and
    is the direct cause of the one-step jumps the warning exists to get ahead of. Reading
    fresh beat reading stored in every arm measured -- 16 of 20 lit at all against 13, and 7
    clearing a full repricing interval against 5.

    **Both are solved, and the more conservative wins.** The standing price gives the
    threshold as the column reads today; the fresh price gives the one the ``renegotiable``
    path will actually test. They differ by up to the deadband -- 4.3 points of regard -- and
    printing the *higher* threshold is printing the earlier warning.

    **The crossing, in closed form.** Everything in the ceiling that moves with the
    relationship is one factor, so setting the price equal to the ceiling and solving for the
    hostility is exact rather than a search::

        willing(h) = willing_at_ease * (1 - HOSTILITY_BITE * h)
        h*         = (1 - price / willing_at_ease) / HOSTILITY_BITE
        s*         = pricing.regard_at_hostility(h*)
    """
    if not enabled() or not pricing.enabled():
        return None
    ceiling = buyer_maximum(
        conn, world_id, seller, buyer, good,
        lanes=lanes, alternatives_cache=alternatives_cache,
    )
    at_ease = ceiling.willing_at_ease
    if at_ease <= 0.0:
        return None
    fresh = pricing.price_route(
        conn, world_id, seller, buyer, good,
        lanes=lanes, alternatives_cache=alternatives_cache,
    ).unit_price
    prices = [p for p in (fresh, standing_price) if p > 0.0]
    if not prices:
        return None
    regard = _regard(conn, world_id, buyer, seller)
    # The more conservative reading on both counts: the higher price against what the buyer
    # will actually pay, and the higher of the two thresholds it solves to.
    ratio = max(prices) / max(ceiling.willing, _PRICE_EPSILON)
    thresholds = []
    for price in prices:
        h = (1.0 - price / at_ease) / HOSTILITY_BITE
        if h >= 1.0:
            # Below the bottom of the live window: no regard this world can hold closes this
            # lane on its own. `ratio` still answers, which is the point of carrying it.
            continue
        thresholds.append(pricing.regard_at_hostility(h))
    if not thresholds:
        return Approach(ratio=round(ratio, 4), regard=round(regard, 2))
    threshold = max(thresholds)
    headroom = regard - threshold
    return Approach(
        ratio=round(ratio, 4),
        regard=round(regard, 2),
        threshold=round(threshold, 2),
        headroom=round(headroom, 2),
        days=round(max(0.0, headroom) / drift_per_day(threshold), 1),
    )


def _regard(conn: sqlite3.Connection, world_id: str, a: str, b: str) -> float:
    """``a``'s regard for ``b``. Directed -- one row per ordered pair."""
    row = conn.execute(
        "SELECT score FROM relation WHERE world_id = ? AND nation_a = ? AND nation_b = ?",
        (world_id, a, b),
    ).fetchone()
    return 0.0 if row is None else float(row["score"] or 0.0)


def explain(state: Band, good: str) -> str:
    """One line a player can read, in the shape :func:`atlas.engine.pricing.explain` uses.

    Says *stopped* rather than *cut*, because the ships could sail, the agreement still
    stands, and the cargo will move again the day the two numbers overlap.
    """
    if state.ended_by == "buyer":
        head = (f"the buyer stopped taking {good} at {state.price:.2f} a unit "
                f"-- it will pay {state.buyer_max:.2f}")
    elif state.ended_by == "seller":
        head = (f"the seller stopped shipping {good} at {state.price:.2f} a unit "
                f"-- it wants {state.seller_min:.2f}")
    else:
        return (f"{good} moves at {state.price:.2f} a unit, between the seller's "
                f"{state.seller_min:.2f} and the buyer's {state.buyer_max:.2f}")
    if not state.because:
        return head
    return head + ": " + "; ".join(state.because)


def what_you_could_do(
    state: Band, seller_name: str, good: str, *, alternatives: int | None = None,
    priced_in: bool = False,
) -> tuple[str, ...]:
    """What the government reading this can actually *do* about it.

    **A refusal that only says no is one the staff will make again next turn.** This module
    is not :class:`atlas.engine.causality.Refusal` and is not under that sweep, but the
    argument behind the sweep applies just as hard here: a briefing that reports a price
    rise and no lever teaches a player that the economy is weather.

    Every line names something the engine actually acts on -- diversifying and seeking an
    alternative supplier are :mod:`atlas.engine.intent` intentions, and improving relations
    moves the hostility that both the price and the ceiling are built on.
    """
    doing: list[str] = []
    ceiling = state.ceiling
    # `alternatives` is passed by the briefing, which has priced the route anyway and so
    # holds the count even where `ceiling` is `None` -- see `_route_lines`. `None` means
    # nobody has counted, which is not the same as counting zero and must not print the
    # sentence that says so.
    if alternatives is not None:
        reach = alternatives
    elif ceiling is not None:
        reach = ceiling.alternatives
    else:
        reach = None
    if reach == 0:
        doing.append(
            f"seek {good} from somebody other than {seller_name} -- with no alternative "
            "supplier in reach, the price carries a captive buyer's premium")
    elif reach:
        doing.append(
            f"open a second {good} route: {reach} other "
            f"{'supplier' if reach == 1 else 'suppliers'} can reach you, "
            "and a buyer with somewhere else to go pays less")
    # **Two ledgers, named apart, because the engine reads both and reads them for
    # different numbers.** `relation` is directed, and until the direction bug in
    # `buyer_maximum` was fixed this branch fired off either and offered one lever for both
    # -- "repair relations with X" aimed at whichever row happened to have produced a
    # sentence with the word "relations" in it. A warning that sends a player to move an
    # opinion the engine never reads is worse than none, so each clause now names the row
    # it can actually be closed by:
    #
    # * `pricing.price_route` and `refusal.seller_declines` read `hostility(seller, buyer)`
    #   -- *the seller's* regard for the reader. That is what raises the price asked and
    #   what makes a seller hold out, and the reader cannot set it directly; it has to be
    #   earned.
    # * `buyer_maximum` reads `hostility(buyer, seller)` -- the reader's *own* regard for
    #   its supplier. That is what cuts the ceiling, and it is the reader's own policy.
    #
    # `priced_in` is the price-side reading, passed by the briefing because it has priced
    # the route anyway and `Band` does not carry the seller's hostility on the path where
    # the seller did not refuse.
    seller_side = priced_in or (
        state.withdrawal is not None
        and any("relations" in reason for reason in state.because))
    if seller_side:
        doing.append(
            f"earn back {seller_name}'s regard for you -- its opinion of you is what "
            "raises the price it asks and what makes it hold out, and it is a different "
            "ledger from your opinion of it")
    if ceiling is not None and ceiling.hostile > _HOSTILITY_WORTH_SAYING:
        doing.append(
            f"or mend your own government's standing toward {seller_name}: what you will "
            f"pay for {good} falls with your regard for the supplier, and that is the "
            "ledger this refusal is read off")
    if ceiling is not None and ceiling.purse_bound:
        doing.append("cut what you have agreed to import, or find the money: the treasury "
                     "cannot cover the standing orders")
    if ceiling is not None and ceiling.dependency > _DEPENDENCY_WORTH_SAYING:
        doing.append(f"raise your own {good} production, which is what ends the "
                     "dependency rather than repricing it")
    return tuple(doing)


def warning_line(near: Approach, seller_name: str, good: str) -> str:
    """The warning, in the units a player acts in.

    **The threshold, not the ratio.** "The price is at 85% of what they will pay" is the
    same fact as "you stop taking this oil below -28, you are at -4, and at the rate
    relations drift that is about 200 days" -- and only the second names a number a
    government can steer toward. :func:`what_you_could_do` already lists the levers; this
    gives them a target.

    **And it names the right ledger.** The crossing is on the *buyer's* regard for the
    seller -- see the direction argument in :func:`buyer_maximum` -- which is the reader's
    own, and the sentence says whose it is rather than leaving a player to improve an
    opinion the engine does not read.
    """
    head = "**Stops before these terms are next reopened.**" if near.urgent else "Warning:"
    if near.on_the_regard_axis:
        return (
            f"{head} You stop taking {good} from {seller_name} once your own regard for "
            f"them falls below {near.threshold:.0f}. It is {near.regard:.0f} today -- "
            f"{near.headroom:.0f} points in hand, which is about {near.days:.0f} days at "
            "the rate relations drift on their own."
        )
    # No crossing on the reader's own ledger: what is closing on the ceiling is the price,
    # and saying "improve your regard" here would name a lever that does not reach it.
    return (
        f"{head} {seller_name} is asking {near.ratio:.0%} of the most you will pay for "
        f"{good}. Your own regard for them is not what is moving -- no opinion you could "
        "hold would stop this cargo -- so the lever is the price: what they think of you, "
        "how short the market is, and whether anybody else can reach you."
    )


# ------------------------------------------------------------------ commercial headroom

#: How little room a lane may have before it is worth a line, as a share of the quote being
#: tested. **The general trigger, and it replaces a regard-only one that could not see the
#: cause 96% of measured suspensions actually had.**
#:
#: :func:`approach` solves a crossing on the *buyer's* regard for the seller, which makes it
#: blind three ways at once: it is buyer-side only, it is regard-only, and it is keyed to a
#: boundary that in a played game does not move. Classifying every in-play suspension across
#: the play battery by which input carried it -- `scratchpad/cliff_inputs.py`, and the
#: written-up counts in `scratchpad/CLIFF_DIAGNOSIS.md` -- the answer was not the buyer's
#: ledger at all: **the seller withdrew, because the price fell.** A transient collapse in
#: the seller's own supply drove the price's `scarcity` term to its floor and the quote with
#: it to :data:`atlas.engine.pricing.FLOOR_PRICE`, which is at or under the seller's reserve.
#: The buyer's ceiling was never near it: the price sat at 0.577 of it throughout.
#:
#: So the surface has to be two-sided and cause-agnostic. This is the margin to *whichever*
#: boundary is nearer, and it fires whatever moved -- the quote or either party's line.
#:
#: **0.15, and it is quiet on a healthy world.** The seeded distribution puts an ordinary
#: lane at 0.577 of its buyer's ceiling -- a 42% margin -- and a seller's reserve at a
#: quarter of the quote, so an untroubled route is nowhere near this. Measured over a whole
#: era with `scratchpad/headroom_scan.py`: **0.5-2% of live lanes warned on a quiet turn**,
#: rising to 23.5% on the turn before the stops arrive -- the shape a warning is supposed to
#: have, and the reason this is a share of the quote rather than a price.
MARGIN_WARNING = 0.15

#: And the margin at which it is a deadline rather than a warning.
MARGIN_ESCALATION = 0.05


@dataclass(frozen=True, slots=True)
class Driver:
    """The one input holding this lane nearest a boundary, and what it is worth in money.

    **Named by counterfactual, not by narration.** Every other "why" in this layer is a
    sentence appended when a reading crosses a saying-threshold, which tells a player what is
    *true* of a lane rather than what is *deciding* it -- three such sentences fire on an
    ordinary route and none of them is the one that matters. This asks a sharper question of
    each input in turn: put this factor back to neutral, leave every other exactly as it is,
    and how much headroom comes back? The input that returns the most is the one currently
    eating it, and :attr:`worth` is the answer in the units the price is quoted in.

    That is also what makes the suggested response honest. A lever is worth offering when
    moving it would move the outcome, and :attr:`worth` is the size of the move.
    """

    #: A short key: ``scarcity``, ``squeeze``, ``distress``, ``seller_regard``, ``war``,
    #: ``shortage``, ``dependency``, ``alternatives``, ``regard``, ``reserve``.
    name: str
    #: Which quantity it moves: ``price``, ``buyer`` or ``seller``.
    side: str
    #: Headroom, in price units, that returning it to neutral would restore.
    worth: float
    #: The sentence naming it.
    said: str
    #: And what the government reading this could actually do about that one input.
    lever: str = ""


@dataclass(frozen=True, slots=True)
class Headroom:
    """How much room a standing lane has before *either* government walks away from it.

    Everything here derives; nothing is stored and nothing is written. Two worlds in the same
    state answer identically, which is the property that lets this be added to a briefing
    without moving a fingerprint -- see the module docstring on pure derivation.

    **The margin is measured against the quote that will actually be tested**, which is the
    higher of the standing price and the fresh one, for the same reason :func:`approach`
    takes the more conservative of the two: the standing column is frozen between repricings,
    and the fresh quote is what the ``renegotiable`` path in
    :func:`atlas.engine.trade.assess_trade` puts to both parties. Measuring the margin off
    the frozen column would be printing the reassurance the staircase is made of.
    """

    #: What the contract stands at -- the column a player has been reading all along.
    price: float
    #: And what it would be if the file were reopened today. The two differ by up to six
    #: months of world movement, and that difference is the step this surface announces.
    repriced: float
    #: The one the margins are measured against. See the class docstring.
    tested: float
    #: The most the buyer will pay before it stops taking the cargo.
    buyer_max: float
    #: What its treasury will carry -- a different and softer limit, because it decides how
    #: much arrives rather than whether the lane stands. ``0.0`` when there is nothing to
    #: weigh. See :attr:`purse_bound`.
    buyer_purse: float
    #: Whether the purse is the tighter of the two.
    purse_bound: bool
    #: The least the seller will ship for. ``0.0`` for a seller in no position to refuse.
    seller_min: float
    #: Margin to the buyer's line, in price units. Negative once past it.
    to_buyer: float
    #: And to the seller's -- ``None`` for a seller with no reserve, which is not a wide
    #: margin but an absent boundary, and printing a number for it would invent one.
    to_seller: float | None
    #: Which line is nearer -- ``buyer``, ``seller``, or ``""`` when neither can be reached.
    binding: str
    #: The nearer margin as a share of :attr:`tested`.
    margin: float
    #: When the file next reopens, and how far off that is in days of world time.
    next_reprice_turn: int
    next_reprice_days: float
    #: The input currently eating the margin. ``None`` only when nothing moves it.
    driver: Driver | None = None
    #: The regard crossing, where the buyer's own ledger can close this lane at all. Carried
    #: rather than folded in, because it is the one boundary that can be stated as a *date*,
    #: and a player steering a relationship wants the date.
    regard: Approach | None = None

    @property
    def step(self) -> float:
        """The move waiting at the next repricing. Positive when the quote is going up."""
        return round(self.repriced - self.price, 4)

    @property
    def steps_out(self) -> str:
        """Which party the *pending step* would put this lane past, ``""`` for neither.

        **This is the forecast the staircase makes possible.** A repricing is a step by
        construction -- the column stands for six months and then moves by at least
        :data:`atlas.engine.pricing.REPRICE_DEADBAND` -- so a lane can sit comfortably inside
        the band right up to the turn it does not. Both ends of that step are computable
        today and so is the date it lands on, which turns the one thing in this layer that
        genuinely arrived without notice into a scheduled event.
        """
        if self.buyer_max > 0 and self.repriced > self.buyer_max + _PRICE_EPSILON:
            return "buyer"
        if self.seller_min > 0 and self.repriced <= self.seller_min + _PRICE_EPSILON:
            return "seller"
        return ""

    @property
    def warned(self) -> bool:
        """Whether this lane is worth a line, for any cause.

        Three triggers answering three different questions: the margin is a *position*, the
        pending step is an *event with a date*, and the regard crossing is a *duration*. A
        lane in trouble is usually caught by one and not the others -- which is the finding
        that built this, since the regard trigger alone caught none of the suspensions the
        play battery actually produced.
        """
        return bool(
            self.margin < MARGIN_WARNING
            or self.steps_out
            or (self.regard is not None and self.regard.warned)
        )

    @property
    def urgent(self) -> bool:
        """Whether it stops at or before the next repricing."""
        return bool(
            self.margin < MARGIN_ESCALATION
            or self.steps_out
            or (self.regard is not None and self.regard.urgent)
        )


def _quote_from(scarcity: float, squeeze: float, distress: float, pressure: float) -> float:
    """:func:`atlas.engine.pricing.price_route`'s own arithmetic, given the four factors.

    Reproduced rather than re-queried because the counterfactual behind :class:`Driver` needs
    the price *with one factor moved and the world left alone*, which no amount of asking the
    database can produce. Both clamps are kept: a factor whose removal only unwinds a clamp
    has restored nothing, and a counterfactual that ignored the ceiling would report headroom
    that does not exist.
    """
    commercial = min(pricing.COMMERCIAL_CEILING,
                     pricing.BASE_PRICE * scarcity * squeeze * distress)
    return max(pricing.FLOOR_PRICE, min(pricing.EMBARGO_PRICE, commercial * pressure))


def _ceiling_from(shortage: float, dependency: float, captivity: float,
                  war: float, regard: float) -> float:
    """And :func:`buyer_maximum`'s, for the same reason. Willingness only -- the purse is a
    ``min`` applied after, and it is reported as a bind rather than as a factor."""
    return (pricing.BASE_PRICE * CEILING_HEADROOM
            * shortage * dependency * captivity * war * regard)


#: The sentence and the lever for each input, keyed the way :class:`Driver` names them. Held
#: as data rather than as a branch per factor because the set is the union of two other
#: modules' term lists and a missing key should read as a missing sentence, not as a silent
#: fall-through to somebody else's explanation.
_DRIVER_WORDS: dict[str, tuple[str, str, str]] = {
    # key: (side, what it is doing, what to do about it)
    "scarcity": ("price", "the market is short of {good} relative to what the seller holds",
                 "build a reserve of {good}, so a tight month is not a renegotiation"),
    "squeeze": ("price", "you have too few suppliers of {good} for the price to be soft",
                "open a second {good} route -- a buyer with somewhere else to go pays less"),
    "distress": ("price", "the seller has nowhere else to put this cargo and is discounting "
                 "it toward its own floor",
                 "nothing on your side moves this: it is the seller's other lanes"),
    "seller_regard": ("price", "{seller} thinks badly enough of you to price it in",
                      "earn back {seller}'s regard for you -- its opinion of you is what "
                      "raises the price it asks"),
    "war": ("price", "a war is being fought and everybody selling to a belligerent knows it",
            "the premium ends with the war"),
    "shortage": ("buyer", "you are short of {good} yourself, which is what makes you willing "
                 "to pay this",
                 "a fuller store lowers what you will pay, which narrows this margin rather "
                 "than widening it"),
    "dependency": ("buyer", "you produce almost no {good} of your own",
                   "raise your own {good} production, which ends the dependency rather than "
                   "repricing it"),
    "alternatives": ("buyer", "you have no other supplier of {good} in reach",
                     "open a second {good} route: it lowers the price and the ceiling "
                     "together, and it is the only lever that reaches both"),
    "regard": ("buyer", "your own government thinks badly enough of {seller} that it would "
               "rather do without",
               "mend your own standing toward {seller}: what you will pay falls with your "
               "regard for the supplier"),
    "reserve": ("seller", "{seller} holds out harder against a government it dislikes",
                "earn back {seller}'s regard for you -- it is what sets the least it will "
                "ship for"),
}


def _say(key: str, seller_name: str, good: str) -> tuple[str, str]:
    side_said_lever = _DRIVER_WORDS.get(key)
    if side_said_lever is None:
        return "", ""
    _side, said, lever = side_said_lever
    return (said.format(good=good, seller=seller_name),
            lever.format(good=good, seller=seller_name))


def _drivers(  # noqa: PLR0913 - a counterfactual is a fact about all of this
    terms, ceiling: Ceiling, *, at_war: bool, hostile_seller: float,
    seller_min: float, binding: str, quote: float, seller_name: str, good: str,
) -> Driver | None:
    """Which single input, returned to neutral, restores the most headroom on the near side.

    Every candidate is scored in the same units -- price -- so a factor that moves the quote
    and a factor that moves a boundary are directly comparable, which is the whole point: the
    question a player has is "what is closing this lane", and the answer is allowed to be on
    either side of it.

    **Scored against the *fresh* quote, never the standing one, and the distinction was a
    bug before it was a docstring.** A counterfactual moves a factor and re-derives the price
    from the factors; the standing column is a frozen number that no factor derives, so a
    baseline taken from it is not on the same curve as the answers being compared with it.
    Measured with :attr:`Headroom.tested` -- the *higher* of the two prices -- as the
    baseline, every seller-side lane in a whole era reported no driver at all: the floor is
    threatened by the price falling, and a baseline taken at the higher price already had the
    headroom every candidate was being credited with restoring.
    """
    if binding == "buyer":
        def headroom_of(q: float, ceil: float) -> float:
            return ceil - q
    elif binding == "seller":
        def headroom_of(q: float, ceil: float) -> float:
            return q - seller_min
    else:
        return None

    # The pressure term carries two independent things multiplied together, and they have
    # different levers, so they are separated before either is neutralised. See
    # `pricing.price_route`: hostility first, then the war premium on top.
    pressure_hostile = pricing.pressure_at_hostility(hostile_seller)
    pressure_war = (terms.pressure / pressure_hostile
                    if pressure_hostile > 0 else 1.0)

    pressed = max(ceiling.need, ceiling.dependency)
    shortage = 1.0 + NEED_GAIN * pressed
    dependency = 1.0 + DEPENDENCY_GAIN * ceiling.dependency
    captivity = 1.0 + CAPTIVITY_GAIN / (1.0 + ceiling.alternatives)
    war_up = 1.0 + WAR_URGENCY if at_war else 1.0
    regard_cut = max(0.0, 1.0 - HOSTILITY_BITE * ceiling.hostile)

    here = headroom_of(quote, ceiling.willing)
    candidates: list[Driver] = []

    def offer(key: str, quote: float, ceil: float) -> None:
        worth = headroom_of(quote, ceil) - here
        if worth <= _PRICE_EPSILON:
            return
        said, lever = _say(key, seller_name, good)
        if not said:
            return
        candidates.append(Driver(
            name=key, side=_DRIVER_WORDS[key][0], worth=round(worth, 4),
            said=said, lever=lever))

    # The four price factors, each neutralised alone.
    offer("scarcity", _quote_from(1.0, terms.squeeze, terms.distress, terms.pressure),
          ceiling.willing)
    offer("squeeze", _quote_from(terms.scarcity, 1.0, terms.distress, terms.pressure),
          ceiling.willing)
    offer("distress", _quote_from(terms.scarcity, terms.squeeze, 1.0, terms.pressure),
          ceiling.willing)
    offer("seller_regard",
          _quote_from(terms.scarcity, terms.squeeze, terms.distress, pressure_war),
          ceiling.willing)
    offer("war", _quote_from(terms.scarcity, terms.squeeze, terms.distress,
                             pressure_hostile), ceiling.willing)
    # And the five the buyer's ceiling is built from.
    offer("shortage", quote,
          _ceiling_from(1.0, dependency, captivity, war_up, regard_cut))
    offer("dependency", quote,
          _ceiling_from(shortage, 1.0, captivity, war_up, regard_cut))
    offer("alternatives", quote,
          _ceiling_from(shortage, dependency, 1.0, war_up, regard_cut))
    offer("regard", quote,
          _ceiling_from(shortage, dependency, captivity, war_up, 1.0))
    # The seller's reserve has exactly one moving part; the distress gate underneath it is
    # not a lever anybody holds, so it is described and not offered.
    if binding == "seller" and hostile_seller > 0 and seller_min > 0:
        bare = refusal.RESERVE_PRICE
        worth = (quote - bare) - here
        if worth > _PRICE_EPSILON:
            said, lever = _say("reserve", seller_name, good)
            candidates.append(Driver(name="reserve", side="seller", worth=round(worth, 4),
                                     said=said, lever=lever))

    if not candidates:
        return None
    # Ties broken by name so two worlds in the same state print the same page.
    return max(candidates, key=lambda d: (d.worth, d.name))


def headroom(  # noqa: PLR0913 - a lane's room is a fact about all of this
    conn: sqlite3.Connection, world_id: str, seller: str, buyer: str, good: str,
    *, standing_price: float, route_id: str = "", turn: int = 0, turn_span: float = 1.0,
    seller_name: str = "", lanes: pricing.Lanes | None = None,
    alternatives_cache: dict | None = None, distress_cache: dict | None = None,
) -> Headroom | None:
    """How much room this lane has before either government walks, and what is eating it.

    ``None`` when the question does not arise: the layer is off, or the route is barter.

    **Both boundaries, whoever is reading.** :func:`approach` is buyer-side by design -- a
    crossing on the buyer's own ledger is not a seller's business -- and that was right for
    what it answered and wrong as the only warning in the engine, because the boundary that
    actually stops lanes in a played game is the seller's. This reports the near side
    whichever it is. It is still only shown to a *party to the lane*: see
    :func:`headroom_lines`, which is called for the government at one end of it, and which
    prints the other party's line as a number that party has already committed to by standing
    in the market, not as a look inside its ministry.
    """
    if not enabled() or not pricing.enabled() or standing_price <= 0:
        return None
    terms = pricing.price_route(
        conn, world_id, seller, buyer, good,
        lanes=lanes, alternatives_cache=alternatives_cache)
    ceiling = buyer_maximum(
        conn, world_id, seller, buyer, good,
        lanes=lanes, alternatives_cache=alternatives_cache)
    seller_min = seller_minimum(
        conn, world_id, seller, buyer, lanes=lanes, distress_cache=distress_cache)

    repriced = terms.unit_price
    # The conservative reading, and the same one `approach` takes: whichever of the two
    # quotes is nearer to ending the lane is the one worth being warned about.
    tested = max(standing_price, repriced)
    to_buyer = ceiling.willing - tested
    # A seller with no reserve cannot be walked past, so its margin is not a number: it is
    # reported as unreachable rather than as a large one.
    to_seller = (min(standing_price, repriced) - seller_min) if seller_min > 0 else None

    reachable = [("buyer", to_buyer)]
    if to_seller is not None:
        reachable.append(("seller", to_seller))
    binding, nearest = min(reachable, key=lambda pair: pair[1])
    margin = nearest / max(tested, _PRICE_EPSILON)

    at_war = pricing.at_war_with_anybody(conn, world_id, buyer)
    hostile_seller = pricing.hostility(conn, world_id, seller, buyer)
    next_turn, next_days = trade_next_repricing(route_id, turn, turn_span=turn_span)
    return Headroom(
        price=round(standing_price, 4),
        repriced=round(repriced, 4),
        tested=round(tested, 4),
        buyer_max=round(ceiling.willing, 4),
        buyer_purse=round(ceiling.affordable, 4),
        purse_bound=ceiling.purse_bound,
        seller_min=round(seller_min, 4),
        to_buyer=round(to_buyer, 4),
        to_seller=None if to_seller is None else round(to_seller, 4),
        binding=binding,
        margin=round(margin, 4),
        next_reprice_turn=next_turn,
        next_reprice_days=round(next_days, 1),
        driver=_drivers(
            terms, ceiling, at_war=at_war, hostile_seller=hostile_seller,
            seller_min=seller_min, binding=binding, quote=repriced,
            seller_name=seller_name or seller, good=good),
        regard=approach(
            conn, world_id, seller, buyer, good, standing_price=standing_price,
            lanes=lanes, alternatives_cache=alternatives_cache),
    )


def trade_next_repricing(route_id: str, turn: int, *, turn_span: float = 1.0):
    """:func:`atlas.engine.trade.next_repricing`, imported at call time.

    :mod:`atlas.engine.trade` is what this module imports *from*, so the reverse edge is
    taken here rather than at module scope -- the same shape :func:`briefing_lines` uses for
    :mod:`atlas.engine.intent`. A route with no id -- a lane being quoted before it exists --
    has no stagger and no next date, and says so with ``(turn, 0.0)``.
    """
    if not route_id:
        return turn, 0.0
    from .trade import next_repricing

    return next_repricing(route_id, turn, turn_span=turn_span)


#: How the price-side factors read to the party on the *other* end of them, where that
#: differs. A driver sentence is written from the buyer's chair -- "the market is short of
#: rare relative to what the seller holds" -- and handing that to the seller, whose own empty
#: warehouse is the thing being described, names somebody else as the cause of its own
#: position and offers it a lever it does not hold. Only the entries that actually differ are
#: here; everything else reads the same from both chairs.
_SELLER_WORDS: dict[str, tuple[str, str]] = {
    "scarcity": (
        "you are short of {good} yourself, and a seller that needs its own cargo is quoted "
        "less for it, not more",
        "raise your own {good} production, or wait: the quote comes back up as your supply "
        "does, and until it does you would rather keep the cargo than ship it at the floor"),
    "distress": (
        "your other lanes are cut, so this cargo has nowhere else to go and is discounted "
        "toward the floor",
        "reopen an outlet: a seller with somewhere else to sell is not quoted a distress "
        "price"),
    "seller_regard": (
        "your own government thinks badly enough of this buyer to price it in",
        "the premium you are charging is what is holding this lane near its edge"),
    "reserve": (
        "you hold out harder against a government you dislike",
        "your own regard for the buyer is what sets the least you will ship for"),
}


def _driver_words(near: Headroom, good: str, *, buying: bool) -> tuple[str, str]:
    """The driver's sentence and lever, written from the chair of whoever is reading."""
    driver = near.driver
    if driver is None:
        return "", ""
    if not buying and driver.name in _SELLER_WORDS:
        said, lever = _SELLER_WORDS[driver.name]
        return said.format(good=good), lever.format(good=good)
    return driver.said, driver.lever


def headroom_lines(
    near: Headroom, seller_name: str, good: str, *, buying: bool = True,
    player: bool = False,
) -> list[str]:
    """The commercial-headroom block for one standing route.

    **What replaced the regard warning, and why it had to be replaced rather than widened.**
    :func:`warning_line` states one boundary -- the buyer's -- crossed by one cause -- the
    buyer's own regard -- and both restrictions were measured to be the wrong ones: across
    the play battery the boundary that stopped lanes was the *seller's*, and the cause was
    the *price falling*, not any boundary moving at all. A warning that can only describe the
    case that does not happen is not a warning that needs a wider threshold.

    Six things, and each is a question a cabinet would ask about a contract:

    1. what it stands at, and what it would be if reopened today;
    2. the two lines it has to stay between -- the buyer's maximum and the seller's minimum;
    3. which of them is nearer, and by how much;
    4. when the file next reopens, because that is when the quote moves and a step is only
       a surprise if the date is not on the calendar;
    5. the one input currently eating the margin, named exactly; and
    6. what to do about that input.

    ``buying`` decides whose side of the sheet this is written from and is not cosmetic: a
    seller is shown its own floor and the fact that the buyer has a ceiling, never the
    *number* -- what a foreign government will pay is the inside of its ministry, and it is
    the restriction :func:`approach` was right to keep.

    **``player`` decides how far item 4 is allowed to commit, and it is a truth claim rather
    than a register.** The date is certain -- :func:`atlas.engine.trade.next_repricing` is a
    fixed stagger and the file *is* opened on that turn -- but "these terms reopen" reads as
    a promise about the *outcome*, and no state recorded today guarantees one. The quote at
    that turn is re-derived from the world as it stands then, and the term that decides it,
    ``scarcity``, is the seller's supply ratio, which is measured to swing by a whole order
    inside three turns -- see :data:`MARGIN_WARNING` and `scratchpad/CLIFF_DIAGNOSIS.md`. A
    lane forecast to reopen higher can reprice lower, and a lane the player was told would
    come back can stay shut. So the player surface names the *review*, not the result: it is
    eligible for reconsideration on that turn, and what it reopens at is the world's that
    day.

    There is deliberately no branch that promises reopening, because there is no recorded
    state that earns one: every input to the future quote is re-read at the repricing, so
    "guaranteed realignment" is not a condition this engine can currently express. If one is
    ever added -- a fixed-term contract, a treaty-set price -- this is where it goes.

    ``player`` is off by default, which keeps the model-facing briefing byte-identical while
    the cassette is frozen; see :func:`briefing_lines`.
    """
    lines: list[str] = []
    head = "**Stops at or before the next repricing.**" if near.urgent else "Headroom:"

    # 1. The two prices, and the step between them.
    said = (f"  - {head} It stands at {near.price:.2f}")
    if abs(near.step) > _PRICE_EPSILON:
        said += f", and would be **{near.repriced:.2f} if repriced today**"
    else:
        said += ", and today's terms are the same"
    lines.append(said + ".")

    # 2. and 3. The band, and the nearer edge of it.
    if buying:
        band_said = f"  - You will pay up to {near.buyer_max:.2f}"
        if near.seller_min > 0:
            band_said += f"; {seller_name} will not ship below {near.seller_min:.2f}"
        else:
            band_said += f"; {seller_name} has nowhere else for the cargo and no floor"
    else:
        band_said = (f"  - You will not ship below {near.seller_min:.2f}"
                     if near.seller_min > 0 else
                     "  - You have nowhere else for this cargo, so you have no floor")
        band_said += f"; {seller_name} has a ceiling of its own"
    lines.append(band_said + ".")

    if near.binding == "buyer" and near.to_buyer < 0:
        lines.append(f"  - The quote is {abs(near.to_buyer):.2f} **above** what the buyer "
                     "will pay. This lane is stopped or stopping.")
    elif near.binding == "seller" and near.to_seller is not None and near.to_seller < 0:
        lines.append(f"  - The quote is {abs(near.to_seller):.2f} **below** the seller's "
                     "floor. This lane is stopped or stopping.")
    else:
        # Named from the chair of whoever is reading: a seller told that "the seller's floor"
        # is the near line has to work out that the seller is itself.
        if near.binding == "buyer":
            whose = "what the buyer will pay" if not buying else "what you will pay"
        else:
            whose = "your floor" if not buying else "the seller's floor"
        room = near.to_buyer if near.binding == "buyer" else (near.to_seller or 0.0)
        lines.append(f"  - Nearer line is {whose}: {room:.2f} a unit of room, "
                     f"{near.margin:.0%} of the quote.")

    # 4. The date, because a step nobody can see coming is the whole complaint. The date is
    #    certain; what the file reopens *at* is not, and the player wording says only the
    #    first. See the docstring on `player`.
    if near.next_reprice_days > 0:
        when = (f"  - Next commercial review at turn {near.next_reprice_turn}, about "
                f"{near.next_reprice_days:.0f} days off; these terms are eligible for "
                "reconsideration then, not certain to move"
                if player else
                f"  - These terms reopen at turn {near.next_reprice_turn}, about "
                f"{near.next_reprice_days:.0f} days off")
        stepping = near.steps_out
        if stepping == "buyer":
            when += (" -- and at today's terms the new quote is **above what the buyer "
                     "will pay**, so that is the turn it stops")
        elif stepping == "seller":
            when += (" -- and at today's terms the new quote is **at or below the "
                     "seller's floor**, so that is the turn it stops")
        lines.append(when + ".")

    # 5. and 6. The one input doing it, and the lever on that input.
    if near.driver is not None:
        said, lever = _driver_words(near, good, buying=buying)
        lines.append(f"  - What is moving it: {said} "
                     f"(worth {near.driver.worth:.2f} a unit of the margin).")
        if lever:
            lines.append(f"  - You could: {lever}.")
    # The regard crossing is the one boundary that can be given a date rather than a
    # distance, so it is added where it exists rather than replacing the above.
    if buying and near.regard is not None and near.regard.warned \
            and near.regard.on_the_regard_axis:
        lines.append(
            f"  - Separately, your own regard for {seller_name} would close this at "
            f"{near.regard.threshold:.0f}; it is {near.regard.regard:.0f} today, about "
            f"{near.regard.days:.0f} days at the rate relations drift on their own.")
    if buying and near.purse_bound:
        lines.append(
            f"  - Your treasury covers only {near.buyer_purse:.2f} a unit at current "
            "commitments. That does not stop the lane -- the engine ships home what you "
            "can pay for and leaves the rest -- but you are not receiving all of it.")
    return lines


def why_it_stopped(
    state: Band, near: Headroom | None, good: str, *, price_before: float = 0.0,
) -> str:
    """One sentence saying **which of the three things actually happened**.

    :func:`explain` already says *who* stopped and at what number. It does not say *why*, and
    the difference is the whole complaint the diagnosis started from: "the buyer stopped
    taking oil at 0.72, it will pay 0.52" reads as though the buyer changed its mind, when in
    the measured majority of cases nobody changed their mind at all and the **price moved**.

    Three cases, and the sentence names which:

    * the quote **rose** through the buyer's ceiling;
    * the quote **fell** through the seller's floor -- the case the play battery says is 96%
      of in-play suspensions, and the one no warning in the engine could previously describe;
    * neither: the quote stood still and a **boundary moved** under it, which is then named
      down to the input by :attr:`Headroom.driver`.

    ``price_before`` is the standing price on the turn before, which the caller has and this
    cannot derive: the column holds one value and the question is about two. Passed as ``0``
    -- by a caller that does not have it -- the sentence falls back to naming the boundary,
    which is still more than :func:`explain` says.
    """
    side = state.ended_by or ("seller" if state.withdrawal is not None else "buyer")
    moved = state.price - price_before if price_before > 0 else 0.0
    driver = near.driver if near is not None else None
    tail = f" What moved it: {driver.said}." if driver is not None else ""

    if moved > _PRICE_EPSILON and side == "buyer":
        return (f"**The price rose.** {good.capitalize()} went from {price_before:.2f} to "
                f"{state.price:.2f} a unit at a repricing, through the "
                f"{state.buyer_max:.2f} the buyer will pay. The buyer's terms did not "
                f"move.{tail}")
    if moved < -_PRICE_EPSILON and side == "seller":
        return (f"**The price fell.** {good.capitalize()} went from {price_before:.2f} to "
                f"{state.price:.2f} a unit at a repricing, through the "
                f"{state.seller_min:.2f} the seller will ship for. The seller's terms did "
                f"not move.{tail}")
    if side == "seller":
        return (f"**The seller's floor moved.** The quote stood at {state.price:.2f} and "
                f"the least {good} would be shipped for rose past it to "
                f"{state.seller_min:.2f}.{tail}")
    return (f"**The buyer's ceiling moved.** The quote stood at {state.price:.2f} and the "
            f"most the buyer would pay for {good} fell past it to "
            f"{state.buyer_max:.2f}.{tail}")


def priceable_goods() -> tuple[str, ...]:
    """The goods a band can be struck over. One list, and it is trade's."""
    return GOODS


#: How many routes one briefing prints, largest bill first.
#:
#: **A cap, because a briefing is already the largest object in the engine.** A promoted
#: power in a spread world can be at one end of a dozen lanes, and a dozen four-line entries
#: is a page of shipping manifest crowding out the turn. Six is enough to carry every route
#: that matters to a great power's economy, and the rest are summarised in one line -- the
#: same trade :func:`atlas.engine.views._powers_worth_naming` makes about the other powers.
BRIEFING_ROUTES = 6

#: How far the world has to have moved since the terms were last settled before the briefing
#: says so. The same deadband the engine renegotiates on, so a briefing never warns about a
#: repricing the engine would not actually make.
_WORTH_WARNING = pricing.REPRICE_DEADBAND


def briefing_lines(
    conn: sqlite3.Connection, world_id: str, nation_id: str, *, player: bool = False,
) -> list[str]:
    """Every standing route this government is at one end of, and what it can do about it.

    **This is where the ``because`` reasons finally go.** :class:`atlas.engine.pricing.Terms`
    has carried a list of named reasons since the pricing layer was written and nothing ever
    showed one to a government: they reached a turn report as one truncated line about
    somebody else's route, and the country actually paying the bill was told nothing. The
    whole risk in an engine-settled price is that it reads as a number the game made up, and
    the answer to that was computed and then dropped on the floor.

    Five things per route, and each is a question a cabinet would actually ask: what is
    moving, what it costs a month, what it makes us dependent on, why the price is what it
    is, and what we can do about it.

    Empty when :mod:`atlas.engine.pricing` is off, so a barter briefing is the
    byte-for-byte briefing it always was.

    **``player`` picks the wording of the repricing forecast and nothing else.** This block
    is built twice from the same state -- once into a tracked power's prompt
    (:func:`atlas.engine.views.build_tracked_agent_view`) and once into the player's advisor
    page (:func:`atlas.engine.views.build_advisor_view`) -- and only the second is a surface
    a person reads and holds the game to. The non-committal phrasing is the correct one for
    both; it is applied to the player's copy alone because the prompt text is gated by a
    cassette that has not been re-recorded, and moving it here would cost a second live
    re-record for a change that alters no mechanic. **Drop the flag and make the player
    wording unconditional at the next re-record** -- there is no reason for the two to
    diverge beyond that point. See :func:`headroom_lines`.
    """
    from . import intent  # local: `intent` reads this module, and a cycle helps nobody

    if not pricing.enabled() or not _priced(conn):
        return []
    routes = conn.execute(
        "SELECT * FROM trade_route"
        "  WHERE world_id = ? AND (seller_id = ? OR buyer_id = ?) ORDER BY id",
        (world_id, nation_id, nation_id),
    ).fetchall()
    wanted = intent.standing(conn, world_id, nation_id, include_met=True)
    if not routes and not wanted:
        return []

    lines = ["## Your trade, and what it is costing you"]
    if routes:
        lines.extend(_route_lines(conn, world_id, nation_id, routes, player=player))
    if wanted:
        lines.append("")
        lines.append("**What you have asked your trade delegations to arrange:**")
        name_of = _namer(conn)
        lines.extend(f"- {item.as_line(name_of)}" for item in wanted)
    # **And the field that takes the decision, named.** Every "You could:" line above is an
    # `atlas.engine.intent` intention and none of them said how to ask for one -- so with
    # the pricing layer default-off the channel was simply absent from the schema, and the
    # turn it shipped default-on it became a mechanic that was built, parsed, tested and
    # nameable by no briefing in the engine. That is exactly the state `covert_support` was
    # in, and `test_no_channel_is_offered_nowhere` caught it on the flip.
    #
    # Printed whenever this block is, not only where an intention already stands: a
    # government that has never asked for one is the reader who most needs telling.
    lines.append("")
    # **Where the ids come from, which this line named the field without ever saying.**
    # Every other id-taking field on this form points at its source -- "region ids from your
    # briefing", "a kind from the forces menu above", "an id from the build menu" -- and the
    # measured result is that they are obeyed: across 47 recorded tapes, 0 of 822 `push`
    # objectives and 3 of 1,235 build orders named something absent from the prompt. This
    # one said only "the nation id to trade with", and **25 of the 100 partners ever named
    # were ids that appear nowhere in the naming model's own briefing** -- `zaf`, `zaire`,
    # `south_africa`, `sau` -- each of which loses the intention outright. The failure
    # concentrates on `diversify`, which is 70% of all intents and the one kind that
    # *requires* a partner; the wording above made the field read as required for all three.
    lines.append(
        f"Ask for one with `trade_intents`: at most {intent.MAX_STANDING} standing, each a "
        "`kind` of `seek`, `sell` or `diversify` and a `good`. **A `partner` is required "
        "only for `diversify`** -- leave it out of a `seek` or a `sell` and the engine finds "
        "whoever is in reach and cheapest. **When you do name one it must be a nation id "
        "from the \"The other powers, as you see them\" list in this briefing**; an id that "
        "is not on that list is refused and the intention is lost, so name a power you can "
        "actually see rather than one you know of. The engine finds who is in reach and "
        "settles the terms; you name what you are after.")
    # And the one refusal a standing list invites by existing. Every other standing channel
    # on this form says "it holds until you change it", so restating is the natural thing to
    # do -- and here it costs the slot: the id is derived from what the intention *is*, so a
    # restatement collides with the original and is turned down. Five of pt77's 42 refusals.
    if wanted:
        lines.append(
            "The intentions above stand until they are met or you retire them. **Do not "
            "restate one**: an intention identical to one already standing is refused and "
            "the slot is spent. List only what is new.")
    lines.append("")
    return lines


def _priced(conn: sqlite3.Connection) -> bool:
    """Whether this world's schema carries a price at all.

    The briefing's own reading of :func:`atlas.engine.trade.pricing_columns_present`,
    imported here rather than at module scope because :mod:`atlas.engine.trade` is what this
    module imports *from* and the function is only wanted on this path.
    """
    from .trade import pricing_columns_present

    return pricing_columns_present(conn)


def turn_and_span(conn: sqlite3.Connection, world_id: str) -> tuple[int, float]:
    """``(turn number, how long a turn is in reference months)`` for this world.

    The span is measured off the last turn that actually has both ends rather than assumed to
    be the reference 30 days, because :func:`atlas.engine.trade.next_repricing` converts turns
    into a *date* and the conversion is the whole value of printing one: a yearly game whose
    briefing said "about 180 days" would be off by a factor of twelve, which is worse than
    saying nothing. Falls back to the reference turn when the world has no sealed turn to
    measure -- turn one, where there is no history and no repricing due either.
    """
    from .pacing import REFERENCE_DAYS, days_between

    row = conn.execute(
        "SELECT turn_number FROM world WHERE id = ?", (world_id,)).fetchone()
    turn = int(row["turn_number"]) if row is not None else 0
    span = conn.execute(
        "SELECT start_date, end_date FROM turn"
        "  WHERE world_id = ? AND end_date IS NOT NULL"
        "  ORDER BY number DESC LIMIT 1", (world_id,)).fetchone()
    if span is None:
        return turn, 1.0
    days = days_between(str(span["start_date"]), str(span["end_date"]))
    if not days or days <= 0:
        return turn, 1.0
    return turn, days / REFERENCE_DAYS


def _namer(conn: sqlite3.Connection):
    """``id -> name``, cached, because a briefing names the same handful of powers over and
    over and each lookup is its own query."""
    seen: dict[str, str] = {}

    def name_of(nation_id: str) -> str:
        if not nation_id:
            return "nobody in particular"
        if nation_id not in seen:
            row = conn.execute(
                "SELECT name FROM nation WHERE id = ?", (nation_id,)).fetchone()
            seen[nation_id] = str(row["name"]) if row is not None else nation_id
        return seen[nation_id]

    return name_of


def _dependency_line(good: str, covers: float, reach: int) -> str:
    """What one route makes this buyer dependent on, in a sentence.

    Split out because it is the one line here with two independent readings in it -- how
    much of the country's demand this lane carries, and what would replace it -- and reading
    them apart is what stops a route that covers 8% of demand and a route that covers all of
    it printing the same warning.
    """
    said = (f"  - It covers {covers:.0%} of your monthly {good} demand"
            if covers < 1.0 else
            f"  - It is {covers:.1f}x your monthly {good} demand")
    if reach == 0:
        return said + (", and no other supplier can reach you -- if this lane closes, "
                       "nothing replaces it.")
    return said + (f", and {reach} other supplier could reach you if it closed."
                   if reach == 1 else
                   f", and {reach} other suppliers could reach you if it closed.")


def _route_lines(
    conn: sqlite3.Connection, world_id: str, nation_id: str, routes,
    *, player: bool = False,
) -> list[str]:
    """One entry per route, five parts each. See :func:`briefing_lines`."""
    from .trade import demand_of

    name_of = _namer(conn)
    lanes: dict = {}
    alternatives_cache: dict = {}
    distress_cache: dict = {}
    demand = demand_of(conn, nation_id)
    # Read once for the page rather than per route: both are properties of the world, and
    # `next_repricing` wants them for every line.
    turn, turn_span = turn_and_span(conn, world_id)

    # Largest bill first, then by id, so the cap keeps the routes that decide a budget and
    # two worlds in the same state print the same page.
    ordered = sorted(
        routes,
        key=lambda r: (-(float(r["volume"]) * float(r["unit_price"] or 0.0)), str(r["id"])),
    )
    lines: list[str] = []
    for row in ordered[:BRIEFING_ROUTES]:
        seller, buyer = str(row["seller_id"]), str(row["buyer_id"])
        good, volume = str(row["good"]), float(row["volume"])
        price = float(row["unit_price"] or 0.0)
        buying = buyer == nation_id
        other = seller if buying else buyer

        # 1. The cargo, and 2. what it costs a month.
        head = (f"- **{volume:g} {good} a month from {name_of(other)}**" if buying
                else f"- **{volume:g} {good} a month to {name_of(other)}**")
        if price <= 0:
            lines.append(head + " -- barter; the engine has not settled terms on this one.")
            continue
        bill = volume * price
        lines.append(
            head
            + (f", costing you {bill:.2f} a month" if buying
               else f", earning you {bill:.2f} a month")
            + f" at {price:.2f} a unit."
        )

        state = band(
            conn, world_id, seller, buyer, good, price,
            lanes=lanes, alternatives_cache=alternatives_cache,
            distress_cache=distress_cache,
        )
        # **Priced before the dependency line, and the order matters.** The count of who
        # else could reach this buyer lives on `Terms` as well as on `Ceiling`, and only
        # `Terms` is always there: `Band.ceiling` is `None` whenever this module is switched
        # off, and reading the count off it told every buyer in a pricing-on, consent-off
        # world that it had no alternative supplier -- the most alarming sentence in the
        # block, printed from a missing object rather than from the map.
        terms = pricing.price_route(
            conn, world_id, seller, buyer, good,
            lanes=lanes, alternatives_cache=alternatives_cache,
        )
        # A world at schema 46 has a price and nowhere to record a stoppage. It still gets
        # the live reading below; it simply has no "since when" to print.
        columns = set(row.keys())
        stopped = int(row["suspended_turn"]) if "suspended_turn" in columns else -1
        if stopped >= 0:
            side = str(row["suspended_by"]) or "terms"
            lines.append(
                f"  - **Stopped since turn {stopped}, by the {side}.** The agreement stands "
                "and nothing was breached; the cargo moves again the turn the two sides' "
                "terms overlap."
            )
        elif not state.overlaps:
            lines.append(f"  - **{explain(state, good)}**")
        else:
            # **The commercial-headroom block, and it is two-sided because the boundary that
            # actually stops lanes is the seller's.** What stood here was
            # `approach` + `warning_line`: one boundary, one cause, and buyer-only. Measured
            # over the play battery -- `scratchpad/cliff_inputs.py` -- that combination could
            # not see 96% of the suspensions the engine actually produced, which were the
            # *seller* withdrawing after the *price fell*. See :data:`MARGIN_WARNING`.
            #
            # Printed for the seller too, and the asymmetry is kept where it belongs: a
            # seller is told its own floor and that the buyer has a ceiling, never the number
            # -- see `headroom_lines`, which takes `buying` for exactly that.
            near = headroom(
                conn, world_id, seller, buyer, good, standing_price=price,
                route_id=str(row["id"]), turn=turn, turn_span=turn_span,
                seller_name=name_of(seller),
                lanes=lanes, alternatives_cache=alternatives_cache,
                distress_cache=distress_cache,
            )
            if near is not None and near.warned:
                lines.extend(headroom_lines(near, name_of(other), good, buying=buying,
                                            player=player))

        # 3. The dependency it creates.
        if buying and demand > 0:
            lines.append(_dependency_line(good, volume / demand, terms.alternatives))
        elif not buying:
            lines.append(
                f"  - {name_of(other)} depends on you for this. What it is worth to you is "
                "what it would cost them to replace it.")

        # 4. Why the price is what it is -- the reasons the pricing layer has always
        #    computed and never shown to the government paying them.
        if terms.because:
            lines.append("  - Why: " + "; ".join(terms.because) + ".")
        if abs(terms.unit_price - price) > _WORTH_WARNING * max(price, pricing.FLOOR_PRICE):
            lines.append(
                f"  - The world has moved since these terms were struck: they would be "
                f"{terms.unit_price:.2f} today, and the engine reopens the file when this "
                "route next comes up for renegotiation."
            )

        # 5. And what this government can actually do about it.
        if buying:
            doing = what_you_could_do(
                state, name_of(other), good, alternatives=terms.alternatives,
                # Read off the relation rather than recovered from `Terms.pressure`: the
                # war premium is folded into `pressure` multiplicatively, so inverting it
                # would report a hostile seller for every buyer at war with anybody.
                priced_in=pricing.hostility(conn, world_id, seller, buyer)
                > _HOSTILITY_WORTH_SAYING,
            )
            if doing:
                lines.append("  - You could: " + "; ".join(doing) + ".")

    if len(ordered) > BRIEFING_ROUTES:
        rest = len(ordered) - BRIEFING_ROUTES
        lines.append(
            f"- ({rest} further route(s), none worth as much a month as the ones above.)")
    return lines
