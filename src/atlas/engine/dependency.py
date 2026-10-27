"""Who supplies you, what it costs, what happens if it stops, and what you can do.

The trade layer works and is almost entirely invisible. A player watches a turn report say
*Karthos: short of steel*, and nothing anywhere in the program tells them **who** was
supposed to send the steel, **how much** of their supply that one lane carried, **why** it
stopped, or **what a government could do about it**. The economy is therefore weather: it
happens to you, and the only available response is to play on and hope.

This module is the answer to that, and it is deliberately nothing else:

**Pure derivation. It writes nothing, ever.** Not a diff, not a row, not a memo. Every
number here is read out of the world or computed from what was read, and a fingerprint
taken across a call to any function in this module is unmoved. That is asserted rather
than promised -- see ``tests/test_the_lens_that_shows_who_supplies_you.py``.

**It re-derives; it does not re-decide.** The one failure mode a panel like this has is
disagreeing with the engine: a lens that says a lane is shipping 4.0 a month while
:func:`atlas.engine.trade.assess_trade` moves 2.1 is worse than no lens at all, because a
player optimises against the number they were shown. So :func:`preview_settlement` walks
the same routes in the same order, asks the same questions in the same sequence, and draws
down the same import allowance as the settlement itself -- and the reconciliation tests run
both and compare route by route. Where a constant or a rule is needed it is *imported* from
the module that owns it, private name and all, rather than copied. A copied constant is the
drift this whole module exists to prevent.

**Visibility is a rule here, not a courtesy.** A government knows its own stores exactly.
It does not know a foreign power's, and the honest shape of that is
:func:`atlas.engine.intelligence.estimate_of` -- a band from the viewer's own service,
displaced before it is opened, never centred on the truth. A trade route is known to its
two parties, which is the rule :func:`atlas.map.query._trade` already holds the map to, so
a lens pointed at a foreign power shows the lanes the viewer is at the other end of and
says *unknown* about the rest. That is a thinner panel than the one a player gets for their
own country, and it is the correct thinness.

**Three questions, in the order a cabinet asks them.** Who do we depend on, what does it
cost us, and what breaks if it stops. :class:`DependencyLens` carries the answers to all
three plus the two that follow from them -- the three largest vulnerabilities, and the
responses a government actually has.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from . import consent, embargo, pricing, refusal
from .capabilities import by_type
from .production import _INDUSTRY_FROM_ECONOMY, _INDUSTRY_PER_SLOT
from .trade import (
    _STARVED_CAPACITY,
    GOODS,
    IMPORT_BUDGET_SHARE,
    SETTLEMENT_UNITS,
    _reprice_period,
    consent_columns_present,
    demand_of,
    dwell_column_present,
    is_unreachable,
    pricing_columns_present,
    route_is_open,
    settle_lane,
    shortfall_of,
)

__all__ = [
    "CONCENTRATED",
    "RESPONSES",
    "STATIC_CAVEAT",
    "STATUSES",
    "Counterfactual",
    "DependencyLens",
    "GoodPosition",
    "Response",
    "RouteView",
    "Settlement",
    "Vulnerability",
    "lens_for",
    "preview_settlement",
    "runway_of",
]


# --------------------------------------------------------------------------- vocabulary

#: Every way a lane can stand, and what each means. **Nine, where the brief asked for
#: five**, and the extra four are not embellishment: each is a different fact about the
#: world with a different remedy, and collapsing two of them would be exactly the failure
#: :mod:`atlas.engine.embargo` was written to prevent -- "the oil stopped" cannot tell a
#: market from an ultimatum.
#:
#: The order is the order the engine asks the questions in, which is also roughly the order
#: from *nothing can be done this turn* to *this is a price somebody would reconsider*.
STATUSES: dict[str, str] = {
    "open": "shipping",
    "at_war": "the two of you are at war, and belligerents do not trade",
    "sanctioned": "a body one of you belongs to has sanctioned the other",
    "blockaded": "the lane is cut -- no sea route between you is passable",
    "unreachable": "nothing on the map can reach one of you",
    "embargoed": "the seller is refusing to ship, at its own cost",
    "suspended": "stopped commercially: the two sides' terms no longer overlap",
    "priced_out": "the lane is open and nobody is willing at this number",
    "barter": "no terms have been settled; the cargo moves and no money does",
}

#: Where one supplier or customer stops being one of several and starts being a dependency.
#: **A reading dial, not a balance constant**: nothing in the engine consults it, it changes
#: which sentence a panel prints and nothing about what is true. Three fifths, because a
#: partner that carries more of a good than everybody else put together is a partner that
#: can decide your industry's month.
#: How many turns a foreign estimate stands before the service will offer a new one.
#: **A reading dial with a disclosure argument behind it.** `atlas.engine.intelligence`
#: re-seeds on the turn, so an unthrottled reader gets an independent draw every turn and
#: twenty-four of them average to the truth within 4%. Six, which is
#: `atlas.engine.pricing.REPRICE_EVERY_MONTHS` -- the interval this economy already treats
#: as "long enough that the file is worth reopening" -- so a fresh estimate arrives on
#: roughly the cadence a fresh price does.
BAND_PERIOD = 6

CONCENTRATED = 0.60

#: How much of a good's monthly draw the store has to cover before the runway stops being
#: worth saying. A reading dial, as above.
RUNWAY_WORTH_SAYING = 12.0

#: The five things a government can actually do about an economic dependency, keyed by the
#: mechanism that carries each. **Every one of these is a lever the engine already acts
#: on**, which is the rule the refusal sweep holds every explanation in this program to: a
#: panel that reports a problem and names no lever teaches a player that the economy is
#: weather.
RESPONSES: dict[str, str] = {
    "seek_supplier": "open a second lane for the good",
    "repair_relations": "mend the relationship the price is being built on",
    "protect_the_lane": "put a fleet on the water the cargo crosses",
    "conserve": "burn less of it",
    "restrict_exports": "stop selling it to somebody",
    # **The sixth, and it is about money rather than about a good.** The five above are the
    # answers to *somebody supplies me and I cannot do without them*; a treasury running out
    # is a different problem with a different lever, and folding it into "conserve" produced
    # the sentence "burn less what you import" -- which is neither English nor a lever.
    "spend_less": "stand down what the treasury cannot pay for",
}


# ------------------------------------------------------------------------------- shapes


@dataclass(frozen=True, slots=True)
class Settlement:
    """What one standing route will actually do this round, decided the engine's way.

    ``shipped`` and ``bill`` are for the round the caller named, not for a month: they are
    already scaled by the span, exactly as :func:`atlas.engine.trade.assess_trade` scales
    them, so a weekly turn reports a week's cargo.
    """

    route_id: str
    seller_id: str
    buyer_id: str
    good: str
    #: The standing agreement, per month, straight off the row.
    volume: float
    #: What the terms stand at per unit per month. ``0.0`` is barter.
    unit_price: float
    #: What moves this round.
    shipped: float
    #: What the buyer pays and the seller receives this round, **in whole
    #: :data:`atlas.engine.trade.SETTLEMENT_UNITS`** -- the same integer
    #: :func:`atlas.engine.trade.assess_trade` moves the two treasuries by.
    #:
    #: Carried as the integer and not as money because that is the only way a preview can
    #: be checked against a settlement *exactly*. Netting money would put a rounding step
    #: between the panel and the treasury, which is the defect one layer down --
    #: :data:`atlas.engine.trade.SETTLEMENT_UNITS` records what it was worth there -- and
    #: a panel is where a player reads the number, so it is worth no less here. Sum these
    #: and divide once; see :attr:`bill`.
    bill_units: int
    #: A key of :data:`STATUSES`.
    status: str
    #: The engine's own sentence for why, where there is one. **Everything it knows**,
    #: including both sides' numbers -- so it is only ever shown to a reader entitled to
    #: the side it describes. :attr:`why_public` is the version for anyone else.
    why: str = ""
    #: The same fact with the other side's position taken out: who stopped, and at what
    #: the lane was standing at, which both parties know because it is their agreement.
    #: Never carries a figure derived from a nation's own rows.
    why_public: str = ""
    #: Which side ended it -- ``buyer``, ``seller`` or ``""``. What decides which of the
    #: two sentences above a given viewer may read.
    ended_by: str = ""
    #: **How many reference months this round covers**, so a reader can put :attr:`shipped`
    #: and :attr:`bill` back on the clock the rest of the lens is on.
    #:
    #: Carried rather than assumed because :func:`preview_settlement` takes ``days`` as a
    #: public parameter while ``volume``, ``unit_price`` and every ``produced``/``demand``
    #: rate this walk is compared against are *per month*. Every caller in the tree pins
    #: thirty days, so the two grains agree today and the divergence was latent -- but a
    #: caller that passed the world's real turn length got a year's cargo divided by a
    #: month's production, and the runways, shares and concentrations built on it were
    #: silently twelve times out. See :attr:`shipped_per_month`.
    span: float = 1.0

    @property
    def shipped_per_month(self) -> float:
        """:attr:`shipped` on the monthly clock, which is the one the lens reads on."""
        return self.shipped / self.span if self.span > 0 else 0.0

    @property
    def bill_per_month(self) -> float:
        """:attr:`bill` on the monthly clock. See :attr:`shipped_per_month`."""
        return self.bill / self.span if self.span > 0 else 0.0

    @property
    def bill(self) -> float:
        """What the buyer pays and the seller receives this round, in money.

        Exact, and deliberately not rounded: the grain is a power of two, so this is an
        exact multiple of it and a caller that sums several gets the figure the treasury
        gets. Round it where it is *displayed* -- which the panel does, once, at the end.
        """
        return self.bill_units / SETTLEMENT_UNITS

    @property
    def moving(self) -> bool:
        return self.shipped > 0


@dataclass(frozen=True, slots=True)
class RouteView:
    """One lane, from the point of view of the nation the lens is pointed at."""

    settlement: Settlement
    #: Whether the cargo is coming *in* to the subject nation.
    inbound: bool
    #: The power at the other end.
    other_id: str
    other_name: str
    #: What share of this nation's monthly supply of the good this lane carries (inbound),
    #: or of its receipts for it (outbound). 0..1.
    share: float
    #: How many other powers could supply this buyer with this good. ``-1`` where nobody
    #: has counted -- a barter world, or an outbound lane, where the question is not asked.
    alternatives: int = -1
    #: **The sentence this viewer may read**, chosen in :func:`_views` rather than in the
    #: interface. A screen that picked between two sentences would be a screen deciding a
    #: disclosure question, and there would then be as many answers as there are screens.
    why: str = ""

    @property
    def route_id(self) -> str:
        return self.settlement.route_id

    @property
    def good(self) -> str:
        return self.settlement.good

    @property
    def status(self) -> str:
        return self.settlement.status


@dataclass(frozen=True, slots=True)
class GoodPosition:
    """Where a nation stands in one of the three goods."""

    good: str
    #: Exact for the viewer's own country; ``None`` when it is somebody else's and only an
    #: estimate is available.
    produced: float | None
    #: What this nation's industry burns a month. **``None`` for a foreign country**, and
    #: that is not fastidiousness: `demand_of` is industry x 0.11, so a published burn is
    #: a published industry -- and it was the denominator that turned two withheld figures
    #: back into themselves through `dependency` and `cover`.
    demand: float | None
    stockpile: float | None
    #: What the estimate says, when the figure itself is not the viewer's to have. Empty
    #: for the viewer's own country, where the number above is the truth.
    estimated: str = ""
    #: What arrives and what leaves each month, at the volumes actually moving.
    imported: float = 0.0
    exported: float = 0.0
    #: Months of cover at the current net draw. ``None`` means it is not running down,
    #: or -- for a foreign country -- that the viewer cannot see the store to say.
    runway: float | None = None
    #: The share of its own monthly demand this nation's ground cannot cover, 0..1.
    #: ``None`` for a foreign country: it is ``(demand - produced) / demand``, and a
    #: viewer holding it and the burn holds the production.
    dependency: float | None = 0.0
    #: Who carries the most of it, and how much of the *supply* that is.
    top_supplier: str = ""
    top_supplier_share: float = 0.0
    top_customer: str = ""
    top_customer_share: float = 0.0
    #: How concentrated the supply is: the sum of the squared shares, 1.0 for one supplier.
    concentration: float = 0.0
    suppliers: int = 0
    customers: int = 0
    #: What :func:`atlas.engine.trade.supply_ratio` currently answers -- the number the
    #: yards are actually being throttled by. ``None`` for a foreign country: it is
    #: ``stockpile / demand``, which returns the store to anyone holding the burn, and it
    #: does so most precisely for the country nearest to running out.
    cover: float | None = 1.0


#: What a counterfactual on this panel is, in the one phrase that has to travel with every
#: one of them. **It is a static, first-order consequence**: the world exactly as it stands
#: today, minus one lane, with nothing else allowed to move. Nobody opens a second lane,
#: nobody raises their own production, nobody reprices, and the store does not go on growing
#: while the player thinks about it.
#:
#: That is not a hedge, it is the definition -- and it was measured. Taking the estimate on
#: one turn and cutting the lane four turns later put the drain six months out instead of
#: the predicted 3.6, because the store had been *growing* by 1.65 a month in the meantime
#: and there was more of it to drain. The estimate was right about the world it was taken
#: from and stale about the world it was tested in, which is exactly what *static* means.
STATIC_CAVEAT = (
    "A static, first-order estimate: this world minus that one lane, with nothing else "
    "moving. Opening another lane, raising your own production, or simply waiting while "
    "the store fills all change it."
)


@dataclass(frozen=True, slots=True)
class Counterfactual:
    """What this nation's industry can build now, and what it could build without a lane.

    **A static, first-order consequence, and it says so on the panel** -- see
    :data:`STATIC_CAVEAT`. Every figure here is the world as it stands minus one lane; the
    substitution a government would actually reach for is precisely what the *responses*
    beside it are, and any of them changes this number.

    **In pace, not in slots, since 2026-09-18.** A shortage no longer reaches the order book
    -- :func:`atlas.engine.production.industrial_capacity` is industry alone -- and lands
    only on the share of a month each yard works, which is
    :func:`atlas.engine.trade.shortfall_of`. So what a lane is worth to the yards is a
    *pace*, 0.35 to 1.0, and a slot count here would read "no change" for every lane in the
    game. The count is still on the lens, as the thing the pace is applied to.

    **Two horizons, because the engine has two.** Losing a supplier does not slow a
    country's yards this month -- the pace reads the *store*, and the store is full. It
    slows them when the store runs out, which is ``months`` away. Reporting only the second
    number would be alarmism; reporting only the first would be the reassurance that gets a
    war economy killed.

    **And the two numbers being compared are on the same basis, which took a correction.**
    The first draft set the after-figure from the flows and compared it against the current
    one from the stores, so *every* lane read as costing something -- what it was actually
    measuring was that a full store flatters the current figure. Worse, it attributed the
    drop to whichever lane was being asked about even when the binding good was a different
    one entirely: on a fixture where steel drained a quarter of a unit a month against a
    store of forty -- thirteen years of cover -- removing an *oil* lane reported a loss,
    because the steady state of a store that empties eventually is empty. ``pace_year`` is
    the fix: the same flow reading with nothing removed, over the same horizon, so the delta
    is what *this lane* costs and nothing else.
    """

    route_id: str
    #: What is lost, in the words a player would use.
    label: str
    good: str
    #: How much of a month the yards work today, from the stores actually held. The
    #: engine's own number: :func:`atlas.engine.trade.shortfall_of`.
    pace_now: float
    #: What they will work within :data:`RUNWAY_WORTH_SAYING` months on current flows, with
    #: every lane still standing. The baseline the counterfactual is a delta against.
    pace_year: float
    #: And the same reading with this lane gone.
    pace_after: float
    #: Months of cover before the last number is the true one. ``None`` when the lane's
    #: loss never bites -- the country covers its own demand without it.
    months: float | None

    @property
    def bites(self) -> bool:
        return self.pace_after < self.pace_year - 1e-9


@dataclass(frozen=True, slots=True)
class Response:
    """Something a government can actually do, and whether it can do it now."""

    kind: str
    #: What to do, naming the counterparty where there is one.
    text: str
    #: The mechanism that carries it, in one clause: what part of the engine acts on this.
    how: str
    #: The order category a player would give, where an existing one genuinely answers it,
    #: and ``""`` where none does. Never a category picked because it was nearest.
    order_category: str = ""
    available: bool = True
    #: Why not, when it is not.
    blocked_because: str = ""


@dataclass(frozen=True, slots=True)
class Vulnerability:
    """One of the ways this economy can be hurt, with the evidence and the answer."""

    #: What it is, in one line.
    headline: str
    #: The numbers that put it here.
    because: str
    #: 0..1. Only ever used to rank three of these against each other.
    severity: float
    response: Response
    good: str = ""
    other_id: str = ""
    route_id: str = ""


@dataclass(frozen=True, slots=True)
class DependencyLens:
    """Everything the lens shows about one nation, derived and nothing else."""

    nation_id: str
    name: str
    turn: int
    #: Whether the viewer is looking at their own country. Everything about what may be
    #: shown exactly follows from this one flag.
    own: bool
    #: Whether this world settles a price at all. False is barter, and most of the money
    #: columns are then empty by construction rather than by accident.
    priced: bool
    goods: tuple[GoodPosition, ...] = ()
    routes: tuple[RouteView, ...] = ()
    #: Per month, at the volumes actually moving.
    bill: float = 0.0
    receipts: float = 0.0
    #: What the treasury actually moves by in a month, **netted the way the settlement nets
    #: it**: the lanes' whole :data:`atlas.engine.trade.SETTLEMENT_UNITS`, summed and
    #: divided once, exactly as :func:`atlas.engine.trade.assess_trade` builds ``owed``.
    #: Not ``receipts - bill``, which rounds twice on two separate sums and can land a
    #: penny away from what the engine writes -- and a panel a penny out from the ledger is
    #: a panel a player will find the discrepancy in. Exact, and not rounded here: this is
    #: the figure, and the screen is where it is shortened.
    settled: float = 0.0
    #: ``None`` on a foreign page: it is derived from that nation's exact industry.
    capacity_now: int | None = 0
    #: The share of a month those yards work **for want of materials** at the stores held
    #: -- the same number :func:`atlas.engine.trade.shortfall_of` gives the engine. It is
    #: not the whole pace: industrialists dragging their feet
    #: (:func:`atlas.engine.constituencies.industrial_throughput`) slow the same yards,
    #: have the opposite remedy, and are reported apart as the turn's ``dragging``. This
    #: lens is about supply, so it says only the supply half, and says so on the panel.
    #: ``None`` on a foreign page, for the reason ``capacity_now`` is.
    pace_now: float | None = 1.0
    counterfactuals: tuple[Counterfactual, ...] = ()
    vulnerabilities: tuple[Vulnerability, ...] = ()
    responses: tuple[Response, ...] = ()
    #: What the lens could not see, said out loud rather than left as a blank.
    unknowns: tuple[str, ...] = ()

    @property
    def net(self) -> float:
        return self.settled

    @property
    def stopped(self) -> tuple[RouteView, ...]:
        return tuple(view for view in self.routes if view.status != "open")


# ------------------------------------------------------------- the settlement, previewed


#: One turn's preview, kept so that the lens, the warnings and the forecast do not each pay
#: for the same walk. Keyed on the world, the turn, the round length, **the last diff
#: written** and **the four trade flags** -- the last two being what make it correct rather
#: than merely fast: any change to the world at all lands a diff, so a rewind, a replay or a
#: fresh turn all miss the memo, and see :func:`_flags` for the changes that land no diff at
#: all.
#:
#: **And on the connection itself, which is not paranoia -- it was a real defect.** Two
#: different worlds may carry the same id: a fork does by construction, and a test suite
#: builds a dozen ``:memory:`` worlds all called the same thing. Without the connection in
#: the key, the second world of a given id and turn with the same number of diffs in it
#: read back the *first* world's lanes, and the panel confidently described somebody else's
#: economy. Holding the connection keeps it from being collected and its id reused, which
#: is what makes the identity trustworthy; the cache is cleared whole at
#: :data:`_PREVIEW_MEMO_CAP` so nothing is held for long.
_PREVIEW_MEMO: dict[tuple, tuple[Settlement, ...]] = {}
_PREVIEW_MEMO_CAP = 8


def world_revision(conn: sqlite3.Connection, world_id: str) -> int:
    """How far this world has been written to: the id of the last diff applied to it.

    **Monotonic forward and non-monotonic backward, which is exactly what is wanted.** A
    turn moves it up; a rewind deletes the diffs it undid and moves it *down*. So any
    change to the world -- in either direction -- changes this number, and a reader holding
    the number it last read at can tell whether what it is showing is still true.

    Used for two things that are the same thing: the preview memo, which must miss after a
    rewind rather than serve a world that no longer exists, and a panel the player has
    pinned open, which must reload for the same reason.
    """
    row = conn.execute(
        "SELECT COALESCE(MAX(d.id), 0) AS at FROM diff d"
        "  JOIN diff_batch b ON b.id = d.batch_id WHERE b.world_id = ?", (world_id,)
    ).fetchone()
    return int(row["at"]) if row is not None else 0


def _flags() -> tuple[bool, bool, bool, bool]:
    """The four switches the walk's answer depends on, as part of the memo's identity.

    **A flag move is not a diff.** :func:`world_revision` sees everything that happens to
    the *world*, and that is what makes the memo correct for a turn, a rewind or a replay
    -- but ``pricing.set_enabled`` and its three siblings change what the same world's
    routes do while writing nothing to it, so a reader that toggled one between two reads
    of the same turn was handed the walk taken under the other setting. Nothing in a played
    game moves these mid-turn; every A/B harness and a good part of the test suite does
    exactly that, and the stale answer is indistinguishable from the fix not working.

    All four, because all four reach the walk: ``pricing`` decides whether there are terms
    at all, ``embargo`` answers before the market in :func:`_walk` itself, and ``consent``
    and ``refusal`` are both consulted inside the shared
    :func:`atlas.engine.trade.settle_lane` -- ``refusal`` through
    :func:`atlas.engine.consent.band`, which is why it is here despite this module never
    naming it anywhere else.
    """
    return (pricing.enabled(), consent.enabled(), refusal.enabled(), embargo.enabled())


def _memo_token(conn: sqlite3.Connection, world_id: str) -> int:
    return world_revision(conn, world_id)


def preview_settlement(
    conn: sqlite3.Connection, world_id: str, *, turn: int, days: float = 30,
) -> tuple[Settlement, ...]:
    """What every standing route in this world will do, without doing any of it.

    **The same walk :func:`atlas.engine.trade.assess_trade` takes, in the same order.** A
    route's viability is decided by the same three tests; an embargo answers before the
    market, exactly as it does there; the seller can only ship what it has, and what it
    shipped on an earlier route is gone by the time a later one is reached; and the buyer's
    import allowance is drawn down in route-id order, so a buyer that spent its money on the
    first cargo cannot pay for the second. Getting any of that wrong would make the lens
    disagree with the settlement, which is the one thing it must never do.

    **One deliberate difference: the standing terms stand.** The engine reprices on a
    stagger, and this does not chase that -- it reports what the route is priced at now,
    which is what the row says, what the briefing prints, and what a government has actually
    agreed to. A route the engine has *never* looked at is priced here exactly as the engine
    would price it, because reporting a bill of zero for a lane that is about to be settled
    would be worse than a stale number: it would be a wrong one. :attr:`RouteView.settlement`
    therefore agrees with the settlement on every turn except the one where terms move, and
    the lens says when terms are due.
    """
    token = (conn, world_id, turn, round(float(days), 4), _memo_token(conn, world_id),
             _flags())
    if token in _PREVIEW_MEMO:
        return _PREVIEW_MEMO[token]
    found = _walk(conn, world_id, turn=turn, days=days)
    if len(_PREVIEW_MEMO) >= _PREVIEW_MEMO_CAP:
        _PREVIEW_MEMO.clear()
    _PREVIEW_MEMO[token] = found
    return found


#: Why a lane that is open on its terms still moved nothing, in the panel's words. Keyed on
#: :attr:`atlas.engine.trade.LaneOutcome.blocked`, so the reasons the engine distinguishes
#: are the reasons the player is given -- an unbillable cargo is refused rather than shipped
#: free, and saying "the seller has none of it" about one would be the panel inventing a
#: cause the world does not have.
_NOTHING_MOVES = {
    "budget": "nothing moves: the buyer's month of import money is already spent",
    "stock": "the seller has none of it to send",
    "unbillable": "the cargo is too small to bill, so it does not move",
    "unstorable": "the buyer keeps no store of it, so there is nowhere for it to land",
}


def _walk(  # noqa: PLR0912, PLR0915 - one loop, mirroring one loop, and it must not diverge
    conn: sqlite3.Connection, world_id: str, *, turn: int, days: float,
) -> tuple[Settlement, ...]:
    """See :func:`preview_settlement`. Split out only so the memo has something to call."""
    from .pacing import compounded, over, span_of

    span = span_of(days)
    priced = pricing.enabled() and pricing_columns_present(conn)
    recordable = priced and consent_columns_present(conn)

    try:
        rows = conn.execute(
            "SELECT nation_id, good, produced, stockpile FROM resource"
            " WHERE world_id = ? ORDER BY nation_id, good", (world_id,)).fetchall()
    except sqlite3.OperationalError:
        return ()
    stock = {(str(r["nation_id"]), str(r["good"])): float(r["stockpile"]) for r in rows}
    produced = {(str(r["nation_id"]), str(r["good"])): float(r["produced"]) for r in rows}
    moved: dict[tuple[str, str], float] = {key: over(rate, span)
                                           for key, rate in produced.items()}

    columns = "id, seller_id, buyer_id, good, volume"
    if priced:
        columns += ", unit_price, priced_turn"
    if recordable:
        columns += ", suspended_turn, suspended_by"
    #: The dwell clause reads it, so the preview has to select it or the shared decision
    #: would be handed a row it cannot ask its own question of.
    dwelling = recordable and dwell_column_present(conn)
    if dwelling:
        columns += ", suspended_date"
    routes = conn.execute(
        f"SELECT {columns} FROM trade_route"  # noqa: S608 - `columns` is a literal
        " WHERE world_id = ? ORDER BY id", (world_id,)).fetchall()
    if not routes:
        return ()
    #: **The engine's own repricing clock, not a second one.** A route's terms are
    #: reconsidered on a stagger counted in turns, so a preview that did not know the
    #: period would miss every repricing that is due -- which is one of the three ways the
    #: old copy of this walk drifted from the settlement.
    reprice_period = _reprice_period(span_of(days))
    #: What the dwell compares against. The world's own date, because the preview is a
    #: statement about the round that is about to run, not about a hypothetical one.
    row = conn.execute("SELECT world_date FROM world WHERE id = ?", (world_id,)).fetchone()
    today = str(row["world_date"]) if row is not None else ""
    #: Empty, and it stays empty: a preview models the *next* round, so no route has had
    #: its terms reconsidered yet. The engine passes what it has already settled.
    considered_this_turn: set[str] = set()

    embargoes = embargo.bites(conn, world_id) if priced else {}
    #: **A sentence a player reads names a country, not a row.** The embargo line said
    #: "sup is refusing to ship it", which is the primary key of a table.
    name_of = _names(conn, world_id)
    lanes: dict[tuple[str, str], tuple[bool, str]] = {}
    alternatives_cache: dict[tuple[str, str, str], int] = {}
    distress_cache: dict[tuple[str, str], float] = {}
    allowance = compounded(IMPORT_BUDGET_SHARE, span)
    budget_left = {
        str(row["id"]): max(0.0, float(row["treasury"] or 0.0)) * allowance
        for row in (conn.execute(
            "SELECT id, treasury FROM nation WHERE world_id = ? ORDER BY id", (world_id,))
            if priced else ())
    }

    found: list[Settlement] = []

    def settled(row, *, status: str, why: str = "", shipped: float = 0.0,
                price: float = 0.0, bill_units: int = 0, why_public: str = "",
                ended_by: str = "") -> Settlement:
        return Settlement(
            route_id=str(row["id"]), seller_id=str(row["seller_id"]),
            buyer_id=str(row["buyer_id"]), good=str(row["good"]),
            volume=float(row["volume"] or 0.0), unit_price=round(price, 4),
            shipped=round(shipped, 3), bill_units=bill_units, status=status, why=why,
            why_public=why_public or why, ended_by=ended_by, span=span)

    for route in routes:
        seller, buyer = str(route["seller_id"]), str(route["buyer_id"])
        good, volume = str(route["good"]), over(float(route["volume"] or 0.0), span)
        pair = (seller, buyer)
        if pair not in lanes:
            lanes[pair] = route_is_open(conn, world_id, seller, buyer)
        ok, why = lanes[pair]
        if not ok:
            found.append(settled(route, status=_status_for(why), why=why))
            continue

        available = max(0.0, stock.get((seller, good), 0.0) + moved.get((seller, good), 0.0))
        shipped = min(volume, available)

        stopper = embargo.stopped_by(embargoes, seller, buyer, good) if priced else ""
        if stopper:
            found.append(settled(
                route, status="embargoed",
                why=f"{name_of.get(seller, seller)} is refusing to ship it, and "
                    "accepting the loss of what it was earning"))
            continue

        # **The engine's own decision, asked for rather than reproduced.** Everything from
        # the terms down to the shipment is :func:`atlas.engine.trade.settle_lane`, which
        # is the same call `assess_trade` makes. What is left here is the wording.
        outcome = settle_lane(
            conn, world_id, route, turn=turn, date=today, available=available,
            volume=volume, budget=budget_left.get(buyer, 0.0), priced=priced,
            recordable=recordable, dwelling=dwelling, reprice_period=reprice_period,
            lanes=lanes, alternatives_cache=alternatives_cache,
            distress_cache=distress_cache,
            considered_this_turn=considered_this_turn,
            storable=(buyer, good) in stock)
        shipped, unit_price = outcome.shipped, outcome.unit_price

        if outcome.status == "barter":
            if shipped > 0:
                moved[(seller, good)] = moved.get((seller, good), 0.0) - shipped
                moved[(buyer, good)] = moved.get((buyer, good), 0.0) + shipped
            found.append(settled(route, status="barter", shipped=shipped,
                                 why="no terms have been settled on this lane"))
            continue

        if outcome.status == "suspended":
            if outcome.blocked == "dwell":
                # The terms agree again and the cargo still is not moving. A different
                # fact from a disagreement, and the engine's own sentence for it.
                found.append(settled(
                    route, status="suspended", price=unit_price,
                    why=f"the terms overlap again, but shipments stay stopped -- "
                        f"{outcome.dwell}"))
                continue
            # **Since when, where the schema can carry it.** A stoppage that has stood for
            # four turns and one that began this month are different facts about the same
            # lane, and only the column knows which. A schema-46 world has the decision
            # and nowhere to write it down, and simply says less.
            since = int(route["suspended_turn"]) if recordable else -1
            stood = (f" (stopped since turn {since}; the agreement stands and nothing "
                     "was breached)" if since >= 0 else "")
            found.append(settled(
                route, status="suspended", price=unit_price,
                ended_by=outcome.state.ended_by,
                why=consent.explain(outcome.state, good) + stood,
                why_public=_without_the_other_side(outcome.state, good) + stood))
            continue

        if outcome.status == "priced_out":
            found.append(settled(
                route, status="priced_out", price=unit_price,
                why=f"priced out at {unit_price:.2f}"))
            continue

        if shipped <= 0:
            found.append(settled(
                route, status="open", price=unit_price,
                why=_NOTHING_MOVES.get(
                    outcome.blocked, "the seller has none of it to send")))
            continue

        moved[(seller, good)] = moved.get((seller, good), 0.0) - shipped
        moved[(buyer, good)] = moved.get((buyer, good), 0.0) + shipped
        budget_left[buyer] = max(0.0, budget_left.get(buyer, 0.0) - outcome.bill)
        found.append(settled(route, status="open", shipped=shipped, price=unit_price,
                             bill_units=outcome.bill_units))

    return tuple(found)


def _without_the_other_side(state, good: str) -> str:
    """Why the lane stopped, with nothing in it that belongs to the other government.

    :func:`atlas.engine.consent.explain` is written for the engine's report, where there is
    no viewer and everything is on the table: it names the price the lane stands at, the
    number the far side would accept instead, and the reasons that side reached it -- *it
    produces almost no oil of its own; it has no other supplier it can reach*. Those last
    two are a foreign ministry's estimate everywhere else on this panel, and the sentence
    was handing them over as fact.

    What survives is what a counterparty genuinely learns by being refused: that it was
    refused, by which side, and at what number the agreement was standing. The price is
    theirs jointly -- it is written on the route both of them signed.
    """
    price = f"{state.price:.2f}"
    if state.ended_by == "buyer":
        return f"the buyer has stopped taking {good} at {price} a unit"
    if state.ended_by == "seller":
        return f"the seller has stopped shipping {good} at {price} a unit"
    return f"{good} is not moving at {price} a unit"


def _status_for(why: str) -> str:
    """:func:`atlas.engine.trade.route_is_open`'s refusal, as a key of :data:`STATUSES`.

    Matched on the strings that function actually returns rather than on a substring, so a
    reworded refusal shows up as ``blockaded`` -- the safe default, because every remaining
    reason it can give is a lane that cannot be walked.
    """
    if why == "at war":
        return "at_war"
    if why == "under sanction":
        return "sanctioned"
    if why in ("the buyer holds no ground", "the seller holds no ground"):
        return "unreachable"
    return "blockaded"


# ------------------------------------------------------------------------- the readings


def runway_of(stockpile: float, draw: float) -> float | None:
    """Months of cover at a net monthly draw. ``None`` when the store is not running down.

    ``draw`` is what leaves *net* of what arrives, so a country covering its own demand has
    no runway to report and a country covering half of it has twice the months a country
    covering none would.
    """
    if draw <= 0:
        return None
    return round(max(0.0, stockpile) / draw, 2)


def _capacity_at(industry: float) -> int:
    """:func:`atlas.engine.production.industrial_capacity`, from an industry already read.

    Industry alone: a shortage stopped reaching the order book on 2026-09-18 and lands on
    :func:`_pace_at` instead.
    """
    return max(1, round(industry / _INDUSTRY_PER_SLOT))


def _pace_at(cover: float) -> float:
    """:func:`atlas.engine.trade.shortfall_of`, asked a counterfactual.

    The same arithmetic, with the worst good's cover supplied rather than read -- so the pace
    the lens shows for *now* is the pace the yards are working at, and the one it shows for
    *after* is the same function answering a different world.
    """
    return _STARVED_CAPACITY + (1.0 - _STARVED_CAPACITY) * max(0.0, min(1.0, cover))


def _industry_of(conn: sqlite3.Connection, nation_id: str) -> float:
    row = conn.execute(
        "SELECT industry, economy FROM nation WHERE id = ?", (nation_id,)).fetchone()
    if row is None:
        return 0.0
    industry = float(row["industry"] or 0.0)
    return industry or float(row["economy"] or 0.0) * _INDUSTRY_FROM_ECONOMY


def _names(conn: sqlite3.Connection, world_id: str) -> dict[str, str]:
    return {
        str(row["id"]): str(row["name"])
        for row in conn.execute(
            "SELECT id, name FROM nation WHERE world_id = ?", (world_id,))}


def _concentration(shares: list[float]) -> float:
    """The sum of the squared shares. One supplier is 1.0; four equal ones are 0.25.

    A Herfindahl index, which is the ordinary way to say *how few hands is this in* and has
    the property the panel wants: it moves when a big supplier gets bigger, not only when
    the count changes.
    """
    return round(sum(share * share for share in shares), 4)


def _can_order(
    conn: sqlite3.Connection, world_id: str, viewer: str, action_type: str, target: str = ""
) -> bool:
    """Whether this player could give this order, as the capability itself decides it.

    The same predicate :mod:`atlas.engine.attention` consults, and for the same reason: a
    panel with its own opinion about what is orderable is a second answer to a question the
    catalogue has already settled.
    """
    capability = by_type(action_type)
    if capability is None:
        return False
    if capability.available is None:
        return True
    try:
        return bool(capability.available(conn, world_id, viewer, target))
    except sqlite3.Error:
        return True


# ------------------------------------------------------------------------ the responses


#: Why an order the catalogue refuses is refused, in the player's words. **A response that
#: is not available must say why**, or the panel is a list of greyed-out controls and a
#: player learns to stop reading it. The predicate itself is
#: :attr:`atlas.engine.capabilities.Capability.available`; these are the plain-English
#: readings of the three it can decline for this panel.
_WHY_NOT: dict[str, str] = {
    "propose_treaty": "there is nobody you can sign with -- an armed-neutral doctrine or "
                      "no reachable counterparty",
    "diplomatic_message": "there is nobody within reach to write to",
    "move_units": "you have no formations to move",
}


def _seek_supplier(
    conn: sqlite3.Connection, world_id: str, viewer: str, good: str, reach: int | None,
    seller_name: str,
) -> Response:
    """Open a second lane. The one answer that ends a dependency rather than repricing it.

    ``reach`` is :func:`atlas.engine.pricing.alternatives` -- the count of powers with a
    surplus of this good that a cargo could actually reach this buyer from. Zero is not
    *no answer*; it is a different and worse answer, and it says so.

    ``None`` is a third thing again: **nobody has counted**. That count is a sea walk per
    candidate seller, which is most of what the settlement preview costs, and
    :func:`warnings_for` deliberately does not pay it -- it runs inside every turn's
    attention read. A lever offered without a count is still a lever; a lever offered with
    a count of zero that nobody measured would be the most alarming sentence on the panel,
    printed out of an absence.
    """
    if reach is None:
        can = _can_order(conn, world_id, viewer, "propose_treaty")
        return Response(
            kind="seek_supplier",
            text=f"Open a second {good} lane, beside {seller_name}",
            how="a trade agreement opens a lane, and a buyer with somewhere else to go "
                "pays less. The lens counts who could actually reach you",
            order_category="propose_treaty",
            available=can,
            blocked_because="" if can else _WHY_NOT["propose_treaty"])
    if reach <= 0:
        return Response(
            kind="seek_supplier",
            text=f"Find {good} somewhere other than {seller_name}",
            how="a trade agreement opens a lane, and the engine decides what it "
                "carries from what one of you digs up and the other cannot get enough of",
            order_category="propose_treaty",
            available=False,
            blocked_because="no other power with a surplus of this good can reach you. "
                            "Until one can, the price carries a captive buyer's premium "
                            "and there is nowhere to take your business")
    plural = "supplier" if reach == 1 else "suppliers"
    can = _can_order(conn, world_id, viewer, "propose_treaty")
    return Response(
        kind="seek_supplier",
        text=f"Open a second {good} lane -- {reach} other {plural} can reach you",
        how="a trade agreement opens a lane, and a buyer with somewhere else to go pays "
            "less: the squeeze term in the price falls with every alternative",
        order_category="propose_treaty",
        available=can,
        blocked_because="" if can else _WHY_NOT["propose_treaty"])


def _repair_relations(
    conn: sqlite3.Connection, world_id: str, viewer: str, other_id: str, other_name: str
) -> Response:
    can = _can_order(conn, world_id, viewer, "diplomatic_message", other_id)
    return Response(
        kind="repair_relations",
        text=f"Mend relations with {other_name}",
        how="hostility is the one term that raises the price and lowers what you will pay "
            "at the same time, so no repricing can reconcile it -- only the relationship",
        order_category="diplomatic_message",
        available=can,
        blocked_because="" if can else _WHY_NOT["diplomatic_message"])


def _protect_the_lane(
    conn: sqlite3.Connection, world_id: str, viewer: str, other_name: str
) -> Response:
    can = _can_order(conn, world_id, viewer, "move_units")
    return Response(
        kind="protect_the_lane",
        text=f"Put a fleet on the water between you and {other_name}",
        how="a cargo crosses water exactly as an army's supply does, and whoever "
            "commands that water decides whether it gets through -- either end may be the "
            "one that escorts it",
        order_category="move_units",
        available=can,
        blocked_because="" if can else _WHY_NOT["move_units"])


def _conserve(good: str) -> Response:
    """**The honest one.** There is no conservation lever in this engine, and saying so is
    worth more than naming an order that would not touch the draw.

    :func:`atlas.engine.trade.demand_of` is ``industry * 0.11``, floored. It does not fall
    when a country builds less, disbands, or stands down: what a country burns is the size
    of its industry, and the only thing that changes the draw is the size of the industry.
    A panel that offered *conserve* as an available response would be offering a control
    that is not wired to anything.
    """
    return Response(
        kind="conserve",
        text=f"Burn less {good}",
        how="there is no lever for this. Your draw is 0.11 of the good per point of "
            "industry per month and does not fall when you build less",
        order_category="",
        available=False,
        blocked_because="what you burn follows the size of your industry, not what you "
                        "have under construction. Raising your own production is the "
                        "answer this one looks like")


def _restrict_exports(
    conn: sqlite3.Connection, world_id: str, viewer: str, good: str, customers: list[str],
) -> Response:
    """Stop selling it to somebody, and take the loss for it.

    Available only where the layer that carries it is switched on, the table exists, and
    this power is *actually exporting* the good -- which is the first thing
    :func:`atlas.engine.embargo.impose` checks. An offer to embargo a power you sell
    nothing to is an offer of a refusal.
    """
    if not (embargo.enabled() and embargo.table_present(conn)):
        return Response(
            kind="restrict_exports", text=f"Restrict {good} exports",
            how="withholding exports as a measure, which this world is not running",
            available=False,
            blocked_because="export embargoes are switched off in this world")
    if not customers:
        return Response(
            kind="restrict_exports", text=f"Restrict {good} exports",
            how="an embargo stops a lane you are the seller on, at your own cost",
            available=False,
            blocked_because=f"you sell {good} to nobody, so there is nothing to withhold")
    named = ", ".join(customers[:3])
    return Response(
        kind="restrict_exports",
        text=f"Stop selling {good} to {named}",
        how="your government withholds the cargo and the treasury bears what it would "
            "have earned; the cost is recorded against the decision",
        order_category="",
        available=True)


def _pay_for_less(conn: sqlite3.Connection, world_id: str, viewer: str) -> Response:
    """The answer to a purse running out, which is not the answer to a shortage.

    ``disband_units`` is the order :data:`atlas.engine.attention._STANDING_ORDER` already
    answers an arrears alert with, taken from there rather than chosen again -- two surfaces
    telling a player two different things about the same empty treasury is exactly what the
    shared substrate exists to prevent.
    """
    can = _can_order(conn, world_id, viewer, "disband_units")
    return Response(
        kind="spend_less",
        text="Stand down formations the treasury cannot pay for, or cut what you have "
             "agreed to import",
        how="upkeep is what the deficit is made of, and a government in arrears pays what "
            "it has and loses stability for the rest",
        order_category="disband_units",
        available=can,
        blocked_because="" if can else "you have nothing standing to stand down")


def _seek_outlet(
    conn: sqlite3.Connection, world_id: str, viewer: str, good: str, customer: str
) -> Response:
    """The customer-side twin of :func:`_seek_supplier`. Same kind, same mechanism, other
    end of the lane: a seller with one buyer is as captive as a buyer with one seller, and
    the thing that ends it is another agreement."""
    can = _can_order(conn, world_id, viewer, "propose_treaty")
    return Response(
        kind="seek_supplier",
        text=f"Find a second buyer for your {good}, beside {customer}",
        how="a seller with one customer takes what that customer will pay; another "
            "agreement is what puts a floor under the terms",
        order_category="propose_treaty",
        available=can,
        blocked_because="" if can else _WHY_NOT["propose_treaty"])


# ------------------------------------------------------------------------------ the lens


def lens_for(
    conn: sqlite3.Connection, world_id: str, viewer: str | None,
    subject: str | None = None, *, turn: int,
) -> DependencyLens:
    """Everything the lens shows about ``subject``, as ``viewer`` is entitled to see it.

    ``subject`` defaults to the viewer's own country, which is the case the panel exists
    for. A viewer with no country gets an empty lens that says so, for the reason
    :func:`atlas.engine.attention.attention_for` gives a spectator nothing: there is no
    *this player* for any of these figures to be about.

    **Every flow here is a month.** The settlement is previewed at a thirty-day round
    whatever length of turn the world is being played at, because *what does this cost me a
    month* is the question a treasury asks and a figure that changed meaning with the pace
    control would be unreadable. On the default monthly turn that is also exactly what the
    next turn will do.
    """
    subject = subject or viewer
    names = _names(conn, world_id)
    if not subject or subject not in names:
        return DependencyLens(
            nation_id="", name="", turn=turn, own=False,
            priced=pricing.enabled() and pricing_columns_present(conn),
            unknowns=("You are spectating, so there is no economy here that is yours. "
                      "Pick a country to see who supplies it.",))

    own = subject == viewer
    priced = pricing.enabled() and pricing_columns_present(conn)
    settlements = preview_settlement(conn, world_id, turn=turn, days=30)
    mine = [s for s in settlements if subject in (s.seller_id, s.buyer_id)]
    unknowns: list[str] = []
    if not own:
        # **A route is known to its two parties.** The same scope rule the strategic-goods
        # map layer holds itself to, and for the same reason: what two other powers ship
        # between themselves is a fact about their books.
        hidden = [s for s in mine if viewer not in (s.seller_id, s.buyer_id)]
        mine = [s for s in mine if viewer in (s.seller_id, s.buyer_id)]
        if not mine:
            unknowns.append(
                f"You are not party to any of {names[subject]}'s trade, so there are no "
                "lanes here to read.")
        elif hidden:
            unknowns.append(
                f"{names[subject]} is at one end of "
                + ("an agreement" if len(hidden) == 1 else f"{len(hidden)} agreements")
                + " you are not party to. You cannot see "
                + ("it" if len(hidden) == 1 else "them")
                + ", so nothing below counts "
                + ("it" if len(hidden) == 1 else "them")
                + " -- the shares and the concentration are about your own lanes only.")
        else:
            unknowns.append(
                f"Every lane {names[subject]} is at one end of is one you are at the "
                "other end of -- as far as you know. There may be others you cannot see.")

    views = _views(conn, world_id, subject, mine, names, priced,
                   own=own, viewer=viewer or subject)
    goods = _positions(conn, world_id, viewer, subject, mine, names,
                       own=own, turn=turn)
    if not own:
        unknowns.append(
            f"Its stores and its production are estimates from your own service, not "
            f"figures. {names[subject]} knows them exactly and you do not.")

    industry = _industry_of(conn, subject)
    # **Not computed at all for somebody else's country.** It is read from that nation's
    # exact industry and its exact stores, and a build-slot count and a pace are a fairly
    # direct statement of both. Nothing prints them on a foreign page; they are absent from
    # the object as well, because "not rendered" is a property of one screen and this needs
    # to be a property of the data.
    capacity_now = None
    pace_now = None
    if own:
        capacity_now = _capacity_at(industry)
        # The engine's own number rather than one re-derived from `covers`: the two agree
        # except where :func:`atlas.engine.trade.shortfall_of` exempts a nation the map
        # cannot reach at all, and a panel that disagrees with the yards there is a panel
        # telling Aruba it is blockaded.
        pace_now = shortfall_of(conn, world_id, subject)
    counterfactuals = (_counterfactuals(views, goods, pace_now)
                       if own and pace_now is not None else ())

    bill = round(sum(view.settlement.bill for view in views if view.inbound), 3)
    receipts = round(sum(view.settlement.bill for view in views if not view.inbound), 3)
    # **Netted the way the settlement nets it, which is on the integer lattice.** This was
    # a running total in money, rounded at each step and once at the end, because that is
    # what `assess_trade` did when it was written. `assess_trade` does not do that any more
    # -- a bill is a whole number of `trade.SETTLEMENT_UNITS` and the two sides of it are
    # the same integer -- so the old netting is now a *third* derivation, and one that
    # lands up to half a penny from the ledger it is reporting. Integers, summed and
    # divided once.
    units = sum(-item.bill_units if item.buyer_id == subject else item.bill_units
                for item in mine)
    settled = units / SETTLEMENT_UNITS

    vulnerabilities = (_vulnerabilities(conn, world_id, viewer or subject, views, goods,
                                        counterfactuals, names, bill)
                       if own else ())
    responses = (_responses(conn, world_id, viewer or subject, views, goods, names)
                 if own else ())
    if not priced:
        unknowns.append(
            "This world barters: no terms have been settled on any lane, so the cargo "
            "moves and no money does. Every figure in money below is zero because there "
            "is no money in this economy, not because nothing was found.")

    return DependencyLens(
        nation_id=subject, name=names[subject], turn=turn, own=own, priced=priced,
        goods=goods, routes=views, bill=bill, receipts=receipts, settled=settled,
        capacity_now=capacity_now, pace_now=pace_now, counterfactuals=counterfactuals,
        vulnerabilities=vulnerabilities, responses=responses,
        unknowns=tuple(unknowns))


def _views(  # noqa: PLR0913 - a view is the world, the pair, the lanes and the disclosure
    conn: sqlite3.Connection, world_id: str, subject: str,
    settlements: list[Settlement], names: dict[str, str], priced: bool,
    *, own: bool = True, viewer: str = "",
) -> tuple[RouteView, ...]:
    """One :class:`RouteView` per lane, with the share each carries.

    The share's denominator is *what this nation's supply of the good would be with this
    lane counted* -- so a lane that has stopped reports the share it would carry if it
    resumed rather than the zero it is currently moving. A blockade that reads as *0% of
    your oil* is a blockade the panel has talked a player out of worrying about.

    **Numerator and denominator in the same currency, which they were not.** A moving lane
    used to be measured as its *contracted* monthly terms -- ``volume`` inbound,
    ``volume * unit_price`` outbound -- over a denominator summed from what actually
    settled (``shipped``, ``bill``). Any lane that shipped short of its contract therefore
    read as a bigger share of the total than it carries, and a seller whose lanes were all
    availability- or budget-limited read *every* customer at 100%, because the clamp below
    caught the overshoot and hid it. Now a moving lane is measured by what it moved and a
    stopped lane by what it would move, matching the term added to the base on the same
    line either way; the clamp is left in place as a floor under floating point rather than
    as the thing that made the figure printable.
    """
    supply: dict[str, float] = {}
    receipts: dict[str, float] = {}
    for good in GOODS:
        # **A foreign country's own ground is not in the denominator, because the
        # denominator is published.** With it, a lane carrying a known volume at a printed
        # share solves straight back to the production the page has just declined to show:
        # `volume / share - shipped` returned it to four decimal places. Without it the
        # share means what the page already says it means -- a share of the supply the
        # viewer can see -- and there is nothing behind it to recover.
        supply[good] = _produced_of(conn, world_id, subject, good) if own else 0.0
        receipts[good] = 0.0
    # **Per month, both of them**, because `supply` starts from `_produced_of` -- a
    # monthly rate -- and `receipts` is compared against `volume * unit_price`, which is
    # monthly terms. `shipped` and `bill` are a *round's*, so they are put back on the
    # month's clock here. See :attr:`Settlement.span`.
    for item in settlements:
        if item.buyer_id == subject:
            supply[item.good] = supply.get(item.good, 0.0) + item.shipped_per_month
        else:
            receipts[item.good] = receipts.get(item.good, 0.0) + item.bill_per_month

    alternatives_cache: dict[tuple[str, str, str], int] = {}
    lanes: dict[tuple[str, str], tuple[bool, str]] = {}
    views: list[RouteView] = []
    for item in sorted(settlements, key=lambda s: (-s.volume, s.route_id)):
        inbound = item.buyer_id == subject
        other = item.seller_id if inbound else item.buyer_id
        if inbound:
            counted = item.shipped_per_month if item.moving else item.volume
            base = supply.get(item.good, 0.0) + (0.0 if item.moving else item.volume)
        else:
            would_earn = item.volume * item.unit_price
            counted = item.bill_per_month if item.moving else would_earn
            base = receipts.get(item.good, 0.0) + (0.0 if item.moving else would_earn)
        share = counted / base if base > 0 else 0.0
        reach = -1
        if inbound and priced:
            reach = pricing.alternatives(
                conn, world_id, item.buyer_id, item.seller_id, item.good,
                lanes=lanes, alternatives_cache=alternatives_cache)
        # **Which sentence this viewer is entitled to, decided here.** The clauses in the
        # engine's own explanation are about whichever side walked away; a reader is
        # entitled to them when that side is their own government and not otherwise.
        ended_id = (item.buyer_id if item.ended_by == "buyer"
                    else item.seller_id if item.ended_by == "seller" else "")
        mine = (viewer or subject) == ended_id if ended_id else own
        views.append(RouteView(
            settlement=item, inbound=inbound, other_id=other,
            other_name=names.get(other, other),
            share=round(max(0.0, min(1.0, share)), 4), alternatives=reach,
            why=item.why if mine else item.why_public))
    return tuple(views)


def _produced_of(
    conn: sqlite3.Connection, world_id: str, nation_id: str, good: str
) -> float:
    try:
        row = conn.execute(
            "SELECT produced FROM resource"
            " WHERE world_id = ? AND nation_id = ? AND good = ?",
            (world_id, nation_id, good)).fetchone()
    except sqlite3.OperationalError:
        return 0.0
    return float(row["produced"] or 0.0) if row is not None else 0.0


def _positions(  # noqa: PLR0913 - a position is the world, the pair, the lanes and the turn
    conn: sqlite3.Connection, world_id: str, viewer: str | None, subject: str,
    settlements: list[Settlement], names: dict[str, str], *, own: bool, turn: int,
) -> tuple[GoodPosition, ...]:
    """Where the subject stands in each good it has a row for.

    For the viewer's own country these are figures. For anybody else's they are what the
    viewer's service believes, through :func:`atlas.engine.intelligence.estimate_of` -- a
    band displaced before it is opened, so averaging the ends does not recover the truth.
    """
    from .intelligence import estimate_of

    try:
        rows = conn.execute(
            "SELECT good, produced, stockpile FROM resource"
            " WHERE world_id = ? AND nation_id = ? ORDER BY good",
            (world_id, subject)).fetchall()
    except sqlite3.OperationalError:
        return ()
    if not rows:
        return ()

    demand = demand_of(conn, subject)
    found: list[GoodPosition] = []
    for row in rows:
        good = str(row["good"])
        produced = float(row["produced"] or 0.0)
        stockpile = float(row["stockpile"] or 0.0)
        inbound = [s for s in settlements if s.buyer_id == subject and s.good == good]
        outbound = [s for s in settlements if s.seller_id == subject and s.good == good]
        # **On the month's clock**, like `produced`, `demand` and the runway they feed.
        # A settlement's `shipped` is a *round's* cargo; see :attr:`Settlement.span`.
        imported = round(sum(s.shipped_per_month for s in inbound), 3)
        exported = round(sum(s.shipped_per_month for s in outbound), 3)

        # **The whole disclosure decision for this good, taken here, once, before a single
        # figure is formatted.** Everything below reads `supply`, and for a foreign subject
        # `supply` is what the viewer can see arriving and nothing else -- so the shares
        # and the concentration are about the visible lanes in fact, and not merely in the
        # sentence that describes them.
        supply = (produced + imported) if own else imported
        by_supplier = [(s.seller_id, s.shipped_per_month) for s in inbound if s.shipped > 0]
        by_customer = [(s.buyer_id, s.bill_per_month) for s in outbound if s.bill > 0]
        earned = sum(bill for _who, bill in by_customer)
        shares = ([produced / supply] if own and supply > 0 and produced > 0 else []) + [
            volume / supply for _who, volume in by_supplier if supply > 0]
        top_supplier, top_share = "", 0.0
        if by_supplier and supply > 0:
            who, volume = max(by_supplier, key=lambda pair: (pair[1], pair[0]))
            top_supplier, top_share = names.get(who, who), round(volume / supply, 4)
        top_customer, customer_share = "", 0.0
        if by_customer and earned > 0:
            who, bill = max(by_customer, key=lambda pair: (pair[1], pair[0]))
            top_customer, customer_share = names.get(who, who), round(bill / earned, 4)

        draw = demand + exported - produced - imported
        cover = 1.0 if demand <= 0 else max(0.0, min(1.0, stockpile / demand))
        estimated = ""
        if not own:
            # The service's reading, never the row. Two separate subjects, so a viewer
            # that learns one is not thereby told the other.
            #
            # **And asked on a slow clock, which is the difference between a fog and a
            # sampling channel.** `estimate_of` seeds on the turn, so it draws a fresh band
            # every turn -- correct where a figure is asked for occasionally, and a leak
            # here, because this panel offers six of them about any country you trade with,
            # for free, whenever you like. Measured over twenty-four turns against a static
            # truth, averaging the midpoints landed every one of the six inside 4%, and two
            # of the bands intersected down to a single point. Rounding the turn down to a
            # `BAND_PERIOD` block means a viewer who re-reads gets the *same* reading, and
            # has to wait for the world to move on before the service will say anything
            # new. The fog is unchanged; what is gone is the ability to average it away.
            asked = turn - (turn % BAND_PERIOD)
            store = estimate_of(conn, world_id, viewer=viewer or subject, target=subject,
                                subject=f"{good} in store", value=stockpile, turn=asked)
            digs = estimate_of(conn, world_id, viewer=viewer or subject, target=subject,
                               subject=f"{good} a month", value=produced, turn=asked)
            estimated = (
                f"your service puts its {good} store at {store.low:g}-{store.high:g} "
                f"({store.confidence}), and what it digs up at "
                f"{digs.low:g}-{digs.high:g} a month")
        found.append(GoodPosition(
            good=good,
            produced=produced if own else None,
            # Three figures that are arithmetic over the two above, and were the way back
            # to them. `demand` is industry x 0.11; `dependency` is
            # `(demand - produced) / demand`; `cover` is `stockpile / demand`. Any two of
            # the four recover the rest, so a page that withholds two and publishes three
            # has withheld nothing. They leave together.
            demand=round(demand, 3) if own else None,
            stockpile=stockpile if own else None,
            estimated=estimated,
            imported=imported, exported=exported,
            runway=runway_of(stockpile, draw) if own else None,
            dependency=(round(max(0.0, (demand - produced) / demand), 4) if demand > 0
                        else 0.0) if own else None,
            top_supplier=top_supplier, top_supplier_share=top_share,
            top_customer=top_customer, top_customer_share=customer_share,
            concentration=_concentration(shares),
            suppliers=len(by_supplier), customers=len(by_customer),
            cover=round(cover, 4) if own else None))
    return tuple(found)


def _counterfactuals(
    views: tuple[RouteView, ...], goods: tuple[GoodPosition, ...],
    pace_now: float,
) -> tuple[Counterfactual, ...]:
    """What the yards could do without each lane that is currently carrying something.

    **The steady state, not this month.** :func:`atlas.engine.trade.shortfall_of` reads
    the *store*, and the store is full, so losing a supplier costs nothing at all in the
    month it happens. What it costs is the pace after the store runs out, and
    :attr:`Counterfactual.months` is how long that takes -- which is the number that decides
    whether losing the lane is an inconvenience or a campaign.
    """
    position = {item.good: item for item in goods}

    def cover_within_the_year(without: str = "", less: float = 0.0) -> float:
        """The worst good's cover at the horizon, optionally with one lane removed.

        **Not the infinite steady state.** A store that drains a quarter of a unit a month
        against forty in hand does empty, in thirteen years, and calling that *no cover* is
        a sentence no cabinet would accept. What a lens is asked is whether the good will
        throttle the yards inside a year, so that is what is measured: a store still
        standing at the horizon is cover, and one that is not is not.
        """
        worst = 1.0
        for item in goods:
            draw = (item.demand + item.exported - (item.produced or 0.0) - item.imported
                    + (less if item.good == without else 0.0))
            months = runway_of(item.stockpile or 0.0, draw)
            worst = min(worst, 0.0 if months is not None and months
                        <= RUNWAY_WORTH_SAYING else 1.0)
        return worst

    baseline = _pace_at(cover_within_the_year())
    found: list[Counterfactual] = []
    for view in views:
        if not view.inbound or not view.settlement.moving:
            continue
        here = position.get(view.good)
        if here is None or here.demand <= 0:
            continue
        after = _pace_at(cover_within_the_year(view.good, view.settlement.shipped))
        drained = runway_of(
            here.stockpile or 0.0,
            here.demand + here.exported - (here.produced or 0.0)
            - (here.imported - view.settlement.shipped))
        found.append(Counterfactual(
            route_id=view.route_id,
            label=f"{view.settlement.volume:g} {view.good} a month from {view.other_name}",
            good=view.good, pace_now=pace_now, pace_year=baseline,
            pace_after=after, months=drained))
    # Worst first, then largest lane, then by id: a stable order that puts the lane whose
    # loss costs the most at the top of the panel.
    found.sort(key=lambda c: (c.pace_after - c.pace_year,
                              c.months if c.months is not None else 1e9, c.route_id))
    return tuple(found)


# --------------------------------------------------------------------- the three worst

#: How much of a month's revenue the import bill has to eat before it is a vulnerability
#: rather than a cost. A reading dial. A third, because a government spending a third of
#: what it earns on raw materials has lost most of its room to do anything else.
_BILL_WORTH_SAYING = 0.33


def _vulnerabilities(  # noqa: PLR0913 - a ranking needs everything that was derived
    conn: sqlite3.Connection, world_id: str, viewer: str,
    views: tuple[RouteView, ...], goods: tuple[GoodPosition, ...],
    counterfactuals: tuple[Counterfactual, ...], names: dict[str, str], bill: float,
) -> tuple[Vulnerability, ...]:
    """The three largest ways this economy can be hurt, ranked and evidenced.

    **Ranked, not listed.** Six kinds of claim are generated and three survive, because
    *what is the worst of it* is the question a player actually has and a panel that answers
    it with nine bullet points has not answered it. Every one carries the numbers that put
    it there and one thing the government could do, which is the rule every explanation in
    this program is held to.
    """
    from .budget import nation_budget

    found: list[Vulnerability] = []
    by_good = {item.good: item for item in goods}
    # A nation the map cannot reach is exempt from the shortage floor altogether (see
    # :func:`atlas.engine.trade.is_unreachable`), so warning it that its yards will slow
    # would be a threat the engine never carries out.
    exempt = is_unreachable(conn, world_id, viewer)
    reach_for: dict[str, int] = {}
    #: Who a good comes from, even when none of it is currently arriving. `top_supplier` is
    #: read off what *shipped*, so a blockaded country has no top supplier at all -- and the
    #: sentence "find oil somewhere other than anybody you buy from" is what that looked
    #: like on the panel. The lane's counterparty is the name the player is owed.
    named: dict[str, str] = {}
    for view in views:
        if not view.inbound:
            continue
        if view.alternatives >= 0:
            reach_for[view.good] = min(
                reach_for.get(view.good, view.alternatives), view.alternatives)
        named.setdefault(view.good, view.other_name)

    for item in goods:
        # 1. The store is running down.
        if not exempt and item.runway is not None and item.runway < RUNWAY_WORTH_SAYING:
            found.append(Vulnerability(
                headline=f"Your {item.good} runs out in about "
                         f"{item.runway:.0f} month{'s' if item.runway >= 1.5 else ''}",
                because=f"you burn {item.demand:.2f} a month and take in "
                        f"{(item.produced or 0.0) + item.imported:.2f}; the store is "
                        f"{item.stockpile or 0.0:.1f}. When it empties your yards slow to "
                        f"the starved floor of {_STARVED_CAPACITY:.0%} of their pace",
                severity=round(max(0.0, 1.0 - item.runway / RUNWAY_WORTH_SAYING), 4),
                response=_seek_supplier(
                    conn, world_id, viewer, item.good, reach_for.get(item.good, 0),
                    item.top_supplier or named.get(item.good, "your suppliers")),
                good=item.good))
        # 2. One power carries most of it.
        if item.top_supplier_share >= CONCENTRATED / 2:
            found.append(Vulnerability(
                headline=f"{item.top_supplier} supplies "
                         f"{item.top_supplier_share:.0%} of your {item.good}",
                because=f"{item.suppliers} "
                        + ("lane brings" if item.suppliers == 1 else "lanes bring")
                        + " it in and your "
                        f"own ground covers {1.0 - item.dependency:.0%} of what you burn. "
                        f"Supply concentration {item.concentration:.2f} of a possible 1.00",
                severity=round(item.top_supplier_share, 4),
                response=_seek_supplier(conn, world_id, viewer, item.good,
                                        reach_for.get(item.good, 0), item.top_supplier),
                good=item.good, other_id=_id_for(names, item.top_supplier)))
        # 3. And nobody else could reach you.
        if (reach_for.get(item.good, 1) == 0 and item.top_supplier
                and item.top_supplier_share > 0):
            found.append(Vulnerability(
                headline=f"Nobody but {item.top_supplier} can get {item.good} to you",
                because="no other power with a surplus of it has a lane to your ground, so "
                        "the price you are quoted carries a captive buyer's premium and "
                        "there is nowhere to take your business",
                severity=round(0.55 + 0.45 * item.top_supplier_share, 4),
                response=_repair_relations(
                    conn, world_id, viewer, _id_for(names, item.top_supplier),
                    item.top_supplier),
                good=item.good, other_id=_id_for(names, item.top_supplier)))
        # 4. One customer is most of the income.
        if item.top_customer_share >= CONCENTRATED:
            found.append(Vulnerability(
                headline=f"{item.top_customer} takes {item.top_customer_share:.0%} of "
                         f"what you earn selling {item.good}",
                because=f"{item.customers} "
                        + ("buyer takes" if item.customers == 1 else "buyers take")
                        + " it, and one of them decides what that trade is worth to you",
                severity=round(0.4 + 0.5 * item.top_customer_share, 4),
                response=_seek_outlet(conn, world_id, viewer, item.good,
                                      item.top_customer),
                good=item.good, other_id=_id_for(names, item.top_customer)))

    # 5. A lane that has stopped, and what stopping it is worth to whoever stopped it.
    for view in views:
        if view.status == "open" or not view.inbound:
            continue
        item = by_good.get(view.good)
        found.append(Vulnerability(
            headline=f"{view.other_name} is not shipping your {view.good}",
            because=f"{STATUSES[view.status]}. That lane is "
                    f"{view.share:.0%} of your {view.good}"
                    + (f", and {view.settlement.why}" if view.settlement.why else ""),
            severity=round(min(1.0, 0.45 + 0.55 * view.share), 4),
            response=_answer_to(conn, world_id, viewer, view,
                                reach_for.get(view.good, 0),
                                (item.top_supplier if item else "") or view.other_name),
            good=view.good, other_id=view.other_id, route_id=view.route_id))

    # 6. The bill itself.
    revenue, _upkeep = nation_budget(conn, world_id, viewer)
    if revenue > 0 and bill / revenue >= _BILL_WORTH_SAYING:
        biggest = max((v for v in views if v.inbound), default=None,
                      key=lambda v: (v.settlement.bill, v.route_id))
        found.append(Vulnerability(
            headline=f"Imports eat {bill / revenue:.0%} of what you earn",
            because=f"{bill:.2f} a month against a revenue of {revenue:.2f}. A buyer that "
                    "spends its allowance on the first cargo cannot pay for the second, "
                    "and the engine simply does not ship the remainder",
            severity=round(min(1.0, bill / revenue), 4),
            response=_seek_supplier(
                conn, world_id, viewer,
                biggest.good if biggest else "what you import",
                reach_for.get(biggest.good, 0) if biggest else 0,
                biggest.other_name if biggest else "your supplier")))

    found.sort(key=lambda v: (-v.severity, v.headline))
    return tuple(found[:3])


def _id_for(names: dict[str, str], name: str) -> str:
    """A nation id back out of a display name. Empty when it does not resolve, which is
    an honest answer -- the id is only ever used to aim an order, and an order aimed at a
    power that could not be identified is worse than no control at all."""
    for nation_id, known in names.items():
        if known == name:
            return nation_id
    return ""


def _answer_to(
    conn: sqlite3.Connection, world_id: str, viewer: str, view: RouteView,
    reach: int, supplier: str,
) -> Response:
    """What answers *this* stoppage. The status decides, because the statuses are kept
    apart precisely so that they can have different answers."""
    if view.status in ("blockaded", "at_war"):
        return _protect_the_lane(conn, world_id, viewer, view.other_name)
    if view.status in ("sanctioned", "suspended", "priced_out"):
        return _repair_relations(conn, world_id, viewer, view.other_id, view.other_name)
    return _seek_supplier(conn, world_id, viewer, view.good, reach, supplier)


def _responses(
    conn: sqlite3.Connection, world_id: str, viewer: str,
    views: tuple[RouteView, ...], goods: tuple[GoodPosition, ...],
    names: dict[str, str],
) -> tuple[Response, ...]:
    """All five, aimed at whatever they are most worth aiming at, in a fixed order.

    Fixed rather than ranked: this is the standing list of what a government can do about
    its economy, and a list that reordered itself between two openings of the same panel
    would be a list nobody could learn.
    """
    worst = max(goods, key=lambda g: (g.dependency, g.good), default=None)
    good = worst.good if worst else "oil"
    inbound = [view for view in views if view.inbound]
    biggest = max(inbound, default=None,
                  key=lambda v: (v.share, v.settlement.volume, v.route_id))
    reach = biggest.alternatives if biggest and biggest.alternatives >= 0 else 0
    partner = biggest.other_name if biggest else "your suppliers"
    partner_id = biggest.other_id if biggest else ""
    stopped = next((v for v in views if v.status in ("blockaded", "at_war")), None)
    # **The export lever is aimed at what this country actually sells**, which is not
    # necessarily the good it is most dependent on -- an oil importer that exports steel
    # has an embargo to impose and it is a steel one. Offering it against the good it buys
    # would be offering a refusal.
    outbound = [view for view in views if not view.inbound]
    sold = max(outbound, default=None,
               key=lambda v: (v.settlement.volume, v.route_id))
    export_good = sold.good if sold else good
    customers = sorted({
        view.other_name for view in outbound if view.good == export_good})
    return (
        _seek_supplier(conn, world_id, viewer, good, reach, partner),
        _repair_relations(conn, world_id, viewer, partner_id, partner),
        _protect_the_lane(conn, world_id, viewer,
                          stopped.other_name if stopped else partner),
        _conserve(good),
        _restrict_exports(conn, world_id, viewer, export_good, customers),
    )


# --------------------------------------------------------------- what changed this turn

#: Where a single supplier's share of a good stops being a commercial fact and becomes a
#: strategic one, and the same for a single customer's share of the income. Reading dials:
#: they decide when a sentence is printed and nothing about what is true.
DEPENDENCY_ALARM = 0.60
CUSTOMER_ALARM = 0.60

#: How far a lane's terms have to move in one turn to be worth waking somebody for. Twice,
#: because a price that has doubled is a price nobody budgeted for -- and well outside
#: :data:`atlas.engine.pricing.REPRICE_DEADBAND`, so this can never fire on churn.
PRICE_SHOCK = 2.0

#: How few months of cover is few enough to say so. Three: long enough that there is time
#: to do something, short enough that the something has to start now.
RUNWAY_ALARM = 3.0

#: How far ahead the arrears warning looks. A year, which is the same horizon
#: :func:`forecast_for` runs to, so the warning and the forecast cannot disagree about
#: whether a treasury lasts.
ARREARS_HORIZON = 12.0


class _Before:
    """Every value this turn has already changed, as it stood before the turn began.

    **This is what makes a warning a threshold *crossing* rather than a standing condition,
    and it is derived rather than remembered.** A substrate that fired every turn a
    dependency was over sixty percent would be a substrate a player learns to ignore by the
    third turn; one that remembered what it had already said would need somewhere to write
    that down, and there is nowhere -- a note in the save would move the fingerprint, and a
    note beside it would not survive a rewind.

    So the memory is the diff log, which the engine keeps anyway because a rewind is a
    replay: every applied change records the value it *replaced*. Read the turn's own diffs
    oldest-first and keep the first ``old_json`` per field, and that is the world as the
    turn found it -- exactly, for free, and rewinding is what un-remembers it.
    """

    __slots__ = ("_added", "_fields", "_gone")

    def __init__(self, conn: sqlite3.Connection, world_id: str, turn: int) -> None:
        import json

        self._fields: dict[tuple[str, str, str], object] = {}
        self._added: set[tuple[str, str]] = set()
        self._gone: dict[tuple[str, str], dict] = {}
        rows = conn.execute(
            "SELECT d.target_table AS t, d.target_id AS i, d.field AS f, d.op AS op,"
            "       d.old_json AS was"
            "  FROM diff d JOIN diff_batch b ON b.id = d.batch_id"
            " WHERE b.world_id = ? AND b.turn = ? AND d.status = 'applied'"
            " ORDER BY d.id", (world_id, turn))
        for row in rows:
            where = (str(row["t"]), str(row["i"]))
            op = str(row["op"])
            if op == "insert":
                self._added.add(where)
            elif op == "delete":
                if where not in self._gone and row["was"] is not None:
                    self._gone[where] = dict(json.loads(str(row["was"])))
            else:
                key = (where[0], where[1], str(row["f"]))
                if key not in self._fields and row["was"] is not None:
                    self._fields[key] = json.loads(str(row["was"]))

    def value(self, table: str, target: str, field: str, now: float) -> float:
        """What the field held before the turn. ``now`` when the turn has not touched it."""
        found = self._fields.get((table, target, field))
        return float(found) if isinstance(found, (int, float)) else now

    def existed(self, table: str, target: str) -> bool:
        """False for a row this turn created. A dependency that only exists because an
        agreement was signed an hour ago did not *cross* anything."""
        return (table, target) not in self._added

    def removed(self, table: str) -> tuple[dict, ...]:
        """Rows this turn deleted, whole, as they were. A lane that has ended still counted
        towards the supply the turn started with, and leaving it out would report every
        cancelled agreement as a dependency that had suddenly appeared."""
        return tuple(row for (where, row) in sorted(self._gone.items())
                     if where[0] == table)


@dataclass(frozen=True, slots=True)
class _Lane:
    """A route reduced to the three numbers a threshold is measured on."""

    route_id: str
    seller_id: str
    buyer_id: str
    good: str
    volume: float
    price: float


def _lanes_now(
    conn: sqlite3.Connection, world_id: str, nation_id: str, priced: bool
) -> list[_Lane]:
    return [
        _Lane(route_id=str(row["id"]), seller_id=str(row["seller_id"]),
              buyer_id=str(row["buyer_id"]), good=str(row["good"]),
              volume=float(row["volume"] or 0.0),
              price=float(row["unit_price"] or 0.0) if priced else 0.0)
        for row in conn.execute(
            "SELECT * FROM trade_route WHERE world_id = ?"
            "   AND (seller_id = ? OR buyer_id = ?) ORDER BY id",
            (world_id, nation_id, nation_id))]


def _lanes_before(
    lanes: list[_Lane], before: _Before, nation_id: str, priced: bool
) -> list[_Lane]:
    """The same lanes as the turn found them: this turn's new ones dropped, this turn's
    cancelled ones put back, and every volume and price rolled to what it was."""
    found = [
        _Lane(route_id=lane.route_id, seller_id=lane.seller_id, buyer_id=lane.buyer_id,
              good=lane.good,
              volume=before.value("trade_route", lane.route_id, "volume", lane.volume),
              price=(before.value("trade_route", lane.route_id, "unit_price", lane.price)
                     if priced else 0.0))
        for lane in lanes if before.existed("trade_route", lane.route_id)]
    for row in before.removed("trade_route"):
        if nation_id in (str(row.get("seller_id")), str(row.get("buyer_id"))):
            found.append(_Lane(
                route_id=str(row.get("id")), seller_id=str(row.get("seller_id")),
                buyer_id=str(row.get("buyer_id")), good=str(row.get("good")),
                volume=float(row.get("volume") or 0.0),
                price=float(row.get("unit_price") or 0.0) if priced else 0.0))
    return sorted(found, key=lambda lane: lane.route_id)


def _supplier_share(
    lanes: list[_Lane], nation_id: str, good: str, produced: float
) -> tuple[str, float]:
    """``(the biggest supplier, its share of the supply)``, measured on agreed volume.

    **Volume, not what actually shipped**, and the difference matters. What shipped moves
    with a blockade, a suspension and a bad month at the docks, so a dependency measured on
    it would fall to zero exactly when the lane was cut -- announcing the end of a
    dependency at the moment it bit. What a country has *agreed* to buy is the structural
    fact, and it changes only when an agreement does.
    """
    inbound: dict[str, float] = {}
    for lane in lanes:
        if lane.buyer_id == nation_id and lane.good == good:
            inbound[lane.seller_id] = inbound.get(lane.seller_id, 0.0) + lane.volume
    total = produced + sum(inbound.values())
    if total <= 0 or not inbound:
        return "", 0.0
    who = max(inbound, key=lambda seller: (inbound[seller], seller))
    return who, round(inbound[who] / total, 4)


def _customer_share(
    lanes: list[_Lane], nation_id: str, good: str
) -> tuple[str, float]:
    """``(the biggest customer, its share of what this good earns)``, on agreed terms.

    **Agreed terms here, settled receipts in :func:`_views` -- and both are right**, which
    is worth saying because one module holding two definitions of "share of what this good
    earns" reads as an oversight. This one is half of a *comparison*: it is computed over a
    reconstructed before-state and an after-state and the difference is what raises the
    warning, so it has to be a quantity a diff log can rebuild for a turn already gone --
    which contracted terms are and a settlement's receipts are not. :func:`_views`
    describes the lane in front of the player right now, where what actually settled is the
    honest figure and is available. Do not "unify" them; the panel would start disagreeing
    with the ledger, or the warning would stop firing on turns it cannot re-derive.
    """
    outbound: dict[str, float] = {}
    for lane in lanes:
        if lane.seller_id == nation_id and lane.good == good:
            outbound[lane.buyer_id] = (outbound.get(lane.buyer_id, 0.0)
                                       + lane.volume * lane.price)
    total = sum(outbound.values())
    if total <= 0:
        return "", 0.0
    who = max(outbound, key=lambda buyer: (outbound[buyer], buyer))
    return who, round(outbound[who] / total, 4)


#: What each economic warning is worth on the two axes the substrate cannot derive for
#: itself. **Severity is how large the consequence is; urgency is how soon it forces a
#: hand**, and for money those are genuinely different questions -- a dependency is grave
#: and has no deadline, a store that empties in ten weeks is a smaller thing on a shorter
#: fuse. The same distinction :data:`atlas.engine.attention._STANDING_URGENCY` is built on,
#: and these sit on the same scale so an oil crisis can be ranked against a war.
_ECONOMIC_AXES: dict[str, tuple[float, float]] = {
    "econ_arrears": (0.80, 0.70),
    "econ_runway": (0.75, 0.85),
    "econ_dependency": (0.60, 0.40),
    "econ_price": (0.50, 0.55),
    "econ_customer": (0.50, 0.30),
}


def _turn_months(conn: sqlite3.Connection, world_id: str, turn: int) -> float:
    """How much world time the turn numbered ``turn`` covered, in reference months.

    **Needed because what a store actually lost is a change per *turn*, and a runway is
    read per month.** A world played in weeks loses a quarter of what a monthly one does
    and must not read as four times the cover.

    Falls back to one month when the calendar cannot answer -- a turn row that was never
    written, a world still on its first turn, a fixture that never advanced the clock. That
    is the reference turn every constant in the engine was tuned against, and it is the one
    assumption that leaves a monthly game exactly as it was.
    """
    from .pacing import REFERENCE_DAYS, days_between

    row = conn.execute(
        "SELECT start_date, end_date FROM turn WHERE world_id = ? AND number = ?",
        (world_id, turn)).fetchone()
    if row is not None and row["end_date"]:
        spent = days_between(str(row["start_date"]), str(row["end_date"]))
        if spent and spent > 0:
            return spent / REFERENCE_DAYS
    earlier = conn.execute(
        "SELECT start_date FROM turn WHERE world_id = ? AND number = ?",
        (world_id, turn - 1)).fetchone()
    if row is not None and earlier is not None:
        spent = days_between(str(earlier["start_date"]), str(row["start_date"]))
        if spent and spent > 0:
            return spent / REFERENCE_DAYS
    return 1.0


def warnings_for(
    conn: sqlite3.Connection, world_id: str, viewer: str | None, *, turn: int,
) -> list:
    """Economic warnings for the attention substrate, on the turns they are true *and new*.

    **Crossings, not conditions, and that is the whole design.** The alert layer is full of
    standing conditions -- a war is on, a capital is threatened -- and they belong there
    because they are true every turn until somebody changes them. An economy is not like
    that: a country is always dependent on somebody for something, and a substrate that
    said so every turn would be a substrate a player stops reading. So each of these fires
    on the turn its measure *crosses* a line, and is silent on every turn after, however
    bad the number stays. The line it crossed from is read out of the diff log -- see
    :class:`_Before` -- so there is nothing to remember and a rewind un-remembers it.

    **Measured on what happened, not on what is predicted, and that is a cost decision as
    much as a design one.** :func:`lens_for` re-derives the settlement to say what *will*
    move, and on the seeded modern-day board that walk costs about three seconds -- fine for
    a panel somebody opens, ruinous for something every turn's attention read pays. So the
    two quantities that would need it are taken from the diff log instead: what a store
    actually lost this turn, and what the treasury actually moved by for cargo. Both are
    free, both are exactly true, and both are the better reading anyway -- a warning about
    a store running down is a warning about a store that *is* running down.

    The other three measures need no settlement at all: a dependency, a customer's share
    and a price shock are read off the agreements themselves.

    Returns :class:`atlas.engine.attention.AttentionItem` values. The import is local
    because :mod:`atlas.engine.attention` calls back into this module, and a module-level
    pair of imports would be a cycle. Empty for a spectator, for the reason
    :func:`atlas.engine.attention.attention_for` gives one nothing.
    """
    from .attention import AttentionItem, NavTarget

    if not viewer:
        return []
    names = _names(conn, world_id)
    if viewer not in names:
        return []
    priced = pricing.enabled() and pricing_columns_present(conn)
    before = _Before(conn, world_id, turn)
    lanes = _lanes_now(conn, world_id, viewer, priced)
    was = _lanes_before(lanes, before, viewer, priced)
    span = _turn_months(conn, world_id, turn)
    demand = demand_of(conn, viewer)
    try:
        held = {
            str(row["good"]): (float(row["produced"] or 0.0),
                               float(row["stockpile"] or 0.0))
            for row in conn.execute(
                "SELECT good, produced, stockpile FROM resource"
                " WHERE world_id = ? AND nation_id = ? ORDER BY good", (world_id, viewer))}
    except sqlite3.OperationalError:
        held = {}

    found: list[AttentionItem] = []

    def emit(source: str, subject: str, reason: str, response: Response,
             *, other: str = "") -> None:
        severity, urgency = _ECONOMIC_AXES[source]
        target = other or viewer
        found.append(AttentionItem(
            source=source, subject=subject,
            reason_selected=f"{reason} What you could do: {response.text.lower()}.",
            severity=severity, urgency=urgency, player_relevance=1.0,
            # It crossed this turn by construction, which is what the axis measures.
            novelty=1.0,
            actionable_now=response.available and bool(response.order_category),
            nation_id=target,
            nav_target=NavTarget(kind="nation", target=target,
                                 label=names.get(target, target)),
            suggested_order_category=response.order_category))

    for good in sorted(held):
        produced, stockpile = held[good]
        # 1. A supplier's share of the good crossed the line this turn.
        who_now, share_now = _supplier_share(lanes, viewer, good, produced)
        _who_was, share_was = _supplier_share(
            was, viewer, good,
            before.value("resource", f"{viewer}|{good}", "produced", produced))
        if share_now >= DEPENDENCY_ALARM > share_was and who_now:
            emit("econ_dependency",
                 f"{names.get(who_now, who_now)} now supplies "
                 f"{share_now:.0%} of your {good}",
                 f"It was {share_was:.0%} at the start of the turn, and "
                 f"{DEPENDENCY_ALARM:.0%} is where one power stops being one of several "
                 "and starts being able to decide your industry's month.",
                 _seek_supplier(conn, world_id, viewer, good, None,
                                names.get(who_now, who_now)),
                 other=who_now)

        # 2. The store crossed into the last few months of cover. **The draw is what the
        #    store actually lost this turn**, scaled to a month, rather than a prediction
        #    of what it will lose. See the note on this function.
        was_stock = before.value("resource", f"{viewer}|{good}", "stockpile", stockpile)
        draw = (was_stock - stockpile) / span if span > 0 else 0.0
        runway_now = runway_of(stockpile, draw)
        was_runway = runway_of(was_stock, draw)
        if (runway_now is not None and runway_now <= RUNWAY_ALARM
                and was_runway is not None and was_runway > RUNWAY_ALARM
                and not is_unreachable(conn, world_id, viewer)):
            others = sorted(lane.seller_id for lane in lanes
                            if lane.buyer_id == viewer and lane.good == good)
            supplier = (names.get(who_now, who_now) if who_now
                        else names.get(others[0], others[0]) if others
                        else "your suppliers")
            emit("econ_runway",
                 f"Your {good} will throttle the yards within about "
                 f"{runway_now:.0f} months",
                 f"The store went from {was_stock:.1f} to {stockpile:.1f} this turn, which "
                 f"is {draw:.2f} a month against a burn of {demand:.2f}. That was "
                 f"{was_runway:.0f} months of cover and is {runway_now:.0f}; at nothing "
                 f"the yards work at {_STARVED_CAPACITY:.0%} of their pace.",
                 _seek_supplier(conn, world_id, viewer, good, None, supplier))

        # 3. One customer became most of the income from it.
        buyer_now, take_now = _customer_share(lanes, viewer, good)
        buyer_was, take_was = _customer_share(was, viewer, good)
        if take_now >= CUSTOMER_ALARM > take_was and buyer_now:
            emit("econ_customer",
                 f"{names.get(buyer_now, buyer_now)} now takes "
                 f"{take_now:.0%} of what your {good} earns",
                 f"It was {take_was:.0%} at the start of the turn. A seller with one buyer "
                 "takes what that buyer will pay.",
                 _seek_outlet(conn, world_id, viewer, good,
                              names.get(buyer_now, buyer_now)),
                 other=buyer_now)

    # 4. A lane's terms moved further in a turn than anybody budgets for.
    standing = {lane.route_id: lane for lane in was}
    for lane in lanes:
        old = standing.get(lane.route_id)
        if old is None or old.price <= 0 or lane.price < old.price * PRICE_SHOCK:
            continue
        inbound = lane.buyer_id == viewer
        other = lane.seller_id if inbound else lane.buyer_id
        emit("econ_price",
             f"The {lane.good} from {names.get(other, other)} costs "
             f"{lane.price / old.price:.1f}x what it did"
             if inbound else
             f"Your {lane.good} to {names.get(other, other)} now fetches "
             f"{lane.price / old.price:.1f}x what it did",
             f"{old.price:.2f} to {lane.price:.2f} a unit in one turn, which is "
             f"{lane.volume * (lane.price - old.price):+.2f} a month on a lane of "
             f"{lane.volume:g}.",
             _repair_relations(conn, world_id, viewer, other, names.get(other, other))
             if inbound else
             _seek_outlet(conn, world_id, viewer, lane.good, names.get(other, other)),
             other=other)

    # 5. And the commitments themselves.
    arrears = _arrears_crossing(conn, world_id, viewer, before, span=span)
    if arrears is not None:
        months, was_months, revenue, upkeep, cargo = arrears
        emit("econ_arrears",
             "Your standing commitments run the treasury out in about "
             f"{months:.0f} months",
             f"{revenue:.2f} in and {upkeep:.2f} of upkeep a month"
             + (f", and cargo took {-cargo:.2f} more" if cargo < -0.005 else
                f", and cargo brought {cargo:.2f}" if cargo > 0.005 else "")
             + f", against a purse of {_treasury_of(conn, world_id, viewer):.2f}. It was "
             f"{was_months:.0f} months of cover at the start of the turn.",
             _pay_for_less(conn, world_id, viewer))

    found.sort(key=lambda item: (-item.score, item.source, item.subject))
    return found


def _treasury_of(conn: sqlite3.Connection, world_id: str, nation_id: str) -> float:
    row = conn.execute(
        "SELECT treasury FROM nation WHERE world_id = ? AND id = ?",
        (world_id, nation_id)).fetchone()
    return float(row["treasury"] or 0.0) if row is not None else 0.0


#: The event `atlas.engine.trade.assess_trade` cites for every cargo it settles. Matched on
#: to tell trade's adjustment of a treasury from the budget's, which lands on the same
#: column in the same turn. Restated here rather than imported because it is a *string in
#: the log*, not a constant that module exports -- and the test that pins them equal is the
#: honest way to keep them together.
TRADE_EVENT_TEXT = "Cargoes move, and the furnaces burn what arrives."


def cargo_settled(
    conn: sqlite3.Connection, world_id: str, nation_id: str, *, turn: int
) -> float:
    """What this treasury actually moved by *for cargo* on ``turn``. Negative for a buyer.

    **Joined through the citing event**, because `collect_revenue` adjusts the same column
    in the same turn and summing the column would count the payroll as a shipping bill.
    **And the delta is ``new - old``**, because an ``adjust`` row records the value the
    change *left*, not the change -- summing ``new_json`` reads every settlement as a
    credit, which is a mistake this project has already made once and measured.
    """
    row = conn.execute(
        "SELECT COALESCE(SUM(CAST(d.new_json AS REAL) - CAST(d.old_json AS REAL)), 0) AS net"
        "  FROM diff d"
        "  JOIN diff_batch b ON b.id = d.batch_id"
        "  JOIN event e ON e.id = d.reason_event_id"
        " WHERE b.world_id = ? AND b.turn = ? AND b.status IN ('applied','partial')"
        "   AND d.status = 'applied' AND d.target_table = 'nation'"
        "   AND d.target_id = ? AND d.field = 'treasury' AND d.op = 'adjust'"
        "   AND e.text = ?", (world_id, turn, nation_id, TRADE_EVENT_TEXT)).fetchone()
    return float(row["net"]) if row is not None else 0.0


def _arrears_crossing(
    conn: sqlite3.Connection, world_id: str, viewer: str, before: _Before, *, span: float,
):
    """``(months left, months left at turn start, revenue, upkeep, cargo)``, or ``None``.

    ``None`` when the commitments are met out of income, or when they already were not at
    the start of the turn -- which is the difference between a warning and a nag.

    Revenue and upkeep are :func:`atlas.engine.budget.nation_budget`'s, which is cheap and
    is the pair the treasury is actually settled from. The cargo line is what the last turn
    really cost, off the diff log, rather than a fresh walk of the settlement -- see the
    note on :func:`warnings_for` for why this one is not allowed to be expensive.
    """
    from .budget import nation_budget

    revenue, upkeep = nation_budget(conn, world_id, viewer)
    cargo = cargo_settled(conn, world_id, viewer, turn=_last_settled_turn(
        conn, world_id, before)) / (span if span > 0 else 1.0)
    net = revenue - upkeep + cargo
    if net >= 0:
        return None
    purse = _treasury_of(conn, world_id, viewer)
    months = purse / -net
    if months > ARREARS_HORIZON:
        return None
    was_purse = before.value("nation", viewer, "treasury", purse)
    was_months = was_purse / -net
    if was_months <= ARREARS_HORIZON:
        return None
    return months, was_months, revenue, upkeep, cargo


def _last_settled_turn(
    conn: sqlite3.Connection, world_id: str, before: _Before
) -> int:
    """The turn the diff log was last written on. See :func:`cargo_settled`."""
    row = conn.execute(
        "SELECT COALESCE(MAX(b.turn), 0) AS at FROM diff_batch b"
        " WHERE b.world_id = ? AND b.status IN ('applied','partial')",
        (world_id,)).fetchone()
    return int(row["at"]) if row is not None else 0
