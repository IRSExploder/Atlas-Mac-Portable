"""One box, four kinds of answer, and a way through to each of them.

**A result you cannot act on is not a result.** Every row here leads somewhere the player
was already able to go -- a province is selected and centred by the same path a click on the
map takes, an event opens in the inspector, a treaty opens on the event that recorded it --
so the box is a shortcut through the program rather than a fifth way of looking at it.

**It types as you type.** The engine's `search` is a handful of `SELECT`s over a world that
fits in memory, so there is no debounce and no worker: every keystroke re-runs it. If that
ever stops being true the fix is a timer here, not a cache, because a cached search is a
second copy of the world and this program has an opinion about those.

Nothing is written by opening, typing in, or dismissing this.
"""

from __future__ import annotations

import sqlite3

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..engine.against import Doable, what_can_i_do_about
from ..engine.search import Hit, search
from .overlays import Overlay
from .theme import PANEL, SPACE, Theme
from .widgets import SectionLabel, TextLabel, clear_layout

__all__ = ["SearchOverlay"]

#: What each kind is called on screen, and what colour marks it. Named for the question the
#: player was asking rather than for the table it came out of.
_KINDS: dict[str, tuple[str, str]] = {
    "region": ("Province", "brass"),
    "nation": ("Power", "info"),
    "event": ("What happened", "ink_faint"),
    "treaty": ("Agreement", "ink_faint"),
}


class _DoableRow(QFrame):
    """One thing you could order about the answer above it.

    **Nothing here is offered that the engine would refuse** -- `engine.against` asks the
    catalogue's own availability test and the undertakings' own requirement gate. A line that
    is a whole order can be taken from here; one that still needs a sentence written says so
    and stays unclickable, because an order appended half-written is refused at the door and
    the player never sees why.
    """

    taken = Signal(object, str)

    def __init__(self, doable: Doable, *, theme: Theme,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._doable = doable
        self.setObjectName("doableRow")
        if doable.ready:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 6, 13, 7)
        layout.setSpacing(2)
        lead = "~ " if doable.is_gamble else ""
        self._what = TextLabel(f"{lead}{doable.explain}", style="body",
                               colour="ink_bright" if doable.ready else "ink_dim",
                               theme=theme, wrap=True)
        layout.addWidget(self._what)
        tail = doable.cost or "nothing directly"
        if doable.still_needs:
            tail += " — you still have to say: " + ", ".join(
                need.replace("_", " ") for need in doable.still_needs)
        self._why = TextLabel(tail, style="mono_small", colour="ink_ghost", theme=theme,
                              wrap=True)
        layout.addWidget(self._why)

    def mousePressEvent(self, event) -> None:
        if self._doable.ready:
            self.taken.emit(dict(self._doable.prefill or {}), self._doable.explain)
        super().mousePressEvent(event)

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.setStyleSheet(
            f"QFrame#doableRow {{ background: {theme.panel};"
            f" border-left: 3px solid {theme.rule_soft}; }}")
        for child in (self._what, self._why):
            child.apply_theme(theme)


class _HitRow(QFrame):
    """One answer, and the way through to it."""

    chosen = Signal(str, str)
    pinned = Signal(str, str)
    #: ``(kind, target)`` — show, or stop showing, what could be ordered about this one.
    asked = Signal(str, str)

    def __init__(self, hit: Hit, *, watched: bool, theme: Theme,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._hit = hit
        self.setObjectName("hitRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(13, 9, 9, 10)
        outer.setSpacing(10)
        layout = QVBoxLayout()
        layout.setSpacing(3)
        name, self._colour = _KINDS.get(hit.kind, (hit.kind, "ink_faint"))
        self._what = TextLabel(hit.label, style="body", colour="ink_bright", theme=theme,
                               wrap=True)
        layout.addWidget(self._what)
        self._why = TextLabel(
            f"{name}" + (f" — {hit.detail}" if hit.detail else ""),
            style="mono_small", colour="ink_dim", theme=theme)
        layout.addWidget(self._why)
        outer.addLayout(layout, 1)

        # **Findable and keepable are the same gesture.** Anything worth searching for twice
        # is worth pinning once, and putting the control here is what stops the watchlist
        # being a screen the player has to go and fill from somewhere else.
        self._pin = TextLabel("unpin" if watched else "pin", style="mono_small",
                              colour="brass", theme=theme)
        self._pin.setCursor(Qt.CursorShape.PointingHandCursor)
        outer.addWidget(self._pin, 0, Qt.AlignmentFlag.AlignVCenter)

        # **Finding a thing and doing something about it are one gesture.** A province you
        # have just looked up is one you were about to act on; the map already answers this
        # for a province you can see, and this answers it for one you had to search for.
        self._ask: TextLabel | None = None
        if hit.kind in ("region", "nation"):
            self._ask = TextLabel("what can I do", style="mono_small", colour="info",
                                  theme=theme)
            self._ask.setCursor(Qt.CursorShape.PointingHandCursor)
            outer.addWidget(self._ask, 0, Qt.AlignmentFlag.AlignVCenter)

    def mousePressEvent(self, event) -> None:
        where = event.position().toPoint()
        if self._pin.geometry().contains(where):
            self.pinned.emit(self._hit.kind, self._hit.target)
        elif self._ask is not None and self._ask.geometry().contains(where):
            self.asked.emit(self._hit.kind, self._hit.target)
        else:
            self.chosen.emit(self._hit.kind, self._hit.target)
        super().mousePressEvent(event)

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        edge = getattr(theme, self._colour, theme.rule)
        self.setStyleSheet(
            f"QFrame#hitRow {{ background: {theme.inset};"
            f" border-left: 3px solid {edge}; }}")
        for child in (self._what, self._why, self._pin, self._ask):
            if child is not None:
                child.apply_theme(theme)


class SearchOverlay(Overlay):
    """Find a province, a power, something that happened, or something that was signed."""

    #: ``(kind, target)``. The window decides what each kind means; this only finds things.
    chosen = Signal(str, str)
    #: ``(kind, target)`` — pin or unpin, whichever this one currently is.
    pinned = Signal(str, str)
    #: ``(action, label)`` — an act taken from a result's own list of what could be ordered
    #: about it. The action is a whole order; the window appends it as it would any other.
    ordered = Signal(object, str)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(theme, parent)
        self.set_panel_width(PANEL.panel)

        self.body.addWidget(SectionLabel("Find anything", theme=theme))
        self.body.addSpacing(SPACE.sm)

        self._box = QLineEdit()
        self._box.setPlaceholderText("a province, a power, a headline, a treaty…")
        self._box.setObjectName("searchBox")
        self._box.textChanged.connect(self._rerun)
        self._box.returnPressed.connect(self._take_first)
        self.body.addWidget(self._box)
        self.body.addSpacing(SPACE.md)

        self._tally = TextLabel("", style="mono_small", colour="ink_ghost", theme=theme,
                                wrap=True)
        self.body.addWidget(self._tally)
        self.body.addSpacing(SPACE.sm)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setMinimumHeight(460)
        inner = QWidget()
        self._stack = QVBoxLayout(inner)
        self._stack.setContentsMargins(0, 0, 12, 0)
        self._stack.setSpacing(0)
        self._scroll.setWidget(inner)
        self.body.addWidget(self._scroll, 1)

        self._rows: list[_HitRow] = []
        self._hits: list[Hit] = []
        self._conn: sqlite3.Connection | None = None
        self._world_id = ""
        self._viewer: str | None = None
        #: ``{(kind, target)}`` for the rows that are already on the watchlist, so the
        #: control can say *unpin* where that is what clicking it will do.
        self._watched: set[tuple[str, str]] = set()
        #: The one result currently showing what could be ordered about it, if any. One at a
        #: time: the list is long enough that two open at once is a scroll, not an answer.
        self._expanded: tuple[str, str] | None = None
        self._doable_rows: list[_DoableRow] = []

    # -- reading -----------------------------------------------------------

    def load(self, conn: sqlite3.Connection, world_id: str,
             viewer: str | None = None,
             watched: set[tuple[str, str]] | None = None) -> None:
        """Point the box at a world. Keeps the typed query, so reopening resumes."""
        self._conn = conn
        self._world_id = world_id
        self._viewer = viewer
        self._watched = set(watched or ())
        self._rerun(self._box.text())

    def open(self) -> None:
        super().open()
        # The whole point of a search box is that you can start typing at it.
        self._box.setFocus()
        self._box.selectAll()

    def _rerun(self, text: str) -> None:
        clear_layout(self._stack)
        self._rows.clear()
        self._doable_rows.clear()
        self._hits = []
        if self._conn is None:
            return
        if len(text.strip()) < 2:
            self._tally.setText(
                "Two letters or more. One matches most of the world and answers nothing.")
            return

        self._hits = search(self._conn, self._world_id, text, viewer=self._viewer)
        if not self._hits:
            self._tally.setText(
                "Nothing found. This searches what you can actually see — provinces you "
                "know, events you witnessed, agreements you were told about — so a blank "
                "answer may mean it is there and hidden from you.")
            return
        self._tally.setText(
            f"{len(self._hits)} result{'s' if len(self._hits) != 1 else ''}; "
            "Enter takes the first.")
        for hit in self._hits:
            row = _HitRow(hit, watched=(hit.kind, hit.target) in self._watched,
                          theme=self._theme)
            row.chosen.connect(self.chosen)
            row.pinned.connect(self.pinned)
            row.asked.connect(self._toggle_doables)
            self._rows.append(row)
            self._stack.addWidget(row)
            if self._expanded == (hit.kind, hit.target):
                self._add_doables(hit.target)
            self._stack.addSpacing(7)
        self._stack.addStretch(1)
        # Only the rows are new. `Overlay.apply_theme` restyles the whole panel
        # recursively and is for a theme *change*; a data load has no business
        # running it. See the note on `Overlay.apply_theme`.
        for row in (*self._rows, *self._doable_rows):
            row.apply_theme(self._theme)

    def _add_doables(self, target: str) -> None:
        """The acts open against this one, asked of the engine rather than described here."""
        if self._conn is None or not self._viewer:
            return
        found = what_can_i_do_about(self._conn, self._world_id, nation_id=self._viewer,
                                    target=target)
        if not found:
            self._stack.addWidget(TextLabel(
                "      nothing you can order about this one right now.",
                style="mono_small", colour="ink_ghost", theme=self._theme, wrap=True))
            return
        for doable in found:
            row = _DoableRow(doable, theme=self._theme)
            row.taken.connect(self._took)
            self._doable_rows.append(row)
            self._stack.addWidget(row)

    def _toggle_doables(self, kind: str, target: str) -> None:
        self._expanded = None if self._expanded == (kind, target) else (kind, target)
        self._rerun(self._box.text())

    def _took(self, action: object, label: str) -> None:
        self.ordered.emit(action, label)
        self.close_overlay()

    def _take_first(self) -> None:
        if self._hits:
            self.chosen.emit(self._hits[0].kind, self._hits[0].target)

    # -- presentation ------------------------------------------------------

    def apply_theme(self, theme: Theme) -> None:
        super().apply_theme(theme)
        self._box.setStyleSheet(
            f"QLineEdit#searchBox {{ background: {theme.inset}; color: {theme.ink_bright};"
            f" border: 1px solid {theme.edge}; padding: 10px 12px; }}"
            f"QLineEdit#searchBox:focus {{ border: 1px solid {theme.brass}; }}")
        # The scroll area, the tally and the rows are all reached by the recursive
        # pass in `Overlay.apply_theme`. Only the search box needs saying, because
        # a QLineEdit has no `apply_theme` for `restyle` to find.
