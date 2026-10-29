"""A foreign army on the map is what your intelligence service believes, not the truth.

Asked for in play: show what you know -- and do not know -- about an enemy's strength and
its upgrades. The map printed every army's exact strength to everybody, while the briefings
beside it went through `atlas.engine.intelligence` and its deliberately displaced bands. Now
the counters read the same fog: your own forces and your allies' are exact; anyone else's
are an estimate whose width is your network's quality, and a blind service is not told
which of their formations are upgraded.
"""

from __future__ import annotations

from atlas.map.query import load_map_view
from atlas.map.render import build_scene
from atlas.map.scene_model import MarkerKind

WORLD, ME, NEIGHBOUR = "sandbox", "aurelia", "veyra"


class _Flat:
    key = "flat"

    def project(self, lon: float, lat: float) -> tuple[float, float]:
        return lon, -lat


def _setup(conn) -> None:
    conn.execute("INSERT INTO unit_template (world_id, kind, domain, base_strength)"
                 " VALUES (?, 'armour', 'land', 6.0)", (WORLD,))
    conn.execute("INSERT INTO unit (id, world_id, nation_id, region_id, kind, strength)"
                 " VALUES ('u_mine', ?, ?, 'r_north', 'armour', 7.3)", (WORLD, ME))
    conn.execute("INSERT INTO unit (id, world_id, nation_id, region_id, kind, strength)"
                 " VALUES ('u_theirs', ?, ?, 'r_east', 'armour', 23.7)", (WORLD, NEIGHBOUR))
    conn.execute("INSERT INTO technology (world_id, nation_id, tech_id, completed_turn)"
                 " VALUES (?, ?, 'armour_mk2', 1)", (WORLD, NEIGHBOUR))


def _network(conn, strength: float) -> None:
    conn.execute("INSERT OR REPLACE INTO intel_network (id, world_id, nation_id,"
                 " target_nation_id, strength) VALUES (?, ?, ?, ?, ?)",
                 (f"int_{ME}_{NEIGHBOUR}", WORLD, ME, NEIGHBOUR, strength))


def _counters(conn) -> dict[str, object]:
    view = load_map_view(conn, WORLD, observer_nation_id=ME)
    points = {r.geo_key: (i * 10.0, 0.0) for i, r in enumerate(view.regions)}
    scene = build_scene(view, projection=_Flat(), pack_id="sandbox", label_points=points,
                        calm=True)
    return {m.region_id: m for m in scene.markers if m.kind == MarkerKind.UNIT}


def test_your_own_forces_are_exact_and_a_neighbours_are_an_estimate(conn):
    _setup(conn)
    _network(conn, 30.0)
    counters = _counters(conn)
    assert counters["r_north"].label == "7.3"
    theirs = counters["r_east"]
    assert "-" in theirs.label or theirs.label.startswith("~")
    assert "by your estimate" in theirs.tooltip
    assert "Your intelligence in Veyra is" in theirs.tooltip
    assert "23.7" not in theirs.tooltip
    # The same fog on every redraw: a guess that changed on a pan would be no guess at all.
    assert _counters(conn)["r_east"].label == theirs.label


def test_a_better_service_reads_closer(conn):
    _setup(conn)
    _network(conn, 0.0)
    blind = _counters(conn)["r_east"].label
    _network(conn, 100.0)
    sharp = _counters(conn)["r_east"].label
    assert "-" in blind and sharp.startswith("~")


def test_upgrades_are_hidden_from_a_blind_service(conn):
    _setup(conn)
    _network(conn, 0.0)
    blind = _counters(conn)["r_east"]
    assert blind.mark == 1 and "upgrades unknown" in blind.tooltip
    _network(conn, 90.0)
    assert _counters(conn)["r_east"].mark == 2


def test_an_allys_forces_are_exact(conn):
    _setup(conn)
    conn.execute("UPDATE relation SET treaty_status = 'alliance' WHERE world_id = ?"
                 " AND nation_a = ? AND nation_b = ?", (WORLD, NEIGHBOUR, ME))
    assert _counters(conn)["r_east"].label == "24"
