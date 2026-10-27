"""Intelligence reveals real state imperfectly, and never lets anybody invent it.

**The load-bearing test in this file is the one that reads the whole briefing and asserts
the true figure is not in it.** Everything else here is a property of the estimate machinery
-- monotone accuracy, stable draws, sensible defaults -- and those matter, but they are
guarding the one guarantee: the engine owns what is true, computes what each nation can
believe about it, and hands over the belief. A model acts on the estimate. It cannot read
the value, cannot derive it from the band, and has no channel to write either.

The failure being guarded is not a crash. It is a briefing that reports a range and, three
lines up, the number the range was built from -- at which point the whole subsystem is
decoration and a model is playing with perfect information while appearing not to.
"""

from __future__ import annotations

import sqlite3

import pytest

from atlas.engine.intelligence import (
    BANDS,
    MAX_ERROR,
    MIN_ERROR,
    RESTING,
    assess_networks,
    band_of,
    estimate_of,
    estimates_for,
    network_between,
    network_id_for,
    networks_of,
)
from atlas.engine.views import build_tracked_agent_view
from atlas.scenarios.sandbox import sandbox_seed
from atlas.state.apply import PRIMARY_KEY
from atlas.state.bootstrap import create_world, fingerprint
from atlas.state.replay import rewind_to

WORLD = "sandbox"
A, B, C = "aurelia", "karthos", "veyra"


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = create_world(":memory:", sandbox_seed())
    connection.execute("UPDATE world SET turn_number = 1 WHERE id = ?", (WORLD,))
    connection.commit()
    yield connection
    connection.close()


def _network(conn, viewer: str, target: str, strength: float) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO intel_network (id, world_id, nation_id,"
        " target_nation_id, strength, opened_turn, last_turn, because)"
        " VALUES (?, ?, ?, ?, ?, 1, 1, 'the fixture said so')",
        (network_id_for(viewer, target), WORLD, viewer, target, strength))
    conn.commit()


def _truth(conn, nation: str, column: str = "military_strength") -> float:
    return float(conn.execute(
        f"SELECT {column} FROM nation WHERE id = ?", (nation,)).fetchone()[0])


# ------------------------------------------------------------------ networks by target


def test_a_network_exists_for_every_pair_without_a_row_for_every_pair(conn):
    """**Lazy, like the constituency store, and for the same reason.** 243 nations against
    243 targets is fifty-nine thousand rows of nobody watching anybody; a pair that has
    never moved answers with its seeded default and stores nothing."""
    assert conn.execute("SELECT COUNT(*) FROM intel_network").fetchone()[0] == 0

    network = network_between(conn, WORLD, A, B)
    assert 0 < network.strength <= 100
    assert network.band in {name for _edge, name in BANDS}
    assert conn.execute("SELECT COUNT(*) FROM intel_network").fetchone()[0] == 0


def test_intelligence_is_a_relationship_and_not_a_stat(conn):
    """The pairing is the design: a country reads one capital well and another badly, and
    a single number on the nation row could not have said so."""
    _network(conn, A, B, 90.0)
    _network(conn, A, C, 10.0)

    assert network_between(conn, WORLD, A, B).band == "excellent"
    assert network_between(conn, WORLD, A, C).band == "blind"
    # ...and it is directional. B reading A is a different question from A reading B.
    assert network_between(conn, WORLD, B, A).strength != 90.0


def test_a_service_starts_where_the_relationship_puts_it(conn):
    """Derived rather than authored per scenario, so every era and every mod gets sensible
    starting intelligence for free: you watch your neighbours, you watch your enemies
    harder, and a rich state can afford attaches everywhere."""
    quiet = network_between(conn, WORLD, A, B).strength
    conn.execute(
        "UPDATE relation SET at_war = 1 WHERE world_id = ? AND nation_a = ?"
        "   AND nation_b = ?", (WORLD, A, B))
    conn.commit()

    assert network_between(conn, WORLD, A, B).strength > quiet
    assert network_between(conn, WORLD, A, B).strength >= RESTING


def test_networks_of_ranks_the_ones_worth_reading(conn):
    _network(conn, A, C, 95.0)
    found = networks_of(conn, WORLD, A)

    assert [n.target_id for n in found][0] == C
    assert A not in [n.target_id for n in found], "a country does not spy on itself"


# ---------------------------------------------------- estimates over real state


def test_an_estimate_brackets_something_near_the_truth(conn):
    """It has to be *about* the real figure, or the layer is a random number generator
    with a nation attached."""
    _network(conn, A, B, 70.0)
    truth = _truth(conn, B)
    guess = estimate_of(conn, WORLD, viewer=A, target=B, subject="army", value=truth,
                        turn=1)

    assert guess.low < guess.high
    assert abs((guess.low + guess.high) / 2 - truth) < truth * MAX_ERROR * 2


def test_a_better_network_reports_a_tighter_band(conn):
    """The whole point of building one. Monotone across the scale, not merely at the ends,
    because a player investing in a service should see it pay every step of the way."""
    truth = _truth(conn, B)
    widths = []
    for strength in (0.0, 25.0, 50.0, 75.0, 100.0):
        _network(conn, A, B, strength)
        guess = estimate_of(conn, WORLD, viewer=A, target=B, subject="army",
                            value=truth, turn=1)
        widths.append(guess.high - guess.low)

    assert widths == sorted(widths, reverse=True), widths
    assert widths[0] > widths[-1]


def test_even_the_best_service_in_the_world_is_still_wrong(conn):
    """**`MIN_ERROR` is not a rounding detail.** A top end that hands over the exact number
    would have spent a whole subsystem re-deriving the leak it was built to close."""
    _network(conn, A, B, 100.0)
    truth = _truth(conn, B)
    guess = estimate_of(conn, WORLD, viewer=A, target=B, subject="army", value=truth,
                        turn=1)

    assert guess.high - guess.low > 0
    assert guess.high - guess.low >= truth * MIN_ERROR


def test_the_band_is_not_centred_on_the_truth(conn):
    """**The subtle failure this layer would otherwise have shipped.** A symmetric interval
    hands the real number to anybody who averages the ends, which makes the noise
    decorative. The centre is displaced first and the band opened around the displacement,
    so the midpoint is wrong too -- and wrong in a different direction for each question.
    """
    _network(conn, A, B, 60.0)
    midpoints = []
    for subject, column in (("army", "military_strength"), ("economy", "economy"),
                            ("industry", "industry")):
        truth = _truth(conn, B, column)
        guess = estimate_of(conn, WORLD, viewer=A, target=B, subject=subject,
                            value=truth, turn=1)
        midpoints.append(abs((guess.low + guess.high) / 2 - truth))

    assert any(gap > 0.5 for gap in midpoints), (
        "every midpoint landed on the truth; the displacement is not doing anything")


def test_two_services_looking_at_the_same_country_see_different_things(conn):
    """There is no such thing as *the* estimate of a nation's army. There is only what
    somebody's service believes, which is why the noise is keyed on the viewer."""
    _network(conn, A, C, 30.0)
    _network(conn, B, C, 30.0)
    truth = _truth(conn, C)

    mine = estimate_of(conn, WORLD, viewer=A, target=C, subject="army", value=truth,
                       turn=1)
    theirs = estimate_of(conn, WORLD, viewer=B, target=C, subject="army", value=truth,
                         turn=1)

    assert (mine.low, mine.high) != (theirs.low, theirs.high)


def test_the_same_question_asked_twice_gets_the_same_answer(conn):
    """Seeded on what is being asked and never on call order, so a briefing assembled
    twice reads identically and a rewind reproduces the same wrong guess."""
    _network(conn, A, B, 40.0)
    truth = _truth(conn, B)
    first = estimate_of(conn, WORLD, viewer=A, target=B, subject="army", value=truth,
                        turn=1)
    second = estimate_of(conn, WORLD, viewer=A, target=B, subject="army", value=truth,
                         turn=1)

    assert (first.low, first.high) == (second.low, second.high)


def test_an_estimate_carries_no_way_back_to_the_truth(conn):
    """The class has no `true_value` field, and that is deliberate rather than incidental
    -- the same argument `visibility` makes about an unscoped reader. Its existence would
    be the leak, whoever promised not to use it."""
    truth = _truth(conn, B)
    guess = estimate_of(conn, WORLD, viewer=A, target=B, subject="army", value=truth,
                        turn=1)

    assert not hasattr(guess, "true_value")
    assert not any(
        isinstance(getattr(guess, field), float) and getattr(guess, field) == truth
        for field in ("low", "high"))


def test_estimates_cover_what_a_foreign_ministry_argues_about_and_no_more(conn):
    """Not a dump of the nation row with noise on it: an estimate of every column would be
    the leak back again, only fuzzier."""
    reported = estimates_for(conn, WORLD, viewer=A, target=B, turn=1)

    assert {e.subject for e in reported} == {"army", "economy", "industry"}
    assert all(e.confidence in {name for _edge, name in BANDS} for e in reported)


# ------------------------------------------------- the guarantee, at the briefing


def test_the_briefing_carries_the_estimate_and_never_the_value(conn):
    """**The load-bearing test.** A model reading its whole turn must not be able to find
    what is secretly true about a rival anywhere in it.

    Checked against the *rendered* briefing rather than against the estimate object,
    because that is what a model actually receives -- and a leak would arrive through some
    other section of it, not through the one that knows it is an estimate.
    """
    conn.execute("UPDATE nation SET military_strength = 63.7 WHERE id = ?", (B,))
    conn.execute("UPDATE nation SET economy = 41.3 WHERE id = ?", (B,))
    conn.commit()
    _network(conn, A, B, 55.0)

    view = build_tracked_agent_view(conn, WORLD, nation_id=A)
    text = view.text if hasattr(view, "text") else str(view)

    assert "What your services can tell you" in text
    assert "63.7" not in text, "the rival's true army strength is in the briefing"
    assert "41.3" not in text, "the rival's true economy is in the briefing"
    # And its own figures still are: a country knows what it has.
    assert f"{_truth(conn, A):.1f}" in text


def test_a_viewer_with_a_blind_service_is_told_a_vaguer_story(conn):
    """The word in the power roster comes from the estimate too, so two nations looking at
    the same country from different services can honestly disagree about it.

    **The line describes the reports rather than the service**, and with nobody sweeping
    the two are the same thing -- an unswept excellent network produces excellent reports.
    They come apart only under deception, which is what `views._how_sure` exists for and
    what `test_a_service_that_can_be_defended_against` covers.
    """
    _network(conn, A, B, 100.0)
    sharp = build_tracked_agent_view(conn, WORLD, nation_id=A)
    _network(conn, A, B, 0.0)
    blind = build_tracked_agent_view(conn, WORLD, nation_id=A)

    def band_line(view) -> str:
        text = view.text if hasattr(view, "text") else str(view)
        return next(line for line in text.splitlines() if "karthos" in line
                    and "report" in line)

    assert "excellent" in band_line(sharp)
    assert "blind" in band_line(blind)


def test_no_model_can_write_its_own_intelligence(conn):
    """Engine-only, and the guard file that demands every such table name its door has the
    entry. Asserted here too because this is the table where it matters most: a power that
    could write its own network would award itself perfect sight of a rival."""
    from atlas.state.permissions import ENGINE_ONLY

    guarded = {column for table, column in ENGINE_ONLY if table == "intel_network"}
    assert {"strength", "nation_id", "target_nation_id"} <= guarded


# --------------------------------------------------------------- state, and rewind


def test_a_network_is_written_as_a_diff_and_rewinds_exactly(conn):
    """Nothing here is stored outside the pipeline, so the intelligence picture goes back
    with everything else -- which matters more here than almost anywhere: a rewind that
    left a service knowing what it learned in a turn that no longer happened is the exact
    leak the espionage grant was found to have."""
    _network(conn, A, B, 95.0)
    before = fingerprint(conn, WORLD)
    conn.execute("UPDATE world SET turn_number = 2 WHERE id = ?", (WORLD,))
    conn.commit()

    assess_networks(conn, WORLD, turn=2, date="1897-03-01", days=30)
    assert fingerprint(conn, WORLD) != before, "the drift wrote nothing at all"

    rewind_to(conn, WORLD, 1)
    conn.execute("UPDATE world SET turn_number = 1 WHERE id = ?", (WORLD,))
    conn.commit()
    assert fingerprint(conn, WORLD) == before


def test_a_raised_network_settles_back_toward_what_the_relationship_sustains(conn):
    """Drift only, in this brick: nothing here builds a network up or tears one down. A
    service raised for a war that has ended goes back to what a country knows without
    trying, slowly enough that it is worth having built."""
    _network(conn, A, B, 95.0)
    floor = 95.0
    for turn in range(2, 8):
        assess_networks(conn, WORLD, turn=turn, date="1897-03-01", days=30)
        now = network_between(conn, WORLD, A, B).strength
        assert now < floor
        floor = now

    assert floor > 60.0, "a decade of drift in six months is not a network, it is a mood"


def test_a_world_that_has_never_had_a_turn_carries_no_rows_and_reads_the_same(conn):
    """A fresh save reads sensible defaults, which is what makes the lazy write invisible.
    Same contract the constituency store has."""
    fresh = network_between(conn, WORLD, A, B)
    assess_networks(conn, WORLD, turn=2, date="1897-03-01", days=30)

    assert conn.execute("SELECT COUNT(*) FROM intel_network").fetchone()[0] == 0
    assert network_between(conn, WORLD, A, B).strength == fresh.strength


def test_the_table_is_registered_everywhere_a_table_has_to_be(conn):
    """A table the pipeline does not know the key of cannot be written through it, and one
    the digest does not cover is one a rewind can silently fail to restore -- which is how
    the espionage grant leaked before `event_visibility` was fingerprinted."""
    from atlas.state.bootstrap import _FINGERPRINT_TABLES

    assert PRIMARY_KEY["intel_network"] == ("id",)
    assert "intel_network" in {table for table, _order, _scope in _FINGERPRINT_TABLES}


def test_the_bands_read_like_something_a_ministry_would_say(conn):
    assert band_of(0.0) == "blind"
    assert band_of(100.0) == "excellent"
    assert band_of(RESTING) in {"patchy", "workable"}


def test_having_nothing_is_not_perfectly_observable(conn):
    """**A leak found by probing the edges.** The noise is multiplicative, so a true figure
    of zero produced a band of 0-0 whatever the network: "they have no army" was the one
    fact every service in the world knew exactly, and disarmament was the only perfectly
    visible act in the game -- which is precisely backwards, since a disarmed country is
    the hardest thing to be sure about.

    The band is now opened against a floor, so a blind service says "somewhere between
    nothing and a little" and only a good one is confident there is nothing there.
    """
    _network(conn, A, B, 0.0)
    blind = estimate_of(conn, WORLD, viewer=A, target=B, subject="army", value=0.0,
                        turn=1)
    _network(conn, A, B, 100.0)
    sharp = estimate_of(conn, WORLD, viewer=A, target=B, subject="army", value=0.0,
                        turn=1)

    assert blind.high > 0, "a blind service was certain a country had no army"
    assert sharp.high <= blind.high


def test_the_floor_does_not_widen_an_ordinary_estimate(conn):
    """The floor is for the small numbers it was added for. A real army is estimated as a
    share of itself, exactly as before, or the fix would have quietly recalibrated every
    band in the game."""
    _network(conn, A, B, 50.0)
    truth = _truth(conn, B)
    guess = estimate_of(conn, WORLD, viewer=A, target=B, subject="army", value=truth,
                        turn=1)

    assert guess.high - guess.low < truth, guess
