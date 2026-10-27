"""The 68-pixel band across the top: who you are, when it is, how you are doing.

Everything here is glanceable and none of it is interactive except the controls on the
right. The meters carry a delta against the previous turn because a number alone does
not tell you whether the year went well -- ``stability 42`` means nothing until you know
it was 46 in January.

**The bar has to fit the monitor the player owns.** It used to be built from three blocks
that could not shrink -- a fixed brand, a status block that insisted on every word of its
widest reading, and eighteen icon buttons in a row -- which added up to a 2,192 px floor
on a window asking for 1,600. On the most common desktop resolution the window was wider
than the screen. So every block here either elides or gives way: the brand asks for its
full width and accepts a third of it, the nation and the date elide, the meters have a
floor they can be squeezed to, and the controls are five buttons, one named control and
a menu rather than eighteen buttons -- and below a measured width the buttons themselves
step into the menu. What survives at the narrow end is the reading; what goes is the
decoration, in the order set out in :meth:`TopBar._apply_breakpoints`.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QAction, QFontMetrics
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QMenu,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..engine.snapshot import PlayerSnapshot
from ..humanize import BANDS, band, exact, figure, movement
from .theme import SPACE, TYPE, Theme
from .widgets import (
    Divider,
    Meter,
    PressFeedback,
    ShieldSwatch,
    TextLabel,
    control_qss,
    pressed_qss,
    restyle,
    scaled,
    styled_font,
    text_scale,
)

__all__ = ["TopBar"]

#: The width at which the whole bar reads: six meters with their labels in full, the
#: nation, the date and the day count, the world's name, every control, and the three
#: channel rows. Measured, by widening the bar until nothing elides. See
#: `TopBar._apply_breakpoints` for the order things come back in below it.
#:
#: **Both this and `_WORLD_NAME_FITS` moved up when Attention went on the glass**, by
#: about what the control costs. They are not preferences; they are the width at which
#: there is room, and a control that takes a hundred pixels moves every width there is
#: room at. Left where they were, the channel rows came back at 1700 and the world's name
#: at 1450 into a bar that no longer had the room for either, and the meters paid --
#: which is the exact failure the original measurement was made to stop.
_CHANNELS_FIT = 1920

#: And the width at which everything but the channels does. Below it the brand gives up
#: the world's name and shrinks to its mark, which buys the meters their labels back.
#:
#: Worth knowing what this fixed on its way past: at 1450 the *old* value was already a
#: few pixels early, so LEGITIMACY and MANPOWER were cut between 1450 and 1490 on the bar
#: as it stood before any of this. Nothing caught it because the viewport matrix steps
#: from 1366 to 1536 and the band falls between them.
_WORLD_NAME_FITS = 1500

#: The brand block matches the left rail so the two edges line up. Without the name it is
#: the mark, the wordmark and the margins.
_BRAND_WIDE, _BRAND_NARROW = 348, 114

#: The width at which the bar can carry Attention *and* every glyph button without a meter
#: giving up its label.
#:
#: **This is the number the whole placement turns on.** Attention is a named control with a
#: count on it, which is about 105 px against the 34 a sixth glyph would have cost -- and at
#: 1280x720 the meters were already holding LEGITIMACY and MANPOWER by a few pixels. So the
#: bar cannot have both below this width, and the question is only which gives way. It is
#: not the meters: a meter whose label reads ``LEGITIM...`` is a number nobody can name, and
#: there are six of them. It is not Attention either, which is the control this bar exists
#: to put in front of somebody who has never played it. It is the glyphs that already have
#: named lines waiting for them behind the door -- see `TopBar._overflow`.
#:
#: Measured, not chosen: on the assembled window, at every width in the viewport matrix and
#: at the boundaries either side of the two breakpoints above, on both palettes. A bare
#: ``TopBar`` resized on its own settles differently and reports elisions the real window
#: does not have, which is why the measurement was taken through `AtlasWindow`.
#: ``test_the_chrome_reads_in_full_at_every_viewport`` and
#: ``test_attention_never_costs_a_meter_its_label`` are what hold it.
_ALL_CONTROLS_FIT = 1600

#: What the clock block asks for with the day count in it, and without.
#:
#: **The day count is the last decoration on this bar and it was never giving way.** A
#: turn-zero world says *the world begins* in a block that asks for 236 px; fourteen turns
#: in it says *25 February 2027* and *420 days elapsed*, and the block holds all 236 --
#: which comes out of the meters, because they are the block beside it that stretches.
#: That is why the first cut of this change measured clean on a new world and elided
#: LEGITIMACY on a played one.
#:
#: The block already knows how to drop the day count: `_Flexible.shed` does it below 226.
#: What was missing is anything ever *asking* it for less. Below :data:`_DAY_COUNT_FITS`
#: it asks for the narrow figure, the day count goes, and the meters have the difference.
#: The date and the turn number stay at every width: they are the reading.
_CLOCK_WIDE, _CLOCK_NARROW = 236, 148

#: The width below which the day count goes. Above it there is room for the whole clock
#: *and* everything else; below it the day count is what the bar can most afford to lose,
#: which is the same judgement `_apply_breakpoints` makes about the channel rows and the
#: world's name, one rung further down.
_DAY_COUNT_FITS = 1800

#: The width below which the date is written short -- *25 Feb 2027* rather than
#: *25 February 2027*.
#:
#: **The day count going was not enough, and the played-world sweep is what showed it.** A
#: turn-zero world says *1 January 1972*, which fits anywhere; fourteen turns into a modern
#: game it says *25 February 2027*, and that elided at every width from 1280 to 1799 -- on
#: the one block this bar is not allowed to clip, because the date *is* the reading. The
#: chrome test never saw it: its fixture is a turn-zero world whose date is short.
#:
#: A month abbreviated is still a date. A month cut in half is not -- *25 Februa...* is
#: worse than useless, because the reader cannot tell whether it is February or a month
#: they have not thought of. So the same width that takes the day count away also takes the
#: month's tail, and the block keeps a whole reading instead of a clipped one.
_DATE_IN_FULL = 1800

#: The width below which meter captions are written short -- *LEGIT* rather than
#: *LEGITIMACY*.
#:
#: At 1100, the declared `MIN_WINDOW_WIDTH`, four of the six captions were being clipped:
#: `STABILITY`, `LEGITIMACY`, `TREASURY` and `MANPOWER` all became `...`-tailed stubs, and
#: the nation's name went with them. That is the window a player is *allowed* to make, so
#: it has to read. The abbreviations are the ones a player already meets in the game's own
#: shorthand, and the accessible name keeps the whole word.
_METER_LABELS_FIT = 1280

#: What each meter is called when there is not room for its name. Keyed by the label
#: `atlas.engine.snapshot.METERS` gives, which is UI-facing only -- no briefing, no prompt
#: and no recorded text reads these, so shortening them cannot move a tape.
_SHORT_METER_LABELS = {
    "Military": "MIL", "Economy": "ECON", "Stability": "STAB",
    "Legitimacy": "LEGIT", "Treasury": "TREAS", "Manpower": "MEN",
}

#: And the width below which *what needs you* goes behind the door as well. It is the last
#: to go and the first to come back: it is the only other control on the bar that answers a
#: question a player has at that moment, and it is the one Attention most nearly duplicates
#: -- the drawer's own footer says *what needs you has the rest* -- which is what makes it
#: the third to be spared rather than the first.
_ALERTS_FITS = 1536


class _IconButton(PressFeedback, QFrame):
    """A 30px bordered square. Icons are drawn as text glyphs to avoid an asset step.

    **Pick a glyph the vendored sans actually has.** There is no asset step and no icon
    font, so the glyph is set in IBM Plex Sans at ``body`` -- and Plex Sans covers Latin-1
    punctuation, the arrows, part of the geometric shapes block, and very little else. A
    symbol outside that renders as an empty box, silently and identically to the border
    already drawn around it, which is why a missing icon on this bar does not look missing:
    eleven of them were boxes for months and the bar looked deliberate.

    Every glyph on the bar is now one the vendored face carries, checked by rendering it
    and comparing the pixels against a private-use-area box -- which is what
    ``test_every_top_bar_glyph_is_in_the_vendored_face`` does, so the next symbol picked
    off a character map fails in CI rather than in a screenshot nobody looks at twice.
    """

    clicked = Signal()

    def __init__(self, glyph: str, tooltip: str, *, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(30, 30)
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip.split("  (")[0])
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.make_focusable()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._glyph = TextLabel(glyph, style="body", theme=theme)
        self._glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._glyph)
        self.apply_theme(theme)

    def set_glyph(self, glyph: str) -> None:
        self._glyph.setText(glyph)

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.setStyleSheet(control_qss("_IconButton", theme))
        self._glyph.set_colour("ink_dim" if self.isEnabled() else "ink_faint", theme)

    def _fire(self) -> None:
        self.clicked.emit()


class _CountedButton(PressFeedback, QFrame):
    """A control on the bar with its name on it, and a count of what is behind it.

    **The one control here that is not a glyph.** Every other button on this bar is a
    character in a box with the words in a tooltip, which is defensible for a door a player
    already knows -- *find*, *what needs you* -- and indefensible for the one that answers
    *what am I supposed to be doing*. That question is asked by somebody who has never seen
    this program, and a tooltip is not an answer to a question you do not know to ask.

    The count is the other half. A door marked *Attention* says there is a room; a door
    marked *Attention 3* says there is something in it, and it says how much without being
    opened -- which is what makes it worth glancing at between turns rather than a place to
    go once. Zero is shown as a word rather than as a nought, because *Attention 0* reads
    as a broken counter and *nothing waiting* reads as a fact.
    """

    clicked = Signal()

    def __init__(self, label: str, tooltip: str, *, theme: Theme,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._label_text = label
        self._count = 0
        self.setFixedHeight(30)
        self.setObjectName("countedButton")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tooltip = tooltip
        self.make_focusable()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE.sm + 1, 0, SPACE.sm + 1, 0)
        layout.setSpacing(SPACE.xs + 1)
        self._name = TextLabel(label, style="button", colour="ink_dim", theme=theme)
        self._tally = TextLabel("", style="num", colour="brass", theme=theme)
        layout.addWidget(self._name)
        layout.addWidget(self._tally)
        self.set_count(0)
        self.apply_theme(theme)

    def set_count(self, count: int) -> None:
        """How many matters are waiting, as of the last read of the ranking."""
        self._count = max(0, int(count))
        self._tally.setText(str(self._count) if self._count else "–")
        self._tally.set_colour("brass" if self._count else "ink_faint", self._theme)
        told = (
            f"{self._label_text}: nothing is waiting on you. {self._tooltip}"
            if not self._count else
            f"{self._label_text}: {self._count} matter"
            f"{'s' if self._count != 1 else ''} waiting. {self._tooltip}")
        self.setToolTip(told)
        self.setAccessibleName(
            f"{self._label_text}, {self._count} waiting" if self._count
            else f"{self._label_text}, nothing waiting")
        self.setAccessibleDescription(told)

    @property
    def count(self) -> int:
        return self._count

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.setStyleSheet(control_qss("QFrame#countedButton", theme))
        self._name.apply_theme(theme)
        self._tally.apply_theme(theme)
        self._tally.set_colour("brass" if self._count else "ink_faint", theme)

    def _fire(self) -> None:
        self.clicked.emit()


class _BarAction(QObject):
    """A control that used to be a glyph on the bar and is now a line in the More menu.

    It keeps the same shape as :class:`_IconButton` from the outside -- a ``clicked``
    signal and a ``_fire`` -- so the window wires it identically and nothing downstream
    has to know whether a control is a button or a menu entry. What it adds is the one
    thing an icon could never carry: a name. *Fork this world at a turn* is a sentence;
    the glyph it replaces was a guess.
    """

    clicked = Signal()

    def __init__(self, label: str, hint: str = "", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.label = label
        self.hint = hint
        self.action: QAction | None = None

    def set_label(self, label: str) -> None:
        self.label = label
        if self.action is not None:
            self.action.setText(label)

    def _fire(self) -> None:
        self.clicked.emit()


class _Flexible(QWidget):
    """A block that asks for one width and settles for whatever it is given.

    Qt sizes a plain container to its contents in both directions at once: the width it
    would like is also the width below which it refuses to go. That is what pinned this
    bar -- and through it the whole window -- to a width no common monitor has. Asking and
    insisting are separated here, so a block can prefer 348 px, be handed 150, and let its
    eliding children do the rest.
    """

    def __init__(self, preferred: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._preferred = preferred
        self._sheds: list[tuple[QWidget, int]] = []
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        return QSize(max(scaled(self._preferred), hint.width()), hint.height())

    def set_preferred(self, preferred: int) -> None:
        """Ask for less, so a neighbour that has something to say can have the room."""
        if preferred == self._preferred:
            return
        self._preferred = preferred
        self.updateGeometry()

    def shed(self, widget: QWidget, below: int) -> None:
        """Drop ``widget`` entirely once the block is narrower than ``below``.

        Eliding is right for a line that still says something when it is shortened. It is
        wrong for one that does not: *420 days el...* is not a shorter way of saying how
        long the world has run, it is the same line with the answer cut off. Those go
        away instead, and come back when there is room for the whole of them.

        Calling this again for the same widget replaces the threshold, which is how a
        line whose length depends on its text -- a government is *the government* or it
        is *Donald Trump, President* -- gets a threshold measured from the words actually
        in it rather than from the longest one anybody thought of.
        """
        self._sheds = [entry for entry in self._sheds if entry[0] is not widget]
        self._sheds.append((widget, below))
        widget.setVisible(self.width() >= scaled(below))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        for widget, below in self._sheds:
            widget.setVisible(self.width() >= scaled(below))


class _AskableMeter(PressFeedback, Meter):
    """A top-bar meter that answers for itself.

    The delta has been on the glass since the first build and is half a sentence: *down 4*
    is a fact with no reason attached, and the reason has been in the database the whole
    time. The meter is the place a player is standing when they want it, so it is the place
    the question gets asked from.

    Nothing else about the meter changes -- it is the same widget drawing the same number,
    with a cursor, a tooltip and a press.

    **It is also where the rounding gives the number back.** Fifty-two pixels hold a figure
    and nothing else, so the meter shows ``47`` where the database holds
    ``47.269999999999996`` -- and the hover carries the whole of what was dropped: the exact
    reading, the scale it sits on, the word for where that is, and how far it moved. See
    `atlas.humanize`.
    """

    asked = Signal(str)

    def __init__(self, key: str, label: str, *, theme: Theme,
                 parent: QWidget | None = None) -> None:
        super().__init__(label, theme=theme, parent=parent)
        self._key = key
        self._label_text = label
        self.setObjectName("askableMeter")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._say(None)
        self.make_focusable()

    def set_value(self, value, pct, colour, *, delta=None, places: int = 0) -> None:
        # The plain reading tints the word; a number drawn after it must not inherit that.
        self._value.set_colour("ink", self._theme)
        self._value.set_style("num", self._theme)
        super().set_value(value, pct, colour, delta=delta, places=places)

    def read(self, value: float, ceiling: float, delta: float) -> None:
        """Told what it is drawing, so the hover can say what the glass cannot."""
        self._say((value, ceiling, delta))
        from .plain import technical

        if not technical():
            self._plainly(value, ceiling, delta)

    def _plainly(self, value: float, ceiling: float, delta: float) -> None:
        """The reading in words: *Modest*, and underneath *42 ↓*.

        ``economy 29 / +0.4`` asked a player to know the scale, and where on it trouble
        starts. The word is the band the hover has always named (`humanize.BANDS`), in the
        danger ink when it is one of the bottom two; the figure stays, smaller, beside the
        way it moved this turn. Exact numbers are still on the hover.
        """
        word = band(self._key, value, ceiling=ceiling)
        if not word:
            return
        table = [name for _edge, name in BANDS.get(self._key, ())]
        low = word in table[:2]
        # A word in the text face: the figures' monospace set "Stretched" too wide to fit.
        self._value.set_style("body_small", self._theme)
        self._value.setText(word[:1].upper() + word[1:])
        self._value.set_colour("danger" if low else "ink", self._theme)
        moved = movement(delta)
        arrow = "" if moved == "steady" else (" \u2191" if delta > 0 else " \u2193")
        self._delta.setText(f"{figure(value)}{arrow}")
        self._delta.set_colour(
            "ink_ghost" if not arrow else ("good" if delta > 0 else "danger"), self._theme)

    def _say(self, reading: tuple[float, float, float] | None) -> None:
        question = f"Why is {self._label_text.lower()} where it is?"
        if reading is None:
            self.setToolTip(question)
            self.setAccessibleDescription(question)
            return
        value, ceiling, delta = reading
        word = band(self._key, value, ceiling=ceiling)
        # "of 150" on a treasury would be a maximum, and there is not one -- the ceiling is
        # what the bar is drawn against and the sentence says so rather than implying a cap.
        scale = (f" of {ceiling:g}" if ceiling and ceiling <= 100.0
                 else f" (the bar runs to {ceiling:g})")
        moved = movement(delta)
        told = (f"{self._label_text}: {exact(value)}{scale}"
                + (f" — {word}" if word else "")
                + ". " + ("Unmoved this turn." if moved == "steady"
                          else f"{moved} this turn.")
                + f"  {question}")
        self.setToolTip(told)
        self.setAccessibleDescription(told)

    def _fire(self) -> None:
        self.asked.emit(self._key)

    def apply_theme(self, theme: Theme) -> None:
        super().apply_theme(theme)
        self.setStyleSheet(pressed_qss("QWidget#askableMeter", theme))


class TopBar(QFrame):
    #: The opening question, and the way back to it: what has the strongest claim on this
    #: government right now. First on the bar because it is where a new player starts.
    matters_requested = Signal()
    search_requested = Signal()
    watchlist_requested = Signal()
    alerts_requested = Signal()
    branches_requested = Signal()
    advance_requested = Signal()
    workbench_requested = Signal()
    strategy_requested = Signal()
    directives_requested = Signal()
    homefront_requested = Signal()
    #: The economy read as a dependency: who supplies this government, what it costs, what
    #: stops if a lane does. Beside the home front rather than under *the world*, because
    #: it is a reading of this player's own position and not a survey of anybody else's.
    dependency_requested = Signal()
    inspector_requested = Signal()
    dispatch_requested = Signal()
    cast_requested = Signal()
    correspondence_requested = Signal()
    council_requested = Signal()
    #: A meter the player asked about, by field name. See `_AskableMeter`.
    meter_asked = Signal(str)
    rewind_requested = Signal()
    #: Go back one turn: the rewind path with the turn already chosen.
    undo_turn_requested = Signal()
    export_requested = Signal()
    theme_toggled = Signal()

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self.setFixedHeight(68)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_brand())
        outer.addWidget(Divider(vertical=True, theme=theme))
        outer.addWidget(self._build_status(), 1)
        outer.addWidget(Divider(vertical=True, theme=theme))
        outer.addWidget(self._build_controls())

        self.apply_theme(theme)
        self._apply_breakpoints(self.width())

    # -- construction ------------------------------------------------------

    def _build_brand(self) -> QWidget:
        holder = _Flexible(348)
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(SPACE.lg, 0, SPACE.lg, 0)
        layout.setSpacing(SPACE.sm)

        self._mark = _BrandMark(self._theme)
        layout.addWidget(self._mark)

        self._wordmark = TextLabel("ATLAS", style="brand", colour="brass", theme=self._theme)
        layout.addWidget(self._wordmark)
        self._brand_rule = Divider(vertical=True, length=20, theme=self._theme)
        layout.addWidget(self._brand_rule)

        # The world's name and scenario. Elided rather than fixed: they are the first
        # thing the bar can afford to shorten, and the last thing it should clip.
        self._names = QWidget()
        names = QVBoxLayout(self._names)
        names.setContentsMargins(0, 0, 0, 0)
        names.setSpacing(3)
        self._world_id = TextLabel("", style="mono", colour="ink_dim", theme=self._theme,
                                   elide=True)
        self._scenario = TextLabel("", style="label", colour="ink_faint", theme=self._theme,
                                   elide=True)
        names.addWidget(self._world_id)
        names.addWidget(self._scenario)
        self._names.setMinimumWidth(0)
        layout.addWidget(self._names, 1)
        self._brand = holder
        return holder

    def _build_status(self) -> QWidget:
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(SPACE.lg, 0, SPACE.md, 0)
        layout.setSpacing(SPACE.lg)

        self._nation_block = _Flexible(176)
        nation = QHBoxLayout(self._nation_block)
        nation.setContentsMargins(0, 0, 0, 0)
        nation.setSpacing(SPACE.sm)
        self._shield = ShieldSwatch("#3B6EA5", width=26, height=33)
        nation.addWidget(self._shield)
        stack = QVBoxLayout()
        stack.setSpacing(3)
        self._nation_name = TextLabel("", style="nation", colour="ink_bright",
                                      theme=self._theme, elide=True)
        self._nation_role = TextLabel("", style="label", colour="ink_dim",
                                      theme=self._theme, elide=True)
        stack.addWidget(self._nation_name)
        stack.addWidget(self._nation_role)
        nation.addLayout(stack, 1)
        # Enough for the flag and a country a player would recognise from it. Below this
        # the name stops being a name -- *Unite...* -- and the block is worth less than
        # the meters it is taking room from.
        self._nation_block.setMinimumWidth(150)
        # A share of whatever the bar has spare, alongside the meters. On a wide screen
        # that is enough for "the Union of Soviet Socialist Republics" to read in full;
        # on a narrow one the name elides and the meters keep their numbers, which is the
        # right way round -- a shortened country is still recognisable, half a number is
        # not.
        layout.addWidget(self._nation_block, 1)

        self._nation_rule = Divider(vertical=True, length=34, theme=self._theme)
        layout.addWidget(self._nation_rule)

        self._clock_block = _Flexible(236)
        clock = QVBoxLayout(self._clock_block)
        clock.setContentsMargins(0, 0, 0, 0)
        clock.setSpacing(4)
        self._date = TextLabel("", style="date", colour="ink", theme=self._theme, elide=True)
        turn_row = QHBoxLayout()
        turn_row.setSpacing(7)
        self._turn = TextLabel("", style="num", colour="brass", theme=self._theme)
        self._elapsed = TextLabel("", style="mono", colour="ink_faint", theme=self._theme,
                                  elide=True)
        turn_row.addWidget(self._turn)
        turn_row.addWidget(self._elapsed, 1)
        clock.addWidget(self._date)
        clock.addLayout(turn_row)
        self._clock_block.setMinimumWidth(96)
        self._clock_block.shed(self._elapsed, 226)
        self._clock_block.setSizePolicy(QSizePolicy.Policy.Maximum,
                                        QSizePolicy.Policy.Preferred)
        layout.addWidget(self._clock_block)

        self._clock_rule = Divider(vertical=True, length=34, theme=self._theme)
        layout.addWidget(self._clock_rule)

        meters = QHBoxLayout()
        meters.setSpacing(SPACE.sm)
        self._meters: dict[str, Meter] = {}
        self._meter_labels: dict[str, tuple[str, str]] = {}
        for key, label, _ceiling in _meter_defs():
            meter = _AskableMeter(key, label, theme=self._theme)
            # The accessible name is the *whole* word at every width. A screen reader has no
            # width problem, and the short form is a concession to pixels rather than a
            # rename.
            meter.setAccessibleName(label)
            meter.asked.connect(self.meter_asked)
            self._meters[key] = meter
            self._meter_labels[key] = (
                label.upper(), _SHORT_METER_LABELS.get(label, label).upper())
            meters.addWidget(meter, 1)
        layout.addLayout(meters, 2)
        return holder

    def _build_controls(self) -> QWidget:
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(SPACE.md, 0, SPACE.md, 0)
        layout.setSpacing(SPACE.sm)

        self._providers = _Flexible(190)
        self._providers_box = QVBoxLayout(self._providers)
        self._providers_box.setContentsMargins(0, 0, 0, 0)
        self._providers_box.setSpacing(5)
        self._providers.setMinimumWidth(0)
        layout.addWidget(self._providers)
        self._provider_rows: list[tuple[_Dot, TextLabel, TextLabel]] = []

        # -- the five that stay on the glass -------------------------------
        #
        # Four surfaces and a door to the rest. Eighteen unlabelled glyphs was not a
        # navigation bar, it was a character map: nothing on it was guessable, all of it
        # was tooltip-only, and it cost more width than the window had. What is left is
        # what a player reaches for between turns -- find something, see what needs them,
        # see where they stand, check what they have written -- and everything else is a
        # named line one press away.
        buttons = QHBoxLayout()
        buttons.setSpacing(4)
        self._search = _IconButton(
            "¤", "Find a province, a power, an event or a treaty  (Ctrl+F)",
            theme=self._theme)
        self._alerts = _IconButton(
            "!", "What needs you", theme=self._theme)
        self._strategy = _IconButton(
            "№", "Standing, score and victory conditions", theme=self._theme)
        self._workbench = _IconButton(
            "✓", "Check the orders you have written", theme=self._theme)
        self._more = _IconButton(
            "…", "Everything else", theme=self._theme)
        self._more.setAccessibleDescription(
            "Opens a menu of the panels not on the bar")
        #: The opening question, given a name and a number. First of the controls because
        #: it is where a new player starts, and it opens the drawer rather than a sheet --
        #: see :class:`atlas.ui.matters.MattersDrawer`.
        self._attention = _CountedButton(
            "Attention", "What is waiting: the strongest claims on you right now",
            theme=self._theme)
        self._attention.clicked.connect(self.matters_requested.emit)
        # **The reduced bar still wins this conflict, and Attention is not an exception to
        # it.** The branch that added *what is waiting* built a row of eleven glyphs, which
        # would have restored the 2,192px floor that does not fit the monitor most people
        # own. What is on the glass now is five glyphs and one named control, and the named
        # control is paid for out of the glyphs rather than out of the meters -- see
        # `_ALL_CONTROLS_FIT`. Nothing here has a wider floor than it had.
        self._search.clicked.connect(self.search_requested.emit)
        self._alerts.clicked.connect(self.alerts_requested.emit)
        self._strategy.clicked.connect(self.strategy_requested.emit)
        self._workbench.clicked.connect(self.workbench_requested.emit)
        self._more.clicked.connect(self._show_more)
        buttons.addWidget(self._attention)
        for button in (self._search, self._alerts, self._strategy, self._workbench,
                       self._more):
            buttons.addWidget(button)
        layout.addLayout(buttons)

        # -- and the ones behind the door ----------------------------------
        #
        # **The design call the last pass left open has been made: *what is waiting* is on
        # the glass.** It sat here as a named line because a sixth *glyph* cost about 40 px
        # that the meters at 1280x720 were only just holding their labels with. That was a
        # true measurement of the wrong control. As a named control with a count it costs
        # more, not less -- and it is still worth it, because it is the one door on this bar
        # a player who has never seen the program needs, and the controls it displaces
        # below :data:`_ALL_CONTROLS_FIT` are ones that read better as words anyway.
        self._dispatch = _BarAction("The dispatch", "this turn across the world", self)
        self._cast = _BarAction("The cast", "the powers you are watching", self)
        self._post = _BarAction("Correspondence", "what the powers said to you", self)
        self._council = _BarAction("Ask your staff", "a question to your own government", self)
        self._directives = _BarAction(
            "Directives", "what this government is trying to do", self)
        self._homefront = _BarAction("The home front", "who you have to govern", self)
        self._dependency = _BarAction(
            "Who supplies you", "the economy you depend on", self)
        self._watchlist = _BarAction("Watchlist", "what you are keeping an eye on", self)
        self._inspector = _BarAction("What happened, and why", parent=self)
        self._advance = _BarAction("Advance to the next event", parent=self)
        self._undo = _BarAction("Go back a turn", "undo the turn just played", self)
        self._rewind = _BarAction("Rewind to an earlier turn", parent=self)
        self._branches = _BarAction("Fork this world at a turn", parent=self)
        self._export = _BarAction("Export a copy", parent=self)
        self._theme_btn = _BarAction(_theme_label(self._theme), parent=self)

        self._dispatch.clicked.connect(self.dispatch_requested.emit)
        self._cast.clicked.connect(self.cast_requested.emit)
        self._post.clicked.connect(self.correspondence_requested.emit)
        self._council.clicked.connect(self.council_requested.emit)
        self._directives.clicked.connect(self.directives_requested.emit)
        self._homefront.clicked.connect(self.homefront_requested.emit)
        self._dependency.clicked.connect(self.dependency_requested.emit)
        self._watchlist.clicked.connect(self.watchlist_requested.emit)
        self._inspector.clicked.connect(self.inspector_requested.emit)
        self._advance.clicked.connect(self.advance_requested.emit)
        self._undo.clicked.connect(self.undo_turn_requested.emit)
        self._rewind.clicked.connect(self.rewind_requested.emit)
        self._branches.clicked.connect(self.branches_requested.emit)
        self._export.clicked.connect(self.export_requested.emit)
        self._theme_btn.clicked.connect(self.theme_toggled.emit)

        #: The menu, in the order a player thinks in: the world, then your government,
        #: then the turn, then the file on disk.
        self._more_groups: list[tuple[str, list[_BarAction]]] = [
            ("The world", [self._dispatch, self._cast, self._post, self._council]),
            ("Your government", [self._directives, self._homefront, self._dependency,
                                 self._watchlist, self._inspector]),
            ("The turn", [self._advance, self._undo, self._rewind]),
            ("This world", [self._branches, self._export, self._theme_btn]),
        ]

        # -- and the two that step behind it when the bar narrows ----------
        #
        # **A control pushed off the glass keeps working; it stops being a picture.** Each
        # of these is a glyph above :data:`_ALL_CONTROLS_FIT` and a *named line* below it,
        # firing the same signal either way, so the window wires one control and never
        # learns which shape it currently has. The order is the order they can be spared:
        # the orders check is a thing a player does once a turn at a moment they have
        # chosen, and standing is a reading rather than an act. Find and what-needs-you
        # stay, because both are how a player answers a question they have this second.
        self._checked = _BarAction(
            "Check the orders you have written", "before the turn is spent", self)
        self._standing = _BarAction(
            "Standing, score and victory conditions", "where you are, and against what",
            self)
        self._needed = _BarAction(
            "What needs you", "everything the ranking found, not just the top of it", self)
        self._checked.clicked.connect(self.workbench_requested.emit)
        self._standing.clicked.connect(self.strategy_requested.emit)
        self._needed.clicked.connect(self.alerts_requested.emit)
        #: ``(button, its named line, the width below which it steps behind the door)``,
        #: widest threshold first so that what is shed is always a prefix of this.
        self._overflow: tuple[tuple[_IconButton, _BarAction, int], ...] = (
            (self._workbench, self._checked, _ALL_CONTROLS_FIT),
            (self._strategy, self._standing, _ALL_CONTROLS_FIT),
            (self._alerts, self._needed, _ALERTS_FITS),
        )
        #: How many of :attr:`_overflow` are behind the door at the current width.
        self._shed_controls = 0
        #: Whether the bar is wide enough for the whole date and the whole captions.
        #: Both start at the wide reading and are corrected by the first resize.
        self._date_iso = ""
        self._date_in_full = True
        self._meter_labels_in_full = True

        self._more_menu = QMenu(self)
        self._build_more_menu()
        return holder

    def _menu_groups(self) -> list[tuple[str, list[_BarAction]]]:
        """The permanent named lines, with whatever the bar has just shed in front.

        Shed controls go first and under their own heading, because they are the ones a
        player has just watched disappear: a control that was on the bar a moment ago and
        is now filed under *Your government* has been hidden, not moved.
        """
        shed = [entry[1] for entry in self._overflow[:self._shed_controls]]
        if not shed:
            return list(self._more_groups)
        return [("Off the bar at this width", shed), *self._more_groups]

    def _build_more_menu(self) -> None:
        self._more_menu.clear()
        for index, (heading, entries) in enumerate(self._menu_groups()):
            if index:
                self._more_menu.addSeparator()
            # A disabled entry rather than `addSection`: Qt's section header draws its
            # text in some styles and silently drops it in others, and on Windows it
            # drops it -- which left four unexplained rules in the middle of a menu.
            title = self._more_menu.addAction(heading)
            title.setEnabled(False)
            title.setFont(styled_font(TYPE["section"]))
            for entry in entries:
                action = self._more_menu.addAction(entry.label)
                action.setFont(styled_font(TYPE["body_small"]))
                if entry.hint:
                    action.setToolTip(entry.hint)
                    # Read out as well as shown: the label says what the entry is and the
                    # hint says what it is for, and a menu read aloud should carry both.
                    action.setStatusTip(entry.hint)
                    action.setWhatsThis(entry.hint)
                action.triggered.connect(entry._fire)
                entry.action = action

    def _show_more(self) -> None:
        corner = self._more.mapToGlobal(self._more.rect().bottomRight())
        width = self._more_menu.sizeHint().width()
        self._more_menu.popup(QPoint(corner.x() - width, corner.y() + SPACE.xs))

    # -- responsiveness ----------------------------------------------------

    def resizeEvent(self, event) -> None:
        # Before the layout runs, not after: hiding the channel block frees width the
        # blocks beside it can then be laid out with. Done the other way round it takes
        # a second pass, and what is on the glass in between is a bar laid out for a
        # width it no longer has.
        self._apply_breakpoints(event.size().width())
        super().resizeEvent(event)

    def _apply_breakpoints(self, width: int) -> None:
        """Shed decoration as the bar narrows, in the order it can be spared.

        This is not load-bearing for the minimum -- every block on the bar elides or
        sheds, so it *fits* at any width. It is about what is worth showing, and the order
        is the order of what a player is actually reading.

        **Every threshold here is a measured width, not a round number.** Each one is the
        point at which everything of higher priority is at its *full reading* -- meters
        whose labels say ``LEGITIMACY`` rather than ``LEGITIM...``, a nation whose name
        says ``United States`` rather than ``United St...``. Set them by eye and you get
        what this bar had: nothing elided at 1536 and six things elided at 1600, because
        the channel rows appeared 100px before there was room for them.

        Going down: the three channel rows first, since they are the only block here
        nobody reads between turns. Then the day count, then the world's name and the
        scenario -- which are on the placard over the map as well. Then the controls in
        `_overflow`, one at a time and lowest-ranked first, into the More menu. The
        wordmark stays: a program with no name on it looks like a screenshot of part of
        one, and Attention stays: it is what the rest of this is giving way for.

        **The day count moved ahead of the world's name in that order, and not on taste.**
        Both are secondary and either could reasonably go first. What decides it is where
        the room lands: the clock block sits *inside* the status holder, between the nation
        and the meters, so what it gives up goes straight to them. The brand is outside
        that holder, so narrowing it hands room to the status block as a whole, which then
        divides it by stretch and the meters get a share. When the block that has to be
        rescued is the meters, the day count is worth about twice what the world's name is
        -- which is why the played-world sweep in `scratchpad/probe_topbar_recorded.py`
        goes green on the first and not on the second.
        """
        self._providers.setVisible(width >= scaled(_CHANNELS_FIT))
        wide = width >= scaled(_WORLD_NAME_FITS)
        self._names.setVisible(wide)
        self._brand_rule.setVisible(wide)
        self._brand.set_preferred(_BRAND_WIDE if wide else _BRAND_NARROW)
        # Then the day count, which is the last thing on this bar that is decoration. See
        # `_CLOCK_WIDE`.
        self._clock_block.set_preferred(
            _CLOCK_WIDE if width >= scaled(_DAY_COUNT_FITS) else _CLOCK_NARROW)
        # The date is written short at the same width, because the block that has just been
        # narrowed is the block the date sits in. See `_DATE_IN_FULL`.
        self._date_in_full = width >= scaled(_DATE_IN_FULL)
        self._render_date()
        # And the captions, which are the last thing on the bar that was still being clipped
        # at the narrowest window a player is allowed to make. See `_METER_LABELS_FIT`.
        full_labels = width >= scaled(_METER_LABELS_FIT)
        if full_labels != self._meter_labels_in_full:
            self._meter_labels_in_full = full_labels
            for key, meter in self._meters.items():
                long_form, short_form = self._meter_labels[key]
                meter.set_label(long_form if full_labels else short_form)

        # And then the controls, which are the one thing on this bar that gives way *for*
        # the meters rather than alongside them. Each glyph in `_overflow` steps behind the
        # More menu below its own measured width and keeps its signal either way -- which
        # buys back the room Attention costs, so a 1280x720 window has six meters that say
        # LEGITIMACY *and* a control that says what is waiting, instead of one or the other.
        self._shed(sum(1 for entry in self._overflow if width < scaled(entry[2])))

    def _render_date(self) -> None:
        """Write the date in whichever form the current width can hold in full."""
        self._date.setText(_pretty_date(self._date_iso, short=not self._date_in_full))

    def _shed(self, count: int) -> None:
        if count == self._shed_controls:
            return
        self._shed_controls = count
        for index, entry in enumerate(self._overflow):
            entry[0].setVisible(index >= count)
        self._build_more_menu()

    # -- data --------------------------------------------------------------

    def set_attention(self, waiting: int) -> None:
        """How many matters are waiting, off the same ranking the drawer lays out.

        Handed in rather than read here: this bar reads no database, and a second call to
        the substrate would be a second answer that could disagree with the drawer's.
        """
        self._attention.set_count(waiting)

    @property
    def attention_count(self) -> int:
        return self._attention.count

    def set_world(self, world_id: str, scenario: str) -> None:
        self._world_id.setText(world_id)
        self._scenario.setText(scenario)

    def set_snapshot(self, snapshot: PlayerSnapshot) -> None:
        self._date_iso = snapshot.world_date
        self._render_date()
        self._turn.setText(f"TURN {snapshot.turn:02d}")
        self._elapsed.setText(
            f"· {snapshot.days_elapsed} days elapsed" if snapshot.days_elapsed else "· the world begins"
        )

        if snapshot.playing:
            self._shield.set_colour(snapshot.colour)
            self._shield.setVisible(True)
            self._nation_name.setText(snapshot.name)
            # Who you are, not that you are playing. "playing" was true and told the
            # player nothing they did not already know; the government's name is the one
            # line that makes a coup something you notice happening *to you*.
            self._nation_role.setText(snapshot.government or "playing")
            self._shed_the_role_when_it_would_not_fit()
        else:
            self._shield.setVisible(False)
            self._nation_name.setText("Spectating")
            self._nation_role.setText("no nation")
            self._shed_the_role_when_it_would_not_fit()

        for meter in snapshot.meters:
            widget = self._meters.get(meter.key)
            if widget is None:
                continue
            widget.set_value(
                meter.value,
                meter.fraction,
                self._theme.meter_colour(meter.key.replace("_strength", "")),
                delta=meter.delta,
            )
            widget.read(meter.value, meter.ceiling, meter.delta)

    def _shed_the_role_when_it_would_not_fit(self) -> None:
        """The government's name goes away rather than being cut in half.

        *Donald Trump, Presi...* is the only kind of shortening this bar does not do
        anywhere else, and the line it happens to is the one that makes a coup something
        a player notices happening to them. Measured from the words in it, because the
        line is four characters long under some governments and twenty-three under
        others. It comes back as a tooltip -- see `widgets.TextLabel`.
        """
        metrics = QFontMetrics(self._nation_role.font())
        text = self._nation_role.text().upper()
        # Already in scaled pixels -- the metrics come from the font as it is set.
        needed = self._shield.width() + SPACE.sm + metrics.horizontalAdvance(text)
        self._nation_block.shed(self._nation_role, needed / max(text_scale(), 0.01))

    def set_providers(self, rows: list[tuple[str, str, str]]) -> None:
        """``(channel, condition, state)`` where state is ok / warn / down.

        The three lines the world's news actually reaches this government over, and
        whether each is carrying. What they are underneath is not this bar's business and
        is not on it: the words are composed once, in
        :meth:`atlas.ui.main_window.AtlasWindow._provider_rows`, and this only sets them.
        """
        while self._providers_box.count():
            item = self._providers_box.takeAt(0)
            if item.layout() is not None:
                while item.layout().count():
                    child = item.layout().takeAt(0)
                    if child.widget():
                        child.widget().deleteLater()
        self._provider_rows.clear()

        for name, condition, state in rows:
            row = QHBoxLayout()
            row.setSpacing(7)
            dot = _Dot(self._theme, state)
            # Two parts name to one part condition, in whatever width the block has
            # left. Fixed 104/58 columns were part of what made this bar unshrinkable,
            # and the name is the whole meaning of the row -- so it takes the larger
            # share of the room and both give way rather than clip.
            label = TextLabel(name, style="label", colour="ink_faint", theme=self._theme,
                              elide=True)
            label.setMinimumWidth(0)
            model_label = TextLabel(condition, style="mono_small", colour="ink_faint",
                                    theme=self._theme, elide=True)
            model_label.setMinimumWidth(0)
            row.addWidget(dot)
            row.addWidget(label, 2)
            row.addWidget(model_label, 1)
            self._providers_box.addLayout(row)
            self._provider_rows.append((dot, label, model_label))

    # -- theme -------------------------------------------------------------

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.setStyleSheet(
            f"TopBar {{ background: {theme.panel}; border-bottom: 1px solid {theme.rule}; }}"
        )
        restyle(self, theme)
        self._more_menu.setStyleSheet(_menu_qss(theme))
        self._theme_btn.set_label(_theme_label(theme))


class _BrandMark(QWidget):
    """The square-in-square mark. Drawn rather than shipped as an asset."""

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self.setFixedSize(22, 22)

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.update()

    def paintEvent(self, _event) -> None:
        from PySide6.QtGui import QColor, QPainter, QPen

        painter = QPainter(self)
        pen = QPen(QColor(self._theme.brass))
        pen.setWidthF(1.5)
        painter.setPen(pen)
        painter.drawRect(1, 1, self.width() - 2, self.height() - 2)
        painter.fillRect(
            self.width() // 2 - 3, self.height() // 2 - 3, 7, 7, QColor(self._theme.brass)
        )
        painter.end()


class _Dot(QWidget):
    """A 5px status dot: green healthy, brass degraded, red unavailable."""

    def __init__(self, theme: Theme, state: str = "ok", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._state = state
        self.setFixedSize(5, 5)

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


def _theme_label(theme: Theme) -> str:
    """What the theme line offers, rather than what the theme is.

    As a glyph it was a circle that filled in, which said nothing about which way the
    press went. As a sentence it can only be read one way.
    """
    return "Switch to the light theme" if theme.is_dark else "Switch to the dark theme"


def _menu_qss(theme: Theme) -> str:
    """The More menu in the interface's own palette.

    Qt draws a menu in the platform style, which on Windows is a white sheet with a blue
    highlight -- the one piece of foreign chrome in the program, and it would open over
    the darkest part of it.
    """
    return (
        f"QMenu {{ background: {theme.panel}; border: 1px solid {theme.rule};"
        f" color: {theme.ink}; padding: {SPACE.sm}px 0; }}"
        f"QMenu::item {{ padding: {SPACE.xs + 1}px {SPACE.xl}px {SPACE.xs + 1}px"
        f" {SPACE.lg}px; color: {theme.ink}; }}"
        f"QMenu::item:selected {{ background: {theme.brass_wash}; color: {theme.brass}; }}"
        f"QMenu::item:disabled {{ color: {theme.ink_faint};"
        f" padding-top: {SPACE.md}px; padding-bottom: {SPACE.xs}px; }}"
        f"QMenu::separator {{ height: 1px; background: {theme.rule_soft};"
        f" margin: {SPACE.xs}px {SPACE.md}px; }}"
        f"QMenu::icon {{ padding-left: {SPACE.md}px; }}"
    )


def _meter_defs():
    from ..engine.snapshot import METERS

    return METERS


def _pretty_date(iso: str, *, short: bool = False) -> str:
    """``1936-03-07`` becomes ``7 March 1936``. The design writes dates like a historian.

    ``short=True`` abbreviates the month -- ``7 Mar 1936`` -- for a bar too narrow to hold
    the name. A shortened month is still a date; a clipped one is a guess.
    """
    from datetime import date

    try:
        parsed = date.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso or ""
    months = (
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    )
    name = months[parsed.month - 1]
    # **May is left alone.** Abbreviating a month that is already three letters buys no
    # pixels and costs the reader a full stop that means nothing.
    if short and len(name) > 3:
        name = name[:3]
    return f"{parsed.day} {name} {parsed.year}"
