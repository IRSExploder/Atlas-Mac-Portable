"""What a player's research looks like on one screen: what is open, what it costs, what it
does, what is under way and what is still locked.

**Research existed and nobody could see it.** A programme was begun by typing its name into
the orders box, and nothing on the screen said what the names were -- so the one lever that
makes a country's army better than its neighbour's was found by players who already knew it
was there. This reads the same catalogue, the same prices and the same rules the order does
(:func:`atlas.engine.production.order_research`), and says them in words; the panel that
shows it writes an ordinary order sentence, so a click and a typed line are the same thing.

A view and nothing more: it writes nothing.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from .production import in_progress, industrial_capacity
from .research import (
    MAX_MARK,
    MAX_RESEARCH_SHARE,
    UPGRADE_LINES,
    Technology,
    catalogue_for,
    known_technologies,
    mark_name,
    marks,
)

__all__ = ["Board", "Card", "LineRow", "board", "effect_words", "kind_words", "mark_label"]

#: A unit kind as a reader says it, plural, for "Armour +1 strength".
_KIND_WORDS = {
    "infantry": "Infantry", "armour": "Armour", "air_wing": "Air wings",
    "battleship": "Battleships", "cruiser": "Cruisers", "destroyer": "Destroyers",
    "submarine": "Submarines", "carrier": "Carriers", "transport": "Transports",
}


def kind_words(kind: str) -> str:
    return _KIND_WORDS.get(kind, kind.replace("_", " ").capitalize())


def effect_words(tech: Technology) -> str:
    """What a programme does, as a sentence fragment a player reads in one go."""
    strength: dict[float, list[str]] = {}
    parts: list[str] = []
    for effect in tech.effects:
        if effect.scope == "unit_template" and effect.field_name == "base_strength":
            strength.setdefault(effect.amount, []).append(kind_words(effect.subject))
        elif effect.scope == "unit_template" and effect.field_name == "build_days":
            parts.append(f"{kind_words(effect.subject)} built {abs(effect.amount):g} days "
                         f"{'faster' if effect.amount < 0 else 'slower'}")
        elif effect.scope == "arsenal":
            parts.append(f"+{effect.amount:g} nuclear warhead")
        else:
            parts.append(f"{effect.amount:+g} {effect.field_name.replace('_', ' ')}")
    lead = [f"{', '.join(kinds)} +{amount:g} strength" for amount, kinds in strength.items()]
    return "; ".join(lead + parts) or tech.blurb


@dataclass(slots=True)
class Card:
    tech_id: str
    name: str
    blurb: str
    effect: str
    cost: float
    turns: int
    #: ``open`` | ``running`` | ``done`` | ``locked`` | ``unaffordable`` | ``no_slot``
    state: str
    #: Why it cannot be started now, when it cannot.
    why: str = ""
    turns_left: int = 0
    #: The sentence the orders box carries to begin it.
    order: str = ""

    @property
    def startable(self) -> bool:
        return self.state == "open"


@dataclass(slots=True)
class LineRow:
    line_id: str
    name: str
    #: The unit kinds the line improves, as a reader says them.
    kinds: str
    mark: int
    max_mark: int
    #: The next mark's programme, or ``None`` at the top of the line.
    next: Card | None


@dataclass(slots=True)
class Board:
    treasury: float
    slots_used: int
    slots: int
    lines: list[LineRow] = field(default_factory=list)
    #: Programmes outside the upgrade lines, open and running first, then locked, then done.
    programmes: list[Card] = field(default_factory=list)
    #: The ceiling on what research can add, as a share of a formation's standard strength.
    ceiling: float = MAX_RESEARCH_SHARE

    @property
    def running(self) -> list[Card]:
        found = [line.next for line in self.lines if line.next and line.next.state == "running"]
        return found + [card for card in self.programmes if card.state == "running"]


def board(conn: sqlite3.Connection, world_id: str, nation_id: str) -> Board:
    nation = conn.execute("SELECT treasury FROM nation WHERE world_id = ? AND id = ?",
                          (world_id, nation_id)).fetchone()
    treasury = float(nation["treasury"] or 0.0) if nation else 0.0
    used = in_progress(conn, world_id, nation_id)
    slots = industrial_capacity(conn, nation_id)
    out = Board(treasury=treasury, slots_used=used, slots=slots)

    catalogue = catalogue_for(conn, world_id)
    names = {tech.id: tech.name for tech in catalogue}
    known = known_technologies(conn, world_id, nation_id)
    running = {str(row["kind"]): int(row["turns_remaining"] or 0) for row in conn.execute(
        "SELECT kind, turns_remaining FROM construction WHERE world_id = ? AND nation_id = ?"
        "   AND project_kind = 'research'", (world_id, nation_id))}

    def card(tech: Technology) -> Card:
        made = Card(tech_id=tech.id, name=tech.name, blurb=tech.blurb,
                    effect=effect_words(tech), cost=tech.cost_treasury, turns=tech.turns,
                    state="open", order=f"Research {tech.name}")
        missing = [names.get(need, need) for need in tech.requires if need not in known]
        if tech.id in known:
            made.state = "done"
        elif tech.id in running:
            made.state, made.turns_left = "running", running[tech.id]
        elif missing:
            made.state, made.why = "locked", "Needs " + " and ".join(missing) + " first"
        elif treasury < tech.cost_treasury:
            made.state = "unaffordable"
            made.why = f"Costs {tech.cost_treasury:.0f}; you have {treasury:.0f}"
        elif used >= slots:
            made.state = "no_slot"
            made.why = "Every build slot is busy; wait for something to finish"
        return made

    reached = marks(conn, world_id, nation_id)
    in_line: set[str] = set()
    for line in UPGRADE_LINES:
        steps = sorted((t for t in catalogue if t.line == line.id), key=lambda t: t.mark)
        if not steps:
            continue
        in_line.update(t.id for t in steps)
        kinds = [e.subject for e in steps[0].effects if e.subject]
        mark = max((reached.get(kind, 1) for kind in kinds), default=1)
        following = next((t for t in steps if t.mark == mark + 1), None)
        out.lines.append(LineRow(
            line_id=line.id, name=line.name,
            kinds=", ".join(kind_words(kind) for kind in kinds),
            mark=mark, max_mark=MAX_MARK,
            next=card(following) if following else None))

    order = {"running": 0, "open": 1, "unaffordable": 2, "no_slot": 2, "locked": 3, "done": 4}
    rest = [card(tech) for tech in catalogue if tech.id not in in_line]
    out.programmes = sorted(rest, key=lambda c: (order[c.state], c.cost, c.name))
    return out


def mark_label(kind: str, mark: int) -> str:
    """"Armour Mk II" -- the name a formation goes by once its line has been improved."""
    return f"{kind_words(kind)} {mark_name(mark)}"
