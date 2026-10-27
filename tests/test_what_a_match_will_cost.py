"""The estimate of a match's calls, held to what a counting mock actually receives.

One mock per provider key, so the count is by provider, the unit a quota is spent in.
The two rates (council sessions, retries) are set to zero here: the sandbox convenes no
council and the mock never fails, so everything left is the structural part, and that has
to be exact.
"""

from __future__ import annotations

import pytest

from atlas.engine import bots
from atlas.engine.eve import Bloc, EveEngine, assign_blocs
from atlas.engine.quota import estimate_match, exhausted_providers
from atlas.engine.routing import Router
from atlas.engine.turn_engine import TurnEngine
from atlas.providers.cli_provider import default_role_models
from atlas.providers.mock import MockProvider
from atlas.state.models import Role

WORLD = "sandbox"
KEYS = ("claude", "codex", "antigravity")


@pytest.fixture(autouse=True)
def restore_switches():
    was = bots.set_bots()
    yield
    bots.set_bots(*was)


def _pins():
    return dict(default_role_models())


def _counted(providers) -> dict[str, float]:
    return {k: float(len(p.calls)) for k, p in providers.items() if p.calls}


def _exact(conn, rounds, **kw):
    return estimate_match(conn, WORLD, rounds=rounds, pins=_pins(), organisation_rate=0.0,
                          retry_allowance=0.0, **kw)


async def _jumps(conn, rounds: int) -> dict[str, float]:
    providers = {k: MockProvider() for k in KEYS}
    engine = TurnEngine(conn=conn, world_id=WORLD, providers=providers,
                        router=Router(pins=_pins()))
    for _ in range(rounds):
        await engine.jump()
    return _counted(providers)


async def test_the_estimate_is_what_the_mock_counts(conn):
    estimate = _exact(conn, 4, eve=False)
    assert await _jumps(conn, 4) == estimate.by_provider


async def test_with_bots_the_estimate_drops_by_exactly_what_they_save(conn):
    scopes = frozenset({"tracked", "gambit"})
    estimate = _exact(conn, 4, eve=False, bot_roles=scopes)
    bots.set_bots(roles=scopes)
    counted = await _jumps(conn, 4)
    assert counted == estimate.by_provider
    assert estimate.total() < _exact(conn, 4, eve=False).total()


async def test_an_eve_round_is_counted_bloc_by_bloc(conn):
    blocs = [Bloc(id=f"bloc_{n}", name=f"Bloc {n}", provider_key=k, model=m,
                  colour="#888888", nations=(n,))
             for n, k, m in (("aurelia", "claude", "claude-sonnet-5"),
                             ("karthos", "codex", "gpt-5.6-terra"),
                             ("veyra", "antigravity", "gemini-3.6-flash-high"))]
    assign_blocs(conn, WORLD, blocs)
    estimate = _exact(conn, 3)

    providers = {k: MockProvider() for k in KEYS}
    router = Router(pins=_pins())
    eve = EveEngine(conn=conn, world_id=WORLD, providers=providers, router=router)
    turns = TurnEngine(conn=conn, world_id=WORLD, providers=providers, router=router)
    for _ in range(3):
        await eve.run_round()
        await turns.jump()
    assert _counted(providers) == estimate.by_provider


def test_the_rates_are_stated_as_rates():
    """A reader must be able to tell the guessed terms from the fixed ones."""
    import inspect

    from atlas.engine import quota

    assert "RATE" in inspect.getsource(quota.estimate_match)


def test_retries_are_added_on_top(conn):
    plain = _exact(conn, 10, eve=False)
    padded = estimate_match(conn, WORLD, rounds=10, pins=_pins(), eve=False,
                            organisation_rate=0.0, retry_allowance=0.1)
    for key, calls in plain.by_provider.items():
        assert padded.by_provider[key] == pytest.approx(calls * 1.1, abs=0.1)


# ------------------------------------------------------------------------ running dry


def _log(conn, turn: int, provider: str, outcome: str) -> None:
    conn.execute(
        "INSERT INTO routing_log (world_id, turn, role, provider, requested_model, outcome,"
        "                         created_at) VALUES (?, ?, ?, ?, 'm', ?, '2026-09-21')",
        (WORLD, turn, Role.TRACKED_AGENT.value, provider, outcome))
    conn.commit()


def test_a_provider_that_ran_dry_is_named(conn):
    _log(conn, 3, "codex", "quota_exhausted")
    _log(conn, 3, "claude", "ok")
    assert exhausted_providers(conn, WORLD, since_turn=3) == {"codex"}


def test_an_old_exhaustion_is_not_news(conn):
    """Quota comes back; a dry spell two turns ago must not pause the match today."""
    _log(conn, 1, "codex", "quota_exhausted")
    _log(conn, 3, "codex", "ok")
    assert exhausted_providers(conn, WORLD, since_turn=3) == set()


def test_an_ordinary_failure_is_not_running_dry(conn):
    """A timeout is not an empty quota; pausing a match for one would stop it for nothing."""
    _log(conn, 3, "antigravity", "failed")
    _log(conn, 3, "codex", "timeout")
    assert exhausted_providers(conn, WORLD, since_turn=3) == set()


def test_nations_kept_off_the_bots_are_still_priced(conn):
    """An excluded nation is a model call whenever Focus tracks it, so the estimate keeps
    it -- as an upper bound, since whether it will be tracked is not known in advance."""
    botted = _exact(conn, 5, eve=False, bot_roles=frozenset({"tracked"}))
    kept = _exact(conn, 5, eve=False, bot_roles=frozenset({"tracked"}),
                  bot_excluded=frozenset({"veyra"}))
    assert kept.by_role.get("tracked_agent", 0) == 4.0  # one nation, from the second jump
    assert botted.by_role.get("tracked_agent", 0) == 0
    assert any("UPPER BOUND" in line for line in kept.lines)



def test_study_subjects_are_priced_every_round_on_their_own_pins(conn):
    without = _exact(conn, 6, eve=False)
    estimate = _exact(conn, 6, eve=False, study_subjects={"veyra": "antigravity"})
    assert estimate.by_role["study_subject"] == 6.0
    # On top of whatever else that provider carries (the default referee is antigravity).
    assert estimate.by_provider["antigravity"] - without.by_provider["antigravity"] == 6.0


def test_a_subjects_gambit_stays_priced_when_gambits_are_botted(conn):
    """The mock run of 2026-09-22 made 2 subject gambits the estimate had priced at zero."""
    botted = _exact(conn, 6, eve=False, bot_roles=frozenset({"gambit"}),
                    study_subjects={"veyra": "claude"})
    assert botted.by_role.get("gambit", 0) > 0
    assert any("UPPER BOUND" in line and "gambit" in line for line in botted.lines)
