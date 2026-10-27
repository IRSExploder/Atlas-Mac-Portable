"""Recording, applying and rejecting diff batches.

The sequence is deliberate and the ordering carries the design:

1. **Record the batch as proposed, and commit.** Before anything is applied. If the
   proposed rows were written inside the savepoint, rolling back a cheating batch would
   erase the evidence of the cheating -- the audit trail would show only the batches that
   were legal, which is exactly backwards.
2. **Open a savepoint and apply in ``seq`` order**, capturing each field's prior value as
   it goes. That captured value is what compare-and-swap and the magnitude caps compare
   against.
3. **Run the rules against the real post-apply database.** Joint effects come free.
4. **Release or roll back.** On rejection the world is untouched, the diffs are marked
   rejected with the rule that caught them, and the player can read why.

Because every mutation leaves a row with both old and new values, rewind is replay: see
:mod:`atlas.state.replay`.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from .diff import Diff, DiffBatch
from .models import Difficulty, DiffOp, DiffStatus, EventKind, EventOrigin, Role, Visibility
from .validate import SEVER_MINORITY, ValidationResult, Violation, validate

__all__ = ["ApplyOutcome", "apply_batch", "record_event", "unrecord_events"]

#: Every table a diff can address, mapped to how its rows are keyed. Public because
#: rewind must undo exactly what the applier can write, and a second copy of this
#: table drifts the moment a new one is added.
PRIMARY_KEY: dict[str, tuple[str, ...]] = {
    "world": ("id",),
    "nation": ("id",),
    "region": ("id",),
    "unit": ("id",),
    "city": ("id",),
    "commitment": ("id",),
    "relation": ("nation_a", "nation_b"),
    # Contests accumulate across turns, so they must be diffed like any other state:
    # recomputing them on load instead would make a rewind land on a different campaign
    # than the one that was played.
    "contest": ("id",),
    # Declared intent is state too: a rewind that restored the campaign but not what
    # each staff was trying to do would replay the turn with different orders.
    #: What a country is built to do. Diffed because it is written after world creation
    #: -- a shift adopted on turn 9 has to be un-adopted by a rewind to turn 8, and the
    #: dip it is in the middle of has to come back with it.
    "doctrine_stance": ("id",),
    "nation_strategy": ("id",),
    "strategy_revision": ("id",),
    "military_objective": ("id",),
    #: And what the war is *for*, which is the same argument one level up: a rewind that
    #: put the front back and left this power fighting for something it had not yet
    #: declared would replay the war with different terms on the table.
    "war_aim": ("id",),
    #: What a war has cost and how tired of it a belligerent is. Diffed because it
    #: accumulates across turns: a rewind that forgot it would replay the peace talks
    #: against an exhaustion the war had not reached yet.
    "war_effort": ("id",),
    #: How the five groups a government answers to feel about it. Diffed because it
    #: accumulates: a rewind that forgot it would replay the turn with a country that had
    #: already forgiven something.
    "constituency": ("id",),
    "intel_network": ("id",),
    "attribution": ("id",),
    "exposure": ("id",),
    "intel_term": ("id",),
    #: What a peace promised. Diffed because a rewind past the signing has to take the
    #: promise back with it -- the same argument `commitment_term` makes next door.
    "peace_term": ("id",),
    #: What the world remembers. Diffed as well as cascading with its source event, because
    #: the two undo different things: the cascade removes a fact whose event is gone, and
    #: the diff restores one that was *retired* on a turn being undone.
    "world_fact": ("id",),
    #: A half-built ship is money already spent, so it has to unbuild on a rewind too.
    "construction": ("id",),
    #: What a nation has worked out how to do, and what that changed about what it builds.
    #: Both are diffable for one reason: a rewind past a completed programme that left the
    #: knowledge behind would replay the turn with better ships than the turn had.
    "technology": ("nation_id", "tech_id"),
    "unit_template": ("world_id", "kind"),
    #: Warheads are spent, destroyed on the ground and fired back. Every one of those is a
    #: diff for the ordinary reason: a rewind that put the city back but left the missile
    #: expended would be a rewind that disarmed somebody retroactively.
    "arsenal": ("nation_id",),
    #: A body's standing moves every time it is honoured or defied, so it is state a
    #: rewind has to put back -- otherwise replaying a turn re-votes against a Council
    #: that had already been discredited.
    "organisation": ("nation_id",),
    #: And a standing measure is exactly the kind of thing a rewind that forgot it would
    #: leave draining an economy for a resolution that no longer happened.
    "sanction": ("id",),
    #: Who holds the sky over a province. Diffable because it persists between turns and
    #: feeds the ground adjudicator: a rewind that put the front line back but left the
    #: air superiority won on a turn that no longer happened would replay the battle with
    #: the wrong odds.
    "air_control": ("world_id", "region_id"),
    #: What a country has in store. Diffable for the reason `arsenal` is: a rewind past a
    #: blockade that restored the fleet but left the stockpile it drained would replay the
    #: turn with a full magazine.
    "resource": ("nation_id", "good"),
    #: A standing agreement to sell. It is opened and broken by turns, so it rewinds with
    #: them.
    "trade_route": ("id",),
    # What a government is trying to arrange. Addressable because the engine writes
    # it through the diff pipeline like everything else -- an intent that appeared
    # outside a batch would be state a rewind could not undo.
    "trade_intent": ("id",),
    # And what it has refused to sell, to whom, on purpose. Every field of it moves
    # through the pipeline -- the imposition, the lifting, and the revenue it has cost
    # its proposer -- because an embargo is the most consequential thing a government
    # can do to a trade lane and a rewind that left one standing would replay the turn
    # under a coercion nobody had ordered.
    "trade_embargo": ("id",),
    #: Who governs, and the agreements between powers. **Both were written with direct SQL
    #: and neither rewound**, which PT31's laboratory caught: a coup on turn 3 rewound to
    #: turn 2 left the junta in the chair, and a pact signed on turn 3 was still standing.
    #: Both tables are in `_FINGERPRINT_TABLES` precisely because they are state a rewind
    #: has to put back, so they belong here too.
    "leader": ("id",),
    "commitment_nation": ("commitment_id", "nation_id"),
    #: What an agreement promised, in terms the engine can check. Diffable because a
    #: rewind past the signing has to take the promise back with it -- otherwise the
    #: breach detector spends the rest of the game checking a clause nobody agreed to.
    "commitment_term": ("commitment_id", "seq"),
    #: Who is backing whom in somebody else's war. Diffable for the reason `sanction` is:
    #: a rewind that forgot it would leave a client fighting with shipments from a decision
    #: that no longer happened.
    "support": ("world_id", "patron_id", "client_id", "kind"),
    #: How far a colony has got toward wanting out. Diffable because it accumulates across
    #: turns: a rewind that put the empire back but left the movement where it had reached
    #: would replay the decade with a rebellion that had not happened yet.
    "independence": ("world_id", "region_id"),
    #: Who is allowed to see what. Espionage grants sight of an *older* event, so the grant
    #: outlives the turn that bought it unless a rewind can undo it -- which needs it here.
    #: Grants that belong to an event of the turn being undone are removed by that event's
    #: own cascade; this is for the ones that point backwards.
    "event_visibility": ("event_id", "nation_id"),
}


@dataclass(slots=True)
class ApplyOutcome:
    batch_id: int
    applied: bool
    validation: ValidationResult
    #: Diffs as they finished: applied, or rejected with the rule that caught them.
    diffs: tuple[Diff, ...] = ()
    #: Seqs dropped so the rest of the batch could stand.
    severed: tuple[int, ...] = ()
    #: ``(seq, rule_id, reason)`` for each severed diff. ``severed`` says how many were
    #: lost; this says which and why, which is what the turn report shows and what a model
    #: would need if the refusals were ever fed back to it.
    refusals: tuple[tuple[int, str, str], ...] = ()

    @property
    def reason(self) -> str:
        return self.validation.reason()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _key_clause(table: str) -> tuple[str, int]:
    columns = PRIMARY_KEY.get(table)
    if columns is None:
        raise ValueError(f"table {table!r} is not addressable by a diff")
    return " AND ".join(f"{c} = ?" for c in columns), len(columns)


def _key_values(table: str, target_id: str) -> tuple[str, ...]:
    """Split a composite key. ``relation`` rows are addressed as ``"<a>|<b>"``."""
    if len(PRIMARY_KEY[table]) == 1:
        return (target_id,)
    parts = tuple(target_id.split("|"))
    if len(parts) != len(PRIMARY_KEY[table]):
        raise ValueError(
            f"{table} needs a composite id like 'a|b'; got {target_id!r}"
        )
    return parts


def _read_current(conn: sqlite3.Connection, table: str, target_id: str, column: str) -> Any:
    clause, _ = _key_clause(table)
    row = conn.execute(
        f"SELECT {column} FROM {table} WHERE {clause}",  # noqa: S608 - identifiers are validated
        _key_values(table, target_id),
    ).fetchone()
    return None if row is None else row[0]


def _row_exists(conn: sqlite3.Connection, table: str, target_id: str) -> bool:
    """Whether the row a diff addresses is there at all.

    Distinct from :func:`_read_current` returning ``None``, which is ambiguous: a column
    that is legitimately NULL and a row that does not exist look identical through it. That
    ambiguity is what let an update to a missing row report as applied.
    """
    clause, _ = _key_clause(table)
    return conn.execute(
        f"SELECT 1 FROM {table} WHERE {clause} LIMIT 1",  # noqa: S608 - identifiers validated
        _key_values(table, target_id),
    ).fetchone() is not None


def _validate_identifier(name: str, allowed: set[str]) -> str:
    """Guard against SQL injection through a model-supplied column name.

    Column names cannot be parameterised, so they are interpolated -- which means they
    must be checked against the real schema first. A model chooses ``diff.field``, so
    this is a genuine injection surface, not a theoretical one.
    """
    if name not in allowed:
        raise ValueError(f"unknown column {name!r}")
    return name


def _columns_of(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _resolve_nation(conn: sqlite3.Connection, world_id: str, reference: str | None) -> str | None:
    """Turn a model's reference to a nation into a real id, or ``None``.

    Models naturally write ``"Aurelia"`` where the schema wants ``"aurelia"``. Names are
    unique per world, so resolving them is unambiguous and saves discarding an otherwise
    sound turn over capitalisation.
    """
    if not reference:
        return None
    row = conn.execute(
        "SELECT id FROM nation WHERE world_id = ? AND (id = ? OR LOWER(name) = LOWER(?))",
        (world_id, reference, reference),
    ).fetchone()
    return row["id"] if row else None


def record_event(
    conn: sqlite3.Connection,
    *,
    world_id: str,
    turn: int,
    date: str,
    kind: EventKind,
    origin: EventOrigin,
    text: str = "",
    actor_nation_id: str | None = None,
    visibility: Visibility = Visibility.PUBLIC,
    visible_to: tuple[str, ...] = (),
    causes: tuple[int, ...] = (),
    batch_id: int | None = None,
    notes: list[str] | None = None,
    participants: tuple[tuple[str, str], ...] = (),
    region_participants: tuple[tuple[str, str], ...] = (),
) -> int:
    """Insert an event and its visibility and causation edges.

    ``origin`` is a parameter rather than inferred, and callers in the model path always
    pass ``MODEL``. Only the engine's own resolution passes ``ENGINE``, which is what
    makes the gating rules meaningful.

    **Model-supplied references are filtered rather than trusted.** A world simulation
    routinely cites a cause id it expects to exist, or names a nation in prose form. Left
    to the foreign keys, any one of those raises ``IntegrityError`` and destroys the whole
    batch -- a turn's worth of expensive work lost to a guessed integer, reported as an
    unreadable database error. This mirrors the decision already made for
    ``diff.reason_event_id``: a bad reference is dropped and *recorded* in ``notes``, so
    it stays visible without being fatal.
    """
    notes = notes if notes is not None else []

    resolved_actor = _resolve_nation(conn, world_id, actor_nation_id)
    if actor_nation_id and resolved_actor is None:
        notes.append(f"unknown actor {actor_nation_id!r} on a {kind.value} event; recorded without one")
    elif actor_nation_id and resolved_actor != actor_nation_id:
        notes.append(f"resolved actor {actor_nation_id!r} to {resolved_actor!r}")
    actor_nation_id = resolved_actor

    known_causes: list[int] = []
    for cause_id in causes:
        exists = conn.execute("SELECT 1 FROM event WHERE id = ?", (cause_id,)).fetchone()
        if exists:
            known_causes.append(int(cause_id))
        else:
            notes.append(f"dropped citation of event {cause_id}, which does not exist")

    resolved_visible: list[str] = []
    for nation_reference in visible_to:
        resolved = _resolve_nation(conn, world_id, nation_reference)
        if resolved:
            resolved_visible.append(resolved)
        else:
            notes.append(f"dropped unknown nation {nation_reference!r} from event visibility")

    cursor = conn.execute(
        """
        INSERT INTO event (world_id, turn, date, actor_nation_id, kind, origin,
                           visibility, text, created_by_batch)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (world_id, turn, date, actor_nation_id, kind.value, origin.value,
         visibility.value, text, batch_id),
    )
    event_id = int(cursor.lastrowid)

    if visibility is not Visibility.PUBLIC:
        for nation_id in resolved_visible:
            conn.execute(
                "INSERT OR IGNORE INTO event_visibility (event_id, nation_id) VALUES (?, ?)",
                (event_id, nation_id),
            )

    for cause_id in known_causes:
        if cause_id != event_id:
            conn.execute(
                "INSERT OR IGNORE INTO event_cause (event_id, cause_id) VALUES (?, ?)",
                (event_id, cause_id),
            )

    # **Who it happened *to*.** `actor_nation_id` records the doer and nothing recorded the
    # other end, so every consumer that needed it guessed -- and `treaties._aggressor`
    # guessed wrong in a way that blamed the wrong country for a broken pact. An unknown
    # nation is dropped with a note rather than raising, exactly as an unknown actor or a
    # dangling citation is: a mistyped participant must not cost a turn's work.
    _record_participants(conn, world_id, event_id, participants, region_participants, notes)
    return event_id


def unrecord_events(conn: sqlite3.Connection, event_ids) -> None:
    """Remove events the engine wrote moments ago and then could not stand behind.

    **Not a way to edit history**, and the distinction is the whole of its safety. These
    are engine-authored events written inside one act that failed: nothing has cited them,
    the batch that would have has been rolled back, and their ``event_visibility``,
    ``event_participant`` and ``event_cause`` rows go with them by cascade. A rewind
    deletes events for the same reason -- a future that no longer happened must not be
    readable as if it had.

    The alternative is worse than untidy. An outcome event has to exist *before* the batch
    so the batch's diffs can cite it, so every engine act that can be refused writes its
    news first and finds out afterwards whether it happened. Left behind, that news is
    indistinguishable from news of something real: every later briefing quotes it, the
    treaty layer attributes from it, and the world reasons from an event with no state
    behind it.

    Lives here rather than in one engine module because three of them need it, and a
    second copy would be a second set of tables to remember to cascade.
    """
    for event_id in event_ids:
        if event_id is not None:
            conn.execute("DELETE FROM event WHERE id = ?", (event_id,))


def _record_participants(
    conn: sqlite3.Connection,
    world_id: str,
    event_id: int,
    participants: tuple[tuple[str, str], ...],
    region_participants: tuple[tuple[str, str], ...],
    notes: list[str],
) -> None:
    """Write ``(role, entity)`` pairs against an event, numbering repeats within a role."""
    seen: dict[str, int] = {}
    for role, reference in participants:
        resolved = _resolve_nation(conn, world_id, reference)
        if resolved is None:
            notes.append(f"dropped unknown {role} {reference!r} from an event's participants")
            continue
        seq = seen.get(role, 0)
        seen[role] = seq + 1
        conn.execute(
            "INSERT OR IGNORE INTO event_participant (event_id, role, seq, nation_id)"
            " VALUES (?, ?, ?, ?)",
            (event_id, role, seq, resolved),
        )
    for role, region_id in region_participants:
        known = conn.execute(
            "SELECT 1 FROM region WHERE world_id = ? AND id = ?", (world_id, region_id)
        ).fetchone()
        if known is None:
            notes.append(f"dropped unknown {role} region {region_id!r} from an event")
            continue
        seq = seen.get(role, 0)
        seen[role] = seq + 1
        conn.execute(
            "INSERT OR IGNORE INTO event_participant (event_id, role, seq, region_id)"
            " VALUES (?, ?, ?, ?)",
            (event_id, role, seq, region_id),
        )


def _insert_batch(conn: sqlite3.Connection, batch: DiffBatch) -> int:
    cursor = conn.execute(
        """
        INSERT INTO diff_batch (world_id, turn, proposer_role, proposer_model,
                                proposer_nation_id, status, created_at)
        VALUES (?, ?, ?, ?, ?, 'proposed', ?)
        """,
        (
            batch.world_id,
            batch.turn,
            batch.proposer_role.value,
            batch.proposer_model,
            batch.proposer_nation_id,
            _now(),
        ),
    )
    batch_id = int(cursor.lastrowid)

    for diff in batch.diffs:
        conn.execute(
            """
            INSERT INTO diff (batch_id, seq, target_table, target_id, field, op,
                              new_json, expected_prior_json, reason_event_id,
                              visibility_scope, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed')
            """,
            (
                batch_id,
                diff.seq,
                diff.target_table,
                diff.target_id,
                diff.field,
                diff.op.value,
                json.dumps(diff.new_value),
                json.dumps(diff.expected_prior),
                diff.reason_event_id,
                diff.visibility_scope.value,
            ),
        )
    return batch_id


#: Columns whose SQL CHECK is a *hard* numeric range, clamped to their edges on write rather
#: than rejected. Keyed by (table, column) → (low, high). Only genuine hard bounds belong here
#: -- a value that means "the maximum", where clamping preserves the intent and rejecting only
#: throws away the rest of an atomic batch. Semantic caps (per-turn swing limits, visibility
#: scope) are rules the proposer must satisfy, not values to silently round, and stay out.
_BOUNDED_COLUMNS: dict[tuple[str, str], tuple[float, float]] = {
    ("relation", "score"): (-100.0, 100.0),
}


def _apply_one(conn: sqlite3.Connection, diff: Diff) -> Diff:
    """Apply a single diff, returning it annotated with the value it displaced.

    **Invariant relied on by every rule:** the returned diff has ``old_value`` and
    ``new_value`` set to the absolute values before and after the write, whatever the
    op was. An ``adjust`` is resolved to its result here, so a rule can always compute
    the change as ``new_value - old_value`` without re-checking the op. Rules run
    post-apply, so this is the only representation they ever see; treating ``new_value``
    as a delta downstream double-counts it.
    """
    table = diff.target_table
    if table not in PRIMARY_KEY:
        raise ValueError(f"table {table!r} is not addressable by a diff")

    # A row appearing or disappearing is still a diff, so that rewind can undo it like
    # any other change. Without this a raised division would be invisible to replay and a
    # rewound world would quietly keep an army it was never given.
    if diff.op is DiffOp.INSERT:
        return _apply_insert(conn, diff)
    if diff.op is DiffOp.DELETE:
        return _apply_delete(conn, diff)

    columns = _columns_of(conn, table)
    column = _validate_identifier(diff.field, columns)
    clause, _ = _key_clause(table)
    keys = _key_values(table, diff.target_id)

    # **An update must find something to update.** `UPDATE ... WHERE` on a row that is not
    # there changes nothing and reports success, and the diff was recorded as applied with
    # `old_value = None` -- so the audit trail claimed a change that never happened, a rule
    # reading `new_value - old_value` saw a swing from nothing, and a rewind would try to
    # restore a value into a row that does not exist. A delete has always refused a missing
    # row (see `_apply_delete`); this is the same rule for the same reason, and it was the
    # only op that did not have it.
    if not _row_exists(conn, table, diff.target_id):
        raise ValueError(
            f"{table} {diff.target_id!r} does not exist and cannot be updated"
        )

    before = _read_current(conn, table, diff.target_id, column)

    if diff.op.value == "adjust":
        base = float(before or 0.0)
        value: Any = base + float(diff.new_value or 0.0)
    else:
        value = diff.new_value

    # A hard numeric bound is clamped to its edge rather than rejected. A model that proposes
    # +120 regard means "as friendly as it gets", and dropping the whole (atomic) batch over
    # the overshoot throws away its other, valid diffs with it -- the single commonest way a
    # real-model turn lost work. Only *hard* bounds are clamped here; a semantic cap like the
    # per-turn relation swing (DIP-002) is a rule the model must respect, not a value to round.
    bounds = _BOUNDED_COLUMNS.get((table, column))
    if bounds is not None and value is not None:
        lo, hi = bounds
        value = max(lo, min(hi, float(value)))

    conn.execute(
        f"UPDATE {table} SET {column} = ? WHERE {clause}",  # noqa: S608 - identifiers validated above
        (value, *keys),
    )
    return replace(diff, old_value=before, new_value=value)


def _apply_insert(conn: sqlite3.Connection, diff: Diff) -> Diff:
    """Create a row. ``new_value`` is the column map; ``old_value`` records that it was absent."""
    table = diff.target_table
    payload = dict(diff.new_value or {})
    # **The row a diff creates must be the row the diff names.** `setdefault` let a payload
    # carrying its own primary key win over `target_id`, so the row landed under one id
    # while the audit trail recorded another -- and a rewind, which deletes by `target_id`,
    # then removed nothing and left the row behind forever. Reproduced on `construction`:
    # the diff said `c_named`, the row appeared as `c_actually`, and the batch reported
    # applied.
    #
    # Absent is fine and is the normal case; conflicting is refused rather than reconciled,
    # because there is no way to tell which of the two the writer meant.
    expected = (
        (("id", diff.target_id),) if PRIMARY_KEY[table] == ("id",)
        else tuple(zip(PRIMARY_KEY[table], _key_values(table, diff.target_id), strict=True))
    )
    for column, value in expected:
        written = payload.get(column)
        if written is not None and str(written) != str(value):
            raise ValueError(
                f"{table} insert names {diff.target_id!r} and its payload says "
                f"{column}={written!r}; a row must be created under the id the diff "
                "addresses, or a rewind cannot find it"
            )
        payload[column] = value
    columns = _columns_of(conn, table)
    for name in payload:
        _validate_identifier(name, columns)

    names = sorted(payload)
    conn.execute(
        f"INSERT INTO {table} ({', '.join(names)}) "  # noqa: S608 - identifiers validated above
        f"VALUES ({', '.join('?' for _ in names)})",
        tuple(payload[name] for name in names),
    )
    return replace(diff, old_value=None, new_value=payload)


def _apply_delete(conn: sqlite3.Connection, diff: Diff) -> Diff:
    """Remove a row, keeping every column in ``old_value`` so rewind can put it back."""
    table = diff.target_table
    clause, _ = _key_clause(table)
    keys = _key_values(table, diff.target_id)
    row = conn.execute(
        f"SELECT * FROM {table} WHERE {clause}", keys  # noqa: S608 - identifiers are literals
    ).fetchone()
    if row is None:
        raise ValueError(f"{table} {diff.target_id!r} does not exist and cannot be removed")

    conn.execute(
        f"DELETE FROM {table} WHERE {clause}", keys  # noqa: S608 - identifiers are literals
    )
    # row.keys() is the sqlite3.Row API, not a dict's -- the row is not a mapping.
    return replace(diff, old_value=dict(zip(row.keys(), tuple(row), strict=True)), new_value=None)


def apply_batch(
    conn: sqlite3.Connection,
    batch: DiffBatch,
    *,
    difficulty: Difficulty = Difficulty.NORMAL,
    days: int = 30,
) -> ApplyOutcome:
    """Record, apply, validate, and either keep or roll back a batch.

    Returns rather than raises on rejection: a refused batch is an ordinary, expected
    outcome that the player is shown, not an exceptional condition.
    """
    batch = batch.with_seq()

    # Step 1: the proposal goes on the record before anything is attempted.
    batch_id = _insert_batch(conn, batch)
    conn.commit()

    # Step 2: model-authored events land inside the savepoint, so a rejected batch does
    # not leave its narrative behind as if it had happened.
    conn.execute("SAVEPOINT atlas_batch")
    applied: list[Diff] = []
    try:
        _record_batch_events(conn, batch, batch_id)

        for diff in batch.diffs:
            applied.append(_apply_one(conn, diff))

    except Exception as exc:  # noqa: BLE001 - a malformed diff must not corrupt the world
        conn.execute("ROLLBACK TO atlas_batch")
        conn.execute("RELEASE atlas_batch")
        result = ValidationResult(
            ok=False,
            violations=(
                Violation("CORE-000", f"batch could not be applied: {type(exc).__name__}: {exc}"),
            ),
        )
        _mark_rejected(conn, batch_id, result)
        conn.commit()
        return ApplyOutcome(batch_id=batch_id, applied=False, validation=result, diffs=tuple(applied))

    # Step 3: rules see the real world, post-apply.
    checked = replace(batch, diffs=tuple(applied))
    result = validate(conn, checked, difficulty=difficulty, days=days)

    # Step 4: keep it or put it back.
    if result.ok:
        # **The audit trail goes in before the RELEASE, not after it.** Releasing the
        # outermost savepoint *is* the commit, so marking afterwards committed the world's
        # change in one transaction and the record rewind reads -- `status='applied'` and
        # `old_json` -- in a second. A crash between the two left a change rewind could not
        # see. Inside, the two land together or not at all (and in one fsync, not one per
        # diff).
        _seal(conn, lambda: _mark_applied(conn, batch_id, applied))
        return ApplyOutcome(
            batch_id=batch_id,
            applied=True,
            validation=result,
            diffs=tuple(replace(d, status=DiffStatus.APPLIED) for d in applied),
        )

    conn.execute("ROLLBACK TO atlas_batch")

    # A handful of bad writes should cost their own writes, not the turn's narrative.
    partial = _sever_and_stand(conn, batch, batch_id, result, difficulty, days)
    if partial is not None:
        return partial

    conn.execute("RELEASE atlas_batch")
    _mark_rejected(conn, batch_id, result)
    conn.commit()

    by_seq = {v.diff_seq: v for v in result.violations if v.diff_seq is not None}
    annotated = tuple(
        d.rejected(by_seq[d.seq].rule_id, by_seq[d.seq].reason) if d.seq in by_seq else d
        for d in applied
    )
    return ApplyOutcome(batch_id=batch_id, applied=False, validation=result, diffs=annotated)


def _record_batch_events(conn: sqlite3.Connection, batch: DiffBatch, batch_id: int) -> None:
    """Write a batch's model-authored events inside the caller's savepoint."""
    row = conn.execute(
        "SELECT world_date FROM world WHERE id = ?", (batch.world_id,)
    ).fetchone()
    date = row["world_date"] if row else ""
    for raw in batch.events:
        try:
            kind = EventKind(raw.get("kind", "narrative"))
        except ValueError:
            kind = EventKind.NARRATIVE
        record_event(
            conn,
            world_id=batch.world_id,
            turn=batch.turn,
            date=date,
            kind=kind,
            origin=EventOrigin.ENGINE
            if batch.proposer_role is Role.ENGINE
            else EventOrigin.MODEL,
            text=str(raw.get("text", "")),
            actor_nation_id=raw.get("actor_nation_id"),
            visibility=Visibility(raw.get("visibility", "public")),
            visible_to=tuple(raw.get("visible_to") or ()),
            causes=tuple(raw.get("causes") or ()),
            batch_id=batch_id,
        )


#: How many times a batch may be re-validated after severing. Severing changes what the
#: aggregate rules see -- drop a purchase and the treasury rule reads a different total --
#: so one pass is not always enough to find every write that has to go. Bounded because
#: each pass costs a full re-apply and a full rule sweep, and a batch needing four rounds
#: of surgery is a batch that should be refused.
_SEVER_ROUNDS = 4


def _sever_and_stand(
    conn: sqlite3.Connection,
    batch: DiffBatch,
    batch_id: int,
    result: ValidationResult,
    difficulty: Difficulty,
    days: int = 30,
) -> ApplyOutcome | None:
    """Drop the condemned diffs and let the rest stand, or ``None`` to refuse the batch.

    **The caller has already rolled back to `atlas_batch`, and the savepoint is still
    open.** ``ROLLBACK TO`` does not release it, so every round of this loop re-applies
    from the same clean pre-batch state -- no nesting, and no way for a half-applied round
    to survive into the next one.

    Three things bound it, and each answers a different way this could go wrong:

    * a violation that names no diff ends it immediately -- that violation is about the
      batch, and no subset of the batch answers it;
    * severing may not take a majority (:data:`atlas.state.validate.SEVER_MINORITY`),
      because a batch that is mostly refused misunderstood the contract and landing its
      remainder is worse than landing none of it. A stale compare-and-swap is exempt, for
      the reason written at :data:`atlas.state.validate._UNLIMITED_SEVERABLE`;
    * at most :data:`_SEVER_ROUNDS` passes, because severing moves the aggregates and each
      pass pays for a full re-apply and rule sweep.

    Events are re-recorded each round, because the rollback took them with the diffs. The
    batch genuinely happened -- minus the writes that were refused.
    """
    condemned: dict[int, Violation] = {}
    total = len(batch.diffs)

    for _round in range(_SEVER_ROUNDS):
        if any(not v.severable for v in result.violations):
            return None
        newly = {
            v.diff_seq: v for v in result.violations if v.diff_seq is not None
        }
        if not newly:
            return None
        condemned.update(newly)

        # The bound is checked against everything condemned so far rather than against
        # this round's violations, so a batch cannot creep past the minority by producing
        # an exempt rule in one round and an ordinary one in the next.
        bounded = not all(v.unlimited for v in condemned.values())
        if bounded and len(condemned) >= total * SEVER_MINORITY:
            return None
        kept = tuple(d for d in batch.diffs if d.seq not in condemned)
        if not kept:
            return None

        conn.execute("ROLLBACK TO atlas_batch")
        reduced = replace(batch, diffs=kept)
        applied: list[Diff] = []
        try:
            _record_batch_events(conn, reduced, batch_id)
            for diff in kept:
                applied.append(_apply_one(conn, diff))
        except Exception:  # noqa: BLE001 - fall through to the ordinary rejection path
            conn.execute("ROLLBACK TO atlas_batch")
            return None

        result = validate(
            conn,
            replace(reduced, diffs=tuple(applied)),
            difficulty=difficulty,
            days=days,
        )
        if result.ok:
            return _stand_partial(conn, batch_id, applied, condemned, result)

    conn.execute("ROLLBACK TO atlas_batch")
    return None


def _stand_partial(
    conn: sqlite3.Connection,
    batch_id: int,
    applied: list[Diff],
    condemned: dict[int, Violation],
    result: ValidationResult,
) -> ApplyOutcome:
    """Commit a batch that lost some of its diffs, with the losses on the record.

    Every severed diff keeps **the rule that caught it and that rule's own words**, rather
    than one generic sentence for the whole batch. That is the difference between telling
    a player three changes were dropped and telling them which three and why -- and it is
    what a model would need if the refusals were ever fed back to it.
    """
    def mark() -> None:
        _mark_applied(conn, batch_id, applied)
        caught_by = ", ".join(sorted({v.rule_id for v in condemned.values()}))
        conn.execute(
            "UPDATE diff_batch SET status = 'partial', rejection_reason = ? WHERE id = ?",
            (f"{len(condemned)} change(s) refused ({caught_by}); the rest applied", batch_id),
        )
        for seq, violation in condemned.items():
            conn.execute(
                "UPDATE diff SET status = 'rejected', rejected_by_rule = ?,"
                " rejection_reason = ? WHERE batch_id = ? AND seq = ?",
                (violation.rule_id, violation.reason[:1000], batch_id, seq),
            )

    # Inside the savepoint for the reason given at the whole-batch RELEASE in `apply_batch`:
    # the surviving writes and the record of which ones survived are one commit.
    _seal(conn, mark)
    return ApplyOutcome(
        batch_id=batch_id,
        applied=True,
        validation=result,
        diffs=tuple(replace(d, status=DiffStatus.APPLIED) for d in applied),
        severed=tuple(sorted(condemned)),
        refusals=tuple(
            (seq, violation.rule_id, violation.reason)
            for seq, violation in sorted(condemned.items())
        ),
    )


def _seal(conn: sqlite3.Connection, mark: Callable[[], None]) -> None:
    """Write a kept batch's audit trail and then release ``atlas_batch``, as one commit.

    **The order is the point.** The world's change is already inside the savepoint, and
    releasing it commits (it is the outermost transaction: `apply_batch` commits the proposal
    before opening it). So the record that says the change happened -- the one rewind walks
    -- is written first, and the RELEASE commits both or, if the process dies before it,
    neither.

    If writing the record itself fails, the change is put back rather than committed
    without its record: the batch stays ``proposed`` and the world stays as it was, which
    is the one state rewind and replay can both read correctly. The error still propagates,
    because the caller asked for a batch to be applied and it was not.
    """
    try:
        mark()
    except BaseException:
        # Guarded: some failures (a full disk, an I/O error) make SQLite roll the whole
        # transaction back itself, and then there is no savepoint left to return to --
        # trying would replace the real error with "no such savepoint".
        if conn.in_transaction:
            conn.execute("ROLLBACK TO atlas_batch")
            conn.execute("RELEASE atlas_batch")
        raise
    conn.execute("RELEASE atlas_batch")
    conn.commit()


def _mark_applied(conn: sqlite3.Connection, batch_id: int, diffs: list[Diff]) -> None:
    conn.execute("UPDATE diff_batch SET status = 'applied' WHERE id = ?", (batch_id,))
    for diff in diffs:
        conn.execute(
            "UPDATE diff SET status = 'applied', old_json = ?, new_json = ? "
            "WHERE batch_id = ? AND seq = ?",
            (json.dumps(diff.old_value), json.dumps(diff.new_value), batch_id, diff.seq),
        )


def _mark_rejected(conn: sqlite3.Connection, batch_id: int, result: ValidationResult) -> None:
    conn.execute(
        "UPDATE diff_batch SET status = 'rejected', rejection_reason = ? WHERE id = ?",
        (result.reason()[:2000], batch_id),
    )
    conn.execute("UPDATE diff SET status = 'rejected' WHERE batch_id = ?", (batch_id,))
    for violation in result.violations:
        if violation.diff_seq is None:
            continue
        conn.execute(
            "UPDATE diff SET rejected_by_rule = ?, rejection_reason = ? "
            "WHERE batch_id = ? AND seq = ?",
            (violation.rule_id, violation.reason[:1000], batch_id, violation.diff_seq),
        )
