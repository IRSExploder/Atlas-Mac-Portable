"""The 404-pixel right rail: who you are talking to, what you are ordering, when to jump.

Three blocks stacked, in the order a turn is actually played. You look at where you
stand with everyone, you talk to someone about it, you write down what you intend, and
then you spend the time. The rail is laid out in that sequence so the turn reads down the
column.

The diplomacy block is a two-page stack rather than a list beside a chat: in 404 pixels a
side-by-side split gives the conversation about 180 pixels of measure, which is unreadable
for prose. Opening talks replaces the list; a back arrow brings it back.
"""

from __future__ import annotations

import sqlite3

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..humanize import regard, regard_word, signed
from .grounding import NEUTRAL_PROMPT, example_order
from .preferences import key_for
from .theme import TYPE, Theme
from .widgets import (
    Chip,
    Divider,
    PressFeedback,
    Rail,
    SectionLabel,
    ShieldSwatch,
    TextLabel,
    focus_qss,
    pressed_qss,
    restyle,
    scaled,
    scroll_qss,
    styled_font,
)

__all__ = ["RightRail"]

#: The four spans worth offering. Anything else is a number the player has to reason
#: about; these are the four they actually mean.
_SPANS = (("Week", 7), ("Month", 30), ("Season", 90), ("Year", 365))


class RightRail(Rail):
    open_chat = Signal(str)
    open_advisor = Signal()
    send_message = Signal(str)
    close_chat = Signal()
    orders_submitted = Signal(str)
    jump_requested = Signal(int)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__("Your turn", width=scaled(404), opens_right=True, theme=theme,
                         parent=parent)
        self._theme = theme
        self._conn: sqlite3.Connection | None = None
        self._world_id = ""
        self._player: str | None = None
        self._days = 30
        self._tracked = 0

        layout = self.body

        self._diplomacy = _DiplomacyBlock(theme)
        self._diplomacy.open_chat.connect(self.open_chat.emit)
        self._diplomacy.open_advisor.connect(self.open_advisor.emit)
        self._diplomacy.send_message.connect(self.send_message.emit)
        self._diplomacy.close_chat.connect(self.close_chat.emit)
        layout.addWidget(self._diplomacy, 1)

        layout.addWidget(Divider(theme=theme))
        self._orders = _OrdersBlock(theme)
        self._orders.submitted.connect(self.orders_submitted.emit)
        layout.addWidget(self._orders)

        layout.addWidget(Divider(theme=theme))
        self._advance = _AdvanceBlock(theme)
        self._advance.span_changed.connect(self._on_span)
        self._advance.jump.connect(lambda: self.jump_requested.emit(self._days))
        layout.addWidget(self._advance)

        self.apply_theme(theme)

    # -- data --------------------------------------------------------------

    def bind(self, conn: sqlite3.Connection, world_id: str, player: str | None) -> None:
        self._conn, self._world_id, self._player = conn, world_id, player
        # Here rather than in `refresh`: what the example can name changes when the world
        # or the government does, and `refresh` is also what a theme change calls.
        self._orders.set_example(conn, world_id, player)
        self.refresh()

    def refresh(self) -> None:
        if self._conn is None:
            return
        self._diplomacy.load(self._conn, self._world_id, self._player, self._theme)

    def set_tracked_count(self, count: int) -> None:
        self._tracked = count
        self._advance.set_estimate(self._days, count)

    @property
    def jump_days(self) -> int:
        """How long a turn covers, as the player has set it.

        Read by anything that has to answer a question about *world* time rather than about
        turns -- the air situation's lapse forecast is the first. Exposed rather than
        duplicated: the pace is chosen here and there must be one answer to what it is.
        """
        return self._days

    def _on_span(self, days: int) -> None:
        self._days = days
        self._advance.set_estimate(days, self._tracked)

    # -- delegation to the blocks ------------------------------------------

    def start_chat(self, nation_name: str) -> None:
        self._diplomacy.start_chat(nation_name, self._theme)

    def start_advisor(self, title: str) -> None:
        self._diplomacy.start_advisor(title, self._theme)

    def append_turn(self, speaker: str, text: str, *, is_player: bool) -> None:
        self._diplomacy.append_turn(speaker, text, is_player=is_player, theme=self._theme)

    def append_note(self, text: str) -> None:
        self._diplomacy.append_note(text, self._theme)

    def show_commitments(self, entries: list[str]) -> None:
        self._diplomacy.show_commitments(entries, self._theme)

    def show_orders_pending(self) -> None:
        self._orders.show_pending(self._theme)

    def show_orders_result(self, accepted: list[str], rejected: list[str]) -> None:
        self._orders.show_result(accepted, rejected, self._theme,
                                 self._offerable() if not accepted and not rejected else ())

    #: How many acts to offer when a parse comes back empty. The catalogue holds
    #: seventeen; a rail column that listed all of them would be a wall rather than a
    #: prompt, and the point here is to get a player unstuck, not to document the game.
    #: The full set is still reachable -- every one of these is an ordinary order.
    _OFFERS = 6

    def _offerable(self) -> tuple[tuple[str, str], ...]:
        """A few things this government could be asked for, from the engine's own
        catalogue.

        Empty when there is no world open yet, and quiet about any failure: a suggestion
        list that raised would turn a bad sentence into a crash, and the sentence above it
        stands on its own.
        """
        if self._conn is None or not self._player:
            return ()
        try:
            from ..engine.capabilities import available_to

            usable = available_to(self._conn, self._world_id, self._player)
        except Exception:  # noqa: BLE001 - a hint is never worth a crash
            return ()
        return tuple((c.explain, c.example[0]) for c in usable[:self._OFFERS])

    def show_orders_note(self, text: str) -> None:
        self._orders.show_note(text, self._theme)

    def clear_orders(self) -> None:
        self._orders.clear()

    def set_busy(self, busy: bool) -> None:
        self._advance.set_busy(busy, self._theme)
        self._orders.setEnabled(not busy)

    def orders_text(self) -> str:
        return self._orders.text()

    # -- theme -------------------------------------------------------------

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.setStyleSheet(
            f"RightRail {{ background: {theme.panel}; border-left: 1px solid {theme.rule}; }}"
        )
        restyle(self, theme)
        self.refresh()


# ---------------------------------------------------------------------------
# Diplomacy
# ---------------------------------------------------------------------------


class _DiplomacyBlock(QWidget):
    open_chat = Signal(str)
    open_advisor = Signal()
    send_message = Signal(str)
    close_chat = Signal()

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._stack = QStackedWidget()
        self._stack.setStyleSheet("background: transparent;")
        layout.addWidget(self._stack, 1)

        self._list_page = _NationListPage(theme)
        self._list_page.chosen.connect(self.open_chat.emit)
        self._list_page.advise.connect(self.open_advisor.emit)
        self._chat_page = _ChatPage(theme)
        self._chat_page.send.connect(self.send_message.emit)
        self._chat_page.back.connect(self._to_list)
        self._chat_page.conclude.connect(self.close_chat.emit)
        self._stack.addWidget(self._list_page)
        self._stack.addWidget(self._chat_page)

    def _to_list(self) -> None:
        self._stack.setCurrentIndex(0)

    def load(self, conn, world_id: str, player: str | None, theme: Theme) -> None:
        self._list_page.load(conn, world_id, player, theme)

    def start_chat(self, nation_name: str, theme: Theme) -> None:
        self._chat_page.start(nation_name, theme)
        self._stack.setCurrentIndex(1)

    def start_advisor(self, title: str, theme: Theme) -> None:
        self._chat_page.start(title, theme, advisory=True)
        self._stack.setCurrentIndex(1)

    def append_turn(self, speaker: str, text: str, *, is_player: bool, theme: Theme) -> None:
        self._chat_page.append_turn(speaker, text, is_player=is_player, theme=theme)

    def append_note(self, text: str, theme: Theme) -> None:
        self._chat_page.append_note(text, theme)

    def show_commitments(self, entries: list[str], theme: Theme) -> None:
        self._list_page.show_commitments(entries, theme)

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        restyle(self, theme)


class _NationListPage(QWidget):
    chosen = Signal(str)
    advise = Signal()

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Your own government sits above the foreign powers, and deliberately outside
        # the Diplomacy list: consulting your ministers is not negotiation, it binds
        # nothing, and putting it among the powers you bargain with would blur the one
        # distinction the player most needs to keep straight.
        council = QWidget()
        council_layout = QVBoxLayout(council)
        council_layout.setContentsMargins(20, 16, 20, 12)
        council_layout.setSpacing(6)
        council_layout.addWidget(SectionLabel("Your council", theme=theme))
        self._council_hint = TextLabel(
            "Ask your own government what it makes of the position. Advice only.",
            style="body_small", colour="ink_faint", theme=theme, wrap=True,
        )
        council_layout.addWidget(self._council_hint)
        self._council_button = Chip("Consult your council", theme=theme, padding=(10, 5))
        self._council_button.clicked.connect(self.advise.emit)
        row = QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 0)
        row.addWidget(self._council_button)
        row.addStretch(1)
        council_layout.addLayout(row)
        layout.addWidget(council)
        layout.addWidget(Divider(soft=True, theme=theme))

        head = QWidget()
        head_layout = QHBoxLayout(head)
        head_layout.setContentsMargins(20, 16, 20, 12)
        self._heading = SectionLabel("Diplomacy", theme=theme)
        self._count = TextLabel("", style="mono_small", colour="ink_ghost", theme=theme)
        head_layout.addWidget(self._heading)
        head_layout.addStretch(1)
        head_layout.addWidget(self._count)
        layout.addWidget(head)

        # A row that opens talks has to say so. Without this the list reads as a status
        # display -- names and coloured bars -- and the player never discovers that
        # negotiation exists at all.
        self._hint = TextLabel(
            "Select a power to open talks.", style="body_small", colour="ink_faint", theme=theme
        )
        self._hint.setContentsMargins(20, 0, 20, 10)
        layout.addWidget(self._hint)

        self._area = QScrollArea()
        self._area.setWidgetResizable(True)
        self._area.setFrameShape(QFrame.Shape.NoFrame)
        self._area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._area.setStyleSheet(scroll_qss(theme))
        body = QWidget()
        body.setStyleSheet("background: transparent;")
        self._body = QVBoxLayout(body)
        self._body.setContentsMargins(20, 0, 20, 16)
        self._body.setSpacing(0)
        self._area.setWidget(body)
        layout.addWidget(self._area, 1)

        self._commitments = TextLabel("", style="body_small", colour="ink_faint",
                                      theme=theme, wrap=True)
        self._commitments.setContentsMargins(20, 0, 20, 14)
        self._commitments.setVisible(False)
        layout.addWidget(self._commitments)

    def load(self, conn, world_id: str, player: str | None, theme: Theme) -> None:
        self._theme = theme
        _empty(self._body)
        # A spectator has no government to consult.
        self._council_button.setEnabled(bool(player))
        self._council_button.setVisible(bool(player))
        self._council_hint.setVisible(bool(player))
        if conn is None or not player:
            self._body.addWidget(
                TextLabel("You are spectating; there is nobody to speak for.",
                          style="prose", colour="ink_faint", theme=theme, wrap=True)
            )
            self._body.addStretch(1)
            return

        rows = conn.execute(
            """
            SELECT n.id, n.name, n.colour, COALESCE(r.score, 0) AS score,
                   COALESCE(r.treaty_status,'none') AS treaty,
                   COALESCE(r.at_war,0) AS at_war
              FROM nation n
              LEFT JOIN relation r ON r.nation_a = ? AND r.nation_b = n.id
             WHERE n.world_id = ? AND n.id <> ? AND n.is_alive = 1
             ORDER BY COALESCE(r.at_war,0) DESC, ABS(COALESCE(r.score,0)) DESC, n.name
            """,
            (player, world_id, player),
        ).fetchall()

        self._count.setText(f"{len(rows)} powers")
        for index, row in enumerate(rows):
            if index:
                self._body.addWidget(Divider(soft=True, theme=theme))
            entry = _NationRow(row, theme=theme)
            entry.clicked.connect(lambda nid=row["id"]: self.chosen.emit(nid))
            self._body.addWidget(entry)
        self._body.addStretch(1)

    def show_commitments(self, entries: list[str], theme: Theme) -> None:
        self._commitments.setText(
            "Standing agreements: " + "; ".join(entries) if entries else ""
        )
        self._commitments.setVisible(bool(entries))
        self._commitments.apply_theme(theme)

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        restyle(self, theme)


class _NationRow(QWidget):
    """A shield, a name, the state of the relationship, and a bar showing how far."""

    clicked = Signal()

    def __init__(self, row, *, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._score = float(row["score"])
        self._at_war = bool(row["at_war"])
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 13, 0, 13)
        layout.setSpacing(12)

        self._shield = ShieldSwatch(row["colour"] or "#888888", width=17, height=22)
        layout.addWidget(self._shield, 0, Qt.AlignmentFlag.AlignTop)

        column = QVBoxLayout()
        column.setSpacing(7)
        head = QHBoxLayout()
        head.setSpacing(8)
        self._name = TextLabel(row["name"], style="row", colour="ink", theme=theme, elide=True)
        head.addWidget(self._name, 1)

        # **The word and the figure, in one label.** The word is what a player reads at a
        # glance and the figure is what tells *hostile* from *very nearly at war*; the bar
        # underneath has carried the second of those since the first build without ever
        # saying what it was a bar of.
        #
        # **Except at zero.** This list is every power alive -- 242 of them on the modern
        # map -- and the overwhelming majority have never had an opinion about you. A
        # column of two hundred noughts is not information, it is the same argument
        # `ui.cast` makes about a spectator's standings: a nought printed two hundred
        # times reads as a claim rather than as the absence of one. *Neutral* already says
        # it, and the hover still carries the figure.
        #
        # **One label rather than two**, because this block rebuilds all 242 rows on every
        # turn and on every theme change: a second `TextLabel` per row costs 60 ms of the
        # rebuild, which is a fifth of it, and the two of them would have carried the same
        # ink anyway. See `tests.test_the_interface_stays_inside_its_frame_budget` for what
        # this rail is being kept inside.
        self._state = TextLabel(
            _relation_word(self._score, self._at_war, row["treaty"])
            + ("" if round(self._score) == 0 else f"   {signed(self._score)}"),
            style="label",
            colour="danger" if self._at_war else _score_ink(self._score),
            theme=theme)
        head.addWidget(self._state)
        self.setToolTip(
            f"{row['name']}: {regard(self._score, at_war=self._at_war, treaty=row['treaty'])}."
            "  Your government's regard for them; theirs for you is their own business.")
        # The affordance. A pointing-hand cursor is invisible until the mouse is already
        # over the row, which is too late to tell someone the row does anything.
        self._talk = TextLabel("TALK →", style="label", colour="brass", theme=theme)
        self._talk.setVisible(False)
        head.addWidget(self._talk)
        column.addLayout(head)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

        self._bar = _RelationBar(theme, self._score, self._at_war)
        column.addWidget(self._bar)
        layout.addLayout(column, 1)

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self._name.apply_theme(theme)
        self._state.set_colour("danger" if self._at_war else _score_ink(self._score), theme)
        self._talk.apply_theme(theme)
        self._bar.apply_theme(theme)

    def enterEvent(self, event) -> None:
        self._talk.setVisible(True)
        self._state.setVisible(False)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._talk.setVisible(False)
        self._state.setVisible(True)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class _RelationBar(QWidget):
    """A bar with zero in the middle, filling left for hostility and right for warmth.

    A left-anchored bar would put "neutral" at the far left next to "hates you", which is
    exactly backwards: neutral is the middle of the scale, so it should be the middle of
    the bar.
    """

    def __init__(self, theme: Theme, score: float, at_war: bool,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._score = max(-100.0, min(100.0, score))
        self._at_war = at_war
        self.setFixedHeight(3)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        theme = self._theme
        painter.fillRect(self.rect(), QColor(theme.raised if theme.is_dark else theme.rule))

        middle = self.width() // 2
        extent = int(abs(self._score) / 100 * (self.width() / 2))
        colour = theme.danger if (self._score < 0 or self._at_war) else theme.good
        if self._score >= 0 and not self._at_war:
            painter.fillRect(middle, 0, extent, self.height(), QColor(colour))
        else:
            painter.fillRect(middle - extent, 0, extent, self.height(), QColor(colour))

        # The zero tick, so the midpoint is legible even at score 0.
        painter.fillRect(middle, 0, 1, self.height(), QColor(theme.ink_ghost))
        painter.end()


class _ChatPage(QWidget):
    send = Signal(str)
    back = Signal()
    conclude = Signal()

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        head = QWidget()
        head_layout = QHBoxLayout(head)
        head_layout.setContentsMargins(20, 14, 16, 12)
        head_layout.setSpacing(10)
        self._back = Chip("← Back", theme=theme, padding=(8, 4))
        self._back.clicked.connect(self.back.emit)
        self._title = TextLabel("", style="nation", colour="ink_bright", theme=theme)
        head_layout.addWidget(self._back)
        head_layout.addWidget(self._title, 1)
        self._end = Chip("Conclude", theme=theme, padding=(8, 4))
        self._end.setToolTip("Record whatever was agreed, so the world can act on it")
        self._end.clicked.connect(self.conclude.emit)
        head_layout.addWidget(self._end)
        layout.addWidget(head)
        layout.addWidget(Divider(soft=True, theme=theme))

        self._area = QScrollArea()
        self._area.setWidgetResizable(True)
        self._area.setFrameShape(QFrame.Shape.NoFrame)
        self._area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._area.setStyleSheet(scroll_qss(theme))
        body = QWidget()
        body.setStyleSheet("background: transparent;")
        self._body = QVBoxLayout(body)
        self._body.setContentsMargins(20, 14, 20, 14)
        self._body.setSpacing(12)
        self._body.addStretch(1)
        self._area.setWidget(body)
        layout.addWidget(self._area, 1)

        self._input = _InputBox(
            f"Say something. {key_for('send')} sends.", height=74, theme=theme
        )
        self._input.submitted.connect(self.send.emit)
        layout.addWidget(self._input)

    def start(self, nation_name: str, theme: Theme, *, advisory: bool = False) -> None:
        self._title.setText(nation_name)
        _empty(self._body)
        self._body.addStretch(1)
        # There is nothing to conclude with your own ministers: the council writes no
        # commitment and changes nothing, so offering the button would promise a
        # consequence that cannot happen.
        self._end.setVisible(not advisory)
        if advisory:
            self.append_note(
                "A private word with your own government. Advice only — nothing here "
                "changes the world, and only orders you issue will.",
                theme,
            )
        else:
            self.append_note(
                f"Talks with {nation_name}. Nothing said here binds until concluded.", theme
            )

    def append_turn(self, speaker: str, text: str, *, is_player: bool, theme: Theme) -> None:
        self._insert(_Bubble(speaker, text, is_player=is_player, theme=theme))

    def append_note(self, text: str, theme: Theme) -> None:
        note = TextLabel(text, style="prose_italic", colour="ink_faint", theme=theme, wrap=True)
        note.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._insert(note)

    def _insert(self, widget: QWidget) -> None:
        self._body.insertWidget(self._body.count() - 1, widget)
        bar = self._area.verticalScrollBar()
        bar.setValue(bar.maximum())

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        restyle(self, theme)


class _Bubble(QFrame):
    """One turn of a conversation. The nation's words are set in the serif, as prose.

    Everything a nation says is untrusted text: it is shown as an utterance by a
    character, never as interface copy, and it can never carry markup -- the label it
    lands in is fixed to plain text.
    """

    def __init__(self, speaker: str, text: str, *, is_player: bool, theme: Theme,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._is_player = is_player
        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 10, 13, 11)
        layout.setSpacing(6)
        self._speaker = TextLabel(speaker, style="label", theme=theme)
        self._text = TextLabel(text, style="prose", colour="ink_body", theme=theme, wrap=True)
        layout.addWidget(self._speaker)
        layout.addWidget(self._text)
        self.apply_theme(theme)

    def apply_theme(self, theme: Theme) -> None:
        if self._is_player:
            border, background, ink = theme.rule, theme.inset, "ink_faint"
        else:
            border, background, ink = theme.brass_line, theme.brass_wash, "brass"
        self.setStyleSheet(f"_Bubble {{ border: 1px solid {border}; background: {background}; }}")
        self._speaker.set_colour(ink, theme)
        self._text.apply_theme(theme)


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------


class _OrdersBlock(QWidget):
    submitted = Signal(str)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(11)

        head = QHBoxLayout()
        self._heading = SectionLabel("Your orders", theme=theme)
        head.addWidget(self._heading)
        head.addStretch(1)
        # The binding as it stands, not as it shipped: a player who has re-bound *send*
        # was being told the wrong key by the one label whose whole job is to say it.
        self._hint = TextLabel(f"{key_for('send')} to check", style="mono_small",
                               colour="ink_ghost", theme=theme)
        head.addWidget(self._hint)
        layout.addLayout(head)

        # Grounded at `bind`, once the rail knows which world and which government it is
        # looking at. Until then the neutral prompt, which is what a spectator keeps.
        self._input = _InputBox(NEUTRAL_PROMPT, height=82, theme=theme, framed=True)
        self._input.submitted.connect(self.submitted.emit)
        layout.addWidget(self._input)

        self._feedback = _ParseFeedback(theme)
        layout.addWidget(self._feedback)

    def text(self) -> str:
        return self._input.text()

    def set_example(self, conn, world_id: str, player: str | None) -> None:
        """Show an order that could be given in *this* world rather than in 1936."""
        self._input.set_placeholder(example_order(conn, world_id, player))

    def clear(self) -> None:
        self._input.clear()
        self._feedback.clear()

    def show_pending(self, theme: Theme) -> None:
        self._feedback.show_pending(theme)

    def show_result(self, accepted: list[str], rejected: list[str], theme: Theme,
                    suggestions: tuple[tuple[str, str], ...] = ()) -> None:
        self._feedback.show_result(accepted, rejected, theme, suggestions)

    def show_note(self, text: str, theme: Theme) -> None:
        self._feedback.show_note(text, theme)

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        restyle(self, theme)


class _ParseFeedback(QWidget):
    """What the parser understood, and what it refused.

    Both halves matter. A refusal that is only a silence teaches the player nothing about
    why an order did not happen, and they write the same impossible order next turn.
    """

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self.setVisible(False)

    def clear(self) -> None:
        _empty(self._layout)
        self.setVisible(False)

    def show_pending(self, theme: Theme) -> None:
        _empty(self._layout)
        self._layout.addWidget(
            TextLabel("Reading your orders…", style="body_small", colour="ink_faint",
                      theme=theme, wrap=True)
        )
        self.setVisible(True)

    def show_note(self, text: str, theme: Theme) -> None:
        """One line, neither an acceptance nor a refusal.

        The state a cancelled read-back leaves: the parse was fine and the player chose
        not to keep it. Printing that under *Cannot do* would blame the order, and leaving
        the block on *Reading your orders…* would be a lie about what the program is doing.
        """
        _empty(self._layout)
        self._layout.addWidget(
            TextLabel(text, style="body_small", colour="ink_faint", theme=theme, wrap=True)
        )
        self.setVisible(True)

    def show_result(self, accepted: list[str], rejected: list[str], theme: Theme,
                    suggestions: tuple[tuple[str, str], ...] = ()) -> None:
        """What the parse made of an order, and -- when it made nothing -- what it could.

        **An empty parse used to say "Nothing actionable in that." and stop.** It named no
        reason and offered no next step, and the reason it was left that way is recorded in
        `UX_INTEGRATION_REPORT.md`: every better sentence points at a list of what a
        government can be asked to do, and there was no player-facing list to point at.
        `understanding.py` is a read-back, not a catalogue.

        There is one now, and it is not a new list: `capabilities.available_to` is the same
        catalogue the parser's enum and the council's advice are both generated from, so
        what this block offers can never drift from what the engine will accept. Filtered to
        the powers this government actually has, so it does not suggest an act the world
        would refuse.
        """
        _empty(self._layout)
        if not accepted and not rejected:
            self._layout.addWidget(
                TextLabel("Nothing in that was an order this government can act on.",
                          style="body_small", colour="ink_faint", theme=theme, wrap=True)
            )
            if suggestions:
                self._layout.addWidget(
                    TextLabel("You could ask for", style="label", colour="brass",
                              theme=theme)
                )
                for explain, example in suggestions:
                    self._layout.addWidget(
                        TextLabel(f"{explain} — “{example}”",
                                  style="body_small", colour="ink_dim", theme=theme,
                                  wrap=True)
                    )
        for heading, entries, colour in (
            ("Will attempt", accepted, "good"),
            ("Cannot do", rejected, "danger"),
        ):
            if not entries:
                continue
            self._layout.addWidget(
                TextLabel(heading, style="label", colour=colour, theme=theme)
            )
            for entry in entries:
                self._layout.addWidget(
                    TextLabel(f"· {entry}", style="body_small", colour="ink_dim",
                              theme=theme, wrap=True)
                )
        self.setVisible(True)

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        restyle(self, theme)


class _InputBox(QWidget):
    """A text area that submits on Ctrl+Enter, and on focus-out if it has content.

    Never on a timer. A debounce here would spawn a parser subprocess on every pause in
    typing -- a real call, for a result the player has not asked for.
    """

    submitted = Signal(str)

    def __init__(self, placeholder: str, *, height: int, theme: Theme,
                 framed: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._framed = framed
        #: The last text actually submitted, so focus-out cannot resend it.
        self._last_submitted = ""
        layout = QVBoxLayout(self)
        # Framed boxes sit inside a block that already has padding; unframed ones sit
        # flush against the rail and have to supply their own.
        layout.setContentsMargins(*((0, 0, 0, 0) if framed else (20, 12, 20, 14)))

        self._edit = QPlainTextEdit()
        self._edit.setPlaceholderText(placeholder)
        self._edit.setFixedHeight(height)
        self._edit.setFrameShape(QFrame.Shape.NoFrame)
        self._edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        layout.addWidget(self._edit)

        # Scoped to this box for the reason given in `ui.council`: two of these are built
        # -- the orders box and the rail's chat composer -- and with the council panel open
        # there are three claimants on the window for one keystroke, which Qt resolves by
        # giving it to nobody. The label beside this box names the key; the key works on
        # the box the label is attached to.
        shortcut = QShortcut(QKeySequence(key_for("send")), self._edit)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(self._submit)
        self._edit.focusOutEvent = self._on_focus_out  # type: ignore[method-assign]
        self.apply_theme(theme)

    def set_placeholder(self, text: str) -> None:
        self._edit.setPlaceholderText(text)

    def _on_focus_out(self, event) -> None:
        QPlainTextEdit.focusOutEvent(self._edit, event)
        self._submit()

    def _submit(self) -> None:
        # Submitting the same unchanged text twice is two real provider subprocesses for
        # one typed line. Focus-out fires whenever the player clicks anything -- including
        # Advance -- so without this, typing an order and pressing Ctrl+Enter and then
        # clicking Advance parsed it twice and raced the two results.
        text = self._edit.toPlainText().strip()
        if text and text == self._last_submitted:
            return
        self._last_submitted = text
        if text:
            self.submitted.emit(text)

    def text(self) -> str:
        return self._edit.toPlainText().strip()

    def clear(self) -> None:
        self._edit.clear()
        self._last_submitted = ""

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        font = styled_font(TYPE["prose"])
        self._edit.setFont(font)
        self._edit.setStyleSheet(
            f"QPlainTextEdit {{ background: {theme.inset};"
            f" border: 1px solid {theme.edge}; color: {theme.ink};"
            f" selection-background-color: {theme.brass_line}; padding: 9px 11px; }}"
            f"QPlainTextEdit:focus {{ border-color: {theme.brass_line}; }}"
            # **The scrollbar belongs to the theme too.** `restyle` hands `scroll_qss` to
            # every `QScrollArea` it walks past, and a `QPlainTextEdit` is not one -- it
            # brings its own. So the moment an order ran to three lines, Windows' light
            # grey scrollbar, arrow buttons and all, appeared inside the darkest panel on
            # the screen. The council's ask box already appended this; the orders box and
            # the chat composer, written earlier, did not.
            + scroll_qss(theme)
        )


# ---------------------------------------------------------------------------
# Advance time
# ---------------------------------------------------------------------------


class _AdvanceBlock(QWidget):
    span_changed = Signal(int)
    jump = Signal()

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._days = 30
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 18)
        layout.setSpacing(12)

        self._heading = SectionLabel("Advance time", theme=theme)
        layout.addWidget(self._heading)

        spans = QHBoxLayout()
        spans.setSpacing(5)
        self._chips: dict[int, Chip] = {}
        for label, days in _SPANS:
            chip = Chip(label, theme=theme, selected=(days == self._days))
            chip.clicked.connect(lambda d=days: self._pick(d))
            self._chips[days] = chip
            spans.addWidget(chip, 1)
        layout.addLayout(spans)

        self._estimate = TextLabel("", style="body_small", colour="ink_faint",
                                   theme=theme, wrap=True)
        layout.addWidget(self._estimate)

        self._button = _AdvanceButton(theme)
        self._button.clicked.connect(self.jump.emit)
        layout.addWidget(self._button)

        self.set_estimate(self._days, 0)

    def _pick(self, days: int) -> None:
        self._days = days
        for value, chip in self._chips.items():
            chip.set_selected(value == days)
        self.span_changed.emit(days)

    def set_estimate(self, days: int, tracked: int) -> None:
        """What the span buys, said in the world rather than in the program.

        This sentence used to count the model calls a jump would make. It sits an inch from
        the button that starts one and stays on screen behind the sheet the turn is watched
        through -- which is written entirely in the world's own words -- so it was the one
        line able to give the whole thing away. What it is actually telling the player is
        how much of the world will be thinking, and that is a fact about the world.
        """
        powers = (
            f"{tracked} power{'s' if tracked != 1 else ''} will decide for "
            f"{'themselves' if tracked != 1 else 'itself'}"
            if tracked else "the world moves on its own"
        )
        self._estimate.setText(
            f"{days} days · {powers}. "
            "Longer jumps make more history and take longer to live through."
        )

    def set_busy(self, busy: bool, theme: Theme) -> None:
        self._button.set_busy(busy, theme)

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        restyle(self, theme)


class _AdvanceButton(PressFeedback, QFrame):
    """The one brass control in the interface. Pressing it spends real time and quota."""

    clicked = Signal()

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._busy = False
        self.setFixedHeight(44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("Advance time")
        self.make_focusable()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._label = TextLabel("Advance", style="action", theme=theme)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._label)
        self.apply_theme(theme)

    def set_busy(self, busy: bool, theme: Theme) -> None:
        self._busy = busy
        self._label.setText("The world turns…" if busy else "Advance")
        self.setCursor(
            Qt.CursorShape.BusyCursor if busy else Qt.CursorShape.PointingHandCursor
        )
        self.apply_theme(theme)

    def apply_theme(self, theme: Theme) -> None:
        if self._busy:
            self.setStyleSheet(
                f"_AdvanceButton {{ background: {theme.inset};"
                f" border: 1px solid {theme.edge}; }}"
                + focus_qss("_AdvanceButton", theme)
            )
            self._label.set_colour("ink_faint", theme)
        else:
            self.setStyleSheet(
                f"_AdvanceButton {{ background: {theme.brass};"
                f" border: 1px solid {theme.brass}; }}"
                f"_AdvanceButton:hover {{ background: {theme.brass_hot};"
                f" border-color: {theme.brass_hot}; }}"
                + focus_qss("_AdvanceButton", theme)
                + pressed_qss("_AdvanceButton", theme)
            )
            self._label.set_colour(theme.void if theme.is_dark else "#FBF8F2", theme)

    def _takes_press(self, event) -> bool:
        # A busy button does not start a second turn, and must not look as though it
        # has taken the press either.
        return super()._takes_press(event) and not self._busy

    def _fire(self) -> None:
        self.clicked.emit()


# ---------------------------------------------------------------------------


def _empty(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            _empty(item.layout())


def _score_ink(score: float) -> str:
    if score >= 25:
        return "good"
    if score <= -25:
        return "danger"
    return "ink_faint"


def _relation_word(score: float, at_war: bool, treaty: str) -> str:
    """The relationship in a word, because ``-37`` is a number, not a feeling.

    Kept as a name in this module because it is the one the rest of the program calls;
    the table behind it now lives in `atlas.humanize` beside every other reading of a
    stored figure, so the cast list and this rail cannot drift into two vocabularies.
    """
    return regard_word(score, at_war=at_war, treaty=treaty)
