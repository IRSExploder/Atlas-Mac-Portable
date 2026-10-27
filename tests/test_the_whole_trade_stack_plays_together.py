"""Five layers that each behave alone, asked what they do in company.

Every test file this branch added pins **one** layer with the others held down, deliberately
and correctly: `test_a_cargo_has_a_price` fixes the vetoes off so a floor-pinned route cannot
fail a price assertion, `test_a_buyer_can_say_that_is_too_much` fixes the seller's reserve
off, and `test_an_embargo_is_a_decision_somebody_makes` fixes both vetoes off "so a test that
let refusal or consent also stop a lane would be a test that could pass with the embargo doing
nothing at all". That discipline is what makes each of those files mean something.

It also means **nothing in the suite has ever run them together**, and the contracts between
them are exactly where a joint failure would live:

* `trade.assess_trade` answers the embargo **before** the band, and `continue`s. That order
  is load-bearing -- it is what keeps a coerced lane from being reported as a bargain that
  stopped paying -- and it is enforced by nothing but the order of two blocks;
* the band's buyer half and `refusal`'s seller half are **one call and two verdicts**, so
  switching one off must not change what the other decides;
* the ``large`` matcher gives a buyer a second supplier, which is the thing that decides
  whether an embargo is a severance or an inconvenience;
* and a suspension is written to the route row **conditionally**, which is the shape of
  thing a rewind misses.

One more is pinned here that is not about the trade layer at all, because the measurement of
this branch turned on it: **`atlas.sim.rng.derive` hashes the world id**, so two worlds with
the same numeric seed and different names are different worlds. Every paired-arm harness on
this branch named its world after its arm, which means its control and its treatment were
never the same world. That is not an engine bug, and it is the reason a test says so out loud.
"""

from __future__ import annotations

import pytest

from atlas.engine import consent, embargo, intent, pricing, refusal
from atlas.engine.trade import assess_trade, demand_of, settled_price
from atlas.sim.rng import derive
from atlas.state.bootstrap import (
    NationSeed,
    RegionSeed,
    WorldSeed,
    create_world,
    fingerprint,
)
from atlas.state.models import Difficulty

WORLD = "w_stack"


@pytest.fixture(autouse=True)
def _every_layer_up():
    """All four flags on, which is the configuration a merge would make default.

    The opposite of the other files' fixtures, on purpose. Any test here that wants a layer
    down turns it down itself and says why.
    """
    was = (pricing.set_enabled(True), refusal.set_enabled(True),
           consent.set_enabled(True), embargo.set_enabled(True))
    yield
    pricing.set_enabled(was[0])
    refusal.set_enabled(was[1])
    consent.set_enabled(was[2])
    embargo.set_enabled(was[3])


def _world(nations: tuple[str, ...] = ("a", "b", "c"), treasury: float = 400.0,
           world_id: str = WORLD):
    """Countries around one sea, so every pair has a lane and any of them can be cut."""
    return create_world(":memory:", WorldSeed(
        id=world_id, name="Stack", start_date="2026-01-01", rng_seed=11,
        difficulty=Difficulty.NORMAL,
        nations=tuple(
            NationSeed(id=n, name=n.upper(), colour="#111", capital_region_id=f"r_{n}",
                       economy=50.0, industry=30.0, military_strength=20.0,
                       treasury=treasury)
            for n in nations
        ),
        regions=tuple(
            [RegionSeed(id=f"r_{n}", name=n.upper(), geo_key=f"g:{n}", owner_nation_id=n,
                        development=5.0) for n in nations]
            + [RegionSeed(id="s_mid", name="The Middle Sea", geo_key="g:s", kind="ocean")]
        ),
        adjacency=tuple((f"r_{n}", "s_mid", "sea") for n in nations),
    ))


def _endow(conn, nation: str, good: str, produced: float, stockpile: float,
           world_id: str = WORLD) -> None:
    conn.execute(
        "UPDATE resource SET produced = ?, stockpile = ?"
        " WHERE world_id = ? AND nation_id = ? AND good = ?",
        (produced, stockpile, world_id, nation, good))
    conn.commit()


def _seller(conn, nation="a", good="oil", produced=40.0, stockpile=200.0, **kw):
    _endow(conn, nation, good, produced, stockpile, **kw)


def _starved(conn, nation="b", good="oil", **kw):
    _endow(conn, nation, good, 0.0, 0.0, **kw)


def _route(conn, seller="a", buyer="b", good="oil", volume=None, world_id=WORLD):
    if volume is None:
        volume = demand_of(conn, buyer) * 1.5
    route_id = f"tr:{good}:{seller}:{buyer}"
    conn.execute(
        "INSERT INTO trade_route (id, world_id, seller_id, buyer_id, good, volume,"
        "                         opened_turn) VALUES (?, ?, ?, ?, ?, ?, 0)",
        (route_id, world_id, seller, buyer, good, volume))
    conn.commit()
    return route_id


def _relations(conn, a: str, b: str, score: float, world_id: str = WORLD) -> None:
    for x, y in ((a, b), (b, a)):
        conn.execute(
            "UPDATE relation SET score = ?"
            " WHERE world_id = ? AND nation_a = ? AND nation_b = ?", (score, world_id, x, y))
    conn.commit()


def _treasuries(conn, world_id: str = WORLD) -> dict[str, float]:
    return {str(r["id"]): float(r["treasury"]) for r in conn.execute(
        "SELECT id, treasury FROM nation WHERE world_id = ?", (world_id,))}


def _stock(conn, nation: str, good: str = "oil", world_id: str = WORLD) -> float:
    return float(conn.execute(
        "SELECT stockpile FROM resource WHERE world_id = ? AND nation_id = ? AND good = ?",
        (world_id, nation, good)).fetchone()["stockpile"])


def _suspension(conn, route_id: str) -> tuple[int, str]:
    row = conn.execute(
        "SELECT suspended_turn, suspended_by FROM trade_route WHERE id = ?",
        (route_id,)).fetchone()
    return int(row["suspended_turn"]), str(row["suspended_by"] or "")


def _hostile_pair(treasury: float = 400.0):
    """A sells oil to B, they loathe each other, and the terms have been settled once.

    Hostility is the one term that pushes the price **up** and the buyer's ceiling **down**
    at the same time, so it is the only state in which the band and the embargo are both
    live on the same lane -- which is the state every interaction question here needs.
    """
    conn = _world(treasury=treasury)
    _seller(conn)
    _starved(conn)
    route = _route(conn)
    #: **Soured before the terms are struck, not after.** A price is settled once and then
    #: frozen until the stagger comes round, so relations broken *after* turn one leave the
    #: standing price answering a world that no longer exists and the band still overlaps.
    #: That is the layer behaving correctly and it is not the state this file needs.
    _relations(conn, "a", "b", -85.0)
    assess_trade(conn, WORLD, turn=1, date="2026-02-01", days=30)
    return conn, route


# ------------------------------------------------------- the order the layers answer in

def test_a_lane_is_stopped_for_exactly_one_reason_and_it_is_the_decision():
    """The contract the whole layering rests on, asserted where both halves are true.

    The pair is hostile enough that the buyer's ceiling has fallen through the standing
    price -- so on this lane, *both* the band and the embargo would stop the cargo. The
    engine must report the one somebody chose. A player told "the terms stopped working"
    about a lane that was actually severed cannot tell a market from an ultimatum, and
    telling those two apart is the entire argument of `atlas.engine.embargo`.
    """
    conn, route = _hostile_pair()

    # Without the embargo, this lane is the band's: hostility has closed it commercially.
    band_only = assess_trade(conn, WORLD, turn=2, date="2026-03-01", days=30)
    assert band_only.refused, "the fixture is not hostile enough to close the band"
    assert not band_only.embargoed

    item, why = embargo.impose(
        conn, WORLD, proposer_id="a", target_id="b", turn=3, date="2026-04-01")
    assert item is not None, why

    report = assess_trade(conn, WORLD, turn=3, date="2026-04-01", days=30)
    assert report.embargoed, "a severed lane was not reported as severed"
    assert not report.refused, (
        "a lane one government stopped on purpose was also reported as a bargain that "
        "stopped paying: " + str(report.refused))
    assert not report.withdrawn
    assert not report.interrupted


def test_an_embargo_does_not_leave_a_stale_suspension_behind_it():
    """The band writes `suspended_turn`; the embargo does not, and answers first.

    So a lane can be suspended by the buyer, then severed, then released -- and the row has
    to still say the true thing at each step. The failure this guards is a route that reads
    "suspended by the buyer" for the six months an embargo stood over it, which would put a
    wrong sentence in front of a player for half a game.
    """
    conn, route = _hostile_pair()
    assess_trade(conn, WORLD, turn=2, date="2026-03-01", days=30)
    turn, by = _suspension(conn, route)
    assert turn >= 0 and by == "buyer"

    embargo.impose(conn, WORLD, proposer_id="a", target_id="b", turn=3, date="2026-04-01")
    assess_trade(conn, WORLD, turn=3, date="2026-04-01", days=30)
    # The suspension stands where the band left it; the embargo neither clears it nor
    # re-stamps it with its own turn.
    assert _suspension(conn, route) == (turn, by)


# ---------------------------------------------------- the layers against one another

def test_switching_the_buyers_ceiling_off_does_not_change_what_the_seller_decides():
    """One call, two verdicts, and they must not be entangled.

    `consent.band` asks `refusal.seller_declines` first and carries its answer. If the
    buyer's half could change the seller's half, then every measurement of the seller's veto
    taken before the band existed would be wrong, and a player switching the flag would see
    withdrawals appear and vanish for no reason anybody could name.
    """
    conn, route = _hostile_pair()
    price, _ = settled_price(conn, route)

    with_band = consent.band(conn, WORLD, "a", "b", "oil", price, shipped=5.0)
    was = consent.set_enabled(False)
    try:
        without_band = consent.band(conn, WORLD, "a", "b", "oil", price, shipped=5.0)
    finally:
        consent.set_enabled(was)

    assert with_band.seller_min == without_band.seller_min
    assert (with_band.withdrawal is None) == (without_band.withdrawal is None)


def test_a_second_supplier_is_what_makes_an_embargo_survivable():
    """The matcher's interaction with the embargo, which is the one that changes the game.

    The ``large`` route-matching mode exists to give a large buyer two suppliers. Its
    consequence for coercion had never been asserted: an embargo against a captive buyer
    stops its imports, and the *same* embargo against a buyer with a second lane stops a
    fraction of them. Built here as two worlds differing in one route, because that is what
    the matcher's flag does to a board.
    """
    def imports_after_embargo(second_supplier: bool) -> float:
        conn = _world()
        _seller(conn, "a")
        _starved(conn, "b")
        _route(conn, "a", "b")
        if second_supplier:
            _seller(conn, "c")
            _route(conn, "c", "b")
        assess_trade(conn, WORLD, turn=1, date="2026-02-01", days=30)
        embargo.impose(conn, WORLD, proposer_id="a", target_id="b",
                       turn=2, date="2026-03-01")
        before = float(conn.execute(
            "SELECT stockpile FROM resource WHERE world_id = ? AND nation_id = 'b'"
            "   AND good = 'oil'", (WORLD,)).fetchone()[0])
        assess_trade(conn, WORLD, turn=2, date="2026-03-01", days=30)
        after = float(conn.execute(
            "SELECT stockpile FROM resource WHERE world_id = ? AND nation_id = 'b'"
            "   AND good = 'oil'", (WORLD,)).fetchone()[0])
        conn.close()
        return after - before

    captive = imports_after_embargo(second_supplier=False)
    diversified = imports_after_embargo(second_supplier=True)
    assert captive <= 0.0, "a captive buyer kept importing through an embargo"
    assert diversified > captive, (
        "the second supplier the `large` matcher exists to create did not carry any cargo "
        "when the first was severed", captive, diversified)


def test_money_is_conserved_with_every_layer_up_and_over_many_turns():
    """What one treasury loses another gains, turn after turn, with all four flags on.

    Each layer was checked for this alone. Together they compound: a suspension that took
    money off a buyer without paying a seller, or an embargo that debited a sacrifice twice,
    would net to a few hundredths a turn -- invisible on turn one, and a visible inflation by
    turn ninety.
    """
    conn = _world(("a", "b", "c"))
    _seller(conn, "a")
    _starved(conn, "b")
    _seller(conn, "c")
    _route(conn, "a", "b")
    _route(conn, "c", "b")
    total_before = sum(_treasuries(conn).values())

    _relations(conn, "a", "b", -85.0)
    assess_trade(conn, WORLD, turn=1, date="2026-02-01", days=30)
    embargo.impose(conn, WORLD, proposer_id="c", target_id="b", turn=2, date="2026-03-01")
    for turn in range(2, 10):
        assess_trade(conn, WORLD, turn=turn, date=f"2026-{turn + 1:02d}-01", days=30)

    total_after = sum(_treasuries(conn).values())
    assert total_after == pytest.approx(total_before, abs=1e-6), (
        "the trade stack created or destroyed money over eight turns",
        total_before, total_after)


def test_an_embargo_nobody_declares_changes_nothing_with_the_whole_stack_up():
    """The escape, re-asked in the configuration a merge would actually ship.

    `test_an_untouched_world_ships_exactly_as_it_did` pins this with the two vetoes held
    down. That is the right test for that file and it is not this claim: what a player gets
    if the embargo becomes available is the *whole* stack with an affordance nobody has used
    yet, and if the flag alone moves that world then the mechanic is not additive.
    """
    def play(with_embargo: bool) -> str:
        was = embargo.set_enabled(with_embargo)
        try:
            conn = _world(world_id="w_same")
            _seller(conn, "a", world_id="w_same")
            _starved(conn, "b", world_id="w_same")
            _seller(conn, "c", world_id="w_same")
            _route(conn, "a", "b", world_id="w_same")
            _route(conn, "c", "b", world_id="w_same")
            _relations(conn, "a", "b", -85.0, world_id="w_same")
            for turn in range(1, 8):
                assess_trade(conn, "w_same", turn=turn, date=f"2026-{turn + 1:02d}-01",
                             days=30)
            mark = fingerprint(conn, "w_same")
            conn.close()
            return mark
        finally:
            embargo.set_enabled(was)

    assert play(True) == play(False)


def test_embargoing_somebody_does_not_make_you_look_distressed_to_everybody_else():
    """A government does not get the pariah discount for a choice it made itself.

    The channel is real and would have been easy to leave open. `pricing.seller_distress`
    reads `_blocked_share` -- the share of a seller's *other* lanes that cannot deliver --
    and a low distress reading walks its prices toward `FLOOR_PRICE`, which is exactly where
    `refusal` lets a seller walk away. If a standing embargo counted as a lane that "cannot
    deliver", then embargoing one customer would discount your goods to everybody else and
    then have you withdrawing from them too: one decision cascading into a general
    liquidation, which is not what anybody chose.

    It does not, because `_blocked_share` walks `trade.route_is_open` -- war, sanction, and
    the sea -- and an embargo is none of those. Asserted rather than assumed, because the
    two modules were written a commit apart and the next person to add a stoppage will have
    to decide this again.
    """
    conn = _world()
    _seller(conn, "a", produced=80.0, stockpile=400.0)
    _starved(conn, "b")
    _starved(conn, "c")
    _route(conn, "a", "b")
    _route(conn, "a", "c")
    assess_trade(conn, WORLD, turn=1, date="2026-02-01", days=30)

    before = pricing.seller_distress(conn, WORLD, "a", "c")
    assert embargo.impose(
        conn, WORLD, proposer_id="a", target_id="b", turn=2, date="2026-03-01")[0]
    assert pricing.seller_distress(conn, WORLD, "a", "c") == before, (
        "a government that stopped selling to one customer was priced as though somebody "
        "had blockaded it")


# ------------------------------------------- a defect only the whole stack can produce

def test_a_government_seeking_a_supplier_is_not_matched_with_the_power_embargoing_it():
    """`intent` can see four reasons a lane will not carry, and a standing embargo was not
    among them -- the one stoppage that is somebody's decision.

    So a power under an all-goods embargo went shopping, was matched with the very
    government refusing to sell to it, and the engine opened the route, priced it and
    announced it: *"B arranges oil from A at 0.26 a unit"* -- and in the same turn's report,
    *"a -> b (oil): embargoed"*. Nothing ever shipped.

    Neither module was wrong on its own, which is why nothing caught it: `intent` predates
    the embargo, and the embargo's check correctly answers before the market. It was
    reachable only with both up.

    **The lane is not opened, and the intention is reported unmet** -- "A will not sell to
    you" is exactly the sentence `IntentReport.unmet` exists to carry. The alternative
    supplier `c` exists in this fixture and is too small to cover the demand, so the
    intention has somewhere else to look and still does not reach for `a`.
    """
    conn = _world()
    _seller(conn, "a", produced=80.0, stockpile=400.0)
    _seller(conn, "c", produced=5.0, stockpile=20.0)
    _starved(conn, "b")
    # One lane in another good, so the embargo has something to be declared against.
    _route(conn, "a", "b", good="rare", volume=1.0)
    assess_trade(conn, WORLD, turn=1, date="2026-02-01", days=30)
    assert embargo.impose(
        conn, WORLD, proposer_id="a", target_id="b", turn=2, date="2026-03-01")[0]

    assert intent.propose(
        conn, WORLD, nation_id="b", kind="seek", good="oil", turn=2,
        date="2026-03-01")[0] is not None
    opened = intent.pursue(conn, WORLD, turn=3, date="2026-04-01")

    assert not any("from A" in line for line in opened.opened), opened.opened
    routes = [str(r[0]) for r in conn.execute(
        "SELECT seller_id FROM trade_route WHERE world_id = ? AND buyer_id = 'b'"
        "   AND good = 'oil'", (WORLD,))]
    assert "a" not in routes, routes
    # And the refusal says which door was closed, rather than reporting the market.
    if not opened.opened:
        assert any("embargo" in why for why in opened.unmet), opened.unmet


def test_the_embargoing_power_is_passed_over_even_when_it_is_the_only_seller():
    """The negative control for the case above: with no alternative at all, the intention
    comes back unmet rather than quietly reaching for the closed door.

    Worth its own test because the fix could have been written as "prefer somebody else",
    which would look identical in a fixture that *has* somebody else and would reopen the
    whole defect in the case that matters -- a small power with one supplier, which is the
    profile an embargo is aimed at in the first place.
    """
    conn = _world()
    _seller(conn, "a", produced=80.0, stockpile=400.0)
    _starved(conn, "b")
    _route(conn, "a", "b", good="rare", volume=1.0)
    assess_trade(conn, WORLD, turn=1, date="2026-02-01", days=30)
    assert embargo.impose(
        conn, WORLD, proposer_id="a", target_id="b", turn=2, date="2026-03-01")[0]
    assert intent.propose(
        conn, WORLD, nation_id="b", kind="seek", good="oil", turn=2,
        date="2026-03-01")[0] is not None

    opened = intent.pursue(conn, WORLD, turn=3, date="2026-04-01")

    assert not opened.opened, opened.opened
    assert opened.unmet, "the intention should be reported unmet, not silently dropped"
    assert any("embargo" in why for why in opened.unmet), opened.unmet


def test_a_lane_cannot_be_born_under_an_embargo_and_inflate_what_it_reports_costing():
    """**The expensive half of the same defect.**

    `revenue_forgone` is the mechanic's whole claim to being a decision rather than a free
    weapon -- "it is the cargo those routes would have moved, at the terms the route was
    standing on". A route opened *after* the embargo, by the target shopping into it, would
    never have moved anything: it exists only because the target went looking. The engine
    priced it, found it severed, and billed the proposer for the cargo it did not ship.

    Measured before the fix, over six turns with one embargo and one `seek`: the reported
    sacrifice went from 0.97 to 6.21, a **6.4x** overstatement, every point of it on a lane
    that had never carried a unit. The two open questions in `EXPORT_EMBARGO.md` are both
    about the column *understating* the cost; this was the same column overstating it, in
    the other direction, and it was not on that list.

    Now the two runs agree exactly -- the shopping trip cannot open the lane, so there is
    no phantom cargo to bill for.
    """
    def forgone(with_seek: bool) -> float:
        conn = _world()
        _seller(conn, "a", produced=80.0, stockpile=400.0)
        _seller(conn, "c", produced=5.0, stockpile=20.0)
        _starved(conn, "b")
        _route(conn, "a", "b", good="rare", volume=1.0)
        assess_trade(conn, WORLD, turn=1, date="2026-02-01", days=30)
        embargo.impose(conn, WORLD, proposer_id="a", target_id="b", turn=2,
                       date="2026-03-01")
        if with_seek:
            intent.propose(conn, WORLD, nation_id="b", kind="seek", good="oil", turn=2,
                           date="2026-03-01")
            intent.pursue(conn, WORLD, turn=3, date="2026-04-01")
        for turn in range(3, 9):
            assess_trade(conn, WORLD, turn=turn, date=f"2026-{turn + 1:02d}-01", days=30)
        out = float(conn.execute(
            "SELECT revenue_forgone FROM trade_embargo WHERE world_id = ?",
            (WORLD,)).fetchone()[0])
        conn.close()
        return out

    plain, shopped = forgone(False), forgone(True)
    assert shopped == plain, (plain, shopped)


# ------------------------------------------------- the fact every measurement rests on

def test_two_worlds_with_the_same_seed_and_different_names_are_different_worlds():
    """`atlas.sim.rng.derive` hashes the world id, so the id is part of the seed.

    Deliberate and documented -- "each call site derives its own independent stream from
    ``(seed, world, turn, call_key)``" -- and pinned here because of what it costs a
    *measurement*. Every paired-arm harness written on this branch named its world after the
    arm it was running, so the control and the treatment were never the same world; six
    turns of `gathering-storm` with pricing off in **both** arms and nothing different but
    the name put France on 10.99 or on 26.99. Any harness comparing two arms must hold the
    world id fixed and vary only the flags, and this is the line that says why.
    """
    a = derive(11, "world_one", 3, "battle:r1:a-vs-b").random()
    b = derive(11, "world_two", 3, "battle:r1:a-vs-b").random()
    assert a != b, "the world id no longer separates two streams; harnesses may rely on it"
    assert derive(11, "world_one", 3, "battle:r1:a-vs-b").random() == a
