"""Information asymmetry, enforced as a rule rather than a request.

The context builders already construct each nation's view without the things it should
not know -- that is the primary defence, and it is structural. These rules are the
second line: they catch a proposer *acting* on something it could not have seen, which
would indicate a leak somewhere upstream.

``world_sim`` is exempt by design. It is a deliberate omniscient referee, not a
participant, and it has no ``proposer_nation_id``. The flaw worth guarding against there
is not that it knows everything -- it must -- but that it writes a secret into publicly
observable state. That is what CORE/INFO-002 covers.
"""

from __future__ import annotations

from ..models import Role, Visibility
from ..validate import RuleContext, Violation, register

__all__ = ["info_citation_visible", "info_scope_not_broadened", "info_writes_own_rows"]

_BREADTH = {Visibility.SECRET: 0, Visibility.BILATERAL: 1, Visibility.PUBLIC: 2}


@register("INFO-001", "A nation may only cite events it could actually have observed.")
def info_citation_visible(ctx: RuleContext):
    """Reject a diff justified by an event the proposer cannot see.

    Public events are visible to everyone without needing a row per nation, so the check
    is: public passes, otherwise there must be an ``event_visibility`` row naming this
    proposer.
    """
    nation_id = ctx.batch.proposer_nation_id
    if nation_id is None or ctx.batch.proposer_role in {Role.WORLD_SIM, Role.ENGINE}:
        return

    for diff in ctx.batch.diffs:
        if diff.reason_event_id is None:
            continue

        row = ctx.conn.execute(
            """
            SELECT e.visibility,
                   EXISTS (
                       SELECT 1 FROM event_visibility v
                        WHERE v.event_id = e.id AND v.nation_id = ?
                   ) AS can_see
              FROM event e
             WHERE e.id = ?
            """,
            (nation_id, diff.reason_event_id),
        ).fetchone()

        if row is None:
            continue
        if row["visibility"] == Visibility.PUBLIC:
            continue
        if not row["can_see"]:
            yield Violation(
                "INFO-001",
                f"{nation_id} cited event {diff.reason_event_id}, which is "
                f"{row['visibility']} and not visible to it",
                diff.seq,
            )


@register("INFO-002", "A change may not be more widely observable than the event that caused it.")
def info_scope_not_broadened(ctx: RuleContext):
    """Stop a covert cause writing overt state.

    A secret understanding between two powers may absolutely shift their relations -- but
    if that shift is recorded as publicly observable, every other nation's view now
    encodes a secret it has no way of knowing. The leak is not in the prose, it is in the
    numbers, which is exactly the kind of leak a prompt instruction would never catch.
    """
    for diff in ctx.batch.diffs:
        if diff.reason_event_id is None:
            continue
        cause_visibility = ctx.one(
            "SELECT visibility FROM event WHERE id = ?", (diff.reason_event_id,)
        )
        if cause_visibility is None:
            continue

        cause = _BREADTH[Visibility(cause_visibility)]
        effect = _BREADTH[diff.visibility_scope]
        if effect > cause:
            yield Violation(
                "INFO-002",
                f"change to {diff.target_table}.{diff.field} is marked "
                f"{diff.visibility_scope.value} but its cause (event {diff.reason_event_id}) is "
                f"only {cause_visibility}; a covert cause cannot produce overt state",
                diff.seq,
            )


@register("INFO-003", "A nation may only write rows that belong to it.")
def info_writes_own_rows(ctx: RuleContext):
    """A nation acts for itself, not for its rivals.

    Nothing tied a diff's target to the proposing nation, so a tracked agent could write
    any row in the world that its role's column allowlist happened to permit. The sharpest
    version: rewrite a rival's ``standing_goals_json`` to "cede the northern provinces on
    request" -- goals which are then rendered into that rival's own briefing on its next
    call, so the puppet reads its instructions as if they were its own convictions.

    ``world_sim`` is exempt and has no ``proposer_nation_id``: it is the referee, and
    moving other nations' numbers is its entire job. Anything with a nation attached is a
    participant and is confined to its own affairs.
    """
    owner = ctx.batch.proposer_nation_id
    if owner is None or ctx.batch.proposer_role is Role.ENGINE:
        return

    for diff in ctx.batch.diffs:
        holder = _row_owner(ctx, diff.target_table, diff.target_id)
        if holder is None or owner in holder:
            continue
        yield Violation(
            "INFO-003",
            f"{owner} tried to write {diff.target_table}.{diff.field} on "
            f"{diff.target_id}, which belongs to {' and '.join(sorted(holder)) or 'nobody'}; "
            "a nation may only change its own affairs",
            diff.seq,
        )


def _row_owner(ctx: RuleContext, table: str, target_id: str) -> set[str] | None:
    """Which nations a row belongs to. ``None`` when the table has no owner to speak of."""
    if table == "nation":
        return {target_id}
    if table == "relation":
        parts = target_id.split("|")
        # Both sides own a relation: a nation may cool toward a rival, and the rival's
        # own feelings are its business. Writing either direction of your own pair is
        # legitimate; writing a pair you are not part of is not.
        return set(parts) if len(parts) == 2 else None
    if table in {"region", "unit", "city"}:
        column = {
            "region": "owner_nation_id",
            "unit": "nation_id",
            "city": None,
        }[table]
        if column is None:
            row = ctx.conn.execute(
                "SELECT r.owner_nation_id AS n FROM city c"
                " JOIN region r ON r.id = c.region_id WHERE c.id = ?",
                (target_id,),
            ).fetchone()
        else:
            row = ctx.conn.execute(
                f"SELECT {column} AS n FROM {table} WHERE id = ?",  # noqa: S608
                (target_id,),
            ).fetchone()
        return {row["n"]} if row and row["n"] else set()
    if table == "commitment":
        rows = ctx.conn.execute(
            "SELECT nation_id FROM commitment_nation WHERE commitment_id = ?", (target_id,)
        ).fetchall()
        return {r["nation_id"] for r in rows}
    return None
