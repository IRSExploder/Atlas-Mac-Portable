"""The player's frame around a game: menu -> setup or a save -> the game -> back to the menu.

`atlas start` (and a double-click on ATLAS.exe) runs this. It owns the order of screens
and nothing else: the menu, setup and settings screens each emit what was chosen, and the
work of building or opening a world happens here, out of their button handlers.

**Closing the game window returns to the menu,** it does not quit -- the menu is where a
player picks another save. Quit is a menu button. Every way a world can fail to open
(written by a newer build, sitting in a synced folder, a refused migration, a file that
is not a database, a missing map pack) is shown on the menu as a sentence, never as a
traceback.

Providers come from a factory handed in by the caller, so `--mock`, `--replay` and the
real CLIs all go through the same frame.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QObject

from ..state.catalog import SavedWorld, list_worlds
from .theme import Theme

__all__ = ["Launcher", "open_problem"]


def open_problem(exc: BaseException) -> str:
    """A world that would not open, said the way a player needs to hear it."""
    from ..state.problems import describe

    return describe(exc)


class _OnClose(QObject):
    """Calls back when the watched window is closed, without subclassing the window."""

    def __init__(self, callback: Callable[[], None]) -> None:
        super().__init__()
        self._callback = callback

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 -- Qt override
        if event.type() == QEvent.Type.Close:
            self._callback()
        return False


class Launcher:
    def __init__(self, app, theme: Theme, *, providers: Callable[[], dict],
                 runtime, worlds_root: Path | None = None,
                 provider_status: str = "") -> None:
        self._app = app
        self._theme = theme
        self._providers = providers
        self._runtime = runtime
        self._worlds_root = worlds_root
        self._provider_status = provider_status
        self.menu = None
        self.setup = None
        self.window = None
        # Held: a top-level Qt widget with no Python reference is collected under shiboken.
        self._keep: list[Any] = []

    # ---- the menu ---------------------------------------------------------------

    def show_menu(self, problem: str = "") -> None:
        from .main_menu import MainMenu

        old = self.menu
        self.menu = MainMenu(self._theme, list_worlds(self._worlds_root),
                             provider_status=self._provider_status)
        self.menu.resize(1280, 800)
        self.menu.new_requested.connect(self.new_game)
        self.menu.open_requested.connect(self.open_world)
        self.menu.settings_requested.connect(self.settings)
        self.menu.quit_requested.connect(self._app.quit)
        if problem:
            self.menu.show_problem(problem)
        self.menu.show()
        if old is not None:
            old.close()
            old.deleteLater()

    def settings(self) -> None:
        from .preferences import load_app_preferences, save_app_preferences
        from .settings import SettingsScreen
        from .widgets import set_text_scale

        sheet = SettingsScreen(load_app_preferences(), theme=self._theme,
                               provider_status=self._provider_status, parent=self.menu)

        def saved(preferences) -> None:
            save_app_preferences(preferences)
            set_text_scale(preferences.text_scale)

        sheet.saved.connect(saved)
        accepted = sheet.exec()
        if accepted:
            # Rebuilt rather than re-themed: the text scale is read as fonts are built.
            self.show_menu()

    # ---- opening a world ----------------------------------------------------------

    def open_world(self, saved: SavedWorld) -> bool:
        from ..config.paths import missing_pack_advice
        from ..map.pack import PackSet
        from ..scenarios.presets import load_preset
        from ..scenarios.territory import pack_paths
        from ..state.db import connect

        preset = load_preset(saved.preset_id) if saved.preset_id else None
        paths = pack_paths(saved.preset_id or "",
                           preset.map_source if preset else "admin0-50m")
        missing = [path for path in paths if not path.exists()]
        if missing:
            self._problem(missing_pack_advice(missing[0]))
            return False
        try:
            conn = connect(saved.path)
        except Exception as exc:  # noqa: BLE001 -- every failure becomes a sentence
            self._problem(open_problem(exc))
            return False
        self._play(conn, PackSet.open(paths), saved.world_id)
        return True

    def new_game(self) -> None:
        from .setup import SetupScreen

        self.setup = SetupScreen(self._theme)
        self.setup.setWindowTitle("ATLAS")
        self.setup.resize(1600, 950)
        self.setup.create.connect(self._create)
        self.setup.cancelled.connect(self._setup_cancelled)
        self.setup.show()
        if self.menu is not None:
            self.menu.hide()

    def _setup_cancelled(self) -> None:
        if self.setup is not None:
            self.setup.close()
            self.setup = None
        self.show_menu()

    def _create(self, request) -> None:
        from ..config.paths import missing_pack_advice
        from ..map.pack import PackSet
        from ..scenarios.registry import preset_politics, preset_seed
        from ..scenarios.territory import pack_paths
        from ..state.bootstrap import create_world
        from ..state.db import connect, world_db_path

        screen = self.setup
        paths = pack_paths(request.preset.id, request.preset.map_source)
        missing = [path for path in paths if not path.exists()]
        if missing:
            screen.show_problem(missing_pack_advice(missing[0]))
            return
        db_path = (self._worlds_root / request.world_id / "world.db"
                   if self._worlds_root is not None else world_db_path(request.world_id))
        if db_path.exists():
            screen.show_problem(
                f"a world called {request.world_id!r} already exists; choose another name")
            return
        screen.set_busy(True)
        try:
            with PackSet.open(paths) as pack:
                seed = preset_seed(request.preset, pack, world_id=request.world_id,
                                   player=request.player, difficulty=request.difficulty,
                                   seed=request.seed)
                conn = create_world(db_path, seed)
                preset_politics(conn, request.world_id, request.preset)
            conn.close()
            conn = connect(db_path)
        except Exception as exc:  # noqa: BLE001
            from ..state.problems import describe

            screen.set_busy(False)
            screen.show_problem(describe(exc, what="The new world"))
            return
        screen.set_busy(False)
        self.setup = None
        self._play(conn, PackSet.open(paths), request.world_id, player=request.player)
        screen.close()

    def _play(self, conn, packs, world_id: str, *, player: str | None = None) -> None:
        from ..engine.routing import Router
        from ..providers.cli_provider import default_role_models
        from .main_window import AtlasWindow

        window = AtlasWindow(conn, packs, world_id, providers=self._providers(),
                             router=Router(pins=default_role_models()),
                             runtime=self._runtime, player_nation=player)
        watcher = _OnClose(self._game_closed)
        window.installEventFilter(watcher)
        self._keep.extend((window, watcher))
        self.window = window
        window.show()
        if self.menu is not None:
            self.menu.hide()

    def _game_closed(self) -> None:
        self.window = None
        self.show_menu()

    def _problem(self, text: str) -> None:
        if self.menu is None:
            self.show_menu(text)
        else:
            self.menu.show_problem(text)
