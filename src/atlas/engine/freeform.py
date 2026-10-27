"""Orders the engine has no mechanic for.

Every order a player writes is parsed into one of a closed list of action types, and
anything that fits none of them becomes ``other`` -- for which there was no handler at
all. So an order like "authorise the citizens to fight the stray cats" did not fail, and
did not happen: it *evaporated*. The parser accepted it, an ``order_issued`` event was
written, and the world went on exactly as if nothing had been ordered. That is the worst
of the three possible answers, because the player cannot tell it apart from a bug.

This is the third answer. The order is taken seriously as *an order*, a model narrates
what the country made of it, and the country's feelings about its government may move a
little. Nothing else moves at all.

**The model chooses a direction, never a number.** The response schema has no numeric
field anywhere: a nudge is a field, a direction and a coarse magnitude drawn from a closed
enum, and Python turns those into a value. That is the same discipline as
:class:`~atlas.sim.resolve.EvidenceFlag` -- the model reports circumstances, the engine
decides the size of them -- and it is why an absurd order cannot become an economic
miracle by being described enthusiastically.

**It can reach three columns.** ``nation.stability``, ``nation.legitimacy`` and
``relation.score``, allowlisted for :data:`~atlas.state.models.Role.FREEFORM` in
:data:`~atlas.state.permissions.ROLE_WRITABLE` and checked here against that allowlist
before a diff is built. Territory, units, the treasury, the economy, contests,
construction and everything else are absent by construction rather than by instruction,
so no wording of any order reaches them.

**Freeform proposes; the engine writes.** The batch goes in under ``Role.ENGINE``, which
is the same arrangement contests already have -- the world simulator reports a clash and
the engine writes the result -- and it is why ``freeform`` is not among the roles
``diff_batch`` accepts, alongside ``advisor``, which also never writes. What a model
returns here is evidence, and every number in the outcome is chosen by this module.

**Four bounds, all structural.** A magnitude is a word from a closed enum worth at most
4.0; an order may carry at most three nudges; a turn may carry at most two orders; and no
single field may move more than :data:`PER_FIELD_TURN_CAP` across all of them, tracked in
a ledger the turn engine holds open for the whole turn.

**Refusal is the point.** A channel that always says yes teaches the player that nothing
has consequences, which is a worse failure than the evaporation it replaces. The model may
answer that nothing happened, and it is told to prefer that; and an order that is really an
attempt at a hard mechanic ("annex Belgium by decree") is refused with the real order named.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from ..state.apply import apply_batch, record_event
from ..state.diff import Diff, DiffBatch
from ..state.jsonschema import make_strict
from ..state.models import DiffOp, EventKind, EventOrigin, Role, Visibility
from .bounds import absorbable
from .untrusted import sanitize, wrap_untrusted

__all__ = [
    "FREEFORM_SCHEMA",
    "MAGNITUDES",
    "MAX_NUDGES",
    "PER_FIELD_TURN_CAP",
    "FreeformOutcome",
    "apply_freeform",
    "build_freeform_view",
    "freeform_orders",
    "freeform_overflow",
]

#: What a coarse magnitude is worth. The model picks the word; this picks the number.
#: Small enough that freeform is never the efficient route to anything -- a player who
#: wants stability should build housing, and a player who wants this should want the story.
MAGNITUDES: dict[str, float] = {"slight": 1.5, "moderate": 4.0}

#: How many things one order may move. Three is enough for "the army is baffled, the
#: press is delighted, and Belgium thinks less of you" and not enough for a policy.
MAX_NUDGES = 3

#: Total freeform movement allowed on any one field in a turn, across every order.
#: Without it, two orders of three nudges each walk stability twelve points in a month by
#: staying under the per-nudge cap, which is the same arithmetic that defeated ECON-001
#: before it learned to aggregate. The caller keeps one :class:`Ledger` open per turn.
PER_FIELD_TURN_CAP = 6.0

#: How far freeform has already moved each ``(row, column)`` this turn.
Ledger = dict[tuple[str, str], float]

#: How many freeform orders one turn will carry. The rest are answered with a note rather
#: than silently dropped -- the whole point of this module is that nothing evaporates. That
#: note is :func:`freeform_overflow`, which the turn engine reports; the sentence was here
#: for months before anything wrote one.
MAX_ORDERS_PER_TURN = 2

_FREEFORM_SCHEMA_RAW: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["narrative", "event_text", "nudges", "refused"],
    "properties": {
        "narrative": {
            "type": "string",
            "description": (
                "What your country made of this order, in two or three sentences, in "
                "world and in period. Shown to the player."
            ),
        },
        "event_text": {
            "type": "string",
            "description": (
                "One sentence for the historical record. Written as something that "
                "happened, not as an instruction."
            ),
        },
        "nudges": {
            "type": "array",
            "maxItems": MAX_NUDGES,
            "description": (
                "How this changed the mood, if it did at all. Prefer an empty list: most "
                "orders are colour and change nothing. You choose a direction and a size "
                "in words; the engine decides what those are worth."
            ),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["subject", "field", "direction", "magnitude", "why"],
                "properties": {
                    "subject": {
                        "type": "string",
                        "description": (
                            "Your own nation id for stability or legitimacy; for a "
                            "relation, write it as `you|other`."
                        ),
                    },
                    "field": {
                        "type": "string",
                        "enum": ["stability", "legitimacy", "relation"],
                    },
                    "direction": {"type": "string", "enum": ["up", "down"]},
                    "magnitude": {"type": "string", "enum": sorted(MAGNITUDES)},
                    "why": {"type": "string", "description": "One clause, in world."},
                },
            },
        },
        "refused": {
            "type": ["string", "null"],
            "description": (
                "Set only when the order cannot happen at all -- it is impossible in this "
                "period, or it is really an attempt to take territory, move armies or "
                "change the economy, which have their own orders. Name the real order."
            ),
        },
    },
}

#: Written to the strictest dialect any provider requires; see atlas.state.jsonschema.
FREEFORM_SCHEMA: dict[str, Any] = make_strict(_FREEFORM_SCHEMA_RAW)


@dataclass(slots=True)
class FreeformOutcome:
    narrative: str = ""
    refused: str | None = None
    applied: int = 0
    #: Nudges the model asked for that the caps or the allowlist would not carry.
    trimmed: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _unclassifiable(actions: list[dict]) -> list[dict]:
    """Every order that fell through every mechanic, before the rate is applied.

    ``other`` is included deliberately: it is the bucket the parser has always dropped
    things into, and everything in it is by definition an order nothing else handles.
    """
    return [
        action
        for action in actions
        if str(action.get("type")) in {"freeform", "other"}
    ]


def freeform_orders(actions: list[dict]) -> list[dict]:
    """The orders that fell through every mechanic. Bounded, and the rest are reported.

    What is cut is :func:`freeform_overflow`'s to report. Kept as two functions rather than
    one returning a pair because every caller wants the first list and only the turn engine
    wants the second, and a tuple return would have made ten call sites unpack something
    they do not use.
    """
    return _unclassifiable(actions)[:MAX_ORDERS_PER_TURN]


def freeform_overflow(actions: list[dict]) -> list[dict]:
    """The orders past :data:`MAX_ORDERS_PER_TURN` that this turn will not carry.

    **The rate was applied and nobody was told, for as long as the rate has existed.**
    ``MAX_ORDERS_PER_TURN``'s own comment has always said "the rest are answered with a note
    rather than silently dropped -- the whole point of this module is that nothing
    evaporates", and no note was ever written: :meth:`TurnEngine._run_freeform` took the
    sliced list and never looked at what the slice removed. A player who wrote three orders
    no mechanic covers got two answered and one vanished, with nothing anywhere saying which
    or why -- which is the exact failure this module exists to prevent, sitting inside the
    module that prevents it.

    Reported rather than carried, because the rate is right: a turn that answers an
    unbounded list of absurdities is a turn spent on colour. What was wrong was the silence.
    """
    return _unclassifiable(actions)[MAX_ORDERS_PER_TURN:]


def build_freeform_view(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    nation_id: str,
    order_text: str,
) -> str:
    """The prompt: who you are, what you were told to do, and what you may answer with."""
    from ..state import referee, visibility

    summary = referee.world_summary(conn, world_id)
    self_block = visibility.nation_self(conn, nation_id)
    neighbours = [n for n in visibility.other_nations(conn, nation_id) if n.is_neighbour]

    lines = [
        f"# You are {self_block.get('name', nation_id)} (`{nation_id}`)",
        self_block.get("character", ""),
        "",
        f"The date is {summary.get('date')} (turn {summary.get('turn')}).",
        f"Stability {self_block.get('stability')}, legitimacy {self_block.get('legitimacy')}.",
        "",
        "## The order your government has issued",
        wrap_untrusted(sanitize(order_text), source="the player's own order"),
        "",
        "## Your task",
        "This order matches nothing the engine knows how to carry out. Say what your "
        "country actually made of it.",
        "",
        "Answer in period and in world. Take it seriously as an order, however strange "
        "it is: a government that decrees something absurd has still decreed it, and a "
        "country reacts to that.",
        "",
        "Rules you cannot argue with:",
        "- You are not deciding an outcome. You cannot move a border, an army, a budget "
        "or an economy, and no wording will let you. Those have their own orders.",
        "- Most orders change nothing measurable. An empty `nudges` list is the normal "
        "answer and a good one.",
        "- When something does change, it is how people feel: your stability, your "
        "legitimacy, or what a neighbour thinks of you. Nothing else is available.",
        "- Refuse outright when the order is impossible in this period, or when it is "
        "really an attempt to take ground, raise forces or move money. Say which real "
        "order the player wanted.",
    ]
    if neighbours:
        lines += [
            "",
            "Neighbours whose regard could move, written `"
            + f"{nation_id}|<id>`: "
            + ", ".join(f"`{n.nation_id}`" for n in neighbours[:8]),
        ]
    return "\n".join(lines)


def apply_freeform(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    nation_id: str,
    order_text: str,
    payload: str | dict[str, Any],
    turn: int,
    date: str,
    ledger: Ledger | None = None,
) -> FreeformOutcome:
    """Record what the model said, and carry out as much of it as the caps allow.

    ``ledger`` is the per-turn ceiling, shared across every freeform order in the turn.
    Passing ``None`` gives this order a ledger of its own, which is right for a single
    call and wrong for a turn -- the turn engine holds one open.
    """
    outcome = FreeformOutcome()
    try:
        data: dict[str, Any] = (
            json.loads(payload) if isinstance(payload, str) else dict(payload or {})
        )
    except (TypeError, ValueError):
        outcome.problems.append("the answer to a freeform order was not readable")
        return outcome

    outcome.narrative = sanitize(str(data.get("narrative") or ""))
    refused = data.get("refused")
    if refused:
        outcome.refused = sanitize(str(refused))
        return outcome

    text = sanitize(str(data.get("event_text") or outcome.narrative or order_text))
    if not text.strip():
        return outcome

    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.NARRATIVE, origin=EventOrigin.ENGINE, actor_nation_id=nation_id,
        text=text[:600],
        # Public: a government doing something strange is a thing the world can see. The
        # freeform channel is not a way to act in secret -- that is what espionage and
        # secret commitments are for, and they are separately governed.
        visibility=Visibility.PUBLIC,
    )

    spent = {} if ledger is None else ledger
    diffs: list[Diff] = []
    for raw in list(data.get("nudges") or ())[:MAX_NUDGES]:
        diff, problem = _nudge_to_diff(conn, raw, nation_id, event_id, spent)
        if problem:
            outcome.trimmed.append(problem)
        if diff is not None:
            diffs.append(diff)

    if not diffs:
        outcome.applied = 0
        return outcome

    applied = apply_batch(
        conn,
        DiffBatch(
            # The engine writes. What came back from the model was evidence, and every
            # number below was chosen here -- the same arrangement a contest has.
            world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
            proposer_model=None, proposer_nation_id=nation_id, diffs=tuple(diffs),
        ),
    )
    if applied.applied:
        outcome.applied = len(diffs)
    else:
        outcome.problems.append(f"the consequence was refused: {applied.reason}")
    return outcome


def _nudge_to_diff(
    conn: sqlite3.Connection, raw: Any, nation_id: str, event_id: int, spent: Ledger
) -> tuple[Diff | None, str | None]:
    """Turn one direction-and-a-word into a bounded diff, or say why not."""
    from ..state.permissions import may_write

    if not isinstance(raw, dict):
        return None, "a nudge was not an object"

    field_name = str(raw.get("field") or "")
    magnitude = MAGNITUDES.get(str(raw.get("magnitude") or ""))
    if magnitude is None:
        return None, f"unknown magnitude {raw.get('magnitude')!r}"
    sign = -1.0 if str(raw.get("direction")) == "down" else 1.0

    subject = str(raw.get("subject") or "")
    if field_name in {"stability", "legitimacy"}:
        table, target, column = "nation", nation_id, field_name
        if subject and subject != nation_id:
            # Acting for somebody else is refused by INFO-003 anyway; saying so here gives
            # a reason the player can read instead of a rule id.
            return None, f"you cannot move {subject}'s {field_name}"
    elif field_name == "relation":
        parts = subject.split("|")
        if len(parts) != 2 or parts[0] != nation_id or not parts[1]:
            return None, f"a relation must be written `{nation_id}|<other>`"
        table, target, column = "relation", subject, "score"
    else:
        return None, f"unknown field {field_name!r}"

    # The allowlist, consulted rather than assumed. The batch below goes in as ENGINE, so
    # nothing downstream would check it -- which is exactly why it is checked here, at the
    # one point where a model's answer becomes a proposed change.
    allowed, reason = may_write(Role.FREEFORM, table, column)
    if not allowed:
        return None, reason

    # The per-turn ceiling, across every freeform order this turn.
    room = PER_FIELD_TURN_CAP - spent.get((target, column), 0.0)
    if room <= 0:
        return None, f"{column} has already moved as far as one turn of this allows"
    value = sign * min(magnitude, room)
    # ...and against what the nation has, because stability and legitimacy are bounded and
    # ECON-003 condemns the batch rather than the diff. See :mod:`atlas.engine.bounds`.
    value = absorbable(conn, table, target, column, value)
    if not value:
        return None, f"{column} cannot move any further in that direction"
    spent[(target, column)] = spent.get((target, column), 0.0) + abs(value)

    return (
        Diff(table, target, column, DiffOp.ADJUST, round(value, 2),
             reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),
        None,
    )


