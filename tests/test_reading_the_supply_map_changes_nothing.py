"""`supply_map` is a reading. Two probes suspected it of moving a later model prompt.

**It does not, and this file is the guard rather than the fix.** The suspicion was worth
taking seriously: a read-only helper that perturbs a prompt would silently invalidate every
measurement harness that inspects a world before playing it, and there was a plausible
mechanism to hand -- :func:`atlas.engine.adjacency.graph_of` is a module-global cache, and
whoever calls it first decides when the graph is built.

Run as a two-arm reproduction (``scratchpad/supplymap_purity2.py``): a fresh world built to
its first briefings, with and without ``supply_map`` called for every nation beforehand,
across three eras, one nation and all nations, tracked agents and gambits on, three turns.

===============  =======  =========  ==============================
era              prompts  RNG draws  statements the pre-call ran
===============  =======  =========  ==============================
cold-war         24       0          2,852, none of them writes
gathering-storm  23       0          1,827, none of them writes
modern-day       24       13         3,069, none of them writes
===============  =======  =========  ==============================

Every prompt identical character for character, every RNG key identical in order, every
table identical once the wall-clock columns are excluded. (Excluding them matters: the
first attempt compared ``world``, ``diff_batch`` and ``routing_log`` including
``created_at`` and ``duration_ms``, which differ between two identical builds and made a
pure result look like a finding.)

**The RNG could not have been reordered in any case**, and that is structural rather than
lucky: :func:`atlas.sim.resolve.uncertain` derives from ``(seed, world_id, turn, key)``.
There is no stream position for an extra call to consume.

**What is real, and is a harness hazard rather than an engine one.** `graph_of` caches on
``(id(conn), world_id)`` and cannot see a map written afterwards -- its docstring says
`forget` is called when a world's map is written, and in ``src`` that contract holds: there
is exactly one writer of ``region_adjacency``, in `bootstrap`, and it forgets on the next
line. A *test or probe* that edits the table directly gets a stale graph, and that has
already cost two diagnoses. `test_the_cache_cannot_see_a_map_written_behind_it` states it
so the next person meets it here rather than in a measurement.
"""

from __future__ import annotations

import sqlite3

import pytest

from atlas.engine import adjacency
from atlas.engine.logistics import in_supply, supply_map

WORLD = "sandbox"

_WRITES = ("insert", "update", "delete", "replace", "create", "drop", "alter",
           "begin", "commit", "savepoint")


class _Watched:
    """A connection that records every statement put through it."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self.statements: list[str] = []

    def execute(self, sql, *args, **kwargs):
        self.statements.append(sql)
        return self._conn.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._conn, name)

    @property
    def writes(self) -> list[str]:
        return [
            s for s in self.statements
            if s.strip() and s.strip().split(None, 1)[0].lower() in _WRITES
        ]


def _dump(conn: sqlite3.Connection) -> dict[str, list[tuple]]:
    """Every row of every table, ordered. Small enough to compare whole on the sandbox."""
    out: dict[str, list[tuple]] = {}
    for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ):
        table = str(row["name"])
        cols = [str(c["name"]) for c in conn.execute(f"PRAGMA table_info({table})")]
        if not cols:
            continue
        order = ", ".join(f'"{c}"' for c in cols)
        out[table] = [
            tuple(r) for r in conn.execute(f'SELECT * FROM "{table}" ORDER BY {order}')
        ]
    return out


@pytest.fixture(autouse=True)
def _clean_graph_cache():
    adjacency.forget()
    yield
    adjacency.forget()


def test_the_walk_writes_nothing(conn: sqlite3.Connection):
    """Not one statement that could change a row, over every nation in the world."""
    watched = _Watched(conn)
    for nation in ("aurelia", "karthos", "veyra"):
        supply_map(watched, WORLD, nation)

    assert watched.statements, "the fixture stopped exercising the walk"
    assert watched.writes == [], f"supply_map wrote: {watched.writes}"


def test_the_world_is_byte_identical_afterwards(conn: sqlite3.Connection):
    """The claim as a player would state it: reading the map does not move the world."""
    before = _dump(conn)
    for nation in ("aurelia", "karthos", "veyra"):
        supply_map(conn, WORLD, nation)
        in_supply(conn, WORLD, nation, "r_north")
    assert _dump(conn) == before


def test_the_answer_does_not_depend_on_who_asked_first(conn: sqlite3.Connection):
    """Calling it twice, and for other nations in between, gives one answer.

    The order-dependence a module-global cache would produce, asked directly.
    """
    first = supply_map(conn, WORLD, "aurelia")
    for other in ("karthos", "veyra", "karthos"):
        supply_map(conn, WORLD, other)
    assert supply_map(conn, WORLD, "aurelia") == first


def test_a_cold_cache_and_a_warm_one_agree(conn: sqlite3.Connection):
    """The graph the walk gets is the same whether it built it or found it."""
    warm = supply_map(conn, WORLD, "aurelia")
    adjacency.forget(WORLD)
    assert supply_map(conn, WORLD, "aurelia") == warm


def test_the_walk_draws_no_randomness(conn: sqlite3.Connection, monkeypatch):
    """Structural, not incidental: there is no stream for a call to consume.

    :func:`atlas.sim.resolve.uncertain` derives from ``(seed, world_id, turn, key)``, so an
    extra call cannot reorder anybody else's draw. This asserts the weaker and more useful
    thing -- that the supply walk does not reach the RNG at all -- so that a future version
    which starts drawing has to say so here.
    """
    from atlas.sim import resolve

    drawn: list[str] = []
    original = resolve.derive
    monkeypatch.setattr(
        resolve, "derive",
        lambda seed, world, turn, key, *a, **k: (
            drawn.append(str(key)) or original(seed, world, turn, key, *a, **k)
        ),
    )
    for nation in ("aurelia", "karthos", "veyra"):
        supply_map(conn, WORLD, nation)
    assert drawn == []


def test_the_cache_cannot_see_a_map_written_behind_it(conn: sqlite3.Connection):
    """The real hazard, and it belongs to whoever edits a map rather than to the walk.

    `graph_of` is keyed on the connection and the world and has no way to notice
    ``region_adjacency`` changing underneath it -- by design, because checking would undo
    the caching. `src` honours the contract: `bootstrap` is the only writer and forgets on
    the next line. A probe that edits the table by hand does not, and gets an answer about
    the map as it was.

    Asserting the sharp edge rather than filing it off, so the next person meets it in a
    test named after it instead of in a measurement they believed.
    """
    supply_map(conn, WORLD, "aurelia")
    conn.execute(
        "INSERT OR IGNORE INTO region_adjacency (world_id, region_a, region_b, crossing)"
        " VALUES (?, 'r_coast', 'r_south', 'land')", (WORLD,)
    )
    conn.commit()

    rows = conn.execute(
        "SELECT COUNT(*) FROM region_adjacency WHERE world_id = ?", (WORLD,)
    ).fetchone()[0]
    stale = adjacency.graph_of(conn, WORLD)
    assert sum(len(v) for v in stale.values()) < rows * 2, (
        "the cache noticed a map written behind it -- if that is now true on purpose, "
        "this test and `graph_of`'s docstring both want rewriting"
    )

    adjacency.forget(WORLD)
    fresh = adjacency.graph_of(conn, WORLD)
    assert sum(len(v) for v in fresh.values()) == rows * 2
