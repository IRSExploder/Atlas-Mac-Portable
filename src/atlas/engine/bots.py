"""Deterministic controllers that answer for a nation, or a whole system, with no model call.

**What this is for.** Every tracked power that is not in a bloc costs one model call a turn,
and so does every gambit and every Security Council session. In the kitchen-sink match that
was about 4.4 calls a round on the GPT pin for neutrals alone -- more than the Eastern bloc
itself. A study that is about how three model families command their blocs does not need a
frontier model deciding what Laos mobilises. A bot answers instead: cheaply, identically on
every replay, and never with a quota to run out.

**A bot writes the same form a model writes, and nothing else.** It produces the JSON a model
would have returned -- structured orders only: build orders, mobilisation, reinforce,
military objectives, a peace offer, a gambit refusal, a council resolution -- and the turn
engine sends that through the *same* `parse_batch`, `apply_batch`, objective, order and
peace gates a model's answer goes through. A bot never writes a raw diff. There is no second
way into the world here, which is the whole reason the policy can be simple: whatever it
asks for, the gates decide.

**Bots run the scenery, never a study subject.** Background neutrals, their economies,
routine council votes -- and nothing a study measures. The engine enforces it rather than
trusting the switches: `TurnEngine._protected` names every bloc nation and every
`TurnEngine.study_subjects` entry (in a betrayal study, the victims), and a protected
nation is asked of its model whatever the scopes and nation lists below say. EVE's bloc
round never consults this module at all. `tests/test_bots_run_the_scenery_only.py` turns
every scope on, names the subjects explicitly, and checks that none of them is botted.

**Off by default, and that is what keeps every study condition where it was.** Nothing here
runs unless ``ATLAS_BOTS`` names a scope (read once at import, like every other staged flag)
or a harness calls :func:`set_bots`. Scopes:

* ``tracked`` -- every tracked power that is not playing in a bloc (the "neutrals");
* ``gambit`` -- the covert-action channel: a bot never schemes;
* ``organisation`` -- Security Council sessions: the mildest proportionate resolution.

and individual nations can be named with ``nations=`` (``ATLAS_BOT_NATIONS``).

**The economy is a module of the nation bot, not a separate call** -- because in the engine
it is not a separate call either. A power's economy is decided inside its own turn (build
orders, mobilisation), and every untracked power's already runs on the deterministic master
AI in `production.autonomous_investment`. The nation bot reuses exactly that heuristic
(`_choose_development`, `_best_investment_region`) and files the result as a build order.
"""

from __future__ import annotations

import os
import sqlite3

__all__ = [
    "BOT_MODEL",
    "bot_nations",
    "bot_roles",
    "controls_nation",
    "controls_role",
    "gambit_decision",
    "nation_decision",
    "organisation_decision",
    "set_bots",
]

#: What the diff log records as the proposer model for a bot's batch, so a bot's answer is
#: never mistaken for a model's in any later count.
BOT_MODEL = "bot:rules-v1"

#: The scopes a harness may turn on.
SCOPES = frozenset({"tracked", "gambit", "organisation"})

#: Outmatched: an army under this share of its strongest enemy's. **On its own this no
#: longer sues for peace** (review v1, point 2b): a minor holding its mountains against a
#: bigger power is not losing, and asking for a truce there was surrender while winning.
#: It sues when it is outmatched *and* has already lost ground.
SUE_FOR_PEACE_BELOW = 0.5

#: Losing this share of its own provinces sues for peace whatever the armies' sizes -- the
#: other half of 2b: a power the same size as its enemy that has lost most of its country
#: used to fight to extinction because the strength ratio never crossed the line.
LOST_GROUND_SUES = 0.3

#: War-weariness at which a power asks for a pause, from `wareffort.effort_between`, the
#: same reading the engine's own `armistice.will_accept` uses to decide a tired side takes
#: one. Fifty: well past the engine's own "tired enough" line, so a bot asks later than
#: the engine would accept.
WEARY_SUES = 50.0

#: Mobilisation is a curve, not a ramp (`mobilisation.LEVELS`), and a bot climbing a rung
#: every war turn bankrupted itself (review v1, point 1). It climbs only with money in the
#: bank and a country that will bear it, and only to total war when its own ground is
#: occupied; below `STAND_DOWN_BELOW` stability or in arrears it steps down.
CLIMB_FROM_STABILITY = 40.0
STAND_DOWN_BELOW = 35.0
PARTIAL_WAR_CEILING = 2

#: Raising a new formation leaves at least this many times its price in the treasury, so a
#: bot does not spend itself into arrears to replace a division (review v1, point 3a).
RAISE_RESERVE = 2.0

#: With the enemy inside its own country the reserve drops to this: a division now is worth
#: more than a cushion (review v4, point 7). "Inside" is the same test that unlocks total
#: war -- one of its own provinces held by an enemy -- so the two emergencies agree.
RAISE_RESERVE_INVADED = 1.2

#: A power that has declared another war this recently is a repeat aggressor, and the
#: council sanctions rather than condemns (review v1, point 5). In world time, two years.
REPEAT_AGGRESSOR_DAYS = 730

#: And only after the war has run this long, so a power does not surrender in the month it
#: was attacked -- the first weeks of a war are the worst time to judge it. **World time,
#: not turns**: the first version counted three turns, which is three months at monthly
#: pacing and three *years* at yearly, and the duration-clock audit refused it for exactly
#: that reason.
WAR_DAYS_BEFORE_PEACE = 90


def _split(raw: str) -> frozenset[str]:
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


_ROLES: frozenset[str] = _split(os.environ.get("ATLAS_BOTS", "")) & SCOPES
_NATIONS: frozenset[str] = _split(os.environ.get("ATLAS_BOT_NATIONS", ""))
#: Nations a bot never plays, whatever the scopes say. **The study needs this**: in the
#: kitchen-sink match the betrayal victims are neutrals, and a rule answering a betrayal
#: would turn a model-vs-model question into a model-vs-script one (review v1, point 6).
_EXCLUDE: frozenset[str] = _split(os.environ.get("ATLAS_BOT_EXCLUDE", ""))


def bot_roles() -> frozenset[str]:
    return _ROLES


def bot_nations() -> frozenset[str]:
    return _NATIONS


def bot_exclusions() -> frozenset[str]:
    return _EXCLUDE


def set_bots(
    roles: set[str] | frozenset[str] = frozenset(),
    nations: set[str] | frozenset[str] = frozenset(),
    exclude: set[str] | frozenset[str] = frozenset(),
) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """Move the switches, returning what they were so a caller can put them back."""
    global _ROLES, _NATIONS, _EXCLUDE
    unknown = set(roles) - SCOPES
    if unknown:
        raise ValueError(f"unknown bot scope(s) {sorted(unknown)}; choose from {sorted(SCOPES)}")
    was = (_ROLES, _NATIONS, _EXCLUDE)
    _ROLES, _NATIONS, _EXCLUDE = frozenset(roles), frozenset(nations), frozenset(exclude)
    return was


def controls_nation(nation_id: str) -> bool:
    """Whether this tracked power's turn is a bot's rather than a model's."""
    if nation_id in _EXCLUDE:
        return False
    return "tracked" in _ROLES or nation_id in _NATIONS


def controls_role(role: str) -> bool:
    return role in _ROLES


# ------------------------------------------------------------------------ the nation bot


def _at_war_with(conn: sqlite3.Connection, world_id: str, nation_id: str) -> list[str]:
    return [str(r[0]) for r in conn.execute(
        "SELECT nation_b FROM relation WHERE world_id = ? AND nation_a = ? AND at_war = 1"
        " ORDER BY nation_b", (world_id, nation_id))]


def _holder_map(conn: sqlite3.Connection, world_id: str) -> dict[str, tuple[str, str]]:
    """region -> (owner, holder), land only."""
    return {str(r["id"]): (str(r["owner"] or ""), str(r["holder"] or "")) for r in conn.execute(
        "SELECT id, owner_nation_id AS owner,"
        "       COALESCE(controller_nation_id, owner_nation_id) AS holder"
        "  FROM region WHERE world_id = ? AND kind <> 'ocean'", (world_id,))}


def _land_neighbours(conn: sqlite3.Connection, world_id: str) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for a, b in conn.execute(
        "SELECT region_a, region_b FROM region_adjacency WHERE world_id = ?"
        "   AND crossing = 'land'", (world_id,)):
        out.setdefault(str(a), set()).add(str(b))
        out.setdefault(str(b), set()).add(str(a))
    return out


def _strength(conn: sqlite3.Connection, world_id: str, nation_id: str) -> float:
    row = conn.execute("SELECT military_strength FROM nation WHERE world_id = ? AND id = ?",
                       (world_id, nation_id)).fetchone()
    return float(row[0]) if row is not None and row[0] is not None else 0.0


def _believed_strength(conn: sqlite3.Connection, world_id: str, viewer: str,
                       target: str, *, turn: int) -> float:
    """What ``viewer``'s own service believes ``target``'s army is -- never the row.

    **The first version read the truth, and a model never does.** `visibility.other_nations`
    gives a model the midpoint of its service's estimate, coarsened to a band; the bot read
    `military_strength` itself and so decided when to sue for peace on information no model
    had. Found while checking a methodology claim in Gemini's review v6 that bots "read the
    identical briefing" -- the gates police what a batch *writes*, not what its author read.
    """
    from ..state.visibility import _believed

    return _believed(conn, world_id, viewer, target, _strength(conn, world_id, target), turn)


def _war_began(conn: sqlite3.Connection, world_id: str, a: str, b: str) -> str | None:
    """The date of the latest declaration between the two, either way round."""
    row = conn.execute(
        "SELECT MAX(e.date) FROM event e JOIN event_participant p ON p.event_id = e.id"
        " WHERE e.world_id = ? AND e.kind = 'war_declared'"
        "   AND ((e.actor_nation_id = ? AND p.nation_id = ?)"
        "     OR (e.actor_nation_id = ? AND p.nation_id = ?))",
        (world_id, a, b, b, a)).fetchone()
    return str(row[0])[:10] if row is not None and row[0] is not None else None


def _war_days(conn: sqlite3.Connection, world_id: str, began: str | None) -> int | None:
    """How long ago, in world days, ``began`` was; ``None`` if either date is unknown."""
    from datetime import date as _date

    now = conn.execute("SELECT world_date FROM world WHERE id = ?", (world_id,)).fetchone()
    try:
        return (_date.fromisoformat(str(now[0])[:10]) - _date.fromisoformat(began)).days
    except (TypeError, ValueError):
        return None


def _reason(conn: sqlite3.Connection, nation_id: str, enemies: list[str]) -> int | None:
    """An event this power may cite for its war plans: the newest it can see that names an
    enemy, else the newest it can see at all. Chosen from what `visibility` says it knows,
    so CORE-001 has nothing to refuse."""
    from ..state.visibility import known_events

    known = known_events(conn, nation_id, limit=60)
    for event in known:
        if str(event.get("actor_nation_id") or "") in enemies:
            return int(event["id"])
    return int(known[0]["id"]) if known else None


def _can_reinforce(conn: sqlite3.Connection, world_id: str, nation_id: str) -> bool:
    from .orders import replaceable_now

    row = conn.execute("SELECT treasury, manpower FROM nation WHERE world_id = ? AND id = ?",
                       (world_id, nation_id)).fetchone()
    if row is None or float(row["treasury"] or 0) <= 0 or float(row["manpower"] or 0) <= 0:
        return False
    return replaceable_now(conn, world_id, nation_id).formations > 0


def _economy(conn: sqlite3.Connection, world_id: str, nation_id: str) -> list[dict]:
    """One build order from the master AI's own heuristic, or none."""
    from .production import (
        _best_investment_region,
        _choose_development,
        in_progress,
        industrial_capacity,
    )

    if in_progress(conn, world_id, nation_id) >= industrial_capacity(conn, nation_id):
        return []
    nation = conn.execute(
        "SELECT id, name, economy, industry, treasury, manpower, stability FROM nation"
        " WHERE world_id = ? AND id = ?", (world_id, nation_id)).fetchone()
    if nation is None:
        return []
    choice = _choose_development(conn, world_id, nation)
    if choice is None or choice[0] == "bank":
        return []
    # **Research is a build order**: the order handler resolves a programme id before a
    # project id (`orders.py`, the `tech_ref` branch). The first version said otherwise in
    # this comment and dropped every research choice, so a bot that grew rich enough to
    # research stood idle instead (review v1, point 3b).
    if choice[0] == "research":
        return [{"project": choice[1]}]
    region = _best_investment_region(conn, world_id, nation_id)
    # A power that holds no ground has nowhere to build; the order would be refused.
    if region is None:
        return []
    return [{"project": choice[1], "region": region}]


def _raise(conn: sqlite3.Connection, world_id: str, nation_id: str, *,
           invaded: bool = False) -> list[dict]:
    """One formation of the cheapest land kind, if the treasury can pay for it twice over.

    Before this a bot could only mend the army it started with: a formation destroyed was
    gone for the match, and an attrition war disarmed a bot minor (review v1, point 3a).
    Priced from `unit_template` like the order handler, falling back to its defaults.
    """
    from .orders import _UNIT_MANPOWER_COST, _UNIT_TREASURY_COST

    row = conn.execute("SELECT treasury, manpower FROM nation WHERE world_id = ? AND id = ?",
                       (world_id, nation_id)).fetchone()
    if row is None:
        return []
    cheapest = conn.execute(
        "SELECT kind, cost_treasury, cost_manpower FROM unit_template"
        " WHERE world_id = ? AND COALESCE(domain, 'land') = 'land'"
        " ORDER BY cost_treasury, kind LIMIT 1", (world_id,)).fetchone()
    kind = str(cheapest["kind"]) if cheapest else "infantry"
    price = float(cheapest["cost_treasury"]) if cheapest else _UNIT_TREASURY_COST
    men = float(cheapest["cost_manpower"]) if cheapest else _UNIT_MANPOWER_COST
    reserve = RAISE_RESERVE_INVADED if invaded else RAISE_RESERVE
    if float(row["treasury"] or 0) < reserve * price or float(row["manpower"] or 0) < men:
        return []
    return [{"unit_kind": kind, "quantity": 1}]


def _stability_and_treasury(conn, world_id: str, nation_id: str) -> tuple[float, float]:
    row = conn.execute("SELECT stability, treasury FROM nation WHERE world_id = ? AND id = ?",
                       (world_id, nation_id)).fetchone()
    if row is None:
        return 0.0, 0.0
    return float(row["stability"] or 0), float(row["treasury"] or 0)


def _interleave(groups: list[list[str]], limit: int) -> list[str]:
    """Round-robin across fronts, so one front's alphabet cannot starve another's."""
    out: list[str] = []
    for depth in range(max((len(g) for g in groups), default=0)):
        for group in groups:
            if depth < len(group) and len(out) < limit:
                out.append(group[depth])
    return out


def nation_decision(
    conn: sqlite3.Connection, world_id: str, nation_id: str, *, turn: int,
) -> dict:
    """What a rules-driven government does with its month, as a tracked-agent answer.

    At peace: build, mend the army, and stand down a step at a time. At war: climb the
    mobilisation ladder only while it can pay and the country will bear it; reinforce and
    raise what it can afford; hold every province facing each enemy and try to recover its
    own; and ask for a ceasefire when it is actually losing -- ground gone, or tired -- not
    merely smaller. Deterministic: the same board gives the same answer.

    Revised after Gemini's review v1 (2026-09-22); the points are cited where they landed.
    """
    from ..state.diff import OBJECTIVE_LIMIT
    from .armistice import fighting_paused
    from .mobilisation import MAX_LEVEL, level_of
    from .wareffort import effort_between

    enemies = _at_war_with(conn, world_id, nation_id)
    level = level_of(conn, world_id, nation_id)
    stability, treasury = _stability_and_treasury(conn, world_id, nation_id)
    answer: dict = {
        "narrative": "",
        "diffs": [],
        "events": [],
        "build_orders": _economy(conn, world_id, nation_id),
    }
    # Mending the army is not a war measure: casualties from the last war stay on the
    # books into the peace unless somebody pays to replace them (review v1, point 4).
    if _can_reinforce(conn, world_id, nation_id):
        answer["reinforce"] = True
    if not enemies:
        if level > 0:
            answer["mobilisation"] = level - 1
        return answer

    holders = _holder_map(conn, world_id)
    neighbours = _land_neighbours(conn, world_id)
    mine = sorted(r for r, (_o, h) in holders.items() if h == nation_id)
    owned = [r for r, (o, _h) in holders.items() if o == nation_id]
    recover_by = [sorted(r for r, (o, h) in holders.items() if o == nation_id and h == e)
                  for e in enemies]
    hold_by = [[r for r in mine if any(holders.get(n, ("", ""))[1] == e
                                       for n in neighbours.get(r, ()))] for e in enemies]
    recover = [r for group in recover_by for r in group]
    lost_share = len(recover) / len(owned) if owned else 0.0

    # --- the ladder
    if treasury <= 0 or stability < STAND_DOWN_BELOW:
        if level > 0:
            answer["mobilisation"] = level - 1
    elif stability >= CLIMB_FROM_STABILITY:
        ceiling = MAX_LEVEL if recover else min(MAX_LEVEL, PARTIAL_WAR_CEILING)
        if level < ceiling:
            answer["mobilisation"] = level + 1

    raised = _raise(conn, world_id, nation_id, invaded=bool(recover))
    if raised:
        answer["raise_formations"] = raised

    # --- the fronts, enemy by enemy (review v1, point 4)
    push = _interleave(recover_by, OBJECTIVE_LIMIT)
    hold = [r for r in _interleave(hold_by, OBJECTIVE_LIMIT) if r not in push]
    if not any(hold_by):
        # A war with no land front -- across water. Hold the coast and the capital, which
        # is where a landing would come; a hold on your own province stands without a
        # border (`objectives._refusal`).
        coast = [str(r[0]) for r in conn.execute(
            "SELECT id FROM region WHERE world_id = ? AND kind = 'coastal'"
            "   AND COALESCE(controller_nation_id, owner_nation_id) = ?"
            # Ports before barren coast (review v4, point 8).
            " ORDER BY development DESC, id",
            (world_id, nation_id))]
        capital = conn.execute("SELECT capital_region_id FROM nation WHERE world_id = ?"
                               " AND id = ?", (world_id, nation_id)).fetchone()
        seat = [str(capital[0])] if capital and capital[0] in mine else []
        hold = [r for r in dict.fromkeys(seat + coast) if r not in push]
    hold = hold[:max(0, OBJECTIVE_LIMIT - len(push))]
    reason = _reason(conn, nation_id, enemies)
    if reason is not None and (hold or push):
        answer["military_objectives"] = {
            "push": push, "hold": hold, "withdraw_from": [], "reason_event_id": reason}

    # --- asking for a pause (review v1, point 2b)
    ours = _strength(conn, world_id, nation_id)  # its own army it knows exactly, as a model does
    seen = {e: _believed_strength(conn, world_id, nation_id, e, turn=turn) for e in enemies}
    for enemy in sorted(enemies, key=lambda e: (-seen[e], e)):
        age = _war_days(conn, world_id, _war_began(conn, world_id, nation_id, enemy))
        if age is not None and age < WAR_DAYS_BEFORE_PEACE:
            continue
        if fighting_paused(conn, world_id, nation_id, enemy):
            continue
        effort = effort_between(conn, world_id, nation_id, enemy)
        weary = effort is not None and effort.exhaustion >= WEARY_SUES
        lost_to_them = sum(1 for _r, (o, h) in holders.items() if o == nation_id and h == enemy)
        outmatched = ours < SUE_FOR_PEACE_BELOW * seen[enemy]
        # **Two provinces, not one** (review v2): one border bite at three to one left the
        # aggressor at about +0.15 leverage, far under `armistice.REFUSE_ABOVE`, so the
        # engine took the truce and froze the bite. A second loss is a war being lost.
        if lost_share >= LOST_GROUND_SUES or weary or (outmatched and lost_to_them >= 2):
            answer["peace_offer"] = {"against": enemy, "kind": "ceasefire"}
            break
    return answer


# ------------------------------------------------------------------------ the systems


def gambit_decision() -> dict:
    """A bot does not scheme. The refusal is a real answer the gambit channel accepts."""
    return {"attempt": None, "refused": "This government does not undertake covert action."}


def organisation_decision(
    conn: sqlite3.Connection | None = None, world_id: str = "", accused: str = "",
    *, date: str = "",
) -> dict:
    """Condemn; sanction a repeat aggressor.

    A session is only convened when a trigger exists, so "none" would be a body ignoring
    the thing it was convened for. The first version always condemned, and Gemini's review
    v1 (point 5) was right that a council that only ever condemns is one every aggressor
    can price in. Sanction when the accused has declared another war within
    `REPEAT_AGGRESSOR_DAYS` before this one; `demand_ceasefire` and `authorise_force` stay
    with the models -- escalating to a demand on a rule is a bigger judgement than this
    layer should make.
    """
    if conn is not None and accused and _repeat_aggressor(conn, world_id, accused, date):
        return {"action": "sanction", "justification":
                "The body sanctions a government that has made war again.", "refused": None}
    return {"action": "condemn", "justification": "The body condemns the act.",
            "refused": None}


def _repeat_aggressor(conn: sqlite3.Connection, world_id: str, accused: str,
                      date: str) -> bool:
    from datetime import date as _date
    from datetime import timedelta

    # Declarations and breaches both (review v4, point 9) -- but by **distinct date**: a
    # war on a treaty partner writes a `war_declared` and a `treaty_broken` on the same
    # day, and counting events would sanction a first offence as a repeat.
    dates = sorted({str(r[0])[:10] for r in conn.execute(
        "SELECT date FROM event WHERE world_id = ? AND actor_nation_id = ?"
        "   AND kind IN ('war_declared', 'treaty_broken')", (world_id, accused))},
        reverse=True)
    if len(dates) < 2:
        return False
    try:
        newest, before = _date.fromisoformat(dates[0]), _date.fromisoformat(dates[1])
    except ValueError:
        return False
    return newest - before <= timedelta(days=REPEAT_AGGRESSOR_DAYS)
