"""A seller that can say no.

**Nothing in the engine let a seller decline.** :func:`atlas.engine.trade.assess_trade`
shipped what the seller had at whatever the terms were, and
:data:`atlas.engine.pricing.FLOOR_PRICE` was the only thing between a distressed seller and
giving the cargo away. Over five years of measured play that was not a corner case: 10-15%
of route-turns sit pinned against the floor and *the share grows with the game* -- 0 at turn
one, 21% by turn 48 in gathering-storm, 108 of 488 routes by turn 24 in modern-day. See
`scratchpad/TRADE_PRICING_VALIDATION.md`. Those are routes shipping a real cargo, every
month, for the least the engine is willing to name, with no way out of the arrangement.

**Why a floor-pinned route is the one worth refusing, specifically.** A price reaches the
floor by exactly one road at this calibration, and it is worth naming because it decides
what this mechanic is *about*. The product ``scarcity * squeeze * distress * pressure`` has
to fall to 0.3; ``pressure`` is never below 1, ``distress`` never below 0.55 and ``squeeze``
never below 1.2 -- so it takes both of the two terms that can go low going low together:
``scarcity`` at its clamp of 0.25, which means **the seller is shorter of the good than the
buyer is**, and ``squeeze`` at its own floor, which means **the buyer had three other
suppliers it could have reached**. That is not a hard-bargained price. That is a country
exporting the fuel its own factories are waiting on, to somebody who did not have to buy it
from them -- and a player who saw it described would ask why it did not simply stop.

So it now can. Three things decide it, and each is read off state the pricing layer already
maintains:

``the terms``
    The price is at or under the least this seller will take. The reserve is
    :data:`RESERVE_PRICE`, which **defaults to the floor itself** -- so out of the box the
    only routes a seller walks away from are the ones the engine had already priced as
    worthless. Raising it is the knob; it is deliberately not raised here.
``the outlets``
    Whether the seller is in a position to walk at all. A seller whose other lanes are cut,
    or who is somebody's sanctions target, is dumping into a market that has shrunk to the
    buyers still willing, and refusing would be a pariah pretending it had options. That is
    :func:`atlas.engine.pricing.seller_distress`, the same number that gives it the
    discount -- so a seller cannot take the distress discount *and* decline for having
    alternatives.
``the temper``
    Hostility raises what the seller demands, and raises it faster than the pressure term
    raises what the buyer pays. Bad relations already walk a price *up*; this is the other
    half of the same fact -- past a point, a seller stops wanting the money at all.

**The engine decides, not the model.** This is the fourth answer in the sequence the trade
layer already keeps: a model may propose that two powers trade,
:func:`atlas.engine.trade.worth_selling` decides what and how much,
:mod:`atlas.engine.pricing` decides the price, and this decides whether the cargo goes. A
model that could refuse a sale could embargo anybody by saying so.

**Nothing is stored.** A refusal is derived fresh from the standing price and live state on
the turn it happens, exactly as the price itself is derived, so there is no new column, no
migration, and nothing extra for a rewind to restore -- a rewound world re-derives the same
refusals from the same state. The route is not torn up either: this is a withdrawal from
*this turn's shipment*, and it reverses on its own the moment the terms improve, which is
what a walk-away is. Tearing up the route would make a bad month permanent and would put a
diplomatic fact (an agreement ending) in the hands of a commercial one.

**The flag, and why it defaults the other way from the pricing flag it sits on.** This ships
**on**, as part of the trade feature rather than as a second thing to switch on beside it: a
priced world in which no seller may decline is the world the long-game validation measured and
found stuck at the floor, and shipping that shape deliberately is not a decision anybody wants
to have made by a default. It is still its own flag -- ``ATLAS_TRADE_REFUSAL=off`` turns it
back off -- because the two have to be separable to be measured apart, and the paired arms in
`scratchpad/TRADE_CONCENTRATION_OPTIONS.md` are what that separability bought.

**Nothing changes for a world with pricing down.** Refusal is consulted only inside the
priced branch of :func:`atlas.engine.trade.assess_trade` -- so a barter world has no terms,
therefore no bad terms, and this module never runs. That was the whole force of this
default while :mod:`atlas.engine.pricing` was itself default-off; now that pricing ships on
too, the escape is `ATLAS_TRADE_PRICING=off`, and this flag remains the way to keep a priced
world in which only the buyer may decline.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field

from . import pricing

__all__ = [
    "DISTRESS_FLOOR",
    "RESERVE_HOSTILITY_GAIN",
    "RESERVE_PRICE",
    "Withdrawal",
    "enabled",
    "explain",
    "seller_declines",
    "set_enabled",
]


def _flag_from_env() -> bool:
    """``ATLAS_TRADE_REFUSAL=off`` takes the seller's voice away again.

    **Default on**, unlike the pricing flag underneath it -- see the module docstring. The
    negative reading is the one that has to work here, so the parse is inverted rather than
    the string list: anything that is not recognisably "off" leaves it on, because a typo in
    an environment variable should not quietly ship a different game.

    Read once, at import, for the reason :mod:`atlas.engine.pricing` gives: a coefficient
    that could change between two turns of the same game breaks replay. Tests move it
    through :func:`set_enabled`.
    """
    raw = os.environ.get("ATLAS_TRADE_REFUSAL", "on").strip().lower()
    return raw not in {"0", "off", "false", "no"}


_ENABLED: bool = _flag_from_env()


def enabled() -> bool:
    return _ENABLED


def set_enabled(value: bool) -> bool:
    """Move the flag, returning what it was."""
    global _ENABLED
    was, _ENABLED = _ENABLED, bool(value)
    return was


#: The least a seller will take, before hostility raises it. **This is the knob.**
#:
#: **It ships at the price floor, which is the conservative setting and is meant to be
#: argued with.** At this value a seller declines exactly the routes the engine had already
#: decided were worth the minimum it is willing to name: the mechanic reaches the measured
#: 10-15% of floor-pinned route-turns and nothing else, and **no route that was earning
#: anything stops**. That is why it is safe to ship on by default -- the population it can
#: touch is exactly the population the validation identified as broken.
#:
#: Raising it is the whole balance dimension this mechanic opens, and it is deliberately
#: left un-raised. ``BASE_PRICE * 0.5`` (0.045, against a floor of 0.027 and a median
#: realized price of 0.108) would let sellers walk away from thin-but-real terms as well --
#: a larger and more interesting mechanic, and one that has not been measured over a long
#: game. Move it with `scratchpad/refusal_longgame.py`, not with an argument: the effect
#: of the floor setting is 6-13% more shortage lines, and there is no reason to think that
#: cost scales linearly with the reserve.
RESERVE_PRICE = pricing.FLOOR_PRICE

#: How much a total collapse in relations raises that reserve, as a share.
#:
#: **Above the pricing layer's own pressure gain on purpose.** Hostility already makes a
#: cargo dearer -- the buyer pays a premium of up to 1.6x -- and if the reserve rose more
#: slowly than the premium, then bad relations would make a seller *less* likely to walk,
#: which is the wrong sign for the same fact. At 2.0 the seller's patience runs out before
#: the buyer's willingness to pay does, so a deteriorating relationship ends a trade from
#: whichever side reaches the end first.
RESERVE_HOSTILITY_GAIN = 2.0

#: How much trouble a seller may be in and still be allowed to decline. Distress runs 1.0
#: (nothing cut, no sanctions) down to 0.55 (both, at their worst).
#:
#: **0.85 is where "I have other buyers" stops being true.** It is the distress of a seller
#: with about half its other lanes cut, and it is below the distress of a seller under any
#: sanction at all (0.71) -- so a pariah always ships. That is the whole point of the
#: pariah discount: a country selling to whoever still will is not in a position to hold
#: out for better terms, and letting it refuse would hand it back the leverage sanctions
#: took away.
DISTRESS_FLOOR = 0.85

#: The stored price is rounded and the reserve is a product of floats. The same trap the
#: embargo comparison in :mod:`atlas.engine.trade` records, avoided the same way.
_PRICE_EPSILON = 1e-6


@dataclass(frozen=True, slots=True)
class Withdrawal:
    """A seller declining this turn's shipment, and why it declined.

    **Not called ``Refusal``, and the collision it avoids is a real one.**
    :class:`atlas.engine.causality.Refusal` is the engine turning down a *proposal*, and it
    carries a hard rule with it -- a refusal that only says no is one the staff will make
    again next turn, so every one of them has to name what to do instead, and there is a
    repo-wide sweep that fails if one does not. This is not that: nobody proposed anything,
    the engine is not correcting a mistake, and the alternative to shipping at the floor is
    simply not shipping. Sharing the name would have put this under a rule written for
    something else and, worse, told a reader the two were the same kind of event.

    ``because`` carries the same weight it does on :class:`atlas.engine.pricing.Terms`: a
    cargo that silently stops arriving is indistinguishable from a blockade, and a player
    who cannot tell those apart will send a fleet at a price.
    """

    #: What the terms actually stand at.
    unit_price: float
    #: The least this seller would have taken, hostility included.
    reserve: float
    #: How badly the seller needed the sale. 1.0 is a seller with its options intact.
    distress: float
    #: How badly the two are getting on, 0.0..1.0.
    hostility: float
    because: tuple[str, ...] = field(default_factory=tuple)


def seller_declines(
    conn: sqlite3.Connection, world_id: str, seller: str, buyer: str, good: str,
    unit_price: float, *, lanes: pricing.Lanes | None = None,
    distress_cache: dict | None = None,
) -> Withdrawal | None:
    """Whether this seller withdraws from this turn's shipment. ``None`` means it ships.

    Derived from the *standing* price rather than from freshly computed
    :class:`~atlas.engine.pricing.Terms`, and that is deliberate: terms are only
    reconsidered on a stagger, so a refusal keyed to a repricing would decline one turn in
    six and ship the other five -- a seller that changed its mind every month for no reason
    anybody could see. The stored price carries the fact between repricings, exactly as it
    carries an embargo.

    **The price test comes first and it is why this is nearly free.** No amount of
    hostility can lift the reserve past ``RESERVE_PRICE * (1 + RESERVE_HOSTILITY_GAIN)``, so
    a route priced above that is refused nothing and costs one float comparison. At the
    measured distribution -- median 0.108 against a widest-possible reserve of 0.081 -- that
    is the great majority of routes, and only the floor-pinned tail reaches a query.
    """
    if not enabled() or unit_price <= 0:
        return None
    # A route can be worth refusing at all only if the terms are inside the widest reserve
    # any relationship could produce. One comparison, and it short-circuits most of a map.
    if unit_price > RESERVE_PRICE * (1.0 + RESERVE_HOSTILITY_GAIN) + _PRICE_EPSILON:
        return None

    hostile = pricing.hostility(conn, world_id, seller, buyer)
    reserve = RESERVE_PRICE * (1.0 + RESERVE_HOSTILITY_GAIN * hostile)
    if unit_price > reserve + _PRICE_EPSILON:
        return None

    key = (seller, buyer)
    if distress_cache is None:
        distress = pricing.seller_distress(conn, world_id, seller, buyer, lanes)
    else:
        if key not in distress_cache:
            distress_cache[key] = pricing.seller_distress(
                conn, world_id, seller, buyer, lanes)
        distress = distress_cache[key]
    if distress < DISTRESS_FLOOR:
        # It has nowhere else to put the cargo. It takes what it is offered -- which is the
        # distress term already doing its job, and refusing here would undo it.
        return None

    because: list[str] = []
    if unit_price <= pricing.FLOOR_PRICE + _PRICE_EPSILON:
        because.append(
            f"the terms are at the floor and the seller needs its own {good} more"
        )
    else:
        because.append("the terms are below what the seller will ship for")
    if hostile > 0.25:
        because.append("and relations are bad enough that it would rather keep the cargo")
    return Withdrawal(
        unit_price=round(unit_price, 4),
        reserve=round(reserve, 4),
        distress=round(distress, 4),
        hostility=round(hostile, 4),
        because=tuple(because),
    )


def explain(withdrawal: Withdrawal, good: str) -> str:
    """One line a player can read, in the shape :func:`atlas.engine.pricing.explain` uses.

    Says *withdrew* rather than *cut*, because the ships could sail and the buyer would
    have paid. Somebody chose.
    """
    head = f"the seller withdrew at {withdrawal.unit_price:.2f} a unit of {good} a month"
    if not withdrawal.because:
        return head
    return head + ": " + " ".join(withdrawal.because)
