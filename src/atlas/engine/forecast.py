"""Where the treasury goes over the next year if this government changes nothing.

**A current-policy estimate, and it says so everywhere.** This is not a prediction of what
will happen -- it is the arithmetic of what is already committed, run forward twelve months
with no new orders, no war, no repricing and nobody else doing anything. That is a narrow
claim and a useful one: a government that cannot see its own standing commitments cannot
tell an affordable war from an unaffordable one, and every arrears the engine has ever
charged has arrived as a surprise.

**Every line is the engine's own number, called rather than copied.** Revenue and upkeep
are :func:`atlas.engine.budget.nation_budget` -- the same pair the treasury is actually
settled from, labour drag, war footing, call-up drag, arsenal and pacts included. The trade
payments are :func:`atlas.engine.dependency.preview_settlement`, which is the settlement
walked without writing. The arrears rule is
:func:`atlas.engine.budget.collect_revenue`'s: pay what there is, floor the treasury at
zero, and charge the stability. Nothing here has an opinion of its own about money.

**What it deliberately does not model.** Growth in the economy, drift in stability and
legitimacy, manpower recovery, the world repricing a lane in four months' time, anybody
declaring anything. All of those are real and none of them is *current policy*; folding
them in would turn an auditable ledger into a forecast nobody could check. The one thing
that does move is upkeep, because a hull already paid for and due in five months is a
commitment this government has already made, and leaving it out would make the queue look
free.

**Two counterfactuals and no more.** Losing one named lane, and moving the call-up one rung.
Two, because a page of what-ifs is a page nobody reads, and these two because they are the
two levers this panel's own subject actually has: what somebody else can do to you, and
what you can do about it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from .budget import _UPKEEP, nation_budget
from .mobilisation import LEVELS, MAX_LEVEL, label_of, level_of
from .pacing import after_months
from .trade import IMPORT_BUDGET_SHARE

__all__ = [
    "HORIZON_MONTHS",
    "Counterfactual",
    "Delivery",
    "Forecast",
    "Ledger",
    "Month",
    "forecast_for",
    "monthly_ledger",
]

#: How far ahead a current-policy estimate is worth running. **Twelve, and no further.**
#: The assumption the whole thing rests on -- that nothing changes -- is thin at a year and
#: absurd at three, and a projection nobody should believe is worse than none.
HORIZON_MONTHS = 12

#: The label. Carried as a constant so the panel, the tooltip and the tests all say the same
#: words, and so nothing can quietly present this as a prediction.
CAVEAT = ("A current-policy estimate: what is already committed, run forward with nothing "
          "new ordered and nobody else acting. Not a prediction.")


@dataclass(frozen=True, slots=True)
class Ledger:
    """What one month costs and earns at today's commitments."""

    revenue: float
    upkeep: float
    #: What cargo costs this nation a month, and what it earns it. Both positive.
    imports: float
    exports: float

    @property
    def net(self) -> float:
        return round(self.revenue - self.upkeep + self.exports - self.imports, 3)


@dataclass(frozen=True, slots=True)
class Delivery:
    """Something already paid for that has not arrived yet, and what it will cost to keep."""

    what: str
    #: Months from now, at the reference month.
    due_in: float
    #: What it adds to upkeep once it is in service. Zero for a public work, which is
    #: delivered and then costs nothing to stand still.
    upkeep: float


@dataclass(frozen=True, slots=True)
class Month:
    """One month of the projection."""

    index: int
    date: str
    revenue: float
    upkeep: float
    imports: float
    exports: float
    #: What the treasury is expected to hold at the end of it.
    treasury: float
    #: True where the month's commitments could not be met out of what there was.
    arrears: bool = False
    #: What arrived this month, if anything.
    delivered: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Counterfactual:
    """One thing changed, and what the year looks like then."""

    label: str
    #: What it does to the treasury at the end of the horizon, against the base case.
    treasury_end: float
    delta: float
    #: When the money runs out under this branch, or ``""`` if it does not.
    arrears_on: str = ""
    because: str = ""


@dataclass(frozen=True, slots=True)
class Forecast:
    """A year of current policy, and the two branches off it."""

    nation_id: str
    name: str
    #: Where it starts.
    treasury: float
    ledger: Ledger
    months: tuple[Month, ...] = ()
    deliveries: tuple[Delivery, ...] = ()
    #: What has already left the treasury for things not yet delivered. Sunk: it is not a
    #: future outflow, and a forecast that charged it again would double-count the queue.
    already_paid: float = 0.0
    #: The first month the commitments cannot be met, or ``""``.
    arrears_on: str = ""
    counterfactuals: tuple[Counterfactual, ...] = ()
    caveat: str = CAVEAT
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def treasury_end(self) -> float:
        return self.months[-1].treasury if self.months else self.treasury


def monthly_ledger(
    conn: sqlite3.Connection, world_id: str, nation_id: str, *, turn: int,
) -> Ledger:
    """Revenue, upkeep and the trade bill for one month at today's commitments.

    Shared with :func:`atlas.engine.dependency.warnings_for`, so the sentence that says a
    government is heading for arrears and the page that shows the year cannot disagree
    about the month they are both built on.
    """
    from .dependency import preview_settlement

    revenue, upkeep = nation_budget(conn, world_id, nation_id)
    settlements = preview_settlement(conn, world_id, turn=turn, days=30)
    imports = round(sum(s.bill for s in settlements if s.buyer_id == nation_id), 3)
    exports = round(sum(s.bill for s in settlements if s.seller_id == nation_id), 3)
    return Ledger(revenue=revenue, upkeep=upkeep, imports=imports, exports=exports)


def _deliveries(
    conn: sqlite3.Connection, world_id: str, nation_id: str,
) -> tuple[tuple[Delivery, ...], float]:
    """What is in the yards, when it lands, and what it will cost to keep once it does.

    **The money is already gone.** ``atlas.engine.production`` charges the treasury when the
    order is given, so a hull under construction is not a future payment -- it is a future
    *upkeep*, and the second return value is what has already been spent on things that have
    not arrived. Both are reported, because a player looking at a thin treasury deserves to
    know how much of it is already in the water.
    """
    from .pacing import REFERENCE_DAYS

    try:
        rows = conn.execute(
            "SELECT c.id, c.kind, c.project_kind, c.days_remaining,"
            "       COALESCE(t.domain, 'land') AS domain,"
            "       COALESCE(t.cost_treasury, 0) AS cost"
            "  FROM construction c"
            "  LEFT JOIN unit_template t ON t.world_id = c.world_id AND t.kind = c.kind"
            " WHERE c.world_id = ? AND c.nation_id = ? ORDER BY c.days_remaining, c.id",
            (world_id, nation_id)).fetchall()
    except sqlite3.OperationalError:
        return (), 0.0
    found: list[Delivery] = []
    paid = 0.0
    for row in rows:
        is_unit = str(row["project_kind"] or "unit") == "unit"
        found.append(Delivery(
            what=str(row["kind"]),
            due_in=round(float(row["days_remaining"] or 0.0) / REFERENCE_DAYS, 2),
            upkeep=round(_UPKEEP.get(str(row["domain"]), _UPKEEP["land"]), 3)
            if is_unit else 0.0))
        paid += float(row["cost"] or 0.0)
    return tuple(found), round(paid, 2)


def _run(  # noqa: PLR0913 - a projection is the ledger, the queue, the purse and the clock
    ledger: Ledger, *, treasury: float, deliveries: tuple[Delivery, ...],
    start_date: str, months: int, import_allowance: float = IMPORT_BUDGET_SHARE,
) -> tuple[tuple[Month, ...], str]:
    """The projection itself, as plain arithmetic over the ledger. No database.

    Split out with no connection in its signature so the counterfactuals are the *same*
    projection with one input changed, rather than a second projection that might have
    drifted. The order inside a month is the order the engine runs it in --
    :func:`atlas.engine.budget.collect_revenue` before
    :func:`atlas.engine.trade.assess_trade` -- which is why a government that goes into
    arrears buys nothing that month: its treasury is zero when the cargo is priced.
    """
    purse = max(0.0, treasury)
    upkeep = ledger.upkeep
    arrears_on = ""
    out: list[Month] = []
    for index in range(1, months + 1):
        landed = tuple(sorted(
            item.what for item in deliveries
            if index - 1 < item.due_in <= index))
        when = after_months(start_date, index)
        net = ledger.revenue - upkeep
        short = purse + net < 0
        purse = 0.0 if short else purse + net
        if short and not arrears_on:
            arrears_on = when
        # The import allowance: a buyer never spends more than
        # :data:`atlas.engine.trade.IMPORT_BUDGET_SHARE` of its purse on cargo in one
        # month, which is the rule the settlement itself enforces. What it cannot afford is
        # simply not shipped, so the payment is capped rather than the treasury driven
        # negative.
        #
        # **The constant itself, not a copy of its value**, which used to be a literal
        # `0.5` sitting here: the day the settlement's share moves, a projection that had
        # written the old number down would go on promising a budget the engine no longer
        # allows, and nothing would say so.
        #
        # **And the uncompounded monthly figure, deliberately.** `assess_trade` passes it
        # through :func:`atlas.engine.pacing.compounded` because a *round* may be any
        # length; every step here is exactly one month, so the per-month rate is already
        # the rate for the step. :func:`atlas.engine.consent.buyer_maximum` is uncompounded
        # for the same reason -- its allowance and its demand are both monthly -- and that
        # asymmetry with `assess_trade` is intended in both places.
        paid = min(ledger.imports, purse * import_allowance)
        purse = max(0.0, purse - paid + ledger.exports)
        out.append(Month(
            index=index, date=when, revenue=round(ledger.revenue, 2),
            upkeep=round(upkeep, 2), imports=round(paid, 2),
            exports=round(ledger.exports, 2), treasury=round(purse, 2),
            arrears=short, delivered=landed))
        # And what arrived this month starts costing next month.
        upkeep += sum(item.upkeep for item in deliveries
                      if index - 1 < item.due_in <= index)
    return tuple(out), arrears_on


def forecast_for(
    conn: sqlite3.Connection, world_id: str, nation_id: str, *, turn: int,
    losing_route: str = "",
) -> Forecast:
    """Twelve months of current policy, with the two branches off it.

    ``losing_route`` names the lane the first counterfactual removes. Empty picks the
    largest bill or receipt this nation is at one end of, which is the one worth asking
    about; a route id that is not this nation's is ignored rather than refused, because a
    stale selection from a panel is not an error worth failing a page over.
    """
    from .dependency import preview_settlement

    row = conn.execute(
        "SELECT name, treasury FROM nation WHERE world_id = ? AND id = ?",
        (world_id, nation_id)).fetchone()
    if row is None:
        return Forecast(nation_id=nation_id, name=nation_id, treasury=0.0,
                        ledger=Ledger(0.0, 0.0, 0.0, 0.0),
                        notes=("There is no such country in this world.",))
    clock = conn.execute(
        "SELECT world_date FROM world WHERE id = ?", (world_id,)).fetchone()
    start = str(clock["world_date"]) if clock is not None else ""

    ledger = monthly_ledger(conn, world_id, nation_id, turn=turn)
    deliveries, already_paid = _deliveries(conn, world_id, nation_id)
    treasury = float(row["treasury"] or 0.0)
    months, arrears_on = _run(
        ledger, treasury=treasury, deliveries=deliveries, start_date=start,
        months=HORIZON_MONTHS)

    settlements = [s for s in preview_settlement(conn, world_id, turn=turn, days=30)
                   if nation_id in (s.seller_id, s.buyer_id) and s.bill > 0]
    chosen = next((s for s in settlements if s.route_id == losing_route), None)
    if chosen is None:
        chosen = max(settlements, default=None,
                     key=lambda s: (s.bill, s.route_id))

    end = months[-1].treasury if months else treasury
    branches: list[Counterfactual] = []
    names = {
        str(other["id"]): str(other["name"])
        for other in conn.execute("SELECT id, name FROM nation WHERE world_id = ?",
                                  (world_id,))}
    branches.append(_without_route(ledger, chosen, treasury, deliveries, start, end,
                                   nation_id, names))
    branches.append(_at_other_footing(conn, world_id, nation_id, ledger, treasury,
                                      deliveries, start, end))

    notes: list[str] = []
    if not settlements:
        notes.append("This country is at neither end of a lane that moves money, so the "
                     "trade lines are zero and the first branch has nothing to remove.")
    if any(item.upkeep > 0 for item in deliveries):
        notes.append(
            f"{already_paid:.0f} has already left the treasury for "
            f"{len(deliveries)} thing(s) still in the yards. That money is spent; what "
            "is ahead is what they cost to keep once they arrive.")
    return Forecast(
        nation_id=nation_id, name=str(row["name"]), treasury=round(treasury, 2),
        ledger=ledger, months=months, deliveries=deliveries,
        already_paid=already_paid, arrears_on=arrears_on,
        counterfactuals=tuple(branches), notes=tuple(notes))


def _without_route(  # noqa: PLR0913 - the branch is the ledger, the lane, the purse, the clock
    ledger: Ledger, lane, treasury: float, deliveries: tuple[Delivery, ...],
    start: str, base_end: float, nation_id: str, names: dict[str, str],
) -> Counterfactual:
    """The year with one lane gone. Money only -- what the *shortage* does to the yards is
    :class:`atlas.engine.dependency.Counterfactual`'s question, and answering it twice in
    two different units is how two panels come to disagree."""
    if lane is None:
        return Counterfactual(
            label="If a trade lane closed",
            treasury_end=base_end, delta=0.0,
            because="there is no priced lane to lose: nothing this country ships or "
                    "receives moves money.")
    inbound = lane.buyer_id == nation_id
    changed = Ledger(
        revenue=ledger.revenue, upkeep=ledger.upkeep,
        imports=round(max(0.0, ledger.imports - (lane.bill if inbound else 0.0)), 3),
        exports=round(max(0.0, ledger.exports - (0.0 if inbound else lane.bill)), 3))
    months, arrears_on = _run(changed, treasury=treasury, deliveries=deliveries,
                              start_date=start, months=HORIZON_MONTHS)
    end = months[-1].treasury if months else treasury
    other = lane.seller_id if inbound else lane.buyer_id
    return Counterfactual(
        label=f"If the {lane.good} lane with {names.get(other, other)} closed",
        treasury_end=end, delta=round(end - base_end, 2), arrears_on=arrears_on,
        because=(f"you stop paying {lane.bill:.2f} a month for it -- the purse is better "
                 "off and the furnaces are not"
                 if inbound else
                 f"you stop earning {lane.bill:.2f} a month from it"))


def _at_other_footing(  # noqa: PLR0913 - as above
    conn: sqlite3.Connection, world_id: str, nation_id: str, ledger: Ledger,
    treasury: float, deliveries: tuple[Delivery, ...], start: str, base_end: float,
) -> Counterfactual:
    """The year one rung up the call-up ladder, or one down from the top.

    **The drag is divided out and the other one multiplied in**, rather than the revenue
    re-derived: :func:`atlas.engine.budget.nation_budget` applies the footing's drag as a
    plain factor on the revenue it has already computed, so this is the same number with
    one factor exchanged, and it cannot drift from what the treasury would actually collect.
    """
    here = level_of(conn, world_id, nation_id)
    step = 1 if here < MAX_LEVEL else -1
    there = here + step
    now_drag, then_drag = LEVELS[here].drag, LEVELS[there].drag
    if now_drag >= 1.0:
        return Counterfactual(
            label="If the call-up moved a rung", treasury_end=base_end, delta=0.0,
            because="the footing's drag cannot be divided out of this revenue.")
    revenue = ledger.revenue / (1.0 - now_drag) * (1.0 - then_drag)
    changed = Ledger(revenue=round(revenue, 2), upkeep=ledger.upkeep,
                     imports=ledger.imports, exports=ledger.exports)
    months, arrears_on = _run(changed, treasury=treasury, deliveries=deliveries,
                              start_date=start, months=HORIZON_MONTHS)
    end = months[-1].treasury if months else treasury
    direction = "up to" if step > 0 else "down to"
    return Counterfactual(
        label=f"If you went {direction} {label_of(there)}",
        treasury_end=end, delta=round(end - base_end, 2), arrears_on=arrears_on,
        because=(f"the call-up takes {then_drag:.0%} of revenue instead of "
                 f"{now_drag:.0%}, which is {revenue - ledger.revenue:+.2f} a month. It "
                 f"also costs {LEVELS[there].unrest:.2f} stability a month, which this "
                 "page does not model."))
