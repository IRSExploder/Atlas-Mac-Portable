"""What winning looks like, and how close anybody is to it.

ATLAS could be played forever without ever being told whether you were doing well. This is
the answer, and the design is in three parts that are easier to argue with separately.

**Nothing is stored.** :func:`score_of` is a pure read over the same tables the map draws.
The state and the audit trail already know everything a score is made of, and a cached copy
is a copy that can disagree with them after a rewind -- so **rewind-exactness here is free
rather than defended**: there is no scoreboard to unwind. It also means a score can never be
stale, farmed, or set by a model.

**A victory is a fact in the event log.** :func:`assess_victory` writes an ENGINE event when
a condition is met, and ``rewind_to`` deletes events past its target turn, so an achievement
unwinds with everything else and this whole layer needs **no schema change at all**. It also
puts the achievement everywhere events already go: the turn report, the overlay, and the
briefing a model reads -- and *you are being watched as the leading power* is a far better
prompt than a hidden number.

That costs one compromise, stated rather than hidden: ``event.kind`` is a closed CHECK
constraint, so adding a ``'victory'`` kind would mean rebuilding the table and invalidating
every save in existence. These are written as ``narrative`` carrying :data:`_MARK`, which
this module owns and reads back. Uglier than a kind of its own; very much cheaper than a
migration nobody's save survives.

**Winning does not end the game.** The engine records that a condition was met and the
report says so. Whether that was the end is the player's call -- a sandbox whose window
closes when a threshold trips punishes the thing people play a sandbox for.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date as _date

from ..state.apply import record_event
from ..state.models import EventKind, EventOrigin, Visibility
from .budget import ground_value, ground_values

__all__ = [
    "CONDITIONS",
    "ConditionProgress",
    "Requirement",
    "victory_progress",
    "WEIGHTS",
    "Score",
    "Standing",
    "VictoryReport",
    "assess_victory",
    "score_of",
    "standings",
]


#: What each component of a score is worth, relative to the others. **One table, in one
#: module**, because "what is a grand-strategy game for" is an argument worth having in a
#: place a reader can find rather than one distributed across five queries.
#:
#: They sum to 1.0 so a total reads as a percentage of a theoretical maximum nobody
#: reaches, and every component is read from something the engine already simulates -- so
#: playing well is what scores well and there is nothing to farm.
WEIGHTS: dict[str, float] = {
    # The map, discounted for occupation by `ground_value` itself: an occupier is scored
    # for what the ground is worth to it rather than for how much of it is shaded.
    "ground": 0.34,
    # A rich country with no industry is a different thing from a poor one with a lot, so
    # both, together.
    "wealth": 0.22,
    # Strength, and the arsenal -- which nothing else in the engine scores at all.
    "arms": 0.20,
    # Legitimacy, the regard of the other powers, and a seat at whatever bodies exist.
    # **The only component a peaceful nation can lead on**, which is the point of it.
    "standing": 0.16,
    "knowledge": 0.08,
}

#: Rough ceilings, so the components can be added at all. Not caps -- a nation past one
#: simply scores 1.0 on that component, which is the honest reading of "as far ahead as
#: this measure can see". Set from the 2026 map's leaders so the scale means something on a
#: real world rather than on a fixture.
_FULL = {
    "ground": 260.0,
    "economy": 110.0,
    "industry": 90.0,
    "military": 120.0,
    "warheads": 2000.0,
    "technologies": 14.0,
}

#: A share of the world's ground value that is simply too much for anyone else to answer.
#:
#: **Set from the map rather than from intuition.** The obvious 40% would never once have
#: fired: measured on the 2026 seed the largest power on Earth holds **5.9%** of world
#: ground value, and the top eight together hold 27% -- a 243-nation world spreads its
#: ground very thin, and a threshold picked by imagining a board game with six players is a
#: threshold nothing reaches. A quarter of the world is *four times the United States* and
#: is a genuine hegemon; 40% would mean conquering most of the planet and would have made
#: this condition decorative.
#: **Measured, and deliberately not lowered.** The obvious reading of "nobody has ever won"
#: is that this number is too high for a 204-nation map, and it was very nearly changed on
#: that basis. What the measurement says is that no threshold would work, because **the
#: quantity does not grow**: over sixty turns the leader's share falls from 6.07% to 5.23% in
#: a passive world and to 3.80% when everybody fights. A single power conquering with nobody
#: fighting back rises from 5.59% to 6.49% by turn thirteen and then decays to 3.33%, below
#: where it began -- occupied ground is discounted and held foreign ground erodes, so a
#: campaign is net-negative on ground value.
#:
#: So any threshold low enough to fire would fire on turn one, for whoever the scenario
#: seeded largest, having conquered nothing. Lowering this buys a participation trophy rather
#: than a victory. The territorial route is closed by the occupation and unrest layers, not
#: by this constant, and it should not be reopened here.
_DOMINATION_SHARE = 0.25

#: ...and how far clear of the next power that has to be.
#:
#: **A share alone says "large", not "dominant", and the difference showed up the moment a
#: small scenario was played.** The three-nation sandbox seeds Karthos and Aurelia at 44.4%
#: of world ground each -- both over any threshold worth setting -- so once decision 119
#: made the condition pick a single leader, whichever of them a rounding-level development
#: shift put ahead was announced as dominating the world without having conquered anything.
#: Raising the share would not fix that: at 44.4% each, no threshold below 44.4% helps and
#: anything above it makes the condition unreachable on a real map.
#:
#: What was missing is that domination is a statement about the *gap*. Twice the runner-up
#: is a power nobody can answer; on the 2026 map the leader stands at 1.09x the second, and
#: in the sandbox the two are level at 1.0x, so neither qualifies -- and a Karthos that
#: actually swallowed Aurelia would stand at 8x and plainly should.
_DOMINATION_MARGIN = 2.0

#: How many provinces a dominant power must actually hold.
#:
#: **The share and the margin are both ratios, and a ratio has no scale.** Decision 124
#: fixed the sandbox by requiring a gap, and a gap is still satisfiable by a world with
#: four provinces in it: hold three of them and you are four times the runner-up, having
#: conquered nothing anybody would call a campaign. A conquest is a number of places as
#: well as a proportion of them.
#:
#: Set low enough that a real map clears it long before the share does -- on the 2026 map
#: 25% of world ground is well over a hundred provinces -- so on any scenario worth playing
#: this is inert, and on a toy one it is the whole guard.
_DOMINATION_PROVINCES = 8

#: How long domination must hold before it is announced, **in days of world time**.
#:
#: A campaign, not a moment. Ground changes hands every turn of a war, and a threshold
#: crossed by one province taken in March and lost again in April was never a domination --
#: it was the front moving. Shorter than hegemony's because the share and province
#: thresholds are already hard to reach by accident.
#:
#: **Days rather than turns, because a victory condition must not depend on how the player
#: spends their turns.** It was two consecutive turns, which is one month of elapsed time at
#: the default pacing and one *year* at yearly pacing -- so the pace control, which exists
#: for how much detail somebody wants to watch, silently decided who won and when. Thirty
#: **28 rather than 30, and the two days are the whole of the care taken here.** Two
#: consecutive monthly turns span a calendar month, and the shortest calendar month is
#: February's 28 days -- so a threshold of 30 would have made domination unreachable across
#: a February at the very pacing it was tuned for. The number is the shortest real span the
#: equivalent monthly run can have, which is what makes "nothing about the default game
#: moves" true in every month rather than in most of them.
_DOMINATION_DAYS = 28.0

#: How far ahead of the runner-up counts as hegemony, and for how long in days of world
#: time. **The one condition that genuinely needs history**, and it is derived by asking
#: which turns the event was written on and how much world time those turns span -- which
#: the event log answers without storing anything new, because every event carries its date.
#:
#: 59 is the shortest real span of three consecutive monthly turns -- January to March, or
#: February to April, in a common year. See `_DOMINATION_DAYS` for why this is a duration
#: rather than a count, and why the number is the shortest span rather than the nominal one.
#: Observed maximum across every run measured: **1.19**. The components are each normalised
#: to their own ceiling before being weighted, so every power clusters near 0.5 and the ratio
#: has nowhere to go. Recorded rather than retuned, for the same reason as
#: `_DOMINATION_SHARE`: the number is not what is stopping this.
_HEGEMONY_MULTIPLE = 2.0
_HEGEMONY_DAYS = 59.0

#: Leading on standing by this much is a *concert* victory: the way a power that never wins
#: a battle is meant to be able to win.
#:
#: **The only condition with real headroom, and the only one worth pursuing further.** A power
#: at the top of all three parts of `standing` scores 1.00 against a typical great power's
#: 0.62 -- a ratio of 1.62, comfortably past this. The reason it has never fired is that none
#: of the three parts is a decision a government can take: `nation.legitimacy` is engine-only,
#: seats are not writable, and regard is `AVG(score) WHERE nation_b = me` -- what *others*
#: think -- while a power may only write the relation it owns. Measured: a power courting
#: everybody for forty turns moved its standing from 0.573 to 0.569.
#:
#: Regard being earned rather than declared is right. What is missing is that nothing a power
#: does earns it fast enough to matter, which is a mechanic to build rather than a threshold
#: to lower.
#: **1.10 rather than 1.25, and lowered only once the quantity underneath it moved.**
#:
#: This is deliberately not the rescale that was refused for `_DOMINATION_SHARE`, and the
#: difference is the whole argument. There, the measured quantity never grew -- the leader's
#: ground share *falls* over any length of game -- so any threshold low enough to fire would
#: have fired on turn one for whoever the scenario seeded largest, having conquered nothing.
#:
#: Here the quantity now grows, and grows only for a power that earns it. With
#: `atlas.engine.concert` awarding the world's regard and a settled country to whoever leads
#: the international order, a power that keeps its agreements for decades, aids the invaded
#: and starts no wars carries its standing from 0.649 to 0.742 and its ratio from 1.00 to
#: 1.10. The same power spending the same years declaring wars instead ends *below* where it
#: started, at 0.605. Everybody begins at 1.00, so nothing fires at turn zero.
_CONCERT_MARGIN = 1.10

#: And it has to be *held*, which it did not before.
#:
#: Concert was the one condition with no duration at all, so a single turn's reading fired
#: it. That was survivable at 1.25 because nothing ever reached 1.25; at 1.10 it is not --
#: measured, a world where every power declares war on every power it can touched 1.08 for
#: one turn around turn 49 on nothing but the churn of everybody else's standing falling.
#: A blip is not leadership.
#:
#: Six months rather than hegemony's two: leading the order is a slower and steadier thing
#: than out-scoring everybody, and the mechanic that feeds it moves in years already.
_CONCERT_DAYS = 180.0

#: And the world has to *actually think well of it*, not merely better than of anybody else.
#:
#: **The comparative test alone can be passed by standing still while everyone else falls.**
#: Measured: in a world where every power declared on every power it could reach, China took
#: the concert victory on turn 47 holding an average regard of **0.3** -- it had not risen,
#: the field had collapsed around it. That is a victory of being the last one standing, and
#: this condition is supposed to mean the opposite.
#:
#: 15 is placed from the two measured wins rather than chosen. The legitimate one -- the
#: United States on turn 97 of seed 88, six provinces, no wars started, a perfect record --
#: held 34.4 and clears this by nineteen points. The collapse win held 0.3. Anywhere between
#: would separate them; 15 is far enough above zero that a merely-unhated power cannot drift
#: into it, and far enough below 34 that a genuine leader is not asked for perfection.
_CONCERT_REGARD_FLOOR = 15.0

#: What the module writes into an event's text so it can find it again. See the module
#: docstring for why this is not an `event.kind`.
_MARK = "[victory]"

#: Per-scenario overrides, keyed on the world's preset id. **A tiny sandbox is not a small
#: world, it is a different kind of object**, and a victory condition tuned for a planet
#: says nothing true about three provinces. An entry of ``None`` disables the condition
#: outright; a dict overrides individual thresholds.
#:
#: The sandbox disables domination entirely rather than retuning it: there is no honest
#: threshold on a three-nation map where two powers hold 44.4% each, and pretending
#: otherwise is what produced a world-conquest announcement for a power that had conquered
#: nothing.
SCENARIO_VICTORY: dict[str, dict[str, dict | None]] = {
    "sandbox": {"domination": None},
}


def _tuning(conn: sqlite3.Connection, world_id: str, condition: str) -> dict | None:
    """Thresholds for one condition in this world, or ``None`` if it does not apply here.

    Reads the world's preset rather than its size, because *which scenario this is* is a
    fact somebody chose and *how many provinces it has* is a consequence -- and a rule
    keyed on the consequence would silently change behaviour the first time a designer
    subdivided a country.
    """
    row = conn.execute(
        "SELECT preset_id FROM world WHERE id = ?", (world_id,)
    ).fetchone()
    preset = str(row["preset_id"] or "") if row else ""
    overrides = SCENARIO_VICTORY.get(preset, {})
    if condition in overrides:
        return overrides[condition]
    return {}


#: Every condition, with the sentence the player reads **when it is achieved**. The ids are
#: stable and are what the report and any later interface should key on.
CONDITIONS: dict[str, str] = {
    "domination": "holds more of the world than the rest can answer",
    "hegemony": "stands twice above every other power, and has for years",
    "concert": "leads the world by standing rather than by force",
}

#: Conditions that **end the game** rather than being things to play for.
#:
#: **Hegemony is not a route and pretending otherwise cost a whole round of calibration.**
#: It asks a power to score twice every other, and there are exactly two ways there. Growing
#: cannot do it: every non-ground component maxed by direct write reaches 1.394, and 2.021
#: needs a hundred per cent of world ground -- which trips `_DOMINATION_SHARE` a very long
#: way first, making hegemony strictly redundant on that path. And *playing* for it is worse
#: than useless: measured across three seeds and three tiers, a specialist that genuinely
#: out-developed its control in every single arm -- the only power in any run to research
#: anything -- **lost on ratio anyway**, because the field converges toward the same clamps
#: and the ratio is mean-reverting. It punishes development rather than rewarding it.
#:
#: What it does do, uniquely, is end a game that has already effectively ended. Demonstrated
#: in `scratchpad/hegemony_backstop.py`: a world whose powers have decayed to wrecks leaves
#: the leader at a ratio of 3.542 while holding 6% of the ground it started with. Domination
#: refuses it because nobody conquered anything; Concert refuses it because a collapsed world
#: admires nobody and the regard floor is watching for exactly that. Neither other condition
#: catches the state, so the backstop is not redundant -- it is the only thing that notices.
#:
#: **The bar stays at 2.00 and is deliberately not tuned.** A backstop does not need to be
#: reachable; it needs to be right when it fires.
TERMINATION: frozenset[str] = frozenset({"hegemony"})

#: What was true *this turn*, which is a different sentence from what was achieved.
#:
#: The distinction is not pedantry. A sustained condition has to be marked on every turn it
#: holds so the run can be counted later -- but on the first two of those turns it has not
#: been achieved, and an event reading "and has for years" in the year it started would be
#: a lie written into the permanent record by the engine itself. The log records facts per
#: turn; the report announces achievements.
_HOLDS: dict[str, str] = {
    "domination": "holds more of the world than the rest can answer this year",
    "hegemony": "stands twice above every other power this year",
    "concert": "leads the world by standing rather than by force",
}

#: Conditions that must hold for a stretch of **world time** before they are achieved, and
#: how long. **Domination joined this after the province threshold went in**: ground changes
#: hands every turn of a war, and a threshold crossed by one province taken in March and
#: lost in April was the front moving rather than a conquest.
#:
#: The run itself is still unbroken *turns* -- a condition that lapsed and returned has not
#: held -- and what is measured across that run is the calendar. Both halves matter: turns
#: are what the engine can observe, and days are what the world experienced.
_SUSTAINED: dict[str, float] = {
    "hegemony": _HEGEMONY_DAYS,
    "domination": _DOMINATION_DAYS,
    "concert": _CONCERT_DAYS,
}


@dataclass(frozen=True, slots=True)
class Score:
    """One nation's standing, all of it derived and none of it stored."""

    nation_id: str
    name: str
    ground: float
    wealth: float
    arms: float
    standing: float
    knowledge: float

    @property
    def total(self) -> float:
        return round(
            WEIGHTS["ground"] * self.ground
            + WEIGHTS["wealth"] * self.wealth
            + WEIGHTS["arms"] * self.arms
            + WEIGHTS["standing"] * self.standing
            + WEIGHTS["knowledge"] * self.knowledge,
            4,
        )

    @property
    def components(self) -> dict[str, float]:
        return {
            "ground": self.ground, "wealth": self.wealth, "arms": self.arms,
            "standing": self.standing, "knowledge": self.knowledge,
        }

    def leading_on(self, best: dict[str, float] | None = None) -> str:
        """What this nation is *relatively* strongest at, against the field.

        **Not the largest raw component**, which is what this returned first and which was
        measuring the ceilings rather than the world: at seed almost every power came back
        "standing", because standing runs 0.5-0.65 for everybody while ground runs 0.07-0.2
        for everybody. That is a fact about `_FULL`, not about Japan.

        Against the field it means something: the component where this nation is closest to
        the best anyone is managing. ``best`` is the per-component maximum across the whole
        table -- :meth:`Standing.leading_on` passes it, and without it this falls back to
        the raw comparison, which is only right for a table of one.
        """
        if not best:
            return max(self.components.items(), key=lambda kv: kv[1])[0]
        return max(
            self.components.items(),
            key=lambda kv: kv[1] / best[kv[0]] if best.get(kv[0]) else 0.0,
        )[0]


@dataclass(frozen=True, slots=True)
class Standing:
    """The whole table, ordered, with each nation's share of the world's ground."""

    scores: tuple[Score, ...]
    ground_share: dict[str, float]

    @property
    def best(self) -> dict[str, float]:
        """The highest anyone is managing, per component. What ``leading_on`` needs to
        answer "strongest at" rather than "largest number"."""
        return {
            component: max((s.components[component] for s in self.scores), default=0.0)
            for component in WEIGHTS
        }

    def leading_on(self, nation_id: str) -> str:
        """What one nation is relatively strongest at, measured against the whole table."""
        for score in self.scores:
            if score.nation_id == nation_id:
                return score.leading_on(self.best)
        return ""

    def rank_of(self, nation_id: str) -> int:
        """1-based. ``0`` for a nation that is not in the table at all."""
        for index, score in enumerate(self.scores, start=1):
            if score.nation_id == nation_id:
                return index
        return 0


@dataclass(slots=True)
class VictoryReport:
    assessed: int = 0
    #: ``(nation name, condition id)`` for every condition met this turn.
    achieved: list[tuple[str, str]] = field(default_factory=list)
    #: The sentences the turn report shows.
    headlines: list[str] = field(default_factory=list)


def _fraction(value: float, full: float) -> float:
    """A component as a share of its ceiling, clamped to 0-1."""
    return max(0.0, min(1.0, value / full)) if full else 0.0


def score_of(conn: sqlite3.Connection, world_id: str, nation_id: str) -> Score | None:
    """One nation's score, computed from live state. ``None`` if it has no nation row.

    Organisations are scored like anybody else and come out near zero, which is correct:
    a body holds no ground, no army and no economy. Nothing filters them out, because a
    filter is a thing that can be wrong and an honest zero cannot.
    """
    row = conn.execute(
        "SELECT id, name, economy, industry, military_strength, legitimacy"
        "  FROM nation WHERE world_id = ? AND id = ? AND is_alive = 1",
        (world_id, nation_id),
    ).fetchone()
    if row is None:
        return None

    warheads = conn.execute(
        "SELECT COALESCE(SUM(warheads), 0) AS n FROM arsenal"
        " WHERE world_id = ? AND nation_id = ?", (world_id, nation_id),
    ).fetchone()
    # Regard is the *average* of what others think, not the sum, so a nation is not
    # rewarded for the size of the map it happens to share.
    regard = conn.execute(
        "SELECT AVG(score) AS avg FROM relation WHERE world_id = ? AND nation_b = ?",
        (world_id, nation_id),
    ).fetchone()
    seats = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(is_permanent), 0) AS perm"
        "  FROM organisation_member WHERE nation_id = ?", (nation_id,),
    ).fetchone()
    known = conn.execute(
        "SELECT COUNT(*) AS n FROM technology WHERE world_id = ? AND nation_id = ?",
        (world_id, nation_id),
    ).fetchone()

    return _score_from(
        row,
        ground=ground_value(conn, world_id, nation_id),
        warheads=float(warheads["n"]),
        regard=float(regard["avg"] or 0.0),
        seats=float(seats["n"]), permanent=float(seats["perm"]),
        technologies=float(known["n"]),
    )


def _score_from(row, *, ground: float, warheads: float, regard: float,
                seats: float, permanent: float, technologies: float) -> Score:
    """Turn one nation's readings into a :class:`Score`.

    **The formula lives here and nowhere else.** :func:`score_of` reads for one nation
    and :func:`standings` reads for all of them in six grouped statements, and the whole
    value of the second is lost the moment it computes a slightly different number than
    the first. So neither of them does arithmetic; they only fetch.
    """
    return Score(
        nation_id=str(row["id"]), name=str(row["name"]),
        ground=round(_fraction(ground, _FULL["ground"]), 4),
        wealth=round(
            _fraction(float(row["economy"]), _FULL["economy"]) * 0.6
            + _fraction(float(row["industry"]), _FULL["industry"]) * 0.4, 4),
        arms=round(
            _fraction(float(row["military_strength"]), _FULL["military"]) * 0.75
            + _fraction(warheads, _FULL["warheads"]) * 0.25, 4),
        standing=round(max(0.0, min(1.0,
            float(row["legitimacy"]) / 100.0 * 0.45
            + ((regard + 100.0) / 200.0) * 0.35
            + min(1.0, seats * 0.5 + permanent * 0.5) * 0.20
        )), 4),
        knowledge=round(_fraction(technologies, _FULL["technologies"]), 4),
    )


def world_regard(conn: sqlite3.Connection, world_id: str, nation_id: str) -> float:
    """What every other power thinks of this one, averaged.

    The same reading `score_of` takes for the standing component, lifted out so the concert
    condition and the score cannot drift about what "the world's regard" means.
    """
    row = conn.execute(
        "SELECT AVG(score) AS avg FROM relation WHERE world_id = ? AND nation_b = ?",
        (world_id, nation_id)).fetchone()
    return float(row["avg"] or 0.0) if row else 0.0


def _tally(conn: sqlite3.Connection, sql: str, params: tuple, *fields: str) -> dict:
    """``nation_id -> reading`` for one grouped aggregate. Absent means the nation had
    no rows to aggregate, which is what the per-nation query's COALESCE says too."""
    if len(fields) == 1:
        return {str(row["nid"]): float(row[fields[0]] or 0.0)
                for row in conn.execute(sql, params) if row["nid"] is not None}
    return {str(row["nid"]): tuple(float(row[f] or 0.0) for f in fields)
            for row in conn.execute(sql, params) if row["nid"] is not None}


def standings(conn: sqlite3.Connection, world_id: str) -> Standing:
    """Every living nation, best first. One pass, because a leaderboard is read whole.

    **Six statements, not seven per nation.** This called :func:`score_of` in a loop, and
    `score_of` is six queries; on the 204-nation Modern Day world that is ~1,430 SQLite
    statements to draw one screen, and 65 ms of the 169 ms the Strategy panel took to
    open. Every one of those queries is an aggregate over a table already being scanned
    for the nation next to it, so they group.

    The arithmetic is not repeated here -- :func:`_score_from` does it for both paths, so
    the fast one cannot come to a different answer than the slow one.
    """
    nations = conn.execute(
        "SELECT id, name, economy, industry, military_strength, legitimacy"
        "  FROM nation WHERE world_id = ? AND is_alive = 1 ORDER BY id",
        (world_id,),
    ).fetchall()

    values = ground_values(conn, world_id)
    warheads = _tally(
        conn,
        "SELECT nation_id AS nid, COALESCE(SUM(warheads), 0) AS n FROM arsenal"
        "  WHERE world_id = ? GROUP BY nation_id", (world_id,), "n")
    regard = _tally(
        conn,
        "SELECT nation_b AS nid, AVG(score) AS avg FROM relation"
        "  WHERE world_id = ? GROUP BY nation_b", (world_id,), "avg")
    # No world filter, because the per-nation query has none either: membership is keyed
    # on the nation alone.
    seats = _tally(
        conn,
        "SELECT nation_id AS nid, COUNT(*) AS n,"
        "       COALESCE(SUM(is_permanent), 0) AS perm"
        "  FROM organisation_member GROUP BY nation_id", (), "n", "perm")
    known = _tally(
        conn,
        "SELECT nation_id AS nid, COUNT(*) AS n FROM technology"
        "  WHERE world_id = ? GROUP BY nation_id", (world_id,), "n")

    scores = []
    for row in nations:
        nation_id = str(row["id"])
        held, permanent = seats.get(nation_id, (0.0, 0.0))
        scores.append(_score_from(
            row,
            ground=values.get(nation_id, 0.0),
            warheads=warheads.get(nation_id, 0.0),
            regard=regard.get(nation_id, 0.0),
            seats=held, permanent=permanent,
            technologies=known.get(nation_id, 0.0),
        ))
    scores.sort(key=lambda s: (-s.total, s.nation_id))

    # **Read once per nation, not twice.** `ground_value` is a query per nation and this
    # asked for every one of them a second time to divide by a total it had just summed.
    # Profiled: 248,017 calls to it across a twelve-turn run, and `standings` is 27 of the
    # 165 seconds. Identical arithmetic, half the queries.
    held_ground = {score.nation_id: values.get(score.nation_id, 0.0) for score in scores}
    total_ground = sum(held_ground.values()) or 1.0
    shares = {nation_id: value / total_ground
              for nation_id, value in held_ground.items()}
    return Standing(scores=tuple(scores), ground_share=shares)


def _already_marked(
    conn: sqlite3.Connection, world_id: str, nation_id: str, condition: str, turn: int
) -> bool:
    """Whether this nation was already credited with this condition on this turn.

    Guards the one thing that would make the event log a bad place to keep this: a turn
    engine that assessed twice would congratulate twice.
    """
    row = conn.execute(
        "SELECT 1 FROM event WHERE world_id = ? AND turn = ? AND actor_nation_id = ?"
        "   AND text LIKE ? LIMIT 1",
        (world_id, turn, nation_id, f"%{_MARK}{condition}%"),
    ).fetchone()
    return row is not None


def _marks_of(
    conn: sqlite3.Connection, world_id: str, nation_id: str, condition: str
) -> dict[int, str]:
    """``turn -> world date`` for every turn this nation was credited with this condition.

    The date comes along because a sustain window is measured in world time now, and every
    event already carries the date it happened on -- so this needs no new storage, which is
    the property the whole scoring layer is built on.
    """
    return {
        int(row["turn"]): str(row["date"])
        for row in conn.execute(
            "SELECT turn, date FROM event WHERE world_id = ? AND actor_nation_id = ?"
            "   AND text LIKE ?",
            (world_id, nation_id, f"%{_MARK}{condition}%"),
        )
    }


def _held_for(marks: dict[int, str], turn: int, date: str) -> float:
    """How many days of world time the unbroken run ending at ``turn`` covers.

    **Two halves, and both are load-bearing.** The run is unbroken *turns*, because a
    condition that lapsed and came back has not been held; what is measured across it is the
    *calendar*, because a victory must not depend on how the player spends their turns.

    ``0.0`` for a single mark: one observation is a moment, and a moment is exactly what a
    sustain window exists to rule out. A player at yearly pacing therefore cannot win on the
    turn a condition first appears -- which is not a penalty, it is the honest consequence of
    never having looked at the months in between.
    """
    if turn not in marks:
        return 0.0
    earliest = turn
    while (earliest - 1) in marks:
        earliest -= 1
    if earliest == turn:
        return 0.0
    try:
        began = _date.fromisoformat(marks[earliest])
        ended = _date.fromisoformat(date)
    except (TypeError, ValueError):
        # A world with an unreadable date is not a world to raise on: fall back to nothing
        # held, which refuses the victory rather than granting one on bad data.
        return 0.0
    return float((ended - began).days)


def assess_victory(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str
) -> VictoryReport:
    """Record which nations meet a victory condition this turn.

    Engine-authored and derived: nothing here is proposed, and nothing is stored beyond
    the events themselves, so a rewind takes it all back with the turn it belonged to.

    **Meeting a condition does not end the game.** It is recorded and reported, and what to
    do about it is the player's business.
    """
    report = VictoryReport()
    table = standings(conn, world_id)
    if not table.scores:
        return report
    report.assessed = len(table.scores)

    leader = table.scores[0]
    runner_up = table.scores[1].total if len(table.scores) > 1 else 0.0

    met: list[tuple[Score, str]] = []

    # Domination needs a dominant power, singular, and *clear* -- see `_DOMINATION_MARGIN`.
    # Several nations can each hold a quarter of a small map, so congratulating everyone
    # over the line says the opposite of what the condition means, and congratulating
    # whichever of them is a hair ahead says almost as little.
    by_ground = sorted(
        table.scores,
        key=lambda score: (-table.ground_share.get(score.nation_id, 0.0), score.nation_id),
    )
    ground_leader = by_ground[0]
    leader_share = table.ground_share.get(ground_leader.nation_id, 0.0)
    ground_runner_up = (
        table.ground_share.get(by_ground[1].nation_id, 0.0) if len(by_ground) > 1 else 0.0
    )
    tuning = _tuning(conn, world_id, "domination")
    if tuning is not None:
        share_needed = float(tuning.get("share", _DOMINATION_SHARE))
        margin_needed = float(tuning.get("margin", _DOMINATION_MARGIN))
        provinces_needed = int(tuning.get("provinces", _DOMINATION_PROVINCES))
        held = conn.execute(
            "SELECT COUNT(*) AS n FROM region WHERE world_id = ? AND kind <> 'ocean'"
            "   AND COALESCE(controller_nation_id, owner_nation_id) = ?",
            (world_id, ground_leader.nation_id),
        ).fetchone()
        if (
            leader_share >= share_needed
            and leader_share >= ground_runner_up * margin_needed
            and int(held["n"] if held else 0) >= provinces_needed
        ):
            met.append((ground_leader, "domination"))

    if runner_up > 0 and leader.total >= runner_up * _HEGEMONY_MULTIPLE:
        # Written every turn it holds; the condition is *achieved* only once it has held
        # for several consecutive turns, which the log answers without storing anything.
        met.append((leader, "hegemony"))

    by_standing = sorted(table.scores, key=lambda s: -s.standing)
    if (
        len(by_standing) > 1
        and by_standing[0].standing >= by_standing[1].standing * _CONCERT_MARGIN
        # **Comparative and absolute, both.** Leading the order means the world thinks well
        # of you, not merely least badly; see `_CONCERT_REGARD_FLOOR`.
        and world_regard(conn, world_id, by_standing[0].nation_id) >= _CONCERT_REGARD_FLOOR
    ):
        met.append((by_standing[0], "concert"))

    for score, condition in met:
        if _already_marked(conn, world_id, score.nation_id, condition, turn):
            continue
        record_event(
            conn, world_id=world_id, turn=turn, date=date,
            kind=EventKind.NARRATIVE, origin=EventOrigin.ENGINE,
            actor_nation_id=score.nation_id,
            text=f"{_MARK}{condition} {score.name} {_HOLDS[condition]}.",
            visibility=Visibility.PUBLIC,
        )
        if condition in _SUSTAINED:
            marks = _marks_of(conn, world_id, score.nation_id, condition)
            # The event for *this* turn was written a moment ago, so it is already in the
            # run being measured -- which is what makes the window inclusive of today.
            if _held_for(marks, turn, date) < _SUSTAINED[condition]:
                continue
        report.achieved.append((score.name, condition))
        report.headlines.append(f"{score.name} {CONDITIONS[condition]}")

    return report


# ---------------------------------------------------------------------------
# Progress, for an interface rather than for the log
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Requirement:
    """One measurable thing a condition asks for, and how the nation is doing on it."""

    label: str
    have: float
    need: float
    #: How to render the pair. ``share`` is a percentage, ``ratio`` a multiple, ``count`` an
    #: integer.
    unit: str = "ratio"

    @property
    def met(self) -> bool:
        return self.have >= self.need

    @property
    def fraction(self) -> float:
        """How far along, clamped. ``1.0`` means met."""
        if self.need <= 0:
            return 1.0
        return max(0.0, min(1.0, self.have / self.need))


@dataclass(frozen=True, slots=True)
class ConditionProgress:
    """One victory condition, as far as this nation has got with it."""

    condition: str
    #: The sentence from :data:`CONDITIONS`, so an interface never writes its own.
    description: str
    #: ``False`` when the scenario disables this condition -- see :data:`SCENARIO_VICTORY`.
    applies: bool
    requirements: tuple[Requirement, ...] = ()
    #: Consecutive turns this nation has already been credited with the condition. Shown
    #: rather than judged on: a reader watching a run build wants to know it is unbroken.
    turns_held: int = 0
    #: **Days of world time that unbroken run covers**, which is what the verdict is
    #: actually made on. See `_DOMINATION_DAYS` for why it is a duration and not a count.
    days_held: float = 0.0
    #: How much world time it needs. ``0`` for a condition true the moment it is true.
    days_needed: float = 0.0

    @property
    def holds_now(self) -> bool:
        """Whether every measurable requirement is met *this turn*."""
        return bool(self.requirements) and all(r.met for r in self.requirements)

    @property
    def achieved(self) -> bool:
        return self.holds_now and self.days_held >= self.days_needed

    @property
    def fraction(self) -> float:
        """**The weakest link, not the average.** A power at 90% of the ground share and 10%
        of the margin has not nearly won; it is a long way off, and averaging the two would
        say otherwise."""
        if not self.applies or not self.requirements:
            return 0.0
        return min(r.fraction for r in self.requirements)


def _consecutive_run(marks: dict[int, str], turn: int) -> int:
    """How many turns up to and including ``turn`` are marked, unbroken.

    Kept for the progress screen, which is about *how close* rather than about the verdict:
    a reader watching a run build wants to know it is unbroken, and the days it covers are
    reported beside it.
    """
    run = 0
    while turn - run in marks:
        run += 1
    return run


def victory_progress(
    conn: sqlite3.Connection,
    world_id: str,
    nation_id: str,
    *,
    turn: int,
    table: Standing | None = None,
) -> list[ConditionProgress]:
    """How close one nation is to each victory condition. Read-only, and writes nothing.

    **A view over :func:`assess_victory`, not a second copy of it.** Every threshold, every
    per-scenario override and every sustained-turn count is read from the same constants and
    the same :func:`_tuning` the assessment uses, so the two cannot drift into disagreeing
    about what winning is -- which is the failure mode of every progress bar bolted onto a
    rule it does not share.

    What it adds is the *distance*. ``assess_victory`` answers achieved or not, because that
    is what belongs in a permanent log; a player wants to know how far off they are and on
    which of the several things a condition asks for. Hence :class:`Requirement`, one per
    measurable clause, rather than a single percentage that hides which clause is the wall.

    ``table`` may be passed in when the caller already has it, since a screen showing the
    standings and the progress together should not compute the standings twice.
    """
    table = table if table is not None else standings(conn, world_id)
    if not table.scores:
        return [
            ConditionProgress(condition=name, description=CONDITIONS[name], applies=False)
            for name in CONDITIONS
        ]

    # The world's own date, because a sustain window is world time now and the caller of a
    # progress screen has no reason to be holding one.
    stamp = conn.execute(
        "SELECT world_date FROM world WHERE id = ?", (world_id,)).fetchone()
    today = str(stamp["world_date"]) if stamp else ""

    out: list[ConditionProgress] = []
    mine = next((s for s in table.scores if s.nation_id == nation_id), None)
    ordered = sorted(table.scores, key=lambda s: -s.total)
    best_other = next((s.total for s in ordered if s.nation_id != nation_id), 0.0)

    # -- domination: a share of the world's ground, a gap to the next power, and a real
    #    number of provinces. The only condition a scenario may switch off entirely.
    tuning = _tuning(conn, world_id, "domination")
    if tuning is None:
        out.append(ConditionProgress(
            condition="domination", description=CONDITIONS["domination"], applies=False))
    else:
        share = table.ground_share.get(nation_id, 0.0)
        others = sorted(
            (v for k, v in table.ground_share.items() if k != nation_id), reverse=True)
        next_share = others[0] if others else 0.0
        held = conn.execute(
            "SELECT COUNT(*) AS n FROM region WHERE world_id = ? AND kind <> 'ocean'"
            "   AND COALESCE(controller_nation_id, owner_nation_id) = ?",
            (world_id, nation_id),
        ).fetchone()
        margin_needed = float(tuning.get("margin", _DOMINATION_MARGIN))
        # A world with nobody else on it is dominated by whoever is on it, which is the
        # same answer `assess_victory` gives when the runner-up share is zero.
        margin_have = (share / next_share) if next_share else (
            margin_needed if share > 0 else 0.0)
        out.append(ConditionProgress(
            condition="domination",
            description=CONDITIONS["domination"],
            applies=True,
            requirements=(
                Requirement("share of world ground", share,
                            float(tuning.get("share", _DOMINATION_SHARE)), "share"),
                Requirement("times the next power's ground", margin_have,
                            margin_needed, "ratio"),
                Requirement("provinces held", float(held["n"] if held else 0),
                            float(tuning.get("provinces", _DOMINATION_PROVINCES)), "count"),
            ),
            turns_held=_consecutive_run(
                _marks_of(conn, world_id, nation_id, "domination"), turn),
            days_held=_held_for(
                _marks_of(conn, world_id, nation_id, "domination"), turn, today),
            days_needed=_SUSTAINED["domination"],
        ))

    # -- hegemony: twice the next power overall, held for years.
    total = mine.total if mine else 0.0
    out.append(ConditionProgress(
        condition="hegemony",
        description=CONDITIONS["hegemony"],
        applies=True,
        requirements=(
            Requirement("times the next power's score",
                        (total / best_other) if best_other else 0.0,
                        _HEGEMONY_MULTIPLE, "ratio"),
        ),
        turns_held=_consecutive_run(
            _marks_of(conn, world_id, nation_id, "hegemony"), turn),
        days_held=_held_for(
            _marks_of(conn, world_id, nation_id, "hegemony"), turn, today),
        days_needed=_SUSTAINED["hegemony"],
    ))

    # -- concert: lead on standing alone. The one a nation that never fights can reach.
    by_standing = sorted(table.scores, key=lambda s: -s.standing)
    best_other_standing = next(
        (s.standing for s in by_standing if s.nation_id != nation_id), 0.0)
    out.append(ConditionProgress(
        condition="concert",
        description=CONDITIONS["concert"],
        applies=True,
        requirements=(
            Requirement("times the next power's standing",
                        (mine.standing / best_other_standing)
                        if (mine and best_other_standing) else 0.0,
                        _CONCERT_MARGIN, "ratio"),
            Requirement("the world's regard for you",
                        world_regard(conn, world_id, nation_id),
                        _CONCERT_REGARD_FLOOR, "count"),
        ),
        # Concert holds a run now like the other two, so a power leading the order can see
        # how much of the six months it has behind it rather than only that it is ahead.
        turns_held=_consecutive_run(
            _marks_of(conn, world_id, nation_id, "concert"), turn),
        days_held=_held_for(
            _marks_of(conn, world_id, nation_id, "concert"), turn, today),
        days_needed=_SUSTAINED["concert"],
    ))
    return out
