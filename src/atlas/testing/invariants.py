"""Things that must never be true of a world, and where they must never be true.

A detector in :mod:`atlas.testing.laboratory` says *this pattern is the shape of a bug we
have had before*. An invariant says something stronger and narrower: *this cannot be true of
a world that is not broken*. They are the same instrument pointed at two kinds of evidence,
so they report through one type and one sweep -- a run whose verdict has to be read in two
places is a run whose verdict gets read once.

**Phase-awareness is the design, not a refinement.** An active pact between two powers at war
is a bug at the end of a turn and the *correct* intermediate state in the middle of one:
`assess_breaches` runs after the fighting resolves, deliberately, so that a war declared this
turn is a breach this turn. Between the declaration and the breach the world holds both facts
at once and is behaving exactly as designed. An invariant that did not know which phase it
was in would fire on that, and a false positive here is worse than a miss for the reason
PT31 established -- three of its four findings were the instrument manufacturing the thing it
exists to detect, in a run where the engine had behaved.

So every invariant declares the phases it holds in, and the checker is told which phase it is
running at. An invariant that cannot state where it holds is an invariant that has not been
thought through.

**Every check is a query.** Nothing here takes a model's word for anything, and nothing here
writes: an instrument that could change the world it measures would eventually be used to.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from .laboratory import Finding

__all__ = [
    "INVARIANTS",
    "Invariant",
    "Phase",
    "check_world",
    "violations",
]


class Phase(StrEnum):
    """When a world is being looked at. Three, because they permit different things."""

    #: A world that has just been created and has taken no turns. A scenario is
    #: authoritative about what it seeds, so some things true of a played world are not
    #: yet true here.
    SEED = "seed"
    #: Inside a turn, between the phases of `jump`. The most permissive: the engine is
    #: partway through making the world consistent again, and most of these do not hold.
    MID_TURN = "mid_turn"
    #: A sealed turn. Everything the engine promises about a world is true here or the
    #: engine is wrong.
    REST = "rest"


_ALWAYS = frozenset({Phase.SEED, Phase.MID_TURN, Phase.REST})
_SETTLED = frozenset({Phase.SEED, Phase.REST})


@dataclass(frozen=True, slots=True)
class Invariant:
    """One thing that must not be true, and where it must not be true."""

    name: str
    #: The phases this holds in. Naming them is what stops the instrument firing on the
    #: engine working.
    holds_in: frozenset[Phase]
    #: Returns one sentence per violation. Empty means the world is fine by this measure.
    check: Callable[[sqlite3.Connection, str], list[str]]
    #: Why this is worth checking at all, in the words a reader needs. Shown with the
    #: finding, because a violation nobody understands is a violation nobody acts on.
    because: str = ""


# --------------------------------------------------------------------------- the checks


def _war_is_symmetric(conn: sqlite3.Connection, world_id: str) -> list[str]:
    """`relation` is two directed rows, and a war is not a thing one side is in.

    Every consumer picks whichever direction is convenient -- `route_is_open` reads
    (seller, buyer), the breach detector reads both -- so a one-sided write makes the engine
    answer differently depending on who is asking.

    **A LEFT join, because the reverse row can be missing rather than merely disagreeing.**
    An inner join only ever compares pairs that both exist, so it caught `at_war = 1` against
    `at_war = 0` and was blind to `at_war = 1` against nothing at all -- which is the worse
    of the two, since a consumer reading the absent direction gets no row and treats the pair
    as at peace. Found by the Codex review of phase 2, and it is the same shape of gap as an
    inner join anywhere: it can only find rows it has.

    Both directions are examined rather than only `a < b`, so a missing row is reported from
    the side that still exists whichever one that is.
    """
    return [
        (f"{row['nation_a']} -> {row['nation_b']} says at_war={row['there']} and there is "
         f"no row for the other direction at all")
        if row["back"] is None else
        (f"{row['nation_a']} -> {row['nation_b']} says at_war={row['there']} and the other "
         f"direction says {row['back']}")
        for row in conn.execute(
            """
            SELECT a.nation_a, a.nation_b, a.at_war AS there, b.at_war AS back
              FROM relation a LEFT JOIN relation b
                ON b.world_id = a.world_id AND b.nation_a = a.nation_b
               AND b.nation_b = a.nation_a
             WHERE a.world_id = ?
               AND (b.at_war IS NULL OR (a.at_war <> b.at_war AND a.nation_a < a.nation_b))
             ORDER BY a.nation_a, a.nation_b
            """,
            (world_id,),
        )
    ]


#: Standing treaties, weakest first -- the same order `_TREATY_FROM_COMMITMENT` resolves in,
#: so "stronger than" means here what it means there. `vassal` is deliberately absent: no
#: commitment kind expresses it, so it is a relationship a scenario asserts rather than one
#: any agreement implies.
_TREATY_STRENGTH = ("none", "trade", "non_aggression", "defensive", "alliance")


def _treaty_status_hides_a_commitment(
    conn: sqlite3.Connection, world_id: str
) -> list[str]:
    """An agreement exists that `relation.treaty_status` does not admit to.

    **This is the half of the reconciliation gap that is unambiguously a bug**, and telling
    it from the other half is the whole reason this is two checks and not one. Decision 120
    made the column a materialised view of the commitments precisely because seven consumers
    read it -- the invasion guard, the gambit odds, the router and four briefings -- so a
    commitment the column is behind is an agreement none of them can see. Measured on the
    shipped scenarios the first time this ran: two pairs in 1936 held an active trade
    agreement while the column said `none`.

    The other direction -- the column claiming a pact no commitment expresses -- is *not*
    checked here. A scenario is authoritative about the world it describes, and Modern Day
    asserts 52 such relationships deliberately. See the module note in the changelog: they
    are worth turning into commitments so they can be *betrayed*, and that is a scenario
    change with balance consequences rather than an invariant violation.
    """
    from ..engine.diplomacy import implied_treaty_status

    out: list[str] = []
    for row in conn.execute(
        "SELECT nation_a, nation_b, treaty_status FROM relation"
        " WHERE world_id = ? ORDER BY nation_a, nation_b",
        (world_id,),
    ):
        a, b = str(row["nation_a"]), str(row["nation_b"])
        held = str(row["treaty_status"])
        if held == "vassal":
            continue
        implied = implied_treaty_status(conn, world_id, a, b)
        if implied == "none" or implied == held:
            continue
        if _TREATY_STRENGTH.index(implied) <= _TREATY_STRENGTH.index(held):
            continue
        out.append(
            f"{a} -> {b} has an active commitment implying {implied!r} and the column "
            f"every consumer reads says {held!r}"
        )
    return out


def _no_standing_pact_between_belligerents(
    conn: sqlite3.Connection, world_id: str
) -> list[str]:
    """**The invariant that must not run mid-turn, and the reason the phase field exists.**

    A pact between two powers at war is what `assess_breaches` exists to find and break, and
    it runs after the fighting resolves. Between a declaration and that pass the world holds
    both facts and is correct. At rest it is not.
    """
    from ..engine.treaties import terms_of

    out: list[str] = []
    for row in conn.execute(
        "SELECT id, kind FROM commitment WHERE world_id = ? AND status = 'active'"
        " ORDER BY id",
        (world_id,),
    ):
        terms = terms_of(conn, int(row["id"]), str(row["kind"]))
        if not any(term.clause == "no_war" for term in terms):
            continue
        parties = [
            str(p["nation_id"]) for p in conn.execute(
                "SELECT nation_id FROM commitment_nation WHERE commitment_id = ?",
                (row["id"],))
        ]
        if len(parties) < 2:
            continue
        marks = ",".join("?" * len(parties))
        if conn.execute(
            "SELECT 1 FROM relation WHERE world_id = ? AND at_war = 1"
            f"   AND nation_a IN ({marks}) AND nation_b IN ({marks}) LIMIT 1",  # noqa: S608
            (world_id, *parties, *parties),
        ).fetchone():
            out.append(
                f"commitment {row['id']} ({row['kind']}) promises not to fight and its "
                f"parties {parties} are at war"
            )
    return out


def _an_active_agreement_has_parties(
    conn: sqlite3.Connection, world_id: str
) -> list[str]:
    """An agreement nobody signed cannot be honoured, broken or reasoned about, and every
    query that reads one gives up halfway rather than saying so."""
    return [
        f"commitment {row['id']} ({row['kind']}) is active with {row['n']} part(y/ies)"
        for row in conn.execute(
            """
            SELECT c.id, c.kind, COUNT(cn.nation_id) AS n
              FROM commitment c
              LEFT JOIN commitment_nation cn ON cn.commitment_id = c.id
             WHERE c.world_id = ? AND c.status = 'active'
             GROUP BY c.id HAVING n < 2 ORDER BY c.id
            """,
            (world_id,),
        )
    ]


def _a_breach_names_a_party(conn: sqlite3.Connection, world_id: str) -> list[str]:
    """Only a signatory can break an agreement. Attribution to anybody else would hand a
    grievance to the wrong nation and charge the regard to a bystander."""
    return [
        f"commitment {row['id']} is recorded as broken by {row['breached_by_nation_id']}, "
        "who is not a party to it"
        for row in conn.execute(
            """
            SELECT c.id, c.breached_by_nation_id FROM commitment c
             WHERE c.world_id = ? AND c.breached_by_nation_id IS NOT NULL
               AND NOT EXISTS (SELECT 1 FROM commitment_nation cn
                                WHERE cn.commitment_id = c.id
                                  AND cn.nation_id = c.breached_by_nation_id)
             ORDER BY c.id
            """,
            (world_id,),
        )
    ]


def _a_broken_agreement_names_its_breaker(
    conn: sqlite3.Connection, world_id: str
) -> list[str]:
    """A treaty recorded as broken by nobody.

    `breach` is what makes tearing up a signature cost something: it writes the
    `treaty_broken` event, charges 35 regard and 6 legitimacy, hands the injured party a
    casus belli, and records `breached_by_nation_id` and `breached_turn`. **Every one of
    those is skipped if the status simply changes.** `commitment.status` is writable by
    three model roles, so a power can retire an agreement it no longer likes by writing one
    column, and the only trace is whatever prose it chose to write beside it.

    PT34e found it on turn 1: India's staff wrote its seeded non-aggression pact with
    Pakistan from `active` to `broken`, narrated it as *"India declares the non-aggression
    pact with Pakistan exhausted and no longer binding"*, and paid nothing at all. Pakistan
    got no grievance. Nothing recorded who had done it.

    `a-breach-names-a-party` does not catch this: it asks whether a *named* breacher is a
    signatory and says nothing about a breach that names nobody. This is the other half.

    Agreements broken before this check existed have no breacher either, and there is no way
    to tell those from the ones that matter -- so, like every other invariant here, it
    reports what it sees and the phase assignment keeps it off a freshly seeded world.
    """
    return [
        f"commitment {row['id']} ({row['kind']}) is broken and nothing records who broke it"
        for row in conn.execute(
            """
            SELECT id, kind FROM commitment
             WHERE world_id = ? AND status = 'broken' AND breached_by_nation_id IS NULL
             ORDER BY id
            """,
            (world_id,),
        )
    ]


def _a_term_belongs_to_an_agreement(
    conn: sqlite3.Connection, world_id: str
) -> list[str]:
    """A clause of nothing. The foreign key makes this impossible through SQL and the diff
    pipeline both, so a violation means something wrote around both -- which is worth
    knowing immediately rather than when the breach detector reads it."""
    return [
        f"commitment_term {row['commitment_id']}|{row['seq']} belongs to no agreement"
        for row in conn.execute(
            """
            SELECT t.commitment_id, t.seq FROM commitment_term t
             WHERE t.world_id = ?
               AND NOT EXISTS (SELECT 1 FROM commitment c WHERE c.id = t.commitment_id)
             ORDER BY t.commitment_id, t.seq
            """,
            (world_id,),
        )
    ]


def _nobody_besieges_their_own_ground(
    conn: sqlite3.Connection, world_id: str
) -> list[str]:
    """An open contest whose attacker already holds the region.

    **It can never end.** `_resolve_one` computes `captured = pressure >= THRESHOLD and
    held != attacker`, so once the attacker holds the ground the capture branch is
    unreachable and the row sits there accumulating pressure for the rest of the game.

    What that costs is not a stale row. The briefing renders it as *"Donets'k -- you hold
    it, you are 55% of the way to taking it"*, which is a sentence a staff acts on: it
    ordered a push into its own province every turn, and the objectives channel refused
    each one with *there is nothing there to push into*. Two parts of the engine describing
    one province in contradictory terms, for nine turns of PT34d, before a detector counted
    the refusals.

    `resolution` already refuses to open one; this is the same claim about the world rather
    than about a proposal, which is what catches a path that writes one some other way.
    """
    return [
        (f"{row['attacker_id']} holds {row['name']} and is besieging it "
         f"({float(row['pressure']):.0%}); that contest can never end")
        for row in conn.execute(
            """
            SELECT c.attacker_id, c.pressure, r.name
              FROM contest c JOIN region r ON r.id = c.region_id
             WHERE c.world_id = ?
               AND c.attacker_id = COALESCE(r.controller_nation_id, r.owner_nation_id)
             ORDER BY c.id
            """,
            (world_id,),
        )
    ]


def _a_route_has_an_agreement_behind_it(
    conn: sqlite3.Connection, world_id: str
) -> list[str]:
    """A route opened by a treaty, still running after the treaty stopped standing.

    `honour_terms` is idempotent and derived: every turn it asks whether the world matches
    the standing agreements and writes the difference, which is what lets it repair a turn
    whose batch was refused. **The failure it cannot repair is its own batch being refused
    on the closing side** -- the agreement lapses, the close is thrown out, and because the
    route now exists without an active term behind it nothing else will ever remove it. The
    seller goes on shipping under a treaty that no longer exists.

    Route ids are derived from the term that opened them (`tr_c<commitment>_<seq>`), so the
    question is answerable from the id alone. Seeded routes carry a scenario's own ids and
    are none of this check's business: a scenario is authoritative about the trade it
    describes, exactly as it is about `relation.treaty_status`.
    """
    return [
        (f"trade route {row['id']} was opened by commitment {row['commitment_id']}, "
         f"which is {row['status'] or 'gone'}")
        for row in conn.execute(
            """
            SELECT t.id,
                   CAST(SUBSTR(t.id, 5, INSTR(SUBSTR(t.id, 5), '_') - 1) AS INTEGER)
                       AS commitment_id,
                   (SELECT c.status FROM commitment c
                     WHERE c.id = CAST(SUBSTR(t.id, 5,
                                              INSTR(SUBSTR(t.id, 5), '_') - 1) AS INTEGER))
                       AS status
              FROM trade_route t
             WHERE t.world_id = ? AND t.id LIKE 'tr_c%'
             ORDER BY t.id
            """,
            (world_id,),
        )
        if (row["status"] or "") != "active"
    ]


def _morale_is_a_percentage(conn: sqlite3.Connection, world_id: str) -> list[str]:
    """Morale runs 0 to 100, and `resolve` multiplies strength by ``morale / 50``.

    **A unit below zero therefore subtracts from the strength of its own army**, which is
    not a degraded formation but an actively helpful one -- for the other side. The nuclear
    `incinerate` effect could do it, taking a garrison at 10 to -15, and that clamp is
    fixed at the call site. This is the same claim about the *world* rather than about one
    effect: any path that ever writes morale outside the range is caught here, including
    one nobody has written yet.

    Above 100 has no caller today -- `orders` fortifies to a ceiling of 95 -- and is
    checked anyway, because the column has no CHECK behind it and the cost of asking is a
    single scan.

    ``_ALWAYS``: there is no phase of a turn in which a unit legitimately has impossible
    morale. Every writer goes through a diff, and a diff applies a value that was already
    clamped, so a violation here is a bug at whichever call site produced it rather than
    the engine being partway through making the world consistent.
    """
    return [
        f"{row['id']} has morale {row['morale']}, which is outside 0-100"
        + (" -- it subtracts from the strength of its own army" if row["morale"] < 0 else "")
        for row in conn.execute(
            """
            SELECT id, morale FROM unit
             WHERE world_id = ? AND (morale < 0 OR morale > 100)
             ORDER BY id
            """,
            (world_id,),
        )
    ]


def _armies_stand_on_land(conn: sqlite3.Connection, world_id: str) -> list[str]:
    """Rule UNIT-002 refuses a *proposal* that would put an army at sea. This is the same
    claim about the world rather than about a batch, which is what catches an engine path
    that never went through a proposal at all."""
    return [
        f"{row['id']} ({row['kind']}, {row['domain']}) is standing in {row['region_id']}, "
        "which is ocean"
        for row in conn.execute(
            """
            SELECT u.id, u.kind, u.region_id, t.domain
              FROM unit u
              JOIN region r ON r.id = u.region_id
              LEFT JOIN unit_template t ON t.world_id = u.world_id AND t.kind = u.kind
             WHERE u.world_id = ? AND r.kind = 'ocean'
               AND COALESCE(t.domain, 'land') = 'land'
             ORDER BY u.id
            """,
            (world_id,),
        )
    ]


def _the_dead_hold_nothing(conn: sqlite3.Connection, world_id: str) -> list[str]:
    """A nation that is not alive still owning ground is how a conquered power keeps
    collecting revenue from provinces somebody else took."""
    return [
        f"{row['id']} is owned by {row['owner_nation_id']}, who is not alive"
        for row in conn.execute(
            """
            SELECT r.id, r.owner_nation_id FROM region r
              JOIN nation n ON n.id = r.owner_nation_id
             WHERE r.world_id = ? AND n.is_alive = 0 ORDER BY r.id
            """,
            (world_id,),
        )
    ]


def _a_route_has_two_living_ends(conn: sqlite3.Connection, world_id: str) -> list[str]:
    """A cargo from nobody to nobody. `assess_trade` walks every route every turn, so a
    dangling one is work done on behalf of a country that no longer exists."""
    return [
        f"trade route {row['id']} runs {row['seller_id']} -> {row['buyer_id']} and one of "
        "them is not a living nation"
        for row in conn.execute(
            """
            SELECT t.id, t.seller_id, t.buyer_id FROM trade_route t
             WHERE t.world_id = ?
               AND (NOT EXISTS (SELECT 1 FROM nation n WHERE n.id = t.seller_id
                                  AND n.is_alive = 1)
                 OR NOT EXISTS (SELECT 1 FROM nation n WHERE n.id = t.buyer_id
                                  AND n.is_alive = 1))
             ORDER BY t.id
            """,
            (world_id,),
        )
    ]


def _one_government_at_a_time(conn: sqlite3.Connection, world_id: str) -> list[str]:
    """Two current leaders is a coup that half happened. A unique index makes it
    impossible; this is here because the index only covers worlds created after schema 13
    and a migrated one has no such guarantee."""
    return [
        f"{row['nation_id']} has {row['n']} current leaders"
        for row in conn.execute(
            """
            SELECT nation_id, COUNT(*) AS n FROM leader
             WHERE world_id = ? AND is_current = 1
             GROUP BY nation_id HAVING n > 1 ORDER BY nation_id
            """,
            (world_id,),
        )
    ]


#: Every invariant, and where it holds. **The `holds_in` column is the product**, as much as
#: the checks: each one is a claim about which phases of the engine's own work the property
#: survives, and getting that wrong is how an instrument starts reporting the engine working
#: as the engine failing.
INVARIANTS: tuple[Invariant, ...] = (
    Invariant(
        "war-is-symmetric", _ALWAYS, _war_is_symmetric,
        because="a war is not a thing one side is in",
    ),
    Invariant(
        "treaty-status-hides-a-commitment", _SETTLED, _treaty_status_hides_a_commitment,
        because="the column is a materialised view of the commitments, and seven "
                "consumers read the column rather than the commitments",
    ),
    Invariant(
        # **Not at seed**: a scenario may seed a war and the paperwork around it in the
        # same breath, and the first turn's breach pass is what resolves them. Not
        # mid-turn either, for the reason in the function's own docstring.
        "no-standing-pact-between-belligerents", frozenset({Phase.REST}),
        _no_standing_pact_between_belligerents,
        because="assess_breaches runs after the fighting, so by the end of a turn this "
                "has been dealt with",
    ),
    Invariant(
        "an-active-agreement-has-parties", _ALWAYS, _an_active_agreement_has_parties,
        because="an agreement nobody signed cannot be honoured or broken",
    ),
    Invariant(
        "a-breach-names-a-party", _ALWAYS, _a_breach_names_a_party,
        because="only a signatory can break an agreement",
    ),
    Invariant(
        "a-broken-agreement-names-its-breaker", _SETTLED,
        _a_broken_agreement_names_its_breaker,
        because=(
            "a treaty that changed status without going through `breach` cost nobody "
            "anything: no charge, no grievance, and no record of who tore it up"
        ),
    ),
    Invariant(
        "a-term-belongs-to-an-agreement", _ALWAYS, _a_term_belongs_to_an_agreement,
        because="a clause of nothing is read by the breach detector every turn",
    ),
    Invariant(
        "nobody-besieges-their-own-ground", _SETTLED, _nobody_besieges_their_own_ground,
        because=(
            "a contest whose attacker already holds the region can never resolve, and the "
            "briefing renders it as being part-way to taking ground you are standing on"
        ),
    ),
    Invariant(
        "a-route-has-an-agreement-behind-it", _SETTLED,
        _a_route_has_an_agreement_behind_it,
        because=(
            "a route opened by a treaty and left running after it lapsed ships cargo under "
            "an agreement that no longer exists, and nothing else will ever close it"
        ),
    ),
    Invariant(
        "morale-is-a-percentage", _ALWAYS, _morale_is_a_percentage,
        because=(
            "resolve multiplies strength by morale/50, so a unit below zero subtracts "
            "from the strength of its own army -- a warhead on a shaken garrison made "
            "that garrison's own side weaker"
        ),
    ),
    Invariant(
        "armies-stand-on-land", _ALWAYS, _armies_stand_on_land,
        because="UNIT-002 refuses the proposal; this is the same claim about the world",
    ),
    Invariant(
        "the-dead-hold-nothing", _SETTLED, _the_dead_hold_nothing,
        because="a conquered power still owning ground still collects from it",
    ),
    Invariant(
        "a-route-has-two-living-ends", _SETTLED, _a_route_has_two_living_ends,
        because="assess_trade walks every route every turn",
    ),
    Invariant(
        "one-government-at-a-time", _ALWAYS, _one_government_at_a_time,
        because="two current leaders is a coup that half happened",
    ),
)


def violations(
    conn: sqlite3.Connection, world_id: str, phase: Phase = Phase.REST
) -> list[str]:
    """Every invariant that does not hold, at this phase. Empty when the world is sound.

    An invariant that raises is reported rather than allowed to end the sweep -- a broken
    check must not hide the nine that work, which is the same rule `suspicious()` follows
    and for the same reason.
    """
    out: list[str] = []
    for invariant in INVARIANTS:
        if phase not in invariant.holds_in:
            continue
        try:
            broken = invariant.check(conn, world_id)
        except (sqlite3.Error, KeyError, ValueError, TypeError) as exc:
            out.append(
                f"[{invariant.name}] could not run: {type(exc).__name__}: {exc}"
            )
            continue
        out.extend(f"[{invariant.name}] {line}" for line in broken)
    return out


def check_world(
    conn: sqlite3.Connection, world_id: str, *, phase: Phase = Phase.REST, turn: int = 0
) -> list[Finding]:
    """The same sweep, as laboratory findings, so a violation reads like every other one."""
    return [
        Finding("invariant-violated", turn, line)
        for line in violations(conn, world_id, phase)
    ]
