"""What your orders will do, before you spend a turn finding out.

A player writes prose, a parser turns it into actions, and until now the next thing that
happened was the turn. An order that could never have worked came back as a refusal
*afterwards* -- with the jump spent, the model calls paid for, and nothing to do about it
until next turn.

This is the preview. It takes the actions the parser produced and asks, of each one, the
questions the turn would ask: does the engine execute this kind of order at all, does this
nation have what the order needs, and -- where the engine can answer without doing anything
-- would it be refused, and what should be written instead.

**It calls the engine's own predicates. It does not restate them.** The refusal on a card
comes from :func:`atlas.engine.gambit._requirements_met`, the same function
``apply_gambit`` calls before it rolls; the cost, the prerequisites and the notes come from
the :data:`atlas.engine.capabilities.CAPABILITIES` table the parser and the prompt already
read. A workbench with its own copy of the rules is a workbench that eventually disagrees
with the turn, and a preview that disagrees with the outcome is worse than no preview --
the player learns to ignore it, which costs them the one they should have believed.

**And it is honest about the limit.** Most orders cannot be judged before they run: whether
a march arrives, whether an investment pays, whether a treaty is accepted are things the
engine decides from the seeded stream at resolution, and a card claiming to know would be
inventing. Those say *will attempt* and carry their cost and prerequisites, and
:data:`PREVALIDATED` names the kinds where a real verdict was available -- so a reader can
tell "checked and fine" from "not checkable", which are very different reassurances.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from .capabilities import available_to, by_type
from .causality import Refusal

__all__ = [
    "PREVALIDATED",
    "OrderCard",
    "Prerequisite",
    "review_orders",
]

#: Action types whose refusal can be decided before the turn runs, and by what.
#:
#: **Short on purpose.** The engine refuses very little in advance -- it *attempts* things
#: and lets the resolution decide -- so this is the honest list rather than an aspiration.
#: A kind gets in here only when there is a real predicate that answers without writing.
PREVALIDATED: dict[str, str] = {
    "gambit": "gambit._requirements_met, the same check apply_gambit makes before it rolls",
}

#: What a card's verdict can be.
VALID = "will attempt"
REFUSED = "refused"
BLOCKED = "cannot be attempted"
INERT = "nothing will happen"


@dataclass(frozen=True, slots=True)
class Prerequisite:
    """One thing the order needs, and whether it has it."""

    name: str
    supplied: bool
    value: str = ""


@dataclass(slots=True)
class OrderCard:
    """One order, as the engine sees it before the turn runs."""

    index: int
    action_type: str
    summary: str
    #: The capability table's own sentence for this kind of order.
    explain: str = ""
    target: str = ""
    #: The capability table's own words about what it costs. Not a computed number: the
    #: engine states costs per kind and computing a figure here would be a second opinion.
    cost: str = ""
    prerequisites: tuple[Prerequisite, ...] = ()
    verdict: str = VALID
    #: Set when a real engine predicate refused it. Carries the reason and the alternative.
    refusal: Refusal | None = None
    #: Whether a real predicate ran, as opposed to the card simply having nothing against
    #: it. ``False`` means *not checkable*, which is not the same as *checked and fine*.
    prevalidated: bool = False
    notes: tuple[str, ...] = ()
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.verdict == VALID

    @property
    def blocked(self) -> bool:
        return self.verdict in (REFUSED, BLOCKED)

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.prerequisites if not p.supplied)


def _target_of(action: dict) -> str:
    """Whatever the order is aimed at, in the order the engine looks for it."""
    for key in ("target_nation", "target", "to_region", "region", "from_region",
                "unit_kind", "project"):
        value = action.get(key)
        if value:
            return str(value)
    return ""


def _gambit_verdict(
    conn: sqlite3.Connection, world_id: str, nation_id: str, action: dict, card: OrderCard
) -> None:
    """Ask the gambit layer the same question it asks itself, and record the answer."""
    from .gambit import KINDS, _requirements_met
    from .orders import PLAYER_GAMBITS, _undertaking_kind, gambit_ground, strike_aim

    named = _undertaking_kind(action)
    if named not in PLAYER_GAMBITS:
        card.verdict = BLOCKED
        card.refusal = Refusal(
            "names_an_undertaking",
            "no undertaking was named, so there is nothing to attempt.",
            "name one of " + ", ".join(PLAYER_GAMBITS) + ".",
        )
        card.prevalidated = True
        return

    kind = next((k for k in KINDS if k.id == named), None)
    if kind is None:
        return
    card.explain = kind.blurb or card.explain
    card.prerequisites += tuple(
        Prerequisite(name=requirement, supplied=True) for requirement in kind.requires
    )

    refusal = _requirements_met(
        conn, world_id, kind, nation_id,
        str(action.get("target_nation") or action.get("target") or "") or None,
        # The ground and the aim the order handler will fly, read through the same
        # helpers: a card that pre-checked `region` or `to_region` passed a strike named
        # by a prefill's `target_region` as unaimed, and one that pre-checked no aim
        # passed a strike over open water that the handler then bombs as "the works".
        str(gambit_ground(action) or "") or None,
        strike_aim(action) if named == "air_strike" else "",
    )
    card.prevalidated = True
    if refusal is not None:
        card.verdict = REFUSED
        card.refusal = refusal
        # The gate that failed is the prerequisite that was not met. Marked rather than
        # described, so the card can show which of several requirements is the wall.
        card.prerequisites = tuple(
            Prerequisite(p.name, supplied=p.name != refusal.requirement, value=p.value)
            for p in card.prerequisites
        )


def review_orders(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    nation_id: str | None,
    actions: list[dict],
) -> list[OrderCard]:
    """One card per parsed order. Read-only: nothing here writes to the world.

    The checks, in the order a turn would hit them:

    1. **is this a kind of order at all** -- an action type the capability table does not
       name is one nothing will execute;
    2. **does anything execute it** -- ``handled_by == "nothing"`` is a real column, and an
       order routed nowhere is inert rather than refused. Telling a player the first was
       the second sends them to fix a sentence that was fine;
    3. **does the order carry what its kind requires** -- ``Capability.required``;
    4. **can this nation use it at all right now** -- ``Capability.available``, the same
       predicate that decides whether the prompt offers the mechanic;
    5. **would it be refused** -- only where a real predicate can say. See
       :data:`PREVALIDATED`.
    """
    if not nation_id or not actions:
        return []

    offered = {capability.action_type for capability in available_to(conn, world_id, nation_id)}
    cards: list[OrderCard] = []

    for index, action in enumerate(actions):
        action_type = str(action.get("type") or "")
        card = OrderCard(
            index=index,
            action_type=action_type,
            summary=str(action.get("summary") or "").strip(),
            target=_target_of(action),
        )
        capability = by_type(action_type)

        if capability is None:
            card.verdict = BLOCKED
            card.refusal = Refusal(
                "known_order",
                f"{action_type!r} is not an order your government knows how to take.",
                "rewrite it as one of the orders the briefing lists.",
            )
            card.prevalidated = True
            cards.append(card)
            continue

        card.explain = capability.explain
        card.cost = capability.cost or "no direct cost"
        card.notes = capability.notes
        card.prerequisites = tuple(
            Prerequisite(name=name, supplied=bool(action.get(name)),
                         value=str(action.get(name) or ""))
            for name in capability.required
        )

        if capability.handled_by == "nothing":
            # Inert, not refused: the sentence was understood and there is simply no
            # mechanism behind it. The capability's own note says what to do instead.
            card.verdict = INERT
            card.prevalidated = True
            card.refusal = Refusal(
                "has_a_handler",
                f"no part of your government carries out a {action_type!r} order.",
                capability.notes[0] if capability.notes else "",
            )
            cards.append(card)
            continue

        if card.missing:
            card.verdict = BLOCKED
            card.prevalidated = True
            card.refusal = Refusal(
                "required_fields",
                "the order does not say " + ", ".join(card.missing) + ".",
                f"name {' and '.join(card.missing)} in the order.",
            )
            cards.append(card)
            continue

        if action_type not in offered:
            card.verdict = BLOCKED
            card.prevalidated = True
            card.refusal = Refusal(
                "available_now",
                f"{nation_id} cannot {capability.explain} at the moment.",
                "",
            )
            cards.append(card)
            continue

        if action_type == "gambit":
            _gambit_verdict(conn, world_id, nation_id, action, card)

        cards.append(card)

    return cards
