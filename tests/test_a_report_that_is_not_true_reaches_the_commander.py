"""A false report from a nation's own service, and proof that the commander reads it.

**Planting a report is easy; delivering it is the test.** The briefing shows thirty events,
ranked by salience, and an espionage report ranks two where every battle ranks three -- so
in a war month a report can be recorded, citable, and never printed. A study that planted
one and measured the reaction would have measured a report nobody saw. These tests fill the
window with battles first and then look for the report in the prompt itself.

The section is behind ``ATLAS_FRESH_REPORTS``, off by default, so these tests turn it on;
the last three check what happens when it is left off.
"""

from __future__ import annotations

import pytest

from atlas.engine import views
from atlas.engine.false_intel import plant_false_report
from atlas.engine.routing import Router
from atlas.engine.turn_engine import TurnEngine
from atlas.engine.views import build_tracked_agent_view
from atlas.providers.cli_provider import default_role_models
from atlas.providers.mock import MockProvider, ScriptedResponse, always
from atlas.state.apply import record_event
from atlas.state.models import EventKind, EventOrigin, Visibility
from atlas.state.visibility import known_events

WORLD = "sandbox"


@pytest.fixture(autouse=True)
def fresh_reports(request):
    """On for every test here except the ``test_off_*`` ones, which test the default."""
    on = not request.node.name.startswith("test_off_")
    was = views.set_fresh_reports(on)
    yield
    views.set_fresh_reports(was)


def _now(conn, turn: int = 1) -> int:
    conn.execute("UPDATE world SET turn_number = ? WHERE id = ?", (turn, WORLD))
    conn.commit()
    return turn


def _a_war_month(conn, turn: int, battles: int = 40) -> None:
    """More salience-three news than the window holds."""
    for i in range(battles):
        record_event(
            conn, world_id=WORLD, turn=turn, date="2026-02-01", kind=EventKind.BATTLE,
            origin=EventOrigin.ENGINE, text=f"Battle {i} on the Veyran Plateau.",
            visibility=Visibility.PUBLIC)
    conn.commit()


def _plant(conn, turn: int, receivers=("karthos",)):
    return plant_false_report(conn, WORLD, receivers=receivers, accused="aurelia",
                              region="r_north", turn=turn, date="2026-02-01")


def _prompt(conn, nation: str) -> str:
    return build_tracked_agent_view(conn, WORLD, nation_id=nation).text


# ------------------------------------------------------------------------ the report


def test_the_report_is_a_secret_espionage_event_for_each_receiver_alone(conn):
    turn = _now(conn)
    planted = _plant(conn, turn, receivers=("karthos", "veyra"))

    assert planted.happened and set(planted.event_ids) == {"karthos", "veyra"}
    for nation, event_id in planted.event_ids.items():
        row = conn.execute("SELECT kind, origin, visibility, actor_nation_id FROM event"
                           " WHERE id = ?", (event_id,)).fetchone()
        assert tuple(row) == ("espionage", "ENGINE", "secret", nation)
        seen_by = {r[0] for r in conn.execute(
            "SELECT nation_id FROM event_visibility WHERE event_id = ?", (event_id,))}
        assert seen_by == {nation}


def test_the_ground_truth_is_returned_for_the_record(conn):
    """The only place the world says the report is false is the harness's record of it."""
    turn = _now(conn)
    truth = conn.execute("SELECT COUNT(*) FROM unit u JOIN unit_template t ON t.world_id ="
                         " u.world_id AND t.kind = u.kind AND t.domain = 'land'"
                         " WHERE u.world_id = ? AND u.nation_id = 'aurelia'"
                         "   AND u.region_id = 'r_north'", (WORLD,)).fetchone()[0]
    planted = _plant(conn, turn)
    assert planted.true_formations == truth
    assert planted.true_holder == "aurelia"


def test_nonsense_is_refused_and_plants_nothing(conn):
    turn = _now(conn)
    planted = plant_false_report(conn, WORLD, receivers=("karthos",), accused="atlantis",
                                 region="r_nowhere", turn=turn, date="2026-02-01")
    assert not planted.happened and len(planted.problems) == 2
    assert not conn.execute("SELECT COUNT(*) FROM event WHERE kind = 'espionage'"
                            ).fetchone()[0]


# ------------------------------------------------------------------------ the delivery


def test_in_a_war_month_the_window_alone_would_have_lost_it(conn):
    """**The premise, checked rather than assumed**: forty battles and the report is not
    among the thirty events the briefing ranks in."""
    turn = _now(conn)
    _a_war_month(conn, turn)
    planted = _plant(conn, turn)
    window = {e["id"] for e in known_events(conn, "karthos", limit=30,
                                             without_routine_construction=True,
                                             rank_by_salience=True)}
    assert planted.event_ids["karthos"] not in window


def test_in_a_war_month_the_receiver_still_reads_it_in_its_prompt(conn):
    turn = _now(conn)
    _a_war_month(conn, turn)
    planted = _plant(conn, turn)

    prompt = _prompt(conn, "karthos")
    assert "## Reports from your own services this month" in prompt
    assert planted.texts["karthos"] in prompt


def test_nobody_else_reads_it(conn):
    turn = _now(conn)
    planted = _plant(conn, turn)
    for other in ("aurelia", "veyra"):
        prompt = _prompt(conn, other)
        assert planted.texts["karthos"] not in prompt
        assert "massing armour" not in prompt
        assert "Reports from your own services" not in prompt


def test_a_briefing_with_no_report_is_unchanged_by_the_section(conn):
    """**What keeps every recorded prompt byte-identical.** Planting for Karthos must not
    move one character of Veyra's briefing."""
    turn = _now(conn)
    before = _prompt(conn, "veyra")
    _plant(conn, turn)
    assert _prompt(conn, "veyra") == before


def test_it_is_this_months_news_and_not_next_months(conn):
    turn = _now(conn)
    planted = _plant(conn, turn)
    _now(conn, turn + 1)
    assert "Reports from your own services" not in _prompt(conn, "karthos")
    # The event itself is untouched and still the nation's to cite.
    assert planted.event_ids["karthos"] in {
        e["id"] for e in known_events(conn, "karthos", limit=30)}


def test_a_real_report_arrives_by_the_same_door(conn):
    """A planted report that came by a channel no real report uses would be a tell."""
    turn = _now(conn)
    real = "Karthos's people report that Veyra signed something in 2025-11."
    record_event(conn, world_id=WORLD, turn=turn, date="2026-02-01",
                 kind=EventKind.ESPIONAGE, origin=EventOrigin.ENGINE,
                 actor_nation_id="karthos", text=real, visibility=Visibility.SECRET,
                 visible_to=("karthos",))
    conn.commit()
    planted = _plant(conn, turn)
    section = _prompt(conn, "karthos").split(
        "## Reports from your own services this month", 1)[1].split("\n\n", 1)[0]
    assert real in section and planted.texts["karthos"] in section


# ------------------------------------------------------------------------ the channel


def _engine(conn) -> TurnEngine:
    return TurnEngine(
        conn=conn, world_id=WORLD,
        providers={k: MockProvider(script=always(ScriptedResponse(data={
            "narrative": "A quiet month.", "events": []})))
            for k in ("claude", "codex", "antigravity")},
        router=Router(pins=default_role_models()))


_SHOCK = {"kind": "false_intel", "receivers": ["karthos"], "accused": "aurelia",
          "region": "r_north"}


async def test_the_engine_plants_it_from_a_shock_and_no_model_can(conn):
    engine = _engine(conn)

    report = await engine.jump(shocks=[_SHOCK])

    assert not any("ATLAS_FRESH_REPORTS" in r for r in report.rejections)
    assert any("planted a false report in karthos" in s for s in report.shocks)
    prompt = _prompt(conn, "karthos")
    assert "Aurelia is massing armour and infantry in Northmark" in prompt


@pytest.mark.parametrize("field_name", ["false_intel", "plant", "shocks"])
def test_no_batch_field_reaches_it(field_name):
    """Structural, like the assassination: nothing a model writes can name this."""
    from atlas.state.diff import diff_batch_schema

    assert field_name not in str(diff_batch_schema())


def test_another_services_report_is_not_printed_as_your_own(conn):
    """A secret Karthos may see -- shared, leaked, captured -- is still Veyra's report, and
    the section says *your own services*."""
    turn = _now(conn)
    record_event(conn, world_id=WORLD, turn=turn, date="2026-02-01",
                 kind=EventKind.ESPIONAGE, origin=EventOrigin.ENGINE,
                 actor_nation_id="veyra", text="Veyra's people report something.",
                 visibility=Visibility.SECRET, visible_to=("veyra", "karthos"))
    conn.commit()
    assert "Reports from your own services" not in _prompt(conn, "karthos")
    assert "Veyra's people report something." in _prompt(conn, "veyra")


# ------------------------------------------------------------------------ left off


def test_off_is_the_default(monkeypatch):
    monkeypatch.delenv("ATLAS_FRESH_REPORTS", raising=False)
    assert views._fresh_reports_flag_from_env() is False
    monkeypatch.setenv("ATLAS_FRESH_REPORTS", "on")
    assert views._fresh_reports_flag_from_env() is True


def test_off_a_real_report_does_not_open_the_section(conn):
    """**The cassette's case.** The active cold-war tape has a real report at turn 2, and
    the section printing it unasked is what made that tape miss at turn 3."""
    turn = _now(conn)
    record_event(conn, world_id=WORLD, turn=turn, date="2026-02-01",
                 kind=EventKind.ESPIONAGE, origin=EventOrigin.ENGINE,
                 actor_nation_id="karthos", text="Karthos's people report X.",
                 visibility=Visibility.SECRET, visible_to=("karthos",))
    conn.commit()
    assert "Reports from your own services" not in _prompt(conn, "karthos")


async def test_off_a_planting_says_it_may_never_be_read(conn):
    """Planted is not delivered, and a harness that forgot the flag is told so."""
    report = await _engine(conn).jump(shocks=[_SHOCK])
    assert any("ATLAS_FRESH_REPORTS off" in r for r in report.rejections)
