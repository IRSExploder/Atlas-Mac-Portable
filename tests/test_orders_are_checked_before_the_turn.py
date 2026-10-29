"""A preview that disagrees with the turn is worse than no preview.

The workbench exists to catch an order that could never have worked before the jump is
spent. The way it could fail is not by crashing -- it is by having an opinion of its own:
a rule restated here, drifting from the engine's, telling a player their order is fine when
the turn will refuse it, or refusing one the turn would have run.

So the load-bearing test is :func:`test_the_card_and_the_turn_agree`, which asks the
workbench and the gambit layer the same question and requires the same answer. Everything
else is about honesty at the edges: an order nothing executes is *inert* rather than
refused, an order whose outcome the engine decides at resolution says so instead of
guessing, and the check writes nothing to the world.
"""

from __future__ import annotations

import os
import sqlite3

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from atlas.engine.capabilities import by_type  # noqa: E402
from atlas.engine.gambit import KINDS, _requirements_met  # noqa: E402
from atlas.engine.workbench import (  # noqa: E402
    BLOCKED,
    INERT,
    PREVALIDATED,
    REFUSED,
    VALID,
    review_orders,
)
from atlas.state.bootstrap import fingerprint  # noqa: E402
from atlas.ui.theme import DARK, LIGHT, load_fonts  # noqa: E402
from atlas.ui.workbench import WorkbenchOverlay  # noqa: E402

WORLD = "sandbox"


@pytest.fixture(scope="module")
def qapp():
    # The application first: Qt's font database is a service of a running QApplication,
    # and asking it to load a font before one exists is an access violation, not an error.
    app = QApplication.instance() or QApplication([])
    load_fonts()
    yield app


@pytest.fixture
def conn() -> sqlite3.Connection:
    from atlas.scenarios.sandbox import sandbox_seed
    from atlas.state.bootstrap import create_world

    connection = create_world(":memory:", sandbox_seed())
    connection.execute(
        "UPDATE relation SET at_war = 1 WHERE nation_a IN ('aurelia', 'karthos')"
        "   AND nation_b IN ('aurelia', 'karthos')")
    connection.commit()
    yield connection
    connection.close()


def _one(conn, action: dict):
    cards = review_orders(conn, WORLD, nation_id="aurelia", actions=[action])
    assert len(cards) == 1
    return cards[0]


# ------------------------------------------------------- the card must not have opinions


def test_the_card_and_the_turn_agree(conn):
    """**The property the whole screen rests on.**

    For every undertaking a player may order, against every target and a region that is and
    is not the target's, the card's verdict must be exactly what the gambit layer itself
    says -- because it is the same call. A workbench with its own copy of the rules is one
    that eventually tells a player their order is fine when the turn will refuse it.
    """
    from atlas.engine.orders import PLAYER_GAMBITS

    checked = 0
    for kind_id in PLAYER_GAMBITS:
        kind = next(k for k in KINDS if k.id == kind_id)
        for target in ("karthos", "aurelia", "veyra"):
            for region in (None, "r_west", "r_north"):
                action = {"type": "gambit", "summary": "x", "gambit_kind": kind_id,
                          "target_nation": target}
                if region:
                    action["region"] = region
                engine_says = _requirements_met(
                    conn, WORLD, kind, "aurelia", target, region, "")
                card = _one(conn, action)
                checked += 1

                if engine_says is None:
                    assert card.verdict == VALID, (
                        f"{kind_id}->{target}/{region}: the engine would attempt it and "
                        f"the card said {card.verdict}")
                    assert card.refusal is None
                else:
                    assert card.verdict == REFUSED, (
                        f"{kind_id}->{target}/{region}: the engine refuses it and the "
                        f"card said {card.verdict}")
                    assert card.refusal.because == engine_says.because
                    assert card.refusal.instead == engine_says.instead
                    assert card.refusal.requirement == engine_says.requirement
    assert checked >= 60, "the sweep should be wide enough to mean something"


def test_the_refusal_names_the_gate_that_failed(conn):
    """A card with five requirements has to say which one is the wall."""
    card = _one(conn, {"type": "gambit", "summary": "Sabotage ourselves",
                       "gambit_kind": "sabotage", "target_nation": "aurelia"})
    assert card.verdict == REFUSED
    assert card.refusal.requirement == "needs_target"
    assert card.missing == ("needs_target",)
    assert "at_war" not in card.missing, "only the failing gate is marked"


def test_a_refusal_carries_the_alternative_where_there_is_one(conn):
    card = _one(conn, {"type": "gambit", "summary": "Strike our own ground",
                       "gambit_kind": "air_strike", "target_nation": "karthos",
                       "region": "r_north", "aim": "forces"})
    assert card.verdict == REFUSED
    assert card.refusal.instead, "the engine offers a legal alternative here"


# ------------------------------------------------------------------ honesty at the edges


def test_an_order_nothing_executes_is_inert_rather_than_refused(conn):
    """"Not attempted" and "refused" are different answers, and telling a player the first
    was the second sends them to fix a sentence that was fine."""
    card = _one(conn, {"type": "other", "summary": "Reorganise the ministries"})
    assert card.verdict == INERT
    assert by_type("other").handled_by == "nothing"
    assert card.refusal.instead, "the capability's own note says what to write instead"


def test_an_unknown_order_type_is_blocked_with_a_reason(conn):
    card = _one(conn, {"type": "teleport", "summary": "Teleport the fleet"})
    assert card.verdict == BLOCKED
    assert "teleport" in card.refusal.because


def test_a_missing_requirement_is_named(conn):
    card = _one(conn, {"type": "move_units", "summary": "March somewhere"})
    assert card.verdict == BLOCKED
    assert set(card.missing) == {"from_region", "to_region"}
    assert "from_region" in card.refusal.because


def test_an_unjudgeable_order_says_so_rather_than_guessing(conn):
    """**The third state.** Whether an investment pays is decided at resolution, so a card
    claiming to know would be inventing -- and a player who learns the preview invents
    things stops reading the ones that matter."""
    card = _one(conn, {"type": "economic_investment", "summary": "Invest twelve",
                       "amount": 12})
    assert card.verdict == VALID
    assert card.prevalidated is False
    assert card.refusal is None
    assert card.action_type not in PREVALIDATED


def test_prevalidated_names_only_kinds_with_a_real_predicate(conn):
    """The list is a promise about where a verdict came from. A kind in it with no real
    check behind it would be the lie the whole module is arranged to avoid."""
    assert set(PREVALIDATED) == {"gambit"}
    card = _one(conn, {"type": "gambit", "summary": "A summit",
                       "gambit_kind": "summit", "target_nation": "karthos"})
    assert card.prevalidated is True


def test_a_card_carries_the_engines_own_cost_and_prerequisites(conn):
    card = _one(conn, {"type": "raise_units", "summary": "Raise two divisions",
                       "unit_kind": "infantry"})
    capability = by_type("raise_units")
    assert card.cost == capability.cost
    assert card.explain == capability.explain
    assert tuple(p.name for p in card.prerequisites) == capability.required


def test_reviewing_changes_nothing(conn):
    """A preview that mutated the world would make looking at your orders cost something."""
    before = fingerprint(conn, WORLD)
    review_orders(conn, WORLD, nation_id="aurelia", actions=[
        {"type": "gambit", "summary": "s", "gambit_kind": "sabotage",
         "target_nation": "karthos"},
        {"type": "move_units", "summary": "m", "from_region": "r_west",
         "to_region": "r_north"},
        {"type": "teleport", "summary": "t"},
    ])
    assert fingerprint(conn, WORLD) == before


def test_a_spectator_has_no_orders_to_review(conn):
    assert review_orders(conn, WORLD, nation_id=None, actions=[{"type": "other"}]) == []


# --------------------------------------------------------------------- the screen


def _screen(theme=DARK) -> tuple[QWidget, WorkbenchOverlay]:
    host = QWidget()
    host.resize(1180, 1080)
    return host, WorkbenchOverlay(theme, host)


def _texts(widget: QWidget) -> list[str]:
    from PySide6.QtWidgets import QLabel

    return [child.text() for child in widget.findChildren(QLabel) if child.text()]


def test_the_screen_shows_the_verdict_and_the_way_out(qapp, conn):
    host, screen = _screen()
    screen.load(conn, WORLD, "aurelia", [
        {"type": "gambit", "summary": "Sabotage our own works",
         "gambit_kind": "sabotage", "target_nation": "aurelia"},
        {"type": "gambit", "summary": "Convene a summit", "gambit_kind": "summit",
         "target_nation": "karthos"},
    ])
    shown = " | ".join(_texts(screen))

    assert "REFUSED" in shown and "WILL ATTEMPT" in shown
    assert "cannot be aimed at yourself" in shown
    assert "2 orders" in shown


def test_the_screen_counts_what_is_wrong(qapp, conn):
    host, screen = _screen()
    screen.load(conn, WORLD, "aurelia", [
        {"type": "teleport", "summary": "a"},
        {"type": "other", "summary": "b"},
        {"type": "raise_units", "summary": "c", "unit_kind": "infantry"},
    ])
    shown = " | ".join(_texts(screen))
    assert "1 will be attempted" in shown
    assert "1 cannot be" in shown
    assert "1 will do nothing" in shown


def test_the_screen_says_which_kinds_it_can_judge(qapp, conn):
    host, screen = _screen()
    screen.load(conn, WORLD, "aurelia", [
        {"type": "raise_units", "summary": "c", "unit_kind": "infantry"}])
    shown = " | ".join(_texts(screen))
    assert "gambit" in shown, "the prevalidated list must be stated, not implied"
    assert "No verdict was available" in shown, "and the unjudged card must say so"


def test_nothing_pending_is_a_sentence_not_an_empty_box(qapp, conn):
    host, screen = _screen()
    screen.load(conn, WORLD, "aurelia", [])
    assert any("Nothing pending" in text for text in _texts(screen))


def test_a_spectator_is_told_why_it_is_empty(qapp, conn):
    host, screen = _screen()
    screen.load(conn, WORLD, None, [])
    assert any("spectating" in text for text in _texts(screen))


def test_opening_the_screen_changes_nothing(qapp, conn):
    before = fingerprint(conn, WORLD)
    host, screen = _screen()
    screen.load(conn, WORLD, "aurelia", [
        {"type": "gambit", "summary": "s", "gambit_kind": "summit",
         "target_nation": "karthos"}])
    screen.open()
    qapp.processEvents()
    assert fingerprint(conn, WORLD) == before


def test_reloading_does_not_stack_duplicate_cards(qapp, conn):
    host, screen = _screen()
    actions = [{"type": "raise_units", "summary": "c", "unit_kind": "infantry"}]
    screen.load(conn, WORLD, "aurelia", actions)
    first = len(_texts(screen))
    for _ in range(3):
        screen.load(conn, WORLD, "aurelia", actions)
        qapp.processEvents()
    assert len(_texts(screen)) == first


def test_both_themes_apply_without_throwing(qapp, conn):
    host, screen = _screen()
    screen.load(conn, WORLD, "aurelia", [
        {"type": "teleport", "summary": "a"},
        {"type": "raise_units", "summary": "c", "unit_kind": "infantry"}])
    for theme in (LIGHT, DARK, LIGHT):
        screen.apply_theme(theme)
        qapp.processEvents()


def test_the_topbar_offers_the_workbench(qapp):
    from atlas.ui.topbar import TopBar

    bar = TopBar(DARK)
    seen: list[bool] = []
    bar.workbench_requested.connect(lambda: seen.append(True))
    bar._workbench.clicked.emit()
    assert seen == [True]


def test_the_window_reviews_the_orders_a_jump_would_carry():
    """It must read `_pending_orders` -- the actions the parser produced -- rather than
    re-parsing, or the preview could review something the turn will not run."""
    import inspect

    from atlas.ui.main_window import AtlasWindow

    assert "workbench_requested.connect(self._open_workbench)" in inspect.getsource(
        AtlasWindow)
    # The handler itself, not the whole class: splitting on the name finds the `connect`
    # line first, and an assertion about the wrong four hundred characters is an assertion
    # about nothing.
    handler = inspect.getsource(AtlasWindow._open_workbench)
    # It loads through `_load_workbench`, which the refresh after a turn shares; the read of
    # `_pending_orders` is there, and following the one call is what checks it.
    loader = inspect.getsource(AtlasWindow._load_workbench)
    assert "self._load_workbench()" in handler
    assert "self._pending_orders" in loader
    assert "self._workbench.open()" in handler
