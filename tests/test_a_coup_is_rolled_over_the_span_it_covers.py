"""A week of instability was charged as a month, and two and a half months as two.

``assess_successions`` drew the coup once per **engine round**, and
:func:`atlas.engine.pacing.rounds` answers a different question: how many discrete months
of fighting to grind out. It rounds the span to a whole number, and the fractional
remainder was simply lost.

Against a government with a 30% annual risk:

===== ====== ========== ========== =========
days  span   old chance new chance error
===== ====== ========== ========== =========
7     0.233  0.0293     0.0069     **+325%**
75    2.500  0.0577     0.0716     -19.4%
365   12.167 0.3000     0.3035     -1.2%
===== ====== ========== ========== =========

So long turns let a shaky government outrun its own instability -- the pacing bug the
module exists to prevent -- and **short turns charged a week four months of risk**, which
is the larger error and the one a player on weekly turns would actually have felt.

:func:`atlas.engine.pacing.compounded` is the exact statement of "this monthly chance, for
this many months", correct for a fractional span in both directions. One draw against it is
distributionally identical to N monthly draws and does not need N to be whole.
"""

from __future__ import annotations

import sqlite3

import pytest

from atlas.engine.leaders import assess_successions, coup_risk
from atlas.engine.pacing import compounded, span_of
from atlas.scenarios.sandbox import sandbox_seed
from atlas.state.bootstrap import create_world

WORLD = "sandbox"
#: Enough draws to tell a 0.7% chance from a 2.9% one without the test being flaky.
_TRIALS = 900


def _shaky(conn) -> None:
    """A government nobody would bet on: bad enough that coups are common."""
    conn.execute(
        "UPDATE nation SET stability = 5.0, legitimacy = 5.0 WHERE world_id = ?", (WORLD,)
    )
    conn.commit()


def _rate(days: int) -> float:
    """The share of trials in which at least one government fell, at this turn length."""
    conn = create_world(":memory:", sandbox_seed())
    try:
        fell = 0
        for seed in range(_TRIALS):
            _shaky(conn)
            report = assess_successions(
                conn, WORLD, turn=1, date="1897-04-01", seed=seed, days=days
            )
            if report.fell:
                fell += 1
        return fell / _TRIALS
    finally:
        conn.close()


def _expected(conn, days: int) -> float:
    """What one nation's chance should be over this span, from the engine's own risk."""
    risk = coup_risk(conn, "aurelia", stability=5.0, legitimacy=5.0)
    monthly = 1.0 - (1.0 - risk) ** (1.0 / 12.0)
    return compounded(monthly, span_of(days))


def test_the_government_really_is_shaky(conn: sqlite3.Connection):
    """The premise: a risk of zero would make every measurement below trivially equal."""
    _shaky(conn)
    assert coup_risk(conn, "aurelia", stability=5.0, legitimacy=5.0) > 0.1


def test_a_week_is_charged_a_week_of_risk(conn: sqlite3.Connection):
    """**The bigger half of the repro.** A seven-day turn used to draw a whole month."""
    _shaky(conn)
    weekly, monthly = _rate(7), _rate(30)
    assert weekly < monthly / 2, (weekly, monthly)
    assert weekly == pytest.approx(3 * _expected(conn, 7), abs=0.035), weekly


def test_two_and_a_half_months_is_charged_two_and_a_half(conn: sqlite3.Connection):
    """The half the scout found: `rounds(2.5)` is 2, and the remaining fortnight was free."""
    _shaky(conn)
    at_75 = _rate(75)
    assert _rate(60) < at_75 < _rate(90), at_75


def test_the_risk_rises_with_the_length_of_the_turn(conn: sqlite3.Connection):
    """The property the whole pacing model exists for, stated over the four spans a
    player can actually choose."""
    rates = [_rate(days) for days in (7, 30, 90, 365)]
    # Strictly, not merely non-decreasing: a week and a month drew the *same* number of
    # times before this, so a non-strict check passed on the bug.
    assert all(a < b for a, b in zip(rates, rates[1:], strict=False)), rates
    assert rates[0] < rates[-1] / 4, rates


def test_a_year_of_monthly_turns_and_one_yearly_turn_agree(conn: sqlite3.Connection):
    """A rate compounded over twelve one-month spans is a rate compounded over twelve
    months. If the two disagree, turn length is a strategy rather than a pacing choice."""
    _shaky(conn)
    monthly_over_a_year = 1.0 - (1.0 - _expected(conn, 30)) ** 12
    assert _expected(conn, 360) == pytest.approx(monthly_over_a_year, abs=1e-9)
