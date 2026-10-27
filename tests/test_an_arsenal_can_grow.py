"""Warheads only ever went down.

`nuclear._spend` adjusts the count negative when weapons are used, a launch clamps what it
commits to what is held, and **nothing anywhere in the engine ever added one**. Every arsenal
on every map was the number a scenario seeded and a monotonic decline from there. So
proliferation could not happen, an arms race could not happen, and a power without warheads
in 1936 could not have them by 1945 -- which is most of what that period is about.

**And the programme was already there, buying the wrong thing.** `atomic_programme` is
sixteen turns, a hundred and twenty treasury, secret, and calls itself "the longest thing a
country can undertake and the only one worth hiding". Its effects were `military_strength
+12` and `legitimacy +4`: finishing the Manhattan Project made your army feel better about
itself and left you non-nuclear.

Two steps, different in kind. Crossing the threshold is a technology and happens once.
Growing the pile is production, in the same queue as a steelworks, because that is the
decision one queue exists to pose.
"""

from __future__ import annotations

import sqlite3

import pytest

from atlas.engine.warheads import (
    WARHEAD_BUILD_DAYS,
    WARHEAD_INDUSTRY,
    WARHEAD_PROJECT,
    WARHEAD_TREASURY,
    can_build_warheads,
    order_warhead,
)
from atlas.scenarios.sandbox import sandbox_seed
from atlas.state.bootstrap import create_world, fingerprint
from atlas.state.replay import rewind_to

WORLD = "sandbox"
NATION = "aurelia"


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = create_world(":memory:", sandbox_seed())
    connection.execute("UPDATE world SET turn_number = 1 WHERE id = ?", (WORLD,))
    connection.execute(
        "UPDATE nation SET treasury = 500, industry = 60 WHERE id = ?", (NATION,))
    connection.commit()
    yield connection
    connection.close()


def _arm(conn, warheads: int = 2, delivery: str = "aircraft") -> None:
    conn.execute(
        "INSERT INTO arsenal (world_id, nation_id, warheads, delivery)"
        " VALUES (?, ?, ?, ?)", (WORLD, NATION, warheads, delivery))
    conn.commit()


def _held(conn) -> int:
    row = conn.execute(
        "SELECT warheads FROM arsenal WHERE world_id = ? AND nation_id = ?",
        (WORLD, NATION)).fetchone()
    return int(row["warheads"]) if row else 0


def _queued(conn) -> int:
    return int(conn.execute(
        "SELECT COUNT(*) c FROM construction WHERE world_id = ? AND project_kind = ?",
        (WORLD, WARHEAD_PROJECT)).fetchone()["c"])


def test_a_country_with_no_programme_behind_it_cannot_order_one(conn):
    """A warhead is not something a country orders; it is something it learns to make."""
    assert not can_build_warheads(conn, WORLD, NATION)
    report = order_warhead(conn, WORLD, nation_id=NATION, turn=2, date="1897-04-01")
    assert not report.commissioned
    assert report.problems, "a refusal must say why, or the same order comes back next turn"


def test_holding_an_arsenal_is_the_licence_rather_than_holding_the_technology(conn):
    """**Scenarios seed nuclear powers without seeding them a research row**, and most do.

    A rule keyed on the technology would tell the United States in 1972 that it could not
    build warheads, which is the kind of correct-looking check that ships broken.
    """
    _arm(conn)
    assert can_build_warheads(conn, WORLD, NATION)


def test_a_seeded_power_with_no_delivery_is_not_a_nuclear_power(conn):
    """`delivery = 'none'` is how the schema says "counted, but cannot reach anybody"."""
    _arm(conn, warheads=3, delivery="none")
    assert not can_build_warheads(conn, WORLD, NATION)


def test_commissioning_one_charges_now_and_delivers_later(conn):
    """Same queue, same money, same waiting as a battleship -- and charged when laid down,
    so a programme abandoned halfway is money spent."""
    _arm(conn)
    before = conn.execute(
        "SELECT treasury, industry FROM nation WHERE id = ?", (NATION,)).fetchone()

    report = order_warhead(conn, WORLD, nation_id=NATION, turn=2, date="1897-04-01")

    assert report.commissioned, report.problems
    assert _queued(conn) == 1
    assert _held(conn) == 2, "it is not on the pile until it is finished"
    after = conn.execute(
        "SELECT treasury, industry FROM nation WHERE id = ?", (NATION,)).fetchone()
    assert float(before["treasury"]) - float(after["treasury"]) == pytest.approx(
        WARHEAD_TREASURY)
    assert float(before["industry"]) - float(after["industry"]) == pytest.approx(
        WARHEAD_INDUSTRY)


def test_a_country_that_cannot_pay_is_told_the_price(conn):
    """Decision 50 again: the refusal names both numbers, so a staff can act on it."""
    _arm(conn)
    conn.execute("UPDATE nation SET treasury = 1, industry = 1 WHERE id = ?", (NATION,))
    conn.commit()

    report = order_warhead(conn, WORLD, nation_id=NATION, turn=2, date="1897-04-01")

    assert not report.commissioned
    assert str(int(WARHEAD_TREASURY)) in report.problems[0]


def test_it_takes_long_enough_that_a_war_does_not_produce_one(conn):
    """Nine months. An arms race should be a decade, and a power that starts a programme
    because it is losing should not finish it before the war does."""
    assert WARHEAD_BUILD_DAYS >= 180.0
    _arm(conn)
    order_warhead(conn, WORLD, nation_id=NATION, turn=2, date="1897-04-01")
    left = float(conn.execute(
        "SELECT days_remaining FROM construction WHERE world_id = ? AND project_kind = ?",
        (WORLD, WARHEAD_PROJECT)).fetchone()["days_remaining"])
    assert left == pytest.approx(WARHEAD_BUILD_DAYS)


def test_commissioning_a_warhead_rewinds(conn):
    """Written as diffs like everything else, so a rewind un-builds the bomb."""
    _arm(conn)
    mark = fingerprint(conn, WORLD)
    conn.execute("UPDATE world SET turn_number = 2 WHERE id = ?", (WORLD,))
    conn.commit()

    assert order_warhead(
        conn, WORLD, nation_id=NATION, turn=2, date="1897-04-01").commissioned
    assert _queued(conn) == 1

    report = rewind_to(conn, WORLD, 1)
    assert report.ok, report.describe()
    assert _queued(conn) == 0
    assert fingerprint(conn, WORLD) == mark
