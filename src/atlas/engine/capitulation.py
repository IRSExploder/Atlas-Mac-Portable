"""A war that has been won stops, and a state that has been erased stops existing.

Every way a war could end went through a *negotiation*: `peace.offer_peace` for a settled
one, `_settle_war` for a ceasefire, both of them started by somebody proposing terms. That
is the right way for a war to end when there is anybody left to talk to, and it is the only
way there was -- so a war nobody proposed terms for simply never ended.

Five thirty-turn play-test games measured what that costs. **No war started and no war
ended in a hundred and fifty turns.** Ukraine reached zero provinces and the war was still
listed open, with Ukraine alive and its military strength unchanged at fifty; Pakistan was
held at zero for twelve consecutive turns against an enemy that had nothing left to take.
There was no terminal state, so nothing that happened on the map could conclude anything.

**Two different endings, and conflating them would be wrong.**

*Capitulation.* A belligerent that holds no ground at all has lost the war. It may still own
that ground -- occupied is not annexed -- so it is still a state, still has a government,
still has an arsenal, and a later settlement can hand its territory back. What it cannot do
is keep fighting, because there is nowhere left to fight from. The war ends; the country
does not.

*Elimination.* A nation that neither holds nor owns any ground has been partitioned out of
existence. `is_alive` goes to zero, which is what every other part of the engine already
reads to mean "not a party to anything any more".

**Held, not owned, is the test for the first one**, and the distinction is load-bearing:
the play-test's Pakistan held zero and owned eight, which is a country under occupation
rather than a country that has ceased to be. Reading ownership there would have declared a
state dead while its territory was still legally its own and a peace treaty could still
return it.

**Engine-authored and written as diffs**, like everything else that has to survive a
rewind: a war ended on turn twelve must be open again at turn eleven, and direct SQL would
leave it closed. Filed under ``peace_signed`` because no new event kind is needed to say a
war stopped, and that kind is already gated to engine origin -- which is exactly what this
is.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..state.apply import apply_batch, record_event, unrecord_events
from ..state.diff import Diff, DiffBatch
from ..state.models import DiffOp, EventKind, EventOrigin, Role, Visibility
from .armistice import discharge_diffs

__all__ = ["CapitulationReport", "assess_capitulation"]


@dataclass(slots=True)
class CapitulationReport:
    #: ``"<loser> could not go on against <winner>"``, one per war ended here.
    capitulated: list[str] = field(default_factory=list)
    #: Nations that no longer hold or own anything and are now marked not alive.
    eliminated: list[str] = field(default_factory=list)
    wars_ended: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def happened(self) -> bool:
        return bool(self.capitulated or self.eliminated)


def _ground(conn: sqlite3.Connection, world_id: str, nation_id: str) -> tuple[int, int]:
    """``(held, owned)`` land regions. Held is what it can fight from; owned is what is
    still legally its own."""
    row = conn.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM region
            WHERE world_id = ? AND kind <> 'ocean'
              AND COALESCE(controller_nation_id, owner_nation_id) = ?) AS held,
          (SELECT COUNT(*) FROM region
            WHERE world_id = ? AND kind <> 'ocean' AND owner_nation_id = ?) AS owned
        """,
        (world_id, nation_id, world_id, nation_id),
    ).fetchone()
    return int(row["held"]), int(row["owned"])


def assess_capitulation(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str
) -> CapitulationReport:
    """End every war one side can no longer fight, and bury anyone left with nothing.

    Run after the fighting and after the partisans, so a province that rose against its
    occupier this turn counts for the side that got it back -- a nation that regained a
    foothold has not capitulated, and checking before the risings would have said it had.
    """
    report = CapitulationReport()

    wars = conn.execute(
        """
        SELECT r.nation_a AS a, r.nation_b AS b, na.name AS a_name, nb.name AS b_name
          FROM relation r
          JOIN nation na ON na.id = r.nation_a AND na.world_id = r.world_id
          JOIN nation nb ON nb.id = r.nation_b AND nb.world_id = r.world_id
         WHERE r.world_id = ? AND r.at_war = 1
           AND na.is_alive = 1 AND nb.is_alive = 1
           AND r.nation_a < r.nation_b
           -- **An international body is a nation row with no territory on purpose.** It
           -- would otherwise read as a state that has lost everything and be buried on
           -- the turn it was seated. `focus.rank_candidates` excludes them for the same
           -- reason and in the same way.
           AND NOT EXISTS (SELECT 1 FROM organisation o WHERE o.nation_id = na.id)
           AND NOT EXISTS (SELECT 1 FROM organisation o WHERE o.nation_id = nb.id)
         ORDER BY r.nation_a, r.nation_b
        """,
        (world_id,),
    ).fetchall()

    names = {}
    for war in wars:
        names[war["a"]] = war["a_name"]
        names[war["b"]] = war["b_name"]

    # Which nations have run out of ground, worked out once rather than per war: a power
    # fighting on three fronts must not be reported as capitulating three times.
    beaten: dict[str, tuple[int, int]] = {}
    for nation_id in sorted(names):
        held, owned = _ground(conn, world_id, nation_id)
        if held == 0:
            beaten[nation_id] = (held, owned)
    if not beaten:
        return report

    for war in wars:
        loser = next((side for side in (war["a"], war["b"]) if side in beaten), None)
        if loser is None:
            continue
        winner = war["b"] if loser == war["a"] else war["a"]
        # **Both sides beaten is a stalemate, not a surrender.** Two governments with no
        # ground between them have nobody to hand anything to; the war stops either way,
        # and picking a winner from an ordering would be inventing one.
        both = war["a"] in beaten and war["b"] in beaten

        event_id = record_event(
            conn, world_id=world_id, turn=turn, date=date,
            kind=EventKind.PEACE_SIGNED, origin=EventOrigin.ENGINE,
            actor_nation_id=winner if not both else loser,
            text=(
                f"{names[war['a']]} and {names[war['b']]} both ran out of ground; the "
                f"fighting between them stopped."
                if both else
                f"{names[loser]} holds no ground and could not go on. The war with "
                f"{names[winner]} is over."
            ),
            visibility=Visibility.PUBLIC,
        )
        diffs = [
            Diff("relation", f"{x}|{y}", "at_war", DiffOp.SET, 0,
                 reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
            for x, y in ((war["a"], war["b"]), (war["b"], war["a"]))
        ]
        # **A ceasefire does not outlive the war it paused.** `settle` discharges one;
        # capitulation has to as well, in this same batch, or the truce stays active with
        # the two at peace and silently suppresses the fighting in any later war of theirs.
        diffs += discharge_diffs(conn, world_id, war["a"], war["b"],
                                 turn=turn, event_id=event_id)
        applied = apply_batch(conn, DiffBatch(
            world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
            proposer_model=None, diffs=tuple(diffs)))
        if not applied.applied:
            report.problems.append(
                f"the war between {names[war['a']]} and {names[war['b']]} could not be "
                f"ended: {applied.reason}")
            continue
        report.wars_ended += 1
        report.capitulated.append(
            f"{names[war['a']]} and {names[war['b']]} both ran out of ground"
            if both else
            f"{names[loser]} could not go on against {names[winner]}")

    # **Partition is a separate question from defeat**, asked after the wars are closed so
    # a nation is never buried while still formally at war with somebody.
    for nation_id, (_held, owned) in sorted(beaten.items()):
        if owned:
            continue
        # **A state is not eliminated for never having had anything.** Elimination means
        # partitioned out of existence, so there has to have been something to partition:
        # some region has to still name this nation as its homeland. Without the check a
        # polity seeded with no ground at all would be buried the first time anybody
        # declared war on it.
        homeland = conn.execute(
            "SELECT 1 FROM region WHERE world_id = ? AND home_nation_id = ? LIMIT 1",
            (world_id, nation_id),
        ).fetchone()
        if homeland is None:
            continue
        # **Nobody is the actor of a partition's last step.** The event used to name the
        # dead nation as its actor, and an engine `territory_transfer` with an actor is
        # exactly what the Council's annexation trigger reads -- so the power that had just
        # been erased was arraigned for annexing something. Whoever took the ground was
        # already recorded taking it; this event only records that nothing is left. The
        # nation it is about is its target.
        event_id = record_event(
            conn, world_id=world_id, turn=turn, date=date,
            kind=EventKind.TERRITORY_TRANSFER, origin=EventOrigin.ENGINE,
            text=f"{names[nation_id]} holds and owns no territory and has ceased to exist.",
            visibility=Visibility.PUBLIC,
            participants=(("target", nation_id),),
        )
        # **A fallen power's forces go with it**, in the same batch. UNIT-003 refuses any
        # batch that leaves a unit belonging to a nation that is not alive, and a state
        # partitioned out of existence very often still has something somewhere -- a
        # squadron at sea, a garrison cut off in somebody else's province. Without this the
        # burial was refused whenever a single unit survived, and was retried, and refused,
        # every turn after.
        disbanded = [
            Diff("unit", str(row["id"]), "__delete__", DiffOp.DELETE, None,
                 reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
            for row in conn.execute(
                "SELECT id FROM unit WHERE world_id = ? AND nation_id = ? ORDER BY id",
                (world_id, nation_id))
        ]
        applied = apply_batch(conn, DiffBatch(
            world_id=world_id, turn=turn, proposer_role=Role.ENGINE, proposer_model=None,
            diffs=(*disbanded,
                   Diff("nation", nation_id, "is_alive", DiffOp.SET, 0,
                        reason_event_id=event_id,
                        visibility_scope=Visibility.PUBLIC))))
        if applied.applied:
            report.eliminated.append(names[nation_id])
        else:
            # The news was written before the batch so the batch could cite it. A burial
            # that did not happen must not leave "has ceased to exist" in every briefing.
            unrecord_events(conn, [event_id])
            report.problems.append(
                f"{names[nation_id]} could not be marked gone: {applied.reason}")

    return report
