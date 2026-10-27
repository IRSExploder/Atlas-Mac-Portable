"""A government removed by something its own numbers did not predict.

Everything else in the leaders layer takes a government away that the country had been
earning: a fall is the last step of a spiral the world can watch coming. This is the one
removal with a cause outside the victim, and it exists twice over -- as the thing every
alternate-history sandbox wants, and as an instrument, because every EVE match so far has
compared model families on decisions they *chose*.

What is pinned here, in order of how much damage getting it wrong would do:

* **an era that did not ask for it takes no draw at all** -- not a draw that fails. A
  seeded stream that is consumed is a world that moves, and a world that moves is a
  cassette re-record, which is the owner's model quota;
* **a rewind puts the dead government back**, which is the bug `seat` was converted from
  raw SQL to diffs to fix, and this is a second way into the same chair;
* the shock is floored, so it cannot condemn its own batch on the shaky states it is most
  interesting on;
* it reaches the army, which no other removal in this layer does;
* the war aims die with the government that declared them;
* **no model can fire it**, and that is checked structurally rather than by reading the
  docstring back.
"""

from __future__ import annotations

from atlas.engine.leaders import assess_shocks, leader_of, shock_assassination
from atlas.state.bootstrap import NationSeed, RegionSeed, WorldSeed, create_world
from atlas.state.models import Difficulty

WORLD = "w_shock"


def _world(stability=60.0, legitimacy=70.0):
    seed = WorldSeed(
        id=WORLD, name="Shock", start_date="2026-01-01", rng_seed=99,
        difficulty=Difficulty.NORMAL,
        nations=(
            NationSeed(id="a", name="Ayland", colour="#111", capital_region_id="r_a",
                       economy=50.0, stability=stability, legitimacy=legitimacy),
            NationSeed(id="b", name="Beeland", colour="#222", capital_region_id="r_b",
                       economy=50.0),
        ),
        regions=(
            RegionSeed(id="r_a", name="Ayland Prime", geo_key="g:a", owner_nation_id="a"),
            RegionSeed(id="r_b", name="Beeland Prime", geo_key="g:b", owner_nation_id="b"),
        ),
    )
    return create_world(":memory:", seed)


def _army(conn, nation_id="a", region_id="r_a", morale=60.0, count=3):
    for index in range(count):
        conn.execute(
            "INSERT INTO unit (id, world_id, nation_id, region_id, kind, strength, morale)"
            " VALUES (?, ?, ?, ?, 'infantry', 10, ?)",
            (f"u_{nation_id}_{index}", WORLD, nation_id, region_id, morale))
    conn.commit()


def _aim(conn, nation_id="a", enemy_id="b", kind="annex"):
    conn.execute(
        "INSERT INTO war_aim (id, world_id, nation_id, enemy_id, kind, subject_id,"
        "                     declared_turn)"
        " VALUES (?, ?, ?, ?, ?, '', 1)",
        (f"wa_{nation_id}_{enemy_id}_{kind}", WORLD, nation_id, enemy_id, kind))
    conn.commit()


def _turns(conn, upto=3):
    for number in range(1, upto + 1):
        conn.execute(
            "INSERT INTO turn (world_id, number, phase, status, start_date)"
            " VALUES (?, ?, 'sealed', 'sealed', ?)",
            (WORLD, number, f"2026-0{number + 1}-01"))
    conn.commit()


# ------------------------------------------------------------------ it removes a government


def test_the_government_changes_and_says_why():
    conn = _world()
    was = leader_of(conn, "a")
    assert was is not None, "the fixture has no government to kill"

    report = shock_assassination(
        conn, WORLD, nation_id="a", turn=1, date="2026-02-01")

    assert report.happened, report.problems
    now = leader_of(conn, "a")
    assert now is not None and now.id != was.id, "nobody was put in the chair"
    assert now.came_to_power == "coup"
    text = conn.execute(
        "SELECT text FROM event WHERE world_id = ? AND kind = 'leader_change'",
        (WORLD,)).fetchone()
    assert text is not None and "assassinated" in str(text["text"])


def test_the_old_government_is_stood_down_rather_than_left_current():
    """One current government per nation is a partial unique index, so getting this wrong
    is an integrity error rather than a wrong answer -- but only if two rows survive."""
    conn = _world()
    was = leader_of(conn, "a")
    shock_assassination(conn, WORLD, nation_id="a", turn=1, date="2026-02-01")

    rows = conn.execute(
        "SELECT id, is_current, left_power_turn FROM leader WHERE world_id = ?"
        "   AND nation_id = 'a' ORDER BY id", (WORLD,)).fetchall()
    assert len(rows) == 2
    old = next(r for r in rows if str(r["id"]) == was.id)
    assert not int(old["is_current"])
    assert int(old["left_power_turn"]) == 1


# ------------------------------------------------------------------ what it costs the state


def test_it_costs_more_than_an_ordinary_fall():
    from atlas.engine.leaders import _COUP_STABILITY, _SHOCK_STABILITY

    conn = _world(stability=60.0, legitimacy=70.0)
    shock_assassination(conn, WORLD, nation_id="a", turn=1, date="2026-02-01")
    row = conn.execute(
        "SELECT stability, legitimacy FROM nation WHERE id = 'a'").fetchone()
    assert float(row["stability"]) == 60.0 + _SHOCK_STABILITY
    assert float(row["legitimacy"]) == 70.0 - 20.0
    assert _SHOCK_STABILITY < _COUP_STABILITY, "a killing should not be cheaper than a coup"


def test_a_shaky_state_is_floored_rather_than_refused():
    """**The common case, not the edge.** ECON-003 holds legitimacy inside 0-100 and
    condemns the whole batch, so an unclamped hit on a country at stability 4 means a
    government that fell in the report and did not fall in the database -- and the shaky
    countries are exactly the ones a shock is interesting on."""
    conn = _world(stability=4.0, legitimacy=3.0)
    report = shock_assassination(conn, WORLD, nation_id="a", turn=1, date="2026-02-01")

    assert report.happened, report.problems
    row = conn.execute(
        "SELECT stability, legitimacy FROM nation WHERE id = 'a'").fetchone()
    assert float(row["stability"]) >= 1.0
    assert float(row["legitimacy"]) >= 1.0
    assert leader_of(conn, "a").came_to_power == "coup", "the chair did not change"


def test_it_reaches_the_army():
    """The one effect with no precedent in the fall path: no succession has ever touched a
    formation, and without this the shock is political only."""
    conn = _world()
    _army(conn, morale=60.0)
    report = shock_assassination(conn, WORLD, nation_id="a", turn=1, date="2026-02-01")

    assert report.formations_shaken == 3
    morale = [float(r["morale"]) for r in conn.execute(
        "SELECT morale FROM unit WHERE world_id = ? AND nation_id = 'a'", (WORLD,))]
    assert morale == [50.0, 50.0, 50.0]


def test_an_army_with_no_morale_left_is_not_driven_below_zero():
    conn = _world()
    _army(conn, morale=4.0, count=1)
    assert shock_assassination(
        conn, WORLD, nation_id="a", turn=1, date="2026-02-01").happened
    assert float(conn.execute(
        "SELECT morale FROM unit WHERE id = 'u_a_0'").fetchone()["morale"]) == 0.0


def test_the_war_aims_die_with_the_government_that_declared_them():
    conn = _world()
    _aim(conn, kind="annex")
    _aim(conn, kind="demilitarise")
    report = shock_assassination(conn, WORLD, nation_id="a", turn=1, date="2026-02-01")

    assert report.aims_cleared == 2
    assert conn.execute(
        "SELECT COUNT(*) n FROM war_aim WHERE world_id = ? AND nation_id = 'a'",
        (WORLD,)).fetchone()["n"] == 0
    # Somebody else's aims are somebody else's.
    _aim(conn, nation_id="b", enemy_id="a")
    shock_assassination(conn, WORLD, nation_id="a", turn=2, date="2026-03-01")
    assert conn.execute(
        "SELECT COUNT(*) n FROM war_aim WHERE world_id = ? AND nation_id = 'b'",
        (WORLD,)).fetchone()["n"] == 1


def test_a_named_sponsor_is_blamed_and_an_unnamed_one_is_not():
    conn = _world()
    before = float(conn.execute(
        "SELECT score FROM relation WHERE world_id = ? AND nation_a = 'a' AND nation_b = 'b'",
        (WORLD,)).fetchone()["score"])

    shock_assassination(conn, WORLD, nation_id="a", turn=1, date="2026-02-01")
    unblamed = float(conn.execute(
        "SELECT score FROM relation WHERE world_id = ? AND nation_a = 'a' AND nation_b = 'b'",
        (WORLD,)).fetchone()["score"])
    assert unblamed == before, "nobody was named and somebody was blamed anyway"

    report = shock_assassination(
        conn, WORLD, nation_id="a", turn=2, date="2026-03-01", sponsor="b")
    assert "Beeland is blamed" in report.text
    after = float(conn.execute(
        "SELECT score FROM relation WHERE world_id = ? AND nation_a = 'a' AND nation_b = 'b'",
        (WORLD,)).fetchone()["score"])
    assert after < unblamed


def test_it_says_so_rather_than_raising_when_there_is_nobody_to_kill():
    conn = _world()
    report = shock_assassination(
        conn, WORLD, nation_id="nowhere", turn=1, date="2026-02-01")
    assert not report.happened
    assert report.problems and "nowhere" in report.problems[0]


# ------------------------------------------------------------------------------- it rewinds


def test_the_dead_government_comes_back_on_a_rewind():
    """**PT31's finding, by a second door.** `seat` used to write the chair with direct
    SQL, and a rewind does not replay, so a coup on turn 3 rewound to turn 2 left the junta
    in place while `leader` sat in `_FINGERPRINT_TABLES`. Every effect here is a diff
    citing one event for exactly that reason, and this is the test that says so."""
    from atlas.state.bootstrap import fingerprint
    from atlas.state.replay import rewind_to

    conn = _world()
    _army(conn, morale=60.0)
    _aim(conn)
    _turns(conn, 3)
    conn.execute(
        "UPDATE world SET turn_number = 2, world_date = '2026-03-01' WHERE id = ?", (WORLD,))
    conn.commit()
    before = fingerprint(conn, WORLD)
    was = leader_of(conn, "a").id

    conn.execute("UPDATE world SET turn_number = 3 WHERE id = ?", (WORLD,))
    conn.commit()
    assert shock_assassination(
        conn, WORLD, nation_id="a", turn=3, date="2026-04-01").happened
    conn.commit()
    assert fingerprint(conn, WORLD) != before, "the fixture did not stage a killing"

    assert rewind_to(conn, WORLD, 2).ok
    assert fingerprint(conn, WORLD) == before
    assert leader_of(conn, "a").id == was, "the dead government stayed dead"
    assert conn.execute(
        "SELECT COUNT(*) n FROM war_aim WHERE world_id = ? AND nation_id = 'a'",
        (WORLD,)).fetchone()["n"] == 1, "the aims did not come back"


# ------------------------------------------------------- the era has to ask, and by default does not


def test_an_era_that_did_not_ask_takes_no_draw_at_all():
    """**Not "a draw that fails".** `derive` is a seeded stream, and consuming one shifts
    every world that shares it: the cold-war cassette would go from MISS to MISMATCH with
    no prompt change at all, and re-recording it is the owner's model quota. The assertion
    that matters is on the *mock* -- that `derive` was never reached."""
    import atlas.engine.leaders as leaders

    conn = _world()
    conn.execute("UPDATE world SET preset_id = 'cold-war' WHERE id = ?", (WORLD,))
    conn.commit()

    drawn: list[str] = []
    real = leaders.derive

    def spy(*args, **kwargs):
        drawn.append(str(args))
        return real(*args, **kwargs)

    leaders.derive = spy
    try:
        assert assess_shocks(
            conn, WORLD, turn=1, date="2026-02-01", seed=7, days=30) == []
    finally:
        leaders.derive = real
    assert drawn == [], f"an opted-out era consumed {len(drawn)} seeded stream(s)"


def test_a_world_with_no_preset_is_left_alone():
    conn = _world()
    conn.execute("UPDATE world SET preset_id = '' WHERE id = ?", (WORLD,))
    conn.commit()
    assert assess_shocks(conn, WORLD, turn=1, date="2026-02-01", seed=7, days=30) == []


def test_an_era_that_does_ask_gets_the_same_answer_every_time(monkeypatch):
    """A certainty rather than a rate, so the test measures the wiring and not the dice --
    and then the same world twice, so it measures the dice too."""
    import atlas.engine.leaders as leaders

    class _Asking:
        assassination_rate = 1.0

    monkeypatch.setattr(
        "atlas.scenarios.registry.scenario_for", lambda _preset: _Asking())

    struck = []
    for _ in range(2):
        conn = _world()
        conn.execute("UPDATE world SET preset_id = 'made-up' WHERE id = ?", (WORLD,))
        conn.commit()
        reports = leaders.assess_shocks(
            conn, WORLD, turn=1, date="2026-02-01", seed=7, days=30)
        struck.append(sorted(r.nation_id for r in reports if r.happened))

    assert struck[0], "an era asking for certainty lost nobody"
    assert struck[0] == struck[1], "the same world drew a different answer twice"


# ---------------------------------------------------------------- and no model can fire it


def test_no_order_a_model_can_write_reaches_it():
    """**Structural, not a policy note.** The tempting implementation registers an
    `assassinate` handler beside `raise_units`, which puts a killing one schema field away
    from every tracked agent in the game. Two things are asserted: the handler table does
    not carry it, and the two converters that turn a tracked power's batch into actions
    emit only the types they are named for."""
    import inspect

    from atlas.engine import turn_engine
    from atlas.engine.orders import _HANDLERS

    assert not any("assassin" in key for key in _HANDLERS), (
        "an assassination became an order type, and orders are a model's channel")

    built = turn_engine._build_orders_to_actions(
        ({"project": "a shipyard"}, {"project": "", "type": "assassinate"}))
    raised = turn_engine._formation_orders_to_actions(
        ({"unit_kind": "infantry", "quantity": 2, "type": "assassinate"},))
    assert {a["type"] for a in built} == {"build_project"}
    assert {a["type"] for a in raised} == {"raise_units"}

    # And the only door in is an argument no parsed answer fills.
    assert "shocks" in inspect.signature(turn_engine.TurnEngine.jump).parameters
