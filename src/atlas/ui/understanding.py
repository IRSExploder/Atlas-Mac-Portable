"""Here is what I understood you to mean.

Free-text orders have one failure that nothing else in the interface has: they can be
*misread*. A refused order at least says why -- the parser returns a reason and the rail
prints it. A misread one comes back looking like success. "Move the northern army to
Kharkiv" becomes an attempt from the wrong province, or against the wrong power, or two
brigades instead of the twelve you meant, and the first the player hears of it is a
battle report a month later. The old shape of this made it worse: the parse landed, the
actions went straight into the draft, and a one-line summary appeared in a rail the
player was no longer looking at. Orders that evaporated and orders that were quietly
reinterpreted were the same three words of feedback.

So this reads the order back before it is anybody's plan. The player's own sentence at
the top, then every action the parser produced -- the act, what the catalogue says that
act *is*, and each field it resolved, with ids turned back into the names of real places
and powers. Then what it refused, and why. And two ways out, named as the two acts they
are rather than as two sentences about them: *Issue orders*, or *Revise*.

**Nothing is queued until it is confirmed.** That is the whole mechanism. The draft is
assigned on the confirm path and nowhere else, so cancelling leaves the world, the draft
and the orders box exactly as they were and the player edits a sentence rather than
undoing a plan.

**It does not validate, and it must not start.** Whether an order is legal is the
workbench's question and the turn's; whether it is *what you said* is this one. A panel
that grew its own opinion of the rules would be the second copy the workbench tests exist
to prevent. Every line here comes from the parsed action, and nothing here reads a rule.

Distinct from :class:`~atlas.ui.overlays.ConfirmOverlay`, which asks a different question
at a different moment. That one is the last look before a turn is *spent*, and it fires
only for orders the turn will not give back -- a confirmation on an ordinary act is how
somebody learns to click through confirmations. This one is not a gate on consequence at
all: it is the read-back of an interpretation, and it happens while the order is still a
draft that costs nothing.
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

from ..humanize import exact
from .overlays import Overlay
from .theme import PANEL, SPACE, Theme
from .widgets import (
    Chip,
    Divider,
    SectionLabel,
    TextLabel,
    clear_layout,
    scroll_qss,
)

__all__ = ["ISSUE", "REVISE", "UnderstoodOverlay", "read_back"]

#: The two doors off the read-back, as words rather than as sentences. Named here because
#: they are asserted by name: a test that retyped the label would pass against a button
#: whose text had drifted back to something that does not fit.
ISSUE = "Issue orders"
REVISE = "Revise"

#: Every field the parser may resolve, in the order a person would read them, with the
#: word to put in front of it. Driven by a table rather than by a run of ``if``s so that a
#: field the schema gains is one line here -- and so the read-back can be checked against
#: the schema itself, which is what ``test_an_order_is_read_back_before_it_is_spent`` does.
#:
#: ``summary`` and ``type`` are absent deliberately: they are the headline, not a detail.
FIELDS: tuple[tuple[str, str], ...] = (
    ("target_nation", "Target"),
    ("from_region", "From"),
    ("to_region", "To"),
    ("quantity", "How many"),
    ("unit_kind", "Formation"),
    ("project", "Project"),
    ("gambit_kind", "Undertaking"),
    ("doctrine", "Doctrine"),
    ("ceasefire", "Ceasefire only"),
    ("terms", "Terms offered"),
    ("tone", "Tone"),
)


def _humanise(action_type: str) -> str:
    return str(action_type).replace("_", " ")


def _explain(action_type: str) -> str:
    """What the catalogue says this act is. Read, never restated.

    The one place the panel reaches outside the parsed action, and it reaches for prose
    rather than for a rule: `Capability.explain` is a description of the *act*, which is
    exactly what a player checking an interpretation needs and is not an opinion about
    whether this particular order is legal.
    """
    from ..engine.capabilities import by_type

    capability = by_type(str(action_type))
    return capability.explain if capability else ""


def read_back(conn: sqlite3.Connection, world_id: str,
              actions: list[dict] | tuple[dict, ...]) -> list[dict]:
    """Turn parsed actions into what to print, with ids resolved to names.

    Two queries for the whole batch rather than one per field. A read-back of six actions
    naming twelve places should not be twelve round trips -- that is the shape of N+1 the
    Strategy screen had to be taken apart to remove.
    """
    nations, regions = set(), set()
    for action in actions:
        if action.get("target_nation"):
            nations.add(str(action["target_nation"]))
        for key in ("from_region", "to_region"):
            if action.get(key):
                regions.add(str(action[key]))

    names = _names(conn, world_id, "nation", nations)
    names |= _names(conn, world_id, "region", regions)

    read: list[dict] = []
    for action in actions:
        details = []
        for key, label in FIELDS:
            value = action.get(key)
            if value is None or value == "" or value == [] or value is False:
                continue
            if key == "terms":
                shown = f"{len(value)} clause(s)"
            elif key == "ceasefire":
                shown = "yes"
            elif isinstance(value, float):
                # A whole number loses its `.0`, and one that is not loses the twelve
                # places of binary float behind it. `str(2.4000000000000004)` was reaching
                # the read-back, on the one screen whose whole job is showing a player
                # that their sentence was understood.
                shown = exact(value)
            else:
                shown = names.get(str(value), str(value))
            details.append((label, shown))
        read.append({
            "summary": str(action.get("summary") or "").strip()
                       or _humanise(action.get("type", "")),
            "act": _humanise(action.get("type", "")),
            "explain": _explain(action.get("type", "")),
            "details": details,
        })
    return read


def _names(conn: sqlite3.Connection, world_id: str, table: str,
           ids: set[str]) -> dict[str, str]:
    """``id -> name`` for one table, in one statement. Empty in, nothing asked."""
    if not ids:
        return {}
    holes = ", ".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT id, name FROM {table} WHERE world_id = ? AND id IN ({holes})",  # noqa: S608
        (world_id, *sorted(ids)),
    ).fetchall()
    return {str(row["id"]): str(row["name"]) for row in rows}


class _ActionBlock(QFrame):
    """One action as the parser understood it: the act, what it is, and every field."""

    def __init__(self, read: dict, *, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("understood")
        self._kids: list[QWidget] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(15, 12, 15, 14)
        outer.setSpacing(7)

        head = QHBoxLayout()
        head.setSpacing(12)
        self._add(head, TextLabel(read["act"].upper(), style="label", colour="brass",
                                  theme=theme))
        head.addStretch(1)
        outer.addLayout(head)

        # The parser's own clause, which is the sentence the player is actually checking.
        self._add(outer, TextLabel(read["summary"], style="row", colour="ink_bright",
                                   theme=theme, wrap=True))
        if read["explain"]:
            self._add(outer, TextLabel(read["explain"], style="body_small",
                                       colour="ink_faint", theme=theme, wrap=True))

        if read["details"]:
            grid = QVBoxLayout()
            grid.setSpacing(3)
            for label, value in read["details"]:
                row = QHBoxLayout()
                row.setSpacing(10)
                caption = TextLabel(label, style="label", colour="ink_ghost", theme=theme)
                caption.setFixedWidth(96)
                self._add(row, caption)
                self._add(row, TextLabel(str(value), style="body_small", colour="ink",
                                         theme=theme, wrap=True))
                row.addStretch(1)
                grid.addLayout(row)
            outer.addSpacing(3)
            outer.addLayout(grid)

        self.apply_theme(theme)

    def _add(self, layout, widget: QWidget) -> None:
        self._kids.append(widget)
        layout.addWidget(widget)

    def apply_theme(self, theme: Theme) -> None:
        self.setStyleSheet(
            f"QFrame#understood {{ background: {theme.inset};"
            f" border-left: 1px solid {theme.brass_line}; }}")
        for child in self._kids:
            child.apply_theme(theme)


class UnderstoodOverlay(Overlay):
    """The read-back: what the parser made of the order, before it becomes a draft."""

    pinnable = False

    #: *Issue orders*. The window queues the actions -- and only here.
    confirmed = Signal()
    #: *Revise*. Nothing is queued and the sentence stays in the orders box.
    reworded = Signal()

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(theme, parent)
        self.set_panel_width(PANEL.panel)

        self.body.addWidget(SectionLabel("Before this becomes your plan", theme=theme))
        self.body.addSpacing(SPACE.sm)

        # **The heading gives way, and that is what makes the controls below reachable.**
        # It insisted on all 667 px of itself beside a count that wants 225, which with the
        # spacing and the body's margins is 972 px of content in an 886 px sheet. Every
        # child of the panel was then laid out 86 px too far right and the last of them --
        # the confirm control, which is right-aligned -- was the part that fell off the
        # edge. That is why shortening its label did not free it: the label was never what
        # set the width. A title that elides is what every other line in this interface
        # does, and `TextLabel` puts the whole of it back on the hover.
        head = QHBoxLayout()
        head.setSpacing(16)
        self._heading = TextLabel("This is what your staff understood", style="title",
                                  colour="ink_bright", theme=theme, elide=True)
        head.addWidget(self._heading, 1)
        self._count = TextLabel("", style="num", colour="brass", theme=theme)
        head.addWidget(self._count, 0, Qt.AlignmentFlag.AlignBottom)
        self.body.addLayout(head)
        self.body.addSpacing(SPACE.md)

        # The player's own words, so the comparison is on one screen rather than between
        # this panel and the rail behind it.
        self._wrote = TextLabel("", style="prose_italic", colour="ink_dim", theme=theme,
                                wrap=True)
        self.body.addWidget(self._wrote)
        self.body.addSpacing(SPACE.md)
        self.body.addWidget(Divider(theme=theme))

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setMinimumHeight(430)
        self._inner = QWidget()
        self._stack = QVBoxLayout(self._inner)
        self._stack.setContentsMargins(0, 16, 14, 8)
        self._stack.setSpacing(9)
        self._scroll.setWidget(self._inner)
        self.body.addWidget(self._scroll, 1)

        self.body.addSpacing(SPACE.md)
        self._note = TextLabel(
            "Nothing is queued until you confirm. Cancelling leaves your words in the "
            "orders box to be edited — no turn is spent either way.",
            style="mono_small", colour="ink_ghost", theme=theme, wrap=True)
        self.body.addWidget(self._note)
        self.body.addSpacing(SPACE.md)

        # **Two verbs, because what a control here has to say is which of the two doors it
        # is.** These were sentences -- *Yes, that is what I meant* and *No — let me
        # re-word it* -- and the sentence was the panel's job, which the panel already does
        # twice above. Naming the act is what a button is for.
        #
        # **This is not why the confirm control was clipped**, and it is worth being exact
        # about that or the next long label goes back in believing it is safe: the panel's
        # *content* was wider than its sheet, so the right-aligned control fell off the edge
        # whatever it said. That is fixed above, at the heading. These labels are a decision
        # about wording; the overflow was a defect; the two are tested apart.
        controls = QHBoxLayout()
        controls.addStretch(1)
        self._back = Chip(REVISE, theme=theme, padding=(12, 6))
        self._back.clicked.connect(self._reword)
        self._go = Chip(ISSUE, theme=theme, selected=True, padding=(14, 6))
        self._go.clicked.connect(self._accept)
        controls.addWidget(self._back)
        controls.addWidget(self._go)
        self.body.addLayout(controls)

    # -- data ----------------------------------------------------------------

    def show_reading(self, conn: sqlite3.Connection, world_id: str, *,
                     wrote: str, parsed) -> None:
        """Lay out one parse: what was written, what was understood, what was refused."""
        theme = self._theme
        actions = list(parsed.actions)
        rejected = list(parsed.rejected)
        self._wrote.setText(f"You wrote: “{wrote.strip()}”")
        self._count.setText(
            f"{len(actions)} ACTION(S)"
            + (f"  ·  {len(rejected)} REFUSED" if rejected else ""))

        clear_layout(self._stack)
        if actions:
            self._stack.addWidget(SectionLabel("You will attempt", theme=theme))
            self._stack.addSpacing(4)
            for read in read_back(conn, world_id, actions):
                self._stack.addWidget(_ActionBlock(read, theme=theme))
        else:
            # The gap this panel exists for. An order that produced nothing used to be
            # three words in a rail; here it is the whole screen, next to the sentence
            # that produced it.
            self._stack.addWidget(TextLabel(
                "Nothing in that could be turned into an order. Nothing has been queued.",
                style="row", colour="danger", theme=theme, wrap=True))

        if rejected:
            self._stack.addSpacing(16)
            self._stack.addWidget(SectionLabel("What they could not do", theme=theme))
            self._stack.addSpacing(4)
            for refusal in rejected:
                block = QVBoxLayout()
                block.setSpacing(3)
                block.addWidget(TextLabel(
                    str(refusal.get("order", "")), style="row", colour="ink_bright",
                    theme=theme, wrap=True))
                block.addWidget(TextLabel(
                    str(refusal.get("reason", "")), style="body_small", colour="danger",
                    theme=theme, wrap=True))
                holder = QWidget()
                holder.setStyleSheet("background: transparent;")
                holder.setLayout(block)
                self._stack.addWidget(holder)

        self._stack.addStretch(1)
        # Nothing to attempt means nothing to agree to, so the affirmative is not offered.
        # An *Issue orders* over an empty list is a button that does nothing.
        self._go.setVisible(bool(actions))
        self._back.set_text(REVISE)

    # -- internals -----------------------------------------------------------

    def _accept(self) -> None:
        self.close_overlay()
        self.confirmed.emit()

    def _reword(self) -> None:
        self.close_overlay()
        self.reworded.emit()

    def _maybe_close(self) -> None:
        """Escape and a click on the dim mean the same thing as *Revise*.

        Both leave the panel by the safe door. The base class would just hide the sheet,
        which is the same outcome for the world -- nothing is queued either way -- but
        leaves the rail still saying *reading your orders…* about a parse nobody accepted.
        """
        if self.closable and self.showing():
            self._reword()

    # -- presentation --------------------------------------------------------

    def apply_theme(self, theme: Theme) -> None:
        super().apply_theme(theme)
        self._scroll.setStyleSheet(scroll_qss(theme))
        for surface in (self._scroll.viewport(), self._inner):
            surface.setStyleSheet("background: transparent;")
