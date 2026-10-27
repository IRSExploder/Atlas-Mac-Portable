"""What the player decided to keep an eye on, and a way back to each of it.

Two sections, and the second is what makes the first get used: *watching* is what has been
pinned, and *your objectives* is a short list the player can pin from without going hunting.
A watchlist you can only add to from somewhere else is a watchlist most people never put
anything on.

Every row leads where the search box's rows lead, through the same signal and the same
routing in the window -- so a pinned event opens the inspector for exactly the reason a found
one does, and there is one answer to *what happens when I click this*.

Nothing is written to the world by opening, pinning, unpinning or dismissing this. The pins
themselves go beside the save; see :mod:`atlas.state.workspace`.
"""

from __future__ import annotations

import sqlite3

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from ..engine.watchlist import PIN_LIMIT, Pin, WatchRow, objectives_to_offer, resolve
from .overlays import Overlay
from .theme import PANEL, SPACE, Theme
from .widgets import SectionLabel, TextLabel, clear_layout, restyle, scroll_qss

__all__ = ["WatchlistOverlay"]

_KINDS: dict[str, tuple[str, str]] = {
    "region": ("Province", "brass"),
    "nation": ("Power", "info"),
    "event": ("What happened", "ink_faint"),
    "treaty": ("Agreement", "ink_faint"),
    "objective": ("Objective", "brass"),
}


class _WatchRow(QFrame):
    """One watched thing: what it is, what has become of it, and how to stop."""

    chosen = Signal(str, str)
    toggled = Signal(str, str)

    def __init__(self, row: WatchRow, *, pinned: bool, theme: Theme,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._row = row
        self.setObjectName("watchRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        name, self._colour = _KINDS.get(row.kind, (row.kind, "ink_faint"))
        if row.stale:
            self._colour = "ink_ghost"

        outer = QHBoxLayout(self)
        outer.setContentsMargins(13, 9, 9, 10)
        outer.setSpacing(10)

        stack = QVBoxLayout()
        stack.setSpacing(3)
        self._what = TextLabel(row.label, style="body",
                               colour="ink_dim" if row.stale else "ink_bright",
                               theme=theme, wrap=True)
        stack.addWidget(self._what)
        self._why = TextLabel(f"{name}" + (f" — {row.detail}" if row.detail else ""),
                              style="mono_small",
                              colour="ink_ghost" if row.stale else "ink_dim", theme=theme)
        stack.addWidget(self._why)
        outer.addLayout(stack, 1)

        # The control says what clicking it does now, not what the row is.
        self._pin = TextLabel("unpin" if pinned else "pin", style="mono_small",
                              colour="brass", theme=theme)
        self._pin.setCursor(Qt.CursorShape.PointingHandCursor)
        outer.addWidget(self._pin, 0, Qt.AlignmentFlag.AlignVCenter)

    def mousePressEvent(self, event) -> None:
        # The pin control is a label rather than a button, so the hit test is here: a click
        # over it toggles, and a click anywhere else on the row travels.
        if self._pin.geometry().contains(event.position().toPoint()):
            self.toggled.emit(self._row.kind, self._row.target)
        else:
            self.chosen.emit(self._row.kind, self._row.target)
        super().mousePressEvent(event)

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        edge = getattr(theme, self._colour, theme.rule)
        self.setStyleSheet(
            f"QFrame#watchRow {{ background: {theme.inset};"
            f" border-left: 3px solid {edge}; }}")
        for child in (self._what, self._why, self._pin):
            child.apply_theme(theme)


class WatchlistOverlay(Overlay):
    """The short list of things the player is keeping an eye on."""

    #: ``(kind, target)`` — routed by the window exactly as a search result is.
    chosen = Signal(str, str)
    #: ``(kind, target)`` — pin or unpin, whichever this one is.
    toggled = Signal(str, str)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(theme, parent)
        self.set_panel_width(PANEL.panel)

        self.body.addWidget(SectionLabel("Watching", theme=theme))
        self.body.addSpacing(SPACE.sm)
        self._tally = TextLabel("", style="body", colour="ink_dim", theme=theme, wrap=True)
        self.body.addWidget(self._tally)
        self.body.addSpacing(SPACE.md)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setMinimumHeight(520)
        inner = QWidget()
        self._stack = QVBoxLayout(inner)
        self._stack.setContentsMargins(0, 0, 12, 0)
        self._stack.setSpacing(0)
        self._scroll.setWidget(inner)
        self.body.addWidget(self._scroll, 1)

        self._rows: list[_WatchRow] = []
        self._headings: list[TextLabel] = []
        self.body.addSpacing(SPACE.md)
        self._footer = TextLabel("", style="mono_small", colour="ink_ghost", theme=theme,
                                 wrap=True)
        self.body.addWidget(self._footer)

    def load(self, conn: sqlite3.Connection, world_id: str, viewer: str | None,
             pins: list[Pin]) -> None:
        """Read the world and lay the list out. Writes nothing."""
        clear_layout(self._stack)
        self._rows.clear()
        self._headings.clear()

        watching = resolve(conn, world_id, pins, viewer=viewer)
        offered = [
            row for row in objectives_to_offer(conn, world_id, viewer)
            if Pin("objective", row.target) not in pins
        ]
        lost = sum(1 for row in watching if row.stale)
        self._tally.setText(
            "Nothing pinned yet. Pin anything you find, or an objective below."
            if not watching else
            f"{len(watching)} pinned"
            + (f", {lost} of which the world can no longer answer for" if lost else "")
            + ".")

        if watching:
            self._section("Pinned", watching, pinned=True)
        if offered:
            self._section("Your objectives, not yet pinned", offered, pinned=False)
        self._stack.addStretch(1)
        self._footer.setText(
            f"Up to {PIN_LIMIT} pins; the value of this screen is that it fits in one "
            "glance. A pin keeps an identity, not a snapshot — every line is what is "
            "true now, and a pin the world can no longer answer for says so rather than "
            "quietly disappearing.")
        self.apply_theme(self._theme)

    def _section(self, title: str, rows: list[WatchRow], *, pinned: bool) -> None:
        heading = TextLabel(f"{title}  ({len(rows)})", style="label", colour="ink_faint",
                            theme=self._theme)
        self._headings.append(heading)
        self._stack.addWidget(heading)
        self._stack.addSpacing(8)
        for row in rows:
            widget = _WatchRow(row, pinned=pinned, theme=self._theme)
            widget.chosen.connect(self.chosen)
            widget.toggled.connect(self.toggled)
            self._rows.append(widget)
            self._stack.addWidget(widget)
            self._stack.addSpacing(7)
        self._stack.addSpacing(10)

    def apply_theme(self, theme: Theme) -> None:
        super().apply_theme(theme)
        self._scroll.setStyleSheet(scroll_qss(theme))
        for label in (self._tally, self._footer, *self._headings):
            label.apply_theme(theme)
        for row in self._rows:
            row.apply_theme(theme)
        restyle(self.panel, theme)
