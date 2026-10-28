"""A war drawn province by province lasts long enough for its attrition to decide it, so
the rules about what wears an army down and what builds it back up have to be fair.

Measured on Modern Day before these existed: Turkey's army bled from 54 to nothing in Iran
while taking three provinces of eleven. Five separate rules were each doing their part --
research grew *everyone's* formations whenever *anybody* finished it, a captured province
was emptied by the advance out of it, an encircled army was deleted outright, nothing ever
refilled a player's formations, and the engine accepted a ceasefire on the player's behalf.
"""

from __future__ import annotations

from atlas.engine.refit import REFIT_FRACTION, refit
from atlas.engine.research import strength_bonus
from atlas.engine.resolution import (
    GARRISON_TARGET,
    ResolutionReport,
    _advance_diffs,
    _breakout_to,
    _put_to_the_player,
)

WORLD = "sandbox"
ME, THEM, THIRD = "aurelia", "karthos", "veyra"


def test_research_makes_your_own_formations_stronger_and_nobody_elses(conn):
    conn.execute("INSERT INTO technology (world_id, nation_id, tech_id, completed_turn)"
                 " VALUES (?, ?, 'radar', 1)", (WORLD, ME))
    assert strength_bonus(conn, WORLD, ME) == {"battleship": 2.5}
    assert strength_bonus(conn, WORLD, THEM) == {}


def test_an_advance_never_takes_the_last_formation_out_of_a_province():
    held = [{"id": f"u{i}", "strength": 5.0, "region_id": "taken_last_month"}
            for i in range(1)]
    home = [{"id": f"h{i}", "strength": 5.0, "region_id": "home"} for i in range(4)]
    moved = {diff.target_id for diff in _advance_diffs(held + home, "target", 1)}
    assert "u0" not in moved              # the only formation in a captured province stays
    assert moved and moved <= {"h0", "h1", "h2", "h3"}
    assert len(moved) <= len(home) - GARRISON_TARGET


def test_the_ground_taken_is_never_left_empty_even_by_a_thin_attack():
    lone = [{"id": "a", "strength": 5.0, "region_id": "x"},
            {"id": "b", "strength": 5.0, "region_id": "y"}]
    assert len(list(_advance_diffs(lone, "target", 1))) == 1


def test_an_encircled_army_breaks_out_to_its_nearest_lines(conn):
    # Aurelia's eastern neighbour Veyra takes Northmark, so a force in Veyra's ground is cut
    # off from every province it borders -- but Aurelia's coast is two provinces away.
    conn.execute("UPDATE region SET controller_nation_id = ? WHERE id = 'r_north'", (THIRD,))
    assert _breakout_to(conn, WORLD, "r_east", ME) == "r_coast"
    # With nothing of its own anywhere in reach, a pocket is still taken whole.
    conn.execute("UPDATE region SET controller_nation_id = ? WHERE id = 'r_coast'", (THIRD,))
    assert _breakout_to(conn, WORLD, "r_east", ME) is None


def test_an_offer_to_the_player_is_put_to_them_and_not_answered_for_them(conn):
    report = ResolutionReport()
    entry = {"action": "ceasefire", "actor": THEM, "target": ME}
    assert _put_to_the_player(conn, WORLD, entry, ME, "ceasefire", report)
    (line,) = report.offers_to_player
    assert "Karthos asks for a ceasefire" in line and "Agree a ceasefire with Karthos" in line
    # Between two powers the engine plays, the rule decides as before.
    other = {"action": "ceasefire", "actor": THEM, "target": THIRD}
    assert not _put_to_the_player(conn, WORLD, other, ME, "ceasefire", ResolutionReport())


def test_worn_formations_in_supply_refill_a_little_each_month_and_never_past_full(conn):
    conn.execute("UPDATE nation SET treasury = 500, manpower = 500 WHERE id = ?", (ME,))
    conn.execute("INSERT INTO unit (id, world_id, nation_id, region_id, kind, strength)"
                 " VALUES ('u_worn', ?, ?, 'r_north', 'infantry', 1.0)", (WORLD, ME))
    full = conn.execute("SELECT base_strength FROM unit_template WHERE world_id = ?"
                        " AND kind = 'infantry'", (WORLD,)).fetchone()
    full = float(full[0]) if full else 4.0
    before = 1.0
    for month in range(1, 40):
        refit(conn, WORLD, turn=month, date="1897-05-01")
        now = conn.execute("SELECT strength FROM unit WHERE id = 'u_worn'").fetchone()[0]
        assert now >= before and now <= full + 1e-6
        assert now - before <= full * REFIT_FRACTION + 0.011
        before = now
    assert before > 1.0
