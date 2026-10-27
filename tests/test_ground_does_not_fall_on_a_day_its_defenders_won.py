"""A region changes hands only on a day the attacker wins.

The advance is continuous in the margin of the roll, so a narrow defensive win still lets
an attacker creep forward -- that is deliberate, and gating the advance on winning made a
cliff. What it allowed as well was a capture in the same breath as "Iran prevailed
narrowly", which a play-test read, fairly, as the dice cheating. The campaign may reach
the brink on a lost day; the last step takes a won one.
"""

from __future__ import annotations

import dataclasses
import sqlite3

import pytest

from atlas.engine import resolution
from atlas.engine.resolution import ResolutionReport, contest_id_for, resolve_turn
from atlas.scenarios.sandbox import sandbox_seed
from atlas.sim.resolve import CAPTURE_THRESHOLD
from atlas.state.bootstrap import create_world

WORLD = "sandbox"
ATTACKER, DEFENDER = "aurelia", "karthos"
TURN = 5


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = create_world(":memory:", sandbox_seed())
    connection.execute("UPDATE world SET turn_number = ? WHERE id = ?", (TURN, WORLD))
    for a, b in ((ATTACKER, DEFENDER), (DEFENDER, ATTACKER)):
        connection.execute(
            "INSERT INTO relation (world_id, nation_a, nation_b, at_war) VALUES (?, ?, ?, 1)"
            " ON CONFLICT (nation_a, nation_b) DO UPDATE SET at_war = 1", (WORLD, a, b))
    connection.commit()
    yield connection
    connection.close()


def _target(conn) -> str:
    return str(conn.execute(
        "SELECT id FROM region WHERE world_id = ? AND owner_nation_id = ?"
        "   AND kind <> 'ocean' ORDER BY id LIMIT 1", (WORLD, DEFENDER)).fetchone()[0])


def _fight(conn, monkeypatch, *, attacker_wins: bool, advance: float, start: float):
    region = _target(conn)
    conn.execute(
        "INSERT INTO contest (id, world_id, region_id, attacker_id, defender_id,"
        " pressure, opened_turn, last_turn) VALUES (?, ?, ?, ?, ?, ?, 3, 4)",
        (contest_id_for(region, ATTACKER), WORLD, region, ATTACKER, DEFENDER, start))
    conn.commit()
    real = resolution.resolve_contest

    def rigged(*args, **kwargs):
        return dataclasses.replace(real(*args, **kwargs), attacker_wins=attacker_wins,
                                   decisive=False, advance=advance)

    monkeypatch.setattr(resolution, "resolve_contest", rigged)
    report = resolve_turn(
        conn, WORLD, turn=TURN, date="1897-05-01", seed=7, actions=[],
        contests=[{"attacker": ATTACKER, "defender": DEFENDER, "region_id": region}])
    holder = conn.execute(
        "SELECT COALESCE(controller_nation_id, owner_nation_id) FROM region WHERE id = ?",
        (region,)).fetchone()[0]
    pressure = conn.execute(
        "SELECT pressure FROM contest WHERE region_id = ? AND attacker_id = ?",
        (region, ATTACKER)).fetchone()
    battle = conn.execute(
        "SELECT text FROM event WHERE world_id = ? AND kind = 'battle'"
        " ORDER BY id DESC LIMIT 1", (WORLD,)).fetchone()[0]
    return report, holder, (float(pressure[0]) if pressure else None), battle


def test_a_lost_day_brings_the_campaign_to_the_brink_and_no_further(conn, monkeypatch):
    report, holder, pressure, battle = _fight(
        conn, monkeypatch, attacker_wins=False, advance=0.5, start=0.8)
    assert isinstance(report, ResolutionReport) and not report.problems
    assert holder == DEFENDER
    assert pressure is not None and 0.8 < pressure < CAPTURE_THRESHOLD
    assert "Karthos prevailed narrowly, but gave ground." in battle


def test_a_won_day_still_carries_it(conn, monkeypatch):
    _, holder, _, battle = _fight(
        conn, monkeypatch, attacker_wins=True, advance=0.5, start=0.8)
    assert holder == ATTACKER
    assert "Aurelia prevailed narrowly." in battle


def test_a_repulse_says_the_line_held(conn, monkeypatch):
    _, holder, pressure, battle = _fight(
        conn, monkeypatch, attacker_wins=False, advance=-0.05, start=0.5)
    assert holder == DEFENDER and pressure is not None and pressure < 0.5
    assert "Karthos prevailed narrowly and held the line." in battle
