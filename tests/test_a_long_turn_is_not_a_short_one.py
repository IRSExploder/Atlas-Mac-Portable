"""Two quantities in the trade layer counted turns, and a turn is not a duration.

:mod:`atlas.engine.pacing` exists because the player chooses how much time a turn covers --
a week, a month, a season or a year -- and for a long time nothing in the simulation knew.
Every flow in :func:`atlas.engine.trade.assess_trade` was brought under that discipline;
two things were not, and both are on the pricing layer:

* **how often a route's terms are reconsidered** was six *turns*, which is six months on the
  monthly game it was calibrated on, six weeks on a weekly one and **six years** on a yearly
  one. The constant's own docstring says it exists so that "the 1936 oil price" does not
  still stand "in 1945" -- and on a yearly game it produced exactly that;
* **what a buyer may spend on imports** was half its treasury per *call*, weighed against a
  cargo that was correctly scaled to the round. A weekly turn therefore bought a quarter of
  a month's cargo out of a full month's allowance, and the cap could not bind.

Neither has moved a recorded number, because both are the identity at the 30-day turn every
figure on this branch was measured at -- and the first test here is the one that says so.
That is the whole reason to fix them now: they are harmless only while
:data:`~atlas.engine.pricing.BASE_PRICE` keeps the cap off its binding edge, and a later
calibration that puts it there would find a layer whose affordability rule depended on a
pacing choice the player makes for pacing reasons.

**Equivalence is asked the way :mod:`atlas.engine.pacing` defines it**: applying a quantity
once over N months has to land where applying it N times over one month does. For the
repricing clock that is a count of renegotiations per year of world time, exact to the
granularity of a turn; for the import budget -- a fraction of what a country is holding, and
therefore a *rate*, which compounds -- it is exact outright, and is asked of an undisturbed
treasury so that nothing but the rule under test can move it.
"""

from __future__ import annotations

import pytest

from atlas.engine import pricing, refusal
from atlas.engine.pacing import compounded, span_of
from atlas.engine.trade import (
    IMPORT_BUDGET_SHARE,
    REPRICE_EVERY_MONTHS,
    _reprice_period,
    assess_trade,
)
from atlas.state.bootstrap import NationSeed, RegionSeed, WorldSeed, create_world
from atlas.state.models import Difficulty

WORLD = "w_span"

#: The four turn lengths the game offers. The month is the one every constant was tuned
#: against, and the one both fixes are the identity at.
WEEK, MONTH, SEASON, YEAR = 7, 30, 90, 360


@pytest.fixture(autouse=True)
def _pricing_on():
    """Pricing up and refusal down, for the reason `test_a_cargo_has_a_price` gives: a
    seller walking away would end a shipment for a reason that is not the one under test."""
    was = pricing.set_enabled(True)
    was_refusal = refusal.set_enabled(False)
    yield
    pricing.set_enabled(was)
    refusal.set_enabled(was_refusal)


def _world(treasury: float = 100.0):
    return create_world(":memory:", WorldSeed(
        id=WORLD, name="Span", start_date="2026-01-01", rng_seed=11,
        difficulty=Difficulty.NORMAL,
        nations=tuple(
            NationSeed(id=n, name=n.upper(), colour="#111", capital_region_id=f"r_{n}",
                       economy=50.0, industry=30.0, military_strength=20.0,
                       treasury=treasury)
            for n in ("a", "b")
        ),
        regions=tuple(
            [RegionSeed(id=f"r_{n}", name=n.upper(), geo_key=f"g:{n}", owner_nation_id=n,
                        development=5.0) for n in ("a", "b")]
            + [RegionSeed(id="s_mid", name="The Middle Sea", geo_key="g:s", kind="ocean")]
        ),
        adjacency=tuple((f"r_{n}", "s_mid", "sea") for n in ("a", "b")),
    ))


def _endow(conn, nation: str, good: str, produced: float, stockpile: float) -> None:
    conn.execute(
        "UPDATE resource SET produced = ?, stockpile = ?"
        " WHERE world_id = ? AND nation_id = ? AND good = ?",
        (produced, stockpile, WORLD, nation, good))
    conn.commit()


ROUTE = "tr:oil:a:b"


def _capped_pair(treasury: float = 40.0):
    """A seller with far more oil than the buyer could ever pay for.

    The volume is set past affordability on purpose: with the cargo effectively unlimited,
    the only thing deciding what arrives is the import budget, so what these tests measure
    is the cap and not the seller's warehouse.
    """
    conn = _world()
    _endow(conn, "a", "oil", 400.0, 5000.0)
    _endow(conn, "b", "oil", 0.0, 0.0)
    conn.execute(
        "INSERT INTO trade_route (id, world_id, seller_id, buyer_id, good, volume,"
        "                         opened_turn) VALUES (?, ?, 'a', 'b', 'oil', 500.0, 0)",
        (ROUTE, WORLD))
    conn.execute("UPDATE nation SET treasury = ? WHERE id = 'b'", (treasury,))
    conn.commit()
    return conn


def _treasury(conn, nation: str) -> float:
    return float(conn.execute(
        "SELECT treasury FROM nation WHERE world_id = ? AND id = ?",
        (WORLD, nation)).fetchone()[0])


def _priced_turn(conn) -> int:
    return int(conn.execute(
        "SELECT priced_turn FROM trade_route WHERE id = ?", (ROUTE,)).fetchone()[0])


def _top_up(conn, treasury: float = 40.0) -> None:
    """What `collect_revenue` does at the head of every round, standing in for it."""
    conn.execute("UPDATE nation SET treasury = ? WHERE id = 'b'", (treasury,))
    conn.commit()


# ------------------------------------------------------- the monthly game does not move

def test_the_monthly_turn_is_untouched_by_both_fixes():
    """**The regression pin, first, because it is the one that protects existing work.**

    Every measured figure on this branch -- the calibration of `BASE_PRICE`, the long-game
    validation, the concentration costing -- was taken at 30-day turns. Both fixes are
    written as multiplications by ``span``, which is 1.0 there, so the monthly game has to
    come out unchanged. If it does not, every recorded number on the branch is stale.
    """
    assert _reprice_period(span_of(MONTH)) == 6, (
        "the monthly repricing cadence is not the six turns it has always been")

    conn = _capped_pair()
    assess_trade(conn, WORLD, turn=1, date="2026-02-01", days=MONTH)
    spent = 40.0 - _treasury(conn, "b")
    assert spent == pytest.approx(40.0 * IMPORT_BUDGET_SHARE, abs=0.05), spent


def test_a_turn_that_is_also_the_whole_round_needs_no_second_length():
    """``turn_days`` defaults to ``days``, which is the truth for every caller that does not
    split a jump into rounds -- a test, a harness, or any monthly game.

    Worth pinning rather than assuming: the default is what keeps three dozen existing call
    sites correct without one of them being edited.
    """
    conn = _capped_pair()
    explicit = assess_trade(conn, WORLD, turn=1, date="2026-02-01", days=MONTH,
                            turn_days=MONTH)
    other = _capped_pair()
    implied = assess_trade(other, WORLD, turn=1, date="2026-02-01", days=MONTH)

    assert len(explicit.repriced) == 1
    assert len(implied.repriced) == len(explicit.repriced)
    assert [_treasury(conn, n) for n in ("a", "b")] == [
        _treasury(other, n) for n in ("a", "b")]


# ---------------------------------------------------- 1. the repricing clock, in months

@pytest.mark.parametrize(("days", "period"), [
    (WEEK, 26),      # 26 x 7  = 182 days
    (MONTH, 6),      #  6 x 30 = 180 days
    (SEASON, 2),     #  2 x 90 = 180 days
    (YEAR, 1),       #  1 x 360 -- the floor, see the next test
])
def test_the_repricing_period_is_six_months_of_world_time(days, period):
    """Six months, whatever the player set the turn length to.

    The old constant was six *turns*: 42 days of world time on a weekly game and 2,190 on a
    yearly one, a fifty-two-fold spread on a choice made for pacing reasons.
    """
    assert _reprice_period(span_of(days)) == period


def test_a_yearly_turn_reprices_as_often_as_a_turn_allows():
    """**The one length that cannot hit the target, and the direction it misses in.**

    Terms are reconsidered at most once per turn by construction -- `priced_turn != turn` is
    what stops a twelve-round jump walking the same seas twelve times -- so a 360-day turn
    can do no better than once a year against a six-month target. Once a year is the floor,
    and it is the right direction to fail in: what the old code actually shipped there was
    once every six years.
    """
    assert _reprice_period(span_of(YEAR)) == 1
    # And nothing can drive it below one, including lengths nobody should pass.
    assert _reprice_period(0.0) == 1
    assert _reprice_period(-1.0) == 1
    assert _reprice_period(span_of(10_000)) == 1


@pytest.mark.parametrize("days", [WEEK, MONTH, SEASON])
def test_the_same_route_is_reconsidered_at_the_same_rate_in_any_length_of_turn(days):
    """The clock, asked of the engine rather than of the arithmetic.

    Two years of world time, spent in turns of three different lengths, and the route's
    terms come round the same four times in each. On the old constant this was four at a
    month, **fifty-two** at a week and **one** at a season.
    """
    conn = _capped_pair(treasury=1_000_000.0)
    turns = round(720 / days)
    considered = 0
    for turn in range(1, turns + 1):
        # Held level, so affordability never decides an outcome this test is not about.
        _top_up(conn, 1_000_000.0)
        assess_trade(conn, WORLD, turn=turn, date="2026-02-01", days=days)
        if _priced_turn(conn) == turn:
            considered += 1
    expected = (720 / 30.0) / REPRICE_EVERY_MONTHS
    assert considered == pytest.approx(expected, abs=1), (
        f"{days}-day turns reconsidered the terms {considered} times in two years, "
        f"not the {expected:.0f} a six-month cadence asks for")


def test_a_yearly_game_no_longer_leaves_a_price_standing_for_six_years():
    """The old failure, stated as the world it produced.

    Six turns of 360 days is 2,190 days between repricings. A price settled in the first
    year of a game stood untouched into the seventh -- through a war, a blockade and a
    collapse in relations -- which is the exact thing
    :data:`~atlas.engine.trade.REPRICE_EVERY_MONTHS` exists to prevent.
    """
    conn = _capped_pair(treasury=1_000_000.0)
    considered = []
    for turn in range(1, 7):
        _top_up(conn, 1_000_000.0)
        assess_trade(conn, WORLD, turn=turn, date="2026-02-01", days=YEAR, turn_days=YEAR)
        if _priced_turn(conn) == turn:
            considered.append(turn)
    assert considered == [1, 2, 3, 4, 5, 6], (
        "six years of a yearly game and the terms were looked at on " + str(considered))


def test_the_rounds_of_one_long_turn_still_reprice_only_once():
    """The guard the shorter period must not have broken.

    A yearly jump is twelve rounds at one turn number, and the period for a yearly turn is
    now 1 -- so every round is nominally due. `priced_turn != turn` is the only thing left
    holding that to one sea walk instead of twelve, and it is worth its own test now that
    the modulo can no longer be relied on to filter anything out.
    """
    conn = _capped_pair(treasury=1_000_000.0)
    first = assess_trade(conn, WORLD, turn=1, date="2026-02-01", days=YEAR / 12,
                         turn_days=YEAR)
    assert len(first.repriced) == 1
    for _round in range(11):
        again = assess_trade(conn, WORLD, turn=1, date="2026-02-01", days=YEAR / 12,
                             turn_days=YEAR)
        assert again.repriced == [], "a round of a long turn repriced a settled route"


# ------------------------------------------------------- 2. the import budget, per month

@pytest.mark.parametrize("days", [WEEK, MONTH, SEASON, YEAR])
def test_a_buyer_spends_half_a_treasury_for_each_month_the_call_covers(days):
    """The cap is a per-month rate now, and the cargo it is weighed against always was one.

    Before this it was a flat half-treasury however much time the call covered, so the same
    allowance bought a week of cargo or a year of it.
    """
    conn = _capped_pair()
    assess_trade(conn, WORLD, turn=1, date="2026-02-01", days=days)
    spent = 40.0 - _treasury(conn, "b")
    want = 40.0 * compounded(IMPORT_BUDGET_SHARE, span_of(days))
    assert spent == pytest.approx(want, abs=0.05), (
        f"a {days}-day call spent {spent:.2f}, not the {want:.2f} its span is worth")


@pytest.mark.parametrize(("short_days", "count"), [
    (WEEK, 4),        # four weekly calls against one 28-day call
    (MONTH, 3),       # three monthly calls against one season
    (MONTH, 12),      # twelve monthly calls against one year
    (SEASON, 4),      # and the season against the year it makes up
])
def test_the_same_world_time_spends_the_same_money_however_it_is_divided(short_days, count):
    """**The equivalence itself, and it is exact rather than approximate.**

    :mod:`atlas.engine.pacing` defines a rate's correctness as "applying it once over N
    months lands where applying it N times over one month does", and a fraction of a
    treasury is a rate. So the *same* purse, undisturbed, must end at the same figure
    whether the world time is spent in one long call or several short ones.

    On the old rule the divided run spent ``count`` half-treasuries against the long run's
    one: 94% of the purse over four weeks where the undivided call took 50%.
    """
    divided = _capped_pair()
    for turn in range(1, count + 1):
        assess_trade(divided, WORLD, turn=turn, date="2026-02-01", days=short_days)

    whole = _capped_pair()
    assess_trade(whole, WORLD, turn=1, date="2026-02-01", days=short_days * count)

    left_divided, left_whole = _treasury(divided, "b"), _treasury(whole, "b")
    assert left_divided == pytest.approx(left_whole, abs=0.05), (
        f"{count} calls of {short_days}d left {left_divided:.3f} where one call of "
        f"{short_days * count}d left {left_whole:.3f}")
    assert _treasury(divided, "a") == pytest.approx(_treasury(whole, "a"), abs=0.05)


def test_the_cap_cannot_take_a_treasury_below_nothing_however_long_the_call():
    """**The bug the first draft of this fix shipped, and the reason the rule compounds.**

    A flat ``share * span`` reads as the obvious generalisation and is not one: at 360 days
    it grants six times a treasury, and a 40-point purse settled at *minus 200*. A share of
    what a country is holding cannot exceed what it is holding, which
    :func:`~atlas.engine.pacing.compounded` guarantees and multiplication does not.
    """
    for days in (MONTH, SEASON, YEAR, 3600):
        conn = _capped_pair(treasury=40.0)
        assess_trade(conn, WORLD, turn=1, date="2026-02-01", days=days)
        left = _treasury(conn, "b")
        assert left >= 0.0, f"a {days}-day call put the buyer at {left:.2f}"
        assert _treasury(conn, "a") + left == pytest.approx(140.0, abs=0.05), (
            "money was created or destroyed settling a long turn")


def test_nothing_is_spent_at_all_with_the_flag_down():
    """The additive promise, restated for the two fixed quantities: with pricing off there
    is no budget to scale and no clock to run, and a turn of any length barters."""
    was = pricing.set_enabled(False)
    try:
        conn = _capped_pair()
        for days in (WEEK, MONTH, SEASON, YEAR):
            before = (_treasury(conn, "a"), _treasury(conn, "b"))
            report = assess_trade(conn, WORLD, turn=1, date="2026-02-01", days=days)
            assert (_treasury(conn, "a"), _treasury(conn, "b")) == before
            assert report.repriced == []
    finally:
        pricing.set_enabled(was)
