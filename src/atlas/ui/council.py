"""Ask your own government, in your own words.

The engine has had a council since the advisor role was wired: a model briefed from the
*player's* view, given no schema and no writable column, which answers in prose and writes
nothing. Until now the only way to reach it was a button in the diplomacy rail that
borrowed the negotiation surface -- so a private word with your ministers looked exactly
like a conversation with a foreign power, in a column two hundred pixels wide, and it shut
the moment you opened a chat with anybody else.

This is that same call with a screen of its own. The player types the question they
actually have -- *what should I worry about?*, *why can't I attack Poland?*, *who is my
biggest threat?* -- and reads an answer set as prose at a reading measure. Nothing here is
a new mechanic. It is the shortest statement of what the game is for: **you ask, instead of
learning which menu the answer is filed under.**

**It cannot change the world, and that is wiring rather than a promise.** Four things hold
it:

* the role's write allowlist is empty (:mod:`atlas.state.permissions`);
* :class:`~atlas.engine.advisor.AdvisorSession` has no writer, no ``close()`` and no
  extraction step, so there is no code path from a reply to a diff;
* the call is made with no schema, so there is no *shape* in which a diff could arrive;
* and this panel builds its session against :func:`advice_only`, which refuses to make a
  council call that carries one.

The fourth is the new one, and it is here because the other three are properties of code
somewhere else. A future change that attaches a schema to the advisor -- the only way a
structured diff could come back through this door -- fails loudly at the panel that offers
the conversation, rather than quietly becoming a write path with a chat window in front of
it.

The reply is model-written text about foreign powers, so it lands in a plain-text label
like every other utterance in the program and can never carry markup.
"""

from __future__ import annotations

import sqlite3

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetrics, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..providers.base import ModelProvider, ProviderRequest, ProviderResult
from ..state.models import Role
from .grounding import a_rival
from .overlays import Overlay
from .preferences import key_for
from .theme import PANEL, SPACE, TYPE, Theme
from .widgets import (
    Chip,
    Divider,
    SectionLabel,
    TextLabel,
    clear_layout,
    scroll_qss,
    styled_font,
)

__all__ = ["OPENERS", "CouncilOverlay", "advice_only"]

#: The questions a person actually arrives with, in the words they would use. Openers,
#: not a menu: each is sent verbatim as the player's own question, and the council answers
#: it from the same view it answers a typed one from.
#:
#: They are here because an empty box with a cursor in it is the hardest interface in the
#: program to start using, and because what this panel may be asked is not discoverable by
#: looking at it. Every one of them is a question the advisor view has the material for.
#: **The air one is here because the council is where the air rules now live.** The
#: advisor's briefing carries them with the engine's own numbers -- see
#: :func:`atlas.engine.views._air_rules_lines` -- and a set of rules nobody knows they can
#: ask about is a set of rules nobody has been told. It is an opener rather than a
#: reference page for the same reason the other four are: what this panel may be asked is
#: not discoverable by looking at it.
OPENERS: tuple[str, ...] = (
    "What should I worry about?",
    "Who is my biggest threat?",
    "What can I actually do this turn?",
    "How does the air war work?",
    "What would you do next, and why?",
)


class _AdviceOnly:
    """A provider wrapper that will not carry a schema on a council call.

    The advisor is the one model-facing role with no write path, and the property that
    makes that true at the provider boundary is the absence of a schema: with no shape to
    return, there is nothing a reply could be parsed into and applied. That is asserted
    against the engine in ``tests/test_advisor.py``. This asserts it again at the point the
    *panel* makes the call, so the guarantee travels with the surface that offers the
    conversation and not only with the module that happens to implement it today.

    Everything else passes straight through. It is a proxy, not a policy.
    """

    __slots__ = ("_inner",)

    def __init__(self, inner: ModelProvider) -> None:
        self._inner = inner

    @property
    def name(self):
        return self._inner.name

    def available(self) -> bool:
        return self._inner.available()

    async def invoke(self, request: ProviderRequest) -> ProviderResult:
        if request.role is Role.ADVISOR and request.schema is not None:
            raise RuntimeError(
                "the council is advisory only: a council call may not carry a schema, "
                "because a schema is the shape in which a diff would arrive"
            )
        return await self._inner.invoke(request)


def advice_only(providers: dict[str, ModelProvider]) -> dict[str, ModelProvider]:
    """The same providers, unable to be asked for a structured answer as the council."""
    return {key: _AdviceOnly(provider) for key, provider in providers.items()}


class _Exchange(QFrame):
    """One turn of the conversation, set as an utterance rather than as interface copy."""

    def __init__(self, speaker: str, text: str, *, is_player: bool, theme: Theme,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.is_player = is_player
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 12, 15, 13)
        layout.setSpacing(7)
        self._speaker = TextLabel(speaker, style="label", theme=theme)
        # Untrusted text: model-written prose about other governments, in a label fixed to
        # plain text, so a reply containing markup renders as the characters it is.
        self._text = TextLabel(text, style="prose", colour="ink_body", theme=theme,
                               wrap=True)
        layout.addWidget(self._speaker)
        layout.addWidget(self._text)
        self.apply_theme(theme)

    def apply_theme(self, theme: Theme) -> None:
        if self.is_player:
            border, background, ink = theme.rule, theme.inset, "ink_faint"
        else:
            border, background, ink = theme.brass_line, theme.brass_wash, "brass"
        self.setStyleSheet(
            f"_Exchange {{ border: 1px solid {border}; background: {background}; }}")
        self._speaker.set_colour(ink, theme)
        self._text.apply_theme(theme)


class _AskBox(QWidget):
    """The box the question is typed into. Submits on the *send* binding, never on a timer.

    A debounce here would spend a real model call on every pause in typing, which is the
    same reason the orders box refuses one.
    """

    submitted = Signal(str)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._edit = QPlainTextEdit()
        self._edit.setPlaceholderText(
            f"Ask your ministers anything. {key_for('send')} sends."
        )
        self._edit.setFixedHeight(66)
        self._edit.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(self._edit)

        # **Scoped to this box.** There is more than one composer on the window -- this
        # one, the orders box, and the chat in the right rail -- and a window-context
        # shortcut from each means Qt sees several claimants for the same keystroke and
        # dispatches to none of them. The placeholder in this box promises the key sends
        # it, so the key sends *this* box: the shortcut fires only while the caret is in
        # it, which is where a person pressing send has just been typing.
        self._shortcut = QShortcut(QKeySequence(key_for("send")), self._edit)
        self._shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._shortcut.activated.connect(self._submit)
        self.apply_theme(theme)

    def _submit(self) -> None:
        text = self._edit.toPlainText().strip()
        if text:
            self._edit.clear()
            self.submitted.emit(text)

    def focus(self) -> None:
        self._edit.setFocus()

    def apply_theme(self, theme: Theme) -> None:
        self._edit.setFont(styled_font(TYPE["body"]))
        self._edit.setStyleSheet(
            f"QPlainTextEdit {{ background: {theme.inset}; color: {theme.ink};"
            f" border: 1px solid {theme.edge}; padding: 9px 11px;"
            f" selection-background-color: {theme.brass_line}; }}"
            f"QPlainTextEdit:focus {{ border-color: {theme.brass_line}; }}"
            + scroll_qss(theme)
        )


class CouncilOverlay(Overlay):
    """Ask your staff: a plain-English conversation with your own government."""

    #: A question the player has put. The window makes the call -- this panel does no
    #: async work of its own, exactly as every other panel does none.
    asked = Signal(str)
    #: The player wants a clean sheet. The window drops the session behind it.
    cleared = Signal()

    #: The line the panel is waiting on, so it can be taken off again when the answer
    #: lands under it. One string, in one place, rather than a suffix match on the label.
    THINKING = "your council is considering…"

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(theme, parent)
        self.set_panel_width(PANEL.panel)

        self._conn: sqlite3.Connection | None = None
        self._world = ""
        self._viewer: str | None = None
        self._waiting = False

        self.body.addWidget(SectionLabel("Ask your staff", theme=theme))
        self.body.addSpacing(SPACE.sm)

        head = QHBoxLayout()
        head.setSpacing(16)
        self._heading = TextLabel("Your council", style="title", colour="ink_bright",
                                  theme=theme)
        head.addWidget(self._heading)
        head.addStretch(1)
        self._standing = TextLabel("", style="num", colour="brass", theme=theme)
        head.addWidget(self._standing, 0, Qt.AlignmentFlag.AlignBottom)
        self._fresh = Chip("Start again", theme=theme, padding=(11, 5))
        self._fresh.setToolTip("Forget this conversation and begin a new one")
        self._fresh.clicked.connect(self._start_again)
        head.addWidget(self._fresh, 0, Qt.AlignmentFlag.AlignBottom)
        self.body.addLayout(head)
        self.body.addSpacing(SPACE.sm)
        self.body.addWidget(Divider(theme=theme))

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # **A floor, not a demand.** This was 430, which is a good height for the
        # conversation and more than a 1280x720 window can spare: heading, transcript,
        # openers and ask box came to more than the sheet had, and the box you type the
        # question into ended up at y=740 on a 720px window -- the one control this panel
        # exists for, below the bottom edge, on the smallest window the shell allows.
        # The transcript still takes every pixel going, because it is added with a
        # stretch factor; this only says what it may be squeezed to when the window is
        # small. Nothing changes above about 800px of window.
        self._scroll.setMinimumHeight(180)
        self._inner = QWidget()
        self._stack = QVBoxLayout(self._inner)
        self._stack.setContentsMargins(0, 16, 14, 8)
        self._stack.setSpacing(11)
        self._stack.addStretch(1)
        self._scroll.setWidget(self._inner)
        self.body.addWidget(self._scroll, 1)

        self.body.addSpacing(SPACE.md)
        self.body.addWidget(TextLabel("You might ask", style="label",
                                      colour="ink_ghost", theme=theme))
        self.body.addSpacing(SPACE.sm)
        # Rows, not one row. Five questions set as sentences do not fit across nine
        # hundred pixels, and a chip that elides is a suggested question the player
        # cannot read -- *WHAT SHOULD I WOR* suggests nothing.
        self._openers_rows = QVBoxLayout()
        self._openers_rows.setContentsMargins(0, 0, 0, 0)
        self._openers_rows.setSpacing(6)
        self._openers = QWidget()
        self._openers.setLayout(self._openers_rows)
        self.body.addWidget(self._openers)
        self.body.addSpacing(SPACE.md)

        self._ask = _AskBox(theme)
        self._ask.submitted.connect(self._put)
        self.body.addWidget(self._ask)
        self.body.addSpacing(SPACE.sm)

        self._hint = TextLabel(
            "Advice only. Your council writes nothing and changes nothing — it is briefed "
            "on what your government can see, not on what your rivals know. Only orders "
            "you issue move the world.",
            style="mono_small", colour="ink_ghost", theme=theme, wrap=True)
        self.body.addWidget(self._hint)

    # -- data ---------------------------------------------------------------

    def load(self, conn: sqlite3.Connection, world_id: str, viewer: str | None,
             *, viewer_name: str = "") -> None:
        """Point the panel at a government. Keeps whatever has already been said.

        A conversation is the memory here -- the council itself remembers nothing between
        calls -- so re-opening the panel mid-thread has to find the thread where it was
        left. It is emptied by *Start again*, by a rewind, or by playing somebody else.
        """
        changed = (conn is not self._conn or world_id != self._world
                   or viewer != self._viewer)
        self._conn, self._world, self._viewer = conn, world_id, viewer
        self._heading.setText(
            f"The council of {viewer_name}" if viewer_name else "Your council")
        if changed:
            self.reset()
        self._build_openers()

    def reset(self) -> None:
        """Empty the sheet. The session behind it is discarded by the window."""
        self._waiting = False
        clear_layout(self._stack)
        self._stack.addStretch(1)
        self._standing.setText("")
        self._insert(TextLabel(
            self._opening_note(), style="prose_italic", colour="ink_faint",
            theme=self._theme, wrap=True))

    def open(self) -> None:
        super().open()
        self._ask.focus()

    # -- the conversation ----------------------------------------------------

    def put_question(self, question: str) -> None:
        """Show a question as asked, and mark the council as thinking about it."""
        self._waiting = True
        self._insert(_Exchange("You", question, is_player=True, theme=self._theme))
        self._insert(TextLabel(self.THINKING, style="prose_italic", colour="ink_faint",
                               theme=self._theme, wrap=True))
        self._standing.setText("CONSULTING")

    def show_answer(self, speaker: str, text: str) -> None:
        """The council's reply, set as prose. Nothing is done with it but read it."""
        self._waiting = False
        self._drop_thinking()
        self._insert(_Exchange(speaker, text, is_player=False, theme=self._theme))
        self._standing.setText(f"{self._answers()} ANSWERED")

    def show_problem(self, message: str, *, detail: str = "") -> None:
        """An unreachable council is an unanswered question, not a traceback.

        It had been printing the traceback anyway: the fault came through interpolated
        into the sentence, so a council that could not be reached told the player which
        command-line tool had exited with which status. ``detail`` keeps that reachable on
        a hover without putting it in the conversation.
        """
        self._waiting = False
        self._drop_thinking()
        label = TextLabel(message, style="body_small", colour="danger",
                          theme=self._theme, wrap=True)
        label.setToolTip(detail)
        self._insert(label)
        self._standing.setText("NO ANSWER")

    @property
    def waiting(self) -> bool:
        return self._waiting

    # -- internals -----------------------------------------------------------

    def _put(self, question: str) -> None:
        if self._waiting or not self._viewer:
            # One question at a time. A second call while the first is out would arrive
            # carrying a transcript that does not contain the answer it follows up on.
            return
        self.asked.emit(question)

    def _start_again(self) -> None:
        self.reset()
        self.cleared.emit()

    def _build_openers(self) -> None:
        """The suggested questions, one of them named after a real rival.

        A generic *who is my biggest threat?* is a fine question; *why can't I attack
        Poland?* is the one that shows what the panel is for, and it can only be written by
        looking at who is actually across the border.
        """
        clear_layout(self._openers_rows)
        questions = list(OPENERS)
        rival = self._a_rival()
        if rival:
            questions.insert(2, f"Why can't I attack {rival}?")

        # Packed by measured width rather than by a fixed count per row, because the
        # rival's name is however long the rival's name is.
        metrics = QFontMetrics(styled_font(TYPE["body_small"]))
        budget = self.panel.width() - 60
        row, used = self._new_openers_row(), 0
        for question in questions:
            chip = Chip(question, theme=self._theme, style="body_small", padding=(11, 5))
            chip.clicked.connect(lambda q=question: self._put(q))
            width = metrics.horizontalAdvance(question) + 30
            if used and used + width > budget:
                row.addStretch(1)
                row, used = self._new_openers_row(), 0
            row.addWidget(chip)
            used += width + 6
        row.addStretch(1)

    def _new_openers_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self._openers_rows.addLayout(row)
        return row

    def _a_rival(self) -> str:
        """Whoever this government is most obviously at odds with.

        The same read the orders box grounds its example on -- see `ui.grounding`. It was
        a copy of that query here until the placeholder needed the identical answer, and
        two statements agreeing about who your enemy is only for as long as nobody edits
        one of them.
        """
        if self._conn is None:
            return ""
        return a_rival(self._conn, self._world, self._viewer)

    def _opening_note(self) -> str:
        if not self._viewer:
            return ("You are spectating. There is no government here to advise you — take "
                    "a nation and its ministers come with it.")
        return ("A private word with your own ministers. Ask in plain English; they answer "
                "from what your government can see, and from nothing else.")

    def _answers(self) -> int:
        return sum(
            1 for index in range(self._stack.count())
            if isinstance(w := self._stack.itemAt(index).widget(), _Exchange)
            and not w.is_player
        )

    def _drop_thinking(self) -> None:
        """Take the *considering…* line off before the answer lands under it."""
        for index in range(self._stack.count()):
            item = self._stack.itemAt(index)
            widget = item.widget() if item else None
            if isinstance(widget, TextLabel) and widget.text() == self.THINKING:
                self._stack.takeAt(index)
                widget.setParent(None)
                widget.deleteLater()
                return

    def _insert(self, widget: QWidget) -> None:
        """Above the stretch, then scrolled to. The stretch is what keeps a short
        conversation at the top of the sheet rather than floating in the middle of it."""
        self._stack.insertWidget(self._stack.count() - 1, widget)
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    # -- presentation --------------------------------------------------------

    def apply_theme(self, theme: Theme) -> None:
        """The recursive pass reaches every exchange; the bare holders it cannot.

        A layout-holder ``QWidget`` and a scroll viewport take Qt's own palette rather than
        the panel's -- the same two surfaces the correspondence and the trace both have to
        paint by hand.
        """
        super().apply_theme(theme)
        self._scroll.setStyleSheet(scroll_qss(theme))
        for surface in (self._scroll.viewport(), self._inner, self._openers):
            surface.setStyleSheet("background: transparent;")
