"""Watching the world turn.

A jump is minutes long. For those minutes this is the whole game, so it had better be the
game rather than a wait for it: the date at the top of a page turning over, beats arriving
as each part of the world settles, and -- as they are actually written into the world --
the news itself.

**Nothing here says how any of it is done.** That is the point of the screen and it is a
hard rule, not a preference. The turn loop underneath has stages with engineering names,
and every one of them is translated here, in :data:`BEATS`, and *only* here. The engine
sends a key and a nation's name; the words a player reads are written on this side of the
boundary. Two places composing the same sentence is how a screen ends up saying "calling
the model for France", and there is no second place.

**No stopwatch either.** A per-stage duration is a measurement of the machinery wearing a
costume: a player who watches a figure tick up beside a country's name is being told what
that country costs to think, which is the one thing this view exists not to tell them. The
numbers still exist and are still reachable -- they are what the trace screen and the
routing log are for -- they are just not on the screen the game is watched through.

**The news is real.** The wire at the foot of the panel is not a script: it is the world's
own ``event`` table, read as the turn writes it, ranked by the same salience the briefing
and the turn summary rank by, and scoped to what this government is entitled to know. A
player who reads "Russia advances on Donetsk" here is reading a row that exists.

Reading is done on the shared connection, which is what the rest of the interface already
does -- WAL, a serialized SQLite build, one writer -- over rows the engine has already
committed. It writes nothing, and the turn cannot tell whether anybody is watching.
"""

from __future__ import annotations

import contextlib
import sqlite3
from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..engine.progress import StageState, StageUpdate
from ..state.visibility import salience_of
from .overlays import Overlay
from .theme import PANEL, SPACE, Theme
from .widgets import (
    Chip,
    Divider,
    SectionLabel,
    TextLabel,
    restyle,
    scroll_qss,
)

__all__ = [
    "BEATS",
    "Beat",
    "BeatState",
    "TurningOverlay",
    "Wire",
    "beats_for",
    "murmurs_from_world",
]


# ---------------------------------------------------------------------------
# The mapping table: engine stages to things happening in the world
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Beat:
    """What one stage of a turn *is*, said in the world rather than in the program.

    ``murmurs`` rotate while the stage is running, which is what keeps a four-minute stage
    from reading as a hang. They are atmosphere and are allowed to be: everything
    load-bearing is either the heading, the settled line, or a real event on the wire.
    """

    heading: str
    settled: str
    murmurs: tuple[str, ...]


#: **The single translation from the turn loop's vocabulary into the player's.**
#:
#: The keys are exactly the stage keys :meth:`atlas.engine.turn_engine.TurnEngine.jump`
#: plans. Adding a stage there without adding it here falls back to :data:`_UNMAPPED`
#: rather than showing the key, and a test asserts the two lists agree -- because the way
#: this screen fails is by quietly putting an engineering word in front of a player.
BEATS: dict[str, Beat] = {
    "orders": Beat(
        "The orders leave your desk",
        "The couriers are away.",
        ("Your instructions are sealed and handed to the couriers.",
         "The ministries are told what you want done."),
    ),
    "world_sim": Beat(
        "The world takes its turn",
        "The month has been lived elsewhere too.",
        ("Reports arrive from every quarter.",
         "The chancelleries exchange notes.",
         "A decision is being taken tonight that you will read about in the morning.",
         "The wires are busy."),
    ),
    "validate": Beat(
        "The record is set down",
        "Entered in the ledger.",
        ("The clerks enter the month into the ledger.",
         "What can be written down is written down; the rest is struck out."),
    ),
    "enact": Beat(
        "Your orders are carried out",
        "Carried out, or explained away.",
        ("Your ministries set to work.",
         "Somebody, somewhere, is being told that this is impossible."),
    ),
    "resolve": Beat(
        "The fighting is decided",
        "Word is back from the fronts.",
        ("Word is awaited from the fronts.",
         "The staffs are counting what is left of the morning.",
         "Ground is being taken and given."),
    ),
    "month": Beat(
        "The month is lived through",
        "The month is spent.",
        ("Taxes are gathered and the yards are paid.",
         "The harvest comes in; a hull moves a little further down the slipway.",
         "The country goes about its business.",
         "Quiet arrangements are made in quiet rooms."),
    ),
    #: Synthesised by this view rather than sent by the engine: the turn plans one stage
    #: per tracked nation and they sit at the same time, so a literal rendering is five
    #: rows that all start together and all say the same thing. See :func:`beats_for`.
    "capitals": Beat(
        "The capitals sit",
        "Every capital has said what it will do.",
        ("The great powers are in session.",
         "Ministers are being woken."),
    ),
    "focus": Beat(
        "The powers worth watching are reckoned again",
        "The watch is reset.",
        ("Your staff argues about which capitals matter now.",),
    ),
    "digest": Beat(
        "The older news is filed",
        "Filed.",
        ("The archivists fold what is no longer news into the record.",),
    ),
    "seal": Beat(
        "The month closes",
        "The date is written at the foot of the page.",
        ("The date is written at the foot of the page.",),
    ),
}

#: What a tracked nation's own murmurs read like. ``{name}`` is the nation as the world
#: knows it, which is the only thing about it the engine sends.
CAPITAL_MURMURS: tuple[str, ...] = (
    "{name} weighs its position.",
    "{name}'s ministers are still sitting.",
    "A dispatch is being drafted in {name}.",
    "{name} has not yet said what it will do.",
)

#: Shown in place of the murmurs while a stage is on a second attempt. The engine sends a
#: count and nothing else, deliberately; this line is the whole of what that count means
#: to a player. See :meth:`atlas.engine.progress.ProgressReporter.retry`.
LOST_DISPATCH = "The first dispatch never arrived. It is being sent again."

#: A stage the turn planned and this table has no words for. Deliberately not the raw key:
#: an unmapped stage is a bug in this file, and the player is not the right person to show
#: it to. It is loud in a test instead.
_UNMAPPED = Beat("The world is busy elsewhere", "Settled.", ("It is taking its time.",))

_MARKS: dict[StageState, tuple[str, str]] = {
    StageState.PENDING: ("·", "ink_ghost"),
    StageState.RUNNING: ("▸", "brass"),
    StageState.DONE: ("✓", "good"),
    StageState.FAILED: ("✕", "danger"),
    StageState.SKIPPED: ("–", "ink_ghost"),
}


@dataclass(frozen=True, slots=True)
class BeatState:
    """One row of the view: a beat, how far along it is, and who it is about."""

    key: str
    beat: Beat
    state: StageState
    #: The nations this row is waiting on, where it is about nations at all. Only
    #: ``capitals`` ever has any.
    subjects: tuple[str, ...] = ()
    #: True while something under this row is being attempted again.
    retrying: bool = False


def beats_for(stages: list[StageUpdate]) -> list[BeatState]:
    """Fold the turn's stage list into the rows the player is shown.

    The one non-obvious move is the tracked nations. The turn plans ``tracked:<id>`` per
    nation because it genuinely runs one per nation, and they run *together*; rendered
    literally that is several rows which all start at once and all say the same thing.
    They fold into a single ``capitals`` row carrying the names of whoever has not
    answered yet -- the same information, said once.

    Order is the turn's own, which is the order the turn actually happens in, with the
    folded row sitting where the first tracked nation sat.
    """
    rows: list[BeatState] = []
    tracked = [stage for stage in stages if stage.key.startswith("tracked:")]
    placed = False
    for stage in stages:
        if stage.key.startswith("tracked:"):
            if placed:
                continue
            placed = True
            rows.append(_fold_capitals(tracked))
            continue
        rows.append(BeatState(
            key=stage.key,
            beat=BEATS.get(stage.key, _UNMAPPED),
            state=stage.state,
            retrying=stage.attempt > 1,
        ))
    return rows


def _fold_capitals(tracked: list[StageUpdate]) -> BeatState:
    """The tracked nations as one row, naming whoever the world is still waiting on."""
    states = {stage.state for stage in tracked}
    if StageState.RUNNING in states:
        state = StageState.RUNNING
    elif states <= {StageState.PENDING}:
        state = StageState.PENDING
    elif states <= {StageState.FAILED}:
        # Every capital silent is worth marking as a failure; one of them silent is not,
        # and a nation with nothing to say this month is not silent, it is content.
        state = StageState.FAILED
    else:
        state = StageState.DONE
    # Who is still being waited on, in the order the turn planned them -- the roster's own
    # order, rather than whichever call happened to come back first.
    waiting = tuple(
        str(stage.label) for stage in tracked
        if stage.state in (StageState.RUNNING, StageState.PENDING)
    )
    return BeatState(
        key="capitals",
        beat=BEATS["capitals"],
        state=state,
        subjects=waiting or tuple(str(stage.label) for stage in tracked),
        retrying=any(stage.attempt > 1 for stage in tracked),
    )


# ---------------------------------------------------------------------------
# Atmosphere drawn from the actual world
# ---------------------------------------------------------------------------


def murmurs_from_world(
    conn: sqlite3.Connection | None, world_id: str, *, limit: int = 6
) -> tuple[str, ...]:
    """In-world lines about *this* world, for the beats where the world is what is moving.

    Written atmosphere is fine for a minute and thin over four, and the cure is free: the
    contests the turn is grinding through are rows, and their regions have names. So the
    line a player reads while a war is being decided names the ground it is being fought
    over -- which is both better writing and true.

    Read-only and best-effort: a world with no fighting in it simply gets the written
    murmurs, and a query that cannot run costs the view nothing.
    """
    if conn is None or not world_id:
        return ()
    lines: list[str] = []
    with contextlib.suppress(sqlite3.Error):
        rows = conn.execute(
            """
            SELECT r.name AS region, a.name AS attacker, d.name AS defender
              FROM contest c
              JOIN region r ON r.id = c.region_id
              JOIN nation a ON a.id = c.attacker_id
              JOIN nation d ON d.id = c.defender_id
             WHERE c.world_id = ?
             ORDER BY c.pressure DESC, c.id
             LIMIT ?
            """,
            (world_id, int(limit)),
        ).fetchall()
        for row in rows:
            lines.append(f"Reports arrive from the {row['region']} front.")
            lines.append(
                f"{row['attacker']} and {row['defender']} are still at each other "
                f"over {row['region']}."
            )
    # **A war with no siege in it is still a war.** The recorded Modern Day world is the
    # case that made this necessary: Russia and Ukraine are at war at turn 14 and no ground
    # contest is open, so a screen that only knew about sieges had nothing to say about the
    # one war the player is watching.
    with contextlib.suppress(sqlite3.Error):
        wars = conn.execute(
            """
            SELECT a.name AS one, b.name AS other
              FROM relation r
              JOIN nation a ON a.id = r.nation_a
              JOIN nation b ON b.id = r.nation_b
             WHERE r.world_id = ? AND r.at_war = 1
               AND a.is_alive = 1 AND b.is_alive = 1
             ORDER BY a.name, b.name
             LIMIT ?
            """,
            (world_id, int(limit)),
        ).fetchall()
        # `relation` holds both directions of every pair, so a war read straight out of it
        # is named twice. Deduped on the pair rather than filtered in SQL by name order,
        # because only one of the two rows carrying the flag is a state this has to survive.
        named: set[frozenset[str]] = set()
        for war in wars:
            pair = frozenset((str(war["one"]), str(war["other"])))
            if pair in named:
                continue
            named.add(pair)
            lines.append(f"No word yet from the war between {war['one']} and {war['other']}.")
    return tuple(lines)


# ---------------------------------------------------------------------------
# The wire
# ---------------------------------------------------------------------------


class Wire:
    """The turn's own events, handed over a few at a time as they are written.

    **A reader, and only a reader.** One ``SELECT`` against the shared connection over rows
    the turn has already committed, exactly as every other panel in the interface reads. It
    holds the highest id it has seen, so nothing is shown twice and the query stays bounded
    however long the turn runs.

    **Scoped like everything else.** The visibility clause is the one the turn summary
    uses: public events, this government's own, and anything it has been let in on. A
    secret programme does not leak through the screen the turn is watched on.
    """

    #: Below this an event is the tick -- a workshop finished, an order acknowledged -- and
    #: the wire is for what a player would look up from the map for. Nothing is dropped
    #: from the world: this decides what is *streamed*, and the turn summary still lists
    #: everything a moment later.
    NOTABLE = 1

    #: Per poll, not per turn. A month can commit forty events at once, and dumping them
    #: all at once reads as a log; a few at a time reads as news coming in.
    PER_POLL = 3

    def __init__(
        self,
        conn: sqlite3.Connection | None,
        world_id: str,
        *,
        turn: int,
        viewer: str | None = None,
    ) -> None:
        self._conn = conn
        self._world_id = world_id
        self._turn = turn
        self._viewer = viewer
        self._seen = 0

    def poll(self) -> list[str]:
        """Whatever is new, notable and visible, oldest first."""
        if self._conn is None or not self._world_id:
            return []
        sql = """
            SELECT e.id, e.kind, e.text, e.turn FROM event e
             WHERE e.world_id = ? AND e.turn = ? AND e.id > ?
        """
        params: list = [self._world_id, self._turn, self._seen]
        if self._viewer:
            sql += """
               AND (e.visibility = 'public' OR e.actor_nation_id = ?
                    OR EXISTS (SELECT 1 FROM event_visibility v
                                WHERE v.event_id = e.id AND v.nation_id = ?))
            """
            params += [self._viewer, self._viewer]
        sql += " ORDER BY e.id LIMIT 60"

        try:
            rows = [dict(row) for row in self._conn.execute(sql, tuple(params))]
        except sqlite3.Error:
            # A turn is mid-write. A busy database is not a reason to bother anybody.
            return []
        if not rows:
            return []
        self._seen = max(int(row["id"]) for row in rows)
        return [
            str(row["text"]).strip()
            for row in rows
            if salience_of(row) >= self.NOTABLE and str(row["text"]).strip()
        ][: self.PER_POLL]


# ---------------------------------------------------------------------------
# The view
# ---------------------------------------------------------------------------


class TurningOverlay(Overlay):
    """The screen a jump is watched through.

    **Stage updates arrive from the engine thread.**
    :class:`~atlas.engine.progress.ProgressReporter` is a plain callback precisely so the
    engine never imports Qt, which means it fires on the asyncio loop thread -- and
    building a QWidget there is undefined behaviour that surfaces as a crash minutes into a
    jump. :attr:`stages_changed` is the crossing: emitting a signal across threads is safe,
    and Qt delivers it queued onto the GUI thread, where the widgets actually live.

    Everything that *moves* -- the rotating murmur, the wire, the trailing ellipsis -- is
    driven by one timer on the GUI thread doing a handful of ``setText`` calls a second and
    one small indexed query every other second. No layout is rebuilt while a beat is merely
    running and the map is never repainted, which is what keeps this inside the frame
    budget the rest of the interface is held to.
    """

    pinnable = False

    cancel_requested = Signal()
    #: Carries a list[StageUpdate] across the thread boundary. Connect nothing else to it.
    stages_changed = Signal(object)
    closable = False

    #: The heartbeat. Slow on purpose: a second is the pace of a thing being watched rather
    #: than a thing being measured, and it costs nothing.
    TICK_MS = 1000
    #: How many ticks a murmur holds before the next one. Long enough to read twice.
    MURMUR_TICKS = 4
    #: How many wire entries stay on screen. Older ones are dropped rather than kept and
    #: scrolled past, so a twelve-turn advance cannot grow the panel without bound.
    WIRE_KEEP = 40

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(theme, parent)
        self.set_panel_width(PANEL.dialog)

        # -- the date turning -------------------------------------------------
        self._from = TextLabel("", style="date", colour="ink_faint", theme=theme)
        self._arrow = TextLabel("→", style="date", colour="brass", theme=theme)
        self._to = TextLabel("", style="heading", colour="ink_bright", theme=theme)
        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(self._from)
        head.addWidget(self._arrow)
        head.addWidget(self._to, 1)
        self.body.addLayout(head)
        self.body.addSpacing(SPACE.xs)

        self._blurb = TextLabel(
            "", style="prose_italic", colour="ink_dim", theme=theme, wrap=True)
        self.body.addWidget(self._blurb)
        self.body.addSpacing(SPACE.lg)

        #: How far through the turn is, with no number on it. A number here would be a
        #: stopwatch by another name, and a rule is honest about being an impression.
        self._rule = _SettledRule(theme)
        self.body.addWidget(self._rule)
        self.body.addSpacing(SPACE.md)

        # -- the beats --------------------------------------------------------
        self._beats = QVBoxLayout()
        self._beats.setSpacing(0)
        self.body.addLayout(self._beats)
        self.body.addSpacing(SPACE.lg)

        # -- the wire ---------------------------------------------------------
        self._wire_label = SectionLabel("From the wire", theme=theme)
        self.body.addWidget(self._wire_label)
        self.body.addSpacing(SPACE.xs)
        self._wire_rule = Divider(theme=theme)
        self.body.addWidget(self._wire_rule)

        self._wire_area = QScrollArea()
        self._wire_area.setWidgetResizable(True)
        self._wire_area.setFrameShape(QFrame.Shape.NoFrame)
        self._wire_area.setFixedHeight(140)
        self._wire_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._wire_host = QWidget()
        self._wire_box = QVBoxLayout(self._wire_host)
        self._wire_box.setContentsMargins(0, 8, 6, 0)
        self._wire_box.setSpacing(0)
        self._quiet = TextLabel("Nothing on the wire yet.", style="prose_italic",
                                colour="ink_ghost", theme=theme, wrap=True)
        self._wire_box.addWidget(self._quiet)
        self._wire_box.addStretch(1)
        self._wire_area.setWidget(self._wire_host)
        self.body.addWidget(self._wire_area)
        self.body.addSpacing(SPACE.md)

        # -- the foot ---------------------------------------------------------
        self._footer = TextLabel(
            "Leaving now would abandon the month half-lived.",
            style="body_small", colour="ink_ghost", theme=theme, wrap=True,
        )
        self.body.addWidget(self._footer)
        self.body.addSpacing(SPACE.md)

        controls = QHBoxLayout()
        controls.addStretch(1)
        self._cancel = Chip("Stop once this has settled", theme=theme, padding=(11, 6))
        self._cancel.clicked.connect(self.cancel_requested.emit)
        controls.addWidget(self._cancel)
        self.body.addLayout(controls)

        self._rows: dict[str, _BeatRow] = {}
        self._wire_lines: list[TextLabel] = []
        self._wire: Wire | None = None
        self._world_murmurs: tuple[str, ...] = ()
        self._tick_count = 0
        self._murmur_index = 0
        #: Held from `begin`, so a turn rolling over under the same sheet can re-read the
        #: clock and re-aim the wire without the window being involved.
        self._conn: sqlite3.Connection | None = None
        self._world_id = ""
        self._viewer: str | None = None
        self._days = 30
        self._months = 1
        self._turns_seen = 1

        self._timer = QTimer(self)
        self._timer.setInterval(self.TICK_MS)
        self._timer.timeout.connect(self._tick)

        # Queued by construction: emitter and receiver are in different threads, so Qt
        # marshals the call onto the GUI thread rather than running it inline.
        self.stages_changed.connect(self.update_stages, Qt.ConnectionType.QueuedConnection)
        self.apply_theme(theme)

    # -- opening and closing ------------------------------------------------

    def begin(
        self,
        days: int,
        from_date: str,
        to_date: str,
        *,
        conn: sqlite3.Connection | None = None,
        world_id: str = "",
        turn: int = 0,
        viewer: str | None = None,
        expectation: str = "",
        months: int = 1,
    ) -> None:
        """Open on a month that has not happened yet.

        ``months`` above one is the *advance* -- run until something happens -- which is
        several turns behind one press and the longest wait in the program. The view stays
        up across all of them and turns its page as each one starts; see :meth:`_roll_over`.

        Re-armed on every jump. ``closable`` is set True when a turn finishes so the view
        can be dismissed; left that way, the *next* jump opens already dismissable and one
        stray Escape hides a running turn behind a live window.
        """
        self.closable = False
        self._conn = conn
        self._world_id = world_id
        self._viewer = viewer
        self._days = days
        self._months = max(1, int(months))
        self._turns_seen = 1
        self._from.setText(_pretty_date(from_date))
        self._to.setText(_pretty_date(to_date))
        self._blurb.setText(self._opening_line(expectation))

        for row in self._rows.values():
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()
        self._rule.set_fraction(0.0)
        self._clear_wire()

        self._wire = Wire(conn, world_id, turn=turn, viewer=viewer)
        self._world_murmurs = murmurs_from_world(conn, world_id)
        self._tick_count = 0
        self._murmur_index = 0
        self.open()
        self._timer.start()

    def _opening_line(self, expectation: str = "") -> str:
        if self._months > 1:
            return (
                f"Up to {self._months} months, one after another, stopping on the one "
                "something happens in."
                + (f" Give it {expectation}." if expectation else "")
            )
        return (
            f"{self._days} days are about to happen. "
            + (f"Give it {expectation}." if expectation
               else "The world moves first; you hear about it afterwards.")
        )

    def _roll_over(self) -> None:
        """A second month has begun under the same sheet. Turn the page.

        Detected rather than announced: an advance is several turns behind one press, and
        the turn loop does not know it is inside one. What it does do at the top of every
        turn is declare the whole plan again, which arrives here as a stage list with
        nothing started -- and that is the page turning.

        The date and the turn number are re-read rather than counted on, because the clock
        belongs to the world and an advance is allowed to halt wherever it likes.
        """
        self._turns_seen += 1
        self._clear_wire()
        self._murmur_index = 0
        if self._conn is None or not self._world_id:
            return
        try:
            row = self._conn.execute(
                "SELECT turn_number, world_date FROM world WHERE id = ?",
                (self._world_id,),
            ).fetchone()
        except sqlite3.Error:
            return
        if row is None:
            return
        # The world clock advances at the *end* of a turn, so at the top of the next one it
        # reads the turn that has just finished and the date that turn ended on -- which is
        # this turn number less one, and this turn opening date.
        start = str(row["world_date"])
        self._from.setText(_pretty_date(start))
        self._to.setText(_pretty_date(_add_days(start, self._days)))
        self._blurb.setText(
            f"Month {self._turns_seen} of up to {self._months}. "
            "It stops on the one something happens in."
            if self._months > 1 else self._opening_line()
        )
        self._wire = Wire(self._conn, self._world_id, turn=int(row["turn_number"]) + 1,
                          viewer=self._viewer)
        self._world_murmurs = murmurs_from_world(self._conn, self._world_id)

    def close_overlay(self) -> None:
        self._timer.stop()
        super().close_overlay()

    # -- stages -------------------------------------------------------------

    def post_stages(self, stages: list[StageUpdate]) -> None:
        """Thread-safe entry point. Give the engine THIS, never :meth:`update_stages`.

        Emitting a signal across threads is safe; touching a widget is not. The connection
        made in ``__init__`` is queued, so the work happens on the GUI thread.
        """
        self.stages_changed.emit(list(stages))

    def update_stages(self, stages: list[StageUpdate]) -> None:
        """Rebuild only what changed. GUI thread only; reached via :meth:`post_stages`."""
        incoming = list(stages)
        # A fresh plan with nothing started, arriving after something had started, is the
        # next turn of an advance beginning. See :meth:`_roll_over`.
        if (incoming and self._rows
                and all(stage.state is StageState.PENDING for stage in incoming)
                and any(row.beat.state is not StageState.PENDING
                        for row in self._rows.values())):
            self._roll_over()
        rows = beats_for(incoming)
        for index, beat in enumerate(rows):
            row = self._rows.get(beat.key)
            if row is None:
                row = _BeatRow(beat, theme=self._theme)
                self._rows[beat.key] = row
                self._beats.insertWidget(index, row)
            row.update_from(beat, self._theme)
        settled = sum(
            1 for beat in rows
            if beat.state in (StageState.DONE, StageState.FAILED, StageState.SKIPPED)
        )
        self._rule.set_fraction(settled / len(rows) if rows else 0.0)
        self._refresh_murmurs()

    # -- the heartbeat ------------------------------------------------------

    def _tick(self) -> None:
        self._tick_count += 1
        for row in self._rows.values():
            row.animate(self._tick_count)
        if self._tick_count % self.MURMUR_TICKS == 0:
            self._murmur_index += 1
            self._refresh_murmurs()
        if self._tick_count % 2 == 0:
            self.drain_wire()

    def _refresh_murmurs(self) -> None:
        for row in self._rows.values():
            row.set_murmur(self._murmur_index, self._world_murmurs, self._theme)

    def drain_wire(self) -> int:
        """Take whatever the world has written since the last look. Returns how many."""
        if self._wire is None:
            return 0
        lines = self._wire.poll()
        for text in lines:
            self._append_wire(text)
        return len(lines)

    def _append_wire(self, text: str) -> None:
        self._quiet.setVisible(False)
        line = TextLabel(text, style="prose", colour="ink_body", theme=self._theme,
                         wrap=True)
        line.setContentsMargins(0, 0, 0, 7)
        # Before the stretch, so the feed grows downward and the newest line is the one at
        # the bottom -- the way a wire actually comes in.
        self._wire_box.insertWidget(self._wire_box.count() - 1, line)
        self._wire_lines.append(line)
        while len(self._wire_lines) > self.WIRE_KEEP:
            stale = self._wire_lines.pop(0)
            stale.setParent(None)
            stale.deleteLater()
        bar = self._wire_area.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _clear_wire(self) -> None:
        for line in self._wire_lines:
            line.setParent(None)
            line.deleteLater()
        self._wire_lines.clear()
        self._quiet.setVisible(True)

    # -- theme --------------------------------------------------------------

    def apply_theme(self, theme: Theme) -> None:
        super().apply_theme(theme)
        self._wire_area.setStyleSheet(scroll_qss(theme))
        self._rule.apply_theme(theme)
        for row in self._rows.values():
            row.apply_theme(theme)
        restyle(self._wire_host, theme)


class _SettledRule(QWidget):
    """A thin brass line that fills as the turn settles. No number on it, deliberately."""

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(2)
        self._fraction = 0.0
        self._fill = QFrame(self)
        self.apply_theme(theme)
        self._lay_out()

    def set_fraction(self, fraction: float) -> None:
        self._fraction = max(0.0, min(1.0, float(fraction)))
        self._lay_out()

    @property
    def fraction(self) -> float:
        return self._fraction

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._lay_out()

    def _lay_out(self) -> None:
        self._fill.setGeometry(0, 0, int(self.width() * self._fraction), 2)

    def apply_theme(self, theme: Theme) -> None:
        self.setStyleSheet(f"background: {theme.rule_soft};")
        self._fill.setStyleSheet(f"background: {theme.brass};")


class _BeatRow(QWidget):
    """One beat: a mark, what is happening, and -- while it is happening -- a murmur."""

    def __init__(self, beat: BeatState, *, theme: Theme,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._beat = beat
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 7, 0, 7)
        layout.setSpacing(12)

        self._mark = TextLabel("", style="body", theme=theme)
        self._mark.setFixedWidth(14)
        self._mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._mark)

        column = QVBoxLayout()
        column.setSpacing(3)
        self._heading = TextLabel(beat.beat.heading, style="row_small", colour="ink",
                                  theme=theme)
        self._murmur = TextLabel("", style="body_small", colour="ink_faint", theme=theme,
                                 wrap=True)
        column.addWidget(self._heading)
        column.addWidget(self._murmur)
        layout.addLayout(column, 1)

        #: A trailing ellipsis on the running beat and nothing anywhere else. Its own
        #: fixed-width label, so a growing string cannot re-lay the row out every second.
        self._dots = TextLabel("", style="mono", colour="brass", theme=theme)
        self._dots.setFixedWidth(22)
        layout.addWidget(self._dots)

        self.update_from(beat, theme)

    @property
    def beat(self) -> BeatState:
        return self._beat

    @property
    def murmur(self) -> str:
        return self._murmur.text()

    def update_from(self, beat: BeatState, theme: Theme) -> None:
        self._beat = beat
        self._heading.setText(beat.beat.heading)
        self.apply_theme(theme)

    def animate(self, tick: int) -> None:
        if self._beat.state is not StageState.RUNNING:
            self._dots.setText("")
            return
        self._dots.setText("." * (1 + tick % 3))

    def set_murmur(self, index: int, world: tuple[str, ...], theme: Theme) -> None:
        """What this row says under its heading, for the state it is in."""
        text = ""
        if self._beat.state is StageState.RUNNING:
            text = self._running_line(index, world)
        elif self._beat.state is StageState.DONE:
            text = self._beat.beat.settled
        elif self._beat.state is StageState.FAILED:
            text = "No word came."
        self._murmur.setText(text)
        self._murmur.setVisible(bool(text))
        self._murmur.apply_theme(theme)

    def _running_line(self, index: int, world: tuple[str, ...]) -> str:
        if self._beat.retrying:
            return LOST_DISPATCH
        if self._beat.key == "capitals" and self._beat.subjects:
            name = self._beat.subjects[index % len(self._beat.subjects)]
            return CAPITAL_MURMURS[index % len(CAPITAL_MURMURS)].format(name=name)
        pool = self._beat.beat.murmurs
        # The world's own wars and fronts get a look-in on the two beats where the world is
        # what is moving. Anywhere else they would be a non-sequitur -- the archivists are
        # not at the front -- so there the written murmurs stand alone.
        #
        # **First, not appended.** A beat is often over before the rotation has come round,
        # so a line about the actual war has to be the one a player is most likely to see
        # rather than the one after the three generic ones.
        if self._beat.key in ("world_sim", "resolve") and world:
            pool = world + pool
        return pool[index % len(pool)] if pool else ""

    def apply_theme(self, theme: Theme) -> None:
        glyph, colour = _MARKS.get(self._beat.state, ("·", "ink_ghost"))
        self._mark.setText(glyph)
        self._mark.set_colour(colour, theme)
        self._heading.set_colour(
            "ink_bright" if self._beat.state is StageState.RUNNING
            else "ink" if self._beat.state is not StageState.PENDING
            else "ink_faint",
            theme,
        )
        for child in (self._murmur, self._dots):
            child.apply_theme(theme)


def _add_days(iso: str, days: int) -> str:
    from datetime import date, timedelta

    try:
        return (date.fromisoformat(iso) + timedelta(days=int(days))).isoformat()
    except (TypeError, ValueError):
        return iso or ""


def _pretty_date(iso: str) -> str:
    """``2026-01-31`` becomes ``31 January 2026``, as the rest of the interface writes it."""
    from datetime import date

    try:
        parsed = date.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso or ""
    months = ("January", "February", "March", "April", "May", "June", "July",
              "August", "September", "October", "November", "December")
    return f"{parsed.day} {months[parsed.month - 1]} {parsed.year}"
