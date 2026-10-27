"""Relations, treaties, war and peace.

This is where the fairness question bites hardest. If one model controls both Germany
and Russia, what stops it inventing an alliance out of nowhere? Numeric hidden state
does: an alliance requires the relation score to clear a threshold, a war requires a
casus belli or costs stability, and both requirements are checked here against the
database rather than trusted from the narrative.

The model proposes. The engine decides whether the proposal was legal.
"""

from __future__ import annotations

from ..models import EventKind, EventOrigin, Role, TreatyStatus
from ..validate import RuleContext, Violation, register

__all__ = [
    "dip_activation_needs_every_party",
    "dip_alliance_threshold",
    "dip_relation_delta_capped",
    "focus_cap_respected",
    "war_declaration_justified",
    "war_peace_requires_commitment",
]

#: Relation score an alliance requires. Below this, the two powers simply do not trust
#: each other enough, whatever the prose says.
_ALLIANCE_THRESHOLD = 55.0
_DEFENSIVE_THRESHOLD = 35.0

#: Largest single-turn relation swing without a dramatic event to justify it.
_RELATION_DELTA_CAP = 25.0

#: Events that justify a large relation swing in either direction.
_DRAMATIC = (
    EventKind.WAR_DECLARED.value,
    EventKind.PEACE_SIGNED.value,
    EventKind.ALLIANCE_FORMED.value,
    EventKind.ALLIANCE_BROKEN.value,
    EventKind.TREATY_BROKEN.value,
    EventKind.COMMITMENT_BROKEN.value,
    EventKind.ULTIMATUM.value,
    EventKind.SABOTAGE.value,
    EventKind.ESPIONAGE.value,
    EventKind.TERRITORY_TRANSFER.value,
)


@register("DIP-001", "An alliance requires sufficient mutual regard.")
def dip_alliance_threshold(ctx: RuleContext):
    """Alliances are earned, not narrated.

    Checked in *both* directions: relations are stored directed, and a pact one side does
    not actually want is a vassalage or a fiction, not an alliance.
    """
    upgrades = [
        d
        for d in ctx.batch.diffs
        if d.target_table == "relation" and d.field == "treaty_status"
    ]
    if not upgrades:
        return

    thresholds = {
        TreatyStatus.ALLIANCE.value: _ALLIANCE_THRESHOLD,
        TreatyStatus.DEFENSIVE.value: _DEFENSIVE_THRESHOLD,
    }

    for diff in upgrades:
        required = thresholds.get(str(diff.new_value))
        if required is None:
            continue

        pair = _split_relation_id(diff.target_id)
        if pair is None:
            continue
        a, b = pair

        rows = ctx.query(
            """
            SELECT nation_a, nation_b, score FROM relation
             WHERE world_id = ?
               AND ((nation_a = ? AND nation_b = ?) OR (nation_a = ? AND nation_b = ?))
            """,
            (ctx.world_id, a, b, b, a),
        )
        for row in rows:
            if row["score"] < required:
                yield Violation(
                    "DIP-001",
                    f"{row['nation_a']} -> {row['nation_b']} regard is {row['score']:.0f}, "
                    f"below the {required:.0f} required for {diff.new_value}",
                    diff.seq,
                )


@register("DIP-002", "Relations may not swing wildly without a dramatic cause.")
def dip_relation_delta_capped(ctx: RuleContext):
    if ctx.batch.proposer_role is Role.ENGINE:
        return

    # Aggregated per relation, not per diff: three sub-cap steps on the same pair in one
    # batch bought an alliance out of nothing, each step individually legal.
    moves: dict[str, list] = {}
    for diff in ctx.batch.diffs:
        if diff.target_table != "relation" or diff.field != "score":
            continue
        before = _as_float(diff.old_value)
        after = _as_float(diff.new_value)
        if before is None or after is None:
            continue
        entry = moves.setdefault(diff.target_id, [before, after, diff.seq, []])
        entry[1] = after
        entry[2] = diff.seq
        entry[3].append(diff.reason_event_id)

    for target_id, (before, after, seq, cited) in moves.items():
        delta = after - before
        if abs(delta) <= _RELATION_DELTA_CAP:
            continue

        # A dramatic cause excuses the swing only if the engine authored it. Otherwise a
        # model writes itself a `treaty_broken`, cites it, and manufactures an alliance
        # from zero regard -- the event kinds it can author are exactly the dramatic ones.
        excused = False
        for event_id in cited:
            row = ctx.conn.execute(
                "SELECT kind, origin FROM event WHERE id = ?", (event_id,)
            ).fetchone()
            if row is not None and row["kind"] in _DRAMATIC and row["origin"] == "ENGINE":
                excused = True
                break
        if excused:
            continue

        yield Violation(
            "DIP-002",
            f"relation {target_id} moved {delta:+.0f} across this batch, above the "
            f"{_RELATION_DELTA_CAP:.0f} cap, with no engine-authored dramatic event to "
            "justify it",
            seq,
        )


@register("WAR-001", "A declaration of war needs a casus belli or costs legitimacy.")
def war_declaration_justified(ctx: RuleContext):
    """War is available to anyone, but not for free.

    A nation with an engine-recognised casus belli may declare freely. Without one it
    may still declare -- history is full of unprovoked aggression -- but its legitimacy
    must fall in the same batch. What is forbidden is declaring war at no cost at all.

    **Somebody standing on your own country is engine-recognised.** It was not, and the
    result was that the single grievance every era accepts -- `warpath.reasons_to_fight`
    calls it exactly that and offers it as the strongest case there is -- was priced as
    unprovoked aggression. A country declaring war to retake its own provinces paid the
    same eight legitimacy as one invading a neighbour that had done nothing, and the
    briefing that offered it the target never said so. Read from `home_nation_id`, which
    is what the ground *is* rather than who currently holds it.
    """
    declarations = [
        d for d in ctx.batch.diffs if d.target_table == "relation" and d.field == "at_war"
    ]
    if not declarations:
        return

    for diff in declarations:
        if not _truthy(diff.new_value) or _truthy(diff.old_value):
            continue

        pair = _split_relation_id(diff.target_id)
        if pair is None:
            continue
        aggressor, target = pair

        # Relations are directed, so a war produces two rows: the declaration and its
        # mirror. Being attacked is not an act of aggression -- without this the victim
        # is charged legitimacy for the war somebody else started, and because the batch
        # is all-or-nothing, the attacker's declaration is refused along with it.
        attacked_by_target = ctx.one(
            """
            SELECT COUNT(*) FROM event
             WHERE world_id = ? AND kind = ? AND origin = 'ENGINE'
               AND actor_nation_id = ? AND turn = ?
            """,
            (ctx.world_id, EventKind.WAR_DECLARED.value, target, ctx.turn),
        )
        if attacked_by_target:
            continue

        # **Against this country, and not merely held by this country.** The join is the
        # whole of the fix: without it the check asked only whether the aggressor had *a*
        # grievance in the last five turns, so a casus belli earned against one neighbour
        # licensed a free war against any other -- and every one of the three writers of
        # these events names an accused, so the grievance always knew who it was about.
        has_cb = ctx.one(
            """
            SELECT COUNT(*) FROM event e
              JOIN event_participant p ON p.event_id = e.id
             WHERE e.world_id = ? AND e.kind = ? AND e.origin = 'ENGINE'
               AND e.actor_nation_id = ? AND e.turn >= ?
               AND p.role = 'target' AND p.nation_id = ?
            """,
            (ctx.world_id, EventKind.CASUS_BELLI.value, aggressor,
             max(0, ctx.turn - 5), target),
        )
        if has_cb:
            continue

        # The grievance nobody had to record, because the map is already saying it. Kept in
        # step with `resolution.declaration_cost`, which is what decides whether a charge is
        # authored -- the two must answer the same question the same way or a declaration
        # is charged nothing and then refused for having paid nothing.
        reclaiming = ctx.one(
            """
            SELECT COUNT(*) FROM region
             WHERE world_id = ? AND home_nation_id = ? AND kind <> 'ocean'
               AND COALESCE(controller_nation_id, owner_nation_id) = ?
            """,
            (ctx.world_id, aggressor, target),
        )
        if reclaiming:
            continue

        paid = any(
            d.target_table == "nation"
            and d.target_id == aggressor
            and d.field == "legitimacy"
            and (_delta(d.old_value, d.new_value) or 0) < 0
            for d in ctx.batch.diffs
        )
        if not paid:
            # **Joint: it names no diff, because no subset of this batch is a smaller
            # war.** Naming the `at_war` write looked right and was not. Relations are
            # directed, so a declaration is two rows plus its event; severing the one row
            # the rule happened to name left the *mirror* row at war, nobody charged, and
            # no `war_declared` event to read it by -- a war that half-started.
            #
            # It went unseen while ECON-003 scanned the whole nation table, because an
            # aggressor too poor to pay was usually also out of range, and that refusal
            # took the batch whole before this one could sever anything. Narrowing
            # ECON-003 to the writes a batch actually made uncovered it.
            yield Violation(
                "WAR-001",
                f"{aggressor} declared war with no casus belli in the last five turns and paid "
                "no legitimacy cost; unprovoked aggression must cost something",
            )


@register("WAR-002", "Peace requires a recorded agreement, not just a change of heart.")
def war_peace_requires_commitment(ctx: RuleContext):
    endings = [
        d for d in ctx.batch.diffs if d.target_table == "relation" and d.field == "at_war"
    ]
    for diff in endings:
        if _truthy(diff.new_value) or not _truthy(diff.old_value):
            continue

        # **Engine-authored, which WAR-001 checks and this did not.** A model could write
        # its own `peace_signed` event and cite it to clear `at_war` -- ending a war by
        # narration, which is the exact thing the gating-kind machinery exists to stop.
        # `PEACE_SIGNED` and `CEASEFIRE` are both in `GATING_EVENT_KINDS`; nothing was
        # reading that here.
        found = ctx.query(
            "SELECT kind, origin FROM event WHERE id = ?", (diff.reason_event_id,))
        kind = str(found[0]["kind"]) if found else ""
        origin = str(found[0]["origin"]) if found else ""
        if kind in {EventKind.PEACE_SIGNED.value, EventKind.CEASEFIRE.value} and (
            origin == EventOrigin.ENGINE.value
        ):
            continue

        yield Violation(
            "WAR-002",
            f"war on {diff.target_id} ended citing a {kind or 'missing'} event; peace must cite "
            "a peace_signed or ceasefire event",
            diff.seq,
        )


@register("FOCUS-001", "At most five nations may be tracked at once.")
def focus_cap_respected(ctx: RuleContext):
    """Second guard on the Focus Mode cap.

    A database trigger enforces this too. Both exist because a multi-front war is exactly
    when Focus Mode matters most and exactly when a ranking heuristic would quietly
    promote eight nations and blow the turn budget.
    """
    count = ctx.one(
        "SELECT COUNT(*) FROM nation WHERE world_id = ? AND is_tracked = 1", (ctx.world_id,)
    )
    if count and count > 5:
        yield Violation("FOCUS-001", f"{count} nations are tracked; the hard cap is 5")


# ---------------------------------------------------------------------------


def _split_relation_id(target_id: str) -> tuple[str, str] | None:
    """Relation rows are addressed as ``"<a>|<b>"`` since the table has a composite key."""
    if "|" not in target_id:
        return None
    a, _, b = target_id.partition("|")
    return (a, b) if a and b else None


def _truthy(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _delta(old: object, new: object) -> float | None:
    """Change between two absolute values.

    The applier resolves an ``adjust`` into its result before rules run, so there is no
    op to branch on here -- both values are absolute. See ``_apply_one``'s invariant.
    """
    if old is None or new is None:
        return None
    try:
        return float(new) - float(old)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


@register("DIP-004", "An agreement takes effect only when every party has accepted it.")
def dip_activation_needs_every_party(ctx: RuleContext):
    """"Pending" has to mean something, and it did not.

    `commitment.status` was writable by three model roles, so **one signatory could flip a
    pending agreement to active on its own** -- and `sync_treaty_status` would then hand both
    sides a standing treaty the others had never agreed to, which the invasion guard, the
    gambit odds, the router and four briefings all read. An agreement one party can impose is
    not an agreement.

    Acceptance is per party (`commitment_nation.accepted_turn`) and activation is the
    consequence of the last one arriving. A party with no recorded acceptance blocks it.

    **A commitment whose parties have *no* acceptance recorded at all is left alone**, which
    is the compatibility rule the rest of this phase used: every agreement in every world
    written before schema 18 has none, and refusing those would freeze the diplomacy of every
    existing save. The rule bites once anybody has started accepting -- that is, on
    agreements made on this build.
    """
    activations = [
        d
        for d in ctx.batch.diffs
        if d.target_table == "commitment" and d.field == "status"
        and str(d.new_value) == "active" and str(d.old_value) == "pending"
    ]
    for diff in activations:
        parties = ctx.query(
            "SELECT nation_id, accepted_turn FROM commitment_nation"
            "  WHERE commitment_id = ? ORDER BY nation_id",
            (diff.target_id,),
        )
        if not parties or all(row["accepted_turn"] is None for row in parties):
            continue
        missing = [
            str(row["nation_id"]) for row in parties if row["accepted_turn"] is None
        ]
        if missing:
            yield Violation(
                "DIP-004",
                f"commitment {diff.target_id} cannot take effect: "
                f"{', '.join(missing)} {'has' if len(missing) == 1 else 'have'} not "
                "accepted it",
                diff.seq,
            )


@register("DIP-005", "Only a discharged one-off obligation may be recorded as fulfilled.")
def dip_fulfilled_means_discharged(ctx: RuleContext):
    """"Fulfilled" means the promise is done, and most promises here cannot be.

    A non-aggression pact is not *completed* by a year of peace; a supply arrangement is not
    *finished* by a month of cargo. Those end by being broken, by expiring, or by being
    terminated. A status saying otherwise deletes a standing obligation while announcing that
    it was honoured -- and everything downstream believes it, because `sync_treaty_status`
    and `honour_terms` both read `status` and neither asks what kind of promise it was.

    **PT34d is the case.** A model marked a standing supply agreement `fulfilled` one month
    in; the engine correctly read it as no longer active and cut the trade route; Germany and
    the United States both sent envoys the next turn asking why the shipments had stopped.

    **This rule governs the engine, which is the only writer left.** `commitment.status` is
    engine-only now, so no model can reach it -- and nothing in the engine sets `fulfilled`
    yet either. So this is the *specification* of a fulfillment derivation that has not been
    written, made enforceable before it is written rather than after it goes wrong. A rule
    that refuses an engine batch is a rule about the engine having decided something it had
    no right to; see `atlas.engine.treaties.fulfillment_blockers` for the reasoning it
    encodes.

    **A mixed agreement stays active while any standing term remains**, however many of its
    one-off terms are complete. That is the case a naive version gets wrong: a treaty that
    cedes a province *and* promises peace is not finished when the province moves.
    """
    from ...engine.treaties import fulfillment_blockers

    for diff in ctx.batch.diffs:
        if (
            diff.target_table != "commitment"
            or diff.field != "status"
            or str(diff.new_value) != "fulfilled"
        ):
            continue
        row = ctx.one(
            "SELECT kind FROM commitment WHERE world_id = ? AND id = ?",
            (ctx.world_id, diff.target_id),
        )
        if row is None:
            continue
        blockers = fulfillment_blockers(
            ctx.conn, ctx.world_id, commitment_id=int(diff.target_id), kind=str(row)
        )
        if blockers:
            yield Violation(
                "DIP-005",
                f"commitment {diff.target_id} cannot be recorded fulfilled: {blockers[0]}",
                diff.seq,
            )
