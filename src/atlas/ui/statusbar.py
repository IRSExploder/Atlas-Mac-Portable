"""The 30-pixel strip along the bottom: what the app is doing, and where it keeps things.

Everything here is the kind of fact you want available without being told: the seed, the
path the live database is actually at, and what the last turn cost. None of it is
interactive, and none of it ever moves -- a status bar whose fields change position is a
status bar you have to read rather than glance at.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QWidget

from ..humanize import duration
from .theme import Theme
from .widgets import Divider, TextLabel, restyle

__all__ = ["StatusStrip"]


class StatusStrip(QFrame):
    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self.setFixedHeight(30)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 0, 18, 0)
        layout.setSpacing(14)

        self._dot = _StateDot(theme)
        self._state = TextLabel("Ready", style="body_small", colour="ink_dim", theme=theme)
        layout.addWidget(self._dot)
        layout.addWidget(self._state)
        layout.addStretch(1)

        self._last_turn = TextLabel("", style="mono_small", colour="ink_faint", theme=theme)
        layout.addWidget(self._last_turn)
        layout.addWidget(Divider(vertical=True, length=14, theme=theme))
        self._seed = TextLabel("", style="mono_small", colour="ink_ghost", theme=theme)
        layout.addWidget(self._seed)
        layout.addWidget(Divider(vertical=True, length=14, theme=theme))
        self._path = TextLabel("", style="mono_small", colour="ink_ghost", theme=theme)
        self._path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._path)

    # -- data --------------------------------------------------------------

    def set_state(self, text: str, *, busy: bool = False, problem: bool = False,
                  detail: str = "") -> None:
        """What the world is doing, and -- on a hover, if anybody wants it -- what broke.

        ``detail`` is the raw fault as the machinery reported it: *codex exited with code
        1*, a provider name, an exit status. It is genuinely useful and it is genuinely not
        the player's sentence, so it goes one layer back rather than being thrown away. The
        line on the glass says what happened to the world; the tooltip says what happened
        to the program, to whoever thinks to ask.
        """
        self._state.setText(text)
        self._state.setToolTip(detail)
        self._dot.set_state("down" if problem else ("warn" if busy else "ok"))
        self._state.set_colour("danger" if problem else "ink_dim", self._theme)

    def set_world(self, db_path: str, seed: int) -> None:
        self._path.setText(db_path)
        self._path.setToolTip(
            "The live world lives outside your synced folders on purpose. "
            "Use Export for a copy you can keep anywhere."
        )
        self._seed.setText(f"seed {seed}")

    def set_last_turn(self, *, seconds: float, calls: int, rejected: int) -> None:
        """How long the last month took to live through, and what was refused in it.

        ``calls`` is still measured and still passed -- the trace screen and the routing
        log are where it is read -- but it is not shown here, for the same reason
        :mod:`atlas.ui.turning` carries no stopwatch: a count of calls beside a date is a
        measurement of the machinery standing where a fact about the world should be.
        """
        parts = [f"last turn {duration(seconds)}"]
        if rejected:
            parts.append(f"{rejected} refused")
        self._last_turn.setText(" · ".join(parts))
        # The tenth of a second is kept, one layer back. It is the figure somebody
        # profiling a turn wants and the figure nobody playing one does.
        self._last_turn.setToolTip(
            f"The last turn took {seconds:.1f} seconds of real time."
            + (f" {rejected} write(s) were refused." if rejected else ""))
        self._last_turn.set_colour("danger" if rejected else "ink_faint", self._theme)

    def clear_last_turn(self) -> None:
        self._last_turn.setText("no turn resolved yet")

    # -- theme -------------------------------------------------------------

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.setStyleSheet(
            f"StatusStrip {{ background: {theme.panel}; border-top: 1px solid {theme.rule}; }}"
        )
        restyle(self, theme)


class _StateDot(QWidget):
    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._state = "ok"
        self.setFixedSize(6, 6)

    def set_state(self, state: str) -> None:
        self._state = state
        self.update()

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.update()

    def paintEvent(self, _event) -> None:
        from PySide6.QtGui import QColor, QPainter

        colour = {
            "ok": self._theme.good,
            "warn": self._theme.brass,
            "down": self._theme.danger,
        }.get(self._state, self._theme.ink_ghost)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(colour))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(self.rect())
        painter.end()
