"""The air situation over one theatre: who holds it, what that is doing, and what to do.

**One surface, not an encyclopedia page.** The air layer has five separate effects, an
interception term, a decay, a reach model and a strike mechanic, and every one of them was
told to the model and to nobody else. The temptation is to put all of it on a page; the
result of that would be a rules dump nobody reads sitting beside a war nobody understands.
So this answers six questions about *this theatre* in the order a staff asks them --

1. who holds the sky here, and when that may lapse;
2. which forces put it there;
3. what it is doing to the land battle, the fleet action, the advance and the supply;
4. what is being interdicted, and which corridor has actually gone;
5. what can be done: contest, strike, reinforce, or accept it;

-- and the complete rules go behind *How the air war works*, which is the reference entry
and is deliberately the last thing on the panel rather than the first.

**Nothing is computed here.** Every figure comes from
:func:`atlas.engine.airsituation.air_situation`, which is a reader over the constants the
engine itself uses. This file lays out what that returned and connects a click to an order;
it has no arithmetic of its own and no second opinion about any number on it. Opening it
cannot change a save, which is asserted rather than asserted-in-a-comment -- see
``tests/test_the_air_situation_is_a_reading.py``.

**Raw rolls are not on it.** The air war is decided by a seeded draw per contested region
per turn, and showing that would be showing the die rather than the position. What is shown
is the adjudicated state -- holder, margin, band -- and the modifiers it produces. Exact
constants live on the tooltips and in the rules section, where somebody who wants them can
go and get them, and where they are not in the way of the four sentences that matter.
"""

from __future__ import annotations

import sqlite3

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..engine.airsituation import AirAction, AirSituation, air_rules, air_situation
from .overlays import Overlay
from .theme import PANEL, PANEL_INSET, SPACE, Theme
from .widgets import (
    Chip,
    Divider,
    PressFeedback,
    SectionLabel,
    StatBar,
    TextLabel,
    clear_layout,
    pressed_qss,
)

__all__ = ["AirSituationOverlay"]

#: What each of the five effect paths is called on the panel, and the order they are read
#: in. **Generated nowhere and hand-ordered here on purpose**: the engine's
#: :data:`atlas.engine.airsituation.EFFECT_PATHS` is the authority on *which* five there
#: are -- a missing key raises rather than silently dropping a row -- and this is the
#: authority on which one a player should meet first. Supply is third from last and not
#: last because it is the effect nobody has ever been told about and the strongest.
_EFFECT_ORDER: tuple[tuple[str, str], ...] = (
    ("land", "Land battles here"),
    ("naval", "Fleet actions"),
    ("advance", "The advance after a win"),
    ("supply", "Supply under it"),
    ("lapse", "How long it holds"),
)

#: What each severity band is called where a player meets it, and the colour it is drawn in.
#: The band itself is the engine's -- :data:`atlas.engine.airsituation.SEVERITY_BANDS` -- and
#: these two tables only say it. A ``KeyError`` here is the right failure for the same reason
#: it is in :meth:`AirSituationOverlay._show_effects`: the engine grew a band and the panel
#: did not, and a corridor drawn in the wrong colour is worse than one drawn in none.
_SEVERITY_SAID: dict[str, str] = {
    "contained": "contained — nothing out of reach yet",
    "constricted": "constricted — part of the corridor gone",
    "severed": "severed — most of the corridor gone",
    "collapsed": "collapsed — almost nothing still in supply",
}

_SEVERITY_TONE: dict[str, str] = {
    # Not green. A corridor that has cost nothing *yet* is one province of enemy advance
    # from costing everything, and drawing it as good news is how a player learns about
    # interdiction on the turn it strands an army.
    "contained": "ink_dim",
    "constricted": "brass",
    "severed": "danger",
    "collapsed": "danger",
}


#: The shortest the scrolling body is allowed to be. **A floor against collapse, not the
#: panel's height** -- :meth:`AirSituationOverlay._use_the_room` sets the height from the
#: window, and this only stops a very short window from reducing the body to a scrollbar
#: with two lines behind it.
#:
#: **It was 360, and 360 was the panel's height rather than its floor.** The body could not
#: shrink below it, so the panel could not fit inside its own cap: at 1280x720 it wanted
#: 680px of a 656px allowance and the *outer* sheet scrolled by 24 to make up the
#: difference, which is two nested scrollbars on the size the interface is drawn for. Now
#: that the height is computed, the floor can be the number it was always supposed to be --
#: low enough never to bind on a window anybody plays on, high enough that a 900x400 window
#: still shows a readable body.
_BODY_MINIMUM = 240


def _ask_for_the_room(scroll: QScrollArea) -> None:
    """Let a scroll area take vertical room the layout has spare.

    Necessary and not sufficient: without it the body will not grow at all, and with it
    alone it grows only to the panel's own size hint. What decides the panel's height is
    :meth:`AirSituationOverlay._use_the_room`.
    """
    scroll.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)


def _when(lapse) -> str:
    """*on 12 March 1954* where the world has a clock, *in 3 turns* where it does not.

    Both where both are known, because they answer different questions: the date is what a
    campaign is planned against and the count is what tells a player whether that is soon at
    the pace they are playing at.
    """
    turns = f"in {lapse.turns} turn{'s' if lapse.turns != 1 else ''}"
    if not lapse.on:
        return turns
    return f"on {_said_date(lapse.on)} ({turns})"


def _said_date(iso: str) -> str:
    """``1954-03-12`` as *12 March 1954*, or unchanged if it will not parse."""
    from datetime import date

    try:
        when = date.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso
    return f"{when.day} {_MONTHS[when.month - 1]} {when.year}"


_MONTHS: tuple[str, ...] = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


#: The colour a term is drawn in. A signed modifier is the one thing on this panel a player
#: reads at a glance, and reading it wrong is reading the war wrong.
def _tone(effect) -> str:
    # **The one row that is not a modifier is not coloured like one.** The other kind of
    # ground's ceiling is on the panel so that *the sky is worth more than twice as much
    # over water* gets said at all -- drawn green it reads as a bonus this theatre is
    # paying, which is the opposite of what it says.
    if effect.informational or effect.path == "lapse":
        return "ink_dim"
    if effect.path == "supply":
        return "danger" if effect.worth else "ink_dim"
    if effect.worth > 0:
        return "good"
    if effect.worth < 0:
        return "danger"
    return "ink_dim"


class _Column(QWidget):
    """A column of word-wrapped rows that actually scrolls instead of being squeezed.

    **Qt sizes a ``widgetResizable`` scroll area's widget from its *minimum* size hint, and
    a word-wrapped label's minimum height is one line however much text is in it.** So a
    scroll area handed a column of prose does not scroll: it clamps the column to the
    viewport and the vertical layout distributes the shortfall by shrinking every row below
    its own hint, which on screen is rows overlapping each other. Measured on this panel
    before the fix: the column needed 1217px at the width it had, was allocated 330, and the
    five effect rows came out as five 3px bars.

    So the column answers the question it is actually asked. ``minimumSizeHint`` is what the
    scroll area reads, ``heightForWidth`` is what knows the answer, and nothing was
    connecting the two.

    **Asked rather than pushed, and that part is not a style preference.** The first draft
    computed the height once after filling and set it -- which produced 112px, because a row
    added in the same pass has not been polished yet and reports a one-line hint of its own.
    Everything in a Qt layout is stale until the event loop has been round once, and the way
    not to care about that is to compute on demand: the scroll area asks during layout, by
    which time the children can answer.
    """

    def minimumSizeHint(self):  # noqa: N802 - Qt's spelling
        hint = super().minimumSizeHint()
        layout = self.layout()
        if layout is None or not layout.hasHeightForWidth():
            return hint
        width = self.width() or hint.width()
        wanted = layout.heightForWidth(width)
        return QSize(hint.width(), max(hint.height(), wanted)) if wanted > 0 else hint


class _EffectRow(QFrame):
    """One of the five paths: what it changes, by how much, and why."""

    def __init__(self, label: str, effect, *, theme: Theme,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._tone = _tone(effect)
        self.setObjectName("airEffect")
        self.setToolTip(effect.rule)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE.md, SPACE.sm, SPACE.md, SPACE.sm)
        layout.setSpacing(SPACE.xs)
        self._kids: list[TextLabel] = []

        head = QHBoxLayout()
        head.setSpacing(SPACE.sm)
        name = TextLabel(label, style="label", colour="ink_faint", theme=theme)
        head.addWidget(name)
        head.addStretch(1)
        value = TextLabel(effect.said, style="body", colour=self._tone, theme=theme,
                          wrap=True)
        value.setAlignment(Qt.AlignmentFlag.AlignRight)
        head.addWidget(value, 1)
        layout.addLayout(head)
        self._kids.extend([name, value])

        because = TextLabel(effect.because, style="prose", colour="ink_dim", theme=theme,
                            wrap=True)
        layout.addWidget(because)
        self._kids.append(because)

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        edge = getattr(theme, self._tone, theme.rule)
        self.setStyleSheet(
            f"QFrame#airEffect {{ background: {theme.inset};"
            f" border-left: 3px solid {edge}; }}")
        for child in self._kids:
            child.apply_theme(theme)


class _ActionRow(PressFeedback, QFrame):
    """One thing that can be done about this sky, and the engine's verdict on it."""

    chosen = Signal(object)

    def __init__(self, action: AirAction, *, theme: Theme,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._action = action
        self.setObjectName("airAction")
        # **Only an order is clickable.** *Accept the risk* is a decision a government makes
        # by spending the turn on something else; giving it a button would invent a mechanic
        # to make a list of four look tidy.
        self._live = action.available and action.order is not None
        if self._live:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE.md, SPACE.sm, SPACE.md, SPACE.sm)
        layout.setSpacing(SPACE.xs)
        self._kids: list[TextLabel] = []

        label = action.label
        if action.needs and self._live:
            label += "   (then click where from)"
        head = TextLabel(label, style="row", theme=theme, wrap=True,
                         colour="ink_bright" if self._live else "ink_faint")
        layout.addWidget(head)
        self._kids.append(head)

        how = TextLabel(action.how, style="prose", colour="ink_dim", theme=theme, wrap=True)
        layout.addWidget(how)
        self._kids.append(how)

        # The engine's own refusal, on the line rather than in a tooltip: a control you have
        # to hover to understand is one nobody reads. The region menu already works this way.
        if action.why_not:
            why = TextLabel(f"— {action.why_not}", style="body_small", colour="danger",
                            theme=theme, wrap=True)
            layout.addWidget(why)
            self._kids.append(why)

    def _fire(self) -> None:
        if self._live:
            self.chosen.emit(self._action)

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        edge = theme.brass_line if self._live else theme.rule
        self.setStyleSheet(
            f"QFrame#airAction {{ background: {theme.inset};"
            f" border-left: 3px solid {edge}; }}"
            + (pressed_qss("QFrame#airAction", theme) if self._live else ""))
        for child in self._kids:
            child.apply_theme(theme)


class AirSituationOverlay(Overlay):
    """Who holds the sky over one theatre, and everything that follows from it."""

    #: An order the player chose. The window appends it to the pending orders, down the
    #: same single pipeline the region menu and the parser both use.
    action_chosen = Signal(object)
    #: An order that still needs a second province. The window waits for the next click.
    second_click_needed = Signal(object)
    #: A province named on the panel, to be shown on the map.
    region_selected = Signal(str)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(theme, parent)
        self.set_panel_width(PANEL.panel)

        self._conn: sqlite3.Connection | None = None
        self._world = ""
        self._viewer: str | None = None
        self._region = ""
        self._days = 30.0
        self._rows: list[QWidget] = []
        self._rules_open = False

        self.body.addWidget(SectionLabel("The air situation", theme=theme))
        self.body.addSpacing(SPACE.xs)
        self._where = TextLabel("", style="nation", colour="ink_bright", theme=theme,
                                wrap=True)
        self.body.addWidget(self._where)
        self.body.addSpacing(SPACE.xs)
        # **The verdict, in one sentence, above everything.** A player who reads only the
        # top line of this panel should still come away knowing whose sky it is.
        #
        # `nation` rather than `modal`: the header is fixed and the body scrolls under it,
        # so every point the heading takes is a point off what can be read without
        # scrolling. At 720px the difference between the two sizes is a whole effect row.
        self._verdict = TextLabel("", style="nation", colour="ink_body", theme=theme,
                                  wrap=True)
        self.body.addWidget(self._verdict)
        self.body.addSpacing(SPACE.sm)
        self._margin = StatBar("Margin", theme=theme)
        self.body.addWidget(self._margin)
        self.body.addSpacing(SPACE.xs)
        self._lapse = TextLabel("", style="body", colour="brass", theme=theme, wrap=True)
        self.body.addWidget(self._lapse)
        self.body.addSpacing(SPACE.lg)

        # **The body scrolls inside a fixed frame**, which is the shape every reading panel
        # in the program has: the verdict and the margin stay put at the top while the five
        # effects, the corridor and the actions scroll under them. Without it, a column of
        # word-wrapped rows reports a one-line height hint apiece and the whole surface
        # comes out as a 30px smear -- which is exactly what the first draft did.
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        _ask_for_the_room(self._scroll)
        self._scroll.setMinimumHeight(_BODY_MINIMUM)
        self._column = _Column()
        self._column.setStyleSheet("background: transparent;")
        self._stack = QVBoxLayout(self._column)
        self._stack.setContentsMargins(0, 0, SPACE.md, 0)
        self._stack.setSpacing(0)
        self._scroll.setWidget(self._column)
        self.body.addWidget(self._scroll, 1)

        self.body.addSpacing(SPACE.lg)
        controls = QHBoxLayout()
        self._rules_chip = Chip("How the air war works", theme=theme,
                                padding=(SPACE.md, SPACE.xs))
        self._rules_chip.setToolTip(
            "The complete rules, with the engine's own numbers")
        self._rules_chip.clicked.connect(self._toggle_rules)
        controls.addWidget(self._rules_chip)
        controls.addStretch(1)
        self.body.addLayout(controls)

        # The rules are a second scroller rather than more rows in the first, because they
        # are not about this theatre: opening them should not scroll the position away.
        self._rules_scroll = QScrollArea()
        self._rules_scroll.setWidgetResizable(True)
        self._rules_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._rules_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        _ask_for_the_room(self._rules_scroll)
        self._rules_scroll.setMinimumHeight(_BODY_MINIMUM)
        self._rules_holder = _Column()
        self._rules_holder.setStyleSheet("background: transparent;")
        self._rules = QVBoxLayout(self._rules_holder)
        self._rules.setContentsMargins(0, SPACE.lg, SPACE.md, 0)
        self._rules.setSpacing(0)
        self._rules_scroll.setWidget(self._rules_holder)
        self._rules_scroll.setVisible(False)
        self.body.addWidget(self._rules_scroll, 1)

        self.body.addSpacing(SPACE.md)
        self._footer = TextLabel(
            "Nobody orders the air war: it is fought every turn wherever two enemies' "
            "aircraft overlap. Escape to close.",
            style="mono_small", colour="ink_ghost", theme=theme, wrap=True)
        self.body.addWidget(self._footer)

    # -- data ---------------------------------------------------------------

    def load(self, conn: sqlite3.Connection, world_id: str, viewer: str | None,
             *, region_id: str, days: float = 30.0) -> None:
        """Read the theatre and lay it out. Writes nothing.

        Called on open and again after every jump and every rewind, because the panel holds
        no state of its own: what it shows is whatever the world says right now, which is
        the property that makes it correct after a rewind for free.
        """
        self._conn, self._world, self._viewer = conn, world_id, viewer
        self._region, self._days = region_id, days
        clear_layout(self._stack)
        clear_layout(self._rules)
        self._rows.clear()

        if viewer is None:
            self._where.setText("The air situation")
            self._verdict.setText(
                "You are spectating, so there is no government whose air war this is.")
            self._margin.setVisible(False)
            self._lapse.setText("")
            return

        found = air_situation(conn, world_id, region_id=region_id, viewer=viewer,
                              days=days)
        if found is None:
            self._where.setText(region_id)
            self._verdict.setText("There is no such province on this map.")
            self._margin.setVisible(False)
            self._lapse.setText("")
            return

        self._where.setText(
            f"{found.region_name}" + ("  ·  open water" if found.at_sea else ""))
        self._verdict.setText(found.verdict)
        self._show_margin(found)
        self._show_lapse(found)
        self._show_forces(found)
        self._show_effects(found)
        self._show_interdiction(found)
        self._show_actions(found)
        self._fill_rules(conn, world_id, viewer)
        for row in self._rows:
            row.apply_theme(self._theme)
        # The theme decides the face, the face decides where every wrapped line breaks, and
        # that decides the height -- so the columns are told to remeasure after the styling
        # rather than before it. `_Column` answers with what it needs when it is next asked.
        self._column.updateGeometry()
        self._rules_holder.updateGeometry()

    # -- the blocks ---------------------------------------------------------

    def _show_margin(self, found: AirSituation) -> None:
        self._margin.setVisible(bool(found.holder))
        if not found.holder:
            return
        # **The band leads and the figure follows it.** Every term scales linearly with the
        # margin, so a player reading "0.62" learns less than one reading "held" -- the band
        # is in the verdict sentence above, and this is the number behind it.
        self._margin.set_value(
            found.margin, found.margin,
            self._theme.good if found.mine else self._theme.danger, places=2)
        self._margin.setToolTip(
            f"{found.band} — how decisively the sky is held, 0 to 1. Every effect below "
            "scales with it, and under 0.08 it is worth nothing to either side.")

    def _show_lapse(self, found: AirSituation) -> None:
        lapse = found.lapse
        if not lapse.applies:
            self._lapse.setText("")
            return
        # **The date leads and the turn count follows it, wherever the world has a clock.**
        # A turn is a unit of interface; a player plans a campaign against a calendar, and
        # at yearly pacing "in 2 turns" and "in 4 turns" read almost identically and are two
        # years apart. Where the clock will not parse the count is still said on its own,
        # because a count is a worse answer than a date and a much better one than silence.
        when = _when(lapse)
        if lapse.still_contested:
            # Said as a counterfactual and flagged as one. The forecast is real -- it is the
            # engine's own decay run forward -- but it is what *would* happen if both sides
            # stopped flying, and neither of them has said they will.
            self._lapse.setText(
                "Both sides can still reach this theatre, so it will be fought over again "
                "next turn rather than decaying. Were both to stop flying, it would be "
                f"nobody's {when}.")
        else:
            self._lapse.setText(
                f"Nobody is contesting it. Unflown, it is nobody's {when} — air "
                "superiority is maintained, not owned.")

    def _heading(self, text: str, *, gap: int = SPACE.lg, note: str = "",
                 tone: str = "ink_dim") -> None:
        """A section label, optionally with a verdict on the section right-aligned beside it.

        **The note is where a severity goes, not the prose underneath.** A player scanning
        this panel reads the headings; a corridor that has lost two thirds of its reach
        should be legible from that scan and not from the third sentence of a paragraph.
        The same idiom as :class:`_EffectRow`'s head row, for the same reason.
        """
        self._stack.addSpacing(gap)
        label = SectionLabel(text, theme=self._theme)
        if not note:
            self._stack.addWidget(label)
        else:
            line = QHBoxLayout()
            line.setSpacing(SPACE.sm)
            line.addWidget(label)
            line.addStretch(1)
            said = TextLabel(note, style="label", colour=tone, theme=self._theme)
            said.setAlignment(Qt.AlignmentFlag.AlignRight)
            line.addWidget(said)
            self._stack.addLayout(line)
            self._rows.append(said)
        self._stack.addSpacing(SPACE.sm)
        self._rows.append(label)

    def _line(self, text: str, *, style: str = "prose", colour: str = "ink_dim") -> None:
        label = TextLabel(text, style=style, colour=colour, theme=self._theme, wrap=True)
        self._stack.addWidget(label)
        self._rows.append(label)

    def _show_forces(self, found: AirSituation) -> None:
        """Which formations put this sky where it is, and where they fly from."""
        self._heading("Who established it", gap=0)
        if not found.established_by:
            if found.contenders:
                self._line(
                    "Nobody holds it. "
                    + "; ".join(f"{name} can put {strength:.1f} over it"
                                for _id, name, strength in found.contenders[:4]))
            else:
                self._line("Nothing reaches this theatre. No power has aircraft that can "
                           "fly here at all.")
            return
        for force in found.established_by[:6]:
            deck = " from a deck" if force.from_deck else ""
            hops = ("overhead" if force.distance == 0
                    else f"{force.distance} region"
                         f"{'s' if force.distance != 1 else ''} away")
            self._line(
                f"{force.nation_name}'s {force.kind.replace('_', ' ')} at "
                f"{force.region_name} — {hops}{deck}, worth "
                f"{force.contribution:.1f} over this theatre",
                style="body", colour="ink_body")
        if len(found.established_by) > 6:
            self._line(f"and {len(found.established_by) - 6} more")
        self._line(
            "Strength falls off with every region flown, and overlapping fields add. "
            "Basing aircraft nearer is what raises it.")
        self._show_interception(found)

    def _show_interception(self, found: AirSituation) -> None:
        """What stops a raid over this theatre, in both directions.

        **Two figures the engine has always computed for every region and never shown.**
        `air_defence_over` is *the* interception term -- the nuclear layer calls the same
        function -- and a player who has researched radar or a SAM belt, or whose patron
        mans their air defences, has bought something they have never once been able to see
        the size of. Per theatre because that is what it is: air defence you did not build
        over ground you did not cover does not exist.
        """
        said = (
            f"Your fighters and technology stop about {found.air_defence:.0%} of a raid "
            "flown at this theatre."
        )
        if found.penetration:
            said += (f" Your own raids carry {found.penetration:+.0%} extra through "
                     "somebody else's.")
        self._line(said, style="body", colour="ink_body")

    def _show_effects(self, found: AirSituation) -> None:
        """The five paths, in the order a player should meet them."""
        self._heading("What it is doing")
        by_path = {effect.path: effect for effect in found.effects}
        # **The theatre's own figure leads, whichever kind of ground it is.** The fixed
        # order below puts land above sea, which is right ashore and backwards over water:
        # a fleet action panel that opened with the land ceiling would be showing the
        # comparison before the thing being compared. Swapping the pair is enough, because
        # they are the only two rows that are a pair.
        order = list(_EFFECT_ORDER)
        if by_path["land"].informational:
            order[0], order[1] = order[1], order[0]
        for path, label in order:
            # A KeyError here is the right failure: the engine's EFFECT_PATHS grew a path
            # and this list did not, and a panel silently showing four of five is exactly
            # the state this whole surface exists to end.
            row = _EffectRow(label, by_path[path], theme=self._theme)
            self._stack.addWidget(row)
            self._stack.addSpacing(SPACE.xs)
            self._rows.append(row)

    def _show_interdiction(self, found: AirSituation) -> None:
        """The corridor, and what has actually gone out of reach because of it."""
        interdiction = found.interdiction
        if not interdiction.active:
            return
        self._heading("What is being interdicted",
                      note=_SEVERITY_SAID[interdiction.severity],
                      tone=_SEVERITY_TONE[interdiction.severity])
        by = ", ".join(name for _id, name in interdiction.by)
        self._line(
            f"{by} holds the sky over {len(interdiction.corridors)} province(s) you "
            f"control: "
            + ", ".join(name for _id, name, _holder in interdiction.corridors[:6])
            + f". Each costs {interdiction.surcharge} extra hops out of a "
              f"{interdiction.budget}-hop supply budget.",
            style="body", colour="ink_body")
        if interdiction.lost:
            more = len(interdiction.lost) - 6
            self._line(
                f"The corridor has shortened by {interdiction.share_lost:.0%}: "
                f"{interdiction.reach_clear} province(s) would be in supply with those "
                f"skies clear and {interdiction.reach_now} are. "
                + ", ".join(name for _id, name in interdiction.lost[:6])
                + (f" and {more} more" if more > 0 else "")
                + f" — {interdiction.formations_cut} formation(s) standing in ground that "
                  "has gone out of reach.",
                style="body", colour="danger")
        else:
            self._line(
                "No ground has gone out of reach yet: the corridor is longer but still "
                "inside the budget.")

    def _show_actions(self, found: AirSituation) -> None:
        self._heading("What you can do")
        for action in found.actions:
            row = _ActionRow(action, theme=self._theme)
            row.chosen.connect(self._chose)
            self._stack.addWidget(row)
            self._stack.addSpacing(SPACE.xs)
            self._rows.append(row)

    def _fill_rules(self, conn: sqlite3.Connection, world_id: str, viewer: str) -> None:
        """The complete rules, behind the disclosure. The reference entry, verbatim."""
        for heading, lines in air_rules(conn, world_id, viewer):
            label = SectionLabel(heading, theme=self._theme)
            self._rules.addWidget(label)
            self._rules.addSpacing(SPACE.sm)
            self._rows.append(label)
            for said in lines:
                # The engine writes emphasis as Markdown, which is what a briefing wants and
                # not what a label renders. Stripped rather than converted: two weights of
                # one sentence is a typographic decision and the panel already has one.
                text = TextLabel(said.replace("**", ""), style="prose", colour="ink_body",
                                 theme=self._theme, wrap=True)
                self._rules.addWidget(text)
                self._rules.addSpacing(SPACE.xs)
                self._rows.append(text)
            self._rules.addSpacing(SPACE.md)
            divider = Divider(theme=self._theme)
            self._rules.addWidget(divider)
            self._rules.addSpacing(SPACE.md)
            self._rows.append(divider)

    # -- interaction --------------------------------------------------------

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt's spelling
        super().resizeEvent(event)
        # **Deferred by a pass, and that is the whole trick.** A resize event arrives
        # *before* the layout it causes has run, so everything readable inside it is the
        # previous geometry: measured here, the panel was 930px tall with a 480px body and
        # a sheet overflowing by 683, none of which was true a moment later. A zero-timer
        # runs after the layout has settled, when the same three questions have real
        # answers.
        QTimer.singleShot(0, self._use_the_room)

    def _use_the_room(self) -> None:
        """Give the scrolling body the height the window has left for it.

        **A taller window was showing exactly as much of this panel as a short one.** The
        base overlay's rule is *what was asked for, or what the window has*, but what it
        sets is a **cap** -- ``setMaximumHeight`` -- and a panel is otherwise as tall as its
        own size hint. That is right for a roster that ends and wrong for a reading surface
        whose body is longer than any window. This panel's hint is
        ``chrome + _BODY_MINIMUM``, a constant, so it asked for 680px whatever room it had:
        at 1920x1080 that was 336 points of empty canvas above and below a column still
        scrolling 855px, with the corridor -- the whole reason the surface exists -- under
        the fold on a screen with room for it twice over.

        **The chrome is measured, because it cannot be a constant.** The heading, the
        wrapped verdict, the margin bar, the lapse line, the disclosure and the wrapped
        footer come to a different height depending on how long a nation's name is and
        whether the lapse line is a forecast or a counterfactual. So it is read off the
        laid-out panel -- ``panel.height() - body.height()`` -- rather than written down
        here, where it would be right for Karthos and wrong for the Union of Soviet
        Socialist Republics.

        Both scrollers get the height because exactly one is ever visible --
        :meth:`_toggle_rules` swaps them -- and the panel must not resize when the reference
        entry opens. Idempotent: the chrome does not depend on the body's height, so a
        second call computes the same answer and the ``!=`` guard makes it free.
        """
        room = self.height() - 2 * PANEL_INSET
        body = self._rules_scroll if self._rules_open else self._scroll
        chrome = self.panel.height() - body.height()
        if room <= 0 or body.height() <= 0 or chrome <= 0:
            return
        wanted = max(_BODY_MINIMUM, room - chrome)
        if all(scroll.minimumHeight() == wanted
               for scroll in (self._scroll, self._rules_scroll)):
            return
        for scroll in (self._scroll, self._rules_scroll):
            scroll.setMinimumHeight(wanted)
        # **The pin is placed on the panel's top edge, and the panel is about to move.** The
        # base positions it during the resize; this runs a pass later and makes the panel
        # taller, which moves its top edge -- so without re-placing it the pin was left
        # stranded on the panel's right-hand side, a quarter of the way down, at every size
        # where the body grew. Deferred again for the same reason this method was:
        # `setMinimumHeight` schedules a layout rather than performing one, so the geometry
        # is not the new geometry until the following pass. Same for the hole cut for a
        # pinned sibling, which is that panel's geometry and is equally out of date.
        QTimer.singleShot(0, self._settle_the_chrome)

    def _settle_the_chrome(self) -> None:
        """Put the pin and the pinned sibling's hole back where the new geometry wants."""
        self._place_the_pin()
        self._cut_a_hole_for_the_pinned_panel()

    def _toggle_rules(self) -> None:
        """Show the reference entry, and give it the room by taking it from the position.

        Both halves cannot be full height at 720px, and the choice is not close: somebody
        who has opened the rules is reading the rules.
        """
        self._rules_open = not self._rules_open
        self._rules_scroll.setVisible(self._rules_open)
        self._scroll.setVisible(not self._rules_open)
        self._rules_chip.set_selected(self._rules_open)

    def _chose(self, action: AirAction) -> None:
        if action.order is None:
            return
        if action.needs:
            # The same two-click shape the region menu uses, and deliberately the same
            # object shape too, so the window's existing handler takes it unchanged.
            self.second_click_needed.emit(_Pending(action))
            self.close_overlay()
            return
        self.action_chosen.emit(_Pending(action))

    def refresh(self) -> None:
        """Re-read the same theatre. Called after a jump and after a rewind."""
        if self._conn is not None and self._region:
            self.load(self._conn, self._world, self._viewer,
                      region_id=self._region, days=self._days)


class _Pending:
    """An air action wearing the shape the window's order pipeline already takes.

    The window's `_take_action` and `_await_second_region` were written against
    :class:`atlas.engine.mapactions.MapAction` and read exactly three attributes off it --
    ``action``, ``label`` and ``needs``. Presenting those rather than teaching the window a
    second shape is what keeps this panel on the one pipeline: an order chosen here is
    indistinguishable, downstream, from one chosen on the map or typed in prose.
    """

    __slots__ = ("action", "label", "needs")

    def __init__(self, chosen: AirAction) -> None:
        self.action = dict(chosen.order or {})
        self.label = chosen.label
        self.needs = chosen.needs
