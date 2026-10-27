"""What a player should be asked about twice, and what to say when asking.

**A dialog that says "are you sure?" asks nothing.** The only kind worth interrupting
somebody for is one that says what the click cannot undo -- so a confirmation here is never
a yes/no on an action type, it is a sentence about *this* order against *this* target, built
from the catalogue's own `grave` note and the order's own fields.

**Only what a player can actually order.** Codex named four acts: a nuclear launch, a
surrender, dissolving a country, abandoning an alliance. Asked of the catalogue rather than
taken on trust, three of the four turn out not to be player-reachable at all --

* `nuclear_launch` is deliberately absent from `orders.PLAYER_GAMBITS`, because a channel
  where a parser's reading of a sentence can put a warhead in the air is not a channel that
  should exist. It is proposed by the world simulator and by tracked nations, never by the
  player's prose.
* Dissolving a country has no single act behind it. `peace.settle` already refuses a term
  set that would leave a power owning nothing, in as many words: *a state is not dissolved
  at the table, however many provinces it is asked for one at a time.*
* Abandoning an alliance is not an order either. A breach is **derived** from behaviour by
  `treaties.assess_breaches` -- you break a pact by doing the thing it forbade, and there is
  no button that says so first.

-- so a confirmation for any of the three would guard a path nobody can walk. What is left,
and what this covers, is every grave act the catalogue actually offers: see the `grave`
field, which is where the declaration and the reason live together.

**Reading, never deciding.** Nothing here refuses an order or writes anything. The engine's
acceptance rules are unchanged, deliberately: making acknowledgement a *requirement* would
mean an order proposed by a tracked nation or the world simulator had to carry a field
invented for a dialog, and that is a rule about the interface leaking into the rules about
the world.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

__all__ = ["AIR_ORDERS", "Confirmation", "grave_capabilities", "needed_for"]

#: The gambits that put aircraft over somebody else's ground, and what a player is agreeing
#: to by ordering one.
#:
#: **Why this is keyed on the gambit kind rather than on the action type.** `gambit` is the
#: umbrella for a summit, an envoy, a bribe, a sting, sabotage, incitement, an expose and an
#: air strike; giving the whole capability a `grave` note would put a confirmation in front
#: of every one of them, which is precisely how a player learns to click through
#: confirmations and how the one that mattered then goes past with the rest.
#:
#: **And why an air strike earns one at all**, when the module's own rule is that a
#: confirmation is for what the next turn will not give back. Three things, and all three
#: were undiscoverable: the raid is a public act against whoever holds the ground, so it is
#: read as one whatever the player thought they were doing; it is met by fighters and
#: air-defence technology and can be broken up entirely, at a cost in aircraft that is paid
#: whether or not anything is hit; and it **takes no ground**, which is the single
#: expectation a player new to the layer is most likely to have wrong. None of that is
#: recoverable by looking at the map afterwards.
AIR_ORDERS: dict[str, str] = {
    "air_strike": (
        "Aircraft over {where} — a public act of war against {who}, and it takes no "
        "ground. The raid is met by their fighters and their air-defence technology and "
        "can be broken up entirely; being intercepted costs you aircraft whether or not "
        "anything is hit."
    ),
}


@dataclass(frozen=True, slots=True)
class Confirmation:
    """One thing to ask about before a turn is spent, and what to say."""

    action_type: str
    #: What the order does, named the way the player wrote it: the act and its target.
    what: str
    #: What cannot be taken back, from the catalogue's own `grave` note.
    because: str

    def rendered(self) -> str:
        return f"{self.what} — {self.because}"


def grave_capabilities() -> dict[str, str]:
    """``action_type -> what cannot be taken back``, for every act that declares one."""
    from .capabilities import CAPABILITIES

    return {c.action_type: c.grave for c in CAPABILITIES if c.grave}


def _named(conn: sqlite3.Connection, world_id: str, action: dict) -> str:
    """The target of an order in the words a person would use, or ``""``.

    Through `naming.resolve_nation` and `resolve_region`, so what is named in the
    confirmation is the row the engine would act on -- not the string the player typed,
    which may be an alias, and not a guess at what they meant.
    """
    from .naming import resolve_nation, resolve_region

    nation = resolve_nation(conn, world_id, action.get("target_nation"))
    if nation:
        row = conn.execute(
            "SELECT name FROM nation WHERE world_id = ? AND id = ?",
            (world_id, nation)).fetchone()
        return str(row["name"]) if row else nation
    region = resolve_region(conn, world_id, action.get("target_region"))
    if region:
        row = conn.execute(
            "SELECT name FROM region WHERE id = ?", (region,)).fetchone()
        return str(row["name"]) if row else region
    return ""


def needed_for(
    conn: sqlite3.Connection, world_id: str, actions: list[dict]
) -> list[Confirmation]:
    """Everything in this batch of orders worth stopping the player for.

    One per grave order rather than one for the batch, because "three of these cannot be
    undone" is not something a person can act on -- *which* three is.

    Deliberately quiet about everything else. A confirmation on an ordinary act is how a
    player learns to click through confirmations, and then the one that mattered goes past
    with the rest of them.
    """
    grave = grave_capabilities()
    found: list[Confirmation] = []
    for action in actions:
        kind = str(action.get("type") or "")
        if kind == "gambit":
            air = _air_confirmation(conn, world_id, action)
            if air is not None:
                found.append(air)
            continue
        because = grave.get(kind)
        if not because:
            continue
        target = _named(conn, world_id, action)
        said = kind.replace("_", " ")
        found.append(Confirmation(
            action_type=kind,
            what=f"{said} — {target}" if target else said,
            because=because))
    return found


def _air_confirmation(
    conn: sqlite3.Connection, world_id: str, action: dict
) -> Confirmation | None:
    """The one gambit worth stopping for, and what to say about *this* one.

    ``None`` for every other gambit, which is most of them -- see :data:`AIR_ORDERS`.

    **The theatre is named through the resolver, not the string.** A player who wrote
    "bomb the Reaches" and a menu that filled in `r_coast` must both be shown the province
    the engine would actually act on, because the whole point of a confirmation is that it
    is the last chance to notice they are not the same place. Same reason :func:`_named`
    exists; this needs the region rather than the nation.

    **And read from the fields, and with the default aim, the order handler flies it by**
    (:data:`~atlas.engine.orders.GAMBIT_GROUND_FIELDS`, :func:`~atlas.engine.orders.strike_aim`).
    This read ``region``/``target_region`` and said "forces" when no aim was given, while
    the handler read ``to_region``/``from_region`` and bombed the works -- so a strike ordered
    in a sentence was confirmed over "an unnamed province, aimed at forces" and flown at the
    railways of a province the player was never shown.
    """
    from .naming import resolve_nation, resolve_region
    from .orders import GAMBIT_GROUND_FIELDS, strike_aim

    said = AIR_ORDERS.get(str(action.get("gambit_kind") or ""))
    if not said:
        return None

    region = next(
        (found for name in GAMBIT_GROUND_FIELDS
         if (found := resolve_region(conn, world_id, action.get(name)))),
        None,
    )
    where = "an unnamed province"
    if region:
        row = conn.execute("SELECT name FROM region WHERE id = ?", (region,)).fetchone()
        where = str(row["name"]) if row else region

    target = resolve_nation(conn, world_id, action.get("target_nation"))
    who = "whoever holds it"
    if not target and region:
        # The holder rather than nothing: an order that named a place and not a power is
        # still aimed at somebody, and that somebody is who the map says holds the ground.
        row = conn.execute(
            "SELECT COALESCE(controller_nation_id, owner_nation_id) AS holder"
            "  FROM region WHERE id = ?", (region,)).fetchone()
        target = str(row["holder"]) if row and row["holder"] else ""
    if target:
        row = conn.execute(
            "SELECT name FROM nation WHERE world_id = ? AND id = ?",
            (world_id, target)).fetchone()
        who = str(row["name"]) if row else target

    aim = strike_aim(action)
    return Confirmation(
        action_type="gambit",
        what=f"air strike — {where}, aimed at {aim}",
        because=said.format(where=where, who=who))
