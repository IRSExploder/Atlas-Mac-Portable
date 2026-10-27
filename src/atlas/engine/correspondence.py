"""What the powers said, in their own words, to the government that was told it.

This is the best writing in the program and it has never had a screen. A turn of the Modern
Day map produces four or five model-written statements -- *Ukraine calls publicly on France,
Germany, the United Kingdom and the United States to sustain urgent deliveries of air-defence
interceptors*; *China tells Russia privately that its partnership remains intact* -- and they
go into the same left-rail scroll as six hundred air-superiority lines and two hundred
completed workshops, in write order, one line high. Fourteen turns of recorded play hold
sixty-nine of them and no way at all to read them as a correspondence.

So this pulls the traffic out: everything one government said or signed toward another, in
the order it arrived, with a sender, a date and a scope. An inbox rather than a log.

**A letter needs a sender.** The filter is by kind *and* by ``actor_nation_id IS NOT NULL``,
which is not a tidiness check -- it is what tells a treaty from the bookkeeping that shares
its kind. The economy writes fourteen ``trade_agreement`` events a game reading *"Cargoes
move, and the furnaces burn what arrives"*, with no actor because no government did it. A
feed that carried those would be a feed with the tick back in it.

**Origin is not the filter, and deliberately not.** ``origin`` marks whether the applier or a
model wrote the row, and it exists to gate privileged mutations -- not to mark prose. Half of
Modern Day's opening dispatches are ENGINE rows written in character (*"Washington's allies
have begun planning for an America that does not show up"*), and a declaration of war is an
ENGINE row by construction. Filtering on it would drop the scenario's own voice and keep
whatever a model happened to file as routine.

**Scoped to the reader, through the predicate the dispatch and the trace use.** Candidates go
through :func:`atlas.engine.causality.visible_event_ids`, so the China-Russia channel that
runs through this world under ``bilateral`` is four letters Washington does not have. That is
the whole point of a diplomatic feed: an inbox that showed you everyone's mail would not be
an inbox.

**Who a letter was addressed to is only named where the reader is entitled to the list.** A
government that receives a démarche knows it received one; it does not thereby know which
other capitals got the same paper. So the addressee list is filled in for the sender -- who
chose it -- and for the referee, and for nobody else, who are told instead that it came to
them in confidence.

Read-only from end to end. Opening the feed cannot change a save.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .causality import visible_event_ids

__all__ = [
    "Correspondence",
    "Letter",
    "correspondence_for",
]

#: What counts as correspondence: a thing one government said, signed, or broke toward
#: another. **Not everything in-character** -- a ``narrative`` is a report of what a country
#: did, not a thing it said to anybody, and a feed carrying both stops being an inbox and
#: becomes the dispatch with the headings taken off. The dispatch is the screen for *what
#: happened*; this one is for *what was said*.
_KINDS: dict[str, str] = {
    "diplomatic_message": "Note",
    "envoy_dispatched": "Envoy",
    "ultimatum": "Ultimatum",
    "casus_belli": "Grievance",
    "war_declared": "Declaration of war",
    "peace_signed": "Peace",
    "ceasefire": "Ceasefire",
    "alliance_formed": "Alliance",
    "alliance_broken": "Alliance renounced",
    "treaty_signed": "Treaty",
    "treaty_broken": "Treaty broken",
    "trade_agreement": "Trade agreement",
    "commitment_made": "Undertaking",
    "commitment_broken": "Undertaking broken",
    "commitment_fulfilled": "Undertaking kept",
}

#: Subjects that are a rupture rather than a message. The panel sets these apart, because an
#: inbox in which a declaration of war is typographically a trade note is an inbox that has
#: lost the plot.
_GRAVE = frozenset({
    "war_declared", "ultimatum", "alliance_broken", "treaty_broken",
    "commitment_broken", "casus_belli",
})

#: How many letters the feed carries. A reader scrolls a correspondence; they do not page
#: through one, and fourteen turns of the recorded Modern Day world produce sixty-nine.
_LETTERS = 150

#: What the roles on ``event_participant`` mean by "this was aimed at you". ``mediator`` is
#: not among them: a mediator is in the room, not addressed.
_ADDRESSED_ROLES = ("target", "defender", "aggrieved", "beneficiary")


@dataclass(frozen=True, slots=True)
class Letter:
    """One piece of correspondence, as one reader is entitled to have it."""

    event_id: int
    turn: int
    date: str
    sender_id: str
    sender: str
    colour: str
    kind: str
    #: The kind as a subject line a reader recognises. See :data:`_KINDS`.
    subject: str
    text: str
    #: ``public``, ``bilateral`` or ``secret``, verbatim from the event.
    scope: str
    to_you: bool = False
    from_you: bool = False
    #: Who it was addressed to, where the reader is entitled to the list. Empty otherwise --
    #: which is not the same as "addressed to nobody", and the panel must not say it is.
    addressed: tuple[str, ...] = ()

    @property
    def grave(self) -> bool:
        return self.kind in _GRAVE

    @property
    def confidential(self) -> bool:
        return self.scope != "public"


@dataclass(frozen=True, slots=True)
class Correspondence:
    """The traffic, newest first."""

    #: The reader. Empty for the referee, who is not a government and has no inbox -- the
    #: feed then carries everything and nothing is addressed to anyone.
    viewer: str
    letters: tuple[Letter, ...] = ()
    #: Whether older correspondence exists beyond what is carried. Reported rather than
    #: dropped silently: a feed that quietly truncates reads as a feed that was complete.
    truncated: bool = False

    @property
    def empty(self) -> bool:
        return not self.letters

    @property
    def to_you(self) -> int:
        return sum(1 for letter in self.letters if letter.to_you)

    @property
    def reach(self) -> int:
        """The oldest turn the feed reaches back to."""
        return min((letter.turn for letter in self.letters), default=0)

    def lines(self) -> list[str]:
        """The whole feed as text. What a test reads."""
        return [f"{letter.sender}: {letter.text}" for letter in self.letters]


def _addressees(
    conn: sqlite3.Connection, event_ids: list[int]
) -> dict[int, tuple[tuple[str, str], ...]]:
    """``event -> the (id, name) pairs it names as recipients``. One query, never one per
    letter.

    Read from ``event_participant`` rather than from ``event_visibility``: the participant
    table is the engine's own structured statement of *who this was aimed at*, while a
    visibility row only says who was told, which is a wider and softer thing. Where the
    engine wrote no participants the answer is nothing, and the panel says nothing rather
    than guessing from the visibility list.

    Ids as well as names, so the *are you one of them* test is an id comparison. Matching on
    the name would need the reader's own name, which is one more lookup per letter -- an
    N+1 hidden inside a membership test.
    """
    if not event_ids:
        return {}
    marks = ",".join("?" * len(event_ids))
    roles = ",".join("?" * len(_ADDRESSED_ROLES))
    found: dict[int, list[tuple[str, str]]] = {}
    for row in conn.execute(
        f"""
        SELECT p.event_id AS event_id, n.id AS id, n.name AS name
          FROM event_participant p JOIN nation n ON n.id = p.nation_id
         WHERE p.event_id IN ({marks}) AND p.role IN ({roles})
         ORDER BY p.event_id, p.seq
        """,  # noqa: S608 - placeholders only
        (*event_ids, *_ADDRESSED_ROLES),
    ):
        found.setdefault(int(row["event_id"]), []).append(
            (str(row["id"]), str(row["name"])))
    return {event_id: tuple(pairs) for event_id, pairs in found.items()}


def _told(conn: sqlite3.Connection, event_ids: list[int]) -> dict[int, set[str]]:
    """``event -> the nations with an explicit entitlement to it``. One query."""
    if not event_ids:
        return {}
    marks = ",".join("?" * len(event_ids))
    found: dict[int, set[str]] = {}
    for row in conn.execute(
        f"SELECT event_id, nation_id FROM event_visibility WHERE event_id IN ({marks})",  # noqa: S608
        tuple(event_ids),
    ):
        found.setdefault(int(row["event_id"]), set()).add(str(row["nation_id"]))
    return found


def correspondence_for(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    viewer: str | None = None,
    limit: int = _LETTERS,
) -> Correspondence:
    """The diplomatic traffic this reader is entitled to, newest first.

    ``viewer=None`` is the referee rather than a nation -- whoever holds the save. There is
    then no inbox, because there is no government for anything to be addressed to, and the
    feed carries the whole correspondence of the world.
    """
    kinds = sorted(_KINDS)
    marks = ",".join("?" * len(kinds))
    # **Fetched wide, cut narrow.** The entitlement filter runs after the window, so a cap
    # applied at the window would hand a reader who may not see the China-Russia channel a
    # shorter feed than one who may -- the same argument `visibility._ranked_window` makes
    # about ranking after fetching. One extra row past the widened cap is what tells a full
    # feed from a truncated one, without a second counting query.
    rows = conn.execute(
        f"""
        SELECT e.id, e.turn, e.date, e.kind, e.text, e.actor_nation_id, e.visibility
          FROM event e
         WHERE e.world_id = ? AND e.kind IN ({marks})
           AND e.actor_nation_id IS NOT NULL AND TRIM(e.text) <> ''
         ORDER BY e.turn DESC, e.id DESC
         LIMIT ?
        """,  # noqa: S608 - placeholders only
        (world_id, *kinds, limit * 2 + 1),
    ).fetchall()
    if not rows:
        return Correspondence(viewer=viewer or "")

    allowed = visible_event_ids(conn, world_id, {int(r["id"]) for r in rows}, viewer)
    kept = [row for row in rows if int(row["id"]) in allowed]
    truncated = len(kept) > limit
    kept = kept[:limit]
    if not kept:
        return Correspondence(viewer=viewer or "")

    senders = {str(row["actor_nation_id"]) for row in kept}
    sender_marks = ",".join("?" * len(senders))
    named = {
        str(row["id"]): (str(row["name"]), str(row["colour"] or "#888888"))
        for row in conn.execute(
            f"SELECT id, name, colour FROM nation WHERE world_id = ? "  # noqa: S608
            f"AND id IN ({sender_marks})",
            (world_id, *sorted(senders)),
        )
    }
    ids = [int(row["id"]) for row in kept]
    addressees = _addressees(conn, ids)
    told = _told(conn, [int(row["id"]) for row in kept
                        if str(row["visibility"]) != "public"])

    letters: list[Letter] = []
    for row in kept:
        event_id = int(row["id"])
        sender_id = str(row["actor_nation_id"])
        scope = str(row["visibility"])
        from_you = bool(viewer) and sender_id == viewer
        # Addressed to you when the engine says so, or -- for anything not public -- when
        # the only reason you are holding it is that somebody put your name on it.
        to_you = bool(viewer) and not from_you and (
            viewer in told.get(event_id, set())
            or any(who == viewer for who, _name in addressees.get(event_id, ()))
        )
        letters.append(Letter(
            event_id=event_id,
            turn=int(row["turn"]),
            date=str(row["date"] or ""),
            sender_id=sender_id,
            sender=named.get(sender_id, (sender_id, "#888888"))[0],
            colour=named.get(sender_id, (sender_id, "#888888"))[1],
            kind=str(row["kind"]),
            subject=_KINDS.get(str(row["kind"]), "Dispatch"),
            text=str(row["text"] or "").strip(),
            scope=scope,
            to_you=to_you,
            from_you=from_you,
            # The sender chose the list and the referee holds the file; a third party who
            # merely received a copy did not, and is told only that it came to them.
            addressed=tuple(name for _who, name in addressees.get(event_id, ()))
            if (from_you or not viewer) else (),
        ))
    return Correspondence(
        viewer=viewer or "", letters=tuple(letters), truncated=truncated)
