"""An order that was misread used to look exactly like an order that worked.

The parser has always come back with two lists -- what it will attempt, and what it
refused with a reason -- and the rail has always printed both. What neither of them could
show is the third case: an order that parsed cleanly into something the player did not
mean. *Move the northern army to Kharkiv* becoming an attempt from the wrong province is
not a refusal and not a failure; it is a summary clause that reads plausibly, queued
before anybody looked at it.

So the read-back is between the parse and the draft. What is tested here:

* nothing is queued until it is confirmed, and cancelling changes nothing at all;
* every field the parser may resolve is read back, checked against the parser's own
  schema so a field added there cannot be silently dropped here;
* ids become the names of real places and powers, in a bounded number of queries;
* and the panel has no opinion about whether an order is *legal*, which is the workbench's
  question and would be a second copy of the rules if it were asked twice.
"""

from __future__ import annotations

import os
import sqlite3

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from atlas.engine.parser import ACTION_SCHEMA, ParsedOrders  # noqa: E402
from atlas.state.bootstrap import fingerprint  # noqa: E402
from atlas.ui.preferences import SCALES  # noqa: E402
from atlas.ui.theme import DARK, LIGHT  # noqa: E402
from atlas.ui.understanding import (  # noqa: E402
    FIELDS,
    ISSUE,
    REVISE,
    UnderstoodOverlay,
    _ActionBlock,
    read_back,
)

WORLD = "sandbox"


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def parsed() -> ParsedOrders:
    return ParsedOrders(
        actions=(
            {"type": "move_units", "summary": "March west with two brigades",
             "from_region": "r_north", "to_region": "r_west", "quantity": 2.0,
             "tone": "neutral"},
            {"type": "diplomatic_message", "summary": "Sound out Karthos",
             "target_nation": "karthos", "tone": "friendly"},
        ),
        rejected=(
            {"order": "Sink their fleet",
             "reason": "You hold no naval formation that can reach them."},
        ),
    )


# -- what the read-back says --------------------------------------------------


def test_ids_become_the_names_of_real_places_and_powers(conn, parsed):
    """`r_west` is not a thing a person wrote and not a thing they can check."""
    read = read_back(conn, WORLD, list(parsed.actions))
    fields = dict(read[0]["details"])
    assert fields["From"] == _name(conn, "region", "r_north")
    assert fields["To"] == _name(conn, "region", "r_west")
    assert dict(read[1]["details"])["Target"] == _name(conn, "nation", "karthos")


def test_a_whole_number_is_read_back_as_a_whole_number(conn, parsed):
    """The schema types quantity as a number, so two brigades arrive as ``2.0``."""
    assert dict(read_back(conn, WORLD, list(parsed.actions))[0]["details"])["How many"] == "2"


def test_the_act_carries_what_the_catalogue_says_it_is(conn, parsed, monkeypatch):
    """Read from the capability manifest, never restated here."""
    from atlas.engine.capabilities import by_type
    from atlas.ui import preferences

    read = read_back(conn, WORLD, list(parsed.actions))
    # In a player's words without technical details; the schema's name with them.
    assert read[0]["act"] == "Move forces"
    assert read[0]["explain"] == by_type("move_units").explain
    monkeypatch.setattr(preferences, "_CURRENT", preferences.Preferences(technical=True))
    assert read_back(conn, WORLD, list(parsed.actions))[0]["act"] == "move units"


def test_every_field_the_parser_may_resolve_is_read_back():
    """**The load-bearing test.** A field the schema gains must appear here or the panel
    silently stops showing part of what it claims to be showing."""
    schema = set(ACTION_SCHEMA["properties"]["actions"]["items"]["properties"])
    # The headline, not a detail: `summary` is printed as the sentence and `type` as the
    # act, both above the field list.
    assert schema - {"summary", "type"} == {key for key, _label in FIELDS}


def test_an_unset_field_is_not_read_back(conn):
    """The schema requires every property, so most of them arrive as null on any given
    action. Printing *Doctrine: none* eleven times is not a read-back."""
    read = read_back(conn, WORLD, [{
        "type": "raise_units", "summary": "Raise two infantry divisions",
        "unit_kind": "infantry", "quantity": 2, "tone": "neutral",
        "target_nation": None, "from_region": None, "to_region": None,
        "project": None, "gambit_kind": None, "doctrine": None,
        "ceasefire": False, "terms": [],
    }])
    assert [label for label, _value in read[0]["details"]] == [
        "How many", "Formation", "Tone"]


def test_an_id_with_no_row_behind_it_is_shown_as_it_came(conn):
    """A name that cannot be resolved is printed rather than dropped: an order naming a
    province that is not there is exactly the misreading this screen is for."""
    read = read_back(conn, WORLD, [{"type": "move_units", "summary": "Go",
                                    "to_region": "r_nowhere"}])
    assert dict(read[0]["details"])["To"] == "r_nowhere"


def test_a_summary_the_parser_left_empty_falls_back_to_the_act(conn):
    read = read_back(conn, WORLD, [{"type": "sue_for_peace", "summary": ""}])
    assert read[0]["summary"] == "sue for peace"


def test_the_batch_is_read_in_a_bounded_number_of_queries(conn):
    """Six actions naming a dozen places is two statements, not twelve round trips."""
    actions = [
        {"type": "move_units", "summary": f"march {i}",
         "from_region": "r_north", "to_region": "r_west", "target_nation": "karthos"}
        for i in range(6)
    ]
    counted = _CountingConn(conn)
    read_back(counted, WORLD, actions)
    assert counted.queries == 2


def test_the_panel_has_no_opinion_about_whether_an_order_is_legal(conn):
    """Legality is the workbench's question. A panel that answered it too would be the
    second copy of the rules those tests exist to prevent."""
    import inspect

    import atlas.ui.understanding as module

    source = inspect.getsource(module)
    for forbidden in ("review_orders", "_requirements_met", "available_to", "may_write"):
        assert forbidden not in source
    # And it reads one back without complaint: sabotaging yourself is refused by the
    # engine and is still what the player said.
    read = read_back(conn, WORLD, [{"type": "gambit", "summary": "Sabotage ourselves",
                                    "gambit_kind": "sabotage", "target_nation": "aurelia"}])
    assert read[0]["details"]


# -- the screen ---------------------------------------------------------------


def test_the_panel_lays_out_every_action_and_every_refusal(qapp, conn, parsed):
    panel = UnderstoodOverlay(DARK)
    panel.show_reading(conn, WORLD, wrote="march west and sound out Karthos",
                       parsed=parsed)
    assert len(panel.findChildren(_ActionBlock)) == 2
    printed = [child.text() for child in panel.findChildren(type(panel._heading))]
    assert any("no naval formation" in text for text in printed)
    assert any("Sink their fleet" in text for text in printed)


def test_the_players_own_words_are_on_the_same_screen(qapp, conn, parsed):
    """The comparison is between two sentences. Putting one of them behind the panel is
    not putting it on the screen."""
    panel = UnderstoodOverlay(DARK)
    panel.show_reading(conn, WORLD, wrote="march west", parsed=parsed)
    assert "march west" in panel._wrote.text()


def test_confirming_is_the_only_thing_that_says_yes(qapp, conn, parsed):
    panel = UnderstoodOverlay(DARK)
    panel.show_reading(conn, WORLD, wrote="x", parsed=parsed)
    said: list[str] = []
    panel.confirmed.connect(lambda: said.append("yes"))
    panel.reworded.connect(lambda: said.append("no"))
    panel._accept()
    assert said == ["yes"]


def test_escape_means_the_same_as_re_wording(qapp, conn, parsed):
    """Both leave by the safe door, and the rail has to be told either way."""
    panel = UnderstoodOverlay(DARK)
    panel.show_reading(conn, WORLD, wrote="x", parsed=parsed)
    panel.open()
    said: list[str] = []
    panel.confirmed.connect(lambda: said.append("yes"))
    panel.reworded.connect(lambda: said.append("no"))
    panel._maybe_close()
    assert said == ["no"]


def test_an_order_that_produced_nothing_gets_the_whole_screen(qapp, conn):
    """The gap this exists for: it used to be three words in a rail nobody was reading."""
    panel = UnderstoodOverlay(DARK)
    panel.show_reading(conn, WORLD, wrote="ponder the situation",
                       parsed=ParsedOrders(rejected=(
                           {"order": "ponder", "reason": "That is not an order."},)))
    assert panel.findChildren(_ActionBlock) == []
    assert not panel._go.isVisibleTo(panel)
    printed = [child.text() for child in panel.findChildren(type(panel._heading))]
    assert any("Nothing in that could be turned into an order" in text for text in printed)


def test_reading_an_order_back_changes_nothing(qapp, conn, parsed):
    before = fingerprint(conn, WORLD)
    panel = UnderstoodOverlay(DARK)
    panel.show_reading(conn, WORLD, wrote="x", parsed=parsed)
    panel._accept()
    panel.show_reading(conn, WORLD, wrote="x", parsed=parsed)
    panel._reword()
    assert fingerprint(conn, WORLD) == before


def test_the_read_back_offers_two_words_rather_than_two_sentences(qapp, conn, parsed):
    """The confirm control read ``YES, THAT IS WHAT I MEA``.

    Both controls were sentences set in the button face, uppercased at 0.12 tracking, in a
    row that has about a third of a capped panel each. What a control here has to say is
    which of the two doors it is; the sentence was the panel's job and the panel already
    says it, twice. So the labels are verbs -- and this is the assertion that they stay
    short enough for the chip to draw the whole of one.

    This is the labels, and only the labels. **It is not the whole of what was wrong** --
    the panel's content was 86 px wider than the sheet it is drawn on, so the confirm
    control was pushed past the right-hand edge whatever it said. That half is asserted by
    ``test_nothing_in_the_read_back_is_scrolled_off_its_own_sheet``, and it is worth
    keeping the two apart: short labels are a decision about wording and the overflow was
    a defect, and a test that mixed them would go green for the wrong reason.
    """
    from PySide6.QtGui import QFontMetrics

    from atlas.ui.theme import TYPE

    panel = UnderstoodOverlay(DARK)
    panel.show_reading(conn, WORLD, wrote="march west", parsed=parsed)
    panel.resize(1280, 720)
    for _ in range(8):
        QApplication.processEvents()

    assert panel._go._label.text() == ISSUE == "Issue orders"
    assert panel._back._label.text() == REVISE == "Revise"

    for chip in (panel._go, panel._back):
        label = chip._label
        text = label.text().upper() if TYPE[label._style_key].upper else label.text()
        drawn = QFontMetrics(label.font()).horizontalAdvance(text)
        assert drawn <= label.sizeHint().width() + 1, (
            f"{label.text()} needs {drawn}px and the label offers "
            f"{label.sizeHint().width()}px")
        # And the chip is wide enough to hold the label plus its own padding, which is
        # the containment the sentence broke.
        assert chip.sizeHint().width() >= drawn, label.text()


@pytest.mark.parametrize("width,height",
                         [(1280, 720), (1366, 768), (1536, 864), (1600, 900), (1920, 1080)],
                         ids=lambda v: str(v))
@pytest.mark.parametrize("scale", [scale for scale, _label in SCALES], ids=lambda v: str(v))
def test_both_controls_fit_unclipped_at_every_viewport(qapp, conn, parsed, width, height,
                                                       scale):
    """Neither control may be drawn narrower than the text in it, anywhere it can be seen.

    Across the text scale as well as the viewport, because that is where the margin is
    thinnest: *Largest* is 1.6x, and 1.6 times a sentence is where a row of two controls
    stops fitting long before the panel does. A `Chip` label does not elide -- it is a
    plain `QLabel` -- so when the row is over-full Qt shrinks it below its hint and the
    text is simply cut, silently, with nothing anywhere reporting a problem. That silence
    is why this is asserted rather than looked at.
    """
    from PySide6.QtGui import QFontMetrics

    from atlas.ui.theme import TYPE
    from atlas.ui.widgets import set_text_scale

    set_text_scale(scale)
    try:
        panel = _opened(conn, parsed, width, height)
        lost = []
        for chip in (panel._go, panel._back):
            label = chip._label
            text = label.text().upper() if TYPE[label._style_key].upper else label.text()
            needs = QFontMetrics(label.font()).horizontalAdvance(text)
            if needs > label.width() + 1:
                lost.append(f"{label.text()} needs {needs}px, has {label.width()}px")
        panel.close_overlay()
    finally:
        set_text_scale(1.0)
    assert lost == [], f"clipped at {width}x{height} at {scale}x text: {lost}"


def _opened(conn, parsed, width: int, height: int) -> UnderstoodOverlay:
    """The panel, laid out on a host of the given size, as the window lays it out.

    Parented to a real host rather than free-floating: the panel caps itself at whatever
    it is drawn on -- see `Overlay._fit_to_window` -- and an unparented one caps itself at
    nothing, which is a width the program never gives it.
    """
    from PySide6.QtWidgets import QWidget

    host = QWidget()
    host.resize(width, height)
    host.show()
    panel = UnderstoodOverlay(DARK, host)
    panel.show_reading(conn, WORLD, wrote="march west into Kharkiv", parsed=parsed)
    panel.open()
    for _ in range(12):
        QApplication.processEvents()
    panel._host_keepalive = host  # noqa: SLF001 -- or Qt collects it under the panel
    return panel


@pytest.mark.parametrize("width,height",
                         [(1280, 720), (1366, 768), (1536, 864), (1600, 900), (1920, 1080)],
                         ids=lambda v: str(v))
def test_nothing_in_the_read_back_is_scrolled_off_its_own_sheet(qapp, conn, parsed,
                                                                width, height):
    """The other half of the clipped confirm control, and the half that actually caused it.

    **The labels were never what set the width.** The heading asked for 667 px beside a
    count that asks for 225, which with the spacing and the body's margins is 972 px of
    content laid out in an 886 px sheet -- so every child of the panel sat 86 px too far
    right and the one that fell off the edge was the last of them, which is the confirm
    control because it is right-aligned. Shortening its label moved nothing: measured with
    *Issue orders* in place, ``scratchpad/readback_geometry.py`` still counted the same 60
    overflows at three viewports.

    So the heading gives way, and this is the assertion that it keeps giving way. Stated as
    *nothing is scrolled out of view* rather than as a width, because that is the property
    a player has: a horizontal scrollbar whose maximum is zero is a panel with nothing
    hidden past its edge.
    """
    panel = _opened(conn, parsed, width, height)
    sheet = panel._sheet
    hidden = sheet.horizontalScrollBar().maximum()
    edges = {
        "confirm": panel._go.mapTo(panel._sheet_body, panel._go.rect().topRight()).x(),
        "cancel": panel._back.mapTo(panel._sheet_body, panel._back.rect().topRight()).x(),
    }
    room = sheet.viewport().width()
    panel.close_overlay()

    assert hidden == 0, (
        f"{hidden}px of the panel is off the right-hand edge at {width}x{height}, "
        "with no scrollbar a player would think to drag")
    for what, right in edges.items():
        assert right <= room + 1, (
            f"the {what} control ends at {right}px in a {room}px sheet "
            f"at {width}x{height}")


def test_nothing_still_offers_the_way_back_and_only_the_way_back(qapp, conn):
    """An *Issue orders* over an empty list is a button that does nothing, so it goes --
    and what is left still says what it does rather than saying *no* to a question that
    was never asked."""
    panel = UnderstoodOverlay(DARK)
    panel.show_reading(conn, WORLD, wrote="ponder", parsed=ParsedOrders(rejected=(
        {"order": "ponder", "reason": "That is not an order."},)))
    assert not panel._go.isVisibleTo(panel)
    assert panel._back._label.text() == REVISE


def test_both_themes_apply_without_throwing(qapp, conn, parsed):
    panel = UnderstoodOverlay(DARK)
    panel.show_reading(conn, WORLD, wrote="x", parsed=parsed)
    panel.apply_theme(LIGHT)
    panel.apply_theme(DARK)


# -- the window ---------------------------------------------------------------


def test_a_parse_is_not_the_draft(qapp):
    """**The whole mechanism.** `_on_orders_parsed` must not assign the draft; only the
    confirm path may, so an order nobody agreed to cannot be queued."""
    import inspect

    from atlas.ui.main_window import AtlasWindow

    parsed_source = inspect.getsource(AtlasWindow._on_orders_parsed)
    assert "self._pending_orders" not in parsed_source
    assert "self._understood.open()" in parsed_source

    queued = inspect.getsource(AtlasWindow._queue_understood_orders)
    assert "self._pending_orders = list(parsed.actions)" in queued
    assert "self._keep_orders()" in queued


def test_cancelling_leaves_the_words_in_the_box(qapp):
    """Re-wording is for a sentence that is nearly right. Emptying the box would make the
    safe answer the expensive one."""
    import inspect

    from atlas.ui.main_window import AtlasWindow

    source = inspect.getsource(AtlasWindow._reword_orders)
    assert "clear_orders" not in source
    assert "self._reading = None" in source


def test_a_parser_that_could_not_be_reached_gets_no_read_back(qapp):
    """There is no reading to confirm, so there is nothing to show."""
    import inspect

    from atlas.ui.main_window import AtlasWindow

    source = inspect.getsource(AtlasWindow._on_orders_parsed)
    head = source.split("if not parsed.actions")[0]
    assert "if not parsed.ok:" in head
    assert "return" in head


def test_the_window_wires_both_answers(qapp):
    import inspect

    from atlas.ui.main_window import AtlasWindow

    source = inspect.getsource(AtlasWindow)
    assert "self._understood.confirmed.connect(self._queue_understood_orders)" in source
    assert "self._understood.reworded.connect(self._reword_orders)" in source


def _name(conn: sqlite3.Connection, table: str, ident: str) -> str:
    return str(conn.execute(
        f"SELECT name FROM {table} WHERE world_id = ? AND id = ?",  # noqa: S608
        (WORLD, ident)).fetchone()["name"])


class _CountingConn:
    def __init__(self, inner: sqlite3.Connection) -> None:
        self._inner = inner
        self.queries = 0

    def execute(self, *args, **kwargs):
        self.queries += 1
        return self._inner.execute(*args, **kwargs)
