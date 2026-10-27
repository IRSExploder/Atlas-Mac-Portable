"""How long a war has gone on, what it has cost, and how tired of it a country is.

**The ledger a war never had.** A war in this engine is two ``relation`` rows: no start
turn, no duration, no accumulator. Casualties existed only as ``unit.strength`` diffs that
nobody added up, so nothing could answer *how long has this gone on* or *what has it cost* --
the two questions every peace in history turned on. A power could fight for a decade at no
political cost whatever, which is why the world's wars ended only when somebody's model
happened to feel like stopping.

This is layer 2 of the war loop. It adds the ledger, and one derived number on top of it.

**Wholly engine-derived. The model proposes nothing here**, and there is no channel to
propose it through: a power that could write its own exhaustion could fight forever, and one
that could write somebody else's could talk a rival into a surrender it had no reason to
accept. Every column is engine-only.

**Exhaustion never forces a hand.** It informs the belligerent, it feeds
:mod:`atlas.engine.leverage`, and at no value does it end a war by itself. Whether a
settlement is *offered* at the top of the scale is layer 4's decision and Alex's to make.

**Every dial below is tunable and none of them is load-bearing on its own.** They are stated
as named constants with the reasoning attached so they can be argued with against real
curves rather than in the abstract.

**And none of them knows how often the player presses the button.** A year of one war costs
what a year of that war costs, whether it is played in fifty-two turns or in one: see
:func:`_charge`, which walks a turn a day at a time for that reason, and
``test_a_war_wearies_a_country_by_the_calendar.py``, which is the statement of it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..state.apply import apply_batch, record_event
from ..state.diff import Diff, DiffBatch
from ..state.models import DiffOp, EventKind, EventOrigin, Role, Visibility
from .doctrines import factor
from .pacing import REFERENCE_DAYS, compounded, span_of

__all__ = [
    "BANDS",
    "OCCUPIED_CEILING",
    "OCCUPIED_DRIFT",
    "WarEffort",
    "assess_war_effort",
    "band_of",
    "casualty_diffs",
    "effort_between",
    "efforts_of",
    "war_effort_id_for",
]

# --------------------------------------------------------------------------- the dials

#: **Where time alone gets to, and no further.** A war nobody is winning still wears a
#: country out -- this is the term that makes a stalemate cost something -- but it must not
#: be able to finish the job.
#:
#: It was a flat 0.6 a month with no ceiling at all, and the 200-turn campaign made the
#: consequence plain: **every long war pinned at 100 with zero provinces occupied and unit
#: strength flat.** Wars in which literally nothing happened reached total exhaustion in
#: about a decade, so a phoney war and a national catastrophe arrived at the same number and
#: the layer stopped telling them apart -- which matters, because exhaustion is an input to
#: whether a peace gets signed.
#:
#: 32 is *straining*, the second of five bands: a country holding a quiet front for years is
#: tired of it and is not falling over. Everything past this is something that actually
#: happened -- the dying, ground lost, a treasury that cannot pay.
TIME_CEILING = 32.0

#: How much of the distance to that ceiling a quiet month closes. Chosen so the first month
#: of a war costs almost exactly what the old flat rate did (0.02 x 32 = 0.64 against 0.6),
#: which keeps the opening of every war where it was tuned and changes only where it ends up.
TIME_DRIFT = 0.02

#: **What the dying costs, as a share of the army that did it.**
#:
#: **The denominator is the army, not the manpower pool, and that is a correction.** It was
#: `population * 2` -- the ceiling the manpower pool refills toward -- and measured against
#: the real eras that reads differently in each one: the 1972 United States fields 754
#: strength against a ceiling of 334, so a 1.2% month cost it 0.95 while the same 1.2% in
#: 1936 cost a third of that. The same loss *rate* now reads the same in every era, which is
#: what makes one set of dials tunable at all.
#:
#: At 60, losing a fiftieth of your army in a month adds 1.2 -- comparable to time itself --
#: and losing a fifth of it in one month adds 12, which is a catastrophe and should read as
#: one.
CASUALTY_WEIGHT = 60.0

#: **What a fully occupied homeland trends toward**, and the rate it gets there.
#:
#: **A ceiling, not an accumulation, and this is the sharpest of the three corrections.** The
#: occupied term used to *add* 25 x share every month, so a half-occupied country gained 12.5
#: a month for ever and collapsed inside a year -- and the last provinces of a conquest
#: changed nothing, because the number had already saturated. Occupation is now a level the
#: country settles toward, the way the economy drifts toward what its industry supports: a
#: half-occupied country trends to `OCCUPIED_CEILING/2` and stays there, permanently
#: war-weary and not collapsing, while a wholly occupied one still trends to collapse.
#:
#: The drift only ever pushes *up*: ground taken back is relief the recovery term gives
#: slowly, not a cure this one hands out at once.
OCCUPIED_CEILING = 100.0

#: The top of the exhaustion scale, and the clamp in `assess_war_effort` is the same number.
#: Named because `_charge` now closes on it rather than walking into it, and a bound that is
#: written as a literal in two places is a bound that will be changed in one.
EXHAUSTION_CEILING = 100.0

#: How much of the remaining distance to that ceiling is closed each month. At a quarter, a
#: country whose homeland is half held crosses into war-weary in about six months and
#: approaches its plateau within a year and a half -- an invasion tells on a country quickly
#: and then keeps telling.
OCCUPIED_DRIFT = 0.25

#: **Empty coffers.** A flat monthly add while the treasury cannot pay for the war. Small,
#: because arrears already cost money and stability elsewhere and this is the political echo
#: of it rather than a second charge.
SHORTAGE_WEIGHT = 3.0

#: **What peace and quiet mend, per month.** Applied when the pair is no longer at war, and
#: also to a war in which nothing at all happened this turn -- a lull is not recovery at the
#: same rate as peace, but a front where nobody fired for a month does let a country breathe.
#: At 1.2 a month a country at 60 needs four years of peace to forget entirely.
RECOVERY_PER_MONTH = 1.2

#: **How much a shaky government multiplies all of it.** A stable country grinds on; an
#: unstable one comes apart faster under the same losses. At 0.5, a nation at zero stability
#: tires half again as fast as one at a hundred.
FRAGILITY = 0.5

#: The five bands, by upper bound. Names rather than numbers, because "war-weary" is what a
#: briefing should say and 63.4 is not.
BANDS: tuple[tuple[float, str], ...] = (
    (20.0, "resolute"),
    (45.0, "straining"),
    (70.0, "war-weary"),
    (90.0, "exhausted"),
    (100.1, "collapsing"),
)


def band_of(exhaustion: float) -> str:
    for ceiling, name in BANDS:
        if exhaustion < ceiling:
            return name
    return BANDS[-1][1]


@dataclass(frozen=True, slots=True)
class WarEffort:
    """One side's ledger of one war."""

    nation_id: str
    enemy_id: str
    opened_turn: int = 0
    last_turn: int = 0
    casualties: float = 0.0
    exhaustion: float = 0.0

    @property
    def band(self) -> str:
        return band_of(self.exhaustion)

    def describe(self, name: str = "") -> str:
        who = name or self.nation_id
        return (f"{who}: {self.band} ({self.exhaustion:.0f}/100), "
                f"{self.casualties:.1f} strength lost since turn {self.opened_turn}")


def war_effort_id_for(nation_id: str, enemy_id: str) -> str:
    """Derived, not autoincremented, so a ledger survives a rewind with its identity."""
    return f"we_{nation_id}_{enemy_id}"


# --------------------------------------------------------------------------- reading


def effort_between(
    conn: sqlite3.Connection, world_id: str, nation_id: str, enemy_id: str
) -> WarEffort | None:
    row = conn.execute(
        "SELECT nation_id, enemy_id, opened_turn, last_turn, casualties, exhaustion"
        "  FROM war_effort WHERE world_id = ? AND nation_id = ? AND enemy_id = ?",
        (world_id, nation_id, enemy_id)).fetchone()
    return _read(row) if row else None


def efforts_of(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> dict[str, WarEffort]:
    """Every war this power has a ledger for, by enemy. Includes wars it has finished."""
    return {
        str(row["enemy_id"]): _read(row)
        for row in conn.execute(
            "SELECT nation_id, enemy_id, opened_turn, last_turn, casualties, exhaustion"
            "  FROM war_effort WHERE world_id = ? AND nation_id = ?"
            " ORDER BY exhaustion DESC, enemy_id", (world_id, nation_id))
    }


def _read(row) -> WarEffort:
    return WarEffort(
        nation_id=str(row["nation_id"]), enemy_id=str(row["enemy_id"]),
        opened_turn=int(row["opened_turn"]), last_turn=int(row["last_turn"]),
        casualties=float(row["casualties"]), exhaustion=float(row["exhaustion"]))


# --------------------------------------------------------------------- the casualty side


def casualty_diffs(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    losses: dict[str, float],
    enemy_of: dict[str, str],
    turn: int,
    event_id: int,
) -> list[Diff]:
    """Charge a contest's losses to the ledgers of the wars they were suffered in.

    Called from the battle path, which already knows the attacker, the defender and what
    each of them lost -- so this adds a row to the same batch rather than recomputing
    anything. A pair that is **not at war** gets no ledger: undeclared fighting is outside
    the war loop by decision, and giving it a ledger would be the first half of pulling it
    in without the rest.

    Returns diffs; the caller applies them with the battle, so a refused battle costs
    nobody a casualty it did not take.
    """
    diffs: list[Diff] = []
    for nation_id, lost in losses.items():
        enemy_id = enemy_of.get(nation_id, "")
        if not enemy_id or lost <= 0 or not _at_war(conn, world_id, nation_id, enemy_id):
            continue
        row_id = war_effort_id_for(nation_id, enemy_id)
        existing = conn.execute(
            "SELECT casualties FROM war_effort WHERE world_id = ? AND id = ?",
            (world_id, row_id)).fetchone()
        if existing is None:
            diffs.append(
                Diff("war_effort", row_id, "__insert__", DiffOp.INSERT,
                     {
                         "world_id": world_id, "nation_id": nation_id,
                         "enemy_id": enemy_id, "opened_turn": turn, "last_turn": turn,
                         "casualties": round(float(lost), 3),
                         "charged_casualties": 0.0, "exhaustion": 0.0,
                     },
                     reason_event_id=event_id, visibility_scope=Visibility.PUBLIC))
        else:
            diffs.append(
                Diff("war_effort", row_id, "casualties", DiffOp.ADJUST,
                     round(float(lost), 3), reason_event_id=event_id,
                     visibility_scope=Visibility.PUBLIC))
    return diffs


# ------------------------------------------------------------------------- the assessment


def assess_war_effort(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str, days: int = 30
) -> list[str]:
    """Move every ledger one turn: charge what the war cost, and mend what peace does.

    Run after the fighting and after the settlements, so a war that ended this turn is
    already at peace when its ledger is read -- which is what makes the first month of
    peace recover rather than exhaust.

    Returns one line per band change, which is the only part of this a turn report should
    carry: a country crossing from *straining* into *war-weary* is news, and 0.7 of a point
    is not.
    """
    span = span_of(days)
    ledgers = list(conn.execute(
        "SELECT id, nation_id, enemy_id, casualties, charged_casualties, exhaustion,"
        "       opened_turn FROM war_effort WHERE world_id = ? ORDER BY id", (world_id,)))
    known = {(str(row["nation_id"]), str(row["enemy_id"])): row for row in ledgers}

    wars = [
        (str(row["nation_a"]), str(row["nation_b"]))
        for row in conn.execute(
            "SELECT nation_a, nation_b FROM relation"
            " WHERE world_id = ? AND at_war = 1 ORDER BY nation_a, nation_b", (world_id,))
    ]
    # Population is the ground's, not the nation's -- the same sub-select
    # `budget.collect_revenue` uses, so the manpower ceiling here is the one the pool
    # actually refills toward. A country whose provinces are occupied has a smaller
    # ceiling, so the same casualties weigh heavier: correct, and worth saying out loud.
    nations = {
        str(row["id"]): row
        for row in conn.execute(
            "SELECT n.id, n.name, n.manpower, n.stability, n.treasury,"
            "       COALESCE((SELECT SUM(r.population) FROM region r"
            "                  WHERE r.world_id = n.world_id AND r.kind <> 'ocean'"
            "                    AND COALESCE(r.controller_nation_id, r.owner_nation_id)"
            "                        = n.id), 0) AS population"
            "  FROM nation n WHERE n.world_id = ? AND n.is_alive = 1", (world_id,))
    }
    occupied = _occupied_homelands(conn, world_id)
    homelands = _homeland_sizes(conn, world_id)
    armies = {
        str(row["nation_id"]): float(row["total"] or 0.0)
        for row in conn.execute(
            "SELECT nation_id, SUM(strength) AS total FROM unit WHERE world_id = ?"
            " GROUP BY nation_id ORDER BY nation_id", (world_id,))
    }

    diffs: list[Diff] = []
    news: list[str] = []
    seen: set[tuple[str, str]] = set()

    for nation_id, enemy_id in wars:
        if nation_id not in nations or enemy_id not in nations:
            continue
        seen.add((nation_id, enemy_id))
        row = known.get((nation_id, enemy_id))
        before = float(row["exhaustion"]) if row else 0.0
        casualties = float(row["casualties"]) if row else 0.0
        charged = float(row["charged_casualties"]) if row else 0.0

        delta = _charge(
            nations[nation_id], fresh=casualties - charged,
            army=armies.get(nation_id, 0.0),
            occupied=occupied.get((nation_id, enemy_id), 0),
            homeland=homelands.get(nation_id, 0), standing=before, span=span,
            grief=factor(conn, world_id, nation_id, "mobilisation", "casualty_grief"))
        after = max(0.0, min(EXHAUSTION_CEILING, before + delta))

        row_id = war_effort_id_for(nation_id, enemy_id)
        if row is None:
            diffs.append(
                Diff("war_effort", row_id, "__insert__", DiffOp.INSERT,
                     {
                         "world_id": world_id, "nation_id": nation_id,
                         "enemy_id": enemy_id, "opened_turn": turn, "last_turn": turn,
                         "casualties": 0.0, "charged_casualties": 0.0,
                         "exhaustion": round(after, 3),
                     }))
        else:
            if abs(after - before) >= 0.005:
                diffs.append(
                    Diff("war_effort", row_id, "exhaustion", DiffOp.SET, round(after, 3)))
            if casualties > charged:
                diffs.append(
                    Diff("war_effort", row_id, "charged_casualties", DiffOp.SET,
                         round(casualties, 3)))
            diffs.append(Diff("war_effort", row_id, "last_turn", DiffOp.SET, turn))
        news.extend(_band_news(nations[nation_id], before, after))

    # Wars that are over, and ledgers whose war ended turns ago: peace mends, slowly.
    for (nation_id, enemy_id), row in known.items():
        if (nation_id, enemy_id) in seen:
            continue
        before = float(row["exhaustion"])
        if before <= 0.0:
            continue
        after = max(0.0, before - RECOVERY_PER_MONTH * span)
        diffs.append(
            Diff("war_effort", war_effort_id_for(nation_id, enemy_id), "exhaustion",
                 DiffOp.SET, round(after, 3)))
        if nation_id in nations:
            news.extend(_band_news(nations[nation_id], before, after))

    if not diffs:
        return []

    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date, kind=EventKind.NARRATIVE,
        origin=EventOrigin.ENGINE,
        text=(news[0] if len(news) == 1 else
              f"{len(news)} power(s) felt the weight of their wars differently."
              if news else "The war ledgers were brought up to date."),
        visibility=Visibility.PUBLIC)
    outcome = apply_batch(
        conn,
        DiffBatch(
            world_id=world_id, turn=turn, proposer_role=Role.ENGINE, proposer_model=None,
            diffs=tuple(
                Diff(d.target_table, d.target_id, d.field, d.op, d.new_value,
                     reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
                for d in diffs)))
    if not outcome.applied:
        return []
    return news


def _charge(  # noqa: PLR0913 - one call site, and each argument is a different driver
    nation, *, fresh: float, army: float, occupied: int, homeland: int,
    standing: float, span: float, grief: float = 1.0,
) -> float:
    """What this turn of this war does to one side's patience, over the whole of its span.

    **Every term was scaled right and the sum of them was not.** Each of the four charges
    below is a rate per reference month, and three of the four *close on a ceiling* -- so
    what each one costs depends on how much room is left under that ceiling. They all read
    the same ``standing``. Applied once over twelve months rather than twelve times over
    one, none of them sees the room the other two have just taken, and the country is
    charged a full year's worth of headroom it no longer has.

    Measured over an identical year of an identical war -- same casualties to two decimals
    (11.92 against 11.94), same army left standing to a tenth of a percent -- a country
    played monthly reached 43.3 exhaustion and the same country played in one yearly turn
    reached 61.4. **1.42x the weariness for the same war**, and 1.60x in the worst cell of
    the battery. A war is not supposed to know how often the player presses the button.

    **So a turn's charge is the month's charge, ground out.** This walks the span a day at a
    time and applies :func:`_charge_step` to each day, which is what "a rate per month" has
    to mean if a year of it is to cost the same however the year is sliced. Fifty-two weekly
    turns, twelve monthly, four seasonal and one yearly are all **360 steps of the same
    size**, so they compose to one number rather than to four different ones.

    **A day rather than a month, and rather than a closed form.** A month would leave the
    weekly turn -- 0.233 of one -- a single ragged step, which is the half of this bug a
    player meets first. And a closed form does exist for the three drifts taken together
    (they are one linear recurrence toward a weighted target), but the fourth term is a hard
    switch: when nothing at all is happening the charge becomes *recovery*, and a country
    that mends back below :data:`TIME_CEILING` starts tiring again on the way down.
    Stepping walks into that crossing; a closed form would have to be told where to look for
    it, and be re-derived every time a term is added.

    **What this changes for a reference month, stated rather than buried.** A month is now
    thirty small bites out of the headroom instead of one, so terms that used to be blind to
    each other for a whole month are blind for a day. A war with one live term -- time
    alone, or ground alone -- is unchanged to nine decimals, because a single closing rate
    already composed exactly. A month with two live drifts comes out about 1.4% lower. A
    month that loses a fifth of the army charges the compounded share of the headroom rather
    than the flat one, which is lower again. That is the price of the invariant and it is
    not avoidable: no formula is both exactly cadence-invariant and equal to a number that
    was produced by taking the month in one bite.
    """
    if span <= 0.0:
        return 0.0
    steps = max(1, round(span * REFERENCE_DAYS))
    step = span / steps
    daily = max(0.0, fresh) / steps
    # **The denominator is the army each day began with, not the one the turn ended on.**
    # `army` is read after the fighting, so what the country had when this turn opened is
    # that plus everything it has since lost -- and it falls as the turn goes on. Handing
    # every step the same opening figure is the one place the walk would still keep a turn's
    # length: twelve monthly turns each measure their dead against a smaller army than the
    # one before, and a single yearly turn measured all of them against the largest. Walking
    # the denominator down makes the two sequences the same sequence, which is what makes
    # the composition exact rather than merely close.
    left = max(0.0, fresh)

    # Clamped inside the walk and not only at the end: a ledger that reaches the ceiling on
    # day three spends the rest of the turn there, and one that mends to zero stops there.
    at = standing
    for _step in range(steps):
        at = max(0.0, min(EXHAUSTION_CEILING, at + _charge_step(
            nation, fresh=daily, had=max(1.0, army + left), occupied=occupied,
            homeland=homeland, standing=at, span=step, grief=grief)))
        left -= daily
    return at - standing


def _charge_step(  # noqa: PLR0913 - one call site, and each argument is a different driver
    nation, *, fresh: float, had: float, occupied: int, homeland: int,
    standing: float, span: float, grief: float = 1.0,
) -> float:
    """One step of :func:`_charge`. The formula, in one place.

    Every term is a rate per month and is scaled by ``span``, which here is the length of
    the step rather than of the turn. The two exceptions are stated where they are made.
    """
    # **The share of the army lost, not of the population.** See `CASUALTY_WEIGHT`: the army
    # that took the losses is the denominator that reads the same in every era. What it had
    # is what survives plus what it has still to lose in this turn -- worked out per step by
    # `_charge` and handed in, so a step is a share of the army that started that step.
    #
    # **And how hard this country takes them.** `grief` is what the nation's mobilisation
    # doctrine makes of a casualty: a conscript army's dead are the country's sons and are
    # felt as such, and a professional one's are soldiers who chose it. 1.0 for a nation
    # with no doctrine, so this term reads exactly as it did before doctrines existed.
    # **Closing on the ceiling rather than walking into it**, which is the argument this
    # function already makes twice below -- for occupation and for time -- and which
    # casualties were left out of. They are the largest term, so leaving them out made the
    # measure a clock anyway: on the Ukraine front the defender reached 100 of 100 on **turn
    # six** of a twenty-turn war and sat there, so three quarters of that war was fought on
    # a flat signal. Everything downstream reads exhaustion -- the negotiating position in
    # `leverage`, what a power will settle for in `peace`, whether it takes a truce in
    # `armistice` -- and none of them could tell a bad war from a catastrophic one.
    #
    # **Only this term, and deliberately.** The first attempt scaled the whole charge by the
    # headroom and broke span invariance in nine tests: `invaded` and `weary` already close
    # on their own ceilings with `compounded`, and multiplying an already-compounded term by
    # a linear factor is the exact trap the comment on `invaded` warns about. This one needs
    # no compounding because the dying is not span-scaled either -- it already happened over
    # the step -- so a proportional bite out of the remaining distance is what it wants.
    bled = CASUALTY_WEIGHT * (max(0.0, fresh) / had) * float(grief)
    bleeding = max(0.0, EXHAUSTION_CEILING - standing) * min(
        1.0, bled / EXHAUSTION_CEILING)
    broke = SHORTAGE_WEIGHT if float(nation["treasury"]) <= 0 else 0.0

    # **Occupation drifts toward a ceiling instead of piling up.** A country half-held
    # settles at half the ceiling; ground held since last year adds nothing once the plateau
    # is reached, which is what stops a stabilised front collapsing a country by itself.
    #
    # **Compounded, not multiplied.** `gap * rate * span` is the same closing-a-fraction
    # rate written linearly, and it walks straight past the plateau it is named for once the
    # span passes `1 / OCCUPIED_DRIFT` -- four months. A quarter-occupied country over a
    # one-year jump took +75 from a term whose whole ceiling is 25, and the clamp at 100 hid
    # it: the value stayed in range, so nothing was refused and nothing was reported. The
    # country simply arrived at total exhaustion. `compounded` is what "closes a quarter of
    # the remaining distance each month" actually means over twelve of them.
    share = (occupied / homeland) if homeland else 0.0
    invaded = max(0.0, OCCUPIED_CEILING * share - standing) * compounded(
        OCCUPIED_DRIFT, span)

    # **Time drifts toward a plateau instead of adding for ever.** The same shape the
    # occupation term above already had, and for the same reason: a quantity that only ever
    # adds will reach the top of its scale given enough months, and then it is a clock
    # rather than a measure. Above the ceiling this contributes nothing at all -- a country
    # already past *straining* is not made wearier by another quiet month, it is made
    # wearier by what is happening to it.
    weary = max(0.0, TIME_CEILING - standing) * compounded(TIME_DRIFT, span)

    # **The dying is not scaled by the step and everything else is.** `_charge` has already
    # apportioned the turn's casualties across its steps, so scaling them again here would
    # charge the same deaths a second time.
    gross = bleeding + invaded + weary + broke * span
    if gross <= 0:
        return -RECOVERY_PER_MONTH * span
    fragility = 1.0 + FRAGILITY * (1.0 - float(nation["stability"]) / 100.0)
    return gross * fragility


def manpower_ceiling(population: float, mobilisation: float = 1.0) -> float:
    """What a country could raise, identical to the ceiling `budget` refills toward.

    **No longer the casualty denominator** -- see :data:`CASUALTY_WEIGHT` -- and kept because
    it is the honest measure of what a country *could* put in the field. One copy, so the
    reports, the budget and the doctrine layer cannot drift.

    ``mobilisation`` is the multiplier a nation's doctrine puts on the pool, from
    :func:`atlas.engine.doctrines.factor`, and it is **1.0 for a nation with no doctrine**
    -- which is every nation of every world written before the table existed. Passed in
    rather than looked up here, so this stays a pure function of the numbers it is given
    and can be reasoned about without a database.
    """
    return max(10.0, float(population) * 2.0 * float(mobilisation))


def _band_news(nation, before: float, after: float) -> list[str]:
    if band_of(before) == band_of(after):
        return []
    direction = "is" if after > before else "is back to being"
    return [f"{nation['name']} {direction} {band_of(after)} ({after:.0f}/100)"]


def _occupied_homelands(
    conn: sqlite3.Connection, world_id: str
) -> dict[tuple[str, str], int]:
    """``(victim, occupier) -> how many of the victim's home provinces they hold``."""
    found: dict[tuple[str, str], int] = {}
    for row in conn.execute(
        "SELECT home_nation_id AS home,"
        "       COALESCE(controller_nation_id, owner_nation_id) AS holder,"
        "       COUNT(*) AS n FROM region"
        " WHERE world_id = ? AND home_nation_id IS NOT NULL AND kind <> 'ocean'"
        "   AND COALESCE(controller_nation_id, owner_nation_id) <> home_nation_id"
        " GROUP BY home, holder ORDER BY home, holder", (world_id,)
    ):
        if row["holder"]:
            found[(str(row["home"]), str(row["holder"]))] = int(row["n"])
    return found


def _homeland_sizes(conn: sqlite3.Connection, world_id: str) -> dict[str, int]:
    return {
        str(row["home"]): int(row["n"])
        for row in conn.execute(
            "SELECT home_nation_id AS home, COUNT(*) AS n FROM region"
            " WHERE world_id = ? AND home_nation_id IS NOT NULL AND kind <> 'ocean'"
            " GROUP BY home ORDER BY home", (world_id,))
    }


def _at_war(conn: sqlite3.Connection, world_id: str, a: str, b: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM relation WHERE world_id = ? AND nation_a = ? AND nation_b = ?"
        "   AND at_war = 1", (world_id, a, b)).fetchone() is not None
