"""What the world remembers, as distinct from what the engine computes.

**The distinction is the whole of this module, and it is structural rather than stated.** A
city renamed, a disputed state recognised, a doctrine announced, an official language set, a
no-first-use pledge, a day of remembrance: each is something the world holds and reasons
from, and **not one of them moves a number**. A fact never implies a mechanical effect.

That is not a convention anybody has to remember. This module writes to exactly one table,
``world_fact``, and a test asserts it -- so a fact *cannot* reach an economy, a stability, a
border or a unit, because the only code that mints facts cannot address those tables. When a
described policy ought to cost something, that cost belongs to the initiative and effect
layers, which are separate, later, and shown separately.

**Why memory needs its own layer at all.** Everything durable in this engine has so far been
a number or a row somebody computes: a treasury, a front, a commitment with a clause the
breach detector can check. There has been nowhere to put a thing that is simply *true from
now on* -- Danzig is called Gdansk; India recognises Bangladesh; this power has sworn never
to strike first. Written as prose in an event, such a thing scrolls out of the briefing
window in four turns and the world forgets it. Written here, it is still true in 1961, still
attributable to the event that made it true, and still visible to exactly the powers that
learned it.

**Causal, because a fact hangs off its event.** ``source_event_id`` is where the fact came
from, and asking *why does the world believe this* is a join. It is also what makes a rewind
clean: a rewind deletes the events of the turns it undoes, and the cascade takes their facts
with them -- no special case, no bookkeeping.

**Visibility-scoped, using the machinery events already have.** A fact is visible to a power
if it is public, or the power is its subject, or the power can see the event that minted it.
The same predicate the briefing's own event window uses, so a secret pledge does not appear
in a rival's briefing for the same reason a secret event does not.

**Engine-owned.** There is no model channel here yet. Facts are minted by engine code from
events the engine wrote; the "anything funnel" that lets a described action become a fact is
a later layer, and it will call :func:`record_fact` exactly as the engine does.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from ..state.apply import apply_batch
from ..state.diff import Diff, DiffBatch
from ..state.models import DiffOp, Role, Visibility

__all__ = [
    "PREDICATES",
    "Fact",
    "Predicate",
    "display_name",
    "every_fact",
    "fact_id_for",
    "facts_about",
    "facts_of",
    "record_fact",
    "retire_fact",
    "visible_facts",
]

_SLUG = re.compile(r"[^a-z0-9]+")

#: What a subject may be. Four kinds and not a foreign key, because one column cannot point
#: at four tables -- see the note in ``schema.sql``.
SUBJECT_KINDS = ("nation", "region", "organisation", "world")


@dataclass(frozen=True, slots=True)
class Predicate:
    """One thing the world can remember, and what it means when it does."""

    name: str
    #: What may be the subject of this predicate.
    subjects: tuple[str, ...]
    #: ``'text'`` for a value, a kind for a reference, ``'none'`` for neither.
    object_kind: str
    #: How it reads in a briefing. Takes the fact and a name resolver.
    phrasing: str
    note: str = ""
    #: **Whether a later restatement retires the earlier one.** A name replaces the name
    #: before it; a pledge does not replace a different pledge. This is per predicate
    #: because it is a fact about the predicate, not about the world.
    single_valued: bool = False


#: The vocabulary, as a registry rather than a CHECK constraint.
#:
#: **Open by design.** The point of a facts layer is that a world can remember things nobody
#: enumerated in advance, and every widening of a SQLite CHECK is a table rebuild. So the
#: column is free text and this is what decides what a predicate *means*: an unregistered
#: predicate is refused at the write path, and adding one is a dictionary entry rather than
#: a migration.
PREDICATES: dict[str, Predicate] = {
    "named": Predicate(
        "named", ("nation", "region", "organisation"), "text",
        "{subject} is called {value}",
        "A renaming. The world remembers it and every briefing uses it; nothing about the "
        "ground, its owner or its worth changes, because a name is not a mechanism.",
        single_valued=True),
    "recognises": Predicate(
        "recognises", ("nation", "organisation"), "nation",
        "{subject} recognises {object}",
        "Recognition is per believer: a state four powers accept and three do not is four "
        "facts, not one fact with a probability."),
    "disputes": Predicate(
        "disputes", ("nation",), "region",
        "{subject} disputes the status of {object}",
        "A stated grievance. It grants no claim and moves no border."),
    "claims": Predicate(
        "claims", ("nation",), "region",
        "{subject} claims {object}",
        "What a power says is rightfully its own. A claim is memory, not title -- ownership "
        "still changes only through conquest or a settlement."),
    "pledges": Predicate(
        "pledges", ("nation",), "text",
        "{subject} has pledged: {value}",
        "A declared undertaking with nobody on the other side of it -- a no-first-use "
        "pledge, a promise never to garrison a neighbour. A promise *to* somebody is a "
        "commitment and belongs in that table, where it can be breached."),
    "doctrine": Predicate(
        "doctrine", ("nation",), "text",
        "{subject}'s declared doctrine: {value}",
        "How a power says it intends to fight or govern. Read by everybody, enforced by "
        "nothing.", single_valued=True),
    "official_language": Predicate(
        "official_language", ("nation", "region"), "text",
        "{subject}'s official language is {value}", "", single_valued=True),
    # **The two the peace layer mints, and the seam working as designed.** A settlement is
    # a commitment (enforceable, breachable) *and* a set of facts the world now takes for
    # granted -- this border is agreed, that ground is to stay empty of troops. Neither of
    # them moves a number: the ground the treaty ceded was moved by the treaty's own diffs,
    # and what is recorded here is that everyone knows it happened.
    # **What the attribution layer mints, and the one predicate here that starts life
    # secret.** Every other fact in this table is something the world takes for granted; this
    # one is something *one country* has proved to its own satisfaction and nobody else has
    # been told. `record_fact` takes the scope of its cause, so it is written at the
    # investigation's own visibility and stays there until an exposure makes it public --
    # which is a different layer, deliberately.
    "was_behind": Predicate(
        "was_behind", ("nation",), "nation",
        "{subject} was behind a covert act against {object}",
        "Written when a victim's counterintelligence proves authorship. Not a claim anybody "
        "made: a claim is prose and this is the engine's finding, which is why it is the "
        "thing a casus belli is minted alongside rather than the thing a model asserts."),
    "renounces": Predicate(
        "renounces", ("nation",), "region",
        "{subject} has renounced its claim to {object}",
        "Written when a settlement takes ground off a power at the table. The province "
        "moved because the treaty moved it; this is the world remembering that the loser "
        "signed."),
    "demilitarised": Predicate(
        "demilitarised", ("region",), "text",
        "{subject} is demilitarised: {value}",
        "A zone a settlement says is to stay empty of one side's forces. The obligation "
        "itself is a `peace_term` the engine checks; this is the world knowing about it.",
        single_valued=True),
    "commemorates": Predicate(
        "commemorates", ("nation", "region", "world"), "text",
        "{subject} commemorates {value}",
        "A holiday, a memorial, a day of mourning. The clearest case of the whole layer: "
        "it changes nothing and the world would be poorer without it."),
}


@dataclass(frozen=True, slots=True)
class Fact:
    """One thing the world believes, and when it started believing it."""

    id: str
    subject_kind: str
    subject_id: str
    predicate: str
    object_kind: str = "none"
    object_id: str = ""
    value: str = ""
    start_turn: int = 0
    end_turn: int | None = None
    source_event_id: int | None = None
    visibility: str = "public"
    confidence: float = 1.0

    @property
    def live(self) -> bool:
        return self.end_turn is None

    def describe(self, names: dict[str, str] | None = None) -> str:
        names = names or {}
        spec = PREDICATES.get(self.predicate)
        subject = names.get(self.subject_id, self.subject_id)
        obj = names.get(self.object_id, self.object_id)
        if spec is None:
            return f"{subject} {self.predicate} {obj or self.value}".strip()
        return spec.phrasing.format(subject=subject, object=obj, value=self.value)


@dataclass(slots=True)
class FactReport:
    """What was recorded, and what was refused with a reason."""

    recorded: list[Fact] = field(default_factory=list)
    retired: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)


def fact_id_for(subject_id: str, predicate: str, discriminator: str, turn: int) -> str:
    """Derived, not autoincremented, so a fact survives a rewind with its identity.

    The turn is part of it because a world may remember the same *shape* of thing twice --
    renamed once in 1919 and again in 1945 -- and those are two facts with two causes, not
    one fact edited.
    """
    tail = _SLUG.sub("-", discriminator.strip().lower()).strip("-")[:40]
    return f"wf_{subject_id}_{predicate}_{tail}_{turn}" if tail else \
        f"wf_{subject_id}_{predicate}_{turn}"


# --------------------------------------------------------------------------- writing


def record_fact(  # noqa: PLR0913 - a fact is its fields; there is nothing to group
    conn: sqlite3.Connection,
    world_id: str,
    *,
    subject_kind: str,
    subject_id: str,
    predicate: str,
    object_id: str = "",
    value: str = "",
    turn: int,
    source_event_id: int,
    visibility: Visibility | str = Visibility.PUBLIC,
    confidence: float = 1.0,
) -> FactReport:
    """Give the world something to remember, caused by an event it already recorded.

    Engine-owned: ``source_event_id`` must name a real event, because a memory with no
    cause is a rumour and the whole layer rests on being able to ask why.

    A single-valued predicate retires whatever it replaces, in the same batch, so the world
    never believes two names for one place at once.
    """
    report = FactReport()
    spec = PREDICATES.get(predicate)
    if spec is None:
        report.refused.append(
            f"{predicate!r} is not something the world knows how to remember; the "
            f"predicates are {', '.join(sorted(PREDICATES))}")
        return report
    if subject_kind not in spec.subjects:
        report.refused.append(
            f"a {predicate} fact is about a {' or a '.join(spec.subjects)}, "
            f"not a {subject_kind}")
        return report
    if not _exists(conn, world_id, subject_kind, subject_id):
        report.refused.append(f"there is no {subject_kind} called {subject_id!r}")
        return report
    if spec.object_kind == "text":
        if not str(value).strip():
            report.refused.append(f"a {predicate} fact has to say what it remembers")
            return report
        object_id = ""
    elif spec.object_kind != "none":
        if not _exists(conn, world_id, spec.object_kind, object_id):
            report.refused.append(
                f"a {predicate} fact names a {spec.object_kind}, and there is no "
                f"{spec.object_kind} called {object_id!r}")
            return report
        value = ""
    if conn.execute("SELECT 1 FROM event WHERE id = ?", (source_event_id,)).fetchone() is None:
        report.refused.append(
            f"event {source_event_id} does not exist; a fact the world cannot trace is a "
            "rumour, not a memory")
        return report

    scope = visibility.value if isinstance(visibility, Visibility) else str(visibility)
    fact = Fact(
        id=fact_id_for(subject_id, predicate, object_id or value, turn),
        subject_kind=subject_kind, subject_id=subject_id, predicate=predicate,
        object_kind=spec.object_kind, object_id=object_id, value=value,
        start_turn=turn, source_event_id=source_event_id, visibility=scope,
        confidence=max(0.0, min(1.0, float(confidence))))

    # **The diff's scope is the citing event's, not the fact's own.** Rule INFO-002 refuses
    # a change marked wider than the event that caused it -- "a covert cause cannot produce
    # overt state" -- and a secret pledge cited by a secret event is exactly that shape.
    # Found by writing one and being refused, which is the rule doing its job.
    cause_scope = _scope_of(conn, source_event_id)

    diffs: list[Diff] = []
    if spec.single_valued:
        for row in conn.execute(
            "SELECT id FROM world_fact WHERE world_id = ? AND subject_id = ?"
            "   AND predicate = ? AND end_turn IS NULL AND id <> ?",
            (world_id, subject_id, predicate, fact.id),
        ):
            diffs.append(
                Diff("world_fact", str(row["id"]), "end_turn", DiffOp.SET, turn,
                     reason_event_id=source_event_id, visibility_scope=cause_scope))
            report.retired.append(str(row["id"]))

    diffs.append(
        Diff("world_fact", fact.id, "__insert__", DiffOp.INSERT,
             {
                 "world_id": world_id,
                 "subject_kind": fact.subject_kind,
                 "subject_id": fact.subject_id,
                 "predicate": fact.predicate,
                 "object_kind": fact.object_kind,
                 "object_id": fact.object_id,
                 "value": fact.value,
                 "start_turn": fact.start_turn,
                 "end_turn": None,
                 "source_event_id": fact.source_event_id,
                 "visibility": fact.visibility,
                 "confidence": fact.confidence,
             },
             reason_event_id=source_event_id, visibility_scope=cause_scope))

    outcome = apply_batch(
        conn,
        DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                  proposer_model=None, diffs=tuple(diffs)))
    if not outcome.applied:
        report.refused.append(f"the world could not record it: {outcome.reason}")
        report.retired = []
        return report
    report.recorded.append(fact)
    return report


def retire_fact(
    conn: sqlite3.Connection, world_id: str, fact_id: str, *,
    turn: int, source_event_id: int
) -> bool:
    """Stop the world believing something, without pretending it never believed it.

    An end turn rather than a delete, so "Danzig was called Gdansk between 1945 and 1990"
    stays askable -- and so a rewind has something to put back.
    """
    row = conn.execute(
        "SELECT end_turn FROM world_fact WHERE world_id = ? AND id = ?",
        (world_id, fact_id)).fetchone()
    if row is None or row["end_turn"] is not None:
        return False
    outcome = apply_batch(
        conn,
        DiffBatch(
            world_id=world_id, turn=turn, proposer_role=Role.ENGINE, proposer_model=None,
            diffs=(Diff("world_fact", fact_id, "end_turn", DiffOp.SET, turn,
                        reason_event_id=source_event_id,
                        visibility_scope=_scope_of(conn, source_event_id)),)))
    return outcome.applied


# --------------------------------------------------------------------------- reading

_COLUMNS = ("id, subject_kind, subject_id, predicate, object_kind, object_id, value,"
            " start_turn, end_turn, source_event_id, visibility, confidence")

#: Visible if it is public, or you are its subject, or you can see the event that made it.
#: The same three-part predicate `visibility.known_events` uses, deliberately: two copies of
#: what a nation may know is one copy too many.
_VISIBLE = """
    (f.visibility = 'public'
     OR (f.subject_kind = 'nation' AND f.subject_id = ?)
     OR EXISTS (SELECT 1 FROM event_visibility v
                 WHERE v.event_id = f.source_event_id AND v.nation_id = ?)
     OR EXISTS (SELECT 1 FROM event e
                 WHERE e.id = f.source_event_id AND e.actor_nation_id = ?))
"""


def _read(rows) -> list[Fact]:
    return [
        Fact(id=str(row["id"]), subject_kind=str(row["subject_kind"]),
             subject_id=str(row["subject_id"]), predicate=str(row["predicate"]),
             object_kind=str(row["object_kind"]), object_id=str(row["object_id"] or ""),
             value=str(row["value"] or ""), start_turn=int(row["start_turn"]),
             end_turn=None if row["end_turn"] is None else int(row["end_turn"]),
             source_event_id=(None if row["source_event_id"] is None
                              else int(row["source_event_id"])),
             visibility=str(row["visibility"]), confidence=float(row["confidence"]))
        for row in rows
    ]


def facts_about(
    conn: sqlite3.Connection, world_id: str, subject_id: str, *, live_only: bool = True
) -> list[Fact]:
    """Everything the world remembers about one thing. Unscoped: see :func:`visible_facts`."""
    clause = " AND f.end_turn IS NULL" if live_only else ""
    return _read(conn.execute(
        f"SELECT {_COLUMNS} FROM world_fact f"  # noqa: S608 - _COLUMNS is a literal
        f" WHERE f.world_id = ? AND f.subject_id = ?{clause}"
        " ORDER BY f.start_turn, f.id", (world_id, subject_id)))


def facts_of(
    conn: sqlite3.Connection, world_id: str, predicate: str, *, live_only: bool = True
) -> list[Fact]:
    """Every live fact of one kind, across the world."""
    clause = " AND f.end_turn IS NULL" if live_only else ""
    return _read(conn.execute(
        f"SELECT {_COLUMNS} FROM world_fact f"  # noqa: S608 - _COLUMNS is a literal
        f" WHERE f.world_id = ? AND f.predicate = ?{clause}"
        " ORDER BY f.start_turn, f.id", (world_id, predicate)))


def visible_facts(
    conn: sqlite3.Connection, world_id: str, viewer: str, *, limit: int = 40
) -> list[Fact]:
    """What ``viewer`` knows the world remembers. **Every caller into a briefing uses this.**

    A secret fact belongs to whoever learned it, exactly as a secret event does. The
    omniscient reader is :func:`every_fact`, in its own function with its own name, so an
    unscoped read is visible in review rather than accidental.
    """
    return _read(conn.execute(
        f"SELECT {_COLUMNS} FROM world_fact f"  # noqa: S608 - _COLUMNS is a literal
        f" WHERE f.world_id = ? AND f.end_turn IS NULL AND {_VISIBLE}"
        " ORDER BY f.start_turn DESC, f.id DESC LIMIT ?",
        (world_id, viewer, viewer, viewer, limit)))


def every_fact(
    conn: sqlite3.Connection, world_id: str, *, live_only: bool = True
) -> list[Fact]:
    """Omniscient. For the referee and for tests, and named so that its use is obvious."""
    clause = " AND f.end_turn IS NULL" if live_only else ""
    return _read(conn.execute(
        f"SELECT {_COLUMNS} FROM world_fact f WHERE f.world_id = ?{clause}"  # noqa: S608
        " ORDER BY f.start_turn, f.id", (world_id,)))


def display_name(
    conn: sqlite3.Connection, world_id: str, subject_id: str, fallback: str = ""
) -> str:
    """What the world currently calls something.

    **The read path honours memory, and that is not a mechanical effect.** A rename writes
    no column: `region.name` is what the scenario laid down, and this is what the world
    calls it now. Nothing about the ground, its owner, its population or its worth is
    touched -- which is exactly the line this whole module is drawn along.
    """
    row = conn.execute(
        "SELECT value FROM world_fact WHERE world_id = ? AND subject_id = ?"
        "   AND predicate = 'named' AND end_turn IS NULL"
        " ORDER BY start_turn DESC, id DESC LIMIT 1", (world_id, subject_id)).fetchone()
    return str(row["value"]) if row else fallback


def names_for(conn: sqlite3.Connection, world_id: str) -> dict[str, str]:
    """Every id the world has a name for, current names winning. For rendering."""
    found: dict[str, str] = {}
    for table in ("nation", "region"):
        for row in conn.execute(
            f"SELECT id, name FROM {table} WHERE world_id = ?", (world_id,)):  # noqa: S608
            found[str(row["id"])] = str(row["name"])
    for fact in facts_of(conn, world_id, "named"):
        found[fact.subject_id] = fact.value
    return found


def _scope_of(conn: sqlite3.Connection, event_id: int) -> Visibility:
    """The visibility of the event a change cites. See rule INFO-002."""
    row = conn.execute("SELECT visibility FROM event WHERE id = ?", (event_id,)).fetchone()
    try:
        return Visibility(str(row["visibility"])) if row else Visibility.PUBLIC
    except ValueError:
        return Visibility.PUBLIC


def _exists(conn: sqlite3.Connection, world_id: str, kind: str, entity_id: str) -> bool:
    if kind == "world":
        return entity_id == world_id
    if kind == "organisation":
        return conn.execute(
            "SELECT 1 FROM organisation o JOIN nation n ON n.id = o.nation_id"
            " WHERE n.world_id = ? AND o.nation_id = ?", (world_id, entity_id)
        ).fetchone() is not None
    if kind not in {"nation", "region"}:
        return False
    return conn.execute(
        f"SELECT 1 FROM {kind} WHERE world_id = ? AND id = ?",  # noqa: S608 - kind checked
        (world_id, entity_id)).fetchone() is not None
