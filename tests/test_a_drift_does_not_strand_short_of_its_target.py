"""A floor on a gap-closing rate is a dead zone, and it stranded manpower 10% short.

Every drift in :mod:`atlas.engine.budget` is ``gap * rate``: the economy climbs toward what
its industry can support, legitimacy drifts toward stability, manpower recovers toward what
the population sustains. So the drift **shrinks as the value approaches its target** -- and
a fixed floor on "the smallest drift worth a diff" eventually catches it for good. The value
stops ``threshold / rate`` short of where it is supposed to settle and stays there forever,
with nothing in the game saying why.

Measured, from an empty pool, 500 game-months at each span, ceiling 12.0:

===== ============== ==============
days  rests at 0.05  rests at 0.01
===== ============== ==============
30    10.78 (-1.22)  11.76 (-0.24)
90    10.79 (-1.21)  11.76 (-0.24)
365   10.49 (-1.51)  11.79 (-0.21)
===== ============== ==============

**This is not the span bug it was first read as.** The main loop already splits a long turn
into monthly calls, so the shortfall is roughly the same at every turn length -- it is a
property of the floor meeting a shrinking drift, and a yearly turn is only slightly worse.

0.01 is not a tuning choice: diff values here are ``round(v, 2)``, so a drift below 0.005
records as ``0.00``. A floor at the rounding grain suppresses exactly the diffs that would
write nothing, and no others -- which is why the anti-churn property it was added for
survives, and the last test here is that property.
"""

from __future__ import annotations

import math
import sqlite3

import pytest

from atlas.engine.budget import (
    _ECONOMY_FROM_DEVELOPMENT,
    _ECONOMY_PER_INDUSTRY,
    _NOTICEABLE,
    collect_revenue,
    ground_value,
)
from atlas.engine.pacing import span_of
from atlas.scenarios.sandbox import sandbox_seed
from atlas.state.bootstrap import create_world

WORLD = "sandbox"
#: Long enough that every span has genuinely stopped moving rather than still climbing.
_MONTHS = 500


def _ceiling(conn) -> float:
    pop = conn.execute(
        "SELECT COALESCE(SUM(population), 0) FROM region WHERE world_id = ?"
        "   AND COALESCE(controller_nation_id, owner_nation_id) = 'aurelia'", (WORLD,)
    ).fetchone()[0]
    return max(10.0, float(pop) * 2.0)


def _settle(days: int, *, manpower: float = 1.0) -> tuple[sqlite3.Connection, int]:
    """Run the same number of *game months* however long each turn is."""
    conn = create_world(":memory:", sandbox_seed())
    conn.execute("UPDATE nation SET manpower = ? WHERE id = 'aurelia'", (manpower,))
    conn.commit()
    turns = max(1, math.ceil(_MONTHS / span_of(days)))
    for turn in range(1, turns + 1):
        collect_revenue(conn, WORLD, turn=turn, date="1897-04-01", days=days)
    return conn, turns


def _manpower(conn) -> float:
    return float(conn.execute(
        "SELECT manpower FROM nation WHERE id = 'aurelia'"
    ).fetchone()["manpower"])


# ------------------------------------------------------------------------ the dead zone


#: What a settled pool may still be short of its ceiling, by turn length.
#:
#: **The monthly-and-longer figure is the threshold; the weekly one is the rounding.** A
#: diff value here is ``round(v, 2)``, so a per-turn drift under 0.005 records as ``0.00``
#: and the value stops moving whether or not the threshold let it through. On weekly turns
#: the per-turn drift is a quarter of a month's, so rounding binds first and the residual
#: is about twice the threshold's. Named here rather than hidden in a loose tolerance,
#: because it is a real limit of writing two-decimal history and not something this commit
#: fixed. Before the change every span was short by 1.2-1.5.
_SETTLES_WITHIN = {7: 0.6, 30: 0.3, 90: 0.3, 365: 0.3}


@pytest.mark.parametrize("days", [7, 30, 90, 365])
def test_manpower_settles_near_its_ceiling_rather_than_a_tenth_below_it(days):
    """**The repro.** At the old floor this rested 1.2-1.5 below the ceiling, forever --
    on a twelve-point pool, a tenth of a nation's manpower permanently missing."""
    conn, _turns = _settle(days)
    try:
        ceiling = _ceiling(conn)
        short = ceiling - _manpower(conn)
        assert _manpower(conn) <= ceiling + 1e-9
        assert short < _SETTLES_WITHIN[days], (days, short, ceiling)
        assert short < 1.2, "the old floor's shortfall must not still be reachable"
    finally:
        conn.close()


def test_the_shortfall_does_not_depend_on_the_turn_length():
    """The reframing, as an assertion.

    Read as a span bug this would improve with shorter turns and collapse at a year. It
    does neither: the three spans a month and over agree to within 0.03, because the main
    loop already splits a long turn into monthly calls. Weekly is excluded and has its own
    reason, recorded on `_SETTLES_WITHIN`.
    """
    settled = {}
    for days in (30, 90, 365):
        conn, _turns = _settle(days)
        try:
            settled[days] = _ceiling(conn) - _manpower(conn)
        finally:
            conn.close()
    assert max(settled.values()) - min(settled.values()) < 0.05, settled


def test_the_economy_settles_at_its_target_too():
    """The dead zone was generic, so the fix has to be: economy closes a gap by the same
    arithmetic and was stranded by the same floor."""
    conn, _turns = _settle(30)
    try:
        row = conn.execute(
            "SELECT economy, industry FROM nation WHERE id = 'aurelia'"
        ).fetchone()
        target = (
            float(row["industry"]) * _ECONOMY_PER_INDUSTRY
            + ground_value(conn, WORLD, "aurelia") * _ECONOMY_FROM_DEVELOPMENT
        )
        assert abs(target - float(row["economy"])) < 0.3, (target, row["economy"])
    finally:
        conn.close()


# ------------------------------------------------------- and the property it must not lose


def test_the_threshold_is_the_grain_the_column_can_record():
    """Why 0.01 rather than a number chosen to make the tests above pass.

    Diff values here are ``round(v, 2)``. A floor at the rounding grain suppresses exactly
    the drifts that would record as ``0.00`` and no others, so "too small to be worth a
    diff" and "too small to be a diff at all" are the same statement.
    """
    assert round(_NOTICEABLE, 2) == pytest.approx(0.01), (
        "a threshold finer than the rounding grain suppresses nothing; a coarser one "
        "suppresses drifts the column could have recorded"
    )
    assert round(_NOTICEABLE / 10.0, 2) == 0.0


def test_a_world_at_equilibrium_pays_no_diffs():
    """The reason the floor exists, which lowering it must not give away: a settled world
    must not write a churn diff every turn forever."""
    conn, turns = _settle(30)
    try:
        before = conn.execute("SELECT COUNT(*) FROM diff").fetchone()[0]
        for turn in range(turns + 1, turns + 13):
            collect_revenue(conn, WORLD, turn=turn, date="1897-04-01", days=30)
        after = conn.execute("SELECT COUNT(*) FROM diff").fetchone()[0]
        # Treasury still moves every turn -- revenue is a flow, not a drift -- so the
        # claim is about the *drifts*: no manpower, economy or legitimacy diff at rest.
        drifts = conn.execute(
            "SELECT COUNT(*) FROM diff d JOIN diff_batch b ON b.id = d.batch_id"
            " WHERE b.turn > ? AND d.target_table = 'nation'"
            "   AND d.field IN ('manpower', 'economy', 'legitimacy')", (turns,)
        ).fetchone()[0]
        assert after > before, "this test is worthless if nothing was written at all"
        assert drifts == 0
    finally:
        conn.close()
