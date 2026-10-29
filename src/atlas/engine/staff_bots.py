"""Rule-based stand-ins for the conversational and interpretive roles, with no model call.

:mod:`atlas.engine.bots` already plays the scenery: tracked neutrals, the covert channel and
the Security Council. That makes a mock world *move*, but everything the player talks to
was still a blank: free-text orders came back "nothing actionable", every power answered
"we will consider your proposal", the council said one canned line and the world-sim wrote
no narrative. This module fills those seats so the game is playable end to end offline.

**The same forms, the same gates.** Every answer here has the shape a model's answer has --
the parser's action list, a diplomacy reply plus the commitments a negotiation produced, a
freeform outcome, a world-sim batch -- and goes through the same validation a model's does.
Nothing here writes around a rule. The diplomacy bot reads the same regard thresholds rule
DIP-001 enforces, so it never agrees to something the gates would then refuse.

**Deterministic.** No ambient entropy (the engine bans it): where a reply varies, the variant
is picked by hashing the words it is answering, so the same message gets the same answer.

**Reads only what the player's side may read**, where a model would have been given a
player's view: the council judges rivals' armies through the player's own intelligence
estimate (`bots._believed_strength`), never the true row.

Scopes, alongside the scenery scopes in :mod:`atlas.engine.bots`:

* ``parser`` -- free-text orders;
* ``diplomacy`` -- replies in talks, and what the talks agreed;
* ``advisor`` -- the player's council;
* ``world_sim`` -- the world-simulation narrative and the history digest, and treaty
  proposals and notes sent as orders, which have no engine handler of their own;
* ``freeform`` -- orders no mechanic covers.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from typing import Any

from . import reference
from .bots import STAFF_SCOPES
from .naming import normalise

__all__ = [
    "STAFF_SCOPES",
    "advise",
    "diplomacy_reply",
    "freeform_answer",
    "parse_orders",
    "treaty_decision",
    "world_sim_batch",
]

#: The regard rule DIP-001 requires, in both directions, before these treaties can stand.
#: Read here so the bot says yes only to what the gates will let through.
ALLIANCE_REGARD = 55.0
DEFENSIVE_REGARD = 35.0
#: Below these, a power declines the lesser agreements. Not rules -- judgement -- so they sit
#: well inside what the engine would allow.
NON_AGGRESSION_REGARD = -10.0
TRADE_REGARD = -25.0
ACCESS_REGARD = 40.0


def _pick(options: tuple[str, ...], *keys: object) -> str:
    """One of ``options``, chosen by the words being answered rather than by chance."""
    digest = hashlib.sha1("|".join(str(k) for k in keys).encode()).digest()
    return options[digest[0] % len(options)]


def _name(conn: sqlite3.Connection, world_id: str, nation_id: str) -> str:
    row = conn.execute("SELECT name FROM nation WHERE world_id = ? AND id = ?",
                       (world_id, nation_id)).fetchone()
    return str(row["name"]) if row else nation_id


def _relation(conn: sqlite3.Connection, world_id: str, a: str, b: str) -> sqlite3.Row | None:
    """How ``a`` stands towards ``b``: regard, treaty, war."""
    return conn.execute(
        "SELECT score, treaty_status, at_war FROM relation"
        " WHERE world_id = ? AND nation_a = ? AND nation_b = ?", (world_id, a, b)).fetchone()


def _regard(conn: sqlite3.Connection, world_id: str, a: str, b: str) -> float:
    row = _relation(conn, world_id, a, b)
    return float(row["score"]) if row is not None else 0.0


def _at_war(conn: sqlite3.Connection, world_id: str, a: str, b: str) -> bool:
    row = _relation(conn, world_id, a, b)
    return bool(row is not None and row["at_war"])


def _enemies(conn: sqlite3.Connection, world_id: str, nation_id: str) -> list[str]:
    return [str(r[0]) for r in conn.execute(
        "SELECT nation_b FROM relation WHERE world_id = ? AND nation_a = ? AND at_war = 1"
        " ORDER BY nation_b", (world_id, nation_id))]


def _mood(score: float) -> str:
    if score >= 55:
        return "warm"
    if score >= 20:
        return "cordial"
    if score > -20:
        return "guarded"
    if score > -55:
        return "cool"
    return "hostile"


# ------------------------------------------------------------------------------- parser

#: Verbs a player reaches for that the capability catalogue does not list. Added to its own
#: aliases, never replacing them, so the catalogue stays the authority on what exists.
_EXTRA_ALIASES: dict[str, tuple[str, ...]] = {
    "diplomatic_message": ("sound out", "ask", "contact", "reassure", "threaten",
                           "negotiate with", "talk to", "approach", "send word", "inform",
                           "congratulate", "thank", "send a note", "note to",
                           "send a message"),
    "propose_treaty": ("ally with", "defensive pact", "trade agreement", "trade deal",
                       "sign", "military access", "offer"),
    "declare_war": ("go to war",),
    "sue_for_peace": ("seek terms", "terms", "end the war"),
    "move_units": ("send troops", "send the army", "deploy", "occupy", "retreat",
                   "pull back", "capture", "conquer", "seize", "take over", "take",
                   "overrun", "storm",
                   # Taking ground back is an attack too. A live Gulf game ordered "Liberate
                   # Kuwait" for seventeen months and was told no order was recognised.
                   "liberate", "retake", "recapture", "reconquer", "drive out", "push out",
                   "expel", "counterattack", "counter-attack"),
    "raise_units": ("train", "draft"),
    "economic_investment": ("boost the economy", "industrialise", "industrialize",
                            "rebuild", "reconstruct"),
    "espionage": ("spy on", "investigate", "scout"),
    "fortify": ("defend", "hold the line"),
    "build_project": ("construct",),
    "freeform": ("announce", "proclaim", "decree", "celebrate", "hold a", "declare a",
                 "honour", "honor", "mourn", "organise", "organize"),
    # Undertakings asked for in a player's own words rather than by the catalogue's name.
    # Each phrase is in `_GAMBIT_WORDS` too, which decides the kind.
    "gambit": ("fund rebels", "fund the rebels", "arm rebels", "arm the rebels",
               "back the rebels", "support the rebels", "support rebels", "foment",
               "stir up", "destabilise", "destabilize", "propaganda", "disinformation",
               "social media campaign", "regime change", "stage a coup", "organise a coup",
               "organize a coup", "assassinate", "hack", "cyberattack", "cyber attack",
               "cyber-attack", "blow up", "wreck", "derail", "buy off", "pay off",
               "leak", "smear", "name and shame", "hold a summit", "hold talks",
               "peace talks", "back channel", "backchannel", "secret talks", "honeypot",
               "airstrike", "missile strike", "drone strike", "air raid", "rebels",
               "insurgents", "uprising", "coup", "defect", "opposition groups",
               "fund the opposition", "fund opposition", "arm militias", "fund militias",
               "back militias", "separatists", "insurgency"),
}

#: A weak verb ("ask", "tell") yields to a stronger act named in the same clause: "ask
#: Poland for an alliance" is a treaty proposal, not a letter.
_WEAK = frozenset({"diplomatic_message"})
_STRONGER = ("sue_for_peace", "propose_treaty")

#: Verbs several capabilities share. The noun decides: a programme, a formation or a stance.
_OVERLOADED = frozenset({"build", "begin", "develop", "research", "construct", "adopt",
                         "programme", "program", "lay down", "mobilise", "mobilize",
                         "raise", "recruit"})

_NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
            "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "a couple of": 2,
            "several": 3}

#: Plain words for formations, mapped to the kinds a world's templates usually carry.
_UNIT_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("armour", ("tank", "tanks", "armor", "armoured", "armored", "panzer", "panzers")),
    ("infantry", ("division", "divisions", "troops", "soldiers", "men", "army", "armies",
                  "infantry", "regiment", "regiments", "brigade", "brigades")),
    ("battleship", ("battleship", "battleships", "dreadnought", "dreadnoughts")),
    ("carrier", ("carrier", "carriers")),
    ("transport", ("transport", "transports", "troopship", "troopships")),
)

_DOCTRINE_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("mass", ("mass", "conscription", "mass army")),
    ("professional", ("professional",)),
    ("retaliation", ("retaliation", "retaliatory")),
    ("minimum", ("minimum", "minimal deterrent")),
    ("collective_security", ("collective security", "collective_security")),
    ("neutrality", ("neutrality", "neutral", "armed neutrality")),
)

_GAMBIT_WORDS: tuple[tuple[str, str], ...] = (
    ("air strike", "air_strike"), ("bomb", "air_strike"), ("airstrike", "air_strike"),
    ("missile strike", "air_strike"), ("drone strike", "air_strike"),
    ("air raid", "air_strike"),
    ("sabotage", "sabotage"), ("assassinate", "sabotage"), ("hack", "sabotage"),
    ("cyberattack", "sabotage"), ("cyber attack", "sabotage"), ("cyber-attack", "sabotage"),
    ("blow up", "sabotage"), ("wreck", "sabotage"), ("derail", "sabotage"),
    ("summit", "summit"), ("hold talks", "summit"), ("peace talks", "summit"),
    ("envoy", "envoy"), ("back channel", "envoy"), ("backchannel", "envoy"),
    ("secret talks", "envoy"),
    ("bribe", "bribe"), ("buy off", "bribe"), ("pay off", "bribe"), ("defect", "bribe"),
    ("sting", "sting"), ("honeypot", "sting"),
    ("incite", "incite"), ("rebels", "incite"), ("foment", "incite"), ("stir up", "incite"),
    ("destabilise", "incite"), ("destabilize", "incite"), ("propaganda", "incite"),
    ("disinformation", "incite"), ("social media campaign", "incite"),
    ("regime change", "incite"), ("coup", "incite"), ("insurgents", "incite"),
    ("uprising", "incite"), ("opposition groups", "incite"), ("fund the opposition", "incite"),
    ("fund opposition", "incite"), ("militias", "incite"), ("separatists", "incite"),
    ("insurgency", "incite"),
    ("expose", "expose"), ("leak", "expose"), ("smear", "expose"),
    ("name and shame", "expose"),
)

_HINT = ("start with a verb such as move, raise, reinforce, fortify, build, research, "
         "invest, declare war on, seek terms with, propose a pact to, warn, spy on, or "
         "announce")

_CLAUSES = re.compile(r"[.;!?\n]+|,\s*(?:and\s+)?then\s+|\s+and then\s+")


def _words(pattern_text: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w){re.escape(pattern_text)}(?!\w)")


def _first_at(hay: str, phrases: tuple[str, ...]) -> tuple[int, int, str] | None:
    """Earliest whole-word occurrence of any phrase: (position, -length, phrase)."""
    best: tuple[int, int, str] | None = None
    for phrase in phrases:
        found = _words(phrase).search(hay)
        if found and (best is None or (found.start(), -len(phrase)) < best[:2]):
            best = (found.start(), -len(phrase), phrase)
    return best


def _aliases() -> dict[str, tuple[str, ...]]:
    from .capabilities import CAPABILITIES

    out: dict[str, tuple[str, ...]] = {}
    for capability in CAPABILITIES:
        if capability.action_type == "other":
            continue
        extra = _EXTRA_ALIASES.get(capability.action_type, ())
        out[capability.action_type] = tuple(
            dict.fromkeys(a.lower() for a in (*capability.aliases, *extra)))
    return out


def _mentions(conn: sqlite3.Connection, world_id: str, kind: str,
              clause: str) -> list[tuple[int, str]]:
    """Every entry of ``kind`` named in ``clause``, with where it was named."""
    hay = normalise(clause)
    raw = clause.lower()
    found: dict[str, int] = {}
    spans: dict[str, tuple[int, int]] = {}
    verbatim: dict[str, int] = {}
    for entry_id, name in reference.vocabulary(conn, world_id, kind):
        for needle in {normalise(entry_id.replace("_", " ")), normalise(name)}:
            if len(needle) < 3:
                continue
            hit = _words(needle).search(hay)
            if hit:
                found[entry_id] = min(found.get(entry_id, hit.start()), hit.start())
                start, end = spans.get(entry_id, hit.span())
                spans[entry_id] = (min(start, hit.start()), max(end, hit.end()))
        if entry_id in found and _words(name.lower()).search(raw):
            verbatim[entry_id] = len(name)
    # "Northern Cyprus" also contains "Cyprus": a name found only inside a longer name
    # that was found is part of that name, not a second place.
    inside = {e for e, (start, end) in spans.items() for other, (s2, e2) in spans.items()
              if other != e and s2 <= start and end <= e2 and (e2 - s2) > (end - start)}
    for entry_id in inside:
        found.pop(entry_id, None)
    # Normalising drops "Republic of", so "China" also reads as "Republic of China".
    # Where several entries match at one place, the longest name written out in full wins.
    by_pos: dict[int, list[str]] = {}
    for entry_id, pos in found.items():
        by_pos.setdefault(pos, []).append(entry_id)
    out: list[tuple[int, str]] = []
    for pos, entries in by_pos.items():
        longest = max((verbatim[e] for e in entries if e in verbatim), default=0)
        exact = [e for e in entries if verbatim.get(e) == longest] if longest else []
        out.extend((pos, e) for e in (exact or entries))
    return sorted(out)


def _demonym_of(said: str, name: str) -> bool:
    """"Karthian" for Karthos, "Turkish" for Turkey, "Greek" for Greece: the name with its
    last letter or two swapped for a people's ending. Single-word names only."""
    if " " in name or len(name) < 4:
        return False
    common = len(os.path.commonprefix([said, name]))
    return (common >= max(4, len(name) - 2) and said != name
            and said[common:] in _DEMONYM_ENDINGS)


_DEMONYM_ENDINGS = frozenset({"", "n", "ns", "an", "ans", "ian", "ians", "ish", "ese", "i",
                              "is", "ic", "k", "ks"})


def _nations_in(conn: sqlite3.Connection, world_id: str, clause: str, *,
                exclude: str) -> list[str]:
    """Nations named in a clause; failing a full name, a capitalised word that is the start
    of exactly one (\"the Soviet railways\" -> Soviet Union)."""
    found = [n for _, n in _mentions(conn, world_id, "nation", clause) if n != exclude]
    if found:
        return found
    names = [(entry_id, normalise(name))
             for entry_id, name in reference.vocabulary(conn, world_id, "nation")
             if entry_id != exclude]
    for word in re.findall(r"(?<=\s)[A-Z][\w'-]{3,}", clause):
        said = normalise(word)
        # "Soviet" opens "Soviet Union"; "Cubans" and "Iranians" extend "Cuba" and
        # "Iran". Only a single match counts: anything looser, or shared, is a guess.
        hits = {entry_id for entry_id, name in names
                if name.startswith(said + " ") or name == said
                or (len(name) >= 4 and said.startswith(name) and len(said) - len(name) <= 4)
                or _demonym_of(said, name)}
        if len(hits) == 1:
            return [hits.pop()]
    return []


def _alive(conn: sqlite3.Connection, world_id: str, nation_id: str) -> bool:
    row = conn.execute("SELECT is_alive FROM nation WHERE world_id = ? AND id = ?",
                       (world_id, nation_id)).fetchone()
    return bool(row and row[0])


def _unit_kind(conn: sqlite3.Connection, world_id: str, clause: str) -> str | None:
    kinds = [k for k, _ in reference.vocabulary(conn, world_id, "unit_kind")]
    hay = clause.lower()
    for kind in kinds:
        spoken = kind.replace("_", " ")
        if _words(spoken).search(hay) or _words(spoken + "s").search(hay):
            return kind
    for kind, words in _UNIT_WORDS:
        if kind in kinds and _first_at(hay, words):
            return kind
    return None


def _quantity(clause: str) -> int | None:
    hay = clause.lower()
    digits = re.search(r"\b(\d{1,3})\b", hay)
    if digits:
        return int(digits.group(1))
    hit = _first_at(hay, tuple(k for k in _NUMBERS if k not in ("a", "an")))
    return _NUMBERS[hit[2]] if hit else None


def _tone(clause: str) -> str:
    hay = clause.lower()
    if _first_at(hay, ("threaten", "or else", "ultimatum", "or face", "consequences")):
        return "threatening"
    if _first_at(hay, ("warn", "demand", "protest", "insist", "condemn")):
        return "firm"
    if _first_at(hay, ("reassure", "thank", "congratulate", "friendship", "sound out",
                       "offer", "propose", "invite", "welcome")):
        return "friendly"
    return "neutral"


def _blank_action(action_type: str, summary: str) -> dict[str, Any]:
    return {"type": action_type, "summary": summary, "target_nation": None,
            "unit_kind": None, "project": None, "gambit_kind": None, "ceasefire": None,
            "terms": [], "doctrine": None, "from_region": None, "to_region": None,
            "quantity": None, "tone": "neutral"}


def _choose_type(conn: sqlite3.Connection, world_id: str, clause: str,
                 aliases: dict[str, tuple[str, ...]]) -> tuple[str | None, str]:
    """Which act a clause orders, and the verb that said so."""
    hay = clause.lower()
    hits: dict[str, tuple[int, int, str]] = {}
    for action_type, phrases in aliases.items():
        hit = _first_at(hay, phrases)
        if hit:
            hits[action_type] = hit
    if not hits:
        return None, ""

    chosen = min(hits, key=lambda t: hits[t][:2])
    verb = hits[chosen][2]
    if chosen in _WEAK:
        for stronger in _STRONGER:
            if stronger in hits:
                chosen, verb = stronger, hits[stronger][2]
                break

    if verb in _OVERLOADED:
        if (_mentions(conn, world_id, "project", clause)
                or _mentions(conn, world_id, "technology", clause)):
            return "build_project", verb
        if _first_at(hay, tuple(w for _, words in _DOCTRINE_WORDS for w in words)):
            return "adopt_doctrine", verb
        if _unit_kind(conn, world_id, clause) or verb in ("raise", "recruit", "lay down"):
            return "raise_units", verb
        # Something built that the catalogue has no entry for: a wall is a fortification,
        # anything else -- a monument, a canal, a stadium -- is freeform's to answer. It
        # used to fall through to raising infantry.
        if _first_at(hay, _FORTIFICATION_WORDS):
            return "fortify", verb
        if verb in ("build", "construct", "begin", "develop", "research", "programme",
                    "program"):
            return "freeform", verb
    return chosen, verb


_FORTIFICATION_WORDS = ("wall", "walls", "fortification", "fortifications", "bunker",
                        "bunkers", "barrier", "fence", "defences", "defenses", "trenches",
                        "defensive line")


def _units_by_region(conn: sqlite3.Connection, world_id: str, nation_id: str) -> list[str]:
    """Where this nation's land forces stand, strongest first. Fleets do not march."""
    return [str(r[0]) for r in conn.execute(
        "SELECT u.region_id FROM unit u"
        "  JOIN unit_template t ON t.world_id = u.world_id AND t.kind = u.kind"
        " WHERE u.world_id = ? AND u.nation_id = ? AND t.domain = 'land'"
        " GROUP BY u.region_id ORDER BY SUM(u.strength) DESC", (world_id, nation_id))]


def _adjacent(conn: sqlite3.Connection, world_id: str, region: str) -> set[str]:
    out: set[str] = set()
    for a, b in conn.execute(
            "SELECT region_a, region_b FROM region_adjacency WHERE world_id = ?"
            " AND (region_a = ? OR region_b = ?)", (world_id, region, region)):
        out.add(str(b) if str(a) == region else str(a))
    return out


def _holder(conn: sqlite3.Connection, world_id: str, region: str) -> str:
    row = conn.execute(
        "SELECT COALESCE(controller_nation_id, owner_nation_id) FROM region"
        " WHERE world_id = ? AND id = ?", (world_id, region)).fetchone()
    return str(row[0] or "") if row else ""


def _border_region(conn: sqlite3.Connection, world_id: str, nation_id: str,
                   other: str) -> str | None:
    """Ground ``nation_id`` holds next to ground ``other`` holds, if there is any."""
    for (region,) in conn.execute(
            "SELECT id FROM region WHERE world_id = ? AND kind <> 'ocean'"
            " AND COALESCE(controller_nation_id, owner_nation_id) = ? ORDER BY id",
            (world_id, nation_id)):
        if any(_holder(conn, world_id, r) == other
               for r in _adjacent(conn, world_id, str(region))):
            return str(region)
    return None


def _front_region(conn: sqlite3.Connection, world_id: str, nation_id: str,
                  enemy: str) -> tuple[str, str] | None:
    """A region ``enemy`` holds next to ground where ``nation_id`` has troops."""
    for source in _units_by_region(conn, world_id, nation_id):
        for target in sorted(_adjacent(conn, world_id, source)):
            if _holder(conn, world_id, target) == enemy:
                return source, target
    return None


def _fill_move(conn: sqlite3.Connection, world_id: str, nation_id: str, action: dict,
               clause: str, regions: list[tuple[int, str]],
               nations: list[str]) -> str | None:
    """Work out where from and where to. Returns a refusal, or ``None`` if it is filled."""
    hay = normalise(clause)
    to_region = from_region = None
    for pos, region in regions:
        before = hay[max(0, pos - 6):pos]
        if re.search(r"\bfrom\s*$", before):
            from_region = region
        else:
            to_region = region
    if to_region is None and nations:
        enemy = nations[0]
        if not _at_war(conn, world_id, nation_id, enemy):
            return (f"you are not at war with {_name(conn, world_id, enemy)}; declare war "
                    "first, or name the province to move into")
        front = _front_region(conn, world_id, nation_id, enemy)
        if front is None:
            return (f"none of your forces stand next to ground "
                    f"{_name(conn, world_id, enemy)} holds")
        from_region, to_region = from_region or front[0], front[1]
    if to_region is None:
        return "name the province your forces should move into"
    if from_region is None:
        stationed = _units_by_region(conn, world_id, nation_id)
        if not stationed:
            return "you have no forces to move"
        near = [r for r in stationed if r == to_region or to_region in _adjacent(
            conn, world_id, r)]
        from_region = (near or stationed)[0]
        if from_region == to_region:
            owner = conn.execute(
                "SELECT owner_nation_id FROM region WHERE world_id = ? AND id = ?",
                (world_id, to_region)).fetchone()
            if owner is not None and owner[0] == nation_id:
                place = conn.execute("SELECT name FROM region WHERE world_id = ? AND id = ?",
                                     (world_id, to_region)).fetchone()
                return f"{place[0] if place else 'that ground'} is already yours"
            return "your forces are already there"
    action["from_region"], action["to_region"] = from_region, to_region
    return None


#: Verbs that mean "make it ours". What they become depends on where the war stands: an
#: invasion while there is fighting to do, an annexation once the other side has surrendered.
_CONQUEST = frozenset({"capture", "conquer", "seize", "take over", "take", "overrun",
                       "storm", "invade", "attack", "occupy"})

#: Words that point back at the previous order's target: "declare war on Iran and invade it".
_BACK_REFERENCE = re.compile(r"\b(it|them|there|its land|their land)\b", re.IGNORECASE)


def _parse_clause(conn: sqlite3.Connection, world_id: str, nation_id: str, clause: str,
                  aliases: dict[str, tuple[str, ...]],
                  previous: dict | None = None) -> tuple[dict | None, str | None]:
    """One clause -> (action, None) or (None, reason it cannot be carried out)."""
    from .capabilities import available_to, by_type
    from .conquest import annexable

    action_type, verb = _choose_type(conn, world_id, clause, aliases)
    if action_type is None:
        # **Anything else at all is still an order.** Refusing what no verb matched sent
        # "host the Olympics in Istanbul" back with a list of verbs to use instead; the
        # freeform channel exists to take it seriously, and it is bounded however it is
        # worded. A word or two is too little to be an order of any kind.
        if len(re.findall(r"[A-Za-z]{2,}", clause)) < 3:
            return None, f"no order recognised; {_HINT}"
        return _blank_action("freeform", clause), None

    action = _blank_action(action_type, clause)
    hay = clause.lower()
    nations = _nations_in(conn, world_id, clause, exclude=nation_id)
    regions = _mentions(conn, world_id, "region", clause)
    if not nations and not regions and previous and _BACK_REFERENCE.search(clause):
        if previous.get("target_nation"):
            nations = [str(previous["target_nation"])]
        elif previous.get("to_region"):
            regions = [(0, str(previous["to_region"]))]
    if not nations and regions and action_type in ("move_units", "annex"):
        owner = conn.execute("SELECT owner_nation_id FROM region WHERE world_id = ? AND id = ?",
                             (world_id, regions[-1][1])).fetchone()
        if owner is not None and owner[0] and owner[0] != nation_id:
            nations = [str(owner[0])]

    # A conquest verb against a country that has already surrendered to you is a request to
    # annex it; while it still holds ground it is an invasion.
    if action_type == "move_units" and verb in _CONQUEST and nations and annexable(
            conn, world_id, nation_id, nations[0])[0]:
        action_type = "annex"
        action = _blank_action("annex", clause)
    if action_type == "annex":
        if not nations:
            return None, "name the country to annex"
        regions_ok, why = annexable(conn, world_id, nation_id, nations[0])
        if not regions_ok:
            return None, why
        action["target_nation"] = nations[0]
        return action, None
    # **A country that no longer exists is now a place.** "Invest in Iraq" after Iraq was
    # annexed means the land, and reading it as an act aimed at a state that is gone was
    # refused with "Iraq no longer exists" -- a player asking how else to say Iraq. Its
    # former ground becomes the order's ground, and nobody is its target.
    gone = [n for n in nations if not _alive(conn, world_id, n)]
    if gone:
        nations = [n for n in nations if n not in gone]
        if not regions:
            regions = [(0, str(row[0])) for dead in gone for row in conn.execute(
                "SELECT id FROM region WHERE world_id = ? AND home_nation_id = ?"
                "   AND owner_nation_id IS NOT NULL ORDER BY development DESC, id",
                (world_id, dead))]
        if regions and action_type in ("build_project", "fortify", "raise_units",
                                       "economic_investment", "reinforce"):
            action["to_region"] = action["from_region"] = regions[0][1]
    action["tone"] = _tone(clause)
    if nations:
        action["target_nation"] = nations[0]
    capability = by_type(action_type)
    needs = capability.required if capability else ()

    if action_type == "sue_for_peace":
        if not nations:
            at_war = _enemies(conn, world_id, nation_id)
            if len(at_war) == 1:
                action["target_nation"] = at_war[0]
        action["ceasefire"] = bool(_first_at(hay, ("ceasefire", "truce", "armistice")))
        enemy = nations[0] if nations else action.get("target_nation")
        if enemy and not action["ceasefire"]:
            action["terms"] = _peace_terms(conn, world_id, nation_id, enemy, hay, regions)
    elif action_type == "declare_war" and nations and _at_war(
            conn, world_id, nation_id, nations[0]):
        return None, f"you are already at war with {_name(conn, world_id, nations[0])}"
    elif action_type == "move_units":
        declared = bool(previous and previous.get("type") == "declare_war"
                        and nations and previous.get("target_nation") == nations[0])
        if declared and not regions:
            # War declared in this same set of orders: aim at their border province now,
            # rather than refusing because the declaration has not taken effect yet.
            front = _front_region(conn, world_id, nation_id, nations[0])
            if front is not None:
                regions = [(0, front[1])]
        refusal = _fill_move(conn, world_id, nation_id, action, clause, regions, nations)
        if refusal:
            return None, refusal
    elif action_type in ("raise_units", "reinforce", "disband_units"):
        action["unit_kind"] = _unit_kind(conn, world_id, clause) or (
            "infantry" if action_type == "raise_units" else None)
        action["quantity"] = _quantity(clause)
        if regions:
            action["to_region"] = action["from_region"] = regions[-1][1]
        if action_type == "raise_units" and action["unit_kind"] not in {
                k for k, _ in reference.vocabulary(conn, world_id, "unit_kind")}:
            kinds = ", ".join(k for k, _ in reference.vocabulary(conn, world_id, "unit_kind"))
            return None, f"name a kind of formation this world builds: {kinds}"
    elif action_type == "fortify" and regions:
        action["to_region"] = action["from_region"] = regions[-1][1]
    elif action_type == "fortify" and nations:
        # "A wall on the Syrian border": your ground that touches theirs.
        border = _border_region(conn, world_id, nation_id, nations[0])
        if border:
            action["to_region"] = action["from_region"] = border
        # Digging in is done on your own ground and addressed to nobody.
        action["target_nation"] = None
    elif action_type == "build_project":
        found = (_mentions(conn, world_id, "project", clause)
                 or _mentions(conn, world_id, "technology", clause))
        if not found:
            works = ", ".join(p for p, _ in reference.vocabulary(conn, world_id, "project"))
            research = ", ".join(
                n for _, n in reference.vocabulary(conn, world_id, "technology")) or "none"
            return None, (f"name the programme to begin -- public works: {works}; "
                          f"research open to this era: {research}")
        action["project"] = found[0][1]
        if regions:
            action["to_region"] = regions[-1][1]
    elif action_type == "adopt_doctrine":
        for doctrine, words in _DOCTRINE_WORDS:
            if _first_at(hay, words):
                action["doctrine"] = doctrine
                break
        else:
            return None, ("name the stance to adopt: mass, professional, retaliation, "
                          "minimum, collective security or neutrality")
    elif action_type == "gambit":
        # The kind named earliest in the order, the longer phrase at a tie: "leak the
        # coup plans" is an exposure, not a coup.
        hit = _first_at(hay, tuple(word for word, _ in _GAMBIT_WORDS))
        if hit:
            action["gambit_kind"] = dict(_GAMBIT_WORDS)[hit[2]]
        if regions:
            action["to_region"] = regions[-1][1]
            if not action["target_nation"]:
                # "Air strike in Westfell": aimed at whoever holds Westfell.
                holder = _holder(conn, world_id, action["to_region"])
                if holder and holder != nation_id:
                    action["target_nation"] = holder
    elif action_type == "economic_investment":
        action["quantity"] = _quantity(clause)

    if action_type == "gambit" and not action["target_nation"]:
        # "A propaganda campaign" with nobody to aim it at is a domestic act, which is
        # freeform's to answer, not an undertaking against nobody.
        return _blank_action("freeform", clause), None
    if "target_nation" in needs and not action["target_nation"]:
        return None, "name the country this is addressed to"
    if action["target_nation"]:
        allowed = {c.action_type for c in available_to(
            conn, world_id, nation_id, action["target_nation"])}
    else:
        allowed = {c.action_type for c in available_to(conn, world_id, nation_id)}
    if action_type not in allowed:
        what = capability.explain if capability else action_type
        target = (f" {_name(conn, world_id, action['target_nation'])}"
                  if action["target_nation"] else "")
        return None, f"you cannot {what}{target} right now"
    return action, None


#: "pay 20", "pays 20", "20 in reparations", "reparations of 20".
_REPARATIONS = re.compile(
    r"\bpays?\s+(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s+in\s+reparations"
    r"|reparations\s+of\s+(\d+(?:\.\d+)?)")


def _peace_terms(conn: sqlite3.Connection, world_id: str, nation_id: str, enemy: str,
                 hay: str, regions: list) -> list[dict]:
    """The terms a peace order names: the enemy's provinces it lists, ceded to the player,
    and any money it asks for.

    **A peace order used to carry no terms at all**, so "sue for peace with Iran and take
    Tabriz" was offered as a white peace -- the clause was read for its country and the rest
    thrown away, and every war a player ended on paper ended without a province changing
    hands. What a term names is all that is read here; whether it can be carried out, and
    whether the other side signs, is `peace.review_terms` and `peace.acceptance`, exactly as
    for anybody else's offer.
    """
    terms: list[dict] = []
    for _position, region_id in regions:
        owner = conn.execute(
            "SELECT owner_nation_id FROM region WHERE world_id = ? AND id = ?",
            (world_id, region_id)).fetchone()
        if owner is not None and owner[0] == enemy and not any(
                t["subject"] == region_id for t in terms):
            terms.append({"kind": "cede_region", "obliged": enemy, "beneficiary": nation_id,
                          "subject": region_id})
    money = _REPARATIONS.search(hay)
    if money:
        value = float(next(group for group in money.groups() if group))
        if value > 0:
            terms.append({"kind": "reparations", "obliged": enemy, "beneficiary": nation_id,
                          "subject": "", "value": value})
    return terms


def parse_orders(conn: sqlite3.Connection, world_id: str, *, nation_id: str,
                 raw_text: str) -> dict[str, list[dict]]:
    """The parser's answer -- ``{"actions": [...], "rejected": [...]}`` -- by rule."""
    aliases = _aliases()
    actions: list[dict] = []
    rejected: list[dict] = []
    verbs = sorted({a for phrases in aliases.values() for a in phrases}, key=len, reverse=True)
    # "Declare war on Iran and invade it" is two orders. Split on "and" only where a verb
    # follows, so "Bosnia and Herzegovina" and "rail and steel" stay whole.
    joined = re.compile(r",?\s+and\s+(?=(?:" + "|".join(re.escape(v) for v in verbs)
                        + r")\b)", re.IGNORECASE)
    previous: dict | None = None
    for sentence in (c.strip(" ,") for c in _CLAUSES.split(raw_text)):
        for clause in (c.strip(" ,") for c in joined.split(sentence)):
            if len(clause) < 3:
                continue
            action, refusal = _parse_clause(conn, world_id, nation_id, clause, aliases,
                                            previous)
            if action is not None:
                actions.append(action)
                previous = action
            else:
                rejected.append({"order": clause, "reason": refusal or "not understood"})
    return {"actions": actions, "rejected": rejected}


# ---------------------------------------------------------------------------- diplomacy

#: What a message is asking for, most specific first.
_TREATY_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("defensive_pact", ("defensive pact", "defensive alliance", "mutual defence",
                        "mutual defense", "defend each other")),
    ("alliance", ("alliance", "ally", "allies", "allied")),
    ("non_aggression_pact", ("non-aggression", "non aggression", "nonaggression",
                             "neutrality pact", "not attack", "pact")),
    ("trade_agreement", ("trade", "commerce", "commercial")),
    ("military_access", ("military access", "passage", "transit", "right of way")),
    ("ceasefire", ("ceasefire", "cease-fire", "truce", "armistice")),
    ("peace_treaty", ("peace",)),
)

_TREATY_LABEL = {
    "defensive_pact": "a defensive pact", "alliance": "an alliance",
    "non_aggression_pact": "a non-aggression pact", "trade_agreement": "a trade agreement",
    "military_access": "military access", "ceasefire": "a ceasefire",
    "peace_treaty": "a peace treaty",
}

#: What ``relation.treaty_status`` reads once each kind is in force, weakest first.
_STANDING = {"trade_agreement": "trade", "non_aggression_pact": "non_aggression",
             "defensive_pact": "defensive", "alliance": "alliance"}
_STANDING_ORDER = ("none", "trade", "non_aggression", "defensive", "alliance", "vassal")


def requested_treaty(text: str) -> str | None:
    hay = text.lower()
    for kind, words in _TREATY_WORDS:
        if _first_at(hay, words):
            return kind
    return None


def treaty_decision(conn: sqlite3.Connection, world_id: str, *, asked: str,
                    proposer: str, kind: str) -> tuple[bool, str]:
    """Whether ``asked`` agrees to ``kind`` with ``proposer``, and why, in its own words."""
    theirs = _regard(conn, world_id, asked, proposer)
    ours = _regard(conn, world_id, proposer, asked)
    label = _TREATY_LABEL.get(kind, kind)
    who = _name(conn, world_id, proposer)
    relation = _relation(conn, world_id, asked, proposer)
    standing = str(relation["treaty_status"]) if relation is not None else "none"
    at_war = _at_war(conn, world_id, asked, proposer)

    if kind in ("ceasefire", "peace_treaty"):
        if not at_war:
            return False, f"We are not at war with {who}; there is nothing to settle."
        return False, ("If you want the guns to stop, put it in writing: order a "
                       f"{'ceasefire' if kind == 'ceasefire' else 'peace offer'} and our "
                       "government will answer it on how the war stands.")
    if at_war:
        return False, f"We do not sign {label} with a power whose armies we are fighting."
    if kind in _STANDING and _STANDING_ORDER.index(standing) >= _STANDING_ORDER.index(
            _STANDING[kind]):
        return False, f"We already stand in {label} or closer with {who}."

    need = {"alliance": ALLIANCE_REGARD, "defensive_pact": DEFENSIVE_REGARD,
            "non_aggression_pact": NON_AGGRESSION_REGARD, "trade_agreement": TRADE_REGARD,
            "military_access": ACCESS_REGARD}.get(kind)
    if need is None:
        return False, f"We are not in a position to agree to {label}."
    # DIP-001 checks both directions for the binding pacts, so the bot does too.
    lowest = min(theirs, ours) if kind in ("alliance", "defensive_pact") else theirs
    if lowest >= need:
        return True, f"Agreed. {_name(conn, world_id, asked)} will enter into {label} with {who}."
    if kind in ("alliance", "defensive_pact") and theirs >= need > ours:
        return False, (f"We would welcome {label}, but {who}'s own regard for us does not "
                       "yet bear it. Show us in deeds first.")
    return False, _pick((
        f"{label.capitalize()} is more than our relations can carry at present.",
        f"Our government is not ready to consider {label} with {who}.",
        f"We decline. Trust between us is too thin for {label}.",
    ), asked, proposer, kind)


def diplomacy_reply(conn: sqlite3.Connection, world_id: str, *, speaker: str,
                    listener: str, message: str, ultimatum: bool = False,
                    treaty: bool = False) -> tuple[str, list[dict]]:
    """``speaker``'s reply to ``listener``, and any agreement it amounts to.

    The agreement is returned in the commitment form the extraction call produces, so the
    session records it through the same diffs and the same gates.
    """
    hay = message.lower()
    regard = _regard(conn, world_id, speaker, listener)
    mood = _mood(regard)
    who = _name(conn, world_id, listener)
    me = _name(conn, world_id, speaker)

    kind = requested_treaty(message)
    asking = _first_at(hay, ("propose", "offer", "sign", "agree", "would you", "will you",
                             "shall we", "let us", "join", "form", "conclude", "seek",
                             "want", "ask")) or hay.rstrip().endswith("?")
    if kind is not None and (asking or treaty):
        agreed, said = treaty_decision(conn, world_id, asked=speaker, proposer=listener,
                                       kind=kind)
        commitments = []
        if agreed:
            commitments.append({
                "kind": kind, "status": "active",
                "condition": f"{me} and {who} agree to {_TREATY_LABEL[kind]}.",
                "parties": [listener, speaker], "region": "", "ceded_by": "",
                "intel_sharing": False, "intel_about": "",
            })
        return said, commitments

    if _at_war(conn, world_id, speaker, listener):
        return _pick((
            f"We are at war, {who}. Say what you have to say through a peace offer.",
            "While your armies stand against ours there is little to discuss.",
            "Words are cheap at a time like this. Make an offer, or do not trouble us.",
        ), speaker, listener, message), []

    threat = ultimatum or _first_at(hay, ("or else", "ultimatum", "withdraw", "surrender",
                                          "cede", "consequences", "threat", "warn"))
    if threat:
        theirs = _strength(conn, world_id, speaker)
        yours = _strength(conn, world_id, listener)
        if yours >= 2 * max(theirs, 1.0):
            return _pick((
                "We have heard you. We will consider our position carefully.",
                "Your message is noted. There is no need for this to come to blows.",
            ), speaker, message), []
        return _pick((
            f"{me} does not answer to threats.",
            "We will not be spoken to in that tone.",
            "If that is a threat, we are not moved by it.",
        ), speaker, message), []

    third = [n for _, n in _mentions(conn, world_id, "nation", message)
             if n not in (speaker, listener)]
    if third:
        other = third[0]
        view = _mood(_regard(conn, world_id, speaker, other))
        return (f"Our view of {_name(conn, world_id, other)}? Relations are {view}"
                + (", and we are at war with them." if _at_war(conn, world_id, speaker, other)
                   else ".")), []

    return _pick({
        "warm": (f"Always good to hear from {who}. Our friendship stands.",
                 f"{who} has no truer friend than {me}. What can we do for you?"),
        "cordial": ("We value good relations with you. Let us keep talking.",
                    f"{me} is glad of the contact. We are listening."),
        "guarded": ("We have heard you. Our government will weigh it.",
                    "Noted. We prefer to see deeds before words."),
        "cool": ("We have little to say to you at present.",
                 f"Relations between {me} and {who} do not invite warmth."),
        "hostile": ("You will forgive us if we do not take you at your word.",
                    f"{me} has nothing to discuss with {who}."),
    }[mood], speaker, listener, message), []


def _strength(conn: sqlite3.Connection, world_id: str, nation_id: str) -> float:
    row = conn.execute("SELECT military_strength FROM nation WHERE world_id = ? AND id = ?",
                       (world_id, nation_id)).fetchone()
    return float(row[0] or 0.0) if row is not None else 0.0


# ------------------------------------------------------------------------------ council

def advise(conn: sqlite3.Connection, world_id: str, *, nation_id: str, question: str) -> str:
    """The council's answer: the position in a few sentences, and orders worth giving.

    Recommendations are written as orders the rule-based parser understands, so a player
    can copy one into the orders box as it stands.
    """
    from .bots import _believed_strength

    row = conn.execute(
        "SELECT name, treasury, stability, legitimacy, economy, military_strength,"
        "       capital_region_id FROM nation WHERE world_id = ? AND id = ?",
        (world_id, nation_id)).fetchone()
    if row is None:
        return "Your council cannot find your government in the record."
    turn = int(conn.execute("SELECT turn_number FROM world WHERE id = ?",
                            (world_id,)).fetchone()[0])
    treasury, stability = float(row["treasury"] or 0), float(row["stability"] or 0)
    legitimacy, strength = float(row["legitimacy"] or 0), float(row["military_strength"] or 0)
    capital = str(row["capital_region_id"] or "")

    others = [r for r in conn.execute(
        "SELECT r.nation_b AS id, r.score AS ours, r.at_war,"
        "       COALESCE(back.score, 0) AS theirs, r.treaty_status"
        "  FROM relation r JOIN nation n ON n.id = r.nation_b AND n.world_id = r.world_id"
        "  LEFT JOIN relation back ON back.world_id = r.world_id"
        "       AND back.nation_a = r.nation_b AND back.nation_b = r.nation_a"
        " WHERE r.world_id = ? AND r.nation_a = ? AND n.is_alive = 1",
        (world_id, nation_id))]
    enemies = [r for r in others if r["at_war"]]
    lines: list[str] = []
    advice: list[str] = []

    named = [n for _, n in _mentions(conn, world_id, "nation", question) if n != nation_id]
    if named:
        other = named[0]
        believed = _believed_strength(conn, world_id, nation_id, other, turn=turn)
        ratio = strength / believed if believed > 0 else float("inf")
        balance = ("we are clearly the stronger" if ratio >= 1.5 else
                   "we are roughly matched" if ratio >= 0.67 else
                   "they are clearly the stronger")
        lines.append(
            f"On {_name(conn, world_id, other)}: they regard us as "
            f"{_mood(_regard(conn, world_id, other, nation_id))}, we regard them as "
            f"{_mood(_regard(conn, world_id, nation_id, other))}, and by our service's "
            f"estimate {balance}"
            + (" -- and we are at war." if _at_war(conn, world_id, nation_id, other) else "."))

    if enemies:
        names = ", ".join(_name(conn, world_id, str(r["id"])) for r in enemies)
        lines.append(f"We are at war with {names}.")
        for enemy in enemies:
            believed = _believed_strength(conn, world_id, nation_id, str(enemy["id"]),
                                          turn=turn)
            ename = _name(conn, world_id, str(enemy["id"]))
            if believed > 0 and strength < 0.6 * believed:
                advice.append(f"Seek a ceasefire with {ename}")
            elif _front_region(conn, world_id, nation_id, str(enemy["id"])) is not None:
                # Only where the parser can carry it out: troops next to their ground.
                advice.append(f"Attack {ename}")
        advice.append("Reinforce our divisions")
    else:
        lines.append("We are at peace.")

    lines.append(f"The treasury stands at {treasury:,.0f}; stability {stability:.0f}, "
                 f"legitimacy {legitimacy:.0f}.")
    if stability < 40:
        lines.append("The country is restless, and that is the first thing to mend.")
        advice.append("Begin the health programme")
    if legitimacy < 40:
        advice.append("Begin the schools programme")

    hostile = sorted((r for r in others if not r["at_war"] and r["theirs"] <= -40),
                     key=lambda r: r["theirs"])
    if hostile:
        lines.append(f"{_name(conn, world_id, str(hostile[0]['id']))} is openly hostile "
                     "to us.")
        place = conn.execute("SELECT name FROM region WHERE world_id = ? AND id = ?",
                             (world_id, capital)).fetchone()
        if place is not None:
            advice.append(f"Fortify {place['name']}")

    def _standing(r) -> int:
        status = str(r["treaty_status"] or "none")
        return _STANDING_ORDER.index(status) if status in _STANDING_ORDER else 0

    friends = sorted((r for r in others if not r["at_war"]),
                     key=lambda r: -min(r["ours"], r["theirs"]))
    for friend in friends[:6]:
        low = min(friend["ours"], friend["theirs"])
        fname = _name(conn, world_id, str(friend["id"]))
        if low >= ALLIANCE_REGARD and _standing(friend) < _STANDING_ORDER.index("alliance"):
            advice.append(f"Propose an alliance to {fname}")
            break
        if low >= DEFENSIVE_REGARD and _standing(friend) < _STANDING_ORDER.index("defensive"):
            advice.append(f"Propose a defensive pact to {fname}")
            break

    if treasury > 0 and stability >= 40 and not enemies:
        advice.append("Invest in the economy")
    if not enemies and strength < 30:
        advice.append("Raise 2 infantry divisions")

    answer = " ".join(lines)
    if advice:
        shown = list(dict.fromkeys(advice))[:4]
        answer += "\n\nWe would order: " + "; ".join(shown) + "."
    return answer + "\n\n(Your council is a rules-based stand-in; no model was consulted.)"


# --------------------------------------------------------------------------- world sim

def world_sim_batch(conn: sqlite3.Connection, world_id: str, *, player: str | None,
                    actions: list[dict], order_events: list[int], days: float,
                    turn: int) -> dict[str, Any]:
    """The world simulator's answer, by rule: narrative and events, no diffs.

    Everything that moves the world mechanically already runs on the engine -- untracked
    economies, the scenery bots, resolution. What the simulator was still needed for is the
    player's notes and treaty proposals sent as orders, which no engine handler carries out.
    Those are answered here with the same decision talks use, and an accepted treaty is
    written through the same commitment path.
    """
    from .diplomacy import write_commitments

    events: list[dict[str, Any]] = []
    for action, event_id in zip(actions, order_events, strict=False):
        kind = str(action.get("type") or "")
        if not player or kind not in ("propose_treaty", "diplomatic_message"):
            continue
        target = reference.resolve(conn, world_id, "nation", action.get("target_nation")).value
        if not target or target == player:
            continue
        text = str(action.get("summary") or "")
        tname, pname = _name(conn, world_id, target), _name(conn, world_id, player)
        if kind == "propose_treaty":
            treaty = requested_treaty(text) or "non_aggression_pact"
            agreed, said = treaty_decision(conn, world_id, asked=target, proposer=player,
                                           kind=treaty)
            if agreed:
                write_commitments(
                    conn, world_id,
                    entries=[{"kind": treaty, "status": "active",
                              "condition": f"{pname} and {tname} agree to "
                                           f"{_TREATY_LABEL[treaty]}.",
                              "parties": [player, target]}],
                    parties=(player, target), source_event_id=event_id)
                events.append({"kind": "treaty_signed", "actor_nation_id": target,
                               "text": f"{tname} accepted {pname}'s proposal of "
                                       f"{_TREATY_LABEL[treaty]}.",
                               "visibility": "public", "visible_to": [], "causes": [event_id]})
            else:
                events.append({"kind": "diplomatic_message", "actor_nation_id": target,
                               "text": f"{tname} answered {pname}'s proposal of "
                                       f"{_TREATY_LABEL[treaty]}: “{said}”",
                               "visibility": "bilateral", "visible_to": [player, target],
                               "causes": [event_id]})
        else:
            said, _ = diplomacy_reply(conn, world_id, speaker=target, listener=player,
                                      message=text,
                                      ultimatum=action.get("tone") == "threatening")
            events.append({"kind": "diplomatic_message", "actor_nation_id": target,
                           "text": f"{tname} replied to {pname}'s note: “{said}”",
                           "visibility": "bilateral", "visible_to": [player, target],
                           "causes": [event_id]})

    wars = {tuple(sorted((str(a), str(b)))) for a, b in conn.execute(
        "SELECT nation_a, nation_b FROM relation WHERE world_id = ? AND at_war = 1",
        (world_id,))}
    def amount(n: int, one: str, many: str) -> str:
        words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}
        return f"{words.get(n, str(n))} {one if n == 1 else many}"

    span = (amount(round(days / 365), "year", "years") if days >= 360 else
            amount(round(days / 30), "month", "months") if days >= 28 else
            amount(round(days), "day", "days"))
    passes = "passes" if span.startswith("one ") else "pass"
    if wars:
        shown = "; ".join(f"{_name(conn, world_id, a)} against {_name(conn, world_id, b)}"
                          for a, b in sorted(wars)[:4])
        more = f", and {len(wars) - 4} more" if len(wars) > 4 else ""
        goes = "goes" if len(wars) == 1 else "go"
        narrative = (f"{span.capitalize()} {passes}. "
                     f"{amount(len(wars), 'war', 'wars').capitalize()} {goes} on: "
                     f"{shown}{more}.")
    else:
        narrative = f"{span.capitalize()} {passes} under an uneasy peace."
    return {"narrative": narrative, "diffs": [], "events": events,
            "contests": [], "declarations": []}


# ---------------------------------------------------------------------------- freeform

#: What kinds of order move how people feel, and which way. Read in order; the first
#: family whose words appear decides. Every nudge is `slight` -- the bot reads words, not
#: intent, and freeform's own caps bound whatever it picks.
_MOOD_FAMILIES: tuple[tuple[tuple[str, ...], tuple[tuple[str, str], ...], str], ...] = (
    (("crack down", "crackdown", "arrest", "martial law", "censor", "ban ", "purge",
      "curfew", "suppress"),
     (("stability", "up"), ("legitimacy", "down")),
     "Order was kept, and people noticed what it cost."),
    (("reform", "corruption", "election", "referendum", "transparency", "free press",
      "amnesty", "constitution"),
     (("legitimacy", "up"),),
     "It read as a government that meant to be held to account."),
    (("olympics", "world cup", "festival", "celebrat", "holiday", "parade", "national day",
      "monument", "statue", "tourism", "stadium", "concert", "mourn", "memorial", "honour",
      "honor", "speech", "address the nation"),
     (("stability", "up"),),
     "It gave the country something to gather around."),
    (("propaganda", "patriotic", "national pride", "rally", "unity"),
     (("stability", "up"),),
     "The message landed with the people it was meant for."),
)

#: Words that say the order is a kindness or an insult to another country.
_REGARD_WORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("aid", "gift", "help", "donate", "apolog", "praise", "honour", "honor", "invite",
      "congratulat", "humanitarian"), "up"),
    (("insult", "mock", "denounce", "condemn", "ridicule", "humiliat", "boycott", "expel"),
     "down"),
)


def _mood_nudges(conn: sqlite3.Connection, world_id: str, nation_id: str,
                 order_text: str) -> tuple[list[dict], str]:
    """Slight, bounded mood changes an order's own words point at, and a line saying why."""
    hay = f" {order_text.lower()} "
    nudges: list[dict] = []
    why = ""
    for words, moves, said in _MOOD_FAMILIES:
        if any(word in hay for word in words):
            nudges += [{"subject": nation_id, "field": field, "direction": way,
                        "magnitude": "slight", "why": said} for field, way in moves]
            why = said
            break
    others = _nations_in(conn, world_id, order_text, exclude=nation_id)
    if others:
        for words, way in _REGARD_WORDS:
            if any(word in hay for word in words):
                nudges.append({"subject": f"{nation_id}|{others[0]}", "field": "relation",
                               "direction": way, "magnitude": "slight",
                               "why": "It was noticed abroad."})
                break
    return nudges[:3], why


def freeform_answer(conn: sqlite3.Connection, world_id: str, *, nation_id: str,
                    order_text: str) -> dict[str, Any]:
    """An order no mechanic covers: recorded, narrated, and felt -- a little.

    A rule cannot judge an arbitrary sentence, so the bot only reads the plainest signals
    -- a crackdown steadies the streets and costs trust, a festival lifts the mood, aid
    warms the country it is sent to -- and always as a `slight` nudge. Anything it cannot
    read changes nothing measurable, which is still an answer.
    """
    name = _name(conn, world_id, nation_id)
    said = order_text.strip().rstrip(".")
    nudges, why = _mood_nudges(conn, world_id, nation_id, order_text)
    if nudges:
        narrative = f"{name}'s ministries saw to it: {said}. {why or 'It was noticed.'}"
    else:
        narrative = _pick((
            f"The order went out and was carried out: {said}. It drew notice in the "
            "capital and little beyond it.",
            f"{name}'s ministries saw to it: {said}. The country took it in its stride.",
            f"It was done: {said}. Opinion was divided, and nothing measurable changed.",
        ), nation_id, order_text)
    return {
        "narrative": narrative,
        "event_text": f"{name}: {said}.",
        "nudges": nudges,
        "refused": None,
    }
