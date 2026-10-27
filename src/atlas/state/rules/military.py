"""Units, movement and territorial change.

These run against post-apply state, so they are written as invariants over the world
rather than as inspections of individual diffs. That catches joint effects for free: a
batch that raises three divisions and pays for one is caught because by the time the
treasury check runs, all three exist and the money has moved.

Note that models cannot write ``unit`` or ``region.owner_nation_id`` at all -- those are
engine-only columns (see :mod:`atlas.state.permissions`). These rules therefore mostly
police the *engine's own* resolution output, which is the right place for them: a bug in
battle resolution that conjures units from nothing should fail loudly rather than quietly
inflating the world.
"""

from __future__ import annotations

from ..models import DiffOp, EventKind
from ..validate import RuleContext, Violation, register

__all__ = [
    "unit_conservation",
    "unit_domain_legality",
    "unit_owner_alive",
    "own_transfer_justified",
]


@register("UNIT-001", "Units may not be created without a raise event and the means to pay.")
def unit_conservation(ctx: RuleContext):
    """No units from nothing.

    Every unit that appeared this turn must correspond to a ``unit_raised`` event, and
    the owning nation must not have been driven negative paying for it. A negative
    treasury after raising is the signal that something was created for free.

    **Scoped to the nations this batch is about**, which is what "the owning nation" meant
    all along -- see the comment on `owners` below for the case that proved the difference.
    """
    if not any(d.target_table == "unit" for d in ctx.batch.diffs):
        return

    raised = ctx.one(
        """
        SELECT COUNT(*) FROM event
         WHERE world_id = ? AND turn = ? AND kind = ? AND origin = 'ENGINE'
        """,
        (ctx.world_id, ctx.turn, EventKind.UNIT_RAISED.value),
    )

    new_units = [d for d in ctx.batch.diffs if d.target_table == "unit" and d.op.value == "insert"]
    if new_units and not raised:
        yield Violation(
            "UNIT-001",
            f"{len(new_units)} unit(s) appeared with no engine-authored unit_raised event this turn",
        )

    # **Whose solvency this batch is actually about.** The docstring above has always said
    # *the owning nation*, and the query did not: it scanned every living nation in the
    # world, so a single insolvent country refused every unit-touching batch anywhere --
    # a ship its yard finished and paid for months ago could not be delivered to Brazil
    # because South Vietnam was overdrawn. Found by the laboratory on PT67, turns 6 and 7,
    # three then six unrelated engine batches thrown out under one country's name.
    #
    # Two ways a nation is in scope. It owns a unit this batch touched -- read after apply,
    # like every rule in this file, so an insert and an adjust are looked up the same way --
    # or the batch moved its treasury or manpower, which is how a patron paying for somebody
    # else's formation stays covered.
    owners: set[str] = {
        str(d.target_id) for d in ctx.batch.diffs
        if d.target_table == "nation" and d.field in {"treasury", "manpower"}
    }
    unit_ids = sorted({
        str(d.target_id) for d in ctx.batch.diffs if d.target_table == "unit"
    })
    if unit_ids:
        owners |= {
            str(row["nation_id"])
            for row in ctx.query(
                "SELECT nation_id FROM unit WHERE world_id = ? AND id IN ("
                + ",".join("?" * len(unit_ids)) + ")",
                (ctx.world_id, *unit_ids),
            )
        }
    if not owners:
        return

    overdrawn = ctx.query(
        "SELECT name, treasury, manpower FROM nation"
        " WHERE world_id = ? AND is_alive = 1 AND (treasury < 0 OR manpower < 0)"
        "   AND id IN (" + ",".join("?" * len(owners)) + ")",
        (ctx.world_id, *sorted(owners)),
    )
    for row in overdrawn:
        yield Violation(
            "UNIT-001",
            f"{row['name']} is overdrawn after this batch "
            f"(treasury {row['treasury']:.1f}, manpower {row['manpower']:.1f}); "
            "forces were raised without the means to pay",
        )


@register("UNIT-002", "Units must respect their domain: no navies inland, no armies at sea.")
def unit_domain_legality(ctx: RuleContext):
    """Domain legality, joined against ``unit_template``.

    This is why ``unit_template`` exists as a table -- without a ``domain`` column the
    rule has nothing to evaluate and would be an assertion in a comment.
    """
    if not any(d.target_table in {"unit", "region"} for d in ctx.batch.diffs):
        return

    bad = ctx.query(
        """
        SELECT u.id, u.kind, t.domain, r.name AS region_name, r.kind AS region_kind
          FROM unit u
          JOIN region r ON r.id = u.region_id
          LEFT JOIN unit_template t ON t.world_id = u.world_id AND t.kind = u.kind
         WHERE u.world_id = ?
           AND (
                (t.domain = 'sea'  AND r.kind = 'land')
             OR (t.domain = 'land' AND r.kind = 'ocean')
           )
        """,
        (ctx.world_id,),
    )
    for row in bad:
        yield Violation(
            "UNIT-002",
            f"{row['domain']} unit {row['id']} ({row['kind']}) is in {row['region_name']}, "
            f"which is {row['region_kind']}",
        )


@register("UNIT-003", "Units must belong to a nation that still exists.")
def unit_owner_alive(ctx: RuleContext):
    if not any(d.target_table in {"unit", "nation"} for d in ctx.batch.diffs):
        return

    orphans = ctx.query(
        """
        SELECT u.id, u.kind, n.name, n.is_alive
          FROM unit u
          LEFT JOIN nation n ON n.id = u.nation_id
         WHERE u.world_id = ? AND (n.id IS NULL OR n.is_alive = 0)
        """,
        (ctx.world_id,),
    )
    for row in orphans:
        yield Violation(
            "UNIT-003",
            f"unit {row['id']} ({row['kind']}) belongs to a nation that no longer exists; "
            "units of a fallen power must be disbanded or transferred",
        )


@register("OWN-001", "Territory changes hands only by conquest of an adjacent region or by treaty.")
def own_transfer_justified(ctx: RuleContext):
    """Territory cannot teleport.

    A region may change owner if the new owner holds an adjacent region (conquest has a
    front), or if a ratified cession commitment names it (diplomacy has a paper trail).
    Anything else is a nation acquiring land it never reached and never negotiated for.
    """
    transfers = [
        d
        for d in ctx.batch.diffs
        if d.target_table == "region" and d.field == "owner_nation_id"
    ]
    if not transfers:
        return

    for diff in transfers:
        new_owner = diff.new_value
        if not new_owner:
            continue

        # **A state coming into existence on its own ground is neither conquest nor
        # cession, and it is not a teleport either -- the ground does not move, the
        # sovereignty does.** Without this escape every decolonisation is refused: a
        # newly independent territory holds one region, is adjacent to nothing of its own
        # by construction, and has no cession because nobody ceded anything.
        #
        # Narrow on purpose, and it cannot be used to acquire land. It fires only when the
        # same batch *creates* the new owner and names this very region as its capital --
        # so the only ground it can ever justify is the one province a new state is born
        # on. A nation INSERT is engine-only (see `atlas.state.permissions`), so no model
        # can reach this at all.
        born_here = any(
            d.target_table == "nation"
            and d.op is DiffOp.INSERT
            and d.target_id == new_owner
            and (d.new_value or {}).get("capital_region_id") == diff.target_id
            for d in ctx.batch.diffs
        )
        if born_here:
            continue

        adjacent = ctx.one(
            """
            SELECT COUNT(*) FROM region_adjacency a
              JOIN region r ON r.id = CASE
                    WHEN a.region_a = ? THEN a.region_b ELSE a.region_a END
             WHERE (a.region_a = ? OR a.region_b = ?)
               AND (r.owner_nation_id = ? OR r.controller_nation_id = ?)
            """,
            (diff.target_id, diff.target_id, diff.target_id, new_owner, new_owner),
        )
        if adjacent:
            continue

        ceded = ctx.one(
            """
            SELECT COUNT(*) FROM commitment c
              JOIN commitment_nation cn ON cn.commitment_id = c.id
             WHERE c.world_id = ?
               AND c.kind = 'territory_cession'
               AND c.status IN ('active','fulfilled')
               AND cn.nation_id = ?
               AND c.condition_text LIKE ?
            """,
            (ctx.world_id, new_owner, f"%{diff.target_id}%"),
        )
        if ceded:
            continue

        # **A peace treaty cedes ground too, and this rule knew only one word for it.**
        # The check above reads `kind = 'territory_cession'` and the agreement's *prose*.
        # `peace.settle` writes the same act as a `peace_treaty` carrying a `cede_region`
        # peace_term, in the same batch as this very transfer -- so a settlement that
        # handed over a province the winner did not already border was refused whole, and
        # the comment beside that write calls it "the ratified cession the permission model
        # reserves for exactly this moment". It was not reserved; it was not recognised.
        #
        # Read from the structured term rather than from prose, which is what makes this
        # narrower than the clause above rather than wider: the term names the ground and
        # the power it goes to, so it cannot be satisfied by a region id that merely
        # appears somewhere in a paragraph.
        by_term = ctx.one(
            """
            SELECT COUNT(*) FROM peace_term pt
              JOIN commitment c ON c.id = pt.commitment_id
             WHERE pt.world_id = ?
               AND pt.kind = 'cede_region'
               AND pt.subject_id = ?
               AND pt.beneficiary_id = ?
               AND c.status IN ('pending','active','fulfilled')
            """,
            (ctx.world_id, diff.target_id, new_owner),
        )
        if by_term:
            continue

        # The same shape for the other term table: `treaties` records a cession clause on
        # `commitment_term`, which names no beneficiary, so the party check does that work.
        by_clause = ctx.one(
            """
            SELECT COUNT(*) FROM commitment_term ct
              JOIN commitment c ON c.id = ct.commitment_id
              JOIN commitment_nation cn ON cn.commitment_id = c.id
             WHERE ct.world_id = ?
               AND ct.clause = 'cede_region'
               AND ct.subject_id = ?
               AND cn.nation_id = ?
               AND c.status IN ('pending','active','fulfilled')
            """,
            (ctx.world_id, diff.target_id, new_owner),
        )
        if by_clause:
            continue

        yield Violation(
            "OWN-001",
            f"region {diff.target_id} transferred to {new_owner}, which holds no adjacent "
            "territory and has no ratified cession covering it",
            diff.seq,
        )
