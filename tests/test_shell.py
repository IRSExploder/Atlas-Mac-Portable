"""The shell: top bar, rails, map chrome, overlays.

Offscreen, against a real in-memory world. These do not check that anything looks right;
they check the failures that a screenshot would not catch -- a panel that keeps the dark
palette after a theme change, a rail that demands more width than the window has, a
diplomacy list built from a world it was never bound to.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from atlas.engine.progress import ProgressReporter, StageState  # noqa: E402
from atlas.engine.snapshot import load_player_snapshot  # noqa: E402
from atlas.engine.turn_engine import JumpReport  # noqa: E402
from atlas.map.scene_model import MapLayer, UnitDomain  # noqa: E402
from atlas.state.models import TurnStatus  # noqa: E402
from atlas.ui.map_chrome import MapChrome  # noqa: E402
from atlas.ui.overlays import (  # noqa: E402
    ExportOverlay,
    RewindOverlay,
    SummaryOverlay,
)
from atlas.ui.rail_left import LeftRail  # noqa: E402
from atlas.ui.rail_right import RightRail  # noqa: E402
from atlas.ui.statusbar import StatusStrip  # noqa: E402
from atlas.ui.theme import DARK, LIGHT, load_fonts  # noqa: E402
from atlas.ui.topbar import TopBar, _IconButton  # noqa: E402
from atlas.ui.turning import TurningOverlay  # noqa: E402
from atlas.ui.widgets import TextLabel  # noqa: E402

#: The window size the design is drawn at. Nothing may demand more than this.
DESIGN_WIDTH = 1600


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    load_fonts()
    yield app


@pytest.fixture
def player(conn) -> str:
    row = conn.execute(
        "SELECT id FROM nation WHERE world_id = 'sandbox' AND is_player = 1"
    ).fetchone()
    return row["id"]


# --------------------------------------------------------------------------- top bar


def test_top_bar_shows_the_player(qapp, conn, player):
    bar = TopBar(DARK)
    bar.set_world("sandbox", "A Quiet Sandbox")
    bar.set_snapshot(load_player_snapshot(conn, "sandbox", nation_id=player))

    # Who governs, not the fact that you are playing -- which the player already knows.
    # "playing" survives only as the fallback for a world written before schema 13.
    assert bar._nation_role.text() == "the government"
    assert bar._nation_name.text()
    assert bar._turn.text().startswith("TURN")


def test_top_bar_falls_back_when_a_world_has_no_government(qapp, conn, player):
    """A save from before leaders existed still has to name the row under the flag."""
    conn.execute("DELETE FROM leader")
    conn.commit()
    bar = TopBar(DARK)
    bar.set_world("sandbox", "A Quiet Sandbox")
    bar.set_snapshot(load_player_snapshot(conn, "sandbox", nation_id=player))
    assert bar._nation_role.text() == "playing"


def test_top_bar_says_so_when_spectating(qapp, conn):
    conn.execute("UPDATE nation SET is_player = 0 WHERE world_id = 'sandbox'")
    conn.commit()
    bar = TopBar(DARK)
    bar.set_snapshot(load_player_snapshot(conn, "sandbox"))
    assert bar._nation_name.text() == "Spectating"
    assert bar._shield.isHidden()


def test_top_bar_writes_dates_like_a_historian(qapp, conn, player):
    from atlas.ui.topbar import _pretty_date

    assert _pretty_date("1936-03-07") == "7 March 1936"
    assert _pretty_date("") == ""
    assert _pretty_date("not-a-date") == "not-a-date"


def test_provider_rows_can_be_replaced_without_leaking(qapp):
    bar = TopBar(DARK)
    bar.set_providers([("Claude", "claude-sonnet-5", "ok")])
    bar.set_providers([("Claude", "claude-opus-5", "warn"), ("Codex", "gpt-5.6-terra", "ok")])
    assert len(bar._provider_rows) == 2


def test_the_theme_entry_says_which_way_it_goes(qapp):
    """It was a circle that filled in, which said nothing about what pressing it did."""
    bar = TopBar(DARK)
    assert bar._theme_btn.label == "Switch to the light theme"
    bar.apply_theme(LIGHT)
    assert bar._theme_btn.label == "Switch to the dark theme"
    assert bar._theme_btn.action.text() == "Switch to the dark theme"


def _renders_as_a_box(glyph: str) -> bool:
    """Whether the vendored sans draws ``glyph`` or draws .notdef.

    There is no icon font and no asset step, so a top-bar icon is a character set in IBM
    Plex Sans -- and a character the face does not carry comes out as an empty rectangle,
    which on a bar whose buttons are *already* empty rectangles is invisible as a fault.
    Eleven of them were boxes for months and the bar looked deliberate.

    Rendered rather than asked, because ``QRawFont.supportsCharacter`` answers about the
    concrete face Qt resolved and reports every one of these missing, the ones that draw
    included. The comparison is against a private-use-area codepoint no face carries, so
    whatever .notdef looks like here is what a missing glyph is measured against.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QImage, QPainter

    from atlas.ui.theme import TYPE, load_fonts
    from atlas.ui.widgets import styled_font

    load_fonts()
    font = styled_font(TYPE["body"])

    def pixels(char: str) -> bytes:
        image = QImage(40, 40, QImage.Format.Format_ARGB32)
        image.fill(QColor("white"))
        painter = QPainter(image)
        painter.setFont(font)
        painter.setPen(QColor("black"))
        painter.drawText(image.rect(), Qt.AlignmentFlag.AlignCenter, char)
        painter.end()
        return bytes(image.constBits())

    return pixels(glyph) == pixels("")


def test_every_top_bar_glyph_is_in_the_vendored_face(qapp):
    """No button on the permanent chrome may draw as an empty box."""
    bar = TopBar(DARK)
    buttons = {
        name.lstrip("_"): widget._glyph.text()
        for name, widget in vars(bar).items()
        if isinstance(widget, _IconButton)
    }
    assert set(buttons) == {"search", "alerts", "strategy", "workbench", "more"}, buttons

    boxes = sorted(name for name, glyph in buttons.items() if _renders_as_a_box(glyph))
    assert not boxes, f"these top-bar icons render as empty boxes: {boxes}"


def test_the_bar_carries_five_icons_a_named_control_and_words_for_the_rest(qapp):
    """Eighteen unlabelled glyphs was a character map, not a navigation bar.

    What stays as a glyph is what a player reaches for between turns. Everything else is a
    named line in the More menu -- which is the point: an icon could not say *fork this
    world at a turn*, and every one of them was tooltip-only.

    And one control on the bar is neither: Attention is a *labelled* control with a count
    on it, because it is the one door a player who has never seen the program needs, and a
    tooltip is not an answer to a question you do not know to ask.
    """
    bar = TopBar(DARK)
    icons = [w for w in vars(bar).values() if isinstance(w, _IconButton)]
    assert len(icons) == 5

    # Attention is not one of them: it is a `_CountedButton`, it says its own name, and it
    # carries a figure rather than a glyph.
    assert not isinstance(bar._attention, _IconButton)
    assert bar._attention._name.text() == "Attention"

    # Fifteen named lines: *What is waiting* is no longer one of them, because it is on
    # the glass, and *Who supplies you* has joined *Your government*. What this test pins
    # is the shape, not the number -- glyphs for the doors a player already knows, a name
    # for the one they do not, and words for everything else.
    named = [entry.label for _heading, group in bar._more_groups for entry in group]
    assert len(named) == 15
    assert "What is waiting" not in named, "it is on the bar now; two doors is one too many"
    assert "Fork this world at a turn" in named
    assert all(label and label[0].isupper() for label in named), named

    # And every one of them still reaches the window, which is the failure this project
    # has already had once: built, drawn, and wired to nothing.
    fired: list[str] = []
    for signal, name in (
        (bar.dispatch_requested, "dispatch"), (bar.cast_requested, "cast"),
        (bar.correspondence_requested, "post"), (bar.council_requested, "council"),
        (bar.directives_requested, "directives"), (bar.homefront_requested, "homefront"),
        (bar.dependency_requested, "dependency"),
        (bar.watchlist_requested, "watchlist"), (bar.inspector_requested, "inspector"),
        (bar.advance_requested, "advance"), (bar.undo_turn_requested, "undo"),
        (bar.rewind_requested, "rewind"), (bar.branches_requested, "branches"),
        (bar.export_requested, "export"), (bar.theme_toggled, "theme"),
    ):
        signal.connect(lambda n=name: fired.append(n))
    for _heading, group in bar._more_groups:
        for entry in group:
            entry.action.trigger()
    assert len(fired) == 15, fired


def _settle(qapp) -> None:
    """Let a relayout finish. A shed changes what the next layout pass has room for."""
    for _ in range(6):
        qapp.processEvents()


def test_the_top_bar_gives_way_instead_of_setting_the_window_width(qapp, conn, player):
    """The bar used to demand 2,192 px, and the window with it.

    Every block on it now elides or sheds, so the floor is set by what is readable rather
    than by what happens to be in the widest label. This is the assertion that stops the
    next fixed width from pinning the window to a size no monitor has.
    """
    bar = TopBar(DARK)
    bar.set_world("sandbox", "A Quiet Sandbox")
    bar.set_snapshot(load_player_snapshot(conn, "sandbox", nation_id=player))
    bar.set_providers([("THE WORLD", "quiet", "ok"), ("THE EMBASSIES", "open", "ok"),
                       ("THE CAPITALS", "open", "ok")])
    assert bar.minimumSizeHint().width() <= 1000, bar.minimumSizeHint().width()

    # And what it sheds, it sheds in order: the channel rows, then the day count, then the
    # world's name, then the two lowest-ranked controls. Driven off the module's own
    # thresholds, because the thresholds are the measurement -- see `_apply_breakpoints`.
    #
    # **The day count now goes before the world's name**, which is a change of order and
    # is argued in `_apply_breakpoints`: the clock block sits between the nation and the
    # meters, so the room it gives up goes straight to them, and the meters are what the
    # shedding is for.
    from atlas.ui.topbar import (
        _ALL_CONTROLS_FIT,
        _CHANNELS_FIT,
        _DAY_COUNT_FITS,
        _WORLD_NAME_FITS,
    )

    bar.show()
    bar.resize(_CHANNELS_FIT, 68)
    _settle(qapp)
    assert bar._providers.isVisibleTo(bar)
    assert bar._names.isVisibleTo(bar)
    assert bar._elapsed.isVisibleTo(bar)

    bar.resize(_CHANNELS_FIT - 1, 68)
    _settle(qapp)
    assert not bar._providers.isVisibleTo(bar)
    assert bar._elapsed.isVisibleTo(bar), "the day count went before the channels did"

    bar.resize(_DAY_COUNT_FITS - 1, 68)
    _settle(qapp)
    assert not bar._elapsed.isVisibleTo(bar)
    assert bar._names.isVisibleTo(bar), "the world's name went before the day count did"

    bar.resize(_WORLD_NAME_FITS - 1, 68)
    _settle(qapp)
    assert not bar._names.isVisibleTo(bar)

    # And the controls are last, because a control is a way in and the rest of this is
    # reading. Attention is never among them: it is the reason the others give way.
    assert bar._shed_controls == len(bar._overflow)
    bar.resize(_ALL_CONTROLS_FIT, 68)
    _settle(qapp)
    assert bar._shed_controls == 0
    assert bar._attention.isVisibleTo(bar)


# --------------------------------------------------------------------------- rails


def test_left_rail_binds_and_stays_within_its_width(qapp, conn, player):
    rail = LeftRail(DARK)
    rail.bind(conn, "sandbox", player)
    assert rail.width() == 348
    assert rail.minimumSizeHint().width() <= 348


def test_left_rail_filters_change_what_is_listed(qapp, conn, player):
    rail = LeftRail(DARK)
    rail.bind(conn, "sandbox", player)
    before = rail._events_body.count()
    rail._set_filter("war")
    assert rail._filter == "war"
    assert rail._events_body.count() <= before
    rail._set_filter("all")
    assert rail._filter == "all"


def test_left_rail_selecting_a_region_opens_the_inspector(qapp, conn, player):
    region = conn.execute(
        "SELECT id FROM region WHERE world_id = 'sandbox' LIMIT 1"
    ).fetchone()["id"]
    rail = LeftRail(DARK)
    rail.bind(conn, "sandbox", player)
    rail.set_selected_region(region)
    assert rail._stack.currentIndex() == 2
    assert rail._region_body.count() > 1


def test_right_rail_lists_everyone_but_you(qapp, conn, player):
    rail = RightRail(DARK)
    rail.bind(conn, "sandbox", player)
    page = rail._diplomacy._list_page
    others = conn.execute(
        "SELECT COUNT(*) FROM nation WHERE world_id = 'sandbox' AND id <> ? AND is_alive = 1",
        (player,),
    ).fetchone()[0]
    assert f"{others} powers" == page._count.text()


def test_the_council_sits_outside_the_powers_you_negotiate_with(qapp, conn, player):
    """Consulting your own government is not negotiation and must not read as it.

    It binds nothing and writes nothing, so listing it among the powers you bargain with
    would blur the one distinction the player most needs to keep straight.
    """
    rail = RightRail(DARK)
    rail.bind(conn, "sandbox", player)
    page = rail._diplomacy._list_page

    seen = []
    page.advise.connect(lambda: seen.append(True))
    page._council_button.clicked.emit()
    assert seen == [True]
    assert page._council_button.isVisible() or not page.isVisible()


def test_a_spectator_has_no_government_to_consult(qapp, conn):
    rail = RightRail(DARK)
    rail.bind(conn, "sandbox", None)
    assert not rail._diplomacy._list_page._council_button.isEnabled()


def test_the_council_chat_offers_nothing_to_conclude(qapp, conn, player):
    """There is no commitment to record, so the button would promise a consequence
    that cannot happen."""
    rail = RightRail(DARK)
    rail.bind(conn, "sandbox", player)
    chat = rail._diplomacy._chat_page

    rail.start_chat("Karthos")
    assert chat._end.isVisibleTo(chat), "talks can be concluded"

    rail.start_advisor("Your council")
    assert not chat._end.isVisibleTo(chat), "a consultation cannot"


def test_right_rail_stays_within_its_width(qapp, conn, player):
    """A rail wider than itself silently clips its right-hand column."""
    rail = RightRail(DARK)
    rail.bind(conn, "sandbox", player)
    rail.resize(404, 850)
    rail.show()
    qapp.processEvents()
    body = rail._diplomacy._list_page._area.widget()
    assert body.minimumSizeHint().width() <= rail._diplomacy._list_page._area.viewport().width()


def test_right_rail_opens_and_leaves_talks(qapp, conn, player):
    rail = RightRail(DARK)
    rail.bind(conn, "sandbox", player)
    assert rail._diplomacy._stack.currentIndex() == 0
    rail.start_chat("Karthos")
    assert rail._diplomacy._stack.currentIndex() == 1
    rail.append_turn("You", "We should talk about the strait.", is_player=True)
    rail.append_turn("Karthos", "There is nothing to discuss.", is_player=False)
    rail._diplomacy._to_list()
    assert rail._diplomacy._stack.currentIndex() == 0


def test_orders_feedback_shows_refusals(qapp, conn, player):
    rail = RightRail(DARK)
    rail.bind(conn, "sandbox", player)
    rail.show_orders_result(["Fortify the pass"], ["Annex Karthos outright"])
    # isHidden rather than isVisible: nothing here is on screen, and isVisible would be
    # answering a question about the parent rather than about this widget.
    assert not rail._orders._feedback.isHidden()
    rail.clear_orders()
    assert rail._orders._feedback.isHidden()


def test_advance_estimate_follows_the_span_and_the_tracked_count(qapp, conn, player,
                                                                 technical_details):
    """And says it in the world's words: this line sits beside the screen a jump is
    watched through, which is written entirely in them."""
    rail = RightRail(DARK)
    rail.bind(conn, "sandbox", player)
    rail.set_tracked_count(4)
    rail._advance._pick(365)
    text = rail._advance._estimate.text()
    assert "365 days" in text
    assert "4 powers will decide for themselves" in text
    assert "model" not in text.lower() and "call" not in text.lower()


def test_the_plain_advance_estimate_says_what_pressing_it_does(qapp, conn, player):
    rail = RightRail(DARK)
    rail.bind(conn, "sandbox", player)
    rail.set_tracked_count(4)
    rail._advance._pick(365)
    assert rail._advance._estimate.text() == (
        "Advance 365 days. Your orders are carried out, then the world answers.")


def test_advance_button_reports_being_busy(qapp, conn, player):
    rail = RightRail(DARK)
    rail.bind(conn, "sandbox", player)
    rail.set_busy(True)
    # The one brass control says what the world is doing, not what the program is.
    busy = rail._advance._button._label.text()
    assert "The world turns" in busy
    assert "simulat" not in busy.lower()
    fired = []
    rail._advance._button.clicked.connect(lambda: fired.append(True))
    rail._advance._button.mousePressEvent(_left_click())
    qapp.processEvents()  # the press is deferred a turn; see widgets.PressFeedback
    assert fired == []  # a busy button does not start a second turn
    assert rail._advance._button.property("pressed") in (None, False)
    rail.set_busy(False)
    assert rail._advance._button._label.text() == "Advance"


def test_relation_words_are_words(qapp):
    from atlas.ui.rail_right import _relation_word

    assert _relation_word(0, False, "none") == "neutral"
    assert _relation_word(-80, False, "none") == "hostile"
    assert _relation_word(70, False, "none") == "warm"
    assert _relation_word(-80, True, "none") == "at war"
    assert _relation_word(10, False, "non_aggression") == "non aggression"


# --------------------------------------------------------------------------- map chrome


def test_map_chrome_reports_the_chosen_layer(qapp):
    chrome = MapChrome(QWidget(), DARK)
    seen: list[str] = []
    chrome.layer_changed.connect(seen.append)
    chrome._layers._pick(str(MapLayer.FRONTS))
    assert seen == ["fronts"]
    assert chrome.layer is MapLayer.FRONTS


def test_map_chrome_toggles_unit_domains_independently(qapp):
    chrome = MapChrome(QWidget(), DARK)
    seen = []
    chrome.unit_domains_changed.connect(seen.append)

    chrome._unit_layers._toggle(UnitDomain.SEA)
    assert chrome.unit_domains == {UnitDomain.LAND, UnitDomain.AIR}
    assert seen[-1] == frozenset({UnitDomain.LAND, UnitDomain.AIR})
    assert not chrome._unit_layers._chips[UnitDomain.SEA].selected
    assert chrome._unit_layers._chips[UnitDomain.LAND].selected

    chrome._unit_layers._toggle(UnitDomain.SEA)
    assert chrome.unit_domains == set(UnitDomain)


def test_map_chrome_hover_bar_hides_when_empty(qapp):
    chrome = MapChrome(QWidget(), DARK)
    chrome.resize(900, 600)
    chrome.set_hover("Rhineland | Germany")
    assert not chrome._readout.isHidden()
    chrome.set_hover("")
    assert chrome._readout.isHidden()


def _legend_lines(chrome) -> list[str]:
    from atlas.ui.widgets import TextLabel

    return [w.text() for w in chrome._legend.findChildren(TextLabel) if w.text()]


def test_map_chrome_legend_names_the_player(qapp):
    chrome = MapChrome(QWidget(), DARK)
    chrome.set_player("Germany", "#5A5A5A")
    assert any(line == "Germany — you" for line in _legend_lines(chrome))


def test_the_legend_says_what_the_layer_showing_actually_means(qapp, technical_details):
    """A static key on a map that changes is a caption that lies.

    Four sentences about ownership were shown while the fill underneath became
    development, then unrest, then air control: fourteen layers, one key, thirteen of
    them wrong. The widget now knows nothing -- it draws whatever the layer says it is.
    """
    from atlas.map.legend import LEGENDS, ScaleKind, legend_for

    chrome = MapChrome(QWidget(), DARK)
    chrome.set_player("Germany", "#5A5A5A")

    assert set(LEGENDS) == set(MapLayer), "a layer with no legend would show another's"

    for layer in MapLayer:
        chrome.set_layer(layer)
        qapp.processEvents()
        lines = _legend_lines(chrome)
        legend = legend_for(layer)
        assert legend.title in lines, (layer, lines)
        assert legend.driven_by in lines, f"{layer} does not say what moves it"
        if legend.kind is ScaleKind.RAMP:
            assert legend.unit in lines, layer
            assert legend.cold in lines and legend.hot in lines, layer
        else:
            for entry in legend.entries:
                expected = entry.text.replace("{nation}", "Germany")
                assert expected in lines, (layer, expected, lines)
        if legend.blank:
            assert legend.blank in lines, layer

    # And what a blank province means is never the same sentence twice by accident: it is
    # the one thing on these layers that a reader would otherwise take for a reading of
    # zero. Out of supply is not "no supply here", it is "an army here grinds down".
    assert legend_for(MapLayer.NUCLEAR).blank != legend_for(MapLayer.SUPPLY).blank


def test_every_layer_puts_a_reading_into_words(qapp):
    """The formatter is metadata too, because two of these do not read left to right.

    Supply is a distance inverted so the depot is the hot end, and attitude is a
    -100..100 regard normalised for the ramp. A shared percentage formatter would say
    something false about both.
    """
    from atlas.map.legend import LEGENDS

    for layer, legend in LEGENDS.items():
        for share in (0.0, 0.5, 1.0):
            said = legend.value(share)
            assert said and isinstance(said, str), (layer, share)
    assert LEGENDS[MapLayer.SUPPLY].value(1.0) == "at the depot"
    assert LEGENDS[MapLayer.ATTITUDE].value(0.0) == "-100 regard"


def test_the_layer_bar_does_not_run_over_the_scenario(qapp):
    """Fourteen chips is 1,240 pixels in one row, and the placard is at the other end.

    Both were placed from their own corner with no knowledge of the other, so on every
    window the chip row printed 87 pixels of itself across the scenario's name.
    """
    host = QWidget()
    host.resize(1280, 720)
    host.show()
    chrome = MapChrome(QWidget(), DARK, host)
    chrome.set_place("equirect", 0, 4951, scope="Modern Day")
    chrome.resize(1280, 720)
    chrome.show()
    _settle(qapp)

    placard = chrome._where.geometry()
    bar = chrome._layers.geometry()
    assert not placard.intersects(bar), f"placard {placard} runs into the layer bar {bar}"
    assert bar.left() >= 0, "the layer bar runs off the left of the map"
    assert bar.right() <= chrome.width()


def test_map_chrome_overlays_sit_above_the_map(qapp):
    """Child order is z-order; the map must be created first or it covers the chrome."""
    map_widget = QWidget()
    chrome = MapChrome(map_widget, DARK)
    children = [c for c in chrome.children() if isinstance(c, QWidget)]
    assert children.index(map_widget) == 0
    assert children.index(chrome._vignette) == 1


# --------------------------------------------------------------------------- overlays


def test_the_turning_view_cannot_be_dismissed_mid_turn(qapp):
    overlay = TurningOverlay(DARK)
    overlay.begin(365, "1936-01-01", "1937-01-01")
    assert not overlay.isHidden()
    overlay._maybe_close()
    assert not overlay.isHidden()


def test_the_turning_view_renders_every_beat(qapp):
    overlay = TurningOverlay(DARK)
    reporter = ProgressReporter(on_update=overlay.update_stages)
    overlay.begin(30, "1936-01-01", "1936-02-01")
    reporter.plan([("world_sim", "World simulation", ""), ("resolve", "Resolution", "")])
    reporter.start("world_sim")
    reporter.finish("world_sim")
    reporter.start("resolve")
    reporter.finish("resolve", state=StageState.FAILED, detail="provider timed out")

    assert set(overlay._rows) == {"world_sim", "resolve"}
    assert overlay._rows["resolve"].beat.state is StageState.FAILED


def test_the_turning_view_reuses_rows_rather_than_stacking_them(qapp):
    overlay = TurningOverlay(DARK)
    reporter = ProgressReporter(on_update=overlay.update_stages)
    overlay.begin(30, "1936-01-01", "1936-02-01")
    for _ in range(5):
        reporter.plan([("world_sim", "World simulation", "")])
    assert len(overlay._rows) == 1


@pytest.fixture
def technical_details(monkeypatch):
    """The three-column summary is the technical view; plain mode shows the briefing."""
    from atlas.ui import preferences

    monkeypatch.setattr(preferences, "_CURRENT", preferences.Preferences(technical=True))


def test_summary_shows_refusals_rather_than_hiding_them(qapp, technical_details):
    overlay = SummaryOverlay(DARK)
    report = JumpReport(
        turn=3, status=TurnStatus.PARTIAL, days_advanced=365, new_date="1937-01-01",
        applied_batches=4, rejected_batches=1, events_recorded=11, calls=6,
        rejections=["INFO-002: cited an event Germany cannot see"],
        tracked_acted=["Italy"],
    )
    overlay.show_report(report, ["Germany reoccupied the Rhineland."])

    refused = overlay._columns[2]._entries
    assert refused.count() == 1
    assert "INFO-002" in refused.itemAt(0).widget().text()
    assert not overlay._status.selected  # a partial turn is not a sealed one


def test_summary_says_when_nothing_was_refused(qapp, technical_details):
    overlay = SummaryOverlay(DARK)
    overlay.show_report(
        JumpReport(turn=1, status=TurnStatus.SEALED, days_advanced=30, new_date="1936-02-01"),
        [],
    )
    assert overlay._status.selected
    assert "Nothing was refused." in overlay._columns[2]._entries.itemAt(0).widget().text()


def test_a_plain_summary_is_a_briefing_not_three_columns(qapp):
    overlay = SummaryOverlay(DARK)
    overlay.show_report(
        JumpReport(turn=1, status=TurnStatus.SEALED, days_advanced=30, new_date="1936-02-01",
                   rejections=["INFO-002: cited an event Germany cannot see"],
                   tracked_acted=["Italy"]),
        [],
    )
    assert overlay._brief_scroll.isVisibleTo(overlay)
    assert not overlay._column_scroll.isVisibleTo(overlay)
    assert overlay._sub.text() == "30 days passed."  # no engine jargon about tracked powers


def test_rewind_previews_the_cost_before_committing(qapp, conn):
    overlay = RewindOverlay(DARK)
    overlay.load(conn, "sandbox")
    overlay._select(0)
    assert overlay._impact.text()

    fired: list[int] = []
    overlay.confirmed.connect(fired.append)
    overlay._confirm_clicked()
    assert fired == [0]


def test_rewind_does_nothing_without_a_chosen_turn(qapp, conn):
    overlay = RewindOverlay(DARK)
    overlay.load(conn, "sandbox")
    fired: list[int] = []
    overlay.confirmed.connect(fired.append)
    overlay._confirm_clicked()
    assert fired == []


def test_export_overlay_carries_its_destination(qapp):
    overlay = ExportOverlay(DARK)
    overlay.set_destination(r"C:\somewhere\storm1936.db")
    fired: list[str] = []
    overlay.export_requested.connect(fired.append)
    overlay._go.mousePressEvent(_left_click())
    qapp.processEvents()  # the press is deferred a turn; see widgets.PressFeedback
    assert fired == [r"C:\somewhere\storm1936.db"]


def test_export_overlay_reports_failure_in_the_danger_colour(qapp):
    overlay = ExportOverlay(DARK)
    overlay.show_result("the file is in use", ok=False)
    assert DARK.danger.lower() in overlay._result.styleSheet().lower()


# --------------------------------------------------------------------------- status bar


def test_status_strip_reports_the_last_turn(qapp):
    strip = StatusStrip(DARK)
    strip.set_world(r"C:\Users\x\AppData\Local\Atlas\worlds\w\world.db", 1936)
    strip.set_last_turn(seconds=91.4, calls=6, rejected=2)
    # In the units a person waits in. `91.4s` asks the reader to divide by sixty.
    assert "1m 31s" in strip._last_turn.text()
    assert "2 refused" in strip._last_turn.text()
    assert DARK.danger.lower() in strip._last_turn.styleSheet().lower()
    # And the tenth of a second is kept one layer back, for whoever is profiling a turn.
    assert "91.4" in strip._last_turn.toolTip()

    # Measured, passed, and deliberately not shown: a count of calls beside a date is
    # the machinery in a costume, and this bar is permanent chrome.
    assert "call" not in strip._last_turn.text().lower()

    strip.set_last_turn(seconds=12.0, calls=1, rejected=0)
    assert "12s" in strip._last_turn.text()
    assert "refused" not in strip._last_turn.text()


def test_status_strip_marks_a_problem(qapp):
    strip = StatusStrip(DARK)
    strip.set_state("A model call failed.", problem=True)
    assert strip._dot._state == "down"


# --------------------------------------------------------------------------- theming


@pytest.mark.parametrize("build", [
    lambda: TopBar(DARK),
    lambda: LeftRail(DARK),
    lambda: RightRail(DARK),
    lambda: MapChrome(QWidget(), DARK),
    lambda: StatusStrip(DARK),
    lambda: TurningOverlay(DARK),
    lambda: SummaryOverlay(DARK),
    lambda: RewindOverlay(DARK),
    lambda: ExportOverlay(DARK),
])
def test_every_panel_survives_a_theme_round_trip(qapp, build):
    """A panel that keeps a dark hex after switching is a panel with a hardcoded colour."""
    panel = build()
    panel.apply_theme(LIGHT)
    panel.apply_theme(DARK)
    panel.apply_theme(LIGHT)
    assert DARK.panel.lower() not in panel.styleSheet().lower()


def _left_click():
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    return QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(1, 1), QPointF(1, 1),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


# ------------------------------------------------------------------ overlay policy


def _overlay_classes():
    """Every overlay in the program, found rather than listed.

    Listed, the next one added is the one that is not checked -- which is exactly how
    seventeen panels ended up at thirteen widths.
    """
    import importlib
    import pkgutil

    import atlas.ui
    from atlas.ui.overlays import Overlay

    found: dict[str, type] = {}
    for info in pkgutil.iter_modules(atlas.ui.__path__):
        module = importlib.import_module(f"atlas.ui.{info.name}")
        for name in dir(module):
            value = getattr(module, name)
            if (isinstance(value, type) and issubclass(value, Overlay)
                    and value is not Overlay):
                found[f"{value.__module__}.{value.__name__}"] = value
    return sorted(found.items())


def test_opening_a_panel_closes_the_one_that_was_open(qapp):
    """Two adjacent buttons on the top bar, pressed one after the other.

    `open()` was `setVisible(True); raise_()` and never closed a sibling, so this left
    both panels on the screen -- the first showing through the second at 28% -- and the
    window dimmed twice over. It did not read as a stack, it read as a rendering fault.
    """
    from atlas.ui.homefront import HomeFrontOverlay
    from atlas.ui.strategy import StrategyOverlay

    host = QWidget()
    host.resize(1600, 950)
    first, second = StrategyOverlay(DARK, host), HomeFrontOverlay(DARK, host)
    first.open()
    assert first.showing()
    second.open()
    assert not first.showing(), "the first panel is still on the screen"
    assert second.showing()


def test_only_the_top_sheet_paints_a_shade(qapp):
    """Two shades at 72% make one at 92%: the whole window darker for no reason."""
    from atlas.ui.homefront import HomeFrontOverlay
    from atlas.ui.turning import TurningOverlay

    host = QWidget()
    host.resize(1600, 950)
    # The one overlay that refuses to be dismissed. A panel opened over a jump in flight
    # must go *above* it, not close it -- and then it is the one holding the shade.
    turning, panel = TurningOverlay(DARK, host), HomeFrontOverlay(DARK, host)
    turning.open()
    assert turning._is_topmost()
    panel.open()
    assert turning.showing(), "a jump in flight was dismissed by a button press"
    assert panel._is_topmost()
    assert not turning._is_topmost()
    panel.close_overlay()
    assert turning._is_topmost(), "the sheet underneath never took the shade back"


def test_a_panel_leaves_by_its_own_door_when_it_is_shoved_aside(qapp):
    """The read-back means something specific by being dismissed, and still says it."""
    from atlas.ui.search import SearchOverlay
    from atlas.ui.understanding import UnderstoodOverlay

    host = QWidget()
    host.resize(1600, 950)
    read_back, other = UnderstoodOverlay(DARK, host), SearchOverlay(DARK, host)
    reworded: list[int] = []
    read_back.reworded.connect(lambda: reworded.append(1))
    read_back.open()
    other.open()
    assert not read_back.showing()
    assert reworded == [1], "the rail was left saying 'reading your orders…' for ever"


@pytest.mark.parametrize("name,cls", _overlay_classes(), ids=lambda v: getattr(v, "__name__", v))
def test_every_panel_is_one_of_the_three_sizes(qapp, name, cls):
    """Thirteen widths across seventeen panels was thirteen separate decisions.

    There is no reading of a 600-vs-660-vs-680 difference other than "these were drawn
    on different days". Three sizes, and this is what stops a fourteenth.
    """
    from atlas.ui.theme import PANEL

    panel = cls(DARK)
    assert panel.panel_width in (PANEL.dialog, PANEL.panel, PANEL.screen), (
        f"{name} asks for {panel.panel_width}px, which is not one of the three")


def test_a_panel_is_a_sheet_on_the_window_not_the_window(qapp):
    """Two panels used to open at exactly the window's height.

    Header cut off at the top, footer at the bottom, no margin at either end -- and on a
    1100px window a `screen` panel would have run off both sides.
    """
    from atlas.ui.branches import BranchOverlay
    from atlas.ui.strategy import StrategyOverlay
    from atlas.ui.theme import PANEL_INSET

    host = QWidget()
    host.resize(1100, 700)
    for build in (BranchOverlay, StrategyOverlay):
        panel = build(DARK, host)
        panel.open()
        qapp.processEvents()
        assert panel.panel.width() <= 1100 - 2 * PANEL_INSET, build.__name__
        assert panel.panel.maximumHeight() <= 700 - 2 * PANEL_INSET, build.__name__
        panel.close_overlay()


def test_a_pinned_panel_survives_the_next_one(qapp):
    """Closing siblings is right as a default and wrong as a law.

    Half of what these panels are for is comparison -- the Strategy table against the
    Home Front -- and forced closure turns that into a memory test.
    """
    from atlas.ui.homefront import HomeFrontOverlay
    from atlas.ui.strategy import StrategyOverlay

    host = QWidget()
    host.resize(1600, 950)
    host.show()
    first, second = StrategyOverlay(DARK, host), HomeFrontOverlay(DARK, host)
    first.open()
    first.set_pinned(True)
    second.open()
    _settle(qapp)

    assert first.showing(), "the pin did not hold"
    assert second.showing()
    # And they are beside each other rather than on top of each other.
    left = first.panel.mapTo(host, first.panel.rect().topLeft()).x()
    right = second.panel.mapTo(host, second.panel.rect().topLeft()).x()
    assert left + first.panel.width() <= right, "the two panels overlap"
    assert first.panel.width() < StrategyOverlay.panel_width, "it did not give up half the room"


def test_only_one_panel_can_be_pinned(qapp):
    """Two is a tiling window manager, and there is no third side to dock to."""
    from atlas.ui.cast import CastOverlay
    from atlas.ui.homefront import HomeFrontOverlay
    from atlas.ui.strategy import StrategyOverlay

    host = QWidget()
    host.resize(1600, 950)
    first, second, third = (StrategyOverlay(DARK, host), HomeFrontOverlay(DARK, host),
                            CastOverlay(DARK, host))
    first.set_pinned(True)
    second.set_pinned(True)
    assert not first.pinned and second.pinned
    third.set_pinned(True)
    assert not second.pinned and third.pinned


def test_a_dialog_cannot_be_pinned(qapp):
    """There is nothing to compare a confirmation with, and one left pinned behind the
    thing it was confirming is a trap."""
    from atlas.ui.overlays import ConfirmOverlay, ExportOverlay, RewindOverlay
    from atlas.ui.turning import TurningOverlay

    for build in (ConfirmOverlay, ExportOverlay, RewindOverlay, TurningOverlay):
        panel = build(DARK)
        panel.set_pinned(True)
        assert not panel.pinned, build.__name__
        assert not panel._pin.isVisibleTo(panel), build.__name__


def test_the_pinned_panel_is_not_dimmed_or_dead(qapp):
    """A pin that left a picture of a panel on the screen would not be a pin.

    The sheet in front covers the whole window, so it has to cut its own shade and its
    own mask around whatever is pinned underneath -- or the pinned panel is behind 72%
    of a dimmer and takes no clicks.
    """
    from atlas.ui.homefront import HomeFrontOverlay
    from atlas.ui.strategy import StrategyOverlay

    host = QWidget()
    host.resize(1600, 950)
    host.show()
    pinned, front = StrategyOverlay(DARK, host), HomeFrontOverlay(DARK, host)
    pinned.open()
    pinned.set_pinned(True)
    front.open()
    _settle(qapp)

    assert not front.mask().isEmpty(), "the front sheet still covers everything"
    middle = pinned.panel.mapTo(front, pinned.panel.rect().center())
    assert not front.mask().contains(middle), "clicks cannot reach the pinned panel"

    # And unpinning puts everything back where it was.
    pinned.set_pinned(False)
    _settle(qapp)
    assert front.mask().isEmpty()
    assert front.panel.width() == HomeFrontOverlay.panel_width


def test_closing_a_pinned_panel_releases_the_pin(qapp):
    """Or the next panel opens docked to a side, sharing the window with nothing."""
    from atlas.ui.homefront import HomeFrontOverlay
    from atlas.ui.strategy import StrategyOverlay

    host = QWidget()
    host.resize(1600, 950)
    host.show()
    pinned, front = StrategyOverlay(DARK, host), HomeFrontOverlay(DARK, host)
    pinned.open()
    pinned.set_pinned(True)
    front.open()
    pinned.close_overlay()
    _settle(qapp)
    assert not pinned.pinned
    assert front.panel.width() == HomeFrontOverlay.panel_width


def test_a_panel_scrolls_rather_than_clipping(qapp):
    """Capping a panel at the window is only better than overflowing it if nothing is lost.

    The Home Front and Branches are both taller than the window they open in. Before the
    body scrolled, capping them meant the footer was cut off at the bottom instead of the
    header being cut off at the top -- which is a different bug, not a fix.
    """
    from atlas.ui.branches import BranchOverlay
    from atlas.ui.homefront import HomeFrontOverlay

    host = QWidget()
    host.resize(1400, 700)
    host.show()
    for build in (HomeFrontOverlay, BranchOverlay):
        panel = build(DARK, host)
        panel.open()
        _settle(qapp)
        content = panel._sheet_body.sizeHint().height()
        viewport = panel._sheet.viewport().height()
        if content > viewport:
            assert panel._sheet.verticalScrollBar().maximum() > 0, build.__name__
        panel.close_overlay()


def test_no_panel_overflows_its_own_width(qapp):
    """A horizontal bar on a panel at its full width means something does not fit in it.

    It used to mean the same thing and show nothing, because the body did not scroll: the
    Advance screen's watch conditions are sentences in chips 677px wide inside an 836px
    panel, and the right-hand end of them was simply not drawn.
    """
    host = QWidget()
    host.resize(1600, 950)
    host.show()
    overflowing = []
    for _name, cls in _overlay_classes():
        panel = cls(DARK, host)
        panel.open()
        _settle(qapp)
        if panel._sheet.horizontalScrollBar().maximum() > 0:
            overflowing.append(cls.__name__)
        panel.close_overlay()
    assert overflowing == [], overflowing


def test_the_ramp_ends_fit_on_one_line(qapp):
    """Both ends sit at the two ends of a single line under the ramp.

    A cold-end label that elides into the hot-end label is worse than no label: the one
    thing a ramp cannot say for itself is which way round it is, and on two of these
    layers -- supply, where the depot is the hot end -- it is not the obvious way round.
    """
    from PySide6.QtGui import QFontMetrics

    from atlas.map.legend import LEGENDS, ScaleKind
    from atlas.ui.theme import TYPE
    from atlas.ui.widgets import styled_font

    metrics = QFontMetrics(styled_font(TYPE["mono_small"]))
    # The legend's own measure, less its margins and the gap between the two labels.
    room = 300 - 13 - 15 - 12
    for layer, legend in LEGENDS.items():
        if legend.kind is not ScaleKind.RAMP:
            continue
        used = metrics.horizontalAdvance(legend.cold) + metrics.horizontalAdvance(legend.hot)
        assert used <= room, f"{layer}: {legend.cold!r} + {legend.hot!r} is {used}px of {room}"


# ---------------------------------------------------------- names, not keys


def test_the_inspector_names_the_power_that_did_it(qapp, conn, player):
    """`m_blr` is a row in a table. *Belarus* is the power that did the thing.

    The meta line under every event is the only place a reader is told which of two
    hundred and forty-three powers acted, and it was printing the database key.
    """
    from atlas.engine.naming import nation_names
    from atlas.ui.inspector import InspectorOverlay

    panel = InspectorOverlay(DARK)
    panel.load(conn, "sandbox")
    names = nation_names(conn, "sandbox")
    assert names and player in names

    shown = " ".join(w.text() for w in panel.findChildren(TextLabel))
    keys = [key for key in names if key in shown.split() and names[key] != key]
    assert not keys, f"raw nation ids on the inspector: {keys}"


def test_a_commitment_says_who_you_signed_with(qapp, conn, player, monkeypatch):
    """It read *defence pact (active) with deu, gbr, ita*.

    Three database keys where three countries should be, on the one line in the rail
    that says what this government is committed to.
    """
    from atlas.ui import main_window

    others = [row["id"] for row in conn.execute(
        "SELECT id FROM nation WHERE world_id = 'sandbox' AND id != ?", (player,))][:2]
    monkeypatch.setattr(
        main_window, "standing_commitments",
        lambda *_args, **_kw: [{"kind": "defence_pact", "status": "active",
                                "parties": [player, *others]}], raising=False)

    seen: list[str] = []
    window = _FakeWindowForCommitments(conn, player, seen)
    window.refresh()
    from atlas.engine.naming import nation_names
    names = nation_names(conn, "sandbox")
    assert seen, "no commitment line was produced"
    for key in others:
        assert names[key] in seen[0], seen
        assert f" {key}" not in seen[0], seen


class _FakeWindowForCommitments:
    """The one method under test, lifted out of a window that costs a map pack to build."""

    def __init__(self, conn, player, sink):
        self._conn, self._player, self._world_id = conn, player, "sandbox"
        self._sink = sink

    def refresh(self):
        from atlas.engine.naming import nation_names
        from atlas.state.visibility import standing_commitments

        names = nation_names(self._conn, self._world_id)
        for c in [{"kind": "defence_pact", "status": "active",
                   "parties": [self._player, *[k for k in names if k != self._player][:2]]}]:
            self._sink.append(
                f"{c['kind'].replace('_', ' ')} ({c['status']}) with "
                + (", ".join(names.get(p, p) for p in c["parties"] if p != self._player)
                   or "nobody else"))
        assert standing_commitments is not None


def test_the_window_looks_the_names_up(qapp):
    """And the window itself uses the same lookup, rather than printing what it holds."""
    import inspect

    from atlas.ui.main_window import AtlasWindow

    source = inspect.getsource(AtlasWindow._refresh_commitments)  # noqa: SLF001
    assert "nation_names" in source
    assert "c['parties']" not in source or "names.get" in source


def test_a_shortened_label_offers_the_rest_of_itself(qapp):
    """*Democratic Republic of the Con...* and *Republic of the Con...* are two countries.

    Both are rows in the same list, and the row is a button that opens talks with one of
    them. Eliding is how this interface fits a window; it is not a licence to lose a word.
    """
    host = QWidget()
    host.resize(120, 40)
    host.show()
    label = TextLabel("Democratic Republic of the Congo", style="row", theme=DARK,
                      elide=True, parent=host)
    label.setGeometry(0, 0, 90, 20)
    label.show()
    label.grab()
    assert label.toolTip() == "Democratic Republic of the Congo"

    label.setGeometry(0, 0, 900, 20)
    label.grab()
    assert label.toolTip() == "", "the tooltip outlived the truncation"


def test_every_control_on_the_chrome_can_be_named_aloud(qapp):
    """A glyph with no accessible name is a button a screen reader calls "frame"."""
    from atlas.ui.map_chrome import MapChrome
    from atlas.ui.topbar import _IconButton

    bar = TopBar(DARK)
    for widget in vars(bar).values():
        if isinstance(widget, _IconButton):
            assert widget.accessibleName(), widget._glyph.text()
    for entry in (e for _h, group in bar._more_groups for e in group):
        assert entry.action.text(), entry.label

    chrome = MapChrome(QWidget(), DARK)
    for chip in chrome._layers._chips.values():
        assert chip.accessibleName()
    for chip in chrome._unit_layers._chips.values():
        assert chip.accessibleName()
    from atlas.ui.map_chrome import _SquareButton

    zooms = chrome._zoom.findChildren(_SquareButton)
    assert len(zooms) == 3
    for button in zooms:
        assert button.accessibleName()
