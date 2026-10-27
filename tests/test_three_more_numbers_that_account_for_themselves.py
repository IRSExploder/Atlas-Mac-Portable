"""Why your position is what it is, why this war is tiring you, and why they are suspected.

Three more values through the same discipline as the coup risk and the refused peace:
**nothing is computed for the occasion**. Every figure is re-derived by calling the function
the engine calls with the same constants, and the only thing added is naming the terms.

The three were chosen because each is a number a player acts on and cannot see inside:

* **A bargaining position** is the one value that already carried its own breakdown.
  `leverage_between` has always returned four weighted components, each with a sentence
  saying what it is about, and every caller threw them away and kept the total. So this is a
  translation rather than a derivation.
* **War exhaustion** decides whether a peace gets signed, and until now it was a number that
  went up.
* **An attribution tier** is a threshold on a contest between two services, and a player
  looking at *suspected* has no way to tell whether that is one turn from *likely* or twenty.

The load-bearing property is the same one: **the terms sum to the number the engine uses.**
Where the engine rounds its own headline, the terms sum to what it rounded — which is stated
in the test rather than papered over with a loose tolerance.
"""

from __future__ import annotations

import sqlite3

import pytest

from atlas.engine.leverage import leverage_between
from atlas.engine.why import (
    why_a_bargaining_position,
    why_they_are_suspected,
    why_this_war_is_exhausting,
)
from atlas.scenarios.sandbox import sandbox_seed
from atlas.state.bootstrap import create_world, fingerprint

WORLD = "sandbox"
ME, THEM = "aurelia", "karthos"


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = create_world(":memory:", sandbox_seed())
    connection.execute("UPDATE world SET turn_number = 4 WHERE id = ?", (WORLD,))
    for a, b in ((ME, THEM), (THEM, ME)):
        connection.execute(
            "UPDATE relation SET at_war = 1 WHERE world_id = ? AND nation_a = ?"
            "   AND nation_b = ?", (WORLD, a, b))
    connection.commit()
    yield connection
    connection.close()


# --------------------------------------------------------------- a bargaining position


def test_the_terms_sum_to_what_the_engine_computed(conn):
    """**The property, and the one place it needs a caveat.** `Leverage.position` is the
    sum of its components' contributions *rounded to four places*, so the terms sum to what
    the engine rounded rather than to the rounded figure. Stated here rather than hidden
    behind a loose tolerance, because the difference is the engine's own rounding and not
    slack in the breakdown."""
    standing = leverage_between(conn, WORLD, ME, THEM)
    answer = why_a_bargaining_position(conn, WORLD, nation_id=ME, enemy_id=THEM)

    unrounded = sum(component.contribution for component in standing.components)

    assert sum(r.worth for r in answer.reasons) == pytest.approx(unrounded, abs=1e-12)
    assert answer.value == standing.position
    assert abs(answer.value - unrounded) < 5e-5


def test_it_names_the_four_the_engine_weighs(conn):
    named = {r.what for r in why_a_bargaining_position(
        conn, WORLD, nation_id=ME, enemy_id=THEM).reasons}

    assert named == {"ground", "weariness", "force", "aims"}


def test_it_keeps_the_engines_own_sentence_for_each(conn):
    """The components carry a `because` already; inventing a second phrasing here would be
    two vocabularies for one idea."""
    standing = leverage_between(conn, WORLD, ME, THEM)
    answer = why_a_bargaining_position(conn, WORLD, nation_id=ME, enemy_id=THEM)
    spoken = {c.name: c.because for c in standing.components if c.because}

    for reason in answer.reasons:
        if reason.what in spoken:
            assert reason.because == spoken[reason.what]


def test_every_term_says_what_would_move_it(conn):
    answer = why_a_bargaining_position(conn, WORLD, nation_id=ME, enemy_id=THEM)

    assert all(reason.lever for reason in answer.reasons)


def test_the_verdict_uses_the_engines_own_band(conn):
    standing = leverage_between(conn, WORLD, ME, THEM)
    answer = why_a_bargaining_position(conn, WORLD, nation_id=ME, enemy_id=THEM)

    assert standing.band in answer.verdict
    assert answer.rule == "leverage"


# ---------------------------------------------------------------------- war exhaustion


def _occupy(conn: sqlite3.Connection, holder: str, home: str) -> int:
    rows = conn.execute(
        "SELECT id FROM region WHERE world_id = ? AND home_nation_id = ?"
        "   AND kind <> 'ocean'", (WORLD, home)).fetchall()
    for row in rows:
        conn.execute("UPDATE region SET controller_nation_id = ? WHERE id = ?",
                     (holder, str(row["id"])))
    conn.commit()
    return len(rows)


def test_a_quiet_war_still_costs_a_month_of_patience(conn):
    """The flat charge is the whole of it when nothing is happening, and it should read
    that way rather than as a mystery."""
    answer = why_this_war_is_exhausting(conn, WORLD, nation_id=ME, enemy_id=THEM)

    assert answer.value > 0
    assert any("the fighting itself" in reason.what for reason in answer.reasons)
    assert answer.rule == "war_effort"


def test_occupied_homeland_is_named_and_is_the_biggest_term(conn):
    if not _occupy(conn, THEM, ME):
        pytest.skip("this fixture has no home provinces to occupy")

    answer = why_this_war_is_exhausting(conn, WORLD, nation_id=ME, enemy_id=THEM)
    biggest = max(answer.reasons, key=lambda reason: reason.worth)

    assert "they hold" in biggest.what


def test_an_empty_treasury_shows_up_as_its_own_term(conn):
    conn.execute("UPDATE nation SET treasury = 0 WHERE world_id = ? AND id = ?",
                 (WORLD, ME))
    conn.commit()

    named = {r.what for r in why_this_war_is_exhausting(
        conn, WORLD, nation_id=ME, enemy_id=THEM).reasons}

    assert "a treasury that cannot pay" in named


def test_a_shaky_country_pays_more_for_the_same_war(conn):
    """Fragility is a multiplication, and it is reported as the difference it made -- for
    the reason `why_a_coup` reports its three that way."""
    conn.execute("UPDATE nation SET stability = 100 WHERE world_id = ? AND id = ?",
                 (WORLD, ME))
    conn.commit()
    steady = why_this_war_is_exhausting(conn, WORLD, nation_id=ME, enemy_id=THEM).value

    conn.execute("UPDATE nation SET stability = 10 WHERE world_id = ? AND id = ?",
                 (WORLD, ME))
    conn.commit()
    shaky = why_this_war_is_exhausting(conn, WORLD, nation_id=ME, enemy_id=THEM)

    assert shaky.value > steady
    assert any("shaky" in reason.what for reason in shaky.reasons)
    assert sum(r.worth for r in shaky.reasons) == pytest.approx(shaky.value, abs=1e-9)


def test_a_steady_country_is_not_charged_a_fragility_it_does_not_have(conn):
    conn.execute("UPDATE nation SET stability = 100 WHERE world_id = ? AND id = ?",
                 (WORLD, ME))
    conn.commit()

    answer = why_this_war_is_exhausting(conn, WORLD, nation_id=ME, enemy_id=THEM)

    assert not any("shaky" in reason.what for reason in answer.reasons)


def test_a_nation_that_does_not_exist_is_answered_plainly(conn):
    answer = why_this_war_is_exhausting(conn, WORLD, nation_id="atlantis",
                                        enemy_id=THEM)

    assert answer.value == 0.0
    assert "no atlantis" in answer.verdict


# -------------------------------------------------------------------- an attribution


def _file_on(conn: sqlite3.Connection, *, event_id: int, confidence: float,
             tier: str) -> None:
    from atlas.engine.attribution import attribution_id_for
    from atlas.state.apply import record_event
    from atlas.state.models import EventKind, EventOrigin, Visibility

    written = record_event(
        conn, world_id=WORLD, turn=2, date="1936-02-01", kind=EventKind.NARRATIVE,
        origin=EventOrigin.ENGINE, actor_nation_id=THEM, text="something quiet happened",
        visibility=Visibility.SECRET)
    conn.execute("UPDATE event SET id = ? WHERE id = ?", (event_id, written))
    conn.execute(
        "INSERT INTO attribution (id, world_id, event_id, actor_nation_id,"
        " victim_nation_id, confidence, tier, opened_turn, last_turn)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, 2, 4)",
        (attribution_id_for(event_id, ME), WORLD, event_id, THEM, ME, confidence, tier))
    conn.commit()


def test_a_file_nobody_has_opened_is_said_to_be_empty(conn):
    answer = why_they_are_suspected(conn, WORLD, victim_id=ME, event_id=404)

    assert answer.value == 0.0
    assert "no file" in answer.verdict


def test_it_names_both_halves_of_the_contest(conn):
    """A tier is a threshold on what your services can do against what theirs can hide,
    and a player shown only one half cannot tell which way to spend."""
    _file_on(conn, event_id=501, confidence=40.0, tier="suspected")

    named = {r.what for r in why_they_are_suspected(
        conn, WORLD, victim_id=ME, event_id=501).reasons}

    assert "what your services can do" in named
    assert "what theirs can hide" in named


def test_it_says_how_far_the_next_tier_is(conn):
    """**The question the tier itself cannot answer.** A player looking at *suspected* has
    no way to tell whether that is one turn from *likely* or twenty."""
    from atlas.engine.attribution import LIKELY_AT

    _file_on(conn, event_id=502, confidence=40.0, tier="suspected")

    answer = why_they_are_suspected(conn, WORLD, victim_id=ME, event_id=502)
    gap = next(r for r in answer.reasons if "next tier" in r.what)

    assert gap.worth == pytest.approx(LIKELY_AT - 40.0)


def test_a_proven_file_has_no_further_tier_to_reach(conn):
    _file_on(conn, event_id=503, confidence=95.0, tier="proven")

    answer = why_they_are_suspected(conn, WORLD, victim_id=ME, event_id=503)

    assert not any("next tier" in reason.what for reason in answer.reasons)
    assert "proven" in answer.verdict


def test_the_value_is_the_confidence_the_engine_recorded(conn):
    _file_on(conn, event_id=504, confidence=61.5, tier="likely")

    assert why_they_are_suspected(
        conn, WORLD, victim_id=ME, event_id=504).value == pytest.approx(61.5)


def test_it_answers_from_the_victims_seat_only(conn):
    """The same scoping `attributions_for` runs on: what a country has worked out is its
    own, and an unscoped reader would be the leak."""
    _file_on(conn, event_id=505, confidence=61.5, tier="likely")

    assert why_they_are_suspected(
        conn, WORLD, victim_id=THEM, event_id=505).value == 0.0


# ------------------------------------------------------------------------- and read-only


def test_asking_any_of_the_three_changes_nothing(conn):
    _file_on(conn, event_id=601, confidence=40.0, tier="suspected")
    before = fingerprint(conn, WORLD)

    why_a_bargaining_position(conn, WORLD, nation_id=ME, enemy_id=THEM)
    why_this_war_is_exhausting(conn, WORLD, nation_id=ME, enemy_id=THEM)
    why_they_are_suspected(conn, WORLD, victim_id=ME, event_id=601)

    assert fingerprint(conn, WORLD) == before


def test_all_five_answers_render_as_something_a_person_reads(conn):
    _file_on(conn, event_id=602, confidence=40.0, tier="suspected")

    for answer in (
        why_a_bargaining_position(conn, WORLD, nation_id=ME, enemy_id=THEM),
        why_this_war_is_exhausting(conn, WORLD, nation_id=ME, enemy_id=THEM),
        why_they_are_suspected(conn, WORLD, victim_id=ME, event_id=602),
    ):
        drawn = answer.rendered()
        assert drawn and drawn[0]
        assert any(answer.rule in line for line in drawn)
