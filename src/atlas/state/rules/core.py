"""Causation, provenance and write permissions.

These are the rules that make the audit trail trustworthy. Everything downstream assumes
that a diff which landed can be traced to an event that justifies it, and that the role
which proposed it was entitled to touch that column.
"""

from __future__ import annotations

import json
import sqlite3

from ..models import GATING_EVENT_KINDS, DiffOp, EventKind, EventOrigin, Role
from ..permissions import may_write
from ..validate import RuleContext, Violation, register

__all__ = [
    "core_capital_integrity",
    "core_citation_exists",
    "core_compare_and_swap",
    "core_gating_event_origin",
    "core_json_columns_parse",
    "core_write_permission",
]


@register("CORE-001", "Every diff must cite an existing event in the same world.")
def core_citation_exists(ctx: RuleContext):
    """Mandatory causation.

    This is the rule that makes ``germany-russia relations +30`` with no traceable cause
    impossible. It is also why ``diff.reason_event_id`` has no foreign key: an FK would
    raise ``IntegrityError`` on a fabricated id and the row would never be written, but a
    fabricated citation is precisely the attempt worth keeping in the audit trail. Here
    it is rejected *and* recorded.
    """
    for diff in ctx.batch.diffs:
        if diff.reason_event_id is None:
            yield Violation(
                "CORE-001",
                f"{diff.target_table}.{diff.field} changed with no reason_event_id; "
                "every change must name the event that caused it",
                diff.seq,
            )
            continue

        row = ctx.conn.execute(
            "SELECT world_id, turn FROM event WHERE id = ?", (diff.reason_event_id,)
        ).fetchone()

        if row is None:
            yield Violation(
                "CORE-001",
                f"cited event {diff.reason_event_id} does not exist",
                diff.seq,
            )
        elif row["world_id"] != ctx.world_id:
            yield Violation(
                "CORE-001",
                f"cited event {diff.reason_event_id} belongs to another world",
                diff.seq,
            )
        elif row["turn"] > ctx.turn:
            # A cause cannot postdate its effect. Without this, a model could cite an
            # event it is creating in the same batch to justify an unrelated change.
            yield Violation(
                "CORE-001",
                f"cited event {diff.reason_event_id} is from turn {row['turn']}, "
                f"which is after this turn ({ctx.turn})",
                diff.seq,
            )


@register("CORE-002", "Privileged changes must cite an engine-authored event.")
def core_gating_event_origin(ctx: RuleContext):
    """Gating events must have ``origin='ENGINE'``.

    Without this, the whole rule table is bypassable in two turns: a nation that wants a
    war emits ``kind='casus_belli'`` on turn N and cites it on turn N+1. The event kind
    enum is closed and these kinds are absent from the schema handed to models, so a
    model *cannot* author one -- this rule is the belt to that braces, and it catches the
    case where a gating event arrives through some future code path.
    """
    if ctx.batch.proposer_role is Role.ENGINE:
        return

    for diff in ctx.batch.diffs:
        if diff.reason_event_id is None:
            continue
        row = ctx.conn.execute(
            "SELECT kind, origin FROM event WHERE id = ?", (diff.reason_event_id,)
        ).fetchone()
        if row is None:
            continue
        try:
            kind = EventKind(row["kind"])
        except ValueError:
            continue
        if kind in GATING_EVENT_KINDS and row["origin"] != EventOrigin.ENGINE:
            yield Violation(
                "CORE-002",
                f"cited event {diff.reason_event_id} is a {kind.value} but was authored by a "
                "model; privileged changes must cite an engine-resolved event",
                diff.seq,
            )


@register("CORE-003", "A role may only write columns it is permitted to write.")
def core_write_permission(ctx: RuleContext):
    """The write matrix.

    Magnitude caps alone would still permit conquest by small increments. The columns
    that *constitute* an outcome are engine-only, so a model can never set them at any
    speed. See :mod:`atlas.state.permissions`.
    """
    role = ctx.batch.proposer_role
    for diff in ctx.batch.diffs:
        # Row lifecycle is an engine decision, and checking `field` alone does not see it.
        # An insert writes a whole row at once -- every engine-only column included -- so a
        # model could conjure a region it already owned, or a nation with is_player set,
        # simply by changing the op. A delete is the same hole pointed the other way: it
        # erases state no rule would have let the model write, and takes its dependants
        # with it. Models propose intents; the engine creates and destroys rows.
        if diff.op in {DiffOp.INSERT, DiffOp.DELETE} and role is not Role.ENGINE:
            yield Violation(
                "CORE-003",
                f"role {role.value} may not {diff.op.value} a {diff.target_table} row; "
                "creating and destroying rows is engine-only, because a whole-row write "
                "would bypass the per-column permissions entirely",
                diff.seq,
            )
            continue

        # An engine insert still has to name real columns, and every column it sets is
        # checked as if it had been written individually.
        if diff.op is DiffOp.INSERT:
            for column in sorted(diff.new_value or {}):
                allowed, reason = may_write(role, diff.target_table, column)
                if not allowed:
                    yield Violation("CORE-003", reason, diff.seq)
            continue
        if diff.op is DiffOp.DELETE:
            continue

        allowed, reason = may_write(role, diff.target_table, diff.field)
        if not allowed:
            yield Violation("CORE-003", reason, diff.seq)


@register("CORE-004", "Compare-and-swap: a field must still hold the value the proposer saw.")
def core_compare_and_swap(ctx: RuleContext):
    """Detect a batch built against stale state.

    Batches are generated against a snapshot. By the time one applies, an earlier batch
    in the same turn may have moved the same field. Without this check the second write
    silently clobbers the first and the causal record becomes a lie.

    ``old_value`` is captured by the applier immediately before the write, so comparing
    it against ``expected_prior`` tells us what the world looked like at write time.
    """
    for diff in ctx.batch.diffs:
        # **Gated on the op, not on the value.** This read `if diff.old_value is None:
        # continue`, which is right for the case it was written for and wrong for the one
        # it also caught. An INSERT sets `old_value = None` to record that the row was
        # *absent*, and a DELETE's is the whole departed row rather than a displaced
        # scalar -- neither has a prior field value to compare, so neither can be stale in
        # this sense. But a plain SET against a column that currently holds NULL *also*
        # arrives with `old_value = None`, and skipping that one disabled the guard
        # exactly where a proposer is most likely to be wrong about what it saw.
        if diff.op in {DiffOp.INSERT, DiffOp.DELETE}:
            continue
        if diff.expected_prior is None:
            continue
        actual = diff.old_value
        if _differs(
            actual,
            diff.expected_prior,
            shown_precision(diff.target_table, diff.field),
        ):
            yield Violation(
                "CORE-004",
                f"{diff.target_table}.{diff.field} was {actual!r} at write time but the "
                f"proposal expected {diff.expected_prior!r}; state moved underneath this batch",
                diff.seq,
            )


#: Decimal places each model-writable numeric field is *shown at* in the briefings, where
#: that is not the usual one.
#:
#: **CORE-004 must compare at the precision the model could actually see, and no finer.**
#: Engine state accumulates fractional flows at full SQLite precision -- the organisation
#: layer writes regard to two decimals and the nuclear layer to three -- while a briefing
#: shows a rounded figure. A model that saw ``20.6`` cannot distinguish it from ``20.58``,
#: so rejecting that expected prior as stale discards a paid batch for citing exactly the
#: snapshot it was handed.
#:
#: Audited field by field. Every other model-writable numeric column is rounded to one
#: decimal before it reaches a prompt -- the six nation figures by
#: :func:`atlas.state.visibility.nation_self`, and province development and population by
#: :func:`atlas.state.visibility.own_regions`. **Regard is the one exception**: it is
#: rendered ``{:+.0f}``, so a model shown ``+42`` against an actual ``42.37`` would fail
#: a one-decimal comparison for a difference it was never told about.
#:
#: `test_the_compare_and_swap_precision_matches_what_briefings_show` renders both through
#: the real view builders and fails if this table and the prompts ever disagree.
_SHOWN_PRECISION: dict[tuple[str, str], int] = {
    ("relation", "score"): 0,
}

#: What everything else is shown at. See :data:`_SHOWN_PRECISION`.
_DEFAULT_PRECISION = 1


def shown_precision(table: str, column: str) -> int:
    """Decimal places ``table.column`` is rendered at in a model-facing briefing."""
    return _SHOWN_PRECISION.get((table, column), _DEFAULT_PRECISION)


def _differs(actual: object, expected: object, places: int = _DEFAULT_PRECISION) -> bool:
    """Compare numeric state at the precision the model was shown. Exact otherwise.

    A change large enough to be visible in the briefing still fails the guard, which is
    the whole point of CORE-004; a change too small to have been visible does not.
    """
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        if isinstance(actual, bool) or isinstance(expected, bool):
            # **A boolean prior is only meaningful against a boolean-shaped value.**
            # SQLite hands back int 0/1 for an INTEGER column while JSON `true`/`false`
            # parses to a Python bool, so 1-vs-True has to match and does. What this
            # branch also did was coerce by truthiness, so `_differs(10, True)` was False
            # and any nonzero number agreed with `true` -- a model naming a type the
            # column does not have was read as agreeing about its value. That is a
            # mismatch, not agreement.
            if actual not in (0, 1) or expected not in (0, 1):
                return True
            return bool(actual) != bool(expected)
        return f"{float(actual):.{places}f}" != f"{float(expected):.{places}f}"
    return str(actual) != str(expected)


@register("CORE-005", "A nation's capital must be a region that exists and that it owns.")
def core_capital_integrity(ctx: RuleContext):
    """Stand-in for the foreign key that cannot exist.

    ``nation.capital_region_id`` and ``region.owner_nation_id`` are mutually referential,
    so one of the two has to go unenforced by SQLite. This rule is that enforcement.
    """
    touched = {d.target_id for d in ctx.batch.diffs if d.target_table in {"nation", "region"}}
    if not touched:
        return

    rows = ctx.conn.execute(
        """
        SELECT n.id AS nation_id, n.name, n.capital_region_id, r.id AS region_id, r.owner_nation_id
          FROM nation n
          LEFT JOIN region r ON r.id = n.capital_region_id
         WHERE n.world_id = ? AND n.capital_region_id IS NOT NULL AND n.is_alive = 1
        """,
        (ctx.world_id,),
    ).fetchall()

    for row in rows:
        if row["region_id"] is None:
            yield Violation(
                "CORE-005",
                f"nation {row['name']} has capital {row['capital_region_id']} which does not exist",
            )
        elif row["owner_nation_id"] != row["nation_id"]:
            # Losing your capital is legal and dramatic; it just has to be recorded as a
            # capital move, not left dangling.
            yield Violation(
                "CORE-005",
                f"nation {row['name']} still lists {row['capital_region_id']} as its capital "
                "but no longer owns it; the capital must be relocated in the same batch",
            )


#: Columns that are a JSON *list* and not merely a string that happens to be stored as
#: text, with the table they live on. Both are read by iterating the parsed value, which is
#: what makes a scalar in them a bug rather than a preference.
_JSON_LIST_COLUMNS: tuple[tuple[str, str], ...] = (
    ("nation", "standing_goals_json"),
    ("leader", "traits_json"),
)


@register("CORE-006", "A JSON column must still parse as the list its readers expect.")
def core_json_columns_parse(ctx: RuleContext):
    """The write that bricked a world, refused where it is made.

    `standing_goals_json` is writable by the world simulator and by every tracked agent,
    and nothing checked its shape: on turn 11 of a live Fulda Gap match a bloc set it to
    the bare string ``hold_line``, the diff applied, and the *next* turn raised
    `JSONDecodeError` out of `TurnEngine.jump` while building the referee's view. Not a
    wrong number -- an unopenable world, and every later turn would have died identically.

    **Refused rather than coerced.** Guessing that ``hold_line`` meant ``["hold_line"]``
    would land a value the proposer did not write, and the whole point of a refusal is that
    the model is told and can say it properly next turn. `atlas.state.tables.json_list` is
    the other half of this and does exactly that guess -- but only on *read*, for worlds
    already carrying the damage, where the alternative is not opening at all.

    Post-apply like every rule here, so it reads the value that actually landed rather than
    re-deriving it from the diff -- which also catches an INSERT that carried a bad column
    without a SET ever naming it.
    """
    for table, column in _JSON_LIST_COLUMNS:
        touched = {
            d.target_id for d in ctx.batch.diffs
            if d.target_table == table and d.op is not DiffOp.DELETE
        }
        if not touched:
            continue
        placeholders = ",".join("?" * len(touched))
        try:
            rows = ctx.conn.execute(
                f"SELECT id, {column} AS value FROM {table}"  # noqa: S608 - names are literals above
                f" WHERE world_id = ? AND id IN ({placeholders})",
                (ctx.world_id, *sorted(touched)),
            ).fetchall()
        except sqlite3.OperationalError:
            # A world seeded before the table existed. Nothing to check is not a violation.
            continue
        for row in rows:
            stored = row["value"]
            if stored is None or stored == "":
                continue
            try:
                parsed = json.loads(stored)
            except (TypeError, ValueError):
                yield Violation(
                    "CORE-006",
                    f"{table}.{column} for {row['id']} is not JSON: {str(stored)[:60]!r}. "
                    "It is a list of short strings -- write [\"a goal\", \"another\"].",
                )
                continue
            if not isinstance(parsed, list):
                yield Violation(
                    "CORE-006",
                    f"{table}.{column} for {row['id']} is a "
                    f"{type(parsed).__name__} and must be a list: {str(stored)[:60]!r}",
                )
