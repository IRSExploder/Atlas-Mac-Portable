"""A randomized history over the fingerprinted tables added since migration 19, and a rewind
to every turn it passed through.

**Rebuilt.** The original of this file was lost with the batch listed in
`docs/MISSING_FILES_2026-10-27.tsv`, and its sibling
(`test_the_older_tables_rewind_whatever_you_do_to_them.py`) still imported its ``TABLES`` --
so the one check that says every fingerprinted table is fuzzed or argued for could not run
at all, which is worse than failing: it covered nothing while looking like a known flake.
The machinery is the sibling's; what is new here is the fourteen tables, each keyed on a
single ``id``.

These are the war loop's state and the strategy layer's: aims, efforts, terms, embargoes,
networks, plans. Each is something a rewind must put back exactly -- a peace term restored
without its outstanding balance is a reparation paid twice; a war effort restored without
its exhaustion is a war that has been going on for a year and feels like a week.

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

#: The fingerprinted, diffable tables added since migration 19.
TABLES = ("attribution", "constituency", "doctrine_stance", "exposure", "intel_network",
          "intel_term", "nation_strategy", "peace_term", "strategy_revision", "trade_embargo",
          "trade_intent", "war_aim", "war_effort", "world_fact")

#: A treaty for the two term tables to belong to; the terms are what is being fuzzed.
_TREATY = 1


def _row(table: str, rng: random.Random, seq: int) -> tuple[str, dict]:
    """``(row_id, columns)`` for one legal row of ``table``.

    Where a table has a natural UNIQUE key, the id carries it, so two different ids can never
    collide on it and the fuzz never writes an illegal batch by accident.
    """
    a, b = rng.sample(NATIONS, 2)
    if table == "attribution":
        event = rng.randint(1, 4)
        return f"attr_{event}_{b}", {
            "world_id": WORLD, "event_id": event, "actor_nation_id": a,
            "victim_nation_id": b, "confidence": round(rng.uniform(0, 100), 2),
            "tier": rng.choice(("none", "suspected", "likely", "proven")),
            "opened_turn": 1, "last_turn": 1,
        }
    if table == "constituency":
        kind = rng.choice(("army", "industry", "people", "patriots", "establishment"))
        return f"con_{a}_{kind}", {
            "world_id": WORLD, "nation_id": a, "kind": kind,
            "support": round(rng.uniform(0, 100), 2), "last_turn": 1, "because": "fuzz",
        }
    if table == "doctrine_stance":
        axis = rng.choice(("mobilisation", "nuclear_posture", "economy"))
        return f"doc_{a}_{axis}", {
            "world_id": WORLD, "nation_id": a, "axis": axis,
            "stance": rng.choice(("mass", "professional", "minimum")),
            "adopted_on": "1897-01-01", "adopted_turn": 1,
        }
    if table == "exposure":
        event = rng.randint(1, 6)
        return f"exp_{event}", {
            "world_id": WORLD, "event_id": event, "actor_nation_id": a,
            "victim_nation_id": b, "importance": round(rng.random(), 3),
            "guardedness": round(rng.random(), 3), "mismatch": round(rng.random(), 3),
            "magnitude": round(rng.uniform(0, 100), 2), "casus_belli": rng.choice((0, 1)),
            "turn": 1,
        }
    if table == "intel_network":
        return f"net_{a}_{b}", {
            "world_id": WORLD, "nation_id": a, "target_nation_id": b,
            "strength": round(rng.uniform(0, 100), 2), "opened_turn": 1, "last_turn": 1,
            "counter_intel": round(rng.uniform(0, 100), 2),
        }
    if table == "intel_term":
        clause = rng.randint(1, 4)
        return f"iterm_{clause}", {
            "world_id": WORLD, "commitment_id": _TREATY, "seq": clause,
            "sharer_id": a, "recipient_id": b, "subject_id": rng.choice(("", NATIONS[0])),
        }
    if table == "nation_strategy":
        return f"plan_{a}", {
            "world_id": WORLD, "nation_id": a,
            "objective": rng.choice(("", "hegemony", "prosperity")),
            "adopted_on": "1897-01-01", "adopted_turn": 1,
            "seen_ground": round(rng.uniform(0, 10), 2),
            "contender_streak": rng.randint(0, 3),
        }
    if table == "peace_term":
        clause = rng.randint(1, 4)
        value = round(rng.uniform(0, 60), 2)
        return f"pterm_{clause}", {
            "world_id": WORLD, "commitment_id": _TREATY, "seq": clause,
            "kind": rng.choice(("cede_region", "withdraw", "reparations", "demilitarise")),
            "obliged_id": a, "beneficiary_id": b, "subject_id": rng.choice(REGIONS),
            "value": value, "outstanding": round(rng.uniform(0, value), 2),
            "due_turn": rng.choice((None, 6, 12)),
        }
    if table == "strategy_revision":
        return f"rev_{seq}", {
            "world_id": WORLD, "nation_id": a, "at_turn": 1, "on_date": "1897-01-01",
            "trigger": rng.choice(("scheduled_review", "major_shock", "first_plan")),
            "from_objective": "", "to_objective": "prosperity",
        }
    if table == "trade_embargo":
        return f"emb_{seq}", {
            "world_id": WORLD, "proposer_id": a, "target_id": b,
            "good": rng.choice(("",) + GOODS), "opened_turn": 1, "opened_on": "1897-01-01",
            "status": rng.choice(("standing", "lifted")),
            "revenue_forgone": round(rng.uniform(0, 40), 2), "episodes": 1,
        }
    if table == "trade_intent":
        kind = rng.choice(("seek", "sell", "diversify"))
        return f"intent_{seq}", {
            "world_id": WORLD, "nation_id": a, "kind": kind, "good": rng.choice(GOODS),
            "partner_id": b if kind == "diversify" else rng.choice(("", b)),
            "opened_turn": 1, "status": rng.choice(("standing", "met", "retired")),
        }
    if table == "war_aim":
        kind = rng.choice(("defend", "recover_cores", "annex", "demilitarise"))
        subject = "" if kind in ("defend", "demilitarise") else rng.choice(REGIONS)
        return f"aim_{a}_{b}_{kind}_{subject}", {
            "world_id": WORLD, "nation_id": a, "enemy_id": b, "kind": kind,
            "subject_id": subject, "declared_turn": 1,
        }
    if table == "war_effort":
        return f"effort_{a}_{b}", {
            "world_id": WORLD, "nation_id": a, "enemy_id": b, "opened_turn": 1,
            "last_turn": 1, "casualties": round(rng.uniform(0, 30), 2),
            "charged_casualties": round(rng.uniform(0, 10), 2),
            "exhaustion": round(rng.uniform(0, 100), 2),
        }
    return f"fact_{seq}", {
        "world_id": WORLD, "subject_kind": "nation", "subject_id": a,
        "predicate": rng.choice(("capital_of", "rival_of", "known_as")),
        "object_kind": "nation", "object_id": b, "value": "", "start_turn": 1,
        "visibility": rng.choice(("public", "bilateral", "secret")),
        "confidence": round(rng.random(), 3),
    }


#: Which column each table takes an ADJUST or a SET on, and the bound its CHECK allows.
_NUMERIC: dict[str, tuple[str, float, float]] = {
    "attribution": ("confidence", 0.0, 100.0),
    "constituency": ("support", 0.0, 100.0),
    "exposure": ("magnitude", 0.0, 100.0),
    "intel_network": ("strength", 0.0, 100.0),
    "nation_strategy": ("seen_ground", 0.0, 50.0),
    "peace_term": ("outstanding", 0.0, 60.0),
    "trade_embargo": ("revenue_forgone", 0.0, 200.0),
    "war_effort": ("exhaustion", 0.0, 100.0),
    "world_fact": ("confidence", 0.0, 1.0),
}


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = create_world(":memory:", sandbox_seed())
    connection.execute("UPDATE world SET turn_number = 1 WHERE id = ?", (WORLD,))
    connection.execute(
        "INSERT INTO commitment (id, world_id, kind, status, created_turn)"
        " VALUES (?, ?, 'peace_treaty', 'active', 1)", (_TREATY, WORLD))
    connection.commit()
    yield connection
    connection.close()


def _event(conn: sqlite3.Connection, turn: int) -> int:
    return record_event(
        conn, world_id=WORLD, turn=turn, date=f"1897-{turn:02d}-01",
        kind=EventKind.NARRATIVE, origin=EventOrigin.ENGINE,
        text=f"turn {turn}", visibility=Visibility.PUBLIC)


def _live(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[0]) for row in conn.execute(
        f"SELECT id FROM {table} WHERE world_id = ? ORDER BY id", (WORLD,))]  # noqa: S608


def _turn(conn: sqlite3.Connection, rng: random.Random, turn: int,
          seq: int) -> tuple[list[str], int]:
    """One turn of random legal writes. Returns what it did, for the failure message."""
    event_id = _event(conn, turn)
    diffs, told = [], []
    live = {table: list(_live(conn, table)) for table in TABLES}
    touched: set[tuple[str, str]] = set()
    for _ in range(rng.randint(1, 6)):
        table = rng.choice(TABLES)
        existing = live[table]
        choice = rng.random()
        if not existing or choice < 0.45:
            seq += 1
            row_id, columns = _row(table, rng, seq)
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
            ceiling = high
            if table == "peace_term":
                # Never more outstanding than the term was worth.
                ceiling = float(conn.execute(
                    "SELECT value FROM peace_term WHERE id = ?", (row_id,)).fetchone()[0])
            diffs.append(Diff(table, row_id, column, DiffOp.SET,
                              round(rng.uniform(low, ceiling), 3),
                              reason_event_id=event_id,
                              visibility_scope=Visibility.PUBLIC))
            told.append(f"set {table}.{column} {row_id}")
            touched.add((table, row_id))
        elif choice < 0.85 and table in _NUMERIC:
            column, low, high = _NUMERIC[table]
            row_id = rng.choice(existing)
            if (table, row_id) in touched:
                continue
            now = float(conn.execute(
                f"SELECT {column} FROM {table} WHERE id = ?",  # noqa: S608
                (row_id,)).fetchone()[0])
            if table == "peace_term":
                high = float(conn.execute(
                    "SELECT value FROM peace_term WHERE id = ?", (row_id,)).fetchone()[0])
            span = (high - low) / 5.0
            delta = round(rng.uniform(max(low - now, -span), min(high - now, span)), 3)
            if abs(delta) < 0.001:
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
        assert outcome.applied and not outcome.severed, (outcome.reason, told)
    return told, seq


# ------------------------------------------------------------------------ the property


@pytest.mark.parametrize("seed", range(12))
def test_a_random_history_rewinds_to_every_turn_it_passed_through(conn, seed):
    """Whatever order the operations arrive in, rewinding to turn N reproduces the digest
    taken at turn N."""
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


def test_every_listed_table_is_fingerprinted_and_diffable():
    """A table listed here that the fingerprint does not read is a rewind nobody checks."""
    from atlas.state.bootstrap import _FINGERPRINT_TABLES

    fingerprinted = {table for table, _order, _scope in _FINGERPRINT_TABLES}
    assert set(TABLES) <= fingerprinted & set(PRIMARY_KEY)
    assert all(PRIMARY_KEY[table] == ("id",) for table in TABLES)


@pytest.mark.parametrize("table", TABLES)
def test_the_fuzz_actually_writes_every_table_it_lists(conn, table):
    """A `_row` branch that never fires covers nothing. Seeded from a stable hash, for the
    reason the sibling file gives."""
    rng = random.Random(zlib.crc32(table.encode()))
    seen = False
    seq = 0
    for turn in range(2, 60):
        conn.execute("UPDATE world SET turn_number = ? WHERE id = ?", (turn, WORLD))
        told, seq = _turn(conn, rng, turn, seq)
        if any(line.split()[1].split(".")[0] == table for line in told):
            seen = True
            break

    assert seen, f"{table} was never written in 58 turns of fuzzing"
