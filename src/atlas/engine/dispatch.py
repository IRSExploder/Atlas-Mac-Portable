"""The turn as a situation report: what happened across the world, and to whom.

**A log is not news.** A turn of the Modern Day map writes about a hundred and fifty events,
ninety of them battle lines, and the left rail shows them in the order the engine happened
to write them. Everything a reader wants is in there and none of it is *findable*: a war
declared, an alliance signed and a coup sit between two dozen completed workshops, and the
only way to know which is which is to read all of it.

So this is the same events, sorted the way a foreign ministry would sort them -- by what a
staff must not miss, then into the sections a reader already has names for. It adds nothing:
every line here is an event's own sentence, and every ranking is
:func:`atlas.state.visibility.salience_of`, which is what the briefings already rank by. Two
copies of *what matters* is one too many.

**Three things are counted rather than listed.** Battles, works and relation shifts arrive in
numbers no one reads through -- ninety, twenty, six -- and a section that prints ninety lines
is the log again with headings on it. The top few print and the rest becomes a figure.

**Scoped to the reader.** The window is :func:`atlas.state.visibility.known_events`, the same
call the briefing handed to a model uses, so a dispatch cannot show a nation something its
own government was not told. The derived lines -- ground that changed hands, who moved
against whom -- are gated the same way: each is tied to the event that caused it, and an
event the reader may not see takes its consequence with it.

**The ``digest`` table is used when it holds anything.** It is written by the consolidation
pass at :mod:`atlas.engine.digest`, scoped per audience for exactly this reason, and where
there is one for this reader it rides at the top as the standing background. Where there is
not -- an early world, or one played without the fold -- the sections carry the whole thing
and nothing is missing but the prose.

Read-only from end to end. Opening a dispatch cannot change a save.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from ..state.visibility import by_salience, salience_of
from .causality import visible_event_ids

__all__ = [
    "DECISIONS",
    "Dispatch",
    "Item",
    "Section",
    "dispatch_for",
]

#: Which section an event kind is filed under. **Grouping, not translation**: the event's
#: own sentence is still what prints, and this only decides the heading it sits beneath, so
#: a kind missing from the table costs a reader a generic heading and nothing else.
_SECTION: dict[str, str] = {
    "war_declared": "War and peace",
    "peace_signed": "War and peace",
    "ceasefire": "War and peace",
    "ultimatum": "War and peace",
    "casus_belli": "War and peace",
    "capitulation": "War and peace",
    "battle": "The fighting",
    "siege": "The fighting",
    "move": "The fighting",
    "territory_transfer": "Ground that changed hands",
    "treaty_signed": "Agreements",
    "treaty_broken": "Agreements",
    "alliance_formed": "Agreements",
    "alliance_broken": "Agreements",
    "trade_agreement": "Agreements",
    "commitment_made": "Agreements",
    "commitment_broken": "Agreements",
    "commitment_fulfilled": "Agreements",
    "coup": "Governments",
    "revolution": "Governments",
    "election": "Governments",
    "leader_change": "Governments",
    "unrest": "Governments",
    "famine": "Governments",
    "plague": "Governments",
    "disaster": "Governments",
    "espionage": "Rumour and dispatch",
    "sabotage": "Rumour and dispatch",
    "diplomatic_message": "Rumour and dispatch",
    "envoy_dispatched": "Rumour and dispatch",
    "narrative": "Rumour and dispatch",
    "discovery": "Rumour and dispatch",
    "construction": "At home",
    "economic_reform": "At home",
    "unit_raised": "At home",
    "unit_disbanded": "At home",
    "order_issued": "Your orders",
    "order_rejected": "Your orders",
}

_OTHER = "Elsewhere"

#: The order a reader looks for things in. Force first, then who will be fighting beside
#: whom, then everything that merely happened. The same argument
#: :data:`atlas.state.visibility.SALIENCE` makes, at the level of the page rather than the
#: line.
_ORDER = (
    "War and peace", "Ground that changed hands", "The fighting", "Agreements",
    "Governments", "Who moved against whom", "Rumour and dispatch", "At home",
    "Your orders", _OTHER,
)

#: How many lines a section prints before the rest becomes a number. Four is what fits on a
#: page beside five other sections; the point of the cap is that the page stays scannable,
#: which stops being true the moment one section can run to ninety.
_SHOWN = 4

#: Sections whose whole point is volume, and which are therefore capped harder. A reader
#: wants to know that ninety engagements were fought and where the worst of them were, not
#: to read ninety lines of front.
_DENSE = {"The fighting", "At home"}
_DENSE_SHOWN = 3

#: How far down the log the window reaches before ranking. Generous, because the ranking is
#: the thing that decides what shows and a cap applied before it would throw away the coup
#: to keep six workshops -- the exact failure `known_events` documents.
_WINDOW = 400

#: How much a relation has to move to be worth a line. Below this it is drift: every pair of
#: neighbours nudges every turn, and printing all of it would bury the one that mattered.
_RELATION_FLOOR = 3.0

#: What a turn gets remembered for. **Gravity alone picks the wrong lede**: a battle and a
#: declaration of war both weigh 3, ties break by recency, and the engine writes the front
#: after the decision -- so the page about the month the United States invaded Mexico opened
#: with *air superiority not flown for is air superiority given up*. A battle is the
#: substrate of a war that was already declared; the decision is the news. Preference, not
#: exclusion: where nothing on this list happened, the gravest thing that did still leads.
#:
#: **Ordered, not a set.** Within the list the first kind present wins, because recency is
#: the wrong tie-break here too: a war beginning or ending is a larger fact than a province
#: moving inside one, and the province moves later in the turn. A front page is an editorial
#: order, and this is it, written down.
_LEDE_KINDS = (
    "capitulation", "war_declared", "peace_signed", "ceasefire",
    "coup", "revolution", "territory_transfer", "alliance_broken", "treaty_broken",
    "leader_change", "alliance_formed", "treaty_signed", "election", "ultimatum",
    "disaster",
)


#: The same editorial order, under a name another screen may use. A per-power lede has the
#: identical problem a page lede has -- see :data:`_LEDE_KINDS` -- and the cast list asks it
#: five times over. Exported rather than copied: two editorial orders drift, and the day
#: they do, one screen calls a declaration of war the news and the other calls it the front.
DECISIONS: tuple[str, ...] = _LEDE_KINDS


@dataclass(frozen=True, slots=True)
class Item:
    """One line of the report."""

    text: str
    #: The event behind the line, where there is one. Derived lines -- ground, regard --
    #: carry the event that caused them, so every line in the report is one click from the
    #: inspector.
    event_id: int | None = None
    kind: str = ""
    actor: str = ""
    #: :func:`atlas.state.visibility.salience_of`. Kept so the panel can mark the grave
    #: ones without re-deriving a second opinion about what grave means.
    weight: int = 1


@dataclass(frozen=True, slots=True)
class Section:
    title: str
    items: tuple[Item, ...]
    #: How many more there were than are printed. Reported rather than dropped silently:
    #: a section that quietly truncates reads as a section that was complete.
    more: int = 0


@dataclass(frozen=True, slots=True)
class Dispatch:
    """One turn, told as a page."""

    turn: int
    date: str
    #: The gravest single thing that happened. Empty in a turn where nothing did.
    lede: str = ""
    #: The consolidated history from the ``digest`` table, when this reader has one.
    standing: str = ""
    sections: tuple[Section, ...] = ()
    #: ``(label, value)``, for the strip along the top. Counted world facts, never
    #: telemetry: how many powers are at war is news, how many model calls were made is not.
    tallies: tuple[tuple[str, str], ...] = ()

    @property
    def quiet(self) -> bool:
        return not self.sections

    def lines(self) -> list[str]:
        """The whole page as text. What a test reads."""
        out = [self.lede] if self.lede else []
        for section in self.sections:
            out.append(f"[{section.title}]")
            out.extend(f"  {item.text}" for item in section.items)
            if section.more:
                out.append(f"  (+{section.more} more)")
        return out


def _turn_events(
    conn: sqlite3.Connection, world_id: str, turn: int, viewer: str | None
) -> list[dict]:
    """This turn's events, as the reader is entitled to see them.

    A viewer goes through :func:`atlas.state.visibility.known_events`, which is the
    briefing's own window -- so the page and the briefing cannot disagree about what a
    government was told. Spectating there is no government to scope to, and the referee
    reads the log.
    """
    if viewer:
        from ..state.visibility import known_events

        return [
            event for event in known_events(
                conn, viewer, since_turn=turn, limit=_WINDOW)
            if int(event["turn"]) == turn
        ]
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT id, turn, date, kind, text, actor_nation_id, visibility
              FROM event WHERE world_id = ? AND turn = ?
             ORDER BY id LIMIT ?
            """,
            (world_id, turn, _WINDOW),
        )
    ]


def _names(conn: sqlite3.Connection, world_id: str, ids: set[str]) -> dict[str, str]:
    """Nation ids as a reader would say them. One query, never one per row."""
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    return {
        str(row["id"]): str(row["name"])
        for row in conn.execute(
            f"SELECT id, name FROM nation WHERE world_id = ? AND id IN ({marks})",  # noqa: S608
            (world_id, *sorted(ids)),
        )
    }


def _regard(
    conn: sqlite3.Connection, world_id: str, turn: int, viewer: str | None
) -> list[Item]:
    """Who moved against whom, from the turn's ``relation.score`` diffs.

    Not an event kind -- regard moves without anybody announcing it -- so it would never
    appear in a report assembled from the log alone, and it is exactly the thing a foreign
    ministry reads a situation report to find out.

    **Gated through the event each shift cites**, not through the diff's own scope: a shift
    caused by something the reader may not see is a shift the reader may not see. Rule
    INFO-002 already forbids a covert cause from producing overt state, so the two agree by
    construction -- but agreeing by construction is not the same as being checked, and the
    cause is the thing this panel is about.
    """
    rows = conn.execute(
        """
        SELECT d.target_id AS pair, d.old_json AS before, d.new_json AS after,
               d.reason_event_id AS cause
          FROM diff d JOIN diff_batch b ON b.id = d.batch_id
         WHERE b.world_id = ? AND b.turn = ? AND d.status = 'applied'
           AND d.target_table = 'relation' AND d.field = 'score'
        """,
        (world_id, turn),
    ).fetchall()
    if not rows:
        return []

    # `a|b` and `b|a` are the two halves of one relationship and the engine writes both.
    # Printing each twice would be the same news under two names.
    moves: dict[tuple[str, str], tuple[float, int | None]] = {}
    for row in rows:
        pair = str(row["pair"]).split("|")
        if len(pair) != 2:
            continue
        try:
            moved = float(json.loads(row["after"]) or 0) - float(json.loads(row["before"]) or 0)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if abs(moved) < _RELATION_FLOOR:
            continue
        key = (pair[0], pair[1]) if pair[0] < pair[1] else (pair[1], pair[0])
        cause = int(row["cause"]) if row["cause"] is not None else None
        if key not in moves or abs(moved) > abs(moves[key][0]):
            moves[key] = (moved, cause)
    if not moves:
        return []

    allowed = visible_event_ids(
        conn, world_id, {cause for _m, cause in moves.values() if cause is not None}, viewer)
    named = _names(conn, world_id, {side for key in moves for side in key})

    items: list[Item] = []
    for (left, right), (moved, cause) in moves.items():
        if cause is not None and cause not in allowed:
            continue
        verb = "warmed toward" if moved > 0 else "cooled toward"
        items.append(Item(
            text=f"{named.get(left, left)} {verb} {named.get(right, right)} "
                 f"({moved:+.0f}).",
            event_id=cause, kind="relation", weight=2 if abs(moved) >= 8 else 1))
    return sorted(items, key=lambda item: -item.weight)


def _ground(
    conn: sqlite3.Connection, world_id: str, turn: int, viewer: str | None
) -> list[Item]:
    """Ground that changed hands, from the turn's ``region`` diffs.

    Owner and controller are told apart, because they are different news: an annexation is
    permanent and an occupation is a front line that moved. The map has always drawn the
    difference and the report never said it.
    """
    rows = conn.execute(
        """
        SELECT d.target_id AS region, d.field AS field, d.old_json AS before,
               d.new_json AS after, d.reason_event_id AS cause
          FROM diff d JOIN diff_batch b ON b.id = d.batch_id
         WHERE b.world_id = ? AND b.turn = ? AND d.status = 'applied'
           AND d.target_table = 'region'
           AND d.field IN ('owner_nation_id', 'controller_nation_id')
         ORDER BY d.batch_id, d.seq
        """,
        (world_id, turn),
    ).fetchall()
    if not rows:
        return []

    def unwrap(raw) -> str:
        try:
            value = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return ""
        return str(value) if value else ""

    allowed = visible_event_ids(
        conn, world_id,
        {int(r["cause"]) for r in rows if r["cause"] is not None}, viewer)

    region_ids = {str(r["region"]) for r in rows}
    marks = ",".join("?" * len(region_ids))
    regions = {
        str(row["id"]): str(row["name"])
        for row in conn.execute(
            f"SELECT id, name FROM region WHERE world_id = ? AND id IN ({marks})",  # noqa: S608
            (world_id, *sorted(region_ids)),
        )
    }
    named = _names(conn, world_id, {
        side for row in rows for side in (unwrap(row["before"]), unwrap(row["after"])) if side
    })

    items: list[Item] = []
    for row in rows:
        cause = int(row["cause"]) if row["cause"] is not None else None
        if cause is not None and cause not in allowed:
            continue
        before, after = unwrap(row["before"]), unwrap(row["after"])
        if before == after:
            continue
        where = regions.get(str(row["region"]), str(row["region"]))
        taker = named.get(after, after) or "nobody"
        loser = named.get(before, before) or "nobody"
        if str(row["field"]) == "owner_nation_id":
            text = f"{where} passes from {loser} to {taker}."
            weight = 3
        else:
            text = (f"{where} is occupied by {taker}." if after
                    else f"{where} is no longer occupied.")
            weight = 2
        items.append(Item(text=text, event_id=cause, kind=str(row["field"]),
                          weight=weight))
    return items


def _tallies(conn: sqlite3.Connection, world_id: str, turn: int) -> tuple[tuple[str, str], ...]:
    """The strip along the top. World facts, counted once each."""
    wars = int(conn.execute(
        "SELECT COUNT(*) FROM relation WHERE world_id = ? AND at_war = 1", (world_id,)
    ).fetchone()[0]) // 2
    fighting = int(conn.execute(
        "SELECT COUNT(*) FROM event WHERE world_id = ? AND turn = ? AND kind IN "
        "('battle','siege')", (world_id, turn)).fetchone()[0])
    occupied = int(conn.execute(
        "SELECT COUNT(*) FROM region WHERE world_id = ? AND controller_nation_id IS NOT NULL"
        "   AND controller_nation_id <> owner_nation_id", (world_id,)).fetchone()[0])
    return (
        ("wars under way", str(wars)),
        ("engagements this turn", str(fighting)),
        ("provinces under occupation", str(occupied)),
    )


def dispatch_for(
    conn: sqlite3.Connection,
    world_id: str,
    turn: int,
    *,
    viewer: str | None = None,
) -> Dispatch:
    """Assemble one turn's situation report, as this reader is entitled to see it."""
    from .digest import digest_for

    events = _turn_events(conn, world_id, turn, viewer)

    # The turn's own end date, which is the date the page is written on. The events of a
    # turn carry a mix of its start and its end, so reading one of them would date the
    # report by whichever event happened to come back first.
    row = conn.execute(
        "SELECT end_date, start_date FROM turn WHERE world_id = ? AND number = ?",
        (world_id, turn)).fetchone()
    date = str((row["end_date"] or row["start_date"]) if row else "")
    if not date and events:
        date = str(events[0].get("date") or "")

    ranked = by_salience([dict(event) for event in events])
    lede = ""
    for kind in _LEDE_KINDS:
        lede = next((str(event.get("text") or "") for event in ranked
                     if str(event.get("kind") or "") == kind), "")
        if lede:
            break
    if not lede:
        # Nothing on the editorial list happened. The gravest thing that did still leads,
        # and if that is a battle in a war already running, then that is the month.
        lede = next((str(event.get("text") or "") for event in ranked
                     if salience_of(event) >= 2), "")

    buckets: dict[str, list[Item]] = {}
    for event in ranked:
        kind = str(event.get("kind") or "")
        if kind == "genesis":
            continue
        title = _SECTION.get(kind, _OTHER)
        text = str(event.get("text") or "").strip()
        if not text:
            continue
        buckets.setdefault(title, []).append(Item(
            text=text,
            event_id=int(event["id"]),
            kind=kind,
            actor=str(event.get("actor_nation_id") or ""),
            weight=salience_of(event),
        ))

    for title, derived in (("Who moved against whom", _regard(conn, world_id, turn, viewer)),
                           ("Ground that changed hands", _ground(conn, world_id, turn, viewer))):
        if derived:
            buckets.setdefault(title, []).extend(derived)

    sections: list[Section] = []
    for title in _ORDER:
        items = buckets.get(title)
        if not items:
            continue
        cap = _DENSE_SHOWN if title in _DENSE else _SHOWN
        # Gravest first inside the section too, so a truncated section keeps the worst of
        # what it holds rather than whatever the engine wrote first.
        items = sorted(items, key=lambda item: -item.weight)
        sections.append(Section(
            title=title, items=tuple(items[:cap]), more=max(0, len(items) - cap)))

    return Dispatch(
        turn=turn,
        date=date,
        lede=lede,
        standing=digest_for(conn, world_id, nation_id=viewer),
        sections=tuple(sections),
        tallies=_tallies(conn, world_id, turn),
    )
