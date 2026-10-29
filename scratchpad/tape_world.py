"""The world a tape is recorded in and replayed against -- one definition for both.

**The pairing is part of the tape** (see `cassettes/INDEX.json`): a replay reproduces a run
only if it builds the same world, seats the same models and gives the same orders. So the
recorder and the replay both read this file, and a profile is never edited once a tape that
names it is active -- add a new one instead.

A profile plays the game the way `atlas start --api` does: models where prose and judgement
show (the world simulator, diplomacy, the advisor, freeform orders), rules everywhere else.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

WORLD = "selftest"


@dataclass(frozen=True)
class Profile:
    preset: str
    player: str
    turns: int
    days: int = 30
    #: The player's orders, by turn. A turn not listed gives none.
    orders: dict[int, str] = field(default_factory=dict)


PROFILES: dict[str, Profile] = {
    # Turkey in 2026: a war in the first month, then the orders that reach the model seats
    # -- a scheme, a freeform act, research and a peace offer -- so a tape records every
    # channel a player's turn can open.
    "api-modern-tur": Profile(
        preset="modern-day", player="tur", turns=8,
        orders={
            1: "Declare war on Syria. Research Armour Mk II.",
            2: "Capture Syria. Host a regional summit on water rights in Ankara.",
            3: "Capture Syria. Fund Kurdish opposition groups inside Iran.",
            4: "Reinforce my army. Capture Syria.",
            5: "Capture Syria. Launch a national campaign to recruit engineers.",
            6: "Sue for peace with Syria.",
            7: "Research Infantry Mk II.",
        },
    ),
}


def build(profile: Profile):
    """A fresh in-memory world for ``profile``, seeded the same way every time."""
    from atlas import selftest

    state = {"paths": selftest._packs(profile.preset)}
    selftest._world(state, profile.preset, profile.player)
    return state["conn"]


def seat(choice: str) -> None:
    """Seat the models and the bots exactly as `atlas start --api` does for ``choice``."""
    from atlas.engine import bots
    from atlas.providers.api_choice import role_models
    from atlas.providers.cli_provider import use_api_pins

    bots.set_bots(roles=bots.SCOPES | {"parser"})
    use_api_pins(role_models(choice))


async def play(conn, profile: Profile, provider, *, on_turn=None) -> list[dict]:
    """Play ``profile`` with ``provider`` in every seat. Returns one record per turn."""
    from atlas.engine.parser import parse_orders
    from atlas.engine.routing import Router
    from atlas.engine.turn_engine import TurnEngine
    from atlas.providers.cli_provider import default_role_models
    from atlas.state.bootstrap import fingerprint

    providers = {key: provider for key in ("claude", "codex", "antigravity")}
    router = Router(pins=default_role_models())
    engine = TurnEngine(conn=conn, world_id=WORLD, providers=providers, router=router)
    records: list[dict] = []
    for turn in range(1, profile.turns + 1):
        actions: list[dict] = []
        text = profile.orders.get(turn, "")
        if text:
            parsed = await parse_orders(conn, WORLD, nation_id=profile.player, raw_text=text,
                                        providers=providers, router=router)
            actions = list(parsed.actions)
        report = await engine.jump(days=profile.days, actions=actions)
        record = {"turn": turn, "fingerprint": fingerprint(conn, WORLD),
                  "orders": len(actions), "status": str(getattr(report, "status", ""))}
        records.append(record)
        if on_turn is not None:
            on_turn(record)
    return records


def run(coro):
    return asyncio.run(coro)
