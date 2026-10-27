"""Click an event: what caused it, what it changed, where, and who wrote it.

**The detailed log has always been complete and has never been legible.** Every applied
change in the world cites the event that caused it -- rule CORE-001, enforced since schema
1 -- and every event that followed from another records the link. All of it has been
queryable since the first commit and none of it has been on screen, so a player reading
*Italy and Ethiopia sign a peace* had no way to find out that the sentence moved three
provinces, ended two war states and shifted Italy's standing with Britain and France.

This is a reader for that, and only a reader. It runs
:func:`atlas.engine.causality.inspect_event` and lays the answer out; it computes nothing of
its own and writes nothing at all, so opening it cannot change a save.

**Navigable in both directions**, because a causal chain is only useful if you can walk it:
every cause and every consequence is a row you can click, which loads that event and leaves
a trail you can come back along.

**Regions come from the diffs**, never from ``event_participant`` -- that table carries a
region on 3 of 716 events in a real recorded world, and an inspector trusting it would
report that almost nothing happened anywhere. Selecting one centres the map on it.
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

from ..engine.causality import EventInspection, EventRef, inspect_event, recent_events
from ..engine.naming import nation_names
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
)

__all__ = ["InspectorOverlay"]

#: How many events the picker offers. Long enough to reach back a few turns, short enough
#: that the list is a list rather than an archive.
_RECENT = 80

#: How deep the back-trail goes. A causal chain worth walking is rarely longer than this,
#: and an unbounded stack is a memory leak with a nice name.
_TRAIL = 24


def _summary(ref: EventRef, limit: int = 88) -> str:
    text = ref.text.strip() or "(no text)"
    return text if len(text) <= limit else text[: limit - 1] + "…"


class _EventRow(PressFeedback, QFrame):
    """One clickable event. The unit of navigation in this screen."""

    clicked = Signal(int)

    def __init__(self, ref: EventRef, *, theme: Theme, selected: bool = False,
                 names: dict[str, str] | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._ref = ref
        self._selected = selected
        self.setObjectName("eventRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 7, 9, 8)
        layout.setSpacing(2)

        head = QHBoxLayout()
        head.setSpacing(8)
        # The actor as a country, not as a key. `m_blr` is a row in a table; *Belarus*
        # is the power that did it, and this line is the only place a reader is told
        # which of the two hundred and forty-three it was.
        actor = (names or {}).get(ref.actor, ref.actor)
        self._meta = TextLabel(
            f"#{ref.event_id}  turn {ref.turn}  {ref.kind}"
            + (f"  ·  {actor}" if ref.actor else ""),
            style="mono_small", colour="ink_ghost", theme=theme)
        head.addWidget(self._meta)
        head.addStretch(1)
        layout.addLayout(head)

        self._text = TextLabel(_summary(ref), style="body",
                               colour="ink_bright" if selected else "ink_dim",
                               theme=theme, wrap=True)
        layout.addWidget(self._text)

    def _fire(self) -> None:
        self.clicked.emit(self._ref.event_id)

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        background = theme.raised if self._selected else "transparent"
        border = theme.brass_line if self._selected else theme.rule_soft
        self.setStyleSheet(
            f"QFrame#eventRow {{ background: {background};"
            f" border-left: 2px solid {border}; }}"
            + pressed_qss("QFrame#eventRow", theme)
        )
        for child in (self._meta, self._text):
            child.apply_theme(theme)


class _ChangeGroupBlock(QWidget):
    """One heading and the changes under it."""

    def __init__(self, title: str, changes, *, theme: Theme,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        self._kids: list[TextLabel] = []
        head = TextLabel(f"{title}  ({len(changes)})", style="label", colour="ink_body",
                         theme=theme)
        self._kids.append(head)
        layout.addWidget(head)

        for change in changes:
            row = QHBoxLayout()
            row.setSpacing(10)
            target = TextLabel(change.target, style="mono_small", colour="ink_dim",
                               theme=theme)
            target.setFixedWidth(210)
            moved = TextLabel(change.rendered(), style="mono_small",
                              colour="ink_body" if change.status == "applied"
                              else "ink_ghost", theme=theme)
            row.addWidget(target)
            row.addWidget(moved)
            row.addStretch(1)
            if change.status != "applied":
                # A refused write is kept and marked. An event whose changes were all
                # refused is a thing that looked like it happened and did not.
                flag = TextLabel(change.status, style="mono_small", colour="danger",
                                 theme=theme)
                self._kids.append(flag)
                row.addWidget(flag)
            self._kids.extend([target, moved])
            holder = QWidget()
            holder.setLayout(row)
            layout.addWidget(holder)

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        for child in self._kids:
            child.apply_theme(theme)


#: **The one place a write's provenance becomes words.** The same discipline as
#: :data:`atlas.ui.turning.BEATS`: the engine names the role a change came in under, and
#: what a *reader* is told is decided here and nowhere else. A role missing from this table
#: falls back to something true rather than to the key, because the player is not the right
#: person to show an unmapped role name to.
AUTHORS: dict[str, str] = {
    "world_sim": "the world going about its business",
    "tracked_agent": "a government acting for itself",
    "diplomacy": "the traffic between chancelleries",
    "organisation": "a body sitting on it",
    "gambit": "an undertaking that could have gone either way",
    "freeform": "your own ministries",
    "parser": "an order of yours",
    "advisor": "your council",
    "arbiter": "a ruling on a disputed account",
    "engine": "the ordinary course of things",
}

#: A provenance this table has no words for. Deliberately not the raw role name.
_UNATTRIBUTED = "the ordinary course of things"


class InspectorOverlay(Overlay):
    """The causal inspector: an event, its chain, its effects and its author."""

    #: A region the reader asked to see. The window centres the map on it.
    region_selected = Signal(str)
    #: An event the reader wants the *chain* behind rather than the detail of. This screen
    #: shows one hop in each direction, which is the right shape for "what did this do";
    #: the whole way back is a different question and has its own panel. See `ui.trace`.
    trace_requested = Signal(int)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(theme, parent)
        self.set_panel_width(PANEL.screen)

        self._conn: sqlite3.Connection | None = None
        self._world = ""
        #: Nation ids to their names, read once per load. Every row on this screen shows
        #: which power did the thing, and a row that says `m_blr` has not told anybody.
        self._names: dict[str, str] = {}
        self._current: int | None = None
        self._trail: list[int] = []
        self._rows: list[_EventRow] = []
        self._blocks: list[QWidget] = []

        self.body.addWidget(SectionLabel("What happened, and why", theme=theme))
        self.body.addSpacing(SPACE.md)

        columns = QHBoxLayout()
        columns.setSpacing(30)

        # -- left: the picker ------------------------------------------------
        left = QVBoxLayout()
        left.setSpacing(8)
        self._picker_title = TextLabel("Recent events", style="label", colour="ink_faint",
                                       theme=theme)
        left.addWidget(self._picker_title)
        self._picker = QScrollArea()
        self._picker.setWidgetResizable(True)
        self._picker.setFrameShape(QFrame.Shape.NoFrame)
        self._picker.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._picker.setMinimumHeight(620)
        picker_inner = QWidget()
        self._list = QVBoxLayout(picker_inner)
        self._list.setContentsMargins(0, 0, 10, 0)
        self._list.setSpacing(3)
        self._picker.setWidget(picker_inner)
        left.addWidget(self._picker, 1)
        left_host = QWidget()
        left_host.setLayout(left)
        left_host.setFixedWidth(400)
        columns.addWidget(left_host)

        # -- right: the inspection -------------------------------------------
        self._detail = QScrollArea()
        self._detail.setWidgetResizable(True)
        self._detail.setFrameShape(QFrame.Shape.NoFrame)
        self._detail.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._detail.setMinimumHeight(620)
        detail_inner = QWidget()
        self._stack = QVBoxLayout(detail_inner)
        self._stack.setContentsMargins(0, 0, 12, 0)
        self._stack.setSpacing(0)
        self._detail.setWidget(detail_inner)
        columns.addWidget(self._detail, 1)

        self.body.addLayout(columns, 1)
        self.body.addSpacing(SPACE.md)
        self._hint = TextLabel("Escape to close", style="mono_small", colour="ink_ghost",
                               theme=theme)
        self.body.addWidget(self._hint)

    # -- data ---------------------------------------------------------------

    def load(self, conn: sqlite3.Connection, world_id: str,
             event_id: int | None = None) -> None:
        """Read the world's recent events and open on one of them."""
        self._conn = conn
        self._world = world_id
        self._names = nation_names(conn, world_id)
        self._trail = []
        events = recent_events(conn, world_id, limit=_RECENT)
        self._fill_picker(events)
        chosen = event_id if event_id is not None else (
            events[0].event_id if events else None)
        self.show_event(chosen, remember=False)

    def show_event(self, event_id: int | None, *, remember: bool = True) -> None:
        """Render one event. ``remember`` puts the previous one on the back-trail."""
        if self._conn is None:
            return
        if remember and self._current is not None and self._current != event_id:
            self._trail.append(self._current)
            del self._trail[:-_TRAIL]
        self._current = event_id
        clear_layout(self._stack)
        self._blocks.clear()

        if event_id is None:
            self._stack.addWidget(TextLabel(
                "This world has no events yet.", style="body", colour="ink_dim",
                theme=self._theme, wrap=True))
            self._stack.addStretch(1)
            return

        found = inspect_event(self._conn, self._world, event_id)
        if found is None:
            self._stack.addWidget(TextLabel(
                f"There is no event #{event_id} in this world.", style="body",
                colour="ink_dim", theme=self._theme, wrap=True))
            self._stack.addStretch(1)
            return

        self._render(found)
        for row in self._rows:
            row._selected = row._ref.event_id == event_id
        self.apply_theme(self._theme)

    def _fill_picker(self, events: list[EventRef]) -> None:
        clear_layout(self._list)
        self._rows.clear()
        for ref in events:
            row = _EventRow(ref, theme=self._theme, names=self._names)
            row.clicked.connect(self.show_event)
            self._rows.append(row)
            self._list.addWidget(row)
        self._list.addStretch(1)

    # -- rendering -----------------------------------------------------------

    def _add(self, widget: QWidget) -> None:
        self._blocks.append(widget)
        self._stack.addWidget(widget)

    def _chain(self, title: str, refs, empty: str) -> None:
        self._add(SectionLabel(title, theme=self._theme))
        self._stack.addSpacing(7)
        if not refs:
            self._add(TextLabel(empty, style="body", colour="ink_ghost",
                                theme=self._theme, wrap=True))
            self._stack.addSpacing(16)
            return
        for ref in refs:
            row = _EventRow(ref, theme=self._theme, names=self._names)
            row.clicked.connect(self.show_event)
            self._rows.append(row)
            self._add(row)
            self._stack.addSpacing(3)
        self._stack.addSpacing(14)

    def _render(self, found: EventInspection) -> None:
        theme = self._theme
        event = found.event

        actor = self._names.get(event.actor, event.actor)
        self._add(TextLabel(
            f"#{event.event_id}   turn {event.turn}   {found.date}   {event.kind}"
            + (f"   ·   {actor}" if event.actor else ""),
            style="mono_small", colour="ink_ghost", theme=theme))
        self._stack.addSpacing(6)
        self._add(TextLabel(event.text or "(no text)", style="prose", colour="ink_bright",
                            theme=theme, wrap=True))
        self._stack.addSpacing(10)

        # **Chosen and followed told apart.** A change somebody decided on is not the same
        # act as one that fell out of the rules, and a reader who cannot tell them apart
        # cannot tell what was done to them from what merely happened. That distinction is
        # the whole of what this line carries; the machinery that produced either -- the
        # role a write came in under, the model that proposed it -- is not the player's
        # business and used to be printed here verbatim.
        #
        # Composed rather than concatenated, because the obvious version produced a
        # sentence about a write that never happened: an event with no diffs has no batch
        # behind it, so there is nothing to attribute.
        who = ("Somebody chose this" if found.origin.upper() == "MODEL"
               else "This followed from the way the world works")
        if not found.author_role:
            written = f"{who}. It changed no state, so nothing in the record carries it."
        else:
            hand = AUTHORS.get(found.author_role, _UNATTRIBUTED)
            written = f"{who} — the change was entered as {hand}."
        self._add(TextLabel(written, style="mono_small", colour="ink_dim", theme=theme,
                            wrap=True))
        self._stack.addSpacing(18)
        self._add(Divider(theme=theme))
        self._stack.addSpacing(16)

        # **Order is an argument about what a reader wants first.** The change list is the
        # heart of this screen and also the longest thing on it, so putting it directly
        # under the headline pushed *where* and *what followed* off the bottom -- the two
        # answers somebody opens a causal inspector to get. Place, then chain, then detail.
        self._add(SectionLabel("Where", theme=theme))
        self._stack.addSpacing(7)
        if not found.regions:
            self._add(TextLabel("No province was touched.", style="body",
                                colour="ink_ghost", theme=theme, wrap=True))
        else:
            wrap = QHBoxLayout()
            wrap.setSpacing(6)
            for region_id, name in found.regions[:12]:
                chip = Chip(name, theme=theme)
                chip.clicked.connect(
                    lambda rid=region_id: self.region_selected.emit(rid))
                self._blocks.append(chip)
                wrap.addWidget(chip)
            wrap.addStretch(1)
            holder = QWidget()
            holder.setLayout(wrap)
            self._add(holder)
            self._stack.addSpacing(6)
            self._add(TextLabel(
                "Derived from what actually changed, not from the event's own record of "
                "itself. Click one to find it on the map.",
                style="mono_small", colour="ink_ghost", theme=theme, wrap=True))
        self._stack.addSpacing(16)

        self._chain("What caused this", found.causes,
                    "Nothing upstream was recorded. This is where the chain starts.")
        # **One hop is not a chain.** This screen shows the immediate neighbours, which is
        # the right shape for *what did this do*; the whole way back is a different
        # question and has its own panel. Placed under the causes rather than beside the
        # headline, because it is a way of going further along the thing above it. See
        # `ui.trace`.
        trace = Chip("Trace the chain back", theme=theme)
        trace.clicked.connect(
            lambda event_id=event.event_id: self.trace_requested.emit(event_id))
        row = QHBoxLayout()
        row.addWidget(trace)
        row.addStretch(1)
        holder = QWidget()
        holder.setLayout(row)
        self._blocks.append(trace)
        self._add(holder)
        self._stack.addSpacing(16)
        self._chain("What followed from it", found.consequences,
                    "Nothing downstream was recorded. The chain ends here.")

        self._add(Divider(theme=theme))
        self._stack.addSpacing(14)
        self._add(SectionLabel(
            f"What it changed  ({found.change_count})", theme=theme))
        self._stack.addSpacing(7)
        if not found.groups:
            self._add(TextLabel(
                "No state changed. The event is a statement rather than an act -- a "
                "message, a headline, or a proposal nobody accepted.",
                style="body", colour="ink_ghost", theme=theme, wrap=True))
        else:
            for group in found.groups:
                block = _ChangeGroupBlock(group.title, group.changes, theme=theme)
                self._add(block)
                self._stack.addSpacing(12)
        self._stack.addStretch(1)

    # -- presentation --------------------------------------------------------

    # No `apply_theme` override. Both scroll areas, every picker row and every
    # detail block are descendants of the panel, so the recursive pass in
    # `Overlay.apply_theme` styles all of them exactly once.
