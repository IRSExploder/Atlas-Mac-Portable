"""What every persistent table is, and what that obliges the rest of the engine to do.

**A paired fingerprint only proves equality over the state the fingerprint covers.** A
table left out of :data:`~atlas.state.bootstrap._FINGERPRINT_TABLES` can differ between
two worlds while the digest reports them identical -- which is the PT31 class exactly,
one level up. PT31 was `leader` and `commitment` written with direct SQL and never
rewound, and the reason nothing noticed for so long was that neither table was in the
digest. Adding the two tables fixed those two tables. It did not fix the *shape* of the
mistake, which is that the digest's coverage is a list nobody is obliged to extend: a new
table joins the schema classified as nothing, covered by nothing, and every
rewind-exactness claim made about it is vacuous until somebody happens to notice.

So the omission is made deliberate here. Every table in `sqlite_schema` must appear in
:data:`REGISTRY` as exactly one thing, and :func:`registry_problems` checks the
classification against the two lists that act on it. A table that is not classified is a
failure, not a default -- which is the whole point: adding one now costs a sentence of
thought rather than a silent hole.

The classes
-----------
``STATE``
    Gameplay state. Two worlds that disagree about it are different worlds, so it is
    fingerprinted; and a rewind has to put it back, either by inverse diff (``BY_DIFF``,
    which needs the table in `apply.PRIMARY_KEY`) or by the turn truncation and foreign-key
    cascade that removes an event and everything hanging off it (``BY_TRUNCATION``).

``HISTORY``
    The append-only record of how the world got here. Deliberately *not* fingerprinted:
    two runs that reach identical state by different recorded routes are the same world,
    and a digest that said otherwise would fail every honest replay. `replay.rewind_to`
    truncates each of these by turn, or cascades it from something it does.

``CACHE``
    Derived, and reconstructible from whatever it was derived from.

``CONSTANT``
    Load-bearing for gameplay -- the map, the charter -- and written only while the world
    is being built. Not fingerprinted, and safe to leave out for exactly one reason: there
    is nothing behind turn zero for a rewind to restore. **That reason is a claim about
    the code rather than a fact about the schema**, which is why it is not enough to
    declare it: `tests/test_every_table_says_what_it_is.py` finds every writer of these
    tables and fails if one appears outside the creation path. The day a nation can join
    the Security Council mid-game, that test goes red and this classification has to
    change -- which is the forcing function the whole module exists for.

``METADATA``
    A fact about the file rather than about the world: where it was forked from, which
    model is playing which bloc, a table nothing reads any more.

Two worlds, compared properly
-----------------------------
:func:`semantic_dump` and :func:`paired_world_problems` exist for the other half of the
reviewer's point. An experiment that pairs two runs on `fingerprint()` alone inherits the
digest's coverage, so it cannot see a divergence in a table the digest does not cover.
The dump compares ``STATE`` *and* ``CONSTANT`` -- everything a difference in which would
mean the two runs were not the same experiment -- and names the table and the rows rather
than reporting one hexadecimal number that does not match.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from .apply import PRIMARY_KEY
from .bootstrap import _FINGERPRINT_TABLES, _VIA_EVENT, canonical_rows

__all__ = [
    "BY_DIFF",
    "BY_TRUNCATION",
    "CACHE",
    "COMPARED",
    "compared_tables",
    "CONSTANT",
    "HISTORY",
    "METADATA",
    "REGISTRY",
    "STATE",
    "Classification",
    "paired_world_problems",
    "persistent_tables",
    "registry_problems",
    "semantic_dump",
    "tables_of_kind",
]

STATE = "state"
HISTORY = "history"
CACHE = "cache"
CONSTANT = "constant"
METADATA = "metadata"

#: How a rewind puts a ``STATE`` table back.
BY_DIFF = "diff"
BY_TRUNCATION = "truncation"

#: The kinds a paired run compares. ``STATE`` because a difference means the two runs
#: diverged; ``CONSTANT`` because a difference means they were never the same experiment.
#:
#: **Kind alone is not the whole membership.** See `Classification.gates_play`: a HISTORY
#: table that the engine reads as a flag decides the next turn, so two runs that disagree
#: about it are two different worlds whatever the record says. `compared_tables` is the
#: membership; this is only the part of it that follows from the kind.
COMPARED = (STATE, CONSTANT)

_BY_WORLD = "WHERE world_id = ?"
_VIA_ORGANISATION = (
    "WHERE organisation_id IN (SELECT nation_id FROM organisation WHERE world_id = ?)"
)


@dataclass(frozen=True, slots=True)
class Classification:
    """What one table is.

    ``why`` is required of everything that is *not* ``STATE``, because those are the
    entries where an absence from the digest has to be justified. A ``STATE`` entry
    carries no prose: the argument for each is already written beside the two lists that
    act on it, and a third copy here would be the one that goes stale.

    ``order`` and ``scope`` are needed only by a ``CONSTANT`` table, which has no entry in
    `_FINGERPRINT_TABLES` to take them from and still has to be readable by
    :func:`semantic_dump`.
    """

    kind: str
    why: str = ""
    reverses_by: str = ""
    order: str = ""
    scope: str = ""
    #: **This is history the engine reads back as a decision.**
    #:
    #: Set on a ``HISTORY`` table when some gate asks whether a row is *there* and does
    #: something different depending on the answer. Such a table stays out of the digest --
    #: it is genuinely append-only and a rewind genuinely truncates it -- but it must be in
    #: `compared_tables`, because the argument for leaving history out of a *comparison*
    #: ("two runs that reached the same state by different recorded routes are the same
    #: world") is false exactly here: the route is the state.
    #:
    #: Needs ``order`` and ``scope`` for the same reason a ``CONSTANT`` does -- there is no
    #: `_FINGERPRINT_TABLES` entry to borrow them from. `registry_problems` checks that.
    gates_play: bool = False


REGISTRY: dict[str, Classification] = {
    # -- STATE, put back by an inverse diff ------------------------------
    # Why each of these is state is argued once, next to the list that acts on it:
    # `bootstrap._FINGERPRINT_TABLES` for the digest, `apply.PRIMARY_KEY` for the rewind.
    # These entries carry the classification and nothing else, and `registry_problems`
    # fails the moment either of those lists stops agreeing with it.
    "world": Classification(STATE, reverses_by=BY_DIFF),
    "nation": Classification(STATE, reverses_by=BY_DIFF),
    "region": Classification(STATE, reverses_by=BY_DIFF),
    "relation": Classification(STATE, reverses_by=BY_DIFF),
    "unit": Classification(STATE, reverses_by=BY_DIFF),
    "city": Classification(STATE, reverses_by=BY_DIFF),
    "commitment": Classification(STATE, reverses_by=BY_DIFF),
    "commitment_nation": Classification(STATE, reverses_by=BY_DIFF),
    "commitment_term": Classification(STATE, reverses_by=BY_DIFF),
    "contest": Classification(STATE, reverses_by=BY_DIFF),
    "construction": Classification(STATE, reverses_by=BY_DIFF),
    "technology": Classification(STATE, reverses_by=BY_DIFF),
    "unit_template": Classification(STATE, reverses_by=BY_DIFF),
    "support": Classification(STATE, reverses_by=BY_DIFF),
    "independence": Classification(STATE, reverses_by=BY_DIFF),
    "arsenal": Classification(STATE, reverses_by=BY_DIFF),
    "organisation": Classification(STATE, reverses_by=BY_DIFF),
    "sanction": Classification(STATE, reverses_by=BY_DIFF),
    "doctrine_stance": Classification(STATE, reverses_by=BY_DIFF),
    "nation_strategy": Classification(STATE, reverses_by=BY_DIFF),
    "strategy_revision": Classification(STATE, reverses_by=BY_DIFF),
    "military_objective": Classification(STATE, reverses_by=BY_DIFF),
    "war_aim": Classification(STATE, reverses_by=BY_DIFF),
    "war_effort": Classification(STATE, reverses_by=BY_DIFF),
    "peace_term": Classification(STATE, reverses_by=BY_DIFF),
    "constituency": Classification(STATE, reverses_by=BY_DIFF),
    "intel_network": Classification(STATE, reverses_by=BY_DIFF),
    "intel_term": Classification(STATE, reverses_by=BY_DIFF),
    "attribution": Classification(STATE, reverses_by=BY_DIFF),
    "exposure": Classification(STATE, reverses_by=BY_DIFF),
    "world_fact": Classification(STATE, reverses_by=BY_DIFF),
    "resource": Classification(STATE, reverses_by=BY_DIFF),
    "trade_route": Classification(STATE, reverses_by=BY_DIFF),
    "trade_intent": Classification(STATE, reverses_by=BY_DIFF),
    "trade_embargo": Classification(STATE, reverses_by=BY_DIFF),
    "leader": Classification(STATE, reverses_by=BY_DIFF),
    "air_control": Classification(STATE, reverses_by=BY_DIFF),
    "event_visibility": Classification(STATE, reverses_by=BY_DIFF),

    # -- STATE, put back by truncating the turn --------------------------
    # Fingerprinted, and deliberately *not* in `PRIMARY_KEY`. An event is written by
    # `apply.record_event` rather than proposed as a diff, and `rewind_to` removes every
    # event of an undone turn outright; participants hang off the event and go with it.
    # Digested anyway, so that the cascade is provable rather than assumed -- which is not
    # a formality: `event_visibility` sits one line above under ``BY_DIFF`` precisely
    # because an espionage grant points *backwards* at an older event and the cascade
    # therefore misses it.
    "event": Classification(
        STATE, reverses_by=BY_TRUNCATION,
        why="the turn's record itself; removed wholesale by a rewind past it",
    ),
    "event_participant": Classification(
        STATE, reverses_by=BY_TRUNCATION,
        why="who an event happened to, cascading from the event",
    ),

    # -- HISTORY, append-only ------------------------------------------------------
    # None of these is fingerprinted, and that is the digest's oldest deliberate
    # omission: two runs that reach identical state by different recorded routes are the
    # same world. Each is truncated by turn in `replay.rewind_to`, or cascades from
    # something that is.
    "turn": Classification(
        HISTORY, why="the turn ledger; `rewind_to` deletes every turn past the target and "
                     "reads the survivor's end_date to put the world clock back",
    ),
    "action": Classification(
        HISTORY, why="what a player typed, and whether it parsed; deleted past the target turn",
    ),
    "diff_batch": Classification(
        HISTORY, why="the proposal record the rewind reads to know what to undo, then deletes",
    ),
    "diff": Classification(
        HISTORY, why="the mutations themselves; cascades from `diff_batch`",
    ),
    # **History that is read as a flag, so a paired run has to compare it.** Three gates
    # ask whether an edge exists and branch on the answer, and each says in its own comment
    # that it is deliberately storing state here rather than adding a column:
    # `organisation.triggers_this_turn` ("a war the Council has already convened about can
    # never raise this trigger again"), `answering.assess_unanswered_calls` ("Settled means
    # something cites it ... No flag, no column"), and `organisation._defied_before`, which
    # decides whether the harshest penalty in the layer applies.
    #
    # It stays out of the digest, which is right: it is append-only, and `rewind_to` really
    # does take both endpoints with the event they hang off. What was wrong was leaving it
    # out of the *comparison* -- two worlds differing by one row here have an identical
    # fingerprint and one Council that will sit forever against one that never will, which
    # is the PT31 shape this module was written to close.
    "event_cause": Classification(
        HISTORY, why="which event cited which; cascades from either event. Read as a flag "
                     "by three gates, so a paired run compares it even though the digest "
                     "does not -- see `gates_play`",
        gates_play=True, order="event_id, cause_id", scope=_VIA_EVENT,
    ),
    "digest": Classification(
        HISTORY, why="older turns summarised for later prompts; deleted past the target turn",
    ),
    "routing_log": Classification(
        HISTORY, why="which model answered which call; deleted past the target turn",
    ),
    # **This one's first classification carried an argument its own feature makes false.**
    # It read "nothing reads it to decide anything and two worlds that reached the same
    # state having been refused different things are the same world" -- the standard
    # HISTORY argument, and exactly the sentence `gates_play` exists to catch. A refusal row
    # is read back by `refusals.briefing_lines` into the next turn's prompt, so two worlds
    # differing by one row here hand one government a correction and the other nothing, and
    # they answer differently from that turn on. The route *is* the state, and this is a
    # purer case of it than `event_cause`: the row is not merely consulted by a gate, it is
    # quoted into a model's input.
    #
    # It still stays out of the digest, and that is not a compromise. It is genuinely
    # append-only, `rewind_to` genuinely truncates it, and `registry_problems` refuses a
    # non-STATE table that appears in `_FINGERPRINT_TABLES` -- digesting it would mean
    # calling it gameplay state, which would mean claiming a rewind puts it back by inverse
    # diff, which is not how it is put back.
    "refusal": Classification(
        HISTORY, why="what a government asked for and did not get, quoted back into its "
                     "next briefing by `engine.refusals`. Read as an input to a model, so "
                     "a paired run compares it even though the digest does not -- see "
                     "`gates_play`. Deleted past the target turn",
        gates_play=True, order="turn, nation_id, id", scope=_BY_WORLD,
    ),

    # -- CONSTANT: gameplay-load-bearing, written only at creation ----------------
    # Not fingerprinted, and the justification is the same sentence three times: there is
    # nothing behind turn zero for a rewind to restore. That is a claim about who writes
    # them, so the test finds every writer and fails if one appears outside the creation
    # path. Each carries an `order` and a `scope` because `semantic_dump` has to read them
    # and there is no `_FINGERPRINT_TABLES` entry to take those from.
    "region_adjacency": Classification(
        CONSTANT,
        why="the map. Fixed for the life of a world -- `engine.adjacency` caches it on that "
            "basis -- and written once by `bootstrap`",
        order="region_a, region_b", scope=_BY_WORLD,
    ),
    "region_geometry": Classification(
        CONSTANT,
        why="which shapes in the pack a province is drawn from; geometry is immutable and "
            "`permissions` already refuses a model the column that names it",
        order="region_id, geo_key", scope=_BY_WORLD,
    ),
    "organisation_member": Classification(
        CONSTANT,
        why="who sits on a body and who holds a veto. Read every session by the resolution "
            "odds, the trade sanctions and the score, and written only by the seeder: "
            "`permissions` records that membership would change through a resolution, and "
            "no resolution writes it yet",
        order="organisation_id, nation_id", scope=_VIA_ORGANISATION,
    ),

    # -- METADATA: about the file, not about the world -----------------------------
    "world_lineage": Classification(
        METADATA, why="which world this one was forked from, and when -- provenance, and "
                      "the same argument that keeps `world.created_at` out of the digest",
    ),
    "bloc": Classification(
        METADATA, why="which provider plays which nations in an EVE match. Configuration: "
                      "`assign_blocs` writes it once at match setup, and `permissions` "
                      "already says of `nation.bloc_id` that it is configuration",
    ),
    "doctrine": Classification(
        METADATA, why="superseded by `doctrine_stance` at schema 38 and read by nothing. "
                      "Kept because dropping a table is the destructive statement the "
                      "migration guard exists to refuse",
    ),
}


def persistent_tables(conn: sqlite3.Connection) -> tuple[str, ...]:
    """Every table the file actually has, SQLite's own bookkeeping aside."""
    return tuple(
        sorted(
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        )
    )


def tables_of_kind(*kinds: str) -> tuple[str, ...]:
    return tuple(sorted(t for t, c in REGISTRY.items() if c.kind in kinds))


def compared_tables() -> tuple[str, ...]:
    """Every table two runs of one experiment must agree about.

    `COMPARED` by kind, plus any table flagged `gates_play` -- history the engine reads
    back as a decision. A digest can leave that out and stay honest; a comparison cannot,
    because the whole claim a paired run makes is that the two arms are the same world, and
    a world whose Council will convene is not the same as one whose Council never will.
    """
    return tuple(sorted(
        table for table, entry in REGISTRY.items()
        if entry.kind in COMPARED or entry.gates_play
    ))


def registry_problems(conn: sqlite3.Connection) -> list[str]:
    """Everything the registry and the lists that act on it disagree about.

    Empty is the only acceptable answer. The checks, in the order a reader wants them:

    1. a table in the file that nothing here classifies -- the hole this module closes;
    2. a classification for a table the file does not have -- a rename nobody followed;
    3. ``STATE`` and `_FINGERPRINT_TABLES` disagreeing, **in both directions**, since a
       digested table that is not declared state is as wrong as the reverse;
    4. ``STATE``/``BY_DIFF`` and `apply.PRIMARY_KEY` disagreeing, in both directions;
    5. a ``BY_TRUNCATION`` table that *is* diff-addressable, which means the declaration
       is describing the wrong mechanism;
    6. a ``CONSTANT`` with no way to read it, and a non-``STATE`` entry with no reason.
    """
    problems: list[str] = []
    present = set(persistent_tables(conn))
    digested = {table for table, _order, _scope in _FINGERPRINT_TABLES}
    diffable = set(PRIMARY_KEY)
    declared_state = set(tables_of_kind(STATE))
    declared_by_diff = {t for t, c in REGISTRY.items() if c.reverses_by == BY_DIFF}

    for table in sorted(present - set(REGISTRY)):
        problems.append(
            f"{table} is in the database and not in the registry: classify it as state, "
            "history, cache, constant or metadata. State is fingerprinted and rewound; "
            "anything else has to say why it need not be"
        )
    for table in sorted(set(REGISTRY) - present):
        problems.append(f"{table} is classified here and no longer exists in the schema")

    for table in sorted(declared_state - digested):
        problems.append(
            f"{table} is classified as gameplay state and is missing from "
            "bootstrap._FINGERPRINT_TABLES: two worlds could differ in it and the digest "
            "would report them equal"
        )
    for table in sorted(digested - declared_state):
        problems.append(
            f"{table} is fingerprinted but not classified as gameplay state; one of the two "
            "is wrong"
        )

    for table in sorted(declared_by_diff - diffable):
        problems.append(
            f"{table} is declared reversible by diff and is missing from apply.PRIMARY_KEY: "
            "a rewind cannot undo a write to it"
        )
    for table in sorted(diffable - declared_by_diff):
        problems.append(
            f"{table} is diff-addressable but not classified as state reversed by diff; "
            "one of the two is wrong"
        )

    for table, entry in sorted(REGISTRY.items()):
        # A table a paired run has to read needs to say how to read it: `_read` borrows
        # order and scope from `_FINGERPRINT_TABLES` for STATE and has nowhere else to
        # look. Without this the dump would raise the first time anybody compared two runs,
        # which is the worst possible moment to find out.
        if entry.gates_play and entry.kind == STATE:
            problems.append(
                f"{table} is gameplay state and does not need `gates_play`: state is "
                "compared and digested already"
            )
        if entry.gates_play and not (entry.order and entry.scope):
            problems.append(
                f"{table} is compared because it gates play and does not say how to read "
                "it: give the classification an `order` and a `scope`, as a constant does"
            )
        if entry.kind == STATE and entry.reverses_by not in {BY_DIFF, BY_TRUNCATION}:
            problems.append(
                f"{table} is state and does not say how a rewind puts it back "
                f"({BY_DIFF!r} or {BY_TRUNCATION!r})"
            )
        if entry.reverses_by == BY_TRUNCATION and table in diffable:
            problems.append(
                f"{table} is declared reversed by truncation and is also in "
                "apply.PRIMARY_KEY; say which mechanism actually undoes it"
            )
        if entry.kind != STATE and entry.reverses_by:
            problems.append(f"{table} is not state and should not declare how it rewinds")
        if entry.kind == CONSTANT and not (entry.order and entry.scope):
            problems.append(
                f"{table} is a constant and needs an order and a scope, or a paired run "
                "cannot read it"
            )
        if entry.kind != STATE and not entry.why:
            problems.append(
                f"{table} is not gameplay state and does not say why it need not be"
            )
    return problems


def _read(conn: sqlite3.Connection, table: str, world_id: str):
    """How to narrow one table to one world, from wherever that is already written down.

    A ``STATE`` table takes its order and scope from `_FINGERPRINT_TABLES`, so the dump
    and the digest cannot drift apart about what a table's rows even are. Only a
    ``CONSTANT`` declares its own, because it has no entry there to borrow from.
    """
    entry = REGISTRY[table]
    if entry.kind == STATE:
        found = [(o, s) for t, o, s in _FINGERPRINT_TABLES if t == table]
        if not found:
            # `registry_problems` reports this properly and at the right moment. Raising
            # here rather than letting a bare StopIteration out says which table, and
            # says where the answer is.
            raise KeyError(
                f"{table} is classified as gameplay state and is not in "
                "_FINGERPRINT_TABLES; run registry_problems() for the whole picture"
            )
        order, scope = found[0]
    else:
        order, scope = entry.order, entry.scope
    return canonical_rows(conn, table, order, scope, world_id)


def semantic_dump(
    conn: sqlite3.Connection, world_id: str
) -> dict[str, tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]]:
    """Every gameplay table of one world, canonically, as ``{table: (columns, rows)}``.

    Read through `bootstrap.canonical_rows`, so a table appears here exactly as it appears
    to the digest -- same column order, same text for a value. What this adds is reach:
    the ``CONSTANT`` tables the digest omits, and a shape a comparison can point at.
    """
    return {table: _read(conn, table, world_id) for table in compared_tables()}


def divergences(
    left: dict[str, tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]],
    right: dict[str, tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]],
    *,
    rows_per_table: int = 3,
) -> list[str]:
    """Where two dumps disagree, table by table and then row by row.

    A fingerprint answers *whether*. This answers *where*, which is the question anybody
    holding two runs that were supposed to match actually has.
    """
    problems: list[str] = []
    for table in sorted(set(left) | set(right)):
        if table not in left or table not in right:
            side = "right" if table not in left else "left"
            problems.append(f"{table}: present on one side only (missing from the {side})")
            continue
        left_columns, left_rows = left[table]
        right_columns, right_rows = right[table]
        if left_columns != right_columns:
            problems.append(
                f"{table}: different columns; {list(left_columns)} against "
                f"{list(right_columns)}"
            )
            continue
        if left_rows == right_rows:
            continue
        problems.append(
            f"{table}: {len(left_rows)} rows on the left against {len(right_rows)} "
            "on the right"
            if len(left_rows) != len(right_rows)
            else f"{table}: same {len(left_rows)} rows, different contents"
        )
        shown = 0
        for index in range(max(len(left_rows), len(right_rows))):
            one = left_rows[index] if index < len(left_rows) else None
            other = right_rows[index] if index < len(right_rows) else None
            if one == other:
                continue
            if shown >= rows_per_table:
                problems.append(f"  {table}: ... and further rows")
                break
            problems.append(f"  {table}[{index}]: {_row_gap(left_columns, one, other)}")
            shown += 1
    return problems


def _row_gap(columns: tuple[str, ...], one, other) -> str:
    if one is None or other is None:
        only = one if other is None else other
        side = "left" if other is None else "right"
        return f"only on the {side}: {dict(zip(columns, only, strict=True))}"
    fields = [
        f"{column}={a} against {b}"
        for column, a, b in zip(columns, one, other, strict=True)
        if a != b
    ]
    return "; ".join(fields)


def paired_world_problems(
    left: sqlite3.Connection,
    left_world: str,
    right: sqlite3.Connection,
    right_world: str,
) -> list[str]:
    """Whether two runs really did reach the same world, over every gameplay table.

    The check an experiment should pair on. `fingerprint()` compares the 40 tables it
    covers and is the right instrument inside one world -- a rewind held against itself.
    Held between two *runs* it inherits its own blind spot, and this does not: it reads
    the constants too, and it says which table and which row rather than which hash.
    """
    return divergences(semantic_dump(left, left_world), semantic_dump(right, right_world))


def json_list(stored: object) -> list:
    """A ``*_json`` column read as the list its readers expect, whatever is in it.

    **This exists because a live match died of it.** `standing_goals_json` is writable by
    both the world simulator and every tracked agent, and on turn 11 of a Fulda Gap match
    one of them set it to the bare string ``hold_line``. The column is `TEXT NOT NULL
    DEFAULT '[]'` and nothing checked the shape, so the write landed; the *next* turn built
    the referee's view, called `json.loads` on it, and raised `JSONDecodeError` out of
    `TurnEngine.jump`. Three more readers -- two nation views and the snapshot -- had the
    same line. The world was not degraded, it was unopenable, and every later turn would
    have died the same way.

    :func:`atlas.state.rules.core.core_json_columns_parse` now refuses that write, which is
    the real fix. This is the second layer, and it is the one that matters for a world
    already damaged: a save that has the bad value in it must still open. The precedent is
    `leaders._row_to_leader`, which has tolerated a malformed `traits_json` since it was
    written -- "flavour that failed to parse, not a reason to lose the government".

    A scalar is wrapped rather than dropped, because ``hold_line`` was a real intention
    badly encoded and silently showing a power *no* goals is a worse lie than showing it
    one.
    """
    if stored is None or stored == "":
        return []
    try:
        parsed = json.loads(stored)
    except (TypeError, ValueError):
        text = str(stored).strip()
        return [text] if text else []
    if parsed is None:
        return []
    # **Strings out, always.** Every caller either renders these or joins them, and
    # `"; ".join` on a list a model wrote as `[1, 2]` raises the same class of crash this
    # function exists to end -- one layer further from the write, where it is harder to
    # trace. Valid data is already strings, so this coerces nothing that was right.
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    # A JSON scalar: valid JSON, wrong shape. `json.loads('"a"')` is a string, and a string
    # is iterable, so an untolerant caller would silently render it one character per goal.
    return [str(parsed)]
