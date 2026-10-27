"""Five defects a bug scout found in the recently-added systems, one section each.

Nothing here is a balance argument. Every one of these is a place where a system's own
words -- a docstring, a briefing line, a column it writes -- said one thing and its code
did another, and the gap was invisible because both halves looked reasonable on their own:

* **N1** `deterrence` hand-built an `Arsenal` without the readiness column, so a rotted
  deterrent deterred exactly as well as an exercised one, and the pressure term disagreed
  with the number the briefing prints from `surviving_warheads`.
* **N2** a warhead's build id encoded the turn and no repeat ordinal, so the *second* one
  of a kind ordered in a turn collided with the first and took the whole batch down with
  it. A cap of one strategic and one tactical warhead per nation per turn, enforced by an
  id rather than by a rule and written down nowhere.
* **S1** `commit_plan` documents `major_shock` / `milestone_reached` / `commitment_cost`
  and the plan carries a `replan_on` list of them, and `review_world` never looked at any
  of it. A government whose country was overrun in March kept ordering its January plan
  until the following January.
* **W1** `may_declare` advertised targets that WAR-001 then refused, because the two were
  asking different questions -- warpath about a grievance the *world* would accept, the
  rule about one the *engine* had recorded.
* **W2** retaking your own occupied provinces was priced as unprovoked aggression, and
  the briefing that offered the target never said what it cost.

Each section asserts the behaviour that was wrong, in the terms the system states it in,
so a regression reads as a contradiction rather than as a number moving.
"""

from __future__ import annotations

import sqlite3

import pytest

from atlas.engine.nuclear import deterrence, surviving_warheads
from atlas.engine.orders import apply_order_effects
from atlas.engine.resolution import (
    ResolutionReport,
    _declare_war,
    declaration_cost,
)
from atlas.engine.strategy import (
    FLOOR,
    REPLAN_GROUND_MOVE,
    Plan,
    commit_plan,
    plan_of,
    replan_trigger,
    review_world,
    revisions_of,
)
from atlas.engine.warheads import TACTICAL_PROJECT, WARHEAD_PROJECT, order_warhead
from atlas.engine.warpath import may_declare, occupied_homeland
from atlas.scenarios.sandbox import sandbox_seed
from atlas.state.bootstrap import create_world

WORLD = "sandbox"
US, THEM = "aurelia", "veyra"


@pytest.fixture
def world() -> sqlite3.Connection:
    connection = create_world(":memory:", sandbox_seed())
    connection.execute("UPDATE world SET turn_number = 1 WHERE id = ?", (WORLD,))
    connection.commit()
    yield connection
    connection.close()


def _home_region(conn: sqlite3.Connection, nation: str = US) -> str:
    row = conn.execute(
        "SELECT id FROM region WHERE world_id = ? AND home_nation_id = ?"
        "   AND kind <> 'ocean' ORDER BY id LIMIT 1", (WORLD, nation)).fetchone()
    return str(row["id"])


# ------------------------------------------------ N1: a deterrent nobody has exercised


def _arm(conn: sqlite3.Connection, *, readiness: float, warheads: int = 16) -> None:
    """Sixteen missiles, deliberately.

    Small enough that the count still moves the term: `_DETERRENCE_CEILING` is 0.18 and a
    survivor is worth 0.015, so any arsenal above a dozen survivors is already at the
    ceiling and *nothing* below it can be seen. A hundred warheads reads 0.18 at every
    readiness there is, which is how a test could have been written that passed against
    the bug.
    """
    conn.execute("DELETE FROM arsenal WHERE world_id = ?", (WORLD,))
    conn.execute(
        "INSERT INTO arsenal (world_id, nation_id, warheads, delivery, tactical, readiness)"
        " VALUES (?, ?, ?, 'missile', 0, ?)", (WORLD, US, warheads, readiness))
    conn.commit()


def test_a_deterrent_nobody_has_exercised_deters_less(world):
    """The defect, stated as the thing it made impossible.

    Readiness is the one column that says a force is *kept up*, `drift_readiness` moves it
    every turn for every arsenal, and letting it rot was supposed to be a real cost. It was
    not: `deterrence` built its own `Arsenal` and left readiness at the dataclass default,
    so the same sixteen missiles bought the same 0.145 whether they had been exercised for
    a decade or abandoned for one.
    """
    region = _home_region(world)

    _arm(world, readiness=1.0)
    exercised = deterrence(world, US, region)
    _arm(world, readiness=0.25)
    rotted = deterrence(world, US, region)

    assert exercised > 0.0, "a live missile force has to deter something"
    assert rotted < exercised, (
        "readiness has to reach deterrence, or letting a deterrent rot costs nothing")


def test_deterrence_and_the_briefing_count_the_same_arsenal(world):
    """**The two halves have to agree**, which is the sharper statement of the same bug.

    `surviving_warheads` is what the map's nuclear panel prints and it reads the column;
    `deterrence` is what the pressure term subtracts and it did not. A government was
    therefore shown one deterrent and defended by another, and no amount of playing would
    have revealed which was which. Asserted as the arithmetic rather than as an
    inequality: the term is survivors x 0.015 plus the missile tier's 0.04.
    """
    region = _home_region(world)
    for readiness in (1.0, 0.5, 0.25):
        _arm(world, readiness=readiness)
        survivors = surviving_warheads(world, WORLD, US)
        assert deterrence(world, US, region) == pytest.approx(
            survivors * 0.015 + 0.04), (
            f"at readiness {readiness} the deterrent and the briefing disagree")


# --------------------------------------------- N2: the second warhead of a turn


def _nuclear(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO arsenal (world_id, nation_id, warheads, delivery)"
        " VALUES (?, ?, 4, 'missile')", (WORLD, US))
    conn.execute(
        "UPDATE nation SET treasury = 5000, industry = 500 WHERE id = ?", (US,))
    conn.commit()


def _builds(conn: sqlite3.Connection) -> list[str]:
    return [str(r["id"]) for r in conn.execute(
        "SELECT id FROM construction WHERE world_id = ? AND nation_id = ? ORDER BY id",
        (WORLD, US))]


def test_a_second_warhead_in_one_turn_is_a_second_warhead(world):
    """The cap nobody wrote down.

    The build id was `c_<nation>_strwarhead_<turn>` and nothing else, so the second order
    of the turn wrote a row that already existed, the insert was refused, and -- because a
    batch is all or nothing -- the charge went with it. A country that ordered three
    strategic warheads laid down one and was told about it in `problems`, which is where a
    staff looks least.
    """
    _nuclear(world)

    first = order_warhead(world, WORLD, nation_id=US, turn=2, date="1897-04-01", repeat=0)
    second = order_warhead(world, WORLD, nation_id=US, turn=2, date="1897-04-01", repeat=1)
    third = order_warhead(world, WORLD, nation_id=US, turn=2, date="1897-04-01", repeat=2)

    assert first.commissioned and second.commissioned and third.commissioned
    assert not (first.problems or second.problems or third.problems)
    assert len(_builds(world)) == 3, "three warheads ordered, three laid down"


def test_the_first_warhead_of_a_turn_keeps_the_id_it_always_had(world):
    """**A fix that renamed the first build would move every fingerprint** to solve a
    problem the first build never had. The ordinal is suffixed from the second on."""
    _nuclear(world)
    order_warhead(world, WORLD, nation_id=US, turn=2, date="1897-04-01")
    order_warhead(world, WORLD, nation_id=US, turn=2, date="1897-04-01", repeat=1)

    assert _builds(world) == [f"c_{US}_strwarhead_2", f"c_{US}_strwarhead_2_2"]


def test_the_two_kinds_still_do_not_collide_with_each_other(world):
    """They never did -- the id carries `str` or `tac` -- and the ordinal must not
    introduce a collision the turn alone did not have."""
    _nuclear(world)
    order_warhead(world, WORLD, nation_id=US, turn=2, date="1897-04-01", tactical=False)
    order_warhead(world, WORLD, nation_id=US, turn=2, date="1897-04-01", tactical=True)

    assert len(_builds(world)) == 2


def test_a_refused_warhead_can_still_say_why(world):
    """Found by reproducing the collision rather than by reading the code.

    The refusal branch read `outcome.problems`, which `ApplyOutcome` does not have, so the
    second warhead of a turn did not fail quietly -- it raised `AttributeError`, out
    through a dispatcher whose `except` clause names `KeyError`, `ValueError`, `TypeError`
    and `sqlite3.Error` and not that one. The collision that reached it is fixed; the path
    is still reachable by any refusal a rule cares to make, and has to be able to speak.
    """
    _nuclear(world)
    order_warhead(world, WORLD, nation_id=US, turn=2, date="1897-04-01", repeat=1)
    # The same ordinal twice: the collision, staged on purpose, because it is the only
    # thing that reaches this branch and reaching it is the point.
    report = order_warhead(world, WORLD, nation_id=US, turn=2, date="1897-04-01", repeat=1)

    assert not report.commissioned
    assert report.problems and all(isinstance(p, str) for p in report.problems)
    assert len(_builds(world)) == 1


def test_the_orders_dispatch_actually_passes_the_ordinal(world):
    """**The wiring, not just the parameter.** `order_warhead` growing a `repeat` argument
    that nothing filled in would be the same bug with a longer signature -- so this goes in
    through the door a player's orders come through and counts what came out."""
    _nuclear(world)
    effects = apply_order_effects(
        world, WORLD, turn=2, date="1897-04-01", nation_id=US,
        actions=[
            {"type": "build_project", "project": WARHEAD_PROJECT, "summary": "one more"},
            {"type": "build_project", "project": WARHEAD_PROJECT, "summary": "and another"},
            {"type": "build_project", "project": TACTICAL_PROJECT, "summary": "and a small one"},
        ])

    assert not effects.problems, effects.problems
    assert len(_builds(world)) == 3


# ------------------------------------------------------ S1: triggers that never fired


def _committed(conn: sqlite3.Connection, *, ground: float, turn: int = 1) -> Plan:
    """A government a year into a plan, with a reading to be a trend against."""
    plan = Plan(nation_id=US, objective="conquest", because="it suits us")
    commit_plan(conn, WORLD, nation_id=US, plan=plan, trigger="first_plan",
                turn=turn, date="1897-01-01",
                seen={"ground": ground, "ratio": 1.0, "index": 0.5})
    conn.commit()
    return plan_of(conn, WORLD, US)


def test_a_crushed_government_does_not_wait_out_the_year(world):
    """The whole complaint, at the level of the loop rather than the predicate.

    `review_world` fired on the calendar and only on the calendar, so a plan adopted in
    January survived the loss of a third of the country in March and went on being the
    thing every briefing framed until the following January.
    """
    held = 40.0
    _committed(world, ground=held)
    # Not the ground it holds now -- the ground it *held when it looked*, which is what a
    # collapse is measured against and what `commit_plan` stores for exactly this.
    world.execute(
        "UPDATE nation_strategy SET seen_ground = ? WHERE world_id = ? AND nation_id = ?",
        (held, WORLD, US))
    world.commit()
    before = len(revisions_of(world, WORLD, US))

    # Two months later, not twelve.
    review_world(world, WORLD, turn=3, date="1897-03-01")

    after = revisions_of(world, WORLD, US)
    assert len(after) > before, "a collapse has to force a look before the annual clock"
    assert after[-1]["trigger"] == "major_shock", (
        "and the record has to say what made it look, not only that it looked")


def test_a_quiet_year_still_waits_for_the_annual_clock(world):
    """**The other half, and the one that makes the layer worth having.** A trigger that
    fired on nothing in particular would turn a standing commitment back into this turn's
    idea, which is the thing the strategy layer exists to stop."""
    _committed(world, ground=40.0)
    world.execute(
        "UPDATE nation_strategy SET seen_ground = ("
        "  SELECT COUNT(*) FROM region WHERE world_id = ? AND kind <> 'ocean'"
        "     AND COALESCE(controller_nation_id, owner_nation_id) = ?)"
        " WHERE world_id = ? AND nation_id = ?", (WORLD, US, WORLD, US))
    world.commit()
    before = len(revisions_of(world, WORLD, US))

    review_world(world, WORLD, turn=3, date="1897-03-01")

    assert len(revisions_of(world, WORLD, US)) == before, (
        "nothing happened; a government that reviews anyway is not committed to anything")


def _plan(**overrides) -> Plan:
    base = {"nation_id": US, "objective": "conquest", "reviewed_on": "1897-01-01",
            "seen_on": "1897-01-01", "seen_ground": 100.0}
    return Plan(**{**base, **overrides})


def test_each_trigger_fires_on_what_its_name_says(world):
    """The vocabulary `commit_plan` documents, one assertion each."""
    plan = _plan()
    collapsed = 100.0 * (1.0 - REPLAN_GROUND_MOVE) - 1.0
    grown = 100.0 * (1.0 + REPLAN_GROUND_MOVE) + 1.0

    assert replan_trigger(plan, ground=collapsed, level=0.9) == "major_shock"
    assert replan_trigger(plan, ground=grown, level=0.9) == "milestone_reached"
    assert replan_trigger(plan, ground=100.0, level=FLOOR - 0.01) == "commitment_cost"
    assert replan_trigger(plan, ground=100.0, level=FLOOR + 0.01) == ""


def test_a_trigger_makes_its_own_move_the_new_baseline(world):
    """**Edge-triggered, which is what stops a trigger from ending the commitment.**

    The comparison is against `seen_ground`, and `commit_plan` rewrites that every time it
    writes a plan -- so a shock that fires is not still firing next turn. A level test
    would re-fire every turn for as long as its condition held, and three reviews in three
    turns spend a `SWITCH_HOLDS` streak that is meant to take three years.
    """
    fired = _plan()
    assert replan_trigger(fired, ground=60.0, level=0.9) == "major_shock"
    # ...and the review it forced stored 60.0 as what this government now looks like.
    assert replan_trigger(_plan(seen_ground=60.0), ground=60.0, level=0.9) == ""

    # `commitment_cost` is a level and not a move, so it has no baseline to reset. It is
    # suppressed by the last thing recorded about this government instead.
    low = _plan()
    assert replan_trigger(low, ground=100.0, level=0.0) == "commitment_cost"
    assert replan_trigger(
        low, ground=100.0, level=0.0, last_trigger="commitment_cost") == ""


def test_a_government_that_stated_its_conditions_gets_those_conditions(world):
    """`replan_on` is what the briefing means by "something you said would force a
    rethink". An empty list is "nothing stated", not "nothing"."""
    only_shock = _plan(replan_on=["major_shock"])
    assert replan_trigger(only_shock, ground=60.0, level=0.9) == "major_shock"
    assert replan_trigger(only_shock, ground=140.0, level=0.9) == "", (
        "a government that named one condition is not held to the others")


def test_a_government_committed_to_nothing_has_nothing_to_reconsider(world):
    """Abstaining is an answer, and a trigger that dragged an abstainer back for a review
    every time its ground moved would review most of the map every turn."""
    assert replan_trigger(_plan(objective=""), ground=1.0, level=0.0) == ""


# ----------------------------------------------- W1: a target offered and then refused


def _hostile(conn: sqlite3.Connection, score: float = -80.0) -> None:
    conn.execute(
        "UPDATE relation SET score = ? WHERE world_id = ? AND nation_a = ? AND nation_b = ?",
        (score, WORLD, US, THEM))
    conn.commit()


def _legitimacy(conn: sqlite3.Connection, value: float) -> None:
    conn.execute("UPDATE nation SET legitimacy = ? WHERE id = ?", (value, US))
    conn.commit()


def test_a_target_the_rule_would_refuse_is_not_offered(world):
    """The two gates asked different questions and only one of them decided.

    Warpath asked whether the *world* would accept a reason -- relations at -80 plainly
    qualify -- and WAR-001 asks whether the aggressor can pay for a war the *engine* has
    recorded no grievance for. A government with nothing left to pay was told it could
    declare, spent the turn declaring, and got "the declaration of war was refused".
    """
    _hostile(world)
    _legitimacy(world, 0.0)

    verdict = may_declare(world, WORLD, aggressor=US, target=THEM, turn=5)
    assert not verdict.allowed
    assert "legitimacy" in verdict.because

    # And the refusal it was predicting is real: the same declaration, actually attempted.
    report = ResolutionReport()
    _declare_war(world, WORLD, turn=5, date="1897-05-01", aggressor=US,
                 target_value=THEM, report=report, justification="we simply dislike them")
    assert report.wars_declared == 0
    assert any("refused" in p for p in report.problems)


def test_a_power_that_can_pay_is_still_offered_the_target(world):
    """**The gate must not have become a wall.** An unprovoked war is a thing this game
    allows and prices; only one it cannot pay for at all is refused."""
    _hostile(world)
    _legitimacy(world, 60.0)

    verdict = may_declare(world, WORLD, aggressor=US, target=THEM, turn=5)
    assert verdict.allowed
    assert verdict.cost == pytest.approx(8.0), "and the price is disclosed with the offer"


# ------------------------------------------- W2: retaking your own country is not a crime


def _occupy(conn: sqlite3.Connection, count: int = 1) -> None:
    """Put `THEM` on ground whose `home_nation_id` says it is ours.

    One province and not both. Aurelia holds exactly two on the sandbox map, so occupying
    everything it has leaves no ground of its own touching anybody and the *reach* gate
    refuses the declaration before the legitimacy one is ever asked -- a war nobody can
    prosecute, correctly refused, and nothing to do with what is being tested here.
    """
    for row in conn.execute(
        "SELECT id FROM region WHERE world_id = ? AND home_nation_id = ?"
        "   AND kind <> 'ocean' ORDER BY id LIMIT ?", (WORLD, US, count)).fetchall():
        conn.execute("UPDATE region SET controller_nation_id = ? WHERE id = ?",
                     (THEM, str(row["id"])))
    conn.commit()


def test_reclaiming_your_own_country_is_not_unprovoked(world):
    """Warpath's strongest case, priced as its weakest.

    `reasons_to_fight` calls occupied home ground "the one grievance every era
    recognises" and offers it first; WAR-001 had never heard of it and charged the
    reclamation the same eight legitimacy as an invasion of a neighbour that had done
    nothing. The two now read the same column.
    """
    _occupy(world)
    _legitimacy(world, 60.0)
    assert occupied_homeland(world, WORLD, US, THEM) == 1

    charge = declaration_cost(world, WORLD, aggressor=US, target=THEM, turn=5)
    assert charge.provoked and charge.accepted

    report = ResolutionReport()
    _declare_war(world, WORLD, turn=5, date="1897-05-01", aggressor=US,
                 target_value=THEM, report=report,
                 justification="they are standing on our provinces")

    assert report.wars_declared == 1, report.problems
    standing = float(world.execute(
        "SELECT legitimacy FROM nation WHERE id = ?", (US,)).fetchone()["legitimacy"])
    assert standing == pytest.approx(60.0), (
        "a country retaking its own ground paid for an unprovoked war")


def test_a_country_with_no_standing_left_can_still_retake_its_own_ground(world):
    """The two fixes meeting: W1's gate must not refuse W2's free war.

    This is the case that mattered most and would have been made *worse* by W1 alone -- a
    government at zero legitimacy, which is precisely the government whose country has
    been overrun, being told it may not declare war to get it back.
    """
    _occupy(world)
    _legitimacy(world, 0.0)

    verdict = may_declare(world, WORLD, aggressor=US, target=THEM, turn=5)
    assert verdict.allowed, verdict.because
    assert verdict.cost == 0.0
    assert verdict.grounds.startswith("they hold")


def test_the_briefing_says_what_a_declaration_costs(world):
    """Disclosed rather than discovered. A target and a grievance without a price is half
    a quote, and which half a candidate was is what a staff could not see."""
    from atlas.engine.views import _warpath_lines

    _occupy(world)
    _legitimacy(world, 60.0)
    free = "\n".join(_warpath_lines(world, WORLD, US, 5))
    assert "no legitimacy cost" in free

    world.execute("UPDATE region SET controller_nation_id = NULL WHERE world_id = ?",
                  (WORLD,))
    _hostile(world)
    priced = "\n".join(_warpath_lines(world, WORLD, US, 5))
    assert "costs 8 legitimacy" in priced
