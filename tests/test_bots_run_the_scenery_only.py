"""Bots run the scenery -- background neutrals, their economies, routine council votes --
and never a study subject: no bloc nation, and in a betrayal study no victim.

Every test turns the bots fully on and then names the subject explicitly, which is the
worst case: if a subject can be botted at all, this is how it would happen.
"""

from __future__ import annotations

import pytest

from atlas.engine import bots
from atlas.engine.eve import Bloc, EveEngine, assign_blocs
from atlas.engine.production import autonomous_investment
from atlas.engine.routing import Router
from atlas.engine.turn_engine import TurnEngine
from atlas.providers.cli_provider import default_role_models
from atlas.providers.mock import MockProvider
from atlas.state.models import Role

WORLD = "sandbox"
KEYS = ("claude", "codex", "antigravity")
EVERYTHING = frozenset(bots.SCOPES)


@pytest.fixture(autouse=True)
def restore_switches():
    was = bots.set_bots()
    yield
    bots.set_bots(*was)


def _providers():
    return {k: MockProvider() for k in KEYS}


def _asked(providers, nation: str, role: Role = Role.TRACKED_AGENT) -> dict[str, int]:
    """How many times each provider was asked to play ``nation``."""
    header = f"(`{nation}`)"
    return {k: sum(1 for c in p.calls if c.role is role and header in c.prompt[:400])
            for k, p in providers.items()}


def _bot_batches(conn, nation: str) -> int:
    return int(conn.execute(
        "SELECT COUNT(*) FROM diff_batch WHERE proposer_model = ? AND proposer_nation_id = ?",
        (bots.BOT_MODEL, nation)).fetchone()[0])


# ------------------------------------------------------------------------ the blocs


async def test_every_bot_scope_on_and_every_bloc_nation_still_answers_by_model(conn):
    """EVE never consults the bot layer: a bloc's move is its model's, full stop."""
    blocs = [Bloc(id=f"bloc_{n}", name=f"Bloc {n}", provider_key=k, model=m,
                  colour="#888888", nations=(n,))
             for n, k, m in (("aurelia", "claude", "claude-sonnet-5"),
                             ("karthos", "codex", "gpt-5.6-terra"),
                             ("veyra", "antigravity", "gemini-3.6-flash-high"))]
    assign_blocs(conn, WORLD, blocs)
    bots.set_bots(roles=EVERYTHING, nations={"aurelia", "karthos", "veyra"})
    providers = _providers()
    router = Router(pins=default_role_models())
    eve = EveEngine(conn=conn, world_id=WORLD, providers=providers, router=router)
    turns = TurnEngine(conn=conn, world_id=WORLD, providers=providers, router=router)
    for _ in range(2):
        await eve.run_round()
        await turns.jump()

    for nation, key in (("aurelia", "claude"), ("karthos", "codex"), ("veyra", "antigravity")):
        assert _asked(providers, nation)[key] == 2, nation
        assert _bot_batches(conn, nation) == 0, nation


async def test_a_bloc_nation_named_to_the_bots_is_still_not_theirs(conn):
    """Naming a bloc nation in `set_bots(nations=...)` is ignored by the engine's gate."""
    blocs = [Bloc(id="bloc_k", name="Bloc K", provider_key="codex", model="gpt-5.6-terra",
                  colour="#888888", nations=("karthos",))]
    assign_blocs(conn, WORLD, blocs)
    conn.execute("UPDATE nation SET is_tracked = 1 WHERE id = 'karthos'")
    conn.commit()
    bots.set_bots(roles=EVERYTHING, nations={"karthos"})
    engine = TurnEngine(conn=conn, world_id=WORLD, providers=_providers(),
                        router=Router(pins=default_role_models()))
    assert "karthos" in engine._protected()
    await engine.jump()
    assert _bot_batches(conn, "karthos") == 0


# ------------------------------------------------------------------------ study subjects


def _engine_with_subject(conn, providers, pin=("claude", "claude-sonnet-5")) -> TurnEngine:
    return TurnEngine(conn=conn, world_id=WORLD, providers=providers,
                      router=Router(pins=default_role_models()),
                      study_subjects={"veyra": pin})


async def test_a_study_subject_is_asked_every_turn_on_its_own_pin_even_untracked(conn):
    """The 2026-09-22 failure: Malaysia, a betrayal victim, got no model call in 15 turns
    because Focus never tracked it."""
    conn.execute("UPDATE nation SET is_tracked = 0")
    conn.commit()
    providers = _providers()
    engine = _engine_with_subject(conn, providers)
    for _ in range(3):
        await engine.jump()
    assert _asked(providers, "veyra") == {"claude": 3, "codex": 0, "antigravity": 0}


async def test_a_study_subject_is_never_botted_even_when_named(conn):
    bots.set_bots(roles=EVERYTHING, nations={"veyra"})
    providers = _providers()
    engine = _engine_with_subject(conn, providers)
    for _ in range(3):
        await engine.jump()
    assert _bot_batches(conn, "veyra") == 0
    assert _asked(providers, "veyra")["claude"] == 3
    # The scenery still is: Karthos, a neutral that is nobody's subject, is the bot's.
    assert _bot_batches(conn, "karthos") > 0


async def test_a_study_subject_is_asked_once_when_focus_tracks_it_too(conn):
    conn.execute("UPDATE nation SET is_tracked = 1 WHERE id = 'veyra'")
    conn.commit()
    providers = _providers()
    engine = _engine_with_subject(conn, providers)
    await engine.jump()
    assert sum(_asked(providers, "veyra").values()) == 1


def test_the_master_ai_does_not_also_govern_a_study_subject(conn):
    """An untracked subject would otherwise get a model's orders *and* the master AI's."""
    conn.execute("UPDATE nation SET is_tracked = 0, treasury = 500")
    conn.commit()
    ungoverned = autonomous_investment(conn, WORLD, turn=1, date="2026-02-01")
    conn.rollback()
    governed = autonomous_investment(conn, WORLD, turn=1, date="2026-02-01",
                                     exclude=frozenset({"veyra"}))
    assert any("Veyra" in line or "veyra" in line for line in ungoverned.invested), \
        "premise: the master AI invests for an untracked Veyra"
    assert not any("Veyra" in line or "veyra" in line for line in governed.invested)


async def test_a_study_subjects_gambit_is_never_the_bots(conn):
    """The gambit channel is scenery too -- except for a subject, whose gambit is its own."""
    conn.execute("UPDATE nation SET is_tracked = 1 WHERE id IN ('karthos', 'veyra')")
    conn.commit()
    bots.set_bots(roles={"gambit"})
    providers = _providers()
    engine = _engine_with_subject(conn, providers)
    for _ in range(3):
        await engine.jump()
    gambits_for_veyra = sum(_asked(providers, "veyra", Role.GAMBIT).values())
    gambits_for_karthos = sum(_asked(providers, "karthos", Role.GAMBIT).values())
    assert gambits_for_karthos == 0, "a scenery nation's gambit is the bot's"
    assert gambits_for_veyra > 0, "a subject's gambit is asked of a model"


async def test_through_the_engine_a_study_subject_gets_no_master_ai_orders(conn):
    """Wired, not just possible: the jump itself passes the exclusion. The mock subject
    files nothing, so anything under construction for it is the master AI's."""
    from atlas.engine.production import in_progress

    conn.execute("UPDATE nation SET is_tracked = 0, treasury = 500")
    conn.commit()
    plain = TurnEngine(conn=conn, world_id=WORLD, providers=_providers(),
                       router=Router(pins=default_role_models()))
    await plain.jump()
    assert in_progress(conn, WORLD, "veyra") > 0, "premise: untracked, the master AI builds"


async def test_through_the_engine_the_master_ai_leaves_a_subject_alone(conn):
    from atlas.engine.production import in_progress

    conn.execute("UPDATE nation SET is_tracked = 0, treasury = 500")
    conn.commit()
    await _engine_with_subject(conn, _providers()).jump()
    assert in_progress(conn, WORLD, "veyra") == 0
