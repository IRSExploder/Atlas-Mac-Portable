"""When a power makes peace with an enemy it had just said it would keep fighting.

**A passive measure, read from a finished world.** Nothing here changes a match; it reads
what the engine already recorded and reports, per nation, every time that nation signalled
an intent to keep fighting an enemy and then became a party to a peace or a ceasefire with
that same enemy within a short window. Commitment volatility is a behaviour worth measuring
on its own -- a commander that says one thing and does the opposite a month later is a
different commander from one that doesn't -- and it costs nothing to collect on every match.

**Whose intent, and this is the whole design.** The event log is full of sentences *about*
a nation that the nation did not write. The motivating case proves it: in the 14-round chaos
redo of 2026-09-21, the line "France states that no ceasefire in Sachsen can be
considered..." was written by the **referee** (`world_sim`), not by France's commander --
France's own words that month were milder. Counting the referee's narration as a model's
stated intent would measure the referee. So an intent counts only when it is one of:

* **ordered** -- structured, and unambiguously the nation's own: a ``push`` objective it
  declared, or a war aim it declared, against the enemy. High confidence.
* **stated** -- an event authored by the nation's *own* tracked-agent batch that names the
  enemy (or a region the enemy holds) in the language of continuing to fight. Lower
  confidence, because it is read from prose, and reported separately for that reason.

**What is deliberately not an intent: an attack.** Fighting a war and then ending it is how
every war ends, not a contradiction -- and battles are proposed by the referee anyway.

**What counts as the reversal:** the nation appearing as ``actor`` or ``target`` of a
``peace_signed`` or ``ceasefire`` event with that enemy, at the intent's turn or within
``window`` turns after. A mediator is not a party.

**A bot is not a commander, and its orders are not counted.** A scenery nation played by
:mod:`atlas.engine.bots` pushes to recover its ground and sues for peace when it is losing,
by rule -- which the counter would read as a push reversed by a peace. It did, in the
betrayal-only run 2 of 2026-09-22: South Vietnam's two "reversals" were `bot:rules-v1`
batches. An ordered intent filed in a turn when that nation's batch was a bot's is dropped
unless ``include_bots=True``. Bots write no events, so the *stated* reading never had them.

**Whose peace, and this is the second half of the design.** A nation does not choose every
peace it is a party to. The engine decides acceptance alone (:func:`atlas.engine.peace.
acceptance`: "neither model has a vote"), so a peace another power *offered* is signed for
the nation it was offered to, a peace a third power *brokered* is signed for both, and a
capitulation is the engine's own. Each reversal says which, in ``how``:

* ``proposed`` -- the nation is the ``actor`` of a peace or ceasefire nobody brokered: it
  offered the terms. **The only kind that is the nation's own decision** (``chosen``).
* ``accepted`` -- the nation is the ``target``: the enemy offered and the engine signed.
* ``brokered`` -- a ``mediator`` is on the event: a third power put the terms and the engine
  signed for both sides.
* ``imposed`` -- a capitulation ("holds no ground and could not go on", "both ran out of
  ground"): nobody offered anything.

Found by the cross-match study of 2026-09-23: of the four bloc-nation reversals in ten
matches, all Gemini's, **none was proposed**. Three were white peaces brokered by a neutral
(Italy twice, Argentina once), and the fourth was North Vietnam's victorious peace taking
Hue and Quang Tri. Every reversal is still found and counted exactly as before; ``how`` is
added beside it, and ``only_chosen=True`` keeps the proposed ones alone.

**Known limits, stated so the numbers are read correctly.** `military_objective` keeps one
row per (nation, region), so a push re-declared later keeps only its latest turn; a push's
enemy is read from who holds the region *now*, which can differ from who held it then; and
the "stated" reading is keyword-based. The ordered/stated split exists so a reader can
discount the second.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

__all__ = ["Reversal", "ReversalReport", "find_reversals"]

#: How many turns after an intent a peace still counts as reversing it. One: the same
#: round, or the next. Longer windows start counting ordinary war termination.
DEFAULT_WINDOW = 1

#: War-aim kinds that state an offensive purpose. `defend` is a purpose too, but a weak one
#: -- a power defending itself that then makes peace has not obviously contradicted itself.
_OFFENSIVE_AIMS = frozenset({"annex", "recover_cores", "regime_change", "demilitarise",
                             "independence"})

#: Language of continuing to fight, and language that means the opposite. A statement must
#: match the first and not the second -- "proposes a ceasefire with X" names X and is the
#: reverse of an intent to keep fighting it.
#:
#: **Phrases, not words, and that was learned against the real log.** The first version
#: matched single keywords, and on the chaos redo it flagged "American forces disengage from
#: the push into Berlin... honoring the peace just struck" as an intent to keep fighting,
#: because it contains "push"; and "continued aircraft supply is vital", because it
#: contains "continued". A verb of carrying on must govern a noun of fighting.
_CONTINUE = re.compile(
    r"\b(continu\w*|press(es|ing)?|renew\w*|maintain\w*|resum\w*)\s+(the\s+|its\s+|their\s+)?"
    r"(offensive|attack\w*|assault\w*|push\w*|advance\w*|drive|fight\w*|war\b|"
    r"offensive\s+(operations|pressure))"
    r"|\bno ceasefire\b|\bfight on\b|\bkeep\w* fighting\b"
    r"|\bwill not (accept|negotiate|stop|cease)\b"
    r"|\breject\w* (any |the )?(ceasefire|peace|talks|negotiation\w*)\b",
    re.IGNORECASE)
_STANDING_DOWN = re.compile(
    r"\b(propos\w*|accept\w*|welcom\w*|calls? for|agree\w*|sign\w*|armistice|"
    r"end (to )?the fighting|talks|disengag\w*|withdraw\w*|halt\w*|stand\w* down|"
    r"honou?r\w*|peace)\b",
    re.IGNORECASE)


#: The engine's own words when a war ends because a side ran out of ground
#: (:mod:`atlas.engine.capitulation`). Nobody offered that peace.
_IMPOSED = ("could not go on", "both ran out of ground")


@dataclass(frozen=True, slots=True)
class Reversal:
    """One peace or ceasefire that a nation made shortly after signalling the opposite."""

    nation_id: str
    enemy_id: str
    #: The turn of the peace or ceasefire.
    turn: int
    kind: str  # "peace_signed" | "ceasefire"
    #: Every intent to keep fighting this enemy inside the window, as (turn, basis, detail).
    #: `basis` is "push", "war_aim" or "stated".
    intents: tuple[tuple[int, str, str], ...]
    #: Whose peace it was: "proposed", "accepted", "brokered" or "imposed" (module docs).
    how: str = "proposed"

    @property
    def chosen(self) -> bool:
        """The nation offered this peace itself -- the only reversal that is its decision."""
        return self.how == "proposed"

    @property
    def ordered(self) -> bool:
        """Backed by at least one structured order, not prose alone."""
        return any(basis in ("push", "war_aim") for _t, basis, _d in self.intents)

    @property
    def same_turn(self) -> bool:
        return any(turn == self.turn for turn, _b, _d in self.intents)


@dataclass(frozen=True, slots=True)
class ReversalReport:
    window: int
    reversals: tuple[Reversal, ...] = ()
    #: Peace or ceasefire events each nation was a party to, whether reversals or not --
    #: the denominator, so a nation that made eight peaces and reversed on one is not read
    #: like one that made one peace and reversed on it.
    peace_moves: dict[str, int] = field(default_factory=dict)

    def for_nations(self, nation_ids: tuple[str, ...]) -> tuple[Reversal, ...]:
        wanted = set(nation_ids)
        return tuple(r for r in self.reversals if r.nation_id in wanted)

    def summary(self, nation_ids: tuple[str, ...]) -> dict:
        mine = self.for_nations(nation_ids)
        return {
            "reversals": len(mine),
            "ordered": sum(1 for r in mine if r.ordered),
            "stated_only": sum(1 for r in mine if not r.ordered),
            "same_turn": sum(1 for r in mine if r.same_turn),
            "chosen": sum(1 for r in mine if r.chosen),
            "peace_moves": sum(self.peace_moves.get(n, 0) for n in nation_ids),
        }


def _names(conn: sqlite3.Connection, world_id: str) -> dict[str, str]:
    return {str(r["id"]): str(r["name"]) for r in conn.execute(
        "SELECT id, name FROM nation WHERE world_id = ?", (world_id,))}


def _held_region_names(conn: sqlite3.Connection, world_id: str) -> dict[str, set[str]]:
    """Region names each nation holds now, for recognising an enemy by its ground."""
    held: dict[str, set[str]] = {}
    for r in conn.execute(
        "SELECT name, COALESCE(controller_nation_id, owner_nation_id) AS holder"
        "  FROM region WHERE world_id = ? AND kind <> 'ocean'", (world_id,)):
        if r["holder"]:
            held.setdefault(str(r["holder"]), set()).add(str(r["name"]))
    return held


def _bot_turns(conn: sqlite3.Connection, world_id: str) -> set[tuple[str, int]]:
    """``(nation, turn)`` for every turn a bot, not a model, answered for that nation."""
    from .bots import BOT_MODEL

    try:
        return {(str(r[0]), int(r[1])) for r in conn.execute(
            "SELECT proposer_nation_id, turn FROM diff_batch WHERE world_id = ?"
            "   AND proposer_model = ? AND proposer_nation_id IS NOT NULL",
            (world_id, BOT_MODEL))}
    except sqlite3.OperationalError:
        return set()


def find_reversals(
    conn: sqlite3.Connection, world_id: str, *, window: int = DEFAULT_WINDOW,
    include_bots: bool = False, only_chosen: bool = False,
) -> ReversalReport:
    """Every peace a nation made within ``window`` turns of signalling it would keep fighting."""
    botted = set() if include_bots else _bot_turns(conn, world_id)
    try:
        names = _names(conn, world_id)
        peaces = list(conn.execute(
            "SELECT id, turn, kind, text FROM event WHERE world_id = ? AND turn > 0"
            "   AND kind IN ('peace_signed', 'ceasefire') ORDER BY turn, id", (world_id,)))
    except sqlite3.OperationalError:
        return ReversalReport(window=window)

    # Parties to each peace: actor and target only. A mediator brokered it and did not
    # make it, and must never be charged with reversing anything.
    parties: dict[int, list[str]] = {}
    roles: dict[tuple[int, str], str] = {}
    brokered: set[int] = set()
    for p in conn.execute(
        "SELECT ep.event_id, ep.nation_id, ep.role FROM event_participant ep"
        "  JOIN event e ON e.id = ep.event_id"
        " WHERE e.world_id = ? AND ep.role IN ('actor', 'target', 'mediator')"
        "   AND ep.nation_id IS NOT NULL", (world_id,)):
        if str(p["role"]) == "mediator":
            brokered.add(int(p["event_id"]))
            continue
        parties.setdefault(int(p["event_id"]), []).append(str(p["nation_id"]))
        roles[(int(p["event_id"]), str(p["nation_id"]))] = str(p["role"])

    def how(event_id: int, text: str, nation: str) -> str:
        if event_id in brokered:
            return "brokered"
        if any(phrase in text for phrase in _IMPOSED):
            return "imposed"
        return "proposed" if roles.get((event_id, nation)) == "actor" else "accepted"

    # --- intents, per (nation, enemy): (turn, basis, detail)
    intents: dict[tuple[str, str], list[tuple[int, str, str]]] = {}

    def note(nation: str, enemy: str, turn: int, basis: str, detail: str) -> None:
        if (nation, turn) in botted:
            return
        if nation and enemy and nation != enemy:
            intents.setdefault((nation, enemy), []).append((turn, basis, detail))

    for r in conn.execute(
        "SELECT nation_id, enemy_id, kind, declared_turn FROM war_aim WHERE world_id = ?",
        (world_id,)):
        if str(r["kind"]) in _OFFENSIVE_AIMS:
            note(str(r["nation_id"]), str(r["enemy_id"]), int(r["declared_turn"]),
                 "war_aim", f"declared war aim '{r['kind']}'")

    for r in conn.execute(
        "SELECT o.nation_id, o.declared_turn, g.name AS region,"
        "       COALESCE(g.controller_nation_id, g.owner_nation_id) AS holder"
        "  FROM military_objective o JOIN region g ON g.id = o.region_id"
        " WHERE o.world_id = ? AND o.kind = 'push'", (world_id,)):
        note(str(r["nation_id"]), str(r["holder"] or ""), int(r["declared_turn"]),
             "push", f"ordered a push on {r['region']}")

    held = _held_region_names(conn, world_id)
    for r in conn.execute(
        "SELECT e.turn, e.text, b.proposer_nation_id AS author FROM event e"
        "  JOIN diff_batch b ON b.id = e.created_by_batch"
        " WHERE e.world_id = ? AND e.turn > 0 AND b.proposer_role = 'tracked_agent'"
        "   AND b.proposer_nation_id IS NOT NULL", (world_id,)):
        text = str(r["text"] or "")
        if not _CONTINUE.search(text) or _STANDING_DOWN.search(text):
            continue
        author = str(r["author"])
        for enemy, enemy_name in names.items():
            if enemy == author:
                continue
            if enemy_name in text or any(g in text for g in held.get(enemy, ())):
                note(author, enemy, int(r["turn"]), "stated", text[:160])

    # --- match each peace against the intents that preceded it
    found: list[Reversal] = []
    moves: dict[str, int] = {}
    for peace in peaces:
        who = parties.get(int(peace["id"]), [])
        turn = int(peace["turn"])
        for nation in who:
            moves[nation] = moves.get(nation, 0) + 1
            for enemy in who:
                if enemy == nation:
                    continue
                before = tuple(sorted(
                    i for i in intents.get((nation, enemy), ())
                    if turn - window <= i[0] <= turn))
                if before:
                    found.append(Reversal(
                        nation_id=nation, enemy_id=enemy, turn=turn,
                        kind=str(peace["kind"]), intents=before,
                        how=how(int(peace["id"]), str(peace["text"] or ""), nation)))
    if only_chosen:
        found = [r for r in found if r.chosen]
    return ReversalReport(window=window, reversals=tuple(found), peace_moves=moves)
