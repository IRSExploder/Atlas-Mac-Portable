"""An inspector must read the causal log, not paraphrase it.

The log has recorded all of this since schema 1 -- every applied diff cites the event that
caused it, and every derived event names its cause -- so the risk here is never that the
data is missing. It is that a reader invents structure the log does not have: attributing a
change to the wrong event, claiming an event touched a province it never wrote to, or
blurring the difference between the engine acting and a model proposing.

So these tests are about faithfulness. The chain is walkable in both directions and the two
directions agree; regions come from the diffs rather than from ``event_participant``, which
a real recorded world populates on 3 events in 716; a refused write is still reported, since
an event whose changes were all refused is a thing that looked like it happened and did not.
"""

from __future__ import annotations

import os
import sqlite3

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from atlas.engine.causality import inspect_event, recent_events  # noqa: E402
from atlas.state.apply import apply_batch, record_event  # noqa: E402
from atlas.state.bootstrap import fingerprint  # noqa: E402
from atlas.state.diff import Diff, DiffBatch, DiffOp  # noqa: E402
from atlas.state.models import EventKind, EventOrigin, Role, Visibility  # noqa: E402
from atlas.ui.inspector import InspectorOverlay  # noqa: E402
from atlas.ui.theme import DARK, LIGHT  # noqa: E402

WORLD = "sandbox"


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def conn() -> sqlite3.Connection:
    from atlas.scenarios.sandbox import sandbox_seed
    from atlas.state.bootstrap import create_world

    connection = create_world(":memory:", sandbox_seed())
    yield connection
    connection.close()


@pytest.fixture
def story(conn):
    """A cause, an effect that moved a province and a war, and a message that moved nothing.

    Deliberately the shape of the chain a real run produces: the peace in PT39 changed
    ownership and war state and was followed by messages that changed nothing at all.
    """
    # The sandbox already has Karthos holding the west and nobody at war, so a batch
    # written straight onto it would be a pair of no-ops -- and an inspector faithfully
    # reporting "karthos -> karthos" would pass a test that proved nothing.
    conn.execute("UPDATE region SET owner_nation_id = 'aurelia' WHERE id = 'r_west'")
    conn.execute("UPDATE relation SET at_war = 1 WHERE nation_a = 'karthos'"
                 "   AND nation_b = 'aurelia'")
    conn.commit()

    first = record_event(
        conn, world_id=WORLD, turn=1, date="1897-04-01", kind=EventKind.ULTIMATUM,
        origin=EventOrigin.MODEL, actor_nation_id="karthos",
        text="Karthos demands the western march.", visibility=Visibility.PUBLIC)
    second = record_event(
        conn, world_id=WORLD, turn=1, date="1897-04-01", kind=EventKind.PEACE_SIGNED,
        origin=EventOrigin.ENGINE, actor_nation_id="karthos",
        text="Aurelia cedes the west.", visibility=Visibility.PUBLIC,
        causes=(first,))
    apply_batch(conn, DiffBatch(
        world_id=WORLD, turn=1, proposer_role=Role.ENGINE, proposer_model=None,
        diffs=(
            Diff("region", "r_west", "owner_nation_id", DiffOp.SET, "karthos",
                 reason_event_id=second),
            Diff("relation", "karthos|aurelia", "at_war", DiffOp.SET, 0,
                 reason_event_id=second),
        )))
    third = record_event(
        conn, world_id=WORLD, turn=1, date="1897-04-01",
        kind=EventKind.DIPLOMATIC_MESSAGE, origin=EventOrigin.MODEL,
        actor_nation_id="karthos", text="Karthos calls the matter settled.",
        visibility=Visibility.PUBLIC, causes=(second,))
    conn.commit()
    return first, second, third


# ------------------------------------------------------------------- the reading


def test_an_event_reports_what_it_changed(conn, story):
    _first, second, _third = story
    found = inspect_event(conn, WORLD, second)

    assert found.change_count == 2
    titles = {group.title for group in found.groups}
    assert titles == {"Territory changed hands", "War and peace"}
    territory = next(g for g in found.groups if g.title == "Territory changed hands")
    assert territory.changes[0].rendered() == "owner_nation_id: aurelia -> karthos"


def test_the_chain_is_walkable_in_both_directions(conn, story):
    """**The property that makes it an inspector rather than a list.**"""
    first, second, third = story
    middle = inspect_event(conn, WORLD, second)

    assert [c.event_id for c in middle.causes] == [first]
    assert [c.event_id for c in middle.consequences] == [third]
    # ...and walking either way lands somewhere that points back.
    assert second in [c.event_id for c in inspect_event(conn, WORLD, third).causes]
    assert second in [c.event_id for c in inspect_event(conn, WORLD, first).consequences]


def test_regions_come_from_the_diffs(conn, story):
    """`event_participant` carries a region on 3 of 716 events in a real recorded world.
    An inspector reading it would report that almost nothing happened anywhere."""
    _first, second, _third = story
    assert not conn.execute(
        "SELECT 1 FROM event_participant WHERE event_id = ? AND region_id IS NOT NULL",
        (second,)).fetchone(), "the fixture must not smuggle the answer in the easy way"

    assert [rid for rid, _name in inspect_event(conn, WORLD, second).regions] == ["r_west"]


def test_a_battle_is_located_through_the_units_it_hurt(conn):
    """A unit diff names a unit, not a province -- so without resolving one to the other a
    battle would report that it happened nowhere."""
    event = record_event(
        conn, world_id=WORLD, turn=1, date="1897-04-01", kind=EventKind.BATTLE,
        origin=EventOrigin.ENGINE, actor_nation_id="karthos",
        text="A battle in the north.", visibility=Visibility.PUBLIC)
    conn.execute(
        "INSERT INTO unit (id, world_id, nation_id, region_id, kind, strength, morale)"
        " VALUES ('u_x', ?, 'karthos', 'r_north', 'infantry', 10, 60)", (WORLD,))
    conn.commit()
    apply_batch(conn, DiffBatch(
        world_id=WORLD, turn=1, proposer_role=Role.ENGINE,
        diffs=(Diff("unit", "u_x", "strength", DiffOp.SET, 4.0, reason_event_id=event),)))

    assert [rid for rid, _n in inspect_event(conn, WORLD, event).regions] == ["r_north"]


def test_authorship_distinguishes_the_engine_from_a_model(conn, story):
    _first, second, _third = story
    found = inspect_event(conn, WORLD, second)
    assert found.origin == "ENGINE"
    assert found.author_role == "engine"
    assert found.author_model == "", "no model was in the loop for this one"


def test_an_event_that_changed_nothing_says_so(conn, story):
    _first, _second, third = story
    found = inspect_event(conn, WORLD, third)
    assert found.groups == () and found.change_count == 0
    assert found.regions == ()


def test_a_refused_write_is_reported_rather_than_dropped(conn):
    """An event whose changes were all refused is a thing that looked like it happened and
    did not, which is exactly what a reader needs to be told."""
    event = record_event(
        conn, world_id=WORLD, turn=1, date="1897-04-01", kind=EventKind.ECONOMIC_REFORM,
        origin=EventOrigin.MODEL, actor_nation_id="karthos",
        text="An impossible reform.", visibility=Visibility.PUBLIC)
    outcome = apply_batch(conn, DiffBatch(
        world_id=WORLD, turn=1, proposer_role=Role.WORLD_SIM, proposer_model="test",
        diffs=(Diff("nation", "karthos", "economy", DiffOp.SET, 9_000.0,
                    reason_event_id=event),)))
    assert not outcome.applied, "the fixture needs a genuinely refused write"

    found = inspect_event(conn, WORLD, event)
    assert found.change_count == 1
    assert found.groups[0].changes[0].status != "applied"


def test_reading_changes_nothing(conn, story):
    before = fingerprint(conn, WORLD)
    for event_id in story:
        inspect_event(conn, WORLD, event_id)
    recent_events(conn, WORLD)
    assert fingerprint(conn, WORLD) == before


def test_an_unknown_event_is_none_rather_than_a_crash(conn):
    assert inspect_event(conn, WORLD, 99_999) is None


# --------------------------------------------------------------------- the screen


def _screen(theme=DARK) -> tuple[QWidget, InspectorOverlay]:
    host = QWidget()
    host.resize(1480, 1000)
    return host, InspectorOverlay(theme, host)


def _texts(widget: QWidget) -> list[str]:
    from PySide6.QtWidgets import QLabel

    return [child.text() for child in widget.findChildren(QLabel) if child.text()]


def test_the_screen_shows_the_chain_and_the_changes(qapp, conn, story):
    _first, second, _third = story
    host, screen = _screen()
    screen.load(conn, WORLD, second)

    shown = " | ".join(_texts(screen))
    assert "Aurelia cedes the west." in shown
    assert "Karthos demands the western march." in shown, "the cause must be on screen"
    assert "Karthos calls the matter settled." in shown, "so must the consequence"
    assert "owner_nation_id: aurelia -> karthos" in shown
    assert "Territory changed hands" in " ".join(_texts(screen))


def test_clicking_a_cause_navigates_to_it(qapp, conn, story):
    """The whole point of an inspector over a list."""
    first, second, _third = story
    host, screen = _screen()
    screen.load(conn, WORLD, second)
    assert screen._current == second

    screen.show_event(first)
    assert screen._current == first
    assert screen._trail == [second], "and it remembers where you came from"
    assert "Karthos demands the western march." in " ".join(_texts(screen))


def test_the_screen_offers_a_region_to_the_map(qapp, conn, story):
    _first, second, _third = story
    host, screen = _screen()
    asked: list[str] = []
    screen.region_selected.connect(asked.append)
    screen.load(conn, WORLD, second)

    from atlas.ui.widgets import Chip

    chips = [c for c in screen.findChildren(Chip)]
    assert chips, "the affected province must be offered"
    chips[0].clicked.emit()
    assert asked == ["r_west"]


def test_opening_the_screen_changes_nothing(qapp, conn, story):
    """A read-only view that mutates a save is the one defect a screenshot cannot show."""
    before = fingerprint(conn, WORLD)
    host, screen = _screen()
    screen.load(conn, WORLD, None)
    screen.open()
    qapp.processEvents()
    assert fingerprint(conn, WORLD) == before


def test_reloading_does_not_stack_duplicate_rows(qapp, conn, story):
    _first, second, _third = story
    host, screen = _screen()
    screen.load(conn, WORLD, second)
    first_count = len(_texts(screen))
    for _ in range(3):
        screen.load(conn, WORLD, second)
        qapp.processEvents()
    assert len(_texts(screen)) == first_count


def test_both_themes_apply_without_throwing(qapp, conn, story):
    host, screen = _screen()
    screen.load(conn, WORLD, story[1])
    for theme in (LIGHT, DARK, LIGHT):
        screen.apply_theme(theme)
        qapp.processEvents()


def test_a_world_with_no_events_does_not_break(qapp):
    from atlas.scenarios.sandbox import sandbox_seed
    from atlas.state.bootstrap import create_world

    bare = create_world(":memory:", sandbox_seed())
    bare.execute("DELETE FROM event WHERE world_id = ?", (WORLD,))
    bare.commit()
    host, screen = _screen()
    screen.load(bare, WORLD, None)
    assert any("no events yet" in text for text in _texts(screen))
    bare.close()


def test_the_topbar_offers_the_inspector(qapp):
    """Built but unreachable is the failure mode this project has already had once."""
    from atlas.ui.topbar import TopBar

    bar = TopBar(DARK)
    seen: list[bool] = []
    bar.inspector_requested.connect(lambda: seen.append(True))
    bar._inspector.clicked.emit()
    assert seen == [True]


def test_the_window_wires_the_button_and_the_map():
    import inspect

    from atlas.ui.main_window import AtlasWindow

    source = inspect.getsource(AtlasWindow)
    assert "inspector_requested.connect(self._open_inspector)" in source
    assert "self._inspector.load(" in source and "self._inspector.open()" in source
    # ...and selecting a region both selects it and centres the map on it.
    assert "region_selected.connect(self._reveal_region)" in source
    assert "self._map.centerOn(" in source
