"""Who is in a position to demand what, read off the war rather than off a model.

**This is the number layer 4 will settle on.** Whether a losing side accepts terms cannot be
the winner's model's call -- that is the same mistake as letting a model decide a battle --
so the engine needs its own read of the bargaining position, computed from the war state and
nothing else. This is that read.

Pure derivation, shaped like :func:`atlas.engine.scoring.victory_progress`: no new table, no
stored number, nothing to rewind and nothing to keep in step. Two worlds in the same state
give the same answer, and asking the question changes nothing.

**Four components, each answering a different question a negotiator would ask.**

``ground``
    Whose home soil is under whose boot. Normalised per side, so taking three provinces
    from a small country counts for more than three from a large one -- which is what it
    is worth at a table.
``force``
    What is left to fight with, as a share of the combined force: ``(mine - theirs) /
    (mine + theirs)``. Three-to-one reads +0.5 and there is no way to reach 1 short of
    total annihilation, which is the right shape -- being ten times stronger is not ten
    times more persuasive than being twice.
``weariness``
    How much more tired of it they are than you, straight from
    :mod:`atlas.engine.wareffort`.
``aims``
    How much of what each side said it was fighting for it has actually got. Only the
    kinds the map can answer are counted -- see :func:`_aims_met` -- because scoring a
    regime change nobody can measure would be inventing.

**Weights are dials, and the sum is bounded.** Each component is in ``[-1, +1]`` and the
weights sum to 1, so leverage is too. Negative means the other side has the whip hand.

**Antisymmetric by construction**, which is a property worth stating because the first
version was not: ``tanh(mine/theirs - 1)`` is not the negative of ``tanh(theirs/mine - 1)``,
so both sides of one war could read as the stronger party at the same time. Every component
is now a difference of two shares, so what one side gains at the table the other loses --
and a test pins it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from .wareffort import effort_between

__all__ = [
    "AIMS_WEIGHT",
    "FORCE_WEIGHT",
    "GROUND_WEIGHT",
    "Leverage",
    "WEARINESS_WEIGHT",
    "leverage_between",
]

#: **Ground and weariness carry the number between them**, and they are level.
#:
#: Ground was 0.40 to weariness's 0.25, and measured against a real war that read wrong: in
#: an attrition war where nothing changes hands, a nineteen-point exhaustion gap moved the
#: position by 0.047 -- inside the "evenly matched" band -- so a visibly more broken country
#: still read as a dead heat. Ground is what a settlement is *made* of; weariness is why
#: anybody signs one, and a country that cannot go on will sign a paper its army did not
#: lose. They belong at the same weight.
GROUND_WEIGHT = 0.38
WEARINESS_WEIGHT = 0.35

#: **What is left to fight with**, which is the threat behind the demand rather than the
#: demand itself -- hence below both of the above.
FORCE_WEIGHT = 0.15

#: **Aims met is the smallest**, deliberately: it says how close each side is to being able
#: to *stop*, which shapes what they will accept more than what they can extract.
AIMS_WEIGHT = 0.12


@dataclass(frozen=True, slots=True)
class Component:
    name: str
    value: float
    weight: float
    because: str = ""

    @property
    def contribution(self) -> float:
        return self.value * self.weight


@dataclass(frozen=True, slots=True)
class Leverage:
    """One side's bargaining position in one war, and why it is what it is."""

    nation_id: str
    enemy_id: str
    position: float = 0.0
    components: tuple[Component, ...] = field(default_factory=tuple)

    @property
    def band(self) -> str:
        """What the position means in words.

        Deliberately wide -- a war is not a scoreboard and a reader given "+0.07, winning"
        would believe it -- but narrower than they were at the edges of *evenly matched*,
        which was +-0.15 and swallowed a forty-point exhaustion gap whole.
        """
        if self.position >= 0.45:
            return "commanding"
        if self.position >= 0.12:
            return "the stronger hand"
        if self.position > -0.12:
            return "evenly matched"
        if self.position > -0.45:
            return "the weaker hand"
        return "beaten"

    def sentence(self, names: dict[str, str] | None = None) -> str:
        """One line a negotiator can act on, built from what actually moved the number."""
        names = names or {}
        enemy = names.get(self.enemy_id, self.enemy_id)
        reasons = [c.because for c in sorted(
            self.components, key=lambda c: -abs(c.contribution)) if c.because][:2]
        opening = {
            "commanding": f"You hold the whip hand against {enemy}",
            "the stronger hand": f"You are the stronger party against {enemy}",
            "evenly matched": f"Neither of you can dictate terms to the other ({enemy})",
            "the weaker hand": f"You are negotiating from weakness against {enemy}",
            "beaten": f"You are beaten against {enemy}",
        }[self.band]
        return f"{opening} ({self.position:+.2f}): " + "; ".join(reasons) + "."


def leverage_between(
    conn: sqlite3.Connection, world_id: str, nation_id: str, enemy_id: str
) -> Leverage:
    """What ``nation_id`` can demand of ``enemy_id``, from the state of their war.

    Deliberately answers for a pair that is no longer at war too: the position at the
    moment of the armistice is exactly what the talks afterwards are about.
    """
    holdings = _occupied_homelands(conn, world_id)
    homelands = _homeland_sizes(conn, world_id)
    strength = _strengths(conn, world_id)

    mine_held = holdings.get((nation_id, enemy_id), 0)      # my home ground, their boot
    theirs_held = holdings.get((enemy_id, nation_id), 0)    # their home ground, my boot
    my_home = homelands.get(nation_id, 0)
    their_home = homelands.get(enemy_id, 0)
    ground = (
        (theirs_held / their_home if their_home else 0.0)
        - (mine_held / my_home if my_home else 0.0)
    )

    mine_strength = max(0.1, strength.get(nation_id, 0.0))
    their_strength = max(0.1, strength.get(enemy_id, 0.0))
    force = (mine_strength - their_strength) / (mine_strength + their_strength)

    my_effort = effort_between(conn, world_id, nation_id, enemy_id)
    their_effort = effort_between(conn, world_id, enemy_id, nation_id)
    my_exhaustion = my_effort.exhaustion if my_effort else 0.0
    their_exhaustion = their_effort.exhaustion if their_effort else 0.0
    weariness = (their_exhaustion - my_exhaustion) / 100.0

    my_aims, my_total = _aims_met(conn, world_id, nation_id, enemy_id)
    their_aims, their_total = _aims_met(conn, world_id, enemy_id, nation_id)
    aims = (
        (my_aims / my_total if my_total else 0.0)
        - (their_aims / their_total if their_total else 0.0)
    )

    components = (
        Component("ground", _clamp(ground), GROUND_WEIGHT, _ground_because(
            theirs_held, mine_held, enemy_id)),
        Component("weariness", _clamp(weariness), WEARINESS_WEIGHT, _weariness_because(
            my_effort, their_effort)),
        Component("force", _clamp(force), FORCE_WEIGHT, _force_because(
            mine_strength, their_strength)),
        Component("aims", _clamp(aims), AIMS_WEIGHT, _aims_because(
            my_aims, my_total, their_aims, their_total)),
    )
    return Leverage(
        nation_id=nation_id, enemy_id=enemy_id,
        position=round(sum(c.contribution for c in components), 4),
        components=components)


# --------------------------------------------------------------------------- the reasons


def _ground_because(theirs_held: int, mine_held: int, enemy_id: str) -> str:
    if theirs_held and mine_held:
        return (f"you hold {theirs_held} of their home province(s) and they hold "
                f"{mine_held} of yours")
    if theirs_held:
        return f"you occupy {theirs_held} of their home province(s)"
    if mine_held:
        return f"{enemy_id} occupies {mine_held} of your home province(s)"
    return "neither of you holds any of the other's ground"


def _weariness_because(mine, theirs) -> str:
    if mine is None and theirs is None:
        return ""
    my_band = mine.band if mine else "resolute"
    their_band = theirs.band if theirs else "resolute"
    if my_band == their_band:
        return f"both sides are {my_band}"
    return f"they are {their_band} and you are {my_band}"


def _force_because(mine: float, theirs: float) -> str:
    ratio = mine / theirs
    if ratio >= 1.5:
        return f"your army is {ratio:.1f}x theirs"
    if ratio <= 0.67:
        return f"their army is {1 / ratio:.1f}x yours"
    return "the armies are a near match"


def _aims_because(mine: int, my_total: int, theirs: int, their_total: int) -> str:
    if not my_total and not their_total:
        return ""
    return (f"you have met {mine} of {my_total} measurable aim(s), they {theirs} of "
            f"{their_total}")


# --------------------------------------------------------------------------- the reads


def _aims_met(
    conn: sqlite3.Connection, world_id: str, nation_id: str, enemy_id: str
) -> tuple[int, int]:
    """``(met, measurable)`` of this power's declared aims in this war.

    **Only what the map can answer is counted.** `annex` and `recover_cores` are true when
    the ground is held; `demilitarise` when no enemy formation stands on it; `defend` when
    none of your homeland is under their boot. `regime_change` and `independence` are
    excluded rather than scored zero -- a war aim nobody can measure counting as unmet
    would make every such war read as a failure forever.
    """
    aims = list(conn.execute(
        "SELECT kind, subject_id FROM war_aim"
        " WHERE world_id = ? AND nation_id = ? AND enemy_id = ?"
        " ORDER BY kind, subject_id", (world_id, nation_id, enemy_id)))
    if not aims:
        return 0, 0

    holder = {
        str(row["id"]): str(row["holder"] or "")
        for row in conn.execute(
            "SELECT id, COALESCE(controller_nation_id, owner_nation_id) AS holder"
            "  FROM region WHERE world_id = ?", (world_id,))
    }
    met = measurable = 0
    for row in aims:
        kind, subject = str(row["kind"]), str(row["subject_id"] or "")
        if kind in {"annex", "recover_cores"} and subject:
            measurable += 1
            met += holder.get(subject) == nation_id
        elif kind == "demilitarise" and subject:
            measurable += 1
            met += conn.execute(
                "SELECT COUNT(*) FROM unit WHERE world_id = ? AND region_id = ?"
                "   AND nation_id = ?", (world_id, subject, enemy_id)).fetchone()[0] == 0
        elif kind == "defend":
            measurable += 1
            met += conn.execute(
                "SELECT COUNT(*) FROM region WHERE world_id = ? AND home_nation_id = ?"
                "   AND COALESCE(controller_nation_id, owner_nation_id) = ?",
                (world_id, nation_id, enemy_id)).fetchone()[0] == 0
    return met, measurable


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
        " GROUP BY home, holder", (world_id,)
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
            " GROUP BY home", (world_id,))
    }


def _strengths(conn: sqlite3.Connection, world_id: str) -> dict[str, float]:
    """What each power has left to fight with: its formations, not its reputation.

    ``nation.military_strength`` is the standing estimate and moves slowly; the sum of
    surviving unit strength is what a war actually consumes, which is the thing a
    negotiator is weighing. Falls back to the estimate for a power with no formations
    modelled, so a minor without units is not read as disarmed.
    """
    found = {
        str(row["nation_id"]): float(row["total"])
        for row in conn.execute(
            "SELECT nation_id, SUM(strength) AS total FROM unit"
            " WHERE world_id = ? GROUP BY nation_id", (world_id,))
    }
    for row in conn.execute(
        "SELECT id, military_strength FROM nation WHERE world_id = ?", (world_id,)
    ):
        found.setdefault(str(row["id"]), float(row["military_strength"]))
    return found


def _clamp(value: float) -> float:
    return max(-1.0, min(1.0, value))
