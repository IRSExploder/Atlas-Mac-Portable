"""The vocabulary the design is built from.

The mock uses the same handful of shapes everywhere: a small-caps section label, a value
over a thin bar, a shield swatch for a nation, a bordered chip that can be selected. Each
appears dozens of times, so each is a widget here rather than a block of inline styling
repeated across six panels.

Everything takes a :class:`Theme` and re-styles on demand, which is what makes the light
theme a runtime toggle instead of a second codebase.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..humanize import figure, movement
from .theme import TYPE, Theme, TypeStyle

__all__ = [
    "Chip",
    "PressFeedback",
    "Rail",
    "scaled",
    "RailHandle",
    "RAIL_HANDLE_WIDTH",
    "Divider",
    "Meter",
    "SectionLabel",
    "ShieldSwatch",
    "StatBar",
    "TextLabel",
    "control_qss",
    "focus_qss",
    "pressed_qss",
    "restyle",
    "scroll_qss",
    "styled_font",
    "tint",
]


def scroll_qss(theme: Theme, *, horizontal: bool = False) -> str:
    """Scrollbars that belong to the theme.

    Qt's native scrollbar is a light Windows chrome bar. Left alone it is the one bright
    rectangle on a dark interface, and it lands on the busiest edge of both rails.
    """
    return (
        "QScrollArea { background: transparent; }"
        "QScrollBar:vertical { background: transparent; width: 9px; margin: 0; }"
        f"QScrollBar::handle:vertical {{ background: {theme.rule};"
        " min-height: 30px; border-radius: 0; }"
        f"QScrollBar::handle:vertical:hover {{ background: {theme.rule_hover}; }}"
        "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical"
        " { background: transparent; }"
        + ("QScrollBar:horizontal { background: transparent; height: 9px; margin: 0; }"
           f"QScrollBar::handle:horizontal {{ background: {theme.rule};"
           " min-width: 30px; border-radius: 0; }"
           f"QScrollBar::handle:horizontal:hover {{ background: {theme.rule_hover}; }}"
           "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }"
           "QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal"
           " { background: transparent; }"
           if horizontal else "QScrollBar:horizontal { height: 0; }")
    )


def clear_layout(layout) -> None:
    """Take a layout back to nothing, widgets and nested layouts alike.

    **``deleteLater`` alone is not enough.** It defers the delete to the event loop, so
    until that turn comes the old rows are still children of the panel: a screen rebuilt
    three times held three copies of everything and drew them on top of each other.
    Reparenting first removes them from the tree immediately and lets the deferred delete
    do the actual freeing, which is the ordinary Qt idiom for this.
    """
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
            continue
        child = item.layout()
        if child is not None:
            clear_layout(child)


def restyle(root: QWidget, theme: Theme) -> None:
    """Re-theme every descendant of ``root``.

    Descends until it meets a widget that knows how to theme itself, then hands that
    widget the theme and stops -- a composite is responsible for its own subtree. Without
    the stop, a rail with sixty rows would re-theme every label two or three times over.
    """
    for child in root.findChildren(
        QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly
    ):
        handler = getattr(child, "apply_theme", None)
        if callable(handler):
            handler(theme)
            continue
        if isinstance(child, QScrollArea):
            child.setStyleSheet(scroll_qss(theme))
        restyle(child, theme)


#: The text scale in force, as a plain multiplier. **One module-level number, because
#: there is exactly one place a type-scale entry becomes a font** -- so setting it here
#: moves all twenty-five sizes together and no widget has to be told about it or hold a
#: reference to a preferences object. A widget still asks for "the label style".
#:
#: Set by :func:`set_text_scale` when the preferences load, and read on every font built
#: after that. Widgets already alive are re-fonted by the theme pass that follows.
_TEXT_SCALE = 1.0


def set_text_scale(scale: float) -> None:
    """Set the multiplier every font built from here on is sized by."""
    global _TEXT_SCALE
    _TEXT_SCALE = max(0.5, min(3.0, float(scale)))


def text_scale() -> float:
    return _TEXT_SCALE


def scaled(pixels: int | float) -> int:
    """A width that was measured against type, expressed at the type scale in force.

    **Every breakpoint in this interface is a measurement of words.** *LEGITIMACY* needs
    68 pixels of meter, the channel rows need 1,700 of window -- at the size the design
    was drawn at. Set the text a quarter larger, which is the first thing somebody does on
    a high-DPI screen, and every one of those numbers is a quarter too small: the bar goes
    on claiming there is room and the labels go on eliding inside it. Anything compared
    against a font goes through here.
    """
    return round(pixels * _TEXT_SCALE)


def styled_font(style: TypeStyle) -> QFont:
    """Turn a type-scale entry into a QFont, tracking included and scaled."""
    font = QFont(style.family)
    font.setPointSizeF(round(style.size * _TEXT_SCALE, 2))
    font.setWeight(QFont.Weight(style.weight))
    font.setItalic(style.italic)
    if style.tracking:
        # Qt takes percentage spacing; the design expresses it in em.
        font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 100 + style.tracking * 100)
    if style.upper:
        font.setCapitalization(QFont.Capitalization.AllUppercase)
    return font


def tint(colour: str, alpha: float) -> str:
    """A translucent version of a colour, for the map's frosted overlays."""
    c = QColor(colour)
    c.setAlphaF(alpha)
    return c.name(QColor.NameFormat.HexArgb)


class TextLabel(QLabel):
    """A label bound to a type-scale entry and a palette colour."""

    def __init__(
        self,
        text: str = "",
        *,
        style: str = "body",
        colour: str | None = None,
        theme: Theme | None = None,
        wrap: bool = False,
        elide: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self._style_key = style
        self._colour_key = colour
        self._elide = elide
        self._ink = "#FFFFFF"
        self.setWordWrap(wrap)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        # Plain text, always. Most of what this label shows is model-written prose about
        # nations, and QLabel guesses rich text from the content -- so a nation whose
        # reply happened to contain markup would be rendering markup in the interface.
        self.setTextFormat(Qt.TextFormat.PlainText)
        #: Whether the tooltip on this label is one it wrote itself. A caller that sets
        #: its own is never overruled.
        self._own_tooltip = False
        if elide:
            # Give up the size hint entirely. A label that insists on its full width
            # pushes the whole rail wider than the window, and the overflow is silently
            # clipped -- which is how "Bosnia and Herzegovina" hid every relation state
            # in the diplomacy list off the right-hand edge.
            self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            self.setMinimumWidth(0)
        if theme is not None:
            self.apply_theme(theme)

    def apply_theme(self, theme: Theme) -> None:
        self.setFont(styled_font(TYPE[self._style_key]))
        self._ink = self._resolve(theme)
        self.setStyleSheet(f"color: {self._ink}; background: transparent;")
        # A different face is a different width, and a width is what decides this.
        self._offer_the_whole_of_it()

    def paintEvent(self, event) -> None:
        if not self._elide:
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(QColor(self._ink))
        metrics = QFontMetrics(self.font())
        # Measured on the cased form, because the type scale uppercases through the font
        # and an uppercase string is wider than the one that was handed in.
        source = self.text().upper() if TYPE[self._style_key].upper else self.text()
        painter.drawText(
            self.rect(), int(self.alignment()),
            metrics.elidedText(source, Qt.TextElideMode.ElideRight, self.width()))
        painter.end()

    def setText(self, text: str) -> None:
        super().setText(text)
        self._offer_the_whole_of_it()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._offer_the_whole_of_it()

    def _offer_the_whole_of_it(self) -> None:
        """A shortened label says the rest of itself on hover.

        Elision is how this interface fits a window; it is not a licence to lose a word.
        *Democratic Republic of the Con...* and *Republic of the Con...* are two different
        countries in the same list, and the row is a button that opens talks with one of
        them. So anything actually being cut carries its full text as a tooltip -- which
        costs nothing, needs no layout, and covers every eliding label in the program
        rather than the handful somebody remembered.

        A label whose caller set its own tooltip keeps it: an explanation of what a
        control does beats a repetition of what it says.

        **Asked on a text or a width change, not while painting.** Painting looked like
        the natural place -- the elision is measured there anyway -- but a label scrolled
        below the fold of a list is never painted, and those are exactly the rows a
        player is about to scroll to. Eight countries in the diplomacy list were being cut
        and none of them had a tooltip.
        """
        if not self._elide:
            return
        if self.toolTip() and not self._own_tooltip:
            return
        style = TYPE[self._style_key]
        source = self.text().upper() if style.upper else self.text()
        cut = QFontMetrics(self.font()).horizontalAdvance(source) > self.width() + 1
        wanted = self.text() if cut else ""
        if wanted != self.toolTip():
            self.setToolTip(wanted)
            self._own_tooltip = bool(wanted)

    def _resolve(self, theme: Theme) -> str:
        if self._colour_key is None:
            return theme.ink
        if self._colour_key.startswith("#"):
            return self._colour_key
        return getattr(theme, self._colour_key, theme.ink)

    def set_colour(self, colour: str, theme: Theme) -> None:
        self._colour_key = colour
        self.apply_theme(theme)

    def set_style(self, style: str, theme: Theme) -> None:
        if style != self._style_key:
            self._style_key = style
            self.apply_theme(theme)


class SectionLabel(TextLabel):
    """The wide-tracked small-caps heading that opens every block in the design."""

    def __init__(self, text: str, *, theme: Theme | None = None, parent: QWidget | None = None) -> None:
        super().__init__(text, style="section", colour="ink_faint", theme=theme, parent=parent)


class Divider(QFrame):
    """A one-pixel rule. Horizontal by default."""

    def __init__(
        self, *, vertical: bool = False, soft: bool = False,
        length: int | None = None, theme: Theme | None = None, parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._soft = soft
        self._vertical = vertical
        if vertical:
            self.setFixedWidth(1)
            if length:
                self.setFixedHeight(length)
            else:
                self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        else:
            self.setFixedHeight(1)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        if theme is not None:
            self.apply_theme(theme)

    def apply_theme(self, theme: Theme) -> None:
        self.setStyleSheet(f"background: {theme.rule_soft if self._soft else theme.rule};")


class ShieldSwatch(QWidget):
    """A nation's colour as a small shield.

    The design uses this shape everywhere a nation is named. A plain square would read as
    a legend swatch; the shield reads as a power.
    """

    def __init__(
        self, colour: str = "#888888", *, width: int = 18, height: int = 24,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._colour = colour
        self.setFixedSize(width, height)

    def set_colour(self, colour: str) -> None:
        self._colour = colour
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        shoulder = h * 0.72

        path = QPainterPath()
        path.moveTo(0, 0)
        path.lineTo(w, 0)
        path.lineTo(w, shoulder)
        path.lineTo(w / 2, h)
        path.lineTo(0, shoulder)
        path.closeSubpath()

        painter.fillPath(path, QColor(self._colour))
        painter.end()


class StatBar(QWidget):
    """A label, a value, and a thin progress rule underneath."""

    def __init__(
        self, label: str = "", *, theme: Theme, show_delta: bool = False,
        label_style: str = "label", elide_label: bool = False, stacked: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._theme = theme
        self._pct = 0.0
        self._colour = theme.info
        self._show_delta = show_delta

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3 if stacked else 6)

        self._label = TextLabel(label, style=label_style, colour="ink_faint", theme=theme,
                                elide=elide_label)
        self._value = TextLabel("", style="num", colour="ink", theme=theme)
        if stacked:
            # Narrow columns: the label gets a line of its own so it reads in full. Side
            # by side, a six-across meter strip leaves it about thirty pixels.
            self._value.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
            layout.addWidget(self._label)
            layout.addWidget(self._value)
        else:
            head = QHBoxLayout()
            head.setContentsMargins(0, 0, 0, 0)
            head.setSpacing(6)
            self._value.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom
            )
            # The label carries the stretch rather than a spacer: when it elides, its size
            # policy is Ignored, and a spacer beside it would take the whole row.
            head.addWidget(self._label, 1)
            head.addWidget(self._value)
            layout.addLayout(head)

        self._track = _BarTrack(theme)
        layout.addWidget(self._track)

        self._delta = TextLabel("", style="mono_small", colour="ink_ghost", theme=theme)
        self._delta.setVisible(show_delta)
        layout.addWidget(self._delta)

    def set_value(self, value: float, pct: float, colour: str, *,
                  delta: float | None = None, places: int = 0) -> None:
        """Draw one figure. ``places`` is how much of it is worth reading.

        Zero by default, which is the answer for every meter on the top bar: a player
        choosing between war and peace is not choosing between an economy of 47.2 and one
        of 47.3, and ``:g`` was printing whatever the float happened to be. A field where
        a fraction is the whole range -- development runs 0 to 10 -- asks for one.
        """
        self._value.setText(figure(value, places=places))
        self._track.set_fill(max(0.0, min(1.0, pct)), colour)
        if self._show_delta:
            if delta is None:
                self._delta.setText("")
                return
            # `movement` decides between *steady* and a figure, and keeps a decimal
            # under a whole point: a slide of 0.2 used to print as `+0`, which reads as
            # a rounding artefact rather than as the beginning of a slide.
            said = movement(delta)
            self._delta.setText(said)
            self._delta.set_colour(
                "ink_ghost" if said == "steady"
                else ("good" if delta > 0 else "danger"), self._theme)

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        for child in (self._label, self._value, self._delta):
            child.apply_theme(theme)
        self._track.apply_theme(theme)


    def set_label(self, text: str) -> None:
        """Re-word the caption, for a bar that has to read at more than one width.

        A caption that does not fit is worse than a shorter caption that does: *LEGITIM...*
        is not a word, and a player reading a narrow window is being asked to guess at the
        one thing on the meter that says what the number means. Callers hand a shorter form
        below a measured width; the accessible name is left alone, because a screen reader
        has no width problem and should still hear the whole word.
        """
        self._label.setText(text)


class _BarTrack(QWidget):
    """The 3px rule a StatBar fills."""

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._fill = 0.0
        self._colour = theme.info
        self.setFixedHeight(3)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_fill(self, fraction: float, colour: str) -> None:
        self._fill = fraction
        self._colour = colour
        self.update()

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(self._theme.raised if self._theme.is_dark else self._theme.rule))
        if self._fill > 0:
            painter.fillRect(0, 0, int(self.width() * self._fill), self.height(), QColor(self._colour))
        painter.end()


class Meter(StatBar):
    """A top-bar meter: value, bar, and the change since last turn."""

    #: What a meter asks for. Deliberately less than the width at which its label reads
    #: in full: six of these carry the stretch in the top bar's status block, so every
    #: pixel the bar has spare comes here anyway. What this number decides is not how
    #: wide a meter *is*, it is what the bar has to be before it stops shortening things
    #: elsewhere to feed them.
    PREFERRED_WIDTH = 52

    def __init__(self, label: str, *, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(label, theme=theme, show_delta=True, label_style="meter",
                         elide_label=True, stacked=True, parent=parent)
        # Six meters, a nation, a date and three channel rows share whatever the top
        # bar has. 68 is the width at which the longest label still reads in full, and
        # what the row asks for; 44 is the floor -- the number and its bar, with the
        # label elided -- because a bar that cannot be squeezed is a window that cannot
        # fit the monitor. See `ui.topbar`.
        self.setMinimumWidth(44)

    def sizeHint(self):
        hint = super().sizeHint()
        hint.setWidth(max(scaled(self.PREFERRED_WIDTH), hint.width()))
        return hint


def control_qss(selector: str, theme: Theme, *, background: str = "transparent") -> str:
    """The whole visible life of a control: resting, hovered, focused, pressed.

    One helper because these four have to be told apart from each other, and four
    stylesheets written in four modules is how you end up with a hover state that is a
    1.88:1 change nobody can see and a focus state that does not exist at all. Both were
    true here.
    """
    return (
        f"{selector} {{ border: 1px solid {theme.edge}; background: {background}; }}"
        f"{selector}:hover {{ border-color: {theme.edge_hover}; }}"
        # A control that cannot be pressed says so by receding: the outline drops back to
        # the structural line, which is the one thing in the palette deliberately below
        # the boundary floor. WCAG exempts an inactive control from that floor; what it
        # does not excuse is a dead button that looks exactly like a live one, which is
        # what this was -- the disabled state was styled nowhere and read as *resting*.
        f"{selector}:disabled {{ border-color: {theme.rule}; }}"
        + focus_qss(selector, theme)
        + pressed_qss(selector, theme))


def focus_qss(selector: str, theme: Theme) -> str:
    """Where the keyboard is.

    **Nothing in this program showed keyboard focus and almost nothing could take it.**
    Every button here is a QFrame -- there is not one QPushButton in the whole interface
    -- and a QFrame has no focus policy, no focus ring and no Space-to-activate. Tabbing
    through the window moved nothing visible and pressing Return did nothing, which is
    not a styling gap: it is the interface being unusable without a mouse.

    Two pixels of brass, which is the accent everywhere else it means *this is the thing
    you are dealing with*. It reads differently from the selected state -- that is a wash
    behind the text as well as a line around it -- and it clears 3:1 against every surface
    a control sits on in both themes.
    """
    return (f"{selector}:focus {{ border: 2px solid {theme.brass};"
            f" outline: none; }}")


def pressed_qss(selector: str, theme: Theme) -> str:
    """The rule that shows a press has landed, for a frame pretending to be a button.

    Keyed on a dynamic property rather than Qt's ``:pressed`` pseudo-state, which only
    QAbstractButton has. See :class:`PressFeedback`.
    """
    return (f'{selector}[pressed="true"] {{ background: {theme.brass_wash};'
            f" border-color: {theme.brass_line}; }}")


class PressFeedback:
    """A frame that shows a press has landed *before* it does what the press means, and
    that a keyboard can reach.

    Every button in this interface is a QFrame -- there is not one QPushButton in the
    program -- and every one of them emitted its signal from inside `mousePressEvent`,
    before calling super. The far end of that signal opens a panel, which is a hundred
    milliseconds and more of synchronous work on the UI thread, so Qt never reached the
    point where it could paint anything. A click that was doing exactly what it was told
    looked like a click that had been ignored, and the honest response to a button that
    looks ignored is to press it again.

    So the frame is marked pressed and repainted **synchronously** -- `update()` only
    queues a paint, and the queue is precisely what is about to be blocked -- and the
    work goes out on the next turn of the event loop, by which time the pressed state is
    on the glass.

    Subclasses say what a press means by implementing :meth:`_fire`, and may refuse one
    by overriding :meth:`_takes_press`. Mix it in *before* the QWidget base, so its
    handlers win and `super()` still reaches Qt's.
    """

    def _fire(self) -> None:
        """Emit whatever this press means. Runs one event-loop turn after the press."""
        raise NotImplementedError

    def _takes_press(self, event) -> bool:
        return event.button() == Qt.MouseButton.LeftButton

    def make_focusable(self) -> None:
        """Let the keyboard have this control. Call from the subclass's constructor.

        Tab reaches it, the ring in `focus_qss` shows where it is, and Space or Return
        does what a click does -- through the same `_fire`, so a keyboard press gets the
        pressed state painted for it exactly as a mouse press does.
        """
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def changeEvent(self, event) -> None:
        # The label inside one of these frames is a child with its own stylesheet, so
        # Qt's `:disabled` never reaches it. Re-theming on the change is what makes the
        # word go grey along with the border around it.
        from PySide6.QtCore import QEvent

        if event.type() == QEvent.Type.EnabledChange:
            theme = getattr(self, "_theme", None)
            if theme is not None:
                self.apply_theme(theme)
        super().changeEvent(event)

    def keyPressEvent(self, event) -> None:
        if (self.focusPolicy() != Qt.FocusPolicy.NoFocus
                and event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter)):
            self._show_pressed(True)
            QTimer.singleShot(0, self._fire_once)
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:
        if not self._takes_press(event):
            super().mousePressEvent(event)
            return
        self._show_pressed(True)
        super().mousePressEvent(event)
        QTimer.singleShot(0, self._fire_once)

    def mouseReleaseEvent(self, event) -> None:
        self._show_pressed(False)
        super().mouseReleaseEvent(event)

    def _fire_once(self) -> None:
        try:
            self._fire()
        finally:
            # Cleared here as well as on the release, because the work `_fire` sets off
            # routinely puts a panel over this widget and the release never arrives.
            self._show_pressed(False)

    def _show_pressed(self, pressed: bool) -> None:
        if bool(self.property("pressed")) is pressed:
            return
        self.setProperty("pressed", pressed)
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        self.repaint()


class Chip(PressFeedback, QFrame):
    """A bordered, clickable pill. Selected state is a brass wash and a brass border."""

    clicked = Signal()

    def __init__(
        self, text: str, *, theme: Theme, selected: bool = False,
        style: str = "button", padding: tuple[int, int] = (10, 5),
        wrap: bool = False, parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._theme = theme
        self._selected = selected
        self._padding = padding

        layout = QHBoxLayout(self)
        layout.setContentsMargins(padding[0], padding[1], padding[0], padding[1])
        layout.setSpacing(7)
        # A chip whose label is a sentence rather than a word -- *an ally was attacked
        # and may call on its pact* -- is 677 pixels of one line, which is wider than the
        # panel it sits in. Those wrap; the ordinary one-word chips do not, because a
        # wrapping label reports no useful minimum and a row of them collapses.
        self._label = TextLabel(text, style=style, theme=theme, wrap=wrap)
        layout.addWidget(self._label)

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.make_focusable()
        self.apply_theme(theme)

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self.apply_theme(self._theme)

    @property
    def selected(self) -> bool:
        return self._selected

    def set_text(self, text: str) -> None:
        self._label.setText(text)

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        live = self.isEnabled()
        ink = "brass" if (self._selected and live) else "ink_dim"
        if not live:
            ink = "ink_faint"
        if self._selected and live:
            # Named, not `QFrame`: QLabel is a QFrame, so an unqualified selector
            # puts this border around the chip's own text as well.
            sheet = (f"Chip {{ border: 1px solid {theme.brass_line};"
                     f" background: {theme.brass_wash}; }}"
                     f"Chip:hover {{ border-color: {theme.edge_hover}; }}"
                     + focus_qss("Chip", theme) + pressed_qss("Chip", theme))
        else:
            # **A disabled chip drops its brass**, selected or not. The primary action of
            # a screen is the one chip that is both selected and sometimes unavailable,
            # and painting an unavailable one in the colour of the live one is the same
            # lie `control_qss` was written to stop: it advertised a press it refused.
            sheet = control_qss("Chip", theme)
        self.setStyleSheet(sheet)
        self.setCursor(Qt.CursorShape.PointingHandCursor if live
                       else Qt.CursorShape.ArrowCursor)
        self._label.set_colour(ink, theme)

    def _fire(self) -> None:
        self.clicked.emit()


#: What a collapsed rail leaves behind on the edge of the window.
RAIL_HANDLE_WIDTH = 34


class RailHandle(PressFeedback, QFrame):
    """The strip a rail collapses to, and the way back out of it.

    **Collapsing beats squeezing.** On a 1280-wide window the two rails take 752 of it and
    leave the world map 528 -- a map of the world in a column narrower than the event feed
    beside it. The alternative on offer was to shrink everything until it fitted, which is
    how a nation's name becomes *Unite...* and a meter's label becomes *LEGITIM...*. So
    below the width where both rails and a usable map fit at once, a rail becomes this: a
    34px strip with its name down the side and a chevron pointing the way it will move.

    The strip only exists where it is needed. Above the breakpoint there is room for
    everything, and a permanent handle would be 34 pixels of the map spent on an
    affordance nobody has a use for.
    """

    clicked = Signal()

    def __init__(self, caption: str, *, opens_right: bool, theme: Theme,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._caption = caption
        self._opens_right = opens_right
        self._open = False
        self._theme = theme
        self.setFixedWidth(RAIL_HANDLE_WIDTH)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"Show {caption.lower()}")
        self.make_focusable()
        self.apply_theme(theme)

    def set_open(self, is_open: bool) -> None:
        """Which way the chevron points, and what the tooltip offers."""
        self._open = is_open
        self.setToolTip(f"{'Hide' if is_open else 'Show'} {self._caption.lower()}")
        self.update()

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.setStyleSheet(
            f"RailHandle {{ background: {theme.panel}; border: none; }}"
            f"RailHandle:hover {{ background: {theme.inset}; }}"
            + focus_qss("RailHandle", theme) + pressed_qss("RailHandle", theme))
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setPen(QColor(self._theme.ink_faint))

        # The chevron points where the rail will go, not where it is.
        pointing_right = self._opens_right ^ self._open
        painter.setFont(styled_font(TYPE["body"]))
        painter.drawText(
            self.rect().adjusted(0, 12, 0, 0), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            "\u203a" if pointing_right else "\u2039")

        painter.setFont(styled_font(TYPE["label"]))
        painter.save()
        painter.translate(self.width() / 2, self.height() - 18)
        painter.rotate(-90)
        painter.drawText(0, 5, self._caption.upper())
        painter.restore()
        painter.end()

    def _fire(self) -> None:
        self.clicked.emit()


class Rail(QFrame):
    """A side rail that can be put away.

    Subclasses build into :attr:`body` exactly as they did when this was a plain frame.
    What is added is a second state: the whole column replaced by a
    :class:`RailHandle`, so the map gets the 348 pixels back rather than the interface
    getting smaller.
    """

    #: Emitted when the rail is put away or brought back, so the window can remember it.
    collapsed_changed = Signal(bool)

    def __init__(self, caption: str, *, width: int, opens_right: bool, theme: Theme,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_width = width
        self._collapsed = False
        self._collapsible = False

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._handle = RailHandle(caption, opens_right=opens_right, theme=theme)
        self._handle.clicked.connect(lambda: self.set_collapsed(not self._collapsed))
        self._handle.setVisible(False)
        self._column = QWidget()
        self._column.setStyleSheet("background: transparent;")
        self.body = QVBoxLayout(self._column)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(0)
        if opens_right:
            outer.addWidget(self._handle)
            outer.addWidget(self._column, 1)
        else:
            outer.addWidget(self._column, 1)
            outer.addWidget(self._handle)
        self.setFixedWidth(width)

    @property
    def full_width(self) -> int:
        """What the column costs when it is out. Already at the type scale in force."""
        return self._full_width

    @property
    def collapsed(self) -> bool:
        return self._collapsed and self._collapsible

    def set_collapsible(self, collapsible: bool) -> None:
        """Whether this viewport is one where putting the rail away is on offer."""
        if collapsible == self._collapsible:
            return
        self._collapsible = collapsible
        self._apply()

    def set_collapsed(self, collapsed: bool) -> None:
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        self._apply()
        self.collapsed_changed.emit(self.collapsed)

    def _apply(self) -> None:
        collapsed = self.collapsed
        self._handle.setVisible(self._collapsible)
        self._handle.set_open(not collapsed)
        self._column.setVisible(not collapsed)
        width = RAIL_HANDLE_WIDTH if collapsed else (
            self._full_width + (RAIL_HANDLE_WIDTH if self._collapsible else 0))
        self.setFixedWidth(width)
