"""`atlas selftest`: does this installation work, with no model behind it?

The frozen build's smoke test, and the thing a clean-VM CI job runs. It answers the
questions a packaging mistake breaks, in the order they would break:

1. the shipped fonts register (a missing data file falls back to system faces silently);
2. the map packs for a scenario resolve and open (a player's install never bakes);
3. a world builds from them, and a few turns run on the mock provider with every bot
   scope on -- the whole turn pipeline, SQLite included, without one model call;
4. the main menu opens over this machine's saves, and the game window opens on the
   world (Qt platform plugins, styles, image formats).

Each check is recorded whether it passes or not, so a failed run still says how far it
got. The report is JSON, written to ``--report`` when given -- a windowed executable has no
console to print to.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import sys
import time
import traceback
from pathlib import Path
from typing import Any

__all__ = ["run_selftest"]


def _check(report: dict[str, Any], name: str, action) -> Any:
    started = time.monotonic()
    entry: dict[str, Any] = {"name": name}
    report["checks"].append(entry)
    try:
        detail = action()
    except Exception as exc:  # noqa: BLE001 -- the point is to record any failure
        entry.update(ok=False, error=f"{type(exc).__name__}: {exc}",
                     traceback=traceback.format_exc(limit=8))
        raise
    entry.update(ok=True, detail=detail, seconds=round(time.monotonic() - started, 2))
    return detail


def run_selftest(*, preset_id: str = "modern-day", player: str = "rus", turns: int = 3,
                 report_path: Path | None = None, open_window: bool = True) -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from .config.paths import bundled_packs_root, is_frozen

    report: dict[str, Any] = {
        "ok": False, "frozen": is_frozen(), "python": sys.version.split()[0],
        "platform": platform.platform(), "preset": preset_id, "checks": [],
        "bundled_packs": str(bundled_packs_root()) if bundled_packs_root() else None,
    }
    state: dict[str, Any] = {}
    try:
        _check(report, "fonts", _fonts)
        state["paths"] = _check(report, "packs", lambda: _packs(preset_id))
        _check(report, "world", lambda: _world(state, preset_id, player))
        _check(report, "turns", lambda: _turns(state, turns))
        if open_window:
            _check(report, "menu", _menu)
            _check(report, "window", lambda: _window(state, player))
        report["ok"] = True
    except Exception:  # noqa: BLE001 -- already recorded against its check
        pass
    finally:
        conn = state.get("conn")
        if conn is not None:
            conn.close()
    text = json.dumps(report, indent=2, default=str)
    if report_path is not None:
        Path(report_path).write_text(text + "\n", encoding="utf-8")
    for entry in report["checks"]:
        mark = "ok  " if entry.get("ok") else "FAIL"
        print(f"{mark} {entry['name']:<7} {entry.get('detail') or entry.get('error')}")
    print("selftest passed" if report["ok"] else "selftest FAILED")
    return 0 if report["ok"] else 1


def _fonts() -> list[str]:
    from .ui.app import ensure_app
    from .ui.theme import FONT, load_fonts

    ensure_app()
    families = load_fonts()
    missing = {FONT.serif, FONT.sans, FONT.mono} - set(families)
    if missing:
        raise RuntimeError(f"fonts did not register: {sorted(missing)}")
    return families


def _packs(preset_id: str) -> list[str]:
    from .map.pack import PackSet
    from .scenarios.presets import load_preset
    from .scenarios.territory import pack_paths

    preset = load_preset(preset_id)
    if preset is None:
        raise RuntimeError(f"no preset {preset_id!r}")
    paths = pack_paths(preset.id, preset.map_source)
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        raise RuntimeError(f"missing packs: {missing}")
    with PackSet.open(paths):
        pass
    return [str(path) for path in paths]


def _world(state: dict[str, Any], preset_id: str, player: str) -> str:
    from .map.pack import PackSet
    from .scenarios.presets import load_preset
    from .scenarios.registry import preset_politics, preset_seed
    from .state.bootstrap import create_world
    from .state.models import Difficulty

    preset = load_preset(preset_id)
    paths = [Path(path) for path in state["paths"]]
    with PackSet.open(paths) as pack:
        seed = preset_seed(preset, pack, world_id="selftest", player=player,
                           difficulty=Difficulty.NORMAL, seed=7)
        conn = create_world(":memory:", seed)
        preset_politics(conn, "selftest", preset)
    state["conn"] = conn
    regions = conn.execute("SELECT COUNT(*) FROM region").fetchone()[0]
    return f"{regions} regions"


def _turns(state: dict[str, Any], turns: int) -> list[str]:
    from .engine import bots
    from .engine.routing import Router
    from .engine.turn_engine import TurnEngine
    from .providers.cli_provider import default_role_models
    from .providers.mock import MockProvider
    from .state.models import TurnStatus

    was = bots.set_bots(roles=bots.SCOPES)
    try:
        shared = MockProvider()
        engine = TurnEngine(conn=state["conn"], world_id="selftest",
                            providers={key: shared for key in ("claude", "codex", "antigravity")},
                            router=Router(pins=default_role_models()))

        async def run() -> list[str]:
            return [(await engine.jump(days=30)).status.value for _ in range(turns)]

        statuses = asyncio.run(run())
    finally:
        bots.set_bots(*was)
    if any(status != TurnStatus.SEALED.value for status in statuses):
        raise RuntimeError(f"turn statuses {statuses}")
    return statuses


def _menu() -> str:
    from .state.catalog import list_worlds
    from .ui.app import ensure_app
    from .ui.main_menu import MainMenu
    from .ui.theme import DARK

    app = ensure_app()
    worlds = list_worlds()
    menu = MainMenu(DARK, worlds, provider_status="selftest")
    menu.show()
    app.processEvents()
    menu.close()
    return f"{len(worlds)} saves listed"


def _window(state: dict[str, Any], player: str) -> str:
    from .engine.routing import Router
    from .map.pack import PackSet
    from .providers.cli_provider import default_role_models
    from .providers.mock import MockProvider
    from .runtime.loop_thread import AsyncRuntime
    from .ui.app import ensure_app
    from .ui.main_window import AtlasWindow

    app = ensure_app()
    runtime = AsyncRuntime()
    runtime.start()
    shared = MockProvider()
    try:
        window = AtlasWindow(
            state["conn"], PackSet.open([Path(path) for path in state["paths"]]), "selftest",
            providers={key: shared for key in ("claude", "codex", "antigravity")},
            router=Router(pins=default_role_models()), runtime=runtime,
            player_nation=player)
        window.show()
        end = time.monotonic() + 1.0
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)
        title = window.windowTitle()
        window.close()
        app.processEvents()
    finally:
        runtime.stop()
    return f"opened: {title!r} on {app.platformName()}"
