"""The orders you have written, as the engine reads them, before the turn runs.

Free text stays the way orders are given -- this is not a form that replaces prose. It is
the step between writing and committing: the parsed orders laid out one card each, with what
each will cost, what it needs, and whether the engine has already decided it cannot be done.

**Green and red have to be readable across the room**, because the whole value is catching
the bad card before the jump. A refused order shows the engine's own reason and, where there
is one, the legal thing to write instead -- the same ``Refusal`` the turn would have handed
back afterwards, obtained from the same predicate, before it costs anything.

**Three states, not two.** *Will attempt* and *refused* are the obvious pair; the third is
the honest one. Most orders cannot be judged in advance -- whether a march arrives or an
investment pays is decided at resolution from the seeded stream -- so those cards say what
they will cost and what they need, and say plainly that no verdict was available. A card
that claimed to know would be inventing, and a player who learned the preview invents things
would stop reading the ones that matter.
"""

from __future__ import annotations

import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from ..engine.workbench import (
    BLOCKED,
    INERT,
    PREVALIDATED,
    REFUSED,
    VALID,
    OrderCard,
    review_orders,
)
from .overlays import Overlay
from .theme import PANEL, SPACE, Theme
from .widgets import SectionLabel, TextLabel, clear_layout, restyle, scroll_qss

__all__ = ["WorkbenchOverlay"]

#: Verdict -> (badge text, theme colour attribute). The colour is the whole point of the
#: screen: a player scanning six cards must find the bad one without reading.
_LOOK: dict[str, tuple[str, str]] = {
    VALID: ("WILL ATTEMPT", "good"),
    REFUSED: ("REFUSED", "danger"),
    BLOCKED: ("CANNOT BE ATTEMPTED", "danger"),
    INERT: ("NOTHING WILL HAPPEN", "ink_ghost"),
}


class _Card(QFrame):
    """One order. The unit of the screen."""

    def __init__(self, card: OrderCard, *, theme: Theme,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._card = card
        self.setObjectName("orderCard")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 13, 16, 15)
        layout.setSpacing(8)
        self._kids: list[TextLabel] = []

        badge, _colour = _LOOK[card.verdict]
        head = QHBoxLayout()
        head.setSpacing(10)
        self._kind = TextLabel(card.action_type, style="label", colour="ink_body",
                               theme=theme)
        self._badge = TextLabel(badge, style="mono_small", theme=theme)
        head.addWidget(self._kind)
        head.addStretch(1)
        head.addWidget(self._badge)
        layout.addLayout(head)
        self._kids.append(self._kind)

        self._summary = TextLabel(card.summary or "(no summary)", style="body",
                                  colour="ink_bright", theme=theme, wrap=True)
        layout.addWidget(self._summary)
        self._kids.append(self._summary)

        line = " · ".join(part for part in (
            card.explain, f"aimed at {card.target}" if card.target else "") if part)
        if line:
            self._explain = TextLabel(line, style="mono_small", colour="ink_dim",
                                      theme=theme, wrap=True)
            layout.addWidget(self._explain)
            self._kids.append(self._explain)

        self._cost = TextLabel(f"cost — {card.cost}", style="mono_small",
                               colour="ink_ghost", theme=theme, wrap=True)
        layout.addWidget(self._cost)
        self._kids.append(self._cost)

        if card.prerequisites:
            row = QHBoxLayout()
            row.setSpacing(7)
            row.addWidget(self._tag("needs", "ink_ghost"))
            for prerequisite in card.prerequisites:
                # The gate that failed is marked, so a card with five requirements says
                # which one is the wall rather than leaving the reader to guess.
                row.addWidget(self._tag(
                    prerequisite.name,
                    "ink_body" if prerequisite.supplied else "danger"))
            row.addStretch(1)
            holder = QWidget()
            holder.setLayout(row)
            layout.addWidget(holder)

        if card.refusal is not None:
            layout.addSpacing(2)
            self._because = TextLabel(f"Why not — {card.refusal.because}", style="body",
                                      colour="danger" if card.blocked else "ink_dim",
                                      theme=theme, wrap=True)
            layout.addWidget(self._because)
            self._kids.append(self._because)
            if card.refusal.instead:
                self._instead = TextLabel(f"Instead — {card.refusal.instead}",
                                          style="body", colour="brass", theme=theme,
                                          wrap=True)
                layout.addWidget(self._instead)
                self._kids.append(self._instead)

        if card.verdict == VALID and not card.prevalidated:
            self._caveat = TextLabel(
                "No verdict was available before the turn: whether this succeeds is "
                "decided when it runs.",
                style="mono_small", colour="ink_ghost", theme=theme, wrap=True)
            layout.addWidget(self._caveat)
            self._kids.append(self._caveat)

    def _tag(self, text: str, colour: str) -> TextLabel:
        label = TextLabel(text, style="mono_small", colour=colour, theme=self._theme)
        self._kids.append(label)
        return label

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        _badge, colour = _LOOK[self._card.verdict]
        edge = getattr(theme, colour, theme.rule)
        self.setStyleSheet(
            f"QFrame#orderCard {{ background: {theme.inset};"
            f" border: 1px solid {theme.rule_soft}; border-left: 3px solid {edge}; }}"
        )
        self._badge.set_colour(colour, theme)
        for child in self._kids:
            child.apply_theme(theme)


class WorkbenchOverlay(Overlay):
    """The orders workbench: every parsed order, checked as far as it can be."""

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(theme, parent)
        self.set_panel_width(PANEL.panel)

        self.body.addWidget(SectionLabel("Orders", theme=theme))
        self.body.addSpacing(SPACE.sm)
        self._headline = TextLabel("", style="display", colour="ink_bright", theme=theme)
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

        self._cards: list[_Card] = []
        self.body.addSpacing(SPACE.md)
        self._footer = TextLabel("", style="mono_small", colour="ink_ghost", theme=theme,
                                 wrap=True)
        self.body.addWidget(self._footer)

    def load(self, conn: sqlite3.Connection, world_id: str, nation_id: str | None,
             actions: list[dict]) -> None:
        """Review the pending orders and lay them out. Reads the world; writes nothing."""
        clear_layout(self._stack)
        self._cards.clear()

        cards = review_orders(conn, world_id, nation_id=nation_id, actions=list(actions))
        if not cards:
            self._headline.setText("Nothing pending")
            self._tally.setText(
                "Write your orders in the right-hand panel and check them; what your "
                "staff makes of them appears here, before the turn runs."
                if nation_id else
                "You are spectating, so there are no orders to review.")
            self._footer.setText("Escape to close")
            return

        blocked = [card for card in cards if card.blocked]
        inert = [card for card in cards if card.verdict == INERT]
        self._headline.setText(
            f"{len(cards)} order{'s' if len(cards) != 1 else ''}")
        parts = [f"{len(cards) - len(blocked) - len(inert)} will be attempted"]
        if blocked:
            parts.append(f"{len(blocked)} cannot be")
        if inert:
            parts.append(f"{len(inert)} will do nothing")
        self._tally.setText(
            " · ".join(parts)
            + (".  Fix the red ones before the turn: they cost a jump and change nothing."
               if blocked else "."))

        for card in cards:
            widget = _Card(card, theme=self._theme)
            self._cards.append(widget)
            self._stack.addWidget(widget)
            self._stack.addSpacing(11)
        self._stack.addStretch(1)

        checked = ", ".join(sorted(PREVALIDATED))
        self._footer.setText(
            f"Verdicts before the turn are available for: {checked}. Every other kind of "
            "order shows what it needs and what it costs, and is settled when it runs."
        )
        self.apply_theme(self._theme)

    def apply_theme(self, theme: Theme) -> None:
        super().apply_theme(theme)
        self._scroll.setStyleSheet(scroll_qss(theme))
        for card in self._cards:
            card.apply_theme(theme)
        for label in (self._headline, self._tally, self._footer):
            label.apply_theme(theme)
        restyle(self.panel, theme)
        for card in self._cards:      # restyle() resets the object-name sheet
            card.apply_theme(theme)
