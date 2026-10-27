"""Focus Mode: which nations get a dedicated call, and which are simulated in bulk.

Most of the world is resolved by the world simulator in one pass. A small number of
nations get their own agent call each turn, with their own goals and their own reading of
events -- which is what produces rivalry that feels authored rather than averaged.

**The cap is five, and it is enforced in code and in the database.** Not because five is
special, but because the moment Focus Mode matters most is a multi-front war -- and that is
exactly the moment a relevance heuristic would happily promote eight nations and triple the
turn's cost. A ceiling that only exists as a guideline is not a ceiling.

**Ranking is deterministic.** No randomness, no model call. The player can be shown why a
nation is tracked, and the same world always produces the same roster.

**Promotion is not personality creation.** Every nation is given a character and standing
goals at world creation, tracked or not, so promotion means "this nation now gets its own
call" rather than "this nation now invents who it is". Without that, a background power
stepping into the spotlight would acquire a personality it never had, and the player would
watch it change character the moment it became important.

**And the roster is written as diffs, because it is fingerprinted state.** `is_tracked`,
`tracked_since_turn` and `tracked_reason` are columns of `nation`, and
:func:`atlas.state.bootstrap.fingerprint` hashes every column of every fingerprinted table.
Written with direct SQL -- as they were until this was found -- a power promoted on turn 5
stayed promoted through a rewind to turn 4, and the digest moved. That is decision 101's
bug exactly: *a rewind does not replay*, so anything the engine changes has to go through
the diff pipeline or it does not come back. The gravest class this project has, found twice
before in `leader` and `commitment`, and this was the third place.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..state.apply import apply_batch, record_event
from ..state.diff import Diff, DiffBatch
from ..state.models import DiffOp, EventKind, EventOrigin, Role, Visibility
from .decolonisation import restive_dependencies

__all__ = [
    "FOCUS_CAP",
    "MERIT_SEATS",
    "FocusCandidate",
    "choose_roster",
    "apply_focus",
    "rank_candidates",
    "tracked_nations",
    "wars_on_the_frontier",
]

#: Hard ceiling. Also enforced by a database trigger.
FOCUS_CAP = 5


def wars_on_the_frontier(
    conn: sqlite3.Connection, world_id: str
) -> dict[str, int]:
    """``nation id -> contested provinces on its border it is not a party to``.

    The nervous bystander. Every other term in the ranking asks what a nation is doing to
    *you* or to a tracked power; none of them saw a country watching a war arrive at its
    frontier, so the neighbour of an invasion stayed in the referee's bulk pass and never
    got to react in its own voice. That is precisely the nation with the most to say.

    Keyed on the ``contest`` table rather than on ``relation.at_war``, because a war
    fought somewhere else is not a war on your border: what makes a country nervous is
    ground changing hands within sight, and a contest is exactly "somebody is taking this
    province right now". A war declared and not yet prosecuted produces nothing here and
    promotes nobody, which costs one turn of lag and is the honest reading.

    Computed once per ranking rather than per candidate. The per-nation form would be
    240 adjacency joins on a full map, against one here.
    """
    rows = conn.execute(
        """
        SELECT COALESCE(nr.controller_nation_id, nr.owner_nation_id) AS nation_id,
               COUNT(DISTINCT c.id) AS fronts
          FROM contest c
          JOIN region_adjacency a
            ON a.region_a = c.region_id OR a.region_b = c.region_id
          JOIN region nr
            ON nr.id = CASE WHEN a.region_a = c.region_id
                            THEN a.region_b ELSE a.region_a END
         WHERE c.world_id = ?
           AND nr.kind <> 'ocean'
           AND COALESCE(nr.controller_nation_id, nr.owner_nation_id) IS NOT NULL
           -- The belligerents are covered by the war terms above; this is for everybody
           -- else standing next to it.
           AND COALESCE(nr.controller_nation_id, nr.owner_nation_id) <> c.attacker_id
           AND COALESCE(nr.controller_nation_id, nr.owner_nation_id) <> c.defender_id
         GROUP BY 1
        """,
        (world_id,),
    ).fetchall()
    return {str(row["nation_id"]): int(row["fronts"]) for row in rows}


#: What a war on the frontier is worth. Placed above "borders your territory" (25) because
#: a neighbour with a war arriving has more to say than one that merely adjoins you, and
#: well below "at war with you" (100) and "fighting you" (80+) so a bystander can never
#: outrank an actual belligerent -- which is the failure mode this term could introduce.
_FRONTIER_WAR = 30.0
_FRONTIER_WAR_PER_FRONT = 5.0
_FRONTIER_WAR_MAX_BONUS = 10.0


@dataclass(frozen=True, slots=True)
class FocusCandidate:
    nation_id: str
    name: str
    score: float
    #: Why this nation ranks where it does, shown verbatim in the Focus panel.
    reasons: tuple[str, ...]

    @property
    def primary_reason(self) -> str:
        return self.reasons[0] if self.reasons else "background power"


def rank_candidates(
    conn: sqlite3.Connection, world_id: str, *, player_nation: str | None
) -> list[FocusCandidate]:
    """Score every living nation for how much attention it deserves.

    The ordering follows the design: a war involving the player outranks pending
    diplomacy, which outranks sharing a border, which outranks having been mentioned
    recently. Ties break on nation id so the roster is stable rather than arbitrary.
    """
    turn = conn.execute(
        "SELECT turn_number FROM world WHERE id = ?", (world_id,)
    ).fetchone()["turn_number"]

    tracked_now = {row["id"] for row in conn.execute(
        "SELECT id FROM nation WHERE world_id = ? AND is_tracked = 1", (world_id,)
    )}

    candidates: list[FocusCandidate] = []
    rows = conn.execute(
        """
        SELECT id, name, military_strength + economy AS weight
          FROM nation n WHERE world_id = ? AND is_alive = 1 AND is_player = 0
           -- An international body is a nation row with no territory. It would score
           -- close to zero anyway, but "close to zero" is not a design: a body with a
           -- pending commitment would score +40 and take a Focus slot off a power at
           -- war. A body acts through its own channel and never needs a tracked-agent
           -- call.
           AND NOT EXISTS (SELECT 1 FROM organisation o WHERE o.nation_id = n.id)
           -- **A bloc's nation is played by its bloc, and only by its bloc.** In an EVE
           -- match `EveEngine` calls this government with the model its bloc is pinned to;
           -- if Focus also picked it up it would be called a second time in the same round
           -- with the role pin instead, so a nation assigned to the Claude bloc would be
           -- half-played by another family and the comparison the match exists to make
           -- would be measuring a blend.
           --
           -- Costs an ordinary game nothing: outside an EVE match no nation carries a
           -- bloc, so this clause removes no candidate that was there before.
           AND (n.bloc_id IS NULL OR n.bloc_id = '')
        """,
        (world_id,),
    ).fetchall()

    # Relative standing, used to temper the proximity terms. Without it a microstate
    # scores exactly as highly as a great power for the crime of sharing a border, and
    # Andorra gets a dedicated agent while a major neighbour is simulated in bulk. War is
    # deliberately *not* scaled -- being at war with anyone matters regardless of size.
    weights = sorted(float(row["weight"] or 0.0) for row in rows)
    median = weights[len(weights) // 2] if weights else 0.0

    def standing(weight: float) -> float:
        if median <= 0:
            return 1.0
        return min(1.5, max(0.25, weight / median))

    frontier = wars_on_the_frontier(conn, world_id)

    for row in rows:
        nation_id = row["id"]
        scale = standing(float(row["weight"] or 0.0))
        score = 0.0
        reasons: list[str] = []

        if player_nation:
            at_war = conn.execute(
                """
                SELECT 1 FROM relation
                 WHERE ((nation_a = ? AND nation_b = ?) OR (nation_a = ? AND nation_b = ?))
                   AND at_war = 1
                """,
                (nation_id, player_nation, player_nation, nation_id),
            ).fetchone()
            if at_war:
                score += 100.0
                reasons.append("at war with you")

            # A war with ground actually changing hands is not the same thing as a war on
            # paper, and neither is the same as a shared border. Before provinces there
            # was no way to tell those apart -- a war was a flag on a relation -- so a
            # neighbour who had declared and done nothing ranked level with one whose
            # army was three provinces into your territory.
            fighting = conn.execute(
                """
                SELECT COUNT(*) FROM contest c
                  JOIN region r ON r.id = c.region_id
                 WHERE c.world_id = ?
                   AND ((c.attacker_id = ? AND c.defender_id = ?)
                     OR (c.attacker_id = ? AND c.defender_id = ?))
                """,
                (world_id, nation_id, player_nation, player_nation, nation_id),
            ).fetchone()[0]
            if fighting:
                score += 80.0 + min(20.0, fighting * 4.0)
                reasons.insert(0, f"fighting you across {fighting} province(s)")

        war_with_tracked = conn.execute(
            """
            SELECT COUNT(*) FROM relation r
              JOIN nation n ON n.id = r.nation_b
             WHERE r.nation_a = ? AND r.at_war = 1 AND n.is_tracked = 1
            """,
            (nation_id,),
        ).fetchone()[0]
        if war_with_tracked:
            score += 60.0
            # Read by the Focus tab and the cast panel and by nothing else -- these are
            # sentences, not keys. The player's word for a tracked power is the one the
            # rest of the interface uses for it.
            reasons.append(f"at war with {war_with_tracked} power(s) worth watching")

        # A war it is not in, on ground it borders. Scaled by standing like the other
        # proximity terms, so a microstate next to a great-power war does not outrank the
        # great powers fighting it.
        fronts = frontier.get(nation_id, 0)
        if fronts:
            score += (
                _FRONTIER_WAR
                + min(_FRONTIER_WAR_MAX_BONUS, fronts * _FRONTIER_WAR_PER_FRONT)
            ) * scale
            reasons.append(f"a war it is not part of on {fronts} of its borders")

        pending = conn.execute(
            """
            SELECT COUNT(*) FROM commitment c
              JOIN commitment_nation cn ON cn.commitment_id = c.id
             WHERE cn.nation_id = ? AND c.status = 'pending'
            """,
            (nation_id,),
        ).fetchone()[0]
        if pending:
            score += 40.0 + min(10.0, pending * 2.0)
            reasons.append(f"{pending} unresolved agreement(s)")

        if player_nation:
            adjacent = conn.execute(
                """
                SELECT COUNT(*) FROM region_adjacency a
                  JOIN region ra ON ra.id = a.region_a
                  JOIN region rb ON rb.id = a.region_b
                 WHERE (ra.owner_nation_id = ? AND rb.owner_nation_id = ?)
                    OR (rb.owner_nation_id = ? AND ra.owner_nation_id = ?)
                """,
                (nation_id, player_nation, nation_id, player_nation),
            ).fetchone()[0]
            if adjacent:
                score += 25.0 * scale
                reasons.append("borders your territory")

        recent = conn.execute(
            """
            SELECT COUNT(*) FROM event
             WHERE world_id = ? AND actor_nation_id = ? AND turn >= ?
            """,
            (world_id, nation_id, max(0, turn - 3)),
        ).fetchone()[0]
        if recent:
            score += min(20.0, recent * 4.0) * scale
            reasons.append(f"active in {recent} recent event(s)")

        if player_nation:
            relation = conn.execute(
                "SELECT ABS(score) AS s FROM relation WHERE nation_a = ? AND nation_b = ?",
                (player_nation, nation_id),
            ).fetchone()
            intensity = float(relation["s"]) if relation else 0.0
            if intensity >= 40:
                score += 15.0 * scale
                reasons.append("strong feelings toward you")

        # **An empire losing its colonies is worth watching.** Without this the Focus
        # roster and decolonisation could not see each other: PT39 ran 22 turns in which
        # the four empires with stirring colonies were never tracked once -- the United
        # States scored 8.0 against Germany's 68 -- so the one channel that can *grant*
        # independence belonged exclusively to the powers with nothing to grant.
        #
        # The engine now frees a colony that goes past the point of holding it
        # (`atlas.engine.decolonisation`), so this is no longer load-bearing. It is worth
        # keeping anyway, and it is the half that gives the patron *agency*: being tracked
        # while a movement is building is what lets a holder negotiate an exit early
        # instead of having one handed to it.
        #
        # Scaled like the other proximity terms and capped, because an empire with forty
        # restive colonies is not eight times the story of one with five.
        restive = len(restive_dependencies(conn, world_id, nation_id))
        if restive:
            score += min(24.0, 6.0 * restive) * scale
            reasons.append(f"{restive} colony(ies) in open agitation")

        # A small bias toward keeping an existing roster. Without it, two nations a
        # fraction apart swap places every turn and the player watches attention flicker
        # for no narrative reason.
        if nation_id in tracked_now:
            score += 8.0
            reasons.append("already in the foreground")

        candidates.append(
            FocusCandidate(
                nation_id=nation_id,
                name=row["name"],
                score=round(score, 1),
                reasons=tuple(reasons),
            )
        )

    candidates.sort(key=lambda c: (-c.score, c.nation_id))
    return candidates


#: How many of the roster's seats are held on merit alone. The rest rotate.
#:
#: **A ranked list sliced at a cap names the same nations forever.** The scores barely move
#: between turns -- a war is still a war next month -- and the "already tracked" bias adds
#: eight more points to whoever is already there, so the top five entrench. A twenty-four
#: power Modern Day measured across five thirty-turn games gave an agent to eight powers and
#: never once to the other sixteen: not Japan, not Iran, not Israel, not Taiwan, and not
#: seven of the eight powers the roster had just gained. They were seated, described,
#: related and audited, and they never made a decision.
#:
#: This is the same bug the gambit channel had (decision 62), in a second place, and it
#: wants a more careful fix than that one did. Rotating *every* seat would demote a
#: belligerent in the middle of its war, which is worse than the problem: the ranking is
#: not noise, and the nations at the top of it are there because something is happening to
#: them. So the top :data:`MERIT_SEATS` are held by score and the remainder rotate through
#: everyone else who qualifies, which keeps the wars staffed and still lets the spotlight
#: travel.
MERIT_SEATS: int = 3


def choose_roster(
    eligible: list[FocusCandidate], *, cap: int, turn: int
) -> list[FocusCandidate]:
    """The nations to track this turn: the urgent few, plus a rotating share of the rest.

    **Deterministic, and a pure function of the turn number.** The offset is the clock and
    nothing else, so the same world always produces the same roster and a rewind to turn 7
    reproduces the seven-turn roster exactly -- the property `apply_focus` writes diffs to
    preserve, which a wall-clock or a counter would quietly break.

    A world with fewer eligible nations than seats simply seats them all, and the rotation
    is inert until there is somebody to rotate.
    """
    if cap <= 0 or not eligible:
        return []
    if len(eligible) <= cap:
        return list(eligible)

    merit = eligible[:min(MERIT_SEATS, cap)]
    rotating = eligible[len(merit):]
    seats = cap - len(merit)
    if seats <= 0 or not rotating:
        return merit

    # Walk the queue by `seats` a turn so consecutive turns hand the spotlight on rather
    # than re-drawing it, and every eligible nation comes up in turn.
    start = (turn * seats) % len(rotating)
    picked = [rotating[(start + offset) % len(rotating)] for offset in range(min(seats, len(rotating)))]
    return merit + picked


def tracked_nations(conn: sqlite3.Connection, world_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, name, tracked_since_turn, tracked_reason
          FROM nation WHERE world_id = ? AND is_tracked = 1 ORDER BY name
        """,
        (world_id,),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "since_turn": row["tracked_since_turn"],
            "reason": row["tracked_reason"] or "",
        }
        for row in rows
    ]


def apply_focus(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    player_nation: str | None,
    cap: int = FOCUS_CAP,
    turn: int | None = None,
    date: str = "",
) -> tuple[list[str], list[str]]:
    """Recompute the roster. Returns ``(promoted, demoted)`` nation ids.

    Demotions are applied **before** promotions, and they are ordered that way *inside the
    batch* rather than in two batches. The database trigger refuses any promotion that
    would exceed the cap, so promoting first would abort the transaction when the roster is
    already full -- the exact case this function exists to handle. `apply_batch` applies a
    batch's diffs in sequence, so one batch preserves the ordering the trigger requires and
    keeps the whole reshuffle atomic: a roster half-applied is a roster that spends the
    next turn's calls on the wrong powers.

    ``turn`` is the turn the change belongs to. It has to be passed in during a jump,
    because the roster is recomputed *before* the clock advances -- filing the promotion
    under the outgoing turn would put it outside the range a rewind to that turn deletes,
    which is the bug this rewrite exists to fix, reintroduced one line lower down.
    """
    row = conn.execute(
        "SELECT turn_number, world_date FROM world WHERE id = ?", (world_id,)
    ).fetchone()
    turn = int(row["turn_number"]) if turn is None else turn
    date = date or str(row["world_date"] or "")

    ranked = rank_candidates(conn, world_id, player_nation=player_nation)
    # Only nations with an actual reason qualify. Filling the roster to five in a quiet
    # world would spend calls on powers doing nothing.
    eligible = [candidate for candidate in ranked if candidate.score > 0]
    qualified = choose_roster(eligible, cap=cap, turn=turn)
    wanted = {candidate.nation_id for candidate in qualified}

    current = {row["id"] for row in conn.execute(
        "SELECT id FROM nation WHERE world_id = ? AND is_tracked = 1", (world_id,)
    )}

    demoted = sorted(current - wanted)
    promoted = sorted(wanted - current)
    if not demoted and not promoted:
        return [], []

    names = {
        str(r["id"]): str(r["name"]) for r in conn.execute(
            "SELECT id, name FROM nation WHERE world_id = ?", (world_id,))
    }
    told = "; ".join(
        [f"{names.get(n, n)} steps into the foreground" for n in promoted]
        + [f"{names.get(n, n)} recedes into the background" for n in demoted]
    )
    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.NARRATIVE, origin=EventOrigin.ENGINE,
        text=f"The powers worth watching change: {told}."[:600],
        visibility=Visibility.PUBLIC,
    )

    reasons = {candidate.nation_id: candidate.primary_reason for candidate in qualified}
    diffs: list[Diff] = []
    for nation_id in demoted:
        diffs.extend(
            Diff("nation", nation_id, field_name, DiffOp.SET, value,
                 reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
            for field_name, value in (
                ("is_tracked", 0), ("tracked_since_turn", None), ("tracked_reason", None)
            )
        )
    for nation_id in promoted:
        diffs.extend(
            Diff("nation", nation_id, field_name, DiffOp.SET, value,
                 reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
            for field_name, value in (
                ("is_tracked", 1),
                ("tracked_since_turn", turn),
                ("tracked_reason", reasons.get(nation_id, "")),
            )
        )

    outcome = apply_batch(
        conn,
        DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                  proposer_model=None, diffs=tuple(diffs)),
    )
    conn.commit()
    if not outcome.applied:
        # The roster stands as it was. Reporting a promotion that did not happen would
        # send the next turn looking for a briefing nobody was given.
        return [], []
    return promoted, demoted
