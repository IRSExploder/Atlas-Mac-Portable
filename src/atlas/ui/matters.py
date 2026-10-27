"""The three things waiting on the desk when a game opens.

A new world hands the player a map of two hundred provinces, a rail of a hundred and fifty
events and eighteen panels, and no answer at all to the only question anybody actually has
in the first minute: *what am I supposed to be doing?* The honest answer exists -- it is
whatever has the strongest claim on this government right now -- and until this panel there
was nowhere it was said.

**It is a drawer, and the way back to it is a control with a count on it.** It opens once,
on the first turn, and after that it is *Attention 3* on the top bar -- a named control
rather than a glyph, because the question it answers is asked by somebody who does not yet
know the program well enough to hover anything. A drawer rather than a sheet because this
is the one surface a player is meant to work *from*: the map stays live beside it, so
acting on the first claim does not cost them the other two. See :class:`MattersDrawer` and
`atlas.ui.topbar`.

**It is a prefix, not a fourth opinion.** Every line here comes from
:func:`atlas.engine.attention.attention_for`, cut at three, which is the top of the same
ranked list the alerts panel, the feed and the post-turn report read. That is the whole
reason the number can be three: a shortlist selected separately would be three things chosen
by this file, and a player who opened the alerts panel next would find a different three.
Cutting a shared list at three cannot disagree with cutting it at ten. The count on the bar
is the length of that same list before it is cut, off the same call -- see
:attr:`MattersDrawer.waiting`, and the reason a second call would be a second answer.

**Nothing here ranks, and nothing here decides.** No weight, no threshold and no ordering
lives in this module; it reads ``subject``, ``reason_selected``, ``nav_target`` and
``suggested_order_category`` off items the engine built, and lays them out. A rule about what
matters written on this side of the boundary would be the second copy the substrate exists
to prevent.

**Three, and then out of the way.** The failure this drawer is one bad revision away from
is becoming the wall it was built to replace -- an opening dashboard with a column per
subject that a player closes without reading. So it shows three, it says out loud that three is not
all of them, and every one is one press from the screen that actually answers it. The whole
list is still where it was: *What needs you* has it.

**Silence is not reassurance**, for the reason the alerts panel gives: this reads what the
substrate reads and no more, and an empty panel means nothing was found rather than that
nothing is wrong.

Reading only. Opening this, travelling from it or putting it away writes nothing to the
world.
"""

from __future__ import annotations

import sqlite3

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..engine.attention import AttentionItem, attention_for
from ..engine.capabilities import by_type
from .theme import SPACE, Theme
from .widgets import (
    Chip,
    Divider,
    PressFeedback,
    Rail,
    SectionLabel,
    TextLabel,
    clear_layout,
    pressed_qss,
    restyle,
    scaled,
    scroll_qss,
)

__all__ = ["DRAWER_WIDTH", "SHOWN", "MattersDrawer"]

#: How wide the drawer is when it is out. Between the two rails it sits beside -- 348 for
#: the record and 404 for the turn -- because what is in it is prose with a control under
#: it, read at the same distance as both.
DRAWER_WIDTH = 372

#: How many claims the opening makes. **Three because a player can hold three**, and because
#: the substrate's own note for this surface says three: a prefix of the shared ranking, so
#: what is first here is first in every other panel. Raising it is one number and no other
#: change -- the engine call already takes it -- but a fourth line is a fourth thing to read
#: before the first turn, which is the tax this panel exists to remove.
SHOWN = 3

#: Counting words, so the heading is a sentence rather than a figure. It runs to five
#: because five is the ceiling the substrate's note for this surface names, which is what
#: makes :data:`SHOWN` a number that can be changed on its own.
_WORDS: tuple[str, ...] = ("Nothing", "One", "Two", "Three", "Four", "Five")


def _count_line(found: int) -> str:
    """``3`` becomes ``Three matters``; nothing becomes a sentence rather than a zero."""
    if not found:
        return "Nothing is waiting"
    return f"{_WORDS[found]} matter{'s' if found != 1 else ''}"


#: How to offer the hop, per :data:`atlas.engine.attention.NAV_KINDS`. The verbs are the ones
#: the alerts panel already uses for the same destinations, so a player who has read one
#: recognises the other. A kind with no entry gets no control rather than a mystery one.
_TRAVEL: dict[str, str] = {
    "region": "Find {label} on the map",
    "nation": "Find {label} on the map",
    "event": "See what caused it",
    "treaty": "See what was signed",
}

#: What to offer when the destination could not be named. A province or a power is still
#: somewhere to go without its name in the sentence.
_TRAVEL_UNNAMED = "Find it on the map"


def _travel_text(item: AttentionItem) -> str:
    """The words on the hop, or ``""`` where there is nowhere to go."""
    nav = item.nav_target
    if not nav or nav.kind not in _TRAVEL:
        return ""
    label = nav.label.strip()
    if not label:
        return (_TRAVEL_UNNAMED if nav.kind in ("region", "nation")
                else _TRAVEL[nav.kind])
    return _TRAVEL[nav.kind].format(label=label)


def _order_hint(item: AttentionItem) -> tuple[str, str]:
    """What could be ordered about it, and the ink that says whether it could be *now*.

    The clause is :attr:`atlas.engine.capabilities.Capability.explain` -- the catalogue's own
    words for the act, which the council and the orders box already put in front of a
    player. A second phrasing written here is how two screens come to describe one act
    differently.

    ``actionable_now`` is the engine's verdict and is reported rather than softened: an act
    that is not available this turn is still worth naming, because knowing what *would*
    answer a thing is most of knowing what it is. It simply does not get the brass.
    """
    capability = by_type(item.suggested_order_category)
    if capability is None:
        return "", ""
    if item.actionable_now:
        return f"You could {capability.explain}.", "brass"
    return (f"Answered by orders that {capability.explain} — but not this turn.",
            "ink_ghost")


#: Sources whose way in is the dependency lens rather than the map. **A second control on
#: the row, not a different destination for the first one**: an economic warning is about a
#: partner *and* about a page, and a player who was offered only one of the two would have
#: to guess which. The map hop stays exactly as it is for every other source.
_LENS_SOURCES: tuple[str, ...] = (
    "econ_dependency", "econ_runway", "econ_price", "econ_customer", "econ_arrears")


class _MatterRow(PressFeedback, QFrame):
    """One claim on the player's attention: what it is, why it is here, and the way in."""

    chosen = Signal(str, str)
    #: The economy behind an economic warning, by the nation it is about.
    lens_requested = Signal(str)

    def __init__(self, item: AttentionItem, rank: int, *, theme: Theme,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._item = item
        self._travel = _travel_text(item)
        self.setObjectName("matterRow")
        if self._travel:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 11, 14, 12)
        row.setSpacing(13)
        self._kids: list[QWidget] = []

        # The rank, as a numeral. The order *is* the content of this panel -- three things
        # in no particular order would be a list, and the point is that the first has the
        # strongest claim -- so it is said rather than left to be inferred from position.
        self._rank = TextLabel(str(rank), style="num", colour="brass", theme=theme)
        self._rank.setFixedWidth(14)
        row.addWidget(self._rank, 0, Qt.AlignmentFlag.AlignTop)
        self._kids.append(self._rank)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        self._what = TextLabel(item.subject, style="body", colour="ink_bright",
                               theme=theme, wrap=True)
        column.addWidget(self._what)
        self._kids.append(self._what)

        # **Why it is here, in the engine's own sentence.** Not a restatement of the
        # subject: the substrate refuses to emit an item that cannot say why it made the
        # list, and this is where that promise is kept in front of a player.
        self._why = TextLabel(item.reason_selected, style="prose", colour="ink_dim",
                              theme=theme, wrap=True)
        # The ranking's working ("salience 3 of 3", "1.15 of unrest is left over"): with
        # technical details. The subject and what can be done about it are the row.
        from .plain import technical

        self._why.setVisible(technical())
        column.addWidget(self._why)
        self._kids.append(self._why)

        hint, ink = _order_hint(item)
        if hint:
            self._hint = TextLabel(hint, style="mono_small", colour=ink, theme=theme,
                                   wrap=True)
            column.addWidget(self._hint)
            self._kids.append(self._hint)

        if self._travel:
            controls = QHBoxLayout()
            controls.setContentsMargins(0, 3, 0, 0)
            controls.setSpacing(8)
            self._go = Chip(self._travel, theme=theme, padding=(11, 5))
            self._go.clicked.connect(self._fire)
            controls.addWidget(self._go)
            if item.source in _LENS_SOURCES:
                self._lens = Chip("Open the dependency lens", theme=theme,
                                  padding=(11, 5))
                self._lens.clicked.connect(
                    lambda: self.lens_requested.emit(self._item.nation_id))
                controls.addWidget(self._lens)
                self._kids.append(self._lens)
            controls.addStretch(1)
            column.addLayout(controls)
            self._kids.append(self._go)
        row.addLayout(column, 1)

    def _takes_press(self, event) -> bool:
        return bool(self._travel) and event.button() == Qt.MouseButton.LeftButton

    def _fire(self) -> None:
        nav = self._item.nav_target
        if nav:
            self.chosen.emit(nav.kind, nav.target)

    def apply_theme(self, theme: Theme) -> None:
        self.setStyleSheet(
            f"QFrame#matterRow {{ background: {theme.inset};"
            f" border-left: 3px solid {theme.brass_line}; }}"
            + pressed_qss("QFrame#matterRow", theme))
        for child in self._kids:
            child.apply_theme(theme)


class MattersDrawer(Rail):
    """What is waiting: the top of the shared ranking, cut at three, in a drawer.

    **A drawer rather than a floating sheet.** This was an :class:`~atlas.ui.overlays.Overlay`
    -- a 720px panel centred on a dimmed copy of the whole window -- and that is the wrong
    shape for the one surface a player is meant to *work from*. A sheet has to be dismissed
    before the map it is talking about can be touched, so the first thing a new player did
    with the answer to *what am I supposed to be doing* was close it. As a drawer it opens
    beside the map, the map stays live underneath, and the three claims stay on the glass
    while the player acts on them.

    It is the same drawer the rails are -- :class:`atlas.ui.widgets.Rail`, with the handle
    the fit-to-viewport work put on the record and the turn -- so it is put away the way
    they are put away, and there is no second collapse mechanism in the program.
    """

    #: Somewhere to go, in the vocabulary ``AtlasWindow._go_to`` already speaks -- which is
    #: :data:`atlas.engine.attention.NAV_KINDS`, which is the search box's own kinds. A nav
    #: target this window cannot travel to is a target the substrate cannot build.
    chosen = Signal(str, str)
    #: An economic warning asked for the page behind it, by the nation it names.
    lens_requested = Signal(str)
    #: Put away. The bar's Attention control is the way back in, so it wants to know.
    closed = Signal()

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__("Attention", width=scaled(DRAWER_WIDTH), opens_right=False,
                         theme=theme, parent=parent)
        self._theme = theme
        # The handle is always on offer: this drawer is opened and closed by hand rather
        # than by a breakpoint, so the strip that closes it is not conditional on width.
        self.set_collapsible(True)
        self.collapsed_changed.connect(self._on_collapsed)
        # Closed, and taking no room at all, until it is asked for. A column that is always
        # there is a rail; this is a drawer.
        self.setVisible(False)

        head = QWidget()
        head.setStyleSheet("background: transparent;")
        heading = QVBoxLayout(head)
        heading.setContentsMargins(SPACE.lg, SPACE.md, SPACE.lg, SPACE.md)
        heading.setSpacing(0)
        heading.addWidget(SectionLabel("What is waiting", theme=theme))
        heading.addSpacing(10)
        self._headline = TextLabel("", style="title", colour="ink_bright", theme=theme)
        heading.addWidget(self._headline)
        self._tally = TextLabel("", style="prose", colour="ink_dim", theme=theme, wrap=True)
        heading.addWidget(self._tally)
        self.body.addWidget(head)
        self.body.addWidget(Divider(soft=True, theme=theme))

        # The rows scroll. Three claims, each with a reason and an order clause, is more
        # than a 372px column holds at every text scale, and a drawer that clipped its
        # third row would be making the panel's promise and then breaking it.
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._inner = QWidget()
        self._inner.setStyleSheet("background: transparent;")
        column = QVBoxLayout(self._inner)
        column.setContentsMargins(SPACE.lg, SPACE.md, SPACE.lg, SPACE.md)
        column.setSpacing(0)
        self._stack = QVBoxLayout()
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.setSpacing(0)
        column.addLayout(self._stack)
        column.addSpacing(16)
        self._footer = TextLabel("", style="mono_small", colour="ink_ghost", theme=theme,
                                 wrap=True)
        column.addWidget(self._footer)
        column.addStretch(1)
        self._scroll.setWidget(self._inner)
        self.body.addWidget(self._scroll, 1)

        self._rows: list[_MatterRow] = []
        self._waiting = 0
        self.apply_theme(theme)

    # -- opening and closing -------------------------------------------------

    def open(self) -> None:
        """Bring it out. Idempotent, so the bar may press it twice."""
        self.set_collapsed(False)
        self.setVisible(True)

    def close_drawer(self) -> None:
        """Put it away entirely, rather than leaving a strip of it behind."""
        was_showing = self.isVisible()
        self.setVisible(False)
        # Reset the rail's own flag, so the next `open` is a fresh one rather than a no-op
        # against a collapsed state nothing on screen is showing.
        self.set_collapsed(False)
        if was_showing:
            self.closed.emit()

    def showing(self) -> bool:
        return self.isVisible() and not self.collapsed

    def _on_collapsed(self, collapsed: bool) -> None:
        # The handle is the close control. Collapsing to a 34px strip beside the map is
        # the right answer for a rail that is always there and the wrong one for a drawer.
        if collapsed:
            self.close_drawer()

    # -- data ---------------------------------------------------------------

    @property
    def waiting(self) -> int:
        """How many claims the last :meth:`load` found -- **all of them, not the three
        shown**. It is what the bar's Attention control counts, and it is what decides
        whether a new game opens the drawer at all: an unbidden drawer saying nothing was
        found is worse than none, and the bar still shows the empty state to somebody who
        went looking for it.

        Counting the whole list rather than the prefix is why :meth:`load` asks the
        substrate for everything and cuts here. One call, one ranking: a bar that counted
        off a second call could disagree with the rows underneath it about how many there
        are, which is precisely the disagreement the shared substrate exists to prevent.
        """
        return self._waiting

    def load(self, conn: sqlite3.Connection, world_id: str, viewer: str | None,
             *, turn: int, viewer_name: str = "",
             watching: frozenset[str] = frozenset()) -> None:
        """Read the ranking for this viewer, count it, and lay out the top of it.

        Writes nothing. Scoped by handing the substrate the viewer and nothing else:
        everything it reads is already behind the visibility predicate the dispatch and
        trace panels use, so there is no second route to widen here, and none is opened.
        """
        clear_layout(self._stack)
        self._rows.clear()

        # The whole list, cut here. `attention_for(..., limit=SHOWN)` would give the same
        # three -- a limit is a prefix, which is the substrate's own guarantee -- but it
        # would not say how many there were, and the count on the bar is the point.
        everything = attention_for(conn, world_id, viewer, turn=turn,
                                   watching=watching)
        self._waiting = len(everything)
        found = everything[:SHOWN]
        if not viewer:
            self._headline.setText(_count_line(0))
            self._tally.setText(
                "You are spectating, so there is nobody this is waiting on.")
            self._footer.setText("")
            return
        if not found:
            self._headline.setText(_count_line(0))
            # **Not "all clear".** The same caution the alerts panel carries, for the same
            # reason: this reads what the ranking reads, which is a set of signals and not
            # the world.
            self._tally.setText(
                "Nothing this reads has a claim on you this turn. It watches your fronts, "
                "your agreements, the ground you hold and what has just been said about "
                "you — not everything.")
            self._footer.setText("")
            return

        who = viewer_name or viewer
        many = len(found) > 1
        self._headline.setText(_count_line(len(found)))
        self._tally.setText(
            f"The strongest claim{'s' if many else ''} on {who} right now"
            f"{', gravest first' if many else ''}. Everything else can keep.")

        for rank, item in enumerate(found, start=1):
            row = _MatterRow(item, rank, theme=self._theme)
            row.chosen.connect(self.chosen)
            row.lens_requested.connect(self.lens_requested)
            self._rows.append(row)
            self._stack.addWidget(row)
            self._stack.addSpacing(9)

        # **The footer has to know which of the two things it is saying.** A full three is
        # a shortlist and must say there is more; fewer than three is everything the
        # ranking found, and telling a player there is a rest when there is not is the
        # same lie in the other direction.
        from .plain import technical

        self._footer.setText(
            (f"{_WORDS[SHOWN]} of however many there are, off the same reading every "
             "other panel uses — so what is first here is first there. What needs you "
             "has the rest."
             if len(found) >= SHOWN else
             "All of them: nothing else has a claim on you this turn. Off the same "
             "reading every other panel uses, so what is first here is first there.")
            if technical() else "")
        # Only the rows are new; the whole recursive pass is for a theme *change* and a
        # data load has no business running it. See `ui.alerts`.
        for row in self._rows:
            row.apply_theme(self._theme)

    # -- presentation --------------------------------------------------------

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        # The same edge the record rail carries, on the same side, because it is the same
        # kind of column and the two are next to each other.
        self.setStyleSheet(
            f"MattersDrawer {{ background: {theme.panel};"
            f" border-right: 1px solid {theme.rule}; }}"
        )
        restyle(self, theme)
        self._scroll.setStyleSheet(scroll_qss(theme))
        for surface in (self._scroll.viewport(), self._inner):
            surface.setStyleSheet("background: transparent;")
