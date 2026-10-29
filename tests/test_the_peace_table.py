"""Making peace on terms: pick the provinces, set the money, see whether they sign.

Asked for in play. Before this a peace order carried no terms at all -- the order reader
set ``terms = []`` -- so every war a player ended by order ended as a white peace, whatever
the sentence said. The table offers the enemy ground you occupy, prices it with the engine's
own review, and answers with the engine's own acceptance; its offer is a sentence the order
reader turns back into exactly those terms.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from atlas.engine.peace import acceptance  # noqa: E402
from atlas.engine.peace_table import sentence, table, terms_for, verdict  # noqa: E402
from atlas.engine.staff_bots import parse_orders  # noqa: E402
from atlas.ui.peace_panel import PeaceOverlay  # noqa: E402
from atlas.ui.theme import DARK  # noqa: E402
from atlas.ui.widgets import Chip  # noqa: E402

WORLD, ME, THEM = "sandbox", "aurelia", "karthos"


def _war(conn) -> None:
    conn.execute("UPDATE relation SET at_war = 1 WHERE world_id = ? AND nation_a IN (?, ?)"
                 " AND nation_b IN (?, ?)", (WORLD, ME, THEM, ME, THEM))
    conn.execute("UPDATE region SET controller_nation_id = ? WHERE id = 'r_west'", (ME,))
    conn.commit()


def _name(conn, region_id: str) -> str:
    return conn.execute("SELECT name FROM region WHERE id = ?", (region_id,)).fetchone()[0]


def test_the_table_offers_the_ground_you_hold_and_money_they_can_pay(conn):
    _war(conn)
    found = table(conn, WORLD, ME, THEM)
    assert [claim.region_id for claim in found.claims] == ["r_west"]
    assert found.max_reparations == int(100.0 * 0.6)
    assert set(found.suggested) <= {"r_west"}


def test_the_verdict_is_the_engines_own_acceptance(conn):
    _war(conn)
    for regions, money in ((["r_west"], 0), ([], 0), (["r_west"], 60)):
        ruling = verdict(conn, WORLD, ME, THEM, regions, money)
        engine = acceptance(conn, WORLD, offered_to=THEM, offered_by=ME,
                            terms=terms_for(ME, THEM, regions, money))
        assert ruling.signs == engine.accepted
    # A demand the rules refuse outright is a refusal, with the reason.
    over = verdict(conn, WORLD, ME, THEM, [], 500)
    assert not over.signs and "cannot pay" in over.because


def test_the_offer_reads_back_as_exactly_its_terms(conn):
    _war(conn)
    text = sentence("Karthos", [_name(conn, "r_west")], 12)
    (action,) = parse_orders(conn, WORLD, nation_id=ME, raw_text=text)["actions"]
    assert action["type"] == "sue_for_peace" and action["target_nation"] == THEM
    assert [(t["kind"], t["subject"], t.get("value")) for t in action["terms"]] == [
        ("cede_region", "r_west", None), ("reparations", "", 12.0)]
    (white,) = parse_orders(conn, WORLD, nation_id=ME,
                            raw_text=sentence("Karthos", [], 0))["actions"]
    assert white["terms"] == []


def test_the_screen_writes_the_offer_into_the_orders(conn):
    _war(conn)
    QApplication.instance() or QApplication([])
    screen = PeaceOverlay(DARK)
    written: list[str] = []
    screen.order_written.connect(written.append)
    screen.load(conn, WORLD, ME, THEM)
    chip = screen.findChild(Chip, "claim:r_west")
    if not chip.selected:
        chip.clicked.emit()
    screen._slider.setValue(5)
    screen._go.clicked.emit()
    assert written == [sentence("Karthos", [_name(conn, "r_west")], 5)]
