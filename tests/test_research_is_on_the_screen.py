"""Research a player can see, improve their formations with, and stop at a ceiling.

Asked for in play: "is there a clear way to upgrade stuff, like investing in research to
make your troops or vehicles better, with limits?" There was research and no way to see it
-- a programme began only when its exact name was typed -- and each era had seven one-off
programmes. Now every era has upgrade lines (Infantry, Armour, Air wings, Fleet) from Mk I
to Mk III, a Research tab that writes the order for you, and a mark on every improved
counter. The limits: three marks and no more, each dearer and slower than the last, a slot
and money to begin one, and never more than half again a formation's standard strength.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from atlas.engine.production import _programme_for  # noqa: E402
from atlas.engine.research import (  # noqa: E402
    MAX_MARK,
    MAX_RESEARCH_SHARE,
    TECHNOLOGIES,
    marks,
    strength_bonus,
)
from atlas.engine.research_board import board  # noqa: E402
from atlas.engine.staff_bots import parse_orders  # noqa: E402
from atlas.map.query import load_map_view  # noqa: E402
from atlas.map.render import build_scene  # noqa: E402
from atlas.map.scene_model import MarkerKind  # noqa: E402
from atlas.scenarios import cold_war, modern_day  # noqa: E402
from atlas.ui.research_panel import ResearchBlock  # noqa: E402
from atlas.ui.theme import DARK  # noqa: E402
from atlas.ui.widgets import Chip  # noqa: E402

WORLD, ME, THEM = "sandbox", "aurelia", "karthos"


def _learn(conn, *techs: str, nation: str = ME) -> None:
    for tech in techs:
        conn.execute("INSERT INTO technology (world_id, nation_id, tech_id, completed_turn)"
                     " VALUES (?, ?, ?, 1)", (WORLD, nation, tech))


def _armour(conn, base: float = 6.0) -> None:
    conn.execute("INSERT INTO unit_template (world_id, kind, domain, base_strength)"
                 " VALUES (?, 'armour', 'land', ?)", (WORLD, base))


@pytest.mark.parametrize("catalogue", [TECHNOLOGIES, cold_war.TECHNOLOGIES,
                                       modern_day.TECHNOLOGIES], ids=["1936", "1972", "2026"])
def test_every_era_has_upgrade_lines_that_stop_at_mk_iii(catalogue):
    lines: dict[str, list] = {}
    for tech in catalogue:
        if tech.line:
            lines.setdefault(tech.line, []).append(tech)
    assert set(lines) == {"infantry", "armour", "air", "navy"}
    for steps in lines.values():
        steps.sort(key=lambda t: t.mark)
        assert [t.mark for t in steps] == list(range(2, MAX_MARK + 1))
        # Each mark needs the one before it, and costs more and takes longer.
        assert steps[0].requires == () and steps[1].requires == (steps[0].id,)
        assert steps[1].cost_treasury > steps[0].cost_treasury
        assert steps[1].turns > steps[0].turns


def test_upgrades_are_your_own_and_research_has_a_ceiling(conn):
    _armour(conn, base=6.0)
    _learn(conn, "armour_mk2")
    assert strength_bonus(conn, WORLD, ME) == {"armour": 1.0}
    assert strength_bonus(conn, WORLD, THEM) == {}
    assert marks(conn, WORLD, ME) == {"armour": 2}
    _learn(conn, "armour_mk3")
    assert marks(conn, WORLD, ME) == {"armour": 3}
    # A base of 3 caps the two marks' +2 at half of it.
    conn.execute("UPDATE unit_template SET base_strength = 3.0 WHERE kind = 'armour'")
    assert strength_bonus(conn, WORLD, ME) == {"armour": 3.0 * MAX_RESEARCH_SHARE}


def test_the_board_says_what_is_open_locked_and_under_way(conn):
    found = board(conn, WORLD, ME)
    armour = next(line for line in found.lines if line.line_id == "armour")
    assert armour.mark == 1 and armour.next.name == "Armour Mk II"
    assert armour.next.state == "open" and armour.next.order == "Research Armour Mk II"
    locked = [card for card in found.programmes if card.state == "locked"]
    assert locked and all(card.why.startswith("Needs ") for card in locked)

    conn.execute("UPDATE nation SET treasury = 5 WHERE id = ?", (ME,))
    poor = next(line for line in board(conn, WORLD, ME).lines if line.line_id == "armour")
    assert poor.next.state == "unaffordable" and "you have 5" in poor.next.why

    _learn(conn, "armour_mk2", "armour_mk3")
    top = next(line for line in board(conn, WORLD, ME).lines if line.line_id == "armour")
    assert top.mark == MAX_MARK and top.next is None


def test_every_start_button_writes_an_order_that_reads_back_as_that_programme(conn):
    found = board(conn, WORLD, ME)
    cards = [line.next for line in found.lines] + found.programmes
    for card in cards:
        parsed = parse_orders(conn, WORLD, nation_id=ME, raw_text=card.order)
        assert [(a["type"], a["project"]) for a in parsed["actions"]] == [
            ("build_project", card.tech_id)], card.order


def test_an_engine_power_buys_better_weapons_for_a_war_and_not_in_peace(conn):
    conn.execute("UPDATE nation SET treasury = 900 WHERE id = ?", (ME,))
    _learn(conn, "welding", "mass_production", "radar", "carrier_doctrine",
           "synthetic_fuel", "atomic_programme")
    assert _programme_for(conn, WORLD, ME, 900.0, ordinary_cost=20.0) is None
    conn.execute("UPDATE relation SET at_war = 1 WHERE world_id = ? AND"
                 " ((nation_a = ? AND nation_b = ?) OR (nation_a = ? AND nation_b = ?))",
                 (WORLD, ME, THEM, THEM, ME))
    assert _programme_for(conn, WORLD, ME, 900.0, ordinary_cost=20.0).endswith("_mk2")


def test_an_improved_formation_wears_its_mark_on_the_map(conn):
    _armour(conn)
    conn.execute("INSERT INTO unit (id, world_id, nation_id, region_id, kind, strength)"
                 " VALUES ('u_tank', ?, ?, 'r_north', 'armour', 7.0)", (WORLD, ME))
    _learn(conn, "armour_mk2", "armour_mk3")
    view = load_map_view(conn, WORLD, observer_nation_id=ME)
    assert view.marks == {ME: {"armour": 3}}
    geo = next(r.geo_key for r in view.regions if r.region_id == "r_north")
    scene = build_scene(view, projection=_Flat(), pack_id="sandbox", lod=0,
                        label_points={geo: (0.0, 0.0)})
    (counter,) = [m for m in scene.markers if m.kind == MarkerKind.UNIT]
    assert counter.mark == 3 and "armour Mk III" in counter.tooltip


class _Flat:
    key = "flat"

    def project(self, lon: float, lat: float) -> tuple[float, float]:
        return lon, -lat


def test_start_puts_the_order_in_the_box_and_says_so(conn):
    QApplication.instance() or QApplication([])
    box: list[str] = []
    panel = ResearchBlock(DARK, orders_text=lambda: "\n".join(box))
    panel.start.connect(box.append)
    panel.load(conn, WORLD, ME, DARK)
    chip = panel.findChild(Chip, "research:armour_mk2")
    chip.clicked.emit()
    assert box == ["Research Armour Mk II"]
    assert panel.findChild(Chip, "research:armour_mk2")._label.text() == "In orders ✓"
