"""The application shell: top bar, two rails, the map between them, overlays over all of it.

Every model call goes through :class:`AsyncRuntime`, so a fifteen-minute world simulation
never touches the Qt thread and the map stays pannable while it runs. Results come back as
queued signals, which is why the handlers here can safely touch widgets.

The window refreshes by re-deriving: after any change it rebuilds the scene description
from world state rather than patching what is on screen. That is the same discipline as
the renderer itself, applied one level up.

**Theme is a runtime property, not a build-time one.** Every widget in the shell takes a
:class:`Theme` and can be handed a different one; the toggle in the top bar walks the tree
and hands out the other palette. That is the whole mechanism -- there is no second
stylesheet and no restart.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Callable
from functools import partial

from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QVBoxLayout, QWidget

from ..engine.advisor import AdvisorSession
from ..engine.confirmations import needed_for
from ..engine.diplomacy import DiplomacySession
from ..engine.parser import parse_orders
from ..engine.progress import ProgressReporter, estimated_advance
from ..engine.routing import Router
from ..engine.snapshot import load_player_snapshot
from ..engine.turn_engine import TurnEngine
from ..humanize import figure
from ..map.pack import GeometryPack, PackSet
from ..map.projection import get_projection
from ..map.query import load_map_view
from ..map.render import build_scene
from ..map.scene_model import MapLayer, UnitDomain
from ..providers.base import ModelProvider
from ..runtime.loop_thread import AsyncRuntime
from ..state.models import TurnStatus
from .advance import AdvanceOverlay
from .airsituation import AirSituationOverlay
from .alerts import AlertsOverlay
from .branches import BranchOverlay, fork_here
from .cast import CastOverlay
from .correspondence import CorrespondenceOverlay
from .council import CouncilOverlay, advice_only
from .dependency import DependencyOverlay, anchors_from
from .directives import DirectivesOverlay
from .homefront import HomeFrontOverlay
from .inspector import InspectorOverlay
from .map_chrome import MapChrome
from .map_view import MapView
from .matters import MattersDrawer
from .newspaper import NewspaperOverlay
from .overlays import (
    ConfirmOverlay,
    ExportOverlay,
    Overlay,
    RewindOverlay,
    SummaryOverlay,
    UndoTurnOverlay,
)
from .preferences import (
    colour_safe,
    load_preferences,
    save_app_preferences,
    save_preferences,
    set_current,
)
from .rail_left import LeftRail
from .rail_right import RightRail
from .regionmenu import RegionMenu
from .search import SearchOverlay
from .statusbar import StatusStrip
from .strategy import StrategyOverlay
from .theme import DARK, LIGHT, Theme, load_fonts
from .topbar import TopBar
from .trace import TraceOverlay
from .turning import TurningOverlay
from .understanding import UnderstoodOverlay
from .watchlist import WatchlistOverlay
from .widgets import RAIL_HANDLE_WIDTH, scaled, set_text_scale
from .workbench import WorkbenchOverlay

__all__ = ["MIN_WINDOW_HEIGHT", "MIN_WINDOW_WIDTH", "AtlasWindow"]

#: The size the design is drawn at, and asked for when the screen has room for it.
DESIGN_WIDTH, DESIGN_HEIGHT = 1600, 950

#: The smallest window the layout has actually been tested at, and the floor it refuses
#: to go below. Every block in the top bar elides, so the *structural* minimum is smaller
#: still (around 960); this is the point below which the map is not worth looking at.
#: Both numbers matter and only this one is a promise, which is why it is a constant with
#: a test on it rather than whatever the layout happened to add up to on the day.
MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT = 1100, 700

#: Below these, the rail on that side is put away by default and left as a handle.
#:
#: **Collapsing beats squeezing.** Both rails are fixed columns -- 348 and 404 -- so on a
#: 1280-wide window they take 752 of it and leave the world map 528: a map of the world
#: narrower than the event feed beside it. Shrinking the rails instead would take a
#: nation's name down to *Unite...*, which is not a smaller interface, it is a broken one.
#: The record goes first because it is a record: it says what has already happened, and it
#: is the one column a player is not acting through. Your turn -- the talks, the orders,
#: the button that spends the month -- is the last thing to go.
#:
#: A player can pull either back out at any width; these only decide what a window opens
#: as. See :class:`atlas.ui.widgets.Rail`.
#:
#: Expressed as *how much map is left*, not as a window width, because a window width is
#: only the right number at one type scale. Set the text a quarter larger -- the first
#: thing anybody does on a high-DPI screen -- and the rails grow with it while a fixed
#: breakpoint does not, so a 1600px window either keeps two rails that no longer fit or
#: folds away the button that plays the turn. Both happened.
MIN_MAP_FOR_RECORD = 620

#: And a much smaller one for the turn rail, because what is in it is the game: the talks,
#: the orders, and the button that spends the month. It is worth more than map. At the
#: design scale this works out below the minimum window, so the primary action is never
#: put away by anything but a deliberate click.
MIN_MAP_FOR_TURN = 420


def _opening_size() -> tuple[int, int]:
    """The design size, clipped to the screen the window is about to open on.

    A 1366x768 laptop must not be handed a 1600x950 window: what it gets is a window
    whose title bar is off the top of the screen and whose controls are off the bottom.
    """
    from PySide6.QtWidgets import QApplication

    screen = QApplication.primaryScreen()
    if screen is None:  # pragma: no cover -- there is always a screen under Qt
        return DESIGN_WIDTH, DESIGN_HEIGHT
    room = screen.availableGeometry()
    return (
        max(MIN_WINDOW_WIDTH, min(DESIGN_WIDTH, room.width() - 40)),
        max(MIN_WINDOW_HEIGHT, min(DESIGN_HEIGHT, room.height() - 60)),
    )


def _restore_orders(conn: sqlite3.Connection) -> list[dict]:
    """The orders the player had drafted and not yet spent, or none.

    **A draft is not world state, and this is where that stops being a slogan.** An order
    that has been written but not advanced has changed nothing: no diff, no event, no turn.
    Keeping it in the database would put an unspent intention in the fingerprint and let a
    rewind delete a player's afternoon, so it lives beside the save with the pins. See
    `atlas.state.workspace`.

    Tolerant on the way back in: anything that is not a dict is dropped rather than raised
    on. The turn engine validates every action it is given anyway -- that is what
    `apply_batch` is -- so a malformed draft costs a refusal and not a crash.
    """
    from ..state.workspace import recall

    kept = recall(conn, "draft_orders", []) or []
    return [entry for entry in kept if isinstance(entry, dict)] if (
        isinstance(kept, list)) else []


def _restore_pins(conn: sqlite3.Connection) -> list:
    """The watchlist as the player left it, or an empty one.

    Tolerant on purpose. The file is beside the save and a person can edit it; anything
    that is not a recognisable pair is dropped rather than raised on, because a strategy
    game that refuses to open over a malformed bookmark would be absurd.
    """
    from ..engine.watchlist import PIN_KINDS, PIN_LIMIT, Pin
    from ..state.workspace import recall

    kept = recall(conn, "pins", []) or []
    if not isinstance(kept, list):
        return []
    found = []
    for entry in kept:
        if (isinstance(entry, list | tuple) and len(entry) == 2
                and str(entry[0]) in PIN_KINDS):
            found.append(Pin(str(entry[0]), str(entry[1])))
    return found[:PIN_LIMIT]


#: **What each line of traffic is, said in the world rather than in the program.** The
#: top-bar strip is permanent chrome, so it is the one surface with no way to look away
#: from it; these are the words it uses. Ordered by which matters most to a player, because
#: one line commonly carries several kinds of traffic and the strip has room for one name.
#: Roles absent from this table (the parser, the arbiter) are machinery the player never
#: waits on, so a line serving only those is named for whatever else it carries.
_CHANNELS: tuple[tuple[str, str], ...] = (
    ("world_sim", "The world"),
    ("tracked_agent", "The capitals"),
    ("diplomacy", "The embassies"),
    ("advisor", "Your council"),
    ("organisation", "The bodies"),
    ("gambit", "Your agents"),
    ("freeform", "Your ministries"),
)


async def _tagged(session, call):
    """Await a diplomacy call and hand back the talk it belongs to along with its answer.

    The answer lands on the GUI thread after the player may have moved on to another talk,
    and the slot has to know which one it is about. Carried in the result rather than in a
    lambda, so the slots stay bound methods of the window -- a queued delivery onto the
    thread the window lives on -- and nothing about the talk is read on the loop thread.
    """
    return session, await call


class AtlasWindow(QMainWindow):
    def __init__(
        self,
        conn: sqlite3.Connection,
        pack: GeometryPack | PackSet,
        world_id: str,
        *,
        providers: dict[str, ModelProvider],
        router: Router,
        runtime: AsyncRuntime,
        player_nation: str | None = None,
        theme: Theme = DARK,
    ) -> None:
        super().__init__()
        load_fonts()
        self._conn = conn
        self._pack = pack
        self._world_id = world_id
        self._providers = providers
        self._router = router
        self._runtime = runtime
        self._theme = theme
        self._projection = get_projection("equirect")
        self._labels = {entry.geo_key: entry.label_point for entry in pack.entries}
        self._bounds = {entry.geo_key: entry.bbox for entry in pack.entries}
        self._selected_region: str | None = None
        #: The unit counter the player has picked, by ``marker_id``, or none. Presentation
        #: only: it decides which counter is drawn ringed and nothing else.
        self._selected_counter: str | None = None
        self._session: DiplomacySession | None = None
        #: The council conversation behind the *Ask your staff* panel. It outlives any
        #: number of negotiations: the rail's chat surface is diplomacy's alone now, and
        #: a private word with your own ministers no longer ends because talks began.
        self._council: AdvisorSession | None = None
        self._pending_orders: list[dict] = _restore_orders(conn)
        #: The orders handed to the turn that is running, exactly as handed over. The
        #: draft stays editable while a turn runs -- a read-back confirmed mid-turn, an
        #: *order this* from search -- so what the turn spent is these and only these, and
        #: landing removes them by identity rather than emptying the draft. It is also a
        #: copy, so nothing the window appends can reach a list the loop thread is reading.
        self._orders_in_flight: list[dict] = []
        self._layer = MapLayer.POLITICAL
        self._unit_domains = frozenset(UnitDomain)
        #: The last read of the world, reused by redraws that only change presentation.
        self._map_view_cache = None
        self._jump_started: float | None = None
        #: How *Stop once this has settled* reaches an advance running on the loop thread.
        #: A fresh one per run, so a stop pressed on one run can never end the next; the
        #: engine reads it between turns and never inside one. `None` for a single jump,
        #: which has no second turn to decline.
        self._stop_advance: threading.Event | None = None
        #: The turn the player is being asked to confirm -- a jump or an advance, ready to
        #: run with the answer given -- or ``None`` when nothing is asked. Only *Spend the
        #: turn* runs it; every other way out of the question clears it (`_jump_declined`),
        #: so a question backed out of is asked again.
        self._awaiting_confirmation: Callable[[], None] | None = None
        #: The parse the player is being shown, and the sentence that produced it. Held
        #: here rather than queued, because the read-back is the moment prose becomes a
        #: plan and nothing may be a plan before the player has agreed that it is.
        self._reading = None
        self._wrote = ""
        #: Whether the opening has already been offered. One per window: the drawer is the
        #: answer to *what am I supposed to be doing*, and a surface that keeps reappearing
        #: beside a map somebody is reading stops being an answer. After the first turn it
        #: is a control on the bar like any other -- with a count on it, so it can be
        #: ignored on the turns when there is nothing behind it.
        self._offered_matters = False

        self._player = player_nation or self._detect_player()
        #: What to call the player on a panel. Filled by `_refresh_all`.
        self._player_name = self._player or ""
        self._pins = _restore_pins(conn)

        # **Read before anything is built with a font in it.** The text scale is a
        # module-level multiplier inside `styled_font`, so it has to be in force before the
        # first widget is constructed -- everything after this line comes out at the size
        # the person asked for, with no widget knowing there was a question.
        #: Endings already shown this session, so a world with no workspace file
        #: (``:memory:``) does not ask again every turn. The workspace remembers the rest.
        self._endings_seen: set[str] = set()
        self._end_screen = None
        self._preferences = load_preferences(conn)
        set_current(self._preferences)
        set_text_scale(self._preferences.text_scale)
        if self._preferences.colour_safe:
            self._theme = colour_safe(self._theme)

        # Find, by whatever it is bound to. On the window rather than on the overlay: the
        # overlay is hidden when the shortcut has to work.
        self._find = QShortcut(QKeySequence(self._preferences.key_for("find")), self)
        self._find.activated.connect(self._open_search)
        # Settings, from inside a game: the same sheet the main menu opens, applied live
        # through `set_preferences`.
        self._settings = QShortcut(QKeySequence(self._preferences.key_for("settings")), self)
        self._settings.activated.connect(self.open_settings)
        # The reporter is a plain callback on the engine's side and a queued signal on
        # ours: the engine runs on the asyncio thread and must never touch a widget.
        self._progress = ProgressReporter()
        self._engine = TurnEngine(
            conn=conn, world_id=world_id, providers=providers, router=router,
            progress=self._progress,
        )

        self.setWindowTitle(f"ATLAS — {world_id}")
        # **The window has to fit the monitor the player owns.** The design is drawn at
        # 1600x950, but the most common desktop in the world is 1920x1080 and the second
        # most common laptop is 1366x768 -- so what is asked for here is the design size
        # *clipped to the screen*, and what is refused below is a size no layout in the
        # program has been tested at. The floor used to be 2192 px, set by nothing more
        # deliberate than a top bar that could not shrink; see `ui.topbar`.
        self.setMinimumSize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
        self.resize(*_opening_size())
        self._build_layout()
        self.apply_theme(theme)
        self._refresh_all()

    # -- construction ------------------------------------------------------

    def showEvent(self, event) -> None:
        """A new game opens on what is waiting.

        **Turn zero and a seat taken, and nothing else.** A world that has been played is a
        world whose player already knows what they are doing, and a spectator has no *you*
        for the panel to be about -- both get the map, and the bar still has the panel. The
        geometry is why this is here rather than in the constructor: an overlay sizes itself
        from its parent, and before the first show there is nothing to size against.
        """
        super().showEvent(event)
        if self._offered_matters or self._player is None or self._current_turn() != 0:
            return
        self._offered_matters = True
        self._load_matters()
        # Opened only if it found something. An uninvited drawer saying nothing was found
        # is worse than no drawer; the bar still shows the empty state to somebody who
        # asks, and the count on it says there is nothing to ask about.
        if self._matters.waiting:
            self._matters.open()

    def _detect_player(self) -> str | None:
        row = self._conn.execute(
            "SELECT id FROM nation WHERE world_id = ? AND is_player = 1", (self._world_id,)
        ).fetchone()
        return row["id"] if row else None

    def _build_layout(self) -> None:
        self._top = TopBar(self._theme)
        self._top.matters_requested.connect(self._open_matters)
        self._top.search_requested.connect(self._open_search)
        self._top.watchlist_requested.connect(self._open_watchlist)
        self._top.alerts_requested.connect(self._open_alerts)
        self._top.branches_requested.connect(self._open_branches)
        self._top.advance_requested.connect(self._open_advance)
        self._top.workbench_requested.connect(self._open_workbench)
        self._top.strategy_requested.connect(self._open_strategy)
        self._top.directives_requested.connect(self._open_directives)
        self._top.homefront_requested.connect(self._open_homefront)
        self._top.dependency_requested.connect(self._open_dependency)
        self._top.inspector_requested.connect(self._open_inspector)
        self._top.dispatch_requested.connect(self._open_dispatch)
        self._top.cast_requested.connect(self._open_cast)
        self._top.correspondence_requested.connect(self._open_correspondence)
        self._top.council_requested.connect(self._open_council)
        self._top.meter_asked.connect(self._open_trace)
        self._top.rewind_requested.connect(self._open_rewind)
        self._top.undo_turn_requested.connect(self._open_undo)
        self._top.export_requested.connect(self._open_export)
        self._top.theme_toggled.connect(self._toggle_theme)

        self._left = LeftRail(self._theme)
        self._left.region_selected.connect(self._on_region_clicked)
        self._left.air_requested.connect(self._open_air)

        self._map = MapView(self._pack, self._projection)
        self._map.region_clicked.connect(self._on_map_clicked)
        self._map.counter_clicked.connect(self._on_counter_clicked)
        self._map.region_context.connect(self._on_region_context)
        self._map.region_hovered.connect(self._on_region_hovered)
        self._map.lod_changed.connect(self._on_lod_changed)
        self._map.viewport_moved.connect(lambda: self._redraw(reload=False))

        self._chrome = MapChrome(self._map, self._theme)
        self._chrome.layer_changed.connect(self._on_layer_changed)
        self._chrome.unit_domains_changed.connect(self._on_unit_domains_changed)
        self._chrome.zoom_in_requested.connect(lambda: self._zoom(1.35))
        self._chrome.zoom_out_requested.connect(lambda: self._zoom(1 / 1.35))
        self._chrome.fit_requested.connect(self._map.fit_world)

        self._region_menu = RegionMenu(self._theme, self)
        self._region_menu.action_chosen.connect(self._take_action)
        self._region_menu.second_click_needed.connect(self._await_second_region)
        self._region_menu.air_requested.connect(self._open_air)
        #: An action waiting for the province it still needs. See `_on_map_clicked`.
        self._awaiting = None

        self._right = RightRail(self._theme)
        self._right.open_chat.connect(self._on_open_chat)
        self._right.open_advisor.connect(self._on_open_advisor)
        self._right.send_message.connect(self._on_send_message)
        self._right.close_chat.connect(self._on_close_chat)
        self._right.orders_submitted.connect(self._on_check_orders)
        self._right.jump_requested.connect(self._on_jump)

        self._status = StatusStrip(self._theme)
        #: Whether the player has overruled the breakpoint for a rail. Cleared when the
        #: window crosses the breakpoint again, so a resize is allowed to have an opinion
        #: and a click is allowed to overrule it until then.
        self._rail_choice: dict[str, bool] = {}
        self._left.collapsed_changed.connect(
            lambda collapsed: self._rail_choice.__setitem__("record", collapsed))
        self._right.collapsed_changed.connect(
            lambda collapsed: self._rail_choice.__setitem__("turn", collapsed))

        # The drawer sits between the record and the map: a reading column beside the
        # other reading column, and closed it costs nothing at all. See
        # `atlas.ui.matters.MattersDrawer`.
        self._matters = MattersDrawer(self._theme)
        self._matters.chosen.connect(self._travel_from_matters)
        self._matters.lens_requested.connect(self._lens_from_matters)
        self._matters.closed.connect(self._on_matters_closed)

        middle = QHBoxLayout()
        middle.setContentsMargins(0, 0, 0, 0)
        middle.setSpacing(0)
        middle.addWidget(self._left)
        middle.addWidget(self._matters)
        middle.addWidget(self._chrome, 1)
        middle.addWidget(self._right)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._top)
        layout.addLayout(middle, 1)
        layout.addWidget(self._status)
        self.setCentralWidget(central)

        self._build_overlays(central)

    def _build_overlays(self, host: QWidget) -> None:
        self._jump_overlay = TurningOverlay(self._theme, host)
        self._jump_overlay.cancel_requested.connect(self._on_cancel_jump)
        self._confirm = ConfirmOverlay(self._theme, host)
        self._confirm.confirmed.connect(self._jump_confirmed)
        self._confirm.declined.connect(self._jump_declined)
        self._summary = SummaryOverlay(self._theme, host)
        # A headline that names a province is a way of looking at it. Straight into
        # `_reveal_region`, which is the same path a click on the map takes.
        self._summary.region_chosen.connect(self._reveal_from_summary)
        # A line in *what affected you*, opened as the event it was built on -- the same
        # inspector every other "why did that happen" in the program lands in.
        self._summary.event_selected.connect(self._inspect_from_summary)
        # The turn lands, the summary opens, and the whole month is one press away.
        self._summary.dispatch_requested.connect(self._read_dispatch)
        self._rewind = RewindOverlay(self._theme, host)
        self._rewind.confirmed.connect(self._do_rewind)
        # Into the same slot, deliberately. Going back a turn is the rewind with the turn
        # already chosen; a second path to the same engine call is a second path to get
        # wrong.
        self._undo_turn = UndoTurnOverlay(self._theme, host)
        self._undo_turn.confirmed.connect(self._do_rewind)
        self._workbench = WorkbenchOverlay(self._theme, host)
        self._search = SearchOverlay(self._theme, host)
        self._search.chosen.connect(self._go_to)
        self._search.pinned.connect(self._toggle_pin)
        self._search.ordered.connect(self._take_doable)
        self._watchlist = WatchlistOverlay(self._theme, host)
        self._watchlist.chosen.connect(self._go_to)
        self._watchlist.toggled.connect(self._toggle_pin)
        self._alerts = AlertsOverlay(self._theme, host)
        self._alerts.region_selected.connect(self._reveal_from_alert)
        self._alerts.event_selected.connect(self._inspect_event)
        # **The air situation, and onto the one order pipeline like everything else.** The
        # panel hands back the shape `MapAction` has -- see `airsituation._Pending` -- so
        # both slots take it unchanged and an order chosen on it is indistinguishable
        # downstream from one chosen on the map or typed in prose.
        self._air = AirSituationOverlay(self._theme, host)
        self._air.action_chosen.connect(self._take_action)
        self._air.second_click_needed.connect(self._await_second_region)
        self._air.region_selected.connect(self._reveal_region)
        self._branches = BranchOverlay(self._theme, host)
        self._branches.fork_requested.connect(self._do_fork)
        self._advance = AdvanceOverlay(self._theme, host)
        self._advance.advance_requested.connect(self._run_advance)
        self._advance.event_selected.connect(self._inspect_event)
        self._strategy = StrategyOverlay(self._theme, host)
        self._directives = DirectivesOverlay(self._theme, host)
        # A policy change reorders what the action list and the alternatives
        # offer, so anything showing either is redrawn. It writes nothing to
        # the world, so there is no turn to spend and nothing to rewind.
        self._directives.changed.connect(lambda _held: self._redraw(reload=False))
        self._homefront = HomeFrontOverlay(self._theme, host)
        self._dependency = DependencyOverlay(self._theme, host)
        self._dependency.chosen.connect(self._go_to)
        self._inspector = InspectorOverlay(self._theme, host)
        self._inspector.region_selected.connect(self._reveal_region)
        self._inspector.trace_requested.connect(self._trace_event)
        self._dispatch = NewspaperOverlay(self._theme, host)
        self._dispatch.event_selected.connect(self._inspect_from_dispatch)
        self._cast = CastOverlay(self._theme, host)
        self._cast.event_selected.connect(self._inspect_from_cast)
        self._post = CorrespondenceOverlay(self._theme, host)
        self._post.event_selected.connect(self._inspect_from_correspondence)
        self._understood = UnderstoodOverlay(self._theme, host)
        self._understood.confirmed.connect(self._queue_understood_orders)
        self._understood.reworded.connect(self._reword_orders)
        self._staff = CouncilOverlay(self._theme, host)
        self._staff.asked.connect(self._ask_the_council)
        self._staff.cleared.connect(self._forget_the_council)
        self._trace = TraceOverlay(self._theme, host)
        # The two screens are each other's other half: the trace walks the chain, the
        # inspector opens any link on it, and that link can be traced again from there.
        self._trace.event_selected.connect(self._inspect_event)
        self._export = ExportOverlay(self._theme, host)
        self._export.export_requested.connect(self._do_export)
        # post_stages, not update_stages: the reporter fires on the asyncio thread.
        self._progress.on_update = self._jump_overlay.post_stages

    def _fit_rails(self, width: int) -> None:
        """Put a rail away, or offer it back, according to the room there is.

        The player's own choice wins while the window stays on one side of a breakpoint;
        crossing it is the window changing its mind, and clears the override. Without
        that, dragging a window wider would keep a rail folded away for ever because it
        was folded once at a narrower size.
        """
        # **The drawer is a third column while it is out.** The room it takes is room the
        # rails cannot also have, so it comes off the width the breakpoints are measured
        # against rather than out of the map. Without this, opening what-is-waiting on a
        # 1280 window left the world about 400 px wide with both rails still out -- which
        # is the squeezing this whole mechanism exists instead of.
        if self._matters.isVisible():
            width = max(0, width - self._matters.width())

        handle = RAIL_HANDLE_WIDTH
        for key, rail, threshold in (
            ("record", self._left,
             self._left.full_width + self._right.full_width + scaled(MIN_MAP_FOR_RECORD)),
            ("turn", self._right,
             handle + self._right.full_width + scaled(MIN_MAP_FOR_TURN)),
        ):
            tight = width < threshold
            if rail._collapsible != tight:  # noqa: SLF001
                self._rail_choice.pop(key, None)
                rail.set_collapsible(tight)
                rail.set_collapsed(tight)
            elif key in self._rail_choice:
                rail.set_collapsed(self._rail_choice[key])

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_rails(event.size().width())
        host = self.centralWidget()
        if host is None:
            return
        for overlay in (self._jump_overlay, self._summary, self._rewind, self._export,
                        self._staff, self._understood, self._undo_turn):
            overlay.setGeometry(host.rect())

    # -- theme -------------------------------------------------------------

    def _toggle_theme(self) -> None:
        other = LIGHT if self._theme.is_dark else DARK
        # The safe palette is a property of the person, not of the theme, so it survives
        # a light/dark switch rather than being undone by one.
        self.apply_theme(colour_safe(other) if self._preferences.colour_safe else other)

    def set_preferences(self, preferences) -> None:
        """Take a new set of preferences and make the window look like them now.

        The text scale reaches live widgets through the theme pass: `apply_theme` walks
        every widget and re-applies its type style, and a style is turned into a font by
        the one function the scale lives in. So there is no separate "re-font everything"
        path to keep in step with the theming one.
        """
        self._preferences = preferences
        save_preferences(self._conn, preferences)
        # And as the player's own default: a choice made in one game is what the next new
        # game should start with.
        save_app_preferences(preferences)
        set_current(preferences)
        set_text_scale(preferences.text_scale)
        base = DARK if self._theme.is_dark else LIGHT
        self._find.setKey(QKeySequence(preferences.key_for("find")))
        self._settings.setKey(QKeySequence(preferences.key_for("settings")))
        self.apply_theme(colour_safe(base) if preferences.colour_safe else base)

    def open_settings(self) -> None:
        """The settings sheet over the game; Save applies at once and is remembered."""
        from .settings import SettingsScreen

        sheet = SettingsScreen(self._preferences, theme=self._theme, parent=self)
        sheet.saved.connect(self.set_preferences)
        sheet.open()

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.setStyleSheet(f"QMainWindow {{ background: {theme.canvas}; }}")
        central = self.centralWidget()
        if central is not None:
            central.setStyleSheet(f"background: {theme.canvas};")
        for widget in (
            self._top, self._left, self._matters, self._chrome, self._right, self._status,
            self._region_menu,
        ):
            widget.apply_theme(theme)
        # **Every overlay, found rather than listed.** This was a hand-kept list, and a
        # panel left off it kept the palette it was built with. Thirteen were, and the
        # province menu, so a light game opened a dark Strategy screen and drew the alerts
        # heading in light ink on a light sheet. A new panel is themed by being a panel.
        if central is not None:
            for overlay in central.findChildren(Overlay):
                overlay.apply_theme(theme)
        # The map's own colours come from the scene description, so a theme change is a
        # redraw like any other rather than a special case in the applier.
        self._redraw(reload=False)

    # -- refresh -----------------------------------------------------------

    def _refresh_all(self) -> None:
        self._redraw()
        self._left.bind(self._conn, self._world_id, self._player)
        self._right.bind(self._conn, self._world_id, self._player)

        snapshot = load_player_snapshot(self._conn, self._world_id, nation_id=self._player)
        self._top.set_world(self._world_id, _scenario_name(self._conn, self._world_id))
        self._player_name = snapshot.name or self._player or ""
        self._top.set_snapshot(snapshot)
        self._top.set_providers(self._provider_rows())
        self._chrome.set_player(snapshot.name, snapshot.colour)
        self._chrome.set_place(
            "equirectangular", self._map.lod, len(self._pack.entries),
            scope=_scenario_name(self._conn, self._world_id).split("—")[0].strip() or "World",
        )

        # **And any panel still on the screen.** Everything else here is rebound because
        # the world moved; a pinned overlay is on the screen for the same world and was
        # not. It costs one `MAX(id)` when nothing has changed and nothing at all while it
        # is closed, which is the common case.
        self._dependency.refresh_if_stale(
            self._conn, self._world_id, turn=self._current_turn())
        self._reread_the_pinned_panel()

        self._refresh_commitments()
        # **The air panel re-reads rather than being rebuilt.** It holds no state of its
        # own, so a jump and a rewind are the same event to it: whatever the world says
        # now. This is what makes it correct after a rewind without a second code path.
        self._air.refresh()
        # And what is waiting, on the bar. Re-read whenever the world moves, because the
        # count is the whole argument for a named control: a number that went stale after
        # a turn would be worse than no number at all. It lays the drawer out as well --
        # one read, so the bar and the rows behind it cannot disagree.
        self._load_matters()
        tracked = int(self._conn.execute(
            "SELECT COUNT(*) FROM nation WHERE world_id = ? AND is_tracked = 1", (self._world_id,)
        ).fetchone()[0])
        self._right.set_tracked_count(tracked)

        seed = int(self._conn.execute(
            "SELECT rng_seed FROM world WHERE id = ?", (self._world_id,)
        ).fetchone()[0])
        self._status.set_world(_db_path_of(self._conn), seed)
        self._status.set_state("Ready")

    def _reread_the_pinned_panel(self) -> None:
        """Re-read the panel the player pinned, if it is one that reads the world.

        **Pinned is the one way a panel outlives the world it read.** Every panel is loaded
        as it opens, and a turn or a rewind opens a sheet over the top that sends every
        other panel away -- except the pinned one, which is what the pin is for. So the
        Strategy table, the Home Front and the cast went on showing the month before, or a
        month a rewind had just unmade, beside the sheet that said it had moved. Only the
        pinned panel, because only it can be on the screen here; a closed one is re-read
        when it opens, so it costs nothing while nothing is pinned.

        The dependency lens keeps its own `refresh_if_stale`: it holds a subject the
        player navigated to, which a plain re-read would throw away.

        The workbench is here as well, because it reads the draft as well as the world. A
        turn spends the draft, and a pinned workbench went on predicting what an order
        that had already been carried out would do.
        """
        readers = {
            self._strategy: self._load_strategy,
            self._homefront: self._load_homefront,
            self._cast: self._load_cast,
            self._alerts: self._load_alerts,
            self._dispatch: self._load_dispatch,
            self._post: self._load_correspondence,
            self._watchlist: self._load_watchlist,
            self._workbench: self._load_workbench,
        }
        for overlay, reread in readers.items():
            if overlay.pinned and overlay.showing():
                reread()

    def _provider_rows(self) -> list[tuple[str, str, str]]:
        """The lines the world reaches this government over, and whether each is carrying.

        **The one place the machinery underneath is turned into words**, in the same
        spirit as :data:`atlas.ui.turning.BEATS`: what a line *is* stays on this side of
        the boundary and the bar only sets what it is handed. This strip used to be three
        brand names and three model identifiers, sitting on the permanent chrome an inch
        from the date -- the one part of the interface a player could not close, saying
        what the program was rather than what the world is.

        What a player can act on is unchanged and is the whole reason the strip exists: a
        line that is not carrying means the world may not be able to answer this turn.
        Each line is named for the traffic it carries rather than for who carries it,
        taken from the most consequential role that line is pinned to.
        """
        pins = getattr(self._router, "pins", {}) or {}
        rows: list[tuple[str, str, str]] = []
        for provider in ("antigravity", "claude", "codex"):
            # Pins are keyed by role, not by provider: the roles this line serves, in the
            # order they matter to a player. A line serving none of them carries nothing.
            served = {str(role) for role, pin in pins.items() if pin[0] == provider}
            channel = next((name for key, name in _CHANNELS if key in served), "")
            verdict = self._conn.execute(
                """
                SELECT audit_verdict FROM routing_log
                 WHERE world_id = ? AND provider = ?
                 ORDER BY id DESC LIMIT 1
                """,
                (self._world_id, provider),
            ).fetchone()
            state = "ok"
            condition = "open"
            if verdict is not None:
                state = {"MATCH": "ok", "MISMATCH": "down"}.get(verdict["audit_verdict"], "warn")
                condition = {"ok": "open", "down": "broken", "warn": "quiet"}[state]
            # A line carrying nothing says so once, in the column that names it, rather
            # than twice across two columns saying almost the same word.
            rows.append((channel, condition, state) if channel else ("not in use", "", state))
        return rows

    def _refresh_commitments(self) -> None:
        if not self._player:
            return
        from ..engine.naming import nation_names
        from ..state.visibility import standing_commitments

        # **Who you signed with, not what the row calls them.** This read
        # *defence pact (active) with deu, gbr, ita* -- three database keys where three
        # countries should be, on the one line in the rail that says what you are
        # committed to.
        names = nation_names(self._conn, self._world_id)
        entries = [
            f"{c['kind'].replace('_', ' ')} ({c['status']}) with "
            + (", ".join(names.get(p, p) for p in c["parties"] if p != self._player)
               or "nobody else")
            for c in standing_commitments(self._conn, self._player)
        ]
        self._right.show_commitments(entries)

    def _redraw(self, *, reload: bool = True) -> None:
        """Rebuild the scene. ``reload=False`` reuses the last read of the world.

        **Panning cannot change the world, and the read is the expensive half.**
        `load_map_view` is a full pass over every region, its owner, its contests and its
        visibility -- measured at 6.6 ms on the real Modern Day map, against a 16 ms frame
        budget. It was being run on every scroll step of every drag, to produce a result
        that was identical every time.

        The boundary is exact rather than a guess about staleness: of the paths that
        redraw, only `_refresh_all` follows a change to the *database*. A theme change, a
        zoom that crosses a detail level, a layer switch and a click on a province all
        change how the same world is drawn, never what it contains. So those may reuse the
        cached read, and anything that touches the world calls the reloading form -- which
        is the default, so a future caller that forgets is correct rather than stale.
        """
        if reload or self._map_view_cache is None:
            self._map_view_cache = load_map_view(
                self._conn, self._world_id, observer_nation_id=self._player
            )
        view = self._map_view_cache
        description = build_scene(
            view,
            projection=self._projection,
            pack_id=self._pack.pack_id,
            lod=self._map.lod,
            label_points=self._labels,
            selected_region_id=self._selected_region,
            selected_marker_id=self._selected_counter,
            layer=self._layer,
            unit_domains=self._unit_domains,
            palette=self._theme.map_palette(),
            dissolve=self._map.dissolve,
            viewport=self._map.viewport_rect(),
            bounds=self._bounds,
        )
        self._map.show_scene(description)

    # -- map ---------------------------------------------------------------

    def _zoom(self, factor: float) -> None:
        self._map.zoom_by(factor)

    def _on_lod_changed(self, lod: int) -> None:
        """Update the readout. **Deliberately does not redraw.**

        Crossing a detail boundary used to rebuild the scene immediately, in the middle of
        the wheel gesture that crossed it -- and rebuilding at a *finer* level of detail is
        the most expensive rebuild there is, which is why a zoom spiked to 129 ms exactly
        when it changed level. The rebuild is not skipped, it is deferred: the map's settle
        timer fires 70 ms after the hand stops and redraws at `self._map.lod`, which is
        already the new level. So the work happens once, when it is cheap, instead of
        mid-flight when it is not.
        """
        self._chrome.set_place(
            "equirectangular", lod, len(self._pack.entries),
            scope=_scenario_name(self._conn, self._world_id).split("—")[0].strip() or "World",
        )

    def _on_layer_changed(self, key: str) -> None:
        self._layer = MapLayer(key)
        self._redraw(reload=False)

    def _on_unit_domains_changed(self, domains) -> None:
        self._unit_domains = frozenset(UnitDomain(domain) for domain in domains)
        # Presentation only: the unit rows and centroids are already in the cached view.
        self._redraw(reload=False)

    def _on_map_clicked(self, region_id: str) -> None:
        """A click on the map itself: the answer to a half-made order, or a selection.

        **Only this click answers the question.** The half-made order used to be completed
        in `_on_region_clicked`, which is also where a search result, an alert, a headline
        and a row on the record all land -- so a player who asked *move from where?*, went
        to look something up and jumped to it had an order filed from wherever the search
        happened to go, with nothing on the screen to say so. The question is asked of the
        map ("now click the province"), and only the map answers it.
        """
        if self._awaiting is not None:
            self._complete_action(region_id)
            return
        self._on_region_clicked(region_id)

    def _on_region_clicked(self, region_id: str) -> None:
        """Select a province. Never completes a half-made order -- see `_on_map_clicked`."""
        # Ground, not a formation. A ring left on a counter in a province the player has
        # since clicked past would say a formation is selected when none is.
        self._selected_counter = None
        self._selected_region = region_id
        self._redraw(reload=False)
        self._left.set_selected_region(region_id)

    def _on_counter_clicked(self, marker_id: str, region_id: str) -> None:
        """A counter is a formation standing somewhere, so a click on it means both.

        The province comes off the counter rather than out of a second hit test -- see
        `atlas.ui.map_view.MapView._select_under`. What the window does with it is exactly
        what a click on the ground does, plus the formation: the same selection, the same
        redraw, the same rail. There is no second selection path to keep in step.
        """
        if self._awaiting is not None:
            # A half-made order wants a province, and a counter is standing in one.
            self._complete_action(region_id)
            return
        self._selected_counter = marker_id
        self._selected_region = region_id
        self._redraw(reload=False)
        self._left.set_selected_region(region_id)

    def _on_region_context(self, region_id: str, where) -> None:
        """Ask the engine what can be done here, and offer exactly that."""
        menu = self._region_menu.build(
            self._conn, self._world_id, self._player, region_id, parent=self)
        menu.popup(where)

    def _open_air(self, region_id: str) -> None:
        """The air situation over one theatre. Read-only; opening it cannot move a world.

        The pace is passed through because *when this lapses* is a question about world
        time rather than about how many times the button is pressed -- a sky that survives
        four monthly turns survives a third of one yearly turn, and a panel that answered
        in turns without knowing how long a turn is would be answering a different question.
        """
        self._air.load(self._conn, self._world_id, self._player,
                       region_id=region_id, days=float(self._right.jump_days))
        self._air.open()

    def _keep_orders(self) -> None:
        """Write the draft down, beside the save.

        Called wherever `_pending_orders` is assigned rather than on a timer, so what is on
        disk is what is on screen: a timer would make the two disagree for a second, and
        the second it disagreed in would be the one the player closed the window in.
        """
        from ..state.workspace import remember

        remember(self._conn, "draft_orders", list(self._pending_orders))

    def _take_action(self, entry) -> None:
        """One pipeline: a chosen action joins the orders the parser produces.

        It flows into the workbench preview and into the turn exactly as a typed order
        does, because it is one -- there is no second path into the engine.
        """
        self._pending_orders.append(dict(entry.action))
        self._keep_orders()
        self._status.set_state(
            f"{entry.label} — added to your orders; check them before the turn.")

    def _take_doable(self, action: object, label: str) -> None:
        """An act taken from a search result's own list of what could be ordered about it.

        The same pipeline as `_take_action`: it is an order, and there is still only one way
        in. Only whole orders reach here -- `engine.against` marks the ones that still need a
        sentence written, and the row for those is not clickable.
        """
        self._pending_orders.append(dict(action))
        self._keep_orders()
        self._status.set_state(
            f"{label} — added to your orders; check them before the turn.")

    def _await_second_region(self, entry) -> None:
        self._awaiting = entry
        self._status.set_state(
            f"{entry.label}: now click the province to move from. Escape to forget it.")

    def _forget_awaiting(self, *, say: bool = True) -> bool:
        """Drop a half-made order, as the line that asked for its province promised.

        Returns whether there was one. ``say`` is off where the caller is about to put its
        own sentence on the strip (a rewind), so the player reads one line, not two.
        """
        entry, self._awaiting = self._awaiting, None
        if entry is None:
            return False
        if say:
            self._status.set_state(f"{entry.label}: forgotten. Nothing was added.")
        return True

    def keyPressEvent(self, event) -> None:
        """Escape, when nothing on the screen has claimed it, forgets a half-made order.

        **A key press and not a shortcut, on purpose.** Every overlay holds the same key as
        a window-wide `QShortcut` while it is on top (see `Overlay._arbitrate_dismiss`),
        and a second shortcut on the same key would make Qt fire neither. A shortcut is
        dispatched before any key press, so an open sheet still takes Escape first and
        closes; with nothing open the press falls through the focused widget to here.
        """
        pressed = QKeySequence(event.keyCombination())
        dismiss = QKeySequence(self._preferences.key_for("dismiss"))
        if self._awaiting is not None and pressed.matches(dismiss) == \
                QKeySequence.SequenceMatch.ExactMatch:
            self._forget_awaiting()
            event.accept()
            return
        super().keyPressEvent(event)

    def _complete_action(self, region_id: str) -> None:
        entry, self._awaiting = self._awaiting, None
        action = dict(entry.action)
        action[entry.needs] = region_id
        name = self._conn.execute(
            "SELECT name FROM region WHERE id = ?", (region_id,)).fetchone()
        action["summary"] = (
            f"{action.get('summary', entry.label)} from "
            f"{name['name'] if name else region_id}")
        self._pending_orders.append(action)
        self._keep_orders()
        self._status.set_state(f"{action['summary']} — added to your orders.")

    def _on_region_hovered(self, region_id: str) -> None:
        row = self._conn.execute(
            """
            SELECT r.name, r.terrain, r.population, n.name AS owner
              FROM region r LEFT JOIN nation n ON n.id = r.owner_nation_id
             WHERE r.id = ?
            """,
            (region_id,),
        ).fetchone()
        if row is None:
            self._chrome.set_hover("")
            return
        self._chrome.set_hover(
            f"{row['name']}  |  {row['owner'] or 'unclaimed'} · {row['terrain']} · "
            f"pop {figure(row['population'])}"
        )

    # -- orders ------------------------------------------------------------

    def _on_check_orders(self, text: str) -> None:
        """Parse on request only. Never on a timer -- that is a paid call per keystroke."""
        if not self._player:
            self._right.show_orders_result(
                [], ["You are spectating; there is no nation to command."]
            )
            return

        self._right.show_orders_pending()
        self._status.set_state("Reading your orders…", busy=True)
        # Kept so the read-back can show the player their own sentence beside what was
        # made of it. Comparing the two across a panel and the rail behind it is not a
        # comparison anybody makes.
        self._wrote = text
        handle = self._runtime.submit(
            parse_orders(
                self._conn,
                self._world_id,
                nation_id=self._player,
                raw_text=text,
                providers=self._providers,
                router=self._router,
            ),
            label="parse-orders",
        )
        handle.finished.connect(self._on_orders_parsed)
        handle.failed.connect(self._on_call_failed)

    def _on_orders_parsed(self, parsed) -> None:
        """Read the order back before it becomes a plan.

        **This is the only place a parse used to become the draft, and it no longer does.**
        The actions are held here and assigned by `_queue_understood_orders`, so an order
        that was misread -- the right act against the wrong power, the right province in
        the wrong direction -- is caught while it is still a sentence in a box. Cancelling
        costs nothing because nothing had happened yet.

        A parser that could not be reached at all is a different failure and keeps its old
        shape: there is no reading to confirm, so there is nothing to show.
        """
        self._status.set_state("Ready")
        if not parsed.ok:
            self._reading = None
            self._right.show_orders_result([], [parsed.error or "orders could not be checked"])
            return
        if not parsed.actions and not parsed.rejected:
            # Nothing was said. Not a misreading, and not worth a screen.
            self._reading = None
            self._right.show_orders_result([], [])
            return
        self._reading = parsed
        self._understood.show_reading(
            self._conn, self._world_id, wrote=self._wrote, parsed=parsed)
        self._understood.open()

    def _queue_understood_orders(self) -> None:
        """The player agreed that is what they meant. Now it is the draft.

        Replaces rather than appends, exactly as the parse used to: a fresh check of the
        orders box describes the whole of what the player intends this turn.
        """
        parsed = self._reading
        if parsed is None:
            return
        self._reading = None
        self._pending_orders = list(parsed.actions)
        self._keep_orders()
        self._right.show_orders_result(parsed.summaries, parsed.rejection_lines)
        self._status.set_state(
            f"{len(parsed.actions)} order(s) queued. They are attempted at the next jump.")

    def _reword_orders(self) -> None:
        """Cancelled. The world, the draft and the words in the box are all where they were.

        Deliberately does *not* clear the orders box: the whole point of re-wording is
        that the sentence is nearly right, and a panel that emptied it would make the safe
        answer the expensive one.
        """
        self._reading = None
        self._right.show_orders_note(
            "Nothing queued. Your words are still in the box — edit them and check again.")
        self._status.set_state("Ready")

    # -- diplomacy ---------------------------------------------------------

    def _on_open_chat(self, nation_id: str) -> None:
        if not self._player:
            self._status.set_state("You are spectating; there is nobody to negotiate for.",
                                   problem=True)
            return
        row = self._conn.execute("SELECT name FROM nation WHERE id = ?", (nation_id,)).fetchone()
        self._session = DiplomacySession(
            conn=self._conn,
            world_id=self._world_id,
            player_nation=self._player,
            counterpart=nation_id,
            providers=self._providers,
            router=self._router,
        )
        self._right.start_chat(row["name"] if row else nation_id)

    def _on_open_advisor(self) -> None:
        """The rail's *consult your council* button, which now opens the council's screen.

        It used to start the conversation in the rail's chat page -- the same two-hundred
        pixel column diplomacy negotiates in, which shut the moment a talk began and set
        the council's prose at the width of a phone. The capability was never the problem;
        the surface was. One council, one screen, two doors into it.
        """
        if not self._player:
            self._status.set_state("You are spectating; there is no government to advise you.",
                                   problem=True)
            return
        self._open_council()

    def _on_send_message(self, message: str) -> None:
        session = self._session
        if session is None:
            return
        self._right.append_turn("You", message, is_player=True)
        self._right.append_note("waiting for a reply…")
        self._status.set_state("Waiting on a reply…", busy=True)

        handle = self._runtime.submit(_tagged(session, session.say(message)),
                                      label="diplomacy")
        handle.finished.connect(self._on_reply)
        handle.failed.connect(self._on_call_failed)

    def _on_reply(self, landed) -> None:
        """A reply, shown in its own talk and nowhere else.

        The rail has one chat page, and the talk on it may have changed while the reply
        was on its way. A reply to a talk that is no longer open is said on the status
        line rather than written into somebody else's conversation.
        """
        session, turn = landed
        name = self._nation_name(turn.speaker)
        if session is not self._session:
            self._status.set_state(f"{name} replied, but those talks are no longer open.")
            return
        self._right.append_turn(name, turn.text, is_player=False)
        self._status.set_state("Ready")

    def _on_close_chat(self) -> None:
        session = self._session
        if session is None:
            return
        self._right.append_note("recording what was agreed…")
        self._status.set_state("Recording agreements…", busy=True)

        handle = self._runtime.submit(_tagged(session, session.close()),
                                      label="extract-commitments")
        handle.finished.connect(self._on_commitments_recorded)
        handle.failed.connect(self._on_call_failed)

    def _on_commitments_recorded(self, landed) -> None:
        """What a concluded talk settled, said about that talk and no other.

        **Extraction is a model call, and it can take a while.** The player need not wait
        for it: *Back* and another nation opens a new talk while this one is still being
        read. This used to clear `_session` whatever it held by then -- so the new talk
        was ended by the old one's landing, and every message typed to the new partner
        went nowhere, silently -- and it wrote the old talk's verdict into the new chat.
        Now it ends only the talk it was recording; if that talk has already been left,
        its verdict goes on the status line with the partner's name on it.
        """
        session, ids = landed
        count = len(ids or [])
        note = (f"{count} agreement(s) recorded. The world will act on them at the next jump."
                if count else "Nothing was settled.")
        if session is self._session:
            self._right.append_note(note)
            self._session = None
            self._refresh_all()
            return
        self._refresh_all()
        # After the refresh, which ends by saying "Ready".
        self._status.set_state(f"Talks with {self._nation_name(session.counterpart)}: {note}")

    def _nation_name(self, nation_id: str) -> str:
        row = self._conn.execute(
            "SELECT name FROM nation WHERE id = ?", (nation_id,)).fetchone()
        return row["name"] if row else nation_id

    # -- jump --------------------------------------------------------------

    def _on_jump(self, days: int, *, confirmed: bool = False) -> None:
        """Spend the turn -- after a last look at anything in it that cannot be undone.

        The check is here rather than beside each order for one reason: this is the moment
        the orders stop being a draft. Everything before it is editable and costs nothing,
        and a confirmation on a draft is a confirmation about a thing that has not happened.

        **`confirmed` is only ever passed by `_jump_confirmed`.** The pass-through used to
        be "a second press for the same length of jump", remembered in
        `_awaiting_confirmation` -- and nothing forgot it when the player backed out, so the
        press after *Go back* spent a declaration of war without asking.
        """
        if self._jump_started is not None:
            return
        if not confirmed and self._asked_first(partial(self._on_jump, days, confirmed=True)):
            return
        self._awaiting_confirmation = None
        self._jump_started = time.monotonic()
        self._right.set_busy(True)
        self._status.set_state(f"Living through {days} days…", busy=True)

        row = self._conn.execute(
            "SELECT world_date FROM world WHERE id = ?", (self._world_id,)
        ).fetchone()
        # **Everything the view needs to show the turn happening, handed over once.** The
        # connection is the one the rest of the interface reads on -- the view only reads,
        # and only rows the turn has already committed. The viewer is what scopes the wire
        # to what this government is entitled to know.
        self._orders_in_flight = list(self._pending_orders)
        self._jump_overlay.begin(
            days,
            row["world_date"],
            _add_days(row["world_date"], days),
            conn=self._conn,
            world_id=self._world_id,
            turn=self._current_turn() + 1,
            viewer=self._player,
            expectation=estimated_advance(self._conn, self._world_id, turns=1),
        )

        handle = self._runtime.submit(
            self._engine.jump(days=days, actions=self._orders_in_flight), label="world-sim"
        )
        handle.finished.connect(self._on_jump_finished)
        handle.failed.connect(self._on_turn_failed)

    def _asked_first(self, then: Callable[[], None]) -> bool:
        """Put the question if the draft holds anything grave, and say whether it did.

        One question for both ways into a turn. `then` is the same call made again with
        `confirmed=True`, and it runs only from `_jump_confirmed`.
        """
        grave = needed_for(self._conn, self._world_id, self._pending_orders)
        if not grave:
            return False
        self._awaiting_confirmation = then
        self._confirm.show_confirmations(grave)
        self._confirm.open()
        return True

    def _jump_confirmed(self) -> None:
        """The player looked at what cannot be undone and meant it."""
        then, self._awaiting_confirmation = self._awaiting_confirmation, None
        if then is not None:
            # Straight back into `_on_jump` or `_run_advance` rather than a second copy of
            # either: one path into the turn, and `confirmed` is what makes it go through.
            then()

    def _jump_declined(self) -> None:
        """The player backed out of the question. The next press asks it again."""
        self._awaiting_confirmation = None

    def _on_cancel_jump(self) -> None:
        """Stop after the turn in progress: never inside one.

        Killing a provider mid-write is how a turn ends up half-applied with nothing
        recording that it was interrupted, so the turn under way always finishes. What
        stopping *does* stop is every turn after it: an advance reads `_stop_advance`
        between turns and ends the run there. It used to change this sentence and nothing
        else, and a four-turn advance ran and billed all four.

        The sheet becomes dismissable, which puts the top bar back in reach while the loop
        thread is still writing the world -- so everything on that bar that writes or
        copies the world refuses until the turn lands. See `_turn_in_flight`.
        """
        if self._stop_advance is not None:
            self._stop_advance.set()
        self._jump_overlay.closable = True
        self._status.set_state("Finishing this turn, then stopping…", busy=True)

    def _on_jump_finished(self, report) -> None:
        elapsed = time.monotonic() - (self._jump_started or time.monotonic())
        self._jump_started = None
        self._stop_advance = None
        # One last look at the wire before it closes. The last events of a turn are written
        # inside the final second of it, and a poll on a two-second heartbeat would miss
        # them -- so the news the player watched arrive is complete rather than nearly so.
        self._jump_overlay.drain_wire()
        self._jump_overlay.closable = True
        self._jump_overlay.close_overlay()
        self._right.set_busy(False)
        self._orders_landed(attempted=report.status is not TurnStatus.ABORTED)

        self._status.set_last_turn(
            seconds=elapsed, calls=report.calls, rejected=report.rejected_batches
        )
        self._refresh_all()

        # **The verdict goes on after the refresh, not before it.** `_refresh_all` ends by
        # setting the strip to "Ready", which is right when it runs for any other reason
        # and wrong here: said first, this verdict was overwritten one line later. A turn
        # in which every provider call failed reported itself as *Ready*, with a time
        # beside it. The world itself was never at risk -- an aborted turn is not written,
        # and the bar above still read TURN 14 -- but the one line a player checks at a
        # glance said the opposite of what had just happened.
        #
        # **On the status, not on the failures.** A partial turn is a turn with failures
        # in it -- that is what makes it partial -- so asking about `failures` first
        # reported every partial turn as one that "could not be completed", over a world
        # that had in fact moved a month and a bar that said so. Aborted is the only turn
        # that did not happen; partial happened and is kept; anything else is sealed.
        if report.status is TurnStatus.ABORTED:
            self._status.set_state(
                "The turn could not be completed. Your orders are still drafted.",
                problem=True, detail="; ".join(report.failures[:3]))
        elif report.status is TurnStatus.PARTIAL:
            self._status.set_state(
                "Turn resolved partially; it was kept rather than discarded.",
                detail="; ".join(report.failures[:3]))
        else:
            self._status.set_state("Ready")

        self._summary.show_report(
            report, self._headlines(report.turn), self._theme,
            conn=self._conn, world_id=self._world_id, viewer=self._player,
            watching=self._watched_regions())
        self._summary.open()
        if report.status is not TurnStatus.ABORTED:
            self._check_ending(report)

    def _check_ending(self, report) -> None:
        """Victory, defeat, another power's win, or the campaign's last turn: say so.

        Over the summary rather than instead of it -- the turn that ended the campaign is
        still a turn worth reading. See :mod:`atlas.engine.campaign` for the endings.
        """
        from ..engine.campaign import ending_after, reckoning
        from .endscreen import EndScreen

        ending = ending_after(self._conn, self._world_id, turn=report.turn,
                              victories=list(report.victories), player=self._player)
        if ending is None or ending.key in self._endings_seen:
            return
        self._endings_seen.add(ending.key)
        date = self._conn.execute(
            "SELECT world_date FROM world WHERE id = ?", (self._world_id,)).fetchone()
        sheet = EndScreen(ending, reckoning(self._conn, self._world_id, self._player),
                          theme=self._theme, date=str(date[0]) if date else "", parent=self)
        sheet.chosen.connect(lambda choice: self._ending_chosen(ending, choice))
        self._end_screen = sheet
        sheet.open()

    def _ending_chosen(self, ending, choice: str) -> None:
        from ..engine.campaign import play_on

        if choice == "play_on":
            play_on(self._conn, ending)
            return
        # Ending here closes the game: under `atlas start` that is the main menu.
        self.close()

    def _orders_landed(self, *, attempted: bool) -> None:
        """Take the orders the turn spent out of the draft, and only those.

        **Not attempted is not spent.** A turn that aborted -- a provider not installed or
        not authenticated, the commonest real failure there is -- returned before a single
        order was tried, and the draft used to be emptied regardless: the player's orders
        were gone and the clock had not moved.

        **Spent means these orders, not the draft.** Anything that joined the draft while
        the turn was running was written against the world the turn was about to change,
        and belongs to the next one; it used to be wiped with the rest. The box is emptied
        only when nothing is left in the draft for it to describe.
        """
        sent, self._orders_in_flight = self._orders_in_flight, []
        if not attempted:
            return
        spent = {id(order) for order in sent}
        left = [order for order in self._pending_orders if id(order) not in spent]
        if not left:
            self._right.clear_orders()
        self._pending_orders = left
        self._keep_orders()

    def _headlines(self, turn: int) -> list[str]:
        """The turn told as events, viewer-scoped like everything else."""
        sql = """
            SELECT e.text FROM event e
             WHERE e.world_id = ? AND e.turn = ?
        """
        params: list = [self._world_id, turn]
        if self._player:
            sql += """
               AND (e.visibility = 'public' OR e.actor_nation_id = ?
                    OR EXISTS (SELECT 1 FROM event_visibility v
                                WHERE v.event_id = e.id AND v.nation_id = ?))
            """
            params += [self._player, self._player]
        sql += " ORDER BY e.id LIMIT 8"
        return [row["text"] for row in self._conn.execute(sql, tuple(params))]

    def _on_call_failed(self, error) -> None:
        """A call beside the turn failed: a parse, a reply, a record of what was agreed.

        **Says so and touches nothing else.** This used to be the turn's failure handler
        as well, so a parse that failed while a turn was running cleared `_jump_started`
        and took the turn's sheet down with the loop thread still writing the world --
        which reopened rewind, fork and export mid-turn by a second door.
        """
        self._status.set_state("That could not be completed.", problem=True,
                               detail=str(error))

    def _turn_in_flight(self, what: str) -> bool:
        """Whether a jump or an advance is still running, saying so if it is.

        The turn runs on the loop thread and writes the world through the same
        connection. Anything that rewrites the history (rewind), copies it (fork, export)
        or reads it as a whole has to wait for it to land: a rewind made mid-turn deleted
        turns 2 and 3 from under an advance that went on to write turn 4.
        """
        if self._jump_started is None:
            return False
        self._status.set_state(
            f"A turn is still being lived through; {what} once it has landed.",
            problem=True)
        return True

    def _on_turn_failed(self, error) -> None:
        self._jump_started = None
        self._stop_advance = None
        # Raised rather than reported, so there is no telling how far it got. The draft
        # stays as it is: the player can see the turn did not land and decide.
        self._orders_in_flight = []
        self._jump_overlay.closable = True
        self._jump_overlay.close_overlay()
        self._right.set_busy(False)
        # The fault itself is a provider name and an exit status. It stays reachable on
        # the tooltip; the sentence a player reads is about the month, not the machinery.
        self._status.set_state("The turn broke off before the month was through.",
                               problem=True, detail=str(error))

    # -- rewind and export -------------------------------------------------

    def _open_homefront(self) -> None:
        self._load_homefront()
        self._homefront.open()

    def _load_homefront(self) -> None:
        self._homefront.load(
            self._conn, self._world_id, self._player, turn=self._current_turn())

    def _open_dependency(self) -> None:
        """Who supplies this government, and what stops if they stop.

        The lane picture is given the pack's own label points, so the countries on it sit
        where they sit on the map rather than on a diagram. Building the mapping here
        rather than inside the panel keeps the panel free of the pack: a test, a synthetic
        world and a headless render all reach the same screen without one.
        """
        self._dependency.load(
            self._conn, self._world_id, self._player, turn=self._current_turn(),
            anchors=anchors_from(self._conn, self._world_id, self._labels))
        self._dependency.open()

    def _open_search(self) -> None:
        self._search.load(self._conn, self._world_id, self._player,
                          {(pin.kind, pin.target) for pin in self._pins})
        self._search.open()

    def _open_watchlist(self) -> None:
        self._load_watchlist()
        self._watchlist.open()

    def _load_watchlist(self) -> None:
        self._watchlist.load(self._conn, self._world_id, self._player, self._pins)

    def _toggle_pin(self, kind: str, target: str) -> None:
        """Start or stop watching something, and keep the answer.

        **Beside the save, never in it.** A pin is a fact about a person using a program,
        not about the world: putting it in the database would move the fingerprint because
        somebody clicked a star, and a rewind would throw away a note about what to watch.
        See `atlas.state.workspace` for why that is structural rather than careful.
        """
        from ..engine.watchlist import Pin, toggled
        from ..state.workspace import remember

        self._pins = toggled(self._pins, Pin(kind, target))
        remember(self._conn, "pins", [pin.as_pair() for pin in self._pins])
        # Both screens can show the same pin, so both are told. Rebuilt rather than
        # patched, for the reason the map is: two copies of a list drift.
        if self._watchlist.isVisible():
            self._watchlist.load(self._conn, self._world_id, self._player, self._pins)
        if self._search.isVisible():
            self._search.load(self._conn, self._world_id, self._player,
                              {(pin.kind, pin.target) for pin in self._pins})

    def _reveal_from_summary(self, region_id: str) -> None:
        """Close the summary and centre the map on the province a headline named."""
        self._summary.close_overlay()
        self._reveal_region(region_id)

    def _inspect_from_summary(self, event_id: int) -> None:
        """From *what affected you* into what caused it. The summary closes first: the
        point of the hop is the causal picture, not two panels stacked."""
        self._summary.close_overlay()
        self._inspect_event(event_id)

    def _open_matters(self) -> None:
        """The bar's Attention control: read the ranking again, then pull the drawer out.

        Re-read rather than shown as it was: the count on the bar is refreshed with the
        world, but the rows are laid out when somebody asks for them, so what is in the
        drawer is what is true when it is opened rather than what was true when the turn
        landed.
        """
        self._load_matters()
        self._matters.open()
        # A third column has just appeared, and on a narrow window something has to give.
        self._fit_rails(self.width())

    def _on_matters_closed(self) -> None:
        """The drawer's handle was pressed. The bar keeps the count and the way back."""
        self._fit_rails(self.width())

    def _load_matters(self) -> None:
        """One read of the ranking, feeding both the drawer's rows and the bar's count.

        Both off the same call, deliberately: a count taken from a second call to the
        substrate is a second answer, and the one thing the shared ranking is for is that
        every surface agrees. See `atlas.ui.matters.MattersDrawer.waiting`.
        """
        self._matters.load(
            self._conn, self._world_id, self._player,
            turn=self._current_turn(), viewer_name=self._player_name,
            watching=self._watched_regions())
        self._top.set_attention(self._matters.waiting)

    def _lens_from_matters(self, _nation_id: str) -> None:
        """An economic warning asked for the page behind it.

        **The player's own economy, whoever the warning named.** A warning that Karthos now
        supplies most of your oil is a fact about *your* position, and the lens pointed at
        Karthos would answer a different question -- and answer most of it with *unknown*,
        because you are not party to its other agreements. The partner is on the page; the
        page is yours.

        The drawer stays where it is, for the reason travelling from it does: the map and
        the other two claims are still live beside it.
        """
        self._open_dependency()

    def _travel_from_matters(self, kind: str, target: str) -> None:
        """Take the same route the search box takes, and leave the drawer where it is.

        **It used to close first, and that was right for a sheet.** A floating panel had to
        get out of the way before the map behind it could be looked at, so travelling from
        one meant dismissing it -- and a player who acted on the first of three claims lost
        the other two. A drawer does not have that problem: the map is live beside it, the
        hop lands on the province, and the second and third claims are still on the glass
        to be dealt with. Closing it is the handle, and only the handle.
        """
        self._go_to(kind, target)

    def _go_to(self, kind: str, target: str) -> None:
        """Take a search result somewhere the player could already have got to.

        **Every branch here is an existing path**, which is what makes the box a shortcut
        through the program rather than a fifth way of looking at it: a province takes the
        same route a click on the map takes, and an event or a treaty opens the inspector
        that was already the answer to *why did that happen*.

        A treaty is shown through the event that recorded it, because that is where its
        causes are. A treaty with no source event -- a scenario's opening alliances, which
        nobody signed inside the game -- has nothing to inspect and does nothing here
        rather than opening an empty panel.
        """
        self._search.close_overlay()
        if kind == "region":
            self._reveal_region(target)
            return
        if kind == "nation":
            row = self._conn.execute(
                "SELECT capital_region_id FROM nation WHERE world_id = ? AND id = ?",
                (self._world_id, target)).fetchone()
            if row and row["capital_region_id"]:
                self._reveal_region(str(row["capital_region_id"]))
            return
        if kind == "event":
            self._inspect_event(int(target))
            return
        if kind == "treaty":
            row = self._conn.execute(
                "SELECT source_event_id FROM commitment WHERE world_id = ? AND id = ?",
                (self._world_id, int(target))).fetchone()
            if row and row["source_event_id"]:
                self._inspect_event(int(row["source_event_id"]))

    def _open_alerts(self) -> None:
        self._load_alerts()
        self._alerts.open()

    def _load_alerts(self) -> None:
        self._alerts.load(
            self._conn, self._world_id, self._player, turn=self._current_turn(),
            watching=self._watched_regions())

    def _watched_regions(self) -> frozenset[str]:
        """The provinces the player has pinned.

        Read from the workspace's pins rather than from the world, because that is where
        they live -- see :mod:`atlas.engine.watchlist`. It is what makes an air war on the
        other side of the map this player's business when they have said it is.
        """
        return frozenset(
            pin.target for pin in self._pins if pin.kind == "region")

    def _reveal_from_alert(self, region_id: str) -> None:
        """Close the panel first: the point of a map hop is to look at the map."""
        self._alerts.close_overlay()
        self._reveal_region(region_id)

    def _open_branches(self) -> None:
        self._branches.load(self._conn, self._world_id)
        self._branches.open()

    def _do_fork(self, turn: int, name: str) -> None:
        """Fork the line. The original -- this world -- is only read, never written."""
        if self._turn_in_flight("fork"):
            return
        message = fork_here(self._conn, self._world_id, turn, name)
        problem = not message.startswith("Forked")
        self._branches.report(message, problem=problem, current=self._world_id)
        self._status.set_state(message, problem=problem)

    def _open_advance(self) -> None:
        self._advance.set_expectation(self._conn, self._world_id)
        self._advance.open()

    def _run_advance(self, cap: int, watch, *, confirmed: bool = False) -> None:
        """Run turns until something happens, on the loop thread like any other jump.

        **It asks the question a jump asks.** It used to go straight to the engine, so a
        declaration of war drafted for the next turn was spent unasked by the one control
        that runs the most turns. Asked once, before the first turn: the orders ride on
        that turn alone, and the question is about the world they will land in, which is
        this one. `confirmed` is only ever passed by `_jump_confirmed`.
        """
        if self._jump_started is not None:
            return
        if not confirmed and self._asked_first(
                partial(self._run_advance, cap, watch, confirmed=True)):
            return
        self._awaiting_confirmation = None
        self._jump_started = time.monotonic()
        self._stop_advance = threading.Event()
        self._right.set_busy(True)
        self._status.set_state(f"Advancing up to {cap} turns…", busy=True)

        # **The longest wait in the program, and until now the only one with nothing to
        # watch.** A jump opened the turning view; an advance -- which is up to two dozen
        # of the same turns back to back -- set a status line and left the player looking
        # at a window that appeared idle for a quarter of an hour. The view stays up across
        # all of them and turns its page as each month begins; see `ui.turning`.
        self._advance.close_overlay()
        row = self._conn.execute(
            "SELECT world_date FROM world WHERE id = ?", (self._world_id,)
        ).fetchone()
        self._orders_in_flight = list(self._pending_orders)
        self._jump_overlay.begin(
            30,
            row["world_date"],
            _add_days(row["world_date"], 30),
            conn=self._conn,
            world_id=self._world_id,
            turn=self._current_turn() + 1,
            viewer=self._player,
            expectation=estimated_advance(self._conn, self._world_id, turns=cap),
            months=cap,
        )
        handle = self._runtime.submit(
            self._engine.advance(turns=cap, days=30, actions=self._orders_in_flight,
                                 watch=watch, stop=self._stop_advance),
            label="advance")
        handle.finished.connect(self._on_advance_finished)
        handle.failed.connect(self._on_turn_failed)

    def _on_advance_finished(self, report) -> None:
        self._jump_started = None
        self._stop_advance = None
        # The last month's news is written inside its final second; take it before the
        # sheet comes down, for the reason `_on_jump_finished` gives.
        self._jump_overlay.drain_wire()
        self._jump_overlay.closable = True
        self._jump_overlay.close_overlay()
        self._right.set_busy(False)
        # Orders ride on the first turn only, so they were attempted unless that one is
        # the turn that aborted. A later abort still spent them.
        first = report.jumps[0] if report.jumps else None
        attempted = first is not None and first.status is not TurnStatus.ABORTED
        self._orders_landed(attempted=attempted)
        self._refresh_all()
        # **After the refresh, for the reason `_on_jump_finished` gives**: `_refresh_all`
        # ends by saying "Ready", so a verdict said first was overwritten one line later and
        # an advance that aborted -- orders still drafted, nothing lived -- reported itself
        # as ready. And on the status rather than the failures, the same way: a month that
        # was lived partially is kept, and is not a month that could not be completed.
        failures = [f for jump in report.jumps for f in jump.failures]
        if report.aborted:
            self._status.set_state(
                "The turn could not be completed." + (
                    "" if attempted else " Your orders are still drafted."),
                problem=True, detail="; ".join(failures[:3]))
        elif any(jump.status is TurnStatus.PARTIAL for jump in report.jumps):
            self._status.set_state(
                "Turn resolved partially; it was kept rather than discarded.",
                detail="; ".join(failures[:3]))
        self._advance.show_report(report, self._conn, self._world_id)
        self._advance.open()

    def _inspect_event(self, event_id: int) -> None:
        """One hop from a halt to the whole causal picture."""
        self._advance.close_overlay()
        self._inspector.load(self._conn, self._world_id, event_id)
        self._inspector.open()

    def _open_workbench(self) -> None:
        """Show what the orders already checked will do, before the turn is spent.

        Reads `_pending_orders`, which is what the parser last produced -- so the workbench
        reviews exactly the actions a jump would carry, and never a re-parse that might
        differ from them.
        """
        self._load_workbench()
        self._workbench.open()

    def _load_workbench(self) -> None:
        self._workbench.load(
            self._conn, self._world_id, self._player, self._pending_orders)

    def _open_dispatch(self) -> None:
        """The turn as a page: what happened across the world, and to whom.

        Scoped to the player for the reason the trace panel is -- a report is what a
        government was told, not what the file holds. Spectating, there is nobody to scope
        it to and the referee reads the log.
        """
        self._load_dispatch()
        self._dispatch.open()

    def _load_dispatch(self) -> None:
        self._dispatch.load(
            self._conn, self._world_id, self._player, self._current_turn())

    def _read_dispatch(self) -> None:
        """From the turn summary into the turn itself."""
        self._summary.close_overlay()
        self._open_dispatch()

    def _inspect_from_dispatch(self, event_id: int) -> None:
        """A line in the report, opened as an event. Close the page first: the point of
        the hop is to look at what one line did, not to stack two panels."""
        self._dispatch.close_overlay()
        self._inspect_event(event_id)

    def _open_cast(self) -> None:
        """The powers the world revolves around for this player, and what each just did.

        Scoped to the player for the reason the dispatch and the trace are: a cast list
        carries the last thing each power did, and *the last thing it did* is a question
        with a different answer for a government than for whoever holds the file.
        """
        self._load_cast()
        self._cast.open()

    def _load_cast(self) -> None:
        self._cast.load(self._conn, self._world_id, self._player, self._current_turn())

    def _inspect_from_cast(self, event_id: int) -> None:
        """An act on a cast row, opened as an event. Close the roster first: the point of
        the hop is to look at what one act did, not to stack two panels."""
        self._cast.close_overlay()
        self._inspect_event(event_id)

    def _open_correspondence(self) -> None:
        """What the powers said, to the government that was told it.

        Scoped to the player, and this is the panel where that matters most: a
        confidential channel between two other powers is exactly the sort of thing an
        unscoped feed would hand over, and it is also the sort of thing a player would
        never know they had been given.
        """
        self._load_correspondence()
        self._post.open()

    def _load_correspondence(self) -> None:
        self._post.load(self._conn, self._world_id, self._player)

    def _inspect_from_correspondence(self, event_id: int) -> None:
        """A letter, opened as an event. Close the feed first: the point of the hop is to
        look at what one letter did, not to stack two panels."""
        self._post.close_overlay()
        self._inspect_event(event_id)

    # -- the council -------------------------------------------------------

    def _open_council(self) -> None:
        """Ask your staff: the advisor role, with a screen instead of a rail.

        The panel is opened on whatever is there. Spectating, it says why it is empty
        rather than refusing to open -- a screen that will not appear teaches nothing
        about what it would have shown.
        """
        self._staff.load(self._conn, self._world_id, self._player,
                         viewer_name=self._player_name)
        self._staff.open()

    def _ask_the_council(self, question: str) -> None:
        """Put the player's own words to their own government.

        The session is built here and lives as long as the conversation, because the
        transcript *is* the memory: the council is stateless and is handed its history
        back as data on every call.

        **Built against `advice_only`.** That wrapper is what makes "the council cannot
        write" a property of this call rather than of a module somewhere else -- see
        `ui.council`. It is the only difference between this session and the rail's.
        """
        if not self._player:
            return
        if self._council is None:
            self._council = AdvisorSession(
                conn=self._conn,
                world_id=self._world_id,
                player_nation=self._player,
                providers=advice_only(self._providers),
                router=self._router,
            )
        self._staff.put_question(question)
        self._status.set_state("Consulting your council…", busy=True)
        handle = self._runtime.submit(self._council.ask(question), label="council")
        handle.finished.connect(self._on_council_answer)
        handle.failed.connect(self._on_council_failed)

    def _on_council_answer(self, turn) -> None:
        self._staff.show_answer(turn.speaker, turn.text)
        self._status.set_state("Ready")

    def _on_council_failed(self, error) -> None:
        """A failed consultation is a failed consultation. It is not a failed turn.

        The general handler closes the jump overlay and un-busies the rail, which is right
        for a call inside a turn and wrong for this one: nothing was spent and nothing is
        in flight but the question on the panel.
        """
        self._staff.show_problem(
            "Your ministers could not be reached. Nothing has been asked of them, and "
            "nothing about the world has changed — put the question again.",
            detail=str(error))
        self._status.set_state("Your council could not be reached.", problem=True,
                               detail=str(error))

    def _forget_the_council(self) -> None:
        """Start again: the transcript goes, and the session that carries it goes with it."""
        self._council = None

    def _open_trace(self, field: str) -> None:
        """Why one of the player's own figures is where it is.

        Scoped to the player rather than to the save: a chain leads back to covert acts as
        readily as to public ones, and this panel is opened by a government. Spectating,
        there is no government to scope it to and nothing to explain.
        """
        if not self._player:
            return
        self._trace.load(
            self._conn, self._world_id, self._player, table="nation",
            target=self._player, field=field, turn=self._current_turn(),
            subject=self._player_name)
        self._trace.open()

    def _trace_event(self, event_id: int) -> None:
        """From one event to everything recorded behind it."""
        self._inspector.close_overlay()
        self._trace.load_event(self._conn, self._world_id, self._player, event_id)
        self._trace.open()

    def _open_inspector(self) -> None:
        """Open the causal inspector on the newest event, or on the selected region's most
        recent one if the player has a province in mind."""
        self._inspector.load(self._conn, self._world_id)
        self._inspector.open()

    def _reveal_region(self, region_id: str) -> None:
        """Select a province and put it in the middle of the map.

        Selection is the existing path -- the same one a click on the map takes -- so the
        left rail and the highlight both follow. Centring is the only new part: the scene
        is in projected degrees, so a region's label point projects straight to the scene
        coordinate to centre on.
        """
        self._on_region_clicked(region_id)
        row = self._conn.execute(
            "SELECT geo_key FROM region WHERE world_id = ? AND id = ?",
            (self._world_id, region_id),
        ).fetchone()
        point = self._labels.get(row["geo_key"]) if row else None
        if point is not None:
            self._map.centerOn(*self._projection.project(*point))

    def _open_directives(self) -> None:
        self._directives.load(self._conn, self._world_id, self._player)
        self._directives.open()

    def _open_strategy(self) -> None:
        """Read the world and show where the player stands. Opens on whatever is there --
        a world with no player nation says so rather than refusing to open."""
        self._load_strategy()
        self._strategy.open()

    def _load_strategy(self) -> None:
        self._strategy.load(
            self._conn, self._world_id, self._player, turn=self._current_turn())

    def _current_turn(self) -> int:
        row = self._conn.execute(
            "SELECT turn_number FROM world WHERE id = ?", (self._world_id,)
        ).fetchone()
        return int(row["turn_number"]) if row else 0

    def _open_rewind(self) -> None:
        self._rewind.load(self._conn, self._world_id)
        self._rewind.open()

    def _open_undo(self) -> None:
        """Go back a turn: the question a player actually has after a jump lands badly.

        The turn list is the right screen for going back to *somewhere*; it is the wrong
        one for going back to the turn before this one, which is the answer nine times in
        ten and which the player already has. This chooses it for them and then asks --
        because rewind cannot itself be undone, and what is about to be discarded has to
        be named before it goes.
        """
        self._undo_turn.load(self._conn, self._world_id)
        self._undo_turn.open()

    def _do_rewind(self, target: int) -> None:
        from ..state.replay import rewind_to

        if self._turn_in_flight("rewind"):
            return
        try:
            report = rewind_to(self._conn, self._world_id, target)
        except sqlite3.Error as exc:
            from ..state.problems import describe

            self._status.set_state(describe(exc, what="The rewind"), problem=True)
            return
        if not report.ok:
            self._status.set_state("; ".join(report.problems), problem=True)
            return
        self._selected_region = None
        self._selected_counter = None
        # A province asked for in a world that has just been unmade. Completing it later
        # would file an order written against turns that no longer happened.
        self._forget_awaiting(say=False)
        self._session = None
        # The council was briefed on a world that no longer happened, and its transcript
        # is a conversation about events the rewind has just deleted.
        self._council = None
        self._staff.reset()
        self._pending_orders = []
        self._keep_orders()
        self._refresh_all()
        self._status.set_state(
            f"Rewound to turn {target}: {report.diffs_undone} change(s) undone, "
            f"{report.events_removed} event(s) discarded."
        )

    def _open_export(self) -> None:
        from pathlib import Path

        self._export.set_destination(
            str(Path.home() / "Desktop" / f"{self._world_id}.atlas.db")
        )
        self._export.open()

    def _do_export(self, destination: str) -> None:
        from ..state.db import export_world

        if self._turn_in_flight("export"):
            self._export.show_result(
                "A turn is still running. Export once it has landed.", ok=False)
            return
        from ..state.problems import WORLD_ERRORS, describe

        try:
            written = export_world(self._conn, destination)
        except WORLD_ERRORS as exc:
            self._export.show_result(describe(exc, what="The copy"), ok=False)
            return
        except OSError as exc:
            self._export.show_result(str(exc), ok=False)
            return
        size = written.stat().st_size / (1024 * 1024)
        self._export.show_result(f"Wrote {figure(size, places=1)} MB to {written}.")


def _scenario_name(conn: sqlite3.Connection, world_id: str) -> str:
    row = conn.execute("SELECT name FROM world WHERE id = ?", (world_id,)).fetchone()
    return str(row["name"]) if row else world_id


def _db_path_of(conn: sqlite3.Connection) -> str:
    for _seq, name, path in conn.execute("PRAGMA database_list"):
        if name == "main":
            return str(path)
    return ""


def _add_days(iso: str, days: int) -> str:
    from datetime import date, timedelta

    try:
        return (date.fromisoformat(iso) + timedelta(days=days)).isoformat()
    except (TypeError, ValueError):
        return iso
