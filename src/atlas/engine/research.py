"""Programmes that end in a country knowing how to do something it could not before.

**Every effect here is something shipped code already reads.** That is the test a
technology has to pass to be in this catalogue at all: a tech that unlocks a capability
nothing consults is a line in a menu, and a game full of those is a game where research is
a tax you pay to make numbers go up. So a programme moves ``unit_template.build_days``
(which the yards read), ``unit_template.base_strength`` (which every unit delivered from
the yards is stamped with), ``nation.industry`` (which sets the size of the queue) or
``nation.economy``. Four levers, all of them load-bearing before this module existed.

**This is where a country outgrows its history.** Nothing in the engine references what
actually happened -- 1936 is a starting position, not a script -- so a power that spends
fifteen turns on shipbuilding research and industry launches better ships faster than it
historically could, and one that spends them on land reform does not. The divergence is
not a feature that had to be added; it is what happens once the numbers behind the map are
things a player can move.

Programmes sit in the same queue as a battleship and a housing estate, for the same
reason: what a country does with the next seven years is one decision, not three.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace

__all__ = [
    "MAX_MARK",
    "MAX_RESEARCH_SHARE",
    "TECHNOLOGIES",
    "UPGRADE_LINES",
    "Technology",
    "TechEffect",
    "UpgradeLine",
    "available_technologies",
    "catalogue_for",
    "known_technologies",
    "all_marks",
    "marks",
    "mark_name",
    "technology_by_id",
    "upgrade_programmes",
]


@dataclass(frozen=True, slots=True)
class TechEffect:
    """One thing knowing this lets a country do.

    ``scope`` is ``nation`` or ``unit_template``; ``subject`` names the unit kind when it
    is the latter.
    """

    scope: str
    field_name: str
    amount: float
    subject: str = ""


@dataclass(frozen=True, slots=True)
class Technology:
    id: str
    name: str
    blurb: str
    turns: int
    cost_treasury: float
    effects: tuple[TechEffect, ...] = ()
    requires: tuple[str, ...] = ()
    #: Pursued out of sight. What actually hides it is the visibility of the events the
    #: programme writes; this only records that it was meant to be hidden.
    secret: bool = False
    #: The upgrade line this programme belongs to ("armour"), and the mark it brings that
    #: line to. Empty and zero for a programme that stands on its own.
    line: str = ""
    mark: int = 0


#: The highest mark a line reaches. Every formation starts at Mk I; two programmes take it
#: to Mk III, and there is no Mk IV -- a country cannot research its way to an invincible
#: army, only to a better one.
MAX_MARK = 3

#: And however a country's programmes stack -- an upgrade line and a one-off programme can
#: both touch the same kind -- research never makes a formation more than this much
#: stronger than its era's standard one.
MAX_RESEARCH_SHARE = 0.5


@dataclass(frozen=True, slots=True)
class UpgradeLine:
    """A family of formations improved together, one mark at a time."""

    id: str
    name: str
    #: Every unit kind the line improves. An era passes the ones it fields.
    kinds: tuple[str, ...]
    #: Full strength added at each mark.
    step: float
    #: What each mark is, in a phrase: index 0 for Mk II, 1 for Mk III.
    blurbs: tuple[str, str]


UPGRADE_LINES: tuple[UpgradeLine, ...] = (
    UpgradeLine("infantry", "Infantry", ("infantry",), 0.5, (
        "Better rifles, radios and training for every infantry formation.",
        "Night fighting, body armour and a heavier support company.")),
    UpgradeLine("armour", "Armour", ("armour",), 1.0, (
        "A better gun and thicker plate on the next production run.",
        "A new generation of tank: faster, better protected, harder hitting.")),
    UpgradeLine("air", "Air wings", ("air_wing",), 1.0, (
        "Better engines and better weapons on the wing.",
        "A new generation of combat aircraft.")),
    UpgradeLine("navy", "Fleet", ("battleship", "cruiser", "destroyer", "submarine",
                                  "carrier"), 1.2, (
        "Better fire control and damage control across the fleet.",
        "A refit programme that brings every hull to the newest standard.")),
)

#: What each mark costs and how many turns it takes. Dearer and slower each time: the
#: second step is worth what the first was, and costs twice as much.
_MARK_COST: dict[int, tuple[float, int]] = {2: (30.0, 5), 3: (60.0, 8)}


def mark_name(mark: int) -> str:
    return "Mk " + {1: "I", 2: "II", 3: "III"}.get(mark, str(mark))


def upgrade_programmes(fielded: tuple[str, ...],
                       lines: tuple[UpgradeLine, ...] = UPGRADE_LINES
                       ) -> tuple[Technology, ...]:
    """The upgrade programmes for an era that fields ``fielded``: Mk II and Mk III of every
    line it has at least one kind for, the Mk III needing the Mk II."""
    out: list[Technology] = []
    for line in lines:
        kinds = tuple(kind for kind in line.kinds if kind in fielded)
        if not kinds:
            continue
        for mark in range(2, MAX_MARK + 1):
            cost, turns = _MARK_COST[mark]
            out.append(Technology(
                id=f"{line.id}_mk{mark}",
                name=f"{line.name} {mark_name(mark)}",
                blurb=line.blurbs[mark - 2],
                turns=turns,
                cost_treasury=cost,
                effects=tuple(TechEffect("unit_template", "base_strength", line.step, kind)
                              for kind in kinds),
                requires=(f"{line.id}_mk{mark - 1}",) if mark > 2 else (),
                line=line.id,
                mark=mark,
            ))
    return tuple(out)


#: The default catalogue: what a nation can work out how to do when its world has no
#: designed scenario behind it.
#:
#: **These are interwar programmes**, because 1936 was the only era there was when
#: research was built. An era supplies its own through ``Scenario.technologies``, and
#: Modern Day does -- a 2026 player researching radio direction finding is a menu that has
#: stopped meaning anything. The catalogue stays here rather than moving into the 1936
#: scenario module only because the engine needs *a* default and must not import a
#: scenario to get one.
TECHNOLOGIES: tuple[Technology, ...] = (
    Technology(
        id="welding",
        name="All-welded construction",
        blurb="Welded hulls instead of riveted. Faster to build and lighter for it.",
        turns=5,
        cost_treasury=30.0,
        effects=(
            TechEffect("unit_template", "build_days", -60.0, "battleship"),
            TechEffect("unit_template", "build_days", -60.0, "carrier"),
            TechEffect("unit_template", "build_days", -30.0, "transport"),
        ),
    ),
    Technology(
        id="mass_production",
        name="Mass production",
        blurb="Jigs, tooling and interchangeable parts. Everything after this is quicker.",
        turns=6,
        cost_treasury=40.0,
        effects=(
            TechEffect("nation", "industry", 8.0),
            TechEffect("unit_template", "build_days", -30.0, "infantry"),
            TechEffect("unit_template", "build_days", -30.0, "armour"),
        ),
    ),
    Technology(
        id="radar",
        name="Radio direction finding",
        blurb=(
            "You see them before they see you, at night and in weather. Worth more than "
            "any gun it is fitted alongside."
        ),
        turns=8,
        cost_treasury=45.0,
        # The battleship term stays: radar-directed gunnery is a real improvement to a
        # real gun, and it is what the set actually did for surface fleets. The air_wing
        # term is gone, and its absence is the point -- as a `base_strength` bump it made
        # radar help you *attack*, which is the one thing radar does not do. It is an
        # interception term now (`air.INTERCEPT_TECHS`), read when a raid arrives.
        effects=(TechEffect("unit_template", "base_strength", 2.5, "battleship"),),
    ),
    Technology(
        id="carrier_doctrine",
        name="Carrier doctrine",
        blurb=(
            "Massed decks and a strike wing that trains together. The argument the naval "
            "staffs were having, settled by whoever gets here first."
        ),
        turns=7,
        cost_treasury=42.0,
        requires=("welding",),
        effects=(TechEffect("unit_template", "base_strength", 4.0, "carrier"),),
    ),
    Technology(
        id="synthetic_fuel",
        name="Synthetic fuel",
        blurb=(
            "Oil out of coal. Ruinously expensive and it is the difference between having "
            "a war economy and having somebody else's permission to run one."
        ),
        turns=7,
        cost_treasury=55.0,
        requires=("mass_production",),
        effects=(
            TechEffect("nation", "economy", 3.0),
            TechEffect("nation", "industry", 6.0),
        ),
    ),
    Technology(
        id="atomic_programme",
        name="Atomic programme",
        blurb=(
            "A laboratory nobody visits and a budget line nobody can read. It is the "
            "longest thing a country can undertake and the only one worth hiding."
        ),
        turns=16,
        cost_treasury=120.0,
        requires=("mass_production", "synthetic_fuel"),
        secret=True,
        effects=(
            # **The one that makes it the atomic programme.** For as long as this catalogue
            # has existed, finishing the longest and most secret undertaking in the game
            # bought a stat bump and nothing else: `military_strength` and `legitimacy`, and
            # no warheads. A country could complete the Manhattan Project and remain a
            # non-nuclear power, because nothing in the engine could add a warhead to
            # anybody -- `nuclear._spend` only ever subtracted.
            #
            # One warhead, delivered by aircraft. The threshold is what matters here; the
            # pile is production, and `warheads.WARHEAD_PROJECT` is where that happens.
            TechEffect("arsenal", "warheads", 1.0),
            TechEffect("nation", "military_strength", 12.0),
            TechEffect("nation", "legitimacy", 4.0),
        ),
    ),
)


#: The default world's formations: the engine's land and air kinds and its 1936 fleet.
TECHNOLOGIES = TECHNOLOGIES + upgrade_programmes(
    ("infantry", "armour", "air_wing", "battleship", "carrier"))


def catalogue_for(
    conn: sqlite3.Connection, world_id: str
) -> tuple[Technology, ...]:
    """What this world's era can work out how to do.

    Read from ``world.preset_id`` through the scenario registry, the same join the
    referee's briefing uses to find a world's lore and hard constraints. A world with no
    designed scenario -- the sandbox, a bare pack -- gets :data:`TECHNOLOGIES`.

    Imported inside the function on purpose: the registry reaches the scenario modules,
    which reach the seeder, which reaches this module for its build times. A module-level
    import here would close that loop.
    """
    row = conn.execute(
        "SELECT preset_id FROM world WHERE id = ?", (world_id,)
    ).fetchone()
    if not row or not row["preset_id"]:
        return _renamed(TECHNOLOGIES)

    from ..scenarios.registry import scenario_for

    scenario = scenario_for(row["preset_id"])
    return _renamed(
        scenario.technologies if scenario is not None else TECHNOLOGIES)


def strength_bonus(conn: sqlite3.Connection, world_id: str, nation_id: str
                   ) -> dict[str, float]:
    """Per unit kind, what ``nation_id``'s own research adds to a formation's full strength.

    **Research is a country's, the unit template is the world's.** A programme that made
    infantry stronger used to add to the world's one infantry template, so every country's
    infantry grew whenever *anybody* finished it -- and grew again for each country that
    did. A Modern Day play-test watched infantry go from 4.5 to 10.5 in five months as four
    powers completed the same programme, and an AI ordering replacements every month
    swelled its army past twice its size while a player who did not fell hopelessly
    behind. The template now keeps the scenario's figure, and this is what a country adds
    to it for what it has itself worked out.
    """
    done = {str(row[0]) for row in conn.execute(
        "SELECT tech_id FROM technology WHERE world_id = ? AND nation_id = ?",
        (world_id, nation_id))}
    if not done:
        return {}
    bonus: dict[str, float] = {}
    for tech in catalogue_for(conn, world_id):
        if tech.id not in done:
            continue
        for effect in tech.effects:
            if (effect.scope == "unit_template" and effect.field_name == "base_strength"
                    and effect.subject):
                bonus[effect.subject] = bonus.get(effect.subject, 0.0) + effect.amount
    # The ceiling, against the era's own standard formation.
    for kind, base in conn.execute(
            "SELECT kind, base_strength FROM unit_template WHERE world_id = ?", (world_id,)):
        if str(kind) in bonus:
            bonus[str(kind)] = min(bonus[str(kind)],
                                   round(float(base or 0.0) * MAX_RESEARCH_SHARE, 4))
    return bonus


def all_marks(conn: sqlite3.Connection, world_id: str) -> dict[str, dict[str, int]]:
    """:func:`marks` for every country that has improved anything, in one read -- for the
    map, which draws every country's formations at once."""
    done: dict[str, set[str]] = {}
    for nation_id, tech_id in conn.execute(
            "SELECT nation_id, tech_id FROM technology WHERE world_id = ?", (world_id,)):
        done.setdefault(str(nation_id), set()).add(str(tech_id))
    if not done:
        return {}
    upgrades = [tech for tech in catalogue_for(conn, world_id) if tech.mark]
    out: dict[str, dict[str, int]] = {}
    for nation_id, known in done.items():
        for tech in upgrades:
            if tech.id in known:
                reached = out.setdefault(nation_id, {})
                for effect in tech.effects:
                    if effect.subject:
                        reached[effect.subject] = max(reached.get(effect.subject, 1),
                                                      tech.mark)
    return out


def marks(conn: sqlite3.Connection, world_id: str, nation_id: str) -> dict[str, int]:
    """Per unit kind, the mark ``nation_id``'s formations of that kind have reached. A kind
    no completed upgrade touches is not listed: it is at Mk I."""
    done = known_technologies(conn, world_id, nation_id)
    out: dict[str, int] = {}
    if not done:
        return out
    for tech in catalogue_for(conn, world_id):
        if tech.mark and tech.id in done:
            for effect in tech.effects:
                if effect.subject:
                    out[effect.subject] = max(out.get(effect.subject, 1), tech.mark)
    return out


def establishment(conn: sqlite3.Connection, world_id: str, nation_id: str, kind: str,
                  base: float) -> float:
    """A formation's full strength for this country: the template plus its own research."""
    return base + strength_bonus(conn, world_id, nation_id).get(kind, 0.0)


def _renamed(catalogue: tuple[Technology, ...]) -> tuple[Technology, ...]:
    """Apply any programme rename the live re-record paid for.

    **The one place a catalogue is read from, so the one place a rename belongs.** A
    technology's ``name`` reaches the tracked-agent briefing with its effects and is
    therefore on the tape; its ``blurb`` is the player's alone and is not, which is why the
    blurb was corrected outright and the name is behind a switch. The ``technology`` table
    stores ``tech_id`` and never a name, so this is render-time text and cannot reach a
    saved world or move a fingerprint.

    **Identity whenever there is nothing to do**, which is most reads: the switch off, and
    -- now that it defaults on -- every catalogue holding none of the renamed programmes.
    ``dataclasses.replace`` is not called at all in either case and the tuple a caller gets
    is the module constant it has always been, so a catalogue an era never renames is not
    quietly rebuilt on each read, and ``catalogue_for(...) is TECHNOLOGIES`` still holds.
    """
    from .rerecord import TECH_RENAMES, enabled

    if not enabled() or not any(tech.id in TECH_RENAMES for tech in catalogue):
        return catalogue
    return tuple(
        replace(tech, name=TECH_RENAMES[tech.id]) if tech.id in TECH_RENAMES else tech
        for tech in catalogue
    )


def technology_by_id(
    tech_id: str, catalogue: tuple[Technology, ...] = TECHNOLOGIES
) -> Technology | None:
    return next((tech for tech in catalogue if tech.id == tech_id), None)


def known_technologies(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> set[str]:
    return {
        row["tech_id"]
        for row in conn.execute(
            "SELECT tech_id FROM technology WHERE world_id = ? AND nation_id = ?",
            (world_id, nation_id),
        )
    }


def available_technologies(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> list[Technology]:
    """What this nation could start today: not already known, prerequisites met."""
    known = known_technologies(conn, world_id, nation_id)
    return [
        tech
        for tech in catalogue_for(conn, world_id)
        if tech.id not in known and all(need in known for need in tech.requires)
    ]
