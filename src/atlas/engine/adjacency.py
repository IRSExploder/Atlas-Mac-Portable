"""The map's adjacency graph, built once per world and remembered.

**This exists because the graph was rebuilt 10,867 times in a twelve-turn run.** The
profile of a simulated turn put :func:`atlas.engine.air._neighbours` at 24.8s of tottime
-- roughly fifteen percent of the whole simulation -- not because building the graph is
slow but because supply, trade and air each rebuild it from SQLite on every one of their
own inner calls. ``trade.route_is_open`` alone runs 5,700 times a run and builds the whole
map each time to ask one question about one lane.

The graph is a safe thing to remember: ``region_adjacency`` is written exactly once, by
:func:`atlas.state.bootstrap.apply_seed`, and nothing else in the engine inserts, updates
or deletes a row of it. It is not a diff target, so a rewind cannot move it either -- which
is also why it is absent from ``_FINGERPRINT_TABLES``. The map is fixed for the life of a
world.

**Keying is the whole risk, and it is where the obvious version of this is wrong.** A cache
keyed on ``world_id`` alone collides: the test suite builds dozens of unrelated worlds
under ids like ``"w"`` and ``"sandbox"``, each in its own in-memory database, and one of
them would answer with another's map. Three guards, each sufficient on its own:

* **the key includes the connection's identity**, so two databases never share an entry;
* **the entry holds the connection by strong reference**, which is what makes keying on
  ``id()`` sound at all -- a freed connection's address can be handed to a new object, and
  an entry that keeps its connection alive can never be looked up by an impostor. The
  identity check on the way out (``entry_conn is conn``) then costs nothing and closes the
  gap for good;
* **and a world build forgets that world everywhere**, across every connection, so even a
  database that rebuilt a world under an id it had already used starts from the map it
  actually has.

The cache is bounded, and the bound is what keeps the strong references honest: a suite
that opens thousands of connections holds at most :data:`_CACHE_LIMIT` of them, and an
evicted entry drops its reference. The bound is small on purpose -- the engine works one
world at a time and the hit rate at eight is indistinguishable from the hit rate at a
thousand. What a pinned entry costs is the connection *object*, not the database: `close()`
frees the pages whether or not anything is still holding the handle, and nothing here ever
executes against a connection it has kept -- it only compares its identity.

**What comes back is read-only.** A shared cache handing out a mutable dict is a bug
waiting for its first caller; the mapping is a proxy and the neighbour lists are tuples, so
a caller that tried to edit the map fails loudly instead of quietly editing everybody's.
"""
from __future__ import annotations

import sqlite3
import threading
from collections import OrderedDict
from collections.abc import Mapping
from types import MappingProxyType

__all__ = ["Adjacency", "forget", "graph_of"]

#: Region id -> the regions it borders, undirected, in the order the rows arrived.
Adjacency = Mapping[str, tuple[str, ...]]

#: How many (connection, world) pairs to remember. See the module docstring: this is a
#: ceiling on retained connections, not a performance knob.
_CACHE_LIMIT = 8

_lock = threading.Lock()
_cache: OrderedDict[
    tuple[int, str], tuple[sqlite3.Connection, Adjacency]
] = OrderedDict()


def _build(conn: sqlite3.Connection, world_id: str) -> Adjacency:
    """Read the whole adjacency table into an undirected graph.

    ``crossing`` is deliberately ignored, as it always has been here: the callers that care
    what kind of border it is read that themselves, and an aeroplane does not care at all.
    """
    graph: dict[str, list[str]] = {}
    for row in conn.execute(
        "SELECT region_a, region_b FROM region_adjacency WHERE world_id = ?", (world_id,)
    ):
        a, b = str(row["region_a"]), str(row["region_b"])
        graph.setdefault(a, []).append(b)
        graph.setdefault(b, []).append(a)
    return MappingProxyType({region: tuple(edges) for region, edges in graph.items()})


def graph_of(conn: sqlite3.Connection, world_id: str) -> Adjacency:
    """The adjacency graph of ``world_id`` on ``conn``, built once and remembered."""
    key = (id(conn), world_id)
    with _lock:
        entry = _cache.get(key)
        if entry is not None and entry[0] is conn:
            _cache.move_to_end(key)
            return entry[1]

    # Built outside the lock: it is a read of somebody else's database and holding a
    # process-wide lock across it would serialise two worlds that have nothing to do with
    # each other. Two threads racing the same world both build and the second wins, which
    # costs one wasted read and is the same graph either way.
    built = _build(conn, world_id)
    with _lock:
        _cache[key] = (conn, built)
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_LIMIT:
            _cache.popitem(last=False)
    return built


def forget(world_id: str | None = None) -> None:
    """Drop what is remembered for ``world_id`` -- on every connection -- or all of it.

    Called when a world's map is written. It is deliberately blind to which connection did
    the writing: an id reused across databases is exactly the case that would otherwise go
    unnoticed, and forgetting a graph costs one rebuild.
    """
    with _lock:
        if world_id is None:
            _cache.clear()
            return
        for key in [key for key in _cache if key[1] == world_id]:
            del _cache[key]
