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
    "TECHNOLOGIES",
    "Technology",
    "TechEffect",
    "available_technologies",
    "catalogue_for",
    "known_technologies",
    "technology_by_id",
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
