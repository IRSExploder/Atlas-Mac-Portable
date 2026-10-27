"""Where you stand, what your score is made of, and how far each victory still is.

**A view, and only a view.** Every number here is read from
:mod:`atlas.engine.scoring` at the moment the screen opens: the standings, the five
weighted components and the distance to each of the three victory conditions. Nothing is
computed here that the engine does not already compute for itself, and nothing is stored --
open it, read it, close it, and the world is byte-identical.

That constraint is the whole design. A strategy screen that invents a metric is a screen
that tells the player to optimise something the engine never scores, and the first time the
two disagree the interface is lying. So the components come from ``WEIGHTS``, the sentences
from ``CONDITIONS``, and the thresholds from :func:`~atlas.engine.scoring.victory_progress`,
which reads the same constants ``assess_victory`` does.

**Honest about what is not there, too.** A condition a scenario has switched off says so
rather than showing an empty bar, and a component nobody has scored yet -- knowledge, at the
start of every era, because nothing has been researched -- shows its zero rather than being
hidden. A screen that quietly drops the parts with no data teaches the player the game has
fewer moving parts than it does.
"""

from __future__ import annotations

import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..engine.scoring import (
    WEIGHTS,
    ConditionProgress,
    Requirement,
    Standing,
    standings,
    victory_progress,
)
from ..humanize import count, exact, percent, points, ratio, signed
from .overlays import Overlay
from .theme import PANEL, SPACE, Theme
from .widgets import (
    Divider,
    SectionLabel,
    TextLabel,
    clear_layout,
    restyle,
    tint,
)

__all__ = ["StrategyOverlay"]

#: How many rival powers the standings table shows around the player. Enough to see who is
#: above and who is coming up; not so many that a 242-nation world becomes a spreadsheet.
_RIVALS = 6

#: The order components are listed in. Deliberately :data:`WEIGHTS`' own order -- heaviest
#: first -- so the screen reads as "what matters most" rather than alphabetically.
_COMPONENTS = tuple(WEIGHTS)


def _pct(value: float) -> str:
    return percent(value)


def _fmt(value: float, unit: str) -> str:
    """A requirement's number, in the unit the requirement is measured in.

    Three units and three readings. A **share** of the world is a percentage and keeps a
    decimal while it is small, because 2.5% and 3% of the world's ground are several
    countries apart. A **ratio** is a multiple of somebody else's figure and says so with
    an ``x``. A **count** is a whole number of things and never had a decimal to lose.
    """
    if unit == "share":
        return percent(value)
    if unit == "count":
        return signed(value) if value < 0 else count(value)
    return ratio(value)


class _Bar(QWidget):
    """A thin filled rule. The only drawing this screen does."""

    def __init__(self, *, theme: Theme, height: int = 4,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._fill = 0.0
        self._colour = theme.info
        self.setFixedHeight(height)
        self.setMinimumWidth(80)

    def set_fill(self, fraction: float, colour: str) -> None:
        self._fill = max(0.0, min(1.0, fraction))
        self._colour = colour
        self.update()

    def paintEvent(self, _event) -> None:
        from PySide6.QtGui import QColor, QPainter

        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(self._theme.inset))
        if self._fill > 0:
            filled = self.rect()
            filled.setWidth(int(filled.width() * self._fill))
            painter.fillRect(filled, QColor(self._colour))
        painter.end()

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.update()


class _ComponentRow(QWidget):
    """One scoring component: what it is, what you scored, and what it is worth."""

    def __init__(self, name: str, *, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        #: Kept rather than read back off the label: the label is now free to say the
        #: component in the reader's words without the weight lookup following it there.
        self._component = name

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(8)
        self._name = TextLabel(name, style="label", colour="ink_body", theme=theme)
        # The weight is shown, not hidden: a player deciding where to spend a decade
        # should be able to see that ground is worth four times knowledge.
        # `0.34` is a weight in the scoring module's units; `34% of the score` is the same
        # fact in the reader's. The weights sum to 1 by construction, so the percentage is
        # not an analogy -- it is what the number means.
        self._weight = TextLabel(f"{percent(WEIGHTS[name], places=0)} of the score",
                                 style="mono_small", colour="ink_ghost", theme=theme)
        self._weight.setToolTip(f"Weight {exact(WEIGHTS[name])} of 1.")
        self._value = TextLabel("", style="mono_small", colour="ink_bright", theme=theme)
        head.addWidget(self._name)
        head.addWidget(self._weight)
        head.addStretch(1)
        head.addWidget(self._value)
        layout.addLayout(head)

        self._bar = _Bar(theme=theme)
        layout.addWidget(self._bar)

    def set_value(self, scored: float, best: float) -> None:
        name = self._component
        contribution = WEIGHTS[name] * scored
        # Out of a hundred, like the total underneath it: `0.098 contributes 0.033` is two
        # numbers on a scale nobody stated, and the same pair on the score's own scale is
        # "you are managing a tenth of what is there, and it is worth three of your marks".
        self._value.setText(
            f"{points(scored, places=0)} of 100   ·   adds {points(contribution)}")
        self._value.setToolTip(
            f"{name}: scored {exact(scored)} of a possible 1, weighted "
            f"{exact(WEIGHTS[name])}, contributing {exact(contribution)} to a total "
            f"that runs 0 to 1.")
        # **Measured against the best anyone is managing, not against 1.0.** Nobody scores
        # near the ceiling on a real map, so a bar against the ceiling is a row of empty
        # rules that tells the player nothing about whether they are ahead.
        self._bar.set_fill(scored / best if best else 0.0,
                           self._theme.brass if scored >= best and best else self._theme.info)

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        for child in (self._name, self._weight, self._value):
            child.apply_theme(theme)
        self._bar.apply_theme(theme)


class _RequirementRow(QWidget):
    """One clause of a victory condition: have, need, and whether it is met."""

    def __init__(self, requirement: Requirement, *, theme: Theme,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        head = QHBoxLayout()
        head.setSpacing(8)
        self._tick = TextLabel("met" if requirement.met else "", style="mono_small",
                               colour="good" if requirement.met else "ink_ghost",
                               theme=theme)
        self._tick.setFixedWidth(28)
        self._label = TextLabel(requirement.label, style="body", colour="ink_dim",
                                theme=theme)
        self._numbers = TextLabel(
            f"{_fmt(requirement.have, requirement.unit)} of "
            f"{_fmt(requirement.need, requirement.unit)}",
            style="mono_small",
            colour="ink_bright" if requirement.met else "ink_body", theme=theme)
        # A clause a nation is close to losing is a clause whose fourth decimal place is
        # a real fact. It is not a fact for the glass.
        self._numbers.setToolTip(
            f"{requirement.label}: {exact(requirement.have)} against "
            f"{exact(requirement.need)} needed, measured as a {requirement.unit}.")
        head.addWidget(self._tick)
        head.addWidget(self._label)
        head.addStretch(1)
        head.addWidget(self._numbers)
        layout.addLayout(head)

        self._bar = _Bar(theme=theme, height=3)
        self._bar.set_fill(requirement.fraction,
                           theme.good if requirement.met else theme.brass)
        layout.addWidget(self._bar)
        self._met = requirement.met

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        for child in (self._tick, self._label, self._numbers):
            child.apply_theme(theme)
        self._bar.apply_theme(theme)
        self._bar.set_fill(self._bar._fill, theme.good if self._met else theme.brass)


class _ConditionCard(QFrame):
    """One route to victory, with every clause it asks for."""

    def __init__(self, progress: ConditionProgress, *, theme: Theme,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self.setObjectName("conditionCard")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 13)
        layout.setSpacing(9)

        head = QHBoxLayout()
        head.setSpacing(10)
        self._title = TextLabel(progress.condition.upper(), style="label",
                                colour="ink_bright", theme=theme)
        head.addWidget(self._title)
        head.addStretch(1)
        if progress.achieved:
            state, colour = "ACHIEVED", "good"
        elif not progress.applies:
            state, colour = "not in this scenario", "ink_ghost"
        elif progress.holds_now:
            state, colour = (
                f"holds — {count(progress.days_held)} of "
                f"{count(progress.days_needed)} days",
                "brass")
        else:
            state, colour = _pct(progress.fraction), "ink_body"
        self._state = TextLabel(state, style="mono_small", colour=colour, theme=theme)
        head.addWidget(self._state)
        layout.addLayout(head)

        self._blurb = TextLabel(progress.description, style="body", colour="ink_dim",
                                theme=theme, wrap=True)
        layout.addWidget(self._blurb)

        self._rows: list[_RequirementRow] = []
        if not progress.applies:
            # **Said, not hidden.** The sandbox switches domination off because there is no
            # honest threshold on a three-province map, and a screen that silently dropped
            # the card would leave the player wondering where a third of the game went.
            note = TextLabel(
                "This scenario does not offer this victory.",
                style="body", colour="ink_ghost", theme=theme, wrap=True)
            layout.addWidget(note)
            self._rows_host = None
            return

        for requirement in progress.requirements:
            row = _RequirementRow(requirement, theme=theme)
            self._rows.append(row)
            layout.addWidget(row)

        if progress.days_needed > 0:
            self._held = TextLabel(
                f"and must hold unbroken for {count(progress.days_needed)} days of world "
                f"time — {count(progress.days_held)} so far, over "
                f"{count(progress.turns_held)} turn(s)",
                style="mono_small", colour="ink_ghost", theme=theme, wrap=True)
            layout.addWidget(self._held)
        self._rows_host = True

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.setStyleSheet(
            f"QFrame#conditionCard {{ background: {theme.inset};"
            f" border: 1px solid {theme.rule_soft}; }}"
        )
        for row in self._rows:
            row.apply_theme(theme)
        restyle(self, theme)


class StrategyOverlay(Overlay):
    """The strategy screen: standing, score, and the three ways this world can be won."""

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(theme, parent)
        self.set_panel_width(PANEL.screen)
        self.body.setSpacing(0)

        self._heading = SectionLabel("Strategy", theme=theme)
        self.body.addWidget(self._heading)
        self.body.addSpacing(SPACE.sm)

        self._nation = TextLabel("", style="display", colour="ink_bright", theme=theme,
                                   wrap=True)
        self.body.addWidget(self._nation)
        self._rank = TextLabel("", style="body", colour="ink_dim", theme=theme, wrap=True)
        self.body.addWidget(self._rank)
        self.body.addSpacing(SPACE.md)

        columns = QHBoxLayout()
        columns.setSpacing(34)

        # Left: what the score is made of, and who else is in the table. Right: the three
        # ways to win, which is the reason to open this screen and therefore never scrolled.
        self._left = QVBoxLayout()
        self._left.setSpacing(0)
        left_host = QWidget()
        left_host.setLayout(self._left)
        left_host.setFixedWidth(520)
        columns.addWidget(left_host)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Without a floor the scroll area collapses to its layout minimum and the panel
        # shrinks to a strip. The width floor is the same argument sideways: the left
        # column is a fixed 520, so on a panel narrowed to half a window this column has
        # nothing left and the victory cards are cut off at the edge. With a floor, the
        # two columns keep their shape and the panel scrolls instead. See
        # `Overlay._room_for_two`.
        self._scroll.setMinimumHeight(560)
        self._scroll.setMinimumWidth(420)
        self._inner = QWidget()
        self._stack = QVBoxLayout(self._inner)
        self._stack.setContentsMargins(0, 0, 12, 0)
        self._stack.setSpacing(0)
        self._scroll.setWidget(self._inner)
        columns.addWidget(self._scroll, 1)
        self.body.addLayout(columns, 1)

        self._rows: list[_ComponentRow] = []
        self._cards: list[_ConditionCard] = []
        self._dismiss = TextLabel("Escape to close", style="mono_small",
                                  colour="ink_ghost", theme=theme)
        self.body.addSpacing(SPACE.md)
        self.body.addWidget(self._dismiss)
        self.setMinimumHeight(200)

    # -- data ---------------------------------------------------------------

    def load(self, conn: sqlite3.Connection, world_id: str, nation_id: str | None,
             *, turn: int) -> None:
        """Read the world once and rebuild the screen from it."""
        for column in (self._stack, self._left):
            clear_layout(column)
        self._rows.clear()
        self._cards.clear()

        table = standings(conn, world_id)
        if nation_id is None or not table.scores:
            self._nation.setText("No nation to report on")
            self._rank.setText(
                "This world has no player nation, so there is no standing to show."
                if table.scores else "Nothing in this world has been scored yet.")
            return

        self._fill(conn, world_id, nation_id, turn=turn, table=table)
        self.apply_theme(self._theme)

    def _fill(self, conn: sqlite3.Connection, world_id: str, nation_id: str, *,
              turn: int, table: Standing) -> None:
        mine = next((s for s in table.scores if s.nation_id == nation_id), None)
        rank = table.rank_of(nation_id)
        best = table.best

        self._nation.setText(mine.name if mine else nation_id)
        if mine is None:
            self._rank.setText("This nation is not in the standings.")
            return
        leading = table.leading_on(nation_id)
        self._rank.setText(
            f"Ranked {rank} of {count(len(table.scores))} — score "
            f"{points(mine.total)} of 100"
            + (f", strongest at {leading}" if leading else "")
        )
        self._rank.setToolTip(
            f"The score runs 0 to 1 and is stored as {exact(mine.total)}; a hundredth of "
            "it is a mark here. Nobody reaches the ceiling.")

        # -- left column: the components, then the table -------------------
        self._left.addWidget(SectionLabel("Where the score comes from", theme=self._theme))
        self._left.addSpacing(12)
        for name in _COMPONENTS:
            row = _ComponentRow(name, theme=self._theme)
            row.set_value(mine.components[name], best.get(name, 0.0))
            self._rows.append(row)
            self._left.addWidget(row)
            self._left.addSpacing(16)
        self._left.addWidget(TextLabel(
            "Each bar is measured against the best anyone in this world is managing.",
            style="mono_small", colour="ink_ghost", theme=self._theme, wrap=True))
        self._left.addSpacing(20)
        self._left.addWidget(Divider(theme=self._theme))
        self._left.addSpacing(18)

        self._left.addWidget(SectionLabel("The table", theme=self._theme))
        self._left.addSpacing(10)
        for index, score in enumerate(table.scores[:_RIVALS], start=1):
            row = QHBoxLayout()
            row.setSpacing(10)
            you = score.nation_id == nation_id
            row.addWidget(TextLabel(f"{index}", style="mono_small",
                                    colour="ink_bright" if you else "ink_ghost",
                                    theme=self._theme))
            row.addWidget(TextLabel(score.name, style="body",
                                    colour="ink_bright" if you else "ink_dim",
                                    theme=self._theme))
            row.addStretch(1)
            total = TextLabel(points(score.total), style="mono_small",
                               colour="ink_bright" if you else "ink_body",
                               theme=self._theme)
            total.setToolTip(f"{score.name}: {exact(score.total)} of 1")
            row.addWidget(total)
            holder = QWidget()
            holder.setLayout(row)
            self._left.addWidget(holder)
            self._left.addSpacing(5)
        if rank > _RIVALS:
            # A player outside the top few still needs to see themselves against it.
            self._left.addSpacing(4)
            self._left.addWidget(TextLabel(
                f"{rank}.  {mine.name}   {points(mine.total)}",
                style="mono_small", colour="ink_bright", theme=self._theme))
        self._left.addStretch(1)

        # -- right column: the ways to win -----------------------------------
        self._stack.addWidget(SectionLabel("How this world can be won", theme=self._theme))
        self._stack.addSpacing(12)
        for progress in victory_progress(conn, world_id, nation_id, turn=turn, table=table):
            card = _ConditionCard(progress, theme=self._theme)
            self._cards.append(card)
            self._stack.addWidget(card)
            self._stack.addSpacing(12)
        self._stack.addWidget(TextLabel(
            "Meeting a condition is recorded and announced; it does not end the game.",
            style="mono_small", colour="ink_ghost", theme=self._theme, wrap=True))
        self._stack.addStretch(1)

    # -- presentation --------------------------------------------------------

    def apply_theme(self, theme: Theme) -> None:
        super().apply_theme(theme)
        # The scroll area itself, the component rows and the condition cards are all
        # reached by the recursive pass super() just ran. The viewport is not: it is
        # a plain QWidget that `restyle` walks straight through.
        self._scroll.viewport().setStyleSheet(f"background: {tint(theme.panel, 0.0)};")
