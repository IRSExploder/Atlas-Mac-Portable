"""Things that take time to build.

Until now everything a nation could raise appeared the turn it was paid for, which is a
defensible abstraction for an infantry division and an absurd one for a battleship. A navy
you can conjure in a month is not a navy, and more to the point it removes the only
interesting decision about one: what to lay down, a year before you know whether you will
need it.

So an order to build becomes a **yard with something in it**. The money and the manpower
leave the treasury when the order is given, the hull sits in ``construction`` for as many
turns as its kind takes, and a formation appears when it is finished. A half-built
battleship is money already spent, which is what makes a building programme a commitment
rather than a shopping list.

**Industrial capacity is a count, not a currency.** A nation may have only so many things
building at once, set by the size of its economy. That is the gate that stops a rich power
simply buying the war in one turn, and it is deliberately the crudest possible version of
one: the shape it needs to have is "you cannot do everything at once", and everything
finer -- steel, yards, factories that are themselves built -- is a system on top of this
one rather than a change to it.

**Time is compressed and the compression is stated.** A 1936 battleship took three to four
years, which at a turn a month is forty turns and longer than any war in this game. Twelve
is a year of wartime emergency building, which keeps the decision real -- lay down now,
fight with it next year -- without making it unplayable.

Everything here is diffs citing engine-authored events, so a rewind unbuilds ships, refunds
nothing it should not, and puts half-finished hulls back in the yard at the right stage.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..sim.resolve import uncertain
from ..state.apply import apply_batch, record_event, unrecord_events
from ..state.diff import Diff, DiffBatch
from ..state.models import DiffOp, EventKind, EventOrigin, Role, Visibility
from .bounds import absorbable
from .constituencies import industrial_throughput
from .projects import CIVIL_PROJECTS, CivilProject, Effect, project_by_id
from .research import catalogue_for, establishment, known_technologies, technology_by_id

__all__ = [
    "raised_strength",
    "BUILD_DAYS",
    "InvestmentReport",
    "ProductionReport",
    "advance_construction",
    "autonomous_investment",
    "expected_stability",
    "in_progress",
    "industrial_capacity",
    "order_construction",
    "order_project",
    "order_research",
    "project_cost",
    "success_chance",
]

#: Days from order to delivery, by unit kind. See the note on compression above.
#:
#: **Days, not turns, and the distinction is the whole point.** A turn is a week or a
#: year depending on a button the player presses for pacing reasons, so a build time in
#: turns made a carrier take eleven weeks in one game and ten years in another. These
#: are the old turn counts times thirty: the tuning was done at monthly turns and this
#: is what those numbers always meant.
BUILD_DAYS: dict[str, float] = {
    "infantry": 30.0,
    "armour": 60.0,
    "air_wing": 60.0,
    "transport": 90.0,
    "carrier": 300.0,
    "battleship": 360.0,
}

#: How much industry one simultaneous project occupies. The United States can run five at
#: once in 1936 and Ethiopia one, which is about right and is the only claim being made.
_INDUSTRY_PER_SLOT = 12.0

#: How much industry a nation is assumed to have when a scenario does not say. The old
#: capacity formula was ``economy / 20``, and this reproduces it exactly, so no existing
#: world's queue changes size on the day industry became a column.
_INDUSTRY_FROM_ECONOMY = 0.6

#: What building industry costs, as a share of what you already have. A country that can
#: build factories which build factories grows exponentially; charging more for each
#: additional works turns that into a curve. This is the whole anti-runaway guard.
_INDUSTRY_COST_SCALING = 60.0


@dataclass(slots=True)
class ProductionReport:
    started: list[str] = field(default_factory=list)
    delivered: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    #: Nations whose yards did less than the month, because industry is dragging its feet.
    #: Reported rather than merely applied: friction a player cannot see is a bug they
    #: cannot diagnose.
    dragging: list[str] = field(default_factory=list)
    #: Nations whose yards did less than the month for want of oil, steel or rare earths.
    #: Separate from :attr:`dragging` because they are different problems with different
    #: answers -- one is settled with your own constituencies and one by opening a route or
    #: breaking a blockade, and a player told only "the yards are slow" cannot tell which
    #: they have.
    starved: list[str] = field(default_factory=list)


def raised_strength(
    conn: sqlite3.Connection, world_id: str, nation_id: str, base: float,
    domain: str = "land",
) -> float:
    """What a formation is worth when this country raises it.

    **Stamped once, at delivery, and never re-applied.** A division raised under mass
    mobilisation is a conscript division for the rest of its life: changing doctrine does
    not retrain the army you already have, it changes the army you raise next. That is
    physically true, it costs one multiplication where `base_strength` is already being
    read, and it is a second reason a doctrine takes time to matter -- the manpower pool
    moves the month you switch, and quality only moves as the army turns over.

    Land only. How the army is raised says nothing about what a battleship is worth.
    """
    if domain != "land":
        return base
    from .doctrines import factor

    return round(base * factor(conn, world_id, nation_id, "mobilisation",
                               "raised_strength"), 3)


def industrial_capacity(conn: sqlite3.Connection, nation_id: str) -> int:
    """How many things this nation can have under construction at once.

    Read from ``industry`` rather than derived from ``economy``, which is the difference
    between a consequence and a decision: a country can now put years into being able to
    build more, and can have that taken away with the province the works were in.
    """
    row = conn.execute(
        "SELECT industry, economy FROM nation WHERE id = ?", (nation_id,)
    ).fetchone()
    if row is None:
        return 0
    industry = float(row["industry"] or 0.0)
    if industry <= 0:
        industry = float(row["economy"] or 0.0) * _INDUSTRY_FROM_ECONOMY
    slots = industry / _INDUSTRY_PER_SLOT

    # **A shortage does not reach the order book, and did until 2026-09-18.** It lands on
    # the *pace* instead -- :func:`advance_construction` scales every yard's month by
    # :func:`atlas.engine.trade.shortfall_of` -- and it used to land here as well, so a
    # blockaded power lost yards *and* worked the ones it kept at a third of the pace.
    # Chosen from the throttle battery (`scratchpad/NAVAL_BALANCE_BATTERIES.md`), which ran
    # slot-only, rate-only and both against three sizes of power:
    #
    # - **Size-neutral.** On the rate alone a total blockade costs a one-yard minor and a
    #   ten-yard great power the same share of their output (35/35/35). The slot count cannot
    #   throttle a one-yard power at all -- ``max(1, round(0.35))`` is 1 -- and applying both
    #   compounds on the large end (a mid-tier fell to 12%).
    # - **The constant is legible.** Under a total blockade a yard delivers exactly
    #   :data:`atlas.engine.trade._STARVED_CAPACITY` of its month, which is what that
    #   constant's docstring has always said it does. Through ``round()`` it was not.
    # - **Monotonic.** The slot count moved in integer jumps that were not connected to the
    #   floor; the rate follows it.
    #
    # The alternative, if the order book should bite again, is to restore
    # ``slots *= shortfall_of(conn, world_id, nation_id)`` here -- that is the whole revert,
    # plus `dependency._capacity_at`, which must agree with this function.
    return max(1, round(slots))


def project_cost(
    conn: sqlite3.Connection, nation_id: str, project: CivilProject
) -> tuple[float, float]:
    """``(treasury, manpower)`` a public work costs this nation *now*.

    A steelworks does not cost what the catalogue says: its price rises with the industry
    already standing, so the number a player is quoted has to be computed against their
    own base, not read off the menu. One place computes it, so the affordance the player
    reads and the charge the engine makes can never drift apart.
    """
    cost_treasury, cost_manpower = project.cost_treasury, project.cost_manpower
    if project.cost_scales_with_industry:
        row = conn.execute(
            "SELECT industry FROM nation WHERE id = ?", (nation_id,)
        ).fetchone()
        scale = 1.0 + float((row and row["industry"]) or 0.0) / _INDUSTRY_COST_SCALING
        cost_treasury *= scale
        cost_manpower *= scale
    return cost_treasury, cost_manpower


def in_progress(conn: sqlite3.Connection, world_id: str, nation_id: str) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM construction WHERE world_id = ? AND nation_id = ?",
            (world_id, nation_id),
        ).fetchone()[0]
    )


def order_construction(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    nation_id: str,
    kind: str,
    region_id: str,
    quantity: int,
    turn: int,
    date: str,
) -> ProductionReport:
    """Lay down ``quantity`` of ``kind``, charging for them now.

    Refuses more than the yards can hold, more than the treasury can pay for, and anything
    the region could not host when it is finished -- there is no point building a
    battleship in Bavaria and discovering it at launch.
    """
    report = ProductionReport()
    template = conn.execute(
        "SELECT kind, domain, cost_treasury, cost_manpower, base_strength, build_days"
        "  FROM unit_template WHERE world_id = ? AND kind = ?",
        (world_id, kind),
    ).fetchone()
    if template is None:
        report.problems.append(f"nothing called {kind!r} can be built here")
        return report

    region = conn.execute(
        "SELECT kind, COALESCE(controller_nation_id, owner_nation_id) AS holder"
        "  FROM region WHERE id = ? AND world_id = ?",
        (region_id, world_id),
    ).fetchone()
    if region is None or region["holder"] != nation_id:
        report.problems.append(f"{nation_id} does not hold {region_id}")
        return report
    if template["domain"] == "sea" and region["kind"] not in {"coastal", "ocean"}:
        report.problems.append(f"a {kind} needs a coast; {region_id} has none")
        return report
    if template["domain"] != "sea" and region["kind"] == "ocean":
        report.problems.append(f"a {kind} cannot be built at sea")
        return report

    nation = conn.execute(
        "SELECT name, treasury, manpower FROM nation WHERE id = ?", (nation_id,)
    ).fetchone()
    if nation is None:
        return report

    room = industrial_capacity(conn, nation_id) - in_progress(conn, world_id, nation_id)
    affordable = min(
        max(0, int(quantity)),
        max(0, room),
        int(float(nation["treasury"]) // max(0.01, float(template["cost_treasury"]))),
        int(float(nation["manpower"]) // max(0.01, float(template["cost_manpower"]))),
    )
    wanted = max(0, int(quantity))
    if affordable <= 0:
        report.problems.append(
            f"{nation['name']} cannot lay down a {kind}: "
            + (
                f"every yard is already occupied ({in_progress(conn, world_id, nation_id)}"
                f" of {industrial_capacity(conn, nation_id)} in use)"
                if room <= 0
                else f"it costs {float(template['cost_treasury']):.0f} treasury and "
                f"{float(template['cost_manpower']):.0f} manpower, and there is "
                f"{float(nation['treasury']):.0f} and {float(nation['manpower']):.0f}"
            )
        )
        return report
    if affordable < wanted:
        # Silent partial fulfilment is how a player orders two transports, gets one, and
        # never finds out. Found by playing Italy: "1 transport laid down" against an
        # order for two, with nothing anywhere saying why.
        report.problems.append(
            f"{nation['name']} could only lay down {affordable} of {wanted} {kind}(s): "
            + ("the yards hold no more" if room < wanted else "the treasury would not stretch")
        )

    build_days = float(template["build_days"] or 0.0)
    # Rounded to whole months for the announcement only. "due in 300 days" is a
    # precision the yards do not have and the player did not ask for.
    due = round(build_days / 30.0)
    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.CONSTRUCTION, origin=EventOrigin.ENGINE, actor_nation_id=nation_id,
        text=(
            f"{nation['name']} lays down {affordable} {kind}"
            f"{'s' if affordable != 1 else ''}"
            + (f", due in {due} month{'s' if due != 1 else ''}." if due else ".")
        ),
        # A hull on a slipway is visible from the water. What it is *for* is not, and that
        # is a secret-projects question rather than a shipyard one.
        visibility=Visibility.PUBLIC,
    )

    # **The next free ordinal, not always 1.** The id is nation, kind and turn, so two
    # orders for the same kind in one turn -- two provinces, or the same one twice -- both
    # numbered from 1, collided on `construction.id`, and the second batch was refused.
    # The format is kept as it was, region and all left out, because the id is state: it
    # orders `advance_construction` and it is in the digest, so a first order this turn
    # must get exactly the id it always got or every recorded world moves.
    prefix = f"c_{nation_id}_{kind}_{turn}_"
    taken = {
        str(row[0]) for row in conn.execute(
            "SELECT id FROM construction WHERE substr(id, 1, ?) = ?", (len(prefix), prefix))
    }
    ids: list[str] = []
    ordinal = 0
    while len(ids) < affordable:
        ordinal += 1
        if f"{prefix}{ordinal}" not in taken:
            ids.append(f"{prefix}{ordinal}")

    diffs: list[Diff] = []
    for construction_id in ids:
        diffs.append(
            Diff(
                "construction", construction_id,
                "__insert__", DiffOp.INSERT,
                {
                    "world_id": world_id, "nation_id": nation_id, "region_id": region_id,
                    "project_kind": "unit",
                    "kind": kind, "days_remaining": build_days, "started_turn": turn,
                },
                reason_event_id=event_id, visibility_scope=Visibility.PUBLIC,
            )
        )
    diffs += [
        Diff("nation", nation_id, "treasury", DiffOp.ADJUST,
             -float(template["cost_treasury"]) * affordable, reason_event_id=event_id),
        Diff("nation", nation_id, "manpower", DiffOp.ADJUST,
             -float(template["cost_manpower"]) * affordable, reason_event_id=event_id),
    ]

    outcome = apply_batch(
        conn,
        DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                  proposer_model=None, diffs=tuple(diffs)),
    )
    if outcome.applied:
        report.started.append(f"{nation['name']}: {affordable} {kind}")
    else:
        # The event went on the record before the batch so the diffs could cite it; with
        # the batch refused, "lays down" would be news of hulls that were never started.
        unrecord_events(conn, [event_id])
        conn.commit()
        report.problems.append(f"the order could not be recorded: {outcome.reason}")
    return report


def order_project(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    nation_id: str,
    project_id: str,
    region_id: str,
    turn: int,
    date: str,
) -> ProductionReport:
    """Begin a public work. Same queue, same money, same waiting as a battleship.

    Deliberately the same queue. A country choosing between housing and a capital ship is
    the decision this whole system exists to pose, and giving the peaceful side its own
    separate allowance would remove it.
    """
    report = ProductionReport()
    project = project_by_id(project_id)
    if project is None:
        report.problems.append(
            f"there is no public work called {project_id!r}; "
            + "try " + ", ".join(p.id for p in CIVIL_PROJECTS)
        )
        return report

    region = conn.execute(
        "SELECT name, kind, COALESCE(controller_nation_id, owner_nation_id) AS holder"
        "  FROM region WHERE id = ? AND world_id = ?",
        (region_id, world_id),
    ).fetchone()
    if region is None or region["holder"] != nation_id:
        report.problems.append(f"{nation_id} does not hold {region_id}")
        return report
    if region["kind"] == "ocean":
        report.problems.append("public works are built on land")
        return report

    nation = conn.execute(
        "SELECT name, treasury, manpower, industry FROM nation WHERE id = ?", (nation_id,)
    ).fetchone()
    if nation is None:
        return report

    # Each additional works costs more than the last, which is what stops factories that
    # build factories from running away. Same helper the affordance quotes from.
    cost_treasury, cost_manpower = project_cost(conn, nation_id, project)

    if in_progress(conn, world_id, nation_id) >= industrial_capacity(conn, nation_id):
        report.problems.append(
            f"{nation['name']} has nothing spare to start {project.name} with: "
            "every yard and works is already occupied"
        )
        return report
    if float(nation["treasury"]) < cost_treasury or float(nation["manpower"]) < cost_manpower:
        report.problems.append(
            f"{nation['name']} cannot afford {project.name} "
            f"({cost_treasury:.0f} treasury, {cost_manpower:.0f} manpower)"
        )
        return report

    # One at a time per region. Two housing programmes in the same province is a way of
    # spending money rather than a plan, and the queue is short enough to matter.
    duplicate = conn.execute(
        "SELECT 1 FROM construction WHERE world_id = ? AND nation_id = ?"
        "   AND region_id = ? AND kind = ?",
        (world_id, nation_id, region_id, project.id),
    ).fetchone()
    if duplicate:
        report.problems.append(
            f"{project.name} is already under way in {region['name']}"
        )
        return report

    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.CONSTRUCTION, origin=EventOrigin.ENGINE, actor_nation_id=nation_id,
        text=(
            f"{nation['name']} begins {project.name} in {region['name']}, "
            f"due in {project.turns} turns."
        ),
        visibility=Visibility.PUBLIC,
    )
    diffs = [
        Diff(
            # The region is in the id because a country may well start the same work in
            # two provinces on the same day, and without it the second insert collides
            # with the first and the whole order is silently refused.
            "construction", f"c_{nation_id}_{project.id}_{region_id}_{turn}", "__insert__",
            DiffOp.INSERT,
            {
                "world_id": world_id, "nation_id": nation_id, "region_id": region_id,
                "project_kind": "civil", "kind": project.id,
                "days_remaining": project.turns * 30.0, "started_turn": turn,
            },
            reason_event_id=event_id, visibility_scope=Visibility.PUBLIC,
        ),
        Diff("nation", nation_id, "treasury", DiffOp.ADJUST, -round(cost_treasury, 2),
             reason_event_id=event_id),
    ]
    if cost_manpower:
        diffs.append(
            Diff("nation", nation_id, "manpower", DiffOp.ADJUST, -round(cost_manpower, 2),
                 reason_event_id=event_id)
        )

    outcome = apply_batch(
        conn,
        DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                  proposer_model=None, diffs=tuple(diffs)),
    )
    if outcome.applied:
        report.started.append(f"{nation['name']}: {project.name} in {region['name']}")
    else:
        # As in `order_construction`: "begins" was written so the diffs could cite it, and
        # with the batch refused it is news of works nobody started.
        unrecord_events(conn, [event_id])
        conn.commit()
        report.problems.append(f"the works could not be started: {outcome.reason}")
    return report


#: Keep this multiple of a project's cost in the treasury as a buffer, so the master AI
#: never spends a nation into arrears against its own upkeep.
_INVEST_RESERVE = 1.4


@dataclass(slots=True)
class InvestmentReport:
    invested: list[str] = field(default_factory=list)
    #: Powers that deliberately spent nothing this turn, and what they are saving for.
    #: **Saving is a decision and is reported as one**, or a turn where a government held
    #: its money is indistinguishable from a turn where the chooser found nothing at all.
    banked: list[str] = field(default_factory=list)


#: **Below this a government builds for its people before it builds for its factories.**
#: TUNABLE, and set to `budget._STABILITY_RESTING` rather than to `leaders._DANGER_BELOW`.
#:
#: At the danger threshold itself the two mechanisms make a country oscillate *across* the
#: line rather than above it: it builds industry down to 50, becomes strained, builds
#: restoratively back up, and repeats -- so half of a peaceful century is spent inside the
#: band where coup risk starts. Measured at 50.6 average with risk still climbing. A
#: government that is paying attention starts spending on the reasons *before* it is in
#: danger, which is both the better rule and the one that puts the equilibrium somewhere a
#: country can sit.
_STRAINED_BELOW = 55.0

#: **What a strained power reaches for, in order -- and every one of these is CERTAIN.**
#: Housing puts back 4 stability and health 3, neither can fail, and the ordinary list has
#: nothing positive at all: steelworks -2, workshops -1, rail 0.
#:
#: **Land reform used to lead this list, and that was the defect.** It is the catalogue's
#: only `risk_from_legitimacy` gamble: +9 stability when it holds, and **-8 stability and
#: -3 legitimacy when it does not** -- and the legitimacy it takes on failure is the same
#: number the *next* attempt's odds are read off, so a government that keeps reaching for
#: it gets worse at it every time it fails. Putting it first meant the government with the
#: least legitimacy, and therefore the worst odds, took the gamble *before* either certain
#: answer. Measured on Russia (Modern Day starts it at stability 54 -- already strained --
#: and legitimacy 44): repeated land reforms, seven collapses of eight points each, and a
#: coup.
#:
#: It is not removed. See :data:`_RESTORATIVE_GAMBLES`.
_RESTORATIVE = ("housing", "health")

#: **The gambles, kept and gated rather than deleted.** A government the country believes
#: in can still break up the great estates, and an unattended one still reaches for it --
#: but only after the safe list, and only when :func:`expected_stability` says the odds are
#: actually on its side. What changed is that it is no longer the default emergency
#: stabiliser for a government that has no business gambling.
_RESTORATIVE_GAMBLES = ("land_reform",)

#: **Research is bought out of surplus, never instead of this turn's public work.**
#:
#: The share of the work it would otherwise have built that a government must *still* be
#: able to afford after paying for a programme. At 1.0 a power begins a programme only when
#: the money for the workshop is still there afterwards -- so the programme comes out of
#: treasury that was going to sit idle anyway, because the queue only takes one thing a
#: turn. That is what makes "knowledge moved" and "development fell" separable rather than
#: two readings of the same spending.
#:
#: **It is a constant because the control arm of the paired measurement is this constant set
#: to infinity.** With it there, no unattended power ever begins a programme and the world
#: is exactly the one every previous measurement was taken in: knowledge 0.000 for every
#: power in every run. A test that only asserted "knowledge rose" would be measuring a
#: feature against nothing.
_RESEARCH_SURPLUS = 1.0

#: How near the price a government must be before it **saves for what it wanted** rather
#: than reordering to something cheaper it did not want.
#:
#: The argument for it is sound and the measurement refused it. The chooser walks its
#: preference list and takes the first affordable entry, so a power that cannot quite afford
#: a steelworks builds a workshop instead -- every turn, for ever, because the workshop keeps
#: the treasury just below the steelworks. Reordering is not a decision, it is a treadmill.
#:
#: **Measured at 0.55, over 60 turns and three seeds of Modern Day, saving costs 4.0% of the
#: world's total development and buys nothing at all.** Its own arm -- saving on, research
#: off -- finished at 10,586 against a baseline's 11,022 with the knowledge component still
#: at zero everywhere. Added to research it takes the joint cost from 1.7% to 4.4% and buys
#: about four more technologies. A government that waits is a government that did not build,
#: and over sixty turns the compounding beats the better factory it was waiting for.
#:
#: So it ships **off**, as a mechanism that exists, is tested, and is one constant away from
#: being on. It is off rather than deleted because the number that was measured is 0.55 and
#: the number nobody has measured is a bar high enough that the wait is short -- and because
#: whether a world should have governments that save is a design question rather than an
#: arithmetic one.
#:
#: Deliberately not applied under strain either way: a country whose people are restive has
#: more pressing uses for the money than the better version of a factory.
_BANK_ABOVE = float("inf")


def success_chance(legitimacy: float) -> float:
    """The odds a gamble of government comes off. **One definition, two callers.**

    A government nobody believes cannot redistribute land quietly: legitimacy of 50 is a
    coin flip, 80 is comfortable, 20 is asking for trouble. Both the policy that decides
    whether to take the gamble (:func:`expected_stability`) and the resolution that settles
    it (:func:`_deliver_project`) read it from here, because a chooser working off
    different odds than the dice is a chooser that is wrong by construction.
    """
    return min(0.9, max(0.15, legitimacy / 100.0))


def expected_stability(
    conn: sqlite3.Connection, nation_id: str, project: CivilProject
) -> float:
    """Stability this project is **expected** to be worth to this government, in points.

    For an ordinary project that is simply its stability effect. For a gamble it is the
    real expectation at the odds this government has *now* --
    ``p x gain x worth - (1 - p) x loss`` -- with gains scaled by the leader's worth and
    the failure not scaled, because that is exactly how :func:`_deliver_project` applies
    them. A good government suffering more when a scheme fails would be backwards there
    and it would be backwards here.

    **The arithmetic, stated rather than implied.** Land reform is +9 / -8, so with a
    leader worth ``w`` the break-even probability is ``8 / (8 + 9w)``. At ``w = 1`` that is
    ``0.4706`` -- legitimacy 47. Russia's government is `hardliner`, which is 0.95 on
    development, so its break-even is ``8 / 16.55 = 0.4834``: it needs legitimacy 48.4 and
    it starts Modern Day on 44, giving ``0.44 x 9 x 0.95 - 0.56 x 8 = -0.72`` stability per
    attempt. **That figure is the model's own per-attempt odds, not a realized failure
    rate.**
    """
    def stability_in(effects: tuple[Effect, ...]) -> float:
        return sum(effect.amount for effect in effects
                   if effect.scope == "nation" and effect.field_name == "stability")

    if not project.risky:
        return stability_in(project.effects)

    from .leaders import modifier as leader_modifier

    row = conn.execute(
        "SELECT legitimacy FROM nation WHERE id = ?", (nation_id,)).fetchone()
    legitimacy = float(50.0 if row is None or row["legitimacy"] is None
                       else row["legitimacy"])
    chance = success_chance(legitimacy)
    worth = leader_modifier(conn, nation_id, "development")
    return (chance * stability_in(project.effects) * worth
            + (1.0 - chance) * stability_in(project.on_failure))


def _programme_for(
    conn: sqlite3.Connection, world_id: str, nation_id: str, treasury: float,
    ordinary_cost: float,
) -> str | None:
    """The programme this power could begin **out of surplus**, if any.

    **Nothing refused research; nothing ever reached it.** The chooser walked an ordered
    list of public works and took the first thing it could afford, and a workshop is always
    cheaper than a programme -- so across every measured run the `knowledge` component sat
    at 0.000 for every power in the world, and the one power in any run that researched
    anything was a hand-built specialist. Research worked in every era and no government
    ever bought any.

    Two rules, and they are what stop this becoming the wasteful-spending failure it would
    otherwise be:

    * **one at a time.** A power with a programme already running starts nothing else. A
      country that queues its whole tree at once is not choosing, it is spending.
    * **out of surplus.** It must still be able to afford the public work it would otherwise
      have laid down this turn -- see :data:`_RESEARCH_SURPLUS`. The queue takes one thing a
      turn either way, so the money a programme costs is money that was going to sit idle,
      and the workshop is still there next turn.

    Cheapest first, ties broken by id, so the same world makes the same choice twice.
    """
    from .research import available_technologies

    if conn.execute(
        "SELECT 1 FROM construction WHERE world_id = ? AND nation_id = ?"
        "   AND project_kind = 'research'", (world_id, nation_id)
    ).fetchone():
        return None
    # **Better weapons are bought for a war.** The upgrade lines are eight programmes in
    # every era, and a settled power that worked through them in peacetime spent its
    # queue on them instead of on its economy -- development fell a tenth behind a power
    # that did not. At war, a better tank is the thing worth the slot.
    at_war = conn.execute(
        "SELECT 1 FROM relation WHERE world_id = ? AND nation_a = ? AND at_war = 1",
        (world_id, nation_id)).fetchone() is not None
    open_now = sorted(
        (tech for tech in available_technologies(conn, world_id, nation_id)
         if at_war or not tech.mark),
        key=lambda tech: (tech.cost_treasury, tech.id),
    )
    for tech in open_now:
        needed = (tech.cost_treasury + ordinary_cost * _RESEARCH_SURPLUS) * _INVEST_RESERVE
        if treasury >= needed:
            return tech.id
    return None


def _choose_development(
    conn: sqlite3.Connection, world_id: str, nation: sqlite3.Row
) -> tuple[str, str] | None:
    """What an unattended power should do with this turn's one order. Deterministic.

    Returns ``(kind, id)`` where ``kind`` is ``"project"``, ``"research"`` or ``"bank"`` --
    the last meaning *save for what you wanted rather than buy something you did not*. A
    power with a workshop of its own is a different thing from a power that keeps buying
    workshops because it can never quite reach anything better.

    A power with real industry behind it reaches for a steelworks; anyone can bootstrap
    with workshops; a nation with money but little to build with paves rail for the
    economy. The first it can afford *with a reserve left over* is the one it starts.

    **And a government under domestic strain builds for its people first**, which it did
    not before and which was a real defect rather than a missing nicety. The list was
    steelworks, workshops, rail -- the three civil projects whose stability effect is
    *negative* -- and housing, health and land reform were never reachable by an unattended
    power at all. Measured over 110 turns with nobody steering: founders' stability 57.8 ->
    37.2, every applied diff a construction charge, and the coup risk that
    `leaders._RISK_FROM_INSTABILITY` computes off it climbing with it. A country
    industrialised itself into a coup, and the industrialising was the only thing it did.

    **And it will hold its money rather than make the crisis worse.** The restorative list
    used to lead with `land_reform`, a gamble that costs 8 stability and 3 legitimacy when
    it fails, and the ordinary industrial list was appended under it -- so a strained
    government that could not quite afford the housing reached first for the bad bet and
    then for a workshop that costs it another point. Under strain it now takes a *certain*
    restorative, or a gamble whose expectation is genuinely positive, or nothing at all.
    See :data:`_RESTORATIVE` and :func:`expected_stability`.

    **Research is offered only to a settled power, and only out of surplus.** A country
    with restive people does not open a laboratory, and a country that would have to skip
    its workshop to pay for one is buying knowledge with development, which is the exact
    failure this has to be able to be measured against. See :func:`_programme_for`.
    """
    nation_id = str(nation["id"])
    treasury = float(nation["treasury"] or 0.0)
    manpower = float(nation["manpower"] or 0.0)
    industry = float(nation["industry"] or 0.0)
    ordinary = (["steelworks"] if industry >= 30 else []) + ["workshops", "rail"]
    stability = nation["stability"]
    # `x or 100.0` reads the *falsy* 0.0 as missing, so a government at exactly zero
    # stability -- the one that needs the restorative list most -- was scored as a
    # settled one and went back to laying steelworks. Only a genuine NULL is missing.
    strained = float(100.0 if stability is None else stability) < _STRAINED_BELOW
    # **Under strain a government does not buy anything that costs it more stability, and
    # does not gamble unless the odds are on its side.** The safe restoratives first, then
    # a gamble only if its expectation is positive -- see :data:`_RESTORATIVE` for what
    # leading with the gamble did to Russia. The ordinary industrial list is not appended:
    # every entry in it is stability-negative or neutral, and a strained power that spends
    # 20 on a workshop it can afford is a strained power that never accumulates the 25 for
    # the housing it needs. That is the same treadmill :data:`_BANK_ABOVE` describes,
    # walked downhill.
    order = ordinary
    if strained:
        gambles = [
            gamble for gamble in _RESTORATIVE_GAMBLES
            if (candidate := project_by_id(gamble)) is not None
            and expected_stability(conn, nation_id, candidate) > 0.0
        ]
        order = [*_RESTORATIVE, *gambles]

    priced: list[tuple[str, float, float]] = []
    for project_id in order:
        project = project_by_id(project_id)
        if project is None:
            continue
        cost_treasury, cost_manpower = project_cost(conn, nation_id, project)
        priced.append((project_id, cost_treasury, cost_manpower))

    if not strained and priced:
        programme = _programme_for(
            conn, world_id, nation_id, treasury, ordinary_cost=priced[0][1])
        if programme is not None:
            return ("research", programme)

    for project_id, cost_treasury, cost_manpower in priced:
        if treasury >= cost_treasury * _INVEST_RESERVE and manpower >= cost_manpower:
            return ("project", project_id)
        # **Within reach of what it actually wanted**: wait rather than reorder. Only the
        # treasury gate banks -- a manpower shortage is not something saving money fixes.
        if not strained and treasury >= cost_treasury * _BANK_ABOVE:
            return ("bank", project_id)
    if strained and priced:
        # **Abstaining is a move, and it is reported as one.** A government that cannot
        # afford anything that would help its people holds the money until it can, rather
        # than falling through to a bad gamble or to a works that costs it another point.
        # Building nothing this turn can genuinely be better than making the crisis worse,
        # and a turn where a government held its money must not look like a turn where the
        # chooser found nothing -- which is why this is a `bank` and not a `None`.
        return ("bank", priced[0][0])
    return None


def _best_investment_region(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> str | None:
    """Where an unattended power should lay its next public work.

    This used to be *the region with the fewest works under way, then alphabetically* --
    which was right when every province was worth exactly the same, and stopped being
    right the moment development varied. What a work is worth now depends on where it goes,
    and by more than the margins usually argued about here:

    **Ground you hold but do not own yields 40% of its development**
    (:data:`atlas.engine.budget._OCCUPIED_YIELD`), and it yields that discount *twice* --
    once into revenue and once into the ceiling the economy grows toward. So a steelworks
    on conquered ground earns two-fifths of the same steelworks at home, for the same money
    and the same years. The old chooser did not know the difference, and an occupier's
    provinces sort early in a good many alphabets.

    **Restive ground is losing development while you add to it.** `erode_development` eats
    the development of a province in proportion to the unrest it carries, so a work laid
    down in occupied territory is filling a bucket the same turn engine is draining.

    So the order is: your own core first, then quiet ground, then spread the works out --
    which is what the old chooser was for and is kept -- and only then the tiebreak.

    The tiebreak is highest development, and it is honestly a tiebreak: the effects a
    project applies are flat, so the *marginal* revenue is the same in a rich province and
    a poor one. What it buys is legibility. A nation that builds in its industrial heart
    rather than in whichever province happens to sort first is making a choice a player can
    read, and reading the master AI's choices is most of how a player learns to trust it.
    """
    row = conn.execute(
        """
        SELECT r.id FROM region r
         WHERE r.world_id = ?
           AND COALESCE(r.controller_nation_id, r.owner_nation_id) = ?
           AND r.kind <> 'ocean'
         ORDER BY
           -- Owned outright, not merely held: worth 2.5x as much, twice over.
           CASE WHEN r.owner_nation_id = ? THEN 0 ELSE 1 END,
           -- ...and your own core rather than somebody else's, which is what decides
           -- whether the unrest layer is quietly eroding what you build.
           CASE WHEN r.home_nation_id = ? THEN 0 ELSE 1 END,
           (SELECT COUNT(*) FROM construction c
             WHERE c.world_id = r.world_id AND c.region_id = r.id),
           r.development DESC,
           r.id
         LIMIT 1
        """,
        (world_id, nation_id, nation_id, nation_id),
    ).fetchone()
    return row["id"] if row else None


def autonomous_investment(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str,
    exclude: frozenset[str] = frozenset(),
) -> InvestmentReport:
    """Play the treasury of every power no human and no tracked agent is driving.

    The economy-growth loop compounds off what a country builds -- but a minor nation has
    no model call of its own, so without this it never builds and its economy sits at
    whatever its starting industry supports while the player's climbs away. This is the
    master AI for everyone else: cheap, deterministic, and routed through the same
    ``order_project`` the player and the tracked agents use, so a minor's steelworks is
    charged and queued exactly as theirs is. A refused order (its one region already busy)
    is not surfaced -- the power simply tries again next turn.
    """
    report = InvestmentReport()
    nations = conn.execute(
        "SELECT id, name, economy, industry, treasury, manpower, stability FROM nation "
        "WHERE world_id = ? AND is_alive = 1 AND is_player = 0 AND is_tracked = 0 "
        "ORDER BY id",
        (world_id,),
    ).fetchall()
    for nation in nations:
        nation_id = str(nation["id"])
        # A study subject has a model of its own every turn (`TurnEngine.study_subjects`);
        # the master AI answering for it too would be a second government.
        if nation_id in exclude:
            continue
        if in_progress(conn, world_id, nation_id) >= industrial_capacity(conn, nation_id):
            continue
        choice = _choose_development(conn, world_id, nation)
        if choice is None:
            continue
        kind, chosen = choice
        if kind == "bank":
            report.banked.append(f"{nation['name']}: saving for {chosen}")
            continue
        if kind == "research":
            # **The same door the player and the tracked agents use.** `order_research`
            # charges the treasury, takes the queue slot and files the event as a
            # DISCOVERY; nothing here is a second way to begin a programme.
            result = order_research(
                conn, world_id, nation_id=nation_id, tech_id=chosen, turn=turn, date=date)
            report.invested.extend(result.started)
            continue
        region_id = _best_investment_region(conn, world_id, nation_id)
        if region_id is None:
            continue
        result = order_project(
            conn, world_id, nation_id=nation_id, project_id=chosen,
            region_id=region_id, turn=turn, date=date,
        )
        report.invested.extend(result.started)
    return report


def order_research(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    nation_id: str,
    tech_id: str,
    turn: int,
    date: str,
) -> ProductionReport:
    """Begin a research programme. Same queue, same money, same waiting."""
    report = ProductionReport()
    tech = technology_by_id(tech_id, catalogue_for(conn, world_id))
    if tech is None:
        # Named rather than merely absent: a 2026 staff asking for radar has made a
        # category error worth telling it about, and "unknown programme" reads as a
        # typo rather than as "that belongs to a different century".
        report.problems.append(
            f"there is no programme called {tech_id!r} in this era"
        )
        return report

    known = known_technologies(conn, world_id, nation_id)
    if tech.id in known:
        report.problems.append(f"{tech.name} is already known")
        return report
    missing = [need for need in tech.requires if need not in known]
    if missing:
        report.problems.append(
            f"{tech.name} needs {', '.join(missing)} first"
        )
        return report

    nation = conn.execute(
        "SELECT name, treasury FROM nation WHERE id = ?", (nation_id,)
    ).fetchone()
    if nation is None:
        return report
    if in_progress(conn, world_id, nation_id) >= industrial_capacity(conn, nation_id):
        report.problems.append(
            f"{nation['name']} has nothing spare to begin {tech.name} with"
        )
        return report
    if float(nation["treasury"]) < tech.cost_treasury:
        report.problems.append(f"{nation['name']} cannot afford {tech.name}")
        return report
    if conn.execute(
        "SELECT 1 FROM construction WHERE world_id = ? AND nation_id = ? AND kind = ?",
        (world_id, nation_id, tech.id),
    ).fetchone():
        report.problems.append(f"{tech.name} is already under way")
        return report

    capital = conn.execute(
        "SELECT capital_region_id FROM nation WHERE id = ?", (nation_id,)
    ).fetchone()["capital_region_id"]
    if not capital:
        report.problems.append(f"{nation['name']} has no capital to run a programme from")
        return report

    # A secret programme is hidden by exactly the mechanism everything else in ATLAS is
    # hidden by: the event is SECRET and only its owner is in event_visibility, so
    # `known_events` never returns it to anybody else and rule INFO-002 guarantees the
    # diffs that cite it are no more observable than it is. There is no second hiding
    # mechanism, and there is deliberately nowhere for one to be forgotten.
    # **DISCOVERY, not CONSTRUCTION.** A technology programme is on the research track, not
    # the public-works one, and the distinction is load-bearing rather than tidy: the
    # briefing collapses *other nations'* construction into a single "routine public works"
    # count, because the master AI develops every unattended nation every turn and the raw
    # feed is otherwise unreadable. Filed as construction, a rival's weapons programme went
    # into that count -- PT34d and PT34e both hid "Russia completes Uncrewed systems at
    # scale" and "United States completes Integrated air and missile defence" behind the
    # word *routine*. Secrecy is untouched: it was never the kind that hid these.
    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.DISCOVERY, origin=EventOrigin.ENGINE, actor_nation_id=nation_id,
        text=(
            f"{nation['name']} begins work on {tech.name}."
            if not tech.secret
            else f"{nation['name']} quietly begins work on {tech.name}."
        ),
        visibility=Visibility.SECRET if tech.secret else Visibility.PUBLIC,
        visible_to=(nation_id,) if tech.secret else (),
    )
    # Every diff carries the same scope as its cause. Rule INFO-002 refuses a public
    # change with a covert cause -- "a covert cause cannot produce overt state" -- and it
    # caught this the first time the secret programme was written, which is the rule doing
    # exactly the job it was added for.
    scope = Visibility.SECRET if tech.secret else Visibility.PUBLIC
    outcome = apply_batch(
        conn,
        DiffBatch(
            world_id=world_id, turn=turn, proposer_role=Role.ENGINE, proposer_model=None,
            diffs=(
                Diff(
                    "construction", f"c_{nation_id}_{tech.id}_{turn}", "__insert__",
                    DiffOp.INSERT,
                    {
                        "world_id": world_id, "nation_id": nation_id,
                        "region_id": capital, "project_kind": "research",
                        "kind": tech.id, "days_remaining": tech.turns * 30.0,
                        "started_turn": turn,
                    },
                    reason_event_id=event_id, visibility_scope=scope,
                ),
                Diff("nation", nation_id, "treasury", DiffOp.ADJUST, -tech.cost_treasury,
                     reason_event_id=event_id, visibility_scope=scope),
            ),
        ),
    )
    if outcome.applied:
        report.started.append(f"{nation['name']}: {tech.name}")
    else:
        # A programme that never began must not be news that it did.
        unrecord_events(conn, [event_id])
        conn.commit()
        report.problems.append(f"the programme could not be started: {outcome.reason}")
    return report


def _deliver_research(
    conn: sqlite3.Connection,
    world_id: str,
    row,
    *,
    turn: int,
    date: str,
    report: ProductionReport,
) -> None:
    """Finish a programme: record what is now known, and apply what it buys.

    The effects are deliberately all things shipped code already reads -- build times the
    yards consult, the base strength every delivered formation is stamped with, the
    industry that sets the size of the queue. A technology that unlocked something nothing
    consults would be a line in a menu.
    """
    tech = technology_by_id(row["kind"], catalogue_for(conn, world_id))
    if tech is None:
        report.problems.append(f"a finished programme named {row['kind']!r} is unknown")
        return

    nation = conn.execute(
        "SELECT name FROM nation WHERE id = ?", (row["nation_id"],)
    ).fetchone()
    name = nation["name"] if nation else row["nation_id"]

    scope = Visibility.SECRET if tech.secret else Visibility.PUBLIC
    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        # DISCOVERY for the same reason as the programme's opening, and this is the end of
        # it that mattered: a completed programme is the moment a rival's capability changes.
        kind=EventKind.DISCOVERY, origin=EventOrigin.ENGINE,
        actor_nation_id=row["nation_id"],
        text=f"{name} completes {tech.name}.",
        visibility=scope,
        visible_to=(row["nation_id"],) if tech.secret else (),
    )

    diffs: list[Diff] = [
        Diff("construction", row["id"], "__delete__", DiffOp.DELETE, None,
             reason_event_id=event_id, visibility_scope=scope),
        # Diffed, not inserted directly, for one reason: a rewind past a completed
        # programme that left the knowledge behind would replay the turn with better ships
        # than the turn actually had.
        Diff(
            "technology", f"{row['nation_id']}|{tech.id}", "__insert__", DiffOp.INSERT,
            {
                "world_id": world_id, "nation_id": row["nation_id"], "tech_id": tech.id,
                "completed_turn": turn, "secret": int(tech.secret),
            },
            reason_event_id=event_id, visibility_scope=scope,
        ),
    ]
    for effect in tech.effects:
        if effect.scope == "arsenal":
            # Minting or adding is decided in one place, because the two callers get it
            # wrong in opposite directions -- see `warheads.arsenal_diffs`.
            from .warheads import arsenal_diffs

            diffs += arsenal_diffs(
                conn, world_id, str(row["nation_id"]),
                warheads=int(effect.amount), event_id=event_id, scope=scope)
        elif effect.scope == "nation":
            # **Charged against what there is**, as `_deliver_project` does. ECON-003 now
            # severs the one diff that leaves 0-100 rather than the batch, so an atomic
            # programme finished at 98.5 legitimacy used to land its military strength,
            # drop its +3.0 legitimacy on the floor, and report the delivery as whole.
            # See :mod:`atlas.engine.bounds`.
            diffs.append(
                Diff("nation", row["nation_id"], effect.field_name, DiffOp.ADJUST,
                     absorbable(conn, "nation", str(row["nation_id"]),
                                effect.field_name, effect.amount),
                     reason_event_id=event_id, visibility_scope=scope)
            )
        elif (effect.scope == "unit_template" and effect.subject
              and effect.field_name == "base_strength"):
            # The country's own: read through `research.strength_bonus`, never written
            # into the template every country shares.
            continue
        elif effect.scope == "unit_template" and effect.subject:
            # The floor matters: a build time driven to zero by two programmes would put a
            # battleship on the map the turn it was ordered, which is the thing the yards
            # exist to prevent.
            current = conn.execute(
                f"SELECT {effect.field_name} AS value FROM unit_template"  # noqa: S608
                "  WHERE world_id = ? AND kind = ?",
                (world_id, effect.subject),
            ).fetchone()
            if current is None:
                # **Never silently.** An effect aimed at a unit kind this era does not
                # field used to vanish here, so a programme could deliver half of what
                # it promised and report success -- which is how 1936's radar quietly
                # became an air-only technology in a world with no battleships. The
                # catalogue is now per era and a test pins every subject against the
                # era's own templates, so reaching this line means the two have drifted
                # apart and somebody needs to know.
                report.problems.append(
                    f"{tech.name} promises {effect.field_name} for {effect.subject!r}, "
                    f"which this world does not build"
                )
                continue
            diffs.append(
                Diff(
                    "unit_template", f"{world_id}|{effect.subject}", effect.field_name,
                    DiffOp.SET, max(1.0, float(current["value"]) + effect.amount),
                    reason_event_id=event_id, visibility_scope=Visibility.PUBLIC,
                )
            )

    outcome = apply_batch(
        conn,
        DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                  proposer_model=None, diffs=tuple(diffs)),
    )
    if outcome.applied:
        report.delivered.append(f"{row['nation_id']}: {tech.name}")
    else:
        # "completes" with nothing learned is the worst kind of false news: a rival's
        # capability changing, told to everyone who can see it, when it did not.
        unrecord_events(conn, [event_id])
        conn.commit()
        report.problems.append(f"a programme could not be completed: {outcome.reason}")


def _deliver_project(
    conn: sqlite3.Connection,
    world_id: str,
    row,
    *,
    turn: int,
    date: str,
    seed: int,
    report: ProductionReport,
) -> None:
    """Finish a public work, and find out whether it worked.

    A risky project is resolved through the world's own seeded stream against the
    nation's legitimacy, so it is exactly as reproducible as a battle -- and the same
    rewind that replays a campaign replays whether the land reform held.
    """
    project = project_by_id(row["kind"])
    if project is None:
        report.problems.append(f"a finished project named {row['kind']!r} is unknown")
        return

    nation = conn.execute(
        "SELECT name, legitimacy FROM nation WHERE id = ?", (row["nation_id"],)
    ).fetchone()
    region = conn.execute(
        "SELECT name FROM region WHERE id = ?", (row["region_id"],)
    ).fetchone()
    name = nation["name"] if nation else row["nation_id"]
    where = region["name"] if region else row["region_id"]

    succeeded = True
    if project.risk_from_legitimacy:
        # The same odds the chooser weighed before it committed the money. See
        # :func:`success_chance` -- one definition, so the decision and the dice cannot
        # drift apart.
        chance = (success_chance(float(nation["legitimacy"])) if nation else 0.5)
        succeeded = uncertain(seed, world_id, turn, f"project:{row['id']}", chance)

    effects = project.effects if succeeded else project.on_failure
    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.CONSTRUCTION, origin=EventOrigin.ENGINE,
        actor_nation_id=row["nation_id"],
        text=(
            f"{name} completes {project.name} in {where}."
            if succeeded
            else f"{name}'s {project.name} in {where} collapses in acrimony."
        ),
        visibility=Visibility.PUBLIC,
    )

    diffs: list[Diff] = [
        Diff("construction", row["id"], "__delete__", DiffOp.DELETE, None,
             reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
    ]
    # **What the government got out of it.** A technocratic administration gets more from
    # the same steelworks than an ideological one -- the money was spent either way, and
    # what differs is how much of it turned into anything. Applied only to what a *gain*
    # is worth: scaling a penalty by the same multiplier would mean a good government
    # suffers more when a scheme fails, which is backwards. See
    # :mod:`atlas.engine.leaders`.
    from .leaders import modifier as leader_modifier

    worth = leader_modifier(conn, str(row["nation_id"]), "development")
    for effect in effects:
        target = row["nation_id"] if effect.scope == "nation" else row["region_id"]
        amount = effect.amount * worth if effect.amount > 0 else effect.amount
        # **Charged against what there is.** A land reform that costs 1.0 stability cannot
        # be completed by a government that has 0.0, and ECON-003 names no diff, so the
        # overshoot took the whole completion with it: the development never landed, the
        # industry never landed, the construction row was never deleted, and the same
        # doomed batch was rebuilt every turn thereafter. Found live in PT39, on Spain,
        # for the last four turns of the run. See :mod:`atlas.engine.bounds`.
        amount = absorbable(conn, effect.scope, str(target), effect.field_name, amount)
        diffs.append(
            Diff(effect.scope, target, effect.field_name, DiffOp.ADJUST, round(amount, 3),
                 reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
        )

    outcome = apply_batch(
        conn,
        DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                  proposer_model=None, diffs=tuple(diffs)),
    )
    if outcome.applied:
        report.delivered.append(
            f"{row['nation_id']}: {project.name}" + ("" if succeeded else " (failed)")
        )
    else:
        # The row is still in the queue and delivers again next turn; the news of it
        # finishing waits until it does.
        unrecord_events(conn, [event_id])
        conn.commit()
        report.problems.append(f"a public work could not be completed: {outcome.reason}")


def _deliver_warhead(
    conn: sqlite3.Connection, world_id: str, row, *, turn: int, date: str,
    report: ProductionReport,
) -> None:
    """One more warhead on the pile.

    Secret, like the programme that made the first one possible. A rival counts an arsenal
    late and imperfectly, which is the whole reason a deterrent is a bluff anybody can call.
    """
    from .warheads import arsenal_diffs

    nation = conn.execute(
        "SELECT name FROM nation WHERE id = ?", (row["nation_id"],)).fetchone()
    name = str(nation["name"]) if nation else str(row["nation_id"])
    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.DISCOVERY, origin=EventOrigin.ENGINE,
        actor_nation_id=str(row["nation_id"]),
        text=f"{name} adds a warhead to its arsenal.",
        visibility=Visibility.SECRET, visible_to=(str(row["nation_id"]),),
    )
    diffs = [
        Diff("construction", row["id"], "__delete__", DiffOp.DELETE, None,
             reason_event_id=event_id, visibility_scope=Visibility.SECRET),
    ]
    from .warheads import TACTICAL_PROJECT

    diffs += arsenal_diffs(
        conn, world_id, str(row["nation_id"]), warheads=1, event_id=event_id,
        scope=Visibility.SECRET, tactical=str(row["kind"]) == TACTICAL_PROJECT)
    outcome = apply_batch(
        conn,
        DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                  proposer_model=None, diffs=tuple(diffs)))
    if outcome.applied:
        report.delivered.append(f"{row['nation_id']}: warhead")
    else:
        unrecord_events(conn, [event_id])
        conn.commit()
        report.problems.append(
            f"a warhead could not be delivered: {outcome.reason}")


def advance_construction(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str, seed: int = 0,
    days: float = 30.0,
) -> ProductionReport:
    """Move every yard on by ``days``, and launch whatever is finished.

    Rule UNIT-001 refuses a formation that appeared with no engine-authored
    ``unit_raised`` event in the same batch, so delivery writes one and every insert cites
    it -- a ship arrives through exactly the door a raised division does.
    """
    report = ProductionReport()
    rows = conn.execute(
        "SELECT id, nation_id, region_id, kind, project_kind, days_remaining"
        "  FROM construction WHERE world_id = ? ORDER BY id",
        (world_id,),
    ).fetchall()
    if not rows:
        return report

    # **What each nation's yards actually did with the month.** Industry below its resting
    # point works to rule: the project does not stop -- an obstructed yard is slow, not shut
    # -- so the days are scaled and everything downstream follows from that one number,
    # including whether a project finishes at all. 1.0 for every nation nobody has angered.
    #
    # **A shortage scales the same days, and did not until now.** `shortfall_of` reached
    # exactly one number in the whole engine -- `industrial_capacity`, the count of
    # concurrent projects -- so a blockade could stop a country *laying down* a battleship
    # and never touched the one already on the slipway. A nation cut off from oil, steel and
    # rare earths finished its programme on schedule and the lever bit a year out, on the
    # order book, and never on anything a player could watch. Two reasons this is the right
    # door rather than a deeper one: it is the same "an obstructed yard is slow, not shut"
    # rule the constituency term already obeys, and it is *continuous*, which the slot count
    # cannot be. `industrial_capacity` is an integer floored at 1, so it can express "this
    # power has one yard" and "this power has none" and nothing in between -- and
    # `_STARVED_CAPACITY` is 0.35 precisely to say that a starved country still builds. A
    # third of a yard is not a number the order book can hold. A third of a month is.
    #
    # **And since 2026-09-18 this is the only place a shortage lands.** The order book
    # stopped reading it -- see :func:`industrial_capacity` for the battery that decided it
    # -- so a totally blockaded yard delivers exactly `_STARVED_CAPACITY` of its month,
    # whatever the size of the power it belongs to.
    from .trade import shortfall_of

    obstruction = {
        nation_id: industrial_throughput(conn, nation_id)
        for nation_id in sorted({str(row["nation_id"]) for row in rows})
    }
    shortage = {
        nation_id: shortfall_of(conn, world_id, nation_id) for nation_id in obstruction
    }
    worked = {
        nation_id: days * obstruction[nation_id] * shortage[nation_id]
        for nation_id in obstruction
    }

    def done(row) -> float:
        return worked.get(str(row["nation_id"]), days)

    # Anything this round finishes is delivered; the rest moves on by the days that
    # passed. The comparison is against the round rather than a fixed month, so a
    # weekly turn advances a yard by a week and a yearly turn -- which arrives here
    # twelve times, once per round -- by a month at a time.
    ticking = [row for row in rows if float(row["days_remaining"]) > done(row)]
    finished = [row for row in rows if float(row["days_remaining"]) <= done(row)]
    # Split by cause, because they have different answers. A country told only that its
    # yards are slow cannot tell whether to talk to its industrialists or break a blockade.
    report.dragging = sorted(
        n for n, factor in obstruction.items() if factor < 1.0 - 0.001)
    report.starved = sorted(
        n for n, factor in shortage.items() if factor < 1.0 - 0.001)

    for row in [r for r in finished if r["project_kind"] == "civil"]:
        _deliver_project(conn, world_id, row, turn=turn, date=date, seed=seed,
                         report=report)
    for row in [r for r in finished if r["project_kind"] == "research"]:
        _deliver_research(conn, world_id, row, turn=turn, date=date, report=report)

    for row in [r for r in finished if r["project_kind"] == "warhead"]:
        _deliver_warhead(conn, world_id, row, turn=turn, date=date, report=report)

    finished = [row for row in finished
                if row["project_kind"] not in {"civil", "research", "warhead"}]
    for nation_id in sorted({row["nation_id"] for row in finished}):
        theirs = [row for row in finished if row["nation_id"] == nation_id]
        name = conn.execute(
            "SELECT name FROM nation WHERE id = ?", (nation_id,)
        ).fetchone()
        event_id = record_event(
            conn, world_id=world_id, turn=turn, date=date,
            kind=EventKind.UNIT_RAISED, origin=EventOrigin.ENGINE, actor_nation_id=nation_id,
            text=(
                f"{name['name'] if name else nation_id} commissions "
                + ", ".join(sorted(f"{row['kind']}" for row in theirs))
                + "."
            ),
            visibility=Visibility.PUBLIC,
        )
        diffs: list[Diff] = []
        for row in theirs:
            strength = conn.execute(
                "SELECT base_strength, COALESCE(domain, 'land') AS domain"
                "  FROM unit_template WHERE world_id = ? AND kind = ?",
                (world_id, row["kind"]),
            ).fetchone()
            diffs.append(
                Diff(
                    "unit", f"u_{row['id']}", "__insert__", DiffOp.INSERT,
                    {
                        "world_id": world_id, "nation_id": nation_id,
                        "region_id": row["region_id"], "kind": row["kind"],
                        "strength": raised_strength(
                            conn, world_id, nation_id,
                            establishment(conn, world_id, nation_id, str(row["kind"]),
                                          float(strength["base_strength"]))
                            if strength else 1.0,
                            str(strength["domain"]) if strength else "land"),
                        "morale": 50.0,
                    },
                    reason_event_id=event_id, visibility_scope=Visibility.PUBLIC,
                )
            )
            diffs.append(
                Diff("construction", row["id"], "__delete__", DiffOp.DELETE, None,
                     reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
            )
        outcome = apply_batch(
            conn,
            DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                      proposer_model=None, diffs=tuple(diffs)),
        )
        if outcome.applied:
            report.delivered.extend(f"{nation_id}: {row['kind']}" for row in theirs)
        else:
            # This nation's "commissions" only: every other nation's delivery is its own
            # batch with its own event, and stands or falls on its own.
            unrecord_events(conn, [event_id])
            conn.commit()
            report.problems.append(f"a delivery failed: {outcome.reason}")

    if ticking:
        event_id = record_event(
            conn, world_id=world_id, turn=turn, date=date,
            kind=EventKind.CONSTRUCTION, origin=EventOrigin.ENGINE,
            text=f"{len(ticking)} project(s) advance in the yards.",
            visibility=Visibility.PUBLIC,
        )
        outcome = apply_batch(
            conn,
            DiffBatch(
                world_id=world_id, turn=turn, proposer_role=Role.ENGINE, proposer_model=None,
                diffs=tuple(
                    Diff("construction", row["id"], "days_remaining", DiffOp.ADJUST,
                         -round(done(row), 4),
                         reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
                    for row in ticking
                ),
            ),
        )
        if not outcome.applied:
            unrecord_events(conn, [event_id])
            conn.commit()
            report.problems.append(f"the yards could not be advanced: {outcome.reason}")
    return report
