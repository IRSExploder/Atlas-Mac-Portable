"""Setting what this government is trying to do, and seeing what follows from it.

Six standing policies, each a sentence a foreign minister could say. Two of them name a
power and are inert until one is chosen — telling a council to contain nobody in particular
is worse than telling it nothing, so a half-written directive is not adopted.

**Nothing here orders anything.** The screen says so, in the words the engine means: a
directive is weighed by the advisor, by the action list and by the alternatives a refusal
offers, and it never withholds a legal act or spends a turn. The player is the government.
"""

from __future__ import annotations

import sqlite3

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..engine.directives import DIRECTIVES, Directive, DirectiveKind
from .overlays import Overlay
from .theme import PANEL, SPACE, Theme
from .widgets import Chip, SectionLabel, TextLabel, restyle

__all__ = ["DirectivesOverlay"]


class _DirectiveRow(QFrame):
    """One policy: whether it is adopted, and the power it is about if it names one."""

    toggled = Signal(str)
    #: ``(kind, nation_id)`` when the power a policy is about is chosen or changed.
    aimed = Signal(str, str)

    def __init__(self, spec: DirectiveKind, *, adopted: bool, subject: str,
                 powers: list[tuple[str, str]], theme: Theme,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._spec = spec
        self._adopted = adopted
        self.setObjectName("directiveRow")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 13)
        layout.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(10)
        self._title = TextLabel(spec.label, style="row", colour="ink_bright", theme=theme)
        head.addWidget(self._title)
        head.addStretch(1)
        self._chip = Chip("adopted" if adopted else "adopt", theme=theme,
                          selected=adopted, padding=(11, 5))
        self._chip.clicked.connect(lambda: self.toggled.emit(spec.id))
        head.addWidget(self._chip)
        layout.addLayout(head)

        self._blurb = TextLabel(spec.blurb, style="prose", colour="ink_dim", theme=theme,
                                wrap=True)
        layout.addWidget(self._blurb)

        self._picker: QComboBox | None = None
        if spec.names_a_power:
            self._picker = QComboBox()
            self._picker.addItem("— choose a power —", "")
            for nation_id, name in powers:
                self._picker.addItem(name, nation_id)
            index = self._picker.findData(subject)
            self._picker.setCurrentIndex(max(0, index))
            self._picker.currentIndexChanged.connect(self._chose)
            layout.addWidget(self._picker)
            if adopted and not subject:
                layout.addWidget(TextLabel(
                    "Not in force until a power is named.",
                    style="mono_small", colour="danger", theme=theme, wrap=True))

        # What it makes of the options, in the catalogue's own words. Shown because a
        # policy whose effect is invisible is one a player cannot reason about.
        weighed = []
        if spec.favours:
            weighed.append("puts first: " + ", ".join(
                kind.replace("_", " ") for kind in spec.favours))
        if spec.against:
            weighed.append("puts last: " + ", ".join(
                kind.replace("_", " ") for kind in spec.against))
        if weighed:
            layout.addWidget(TextLabel(" · ".join(weighed), style="mono_small",
                                       colour="ink_ghost", theme=theme, wrap=True))

    def _chose(self) -> None:
        if self._picker is not None:
            self.aimed.emit(self._spec.id, str(self._picker.currentData() or ""))

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        edge = theme.brass if self._adopted else theme.rule_soft
        self.setStyleSheet(
            f"QFrame#directiveRow {{ background: {theme.inset};"
            f" border-left: 3px solid {edge}; }}")
        restyle(self, theme)


class DirectivesOverlay(Overlay):
    """What this government is trying to do, standing, across turns."""

    #: The policy changed. The window saves it and redraws anything that reads it.
    changed = Signal(object)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(theme, parent)
        self.set_panel_width(PANEL.panel)

        self.body.addWidget(SectionLabel("What this government is trying to do",
                                         theme=theme))
        self.body.addSpacing(SPACE.sm)
        self.body.addWidget(TextLabel(
            "Standing policy, across turns. Your council weighs its advice against it, the "
            "list of what you can do about a power puts what serves it first, and a refused "
            "order suggests alternatives in that order.",
            style="prose", colour="ink_dim", theme=theme, wrap=True))
        self.body.addSpacing(SPACE.xs)
        self.body.addWidget(TextLabel(
            "It never orders anything, never spends a turn, and never hides an option you "
            "are allowed to take. It is yours, kept beside the save rather than in it — a "
            "rewind will not touch it.",
            style="prose", colour="ink_faint", theme=theme, wrap=True))
        self.body.addSpacing(SPACE.lg)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setMinimumHeight(430)
        holder = QWidget()
        self._rows = QVBoxLayout(holder)
        self._rows.setContentsMargins(0, 0, 12, 0)
        self._rows.setSpacing(9)
        self._scroll.setWidget(holder)
        self.body.addWidget(self._scroll, 1)

        self._conn: sqlite3.Connection | None = None
        self._world_id = ""
        self._player = ""
        self._active: list[Directive] = []
        self._widgets: list[_DirectiveRow] = []

    # -- reading -----------------------------------------------------------

    def load(self, conn: sqlite3.Connection, world_id: str, player: str | None) -> None:
        from ..engine.directives import active

        self._conn, self._world_id, self._player = conn, world_id, player or ""
        self._active = active(conn)
        self._rebuild()

    def _powers(self) -> list[tuple[str, str]]:
        if self._conn is None:
            return []
        return [
            (str(row["id"]), str(row["name"]))
            for row in self._conn.execute(
                "SELECT id, name FROM nation WHERE world_id = ? AND is_alive = 1"
                "   AND id <> ? ORDER BY name", (self._world_id, self._player))
        ]

    def _rebuild(self) -> None:
        from .widgets import clear_layout

        clear_layout(self._rows)
        self._widgets.clear()
        adopted = {d.kind: d.subject for d in self._active}
        powers = self._powers()
        for spec in DIRECTIVES:
            row = _DirectiveRow(
                spec, adopted=spec.id in adopted, subject=adopted.get(spec.id, ""),
                powers=powers, theme=self._theme)
            row.toggled.connect(self._toggle)
            row.aimed.connect(self._aim)
            self._widgets.append(row)
            self._rows.addWidget(row)
        self._rows.addStretch(1)
        # **Style the rows, not the panel.** This used to call `apply_theme`, which
        # restyled the entire panel recursively -- three times over, once from
        # `Overlay.apply_theme` and twice more from the loops below it -- on every data
        # load. Nothing about the panel's chrome changed; only these rows are new, and a
        # row styles its own subtree.
        for row in self._widgets:
            row.apply_theme(self._theme)

    # -- writing -----------------------------------------------------------

    def _toggle(self, kind: str) -> None:
        held = {d.kind: d.subject for d in self._active}
        if kind in held:
            del held[kind]
        else:
            held[kind] = ""
        self._active = [Directive(k, s) for k, s in held.items()]
        self._save()

    def _aim(self, kind: str, subject: str) -> None:
        """Naming a power adopts the policy: choosing one *is* the decision."""
        held = {d.kind: d.subject for d in self._active}
        if subject:
            held[kind] = subject
        elif kind in held:
            held[kind] = ""
        self._active = [Directive(k, s) for k, s in held.items()]
        self._save()

    def _save(self) -> None:
        from ..engine.directives import set_active

        if self._conn is not None:
            set_active(self._conn, self._active)
        self._rebuild()
        self.changed.emit(list(self._active))

    # -- presentation ------------------------------------------------------

    # No `apply_theme` override. `Overlay.apply_theme` calls `restyle(self.panel)`,
    # which descends through the scroll area -- styling it on the way -- and hands each
    # row the theme. The override here re-did all three of those things and then ran the
    # whole recursive pass a second time.
