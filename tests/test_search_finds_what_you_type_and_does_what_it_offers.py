"""The search panel: what you type is what it finds, and every row leads somewhere.

`atlas.engine.search` is tested on its own for ranking and for the fog. This is the panel
in front of it, and it has three promises of its own:

**It types as you type.** Every keystroke re-runs the search -- no debounce, no worker, no
cache -- so the rows on screen are always the answer to the text in the box, and a short or
empty answer says *why* it is short rather than leaving a blank the player has to interpret.

**A result you cannot act on is not a result.** A row click hands the window a
``(kind, target)`` to go to; the pin control keeps it; Enter takes the first. And a province
or a power can be asked *what can I do about this*, which lists acts the engine itself would
accept -- a whole order can be taken from the list, one that still needs a sentence written
stays unclickable, because an order appended half-written is refused at the door and the
player never sees why.

**It cannot see round the fog, and neither can anything built on top of it.** A panel is
one more place a secret can leak from, and the easy way to leak it is not the search -- it
is the panel keeping the typed query across a reload for a *different* viewer and showing
rows it computed for the last one. So the secret-event and private-treaty cases are asked of
the panel as a player would meet them, rows and tally both, and not only of the engine.
"""

from __future__ import annotations

import os
import sqlite3

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from atlas.scenarios.sandbox import sandbox_seed  # noqa: E402
from atlas.state.apply import record_event  # noqa: E402
from atlas.state.bootstrap import create_world, fingerprint  # noqa: E402
from atlas.state.models import EventKind, EventOrigin, Visibility  # noqa: E402
from atlas.ui.search import SearchOverlay, _DoableRow, _HitRow  # noqa: E402
from atlas.ui.theme import DARK, LIGHT, load_fonts  # noqa: E402
from atlas.ui.widgets import TextLabel  # noqa: E402

WORLD = "sandbox"
ME, THEM, THIRD = "aurelia", "karthos", "veyra"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    load_fonts()
    yield app


@pytest.fixture
def conn() -> sqlite3.Connection:
    """The sandbox, with one thing everybody saw, one thing only Veyra was told, and one
    agreement Karthos and Veyra reached where nobody else was in the room."""
    connection = create_world(":memory:", sandbox_seed())
    connection.execute("UPDATE world SET turn_number = 3 WHERE id = ?", (WORLD,))

    def event(text: str, visibility: Visibility, seen_by=()) -> int:
        return record_event(
            connection, world_id=WORLD, turn=3, date="1897-03-01",
            kind=EventKind.NARRATIVE, origin=EventOrigin.ENGINE, actor_nation_id=THEM,
            text=text, visibility=visibility, visible_to=tuple(seen_by))

    event("Karthos closed the straits to Aurelian shipping.", Visibility.PUBLIC)
    event("Karthos is preparing a coup in Aurelia.", Visibility.SECRET, seen_by=(THIRD,))
    private = event("A quiet understanding over the plateau.", Visibility.BILATERAL,
                    seen_by=(THEM, THIRD))
    connection.execute(
        "INSERT INTO commitment (id, world_id, kind, status, created_turn,"
        " source_event_id) VALUES (9, ?, 'alliance', 'active', 3, ?)", (WORLD, private))
    for party in (THEM, THIRD):
        connection.execute(
            "INSERT INTO commitment_nation (commitment_id, nation_id, role, accepted_turn)"
            " VALUES (9, ?, 'party', 3)", (party,))
    connection.commit()
    yield connection
    connection.close()


@pytest.fixture
def panel(qapp, conn):
    """The panel open on a shown window, as ⌘F leaves it, for Aurelia."""
    host = QWidget()
    host.resize(1600, 950)
    host.show()
    overlay = SearchOverlay(DARK, host)
    overlay.load(conn, WORLD, ME)
    overlay.open()
    qapp.processEvents()
    yield overlay
    host.close()
    host.deleteLater()


def _type(panel: SearchOverlay, text: str) -> None:
    panel._box.setText(text)  # noqa: SLF001 - the box is the panel's whole interface
    QApplication.processEvents()


def _rows(panel: SearchOverlay) -> list[_HitRow]:
    return list(panel._rows)  # noqa: SLF001


def _doables(panel: SearchOverlay) -> list[_DoableRow]:
    return list(panel._doable_rows)  # noqa: SLF001


def _tally(panel: SearchOverlay) -> str:
    return panel._tally.text()  # noqa: SLF001


def _row(panel: SearchOverlay, kind: str, target: str) -> _HitRow:
    [row] = [row for row in _rows(panel)
             if (row._hit.kind, row._hit.target) == (kind, target)]  # noqa: SLF001
    return row


def _the_one(panel: SearchOverlay, kind: str) -> _HitRow:
    [row] = [row for row in _rows(panel) if row._hit.kind == kind]  # noqa: SLF001
    return row


def _click(widget: QWidget, at: QPoint | None = None) -> None:
    QApplication.processEvents()
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     at if at is not None else QPoint(4, 4))
    QApplication.processEvents()


def _texts(widget: QWidget) -> list[str]:
    return [label.text() for label in widget.findChildren(TextLabel)]


# ---------------------------------------------------------------------- it types as you type


def test_typing_fills_the_list_with_what_the_engine_found(panel, conn):
    """The rows are the engine's answer and nothing else: same hits, same order."""
    from atlas.engine.search import search

    _type(panel, "kart")

    expected = [(hit.kind, hit.target) for hit in search(conn, WORLD, "kart", viewer=ME)]
    assert expected, "the fixture should have something called Karthos in it"
    assert [(row._hit.kind, row._hit.target) for row in _rows(panel)] == expected  # noqa: SLF001
    assert _tally(panel).startswith(f"{len(expected)} result")


def test_each_keystroke_replaces_the_list_rather_than_adding_to_it(panel):
    """**A re-run that appended** would leave the answer to *ka* under the answer to
    *karthos*, and the player would be clicking rows for a question they stopped asking."""
    _type(panel, "ka")
    _type(panel, "northmark")

    assert [row._hit.target for row in _rows(panel)] == ["r_north"]  # noqa: SLF001
    assert _tally(panel) == "1 result; Enter takes the first."
    shown = [w for w in panel.findChildren(_HitRow) if w.parent() is not None]
    assert len(shown) == 1, "the rows from the last keystroke are still on the panel"


def test_one_letter_is_answered_with_why_not_with_the_whole_world(panel):
    _type(panel, "k")

    assert _rows(panel) == []
    assert _tally(panel).startswith("Two letters or more")


def test_nothing_found_says_that_hidden_is_one_of_the_reasons(panel):
    """A blank list in a fogged world is ambiguous, and the panel says so rather than
    implying the thing does not exist."""
    _type(panel, "zzqx")

    assert _rows(panel) == []
    assert "hidden from you" in _tally(panel)


def test_a_row_says_what_kind_of_thing_it_is_in_the_players_words(panel):
    """"region" and "commitment" are table names. The row says Province, Power, What
    happened, Agreement -- the question the player was asking."""
    _type(panel, "straits")
    assert any("What happened" in text for text in _texts(_the_one(panel, "event")))

    _type(panel, "northmark")
    assert any(text.startswith("Province — Aurelia") for text in
               _texts(_row(panel, "region", "r_north")))


def test_reopening_resumes_the_query_rather_than_starting_again(panel, conn):
    _type(panel, "karthos")
    before = [row._hit.target for row in _rows(panel)]  # noqa: SLF001

    panel.close_overlay()
    panel.load(conn, WORLD, ME)
    panel.open()

    assert panel._box.text() == "karthos"  # noqa: SLF001
    assert [row._hit.target for row in _rows(panel)] == before  # noqa: SLF001


# ------------------------------------------------------------- and every row leads somewhere


def test_clicking_a_row_hands_the_window_where_to_go(panel):
    _type(panel, "northmark")
    chosen: list[tuple[str, str]] = []
    panel.chosen.connect(lambda kind, target: chosen.append((kind, target)))

    _click(_row(panel, "region", "r_north"))

    assert chosen == [("region", "r_north")]


def test_enter_takes_the_first_row(panel):
    _type(panel, "karthos")
    chosen: list[tuple[str, str]] = []
    panel.chosen.connect(lambda kind, target: chosen.append((kind, target)))

    QTest.keyClick(panel._box, Qt.Key.Key_Return)  # noqa: SLF001

    first = _rows(panel)[0]._hit  # noqa: SLF001
    assert chosen == [(first.kind, first.target)]


def test_enter_on_nothing_goes_nowhere(panel):
    _type(panel, "zzqx")
    chosen: list[tuple[str, str]] = []
    panel.chosen.connect(lambda kind, target: chosen.append((kind, target)))

    QTest.keyClick(panel._box, Qt.Key.Key_Return)  # noqa: SLF001

    assert chosen == []


def _as_the_window_does(panel: SearchOverlay, conn) -> list[tuple[str, str]]:
    """Wire the pin the way `MainWindow._toggle_pin` does: toggle the watchlist and, the
    panel being open, reload it so the control can say what it now does."""
    watched: set[tuple[str, str]] = set()
    pinned: list[tuple[str, str]] = []

    def toggle(kind: str, target: str) -> None:
        pinned.append((kind, target))
        watched.symmetric_difference_update({(kind, target)})
        if panel.isVisible():
            panel.load(conn, WORLD, ME, watched)

    panel.pinned.connect(toggle)
    return pinned


def test_the_pin_control_pins_and_does_not_also_navigate(panel, conn):
    """**Findable and keepable are the same gesture** -- but they are two gestures, and a
    pin that also jumped the map would move the player away from what they were doing. The
    panel stays up, and the control turns over to say what the next click will do."""
    _type(panel, "northmark")
    chosen: list[tuple[str, str]] = []
    panel.chosen.connect(lambda *a: chosen.append(a))
    pinned = _as_the_window_does(panel, conn)

    row = _row(panel, "region", "r_north")
    _click(row, row._pin.geometry().center())  # noqa: SLF001

    assert pinned == [("region", "r_north")]
    assert chosen == []
    assert panel.showing()
    assert _row(panel, "region", "r_north")._pin.text() == "unpin"  # noqa: SLF001


def test_a_watched_row_offers_to_unpin_and_the_others_to_pin(panel, conn):
    """The control says what clicking it will do, which depends on whether the row is
    already on the watchlist -- a pin that said *pin* on a pinned row would unpin it."""
    panel.load(conn, WORLD, ME, watched={("nation", THEM)})
    _type(panel, "karthos")

    assert _row(panel, "nation", THEM)._pin.text() == "unpin"  # noqa: SLF001
    others = [row for row in _rows(panel) if row._hit.target != THEM]  # noqa: SLF001
    assert others, "the sandbox's Karthos Basin should match 'karthos' too"
    assert {row._pin.text() for row in others} == {"pin"}  # noqa: SLF001


def test_only_a_place_or_a_power_can_be_asked_what_to_do_about_it(panel):
    """There is nothing to order *about* a headline or a treaty; the question is offered
    where it has an answer."""
    _type(panel, "karthos")
    assert _row(panel, "nation", THEM)._ask is not None  # noqa: SLF001

    _type(panel, "straits")
    assert _the_one(panel, "event")._ask is None  # noqa: SLF001


# --------------------------------------------------------- and what can I do about this one


def _ask(panel: SearchOverlay, kind: str, target: str) -> None:
    QApplication.processEvents()
    row = _row(panel, kind, target)
    _click(row, row._ask.geometry().center())  # noqa: SLF001


def test_asking_lists_what_the_engine_would_accept_under_the_row(panel, conn):
    """**Nothing is offered that the engine would refuse**: the list is
    `what_can_i_do_about`'s answer, not a menu of hopes the panel keeps for itself."""
    from atlas.engine.against import what_can_i_do_about

    _type(panel, "karthos")
    _ask(panel, "nation", THEM)

    offered = [row._doable for row in _doables(panel)]  # noqa: SLF001
    assert offered == what_can_i_do_about(conn, WORLD, nation_id=ME, target=THEM)
    assert offered, "the sandbox's neighbour should have something to order about it"


def test_asking_again_puts_the_list_away(panel):
    """One open at a time: the list is long enough that two open at once is a scroll,
    not an answer -- and the same control closes it."""
    _type(panel, "karthos")
    _ask(panel, "nation", THEM)
    assert _doables(panel)

    _ask(panel, "nation", THEM)
    assert _doables(panel) == []


def test_the_open_list_survives_the_next_keystroke_if_its_row_does(panel):
    _type(panel, "karthos")
    _ask(panel, "nation", THEM)
    _type(panel, "karthos ")

    assert _doables(panel), "the list closed under the player while they were reading it"


def test_taking_a_whole_order_hands_it_to_the_window_and_closes_the_panel(panel):
    _type(panel, "karthos")
    _ask(panel, "nation", THEM)
    ordered: list[tuple[object, str]] = []
    panel.ordered.connect(lambda action, label: ordered.append((action, label)))

    ready = next(row for row in _doables(panel) if row._doable.ready)  # noqa: SLF001
    _click(ready)

    [(action, label)] = ordered
    assert action == ready._doable.prefill  # noqa: SLF001
    assert action is not ready._doable.prefill, "the window was handed the engine's own dict"  # noqa: SLF001
    assert label == ready._doable.explain  # noqa: SLF001
    assert not panel.showing(), "the order was taken and the panel stayed over the map"


def test_an_act_that_still_needs_a_sentence_cannot_be_taken(panel):
    """**An order appended half-written is refused at the door and the player never sees
    why.** So a line that still needs something says what, and a click on it does nothing."""
    _type(panel, "northmark")
    _ask(panel, "region", "r_north")
    ordered: list[object] = []
    panel.ordered.connect(lambda *a: ordered.append(a))

    unready = [row for row in _doables(panel) if not row._doable.ready]  # noqa: SLF001
    assert unready, "the sandbox offers an undertaking against a province that needs a kind"
    row = unready[0]
    assert any("you still have to say" in text for text in _texts(row))

    _click(row)
    assert ordered == []


# -- a click inside the panel is not a click on the dim ----------------------------------
#
# `Overlay.mousePressEvent` closed the panel on the belief that "a click that lands on a
# child never reaches here" -- but a `QFrame` or a `QLabel` that does not accept a press
# *ignores* it, and Qt hands an ignored press to the parent, and so on up to the overlay.
# The hit rows and a taken act escaped by luck: the click rebuilt the list or closed the
# panel first, so the press had no parent left to reach. It is now asked where the click
# landed, not who received it.


def test_clicking_an_act_that_cannot_be_taken_does_not_throw_the_panel_away(panel):
    """The line is unclickable by design -- it says what it still needs, and the player
    goes and writes it. **Unclickable has to mean nothing happens.** What happens instead
    is the whole panel vanishes, taking the query, the open list and the sentence that
    explained why with it."""
    _type(panel, "northmark")
    _ask(panel, "region", "r_north")

    _click(next(row for row in _doables(panel) if not row._doable.ready))  # noqa: SLF001

    assert panel.showing()


def test_clicking_the_panels_own_words_does_not_dismiss_it(panel):
    """The dim around the panel dismisses it; the panel does not. A player who clicks the
    line saying *nothing found, it may be hidden from you* while reading it has not asked
    to leave."""
    _type(panel, "zzqx")

    _click(panel._tally)  # noqa: SLF001

    assert panel.showing()


def test_a_click_on_the_dim_still_dismisses_it(panel):
    """The control: the fix must not make the panel impossible to leave by clicking away."""
    _type(panel, "zzqx")
    corner = QPoint(1, 1)
    assert not panel.panel.rect().contains(panel.panel.mapFrom(panel, corner)), (
        "the fixture's panel fills the dim")

    QTest.mouseClick(panel, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, corner)

    assert not panel.showing()


def test_asking_about_yourself_says_there_is_nothing_to_order(panel):
    """You do not declare war on yourself, and a blank space where the list should be
    reads as the panel having broken."""
    _type(panel, ME)
    _ask(panel, "nation", ME)

    assert _doables(panel) == []
    assert any("nothing you can order about this one" in label.text()
               for label in panel.findChildren(TextLabel) if label.isVisibleTo(panel))


def test_a_spectator_is_offered_nothing_to_order(panel, conn):
    """With no viewer -- the omniscient view -- there is no government to give orders on
    behalf of, and the panel does not invent one."""
    panel.load(conn, WORLD, None)
    _type(panel, "karthos")
    _ask(panel, "nation", THEM)

    assert _doables(panel) == []


# ------------------------------------------------------------------------ and it keeps secrets


def test_a_secret_is_not_found_by_typing_the_words_in_it(panel, conn):
    """**The load-bearing test.** Veyra was told Karthos is planning a coup in Aurelia;
    Aurelia was not. Typing the word is exactly how a curious player would look, and the
    panel must answer as though nothing were there -- no row, no count that gives it away."""
    _type(panel, "coup")

    assert _rows(panel) == []
    assert "Nothing found" in _tally(panel)
    assert not any("coup" in text.casefold() for text in _texts(panel))


def test_an_agreement_two_others_reached_in_private_is_not_found(panel):
    """Karthos and Veyra's alliance was recorded on an event only the two of them saw.
    Aurelia can find each of them by name, and cannot find what they signed."""
    _type(panel, "alliance")
    assert not [row for row in _rows(panel) if row._hit.kind == "treaty"]  # noqa: SLF001

    _type(panel, "veyra")
    assert not [row for row in _rows(panel) if row._hit.kind == "treaty"], (  # noqa: SLF001
        "a treaty is also found by who signed it, and that door has to be shut too")


def test_the_query_kept_across_a_reload_is_re_asked_for_the_new_viewer(panel, conn):
    """**The leak the panel could add on its own.** It keeps the typed query so reopening
    resumes -- and if it kept the *rows* too, the answer computed for Veyra would still be
    on screen when the panel was pointed at Aurelia."""
    panel.load(conn, WORLD, THIRD)
    _type(panel, "coup")
    assert [row._hit.kind for row in _rows(panel)] == ["event"]  # noqa: SLF001

    panel.load(conn, WORLD, ME)

    assert _rows(panel) == []
    assert "Nothing found" in _tally(panel)


def test_the_parties_to_a_private_agreement_can_find_it(panel, conn):
    """The gate is who was in the room, not a blanket ban -- otherwise the two powers that
    signed it could not look it up either, and the test above would pass for the wrong
    reason."""
    panel.load(conn, WORLD, THIRD)
    _type(panel, "alliance")

    assert [row._hit.kind for row in _rows(panel)] == ["treaty"]  # noqa: SLF001


# ------------------------------------------------------------------ and it touches nothing


def test_searching_and_asking_write_nothing(panel, conn):
    """Nothing is written by opening, typing in, asking, or dismissing this. Taking an act
    hands it to the window; the panel itself has no pen."""
    before = fingerprint(conn, WORLD)

    for text in ("karthos", "coup", "northmark", "alliance"):
        _type(panel, text)
    _type(panel, "karthos")
    _ask(panel, "nation", THEM)
    panel.close_overlay()

    assert fingerprint(conn, WORLD) == before


def test_a_theme_change_reaches_the_rows_already_on_screen(panel):
    """A row built in the dark and left dark after the switch is a strip of the wrong
    colour down the middle of a light panel -- and the rows are rebuilt on every keystroke,
    so the next ones have to be built in the new theme as well."""
    assert DARK.inset != LIGHT.inset and DARK.panel != LIGHT.panel
    _type(panel, "karthos")
    _ask(panel, "nation", THEM)

    panel.apply_theme(LIGHT)

    assert all(LIGHT.inset in row.styleSheet() for row in _rows(panel))
    assert all(LIGHT.panel in row.styleSheet() for row in _doables(panel))
    assert LIGHT.brass in panel._box.styleSheet()  # noqa: SLF001

    _type(panel, "northmark")
    assert all(LIGHT.inset in row.styleSheet() for row in _rows(panel)), (
        "rows built after the switch came out in the old theme")
