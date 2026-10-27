"""One place a model-facing reference becomes an engine id, for every vocabulary.

The engine is strict about ids and shows people *names*. Between those two facts sits every
naming bug this project has had: a staff told *Northmark [r_north]* wrote back `Northmark`
and was told the place is not on the map; a player ordered *All-welded construction* and the
parser had no id to give it; an order for a `transport` fell through a filter and became
infantry.

Two things this adds over :mod:`atlas.engine.naming`, which already had a good tiered
matcher and keeps doing the matching:

**It can say why it failed.** ``None`` meant both *no such place* and *seven places match*,
so no caller could produce a message worth reading. Ambiguity is the useful answer -- it
means the vocabulary was right and the phrase was not specific enough -- and it comes back
with the candidates, so the message can name them.

**It knows the vocabularies that are not tables.** Regions and nations live in the
database; technologies, civil projects and unit kinds are catalogues, and every caller that
wanted one wrote its own matching. Five vocabularies, one algorithm.

**The engine stays exactly as strict as it was.** Nothing here relaxes a rule. What changes
is that the rule is applied to what the model *meant* rather than to what it typed --
resolution happens at the boundary, and everything behind the boundary still deals only in
ids. A phrase that resolves to nothing is still refused, and refused *by the spelling that
was written*, because that message is the only thing telling the writer what to say instead.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from difflib import get_close_matches
from functools import lru_cache

from .naming import normalise

__all__ = [
    "VOCABULARIES",
    "Reference",
    "label",
    "label_of",
    "mentioned_in",
    "resolve",
    "vocabulary",
]

#: The id a displayed label carries, in either shape the engine has ever shown: ``Name
#: [id]`` and the older ``Name (`id`)``. Both are recognised because model output outlives
#: a change of house style -- a staff that learned the second form will keep writing it.
_LABELLED = re.compile(r"^(?P<name>.*?)\s*[\[(]`?(?P<id>[A-Za-z0-9_.:-]+)`?[\])]\s*$")

#: Every vocabulary a model-facing reference can be drawn from. The same names
#: :attr:`atlas.engine.capabilities.Capability.targets` uses, so the manifest and this
#: cannot disagree about what a target is.
VOCABULARIES: tuple[str, ...] = (
    "region", "nation", "project", "technology", "unit_kind",
)


@dataclass(frozen=True, slots=True)
class Reference:
    """What a written reference turned out to mean."""

    kind: str
    #: What was written, verbatim. Refusals quote this rather than a best guess at it.
    written: str
    #: The engine id, or ``None`` if it did not resolve.
    value: str | None = None
    #: Set when several entries matched. Non-empty here means *ambiguous* rather than
    #: *unknown*, and the two deserve different messages.
    candidates: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.value is not None

    @property
    def ambiguous(self) -> bool:
        return self.value is None and bool(self.candidates)

    @property
    def problem(self) -> str | None:
        """The sentence to show, or ``None`` if it resolved.

        Ambiguity names the candidates, because a writer who was nearly right needs to
        know which way to be more specific. An unknown reference does not list the whole
        vocabulary -- for regions that is several hundred entries and would bury the
        message it is attached to.
        """
        if self.ok:
            return None
        if self.ambiguous:
            shown = ", ".join(self.candidates[:4])
            return (
                f"{self.written!r} could be any of {shown}; name which one"
            )
        return f"there is no {self.kind.replace('_', ' ')} called {self.written!r}"


def vocabulary(
    conn: sqlite3.Connection, world_id: str, kind: str
) -> tuple[tuple[str, str], ...]:
    """``(id, display name)`` for one vocabulary in this world.

    Every entry is something a briefing might have shown, which is the test for whether it
    belongs here: the boundary exists to understand what was displayed.
    """
    if kind == "region":
        rows = conn.execute(
            "SELECT id, name FROM region WHERE world_id = ?", (world_id,)
        ).fetchall()
        return tuple((str(r["id"]), str(r["name"])) for r in rows)
    if kind == "nation":
        rows = conn.execute(
            "SELECT id, name FROM nation WHERE world_id = ?", (world_id,)
        ).fetchall()
        return tuple((str(r["id"]), str(r["name"])) for r in rows)
    if kind == "unit_kind":
        rows = conn.execute(
            "SELECT kind FROM unit_template WHERE world_id = ?", (world_id,)
        ).fetchall()
        # A unit kind is its own display name, give or take the underscore a player will
        # not type.
        return tuple((str(r["kind"]), str(r["kind"]).replace("_", " ")) for r in rows)
    if kind == "project":
        from .projects import CIVIL_PROJECTS

        return tuple((p.id, p.name) for p in CIVIL_PROJECTS)
    if kind == "technology":
        from .research import catalogue_for

        return tuple((t.id, t.name) for t in catalogue_for(conn, world_id))
    raise ValueError(f"unknown vocabulary {kind!r}; expected one of {VOCABULARIES}")


def _prefixes(longer: str, shorter: str) -> bool:
    """Whether ``shorter`` is a whole-word prefix of ``longer``.

    The word boundary carries a scar: a bare ``startswith`` reads *British India* as
    *British Indian Ocean Territory*, three thousand miles away, and the engine then
    invades it. See :mod:`atlas.engine.naming`, which learned this first.
    """
    if not shorter or len(shorter) >= len(longer):
        return False
    return longer.startswith(shorter) and longer[len(shorter)] == " "


def resolve(
    conn: sqlite3.Connection, world_id: str, kind: str, written: object
) -> Reference:
    """Turn whatever was written into an id of ``kind``, or say why it could not be.

    Tiers, each tried in full before the next so a real name is never beaten by a fuzzy
    match on something else: exact id, exact display name, whole-word prefix, close match.
    Anything matching several entries stops as *ambiguous* rather than picking one --
    guessing between two real places is worse than refusing, because the refusal is
    correctable and the guess is a war in the wrong country.
    """
    text = "" if written is None else str(written).strip()
    if not text:
        return Reference(kind=kind, written=text)

    entries = vocabulary(conn, world_id, kind)
    if not entries:
        return Reference(kind=kind, written=text)

    by_id = {entry_id: entry_id for entry_id, _name in entries}
    if text in by_id:
        return Reference(kind=kind, written=text, value=text)

    # **What was displayed, written back.** :func:`label` renders `Name [id]`, and a model
    # copying that line verbatim is doing the most sensible thing available to it. Handled
    # here, before any name matching, because the id in the brackets is authoritative --
    # this is the tier that makes "what a model is shown is what the resolver takes" true
    # by construction rather than by the prefix tier happening to cope.
    #
    # **Unless the two halves disagree, in which case neither is trusted.** `Germany [fra]`
    # is not a reference with a typo in it; it is a writer who has lost track of which
    # country they mean, and taking the id because ids are authoritative would act on
    # France while the sentence says Germany. Refusing is correctable and the guess is a war
    # in the wrong place -- the same reasoning the ambiguity tier already runs on.
    if (marked := _LABELLED.match(text)) and marked.group("id") in by_id:
        written_name = normalise(marked.group("name"))
        actual = next(
            (name for entry_id, name in entries if entry_id == marked.group("id")), ""
        )
        if not written_name or written_name == normalise(actual):
            return Reference(kind=kind, written=text, value=marked.group("id"))
        # The name may still be a real one. Say so, because "these are two different
        # things" is the useful message rather than "no such thing".
        #
        # **Both readings of the name**, and the second one is easy to miss: the *whole
        # written text* can itself be an entry's name. A place genuinely called
        # `Sector [r_east]` is indistinguishable, as written, from a label for the Veyran
        # Plateau -- and listing only the id would refuse safely while naming one of the two
        # things it might have meant. No such name exists in either era (swept: 1421 entries,
        # none matching the label shape at all), which is exactly why the message for it
        # should be right before one does.
        named = [
            entry_id
            for entry_id, name in entries
            if normalise(name) in {written_name, normalise(text)}
        ]
        return Reference(
            kind=kind, written=text,
            candidates=tuple(sorted({marked.group("id"), *named})),
        )

    wanted = normalise(text)
    if not wanted:
        return Reference(kind=kind, written=text)

    # An id written in a slightly different shape -- `Welding`, `rare earths` for
    # `rare_earths` -- before any name matching, because an id is the least ambiguous
    # thing anybody can write.
    normalised_ids = {normalise(entry_id.replace("_", " ")): entry_id
                      for entry_id, _name in entries}
    if wanted in normalised_ids:
        return Reference(kind=kind, written=text, value=normalised_ids[wanted])

    by_name: dict[str, list[str]] = {}
    for entry_id, name in entries:
        by_name.setdefault(normalise(name), []).append(entry_id)

    if (exact := by_name.get(wanted)) is not None:
        if len(exact) == 1:
            return Reference(kind=kind, written=text, value=exact[0])
        return Reference(kind=kind, written=text, candidates=tuple(sorted(exact)))

    prefixed = sorted({
        entry_id
        for name, ids in by_name.items()
        if _prefixes(name, wanted) or _prefixes(wanted, name)
        for entry_id in ids
    })
    if len(prefixed) == 1:
        return Reference(kind=kind, written=text, value=prefixed[0])
    if prefixed:
        return Reference(kind=kind, written=text, candidates=tuple(prefixed))

    close = get_close_matches(wanted, list(by_name), n=3, cutoff=0.86)
    matched = sorted({entry_id for name in close for entry_id in by_name[name]})
    if len(matched) == 1:
        return Reference(kind=kind, written=text, value=matched[0])
    if matched:
        return Reference(kind=kind, written=text, candidates=tuple(matched))

    return Reference(kind=kind, written=text)


def label(name: object, entry_id: object) -> str:
    """How to show a place, a power or a programme to a model: ``Name [id]``.

    **One renderer, because there were four.** A region reached a model as
    ``Name (`id`)`` in the front lines, ``Name [id]`` in the parser view, and as a bare
    ``Name`` twice in the world simulator's own briefing -- and the bare ones are the
    interesting failure. PT33 watched two independent staffs write *Coastal Reaches* every
    turn for six turns for a province named **The Reaches** of kind `coastal`. The phrase
    was in no briefing's structured lines: it was invented in turn-1 narrative prose by a
    model that had been shown the name without an id, and every later staff read that prose
    back as history and reused it. The resolver refused it, correctly, ten times.

    A name with no id beside it is a name a model can only repeat approximately. This is
    the fix: everything a model is shown carries the thing the resolver will take, and
    :func:`resolve` recognises this exact form before it tries any name matching.

    Pure, and takes the name the caller already has -- every call site is rendering a row it
    has just read, and a helper that went back to the database per region would put four
    hundred lookups behind one briefing.

    Falls back to the id alone when there is no name, which is honest: a bare id is still
    resolvable, and inventing a display name here would be inventing content.
    """
    shown, known = str(name or "").strip(), str(entry_id or "").strip()
    return f"{shown} [{known}]" if shown and shown != known else known


def label_of(conn: sqlite3.Connection, world_id: str, kind: str, entry_id: str) -> str:
    """:func:`label` for a caller that has only an id. One query, so use it sparingly."""
    for known_id, name in vocabulary(conn, world_id, kind):
        if known_id == entry_id:
            return label(name, known_id)
    return str(entry_id)


@lru_cache(maxsize=8192)
def _whole_word(needle: str) -> re.Pattern[str]:
    """``needle``, anchored to word boundaries, compiled once.

    **The pattern is a pure function of the needle, and the needles come from a
    vocabulary** -- two hundred nations and as many regions, each tried twice, on every
    call. Building it per call cost 47,077 regex compilations and 141,216 parser calls
    in a twelve-turn run, for about four hundred distinct answers.

    Deliberately still one pattern per needle rather than one alternation over all of
    them. An alternation is not the same test: it matches at most once at any position,
    so a shorter name listed first would shadow a longer one that also matches there,
    and two entries that both appear would become one. This is memoisation, which
    cannot change an answer, and that is the whole reason to prefer it.
    """
    return re.compile(rf"(?<!\w){re.escape(needle)}(?!\w)")


def all_mentioned(
    conn: sqlite3.Connection, world_id: str, kind: str, prose: object
) -> tuple[str, ...]:
    """Every known entry named inside a longer text, in vocabulary order.

    :func:`mentioned_in` answers *which one is this sentence about*, and stops at
    ambiguity because a schema field takes one value. A briefing is the other case: a
    paragraph of history mentions several places and wants **all** of them, precisely so it
    can tell the reader what each is called.
    """
    text = "" if prose is None else str(prose).strip()
    hay = normalise(text)
    if not hay:
        return ()
    found: list[str] = []
    for entry_id, name in vocabulary(conn, world_id, kind):
        for needle in {normalise(entry_id.replace("_", " ")), normalise(name)}:
            if needle and _whole_word(needle).search(hay):
                found.append(entry_id)
                break
    return tuple(found)


def mentioned_in(
    conn: sqlite3.Connection, world_id: str, kind: str, prose: object
) -> Reference:
    """Find a known entry *named inside* a longer sentence.

    :func:`resolve` answers "what is this phrase", which is the right question for a
    schema field. It is the wrong one for a summary: a parser that left `project` empty
    still wrote *begin work on radar*, and resolving that whole sentence finds nothing.

    **Whole words only, and that is the entire difference from the substring test this
    replaces.** The old check was ``entry.id in haystack``, which matched `rail` inside
    *the Reichsrail agreement*, inside *derailed*, and inside any sentence that happened
    to contain the letters. Anchoring to word boundaries keeps the convenience and drops
    the accidents. Several distinct entries named in one sentence is *ambiguous*, not a
    reason to take the first.
    """
    text = "" if prose is None else str(prose).strip()
    if not text:
        return Reference(kind=kind, written=text)

    hay = normalise(text)
    if not hay:
        return Reference(kind=kind, written=text)

    found: set[str] = set()
    for entry_id, name in vocabulary(conn, world_id, kind):
        for needle in {normalise(entry_id.replace("_", " ")), normalise(name)}:
            if not needle:
                continue
            if _whole_word(needle).search(hay):
                found.add(entry_id)
                break

    if len(found) == 1:
        return Reference(kind=kind, written=text, value=next(iter(found)))
    if found:
        return Reference(kind=kind, written=text, candidates=tuple(sorted(found)))
    return Reference(kind=kind, written=text)
