"""A target without a lever is another kind of invisible.

Making the victory conditions visible got them read and did not get them pursued. Measured
over forty turns after that landed: the leader's score sat flat at 0.5 and never opened more
than a **1.11x** lead on the runner-up, against the **2.00x** hegemony asks for.

So this is the other half. `_victory_lines` says what winning is; `_standing_lines` says
where you are against everybody else, which of the five components has the most score still
in it, and what channel moves it. Separately the first is a poster.

**What it cannot fix, and the measurement that says so.** Running every power flat out on
every component it can actually move -- building, researching, reinforcing, digging for men
-- carries the leader's ratio from 1.11 to **1.16** over forty turns, and fires no victories.
Ground is 34% of the score and moves only by conquest, and a tracked power has no channel
that starts a war: `declarations` is world-sim only and `relation.at_war` is engine-only. The
dominant component is not a decision any AI power gets to make, which is why the pack never
separates. That is a design question and it is recorded here rather than papered over.
"""

from __future__ import annotations

import sqlite3

import pytest

from atlas.engine.views import _standing_lines
from atlas.scenarios.sandbox import sandbox_seed
from atlas.state.bootstrap import create_world
from atlas.state.diff import diff_batch_schema
from atlas.state.models import Role

WORLD = "sandbox"
NATION = "aurelia"


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = create_world(":memory:", sandbox_seed())
    connection.execute("UPDATE world SET turn_number = 1 WHERE id = ?", (WORLD,))
    connection.commit()
    yield connection
    connection.close()


def _text(conn, nation: str = NATION) -> str:
    return "\n".join(_standing_lines(conn, WORLD, nation))


def test_a_power_is_told_its_rank_and_the_leader(conn):
    """Every condition in `scoring` is comparative -- a share of the world, a multiple of
    the runner-up -- so a power's own number tells it nothing without the one beside it."""
    text = _text(conn)
    assert "Where you stand" in text
    assert "of" in text and "by the engine's own reckoning" in text


def test_all_five_components_are_shown_with_what_each_is_worth(conn):
    """A staff that cannot see the weights cannot tell which gap is worth closing."""
    text = _text(conn)
    for component in ("ground", "wealth", "arms", "standing", "knowledge"):
        assert component in text, component
    assert "34%" in text, "the weights have to be on the page, not just the values"


def test_the_lever_named_is_where_the_most_score_is_left(conn):
    """**The first draft got this backwards and pointed everyone at their smallest number.**

    It took the lowest *contribution*, which sent India to knowledge -- worth 8% of the
    total and already exhausted -- while it sat on 0.12 of a ground component worth 34%.
    What matters is headroom times weight: where the score still is, not where it is least.
    """
    text = _text(conn)
    assert "most score still available" in text
    # Ground is the heaviest component and no power on any shipped map holds much of the
    # scale, so it is the honest answer for nearly everybody -- which is why a second is
    # named beside it.
    assert "and after it" in text


def test_a_power_is_given_a_channel_and_not_just_a_diagnosis(conn):
    """The levers are named in the vocabulary of channels the briefing already offered."""
    text = _text(conn)
    assert any(channel in text for channel in
               ("`build_orders`", "`raise_formations`", "`military_objectives`",
                "`reinforce`"))


def test_the_dominant_component_is_not_a_decision_any_power_can_take(conn):
    """**The ceiling, asserted so it cannot quietly stop being true.**

    Ground is the largest component and moves by conquest. No tracked power can start a
    war: the declaration channel belongs to the world simulator alone. If that ever
    changes, this test should fail and somebody should re-measure whether the pack can
    separate -- because that is the whole reason it currently cannot.
    """
    tracked = set(diff_batch_schema(role=Role.TRACKED_AGENT)["properties"])
    world_sim = set(diff_batch_schema(role=Role.WORLD_SIM)["properties"])

    assert "declarations" in world_sim
    assert "declarations" not in tracked


def test_a_world_with_nobody_else_in_it_says_nothing(conn):
    """Comparative by construction: one power is not a standing."""
    conn.execute(
        "UPDATE nation SET is_alive = 0 WHERE world_id = ? AND id <> ?", (WORLD, NATION))
    conn.commit()
    assert _text(conn) == ""
