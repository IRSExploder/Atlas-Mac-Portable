"""Why a number moved, and what became of an order.

The detailed event log has always been complete and has never been an explanation. A player
reading *economy 28.2 -> 31.3* had to reconstruct which of revenue, development, wartime
disruption and a completed public work was responsible, from a list of events in the order
the engine happened to write them.

Both halves of this module are **derived, never stored**, for the reason the scoring layer
is: the diff log already records what moved and which event caused it, so a second copy
could only disagree with it after a rewind.

**The causal breakdown is free because `diff.reason_event_id` already exists.** Every
engine-authored change cites the event that caused it -- that is rule CORE-001, enforced
since schema 1 -- so grouping a turn's diffs by their cited event turns the audit trail into
a sentence without adding a single column.

**An order's fate needs a correlation id**, because an order is a *sentence* and the things
that happen to it are spread across the parser, the handler and the resolver. The id is
assigned when the order is parsed and travels with it, so a refusal three layers down can
still be reported against the words the player typed.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

__all__ = [
    "CausalLink",
    "CausalStrand",
    "CausalTrace",
    "Change",
    "ChangeGroup",
    "Contribution",
    "EventInspection",
    "EventRef",
    "OrderOutcome",
    "UNEXPLAINED",
    "Refusal",
    "audience_of",
    "breakdown_of",
    "explain_change",
    "inspect_event",
    "recent_events",
    "latest_move_turn",
    "trace_of",
    "trace_of_event",
    "visible_event_ids",
]


@dataclass(frozen=True, slots=True)
class Refusal:
    """Why something was turned down, and what to write instead.

    **A refusal that only says no is a refusal the staff will make again next turn**, and
    the recorded runs are full of exactly that: an air strike aimed past its combat radius,
    re-proposed every turn for six turns, against a message that said "nothing you own can
    reach that region" and named nothing that could. The proposer had every reason to think
    the answer was to try harder.

    Three parts, and the third is the one that changes behaviour. ``requirement`` is the
    gate that failed, in the engine's own vocabulary, so a reader can tell two refusals
    apart without parsing prose. ``because`` is the sentence. ``instead`` is the legal
    equivalent **where one exists** -- and it is empty rather than invented when it does
    not, because a suggestion the engine would also refuse is worse than silence.

    Renders to one line, and stringifies to it, so every caller that already treats a
    refusal as text keeps working unchanged.
    """

    requirement: str
    because: str
    instead: str = ""

    def rendered(self) -> str:
        line = f"{self.because} [{self.requirement}]"
        return f"{line} Instead: {self.instead}" if self.instead else line

    def __str__(self) -> str:  # pragma: no cover - trivial delegation
        return self.rendered()


@dataclass(frozen=True, slots=True)
class Contribution:
    """One reason a field moved, and by how much."""

    amount: float
    #: The event's own words. Not a category: the engine already writes a sentence for
    #: every change it makes, and inventing a taxonomy on top would be a second thing to
    #: keep in step with the first.
    because: str

    def rendered(self) -> str:
        return f"{self.amount:+.1f} {self.because}"


@dataclass(slots=True)
class OrderOutcome:
    """What became of one order, tied to the words that produced it."""

    #: Stable within a turn, and assigned where the order is parsed, so a refusal deep in
    #: a handler still reports against what the player typed.
    correlation_id: str
    order_text: str = ""
    action_type: str = ""
    #: ``attempted`` | ``refused`` | ``not_attempted``. Three, not two: an order the engine
    #: never reached is a different thing from one it considered and declined, and telling
    #: a player the first was the second sends them to fix a sentence that was fine.
    status: str = "attempted"
    reason: str = ""
    cost: str = ""
    #: What to write instead. The most useful field in the record and the only one that
    #: makes a refusal actionable rather than merely honest.
    suggestion: str = ""

    def rendered(self) -> str:
        parts = [f"{self.order_text or self.action_type}: {self.status}"]
        if self.reason:
            parts.append(self.reason)
        if self.cost:
            parts.append(f"cost {self.cost}")
        if self.suggestion:
            parts.append(f"try: {self.suggestion}")
        return " -- ".join(parts)


@dataclass(slots=True)
class Breakdown:
    """Everything that moved one nation's numbers on one turn."""

    nation_id: str
    turn: int
    fields: dict[str, list[Contribution]] = field(default_factory=dict)

    def lines(self, *, top: int = 3) -> list[str]:
        """One sentence per field that moved, largest cause first."""
        out: list[str] = []
        for name, causes in sorted(self.fields.items()):
            total = sum(c.amount for c in causes)
            if abs(total) < 0.05:
                continue
            ranked = sorted(causes, key=lambda c: -abs(c.amount))[:top]
            out.append(
                f"{name} {total:+.1f}: " + ", ".join(c.rendered() for c in ranked)
            )
        return out


#: The label a cause the reader may not see is filed under. **The number stays and the
#: sentence goes**: a breakdown whose parts stopped summing to the whole would be a second
#: tell, and "stability -3, unexplained" is exactly what a victim of a covert operation
#: honestly knows.
UNEXPLAINED = "unexplained"

#: ``breakdown_of``'s default reader: the nation whose numbers are being explained.
_OWN = object()


def breakdown_of(
    conn: sqlite3.Connection, world_id: str, nation_id: str, turn: int,
    *, viewer: str | None | object = _OWN,
) -> Breakdown:
    """Group a turn's applied diffs for one nation by the event each of them cites.

    The join is the whole trick and it costs nothing: `diff.reason_event_id` has been
    mandatory since rule CORE-001, so every applied change already knows why it happened.

    The ``LEFT`` in the join is not defensiveness about missing citations -- CORE-001 makes
    those impossible, and a test that tried to write one was refused by the rule. It is
    about **missing events**: a rewind deletes the events of the turns it undoes while the
    diff rows survive, so asking for a breakdown of a rewound turn finds citations pointing
    at nothing. Those still count, under a plain label, because dropping them would make
    the parts stop summing to the whole and a breakdown that does not add up is worse than
    none.

    **Scoped to a reader, and by default to the nation itself.** A covert operation against
    this nation moves its numbers through diffs that cite a SECRET event, and joining those
    to the event's text printed the operation, author and all, to its victim -- the leak
    rule INFO-002 exists to prevent. A cause the reader may not see (by the one predicate,
    :func:`visible_event_ids`) keeps its amount and loses its words, filed under
    :data:`UNEXPLAINED`. ``viewer=None`` is the referee and sees everything.
    """
    reader: str | None = nation_id if viewer is _OWN else viewer  # type: ignore[assignment]
    rows = conn.execute(
        """
        SELECT d.field AS field, d.old_json AS old_json, d.new_json AS new_json,
               d.reason_event_id AS event_id, e.text AS because
          FROM diff d
          JOIN diff_batch b ON b.id = d.batch_id
          LEFT JOIN event e ON e.id = d.reason_event_id
         WHERE b.world_id = ? AND b.turn = ? AND d.status = 'applied'
           AND d.target_table = 'nation' AND d.target_id = ?
         ORDER BY d.batch_id, d.seq
        """,
        (world_id, turn, nation_id),
    ).fetchall()

    cited = {int(row["event_id"]) for row in rows
             if row["event_id"] is not None and row["because"] is not None}
    seen = visible_event_ids(conn, world_id, cited, reader)

    merged: dict[str, dict[str, float]] = {}
    for row in rows:
        try:
            before = float(json.loads(row["old_json"]) or 0.0)
            after = float(json.loads(row["new_json"]) or 0.0)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        moved = after - before
        if abs(moved) < 1e-9:
            continue
        if row["because"] is None:
            because = "no reason recorded"
        elif int(row["event_id"]) not in seen:
            because = UNEXPLAINED
        else:
            because = str(row["because"] or "no reason recorded")
        per_field = merged.setdefault(str(row["field"]), {})
        per_field[because] = per_field.get(because, 0.0) + moved

    return Breakdown(
        nation_id=nation_id,
        turn=turn,
        fields={
            name: [Contribution(amount=amount, because=because)
                   for because, amount in causes.items()]
            for name, causes in merged.items()
        },
    )


def explain_change(
    conn: sqlite3.Connection, world_id: str, nation_id: str, turn: int, field_name: str,
    *, viewer: str | None | object = _OWN,
) -> str:
    """One sentence for one field. ``""`` when it did not move.

    The shape a player reads: *economy 28.2 -> 31.3: +4.2 revenue, -1.1 wartime
    disruption*. The endpoints come from the diffs rather than from the live row, so the
    sentence describes the turn rather than the moment it is asked.
    """
    rows = conn.execute(
        """
        SELECT d.old_json AS old_json, d.new_json AS new_json
          FROM diff d
          JOIN diff_batch b ON b.id = d.batch_id
         WHERE b.world_id = ? AND b.turn = ? AND d.status = 'applied'
           AND d.target_table = 'nation' AND d.target_id = ? AND d.field = ?
         ORDER BY d.batch_id, d.seq
        """,
        (world_id, turn, nation_id, field_name),
    ).fetchall()
    if not rows:
        return ""
    try:
        started = float(json.loads(rows[0]["old_json"]) or 0.0)
        ended = float(json.loads(rows[-1]["new_json"]) or 0.0)
    except (TypeError, ValueError, json.JSONDecodeError):
        return ""

    causes = breakdown_of(
        conn, world_id, nation_id, turn, viewer=viewer).fields.get(field_name, [])
    if not causes:
        return ""
    ranked = sorted(causes, key=lambda c: -abs(c.amount))[:3]
    return (
        f"{field_name} {started:.1f} -> {ended:.1f}: "
        + ", ".join(c.rendered() for c in ranked)
    )


# ---------------------------------------------------------------------------
# One event, from both directions
# ---------------------------------------------------------------------------
#
# `breakdown_of` above pivots the same join the other way: it asks what moved *this
# nation's* numbers this turn and groups by the event responsible. This asks what *one
# event* did, and walks the chain it sits in. Same join, same guarantee behind it -- rule
# CORE-001 means every applied diff cites its cause -- and deliberately in the same module,
# so there is one place that knows how a change is tied to its reason.


#: How a ``(table, field)`` is titled for a reader. **Grouping, not translation**: the field
#: name is still shown on every row, and this only decides which heading it sits under, so a
#: field missing from the table costs a reader nothing but a generic heading.
_GROUPS: dict[tuple[str, str], str] = {
    ("region", "owner_nation_id"): "Territory changed hands",
    ("region", "home_nation_id"): "Territory changed hands",
    ("region", "controller_nation_id"): "Occupation",
    ("region", "development"): "Ground",
    ("region", "population"): "Ground",
    ("relation", "at_war"): "War and peace",
    ("relation", "score"): "Regard between powers",
    ("relation", "treaty_status"): "Agreements",
    ("unit", "strength"): "Forces",
    ("unit", "morale"): "Forces",
    ("nation", "stability"): "National condition",
    ("nation", "legitimacy"): "National condition",
    ("nation", "treasury"): "National condition",
    ("nation", "economy"): "National condition",
    ("nation", "industry"): "National condition",
    ("nation", "manpower"): "National condition",
    ("nation", "military_strength"): "National condition",
    ("nation", "is_alive"): "Sovereignty",
    ("arsenal", "warheads"): "Nuclear arsenals",
    ("commitment", "status"): "Agreements",
    ("construction", "__delete__"): "Construction",
    ("independence", "momentum"): "Colonial movements",
    ("support", "level"): "Proxy support",
}

#: Anything the table above does not name.
_OTHER = "Other changes"

#: The order groups are shown in: what a reader looks for first. Territory before numbers,
#: because a province changing hands is the thing a player wants to see at a glance.
_GROUP_ORDER = (
    "Sovereignty", "Territory changed hands", "Occupation", "War and peace",
    "Agreements", "Forces", "Nuclear arsenals", "Regard between powers",
    "National condition", "Ground", "Construction", "Colonial movements",
    "Proxy support", _OTHER,
)


@dataclass(frozen=True, slots=True)
class Change:
    """One state change an event caused."""

    table: str
    target: str
    field: str
    before: str
    after: str
    #: ``applied`` or ``rejected``. Refused writes are kept and marked rather than dropped:
    #: an event whose changes were all refused is a thing that looked like it happened and
    #: did not, which is exactly what a reader needs to be told.
    status: str = "applied"

    def rendered(self) -> str:
        return f"{self.field}: {self.before} -> {self.after}"


@dataclass(frozen=True, slots=True)
class ChangeGroup:
    title: str
    changes: tuple[Change, ...]


@dataclass(frozen=True, slots=True)
class EventRef:
    """Enough of an event to render one clickable line."""

    event_id: int
    turn: int
    kind: str
    actor: str
    text: str


@dataclass(frozen=True, slots=True)
class EventInspection:
    """One event, what led to it, what it did, and who wrote it."""

    event: EventRef
    date: str
    origin: str
    #: Upstream: the events recorded as having caused this one.
    causes: tuple[EventRef, ...] = ()
    #: Downstream: the events that name this one as their cause.
    consequences: tuple[EventRef, ...] = ()
    groups: tuple[ChangeGroup, ...] = ()
    #: ``(region_id, name)``, derived from the diffs rather than from
    #: ``event_participant``. See :func:`inspect_event`.
    regions: tuple[tuple[str, str], ...] = ()
    author_role: str = ""
    author_model: str = ""

    @property
    def change_count(self) -> int:
        return sum(len(group.changes) for group in self.groups)


def _unwrap(raw) -> str:
    """A diff's stored JSON as something short enough to put in a column."""
    if raw is None:
        return "—"
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return str(raw)
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _refs(conn: sqlite3.Connection, world_id: str, ids: list[int]) -> tuple[EventRef, ...]:
    if not ids:
        return ()
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"""
        SELECT id, turn, kind, COALESCE(actor_nation_id, '') AS actor, text
          FROM event WHERE world_id = ? AND id IN ({marks}) ORDER BY turn, id
        """,  # noqa: S608 - placeholders only
        (world_id, *ids),
    ).fetchall()
    return tuple(
        EventRef(event_id=int(r["id"]), turn=int(r["turn"]), kind=str(r["kind"]),
                 actor=str(r["actor"]), text=str(r["text"] or ""))
        for r in rows
    )


def recent_events(
    conn: sqlite3.Connection, world_id: str, *, limit: int = 60, turn: int | None = None
) -> list[EventRef]:
    """The most recent events, newest first. What an inspector opens on."""
    sql = "SELECT id, turn, kind, COALESCE(actor_nation_id, '') AS actor, text FROM event WHERE world_id = ?"
    args: list = [world_id]
    if turn is not None:
        sql += " AND turn = ?"
        args.append(turn)
    sql += " ORDER BY turn DESC, id DESC LIMIT ?"
    args.append(limit)
    return [
        EventRef(event_id=int(r["id"]), turn=int(r["turn"]), kind=str(r["kind"]),
                 actor=str(r["actor"]), text=str(r["text"] or ""))
        for r in conn.execute(sql, args)
    ]


def inspect_event(
    conn: sqlite3.Connection, world_id: str, event_id: int
) -> EventInspection | None:
    """Everything the log knows about one event. ``None`` if there is no such event.

    Four questions, all answered from state that already exists:

    * **what caused it** and **what it led to** -- ``event_cause``, read in both directions,
      so the chain is walkable either way;
    * **what it changed** -- the diffs citing it, grouped for reading. Refused diffs are
      kept and marked, because an event whose every change was refused is a thing that
      looked like it happened and did not;
    * **which regions it touched** -- **derived from the diffs, not from
      ``event_participant``.** The participant table carries a region on 3 of 716 events in
      a real recorded world, so an inspector trusting it would show almost nothing; the
      diffs name their target directly, and a diff against a *unit* is resolved through the
      unit's province, which is what makes a battle show where it was fought;
    * **who wrote it** -- the batch's ``proposer_role`` and ``proposer_model``, so engine
      and model authorship are told apart rather than both reading as "the game did it".

    Read-only. Nothing here writes, so opening an inspector cannot change a save.
    """
    row = conn.execute(
        """
        SELECT id, turn, date, kind, origin, COALESCE(actor_nation_id, '') AS actor, text
          FROM event WHERE world_id = ? AND id = ?
        """,
        (world_id, event_id),
    ).fetchone()
    if row is None:
        return None

    causes = _refs(conn, world_id, [
        int(r["cause_id"]) for r in conn.execute(
            "SELECT cause_id FROM event_cause WHERE event_id = ?", (event_id,))])
    consequences = _refs(conn, world_id, [
        int(r["event_id"]) for r in conn.execute(
            "SELECT event_id FROM event_cause WHERE cause_id = ?", (event_id,))])

    diffs = conn.execute(
        """
        SELECT d.target_table AS tbl, d.target_id AS target, d.field AS field,
               d.old_json AS before, d.new_json AS after, d.status AS status,
               b.proposer_role AS role, COALESCE(b.proposer_model, '') AS model
          FROM diff d JOIN diff_batch b ON b.id = d.batch_id
         WHERE d.reason_event_id = ? AND b.world_id = ?
         ORDER BY d.batch_id, d.seq
        """,
        (event_id, world_id),
    ).fetchall()

    buckets: dict[str, list[Change]] = {}
    role = model = ""
    for diff in diffs:
        role = role or str(diff["role"] or "")
        model = model or str(diff["model"] or "")
        title = _GROUPS.get((str(diff["tbl"]), str(diff["field"])), _OTHER)
        buckets.setdefault(title, []).append(Change(
            table=str(diff["tbl"]), target=str(diff["target"]), field=str(diff["field"]),
            before=_unwrap(diff["before"]), after=_unwrap(diff["after"]),
            status=str(diff["status"] or "applied"),
        ))

    groups = tuple(
        ChangeGroup(title=title, changes=tuple(buckets[title]))
        for title in _GROUP_ORDER if title in buckets
    )

    # Regions, from the diffs. A region diff names its province; a unit diff names a unit,
    # and the province is where that unit is -- which is how a battle gets a location.
    touched = {
        str(d["target"]) for d in diffs if str(d["tbl"]) == "region"
    }
    unit_ids = [str(d["target"]) for d in diffs if str(d["tbl"]) == "unit"]
    if unit_ids:
        marks = ",".join("?" * len(unit_ids))
        touched.update(
            str(r["region_id"]) for r in conn.execute(
                f"SELECT DISTINCT region_id FROM unit WHERE id IN ({marks})"  # noqa: S608
                "   AND region_id IS NOT NULL",
                unit_ids)
        )
    regions: tuple[tuple[str, str], ...] = ()
    if touched:
        marks = ",".join("?" * len(touched))
        regions = tuple(
            (str(r["id"]), str(r["name"])) for r in conn.execute(
                f"SELECT id, name FROM region WHERE world_id = ? AND id IN ({marks})"  # noqa: S608
                " ORDER BY name",
                (world_id, *sorted(touched)))
        )

    return EventInspection(
        event=EventRef(event_id=int(row["id"]), turn=int(row["turn"]),
                       kind=str(row["kind"]), actor=str(row["actor"]),
                       text=str(row["text"] or "")),
        date=str(row["date"] or ""),
        origin=str(row["origin"] or ""),
        causes=causes,
        consequences=consequences,
        groups=groups,
        regions=regions,
        author_role=role,
        author_model=model,
    )


# ---------------------------------------------------------------------------
# The chain behind one number
# ---------------------------------------------------------------------------
#
# `breakdown_of` says *what* moved a field and by how much; `inspect_event` says what one
# event did and names its immediate neighbours. Neither answers the question a player
# actually asks, which is **why is this number what it is** -- and the answer to that is a
# walk, not a lookup: the stability fell because of unrest, and the unrest is there because
# of an annexation four turns ago.
#
# The walk is free for the same reason the breakdown is. `diff.reason_event_id` names the
# event, `event_cause` names that event's causes, and following the second from the first is
# the whole mechanism. Nothing here is stored and nothing here is computed twice.
#
# **What is new is the visibility gate.** The inspector was written for a reader who is
# allowed to see everything, and it is: it is opened from the map by whoever holds the save.
# A chain shown *to a nation* cannot be, because a covert cause is exactly the sort of thing
# a chain leads back to -- an attribution, a sabotage, a secret clause. So the walk takes a
# viewer, and where the trail runs into something that viewer is not entitled to see it
# stops and says so, rather than either printing the secret or pretending the chain ended.

#: How far back the walk goes. Deep enough for the chains the engine actually writes, and
#: bounded because `event_cause` is a graph and a graph walked without a bound is a hang
#: with a nice name.
_DEPTH = 8

#: How many events one strand may name in total, across every depth. A turn's resolution can
#: cite half a dozen causes at one level; a reader wants the shape of the chain, not its
#: transitive closure.
_LINKS = 14


@dataclass(frozen=True, slots=True)
class CausalLink:
    """One step back along a chain."""

    event: EventRef
    #: 0 is the event the change itself cites. 1 is what caused that, and so on.
    depth: int


@dataclass(frozen=True, slots=True)
class CausalStrand:
    """One reason a value moved, and everything recorded behind that reason."""

    #: How far the field moved on this strand. ``None`` when the field is not a number --
    #: an owner changing hands has no magnitude, and inventing one would be a lie in a
    #: column of true things.
    amount: float | None
    #: What the change was, in the field's own terms: ``aurelia -> karthos``.
    detail: str
    links: tuple[CausalLink, ...] = ()
    #: The walk reached an event this viewer is not entitled to see, and stopped there.
    #: **Reported rather than hidden**: a chain that quietly ends at a secret is
    #: indistinguishable from a chain that ended, and telling a player the first was the
    #: second is the one failure mode a causal reader must not have.
    hidden_upstream: bool = False

    def rendered(self) -> str:
        """``-4.0 62 -> 58 <- unrest in occupied Donetsk (t6) <- Donetsk annexed (t2)``."""
        head = f"{self.amount:+.1f} " if self.amount is not None else ""
        chain = " <- ".join(
            f"{link.event.text or link.event.kind} (t{link.event.turn})"
            for link in self.links
        )
        body = " <- ".join(part for part in (self.detail, chain) if part)
        tail = " <- [something you are not privy to]" if self.hidden_upstream else ""
        return f"{head}{body}{tail}"


@dataclass(frozen=True, slots=True)
class CausalTrace:
    """Why one value is what it is, over one turn."""

    #: What is being explained, as a reader would say it: ``United States - stability``.
    title: str
    table: str
    target: str
    field: str
    turn: int
    before: str = "-"
    after: str = "-"
    #: The net move, when the field is numeric.
    moved: float | None = None
    strands: tuple[CausalStrand, ...] = ()
    #: How many strands were dropped entirely because the event they cite is one this
    #: viewer cannot see. Counted so the panel can say that the parts do not sum to the
    #: whole, and why.
    withheld: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.strands) or self.withheld > 0

    def lines(self) -> list[str]:
        """The whole trace as text, gravest strand first. What a test reads."""
        return [strand.rendered() for strand in self.strands]


def visible_event_ids(
    conn: sqlite3.Connection, world_id: str, ids, viewer: str | None
) -> set[int]:
    """Which of ``ids`` this nation is entitled to see. ``viewer=None`` sees everything.

    **The predicate is the briefing's**, deliberately word for word: public to everyone, a
    nation's own acts always to itself, anything else only through an explicit
    ``event_visibility`` row. Two copies of a visibility rule is one copy too many, and the
    day they drift is the day a panel shows a secret the briefing withheld. See
    :func:`atlas.state.visibility.known_events`.

    ``None`` is the referee rather than a nation: the inspector opened from the map belongs
    to whoever holds the save, and gating that would be theatre.
    """
    wanted = [int(i) for i in ids]
    if not wanted:
        return set()
    if viewer is None:
        return set(wanted)
    marks = ",".join("?" * len(wanted))
    return {
        int(row["id"])
        for row in conn.execute(
            f"""
            SELECT e.id FROM event e
             WHERE e.world_id = ? AND e.id IN ({marks})
               AND (
                    e.visibility = 'public'
                 OR e.actor_nation_id = ?
                 OR EXISTS (SELECT 1 FROM event_visibility v
                             WHERE v.event_id = e.id AND v.nation_id = ?)
               )
            """,  # noqa: S608 - placeholders only
            (world_id, *wanted, viewer, viewer),
        )
    }


def audience_of(
    conn: sqlite3.Connection, world_id: str, event_id: int | None
) -> frozenset[str] | None:
    """Who may read one event: ``None`` for everybody, otherwise the nations entitled to.

    :func:`visible_event_ids` asked from the other side, for a caller holding a line of
    text rather than a reader -- the turn report tags what it copies out of an event with
    this, so the event's scope travels with the words. The same three clauses: public to
    everyone, the actor always, anyone else only through an ``event_visibility`` row.

    An event that is not there (``None``, or rewound away) is **nobody's but the referee's**
    -- the empty set, never ``None``: a line whose provenance cannot be checked is not
    thereby public.
    """
    if event_id is None:
        return frozenset()
    row = conn.execute(
        "SELECT visibility, actor_nation_id FROM event WHERE world_id = ? AND id = ?",
        (world_id, int(event_id))).fetchone()
    if row is None:
        return frozenset()
    if str(row["visibility"]) == "public":
        return None
    granted = {
        str(grant["nation_id"]) for grant in conn.execute(
            "SELECT nation_id FROM event_visibility WHERE event_id = ?", (int(event_id),))
    }
    if row["actor_nation_id"]:
        granted.add(str(row["actor_nation_id"]))
    return frozenset(granted)


def _walk_back(
    conn: sqlite3.Connection, world_id: str, root: int, viewer: str | None
) -> tuple[tuple[CausalLink, ...], bool]:
    """Follow ``event_cause`` backwards from one event.

    Returns the chain and whether it was cut short by something the viewer may not see.

    Breadth-first, so the nearest cause is the first thing read. Visited is recorded before
    the queue moves rather than after, because the cause graph is a DAG that converges -- a
    peace and a territory transfer routinely share one ultimatum -- and a walk that does not
    remember where it has been reports that ultimatum four times.
    """
    chain: list[CausalLink] = []
    seen = {root}
    frontier = [root]
    hidden = False
    for depth in range(1, _DEPTH + 1):
        if not frontier or len(chain) >= _LINKS:
            break
        marks = ",".join("?" * len(frontier))
        found = [
            int(row["cause_id"])
            for row in conn.execute(
                f"SELECT DISTINCT cause_id FROM event_cause WHERE event_id IN ({marks})"  # noqa: S608
                " ORDER BY cause_id",
                frontier,
            )
            if int(row["cause_id"]) not in seen
        ]
        if not found:
            break
        seen.update(found)
        allowed = visible_event_ids(conn, world_id, found, viewer)
        if len(allowed) < len(found):
            hidden = True
        keep = [event_id for event_id in found if event_id in allowed][: _LINKS - len(chain)]
        chain.extend(
            CausalLink(event=ref, depth=depth) for ref in _refs(conn, world_id, keep)
        )
        frontier = keep
    return tuple(chain), hidden


def _numeric(raw) -> float | None:
    """A diff's stored JSON as a number, or ``None`` when it is not one."""
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def latest_move_turn(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    table: str,
    target: str,
    field: str,
    upto: int,
) -> int | None:
    """The most recent turn at or before ``upto`` on which this field actually moved.

    **A figure that did not move this turn still has a reason for being where it is**, and
    "nothing happened" is a worse answer than "nothing since April" -- a stability of 51 that
    has not shifted in three turns was put there by something, and that something is what the
    reader wants. So the panel asks this before giving up, and says which turn it fell back
    to rather than quietly reporting the wrong one.
    """
    row = conn.execute(
        """
        SELECT MAX(b.turn) AS turn
          FROM diff d JOIN diff_batch b ON b.id = d.batch_id
         WHERE b.world_id = ? AND b.turn <= ? AND d.status = 'applied'
           AND d.target_table = ? AND d.target_id = ? AND d.field = ?
        """,
        (world_id, upto, table, target, field),
    ).fetchone()
    return int(row["turn"]) if row is not None and row["turn"] is not None else None


def trace_of(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    table: str,
    target: str,
    field: str,
    turn: int,
    viewer: str | None = None,
    title: str = "",
) -> CausalTrace:
    """Why one field of one row is what it is, after one turn.

    Groups the turn's applied diffs against that field by the event each cites -- the same
    join :func:`breakdown_of` uses, so the two cannot disagree about what moved a number --
    and then walks each cited event back through ``event_cause``.

    Read-only, and gated: see :func:`visible_event_ids`.
    """
    rows = conn.execute(
        """
        SELECT d.old_json AS before, d.new_json AS after, d.reason_event_id AS cause
          FROM diff d JOIN diff_batch b ON b.id = d.batch_id
         WHERE b.world_id = ? AND b.turn = ? AND d.status = 'applied'
           AND d.target_table = ? AND d.target_id = ? AND d.field = ?
         ORDER BY d.batch_id, d.seq
        """,
        (world_id, turn, table, target, field),
    ).fetchall()

    heading = title or f"{target} - {field}"
    if not rows:
        return CausalTrace(title=heading, table=table, target=target, field=field, turn=turn)

    first, last = rows[0], rows[-1]
    started, ended = _numeric(first["before"]), _numeric(last["after"])
    moved = (ended - started) if (started is not None and ended is not None) else None

    # Grouped by cause, in the order the causes were first written: a turn's diffs arrive in
    # resolution order, and that order is the closest thing to a narrative the log has.
    grouped: dict[int | None, list] = {}
    for row in rows:
        key = int(row["cause"]) if row["cause"] is not None else None
        grouped.setdefault(key, []).append(row)

    allowed = visible_event_ids(
        conn, world_id, [key for key in grouped if key is not None], viewer)

    strands: list[CausalStrand] = []
    withheld = 0
    for key, group in grouped.items():
        if key is not None and key not in allowed:
            withheld += 1
            continue
        deltas = [
            after - before
            for before, after in (
                (_numeric(row["before"]), _numeric(row["after"])) for row in group)
            if before is not None and after is not None
        ]
        amount = sum(deltas) if deltas else None
        detail = f"{_unwrap(group[0]['before'])} -> {_unwrap(group[-1]['after'])}"
        if key is None:
            # A citation pointing at nothing. A rewind deletes the events of the turns it
            # undoes and leaves the diffs, so this is a rewound turn rather than a bug --
            # and it is kept, under a plain label, because dropping it would stop the parts
            # summing to the whole. The same argument `breakdown_of`'s LEFT JOIN makes.
            strands.append(CausalStrand(amount=amount, detail=detail))
            continue
        links, hidden = _walk_back(conn, world_id, key, viewer)
        head = _refs(conn, world_id, [key])
        strands.append(CausalStrand(
            amount=amount,
            detail=detail,
            links=((CausalLink(event=head[0], depth=0),) + links) if head else links,
            hidden_upstream=hidden,
        ))

    strands.sort(key=lambda s: -abs(s.amount) if s.amount is not None else 0.0)
    return CausalTrace(
        title=heading, table=table, target=target, field=field, turn=turn,
        before=_unwrap(first["before"]), after=_unwrap(last["after"]), moved=moved,
        strands=tuple(strands), withheld=withheld,
    )


def trace_of_event(
    conn: sqlite3.Connection, world_id: str, event_id: int, viewer: str | None = None
) -> CausalTrace | None:
    """The chain behind one event, as a trace. ``None`` if the viewer may not see it.

    The other door into the same walk: a player who clicks an event rather than a number is
    asking the identical question and gets the identical answer shape, so one panel renders
    both.
    """
    if not visible_event_ids(conn, world_id, [event_id], viewer):
        return None
    head = _refs(conn, world_id, [event_id])
    if not head:
        return None
    ref = head[0]
    links, hidden = _walk_back(conn, world_id, event_id, viewer)
    return CausalTrace(
        title=ref.text or ref.kind,
        table="event", target=str(event_id), field="", turn=ref.turn,
        strands=(CausalStrand(
            amount=None, detail="",
            links=(CausalLink(event=ref, depth=0),) + links,
            hidden_upstream=hidden,
        ),),
    )
