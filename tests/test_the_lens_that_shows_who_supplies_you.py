"""The dependency lens may only show what the settlement is actually going to do.

The failure this file exists to prevent is not a crash. It is a panel that tells a player
their oil lane ships 4.95 a month for 0.56 while :func:`atlas.engine.trade.assess_trade`
moves 2.1 for 0.24 -- because then the player budgets against a number nothing enforces,
and the interface is lying with a straight face. So most of what is here is *reconciliation*:
the lens's preview and the engine's settlement are both run over the same world and compared
route by route, nation by nation, at three different turn lengths.

The rest divides into four claims:

* **it writes nothing** -- the world fingerprint is unmoved by deriving the lens, by ranking
  the warnings, by running the forecast and by rendering the panel in both themes;
* **it does not leak** -- a lens pointed at somebody else's country shows the lanes the
  viewer is at the other end of and nothing else, and reports that country's stores as a
  band from the viewer's own service rather than as a figure;
* **a warning is a crossing** -- it fires on the turn its measure crosses the line and is
  silent afterwards however bad the number stays, and a rewind un-remembers it, because
  what it remembers is the diff log and a rewind is what un-writes that;
* **the forecast is the engine's own arithmetic** -- its first month is what the treasury
  actually does when the engine is asked to run that month.

The rendering tests run offscreen. They catch the layout throwing, a reload stacking rows,
the wrong nation being read, and a read-only view that is not.
"""

from __future__ import annotations

import os
import sqlite3

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QWidget  # noqa: E402

from atlas.engine import consent, embargo, pricing, refusal  # noqa: E402
from atlas.engine.budget import collect_revenue, nation_budget  # noqa: E402
from atlas.engine.dependency import (  # noqa: E402
    ARREARS_HORIZON,
    CONCENTRATED,
    DEPENDENCY_ALARM,
    RESPONSES,
    RUNWAY_ALARM,
    STATIC_CAVEAT,
    STATUSES,
    TRADE_EVENT_TEXT,
    cargo_settled,
    lens_for,
    preview_settlement,
    warnings_for,
)
from atlas.engine.forecast import HORIZON_MONTHS, forecast_for, monthly_ledger  # noqa: E402
from atlas.engine.trade import (  # noqa: E402
    _MAX_MONTHS_STORED,
    SETTLEMENT_UNITS,
    assess_trade,
    demand_of,
    shortfall_of,
)
from atlas.state.bootstrap import (  # noqa: E402
    NationSeed,
    RegionSeed,
    WorldSeed,
    create_world,
    fingerprint,
)
from atlas.state.models import Difficulty  # noqa: E402
from atlas.state.replay import rewind_to  # noqa: E402
from atlas.ui.dependency import DependencyOverlay, anchors_from  # noqa: E402
from atlas.ui.theme import DARK, LIGHT  # noqa: E402

WORLD = "w_lens"
ME = "me"


@pytest.fixture(autouse=True)
def _layers_on():
    """Pricing, refusal and consent all up, which is the world this lens is *for*.

    Named here rather than assumed, for the reason `test_a_cargo_has_a_price` names them:
    a file that did not say would silently be measuring whichever flags the last test left
    behind. Embargoes stay down except where a test asks for them, because they are off by
    default and a lens that only worked with them up would not be the shipped one.
    """
    was = (pricing.set_enabled(True), refusal.set_enabled(True),
           consent.set_enabled(True), embargo.set_enabled(False))
    yield
    pricing.set_enabled(was[0])
    refusal.set_enabled(was[1])
    consent.set_enabled(was[2])
    embargo.set_enabled(was[3])


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


# ------------------------------------------------------------------------ the fixtures


def _world(nations: tuple[str, ...] = (ME, "sup", "alt", "cust"), treasury: float = 100.0):
    """Countries around one sea, so every pair has a lane and any of them can be cut."""
    return create_world(":memory:", WorldSeed(
        id=WORLD, name="Lens", start_date="2026-01-01", rng_seed=11,
        difficulty=Difficulty.NORMAL,
        nations=tuple(
            NationSeed(id=n, name=n.upper(), colour="#111", capital_region_id=f"r_{n}",
                       economy=50.0, industry=30.0, military_strength=20.0,
                       treasury=treasury)
            for n in nations),
        regions=tuple(
            [RegionSeed(id=f"r_{n}", name=n.upper(), geo_key=f"g:{n}", owner_nation_id=n,
                        development=5.0) for n in nations]
            + [RegionSeed(id="s_mid", name="The Middle Sea", geo_key="g:s", kind="ocean")]),
        adjacency=tuple((f"r_{n}", "s_mid", "sea") for n in nations),
    ))


def _endow(conn, nation: str, good: str, produced: float, stockpile: float) -> None:
    conn.execute(
        "UPDATE resource SET produced = ?, stockpile = ?"
        " WHERE world_id = ? AND nation_id = ? AND good = ?",
        (produced, stockpile, WORLD, nation, good))
    conn.commit()


def _comfortable(conn, nations=(ME, "sup", "alt", "cust")) -> None:
    """Everybody self-sufficient in everything, so a test's own endowments are the only
    thing that makes anybody dependent on anybody."""
    for nation in nations:
        for good in ("oil", "steel", "rare"):
            _endow(conn, nation, good, 8.0, 40.0)


def _route(conn, seller: str, buyer: str, good: str = "oil", volume: float | None = None,
           route_id: str | None = None) -> str:
    if volume is None:
        volume = demand_of(conn, buyer) * 1.5
    route_id = route_id or f"tr:{good}:{seller}:{buyer}"
    conn.execute(
        "INSERT INTO trade_route (id, world_id, seller_id, buyer_id, good, volume,"
        " opened_turn) VALUES (?, ?, ?, ?, ?, ?, 0)",
        (route_id, WORLD, seller, buyer, good, volume))
    conn.commit()
    return route_id


def _at_war(conn, a: str, b: str) -> None:
    for x, y in ((a, b), (b, a)):
        conn.execute(
            "UPDATE relation SET at_war = 1 WHERE world_id = ? AND nation_a = ?"
            "   AND nation_b = ?", (WORLD, x, y))
    conn.commit()


def _relations(conn, a: str, b: str, score: float) -> None:
    for x, y in ((a, b), (b, a)):
        conn.execute(
            "UPDATE relation SET score = ? WHERE world_id = ? AND nation_a = ?"
            "   AND nation_b = ?", (score, WORLD, x, y))
    conn.commit()


def _turns(conn, upto: int) -> None:
    """Sealed turn rows, which is what `rewind_to` walks back over."""
    for number in range(1, upto + 1):
        conn.execute(
            "INSERT OR IGNORE INTO turn (world_id, number, phase, status, start_date)"
            " VALUES (?, ?, 'sealed', 'sealed', ?)",
            (WORLD, number, f"2026-{number + 1:02d}-01"))
    conn.commit()


def _at_turn(conn, number: int) -> None:
    conn.execute("UPDATE world SET turn_number = ?, world_date = ? WHERE id = ?",
                 (number, f"2026-{min(12, number + 1):02d}-01", WORLD))
    conn.commit()


def single_supplier() -> sqlite3.Connection:
    """One power, one lane, nothing of its own: the captive buyer."""
    conn = _world()
    _comfortable(conn)
    _endow(conn, ME, "oil", 0.0, 12.0)
    _endow(conn, "sup", "oil", 30.0, 90.0)
    # And nobody else has any to spare, so there is genuinely nowhere else to go.
    _endow(conn, "alt", "oil", 3.0, 6.0)
    _endow(conn, "cust", "oil", 3.0, 6.0)
    _route(conn, "sup", ME, "oil")
    return conn


def diversified() -> sqlite3.Connection:
    """The same shortfall, three suppliers, and a customer of its own."""
    conn = _world()
    _comfortable(conn)
    _endow(conn, ME, "oil", 0.0, 12.0)
    for seller in ("sup", "alt", "cust"):
        _endow(conn, seller, "oil", 30.0, 90.0)
        _route(conn, seller, ME, "oil", volume=demand_of(conn, ME) * 0.5,
               route_id=f"tr:oil:{seller}:{ME}")
    _endow(conn, "cust", "steel", 0.0, 2.0)
    _route(conn, ME, "cust", "steel")
    return conn


def blockaded() -> sqlite3.Connection:
    """The single-supplier world with a war across the only water between them."""
    conn = single_supplier()
    _at_war(conn, ME, "sup")
    return conn


def suspended() -> sqlite3.Connection:
    """The single-supplier world with the relationship in ruins.

    **A different fact from a blockade and the panel must say so.** Nobody is stopping the
    ships: the two sides simply cannot agree a price, because hostility is the one term
    that raises what the seller wants and lowers what the buyer will pay at the same time.
    """
    conn = single_supplier()
    _relations(conn, ME, "sup", -70.0)
    return conn


FIXTURES = {"single_supplier": single_supplier, "diversified": diversified,
            "blockaded": blockaded, "suspended": suspended}


# --------------------------------------------- the lens agrees with what actually happens


def _net(items) -> dict[str, float]:
    """What the preview says each treasury will move by, netted **the way the engine nets it**.

    Which means on the integer lattice, and it has to be said out loud because this helper
    used to net in money: round each side to three decimals, round the total to two, and
    drop anything under a cent. That is the arithmetic `trade.SETTLEMENT_UNITS` exists to
    remove -- it is how the settlement created money -- and a reconciliation helper that
    kept it could only ever agree with a settlement that still had the bug. Sum the
    integers, divide once, and drop only an exact zero, which is what `assess_trade` does.
    """
    net: dict[str, int] = {}
    for item in items:
        if not item.bill_units:
            continue
        net[item.buyer_id] = net.get(item.buyer_id, 0) - item.bill_units
        net[item.seller_id] = net.get(item.seller_id, 0) + item.bill_units
    return {who: units / SETTLEMENT_UNITS for who, units in net.items() if units}


def _predicted(conn, turn: int, days: float) -> dict[str, float]:
    return _net(preview_settlement(conn, WORLD, turn=turn, days=days))


def _moved(conn, before: dict[str, float]) -> dict[str, float]:
    """What the treasuries *actually* moved by, read off the rows and not rounded.

    This used to round to two decimals and drop anything under a cent, which was the right
    shape when `report.settled` was itself a 2dp figure. It is not any more: a settlement
    is a whole number of `trade.SETTLEMENT_UNITS` and `apply` adds it to the column
    unrounded, so the column moves by exactly the amount the report names and the
    comparison can be exact. Rounding here would only be able to hide a discrepancy
    smaller than a cent -- which is precisely the size of the one that was creating money.
    """
    now = {
        str(row["id"]): float(row["treasury"])
        for row in conn.execute(
            "SELECT id, treasury FROM nation WHERE world_id = ?", (WORLD,))}
    return {who: now[who] - was for who, was in before.items() if now[who] != was}


@pytest.mark.parametrize("name", sorted(FIXTURES))
@pytest.mark.parametrize("days", [7, 30, 90])
def test_the_preview_is_what_the_settlement_then_does(name, days):
    """**The claim the whole panel rests on**, at three turn lengths.

    Route by route: what the lens says will ship is what ships, what it says will be paid is
    what is paid, and a lane it reports as stopped moves nothing. Run at a week, a month and
    a quarter, because every flow in the trade layer is span-scaled and a preview that only
    agreed at thirty days would be a preview that agreed by accident.
    """
    conn = FIXTURES[name]()
    predicted = {item.route_id: item
                 for item in preview_settlement(conn, WORLD, turn=1, days=days)}
    stock_before = {
        (str(r["nation_id"]), str(r["good"])): float(r["stockpile"])
        for r in conn.execute("SELECT nation_id, good, stockpile FROM resource"
                              " WHERE world_id = ?", (WORLD,))}
    treasuries = {
        str(row["id"]): float(row["treasury"])
        for row in conn.execute("SELECT id, treasury FROM nation WHERE world_id = ?",
                                (WORLD,))}
    money = _predicted(conn, turn=1, days=days)

    report = assess_trade(conn, WORLD, turn=1, date="2026-02-01", days=days)

    assert not report.problems
    assert money == report.settled, "the lens and the settlement disagree about the money"
    assert _moved(conn, treasuries) == report.settled

    # And the cargo: for each buyer and good, what arrived is what was predicted to arrive,
    # net of the month's burn and its own production.
    for item in predicted.values():
        assert item.status in STATUSES
        if item.status != "open":
            assert item.shipped == 0.0, item
            assert item.bill == 0.0, item
    delivered: dict[tuple[str, str], float] = {}
    for item in predicted.values():
        if item.shipped <= 0:
            continue
        delivered[(item.buyer_id, item.good)] = round(
            delivered.get((item.buyer_id, item.good), 0.0) + item.shipped, 3)
        delivered[(item.seller_id, item.good)] = round(
            delivered.get((item.seller_id, item.good), 0.0) - item.shipped, 3)
    for (nation, good), moved in delivered.items():
        row = conn.execute(
            "SELECT produced, stockpile FROM resource WHERE world_id = ? AND"
            " nation_id = ? AND good = ?", (WORLD, nation, good)).fetchone()
        expected = (stock_before[(nation, good)]
                    + float(row["produced"]) * (days / 30.0)
                    + moved
                    - demand_of(conn, nation) * (days / 30.0))
        # And the store's own ceiling, which is the engine's last word on the month: a
        # surplus above a year of demand is simply not kept.
        expected = min(max(0.0, expected),
                       demand_of(conn, nation) * _MAX_MONTHS_STORED)
        assert float(row["stockpile"]) == pytest.approx(expected, abs=0.02), (
            nation, good, moved)


def _filling():
    """A captive buyer whose store fills while the lane runs.

    The shape the engine's repricing clauses are *for*: the price is struck on turn one
    and then frozen until the stagger comes round, while the buyer's position improves
    underneath it. Every clause that reopens a route early fires on a world like this one.
    """
    conn = _world()
    _comfortable(conn)
    _endow(conn, ME, "oil", 0.0, 1.0)
    _endow(conn, "sup", "oil", 60.0, 200.0)
    _route(conn, "sup", ME, "oil", volume=9.0)
    return conn


@pytest.mark.parametrize("name", ["diversified", "_filling"])
def test_the_preview_is_what_the_settlement_does_on_every_turn_not_only_the_first(name):
    """**Turn one is the one turn that cannot catch this**, which is why it was missed.

    On turn one no route has been priced, so the panel and the engine both fall through to
    the same `price_route` call and agree by construction. It is turn *two* onwards that
    tells them apart: by then a price is standing, the stagger decides when it is
    reconsidered, and the buyer's ceiling may have moved under it. A panel that reads the
    stored price where the engine is about to settle a new one reports a bill nothing will
    charge -- measured at **-1.81 against a treasury that moved -1.03**, a 76% overstatement,
    on the turn the stagger first came round.

    Both are `atlas.engine.trade.settle_lane` now, so there is one decision rather than two
    that have to be kept in step. This runs the world forward and asserts they never part.
    """
    conn = _filling() if name == "_filling" else FIXTURES[name]()
    _turns(conn, 24)
    for turn in range(1, 25):
        date = f"2026-{min(12, turn + 1):02d}-01"
        _at_turn(conn, turn)
        collect_revenue(conn, WORLD, turn=turn, date=date, days=30)
        predicted = _predicted(conn, turn=turn, days=30)
        treasuries = {
            str(row["id"]): float(row["treasury"])
            for row in conn.execute("SELECT id, treasury FROM nation WHERE world_id = ?",
                                    (WORLD,))}

        report = assess_trade(conn, WORLD, turn=turn, date=date, days=30)

        assert predicted == report.settled, (
            f"turn {turn}: the panel and the settlement disagree about the money")
        assert _moved(conn, treasuries) == report.settled, (
            f"turn {turn}: the treasury did not move by what was settled")


def test_the_panel_and_the_settlement_share_one_derivation():
    """Structural, and it is the guarantee the test above only samples.

    Two walks over the same routes cannot be kept in step by review; the reconciliation
    tests can only ever check the worlds somebody thought to build. So the lens does not
    have its own copy of the decision -- it calls the engine's.
    """
    import inspect

    from atlas.engine import dependency as lens
    from atlas.engine import trade

    source = inspect.getsource(lens._walk)
    assert "settle_lane(" in source, (
        "the lens must ask the engine what a lane does, not decide for itself")
    for owned in ("consent.band(", "pricing.price_route("):
        assert owned not in source, (
            f"{owned} is the settlement's to call, and a second caller is a second answer")
    assert "settle_lane" in inspect.getsource(trade.assess_trade), (
        "and the engine must be reading the same function, or there are still two")


def _two_worlds_differing_only_in_what_is_hidden(mine: float, theirs: float):
    """The same world twice, with one number changed: what FOR digs up and what it holds.

    Everything a viewer is entitled to see is pinned identical between the two -- the same
    lane, the same volume, the same settled terms, the same relations -- so any difference
    that shows up on the panel is a difference the viewer was not entitled to.
    """
    made = []
    for produced, stockpile in ((mine, mine * 6), (theirs, theirs * 6)):
        conn = _world(nations=(ME, "for", "alt", "cust"))
        _comfortable(conn, nations=(ME, "for", "alt", "cust"))
        for good in ("oil", "steel", "rare"):
            _endow(conn, "for", good, produced, stockpile)
        _endow(conn, ME, "oil", 30.0, 90.0)
        _route(conn, ME, "for", "oil", volume=2.0, route_id="tr:oil:me:for")
        # Terms settled and stamped, identically, so the negotiated price -- which both
        # parties know and which does legitimately move with the buyer's position -- is
        # held still and cannot stand in for the figure under test.
        conn.execute(
            "UPDATE trade_route SET unit_price = 0.2, priced_turn = 1 WHERE id = ?",
            ("tr:oil:me:for",))
        conn.commit()
        made.append(conn)
    return made


def test_two_worlds_that_differ_only_in_hidden_production_read_the_same():
    """**The whole disclosure guarantee in one assertion.**

    Four published figures used to put a withheld one back: ``demand x (1 - dependency)``
    returned the production to four decimals, ``cover x demand`` returned the store,
    ``volume / share - shipped`` returned the production again, and the stopped-lane
    sentence simply narrated it. Each was individually defensible and together they left
    nothing withheld.

    Patching four formulas would invite a fifth. The guarantee is stated the other way
    round instead: change *only* what the viewer may not see, and the page may not move.
    Anything that does move is a channel, whether or not anybody has thought of the
    arithmetic yet.
    """
    # A fivefold difference in what FOR digs up, chosen so that its *behaviour* is the
    # same in both: it is short of oil either way and goes on buying. Whether a customer
    # is still buying is something a seller observes by being paid; how much ground it has
    # of its own is not, and that is the figure under test.
    a, b = _two_worlds_differing_only_in_what_is_hidden(0.5, 2.5)
    one = lens_for(a, WORLD, ME, "for", turn=1)
    two = lens_for(b, WORLD, ME, "for", turn=1)

    assert not one.own and not two.own, "this has to be the low-visibility page"
    assert one.routes and two.routes, "a page with no lanes proves nothing"
    assert one.capacity_now is None and two.capacity_now is None
    assert one.pace_now is None and two.pace_now is None

    for x, y in zip(one.goods, two.goods, strict=True):
        assert x.good == y.good
        # The banded estimate is the *sanctioned* channel and is meant to move with the
        # truth; it is the one field allowed to differ, and it is checked separately.
        for field in ("produced", "stockpile", "demand", "runway", "dependency", "cover",
                      "imported", "exported", "top_supplier", "top_supplier_share",
                      "top_customer", "top_customer_share", "concentration",
                      "suppliers", "customers"):
            assert getattr(x, field) == getattr(y, field), (
                f"{x.good}.{field} moved with a figure the viewer cannot see: "
                f"{getattr(x, field)!r} against {getattr(y, field)!r}")

    for x, y in zip(one.routes, two.routes, strict=True):
        assert (x.route_id, x.inbound, x.other_id) == (y.route_id, y.inbound, y.other_id)
        assert x.share == y.share, "the lane share carried the hidden supply in its divisor"
        assert x.why == y.why, "the sentence narrated a position the viewer cannot see"
        assert x.status == y.status


def test_the_low_visibility_page_reads_the_same_word_for_word(qapp):
    """And on the glass, which is where a leak actually reaches somebody.

    The object-level test above cannot see a sentence assembled in the interface out of two
    fields that are each innocent -- which is exactly what *Your own ground covers 100%*
    was. So the rendered page is compared too, line for line.
    """
    a, b = _two_worlds_differing_only_in_what_is_hidden(0.5, 2.5)

    def said(conn):
        overlay = DependencyOverlay(theme=DARK)
        overlay.load(conn, WORLD, ME, "for", turn=1)
        # The service's own banded reading is the sanctioned channel; everything else on
        # the page is under test.
        return [label.text() for label in overlay.findChildren(QLabel)
                if label.text() and "your service puts its" not in label.text()]

    assert said(a) == said(b)


def test_the_money_on_the_panel_is_the_money_that_moves():
    """The two headline figures -- what goes out for cargo and what comes in -- are the
    ones the treasury is actually charged and credited."""
    conn = diversified()
    lens = lens_for(conn, WORLD, ME, turn=1)
    treasuries = {
        str(row["id"]): float(row["treasury"])
        for row in conn.execute("SELECT id, treasury FROM nation WHERE world_id = ?",
                                (WORLD,))}

    assess_trade(conn, WORLD, turn=1, date="2026-02-01", days=30)
    moved = _moved(conn, treasuries)

    assert lens.net == pytest.approx(moved.get(ME, 0.0), abs=0.001)
    assert lens.bill > 0 and lens.receipts > 0, (
        "a fixture where nothing moves either way proves nothing")


def test_a_buyer_that_cannot_pay_is_shown_not_paying():
    """**The affordability cap, previewed in the order the engine draws it down.**

    A buyer never spends more than half its purse on cargo in a month, and the routes are
    settled in id order -- so which of two lanes gets cut depends on which id sorts first.
    A preview that applied the cap in a different order would name the wrong lane, which is
    worse than not naming one.
    """
    conn = _world(treasury=0.4)
    _comfortable(conn)
    _endow(conn, ME, "oil", 0.0, 12.0)
    _endow(conn, ME, "steel", 0.0, 12.0)
    _endow(conn, "sup", "oil", 30.0, 90.0)
    _endow(conn, "alt", "steel", 30.0, 90.0)
    first = _route(conn, "sup", ME, "oil", route_id="tr:a")
    second = _route(conn, "alt", ME, "steel", route_id="tr:b")

    predicted = {item.route_id: item
                 for item in preview_settlement(conn, WORLD, turn=1, days=30)}
    assert predicted[first].shipped > 0, "the first lane by id should get the money"
    assert predicted[second].shipped < predicted[second].volume, (
        "the second should be cut short by a purse the first has drawn down")

    treasuries = {
        str(row["id"]): float(row["treasury"])
        for row in conn.execute("SELECT id, treasury FROM nation WHERE world_id = ?",
                                (WORLD,))}
    report = assess_trade(conn, WORLD, turn=1, date="2026-02-01", days=30)
    assert _predicted_for(predicted) == report.settled
    assert _moved(conn, treasuries) == report.settled


def _predicted_for(predicted) -> dict[str, float]:
    return _net(predicted.values())


def test_one_seller_cannot_ship_the_same_barrel_twice():
    """A seller's pool is drawn down by each lane in turn, and the preview draws it down
    in the same order for the same reason the affordability cap does."""
    conn = _world()
    _comfortable(conn)
    _endow(conn, "sup", "oil", 6.0, 1.0)
    for buyer in (ME, "alt", "cust"):
        _endow(conn, buyer, "oil", 0.0, 0.0)
        _route(conn, "sup", buyer, "oil", volume=5.0, route_id=f"tr:oil:sup:{buyer}")

    predicted = {item.route_id: item
                 for item in preview_settlement(conn, WORLD, turn=1, days=30)}
    shipped = sum(item.shipped for item in predicted.values())
    assert shipped == pytest.approx(7.0, abs=0.01), (
        "the seller has 6 produced and 1 stored and cannot send 15")

    report = assess_trade(conn, WORLD, turn=1, date="2026-02-01", days=30)
    assert _predicted_for(predicted) == report.settled


# ---------------------------------------------------------------- the fixtures, read


def test_the_captive_buyer_is_told_it_is_captive():
    conn = single_supplier()
    lens = lens_for(conn, WORLD, ME, turn=1)
    oil = next(item for item in lens.goods if item.good == "oil")

    assert oil.top_supplier == "SUP"
    assert oil.top_supplier_share == pytest.approx(1.0)
    assert oil.concentration == pytest.approx(1.0), "one supplier is a concentration of 1"
    assert oil.suppliers == 1
    assert any("SUP" in item.headline for item in lens.vulnerabilities)
    assert lens.vulnerabilities[0].severity >= CONCENTRATED


def test_the_diversified_buyer_is_not():
    conn = diversified()
    lens = lens_for(conn, WORLD, ME, turn=1)
    oil = next(item for item in lens.goods if item.good == "oil")

    assert oil.suppliers == 3
    assert oil.top_supplier_share < DEPENDENCY_ALARM
    assert oil.concentration < 0.5
    assert not any(item.severity >= 0.9 and item.good == "oil"
                   for item in lens.vulnerabilities), [v.headline
                                                       for v in lens.vulnerabilities]


def test_a_blockaded_lane_says_which_of_the_five_reasons_it_is():
    """The statuses are kept apart precisely so a player can tell a navy from a market."""
    conn = blockaded()
    lens = lens_for(conn, WORLD, ME, turn=1)

    assert len(lens.routes) == 1
    lane = lens.routes[0]
    assert lane.status == "at_war"
    assert lane.settlement.shipped == 0.0
    assert "at war" in lane.settlement.why
    assert lens.stopped == lens.routes
    assert any("SUP" in item.headline for item in lens.vulnerabilities)


def test_a_lane_stopped_on_terms_is_not_reported_as_a_blockade():
    """The distinction the whole status vocabulary exists for: a lane nobody is stopping
    and a lane a fleet is stopping have different answers, and only one of them is a navy."""
    conn = suspended()
    lens = lens_for(conn, WORLD, ME, turn=1)
    lane = lens.routes[0]

    assert lane.status == "suspended"
    assert lane.settlement.shipped == 0.0
    assert "stopped taking" in lane.settlement.why or "stopped shipping" in (
        lane.settlement.why)
    # And the answer offered is the relationship, not a fleet.
    stopped = next(item for item in lens.vulnerabilities
                   if item.route_id == lane.route_id)
    assert stopped.response.kind == "repair_relations"

    # And the settlement agrees: no cargo, no money.
    treasuries = {
        str(row["id"]): float(row["treasury"])
        for row in conn.execute("SELECT id, treasury FROM nation WHERE world_id = ?",
                                (WORLD,))}
    report = assess_trade(conn, WORLD, turn=1, date="2026-02-01", days=30)
    assert report.refused or report.withdrawn, report
    assert _moved(conn, treasuries) == {}


def test_a_standing_suspension_says_since_when():
    """A stoppage that has stood for turns and one that began this month are different
    facts about the same lane, and only the column knows which."""
    conn = suspended()
    assess_trade(conn, WORLD, turn=1, date="2026-02-01", days=30)
    stored = conn.execute(
        "SELECT suspended_turn FROM trade_route WHERE id = ?",
        (f"tr:oil:sup:{ME}",)).fetchone()
    assert int(stored["suspended_turn"]) == 1, "the fixture must actually record one"

    lens = lens_for(conn, WORLD, ME, turn=2)
    assert "stopped since turn 1" in lens.routes[0].settlement.why


def test_an_embargo_line_names_a_country_rather_than_a_row_id():
    conn = single_supplier()
    embargo.set_enabled(True)
    conn.execute(
        "INSERT INTO trade_embargo (id, world_id, proposer_id, target_id, good,"
        " opened_turn, opened_on, status, episodes)"
        " VALUES ('e2', ?, 'sup', ?, 'oil', 0, '2026-01-01', 'standing', 1)", (WORLD, ME))
    conn.commit()

    why = lens_for(conn, WORLD, ME, turn=1).routes[0].settlement.why
    assert why.startswith("SUP "), why


def test_a_sanction_and_a_war_are_different_facts_about_the_same_lane():
    conn = single_supplier()
    # An organisation *is* a nation row, keyed on `nation_id`; `alt` stands in for the
    # body and `sup` is the member whose obligation stops the cargo.
    conn.execute(
        "INSERT INTO organisation (nation_id, world_id, kind) VALUES ('alt', ?, 'alliance')",
        (WORLD,))
    conn.execute(
        "INSERT INTO organisation_member (organisation_id, nation_id, joined_turn)"
        " VALUES ('alt', 'sup', 0)")
    conn.execute(
        "INSERT INTO sanction (id, world_id, organisation_id, target_nation_id,"
        " opened_turn, until_turn) VALUES ('s1', ?, 'alt', ?, 0, 99)", (WORLD, ME))
    conn.commit()

    lens = lens_for(conn, WORLD, ME, turn=1)
    assert lens.routes[0].status == "sanctioned"
    assert lens.routes[0].status != "blockaded"


def test_an_embargoed_lane_is_reported_as_a_decision_rather_than_a_market():
    conn = single_supplier()
    embargo.set_enabled(True)
    conn.execute(
        "INSERT INTO trade_embargo (id, world_id, proposer_id, target_id, good,"
        " opened_turn, opened_on, status, episodes)"
        " VALUES ('e1', ?, 'sup', ?, 'oil', 0, '2026-01-01', 'standing', 1)", (WORLD, ME))
    conn.commit()

    lens = lens_for(conn, WORLD, ME, turn=1)
    assert lens.routes[0].status == "embargoed"
    assert "refusing" in lens.routes[0].settlement.why


def test_losing_the_only_lane_costs_pace_and_says_when():
    conn = single_supplier()
    lens = lens_for(conn, WORLD, ME, turn=1)

    assert lens.counterfactuals, "a moving inbound lane has a counterfactual"
    worst = lens.counterfactuals[0]
    assert worst.bites
    assert worst.pace_after < worst.pace_now
    assert worst.months is not None and worst.months > 0, (
        "the store is what stands between the loss and the consequence")


def test_a_lane_the_country_does_not_need_costs_it_nothing():
    conn = _world()
    _comfortable(conn)
    # ME digs up more than it burns; the lane is a top-up rather than a lifeline.
    _endow(conn, ME, "oil", 20.0, 40.0)
    _endow(conn, "sup", "oil", 30.0, 90.0)
    _route(conn, "sup", ME, "oil", volume=1.0)

    lens = lens_for(conn, WORLD, ME, turn=1)
    assert lens.counterfactuals
    assert not lens.counterfactuals[0].bites


def _advance(conn, turn: int) -> None:
    """One turn, in the order the engine runs it: revenue, then cargo."""
    date = f"2026-{min(12, turn + 1):02d}-01"
    _at_turn(conn, turn)
    collect_revenue(conn, WORLD, turn=turn, date=date, days=30)
    assess_trade(conn, WORLD, turn=turn, date=date, days=30)


def test_the_counterfactual_is_what_actually_happens_when_the_lane_goes():
    """**The estimate, checked against the world rather than against its own arithmetic.**

    The panel says *without this lane your yards slow from N% to M% of their pace in about K
    months*.
    So take the lane away for real and run the turns. A counterfactual nobody has falsified
    is a number the panel made up, however carefully it was derived.

    Tested at the state the estimate was taken from, which is the only fair test: it is a
    static first-order claim about *this* world, and the store goes on moving if you wait.
    See `test_a_stale_counterfactual_is_stale_in_the_direction_the_caveat_says`.
    """
    conn = single_supplier()
    _turns(conn, 30)
    _advance(conn, 1)
    _at_turn(conn, 2)

    estimate = lens_for(conn, WORLD, ME, turn=2).counterfactuals[0]
    assert estimate.bites and estimate.months is not None

    _drop_routes(conn, (estimate.route_id,), turn=2)
    fell_on = None
    for turn in range(2, 14):
        _advance(conn, turn)
        if fell_on is None and shortfall_of(conn, WORLD, ME) <= estimate.pace_after + 1e-9:
            fell_on = turn

    assert shortfall_of(conn, WORLD, ME) == pytest.approx(estimate.pace_after), (
        "the magnitude: the yards ended somewhere other than where the panel said")
    assert fell_on is not None
    # The engine settles in whole turns and the estimate is a fraction of a month, so
    # landing on the exact month would be luck. Within a month either way is the claim.
    assert abs((fell_on - 2 + 1) - estimate.months) <= 1.0, (
        f"predicted {estimate.months} months, took {fell_on - 2 + 1}")


def test_a_stale_counterfactual_is_stale_in_the_direction_the_caveat_says():
    """**Why the label is on the panel.** The estimate is a statement about the world it was
    read from. Let three turns pass with the lane still shipping and the store *grows*, so
    the same lane's loss is further away than it was -- and the panel, re-read, says so.

    This is the whole content of *static and first-order*: not that the number is unreliable,
    but that it is a claim about now, and now moves.
    """
    conn = single_supplier()
    _turns(conn, 30)
    _advance(conn, 1)
    _at_turn(conn, 2)
    early = lens_for(conn, WORLD, ME, turn=2).counterfactuals[0]

    for turn in (2, 3, 4):
        _advance(conn, turn)
    _at_turn(conn, 5)
    later = lens_for(conn, WORLD, ME, turn=5).counterfactuals[0]

    assert later.route_id == early.route_id
    assert later.months > early.months, (
        "the store filled while nobody cut the lane, so the drain is further off")
    assert later.pace_after == early.pace_after, "and the magnitude is unchanged"


def test_no_single_lane_bites_until_the_alternatives_are_gone():
    """**The first-order caveat, from the other end.** With three suppliers the panel says
    losing any one of them costs nothing, and it is right. Losing the second is a different
    sentence, and the panel only says it once the first is gone -- because that is when it
    becomes true. A counterfactual that tried to answer *and then what* would be guessing at
    a world nobody has played.
    """
    conn = diversified()
    _turns(conn, 30)
    _advance(conn, 1)
    _at_turn(conn, 2)

    first = lens_for(conn, WORLD, ME, turn=2)
    oil = [item for item in first.counterfactuals if item.good == "oil"]
    assert len(oil) == 3
    assert not any(item.bites for item in oil), "three lanes, and no one of them is load-bearing"

    _drop_routes(conn, ("tr:oil:alt:me",), turn=2)
    for turn in range(2, 6):
        _advance(conn, turn)
    _at_turn(conn, 6)

    second = lens_for(conn, WORLD, ME, turn=6)
    left = [item for item in second.counterfactuals if item.good == "oil"]
    assert len(left) == 2
    assert all(item.bites for item in left), (
        "with two lanes carrying exactly the demand, either one of them is now the one")


# --------------------------------------------------------- the responses are real levers


def test_every_vulnerability_names_something_that_can_be_done():
    for name, build in sorted(FIXTURES.items()):
        conn = build()
        lens = lens_for(conn, WORLD, ME, turn=1)
        assert lens.vulnerabilities, name
        for item in lens.vulnerabilities:
            assert item.response.text, (name, item.headline)
            assert item.response.how, (name, item.headline)
            if not item.response.available:
                assert item.response.blocked_because, (name, item.headline)


def test_the_five_responses_are_always_offered_and_say_when_they_are_not_available():
    """**A response that is not available is named and explained, not hidden.** Knowing
    what *would* answer a thing is most of knowing what it is, and a list that silently
    dropped the unavailable ones would teach a player the lever does not exist."""
    conn = single_supplier()
    lens = lens_for(conn, WORLD, ME, turn=1)
    kinds = [item.kind for item in lens.responses]

    assert kinds == ["seek_supplier", "repair_relations", "protect_the_lane", "conserve",
                     "restrict_exports"]
    assert set(kinds) <= set(RESPONSES), "a lever the vocabulary does not name"
    assert set(RESPONSES) - set(kinds) == {"spend_less"}, (
        "spend_less is the money lever and belongs to the arrears warning, not to the "
        "standing list about goods")
    conserve = next(item for item in lens.responses if item.kind == "conserve")
    assert not conserve.available, (
        "there is no conservation lever in this engine and the panel must not invent one")
    assert "industry" in conserve.blocked_because


def test_a_buyer_with_nowhere_else_to_go_is_told_so_rather_than_told_to_shop_around():
    conn = single_supplier()
    lens = lens_for(conn, WORLD, ME, turn=1)
    seek = next(item for item in lens.responses if item.kind == "seek_supplier")

    assert not seek.available
    assert "no other power" in seek.blocked_because


def test_a_buyer_with_alternatives_is_told_how_many():
    conn = diversified()
    lens = lens_for(conn, WORLD, ME, turn=1)
    seek = next(item for item in lens.responses if item.kind == "seek_supplier")

    assert seek.available
    assert "other supplier" in seek.text


# ------------------------------------------------------------------------- visibility


def test_a_foreign_economy_is_estimated_and_never_read_off_the_row():
    """The exact figure is the leak this whole layer exists to prevent."""
    conn = diversified()
    _endow(conn, "sup", "rare", 17.0, 123.0)

    theirs = lens_for(conn, WORLD, ME, "sup", turn=1)
    assert not theirs.own
    for item in theirs.goods:
        assert item.produced is None
        assert item.stockpile is None
        assert item.estimated
    rare = next(item for item in theirs.goods if item.good == "rare")
    assert "123" not in rare.estimated, "the store came back exact"
    assert "17" not in rare.estimated.split("digs")[0]


def test_a_foreign_lens_shows_the_lanes_you_are_party_to_and_no_others():
    conn = diversified()
    # A lane between two other powers that the viewer has nothing to do with.
    _endow(conn, "alt", "steel", 0.0, 1.0)
    _route(conn, "sup", "alt", "steel", route_id="tr:steel:sup:alt")

    theirs = lens_for(conn, WORLD, ME, "sup", turn=1)
    assert [view.route_id for view in theirs.routes] == [f"tr:oil:sup:{ME}"]
    assert any("not party to" in line for line in theirs.unknowns)


def test_a_foreign_lens_with_lanes_does_not_claim_there_are_none():
    """The first draft said *you are not party to any of its trade* on a page listing a
    lane the viewer was at the other end of. The three cases are three sentences."""
    conn = diversified()
    theirs = lens_for(conn, WORLD, ME, "sup", turn=1)

    assert theirs.routes
    assert not any("not party to any" in line for line in theirs.unknowns)
    assert any("cannot see" in line for line in theirs.unknowns)


def test_a_foreign_store_is_unknown_rather_than_reassuring(qapp):
    """**The runway is `None` for a foreign power because the viewer does not know it, not
    because the store is holding**, and the two must not print the same green sentence."""
    conn = diversified()
    host, screen = _screen()
    screen.load(conn, WORLD, ME, "sup", turn=1)

    shown = _texts(screen)
    assert "unknown" in shown
    assert "not running down" not in shown
    host.deleteLater()


def test_a_spectator_is_told_so_rather_than_shown_an_empty_panel():
    conn = diversified()
    nothing = lens_for(conn, WORLD, None, turn=1)
    assert not nothing.nation_id
    assert any("spectating" in line for line in nothing.unknowns)


# ------------------------------------------------------------- it moves nothing, ever


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_reading_the_lens_changes_nothing(name):
    conn = FIXTURES[name]()
    before = fingerprint(conn, WORLD)

    lens_for(conn, WORLD, ME, turn=1)
    lens_for(conn, WORLD, ME, "sup", turn=1)
    preview_settlement(conn, WORLD, turn=1, days=30)
    preview_settlement(conn, WORLD, turn=1, days=7)
    warnings_for(conn, WORLD, ME, turn=1)
    forecast_for(conn, WORLD, ME, turn=1)

    assert fingerprint(conn, WORLD) == before


def test_a_panel_left_open_reloads_when_the_world_moves_under_it(qapp):
    """**A closed panel cannot be stale; a pinned one is the whole problem.**

    `load` rebuilds this page every time it opens, so for most of its life the question
    never arises. But `Overlay.pinnable` is on, which means a player can keep the lens up
    beside the rewind sheet, press it, and go on reading. Driving the real window before
    this existed: **zero of 779 labels changed** across a rewind that moved the world, and
    a fresh read of the rewound world differed from what was on the screen in six -- among
    them a counterfactual reading 476.66 where the truth was 436.69.

    What decides it is `world_revision`, the id of the last diff written to the world: a
    turn moves it up, a rewind moves it down, and either way it is no longer the number
    the page was drawn at.
    """
    conn = single_supplier()
    _turns(conn, 4)
    _at_turn(conn, 1)

    overlay = DependencyOverlay(theme=DARK)
    overlay.load(conn, WORLD, ME, turn=1)
    overlay.setVisible(True)
    at_one = [label.text() for label in overlay.findChildren(QLabel) if label.text()]

    # Nothing has happened, so nothing is re-read. This half matters as much as the other:
    # `_refresh_all` calls it every turn, and a cold read of this page is a settlement walk.
    assert overlay.refresh_if_stale(conn, WORLD, turn=1) is False
    assert [label.text() for label in overlay.findChildren(QLabel)
            if label.text()] == at_one

    _at_turn(conn, 2)
    assess_trade(conn, WORLD, turn=2, date="2026-03-01", days=30)
    assert overlay.refresh_if_stale(conn, WORLD, turn=2) is True
    at_two = [label.text() for label in overlay.findChildren(QLabel) if label.text()]
    assert at_two != at_one, "the world moved and the panel on the screen did not"

    assert rewind_to(conn, WORLD, 1).ok
    _at_turn(conn, 1)
    assert overlay.refresh_if_stale(conn, WORLD, turn=1) is True

    # And what it now shows is what a page opened now would show -- not merely something
    # different from what it showed before.
    fresh = DependencyOverlay(theme=DARK)
    fresh.load(conn, WORLD, ME, turn=1)
    assert ([label.text() for label in overlay.findChildren(QLabel) if label.text()]
            == [label.text() for label in fresh.findChildren(QLabel) if label.text()])


def test_the_window_refreshes_the_lens_with_everything_else():
    """Read in the source rather than by building a window, for the reason the other
    wiring tests in this tree are: a `MainWindow` needs a world on disk and a geometry
    pack. What is asserted is the one thing that was missing -- that the refresh path
    every world change goes through knows this panel exists."""
    import inspect

    from atlas.ui.main_window import AtlasWindow

    source = inspect.getsource(AtlasWindow._refresh_all)
    assert "_dependency.refresh_if_stale" in source, (
        "a world change must reach the lens the same way it reaches the map and the rails")


def test_the_lens_survives_a_rewind_of_the_world_it_read():
    """A read-only view opened either side of a rewind leaves the world where it was, and
    reads the world it is actually looking at both times."""
    conn = single_supplier()
    _turns(conn, 2)
    _at_turn(conn, 1)
    before = fingerprint(conn, WORLD)
    was = lens_for(conn, WORLD, ME, turn=1)

    _at_turn(conn, 2)
    assess_trade(conn, WORLD, turn=2, date="2026-03-01", days=30)
    after = lens_for(conn, WORLD, ME, turn=2)
    assert after.goods != was.goods, "the world moved and the lens did not notice"

    assert rewind_to(conn, WORLD, 1).ok
    assert fingerprint(conn, WORLD) == before
    _at_turn(conn, 1)
    assert lens_for(conn, WORLD, ME, turn=1).goods == was.goods


@pytest.mark.parametrize("days", [7, 30, 90])
def test_the_lens_reads_the_same_month_however_long_the_turn_is(days):
    """**The panel speaks in months at every pace**, which is what makes its figures
    comparable between two worlds and between two players. The settlement is span-scaled
    and this is not: what the lens reports is what a month costs, and running the world
    at a week or a quarter does not change what a month costs."""
    conn = single_supplier()
    monthly = lens_for(conn, WORLD, ME, turn=1)
    before = fingerprint(conn, WORLD)

    # Reading the settlement at another span must not move the world or the lens.
    preview_settlement(conn, WORLD, turn=1, days=days)
    assert fingerprint(conn, WORLD) == before
    assert lens_for(conn, WORLD, ME, turn=1).routes == monthly.routes


# --------------------------------------------------------------- warnings are crossings


def test_a_quiet_world_produces_no_economic_warnings():
    """The same promise the substrate makes everywhere: an empty list is what a quiet world
    produces, and nothing here manufactures a reason to worry."""
    conn = diversified()
    assert warnings_for(conn, WORLD, ME, turn=0) == []


def test_a_store_that_crosses_into_the_last_few_months_says_so_once():
    conn = _world()
    _comfortable(conn)
    _endow(conn, ME, "steel", 0.0, 13.5)
    _endow(conn, ME, "oil", 8.0, 40.0)

    fired: list[list[str]] = []
    for number in range(1, 5):
        assess_trade(conn, WORLD, turn=number, date=f"2026-{number + 1:02d}-01", days=30)
        fired.append([item.source for item in warnings_for(conn, WORLD, ME, turn=number)])

    crossings = [index + 1 for index, sources in enumerate(fired)
                 if "econ_runway" in sources]
    assert len(crossings) == 1, fired
    # And it crossed where the arithmetic says it should. 13.5 in store, 3.3 burnt a
    # month: turn 1 leaves 10.2 (3.1 months of cover, still outside the band) and turn 2
    # leaves 6.9 (2.1 months, inside it).
    assert crossings == [2], fired
    # And it is the stated threshold it crossed, not a number this test made up.
    steel = next(item for item in lens_for(conn, WORLD, ME, turn=2).goods
                 if item.good == "steel")
    assert steel.runway is not None and steel.runway <= RUNWAY_ALARM


def test_a_warning_names_the_response_and_points_somewhere_the_program_can_go():
    from atlas.engine.attention import NAV_KINDS
    from atlas.engine.capabilities import action_types

    conn = _world()
    _comfortable(conn)
    _endow(conn, ME, "steel", 0.0, 13.5)
    for number in (1, 2):
        assess_trade(conn, WORLD, turn=number, date=f"2026-{number + 1:02d}-01", days=30)
    found = warnings_for(conn, WORLD, ME, turn=2)

    assert found
    known = set(action_types())
    for item in found:
        assert item.nav_target.kind in NAV_KINDS
        assert item.nav_target.target
        assert "What you could do" in item.reason_selected
        assert item.suggested_order_category in known or not item.suggested_order_category


def test_a_dependency_that_appears_this_turn_crosses_and_one_that_was_always_there_does_not():
    conn = diversified()
    _turns(conn, 2)
    _at_turn(conn, 1)
    assert not [item for item in warnings_for(conn, WORLD, ME, turn=1)
                if item.source == "econ_dependency"]

    # Two of the three lanes are cancelled inside turn 2, leaving one supplier carrying
    # everything -- which is a dependency that did not exist when the turn began.
    _at_turn(conn, 2)
    _drop_routes(conn, ("tr:oil:alt:me", "tr:oil:cust:me"), turn=2)
    found = [item for item in warnings_for(conn, WORLD, ME, turn=2)
             if item.source == "econ_dependency"]

    assert len(found) == 1, [item.subject for item in
                             warnings_for(conn, WORLD, ME, turn=2)]
    assert "SUP" in found[0].subject
    # And the turn after, with nothing further changed, it is silent.
    _at_turn(conn, 3)
    assert not [item for item in warnings_for(conn, WORLD, ME, turn=3)
                if item.source == "econ_dependency"]


def _drop_routes(conn, route_ids, *, turn: int) -> None:
    """Cancel agreements the way the engine does, as diffs, so the diff log records them."""
    from atlas.state.apply import apply_batch, record_event
    from atlas.state.diff import Diff, DiffBatch
    from atlas.state.models import DiffOp, EventKind, EventOrigin, Role, Visibility

    event_id = record_event(
        conn, world_id=WORLD, turn=turn, date="2026-03-01",
        kind=EventKind.TRADE_AGREEMENT, origin=EventOrigin.ENGINE,
        text="Two agreements lapse.", visibility=Visibility.PUBLIC)
    outcome = apply_batch(conn, DiffBatch(
        world_id=WORLD, turn=turn, proposer_role=Role.ENGINE, proposer_model=None,
        diffs=tuple(
            Diff("trade_route", route_id, "__delete__", DiffOp.DELETE, None,
                 reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
            for route_id in route_ids)))
    assert outcome.applied, outcome.reason
    conn.commit()


def test_a_rewind_un_remembers_a_crossing():
    """**Nothing is remembered, so a rewind cannot leave a stale warning behind.** The
    memory is the diff log; a rewind is what un-writes it."""
    conn = diversified()
    _turns(conn, 2)
    _at_turn(conn, 1)
    before = fingerprint(conn, WORLD)

    _at_turn(conn, 2)
    _drop_routes(conn, ("tr:oil:alt:me", "tr:oil:cust:me"), turn=2)
    assert [item for item in warnings_for(conn, WORLD, ME, turn=2)
            if item.source == "econ_dependency"]

    assert rewind_to(conn, WORLD, 1).ok
    assert fingerprint(conn, WORLD) == before
    assert not [item for item in warnings_for(conn, WORLD, ME, turn=2)
                if item.source == "econ_dependency"]


def test_a_purse_that_crosses_into_arrears_says_so_once():
    """The fifth warning, and the one with a date on it. Fired on the turn the cover
    crosses a year, and silent on every turn after it however far the purse falls."""
    # An army it cannot pay for, and an economy pinned flat so the deficit does not
    # quietly grow its way out of the problem: 3.76 a month against a falling purse, so
    # the cover falls by one month a turn and crosses the year on turn four.
    conn = _world(treasury=53.0)
    _comfortable(conn)
    for index in range(40):
        conn.execute(
            "INSERT INTO unit (id, world_id, nation_id, region_id, kind, strength)"
            " VALUES (?, ?, ?, ?, 'infantry', 10)",
            (f"u{index}", WORLD, ME, f"r_{ME}"))
    conn.execute("UPDATE nation SET economy = 2.0, industry = 0 WHERE id = ?", (ME,))
    conn.commit()

    fired: list[list[str]] = []
    for number in range(1, 7):
        collect_revenue(conn, WORLD, turn=number, date=f"2026-{number + 1:02d}-01",
                        days=30)
        fired.append([item.source for item in warnings_for(conn, WORLD, ME, turn=number)])

    crossings = [index + 1 for index, sources in enumerate(fired)
                 if "econ_arrears" in sources]
    assert crossings == [4], fired
    said = next(item for item in warnings_for(conn, WORLD, ME, turn=4)
                if item.source == "econ_arrears")
    assert "stand down formations" in said.reason_selected.lower()
    assert said.suggested_order_category == "disband_units", (
        "the same order the alert layer already answers an arrears with")


def test_the_trade_event_this_reads_is_the_one_the_settlement_writes():
    """**A string in the log, matched on.** `cargo_settled` tells trade's adjustment of a
    treasury from the budget's by the event each cites, and the only thing holding the two
    together is this assertion -- reword the settlement's event and the arrears warning
    quietly starts reading a cargo bill of zero."""
    conn = diversified()
    assess_trade(conn, WORLD, turn=1, date="2026-02-01", days=30)
    text = conn.execute(
        "SELECT text FROM event WHERE world_id = ? AND kind = 'trade_agreement'"
        "   AND origin = 'ENGINE' ORDER BY id DESC LIMIT 1", (WORLD,)).fetchone()

    assert str(text["text"]) == TRADE_EVENT_TEXT
    # And it reads the money the right way round: a buyer's is negative.
    assert cargo_settled(conn, WORLD, ME, turn=1) != 0.0


def test_the_warnings_never_walk_the_settlement():
    """**The cost claim, asserted structurally.** `warnings_for` runs inside every turn's
    attention read; the settlement walk costs about three seconds on the seeded modern-day
    board. If a future edit reaches for `preview_settlement` here, the turn pays for it and
    nothing else in this file would notice.
    """
    import atlas.engine.dependency as module

    conn = diversified()
    assess_trade(conn, WORLD, turn=1, date="2026-02-01", days=30)
    real = module.preview_settlement

    def refuse(*_args, **_kwargs):
        raise AssertionError("warnings_for walked the settlement")

    module.preview_settlement = refuse
    try:
        warnings_for(conn, WORLD, ME, turn=1)
    finally:
        module.preview_settlement = real


def test_the_substrate_carries_them_and_the_vocabulary_knows_them():
    from atlas.engine.attention import ATTENTION_SOURCES, attention_for

    conn = _world()
    _comfortable(conn)
    _endow(conn, ME, "steel", 0.0, 13.5)
    for number in (1, 2):
        assess_trade(conn, WORLD, turn=number, date=f"2026-{number + 1:02d}-01", days=30)
    before = fingerprint(conn, WORLD)
    ranked = attention_for(conn, WORLD, ME, turn=2)

    assert fingerprint(conn, WORLD) == before
    economic = [item for item in ranked if item.source.startswith("econ_")]
    assert economic
    for item in economic:
        assert item.source in ATTENTION_SOURCES
        assert item.reason_selected and item.subject


# ------------------------------------------------------------------------- the forecast


def test_the_forecast_uses_the_budget_the_treasury_is_actually_settled_from():
    conn = diversified()
    found = forecast_for(conn, WORLD, ME, turn=1)
    revenue, upkeep = nation_budget(conn, WORLD, ME)

    assert found.ledger.revenue == revenue
    assert found.ledger.upkeep == upkeep
    assert len(found.months) == HORIZON_MONTHS
    assert "current-policy estimate" in found.caveat


def test_the_first_month_is_what_the_engine_then_does():
    """**The reconciliation for the fiscal page.** A projection whose very first month
    disagrees with the engine is a projection of a different game."""
    conn = diversified()
    found = forecast_for(conn, WORLD, ME, turn=1)

    collect_revenue(conn, WORLD, turn=1, date="2026-02-01", days=30)
    assess_trade(conn, WORLD, turn=1, date="2026-02-01", days=30)
    actual = float(conn.execute(
        "SELECT treasury FROM nation WHERE id = ?", (ME,)).fetchone()["treasury"])

    assert found.months[0].treasury == pytest.approx(actual, abs=0.02)


def test_a_government_that_cannot_meet_its_commitments_is_given_the_date():
    conn = _world(treasury=6.0)
    _comfortable(conn)
    # An army it cannot pay for.
    for index in range(40):
        conn.execute(
            "INSERT INTO unit (id, world_id, nation_id, region_id, kind, strength)"
            " VALUES (?, ?, ?, ?, 'infantry', 10)",
            (f"u{index}", WORLD, ME, f"r_{ME}"))
    conn.execute("UPDATE nation SET economy = 1.0 WHERE id = ?", (ME,))
    conn.commit()

    found = forecast_for(conn, WORLD, ME, turn=1)
    assert found.ledger.net < 0
    assert found.arrears_on, "a purse that runs out has a date on it"
    assert any(month.arrears for month in found.months)


def test_the_two_counterfactuals_are_the_two_that_were_promised():
    conn = diversified()
    found = forecast_for(conn, WORLD, ME, turn=1)

    assert len(found.counterfactuals) == 2
    lane, footing = found.counterfactuals
    assert "lane" in lane.label
    assert "mobilisation" in footing.label or "war" in footing.label
    assert footing.because, "a branch that cannot say what it changed is not a branch"


def test_losing_a_lane_you_pay_for_leaves_the_purse_better_off_and_the_furnaces_worse():
    """The two counterfactuals answer different questions in different units, deliberately:
    money here, the yards' pace on the lens. A page that mixed them would read as a
    contradiction."""
    conn = single_supplier()
    money = forecast_for(conn, WORLD, ME, turn=1).counterfactuals[0]
    yards = lens_for(conn, WORLD, ME, turn=1).counterfactuals[0]

    assert money.delta > 0, "you stop paying for it"
    assert yards.pace_after < yards.pace_now, "and you stop receiving it"


def test_the_arrears_warning_and_the_forecast_agree_about_the_month():
    conn = _world(treasury=6.0)
    _comfortable(conn)
    for index in range(40):
        conn.execute(
            "INSERT INTO unit (id, world_id, nation_id, region_id, kind, strength)"
            " VALUES (?, ?, ?, ?, 'infantry', 10)",
            (f"u{index}", WORLD, ME, f"r_{ME}"))
    conn.execute("UPDATE nation SET economy = 1.0 WHERE id = ?", (ME,))
    conn.commit()

    ledger = monthly_ledger(conn, WORLD, ME, turn=1)
    assert ledger.net < 0
    assert 6.0 / -ledger.net <= ARREARS_HORIZON
    assert forecast_for(conn, WORLD, ME, turn=1).arrears_on


# ------------------------------------------------------------------------ the screen


def _screen(theme=DARK) -> tuple[QWidget, DependencyOverlay]:
    host = QWidget()
    host.resize(1180, 1400)
    return host, DependencyOverlay(theme, host)


def _texts(widget: QWidget) -> list[str]:
    return [child.text() for child in widget.findChildren(QLabel) if child.text()]


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_the_screen_renders_each_fixture(qapp, name):
    conn = FIXTURES[name]()
    host, screen = _screen()
    screen.load(conn, WORLD, ME, turn=1)

    shown = " ".join(_texts(screen))
    assert "SUP" in shown
    assert "oil" in shown.lower()
    assert "What you can do about it".upper() in shown.upper()
    host.deleteLater()


def test_the_screen_calls_the_counterfactual_what_it_is(qapp):
    """A number this easy to read as a forecast has to say that it is not one, and say it
    above the figure rather than in a footnote underneath."""
    conn = single_supplier()
    host, screen = _screen()
    screen.load(conn, WORLD, ME, turn=1)

    shown = " ".join(_texts(screen))
    assert STATIC_CAVEAT in shown
    assert "first-order" in shown
    host.deleteLater()


def test_the_screen_writes_nothing_to_the_world_it_shows(qapp):
    conn = blockaded()
    before = fingerprint(conn, WORLD)

    host, screen = _screen()
    screen.load(conn, WORLD, ME, turn=1)
    screen.apply_theme(LIGHT)
    screen.load(conn, WORLD, ME, turn=1)

    assert fingerprint(conn, WORLD) == before
    host.deleteLater()


def test_reloading_does_not_stack_rows(qapp):
    conn = diversified()
    host, screen = _screen()
    screen.load(conn, WORLD, ME, turn=1)
    once = len(_texts(screen))
    for _ in range(3):
        screen.load(conn, WORLD, ME, turn=1)

    assert len(_texts(screen)) == once
    host.deleteLater()


def test_both_themes_render(qapp):
    conn = single_supplier()
    for theme in (DARK, LIGHT):
        host, screen = _screen(theme)
        screen.load(conn, WORLD, ME, turn=1)
        screen.apply_theme(theme)
        assert _texts(screen)
        host.deleteLater()


def test_the_screen_says_when_it_is_looking_at_somebody_elses_economy(qapp):
    conn = diversified()
    host, screen = _screen()
    screen.load(conn, WORLD, ME, "sup", turn=1)

    shown = " ".join(_texts(screen))
    assert "estimates" in shown or "estimate" in shown
    host.deleteLater()


def test_a_spectator_sees_a_sentence_rather_than_an_empty_panel(qapp):
    conn = diversified()
    host, screen = _screen()
    screen.load(conn, WORLD, None, turn=1)

    assert any("spectating" in text for text in _texts(screen))
    host.deleteLater()


def test_the_lane_map_places_countries_where_the_pack_puts_them(qapp):
    """The anchors come from the pack's own label points, so the picture and the map cannot
    disagree about where a country is. A world with no geometry falls back to the fan."""
    conn = diversified()
    labels = {"g:me": (0.0, 0.0), "g:sup": (10.0, 5.0), "g:alt": (-8.0, 3.0),
              "g:cust": (4.0, -6.0)}
    anchors = anchors_from(conn, WORLD, labels)

    assert anchors["sup"] == (10.0, 5.0)
    host, screen = _screen()
    screen.load(conn, WORLD, ME, turn=1, anchors=anchors)
    screen.load(conn, WORLD, ME, turn=1, anchors={})
    host.deleteLater()


# --------------------------------------------------------------------------- wiring


def test_the_bar_offers_it_and_the_window_opens_it():
    """The screen exists and nothing reaches it -- the gap this project has shipped twice."""
    from pathlib import Path

    topbar = Path("src/atlas/ui/topbar.py").read_text(encoding="utf-8")
    window = Path("src/atlas/ui/main_window.py").read_text(encoding="utf-8")
    matters = Path("src/atlas/ui/matters.py").read_text(encoding="utf-8")

    assert "dependency_requested" in topbar
    assert "self._dependency.clicked.connect(self.dependency_requested.emit)" in topbar
    assert "self._dependency" in topbar.split("for button in")[1], (
        "the control is built and never added to the menu")
    assert "DependencyOverlay" in window
    assert "self._top.dependency_requested.connect(self._open_dependency)" in window
    assert "def _open_dependency" in window
    # And the warning's own way in.
    assert "lens_requested" in matters
    assert "self._matters.lens_requested.connect(self._lens_from_matters)" in window


def test_an_island_nobody_can_reach_is_not_threatened_with_a_floor_it_is_exempt_from(
        monkeypatch):
    """``shortfall_of`` exempts a nation the map cannot reach, so its yards never slow. A
    runway warning that says they will is a threat the engine never carries out."""
    import atlas.engine.dependency as dependency

    conn = _world()
    _comfortable(conn)
    _endow(conn, ME, "oil", 0.0, 3.0)  # none of its own, a few months in store
    reachable = lens_for(conn, WORLD, ME, turn=1)
    assert any("runs out" in v.headline for v in reachable.vulnerabilities), (
        "the premise: a reachable country is warned")

    monkeypatch.setattr(dependency, "is_unreachable", lambda *_args: True)
    island = lens_for(conn, WORLD, ME, turn=1)
    assert not any("runs out" in v.headline for v in island.vulnerabilities)
