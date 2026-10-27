"""Every mechanic a player can reach, declared once.

An action type used to be declared in **three** places that had to agree and nothing made
them: the parser's schema enum, the council's advisable list, and the orders dispatch
table. A parity test held the first two together -- it is what caught `gambit` -- and
nothing tied either to a handler, to an availability rule, or to a sentence explaining what
the thing is. That is exactly the shape of the seam bugs: *research exists in the engine and
the parser has never heard of it*.

So this is the catalogue, and everything else is generated from it: the parser enum
(:func:`action_types`), the prompt menu (:func:`prompt_menu`), the council's list
(:func:`advisable`), and the model's examples (:func:`examples`).

**The exhaustiveness runs both ways, and that is what makes it structural.** A capability
here that never reaches a prompt fails a test; a value in the parser enum with no capability
fails the same test. There is no second file to remember to update, because the second file
is generated.

**It is a catalogue, not a dispatch framework.** Handlers stay where they are and keep their
signatures; a capability records *where* execution lives rather than owning it. A catalogue
that also runs things is a catalogue people are afraid to edit -- and the point of this one
is that adding a mechanic to it should be the cheapest way to add a mechanic at all.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field

__all__ = [
    "CAPABILITIES",
    "Capability",
    "action_types",
    "advisable",
    "available_to",
    "by_type",
    "examples",
    "prompt_menu",
]


#: What a capability may be aimed at. Names a vocabulary in
#: :mod:`atlas.engine.reference`, so the manifest and the resolver cannot disagree about
#: what a target *is*.
TargetKind = str


@dataclass(frozen=True, slots=True)
class Capability:
    """One thing a player can order, and everything anybody needs to know about it."""

    #: The parser's action type. The identity of the capability everywhere.
    action_type: str
    #: One clause the player and the council read. Not a description of the field --
    #: a description of the *act*.
    explain: str
    #: Words a player might reach for. Fed to the parser so it can recognise the order,
    #: and to the reference boundary so a near-miss resolves rather than being refused.
    aliases: tuple[str, ...] = ()
    #: Action fields this capability needs to be executable at all.
    required: tuple[str, ...] = ()
    #: Which vocabularies its targets are drawn from. Empty means it names nothing.
    targets: tuple[TargetKind, ...] = ()
    #: What it costs and how long it takes, in words. ``""`` when the answer is "nothing
    #: directly" -- which is itself worth saying, because a player asked to choose between
    #: acts needs to know which ones are free.
    cost: str = ""
    #: Where execution lives. Either a key in `orders._HANDLERS` or the module that owns
    #: it; the test asserts one of the two is true, so a capability cannot claim a handler
    #: that is not there.
    handled_by: str = "orders"
    #: Whether this nation can order it *now*, **optionally against a named target**.
    #:
    #: Called as ``available(conn, world_id, nation_id, target)``, where ``target`` is a
    #: nation or region id, or ``""`` when the question is only "can this power do this at
    #: all". Absent means always.
    #:
    #: **The target came late, and it came because two special cases had already leaked.**
    #: The predicate used to take a nation and nothing else, which let it answer "does this
    #: power have neighbours" and never "is it already at war with *this* one" -- so
    #: `engine.against` grew a private table of two exceptions (a declaration against a
    #: standing enemy, an offer of peace to somebody you are not fighting), each naming the
    #: engine rule it stood in for. Two is a coincidence; a third would have been a second
    #: catalogue, which is the thing this module exists to prevent. The exceptions are gone
    #: and live here as ordinary predicates.
    #:
    #: A predicate that does not care about the target simply ignores it, which is most of
    #: them: what a target is *for* is the handful of acts whose legality is about a pair.
    available: Callable[..., bool] | None = None
    #: A concrete order and the action it should become. Shown to the parser, and asserted
    #: to be well-formed, so an example can never drift from the schema it illustrates.
    example: tuple[str, str] | None = None
    #: Free-form notes for the prompt menu, when the act needs a caveat.
    notes: tuple[str, ...] = field(default_factory=tuple)
    #: **What cannot be taken back**, in one sentence, or empty for an ordinary act.
    #:
    #: The declaration and the reason are the same field on purpose: a boolean would let
    #: somebody mark an act grave without saying why, and *why* is the whole content of a
    #: confirmation. A dialog that says "are you sure?" asks nothing; one that says what
    #: the click cannot undo is the only kind worth interrupting somebody for.
    #:
    #: Declared here rather than in a list beside the confirmation code, for the reason
    #: everything else about an act is declared here: a second file would be a second thing
    #: to remember, and the whole argument for this catalogue is that there is not one.
    grave: str = ""


def _may_sign(conn, world_id: str, nation_id: str, target: str = "") -> bool:
    """Whether this country signs agreements at all.

    **Armed neutrality is the one doctrine that takes an option away**, and it is a refusal
    rather than a multiplier because that is what the stance *is*: a country whose whole
    policy is staying out of other people's wars does not sign up to fight them. Every
    other doctrine changes what an act is worth; this one is the exception, and it is the
    exception on purpose.

    A neutral may still trade, still talk, and still declare war. What it may not do is
    promise to fight for somebody else.
    """
    from .doctrines import stance_of

    if not _has_neighbours(conn, world_id, nation_id, target):
        return False
    return stance_of(conn, world_id, nation_id, "alignment") != "neutrality"


def _doctrine_to_adopt(conn, world_id: str, nation_id: str, target: str = "") -> bool:
    """Whether there is any doctrine this country could move to right now.

    False while a shift is in flight, which is the whole reason this predicate takes a
    connection rather than being always-available: an order that would certainly be refused
    should not be in the list the player is shown.
    """
    from .doctrines import axis_of_stance, offered_to

    offered = offered_to(conn, world_id, nation_id)
    if target:
        # Named a stance: the question is whether *that* one is on the table.
        axis = axis_of_stance(target)
        return axis is not None and any(stance.id == target for stance in offered)
    return bool(offered)


def _has_units(
    conn: sqlite3.Connection, world_id: str, nation_id: str, target: str = ""
) -> bool:
    row = conn.execute(
        "SELECT 1 FROM unit WHERE world_id = ? AND nation_id = ? LIMIT 1",
        (world_id, nation_id),
    ).fetchone()
    return row is not None


def _has_neighbours(
    conn: sqlite3.Connection, world_id: str, nation_id: str, target: str = ""
) -> bool:
    """Whether there is anybody to talk to. False only in a one-nation world."""
    row = conn.execute(
        "SELECT 1 FROM nation WHERE world_id = ? AND id <> ? AND is_alive = 1 LIMIT 1",
        (world_id, nation_id),
    ).fetchone()
    return row is not None


def _at_war_with(
    conn: sqlite3.Connection, world_id: str, nation_id: str, target: str
) -> bool:
    """Whether these two are fighting. ``target`` empty means "anybody", for a menu."""
    if not target:
        return conn.execute(
            "SELECT 1 FROM relation WHERE world_id = ? AND at_war = 1"
            "   AND (nation_a = ? OR nation_b = ?) LIMIT 1",
            (world_id, nation_id, nation_id)).fetchone() is not None
    row = conn.execute(
        "SELECT at_war FROM relation WHERE world_id = ? AND nation_a = ? AND nation_b = ?",
        (world_id, nation_id, target)).fetchone()
    return bool(row["at_war"]) if row is not None else False


def _somebody_to_declare_on(
    conn: sqlite3.Connection, world_id: str, nation_id: str, target: str = ""
) -> bool:
    """Somebody to fight who is not already being fought.

    **The rule `resolution._declare_war` enforces, said before the order is written.** A
    declaration against a power you are already at war with returns from that function
    having done nothing at all, so offering it is offering a no-op -- and an interface that
    offers no-ops teaches a player that its list is decoration.
    """
    return _has_neighbours(conn, world_id, nation_id) and not _at_war_with(
        conn, world_id, nation_id, target)


def _somebody_to_make_peace_with(
    conn: sqlite3.Connection, world_id: str, nation_id: str, target: str = ""
) -> bool:
    """Somebody you are actually fighting.

    `peace.settle` refuses an offer to a power you are not at war with, in as many words.
    Without a target this is "are you at war at all", which is the right answer for a menu.
    """
    return _at_war_with(conn, world_id, nation_id, target)


#: What the menu says for a capability whose entry records no cost.
#:
#: Not "free", which would be a claim about consequences: declaring war costs nothing out
#: of the treasury and costs a great deal of everything else, and the caveats beneath the
#: line are where that lives. This says only what the catalogue means -- nothing is charged
#: when you order it.
_NO_DIRECT_COST = "no direct cost"


#: The catalogue. Order is the order a player is shown them: what an army does, then what
#: a state does, then what a treasury does, then the escape hatches.
CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        action_type="move_units",
        explain="move forces between regions",
        aliases=("move", "march", "advance", "redeploy", "invade", "attack", "land"),
        required=("from_region", "to_region"),
        targets=("region",),
        cost="no direct cost; the ground decides the rest",
        handled_by="atlas.engine.resolution",
        available=_has_units,
        example=("march the western army into Karthos", "move_units"),
        notes=("A destination reachable only by sea is still a destination -- the "
               "crossing is accounted for.",),
    ),
    Capability(
        action_type="raise_units",
        explain="raise new formations",
        aliases=("raise", "recruit", "levy", "conscript", "build", "lay down"),
        required=("unit_kind",),
        targets=("unit_kind",),
        cost="treasury and manpower per formation; anything with a build time enters the yards",
        example=("raise two divisions", "raise_units"),
    ),
    Capability(
        action_type="reinforce",
        explain="send replacements to the formations you already have",
        aliases=("reinforce", "replacements", "bring up to strength", "make good losses",
                 "refit", "top up"),
        targets=("region", "unit_kind"),
        cost=("a quarter of a new formation's treasury per point of strength and all of "
              "its manpower; never more than half the treasury in one turn"),
        handled_by="atlas.engine.orders",
        example=("bring the eastern divisions back up to strength", "reinforce"),
        notes=("A formation out of supply is not reinforced at any price.",
               "Cheaper than raising: the guns and the officers are still there, the men "
               "are not."),
    ),
    Capability(
        action_type="disband_units",
        explain="stand formations down",
        aliases=("disband", "stand down", "demobilise", "demobilize"),
        targets=("region", "unit_kind"),
        # **"Refunds nothing" was not true**: standing a formation down returns half its
        # manpower. The Codex review of phase 2 caught it, along with the two advertised
        # targets the handler ignored. A manifest that misdescribes what a mechanic does is
        # the same seam it exists to close, pointing the other way.
        cost="returns half the manpower; refunds no treasury; saves the upkeep",
        available=_has_units,
        example=("disband the garrison in the north", "disband_units"),
        grave="A formation stood down is gone. What comes back is a new one, paid for "
              "again and raised from nothing.",
    ),
    Capability(
        action_type="adopt_doctrine",
        explain="change what this country is built to do",
        aliases=("doctrine", "adopt", "reorganise", "reorganize", "restructure",
                 "go professional", "mobilise", "mobilize", "posture", "realign"),
        required=("doctrine",),
        cost="a share of the treasury now, and half a year during which you have the "
             "advantages of neither the old doctrine nor the new one",
        available=_doctrine_to_adopt,
        example=("put the army on a professional footing", "adopt_doctrine"),
        grave="Until the reorganisation is finished this country has the advantages of "
              "neither doctrine, and at war that takes twice as long.",
        notes=("A doctrine is what the country is built to do, and it is not the same as "
               "your own standing policy: policy reorders advice, a doctrine changes the "
               "arithmetic.",),
    ),
    Capability(
        action_type="fortify",
        explain="dig in where forces already stand",
        aliases=("fortify", "entrench", "dig in", "fortifications"),
        targets=("region",),
        # It claimed treasury and spends none: digging in moves morale and nothing else.
        # `""` is the manifest's way of saying "nothing directly", which is worth saying to
        # a player choosing between acts.
        cost="",
        available=_has_units,
        example=("fortify the eastern frontier", "fortify"),
    ),
    Capability(
        action_type="diplomatic_message",
        explain="send a message to another power",
        aliases=("message", "write to", "tell", "warn", "demand", "protest"),
        required=("target_nation",),
        targets=("nation",),
        available=_has_neighbours,
        handled_by="atlas.engine.diplomacy",
        example=("warn Aurelia to stay out of the straits", "diplomatic_message"),
    ),
    Capability(
        action_type="propose_treaty",
        explain="propose a treaty",
        aliases=("treaty", "pact", "alliance", "non-aggression", "propose"),
        required=("target_nation",),
        targets=("nation",),
        available=_may_sign,
        handled_by="atlas.engine.diplomacy",
        example=("offer Karthos a non-aggression pact", "propose_treaty"),
        notes=("A country on armed neutrality does not sign pacts. That is what the "
               "doctrine is; change it first if you want allies.",),
    ),
    Capability(
        action_type="declare_war",
        explain="declare war",
        aliases=("declare war", "war on", "attack openly"),
        required=("target_nation",),
        targets=("nation",),
        available=_somebody_to_declare_on,
        handled_by="atlas.engine.resolution",
        example=("declare war on Veyra", "declare_war"),
        grave="A war cannot be called off; it has to be ended, and the other side has to "
              "agree to that.",
    ),
    Capability(
        action_type="sue_for_peace",
        explain="offer to end a war, on terms",
        aliases=("peace", "armistice", "ceasefire", "truce", "surrender",
                 "sue for peace", "make peace", "offer terms", "settle"),
        required=("target_nation",),
        targets=("nation",),
        cost="nothing to ask; what you offer is what it costs",
        handled_by="orders",
        available=_somebody_to_make_peace_with,
        example=("seek terms with Aurelia", "sue_for_peace"),
        notes=(
            "Name what the settlement says in `terms`: who cedes or restores which "
            "province, who withdraws, reparations, a demilitarised zone, a guarantee, "
            "recognition. Leave it empty to offer an unconditional end to the fighting, "
            "which a power that is winning will refuse.",
            "The other side accepts or refuses according to how the war has actually "
            "gone, not according to how the offer is worded.",
            "Ask for a `ceasefire` instead to stop the fighting for a few months without "
            "settling anything: no terms, the war continues, and either side can break it.",
        ),
        grave="Ground signed away at a table does not come back with the next turn. A "
              "settlement is the one place a player can lose a province without a battle.",
    ),
    Capability(
        action_type="economic_investment",
        explain="put money into the economy",
        aliases=("invest", "spend on", "develop the economy", "stimulus"),
        cost="treasury, and the economy moves within its per-turn cap",
        example=("invest in the economy", "economic_investment"),
    ),
    Capability(
        action_type="build_project",
        explain="begin a public work or research programme (see the menu above)",
        aliases=("build", "begin", "research", "develop", "adopt", "programme", "program"),
        required=("project",),
        targets=("project", "technology"),
        cost="treasury and a build slot; the menu quotes the real figure",
        example=("begin research into welding", "build_project"),
        notes=("Public works and research are the same order -- name the programme's id.",),
    ),
    Capability(
        action_type="espionage",
        explain="act covertly",
        aliases=("spy", "espionage", "intelligence", "agents", "find out"),
        required=("target_nation",),
        targets=("nation",),
        available=_has_neighbours,
        example=("find out what Karthos is building", "espionage"),
        notes=("This *learns* things. To break something, order an undertaking.",),
    ),
    Capability(
        action_type="counterintelligence",
        explain="hunt somebody else's agents at home",
        aliases=("counterintelligence", "counter-intelligence", "counterintel",
                 "security sweep", "sweep", "round up spies", "internal security"),
        required=("target_nation",),
        targets=("nation",),
        available=_has_neighbours,
        example=("round up Karthos's agents", "counterintelligence"),
        notes=("The other half of espionage. It blinds their service in your country, "
               "makes what they think they know about you worse, and helps your police "
               "work out who has been operating. A standing effort: it lapses if you "
               "stop.",),
    ),
    Capability(
        action_type="gambit",
        explain=("attempt something with an uncertain outcome by name -- a summit, an "
                 "envoy, a bribe, a sting, sabotage, incitement, an expose, an air strike"),
        aliases=("summit", "envoy", "bribe", "sting", "sabotage", "incite", "expose",
                 "air strike", "bomb"),
        required=("gambit_kind",),
        targets=("nation", "region"),
        cost="a stake from the treasury, charged win or lose",
        example=("sabotage the Karthian railways", "gambit"),
        notes=("Nuclear release is **not** available here and no wording reaches it.",),
    ),
    Capability(
        action_type="freeform",
        explain="anything else at all -- the world will tell you what it made of it",
        handled_by="atlas.engine.freeform",
        example=("hold a national day of mourning", "freeform"),
    ),
    Capability(
        action_type="other",
        explain="anything else, described plainly",
        handled_by="nothing",
        notes=("Deliberately unhandled: it is the parser's way of saying it understood "
               "the words and found no mechanic. Prefer `freeform`, which does something.",),
    ),
)

_BY_TYPE = {capability.action_type: capability for capability in CAPABILITIES}


def by_type(action_type: str) -> Capability | None:
    return _BY_TYPE.get(action_type)


def action_types() -> tuple[str, ...]:
    """The parser's enum. Generated, so it cannot omit a mechanic that exists."""
    return tuple(capability.action_type for capability in CAPABILITIES)


def advisable() -> tuple[tuple[str, str], ...]:
    """What the council may recommend. Generated from the same catalogue the parser is,
    so a council can never suggest something unorderable."""
    return tuple((c.action_type, c.explain) for c in CAPABILITIES)


def available_to(
    conn: sqlite3.Connection, world_id: str, nation_id: str, target: str = ""
) -> tuple[Capability, ...]:
    """The capabilities this nation could actually use right now.

    **A database error is treated as *available*; a programming error is not caught at
    all.** The split is deliberate. An availability rule is a convenience for the prompt,
    so a world that cannot answer the question must not silently lose a mechanic the engine
    would still have executed -- failing open is the safe direction. A `TypeError` in the
    predicate itself is a bug, and swallowing it would hide the bug behind a menu that is
    quietly one line short, which is the failure this whole module exists to make
    impossible.
    """
    out = []
    for capability in CAPABILITIES:
        if capability.available is None:
            out.append(capability)
            continue
        try:
            if capability.available(conn, world_id, nation_id, target):
                out.append(capability)
        except sqlite3.Error:
            out.append(capability)
    return tuple(out)


def prompt_menu(
    conn: sqlite3.Connection | None = None,
    world_id: str = "",
    nation_id: str = "",
) -> list[str]:
    """The menu a model-facing prompt shows. One line per capability, plus its caveats.

    Without a connection this is the whole catalogue, which is what a schema description
    wants; with one it is narrowed to what this nation can actually do.
    """
    shown = (
        available_to(conn, world_id, nation_id)
        if conn is not None and nation_id
        else CAPABILITIES
    )
    lines: list[str] = []
    for capability in shown:
        parts = [f"`{capability.action_type}` -- {capability.explain}"]
        if capability.required:
            parts.append("needs " + ", ".join(f"`{f}`" for f in capability.required))
        # **"Nothing directly" is an answer, and it used to be rendered as silence.**
        # `Capability.cost` documents `""` as the manifest's way of saying a thing costs
        # nothing directly -- "which is itself worth saying, because a player asked to
        # choose between acts needs to know which ones are free" -- and this dropped the
        # clause entirely, so six acts showed a bare verb. One of them was `declare_war`,
        # which the tracked agent's own briefing calls the heaviest thing on its form and
        # prices in standing, in concurrent wars and in years of cooldown. The catalogue
        # said what was worth saying; the menu declined to say it.
        parts.append(capability.cost or _NO_DIRECT_COST)
        lines.append("; ".join(parts))
        lines.extend(f"    {note}" for note in capability.notes)
    return lines


def examples() -> list[str]:
    """Worked examples for a prompt: an order, and what it should become."""
    return [
        f'"{order}" -> `{action_type}`'
        for capability in CAPABILITIES
        if capability.example
        for order, action_type in (capability.example,)
    ]
