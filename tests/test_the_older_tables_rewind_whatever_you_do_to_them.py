"""The same randomized history, over the fingerprinted tables that predate the war loop.

**The sibling file covers the tables added since migration 19; these are the ones that were
already there.** Each had example-based rewind tests — the sequence somebody wrote down — and
none was in a randomized history, which is the only thing that covers the sequences nobody
thought of. The gap was not in the newest code at all; it was in the code old enough that
nobody re-read it.

Eleven tables, and the reason each is state a rewind must put back is written down in
`state/apply.py` beside its key. Three of them say it in terms worth repeating here, because
they are what makes this the *dangerous* class rather than the tidy one:

* **`contest`** — a campaign in progress. A rewind that restores the map but not the siege
  lands on a world that looks identical and is at a different point in the same battle.
* **`construction`** — a yard with a hull in it. Restoring the money and not the hull is a
  rewind that stole a ship; restoring the hull and not the money mints one.
* **`air_control`** — who holds the sky. Put the front line back and leave the air
  superiority won on a turn that no longer happened, and the battle replays at the wrong
  odds.

**Composite keys are the substance here, not an incidental.** Every table the sibling file
fuzzes is keyed on a single `id`; six of these are keyed on two, three or four columns and
addressed as `"a|b|c"`. That path — split the id, check the payload agrees with it, find the
row again on the way back — is the one a rewind uses to undo an insert, and it had no
randomized coverage at all.

Seeded, so a failure is reproducible: the sequence is printed in the assertion.
"""

from __future__ import annotations

import random
import sqlite3
import zlib

import pytest

from atlas.scenarios.sandbox import sandbox_seed
from atlas.state.apply import PRIMARY_KEY, apply_batch, record_event
from atlas.state.bootstrap import create_world, fingerprint
from atlas.state.diff import Diff, DiffBatch
from atlas.state.models import DiffOp, EventKind, EventOrigin, Role, Visibility
from atlas.state.replay import rewind_to

WORLD = "sandbox"
NATIONS = ("aurelia", "karthos", "veyra")
REGIONS = ("r_north", "r_coast", "r_south", "r_west")
GOODS = ("oil", "steel", "rare")

#: The fingerprinted tables this file is about: everything diffable that the sibling file
#: does not already fuzz, minus the ones below that cannot be fuzzed independently.
TABLES = ("contest", "construction", "technology", "support", "independence", "arsenal",
          "sanction", "military_objective", "air_control", "trade_route", "resource")

#: Fingerprinted and deliberately not fuzzed here, each for a stated reason. Held in the
#: test at the bottom so the list is an argument rather than an omission.
NOT_FUZZED: dict[str, str] = {
    "world": "one row per world; there is no second one to insert and no world to delete",
    "nation": "seeded, and deleting one cascades through half the schema -- what would be "
              "under test is the FK graph, not the diff pipeline",
    "region": "the same, and the map is what every other table is addressed by",
    "relation": "a row already exists for every ordered pair, so insert and delete are "
                "unreachable and only SET is left, which the sibling file already covers "
                "in its own numeric tables",
    "unit": "covered by the older example-based rewind tests and written on every turn of "
            "the long-haul campaign, which is a randomized history by another name",
    "city": "the same as unit",
    "leader": "PT31 found this one by playing rather than by fuzzing, and its rewind is "
              "covered by the coup tests it produced",
    "organisation": "keyed on a body that has to exist first; `sanction` fuzzes the "
                    "interesting half, which is the standing measure that drains an economy",
    "unit_template": "seeded per era and never written after; a template that changed "
                     "mid-campaign would be a different finding",
    "commitment": "the agreement lifecycle has its own tests and its own laboratory "
                  "detector, and `commitment_term` is the half a rewind actually loses",
    "commitment_nation": "the same",
    "commitment_term": "fuzzed by the sibling file through `peace_term`, which shares its "
                       "shape and its composite key",
    "event": "not diffable -- events are retracted by `unrecord_events`, which has its own "
             "test, and a diff that wrote one would be the finding",
    "event_visibility": "written with its event, never separately",
    "event_participant": "the same",
}


def _row(table: str, rng: random.Random, seq: int,
         held: dict[str, str]) -> tuple[str, dict] | None:
    """``(row_id, columns)`` for one legal row of ``table``, or ``None`` if there is none.

    Row ids for composite keys are `"a|b"`, which is what `apply._key_values` splits — and
    the payload must agree with the id, which is the check this file exists to exercise.

    ``held`` is who controls each province, and it is here for one table: a contest whose
    attacker already holds the ground is a **siege of yourself**, which an invariant refuses
    and the engine never opens. Writing one would be the fuzz breaking a rule rather than
    finding anything -- the same reason every bound below is respected.
    """
    a, b = rng.sample(NATIONS, 2)
    region = rng.choice(REGIONS)
    if table == "contest":
        # UNIQUE (world, region, attacker), so the id carries the natural key: two
        # different ids can still collide, and the fuzz would be writing an illegal batch.
        holder = held.get(region)
        attackers = [n for n in NATIONS if n != holder]
        if not attackers or holder is None:
            return None
        attacker = rng.choice(attackers)
        return f"contest_{region}_{attacker}", {
            "world_id": WORLD, "region_id": region, "attacker_id": attacker,
            "defender_id": holder,
            "pressure": round(rng.uniform(0, 90), 2), "opened_turn": 1, "last_turn": 1,
        }
    if table == "construction":
        return f"build_{seq}", {
            "world_id": WORLD, "nation_id": a, "region_id": region,
            "project_kind": "unit", "kind": "infantry",
            "turns_remaining": rng.randint(1, 6),
            "days_remaining": round(rng.uniform(1, 180), 1), "started_turn": 1,
        }
    if table == "technology":
        tech = rng.choice(("radar", "jet_engine", "rocketry", "sonar"))
        return f"{a}|{tech}", {
            "world_id": WORLD, "nation_id": a, "tech_id": tech,
            "completed_turn": 1, "secret": rng.choice((0, 1)),
        }
    if table == "support":
        kind = rng.choice(("equipment", "advisors", "intelligence", "air_defence",
                           "funding"))
        return f"{WORLD}|{a}|{b}|{kind}", {
            "world_id": WORLD, "patron_id": a, "client_id": b, "kind": kind,
            "level": round(rng.uniform(0.5, 3.0), 2), "opened_turn": 1,
        }
    if table == "independence":
        return f"{WORLD}|{region}", {
            "world_id": WORLD, "region_id": region, "holder_id": a,
            "momentum": round(rng.uniform(0, 90), 2), "opened_turn": 1,
        }
    if table == "arsenal":
        return a, {
            "world_id": WORLD, "nation_id": a, "warheads": rng.randint(0, 40),
            "delivery": rng.choice(("none", "aircraft", "missile", "sea", "triad")),
            "deterrent_boats": rng.choice((None, 0, 2, 5)),
        }
    if table == "sanction":
        return f"sanction_{seq}", {
            "world_id": WORLD, "organisation_id": _BODY, "target_nation_id": a,
            "severity": round(rng.uniform(0.1, 3.0), 2), "opened_turn": 1,
            "until_turn": rng.randint(2, 30),
        }
    if table == "military_objective":
        # UNIQUE (nation, region), so again the id is the natural key.
        return f"obj_{a}_{region}", {
            "world_id": WORLD, "nation_id": a, "region_id": region,
            "kind": rng.choice(("push", "hold", "withdraw_from")), "declared_turn": 1,
        }
    if table == "air_control":
        return f"{WORLD}|{region}", {
            "world_id": WORLD, "region_id": region, "holder_nation_id": a,
            "margin": round(rng.uniform(0, 1), 3), "last_turn": 1,
        }
    if table == "trade_route":
        return f"route_{seq}", {
            "world_id": WORLD, "seller_id": a, "buyer_id": b,
            "good": rng.choice(GOODS), "volume": round(rng.uniform(1, 90), 2),
            "opened_turn": 1,
        }
    good = rng.choice(GOODS)
    return f"{a}|{good}", {
        "world_id": WORLD, "nation_id": a, "good": good,
        "produced": round(rng.uniform(0, 90), 2),
        "stockpile": round(rng.uniform(0, 90), 2),
    }


#: Which column each table takes an ADJUST or a SET on, and the bound it must stay inside.
#: Bounds matter: `support.level` is CHECKed to (0, 3] and `independence.momentum` to >= 0,
#: so a fuzz that ignored them would be testing the rules rather than the pipeline.
_NUMERIC: dict[str, tuple[str, float, float]] = {
    "contest": ("pressure", 0.0, 100.0),
    "construction": ("days_remaining", 0.0, 400.0),
    "support": ("level", 0.01, 3.0),
    "independence": ("momentum", 0.0, 100.0),
    "sanction": ("severity", 0.01, 5.0),
    "air_control": ("margin", 0.0, 1.0),
    "trade_route": ("volume", 0.0, 500.0),
    "resource": ("stockpile", 0.0, 500.0),
}

#: A body for `sanction` to be issued by. Its own table is not what is being fuzzed.
_BODY = "council"


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = create_world(":memory:", sandbox_seed())
    connection.execute("UPDATE world SET turn_number = 1 WHERE id = ?", (WORLD,))
    connection.execute(
        "INSERT INTO nation (id, world_id, name, colour, is_alive)"
        " VALUES (?, ?, 'The Council', '#8899aa', 1)", (_BODY, WORLD))
    connection.execute(
        "INSERT INTO organisation (nation_id, world_id, kind, authority)"
        " VALUES (?, ?, 'universal', 50.0)", (_BODY, WORLD))
    connection.commit()
    yield connection
    connection.close()


def _event(conn: sqlite3.Connection, turn: int) -> int:
    return record_event(
        conn, world_id=WORLD, turn=turn, date=f"1897-{turn:02d}-01",
        kind=EventKind.NARRATIVE, origin=EventOrigin.ENGINE,
        text=f"turn {turn}", visibility=Visibility.PUBLIC)


def _live(conn: sqlite3.Connection, table: str) -> list[str]:
    """Every row id currently in ``table``, composite ids joined the way a diff writes them."""
    key = PRIMARY_KEY[table]
    columns = ", ".join(key)
    return ["|".join(str(value) for value in row) for row in conn.execute(
        f"SELECT {columns} FROM {table} WHERE world_id = ? ORDER BY {columns}", (WORLD,))]


def _turn(conn: sqlite3.Connection, rng: random.Random, turn: int,
          seq: int) -> tuple[list[str], int]:
    """One turn of random legal writes. Returns what it did, for the failure message."""
    event_id = _event(conn, turn)
    diffs, told = [], []
    live = {table: list(_live(conn, table)) for table in TABLES}
    held = {
        str(row["id"]): str(row["holder"]) for row in conn.execute(
            "SELECT id, COALESCE(controller_nation_id, owner_nation_id) AS holder"
            "  FROM region WHERE world_id = ? AND holder IS NOT NULL", (WORLD,))
    }
    touched: set[tuple[str, str]] = set()
    for _ in range(rng.randint(1, 6)):
        table = rng.choice(TABLES)
        existing = live[table]
        choice = rng.random()
        if not existing or choice < 0.45:
            seq += 1
            built = _row(table, rng, seq, held)
            if built is None:
                continue
            row_id, columns = built
            if row_id in existing or (table, row_id) in touched:
                continue
            diffs.append(Diff(table, row_id, "__insert__", DiffOp.INSERT, columns,
                              reason_event_id=event_id,
                              visibility_scope=Visibility.PUBLIC))
            told.append(f"insert {table} {row_id}")
            live[table].append(row_id)
            touched.add((table, row_id))
        elif choice < 0.70 and table in _NUMERIC:
            column, low, high = _NUMERIC[table]
            row_id = rng.choice(existing)
            if (table, row_id) in touched:
                continue
            diffs.append(Diff(table, row_id, column, DiffOp.SET,
                              round(rng.uniform(low, high), 3),
                              reason_event_id=event_id,
                              visibility_scope=Visibility.PUBLIC))
            told.append(f"set {table}.{column} {row_id}")
            touched.add((table, row_id))
        elif choice < 0.85 and table in _NUMERIC:
            # An adjust that cannot leave the bound, so the batch is legal by construction:
            # what is under test is the pipeline, not the rules.
            column, low, high = _NUMERIC[table]
            row_id = rng.choice(existing)
            if (table, row_id) in touched:
                continue
            clause = " AND ".join(f"{name} = ?" for name in PRIMARY_KEY[table])
            now = float(conn.execute(
                f"SELECT {column} FROM {table} WHERE {clause}",  # noqa: S608
                tuple(row_id.split("|")) if len(PRIMARY_KEY[table]) > 1 else (row_id,),
            ).fetchone()[0])
            delta = round(rng.uniform(max(low - now, -20.0), min(high - now, 20.0)), 3)
            if abs(delta) < 0.01:
                continue
            diffs.append(Diff(table, row_id, column, DiffOp.ADJUST, delta,
                              reason_event_id=event_id,
                              visibility_scope=Visibility.PUBLIC))
            told.append(f"adjust {table}.{column} {row_id} {delta:+}")
            touched.add((table, row_id))
        else:
            row_id = rng.choice(existing)
            if (table, row_id) in touched:
                continue
            diffs.append(Diff(table, row_id, "__delete__", DiffOp.DELETE, None,
                              reason_event_id=event_id,
                              visibility_scope=Visibility.PUBLIC))
            told.append(f"delete {table} {row_id}")
            live[table].remove(row_id)
            touched.add((table, row_id))
    if diffs:
        outcome = apply_batch(
            conn,
            DiffBatch(world_id=WORLD, turn=turn, proposer_role=Role.ENGINE,
                      proposer_model=None, diffs=tuple(diffs)))
        assert outcome.applied, (outcome.reason, told)
    return told, seq


# ------------------------------------------------------------------------ the property


@pytest.mark.parametrize("seed", range(12))
def test_a_random_history_rewinds_to_every_turn_it_passed_through(conn, seed):
    """**The property.** Whatever order the operations arrive in, whatever mix of insert,
    set, adjust and delete, rewinding to turn N reproduces the digest taken at turn N."""
    rng = random.Random(seed)
    marks = {1: fingerprint(conn, WORLD)}
    history: dict[int, list[str]] = {}
    seq = 0
    for turn in range(2, 9):
        conn.execute("UPDATE world SET turn_number = ? WHERE id = ?", (turn, WORLD))
        history[turn], seq = _turn(conn, rng, turn, seq)
        marks[turn] = fingerprint(conn, WORLD)

    for target in sorted(marks, reverse=True):
        rewind_to(conn, WORLD, target)
        assert fingerprint(conn, WORLD) == marks[target], (
            f"seed {seed}: rewinding to turn {target} did not reproduce it; "
            f"history was {history}")


@pytest.mark.parametrize("seed", range(8))
def test_a_random_history_never_breaks_an_invariant(conn, seed):
    """A pipeline that rewinds exactly to a broken world has not helped anybody."""
    from atlas.testing.invariants import violations

    rng = random.Random(seed)
    seq = 0
    for turn in range(2, 9):
        conn.execute("UPDATE world SET turn_number = ? WHERE id = ?", (turn, WORLD))
        told, seq = _turn(conn, rng, turn, seq)
        broken = violations(conn, WORLD, "rest")
        assert not broken, f"seed {seed}, turn {turn}: {broken}; last did {told}"


# ------------------------------------------------------- and the list is the whole list


def test_every_fingerprinted_diffable_table_is_fuzzed_or_argued_for():
    """**The exhaustiveness that makes this a guarantee rather than a sample.**

    The sibling file checks the tables added since migration 19 against the migrations.
    This checks *everything else*: a fingerprinted, diffable table must be in `TABLES`
    here, in the sibling file's list, or in `NOT_FUZZED` with a reason somebody wrote.
    There is no fourth category, so the next table added cannot land in one.
    """
    from atlas.state.bootstrap import _FINGERPRINT_TABLES
    from tests.test_the_new_tables_rewind_whatever_you_do_to_them import (
        TABLES as NEWER,
    )

    fingerprinted = {table for table, _order, _scope in _FINGERPRINT_TABLES}
    diffable = fingerprinted & set(PRIMARY_KEY)
    accounted = set(TABLES) | set(NEWER) | set(NOT_FUZZED)
    missing = sorted(diffable - accounted)

    assert not missing, (
        f"fingerprinted and diffable, and in no fuzz list and no argument: {missing}. "
        "A rewind that fails to restore one produces a world that looks identical and is "
        "not; add a `_row` branch, or say in NOT_FUZZED why it cannot have one.")


def test_nothing_is_excused_that_is_also_fuzzed():
    """An entry in both lists means one of them is stale, and a stale excuse is worse than
    no excuse: it reads as a decision."""
    from tests.test_the_new_tables_rewind_whatever_you_do_to_them import (
        TABLES as NEWER,
    )

    overlap = sorted((set(TABLES) | set(NEWER)) & set(NOT_FUZZED))

    assert not overlap, f"fuzzed and also excused: {overlap}"


@pytest.mark.parametrize("table", TABLES)
def test_the_fuzz_actually_writes_every_table_it_lists(conn, table):
    """A `_row` branch that never fires covers nothing, and a table listed with a typo'd
    branch would look covered while being skipped.

    **Seeded from a stable hash, and that is not a detail.** The first version seeded from
    the builtin `hash()`, which is randomised per process: the test drew a different
    sequence every run, passed on its own, and failed on the next full suite. A seeded fuzz
    whose seed is not seeded is not reproducible, which is the one property it exists to
    have.
    """
    rng = random.Random(zlib.crc32(table.encode()))
    seen = False
    seq = 0
    for turn in range(2, 40):
        conn.execute("UPDATE world SET turn_number = ? WHERE id = ?", (turn, WORLD))
        told, seq = _turn(conn, rng, turn, seq)
        # `set`/`adjust` name the column too -- "set resource.stockpile a|oil" -- so the
        # table is the part before the dot. Matching the whole word missed every table
        # whose rows the world already seeds, because those are only ever written to.
        if any(line.split()[1].split(".")[0] == table for line in told):
            seen = True
            break

    assert seen, f"{table} was never written in 38 turns of fuzzing"
