"""A figure on the glass is rounded, carries its scale, and never loses the original.

`atlas.humanize` is the one place a stored number becomes a shown one. What is asserted
here is the contract every panel depends on: that the noise goes, that a movement too small
to print does not print as ``+0``, that a regard is read out on the scale it lives on, and
that the exact figure survives the rounding somewhere a reader can reach it.

The last of those is the one worth having a test for. Rounding is easy to add and easy to
add too much of; a screen that rounds a treasury to 38 and then has no way to say 37.83 has
not made the game more readable, it has deleted a number the player was entitled to.
"""

from __future__ import annotations

import pytest

from atlas.engine.cast import CastMember
from atlas.humanize import (
    band,
    count,
    duration,
    exact,
    figure,
    movement,
    percent,
    points,
    ratio,
    regard,
    regard_word,
    signed,
    strength,
)

# --------------------------------------------------------------------------- quantities


def test_a_figure_loses_its_float_noise_and_keeps_its_grouping():
    # The economy of Russia on turn 14 of the PT31 world, exactly as stored.
    assert figure(47.269999999999996) == "47"
    assert figure(148.85999999999999) == "149"
    assert figure(37.83) == "38"
    assert count(4951) == "4,951"


@pytest.mark.parametrize("value", [-0.2, -0.4, -0.0001, 0.0])
def test_no_figure_is_ever_negative_zero(value):
    """``-0`` is not a number, and it was on the top bar six times over."""
    assert not figure(value).startswith("-")
    assert signed(value) in {"0", "-0.2", "-0.4"} or not signed(value).startswith("-0")
    assert signed(0.0) == "0"


def test_a_movement_says_steady_or_says_how_far_but_never_plus_zero():
    assert movement(0.0) == "steady"
    assert movement(0.03) == "steady"
    # The case the fixed precision could not express: a real move under a whole point.
    assert movement(-0.2) == "-0.2"
    assert movement(0.2) == "+0.2"
    assert movement(2.0) == "+2"
    assert movement(-6.4) == "-6"


def test_the_exact_figure_survives_the_rounding():
    """What a tooltip carries. Not the float's own repr, and not the rounded reading."""
    assert exact(47.269999999999996) == "47.27"
    assert exact(148.85999999999999) == "148.86"
    assert exact(0.2902) == "0.2902"
    assert exact(0) == "0"


def test_a_force_keeps_a_decimal_only_while_it_is_small():
    """A garrison of 0.27 rounded to a whole number would read as no garrison."""
    assert strength(26.97) == "27"
    assert strength(2.31) == "2.3"
    assert strength(0.27) == "0.3"
    # A whole number keeps no decimal: `6.0` on a map counter is punctuation, not a fact.
    assert strength(6.0) == "6"
    assert strength(2.04) == "2"


# --------------------------------------------------------------------------- proportions


def test_a_share_keeps_a_decimal_while_it_is_small():
    """2.5% and 3% of the world's ground are several countries apart."""
    assert percent(0.025) == "2.5%"
    assert percent(0.2902) == "29%"
    assert percent(0.101) == "10%"


def test_a_multiple_reads_as_a_sentence_rather_than_a_spreadsheet():
    assert ratio(2.0) == "2x"
    assert ratio(0.45) == "0.45x"
    assert ratio(1.10) == "1.1x"


def test_a_score_reads_out_of_a_hundred():
    """The scoring weights sum to 1 so the total is a share; this is that share."""
    assert points(0.2902) == "29.0"
    assert points(0.5183) == "51.8"


# --------------------------------------------------------------------------- durations


def test_a_wait_is_told_in_the_units_people_wait_in():
    assert duration(84.3) == "1m 24s"
    assert duration(12.0) == "12s"
    assert duration(60) == "1m"
    assert duration(4830) == "1h 20m"
    assert duration(0.2) == "under a second"


# --------------------------------------------------------------------------- bands


def test_a_band_names_where_a_figure_sits_without_replacing_it():
    # Stability and legitimacy break at 50 because `leaders._DANGER_BELOW` is 50.
    assert band("stability", 48.92) == "strained"
    assert band("stability", 59.75) == "steady"
    assert band("legitimacy", 46.84) == "contested"
    assert band("economy", 47.27) == "modest"
    assert band("economy", 119.19) == "commanding"
    assert band("military_strength", 95.0) == "formidable"
    # Treasury and manpower have no maximum, so they are banded against the ceiling the
    # meter draws their bar to -- see `atlas.engine.snapshot.METERS`.
    assert band("treasury", 19.32, ceiling=150.0) == "thin"
    assert band("manpower", 148.86, ceiling=200.0) == "deep"
    # A field with no vocabulary says nothing rather than inventing one.
    assert band("rng_seed", 2026) == ""


def test_the_bands_cover_the_whole_scale_with_no_gap():
    for key in ("military_strength", "economy", "stability", "legitimacy"):
        for value in range(-20, 240, 5):
            assert band(key, float(value)), f"{key} had nothing to say about {value}"


# --------------------------------------------------------------------------- regard


def test_a_regard_is_read_out_on_the_scale_it_lives_on():
    """``-0.24`` means nothing to a player. ``-24 of 100, cool`` does."""
    said = regard(-24.0)
    assert "-24" in said
    assert "-100 to 100" in said
    assert said.endswith("neutral")
    assert regard(-80.0).endswith("hostile")
    assert regard(70.0, treaty="non_aggression").endswith("non aggression")
    assert regard(70.0, at_war=True).endswith("at war")


@pytest.mark.parametrize("score", [-100.0, -80.0, -60.0, -25.0, -1.0, 0.0, 24.9, 25.0,
                                   59.9, 60.0, 100.0])
def test_the_presentation_vocabulary_matches_the_engines_own(score):
    """**One vocabulary, asserted rather than maintained.**

    :meth:`atlas.engine.cast.CastMember.standing` is the authority for what a regard is
    called; `humanize.regard_word` is the copy every panel actually calls. They were three
    copies before this pass and are two now, and this is what stops them drifting.
    """
    member = CastMember(nation_id="x", name="X", colour="#fff", since_turn=1, reason="",
                        score=score, trend=0.0, at_war=False, treaty="none")
    assert regard_word(score) == member.standing

    at_war = CastMember(nation_id="x", name="X", colour="#fff", since_turn=1, reason="",
                        score=score, trend=0.0, at_war=True, treaty="none")
    assert regard_word(score, at_war=True) == at_war.standing
