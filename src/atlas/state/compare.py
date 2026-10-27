"""Two lines you have played, held against each other on the same day.

**A raw diff of two worlds is unreadable and would be useless.** Two branches an afternoon
apart differ in tens of thousands of rows — every unit's morale, every province's
development, every event either of them wrote. A player asking *what did that decision cost
me* does not want that, and a screen that produced it would be a screen nobody opened twice.

So this is a **curated headline**: nine questions a person actually asks about a campaign,
answered for each line and set side by side. Ground, wars, agreements, who governs, the
economy, the armies, the warheads, what the world remembers, and the score. Anything not on
that list is deliberately absent rather than accidentally missing.

**The same *moment*, or it is not a comparison.** Two branches drift apart in turn number
the moment their pacing differs, so "A at turn 30 against B at turn 25" holds two different
moments beside each other and calls the gap a consequence.

And a shared *label* is not a shared moment, which is the sharper version of the same
mistake and the one this made. Reading each line at "the last turn that had started by
1902" puts a yearly line at a turn beginning in 1900 and a monthly line at one beginning in
1902, two years apart, both captioned 1902. The comparison is keyed on a turn's `end_date`
now -- the day the world actually stood at when that turn finished, and the day `rewind_to`
puts the clock back to -- so the latest end date the two lines *share* is a moment both of
them were genuinely in. Two lines that never shared one are told so.

**Read-only, and the copy is how.** Reading a line as it stood earlier means unwinding it,
and unwinding a player's save to draw them a screen is not acceptable at any price. Each
branch is copied to a temporary file first and the copy is rewound; the player's own worlds
are opened read-only and never written. The copies are deleted on the way out.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["Comparison", "Heading", "compare", "headline_of", "render_comparison"]


@dataclass(frozen=True, slots=True)
class Heading:
    """One question, answered for both lines."""

    #: What is being compared, in the words a player would use.
    what: str
    left: str
    right: str
    #: True when the two answers differ. What a comparison screen exists to pick out.
    differs: bool = False

    @property
    def same(self) -> bool:
        return not self.differs


@dataclass(frozen=True, slots=True)
class Comparison:
    """Two lines, and the nine ways they are being held against each other."""

    left_name: str
    right_name: str
    #: The world date both lines are read at.
    on: str = ""
    #: Where the two lines parted, if they share an ancestor.
    forked_at: str = ""
    headings: tuple[Heading, ...] = ()
    #: Why there is nothing to compare, when there is not.
    refused: str = ""

    @property
    def differences(self) -> tuple[Heading, ...]:
        return tuple(heading for heading in self.headings if heading.differs)


# --------------------------------------------------------------- reading one line


def _one(conn: sqlite3.Connection, sql: str, *args) -> str:
    row = conn.execute(sql, args).fetchone()
    value = row[0] if row else None
    return "" if value is None else str(value)


def headline_of(conn: sqlite3.Connection, world_id: str, viewer: str = "") -> dict[str, str]:
    """The nine answers, for one line, as it stands in this connection.

    Every one is a `SELECT` and some arithmetic. Deliberately coarse: this is what a person
    would say if you asked them how a campaign was going, not a census.

    **Each heading fails alone.** A branch saved before a table existed -- and there are
    saves on disk older than `leader` -- should cost that one line, not the comparison. An
    unanswerable heading says so and the other eight are still worth having.
    """
    answers: dict[str, str] = {}
    for name, ask in (
        ("territory", _territory),
        ("wars", _wars),
        ("treaties", _treaties),
        ("governments", _governments),
        ("economy", _economy),
        ("armed forces", _forces),
        ("nuclear balance", _arsenals),
        ("major facts", _facts),
        ("score", _score_line),
    ):
        try:
            answers[name] = ask(conn, world_id, viewer)
        except sqlite3.Error:
            answers[name] = "not recorded in this save"
    return answers


def _territory(conn: sqlite3.Connection, world_id: str, viewer: str) -> str:
    held = _one(conn,
                "SELECT COUNT(*) FROM region WHERE world_id = ? AND kind <> 'ocean'"
                "  AND COALESCE(controller_nation_id, owner_nation_id) = ?",
                world_id, viewer) if viewer else ""
    occupied = _one(conn,
                    "SELECT COUNT(*) FROM region WHERE world_id = ? AND kind <> 'ocean'"
                    "  AND controller_nation_id IS NOT NULL"
                    "  AND controller_nation_id <> owner_nation_id", world_id)
    return (
        f"{held} province(s) held" if viewer else "") + (
        f"{', ' if viewer else ''}{occupied} under occupation" if occupied != "0" else
        (", none occupied" if viewer else "no ground occupied"))


def _wars(conn: sqlite3.Connection, world_id: str, _viewer: str) -> str:
    # Named, not keyed. A line reading "dvn vs usa" is a database talking to itself; a
    # player asked what a decision cost and is owed the answer in the words on the map.
    wars = conn.execute(
        "SELECT a.name AS left_side, b.name AS right_side FROM relation r"
        "  JOIN nation a ON a.id = r.nation_a JOIN nation b ON b.id = r.nation_b"
        " WHERE r.world_id = ? AND r.at_war = 1", (world_id,)).fetchall()
    pairs = sorted({tuple(sorted((str(row["left_side"]), str(row["right_side"]))))
                    for row in wars})
    return (
        "at peace" if not pairs else
        f"{len(pairs)} war(s): " + "; ".join(f"{a} vs {b}" for a, b in pairs[:4])
        + (" and more" if len(pairs) > 4 else ""))


def _treaties(conn: sqlite3.Connection, world_id: str, _viewer: str) -> str:
    agreements = conn.execute(
        "SELECT kind, COUNT(*) AS n FROM commitment WHERE world_id = ? AND status = 'active'"
        " GROUP BY kind ORDER BY kind", (world_id,)).fetchall()
    return ("nothing standing" if not agreements else ", ".join(
        f"{int(row['n'])} {str(row['kind']).replace('_', ' ')}" for row in agreements))


def _governments(conn: sqlite3.Connection, world_id: str, _viewer: str) -> str:
    governments = conn.execute(
        "SELECT n.name AS nation, l.name AS leader, l.came_to_power AS how"
        "  FROM leader l JOIN nation n ON n.id = l.nation_id"
        " WHERE l.world_id = ? AND n.is_alive = 1 ORDER BY n.name", (world_id,)).fetchall()
    seized = [row for row in governments
              if str(row["how"] or "") in ("coup", "revolution")]
    return (f"{len(governments)} government(s)"
            + (f", {len(seized)} of them seized power" if seized else ", none seized power"))


def _economy(conn: sqlite3.Connection, world_id: str, _viewer: str) -> str:
    return "world economy {}, treasuries {}".format(
        _round(_one(conn, "SELECT SUM(economy) FROM nation WHERE world_id = ?", world_id)),
        _round(_one(conn, "SELECT SUM(treasury) FROM nation WHERE world_id = ?", world_id)))


def _forces(conn: sqlite3.Connection, world_id: str, _viewer: str) -> str:
    return "{} formation(s), {} strength".format(
        _one(conn, "SELECT COUNT(*) FROM unit WHERE world_id = ?", world_id),
        _round(_one(conn, "SELECT SUM(strength) FROM unit WHERE world_id = ?", world_id)))


def _arsenals(conn: sqlite3.Connection, world_id: str, _viewer: str) -> str:
    arsenals = conn.execute(
        "SELECT n.name AS nation, a.warheads AS warheads FROM arsenal a"
        "  JOIN nation n ON n.id = a.nation_id"
        " WHERE a.world_id = ? AND a.warheads > 0 ORDER BY a.warheads DESC, n.name",
        (world_id,)).fetchall()
    return ("nobody is armed" if not arsenals else ", ".join(
        f"{str(row['nation'])} {int(row['warheads'])}" for row in arsenals[:5])
        + (" and more" if len(arsenals) > 5 else ""))


def _facts(conn: sqlite3.Connection, world_id: str, _viewer: str) -> str:
    facts = _one(conn,
                 "SELECT COUNT(*) FROM world_fact WHERE world_id = ? AND end_turn IS NULL",
                 world_id)
    return f"{facts} thing(s) the world still believes"


def _round(value: str) -> str:
    try:
        return f"{float(value):,.0f}"
    except (TypeError, ValueError):
        return value or "0"


def _score_line(conn: sqlite3.Connection, world_id: str, viewer: str) -> str:
    """Where the observer stands, or who leads when there is no observer."""
    from ..engine.scoring import standings

    # A database error is *not* caught here: it belongs to the caller, which reports the
    # heading as unrecorded. Swallowing it would answer "unscored", and "this world has no
    # scores yet" is a different claim from "this save cannot be scored".
    table = standings(conn, world_id)
    if not table.scores:
        return "unscored"
    ordered = sorted(table.scores, key=lambda score: -score.total)
    if viewer:
        for rank, score in enumerate(ordered, start=1):
            if score.nation_id == viewer:
                return f"{score.name} {score.total:.0f}, ranked {rank} of {len(ordered)}"
    best = ordered[0]
    return f"{best.name} leads on {best.total:.0f}"


# ------------------------------------------------------ reading one line as it stood


def _open_read_only(path: Path) -> sqlite3.Connection:
    """Open a player's world without any possibility of writing to it.

    `db.connect` is the writer's door and carries the sync-root guard with it; this is the
    reader's, and SQLite's own `mode=ro` is what makes the promise rather than a convention
    about which functions we call. Opened and closed explicitly, because `with
    sqlite3.connect(...)` scopes the *transaction* and not the handle -- which is how a
    branch rename came to fail on Windows once already.

    **Through `read_only_uri`, not ``f"file:{path}"``**: a ``#`` in the path cut the URI
    short, and ``%XX`` was decoded into a different file name (bug scan S6).
    """
    from .db import read_only_uri

    conn = sqlite3.connect(read_only_uri(path), uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _date_of(conn: sqlite3.Connection, world_id: str) -> str:
    return _one(conn, "SELECT world_date FROM world WHERE id = ?", world_id)


def _turn_ending_on(conn: sqlite3.Connection, world_id: str, date: str) -> int:
    """The turn this line *finished* on ``date``. ``0`` when it never did.

    **The turn that ended on the day, not the last one that had started by it**, and the
    difference is the whole of a bug Codex found. `rewind_to` restores the world's date
    from the turn's `end_date`, so the turn whose end date is the day being compared is the
    only one that actually puts the world at that moment.

    Asking for the last turn to have *started* by the date instead compared two different
    moments and labelled them with one: a yearly line's turn beginning in 1900 and a monthly
    line's turn beginning in 1902 both satisfy "started on or before 1902", and the two
    snapshots are two years apart.
    """
    found = _one(
        conn,
        "SELECT MAX(number) FROM turn WHERE world_id = ? AND end_date = ?",
        world_id, date)
    with suppress(TypeError, ValueError):
        return int(found)
    return 0


def _moments(branch) -> list[str]:
    """Every world date this line has actually stood at, oldest first.

    A turn's `end_date` is a moment the world was in: the turn finished, the clock read
    that, and `rewind_to` will put it back there exactly. The line's own current date is a
    moment too -- it is standing at it now -- and is included so two lines that are simply
    up to date can be compared without either being rewound.
    """
    conn = _open_read_only(branch.path)
    try:
        dates = [str(row["end_date"]) for row in conn.execute(
            "SELECT DISTINCT end_date FROM turn"
            " WHERE world_id = ? AND end_date IS NOT NULL AND end_date <> ''"
            " ORDER BY end_date", (branch.world_id,))]
        today = _date_of(conn, branch.world_id)
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
    if today and today not in dates:
        dates.append(today)
    return sorted(dates)


@dataclass
class _Reading:
    """One line's answers at a date, and the temporary copy it took to get them."""

    answers: dict[str, str] = field(default_factory=dict)
    scratch: Path | None = None
    problem: str = ""


def _read_at(branch, date: str, viewer: str) -> _Reading:
    """This line's headline as it stood on ``date``, without touching the player's save.

    **The copy is the whole of the read-only promise.** Reading a line as it stood means
    unwinding it, and unwinding somebody's save to draw them a screen is not acceptable at
    any price -- so a copy is taken, the copy is rewound, and the copy is deleted. Where the
    line is already at the date, nothing is copied at all.

    **`export_world`, not a file copy.** A world that is open -- and the line a player is
    playing always is -- keeps its recent turns in the write-ahead log rather than in the
    `.db` file. Copying the file alone gave a database that was not merely stale: on a
    freshly created world it had *no tables at all*. `VACUUM INTO` reads through the log and
    is what `fork_world` already uses for the same reason, and it is legal from a read-only
    handle because it writes only to the destination.
    """
    from .db import connect, export_world
    from .replay import rewind_to

    reading = _Reading()
    live = _open_read_only(branch.path)
    try:
        if _date_of(live, branch.world_id) == date:
            reading.answers = headline_of(live, branch.world_id, viewer)
            return reading
        target = _turn_ending_on(live, branch.world_id, date)
        if target <= 0:
            reading.problem = (
                f"{branch.name} was never standing on {date}; it has no turn that "
                "ended there")
            return reading
        # Recorded the moment the directory exists, so that whatever fails from here on --
        # the export, the rewind, the reading -- the copy is still found and deleted.
        reading.scratch = Path(tempfile.mkdtemp(prefix="atlas-compare-")) / branch.path.name
        try:
            export_world(live, reading.scratch)
        except BaseException:
            _discard(reading)
            raise
    finally:
        live.close()
    try:
        copy = connect(reading.scratch)
        try:
            outcome = rewind_to(copy, branch.world_id, target)
            if not outcome.ok:
                reading.problem = (
                    f"{branch.name} could not be read at {date}: "
                    + "; ".join(outcome.problems))
                return reading
            reading.answers = headline_of(copy, branch.world_id, viewer)
        finally:
            copy.close()
    except BaseException:
        _discard(reading)
        raise
    return reading


def _discard(reading: _Reading) -> None:
    if reading.scratch is not None:
        shutil.rmtree(reading.scratch.parent, ignore_errors=True)


# ------------------------------------------------------------------ the comparison


#: The nine, in the order a person asks them: where you are, who you are fighting, what you
#: have signed, who governs, what you can afford, what you can field, what you can threaten,
#: what the world believes, and how you are doing.
HEADINGS: tuple[str, ...] = (
    "territory", "wars", "treaties", "governments", "economy",
    "armed forces", "nuclear balance", "major facts", "score",
)


def compare(left, right, *, viewer: str = "", on: str = "") -> Comparison:
    """Hold two lines against each other on the same day.

    ``left`` and ``right`` are :class:`~atlas.state.branch.Branch` records. ``on`` pins the
    date; without it, the latest day *both* lines have reached is used, because that is the
    last moment they can honestly be compared at.
    """
    for branch in (left, right):
        # Checked before anything is read, because a comparison with one side silently
        # blank reads as "this line has no wars" rather than as "this line is unreadable".
        unreadable = _unreadable(branch)
        if unreadable:
            return Comparison(left.name, right.name, refused=unreadable)

    # **A moment both lines actually stood at, not a date both can be labelled with.**
    #
    # This used to be `min(current, current)` -- the earlier of the two lines' present
    # dates -- and then each line was rewound to the last turn that had *started* by it. Two
    # lines at different pacings satisfy that with turns years apart, so the comparison
    # showed two different moments under one heading. Codex found it.
    #
    # A turn's end date is a moment the world was in and `rewind_to` puts it back there
    # exactly, so the honest common ground is the latest end date the two lines share.
    shared = sorted(set(_moments(left)) & set(_moments(right)))
    when = on or (shared[-1] if shared else "")
    if not when:
        return Comparison(
            left.name, right.name,
            refused="these two lines were never standing on the same day, so there is no "
                    "moment to compare them at")
    if on and (on not in set(_moments(left)) or on not in set(_moments(right))):
        return Comparison(
            left.name, right.name, on=on,
            refused=f"one of these lines was never standing on {on}"
                    + (f"; they share {', '.join(shared[-3:])}" if shared else ""))

    first = _read_at(left, when, viewer)
    try:
        second = _read_at(right, when, viewer)
    except BaseException:
        _discard(first)
        raise
    readings = (first, second)
    try:
        for reading in readings:
            if reading.problem:
                return Comparison(left.name, right.name, on=when, refused=reading.problem)
        headings = tuple(
            Heading(what=name,
                    left=readings[0].answers.get(name, ""),
                    right=readings[1].answers.get(name, ""),
                    differs=readings[0].answers.get(name) != readings[1].answers.get(name))
            for name in HEADINGS
        )
    finally:
        for reading in readings:
            _discard(reading)

    return Comparison(
        left_name=left.name, right_name=right.name, on=when,
        forked_at=_shared_fork(left, right), headings=headings)


def _unreadable(branch) -> str:
    """Why this line cannot be opened, or ``""`` when it can.

    A branch record can outlive its file: exported, moved, deleted from under the launcher.
    That is a refusal with a reason on it, not a traceback out of a screen.
    """
    if not branch.path.exists():
        return f"there is no world file for {branch.name}"
    try:
        conn = _open_read_only(branch.path)
    except sqlite3.Error as problem:
        return f"{branch.name} could not be opened: {problem}"
    try:
        if not _date_of(conn, branch.world_id):
            return f"{branch.name} holds no world called {branch.world_id!r}"
    except sqlite3.Error as problem:
        return f"{branch.name} could not be read: {problem}"
    finally:
        conn.close()
    return ""


def _current(branch) -> str:
    conn = _open_read_only(branch.path)
    try:
        return _date_of(conn, branch.world_id)
    finally:
        conn.close()


def _shared_fork(left, right) -> str:
    """Where the two parted, said plainly, or ``""`` when they never shared a line."""
    if left.forked_from == right.name:
        return f"{left.name} was forked from {right.name} at turn {left.forked_at}"
    if right.forked_from == left.name:
        return f"{right.name} was forked from {left.name} at turn {right.forked_at}"
    if left.forked_from and left.forked_from == right.forked_from:
        return (f"both were forked from {left.forked_from}, at turns "
                f"{left.forked_at} and {right.forked_at}")
    return ""


def render_comparison(comparison: Comparison) -> list[str]:
    """The comparison as lines, for a terminal or an export."""
    if comparison.refused:
        return [f"No comparison: {comparison.refused}"]
    out = [f"{comparison.left_name} against {comparison.right_name}, on {comparison.on}"]
    if comparison.forked_at:
        out.append(f"  ({comparison.forked_at})")
    out.append("")
    for heading in comparison.headings:
        # ASCII, because this goes to terminals and exported files as well as to a
        # screen, and a console that cannot encode the glyph would fail on the *report*
        # rather than on anything being reported.
        mark = "!=" if heading.differs else "  "
        out.append(f"{mark} {heading.what}")
        out.append(f"    {comparison.left_name}: {heading.left}")
        out.append(f"    {comparison.right_name}: {heading.right}")
    if not comparison.differences:
        out += ["", "These two lines are the same in every way this screen looks at."]
    return out
