"""The right-hand column, the one play-testers found hardest to read, without technical
details: your orders and Advance at the top, then the countries that matter to you in
three short groups, everyone else a search away, and the council as one button.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from atlas.ui.rail_right import RightRail, _plain_relation  # noqa: E402
from atlas.ui.theme import DARK  # noqa: E402

ME = "aurelia"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def plain(monkeypatch):
    from atlas.ui import preferences

    monkeypatch.setattr(preferences, "_CURRENT", preferences.Preferences(technical=False))


def _relate(conn, other: str, *, war: int = 0, treaty: str = "none", score: float = 0.0):
    conn.execute(
        "INSERT INTO relation (world_id, nation_a, nation_b, at_war, treaty_status, score)"
        " VALUES ('sandbox', ?, ?, ?, ?, ?) ON CONFLICT (nation_a, nation_b) DO UPDATE SET"
        " at_war = excluded.at_war, treaty_status = excluded.treaty_status,"
        " score = excluded.score", (ME, other, war, treaty, score))


def _visible(page) -> list[str]:
    return [name for name, entry, _rule, _group in page._rows if not entry.isHidden()]


def test_orders_and_advance_come_before_the_talking(qapp, conn):
    rail = RightRail(DARK)
    rail.bind(conn, "sandbox", ME)
    body = rail.body
    order = [body.itemAt(i).widget() for i in range(body.count())]
    assert order.index(rail._orders) < order.index(rail._advance) < order.index(
        rail._diplomacy)
    assert rail._advance._button._label.text() == "Advance one month"


def test_the_countries_that_matter_are_grouped_and_the_rest_are_a_search_away(qapp, conn):
    _relate(conn, "karthos", war=1, score=-80)
    _relate(conn, "veyra", treaty="defensive", score=40)
    extra = [r[0] for r in conn.execute(
        "SELECT id FROM nation WHERE world_id = 'sandbox' AND id NOT IN (?, ?, ?)",
        (ME, "karthos", "veyra"))]
    rail = RightRail(DARK)
    rail.bind(conn, "sandbox", ME)
    page = rail._diplomacy._list_page
    groups = {name: group for name, _entry, _rule, group in page._rows}
    assert groups["karthos"] == "war" and groups["veyra"] == "bound"
    assert [h.text() for h in page._headers] == ["At war with you", "Allies and pacts"]
    shown = _visible(page)
    assert shown[:2] == ["karthos", "veyra"]
    # Countries nobody has dealt with wait behind the search box.
    for other in extra:
        name = conn.execute("SELECT name FROM nation WHERE id = ?", (other,)).fetchone()[0]
        assert name.lower() not in shown
        page._find.setText(name[:4])
        assert name.lower() in _visible(page)
        assert all(header.isHidden() for header in page._headers)
        page._find.setText("")


def test_the_council_is_one_button_beside_the_heading(qapp, conn):
    rail = RightRail(DARK)
    rail.bind(conn, "sandbox", ME)
    page = rail._diplomacy._list_page
    assert page._heading.text() == "Talk to a country"
    assert not page._council_inline.isHidden() and page._council.isHidden()
    asked = []
    page.advise.connect(lambda: asked.append(True))
    page._council_inline.clicked.emit()
    assert asked == [True]


def test_relations_read_as_a_player_would_say_them():
    assert _plain_relation(-80, True, "none") == "At war"
    assert _plain_relation(40, False, "defensive") == "Defence pact"
    assert _plain_relation(70, False, "alliance") == "Ally"
    assert _plain_relation(30, False, "none") == "Friendly"
    assert _plain_relation(0, False, "none") == "Neutral"
