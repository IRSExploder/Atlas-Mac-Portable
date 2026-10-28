"""Conquest: making a beaten country's occupied land your own.

The peace table refuses to take a country's last province -- "a state cannot be dissolved at
the peace table, only conquered" (`resolution._apply_cessions`, `peace.settle`) -- and
capitulation leaves the loser owning what it no longer holds ("occupied is not annexed").
Both are right, and between them they left no way at all for a country to be conquered: a
power that took every province of a one-province state won the war, the war ended, and the
land stayed the loser's for good. Most states on the map are one province.

**This is the conquest those rules point at, and it has a narrow door.** A power may annex
another's land only when

* the two are no longer at war -- a war still running is ended at the peace table, with
  cession terms the loser can accept or refuse;
* the other side **holds no ground at all**, which is exactly the test capitulation uses to
  say it could not go on; and
* the land is **occupied by the annexing power** right now.

So the battlefield decides what can be taken, as it does at the peace table, and the act of
taking it is the player's (or a power's) own decision, made in an order, with the world's
usual answers to an annexation: the land keeps its home nation, so it can rise (see
:mod:`atlas.engine.unrest`), and the Security Council reads the ``territory_transfer`` event
this writes, with its actor, as an annexation.

A country left owning nothing is buried in the same batch -- its remaining forces disbanded,
``is_alive`` cleared -- exactly as :mod:`atlas.engine.capitulation` buries one partitioned
out of existence.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..state.apply import apply_batch, record_event, unrecord_events
from ..state.diff import Diff, DiffBatch
from ..state.models import DiffOp, EventKind, EventOrigin, Role, Visibility

__all__ = ["Annexation", "annex", "annexable", "settle_buried_land"]


@dataclass(slots=True)
class Annexation:
    done: bool = False
    regions: list[str] = field(default_factory=list)
    eliminated: bool = False
    #: Why it could not be done, in a sentence a player can act on.
    refused: str = ""
    headline: str = ""


def _name(conn: sqlite3.Connection, world_id: str, nation_id: str) -> str:
    row = conn.execute("SELECT name FROM nation WHERE world_id = ? AND id = ?",
                       (world_id, nation_id)).fetchone()
    return str(row["name"]) if row else nation_id


def annexable(conn: sqlite3.Connection, world_id: str, nation_id: str,
              target: str) -> tuple[list[sqlite3.Row], str]:
    """The regions ``nation_id`` may annex from ``target`` now, or why it may not."""
    them = _name(conn, world_id, target)
    row = conn.execute("SELECT is_alive FROM nation WHERE world_id = ? AND id = ?",
                       (world_id, target)).fetchone()
    if row is None or not row["is_alive"]:
        return [], f"{them} no longer exists"
    if target == nation_id:
        return [], "a country cannot annex itself"
    war = conn.execute(
        "SELECT at_war FROM relation WHERE world_id = ? AND nation_a = ? AND nation_b = ?",
        (world_id, nation_id, target)).fetchone()
    occupied = conn.execute(
        "SELECT id, name, capital FROM ("
        "  SELECT r.id, r.name, (n.capital_region_id = r.id) AS capital FROM region r"
        "  JOIN nation n ON n.id = r.owner_nation_id AND n.world_id = r.world_id"
        "  WHERE r.world_id = ? AND r.owner_nation_id = ? AND r.controller_nation_id = ?"
        "    AND r.kind <> 'ocean') ORDER BY id",
        (world_id, target, nation_id)).fetchall()
    held = conn.execute(
        "SELECT COUNT(*) FROM region WHERE world_id = ? AND kind <> 'ocean'"
        " AND COALESCE(controller_nation_id, owner_nation_id) = ?",
        (world_id, target)).fetchone()[0]
    if war is not None and war["at_war"]:
        if held:
            return [], (f"you are still at war with {them}, and it still holds ground; take "
                        f"all of it, or offer peace with terms that cede what you occupy")
        return [], f"the war with {them} has not ended yet; annex it next turn"
    if not occupied:
        return [], f"you do not occupy any of {them}'s land; it has to be taken in a war first"
    if held:
        return [], (f"{them} still holds ground of its own and has not been beaten; a peace "
                    f"treaty is the way to take land from a country still standing")
    return list(occupied), ""


def annex(conn: sqlite3.Connection, world_id: str, *, nation_id: str, target: str,
          turn: int, date: str) -> Annexation:
    """Make ``target``'s land that ``nation_id`` occupies its own, if the rules above allow."""
    outcome = Annexation()
    regions, why = annexable(conn, world_id, nation_id, target)
    if not regions:
        outcome.refused = why
        return outcome

    me, them = _name(conn, world_id, nation_id), _name(conn, world_id, target)
    taken = {str(r["id"]) for r in regions}
    kept = [str(r[0]) for r in conn.execute(
        "SELECT id FROM region WHERE world_id = ? AND owner_nation_id = ? AND kind <> 'ocean'"
        " ORDER BY development DESC, id", (world_id, target)) if str(r[0]) not in taken]
    names = ", ".join(str(r["name"]) for r in regions[:6]) + (
        f" and {len(regions) - 6} more" if len(regions) > 6 else "")

    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.TERRITORY_TRANSFER, origin=EventOrigin.ENGINE,
        actor_nation_id=nation_id,
        text=(f"{me} annexed {them}." if not kept else f"{me} annexed {names} from {them}."),
        visibility=Visibility.PUBLIC,
        participants=(("actor", nation_id), ("target", target)),
    )
    diffs: list[Diff] = []
    for region in regions:
        diffs.append(Diff("region", str(region["id"]), "owner_nation_id", DiffOp.SET,
                          nation_id, reason_event_id=event_id,
                          visibility_scope=Visibility.PUBLIC))
        diffs.append(Diff("region", str(region["id"]), "controller_nation_id", DiffOp.SET,
                          nation_id, reason_event_id=event_id,
                          visibility_scope=Visibility.PUBLIC))
    capital_taken = any(region["capital"] for region in regions)
    if kept and capital_taken:
        # CORE-005: a nation's capital must be a region it owns.
        diffs.append(Diff("nation", target, "capital_region_id", DiffOp.SET, kept[0],
                          reason_event_id=event_id, visibility_scope=Visibility.PUBLIC))
    if not kept:
        # Conquered outright. Its forces go with it (UNIT-003 refuses units of a dead
        # nation), as in a partition.
        diffs += [
            Diff("unit", str(row["id"]), "__delete__", DiffOp.DELETE, None,
                 reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
            for row in conn.execute(
                "SELECT id FROM unit WHERE world_id = ? AND nation_id = ? ORDER BY id",
                (world_id, target))]
        diffs.append(Diff("nation", target, "is_alive", DiffOp.SET, 0,
                          reason_event_id=event_id, visibility_scope=Visibility.PUBLIC))

    # Whole or not at all. Severed, a refused transfer left the burial standing: the
    # summary said the country was annexed and gone, and the map kept it as the owner.
    applied = apply_batch(conn, DiffBatch(
        world_id=world_id, turn=turn, proposer_role=Role.ENGINE, proposer_model=None,
        diffs=tuple(diffs)), whole=True)
    if not applied.applied:
        unrecord_events(conn, [event_id])
        outcome.refused = f"the annexation of {them} was refused: {applied.reason}"
        return outcome

    outcome.done = True
    outcome.regions = sorted(taken)
    outcome.eliminated = not kept
    outcome.headline = (f"{me} annexed {them}; it has ceased to exist as a state."
                        if not kept else f"{me} annexed {names} from {them}.")
    return outcome


def settle_buried_land(conn: sqlite3.Connection, world_id: str, *, turn: int,
                       date: str) -> tuple[list[str], list[str]]:
    """Give land still owned by a country that no longer exists to whoever occupies it.

    **A conquest left half done.** Before annexation went through whole, a refused transfer
    was cut out of the batch and the rest stood: the country was buried, its land kept it as
    owner, and no order could finish the job because there was no longer a country to name.
    Saves made then carry that state, and this is what mends them -- and anything else that
    ever leaves a dead state owning ground under somebody's flag.

    Returns ``(headlines, problems)``.
    """
    rows = conn.execute(
        "SELECT r.id, r.owner_nation_id AS dead, r.controller_nation_id AS holder"
        "  FROM region r"
        "  JOIN nation gone ON gone.id = r.owner_nation_id AND gone.world_id = r.world_id"
        "  JOIN nation n ON n.id = r.controller_nation_id AND n.world_id = r.world_id"
        " WHERE r.world_id = ? AND r.kind <> 'ocean'"
        "   AND gone.is_alive = 0 AND n.is_alive = 1"
        " ORDER BY r.controller_nation_id, r.owner_nation_id, r.id",
        (world_id,)).fetchall()
    headlines: list[str] = []
    problems: list[str] = []
    pairs: dict[tuple[str, str], list[str]] = {}
    for row in rows:
        pairs.setdefault((str(row["holder"]), str(row["dead"])), []).append(str(row["id"]))
    for (holder, dead), regions in pairs.items():
        me, them = _name(conn, world_id, holder), _name(conn, world_id, dead)
        text = f"{me} annexed what was left of {them}."
        event_id = record_event(
            conn, world_id=world_id, turn=turn, date=date,
            kind=EventKind.TERRITORY_TRANSFER, origin=EventOrigin.ENGINE,
            actor_nation_id=holder, text=text, visibility=Visibility.PUBLIC,
            participants=(("actor", holder), ("target", dead)),
        )
        applied = apply_batch(conn, DiffBatch(
            world_id=world_id, turn=turn, proposer_role=Role.ENGINE, proposer_model=None,
            diffs=tuple(Diff("region", region_id, "owner_nation_id", DiffOp.SET, holder,
                             reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
                        for region_id in regions)), whole=True)
        if applied.applied:
            headlines.append(text)
        else:
            unrecord_events(conn, [event_id])
            problems.append(f"{them}'s last ground could not pass to {me}: {applied.reason}")
    return headlines, problems
