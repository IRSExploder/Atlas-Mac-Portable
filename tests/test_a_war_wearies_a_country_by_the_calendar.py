"""The same war over the same year wearies a country by the same amount, at every cadence.

**The bug.** `wareffort._charge` scaled each of its four terms correctly on its own -- the
occupation and time drifts through `pacing.compounded`, the shortage as a flow, and both
comments say they were fixed for exactly this reason -- and the *sum* of them still kept the
player's clock. Three of the four close on a ceiling, so what each charges depends on how
much room is left below it, and all four read the same ``standing``. Applied once over
twelve months instead of twelve times over one, none of them sees the room the other two
have just taken.

**Measured, with the fighting held identical.** One year of gathering-storm off a single
pre-intervention fingerprint, the same standing offensive planted in every arm:

| | week | month | season | year | year/month |
|---|---|---|---|---|---|
| casualties (war ledger) | 10.89 | 11.92 | 13.74 | 11.94 | 1.00x |
| unit strength remaining | 1,984.8 | 1,983.4 | 1,982.4 | 1,984.5 | 1.00x |
| **war exhaustion** | 40.30 | 43.27 | 56.80 | 61.39 | **1.42x** |

The same men died and the same army was left standing; the country was charged 42% more
weariness for it because the year was played in one turn rather than twelve, and 1.60x in
the worst cell. Everything downstream reads exhaustion -- the negotiating position in
`leverage`, what a power will settle for in `peace`, whether it takes a truce in
`armistice` -- so a player choosing a pace was choosing how soon the world's wars ended.

**The fix is composition, not a scaling.** No term wanted a different *form*: the drifts are
rates and stay compounded, the shortage is a flow and stays linear, the dying is neither and
is still a proportional bite. `_charge` now walks the turn a day at a time and applies the
month's formula to each day, so the four terms are blind to each other for a day rather than
for a year -- and fifty-two weekly turns, twelve monthly, four seasonal and one yearly are
all three hundred and sixty steps of the same size.

The claim this file makes is the one a player would state: **a year of war costs a year of
war, however the year is sliced.**
"""

from __future__ import annotations

import sqlite3

import pytest

from atlas.engine.pacing import compounded, span_of
from atlas.engine.wareffort import (
    OCCUPIED_CEILING,
    TIME_CEILING,
    TIME_DRIFT,
    _charge,
    assess_war_effort,
    effort_between,
)
from atlas.scenarios.sandbox import sandbox_seed
from atlas.state.bootstrap import create_world

WORLD = "sandbox"
A, B = "aurelia", "karthos"

#: **Twelve reference months, sliced four ways, in whole days of world time.** The counts
#: are chosen so every cadence covers *exactly* the same elapsed span -- and a week is seven
#: real days here rather than a fifty-second of a year, because that is what the engine is
#: actually asked for and the three-day remainder at the end of a weekly year is part of the
#: question. `51 * 7 + 3 == 12 * 30 == 4 * 90 == 1 * 360`.
_YEAR_DAYS = 360
_CADENCES: tuple[tuple[str, tuple[int, ...]], ...] = (
    ("week", (7,) * 51 + (3,)),
    ("month", (30,) * 12),
    ("season", (90,) * 4),
    ("year", (360,)),
)


class _Nation(dict):
    """The two columns `_charge` reads, and nothing else."""

    def __init__(self, *, treasury: float = 100.0, stability: float = 100.0) -> None:
        super().__init__(treasury=treasury, stability=stability)


def _year_of_war(
    schedule: tuple[int, ...], *, casualties: float = 0.0, occupied: int = 0,
    homeland: int = 12, army: float = 1000.0, stability: float = 100.0,
    treasury: float = 100.0,
) -> float:
    """The exhaustion a year of one war reaches, played in turns of ``schedule``.

    **The war is held identical and only the cadence moves.** Casualties accrue at a
    constant rate per elapsed *day*, and the army falls by exactly what it lost -- `army` is
    the figure `assess_war_effort` reads, which is taken after the fighting. Holding the
    army flat while the dead pile up would be a world that cannot exist, and would put a
    turn's length back into the casualty denominator through the back door.
    """
    standing, left = 0.0, float(army)
    assert sum(schedule) == _YEAR_DAYS, schedule
    for days in schedule:
        fresh = casualties * days / _YEAR_DAYS
        left -= fresh
        standing += _charge(
            _Nation(treasury=treasury, stability=stability), fresh=fresh, army=left,
            occupied=occupied, homeland=homeland, standing=standing,
            span=span_of(days))
        standing = max(0.0, min(100.0, standing))
    return standing


def _at_every_cadence(**war) -> dict[str, float]:
    return {label: _year_of_war(schedule, **war) for label, schedule in _CADENCES}


# ------------------------------------------------------------------------- the premise


def test_the_four_cadences_cover_the_same_year():
    """Worthless otherwise: four arms of different lengths would differ for a dull reason."""
    for label, schedule in _CADENCES:
        assert sum(schedule) == _YEAR_DAYS, label
    assert [len(schedule) for _label, schedule in _CADENCES] == [52, 12, 4, 1]


# ---------------------------------------------------- the invariant, one driver at a time


#: Each row is a war, described by what is happening in it. The last two are the cells the
#: measurement found worst: a shaky government losing ground and men, and a bankrupt one.
_WARS: tuple[tuple[str, dict], ...] = (
    ("nothing happens at all", {}),
    ("men die and no ground moves",
     dict(casualties=11.93, army=1984.0)),
    ("half the homeland is held", dict(occupied=6)),
    ("a fifth of the army is lost", dict(casualties=200.0, army=1000.0)),
    ("ground and men, and a shaky government",
     dict(casualties=120.0, occupied=4, army=800.0, stability=20.0)),
    ("occupied, bleeding and broke",
     dict(casualties=50.0, occupied=8, army=600.0, treasury=-5.0)),
)


@pytest.mark.parametrize(("what", "war"), _WARS)
def test_the_same_year_of_the_same_war_wearies_the_same_at_every_cadence(what, war):
    """**The invariant, stated as a player would.** Same days, same dead, same ground."""
    reached = _at_every_cadence(**war)

    assert max(reached.values()) - min(reached.values()) < 1e-6, (what, reached)


@pytest.mark.parametrize(("what", "war"), _WARS)
def test_and_the_year_is_not_a_flat_line(what, war):
    """The test above would pass on a `_charge` that returned zero. This is why it does
    not: every one of these wars actually wears the country down."""
    reached = _at_every_cadence(**war)

    assert min(reached.values()) > 0.5, (what, reached)


def test_the_worst_cell_the_measurement_found():
    """The headline repro, kept as a number rather than a property.

    A shaky government losing ground and men reached 37.9 played monthly and **67.4** played
    in one yearly turn -- 1.78x -- before this. The war is the same war in both.
    """
    reached = _at_every_cadence(casualties=120.0, occupied=4, army=800.0, stability=20.0)

    assert reached["year"] == pytest.approx(reached["month"], abs=1e-6), reached
    assert 30.0 < reached["month"] < 45.0, reached


# --------------------------------------------------- and the arithmetic it must not break


@pytest.mark.parametrize("span", [span_of(7), 1.0, 3.0, 12.0])
def test_a_quiet_war_still_charges_exactly_the_time_drift(span):
    """**The identity at the default, and the check that the walk changed no form.**

    Time alone is a single closing rate, and a single closing rate already composed exactly
    -- `1 - (1 - r) ** span` is its own semigroup. Grinding a month into thirty days must
    therefore return the *same* number to the last decimal, not merely a near one. If this
    drifts, the walk has changed what a rate means rather than how rates compose.
    """
    charged = _charge(_Nation(), fresh=0.0, army=100.0, occupied=0, homeland=12,
                      standing=0.0, span=span)

    assert charged == pytest.approx(
        TIME_CEILING * compounded(TIME_DRIFT, span), abs=1e-9)


@pytest.mark.parametrize("span", [1.0, 3.0, 12.0, 60.0])
def test_a_longer_turn_of_the_same_war_still_costs_more(span):
    """Invariant is not the same as flat: more months under occupation must weigh more than
    fewer, or a player could dodge the cost by jumping past it."""
    less = _charge(_Nation(), fresh=0.0, army=100.0, occupied=6, homeland=12,
                   standing=0.0, span=span)
    more = _charge(_Nation(), fresh=0.0, army=100.0, occupied=6, homeland=12,
                   standing=0.0, span=span + 1.0)

    assert more > less


def test_a_war_that_costs_nothing_is_a_war_that_wearies_by_time_alone():
    """The floor of the scale: with nothing happening, a year reaches the time plateau's
    first year and no further -- the sanity check that the walk did not become a clock."""
    reached = _at_every_cadence()

    assert reached["month"] == pytest.approx(
        TIME_CEILING * compounded(TIME_DRIFT, 12.0), abs=1e-6)
    assert reached["month"] < TIME_CEILING


def test_the_ground_still_settles_on_its_plateau_and_not_past_it():
    """The property `OCCUPIED_CEILING` is named for, restated at the year scale: a
    half-occupied country trends to half the ceiling, at every cadence."""
    for span in (1.0, 12.0, 120.0):
        standing = 0.0
        for _turn in range(int(600 / span)):
            standing = max(0.0, min(100.0, standing + _charge(
                _Nation(), fresh=0.0, army=100.0, occupied=6, homeland=12,
                standing=standing, span=span)))
        assert standing == pytest.approx(OCCUPIED_CEILING / 2, abs=0.01), span


# --------------------------------------------------------- and it is wired up, end to end


def _world() -> sqlite3.Connection:
    """A sandbox with Aurelia and Karthos at war and one of Aurelia's provinces held.

    Its own world rather than the `conn` fixture, because comparing cadences means several
    worlds in one test and they have to start identical.
    """
    conn = create_world(":memory:", sandbox_seed())
    conn.execute("UPDATE world SET turn_number = 1 WHERE id = ?", (WORLD,))
    conn.execute(
        "UPDATE relation SET at_war = 1 WHERE world_id = ?"
        "   AND ((nation_a = ? AND nation_b = ?) OR (nation_a = ? AND nation_b = ?))",
        (WORLD, A, B, B, A))
    # Karthos holds one of Aurelia's home provinces for the whole year, in every arm.
    conn.execute(
        "UPDATE region SET controller_nation_id = ? WHERE world_id = ? AND id = 'r_north'",
        (B, WORLD))
    conn.execute(
        "INSERT OR REPLACE INTO unit_template (world_id, kind, domain, cost_treasury,"
        " cost_manpower, base_strength, move_range, build_days)"
        " VALUES (?, 'infantry', 'land', 10, 10, 4.0, 1, 60)", (WORLD,))
    conn.execute(
        "INSERT INTO unit (id, world_id, nation_id, region_id, kind, strength, morale)"
        " VALUES ('a1', ?, ?, 'r_coast', 'infantry', 600.0, 50)", (WORLD, A))
    conn.commit()
    return conn


def _fight_a_year(schedule: tuple[int, ...], *, casualties: float = 120.0) -> float:
    """One year of one war through `assess_war_effort` itself, at one cadence.

    The dying is written into the ledger and taken off the army by hand, at a constant rate
    per elapsed day, so every arm fights the same war -- which is the whole point, and is
    what the measurement had to confirm with a separate instrument because a real front
    fights a *different* war at each cadence.
    """
    conn = _world()
    for turn, days in enumerate(schedule, start=1):
        fresh = casualties * days / _YEAR_DAYS
        conn.execute(
            "UPDATE unit SET strength = strength - ? WHERE world_id = ? AND id = 'a1'",
            (fresh, WORLD))
        conn.execute(
            "INSERT INTO war_effort (id, world_id, nation_id, enemy_id, opened_turn,"
            " last_turn, casualties, charged_casualties, exhaustion)"
            " VALUES (?, ?, ?, ?, 1, 1, ?, 0, 0)"
            " ON CONFLICT(id) DO UPDATE SET casualties = casualties + ?",
            (f"we_{A}_{B}", WORLD, A, B, fresh, fresh))
        conn.commit()
        assess_war_effort(conn, WORLD, turn=turn, date="1897-01-01", days=days)
    effort = effort_between(conn, WORLD, A, B)
    reached = effort.exhaustion if effort else 0.0
    conn.close()
    return reached


def test_the_span_reaches_the_ledger_through_a_real_turn():
    """End to end, because the arithmetic being right does not prove it is wired up.

    `assess_war_effort` is called **once per jump** with the whole jump's `days` -- see
    `turn_engine.jump` -- and it already handed that to `span_of`. This is the assertion
    that what it hands over now composes.

    The residual is the ledger rounding `exhaustion` to three decimals on every write, so
    the weekly arm carries fifty-two roundings and the yearly one carries one. That is the
    tolerance below and it is not tuned: it is `52 * 0.0005`.
    """
    reached = {label: _fight_a_year(schedule) for label, schedule in _CADENCES}

    assert max(reached.values()) - min(reached.values()) < 0.026, reached
    assert min(reached.values()) > 5.0, ("a war nobody felt proves nothing", reached)
