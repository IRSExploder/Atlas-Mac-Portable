"""What is worth stopping for, when time is being run forward several turns at once.

**Reading, never deciding.** Every condition here is a question asked of a turn *after* it
has resolved, against the event log and the diff log it left behind. Nothing in this module
writes, and nothing it returns changes what the turn did -- which is the property that makes
an interruptible advance safe: halting is an observation, so stopping after turn three
leaves a world byte-identical to having asked for three turns in the first place.

That is asserted rather than asserted-to: see the determinism test. It is the whole
correctness argument, because a stop condition that *changed* the world would make "run
until something happens" a different game from "run three turns".

**State over prose, wherever there is a choice.** A nuclear launch is read as warheads
leaving an arsenal rather than as the word "nuclear" in a headline; a colony reaching
independence is read as a nation row appearing. Model-written prose is the one part of a
turn nobody controls, and a stop condition keyed on it would fire on a metaphor and miss
the real thing. Where the engine's own event *kinds* are the fact -- a war declared, a
treaty signed -- those are used directly, because the kind is written by the engine.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

__all__ = [
    "CONDITIONS",
    "DEFAULT_WATCH",
    "Trigger",
    "triggers_in",
]

#: Every condition, with the sentence a player reads when it halts a run. The ids are
#: stable and are what any interface or setting should key on.
CONDITIONS: dict[str, str] = {
    "war": "a war began",
    "ally_attacked": "an ally was attacked and may call on its pact",
    "nuclear": "a nuclear weapon was used",
    "capital": "a capital changed hands",
    "treaty": "an agreement was signed",
    "independence": "a new sovereign state appeared",
    "project": "a major work was completed",
    "order_refused": "an order was refused",
}

#: What an advance watches unless told otherwise.
#:
#: **Everything except the two quiet ones.** A completed workshop and a refused order are
#: real and worth *reporting*; stopping a year's advance for either would mean the advance
#: never gets anywhere, and a control that always halts on the first turn is a control
#: nobody uses. They stay in :data:`CONDITIONS` so a caller that wants them can ask.
DEFAULT_WATCH: frozenset[str] = frozenset(
    {"war", "ally_attacked", "nuclear", "capital", "treaty", "independence"}
)


@dataclass(frozen=True, slots=True)
class Trigger:
    """One reason a turn was worth stopping on."""

    condition: str
    #: The sentence from :data:`CONDITIONS`, so no caller writes its own.
    label: str
    #: The events that tripped it, for an interface to show and to link through to.
    event_ids: tuple[int, ...] = ()
    #: What specifically happened, in the world's own words where there are any.
    detail: str = ""


def _events_of_kind(
    conn: sqlite3.Connection, world_id: str, turn: int, kinds: tuple[str, ...]
) -> list[tuple[int, str]]:
    marks = ",".join("?" * len(kinds))
    return [
        (int(row["id"]), str(row["text"] or ""))
        for row in conn.execute(
            f"""
            SELECT id, text FROM event
             WHERE world_id = ? AND turn = ? AND kind IN ({marks})
             ORDER BY id
            """,  # noqa: S608 - placeholders only
            (world_id, turn, *kinds),
        )
    ]


def _applied_diffs(
    conn: sqlite3.Connection, world_id: str, turn: int, table: str, field: str
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT d.target_id AS target, d.old_json AS before, d.new_json AS after,
               d.reason_event_id AS event_id, d.op AS op
          FROM diff d JOIN diff_batch b ON b.id = d.batch_id
         WHERE b.world_id = ? AND b.turn = ? AND d.status = 'applied'
           AND d.target_table = ? AND d.field = ?
         ORDER BY d.id
        """,
        (world_id, turn, table, field),
    ).fetchall()


def _first(detail: list[tuple[int, str]], limit: int = 2) -> str:
    return " · ".join(text[:90] for _id, text in detail[:limit])


def triggers_in(
    conn: sqlite3.Connection,
    world_id: str,
    turn: int,
    *,
    watch: frozenset[str] | set[str] | None = None,
    viewer: str | None = None,
) -> list[Trigger]:
    """Everything worth stopping for that happened on this turn. Read-only.

    Ordered by how much a player would want to be told, not by how the questions are asked:
    a nuclear use outranks a treaty, and a treaty outranks a finished workshop.

    **Only what ``viewer`` could have seen.** A halt is itself a message: stopping a
    player's advance on a secret treaty between two other powers -- and printing its text as
    the reason -- told the player about a partition of their own country that nobody had
    told them about. Every event-backed condition is filtered through
    :func:`~atlas.engine.causality.visible_event_ids`, the briefing's predicate, before it
    can count; a state-backed one (a capital, an arsenal, a new state) counts only through
    the rows whose cited event the viewer may see. ``None`` is the referee.
    """
    watching = DEFAULT_WATCH if watch is None else frozenset(watch)
    found: list[Trigger] = []

    def seen(events: list[tuple[int, str]]) -> list[tuple[int, str]]:
        if viewer is None:
            return events
        from .causality import visible_event_ids

        allowed = visible_event_ids(
            conn, world_id, [event_id for event_id, _text in events if event_id], viewer)
        return [(event_id, text) for event_id, text in events if event_id in allowed]

    def seen_rows(rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
        if viewer is None:
            return rows
        from .causality import visible_event_ids

        allowed = visible_event_ids(
            conn, world_id, [int(r["event_id"]) for r in rows if r["event_id"]], viewer)
        return [r for r in rows if r["event_id"] and int(r["event_id"]) in allowed]

    def add(condition: str, events: list[tuple[int, str]], detail: str = "") -> None:
        if condition in watching and events:
            found.append(Trigger(
                condition=condition, label=CONDITIONS[condition],
                event_ids=tuple(event_id for event_id, _text in events),
                detail=detail or _first(events)))

    # -- nuclear: warheads leaving an arsenal, which is the act rather than the word.
    if "nuclear" in watching:
        spent = seen_rows([
            row for row in _applied_diffs(conn, world_id, turn, "arsenal", "warheads")
            if _fell(row["before"], row["after"])
        ])
        if spent:
            texts = _texts_for(conn, [int(r["event_id"]) for r in spent if r["event_id"]])
            add("nuclear", texts or [(0, "an arsenal fired")],
                _first(texts) or "warheads left an arsenal")

    # -- a war, and separately an ally being attacked. The second is the Article 5 signal:
    #    `_invoke_defensive_pacts` writes an engine-authored casus belli per ally.
    add("war", seen(_events_of_kind(conn, world_id, turn, ("war_declared",))))
    add("ally_attacked", seen([
        (event_id, text) for event_id, text in
        _events_of_kind(conn, world_id, turn, ("casus_belli",))
        if "defensive pact" in text
    ]))

    # -- a capital changing hands. Read against `nation.capital_region_id`, so it is the
    #    seat of a government rather than any large province.
    if "capital" in watching:
        capitals = {
            str(row["capital_region_id"]): str(row["name"])
            for row in conn.execute(
                "SELECT capital_region_id, name FROM nation"
                " WHERE world_id = ? AND capital_region_id IS NOT NULL", (world_id,))
        }
        taken = seen_rows([
            row for field in ("owner_nation_id", "controller_nation_id")
            for row in _applied_diffs(conn, world_id, turn, "region", field)
            if str(row["target"]) in capitals and row["before"] != row["after"]
        ])
        if taken:
            names = sorted({capitals[str(row["target"])] for row in taken})
            add("capital",
                [(int(row["event_id"]), "") for row in taken if row["event_id"]] or [(0, "")],
                f"the capital of {', '.join(names)}")

    add("treaty", seen(_events_of_kind(
        conn, world_id, turn, ("treaty_signed", "alliance_formed", "peace_signed"))))

    # -- independence: a nation row appearing is a new sovereign state, which is what
    #    `decolonise` writes and nothing else does.
    if "independence" in watching:
        born = seen_rows([
            row for row in _applied_diffs(conn, world_id, turn, "nation", "is_alive")
            if str(row["op"]) == "insert"
        ])
        if born:
            texts = _texts_for(conn, [int(r["event_id"]) for r in born if r["event_id"]])
            add("independence", texts or [(0, "")], _first(texts) or "a new state was recognised")

    add("project", seen(_events_of_kind(conn, world_id, turn, ("construction",))))

    # -- a refused order. The batch layer records this, so it is the engine's own verdict.
    #    A viewer is not told about another power's refusals: a tracked power's refused
    #    batch is what it *tried*, which is exactly what it did not get to make known.
    #    Batches no nation proposed (the world simulator, the engine acting on the player's
    #    orders) still count, as they always did.
    if "order_refused" in watching:
        refused = conn.execute(
            "SELECT COUNT(*) FROM diff_batch WHERE world_id = ? AND turn = ?"
            "   AND status = 'rejected'"
            + ("" if viewer is None
               else " AND (proposer_nation_id IS NULL OR proposer_nation_id = ?)"),
            (world_id, turn) if viewer is None else (world_id, turn, viewer),
        ).fetchone()[0]
        if refused:
            found.append(Trigger(
                condition="order_refused", label=CONDITIONS["order_refused"],
                detail=f"{refused} batch(es) were refused"))

    order = list(CONDITIONS)
    found.sort(key=lambda trigger: order.index(trigger.condition))
    return found


def _fell(before, after) -> bool:
    """Whether a stored number went down. Warheads only leave an arsenal by being used."""
    import json

    try:
        return float(json.loads(after) or 0) < float(json.loads(before) or 0)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False


def _texts_for(conn: sqlite3.Connection, ids: list[int]) -> list[tuple[int, str]]:
    ids = [i for i in ids if i]
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    return [
        (int(row["id"]), str(row["text"] or ""))
        for row in conn.execute(
            f"SELECT id, text FROM event WHERE id IN ({marks}) ORDER BY id",  # noqa: S608
            ids)
    ]
