"""Run time forward until something happens, and land on the turn it happened.

**A new control, beside the spans rather than instead of them.** Week, Month, Season and
Year still mean exactly what they meant: one turn covering that many days. This is the other
question a player asks -- *"take me to the next thing worth seeing"* -- and it is a different
control because it costs a different amount. A span is one turn and one set of model calls;
this is up to a dozen, and a screen that quietly turned one into the other would be spending
the player's quota on their behalf.

So the cap is on the face of it, not buried: the run stops after that many turns whatever
happens, and says so when it does. Nothing is more corrosive to a "run until" control than
one that might run forever.

On halting it shows what stopped it and the events that tripped it, each a click away from
the causal inspector -- which is the point of landing on the turn rather than past it.
"""

from __future__ import annotations

import sqlite3

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

from ..engine.interrupt import CONDITIONS, DEFAULT_WATCH
from ..humanize import duration
from .overlays import Overlay
from .theme import PANEL, SPACE, Theme
from .widgets import (
    Chip,
    Divider,
    PressFeedback,
    SectionLabel,
    TextLabel,
    clear_layout,
    pressed_qss,
    restyle,
)

__all__ = ["AdvanceOverlay"]

#: The caps worth offering. A year of monthly turns is the long one; beyond that a player
#: is not advancing to the next event, they are simulating a decade and should say so.
_CAPS = (3, 6, 12, 24)

#: What the control opens on.
_DEFAULT_CAP = 12


class _EventLine(PressFeedback, QFrame):
    """One triggering event, and a way into the inspector."""

    clicked = Signal(int)

    def __init__(self, event_id: int, text: str, *, theme: Theme,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._event_id = event_id
        self.setObjectName("triggerLine")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 8)
        layout.setSpacing(10)
        self._id = TextLabel(f"#{event_id}", style="mono_small", colour="ink_ghost",
                             theme=theme)
        self._text = TextLabel(text or "(no text)", style="body", colour="ink_bright",
                               theme=theme, wrap=True)
        self._hint = TextLabel("inspect", style="mono_small", colour="brass", theme=theme)
        layout.addWidget(self._id)
        layout.addWidget(self._text, 1)
        layout.addWidget(self._hint)

    def _fire(self) -> None:
        self.clicked.emit(self._event_id)

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.setStyleSheet(
            f"QFrame#triggerLine {{ background: {theme.inset};"
            f" border-left: 2px solid {theme.brass_line}; }}"
            + pressed_qss("QFrame#triggerLine", theme))
        for child in (self._id, self._text, self._hint):
            child.apply_theme(theme)


class AdvanceOverlay(Overlay):
    """Settings before the run, and what stopped it afterwards."""

    pinnable = False

    #: ``(cap, watch)`` -- the window runs the advance and hands the report back.
    advance_requested = Signal(int, object)
    #: An event the reader wants to look at properly.
    event_selected = Signal(int)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(theme, parent)
        self.set_panel_width(PANEL.panel)

        self._cap = _DEFAULT_CAP
        #: ``(conn, world_id)`` once somebody has pointed this at a world, so
        #: changing the cap can re-answer without the window being involved.
        self._clock: tuple | None = None
        self._watch: set[str] = set(DEFAULT_WATCH)
        self._cap_chips: dict[int, Chip] = {}
        self._watch_chips: dict[str, Chip] = {}
        self._result: list[QWidget] = []

        self.body.addWidget(SectionLabel("Advance to the next event", theme=theme))
        self.body.addSpacing(SPACE.sm)
        self._blurb = TextLabel(
            "Runs one turn at a time and stops on the turn something happens. The spans "
            "beside this still mean one turn each; this one may cost several.",
            style="body", colour="ink_dim", theme=theme, wrap=True)
        self.body.addWidget(self._blurb)
        self.body.addSpacing(SPACE.xs)
        #: **How long this will take, from how long the last few actually took.** Filled by
        #: `set_expectation`; blank until there is something to go on, because a number
        #: invented for a progress bar is still a number the player will believe.
        self._expect = TextLabel("", style="mono_small", colour="ink_ghost", theme=theme,
                                 wrap=True)
        self.body.addWidget(self._expect)
        self.body.addSpacing(SPACE.lg)

        self._cap_title = TextLabel("Stop after at most", style="label",
                                    colour="ink_faint", theme=theme)
        self.body.addWidget(self._cap_title)
        self.body.addSpacing(SPACE.sm)
        caps = QHBoxLayout()
        caps.setSpacing(6)
        for value in _CAPS:
            chip = Chip(f"{value} turns", theme=theme, selected=(value == self._cap))
            chip.clicked.connect(lambda v=value: self._choose_cap(v))
            self._cap_chips[value] = chip
            caps.addWidget(chip)
        caps.addStretch(1)
        holder = QWidget()
        holder.setLayout(caps)
        self.body.addWidget(holder)
        self.body.addSpacing(SPACE.lg)

        self._watch_title = TextLabel("Stop for", style="label", colour="ink_faint",
                                      theme=theme)
        self.body.addWidget(self._watch_title)
        self.body.addSpacing(SPACE.sm)
        grid = QVBoxLayout()
        grid.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(6)
        for index, (condition, sentence) in enumerate(CONDITIONS.items()):
            chip = Chip(sentence, theme=theme, selected=condition in self._watch,
                        wrap=True)
            chip.clicked.connect(lambda c=condition: self._toggle(c))
            self._watch_chips[condition] = chip
            row.addWidget(chip)
            if index % 2 == 1:
                row.addStretch(1)
                wrapper = QWidget()
                wrapper.setLayout(row)
                grid.addWidget(wrapper)
                row = QHBoxLayout()
                row.setSpacing(6)
        if row.count():
            row.addStretch(1)
            wrapper = QWidget()
            wrapper.setLayout(row)
            grid.addWidget(wrapper)
        grid_host = QWidget()
        grid_host.setLayout(grid)
        self.body.addWidget(grid_host)
        self.body.addSpacing(SPACE.sm)
        self._note = TextLabel(
            "A finished work and a refused order are off by default: stopping for either "
            "means the advance rarely gets anywhere.",
            style="mono_small", colour="ink_ghost", theme=theme, wrap=True)
        self.body.addWidget(self._note)
        self.body.addSpacing(SPACE.lg)

        self._go = Chip("Advance", theme=theme, selected=True, padding=(20, 9))
        self._go.clicked.connect(self._run)
        go_row = QHBoxLayout()
        go_row.addWidget(self._go)
        go_row.addStretch(1)
        go_host = QWidget()
        go_host.setLayout(go_row)
        self.body.addWidget(go_host)

        self.body.addSpacing(SPACE.lg)
        self._outcome = QVBoxLayout()
        self._outcome.setSpacing(0)
        outcome_host = QWidget()
        outcome_host.setLayout(self._outcome)
        self.body.addWidget(outcome_host, 1)

        self.body.addSpacing(SPACE.sm)
        self._hint = TextLabel("Escape to close", style="mono_small", colour="ink_ghost",
                               theme=theme)
        self.body.addWidget(self._hint)

    # -- settings -----------------------------------------------------------

    def _choose_cap(self, value: int) -> None:
        self._cap = value
        for cap, chip in self._cap_chips.items():
            chip.set_selected(cap == value)
        self._say_expectation()

    def _toggle(self, condition: str) -> None:
        if condition in self._watch:
            self._watch.discard(condition)
        else:
            self._watch.add(condition)
        self._watch_chips[condition].set_selected(condition in self._watch)

    @property
    def watching(self) -> frozenset[str]:
        return frozenset(self._watch)

    @property
    def cap(self) -> int:
        return self._cap

    def _run(self) -> None:
        self._clear()
        self._add(TextLabel(f"Advancing, up to {self._cap} turns…", style="body",
                            colour="ink_dim", theme=self._theme, wrap=True))
        self.advance_requested.emit(self._cap, frozenset(self._watch))

    # -- the outcome ---------------------------------------------------------

    def _clear(self) -> None:
        clear_layout(self._outcome)
        self._result.clear()

    def _add(self, widget: QWidget) -> None:
        self._result.append(widget)
        self._outcome.addWidget(widget)

    def set_expectation(self, conn: sqlite3.Connection, world_id: str) -> None:
        """Say what a run of the current cap is likely to cost, if this world knows.

        Measured from `routing_log`, which already records how long every provider call
        took and which turn it was made on -- so this is the median of what recent turns
        actually cost in *this* world with *these* models, rather than a model of call
        counts that would be a second thing to keep in step with the router.

        Kept, so choosing a different cap can re-answer without the window being involved.
        """
        self._clock = (conn, world_id)
        self._say_expectation()

    def _say_expectation(self) -> None:
        from ..engine.progress import estimated_advance, observed_turn_seconds

        if self._clock is None:
            return
        conn, world_id = self._clock
        each = observed_turn_seconds(conn, world_id)
        total = estimated_advance(conn, world_id, turns=self._cap)
        # Both halves, because they answer different questions: how long before it *might*
        # stop, and how long if it runs all the way. One figure would hide the other.
        self._expect.setText(
            f"A turn here has been taking about {duration(each)} lately, so a full "
            f"{self._cap} is {total}." if each and total else "")
        self._expect.setVisible(bool(each and total))

    def show_report(self, report, conn: sqlite3.Connection, world_id: str) -> None:
        """Say what happened: where it stopped, why, and what tripped it."""
        self._clear()
        theme = self._theme
        self._add(Divider(theme=theme))
        self._outcome.addSpacing(14)

        date = conn.execute(
            "SELECT world_date FROM world WHERE id = ?", (world_id,)).fetchone()
        when = str(date["world_date"]) if date else ""
        turns = report.turns_run
        plural = "s" if turns != 1 else ""

        if report.aborted:
            self._add(TextLabel(
                f"Stopped after {turns} turn{plural} — the turn could not be completed.",
                style="body", colour="danger", theme=theme, wrap=True))
            self._outcome.addStretch(1)
            self.apply_theme(theme)
            return

        if not report.halted:
            # **Said plainly.** A control that ran its whole budget and found nothing has
            # not failed, and a player who cannot tell that from a halt will not trust it.
            self._add(TextLabel(
                f"Advanced {turns} turn{plural} to {when} — reached the cap with nothing "
                "flagged.", style="body", colour="ink_bright", theme=theme, wrap=True))
            self._outcome.addSpacing(6)
            self._add(TextLabel(
                "Nothing you were watching for happened. Advance again, or widen what "
                "counts as worth stopping for.",
                style="mono_small", colour="ink_ghost", theme=theme, wrap=True))
            self._outcome.addStretch(1)
            self.apply_theme(theme)
            return

        self._add(TextLabel(
            f"Advanced {turns} turn{plural} — stopped at {when}", style="heading",
            colour="ink_bright", theme=theme, wrap=True))
        self._outcome.addSpacing(4)
        self._add(TextLabel(f"because: {report.why()}", style="body", colour="brass",
                            theme=theme, wrap=True))
        self._outcome.addSpacing(12)

        seen: set[int] = set()
        for trigger in report.triggers:
            if trigger.detail:
                self._add(TextLabel(trigger.detail, style="mono_small", colour="ink_dim",
                                    theme=theme, wrap=True))
                self._outcome.addSpacing(6)
            for event_id in trigger.event_ids:
                if not event_id or event_id in seen:
                    continue
                seen.add(event_id)
                row = conn.execute(
                    "SELECT text FROM event WHERE id = ?", (event_id,)).fetchone()
                line = _EventLine(event_id, str(row["text"]) if row else "",
                                  theme=theme)
                line.clicked.connect(self.event_selected)
                self._add(line)
                self._outcome.addSpacing(4)
        self._outcome.addStretch(1)
        self.apply_theme(theme)

    def apply_theme(self, theme: Theme) -> None:
        super().apply_theme(theme)
        for chip in (*self._cap_chips.values(), *self._watch_chips.values(), self._go):
            chip.apply_theme(theme)
        for widget in self._result:
            if hasattr(widget, "apply_theme"):
                widget.apply_theme(theme)
        for label in (self._blurb, self._cap_title, self._watch_title, self._note,
                      self._hint):
            label.apply_theme(theme)
        restyle(self.panel, theme)
        for widget in self._result:
            if isinstance(widget, _EventLine):
                widget.apply_theme(theme)
