"""A transfer moves goods. It does not make any, it does not burn any, and it is not too
small to be worth recording.

**This is the goods-side of the settlement promise, and it is a narrower promise than the
money one on purpose.** Money is conserved *globally*: trade moves it between treasuries and
the batch nets to zero, which `test_a_settlement_creates_no_money` asserts with no tolerance
at all. Goods are not, and must not be. A world's stockpiles legitimately grow when mines
produce and shrink when industry burns, and a test that demanded the total hold still would
be pinning the economy shut. What has to hold is the narrower thing:

* **a transfer** moves goods from one nation to another without creating or destroying any;
* and **the record of it survives being written down**, however small it is.

The second is where the defect was. Step 3 wrote each nation-good's new stockpile only when
it had moved by at least a hundredth, and the write is a ``SET`` of the whole figure -- so a
round whose net came to less than that had its production, its consumption and its cargo
discarded together, and the next round began again from the old number. For a nation-good
that moves slowly the change is under the threshold *every* round, so it is dropped every
round and the stockpile never moves at all.

Measured on the shipped cold-war map before the fix: **Italy's oil and rare sat at exactly
7.92 for twelve turns** while the engine computed ``-0.004`` and discarded it twelve times
running, and **11 of 441 nation-goods were frozen for the entire game** -- 2.5% to 2.7% of
them in every era tried, deterministically, in every world.
"""

from __future__ import annotations

import pytest

from atlas.engine import consent, pricing, refusal
from atlas.engine.trade import assess_trade, demand_of
from atlas.state.bootstrap import NationSeed, RegionSeed, WorldSeed, create_world
from atlas.state.models import Difficulty

WORLD = "w_cargo"


@pytest.fixture(autouse=True)
def _barter():
    """Pricing off, and both vetoes with it.

    This file is about the *goods*, and a price only decides whether a cargo moves at all --
    which is a question `test_a_cargo_has_a_price` and `test_a_seller_can_say_no` already
    own. With the layer down the lane ships what the volume says, every round, which is what
    makes a frozen stockpile unambiguous rather than a lane that happened to stop.
    """
    was = pricing.set_enabled(False)
    was_refusal = refusal.set_enabled(False)
    was_consent = consent.set_enabled(False)
    yield
    pricing.set_enabled(was)
    refusal.set_enabled(was_refusal)
    consent.set_enabled(was_consent)


def _world(names, treasury: float = 100.0):
    return create_world(":memory:", WorldSeed(
        id=WORLD, name="Cargo", start_date="2026-01-01", rng_seed=5,
        difficulty=Difficulty.NORMAL,
        nations=tuple(
            NationSeed(id=n, name=n.upper(), colour="#111", capital_region_id=f"r_{n}",
                       economy=50.0, industry=30.0, military_strength=20.0,
                       treasury=treasury)
            for n in names),
        regions=tuple(
            [RegionSeed(id=f"r_{n}", name=n.upper(), geo_key=f"g:{n}", owner_nation_id=n,
                        development=5.0) for n in names]
            + [RegionSeed(id="s_mid", name="The Middle Sea", geo_key="g:s", kind="ocean")]),
        adjacency=tuple((f"r_{n}", "s_mid", "sea") for n in names),
    ))


def _endow(conn, nation: str, good: str, produced: float, stockpile: float) -> None:
    conn.execute(
        "UPDATE resource SET produced = ?, stockpile = ?"
        " WHERE world_id = ? AND nation_id = ? AND good = ?",
        (produced, stockpile, WORLD, nation, good))


def _route(conn, seller: str, buyer: str, good: str, volume: float) -> None:
    conn.execute(
        "INSERT INTO trade_route (id, world_id, seller_id, buyer_id, good, volume,"
        "                         opened_turn)"
        " VALUES (?, ?, ?, ?, ?, ?, 0)",
        (f"tr:{good}:{seller}:{buyer}", WORLD, seller, buyer, good, volume))


def _stock(conn, nation: str, good: str = "oil") -> float:
    return float(conn.execute(
        "SELECT stockpile FROM resource WHERE world_id = ? AND nation_id = ? AND good = ?",
        (WORLD, nation, good)).fetchone()[0])


# ------------------------------------------------------------------- the transfer itself

def test_a_transfer_moves_goods_without_creating_or_destroying_any():
    """What one nation loses, the other gains -- exactly.

    Consumption is in the way of stating that directly, because both parties burn their
    industry's demand in the same round and neither can be switched off (``_MIN_DEMAND``
    puts a floor under it). So the assertion is made where the cargo actually shows: the two
    nations' stockpile changes, added together, come to **the demand they burned and nothing
    else**. Every unit that left the seller arrived at the buyer.

    Both are given deep stockpiles so that neither the starvation floor nor the ceiling
    fires -- those two clamps *do* legitimately destroy and create goods, and this test is
    not about them.
    """
    conn = _world(["a", "b"])
    for n in ("a", "b"):
        for good in ("oil", "steel", "rare"):
            _endow(conn, n, good, 0.0, 20.0)
    _route(conn, "a", "b", "oil", 1.5)
    conn.commit()
    before_a, before_b = _stock(conn, "a"), _stock(conn, "b")
    burn = demand_of(conn, "a") + demand_of(conn, "b")

    assess_trade(conn, WORLD, turn=1, date="2026-02-01", days=30)

    after_a, after_b = _stock(conn, "a"), _stock(conn, "b")
    moved_out = before_a - after_a - demand_of(conn, "a")
    moved_in = after_b - before_b + demand_of(conn, "b")
    # **The buyer's stockpile still falls, and that is not a failure.** Its industry burns
    # 3.3 a round against a 1.5 cargo, so it ends the round down 1.8 -- the lane slows the
    # decline rather than reversing it. Asserting "the buyer gained" would be asserting a
    # property of the fixture's arithmetic, not of the transfer.
    assert moved_out > 0.0, "no cargo left the seller"
    assert moved_out == pytest.approx(moved_in, abs=0.001), (
        f"the seller gave up {moved_out:.4f} and the buyer received {moved_in:.4f}")
    assert (after_a + after_b) - (before_a + before_b) == pytest.approx(-burn, abs=0.001), (
        "the pair's stockpiles moved by something other than what they burned, so the "
        "transfer created or destroyed goods")


def test_the_world_total_is_not_conserved_and_must_not_be():
    """**The promise this file does not make**, written down so nobody adds it later.

    Money is conserved globally because trade is the only thing that moves it. Goods are
    not: a mine produces and a factory burns, and both change the world's total on purpose.
    A test asserting the total holds still would pin the economy shut, and it would pass on
    a build where production had stopped working -- which is the failure it would be
    hiding.
    """
    conn = _world(["a"])
    for good in ("oil", "steel", "rare"):
        _endow(conn, "a", good, 9.0, 5.0)
    conn.commit()
    before = _stock(conn, "a")

    assess_trade(conn, WORLD, turn=1, date="2026-02-01", days=30)

    assert _stock(conn, "a") > before, (
        "a nation that produces more than it burns did not gain -- production is not "
        "reaching the stockpile at all")


# ------------------------------------------------------- and it survives being written

def test_a_slow_stockpile_is_not_frozen_by_the_threshold():
    """**The freeze, which is what the hundredth threshold actually did.**

    The buyer produces exactly what it burns, so the only thing moving its stockpile is the
    cargo -- and the cargo is deliberately worth less than a hundredth a round. On the
    unfixed engine the write was dropped in *every* round, so the stockpile was a flat line
    for the whole game however long it ran. Twelve rounds of a lane that delivers 0.005 a
    round must leave the buyer 0.06 better off, not level.
    """
    conn = _world(["a", "b"])
    burn = None
    for n in ("a", "b"):
        for good in ("oil", "steel", "rare"):
            _endow(conn, n, good, 0.0, 20.0)
    # The buyer's own production exactly covers its own demand, so its net movement each
    # round *is* the cargo and nothing else.
    burn = demand_of(conn, "b")
    _endow(conn, "b", "oil", burn, 20.0)
    _endow(conn, "a", "oil", 0.0, 60.0)
    _route(conn, "a", "b", "oil", 0.005)
    conn.commit()
    start = _stock(conn, "b")

    for turn in range(1, 13):
        assess_trade(conn, WORLD, turn=turn, date="2026-02-01", days=30)

    gained = _stock(conn, "b") - start
    assert gained > 0.0, (
        "twelve rounds of cargo left the buyer's stockpile exactly where it started -- "
        "the write threshold froze it")
    assert gained == pytest.approx(0.06, abs=0.005), (
        f"twelve rounds of 0.005 delivered {gained:.4f}, not 0.06")


def test_a_stockpile_that_did_not_move_is_not_rewritten():
    """The other half of the gate: a write only happens when the figure changes.

    Removing a threshold is only correct if what replaces it is "did this move", not
    "write it every time". A nation with nothing produced, nothing traded and its industry
    burning it flat sits at zero, and a round that changes nothing must add nothing to the
    batch -- otherwise the rewind log fills with diffs that say a number stayed the same.
    """
    conn = _world(["a"])
    for good in ("oil", "steel", "rare"):
        _endow(conn, "a", good, 0.0, 0.0)
    conn.commit()

    assess_trade(conn, WORLD, turn=1, date="2026-02-01", days=30)
    assert _stock(conn, "a") == 0.0

    rows_before = conn.execute("SELECT COUNT(*) FROM diff").fetchone()[0]
    assess_trade(conn, WORLD, turn=2, date="2026-03-01", days=30)
    rows_after = conn.execute("SELECT COUNT(*) FROM diff").fetchone()[0]
    assert rows_after == rows_before, (
        f"{rows_after - rows_before} diffs were written for a world where nothing moved")
