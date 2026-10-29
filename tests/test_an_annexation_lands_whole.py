"""An annexation the summary reports is one the map and the province panel show.

Reported from play: "I annexed Afghanistan, the turn summary said I did, the map and the
country panel say it is not annexed." Three faults lined up:

* the territory rule refused the transfer, because Afghanistan is one province and borders
  nothing Turkish once the Iranian ground the army crossed was handed back -- although Turkey
  occupied Afghanistan itself;
* the refused transfer was severed from the batch and the rest stood, so Afghanistan was
  buried while still owning its land, and no order could finish the job; and
* the province panel compared the occupier's id with the owner's *name*, so every annexed
  province said "Occupied, not annexed" anyway.

Sandbox Karthos stands in for Afghanistan: once its western province goes to Veyra it is
one province, across water from Aurelia.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from atlas.engine.conquest import annex, settle_buried_land  # noqa: E402
from atlas.ui.rail_left import LeftRail  # noqa: E402
from atlas.ui.theme import DARK  # noqa: E402
from atlas.ui.widgets import TextLabel  # noqa: E402

WORLD = "sandbox"
ME, THEM = "aurelia", "karthos"


def _beaten_and_out_of_reach(conn) -> None:
    conn.execute("UPDATE region SET owner_nation_id = 'veyra', controller_nation_id = NULL,"
                 " home_nation_id = 'veyra' WHERE id = 'r_west'")
    conn.execute("UPDATE region SET controller_nation_id = ? WHERE id = 'r_south'", (ME,))
    conn.execute("DELETE FROM unit WHERE nation_id = ?", (THEM,))
    conn.commit()


def _region(conn, region_id: str = "r_south") -> tuple:
    row = conn.execute("SELECT owner_nation_id, controller_nation_id FROM region"
                       " WHERE id = ?", (region_id,)).fetchone()
    return tuple(row)


def _alive(conn, nation_id: str = THEM) -> int:
    return conn.execute("SELECT is_alive FROM nation WHERE id = ?", (nation_id,)).fetchone()[0]


def test_a_country_you_occupy_can_be_annexed_without_a_border_to_it(conn):
    _beaten_and_out_of_reach(conn)
    done = annex(conn, WORLD, nation_id=ME, target=THEM, turn=1, date="1897-05-01")
    assert done.done and done.eliminated, done.refused
    assert _region(conn) == (ME, ME)
    assert _alive(conn) == 0


def test_an_annexation_the_rules_refuse_changes_nothing_at_all(conn):
    _beaten_and_out_of_reach(conn)
    # Occupied, then lost to a rising: Aurelia controls nothing of Karthos's, and the
    # order must be refused whole -- not bury Karthos and leave the land behind.
    conn.execute("UPDATE region SET controller_nation_id = NULL WHERE id = 'r_south'")
    done = annex(conn, WORLD, nation_id=ME, target=THEM, turn=1, date="1897-05-01")
    assert not done.done and done.refused
    assert _alive(conn) == 1 and _region(conn) == (THEM, None)


def test_a_save_left_with_a_buried_owner_is_mended(conn):
    _beaten_and_out_of_reach(conn)
    conn.execute("UPDATE nation SET is_alive = 0 WHERE id = ?", (THEM,))
    conn.commit()
    headlines, problems = settle_buried_land(conn, WORLD, turn=2, date="1897-06-01")
    assert not problems
    assert headlines == ["Aurelia annexed what was left of Karthos."]
    assert _region(conn) == (ME, ME)
    # Nothing left to mend the second time.
    assert settle_buried_land(conn, WORLD, turn=3, date="1897-07-01") == ([], [])


def _panel_text(conn, region_id: str) -> str:
    QApplication.instance() or QApplication([])
    rail = LeftRail(DARK)
    rail.bind(conn, WORLD, ME)
    rail.set_selected_region(region_id)
    return " ".join(label.text() for label in rail.findChildren(TextLabel))


def test_the_province_panel_calls_annexed_ground_yours(conn):
    _beaten_and_out_of_reach(conn)
    annex(conn, WORLD, nation_id=ME, target=THEM, turn=1, date="1897-05-01")
    assert "not annexed" not in _panel_text(conn, "r_south")
    # And still says so of ground that is only occupied.
    conn.execute("UPDATE region SET controller_nation_id = ? WHERE id = 'r_east'", (ME,))
    assert "Occupied, not annexed" in _panel_text(conn, "r_east")


def test_an_annexed_country_stops_trading(conn):
    """Found by a 60-turn play-test: every annexation left the dead country's trade routes
    running, and `assess_trade` walked them for nobody every turn after."""
    from atlas.testing.invariants import violations

    _beaten_and_out_of_reach(conn)
    conn.execute("INSERT INTO trade_route (id, world_id, seller_id, buyer_id, good, volume,"
                 " opened_turn) VALUES ('r1', ?, 'veyra', ?, 'oil', 5, 1)", (WORLD, THEM))
    annex(conn, WORLD, nation_id=ME, target=THEM, turn=1, date="1897-05-01")
    assert conn.execute("SELECT COUNT(*) FROM trade_route WHERE id = 'r1'").fetchone()[0] == 0
    assert not [v for v in violations(conn, WORLD) if "route" in v]


def test_a_save_with_trade_to_a_dead_country_is_mended(conn):
    conn.execute("INSERT INTO trade_route (id, world_id, seller_id, buyer_id, good, volume,"
                 " opened_turn) VALUES ('r1', ?, 'veyra', ?, 'oil', 5, 1)", (WORLD, THEM))
    conn.execute("UPDATE nation SET is_alive = 0 WHERE id = ?", (THEM,))
    conn.execute("UPDATE region SET owner_nation_id = 'veyra' WHERE owner_nation_id = ?",
                 (THEM,))
    conn.commit()
    assert settle_buried_land(conn, WORLD, turn=2, date="1897-06-01") == ([], [])
    assert conn.execute("SELECT COUNT(*) FROM trade_route").fetchone()[0] == 0
