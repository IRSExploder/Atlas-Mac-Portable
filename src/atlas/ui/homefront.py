"""The five groups a government has to keep, and what each is currently costing it.

The other screens answer *what is happening out there*. This one answers *who at home will
not have it*, which until the constituencies layer existed was a question the game could not
be asked -- there was one number called stability and nobody had an opinion about anything.

**Consequence first, mood second.** A support figure on its own is a stat block: it tells a
player their industry is at 31 and leaves them to guess what that buys them. So every row
leads with what the group is *doing* -- the revenue it is costing, the month of work it is
withholding, the peace it would not sign, the odds it will remove you -- and the band and the
number sit underneath as the evidence. A row with nothing to say says so, in as many words,
because a blank line reads as a rendering bug.

**Nothing here is computed twice.** Every figure comes from
:func:`atlas.engine.constituencies.home_front`, which asks the same functions the engine
acts on: the drag from the budget's own, the throughput from the yards' own, the stiffening
from the acceptance model, the odds from the succession roll. A panel that worked out a
strike drag its own way would be a second answer to a question the treasury has settled.

Read-only, like every other overlay: it opens a connection it never writes to, and the
screenshot pass asserts the world fingerprint is unmoved by rendering it.
"""

from __future__ import annotations

import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from ..engine.constituencies import BANDS, HomeFront, Standing, home_front
from ..humanize import exact, figure, percent
from .overlays import Overlay
from .theme import PANEL, SPACE, Theme
from .widgets import SectionLabel, TextLabel, clear_layout

__all__ = ["HomeFrontOverlay"]

#: The colour each band is drawn in. Same vocabulary as the alerts panel, so "this is bad"
#: looks the same on both screens.
#:
#: **"uneasy" is the modal band, and it used to be invisible.** It was drawn in
#: ``ink_ghost``, which put the fill at 1.49:1 against the meter track -- so on the one
#: screen whose entire job is *how happy is each faction*, four of the five answers could
#: not be read at a glance. It is the neutral band, not the absent one, so it is drawn in
#: the neutral ink rather than the one reserved for things that barely need to be there.
_BAND_COLOUR: dict[str, str] = {
    "hostile": "danger",
    "restive": "brass",
    "uneasy": "ink_dim",
    "content": "info",
    "devoted": "info",
}

#: How the removal channel reads to somebody who has to do something about it.
_CHANNEL: dict[str, str] = {
    "coup": "This government would be removed by its officer corps",
    "election": "This government can be voted out",
    "revolution": "There is no way to remove this government but to sweep it away",
}

#: Spelled, because "4 of five" is what a template looks like from the outside.
_COUNTED: tuple[str, ...] = ("None", "One", "Two", "Three", "Four", "All five")


class _StandingRow(QFrame):
    """One constituency: what it is doing to you, then who it is and where it stands."""

    def __init__(self, standing: Standing, *, consequences: tuple[str, ...],
                 lately: tuple[str, ...], theme: Theme,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._band = standing.band
        self.setObjectName("standingRow")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 9, 13, 10)
        layout.setSpacing(3)
        self._kids: list[TextLabel] = []

        head = QHBoxLayout()
        head.setSpacing(10)
        self._who = TextLabel(standing.spec.name, style="body", colour="ink_bright",
                              theme=theme)
        head.addWidget(self._who, 1)
        self._where = TextLabel(f"{standing.band}  {figure(standing.support)}/100",
                                style="mono_small", colour=_BAND_COLOUR[standing.band],
                                theme=theme)
        self._where.setToolTip(
            f"{standing.spec.name}: {exact(standing.support)} of 100 — {standing.band}. "
            "The bands are hostile, restive, uneasy, content, devoted.")
        head.addWidget(self._where)
        layout.addLayout(head)
        self._kids.extend([self._who, self._where])

        # **The bar is the band, not the number.** A player should be able to find the row
        # that matters without reading five figures.
        self._meter = _Meter(standing.support, band=standing.band, theme=theme)
        layout.addWidget(self._meter)

        self._wants = TextLabel(f"wants {standing.spec.wants}", style="mono_small",
                                colour="ink_dim", theme=theme, wrap=True)
        layout.addWidget(self._wants)
        self._kids.append(self._wants)

        for line in consequences:
            label = TextLabel(f"→  {line}", style="body", colour="brass", theme=theme,
                              wrap=True)
            layout.addWidget(label)
            self._kids.append(label)
        if not consequences:
            label = TextLabel("→  costing you nothing at the moment", style="mono_small",
                              colour="ink_ghost", theme=theme, wrap=True)
            layout.addWidget(label)
            self._kids.append(label)

        for line in lately:
            label = TextLabel(line, style="mono_small", colour="ink_faint", theme=theme,
                              wrap=True)
            layout.addWidget(label)
            self._kids.append(label)

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        edge = getattr(theme, _BAND_COLOUR[self._band], theme.rule)
        self.setStyleSheet(
            f"QFrame#standingRow {{ background: {theme.inset};"
            f" border-left: 3px solid {edge}; }}")
        for child in self._kids:
            child.apply_theme(theme)
        self._meter.apply_theme(theme)


class _Meter(QFrame):
    """Where one constituency sits on the scale, with the band edges marked."""

    def __init__(self, support: float, *, band: str, theme: Theme,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._support = max(0.0, min(100.0, support))
        self._band = band
        self.setFixedHeight(6)
        self.setObjectName("standingMeter")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self._filled = QFrame()
        self._filled.setObjectName("standingMeterFill")
        self._rest = QFrame()
        self._rest.setObjectName("standingMeterRest")
        row.addWidget(self._filled, max(1, int(round(self._support))))
        row.addWidget(self._rest, max(1, int(round(100 - self._support))))
        self.apply_theme(theme)

    def apply_theme(self, theme: Theme) -> None:
        fill = getattr(theme, _BAND_COLOUR[self._band], theme.rule)
        self._filled.setStyleSheet(
            f"QFrame#standingMeterFill {{ background: {fill}; }}")
        self._rest.setStyleSheet(
            f"QFrame#standingMeterRest {{ background: {theme.rule}; }}")


class HomeFrontOverlay(Overlay):
    """Who the player has to govern, what it is costing, and how they would lose the job."""

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(theme, parent)
        self.set_panel_width(PANEL.panel)

        self.body.addWidget(SectionLabel("Who you have to govern", theme=theme))
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
        # Tall enough that all five fit without scrolling on an ordinary window: the
        # panel sizes to its content hint, and a fifth row half below the fold is a
        # constituency a player does not know they have.
        self._scroll.setMinimumHeight(700)
        inner = QWidget()
        self._stack = QVBoxLayout(inner)
        self._stack.setContentsMargins(0, 0, 12, 0)
        self._stack.setSpacing(0)
        self._scroll.setWidget(inner)
        self.body.addWidget(self._scroll, 1)

        self._rows: list[_StandingRow] = []
        self.body.addSpacing(SPACE.md)
        self._footer = TextLabel("", style="mono_small", colour="ink_ghost", theme=theme,
                                 wrap=True)
        self.body.addWidget(self._footer)

    def load(self, conn: sqlite3.Connection, world_id: str, nation_id: str | None,
             *, turn: int) -> None:
        """Read the world and lay the five out. Writes nothing."""
        clear_layout(self._stack)
        self._rows.clear()

        if nation_id is None:
            self._headline.setText("Nobody to govern")
            self._tally.setText("You are spectating, so there is no home front to keep.")
            self._footer.setText("")
            return

        front = home_front(conn, world_id, nation_id, turn=turn)
        self._fill(front)
        # Only the rows are new. `Overlay.apply_theme` restyles the whole panel
        # recursively and is for a theme *change*; a data load has no business
        # running it. See the note on `Overlay.apply_theme`.
        for row in self._rows:
            row.apply_theme(self._theme)

    def _fill(self, front: HomeFront) -> None:
        unhappy = front.unhappy
        self._headline.setText(
            f"{_COUNTED[len(unhappy)]} of five "
            + ("is" if len(unhappy) == 1 else "are") + " unhappy" if unhappy
            else "All five are behind you")
        # "about a 0% chance" is what rounding looks like from the outside, and a settled
        # country deserves the sentence a settled country would get.
        odds = (f"it stands about a {percent(front.risk, places=0)} chance of falling "
                "this year"
                if front.risk >= 0.005 else "it is in no particular danger")
        self._tally.setToolTip(
            f"The succession roll puts the odds at {exact(front.risk)} over a year.")
        self._tally.setText(
            f"{_CHANNEL.get(front.channel, _CHANNEL['coup'])}, and {odds}. "
            + (f"The worst of them are {front.worst.spec.name}." if unhappy else
               "Nothing at home is costing you anything."))

        for standing in front.standings:
            row = _StandingRow(
                standing,
                consequences=front.consequences.get(standing.kind, ()),
                lately=front.lately.get(standing.kind, ()),
                theme=self._theme)
            self._rows.append(row)
            self._stack.addWidget(row)
            self._stack.addSpacing(6)
        self._stack.addStretch(1)
        self._footer.setText(
            "You cannot set any of these. They follow from what your government has "
            "actually done, and every figure above is the one the world is acting on — "
            "the drag is the one your budget bears, the odds are the ones your succession "
            "runs, and the peace is the one a rival would actually sign. Bands: "
            + ", ".join(name for _edge, name in BANDS) + ".")

    # No `apply_theme` override: the recursive pass in `Overlay.apply_theme` reaches
    # the scroll area, the labels and the rows already.
