"""The peace table, as a player sees it: what you could ask for, and whether they would sign.

**Every number here is the engine's own.** The provinces offered are the enemy's ground you
occupy, priced by :func:`atlas.engine.peace.review_terms`; the money is capped where
:func:`peace._enforceable` caps it; the verdict is :func:`peace.acceptance` -- the very call a
settlement makes when the order is carried out. So "they will sign" here is not a forecast:
the engine decides acceptance alone and deterministically, from the war, and this asks it the
same question early. Only what happens between now and the turn -- a battle, a province
lost -- can change the answer.

The table writes an ordinary order sentence ("Sue for peace with Iran: they cede Zanjan and
Ilam and pay 20."), which the order reader turns back into these terms. A view; it writes
nothing.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from .leverage import leverage_between
from .peace import (
    REPARATION_CAP,
    Acceptance,
    ProposedTerm,
    acceptance,
    review_terms,
    suggested_terms,
)
from .wareffort import effort_between

__all__ = ["Claim", "PeaceTable", "Verdict", "sentence", "table", "terms_for", "verdict"]


@dataclass(slots=True)
class Claim:
    """One of the enemy's provinces you occupy, and what asking for it costs them."""

    region_id: str
    name: str
    #: Share of the enemy's homeland it represents, as acceptance prices it.
    cost: float
    #: Their own people's ground rather than a colony or an earlier conquest.
    core: bool
    #: Empty when it can be asked for; otherwise why not (their last province, say).
    why_not: str = ""


@dataclass(slots=True)
class PeaceTable:
    me: str
    enemy: str
    enemy_name: str
    #: -1..+1 from the enemy's side: below zero they are losing.
    position: float
    their_exhaustion: float
    my_exhaustion: float
    claims: list[Claim] = field(default_factory=list)
    #: The most money a settlement may ask of them.
    max_reparations: int = 0
    #: The provinces the engine's own best package would ask for, which they would sign.
    suggested: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Verdict:
    signs: bool
    #: How much of what they will bear this package uses, 0..1+ -- the meter on the screen.
    load: float
    because: str
    refused: list[str] = field(default_factory=list)


def _name(conn: sqlite3.Connection, world_id: str, nation_id: str) -> str:
    row = conn.execute("SELECT name FROM nation WHERE world_id = ? AND id = ?",
                       (world_id, nation_id)).fetchone()
    return str(row["name"]) if row else nation_id


def table(conn: sqlite3.Connection, world_id: str, me: str, enemy: str) -> PeaceTable:
    effort_them = effort_between(conn, world_id, enemy, me)
    effort_me = effort_between(conn, world_id, me, enemy)
    out = PeaceTable(
        me=me, enemy=enemy, enemy_name=_name(conn, world_id, enemy),
        position=round(leverage_between(conn, world_id, enemy, me).position, 3),
        their_exhaustion=round(effort_them.exhaustion if effort_them else 0.0, 1),
        my_exhaustion=round(effort_me.exhaustion if effort_me else 0.0, 1))
    for row in conn.execute(
            "SELECT id, name, home_nation_id FROM region WHERE world_id = ?"
            "   AND owner_nation_id = ? AND controller_nation_id = ? AND kind <> 'ocean'"
            " ORDER BY name", (world_id, enemy, me)):
        term = ProposedTerm("cede_region", obliged=enemy, beneficiary=me,
                            subject=str(row["id"]))
        (card,) = review_terms(conn, world_id, belligerents=(enemy, me), terms=[term])
        out.claims.append(Claim(
            region_id=str(row["id"]), name=str(row["name"]), cost=round(card.cost, 4),
            core=str(row["home_nation_id"] or "") == enemy,
            why_not="" if card.ok else card.because))
    treasury = conn.execute("SELECT treasury FROM nation WHERE world_id = ? AND id = ?",
                            (world_id, enemy)).fetchone()
    out.max_reparations = int(float(treasury["treasury"] or 0.0) * REPARATION_CAP) \
        if treasury else 0
    out.suggested = [term.subject for term in suggested_terms(
        conn, world_id, loser=enemy, winner=me)]
    return out


def terms_for(me: str, enemy: str, regions, reparations: float) -> list[dict]:
    """The settlement terms a choice on the table stands for, in the shape `settle` reads."""
    terms = [{"kind": "cede_region", "obliged": enemy, "beneficiary": me, "subject": region}
             for region in regions]
    if reparations and reparations > 0:
        terms.append({"kind": "reparations", "obliged": enemy, "beneficiary": me,
                      "value": float(int(reparations))})
    return terms


def verdict(conn: sqlite3.Connection, world_id: str, me: str, enemy: str, regions,
            reparations: float) -> Verdict:
    """What the engine would answer, if this were offered now."""
    terms = terms_for(me, enemy, regions, reparations)
    cards = review_terms(conn, world_id, belligerents=(me, enemy), terms=terms)
    refused = [f"{card.term.describe()}: {card.because}" for card in cards if not card.ok]
    answer: Acceptance = acceptance(conn, world_id, offered_to=enemy, offered_by=me,
                                    terms=terms)
    load = answer.cost / answer.tolerance if answer.tolerance > 0 else (
        0.0 if answer.cost <= 0 else 2.0)
    name = _name(conn, world_id, enemy)
    if refused:
        because = refused[0]
    elif answer.accepted:
        because = (f"Your terms use about {min(load, 1.0):.0%} of what {name} is willing to "
                   "give up right now.")
    else:
        because = _plain(answer, name, load)
    return Verdict(signs=answer.accepted and not refused, load=round(load, 3),
                   because=because, refused=refused)


def _plain(answer: Acceptance, name: str, load: float) -> str:
    if answer.position > 0.05:
        return (f"{name} thinks it is winning the war and expects to be given something, "
                "not to give.")
    return (f"Your terms ask {load:.0%} of what {name} is willing to give up. Ask for less, "
            "or keep fighting until it is weaker.")


def sentence(enemy_name: str, region_names, reparations: float) -> str:
    """The order a choice on the table becomes, in words the order reader reads back."""
    names = list(region_names)
    parts = []
    if names:
        listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        parts.append(f"they cede {listed}")
    if reparations and reparations > 0:
        parts.append(f"pay {int(reparations)}")
    if not parts:
        return f"Sue for peace with {enemy_name}: a white peace."
    return f"Sue for peace with {enemy_name}: " + " and ".join(parts) + "."
