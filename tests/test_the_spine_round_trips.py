"""The spine: a turn that happened can be made not to have happened, exactly.

Two properties, and the second is the one nothing else in the suite checks.

**Reversibility.** State A, an arbitrary valid turn, state B, rewind -- and A again, in
*every persistent table*, not merely in the digest. The existing fuzz files compare
`fingerprint()`, which covers 40 of the 53 tables; a rewind that restored the digest and
lost a constant, a history row or an `event_cause` edge would pass them and fail here.

**Determinism forward.** A, the same diffs again, and B again. That is what makes a rewind
a *seek* rather than an undo: the branch a player rewinds away from must be re-reachable,
or "rewind and replay" is a lie. It is checkable byte-for-byte because no table in this
schema uses `AUTOINCREMENT` -- every `id` is a rowid alias, so ids deleted by a rewind are
reissued in the same order.

The hard cases are deliberate rather than incidental, because each is a way the pipeline
could be exactly wrong in a way a digest would hide: inserts, deletes, a delete and
reinsert of the same key in one history, repeated writes to one field, NULLs written and
read back, a foreign-key cascade, and a batch that is refused halfway through a turn --
which must leave nothing at all, not even the events it wrote before finding out.
"""

from __future__ import annotations

import random
import sqlite3

import pytest

from atlas.scenarios.sandbox import sandbox_seed
from atlas.state.apply import apply_batch, record_event
from atlas.state.bootstrap import create_world
from atlas.state.diff import Diff, DiffBatch
from atlas.state.models import DiffOp, EventKind, EventOrigin, Role, Visibility
from atlas.state.replay import rewind_to
from atlas.state.tables import persistent_tables, semantic_dump

WORLD = "sandbox"


@pytest.fixture
def conn() -> sqlite3.Connection:
    """The sandbox, plus the body a `sanction` needs to hang off.

    Matched to the older fuzz file's fixture on purpose, because this file borrows that
    file's turn generator: a generator and the world it writes into are one thing, and
    seeding them differently is how a shared fuzz stops being shared.
    """
    from tests.test_the_older_tables_rewind_whatever_you_do_to_them import _BODY

    connection = create_world(":memory:", sandbox_seed())
    connection.execute(
        "INSERT INTO nation (id, world_id, name, colour, is_alive)"
        " VALUES (?, ?, 'The Council', '#8899aa', 1)", (_BODY, WORLD))
    connection.execute(
        "INSERT INTO organisation (nation_id, world_id, kind, authority)"
        " VALUES (?, ?, 'universal', 50.0)", (_BODY, WORLD))
    connection.commit()
    yield connection
    connection.close()


#: Columns holding **real** time rather than world time, normalised out of the
#: comparison. A replay reaches the same world a minute later than the first run did,
#: so these differ for a reason that is not a difference -- and left in, they make this
#: file flaky rather than wrong: it passes whenever a replay lands inside the same
#: second, which is how it passed the first time it was run.
#:
#: An explicit list rather than a name pattern, because the schema holds a trap:
#: ``unit.entrenched_at`` looks like a timestamp and is a **region id** -- "the region
#: the digging was done in" -- so a rule excluding ``*_at`` would quietly stop
#: comparing where an army dug in.
_WALL_CLOCK = frozenset({
    ("world", "created_at"),
    ("diff_batch", "created_at"),
    ("routing_log", "created_at"),
    ("world_lineage", "forked_at"),
})


def _snapshot(conn: sqlite3.Connection) -> dict[str, list[tuple[str, ...]]]:
    """Every persistent table, every row, as comparable text.

    Deliberately wider than `semantic_dump`: the whole file, history included, because
    a rewind is supposed to remove the record of the futures it undid as well as their
    consequences. NULL becomes a sentinel rather than "", so a column that moved from
    NULL to empty string is a difference rather than a match.
    """
    out: dict[str, list[tuple[str, ...]]] = {}
    for table in sorted(persistent_tables(conn)):
        columns = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
        clocks = {i for i, c in enumerate(columns) if (table, c) in _WALL_CLOCK}
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608 - schema names
        out[table] = sorted(
            tuple(
                "<CLOCK>" if i in clocks
                else "<NULL>" if value is None
                else str(value)
                for i, value in enumerate(row)
            )
            for row in rows
        )
    return out


def _describe(before: dict, after: dict, limit: int = 4) -> str:
    """What two snapshots disagree about, table by table then row by row."""
    problems: list[str] = []
    for table in sorted(set(before) | set(after)):
        left, right = before.get(table, []), after.get(table, [])
        if left == right:
            continue
        gone = [r for r in left if r not in right][:limit]
        came = [r for r in right if r not in left][:limit]
        problems.append(
            f"{table}: {len(left)} -> {len(right)} rows"
            + (f"; missing {gone}" if gone else "")
            + (f"; unexpected {came}" if came else "")
        )
    return " | ".join(problems) if problems else "(snapshots agree)"


def _event(conn: sqlite3.Connection, turn: int, text: str = "something happened") -> int:
    return record_event(
        conn, world_id=WORLD, turn=turn, date=f"1897-{3 + turn:02d}-01",
        kind=EventKind.NARRATIVE, origin=EventOrigin.ENGINE, actor_nation_id="aurelia",
        text=text, visibility=Visibility.PUBLIC)


def _apply(conn: sqlite3.Connection, turn: int, *diffs: Diff):
    return apply_batch(conn, DiffBatch(
        world_id=WORLD, turn=turn, proposer_role=Role.ENGINE, proposer_model=None,
        diffs=diffs))


def _at_turn(conn: sqlite3.Connection, turn: int) -> None:
    """Open a turn the way the engine does, so a rewind has a row to restore a date from."""
    conn.execute(
        "INSERT OR IGNORE INTO turn (world_id, number, phase, status, start_date, end_date)"
        " VALUES (?, ?, 'jump', 'sealed', ?, ?)",
        (WORLD, turn, f"1897-{2 + turn:02d}-01", f"1897-{3 + turn:02d}-01"))
    conn.execute("UPDATE world SET turn_number = ?, world_date = ? WHERE id = ?",
                 (turn, f"1897-{3 + turn:02d}-01", WORLD))
    conn.commit()


# --------------------------------------------------------------- the randomized property


def _random_history(conn: sqlite3.Connection, rng: random.Random,
                    first: int, last: int) -> list[str]:
    """Random legal turns, through the generator the older fuzz file already owns.

    Imported rather than rewritten: two generators would drift, and the interesting half
    of this file is the comparison, not the invention of legal rows.
    """
    from tests.test_the_older_tables_rewind_whatever_you_do_to_them import _turn

    told: list[str] = []
    seq = 0
    for turn in range(first, last + 1):
        _at_turn(conn, turn)
        did, seq = _turn(conn, rng, turn, seq)
        told.extend(f"t{turn}: {line}" for line in did)
    return told


@pytest.mark.parametrize("seed", range(6))
def test_a_turn_rewinds_to_exactly_the_state_it_started_from(conn, seed):
    """**Property one, over every table rather than over the digest.**"""
    _at_turn(conn, 1)
    before = _snapshot(conn)

    rng = random.Random(seed)
    told = _random_history(conn, rng, 2, 6)
    assert _snapshot(conn) != before, f"seed {seed}: the history changed nothing"

    report = rewind_to(conn, WORLD, 1)
    assert report.problems == [], f"seed {seed}: {report.problems}"

    after = _snapshot(conn)
    assert after == before, (
        f"seed {seed}: rewind did not restore the world.\n"
        f"  {_describe(before, after)}\n  history was {told}")


@pytest.mark.parametrize("seed", range(6))
def test_the_same_turns_replayed_forward_reach_the_same_world(conn, seed):
    """**Property two.** Rewinding is a seek, so the branch has to be re-reachable.

    Byte-for-byte including ids, which is only fair because nothing here is
    `AUTOINCREMENT`: a rewind returns the id space as well as the rows.
    """
    _at_turn(conn, 1)
    start = _snapshot(conn)

    told = _random_history(conn, random.Random(seed), 2, 6)
    reached = _snapshot(conn)

    rewind_to(conn, WORLD, 1)
    assert _snapshot(conn) == start, f"seed {seed}: the rewind half failed first"

    _random_history(conn, random.Random(seed), 2, 6)
    again = _snapshot(conn)
    assert again == reached, (
        f"seed {seed}: replaying the same turns reached a different world.\n"
        f"  {_describe(reached, again)}\n  history was {told}")


@pytest.mark.parametrize("seed", range(6))
def test_every_intermediate_turn_is_reachable_going_backwards(conn, seed):
    """Not only the beginning. A rewind to any turn the history passed through has to
    reproduce that turn, which is what a player scrubbing backwards actually does."""
    marks: dict[int, dict] = {}
    rng = random.Random(seed)
    _at_turn(conn, 1)
    marks[1] = _snapshot(conn)
    seq = 0
    from tests.test_the_older_tables_rewind_whatever_you_do_to_them import _turn

    for turn in range(2, 7):
        _at_turn(conn, turn)
        _did, seq = _turn(conn, rng, turn, seq)
        marks[turn] = _snapshot(conn)

    for target in sorted(marks, reverse=True):
        rewind_to(conn, WORLD, target)
        assert _snapshot(conn) == marks[target], (
            f"seed {seed}: rewinding to turn {target} did not reproduce it.\n"
            f"  {_describe(marks[target], _snapshot(conn))}")


def test_the_only_columns_excluded_are_real_clocks(conn):
    """The exclusion is a hole in the comparison, so it is pinned.

    Every name in `_WALL_CLOCK` must exist, and the column that *looks* like a clock and
    is not must keep being compared: `unit.entrenched_at` holds a region id, and excluding
    it would stop this file noticing that a rewind moved an army.
    """
    real = {
        (table, row["name"])
        for table in persistent_tables(conn)
        for row in conn.execute(f"PRAGMA table_info({table})")
    }
    assert not sorted(_WALL_CLOCK - real), (
        f"_WALL_CLOCK names columns that no longer exist: {sorted(_WALL_CLOCK - real)}")
    assert ("unit", "entrenched_at") not in _WALL_CLOCK
    assert ("unit", "entrenched_at") in real


# ------------------------------------------------------------------ the hard cases, named


def test_a_deleted_row_comes_back_with_every_column(conn):
    """A delete keeps the whole row in `old_value`, and a rewind re-inserts it. If any
    column were dropped the row would return as a different row."""
    _at_turn(conn, 1)
    conn.execute(
        "INSERT INTO construction (id, world_id, nation_id, region_id, project_kind,"
        " kind, turns_remaining, days_remaining, started_turn)"
        " VALUES ('build_x', ?, 'aurelia', 'r_north', 'unit', 'infantry', 4, 120.0, 1)",
        (WORLD,))
    conn.commit()
    before = _snapshot(conn)

    _at_turn(conn, 2)
    assert _apply(conn, 2, Diff("construction", "build_x", "__delete__", DiffOp.DELETE,
                                None, reason_event_id=_event(conn, 2))).applied
    assert conn.execute("SELECT COUNT(*) FROM construction").fetchone()[0] == 0

    rewind_to(conn, WORLD, 1)
    after = _snapshot(conn)
    assert after == before, _describe(before, after)


def test_a_key_deleted_and_reinserted_in_one_history_rewinds(conn):
    """The case an undo-by-field would get exactly wrong: the same id leaves and returns
    with different contents, so the log holds two rows for one key."""
    _at_turn(conn, 1)
    before = _snapshot(conn)

    _at_turn(conn, 2)
    assert _apply(conn, 2, Diff("construction", "build_y", "__insert__", DiffOp.INSERT,
                                {"world_id": WORLD, "nation_id": "aurelia",
                                 "region_id": "r_north", "project_kind": "unit",
                                 "kind": "infantry", "turns_remaining": 3,
                                 "days_remaining": 90.0, "started_turn": 2},
                                reason_event_id=_event(conn, 2))).applied
    _at_turn(conn, 3)
    assert _apply(conn, 3, Diff("construction", "build_y", "__delete__", DiffOp.DELETE,
                                None, reason_event_id=_event(conn, 3))).applied
    _at_turn(conn, 4)
    assert _apply(conn, 4, Diff("construction", "build_y", "__insert__", DiffOp.INSERT,
                                {"world_id": WORLD, "nation_id": "karthos",
                                 "region_id": "r_south", "project_kind": "unit",
                                 "kind": "cavalry", "turns_remaining": 6,
                                 "days_remaining": 180.0, "started_turn": 4},
                                reason_event_id=_event(conn, 4))).applied

    rewind_to(conn, WORLD, 1)
    after = _snapshot(conn)
    assert after == before, _describe(before, after)


def test_repeated_writes_to_one_field_unwind_in_order(conn):
    """Five writes to one column in one turn. A rewind has to walk them backwards, not
    take the first prior it finds."""
    _at_turn(conn, 1)
    before = _snapshot(conn)

    for turn, value in enumerate([11.0, 22.0, 33.0, 44.0, 55.0], start=2):
        _at_turn(conn, turn)
        assert _apply(conn, turn,
                      Diff("nation", "aurelia", "economy", DiffOp.SET, value,
                           reason_event_id=_event(conn, turn))).applied

    rewind_to(conn, WORLD, 1)
    after = _snapshot(conn)
    assert after == before, _describe(before, after)


def test_a_null_written_and_cleared_rewinds_to_null(conn):
    """NULL is the value most easily confused with "no value recorded", which is exactly
    what `old_value = None` means for an insert. A column that starts NULL, is written,
    and is rewound has to be NULL again rather than empty."""
    _at_turn(conn, 1)
    conn.execute("UPDATE nation SET capital_region_id = NULL WHERE id = 'aurelia'")
    conn.commit()
    before = _snapshot(conn)
    assert any("<NULL>" in row for row in before["nation"]), "the fixture stopped nulling"

    _at_turn(conn, 2)
    assert _apply(conn, 2, Diff("nation", "aurelia", "capital_region_id", DiffOp.SET,
                                "r_north", reason_event_id=_event(conn, 2))).applied

    rewind_to(conn, WORLD, 1)
    after = _snapshot(conn)
    assert after == before, _describe(before, after)


def test_a_refused_batch_mid_turn_leaves_nothing_behind(conn):
    """A turn where one batch is refused between two that are not.

    The refused batch writes its events *before* it finds out -- every diff must cite one
    -- so "nothing behind" has to mean the events too, not just the rows. This is the same
    shape as the `unrecord_events` cases, checked here over the whole file rather than one
    table.
    """
    _at_turn(conn, 1)
    _at_turn(conn, 2)
    cause = _event(conn, 2)
    assert _apply(conn, 2, Diff("nation", "aurelia", "economy", DiffOp.SET, 21.0,
                                reason_event_id=cause)).applied
    conn.commit()
    settled = _snapshot(conn)
    dump_before = semantic_dump(conn, WORLD)

    # Refused: ECON rules hold stability inside 0-100, and this asks for 900. It cites the
    # event already on the record rather than writing one, so that what survives is the
    # engine's doing and not the fixture's -- `event` is a STATE table and lands in the
    # dump, so a test that minted its own event here would be measuring itself.
    refused = _apply(conn, 2, Diff("nation", "aurelia", "stability", DiffOp.SET, 900.0,
                                   reason_event_id=cause))
    assert not refused.applied, "the fixture stopped being refused"
    conn.commit()

    after = _snapshot(conn)
    # The proposal itself is on the record -- that is deliberate, and `diff_batch` keeps a
    # rejected row so a player can be shown what was refused. Nothing else may move.
    ignored = {"diff_batch", "diff", "event"}
    for table in sorted(set(settled) | set(after)):
        if table in ignored:
            continue
        assert settled[table] == after[table], (
            f"a refused batch changed {table}: {_describe(settled, after)}")

    # And by the project's own instrument, against the dump taken before the refusal.
    assert semantic_dump(conn, WORLD) == dump_before, (
        "the refused batch moved gameplay state that the raw comparison above missed")
    assert conn.execute(
        "SELECT stability FROM nation WHERE id = 'aurelia'").fetchone()[0] != 900.0


def test_a_refused_batch_does_not_survive_a_rewind_either(conn):
    """And the turn it was refused in still rewinds cleanly afterwards."""
    _at_turn(conn, 1)
    before = _snapshot(conn)

    _at_turn(conn, 2)
    assert _apply(conn, 2, Diff("nation", "aurelia", "economy", DiffOp.SET, 21.0,
                                reason_event_id=_event(conn, 2))).applied
    assert not _apply(conn, 2, Diff("nation", "aurelia", "stability", DiffOp.SET, 900.0,
                                    reason_event_id=_event(conn, 2, "doomed"))).applied
    conn.commit()

    rewind_to(conn, WORLD, 1)
    after = _snapshot(conn)
    assert after == before, _describe(before, after)


def test_a_cascade_parent_restored_by_rewind_brings_its_children(conn):
    """**The FK-cascade case, from the reachable direction.**

    `commitment` is a cascade parent and is diff-*inserted* rather than diff-deleted, so
    the reachable question is not "does a delete lose children" -- the guard in
    `test_no_delete_reaches_a_cascade_parent` covers that -- but whether undoing the
    insert takes the children cleanly and puts them back on the way forward. A rewind
    removes the parent, SQLite cascades the terms and parties away, and every one of those
    rows is separately in the log, so the two must agree exactly.
    """
    _at_turn(conn, 1)
    before = _snapshot(conn)

    _at_turn(conn, 2)
    event_id = _event(conn, 2)
    commitment_id = 5001
    assert _apply(
        conn, 2,
        Diff("commitment", str(commitment_id), "__insert__", DiffOp.INSERT,
             {"world_id": WORLD, "kind": "territory_cession", "status": "active",
              "condition_text": "a cession", "created_turn": 2},
             reason_event_id=event_id),
        Diff("commitment_nation", f"{commitment_id}|aurelia", "__insert__", DiffOp.INSERT,
             {"role": "party", "accepted_turn": 2}, reason_event_id=event_id),
        Diff("commitment_nation", f"{commitment_id}|karthos", "__insert__", DiffOp.INSERT,
             {"role": "party", "accepted_turn": 2}, reason_event_id=event_id),
        Diff("commitment_term", f"{commitment_id}|0", "__insert__", DiffOp.INSERT,
             {"world_id": WORLD, "clause": "cede_region", "obliged_id": "karthos",
              "subject_id": "r_south", "value": 0.0}, reason_event_id=event_id),
    ).applied

    assert conn.execute(
        "SELECT COUNT(*) FROM commitment_term WHERE commitment_id = ?",
        (commitment_id,)).fetchone()[0] == 1

    rewind_to(conn, WORLD, 1)
    after = _snapshot(conn)
    assert after == before, (
        "undoing the parent left its children behind, or removed rows the log did not "
        f"record: {_describe(before, after)}")
