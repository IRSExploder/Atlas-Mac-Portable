"""Leading the international order, as something a power can actually earn.

**The victory nobody could ever win.** `scoring` has always offered `concert` -- "leads the
world by standing rather than by force" -- as the route a power that never wins a battle is
meant to take. It has never once fired, and measuring said why: standing is legitimacy, the
regard of other powers, and a seat, and **not one of the three is a decision a government can
take.** `nation.legitimacy` is engine-only, seats are not writable, and regard is
``AVG(score) WHERE nation_b = me`` -- what *others* think -- while a power may only write the
relation it owns. A power courting everybody for forty turns moved its standing from 0.573
to 0.569.

That principle is correct and this module does not touch it. **Regard is still never
declared.** What is added is that the engine now notices behaviour that deserves it.

Three measurements shaped everything here.

**Regard is averaged over 203 nations**, so a bilateral reward is diluted to nothing --
`answering.REGARD_FOR_ANSWERING` is twelve points from one country and moves standing by six
thousandths. Anything that works has to move the *world's* view at once, which is why the
award below writes a nudge into every relation rather than a large one into a few.

**Legitimacy converges on stability** -- `budget` drifts it by ``(stability - legitimacy) *
rate`` -- so it cannot be granted directly and there is no point trying. The credit here is
paid in stability and legitimacy follows it over years, which is also the honest shape: a
government admired abroad is a government with an easier time at home.

**And the award has to be rivalrous.** The condition is comparative -- a multiple of the
runner-up -- so a mechanic that rewarded every well-behaved power would lift the whole field
and move nobody's ratio. Only the leaders are paid. A power can do everything right and earn
nothing because two others did more, which is uncomfortable and is the point.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..state.apply import apply_batch
from ..state.diff import Diff, DiffBatch
from ..state.models import DiffOp, Role, Visibility
from .bounds import absorbable
from .pacing import compounded, days_between, span_of

__all__ = [
    "LEADERS_REWARDED",
    "EDGE_FLOOR",
    "REGARD_CEILING",
    "ConcertReport",
    "award_leadership",
    "leadership_index",
    "leadership_parts",
]

#: How many powers are paid each turn. **Three, because the condition is a multiple of the
#: runner-up**: pay one and the leader runs away unopposed, pay ten and the whole field
#: rises together and the ratio never moves. Three leaves a contest at the top that somebody
#: has to actually win.
LEADERS_REWARDED = 3

#: Where the world's regard for a sustained leader settles, and the rate it closes at per
#: reference month.
#:
#: **Closing on a ceiling rather than adding for ever**, which is the shape `wareffort` and
#: `budget` both argue for at length: a quantity that only adds reaches the top of its scale
#: given enough months and stops being a measure. It also means leadership cannot be banked
#: -- stop leading and the drift stops, and the ordinary regard mechanics pull back.
#:
#: 45 rather than 100: the world warming to a power is not the world loving it, and an
#: average regard of 45 across two hundred nations is already a position no power on the
#: shipped map is remotely near. Measured, it carries the standing component from 0.177 to
#: about 0.25.
REGARD_CEILING = 45.0
REGARD_RATE = 0.035

#: What leading is worth at home, per reference month, in stability.
#:
#: **This is the lever that actually decides the route, and the first draft had it ten times
#: too small.** Legitimacy is 45% of standing against regard's 35%, and legitimacy converges
#: on stability -- so stability is where most of the movement has to come from. Worked
#: through: warming the whole world's regard from 3.6 to 32 is worth about +0.05 of standing,
#: and the condition needs about +0.16 over a runner-up who is not moving. Regard cannot
#: reach that even at its ceiling; carrying a leader's stability from the forties toward
#: `STABILITY_CEILING` can.
#:
#: It reads right, too: a government the world defers to is a government with an easier time
#: at home. Bounded by the ceiling below, paid only to a power standing clear of the field,
#: and it stops the moment that stops being true.
STABILITY_CREDIT = 0.75

#: The most stability this can carry a power to on its own. Above it the credit stops: a
#: contented country is a fact about its government and its economy, and admiration abroad
#: should not paper over a country coming apart.
STABILITY_CEILING = 82.0

#: How far back the restraint window looks, in days of world time. Two years -- long enough
#: that one old war does not disqualify a power for ever, short enough that a recent one
#: does.
RESTRAINT_DAYS = 730.0

#: How far above the field's median a power must stand before the world notices at all.
#:
#: **The guard against a clustered field.** Every power on the shipped map sits within a few
#: hundredths of every other on this index at the start, and paying the top of a dead heat
#: lifts the leader and the runner-up together -- which moves nobody's *ratio*, and the ratio
#: is the whole condition. Below this the award is nothing, so leading has to be visible
#: before it pays for anything.
#:
#: Measured against the shipped map: by turn forty a power that has kept four agreements the
#: whole game, aided somebody losing and started no wars stands at 0.85 against a runner-up
#: on 0.66. An edge of 0.19 is what genuinely leading looks like here, so a floor of 0.12
#: admits that and refuses the 0.02 gaps the field starts with.
EDGE_FLOOR = 0.12

#: Below this a nudge is not worth a diff, on the argument every other per-turn drift makes.
_NOTICEABLE = 0.005


@dataclass
class ConcertReport:
    """Who led this turn, and what it bought them."""

    led: list[str] = field(default_factory=list)
    #: How many relations were warmed. Reported because it is the expensive part.
    warmed: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def happened(self) -> bool:
        return bool(self.led)


@dataclass(frozen=True, slots=True)
class Parts:
    """What a power's leadership is made of, so a briefing can say rather than score."""

    kept: float = 0.0
    aid: float = 0.0
    restraint: float = 0.0
    seats: float = 0.0
    #: Wars this power ended that were not its own. See :data:`MEDIATION_WEIGHT`.
    mediation: float = 0.0

    @property
    def total(self) -> float:
        # Weighted so that *keeping promises over years* is the largest part. Aid and a
        # seat are things a power can arrange in a season; a record of never having broken
        # anything is the one that takes a game to build and cannot be bought.
        #
        # **Mediation is added on top rather than folded into the four, and that is
        # deliberate.** Renormalising to make room would drop every power that has never
        # brokered anything -- which is nearly all of them -- and the index is consumed as
        # an *edge over the runner-up*, so lowering the whole field lowers the edge too.
        # The measured concert win clears `EDGE_FLOOR` by 0.07; a reweighting could have
        # closed that without a single line of it looking wrong. Clamped, so the index
        # stays the 0..1 quantity everything downstream assumes.
        return min(1.0, self.kept * 0.40 + self.aid * 0.20
                   + self.restraint * 0.25 + self.seats * 0.15
                   + self.mediation * MEDIATION_WEIGHT)


#: How many commitment-turns of unbroken record counts as a full mark. Four agreements kept
#: for fifty turns, or ten kept for twenty.
#:
#: **Counted as turns rather than as agreements, and that is the whole of what makes this
#: route take a game.** Measured with a plain count, every power sat within a hundredth of
#: every other -- promises held are nearly static, so the index was a constant and the same
#: three powers led from turn one to turn sixty regardless of what anybody did. A record is
#: a thing that accumulates; counting it any other way makes leadership an accident of what
#: the scenario seeded.
_KEPT_FULL = 200.0

#: What brokering other people's wars is worth on top of the four parts, and how many it
#: takes to earn all of it.
#:
#: **The one part of leadership a power earns by doing something rather than by not.** Kept
#: promises accumulate for anybody who signs anything and never breaks it; restraint is
#: earned by staying home; a seat is mostly inherited from the scenario. Ending a war you
#: are not in is an act, it is hard -- both sides have to sign, which no bilateral peace
#: requires -- and before this there was no way for it to count.
#:
#: 0.20 sits between `aid` and `restraint` in weight: worth more than backing somebody
#: losing, less than a whole game of keeping your word. Three, because the gate and the
#: cooldown together mean a power that brokers three wars in a game has spent most of it
#: being the one everybody calls.
MEDIATION_WEIGHT = 0.20
_MEDIATIONS_FULL = 3.0

#: What a recent peacemaker is paid, as a share of what a ranked leader is paid.
#:
#: **Because feeding the index turned out not to be enough, and the trace said exactly
#: where.** Two mediations carried a plausible broker's index from 0.33 to 0.68 and its rank
#: from 11th to 8th, and it entered `led_the_order` on none of a hundred and ten turns: the
#: award pays the top three, the United States sat at 0.93 to 1.00, and the largest credit
#: brokering can earn is 0.20. The gap was not closeable in principle. Meanwhile the two
#: belligerents' gratitude -- six points on two relations of two hundred and three -- moves
#: the regard average by 0.06, which is 0.0001 of standing.
#:
#: So the eligibility gate and the reward were selecting opposite populations. A power must
#: stand clear of the field to be heard at somebody else's war, and must not be a belligerent
#: in it -- which picks regional powers at ranks four to ten. To be paid it had to be top
#: three on an index a superpower dominates. The nine powers that could broker were precisely
#: the nine that could never be paid for it.
#:
#: The fix is not more leaders. Paying the top four or five lifts the top of the field
#: together and moves nobody's *ratio*, which is the mistake this module's own second draft
#: made and which `EDGE_FLOOR` exists to refuse. It is to pay for the *act*: brokering a
#: peace is a discrete, verifiable, rare thing that most of the field never does, so paying
#: for it separates the doer from everybody else instead of raising them together.
#:
#: A third, because one act is not a record. Scaled again by how many wars this power has
#: actually ended lately, so a single brokered peace earns a third of a third and only a
#: standing peacemaker earns the whole share.
MEDIATION_SHARE = 0.34


def _commitments(
    conn: sqlite3.Connection, world_id: str, nation_id: str, *, turn: int
) -> float:
    """The years of unbroken agreement behind this power, against what it has broken.

    Read from `commitment.breached_by_nation_id` rather than from event prose, so a power's
    record is a fact in the table rather than a string somebody might reword.
    """
    rows = conn.execute(
        """
        SELECT c.status, c.created_turn, c.breached_by_nation_id AS broke
          FROM commitment c
          JOIN commitment_nation cn ON cn.commitment_id = c.id
         WHERE c.world_id = ? AND cn.nation_id = ?
        """,
        (world_id, nation_id)).fetchall()
    held = 0.0
    broken = 0
    for row in rows:
        if str(row["broke"] or "") == nation_id:
            broken += 1
            continue
        if str(row["status"]) == "active":
            held += max(0.0, float(turn - int(row["created_turn"] or 0)))
    # A breach is worth more than a promise kept, because it is the rarer signal and the one
    # the world remembers. Charged against the record in years, so breaking one agreement
    # costs a power a great deal of the time it spent keeping the others.
    return max(0.0, min(1.0, (held - broken * 40.0) / _KEPT_FULL))


def _aid(conn: sqlite3.Connection, world_id: str, nation_id: str) -> float:
    """Backing given to a partner whose own country is being held by somebody else.

    **Only to a client actually in trouble.** Arming a comfortable friend is ordinary
    statecraft; standing behind one that is losing is the thing the world notices, and it is
    the behaviour this route exists to reward.
    """
    try:
        rows = conn.execute(
            """
            SELECT s.client_id, SUM(s.level) AS level FROM support s
             WHERE s.world_id = ? AND s.patron_id = ?
             GROUP BY s.client_id
            """, (world_id, nation_id)).fetchall()
    except sqlite3.OperationalError:
        return 0.0
    worth = 0.0
    for row in rows:
        occupied = conn.execute(
            "SELECT COUNT(*) c FROM region WHERE world_id = ? AND owner_nation_id = ?"
            "   AND kind <> 'ocean' AND controller_nation_id IS NOT NULL"
            "   AND controller_nation_id <> owner_nation_id",
            (world_id, str(row["client_id"]))).fetchone()
        if int(occupied["c"] or 0) > 0:
            worth += min(1.0, float(row["level"] or 0.0) / 6.0)
    return max(0.0, min(1.0, worth))


def _restraint(
    conn: sqlite3.Connection, world_id: str, nation_id: str, *, date: str
) -> float:
    """Whether this power has kept its hands clean lately.

    Wars it started and covert operations of its own that came out, inside the window. Both
    are already recorded and both already cost regard directly -- this is the *second* thing
    they cost, which is the leadership the power is not accumulating while it does them.
    """
    from .pacing import days_between

    recent = 0
    for row in conn.execute(
        "SELECT date FROM event WHERE world_id = ? AND kind = 'war_declared'"
        "   AND actor_nation_id = ?", (world_id, nation_id)):
        gap = days_between(str(row["date"]), date) if date and row["date"] else None
        if gap is not None and gap <= RESTRAINT_DAYS:
            recent += 1
    try:
        blown = conn.execute(
            "SELECT COUNT(*) c FROM exposure WHERE world_id = ? AND actor_nation_id = ?",
            (world_id, nation_id)).fetchone()
        recent += int(blown["c"] or 0)
    except sqlite3.OperationalError:
        pass
    return 1.0 if recent == 0 else max(0.0, 1.0 - recent * 0.5)


def _seats(conn: sqlite3.Connection, nation_id: str) -> float:
    row = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(is_permanent), 0) AS perm"
        "  FROM organisation_member WHERE nation_id = ?", (nation_id,)).fetchone()
    if row is None:
        return 0.0
    return max(0.0, min(1.0, float(row["n"] or 0) * 0.5 + float(row["perm"] or 0) * 0.5))


def _recent_mediations(
    conn: sqlite3.Connection, world_id: str, nation_id: str, *, date: str
) -> int:
    """Wars this power ended that were not its own, inside the restraint window.

    Recent rather than ever, so one old peace does not pay a country for the rest of the
    game -- the same window and the same argument as `_restraint`.
    """
    if not date:
        return 0
    rows = conn.execute(
        "SELECT e.date AS date FROM event e"
        "  JOIN event_participant m ON m.event_id = e.id AND m.role = 'mediator'"
        " WHERE e.world_id = ? AND m.nation_id = ? AND e.kind = 'peace_signed'",
        (world_id, nation_id)).fetchall()
    return sum(1 for row in rows
               if 0.0 <= days_between(str(row["date"]), date) <= RESTRAINT_DAYS)


def _mediation(conn: sqlite3.Connection, world_id: str, nation_id: str) -> float:
    """How much of a broker this power has been, 0 to 1. Counted from the log."""
    from .mediation import mediations_by

    return min(1.0, mediations_by(conn, world_id, nation_id) / _MEDIATIONS_FULL)


def leadership_parts(
    conn: sqlite3.Connection, world_id: str, nation_id: str, *, date: str = "",
    turn: int = 0,
) -> Parts:
    """What this power's claim to lead is made of. Read-only, and draws no RNG."""
    return Parts(
        kept=_commitments(conn, world_id, nation_id, turn=turn),
        aid=_aid(conn, world_id, nation_id),
        restraint=_restraint(conn, world_id, nation_id, date=date),
        seats=_seats(conn, nation_id),
        mediation=_mediation(conn, world_id, nation_id),
    )


def leadership_index(
    conn: sqlite3.Connection, world_id: str, nation_id: str, *, date: str = "",
    turn: int = 0,
) -> float:
    """One number for how much this power is leading, 0 to 1."""
    return leadership_parts(conn, world_id, nation_id, date=date, turn=turn).total


def award_leadership(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str, days: float = 30.0
) -> ConcertReport:
    """Warm the world toward this turn's leaders, and settle their countries a little.

    **Every relation, not a chosen few.** Standing is an average over two hundred and three
    views, so a nudge has to reach all of them to be worth anything -- and it is a *nudge*,
    closing a fraction of the distance to a ceiling, so it converges and cannot be banked.

    Nations already at war with a leader are left out. Admiration for the power currently
    invading you is not a thing this mechanic should be able to manufacture, and it is the
    one case where a broad warming would read as obviously wrong.
    """
    report = ConcertReport()
    span = span_of(days)
    if span <= 0.0:
        return report

    ranked = []
    for row in conn.execute(
        "SELECT id FROM nation WHERE world_id = ? AND is_alive = 1 ORDER BY id",
        (world_id,)):
        nation = str(row["id"])
        if conn.execute(
            "SELECT 1 FROM organisation WHERE nation_id = ?", (nation,)).fetchone():
            continue
        ranked.append(
            (leadership_index(conn, world_id, nation, date=date, turn=turn), nation))
    if not ranked:
        return report
    # Sorted by index then by id, so a tie is broken the same way twice and a rewind lands
    # on the world that was played.
    ranked.sort(key=lambda entry: (-entry[0], entry[1]))

    # **Paid for standing clear of the next power, and of nobody else.**
    #
    # Two versions of this were wrong before the measurement settled it. Paying the top three
    # by raw index rose all three together, so the leader never separated and the ratio never
    # left 1.00 -- and the condition is a *multiple of the runner-up*, so together is the one
    # direction that cannot win. Measuring the edge over the field's *median* was worse in a
    # way that looked better: on a two-hundred-nation map the median power has no commitments
    # and no seat, so every great power cleared it by a mile and all three were paid in full
    # again.
    #
    # The gap that matters is the one the condition asks about. A power is paid for being
    # ahead of whoever is second, which usually means only one power is paid at all -- and
    # when the top of the field is close-run, nobody is, because a dead heat has no leader in
    # it. This does not run away: the award is regard and stability, and neither feeds back
    # into the index, which is built from commitments, aid and restraint.
    #: What share of the full award each power has earned, 0..1, by whichever route.
    paid: dict[str, float] = {}
    for place, (score, nation) in enumerate(ranked[:LEADERS_REWARDED]):
        rival = max((other for i, (other, _n) in enumerate(ranked) if i != place),
                    default=0.0)
        edge = score - rival
        if edge >= EDGE_FLOOR:
            # Scaled by the edge rather than the index, so a power half a point clear of
            # the world is warmed toward far faster than one a hundredth clear of its
            # nearest rival.
            paid[nation] = min(1.0, edge / 0.35)

    # **And the second way in, which is an act rather than a rank.** See `MEDIATION_SHARE`:
    # a power that has lately ended somebody else's war is paid whether or not it is near
    # the top, because that is a thing it *did* and the ranked award could never reach it.
    # A power qualifying both ways keeps the larger of the two rather than collecting both.
    for row in conn.execute(
        "SELECT DISTINCT m.nation_id AS who FROM event e"
        "  JOIN event_participant m ON m.event_id = e.id AND m.role = 'mediator'"
        " WHERE e.world_id = ? AND e.kind = 'peace_signed' ORDER BY m.nation_id",
        (world_id,)):
        broker = str(row["who"])
        recent = _recent_mediations(conn, world_id, broker, date=date)
        if not recent:
            continue
        share = MEDIATION_SHARE * min(1.0, recent / _MEDIATIONS_FULL)
        if share > paid.get(broker, 0.0):
            paid[broker] = share

    if not paid:
        return report
    # Sorted, so the batch is written in the same order twice and a rewind lands on the
    # world that was played.
    leaders = sorted(((share, nation) for nation, share in paid.items()),
                     key=lambda entry: entry[1])

    event = conn.execute(
        "SELECT id FROM event WHERE world_id = ? AND kind = 'genesis' ORDER BY id LIMIT 1",
        (world_id,)).fetchone()
    if event is None:
        return report
    event_id = int(event["id"])

    diffs: list[Diff] = []
    for share, nation in leaders:
        closing = compounded(REGARD_RATE * share, span)
        for other in conn.execute(
            "SELECT nation_a, score FROM relation"
            " WHERE world_id = ? AND nation_b = ? AND at_war = 0",
            (world_id, nation)):
            now = float(other["score"])
            if now >= REGARD_CEILING:
                continue
            step = round((REGARD_CEILING - now) * closing, 3)
            if step < _NOTICEABLE:
                continue
            diffs.append(
                Diff("relation", f"{other['nation_a']}|{nation}", "score", DiffOp.ADJUST,
                     step, reason_event_id=event_id,
                     visibility_scope=Visibility.PUBLIC))
        settled = conn.execute(
            "SELECT stability FROM nation WHERE world_id = ? AND id = ?",
            (world_id, nation)).fetchone()
        if settled is not None and float(settled["stability"]) < STABILITY_CEILING:
            credit = round(
                min(STABILITY_CREDIT * share * span,
                    STABILITY_CEILING - float(settled["stability"])), 3)
            if credit >= _NOTICEABLE:
                # **Through the clamp, like every other bounded charge.** A diff that
                # overshoots `stability`'s range is severed while the batch around it is
                # accepted, so the credit would vanish and the leadership it was paying for
                # would stand -- which is the failure `bounds.absorbable` exists to stop.
                # `STABILITY_CEILING` already bounds this from above; the clamp is what
                # guarantees it against the column rather than against my own arithmetic.
                diffs.append(
                    Diff("nation", nation, "stability", DiffOp.ADJUST,
                         absorbable(conn, "nation", nation, "stability", credit),
                         reason_event_id=event_id,
                         visibility_scope=Visibility.PUBLIC))
        report.led.append(nation)

    if not diffs:
        return report
    outcome = apply_batch(
        conn,
        DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                  proposer_model=None, diffs=tuple(diffs)))
    if not outcome.applied:
        report.problems.append(
            f"the concert could not be warmed: {outcome.reason}")
        report.led = []
        return report
    report.warmed = len(diffs)
    return report


def briefing_lines(
    conn: sqlite3.Connection, world_id: str, nation_id: str, *, date: str = "",
    turn: int = 0,
) -> list[str]:
    """What this power's standing in the world rests on, and what would raise it.

    Shown to everybody, because the route is open to everybody and a power told only when it
    is already winning learns the rules by accident.
    """
    parts = leadership_parts(conn, world_id, nation_id, date=date, turn=turn)
    ranked = sorted(
        ((leadership_index(conn, world_id, str(row["id"]), date=date, turn=turn),
          str(row["id"]))
         for row in conn.execute(
             "SELECT id FROM nation WHERE world_id = ? AND is_alive = 1", (world_id,))),
        key=lambda entry: (-entry[0], entry[1]))
    rival = max((score for score, other in ranked if other != nation_id), default=0.0)
    edge = parts.total - rival
    place = next((i + 1 for i, (_s, n) in enumerate(ranked) if n == nation_id), 0)

    lines = [
        "## Your standing in the international order",
        f"You are **{place} of {len(ranked)}** on the engine's reckoning of who is leading, "
        f"at {parts.total:.2f}, against {rival:.2f} for the next power -- a gap of "
        f"{edge:+.2f}. The world warms toward a power **only while it stands at least "
        f"{EDGE_FLOOR:.2f} clear of whoever is second**, and faster the further clear it is. "
        "A close-run field earns nothing at all, because leading means leading somebody.",
    ]
    _named = {
        "promises kept": (parts.kept, "active commitments you are party to and have never "
                                      "broken -- the largest part, and the one that takes a "
                                      "game to build"),
        "aid to the losing": (parts.aid, "backing a partner whose own country is being held "
                                         "by somebody else; arming a comfortable friend "
                                         "counts for nothing here"),
        "restraint": (parts.restraint, "no war you started in the last two years, and no "
                                       "covert operation of yours that came out"),
        "wars you ended": (parts.mediation, "peaces you brokered between other powers -- "
                           "the one part of this a power earns by acting rather than by "
                           "keeping its word or staying home"),
        "a seat": (parts.seats, "membership of the bodies that exist, a permanent seat "
                                "counting double"),
    }
    for label, (value, why) in _named.items():
        lines.append(f"- {label}: **{value:.2f}** -- {why}")
    lines.append(
        "This is the `concert` victory: leading the world by standing rather than by force. "
        "It is slow on purpose -- the world warms toward a power over decades of consistent "
        "behaviour, and a war you start sets it back. Nothing you write moves it; it is "
        "earned or it is not."
    )
    lines.append("")
    return lines
