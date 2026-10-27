"""What you can actually do to a province, decided by the engine rather than by a menu.

The map has always been the one surface with no verbs on it: a player could look at a
province and read everything about it, and the only way to act on it was to describe the
action in prose somewhere else.

**The menu is not a list of actions; it is the workbench asked about one region.** Every
candidate here is built into the same action dict the parser produces and then handed to
:func:`atlas.engine.workbench.review_orders` -- the same call the orders preview makes, which
in turn calls the engine's own predicates. So a strike the menu offers and a strike the
workbench blesses are the same verdict from the same code, and the menu cannot drift into
offering something the turn would refuse. A hand-maintained list of "what you can do here"
is exactly the thing that goes stale the first time a rule changes.

**Refused actions are shown, not hidden.** A greyed line reading *"you are not at war with
aurelia, who holds this"* teaches the map; an absent line teaches nothing, and the player
learns the game's shape only by having orders bounce after the fact.

**One pipeline.** Choosing an action produces an ordinary action dict, which the window
appends to the same pending orders the parser fills. It flows into the workbench preview and
into the turn exactly as a typed order does, because it *is* one -- there is no second path
into the engine and this deliberately does not open one.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .workbench import BLOCKED, INERT, REFUSED, VALID, OrderCard, review_orders

__all__ = ["MapAction", "actions_for_region"]

#: Undertakings worth offering against a province, in the order a player thinks of them.
#: Read from the gambit catalogue rather than named here -- these are the ids, the rules are
#: the engine's.
_REGION_GAMBITS = ("air_strike", "sabotage", "incite")


@dataclass(frozen=True, slots=True)
class MapAction:
    """One thing the menu offers, and what the engine makes of it."""

    label: str
    #: The order this would produce -- an ordinary action dict, the same shape the parser
    #: emits, so it can be appended to pending orders unchanged.
    action: dict
    #: The workbench's own verdict. Not a second opinion: literally its call.
    card: OrderCard
    #: A field the action still needs before it can be attempted, filled by a second click.
    #: ``""`` when the action is complete as it stands.
    needs: str = ""

    @property
    def enabled(self) -> bool:
        """Offered as something that can be chosen.

        An action awaiting a second target counts as enabled: it is incomplete rather than
        impossible, and greying it would say the opposite of what is true.
        """
        return bool(self.needs) or self.card.verdict == VALID

    @property
    def why_not(self) -> str:
        """The engine's reason, for a line that cannot be chosen."""
        if self.enabled or self.card.refusal is None:
            return ""
        return self.card.refusal.because

    @property
    def instead(self) -> str:
        if self.enabled or self.card.refusal is None:
            return ""
        return self.card.refusal.instead


def _holder(conn: sqlite3.Connection, world_id: str, region_id: str) -> tuple[str, str]:
    row = conn.execute(
        "SELECT name, COALESCE(controller_nation_id, owner_nation_id) AS holder"
        "  FROM region WHERE world_id = ? AND id = ?", (world_id, region_id)).fetchone()
    if row is None:
        return "", ""
    return str(row["name"]), str(row["holder"] or "")


def _candidates(
    conn: sqlite3.Connection, world_id: str, nation_id: str, region_id: str
) -> list[tuple[str, dict, str]]:
    """``(label, action, needs)`` for everything worth asking the engine about here."""
    name, holder = _holder(conn, world_id, region_id)
    if not name:
        return []
    mine = holder == nation_id
    out: list[tuple[str, dict, str]] = []

    if mine:
        out.append((f"Fortify {name}", {
            "type": "fortify", "summary": f"Fortify {name}",
            "region": region_id}, ""))
        out.append((f"Stand down the garrison in {name}", {
            "type": "disband_units", "summary": f"Disband the units in {name}",
            "region": region_id}, ""))

    # **Two clicks, not a dialog.** A move needs somewhere to come from, and the province
    # under the cursor is the obvious destination -- so the action is created pointing here
    # and the source arrives with the next click. `needs` is what is still missing.
    out.append((f"Move forces into {name}", {
        "type": "move_units", "summary": f"Move forces into {name}",
        "to_region": region_id}, "from_region"))

    if holder and not mine:
        for kind in _REGION_GAMBITS:
            out.append((f"{kind.replace('_', ' ').title()} in {name}", {
                "type": "gambit", "summary": f"{kind.replace('_', ' ')} in {name}",
                "gambit_kind": kind, "target_nation": holder,
                "region": region_id, "aim": "forces"}, ""))
        out.append((f"Send word to {holder}", {
            "type": "diplomatic_message", "summary": f"Send word to {holder} about {name}",
            "target_nation": holder}, ""))

    out.append((f"Invest in {name}", {
        "type": "economic_investment", "summary": f"Invest in {name}",
        "region": region_id, "amount": 5}, ""))
    return out


def actions_for_region(
    conn: sqlite3.Connection, world_id: str, nation_id: str | None, region_id: str
) -> list[MapAction]:
    """Everything this player might do to this province, with the engine's verdict on each.

    Read-only: it builds candidate orders and asks the workbench about them, and the
    workbench asks the engine. Nothing here writes, so opening a menu cannot change a world.

    Ordered with what can be done first and what cannot after it, because a menu whose
    top item is greyed reads as a menu that does not work.
    """
    if not nation_id:
        return []
    candidates = _candidates(conn, world_id, nation_id, region_id)
    if not candidates:
        return []

    cards = review_orders(
        conn, world_id, nation_id=nation_id,
        actions=[action for _label, action, _needs in candidates])

    found = [
        MapAction(label=label, action=action, card=card, needs=needs)
        for (label, action, needs), card in zip(candidates, cards, strict=True)
        # An order nothing executes is not worth a line on a menu. It is honest in the
        # workbench, where the player wrote it; offering it here would be inventing it.
        if card.verdict != INERT
    ]
    found.sort(key=lambda entry: (not entry.enabled, entry.label))
    return found


def is_refused(card: OrderCard) -> bool:
    """Whether a card is one of the two ways the engine says no."""
    return card.verdict in (REFUSED, BLOCKED)
