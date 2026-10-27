"""The things worth acting on, gravest first.

Ranked by the same salience the newspaper uses, so an alert and a headline about the same
event sort together -- a second scale here would mean two screens disagreeing about what
matters, and a player reading both would trust neither.

**Banded rather than listed.** A flat list of eleven things is the construction-spam problem
again: the workshop and the capital sit in the same column and the eye finds whichever is
first. So the panel is sectioned by band, gravest at the top, and the top band is never the
thing that scrolls away.

**Silence is not reassurance.** A category with no live signal is simply absent, and an
empty panel says only that nothing was found -- never that nothing is wrong. The difference
matters: this reads a fixed set of signals, not all of them, and a player who takes an empty
list as "all clear" has been told something the data cannot support.

**The air war is on this list, and only where it is *this player's* air war.** A sky changes
hands somewhere most turns of most wars, and an alert for every air contest is an alert list
nobody reads -- so a theatre reaches it when the player holds the ground, an ally does, an
enemy does, or the player has pinned it. See :func:`atlas.engine.alerts._air_alerts`.
"""

from __future__ import annotations

import sqlite3

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from ..engine.alerts import Alert, alerts_for
from .overlays import Overlay
from .theme import PANEL, SPACE, Theme
from .widgets import (
    PressFeedback,
    SectionLabel,
    TextLabel,
    clear_layout,
    pressed_qss,
)

__all__ = ["AlertsOverlay"]

#: The bands, in the order they are shown, with the heading each gets. These are
#: :data:`atlas.state.visibility.SALIENCE`'s bands, named for what a player does about them.
_BANDS: tuple[tuple[int, str, str], ...] = (
    (3, "Act now", "danger"),
    (2, "Decide this turn", "brass"),
    (1, "Worth knowing", "info"),
    (0, "Routine", "ink_ghost"),
)


class _AlertRow(PressFeedback, QFrame):
    """One alert, and the way through to whatever it is about."""

    region_selected = Signal(str)
    event_selected = Signal(int)

    def __init__(self, alert: Alert, *, colour: str, theme: Theme,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._alert = alert
        self._colour = colour
        self.setObjectName("alertRow")
        if alert.region_id or alert.event_id:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 10, 13, 11)
        layout.setSpacing(3)
        self._kids: list[TextLabel] = []

        head = QHBoxLayout()
        head.setSpacing(10)
        self._what = TextLabel(alert.headline, style="body", colour="ink_bright",
                               theme=theme, wrap=True)
        head.addWidget(self._what, 1)
        # The category, so two alerts with similar sentences are still told apart.
        self._kind = TextLabel(alert.label, style="mono_small", colour="ink_ghost",
                               theme=theme)
        head.addWidget(self._kind)
        layout.addLayout(head)
        self._kids.extend([self._what, self._kind])

        if alert.detail:
            self._why = TextLabel(alert.detail, style="mono_small", colour="ink_dim",
                                  theme=theme, wrap=True)
            layout.addWidget(self._why)
            self._kids.append(self._why)

        hop = ("find it on the map" if alert.region_id
               else "what caused it" if alert.event_id else "")
        if hop:
            self._hop = TextLabel(hop, style="mono_small", colour="brass", theme=theme)
            layout.addWidget(self._hop)
            self._kids.append(self._hop)

    def _fire(self) -> None:
        if self._alert.region_id:
            self.region_selected.emit(self._alert.region_id)
        elif self._alert.event_id:
            self.event_selected.emit(self._alert.event_id)

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        edge = getattr(theme, self._colour, theme.rule)
        self.setStyleSheet(
            f"QFrame#alertRow {{ background: {theme.inset};"
            f" border-left: 3px solid {edge}; }}"
            + pressed_qss("QFrame#alertRow", theme))
        for child in self._kids:
            child.apply_theme(theme)


class AlertsOverlay(Overlay):
    """Everything the player should be looking at, banded and gravest first."""

    region_selected = Signal(str)
    event_selected = Signal(int)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(theme, parent)
        self.set_panel_width(PANEL.panel)

        self.body.addWidget(SectionLabel("What needs you", theme=theme))
        self.body.addSpacing(SPACE.sm)
        self._headline = TextLabel("", style="display", colour="ink_bright", theme=theme,
                                   wrap=True)
        self.body.addWidget(self._headline)
        self._tally = TextLabel("", style="body", colour="ink_dim", theme=theme, wrap=True)
        self.body.addWidget(self._tally)
        self.body.addSpacing(SPACE.md)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setMinimumHeight(560)
        inner = QWidget()
        self._stack = QVBoxLayout(inner)
        self._stack.setContentsMargins(0, 0, 12, 0)
        self._stack.setSpacing(0)
        self._scroll.setWidget(inner)
        self.body.addWidget(self._scroll, 1)

        self._rows: list[_AlertRow] = []
        self._headings: list[TextLabel] = []
        self.body.addSpacing(SPACE.md)
        self._footer = TextLabel("", style="mono_small", colour="ink_ghost", theme=theme,
                                 wrap=True)
        self.body.addWidget(self._footer)

    def load(self, conn: sqlite3.Connection, world_id: str, nation_id: str | None,
             *, turn: int, watching: frozenset[str] = frozenset()) -> None:
        """Read the world and lay the alerts out. Writes nothing.

        ``watching`` is the provinces the player has pinned. It is handed in rather than
        read here because the watchlist lives beside the save rather than inside it -- and
        it is handed in at all because a pin is the player saying a theatre matters, which
        is one of the four things that make an air war on the other side of the map theirs.
        """
        clear_layout(self._stack)
        self._rows.clear()
        self._headings.clear()

        found = alerts_for(conn, world_id, nation_id, turn=turn,
                           watching=watching)
        if nation_id is None:
            self._headline.setText("Nothing to report")
            self._tally.setText("You are spectating, so there is nobody to advise.")
            self._footer.setText("")
            return
        if not found:
            self._headline.setText("Nothing found")
            # **Not "all clear".** This reads seven signals, and a player who takes silence
            # as safety has been told something the data cannot support.
            self._tally.setText(
                "None of the things this watches for is true right now. It watches your "
                "capital, your fronts, your money, your agreements, your colonies, the "
                "sky over ground you care about and what happened to you this turn — not "
                "everything.")
            self._footer.setText("")
            return

        gravest = found[0].salience
        self._headline.setText(f"{len(found)} thing{'s' if len(found) != 1 else ''}")
        self._tally.setText(
            next(name for band, name, _c in _BANDS if band == gravest).lower()
            .join(("The gravest is one to ", "."))
            if gravest else "Nothing urgent; the rest is the tick of the turn.")

        for band, name, colour in _BANDS:
            in_band = [alert for alert in found if alert.salience == band]
            if not in_band:
                continue
            heading = TextLabel(f"{name}  ({len(in_band)})", style="label",
                                colour="ink_faint", theme=self._theme)
            self._headings.append(heading)
            self._stack.addWidget(heading)
            self._stack.addSpacing(8)
            for alert in in_band:
                row = _AlertRow(alert, colour=colour, theme=self._theme)
                row.region_selected.connect(self.region_selected)
                row.event_selected.connect(self.event_selected)
                self._rows.append(row)
                self._stack.addWidget(row)
                self._stack.addSpacing(7)
            self._stack.addSpacing(10)
        self._stack.addStretch(1)
        self._footer.setText(
            "Ranked by the same salience the news uses, so an alert and a headline about "
            "the same thing sort together.")
        # Only the rows are new. `Overlay.apply_theme` restyles the whole panel
        # recursively and is for a theme *change*; a data load has no business
        # running it. See the note on `Overlay.apply_theme`.
        for row in self._rows:
            row.apply_theme(self._theme)

    # No `apply_theme` override: `Overlay.apply_theme` already descends the panel,
    # styling the scroll area on the way and handing every label and row the theme.
    # The override restyled all of them again, then ran the recursive pass a second
    # time, then restyled the rows a third.
