"""Public works: what a country builds when it is not building a war.

ATLAS skews toward war, and the cause is not that war is over-built. It is that **war was
the only system with a middle.** A contest accumulates pressure over turns and a player
can watch it move; everything else resolved the instant it was ordered, so a player who
wanted to build had nothing to watch. The queue is the feature here, and the catalogue is
just what goes in it.

Each project is paid for when it is ordered, occupies a slot in the same finite queue as a
battleship -- a country choosing between housing and a capital ship is the decision this
system exists to pose -- and delivers its effect when it finishes. Effects land on
*regions* as often as on nations, because development is a column the map already draws:
a player who builds nothing military still watches their country change.

**Some of them can go wrong.** Land reform in a state with no legitimacy behind it is a
gamble, resolved through the same seeded stream as a battle, and that is deliberate. A
menu where every item is a small guaranteed gain is a shopping list; a queue where one
choice is a risk you took is a game. It also means the peaceful side has the property that
made the war side work -- an outcome the player did not choose.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["CIVIL_PROJECTS", "CivilProject", "Effect", "project_by_id"]


@dataclass(frozen=True, slots=True)
class Effect:
    """One thing a finished project does. ``scope`` is ``nation`` or ``region``."""

    scope: str
    field_name: str
    amount: float


@dataclass(frozen=True, slots=True)
class CivilProject:
    id: str
    name: str
    #: One line the player reads before committing years to it.
    blurb: str
    turns: int
    cost_treasury: float
    cost_manpower: float = 0.0
    effects: tuple[Effect, ...] = ()
    #: When set, the project is a gamble: the chance it goes well is this, adjusted by the
    #: nation's legitimacy, and the effects below are applied instead when it does not.
    risk_from_legitimacy: bool = False
    on_failure: tuple[Effect, ...] = field(default_factory=tuple)
    #: When set, the cost rises with the industry the nation already has. Without it a
    #: country builds factories which build factories and the curve is exponential; with
    #: it, each additional works costs more than the last and the curve is a curve.
    cost_scales_with_industry: bool = False

    @property
    def risky(self) -> bool:
        return self.risk_from_legitimacy


#: The catalogue. Deliberately short: every entry has to be a decision somebody would
#: genuinely agonise over, and a list of twenty small bonuses is a spreadsheet.
CIVIL_PROJECTS: tuple[CivilProject, ...] = (
    CivilProject(
        id="housing",
        name="Housing programme",
        blurb="Slum clearance and new estates. Cheap, quick, and everybody can see it.",
        turns=4,
        cost_treasury=25.0,
        effects=(
            Effect("region", "development", 1.5),
            Effect("nation", "stability", 4.0),
        ),
    ),
    CivilProject(
        id="rail",
        name="Rail and roads",
        blurb=(
            "Trunk lines and metalled roads. Expensive and slow, and it is what everything "
            "else is carried on."
        ),
        turns=6,
        cost_treasury=45.0,
        cost_manpower=8.0,
        effects=(
            Effect("region", "development", 3.0),
            Effect("nation", "economy", 3.0),
        ),
    ),
    CivilProject(
        id="health",
        name="Public health",
        blurb="Clinics, clean water and inspectors. The returns arrive slowly and stay.",
        turns=5,
        cost_treasury=30.0,
        effects=(
            Effect("region", "population", 2.0),
            Effect("nation", "stability", 3.0),
            Effect("nation", "legitimacy", 2.0),
        ),
    ),
    CivilProject(
        id="schools",
        name="Schools and universities",
        blurb=(
            "A literate country is a richer one in twenty years and an argumentative one "
            "in five."
        ),
        turns=6,
        cost_treasury=28.0,
        effects=(
            Effect("nation", "economy", 2.5),
            Effect("nation", "legitimacy", 3.0),
            Effect("nation", "stability", -1.0),
        ),
    ),
    CivilProject(
        id="workshops",
        name="Workshops and light industry",
        blurb=(
            "Machine shops, foundries and the first assembly lines. Small, cheap and "
            "quick -- the rung a poor country climbs onto before it can afford a steelworks."
        ),
        turns=4,
        cost_treasury=20.0,
        cost_scales_with_industry=True,
        effects=(
            Effect("nation", "industry", 5.0),
            Effect("region", "development", 1.0),
            Effect("nation", "stability", -1.0),
        ),
    ),
    CivilProject(
        id="steelworks",
        name="Steelworks",
        blurb=(
            "Blast furnaces and rolling mills. It is what everything else is made of, and "
            "it is filthy, and the district knows it."
        ),
        turns=7,
        cost_treasury=50.0,
        cost_manpower=10.0,
        cost_scales_with_industry=True,
        effects=(
            Effect("nation", "industry", 12.0),
            Effect("region", "development", 2.0),
            Effect("nation", "stability", -2.0),
        ),
    ),
    CivilProject(
        id="shipyards",
        name="Shipyards and machine tools",
        blurb=(
            "Slipways, cranes and the men who know how to use them. Slower to build than "
            "anything it will build."
        ),
        turns=9,
        cost_treasury=65.0,
        cost_manpower=12.0,
        cost_scales_with_industry=True,
        effects=(
            Effect("nation", "industry", 18.0),
            Effect("nation", "economy", 2.0),
        ),
    ),
    CivilProject(
        id="land_reform",
        name="Land reform",
        blurb=(
            "Break up the great estates. It settles the countryside or it sets it alight, "
            "and which one depends on whether anybody believes your government."
        ),
        turns=8,
        cost_treasury=20.0,
        risk_from_legitimacy=True,
        effects=(
            Effect("nation", "stability", 9.0),
            Effect("nation", "legitimacy", 4.0),
            Effect("region", "development", 1.0),
        ),
        on_failure=(
            Effect("nation", "stability", -8.0),
            Effect("nation", "legitimacy", -3.0),
        ),
    ),
)


def project_by_id(project_id: str) -> CivilProject | None:
    return next((p for p in CIVIL_PROJECTS if p.id == project_id), None)
