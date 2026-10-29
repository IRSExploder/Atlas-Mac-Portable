"""The turn report is news from inside the world, not the referee describing the game.

Found by a live run on Claude Haiku 5.5, which wrote "Whether Tehran accepts is a matter for
the engine to resolve" and "Ukraine now stands 62% of the way to taking the province" into
the report a player reads. The briefings now ask for world-only prose, and `in_world` is the
net under that request.
"""

from __future__ import annotations

from atlas.engine.untrusted import in_world
from atlas.engine.views import build_world_sim_view


def test_a_sentence_about_the_machinery_is_dropped():
    said = ("Ankara has put its terms to Tehran. Whether Tehran accepts is a matter for the "
            "engine to resolve against how the war has gone.")
    assert in_world(said) == "Ankara has put its terms to Tehran."
    assert "turn" not in in_world("Fighting went on. Next turn the line may break.")


def test_a_pressure_figure_is_said_in_words():
    assert in_world("Ukraine stands 62% of the way to taking the province.") == (
        "Ukraine stands well on the way to taking the province.")
    assert "close to" in in_world("Russia is 85 per cent of the way to Kharkiv.")


def test_prose_with_nothing_to_drop_is_untouched_and_nothing_is_never_the_answer():
    plain = "Turkish columns press east. Tehran must choose where to hold."
    assert in_world(plain) == plain
    assert in_world("The engine decides.") == "The engine decides."


def test_the_simulator_is_told_to_write_from_inside_the_world(conn):
    text = build_world_sim_view(conn, "sandbox").text
    assert "news from inside the world" in text and "Never mention the engine" in text
