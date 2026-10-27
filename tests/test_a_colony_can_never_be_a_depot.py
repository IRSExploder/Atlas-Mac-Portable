"""A colony is nobody's homeland, and `_depots` asks for homeland. So no colony supplies.

**Two correct changes that were never run against each other.** On 2026-08-08, `f4110e7`
measured the supply layer on both maps and wrote the finding into
:mod:`atlas.engine.logistics`'s docstring: 1936 goes short because the *depot development
bar* is an absolute number applied to a quantity divided among however many provinces a
country happens to be drawn with, and lowering
:data:`~atlas.engine.logistics._DEPOT_DEVELOPMENT` from 0.8 to 0.3 recovers 19 of the 23
unfed regions. That was true when it was written.

On 2026-08-09 -- the next day -- `24ef42d` fixed a different and equally real bug: every
region was seeded ``home_nation_id = owner_nation_id``, which made Hong Kong British
homeland and therefore nuclear-protected. :func:`atlas.scenarios.seeder._disown_dependencies`
now sets ``home_nation_id = NULL`` on every colony and dependency, because a dependency's
home is nobody.

:func:`atlas.engine.logistics._depots` selects on ``r.home_nation_id = ?``. ``NULL = 'ita'``
is NULL, which is not true, so **a colony cannot be a depot at any development, behind any
port, under any owner**. The supply sweep was never re-taken, so the docstring still
prescribes a fix that no longer works and names the wrong constant as the culprit.

Measured on master, gathering-storm, over the 374 owned land regions:

======================  =====  ================
``_DEPOT_DEVELOPMENT``  unfed  Eritrea supplied
======================  =====  ================
0.8 (shipped)           146    no
0.3 (the prescription)  146    no
0.0                     133    no
0.3, no ``home`` test   8      yes
======================  =====  ================

Lowering the bar recovers **nothing**; dropping the homeland test recovers 138. Britain
feeds 14 of the 81 regions it holds, Italy 12 of 31, France 12 of 44. Eight Italian
formations in Eritrea and Somaliland start every 1936 game out of supply, at peace.

**What these tests assert is a proposal, not a settled rule.** Whether a colony *should*
supply an army -- and whether it should need a port, a development bar of its own, or a
lower one than the metropole's -- is a balance judgement with scenario weight, and it is
Alex's. What is not a judgement is that the mechanism now in the tree cannot express any
answer to it: the development bar it offers is unreachable.
"""

from __future__ import annotations

import sqlite3

import pytest

from atlas.engine import logistics
from atlas.engine.logistics import _depots, in_supply, supply_map
from atlas.map.pack import PackSet
from atlas.scenarios.presets import load_preset
from atlas.scenarios.registry import preset_politics, preset_seed
from atlas.scenarios.territory import pack_paths
from atlas.state.bootstrap import create_world

WORLD = "sandbox"
ERA = "colonial_era"


@pytest.fixture
def with_a_colony(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Aurelia holds Westfell as a colony: owned, controlled, developed, and a port.

    Everything `_depots` asks for except the one thing a colony can never have. The
    development is set well above :data:`~atlas.engine.logistics._DEPOT_DEVELOPMENT` so a
    red below cannot be read as the bar biting.
    """
    conn.execute(
        "UPDATE region SET owner_nation_id = 'aurelia', controller_nation_id = 'aurelia',"
        "   home_nation_id = NULL, development = 9.0, kind = 'coastal'"
        " WHERE world_id = ? AND id = 'r_west'", (WORLD,)
    )
    conn.commit()
    return conn


def test_the_colony_meets_every_stated_depot_condition(with_a_colony):
    """The premise, so a red below cannot be passing for the wrong reason."""
    row = with_a_colony.execute(
        "SELECT owner_nation_id AS owner, controller_nation_id AS held, development AS dev,"
        "   home_nation_id AS home, kind FROM region WHERE world_id = ? AND id = 'r_west'",
        (WORLD,),
    ).fetchone()
    assert row["owner"] == "aurelia"
    assert row["held"] == "aurelia"
    assert row["dev"] >= logistics._DEPOT_DEVELOPMENT
    assert row["kind"] != "ocean"
    assert row["home"] is None


def test_a_developed_owned_colony_is_a_depot(with_a_colony):
    """RED on master: the ``home_nation_id`` clause excludes it before development is read."""
    assert "r_west" in _depots(with_a_colony, WORLD, "aurelia")


def test_the_development_bar_is_what_decides_a_colony(with_a_colony):
    """RED on master, and it is the whole misdiagnosis in one assertion.

    A colony's development should be the thing that says whether it can supply an army --
    that is what :data:`~atlas.engine.logistics._DEPOT_DEVELOPMENT` is for, and it is what
    the module docstring's prescription assumes. On master the bar is never reached, so
    the colony is refused at 9.0 development and refused at 0.0 alike, and a sweep of the
    constant moves nothing. That is why lowering it recovered nothing on the real map.
    """
    def admits(bar: float) -> bool:
        original, logistics._DEPOT_DEVELOPMENT = logistics._DEPOT_DEVELOPMENT, bar
        try:
            return "r_west" in _depots(with_a_colony, WORLD, "aurelia")
        finally:
            logistics._DEPOT_DEVELOPMENT = original

    with_a_colony.execute(
        "UPDATE region SET development = 0.1 WHERE world_id = ? AND id = 'r_west'", (WORLD,)
    )
    with_a_colony.commit()
    assert not admits(0.8), "an undeveloped colony was admitted"
    assert admits(0.05), "the bar does not decide: it is unreachable"

    with_a_colony.execute(
        "UPDATE region SET development = 9.0 WHERE world_id = ? AND id = 'r_west'", (WORLD,)
    )
    with_a_colony.commit()
    assert admits(0.8), "a developed colony was still refused"


def test_conquered_ground_is_still_not_a_depot(with_a_colony):
    """Green on master and it must stay green: the corridor rule is what `_depots` is for.

    "Depots are your own core, not merely ground you hold" is the rule that makes a deep
    invasion a thread from home rather than a spreading stain. Owning a colony is not the
    same thing as holding somebody else's province, and a fix for the first must not
    loosen the second: Karthos's own developed ground, occupied by Aurelia, supplies
    Aurelia with nothing.
    """
    with_a_colony.execute(
        "UPDATE region SET controller_nation_id = 'aurelia', development = 9.0"
        " WHERE world_id = ? AND id = 'r_south'", (WORLD,)
    )
    with_a_colony.commit()
    assert "r_south" not in _depots(with_a_colony, WORLD, "aurelia"), (
        "an occupier got a supply base out of the province it took"
    )
    assert "r_south" not in _depots(with_a_colony, WORLD, "karthos"), (
        "the sovereign kept a depot on ground it no longer holds"
    )


# ---------------------------------------------------------- the reported case, on the map


@pytest.fixture(scope="module")
def storm():
    preset = load_preset("gathering-storm")
    paths = pack_paths(preset.id, preset.map_source)
    if not all(path.exists() for path in paths):
        pytest.skip("geometry pack not baked; run `atlas map bake`")
    with PackSet.open(paths) as pack:
        seed = preset_seed(preset, pack, world_id=ERA)
    conn = create_world(":memory:", seed)
    preset_politics(conn, ERA, preset)
    yield conn
    conn.close()


def test_italy_does_not_lose_its_colonial_army_to_a_peaceful_year(storm):
    """RED on master, and it is the reported case exactly.

    The module docstring's own complaint: eight formations in Eritrea and Somaliland out of
    supply on turn 1 of every 1936 game, at peace, costing Italy 16% of its total military
    strength over the first year. On master all eight starve. Bari, on the tip of the Horn,
    is genuinely five hops from anything and is allowed to stay short -- a supply layer
    that never says no anywhere is not one.
    """
    starving = sorted(
        str(row["id"])
        for row in storm.execute(
            "SELECT id, region_id FROM unit WHERE world_id = ? AND nation_id = 'ita'",
            (ERA,),
        )
        if not in_supply(storm, ERA, "ita", str(row["region_id"]))
    )
    assert starving == ["u_ita_somalia_inf3"], starving


def test_italian_east_africa_is_supplied_from_italian_east_africa(storm):
    """RED on master: Eritrea's own best province cannot feed the army standing on it.

    ``r_m_eri_maekel`` is Italian-owned, Italian-controlled, and the most developed
    province in the colony. That it is out of the *metropole's* reach is correct and
    deliberate -- the sea crossing plus the distance is more than
    :data:`~atlas.engine.logistics._SUPPLY_RANGE` buys, which is the right answer to "can
    Rome feed Asmara". It is the wrong answer to "can Asmara feed Asmara".
    """
    assert "r_m_eri_maekel" in _depots(storm, ERA, "ita")
    assert "r_m_eri_maekel" in supply_map(storm, ERA, "ita")


def test_a_colonial_power_feeds_the_ground_it_holds(storm):
    """RED on master, and the scale of it. Britain feeds 14 of the 81 regions it holds.

    The empires are not asked for perfection -- a handful of Atlantic islands stay out of
    reach at :data:`~atlas.engine.logistics._DEPOT_DEVELOPMENT` 0.3, deliberately, because
    the value that feeds every last province is the value at which the bar admits
    everything and stops being a parameter. What is asked is that holding an empire is not
    a standing penalty: nine tenths of what a power holds should be ground it can feed.

    ==== ====== ====== ======
    era  master fixed  held
    ==== ====== ====== ======
    gbr  14     81     81
    fra  12     43     44
    ita  12     29     31
    ==== ====== ====== ======
    """
    short = {}
    for nation in ("gbr", "fra", "ita"):
        held = [
            str(row["id"])
            for row in storm.execute(
                "SELECT id FROM region WHERE world_id = ? AND kind <> 'ocean'"
                "   AND COALESCE(controller_nation_id, owner_nation_id) = ?", (ERA, nation)
            )
        ]
        reach = supply_map(storm, ERA, nation)
        fed = sum(1 for r in held if r in reach)
        if fed < 0.9 * len(held):
            short[nation] = f"{fed}/{len(held)}"
    assert not short, f"held ground its owner cannot supply: {short}"


def _open_a_front(conn, taker: str, victim: str) -> str:
    """The taker holds one of the victim's provinces: the first bordering ground it already
    holds, at war both ways. Controller moves; owner and home do not."""
    from atlas.engine.adjacency import graph_of

    graph = graph_of(conn, ERA)
    mine = {str(row["id"]) for row in conn.execute(
        "SELECT id FROM region WHERE world_id = ? AND kind <> 'ocean'"
        "   AND COALESCE(controller_nation_id, owner_nation_id) = ?", (ERA, taker))}
    frontier = sorted(
        str(row["id"]) for row in conn.execute(
            "SELECT id FROM region WHERE world_id = ? AND kind <> 'ocean'"
            "   AND owner_nation_id = ?", (ERA, victim))
        if mine & set(graph.get(str(row["id"]), ())))
    assert frontier, f"{taker} borders nothing of {victim}'s"
    conn.execute("UPDATE region SET controller_nation_id = ? WHERE world_id = ? AND id = ?",
                 (taker, ERA, frontier[0]))
    for a, b in ((taker, victim), (victim, taker)):
        conn.execute("UPDATE relation SET at_war = 1 WHERE world_id = ? AND nation_a = ?"
                     "   AND nation_b = ?", (ERA, a, b))
    conn.commit()
    return frontier[0]


def test_a_colony_can_supply_an_invasion_launched_from_it(storm):
    """**Decided 2026-09-18, and the one judgement the depot battery could not make.**

    Under owner-0.3 a developed colony is a depot, so Italy can open a front into Ethiopia
    from Eritrea and feed the province it takes. The shipped rule before that could not
    (0/1), and the only arm that refuses it -- owner-0.8 -- does so by starving eight
    Italian formations at peace in every 1936 game, which is the regression this module
    exists to fix. Yes, because it is what happened in 1935: the Ethiopian campaign was
    staged through Massawa and Asmara. A colony is a base as well as a possession.

    To revisit: the alternative is a second, higher bar for ground whose ``home_nation_id``
    is NULL (colonial-port in the battery), which also admits the beachhead and fed the
    *whole* Italian conquest (11/11 against owner-0.3's 6/11). There is no arm that feeds
    Eritrea at peace and refuses the front; that is the trade.
    """
    front = _open_a_front(storm, "ita", "eth")
    assert front in supply_map(storm, ERA, "ita")


def test_without_the_colony_the_same_front_is_not_supplied():
    """The control: the permission comes from the colony. The same Italian front, in a world
    where Italy's African colonies belong to somebody else, is fed from nowhere -- so the
    test above is measuring Eritrea and not some looser rule."""
    preset = load_preset("gathering-storm")
    paths = pack_paths(preset.id, preset.map_source)
    if not all(path.exists() for path in paths):
        pytest.skip("geometry pack not baked; run `atlas map bake`")
    with PackSet.open(paths) as pack:
        seed = preset_seed(preset, pack, world_id=ERA)
    world = create_world(":memory:", seed)
    try:
        preset_politics(world, ERA, preset)
        front = _open_a_front(world, "ita", "eth")
        colonies = [str(row["id"]) for row in world.execute(
            "SELECT id FROM region WHERE world_id = ? AND owner_nation_id = 'ita'"
            "   AND home_nation_id IS NULL AND kind <> 'ocean'", (ERA,))]
        assert colonies, "the premise: Italy holds colonies in 1936"
        world.executemany(
            "UPDATE region SET owner_nation_id = 'gbr', controller_nation_id = 'gbr'"
            " WHERE world_id = ? AND id = ?", [(ERA, rid) for rid in colonies])
        world.commit()
        assert front not in supply_map(world, ERA, "ita")
    finally:
        world.close()
