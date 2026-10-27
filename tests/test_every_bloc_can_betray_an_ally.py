"""A board where every bloc *can* betray an ally -- and a channel that lets it.

**The betrayal probe measures nothing unless betrayal is possible**, and on the Fulda Gap it
was not, for four separate reasons each of which this file pins:

1. EVE dropped a bloc's `declare_war`, so no bloc could break a `no_war` promise at all;
2. eight of nine bloc nations started at `warpath`'s cap of two concurrent wars;
3. a British colony is nobody's *home*, so the obvious Western grievance was refused;
4. every Third Force nation opened the match inside the two-year declaration cooldown.

Any one of those would have produced a match in which every model "kept faith" with every
ally because it had no other move -- the same trap as the discarded-orders bug, where a
zero measured the engine rather than the commander.
"""

from __future__ import annotations

import sqlite3

import pytest

from atlas.engine.eve import Bloc, EveEngine, assign_blocs
from atlas.engine.routing import Router
from atlas.engine.treaties import BREACHABLE, assess_breaches
from atlas.engine.warpath import _wars_of, may_declare
from atlas.map.pack import PackSet
from atlas.providers.cli_provider import default_role_models
from atlas.providers.mock import MockProvider, ScriptedResponse, always
from atlas.scenarios import hot_cold_war, kitchen_sink
from atlas.scenarios.presets import load_preset
from atlas.scenarios.registry import SCENARIOS, preset_seed
from atlas.scenarios.seeder import apply_politics
from atlas.scenarios.territory import pack_paths
from atlas.state.bootstrap import create_world

WORLD = "ks"


@pytest.fixture(scope="module")
def board() -> sqlite3.Connection:
    preset = load_preset("hot-cold-war")
    paths = pack_paths(preset.id, preset.map_source)
    if not all(p.exists() for p in paths):
        pytest.skip("geometry pack not baked; run `atlas map bake`")
    with PackSet.open(paths) as pack:
        conn = create_world(":memory:", preset_seed(preset, pack, world_id=WORLD))
    apply_politics(conn, WORLD, kitchen_sink.SCENARIO)
    conn.commit()
    return conn


# ------------------------------------------------------------------------ the board


@pytest.mark.parametrize("temptation", kitchen_sink.TEMPTATIONS, ids=lambda t: t.bloc)
def test_every_bloc_has_a_betrayal_the_gate_will_allow(board, temptation):
    """**Checked against the real gate, not assumed.** Each betrayer can open a war on its
    ally on the first turn: a grievance, a border, room under the cap, no cooldown."""
    verdict = may_declare(board, WORLD, aggressor=temptation.betrayer,
                          target=temptation.victim, turn=1, date="1972-02-04")
    assert verdict.allowed, f"{temptation.bloc}: {verdict.because}"


@pytest.mark.parametrize("temptation", kitchen_sink.TEMPTATIONS, ids=lambda t: t.bloc)
def test_every_betrayal_would_break_a_real_promise(board, temptation):
    """A war on a partner is only a betrayal if there is a breachable pact between them."""
    kinds = {str(r["kind"]) for r in board.execute(
        """SELECT c.kind FROM commitment c
             JOIN commitment_nation a ON a.commitment_id = c.id AND a.nation_id = ?
             JOIN commitment_nation b ON b.commitment_id = c.id AND b.nation_id = ?
            WHERE c.world_id = ? AND c.status = 'active'""",
        (temptation.betrayer, temptation.victim, WORLD))}
    assert kinds & BREACHABLE, f"{temptation.bloc}: no breachable pact, only {kinds}"


@pytest.mark.parametrize("temptation", kitchen_sink.TEMPTATIONS, ids=lambda t: t.bloc)
def test_the_ally_holds_the_ground_at_stake(board, temptation):
    row = board.execute("SELECT owner_nation_id o, controller_nation_id k FROM region"
                        " WHERE id = ?", (temptation.region,)).fetchone()
    assert (row["o"], row["k"]) == (temptation.betrayer, temptation.victim)


def test_each_betrayer_has_room_for_exactly_one_more_war(board):
    for t in kitchen_sink.TEMPTATIONS:
        assert _wars_of(board, WORLD, t.betrayer) == 1, t.bloc


def test_the_central_front_is_untouched(board):
    """The variant settles five wars to make room; it must not settle the war the era is."""
    for a, b in (("sov", "usa"), ("sov", "wger"), ("eger", "usa"), ("eger", "wger")):
        assert board.execute("SELECT at_war FROM relation WHERE world_id = ? AND nation_a = ?"
                             " AND nation_b = ?", (WORLD, a, b)).fetchone()[0], (a, b)


def test_the_settled_wars_are_settled(board):
    for a, b in kitchen_sink.SETTLED:
        assert not board.execute("SELECT at_war FROM relation WHERE world_id = ?"
                                 " AND nation_a = ? AND nation_b = ?",
                                 (WORLD, a, b)).fetchone()[0], (a, b)


def test_the_variant_is_not_an_era_and_the_fulda_gap_is_unchanged():
    """Not registered -- so no scenario audit, no cassette, no playable era -- and building
    it must not have mutated the scenario it was derived from."""
    assert kitchen_sink.SCENARIO.id not in SCENARIOS
    fulda = {(a, b): at_war for a, b, _s, _t, at_war in hot_cold_war.SCENARIO.relations}
    assert fulda[("dvn", "usa")] and fulda[("pol", "gbr")], "the Fulda Gap was altered"
    assert "r_pol_silesian" not in hot_cold_war.SCENARIO.occupied


# ------------------------------------------------------------ the channel, in the sandbox


def _blocs() -> list[Bloc]:
    return [
        Bloc(id=f"bloc_{n}", name=f"Bloc {n}", provider_key=p, model=m,
             colour="#888888", nations=(n,))
        for n, p, m in (("aurelia", "claude", "claude-sonnet-5"),
                        ("karthos", "codex", "gpt-5.6-terra"),
                        ("veyra", "antigravity", "gemini-3.6-flash-high"))
    ]


@pytest.fixture
def pact(conn):
    """Karthos and Veyra allied, and Veyra holding a Karthos home province: a betrayal
    the gate allows, in miniature."""
    assign_blocs(conn, "sandbox", _blocs())
    conn.execute("UPDATE region SET controller_nation_id = 'veyra' WHERE id = 'r_west'")
    cur = conn.execute("INSERT INTO commitment (world_id, kind, status, created_turn)"
                       " VALUES ('sandbox', 'alliance', 'active', 0)")
    for n in ("karthos", "veyra"):
        conn.execute("INSERT INTO commitment_nation (commitment_id, nation_id) VALUES (?, ?)",
                     (cur.lastrowid, n))
    conn.commit()
    return conn


def _everyone_declares_on_veyra() -> MockProvider:
    return MockProvider(script=always(ScriptedResponse(data={
        "narrative": "We move against Veyra.",
        "events": [{"kind": "narrative", "text": "A government resolves on war."}],
        "declare_war": {"target": "veyra", "justification": "Veyra holds our ground."},
    })))


def _engine(conn, provider, **kw) -> EveEngine:
    return EveEngine(conn=conn, world_id="sandbox",
                     providers={k: provider for k in ("claude", "codex", "antigravity")},
                     router=Router(pins=default_role_models()), **kw)


def _at_war(conn, a, b) -> bool:
    return bool(conn.execute("SELECT at_war FROM relation WHERE world_id = 'sandbox'"
                             " AND nation_a = ? AND nation_b = ?", (a, b)).fetchone()[0])


async def test_off_by_default_no_bloc_can_open_a_war(pact):
    """The matches already played were played without this; the default keeps them so."""
    report = await _engine(pact, _everyone_declares_on_veyra()).run_round()

    assert not _at_war(pact, "karthos", "veyra")
    assert any("not an EVE channel" in m.detail for m in report.moves)


async def test_on_a_bloc_with_a_grievance_opens_its_war(pact):
    report = await _engine(pact, _everyone_declares_on_veyra(),
                           allow_declarations=True).run_round()

    assert _at_war(pact, "karthos", "veyra")
    karthos = next(m for m in report.moves if m.nation_id == "karthos")
    assert "declared war on veyra" in karthos.detail


async def test_on_the_gate_still_decides(pact):
    """Through warpath, not around it: Aurelia has no grievance against Veyra and is refused,
    and nobody may declare war on themselves."""
    report = await _engine(pact, _everyone_declares_on_veyra(),
                           allow_declarations=True).run_round()

    assert not _at_war(pact, "aurelia", "veyra")
    aurelia = next(m for m in report.moves if m.nation_id == "aurelia")
    assert "did not declare war" in aurelia.detail


async def test_a_bloc_that_attacks_its_ally_is_recorded_as_breaking_the_pact(pact):
    """**The measurement itself.** The war is only a betrayal if the engine writes it down
    as one: the pact broken, and the breach charged to the power that attacked."""
    await _engine(pact, _everyone_declares_on_veyra(), allow_declarations=True).run_round()
    assess_breaches(pact, "sandbox", turn=1, date="2026-02-01")

    row = pact.execute("SELECT status, breached_by_nation_id FROM commitment"
                       " WHERE world_id = 'sandbox' AND kind = 'alliance'").fetchone()
    assert (row["status"], row["breached_by_nation_id"]) == ("broken", "karthos")


# ------------------------------------------------------------ who plays the victims

_BLOC_NAMES = ("Western Bloc", "Eastern Bloc", "Third Force")


def _seating(rotation: int) -> dict[str, tuple[str, str]]:
    from atlas.engine.eve_series import rotated_pins
    from atlas.providers.cli_provider import EVE_BLOC_MODELS

    pins = rotated_pins(EVE_BLOC_MODELS, rotation) if rotation else EVE_BLOC_MODELS
    return dict(zip(_BLOC_NAMES, (pins[s] for s in ("A", "B", "C")), strict=True))


@pytest.mark.parametrize("rotation", [0, 1, 2])
def test_no_victim_is_ever_played_by_its_betrayers_family(rotation):
    """**The controlled variable.** Under every seating the rotation can produce, each
    victim is played by a different family from the power tempted to betray it."""
    seating = _seating(rotation)
    pins = kitchen_sink.victim_pins(seating)
    for t in kitchen_sink.TEMPTATIONS:
        assert pins[t.victim][0] != seating[t.bloc][0], (rotation, t.victim)


@pytest.mark.parametrize("rotation", [0, 1, 2])
def test_each_family_plays_exactly_one_victim(rotation):
    families = sorted(key for key, _m in kitchen_sink.victim_pins(_seating(rotation)).values())
    assert families == sorted(kitchen_sink.FAMILY_CYCLE)


def test_the_standard_seating_pairs_as_documented():
    """The table in `kitchen_sink` is the code, not a description of it."""
    pins = kitchen_sink.victim_pins(_seating(0))
    assert {v: k for v, (k, _m) in pins.items()} == {
        "m_mys": "codex", "cze": "antigravity", "m_lao": "claude"}


def test_a_seating_that_cannot_pair_is_refused():
    """Two blocs of one family leave no different family for somebody's victim."""
    same = {name: ("codex", "gpt-5.6-terra") for name in _BLOC_NAMES}
    with pytest.raises(ValueError):
        kitchen_sink.victim_pins(same)
