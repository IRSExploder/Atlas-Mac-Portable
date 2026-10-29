"""The peace table: tick the provinces you want, set the money, and see whether they sign.

A presentation of :mod:`atlas.engine.peace_table`, which asks the engine's own acceptance
model the question the settlement will ask -- so the answer on the screen is the answer the
turn will give, unless the war moves first. **Add to orders** writes the offer as a sentence
in the orders box, like every other panel; nothing is offered until the player advances.
"""

from __future__ import annotations

import sqlite3

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QSlider, QWidget

from ..engine.peace_table import PeaceTable, sentence, table, verdict
from .overlays import Overlay
from .theme import PANEL, SPACE, Theme
from .widgets import Chip, SectionLabel, TextLabel

__all__ = ["PeaceOverlay"]


class PeaceOverlay(Overlay):
    pinnable = False
    #: The offer, as the sentence the orders box carries.
    order_written = Signal(str)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(theme, parent)
        self.set_panel_width(PANEL.dialog)
        self._conn: sqlite3.Connection | None = None
        self._world_id = ""
        self._table: PeaceTable | None = None
        self._chosen: set[str] = set()
        self._chips: dict[str, Chip] = {}

        self._heading = TextLabel("", style="modal", colour="ink_bright", theme=theme,
                                  wrap=True)
        self.body.addWidget(self._heading)
        self.body.addSpacing(SPACE.xs)
        self._standing = TextLabel("", style="prose", colour="ink_dim", theme=theme,
                                   wrap=True)
        self.body.addWidget(self._standing)
        self.body.addSpacing(SPACE.lg)

        self.body.addWidget(SectionLabel("Provinces you hold", theme=theme))
        self.body.addSpacing(SPACE.xs)
        self._claims_hint = TextLabel(
            "Pick what you want them to give up. Only ground your forces occupy can be "
            "asked for.", style="body_small", colour="ink_faint", theme=theme, wrap=True)
        self.body.addWidget(self._claims_hint)
        self.body.addSpacing(SPACE.sm)
        holder = QWidget()
        holder.setStyleSheet("background: transparent;")
        self._claims = QGridLayout(holder)
        self._claims.setContentsMargins(0, 0, 0, 0)
        self._claims.setHorizontalSpacing(8)
        self._claims.setVerticalSpacing(6)
        self.body.addWidget(holder)
        self.body.addSpacing(SPACE.lg)

        self.body.addWidget(SectionLabel("Money", theme=theme))
        self.body.addSpacing(SPACE.xs)
        money = QHBoxLayout()
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setObjectName("reparations")
        self._slider.valueChanged.connect(self._changed)
        self._money = TextLabel("", style="mono_small", colour="ink", theme=theme)
        self._money.setMinimumWidth(110)
        money.addWidget(self._slider, 1)
        money.addWidget(self._money)
        self.body.addLayout(money)
        self.body.addSpacing(SPACE.lg)

        quick = QHBoxLayout()
        self._suggest = Chip("Best deal they'd sign", theme=theme, padding=(10, 4))
        self._suggest.setToolTip("The most ground the engine thinks they would give up now.")
        self._suggest.clicked.connect(self._use_suggestion)
        self._white = Chip("White peace", theme=theme, padding=(10, 4))
        self._white.setToolTip("Stop the war and ask for nothing.")
        self._white.clicked.connect(self._white_peace)
        quick.addWidget(self._suggest)
        quick.addWidget(self._white)
        quick.addStretch(1)
        self.body.addLayout(quick)
        self.body.addSpacing(SPACE.lg)

        self._answer = TextLabel("", style="row", colour="ink", theme=theme, wrap=True)
        self.body.addWidget(self._answer)
        self.body.addSpacing(SPACE.xs)
        self._meter = _Meter(theme)
        self.body.addWidget(self._meter)
        self.body.addSpacing(SPACE.xs)
        self._because = TextLabel("", style="body_small", colour="ink_dim", theme=theme,
                                  wrap=True)
        self.body.addWidget(self._because)
        self.body.addSpacing(SPACE.xl)

        controls = QHBoxLayout()
        controls.addStretch(1)
        self._back = Chip("Close", theme=theme, padding=(12, 6))
        self._back.clicked.connect(self.close_overlay)
        self._go = Chip("Add to orders", theme=theme, selected=True, padding=(14, 6))
        self._go.setObjectName("offerPeace")
        self._go.clicked.connect(self._offer)
        controls.addWidget(self._back)
        controls.addWidget(self._go)
        self.body.addLayout(controls)
        self.apply_theme(theme)

    # -- data --------------------------------------------------------------

    def load(self, conn: sqlite3.Connection, world_id: str, me: str, enemy: str) -> None:
        self._conn, self._world_id = conn, world_id
        found = self._table = table(conn, world_id, me, enemy)
        self._heading.setText(f"Make peace with {found.enemy_name}")
        if found.position < -0.05:
            how = f"{found.enemy_name} is losing the war"
        elif found.position > 0.05:
            how = f"{found.enemy_name} thinks it is winning the war"
        else:
            how = "Neither side has the upper hand"
        self._standing.setText(
            f"{how}. Their war-weariness {found.their_exhaustion:.0f}/100, "
            f"yours {found.my_exhaustion:.0f}/100.")

        while self._claims.count():
            item = self._claims.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._chips.clear()
        self._chosen = set(found.suggested)
        if not found.claims:
            self._claims.addWidget(TextLabel(
                "You hold none of their ground yet, so there is nothing to ask for but "
                "money or a white peace.", style="body_small", colour="ink_faint",
                theme=self._theme, wrap=True), 0, 0, 1, 3)
        for index, claim in enumerate(found.claims):
            label = claim.name + ("" if claim.core else "  · not their homeland")
            chip = Chip(label, theme=self._theme, selected=claim.region_id in self._chosen,
                        padding=(10, 4))
            chip.setObjectName(f"claim:{claim.region_id}")
            if claim.why_not:
                chip.setEnabled(False)
                chip.setToolTip(claim.why_not)
                self._chosen.discard(claim.region_id)
            else:
                chip.setToolTip(f"Costs them about {claim.cost:.0%} of their homeland.")
                chip.clicked.connect(lambda r=claim.region_id: self._toggle(r))
            self._chips[claim.region_id] = chip
            self._claims.addWidget(chip, index // 3, index % 3, Qt.AlignmentFlag.AlignLeft)

        self._slider.blockSignals(True)
        self._slider.setRange(0, max(0, found.max_reparations))
        self._slider.setValue(0)
        self._slider.setEnabled(found.max_reparations > 0)
        self._slider.blockSignals(False)
        self._changed()

    @property
    def offer(self) -> str:
        found = self._table
        if found is None:
            return ""
        names = [c.name for c in found.claims if c.region_id in self._chosen]
        return sentence(found.enemy_name, names, self._slider.value())

    # -- interaction -------------------------------------------------------

    def _toggle(self, region_id: str) -> None:
        if region_id in self._chosen:
            self._chosen.discard(region_id)
        else:
            self._chosen.add(region_id)
        self._chips[region_id].set_selected(region_id in self._chosen)
        self._changed()

    def _set_choice(self, regions, money: int) -> None:
        self._chosen = set(regions)
        for region_id, chip in self._chips.items():
            chip.set_selected(region_id in self._chosen)
        self._slider.setValue(money)
        self._changed()

    def _use_suggestion(self) -> None:
        if self._table is not None:
            self._set_choice(self._table.suggested, 0)

    def _white_peace(self) -> None:
        self._set_choice((), 0)

    def _changed(self, *_args) -> None:
        found = self._table
        if found is None or self._conn is None:
            return
        money = self._slider.value()
        self._money.setText(f"{money} of {found.max_reparations} max")
        ruling = verdict(self._conn, self._world_id, found.me, found.enemy,
                         sorted(self._chosen), money)
        self._answer.setText(f"{found.enemy_name} will sign." if ruling.signs
                             else f"{found.enemy_name} would refuse.")
        self._answer.set_colour("ink_bright" if ruling.signs else "danger", self._theme)
        self._because.setText(ruling.because)
        self._meter.set_load(ruling.load, ruling.signs)

    def _offer(self) -> None:
        text = self.offer
        if text:
            self.order_written.emit(text)
        self.close_overlay()

    def apply_theme(self, theme: Theme) -> None:
        super().apply_theme(theme)
        self._theme = theme
        self._meter.apply_theme(theme)


class _Meter(QWidget):
    """How much of what they will bear the offer uses. Past the mark, they refuse."""

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._load = 0.0
        self._signs = True
        self.setFixedHeight(8)

    def set_load(self, load: float, signs: bool) -> None:
        self._load, self._signs = load, signs
        self.update()

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        width, height = float(self.width()), float(self.height())
        painter.setBrush(QColor(self._theme.inset))
        painter.drawRoundedRect(QRectF(0, 0, width, height), 4, 4)
        # The whole bar is 1.5x what they will bear; the tick is the line itself.
        share = max(0.0, min(1.0, self._load / 1.5))
        painter.setBrush(QColor(self._theme.brass if self._signs else self._theme.danger))
        painter.drawRoundedRect(QRectF(0, 0, width * share, height), 4, 4)
        painter.setBrush(QColor(self._theme.ink_bright))
        painter.drawRect(QRectF(width / 1.5 - 1, 0, 2, height))
        painter.end()
