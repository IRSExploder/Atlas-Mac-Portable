"""Two kinds of warhead that do opposite jobs.

**The trap this design had to avoid** is the one `relation.treaty_status = 'vassal'` fell
into: a value the schema carries and nothing reads. A tactical/strategic split that only
recorded which kind a warhead was would be a label, and a label is not a mechanic.

So the split cuts both ways and each half is real. Only strategic weapons deter, because a
deterrent works by threatening what an attacker cannot accept losing and a battlefield weapon
threatens the battlefield -- which the attacker is already standing on and has already
decided to accept. And only tactical weapons help win a contest, anywhere the holder is
fighting rather than only over its own core.

That makes the choice a trade at the same price rather than a preference: every battlefield
weapon is one the deterrent does not have.
"""

from __future__ import annotations

import sqlite3

import pytest

from atlas.engine.nuclear import deterrence, tactical_edge
from atlas.engine.warheads import TACTICAL_PROJECT, WARHEAD_PROJECT, order_warhead
from atlas.scenarios.sandbox import sandbox_seed
from atlas.state.bootstrap import create_world

WORLD = "sandbox"
NATION = "aurelia"


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = create_world(":memory:", sandbox_seed())
    connection.execute("UPDATE world SET turn_number = 1 WHERE id = ?", (WORLD,))
    connection.execute(
        "UPDATE nation SET treasury = 900, industry = 90 WHERE id = ?", (NATION,))
    connection.commit()
    yield connection
    connection.close()


def _arm(conn, *, warheads: int = 6, tactical: int = 0) -> None:
    conn.execute(
        "INSERT INTO arsenal (world_id, nation_id, warheads, delivery, tactical)"
        " VALUES (?, ?, ?, 'missile', ?)", (WORLD, NATION, warheads, tactical))
    conn.commit()


def _home(conn) -> str:
    row = conn.execute(
        "SELECT id FROM region WHERE world_id = ? AND home_nation_id = ?"
        "   AND kind <> 'ocean' ORDER BY id LIMIT 1", (WORLD, NATION)).fetchone()
    return str(row["id"]) if row else ""


def test_an_all_strategic_arsenal_deters_and_does_not_fight(conn):
    """The shape every existing world migrates to, and it must behave as it always did."""
    _arm(conn, warheads=6, tactical=0)
    assert deterrence(conn, NATION, _home(conn)) > 0.0
    assert tactical_edge(conn, NATION, _home(conn)) == 0.0


def test_an_all_tactical_arsenal_fights_and_deters_nothing(conn):
    """**The half that makes the split a cost.**

    A country that turned its whole deterrent into battlefield weapons has bought a modest
    combat edge with the thing that was keeping its capital off the table.
    """
    _arm(conn, warheads=6, tactical=6)
    assert deterrence(conn, NATION, _home(conn)) == 0.0
    assert tactical_edge(conn, NATION, _home(conn)) > 0.0


def test_the_deterrent_is_what_is_left_after_the_battlefield_weapons(conn):
    """A subset, never a second pile. Splitting one off takes it from the other."""
    _arm(conn, warheads=6, tactical=0)
    whole = deterrence(conn, NATION, _home(conn))

    conn.execute("UPDATE arsenal SET tactical = 4 WHERE nation_id = ?", (NATION,))
    conn.commit()

    assert 0.0 < deterrence(conn, NATION, _home(conn)) < whole


def test_battlefield_weapons_help_anywhere_the_holder_is_fighting(conn):
    """The whole difference from deterrence, which applies only over a power's own core.

    A deterrent is about what cannot be taken *from you*. A weapon is a weapon, and works
    wherever it is carried -- which is the reason to hold any.
    """
    _arm(conn, warheads=4, tactical=4)
    abroad = conn.execute(
        "SELECT id FROM region WHERE world_id = ? AND COALESCE(home_nation_id, '') <> ?"
        "   AND kind <> 'ocean' ORDER BY id LIMIT 1", (WORLD, NATION)).fetchone()

    assert deterrence(conn, NATION, str(abroad["id"])) == 0.0
    assert tactical_edge(conn, NATION, str(abroad["id"])) > 0.0


def test_the_edge_diminishes_so_a_big_pile_does_not_decide_a_war(conn):
    """The tenth battlefield weapon changes a battle far less than the first."""
    _arm(conn, warheads=200, tactical=1)
    first = tactical_edge(conn, NATION, _home(conn))
    conn.execute("UPDATE arsenal SET tactical = 100 WHERE nation_id = ?", (NATION,))
    conn.commit()
    hundred = tactical_edge(conn, NATION, _home(conn))

    assert hundred > first
    assert hundred < first * 100, "a hundred weapons must not be a hundred times the edge"
    assert hundred <= 0.06, "and nothing here should decide a war on its own"


def test_a_world_with_no_arsenals_is_untouched(conn):
    """Zero for every 1936 world and every world written before the column existed."""
    assert deterrence(conn, NATION, _home(conn)) == 0.0
    assert tactical_edge(conn, NATION, _home(conn)) == 0.0


def test_the_two_kinds_cost_the_same(conn):
    """**Cheaper would be wrong.** A tactical weapon is not a cheap weapon, it is a
    differently aimed one, and the price of choosing it is paid in deterrence."""
    _arm(conn, warheads=2)
    before = conn.execute(
        "SELECT treasury, industry FROM nation WHERE id = ?", (NATION,)).fetchone()
    order_warhead(conn, WORLD, nation_id=NATION, turn=2, date="1897-04-01", tactical=False)
    mid = conn.execute(
        "SELECT treasury, industry FROM nation WHERE id = ?", (NATION,)).fetchone()
    order_warhead(conn, WORLD, nation_id=NATION, turn=3, date="1897-05-01", tactical=True)
    after = conn.execute(
        "SELECT treasury, industry FROM nation WHERE id = ?", (NATION,)).fetchone()

    strategic_cost = float(before["treasury"]) - float(mid["treasury"])
    tactical_cost = float(mid["treasury"]) - float(after["treasury"])
    assert strategic_cost == pytest.approx(tactical_cost)


def test_the_queue_remembers_which_kind_was_ordered(conn):
    """Both go in the same queue under the same `project_kind`; the `kind` is the choice."""
    _arm(conn, warheads=2)
    order_warhead(conn, WORLD, nation_id=NATION, turn=2, date="1897-04-01", tactical=True)
    order_warhead(conn, WORLD, nation_id=NATION, turn=3, date="1897-05-01", tactical=False)

    kinds = {str(r["kind"]) for r in conn.execute(
        "SELECT kind FROM construction WHERE world_id = ? AND project_kind = ?",
        (WORLD, WARHEAD_PROJECT))}
    assert kinds == {TACTICAL_PROJECT, WARHEAD_PROJECT}
