"""A JSON column a model may write cannot be left holding something nobody can read.

**This is a post-mortem, not a precaution.** Round 11 of the rotated Fulda Gap match on
2026-09-20 died like this: a tracked agent playing East Germany set
`nation.standing_goals_json` to the bare string ``hold_line``, no rule objected, the diff
applied -- and the *next* turn raised `JSONDecodeError` out of `TurnEngine.jump` while
building the referee's view. Four readers had the same unguarded `json.loads`. The world
was not wrong, it was unopenable, and every later turn would have died identically.

Two layers are pinned here, and they are not redundant:

* **CORE-006 refuses the write**, which is the fix -- the model is told, and can say it
  properly next turn, rather than having a value it did not write landed on its behalf;
* **`json_list` survives the read**, which is what matters for a world already carrying
  the damage, where the alternative is a save that will not open at all.
"""

from __future__ import annotations

import json

import pytest

from atlas.state.apply import apply_batch, record_event
from atlas.state.bootstrap import NationSeed, RegionSeed, WorldSeed, create_world
from atlas.state.diff import Diff, DiffBatch
from atlas.state.models import (
    Difficulty,
    DiffOp,
    EventKind,
    EventOrigin,
    Role,
    Visibility,
)
from atlas.state.tables import json_list

WORLD = "w_brick"


@pytest.fixture
def conn():
    return create_world(":memory:", WorldSeed(
        id=WORLD, name="Brick", start_date="2026-01-01", rng_seed=7,
        difficulty=Difficulty.NORMAL,
        nations=(
            NationSeed(id="a", name="Ayland", colour="#111", capital_region_id="r_a",
                       economy=50.0),
            NationSeed(id="b", name="Beeland", colour="#222", capital_region_id="r_b",
                       economy=50.0),
        ),
        regions=(
            RegionSeed(id="r_a", name="Ayland Prime", geo_key="g:a", owner_nation_id="a"),
            RegionSeed(id="r_b", name="Beeland Prime", geo_key="g:b", owner_nation_id="b"),
        ),
    ))


def _propose(conn, value, *, role=Role.TRACKED_AGENT):
    event_id = record_event(
        conn, world_id=WORLD, turn=1, date="2026-02-01", kind=EventKind.NARRATIVE,
        origin=EventOrigin.ENGINE, text="a power restated its aims",
        visibility=Visibility.PUBLIC)
    return apply_batch(conn, DiffBatch(
        world_id=WORLD, turn=1, proposer_role=role, proposer_model="a-model",
        proposer_nation_id="a",
        diffs=(Diff("nation", "a", "standing_goals_json", DiffOp.SET, value,
                    reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),)))


# ----------------------------------------------------------------- the write is refused


def test_the_exact_write_that_killed_the_match_is_refused(conn):
    outcome = _propose(conn, "hold_line")

    assert not outcome.applied, "a bare string landed in a JSON column again"
    assert "CORE-006" in str(outcome.reason)
    stored = conn.execute(
        "SELECT standing_goals_json FROM nation WHERE id = 'a'").fetchone()[0]
    assert json.loads(stored) == [], "the refused batch was not rolled back"


def test_a_json_scalar_is_refused_too(conn):
    """`"hold_line"` parses -- as a string. A string is iterable, so an untolerant reader
    renders it one character per goal instead of crashing, which is worse: a wrong world
    that nobody notices beats an unopenable one only for the person not reading it."""
    outcome = _propose(conn, json.dumps("hold_line"))
    assert not outcome.applied
    assert "must be a list" in str(outcome.reason)


def test_an_object_is_refused(conn):
    assert not _propose(conn, json.dumps({"goal": "hold_line"})).applied


def test_a_proper_list_is_accepted(conn):
    outcome = _propose(conn, json.dumps(["hold the line", "keep the border sealed"]))

    assert outcome.applied, outcome.reason
    stored = conn.execute(
        "SELECT standing_goals_json FROM nation WHERE id = 'a'").fetchone()[0]
    assert json.loads(stored) == ["hold the line", "keep the border sealed"]


def test_an_empty_list_is_accepted(conn):
    assert _propose(conn, "[]").applied


def test_the_rule_reaches_the_world_simulator_too(conn):
    """Both roles that may write the column are covered. `standing_goals_json` is in the
    permitted set for WORLD_SIM as well as TRACKED_AGENT, and a referee that broke it would
    brick the world by exactly the same route."""
    assert not _propose(conn, "hold_line", role=Role.WORLD_SIM).applied


def test_a_batch_that_touches_nothing_json_is_left_alone(conn):
    """The rule must not cost a query on every batch in the game."""
    event_id = record_event(
        conn, world_id=WORLD, turn=1, date="2026-02-01", kind=EventKind.NARRATIVE,
        origin=EventOrigin.ENGINE, text="a note", visibility=Visibility.PUBLIC)
    outcome = apply_batch(conn, DiffBatch(
        world_id=WORLD, turn=1, proposer_role=Role.TRACKED_AGENT, proposer_model="a-model",
        proposer_nation_id="a",
        diffs=(Diff("relation", "a|b", "score", DiffOp.ADJUST, 5.0,
                    reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),)))
    assert outcome.applied, outcome.reason


# ------------------------------------------------------- and a damaged world still opens


def test_a_world_already_carrying_the_damage_still_opens(conn):
    """**The half that matters for `fulda-2-rot2`, which has the bad value in it now.**
    Written with raw SQL on purpose: the point is a database that is already wrong, which
    is exactly what no legitimate path can now produce."""
    from atlas.state.referee import all_nations
    from atlas.state.visibility import nation_self

    conn.execute(
        "UPDATE nation SET standing_goals_json = 'hold_line' WHERE id = 'a'")
    conn.commit()

    nations = {n["id"]: n for n in all_nations(conn, WORLD)}
    assert nations["a"]["goals"] == ["hold_line"], "the referee's view still dies on it"
    assert nation_self(conn, "a")["standing_goals"] == ["hold_line"]


def test_the_snapshot_survives_it_too(conn):
    from atlas.engine.snapshot import load_player_snapshot

    conn.execute(
        "UPDATE nation SET standing_goals_json = 'hold_line', is_player = 1 WHERE id = 'a'")
    conn.commit()
    assert load_player_snapshot(conn, WORLD, nation_id="a").goals == ("hold_line",)


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        (None, []),
        ("", []),
        ("   ", []),
        ("null", []),
        ('["a", "b"]', ["a", "b"]),
        ("hold_line", ["hold_line"]),
        ('"hold_line"', ["hold_line"]),
        ("5", ["5"]),
        ("[1, 2]", ["1", "2"]),
    ],
)
def test_json_list_never_raises_and_always_gives_strings(stored, expected):
    """Strings out, always: every caller renders or joins these, and `"; ".join` on a list
    a model wrote as `[1, 2]` is the same crash one layer further from the write."""
    got = json_list(stored)
    assert got == expected
    assert all(isinstance(item, str) for item in got)
