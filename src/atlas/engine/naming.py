"""Matching the names a model writes to the rows the engine keeps.

A simulator narrating 1936 writes "Abyssinia", "Persia", "the Reich", "Britain". The
database holds ``eth``, ``m_irn``, ``ger``, ``gbr``. Every name that fails to match is a
battle that silently did not happen or a war that never started, and the failure is
invisible -- it shows up as the world ignoring something the player watched it describe.

So matching is deliberately generous, in a fixed order from exact to approximate, and it
stops at the first tier that produces exactly one candidate. Ambiguity resolves to nothing
rather than to a guess: quietly attacking the wrong country is worse than not attacking.

None of this lets a model reach anything new. It resolves a *reference* to a row that
already exists; what may then be done to that row is decided entirely elsewhere.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from difflib import get_close_matches
from functools import lru_cache

__all__ = ["ALIASES", "region_named_in", "resolve_nation", "resolve_region"]

#: Names a period simulator uses that no modern map carries, each mapped to the names a
#: pack might hold, in order of preference. Deliberately about *naming*, never about
#: politics: every entry is the same place under another name, not a claim about who
#: should hold it.
#:
#: Regional groupings -- the Balkans, Scandinavia, the Low Countries -- are absent on
#: purpose. They are not other names for one nation, and resolving one to whichever
#: member happened to sort first would attack a country nobody named.
ALIASES: dict[str, tuple[str, ...]] = {
    "abyssinia": ("ethiopia",),
    "persia": ("iran",),
    "siam": ("thailand",),
    "reich": ("germany",),
    "third reich": ("germany",),
    "nazi germany": ("germany",),
    "britain": ("united kingdom",),
    "great britain": ("united kingdom",),
    "england": ("united kingdom",),
    "ussr": ("soviet union", "russia"),
    "soviet russia": ("soviet union", "russia"),
    "america": ("united states",),
    "usa": ("united states",),
    "holland": ("netherlands",),
    "czechia": ("czechoslovakia", "czechia"),
    "bohemia": ("czechoslovakia", "czechia"),
    "manchukuo": ("china",),
    "manchuria": ("china",),
    "formosa": ("taiwan",),
    "burma": ("myanmar",),
    "ceylon": ("sri lanka",),
    "rhodesia": ("zimbabwe",),
    "yugoslavia": ("yugoslavia", "serbia"),
}

_LEADING_ARTICLE = re.compile(r"^(the|republic of|kingdom of|empire of|state of)\s+")
_NOISE = re.compile(r"[^a-z0-9 ]+")
#: Compiled once rather than looked up in `re`'s own cache on every call. The other two
#: patterns here were already module-level; this one was the odd one out.
_RUNS_OF_SPACE = re.compile(r"\s+")


@lru_cache(maxsize=8192)
def _word(needle: str) -> re.Pattern[str]:
    """``needle`` as a whole-word pattern, compiled once.

    Same argument as :func:`_normalised`: the needle is drawn from a fixed vocabulary
    and the pattern for it never changes, so building it per call is work with one
    answer.
    """
    return re.compile(rf"\b{re.escape(needle)}\b")


def normalise(text: str) -> str:
    """Casefold, strip accents and punctuation, drop a leading article.

    **A thin wrapper over a memoised core, and the split is not cosmetic.** The cache is
    on a function taking a `str`, so it can never be handed something unhashable; this
    half keeps the "whatever a model wrote" contract its callers rely on, where the
    value may be a number, ``None``, or anything else with a ``__str__``.
    """
    return _normalised(str(text))


#: **A pure function called tens of thousands of times a run on four hundred distinct
#: strings.** `reference.all_mentioned` normalises every id and every display name in a
#: vocabulary on every call -- two hundred nations, twice, fifty-five times a run -- and
#: the answer for "United Kingdom" is the same every one of those times. Profiled at
#: 47,077 regex compilations and 141,216 parser calls in a twelve-turn run.
#:
#: Bounded well above any world's vocabulary rather than unbounded, because the *other*
#: thing that arrives here is a paragraph of briefing prose, which is unique every time
#: and would grow the cache without ever being hit. Least-recently-used keeps the names,
#: which are what repeat.
@lru_cache(maxsize=8192)
def _normalised(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    folded = _NOISE.sub(" ", folded.lower()).strip()
    folded = _RUNS_OF_SPACE.sub(" ", folded)
    return _LEADING_ARTICLE.sub("", folded).strip()


#: The alias table keyed the way lookups arrive. Written above with articles for
#: readability; normalising both sides is what makes "the Reich" find Germany.
_NORMALISED_ALIASES: dict[str, tuple[str, ...]] = {}


def _build_alias_index() -> None:
    for alias, targets in ALIASES.items():
        _NORMALISED_ALIASES.setdefault(normalise(alias), ())
        _NORMALISED_ALIASES[normalise(alias)] += tuple(normalise(t) for t in targets)


def nation_names(conn: sqlite3.Connection, world_id: str) -> dict[str, str]:
    """Every nation in this world, by id.

    **An id is a database key and a player is not looking at a database.** *deu, gbr, ita*
    is not a shorter way of writing *Germany, the United Kingdom and Italy*; it is a
    different sentence, in a language nobody speaks, about powers the player is at war or
    at peace with. It leaked wherever a panel took a list of parties or an event's actor
    and printed it, because the id is what the row holds and the name is one join away.

    One query, so a caller can look up fifty parties without asking fifty times -- this
    is read on every refresh of the right rail. See `atlas.ui.main_window`.
    """
    return {row["id"]: row["name"] for row in conn.execute(
        "SELECT id, name FROM nation WHERE world_id = ?", (world_id,))}


def named(conn: sqlite3.Connection, world_id: str, value: str) -> str:
    """One id as its name, or the id itself if the world has never heard of it.

    Falling back rather than raising: an id with no nation behind it is a bug somewhere
    upstream, and showing the raw value is how somebody finds out. Showing nothing is how
    nobody does.
    """
    return nation_names(conn, world_id).get(value, value)


def resolve_nation(conn: sqlite3.Connection, world_id: str, value) -> str | None:
    """Best-effort nation id for whatever a model wrote. ``None`` if genuinely unclear."""
    return _resolve(conn, world_id, value, table="nation", extra_filter="AND is_alive = 1")


def resolve_region(conn: sqlite3.Connection, world_id: str, value) -> str | None:
    """Best-effort region id for whatever a model wrote."""
    return _resolve(conn, world_id, value, table="region", extra_filter="")


def _resolve(
    conn: sqlite3.Connection, world_id: str, value, *, table: str, extra_filter: str
) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    rows = conn.execute(
        f"SELECT id, name FROM {table} WHERE world_id = ? {extra_filter}",  # noqa: S608
        (world_id,),
    ).fetchall()
    if not rows:
        return None

    by_id = {str(row["id"]): str(row["id"]) for row in rows}
    if text in by_id:
        return by_id[text]

    wanted = normalise(text)
    if not wanted:
        return None

    by_name: dict[str, list[str]] = {}
    for row in rows:
        by_name.setdefault(normalise(row["name"]), []).append(str(row["id"]))

    # Exact, then alias, then prefix, then approximate. Each tier is tried in full before
    # the next, so a real name is never beaten by a fuzzy match on something else.
    if (found := _only(by_name.get(wanted))) is not None:
        return found

    for alias in _NORMALISED_ALIASES.get(wanted, ()):
        if (found := _only(by_name.get(alias))) is not None:
            return found

    prefixed = [
        ids for name, ids in by_name.items() if _prefixes(name, wanted) or _prefixes(wanted, name)
    ]
    if len(prefixed) == 1 and (found := _only(prefixed[0])) is not None:
        return found

    # There was a tier here that matched on a distinctive leading word, so that "Swiss
    # Confederation" would find Switzerland. It also decided that:
    #
    #     East Africa    -> East Timor        Western Front -> Western Sahara
    #     Central Europe -> Central African Republic
    #     British India  -> British Indian Ocean Territory
    #
    # and the engine then fought and conquered there. A uniqueness check does not save it:
    # exactly one nation starts with "East", and it is the wrong one. Regional and
    # colonial phrases are the natural vocabulary of a 1936 simulator, and every one of
    # them is a compound whose first word is somebody else's first word. Convenience for
    # one alias is not worth invading Timor.
    close = get_close_matches(wanted, list(by_name), n=2, cutoff=0.86)
    if len(close) == 1 and (found := _only(by_name[close[0]])) is not None:
        return found

    return None


def _prefixes(longer: str, shorter: str) -> bool:
    """Whether ``shorter`` is a whole-word prefix of ``longer``.

    The word boundary is the whole point. A bare ``startswith`` reads "British India" as
    "British Indian Ocean Territory" -- a real archipelago three thousand miles from the
    subcontinent, which the engine will then happily invade. Matching must stop at a word,
    not in the middle of one.
    """
    if not shorter or len(shorter) >= len(longer):
        return False
    return longer.startswith(shorter) and longer[len(shorter)] == " "


def _only(ids: list[str] | None) -> str | None:
    """One candidate is a match; several is an ambiguity, and ambiguity is not a match."""
    if ids and len(ids) == 1:
        return ids[0]
    return None


_build_alias_index()


def region_named_in(
    conn: sqlite3.Connection, world_id: str, text: str, *, viewer: str | None = None
) -> str:
    """The one province this sentence names, or ``""`` if it does not name exactly one.

    **For turning a headline into somewhere to go.** A summary line reads "Fighting reached
    Silesia" and the province is right there in it -- so a reader should be able to click
    the line and have the map centre on it, rather than reading the name and then typing it
    into the search box.

    Whole names only, matched on word boundaries against the provinces the viewer is
    entitled to see. Deliberately strict in three ways, because a headline that centres the
    *wrong* province is worse than one that is not clickable:

    * **Exactly one, or nothing.** A line naming two provinces has no single answer and gets
      none. Guessing which one was meant is how a shortcut becomes a hazard.
    * **Fogged like everything else.** Asked of `map.query.known_regions`, the same function
      the search box uses, so a headline cannot become a hole in the fog with a name in it.
    * **No fuzzy matching.** `resolve_region` above is generous on purpose, because a model
      writing "Persia" means Iran. This is the opposite case -- the text is the engine's own
      prose and the names in it are already the database's -- so generosity here would only
      buy false positives.
    """
    from ..map.query import known_regions

    if not text:
        return ""
    lowered = text.lower()
    known = known_regions(conn, world_id, viewer)
    found: set[str] = set()
    for row in conn.execute(
        "SELECT id, name FROM region WHERE world_id = ? AND kind <> 'ocean'", (world_id,)
    ):
        region_id, name = str(row["id"]), str(row["name"] or "")
        if len(name) < 3 or region_id not in known:
            continue
        if _word(name.lower()).search(lowered):
            found.add(region_id)
    return found.pop() if len(found) == 1 else ""
