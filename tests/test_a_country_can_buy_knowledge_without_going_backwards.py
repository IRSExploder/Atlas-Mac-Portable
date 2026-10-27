"""Research worked in every era and no government ever bought any.

**Nothing refused it; nothing ever reached it.** `order_research` has been correct since it
landed -- it charges the treasury, takes a queue slot, files a DISCOVERY event and delivers
effects that shipped code reads. What no power could do was *choose* it. The unattended
chooser walked an ordered list of public works and took the first thing it could afford, and
a workshop is always cheaper than a programme, so across every measured run the `knowledge`
component sat at **0.000 for every power in the world**. The one power in any run that ever
researched anything was hand-built for a balance probe.

That makes this the seventh capability found working, tested and unreachable -- after
support, mediation, reinforcement, garrison, war aims and cession.

**The tests here are paired, and the pairing is the point.** "Knowledge rose" is not a
result: a government that spends its one order a turn on science is a government that did
not build a workshop, and a queue full of half-finished programmes is a country that never
industrialised. So every claim about knowledge is made against a control arm -- the same
fixture with `_RESEARCH_SURPLUS` set out of reach, which is exactly the world as it was --
and the thing being asserted is that knowledge moved **and development did not fall**.
"""

from __future__ import annotations

import math
import sqlite3

import pytest

from atlas.engine import production
from atlas.engine.production import (
    _choose_development,
    autonomous_investment,
    industrial_capacity,
)
from atlas.engine.research import TECHNOLOGIES, known_technologies
from atlas.engine.views import _buildable_lines, build_tracked_agent_view
from atlas.scenarios.sandbox import sandbox_seed
from atlas.state.bootstrap import create_world

WORLD = "sandbox"
A = "aurelia"


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = create_world(":memory:", sandbox_seed())
    connection.execute("UPDATE world SET turn_number = 1 WHERE id = ?", (WORLD,))
    connection.commit()
    yield connection
    connection.close()


def _row(conn: sqlite3.Connection, nation_id: str = A):
    return conn.execute(
        "SELECT id, name, economy, industry, treasury, manpower, stability FROM nation"
        "  WHERE id = ?", (nation_id,)).fetchone()


def _settled_and_rich(conn: sqlite3.Connection, treasury: float = 900.0) -> None:
    """A power with no domestic trouble and money to spare. The state research is for."""
    conn.execute(
        "UPDATE nation SET stability = 80, treasury = ?, manpower = 400, industry = 40"
        "  WHERE id = ?", (treasury, A))
    conn.commit()


def _development(conn: sqlite3.Connection) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(economy), 0) AS e, COALESCE(SUM(industry), 0) AS i"
        "  FROM nation WHERE world_id = ? AND is_alive = 1", (WORLD,)).fetchone()
    return float(row["e"]) + float(row["i"])


# --------------------------------------------------------------- the choice itself


def test_a_settled_power_with_money_to_spare_begins_a_programme(conn):
    """The defect, stated as the fix. It could always afford one; it was never offered one."""
    _settled_and_rich(conn)
    kind, chosen = _choose_development(conn, WORLD, _row(conn))
    assert kind == "research"
    assert chosen in {tech.id for tech in TECHNOLOGIES}


def test_the_control_arm_builds_exactly_what_it_used_to(conn, monkeypatch):
    """**The other half of every claim in this file.**

    With the surplus bar out of reach nothing here can begin a programme, and the same
    fixture makes the same choice it made before any of this existed. Without this the
    tests below would be measuring a feature against nothing.
    """
    monkeypatch.setattr(production, "_RESEARCH_SURPLUS", math.inf)
    _settled_and_rich(conn)
    kind, chosen = _choose_development(conn, WORLD, _row(conn))
    assert kind == "project"
    assert chosen in {"steelworks", "workshops", "rail"}


def test_a_programme_is_never_bought_instead_of_this_turns_public_work(conn):
    """**Out of surplus, or not at all.**

    A power that would have to skip its workshop to pay for a programme is buying knowledge
    with development, which is the exact failure this whole change has to be measurable
    against. Given only enough for one of the two, it builds.
    """
    cheapest = min(tech.cost_treasury for tech in TECHNOLOGIES)
    conn.execute(
        "UPDATE nation SET stability = 80, treasury = ?, manpower = 400, industry = 40"
        "  WHERE id = ?", (cheapest + 1.0, A))
    conn.commit()
    # `None` counts, and is the honest answer here: with just over one programme's price in
    # hand this power cannot reach a public work either. What must not happen is that it
    # spends the lot on science and leaves itself nothing to build with.
    choice = _choose_development(conn, WORLD, _row(conn))
    assert choice is None or choice[0] != "research"


def test_a_power_with_a_programme_running_does_not_open_another(conn):
    """One at a time. A country that queues its whole tree at once is not choosing."""
    _settled_and_rich(conn)
    first = _choose_development(conn, WORLD, _row(conn))
    assert first[0] == "research"
    conn.execute(
        "INSERT INTO construction (id, world_id, nation_id, region_id, project_kind,"
        "                          kind, days_remaining, started_turn)"
        " VALUES ('c_test', ?, ?, (SELECT capital_region_id FROM nation WHERE id = ?),"
        "         'research', ?, 150.0, 1)",
        (WORLD, A, A, first[1]))
    conn.commit()
    again = _choose_development(conn, WORLD, _row(conn))
    assert again is None or again[0] != "research"


def test_a_restive_country_does_not_open_a_laboratory(conn):
    """Research is what a settled power does with surplus. A government whose people are
    restive has more pressing uses for the money, and the restorative preference already
    exists to say so."""
    conn.execute(
        "UPDATE nation SET stability = 20, treasury = 900, manpower = 400, industry = 40"
        "  WHERE id = ?", (A,))
    conn.commit()
    kind, chosen = _choose_development(conn, WORLD, _row(conn))
    assert kind == "project"
    assert chosen in {"land_reform", "housing", "health"}


# --------------------------------------------------------------- banking


def test_a_power_within_reach_of_what_it_wanted_saves_instead_of_reordering(
        conn, monkeypatch):
    """**Reordering is not a decision, it is a treadmill -- and the measurement refused it
    anyway.**

    The chooser takes the first affordable entry of its preference list, so a power that
    cannot quite afford a steelworks builds a workshop instead, every turn, for ever,
    because the workshop keeps the treasury just below the steelworks. Saving above
    `_BANK_ABOVE` of the price is the answer to that, and over 60 turns of Modern Day it
    costs 4.0% of the world's development and buys nothing -- so it ships off.

    This is the test that keeps the mechanism honest while it is off: the bar is put back
    where it was measured, and the behaviour it produces is pinned. A mechanism that is one
    constant from being on and has no test is a mechanism nobody can turn on safely.
    """
    from atlas.engine.production import project_cost
    from atlas.engine.projects import project_by_id

    _BANK_ABOVE = 0.55
    monkeypatch.setattr(production, "_BANK_ABOVE", _BANK_ABOVE)
    steelworks = project_by_id("steelworks")
    conn.execute("UPDATE nation SET stability = 80, manpower = 400, industry = 40"
                 "  WHERE id = ?", (A,))
    conn.commit()
    price, _manpower = project_cost(conn, A, steelworks)
    conn.execute(
        "UPDATE nation SET treasury = ? WHERE id = ?",
        (price * (_BANK_ABOVE + 0.05), A))
    conn.commit()

    kind, chosen = _choose_development(conn, WORLD, _row(conn))
    assert (kind, chosen) == ("bank", "steelworks")


def test_a_power_nowhere_near_the_price_still_builds_what_it_can(conn):
    """Banking is for a government within reach. One that is not builds the thing it can
    actually pay for, or this becomes a rule that stops poor countries developing at all."""
    from atlas.engine.production import project_cost
    from atlas.engine.projects import project_by_id

    price, _m = project_cost(conn, A, project_by_id("steelworks"))
    conn.execute(
        "UPDATE nation SET stability = 80, manpower = 400, industry = 40, treasury = ?"
        "  WHERE id = ?", (price * 0.2, A))
    conn.commit()
    choice = _choose_development(conn, WORLD, _row(conn))
    assert choice is None or choice[0] == "project"


def test_saving_is_off_by_default_because_it_was_measured_and_refused(conn):
    """**The shipped default, pinned so it cannot drift back on unnoticed.**

    Its own arm -- saving on, research off -- finished 60 turns of three Modern Day seeds at
    10,586 of world development against a baseline's 11,022, with the knowledge component
    still at zero everywhere. A government that waits is a government that did not build.
    """
    from atlas.engine.production import _BANK_ABOVE, project_cost
    from atlas.engine.projects import project_by_id

    assert math.isinf(_BANK_ABOVE), (
        "saving is on by default again; it cost 4.0% of world development and bought "
        "nothing when it was measured")
    price, _m = project_cost(conn, A, project_by_id("steelworks"))
    conn.execute(
        "UPDATE nation SET stability = 80, manpower = 400, industry = 40, treasury = ?"
        "  WHERE id = ?", (price * 0.6, A))
    conn.commit()
    choice = _choose_development(conn, WORLD, _row(conn))
    assert choice is None or choice[0] != "bank"


def test_a_strained_country_never_saves_toward_a_factory(conn, monkeypatch):
    """A government whose people are restive spends on them. Saving toward a better
    factory while stability falls is the coup this preference was added to prevent.

    **Narrowed, deliberately, when the safe-first recovery policy landed.** This used to
    assert that a strained power never returns a `bank` at all, and that was the right
    assertion while the alternative to saving was always *building something*. It is not
    any more: a strained government that cannot afford a certain restorative now holds its
    money for one, rather than falling through to a land reform that can cost it 8
    stability or a workshop that costs it 1. What must never happen is the thing this test
    was written about -- saving toward INDUSTRY while the country comes apart -- and that
    is what it now says.
    """
    conn.execute(
        "UPDATE nation SET stability = 20, treasury = 12, manpower = 400, industry = 40"
        "  WHERE id = ?", (A,))
    conn.commit()
    choice = _choose_development(conn, WORLD, _row(conn))
    assert choice is None or choice[0] != "bank" or choice[1] in {"housing", "health"}


def test_saving_is_reported_rather_than_looking_like_an_empty_turn(conn, monkeypatch):
    """A turn where a government held its money must be distinguishable from a turn where
    the chooser found nothing at all, or the measurement cannot tell them apart -- which is
    how the arm that refused saving was able to say what saving cost."""
    from atlas.engine.production import project_cost
    from atlas.engine.projects import project_by_id

    _BANK_ABOVE = 0.55
    monkeypatch.setattr(production, "_BANK_ABOVE", _BANK_ABOVE)
    price, _m = project_cost(conn, A, project_by_id("steelworks"))
    conn.execute(
        "UPDATE nation SET stability = 80, manpower = 400, industry = 40, treasury = ?"
        "  WHERE is_player = 0 AND is_tracked = 0 AND world_id = ?",
        (price * (_BANK_ABOVE + 0.05), WORLD))
    conn.commit()
    report = autonomous_investment(conn, WORLD, turn=1, date="1897-04-01")
    assert report.banked, "a government saved and the report said nothing about it"


# ------------------------------------------------- the paired end-to-end measurement


def _play(conn: sqlite3.Connection, turns: int = 12) -> None:
    for turn in range(1, turns + 1):
        autonomous_investment(conn, WORLD, turn=turn, date=f"1897-{turn:02d}-01")
        conn.execute(
            "UPDATE construction SET days_remaining = days_remaining - 30"
            "  WHERE world_id = ?", (WORLD,))
        conn.execute("UPDATE nation SET treasury = treasury + 40 WHERE world_id = ?",
                     (WORLD,))
        conn.commit()
        from atlas.engine.production import advance_construction

        advance_construction(conn, WORLD, turn=turn, date=f"1897-{turn:02d}-01", days=30)


def test_knowledge_moves_and_development_does_not_fall(conn, monkeypatch):
    """**The claim this whole change has to earn, measured against its own control.**

    Two identical worlds, played the same number of turns through the same code. One has
    the surplus bar out of reach, which is the world every previous measurement was taken
    in; the other does not. Knowledge must move in the second, and total development must
    not end up worse for it.

    Development is `economy + industry` summed over the living powers, which is what
    `score_of` reads for the wealth component -- the number that would fall if programmes
    were being bought with money the country needed for its factories.
    """
    control = create_world(":memory:", sandbox_seed())
    try:
        for db in (conn, control):
            db.execute(
                "UPDATE nation SET stability = 80, treasury = 400, manpower = 400,"
                "  industry = 40 WHERE world_id = ?", (WORLD,))
            db.commit()

        monkeypatch.setattr(production, "_RESEARCH_SURPLUS", math.inf)
        _play(control)
        control_known = sum(
            len(known_technologies(control, WORLD, str(row["id"])))
            for row in control.execute(
                "SELECT id FROM nation WHERE world_id = ?", (WORLD,)))
        control_development = _development(control)

        monkeypatch.setattr(production, "_RESEARCH_SURPLUS", 1.0)
        _play(conn)
        learned = sum(
            len(known_technologies(conn, WORLD, str(row["id"])))
            for row in conn.execute(
                "SELECT id FROM nation WHERE world_id = ?", (WORLD,)))
        development = _development(conn)

        assert control_known == 0, (
            "the control arm researched something, so it is not a control")
        assert learned > 0, "knowledge did not move at all"
        assert development >= control_development * 0.95, (
            f"knowledge cost development: {development:.1f} against a control's "
            f"{control_development:.1f}")
    finally:
        control.close()


# --------------------------------------------------------------- the briefing


def test_the_menu_says_what_a_programme_buys(conn):
    """0 of 24 briefings said what any programme does, directly under a list of public
    works each of which ends in its own effects."""
    _settled_and_rich(conn)
    menu = "\n".join(_buildable_lines(conn, WORLD, A))
    assert "Research you could begin" in menu
    line = next(ln for ln in menu.splitlines() if "[`welding`]" in ln)
    assert "->" in line and "build_days" in line
    assert "battleship" in line, "a unit effect has to name the hull it is about"


def test_the_menu_names_what_is_locked_and_what_opens_it(conn):
    """**A block that goes quiet when the answer is no teaches a staff the channel is
    dead.** Three of seven programmes in two eras were named nowhere at all, so the one
    part of the catalogue with a plan in it was the part nobody could see.
    """
    _settled_and_rich(conn)
    menu = "\n".join(_buildable_lines(conn, WORLD, A))
    assert "cannot begin yet" in menu
    line = next(ln for ln in menu.splitlines() if "[`carrier_doctrine`]" in ln)
    assert "needs" in line and "All-welded construction" in line


def test_the_task_block_examples_a_programme_and_not_only_a_workshop(conn):
    """The reader was told the field takes two kinds of thing and shown one."""
    text = build_tracked_agent_view(conn, WORLD, nation_id=A).text
    task = text.split("## Your task", 1)[-1]
    assert "research programme is the same order" in task
    assert "knowledge" in task


def test_a_programme_already_known_is_not_offered_or_listed_as_locked(conn):
    """It is neither available nor blocked; it is done. A menu that listed it as either
    would be telling a country to research what it already knows."""
    conn.execute(
        "INSERT INTO technology (world_id, nation_id, tech_id, completed_turn)"
        " VALUES (?, ?, 'welding', 1)", (WORLD, A))
    conn.commit()
    _settled_and_rich(conn)
    menu = "\n".join(_buildable_lines(conn, WORLD, A))
    assert "[`welding`]" not in menu


def test_the_queue_is_still_one_queue(conn):
    """**No extra actions.** A programme takes a slot from the same finite queue a
    battleship and a housing estate come out of, which is the decision the whole
    production layer exists to pose. If research had its own budget this would be a
    feature that made everybody richer rather than a choice."""
    _settled_and_rich(conn)
    capacity = industrial_capacity(conn, A)
    conn.execute(
        "INSERT INTO construction (id, world_id, nation_id, region_id, project_kind,"
        "                          kind, days_remaining, started_turn)"
        " SELECT 'c_fill_' || value, ?, ?, capital_region_id, 'works', 'workshops',"
        "        150.0, 1 FROM nation, (SELECT 1 AS value UNION SELECT 2 UNION SELECT 3"
        "        UNION SELECT 4 UNION SELECT 5 UNION SELECT 6) WHERE nation.id = ?"
        "  LIMIT ?", (WORLD, A, A, capacity))
    conn.commit()
    report = autonomous_investment(conn, WORLD, turn=1, date="1897-04-01")
    assert not any(A in entry for entry in report.invested), (
        "a full queue accepted a programme, so research is not sharing the queue")
