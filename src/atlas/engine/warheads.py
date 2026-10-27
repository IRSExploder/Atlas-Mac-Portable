"""How a country comes to have an arsenal, and how it grows one.

**Warheads only ever went down.** `nuclear._spend` adjusts the count negative when weapons
are used, a launch clamps what it commits to what is held, and nothing anywhere in the engine
ever added one. Every arsenal on every map was the number a scenario seeded and a monotonic
decline from there -- so proliferation could not happen, an arms race could not happen, and a
power without warheads in 1936 could not acquire them by 1945, which is most of what the
period is about.

**The programme was already there and bought the wrong thing.** `research.atomic_programme`
is sixteen turns, a hundred and twenty treasury, secret, and describes itself as "the longest
thing a country can undertake and the only one worth hiding" -- and its effects were
`military_strength +12` and `legitimacy +4`. Finishing the Manhattan Project made your army
feel better about itself. It did not make you nuclear.

So there are two steps here and they are different in kind:

**Crossing the threshold** is a technology. It happens once, it is slow, it is secret while it
runs, and the moment it lands the world has a new nuclear power in it. That is `TechEffect`
with an ``arsenal`` scope -- a fourth scope beside `nation` and `unit_template`, applied by
the same completion path, so it rewinds like everything else.

**Growing the pile** is production. It goes in the same queue as everything else, for the
reason that queue exists: a country choosing between another warhead and a steelworks is
exactly the decision the system is built to pose. `construction.project_kind` has no closed
enum on it, so this needed no schema change -- warheads are a third kind of thing in the
queue beside units and public works.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..state.diff import Diff
from ..state.models import DiffOp, Visibility

__all__ = [
    "WARHEAD_BUILD_DAYS",
    "WARHEAD_INDUSTRY",
    "WARHEAD_PROJECT",
    "TACTICAL_PROJECT",
    "WARHEAD_TREASURY",
    "WarheadReport",
    "arsenal_diffs",
    "can_build_warheads",
    "order_warhead",
    "warhead_lines",
]

#: The id a staff names in `build_orders` to commission one, and the `construction.kind`
#: it is queued under.
WARHEAD_PROJECT = "warhead"

#: The battlefield kind, ordered the same way and priced the same. **Cheaper would be
#: wrong**: a tactical weapon is not a cheap weapon, it is a differently aimed one, and the
#: cost of choosing it is paid in deterrence rather than in treasury -- see
#: `nuclear.deterrence`, which counts only the strategic remainder.
TACTICAL_PROJECT = "tactical_warhead"

#: **Balance numbers.** A warhead is dear in money and dearer in industry: the constraint on
#: an arsenal has always been fissile material and the plant to make it, not the budget line.
#: Priced against a steelworks so the queue poses a real question -- a power building
#: warheads is visibly not building anything else.
WARHEAD_TREASURY = 26.0
WARHEAD_INDUSTRY = 4.0

#: Nine months a warhead. Slow enough that an arms race is a decade rather than a year, and
#: slow enough that a power which starts one during a war does not finish it during that war.
WARHEAD_BUILD_DAYS = 270.0

#: What a country has the moment its atomic programme lands: one weapon, and the crudest way
#: of delivering it. **Deliberately one.** The threshold is the thing that matters -- the
#: world now contains a power that could -- and an arsenal worth deterring with is a decade
#: of the production below, which is what proliferation actually looked like.
FIRST_ARSENAL_WARHEADS = 1
FIRST_ARSENAL_DELIVERY = "aircraft"


@dataclass
class WarheadReport:
    """What the pile did this turn."""

    commissioned: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def happened(self) -> bool:
        return bool(self.commissioned)


def _arsenal_row(conn: sqlite3.Connection, world_id: str, nation_id: str):
    try:
        return conn.execute(
            "SELECT warheads, delivery FROM arsenal WHERE world_id = ? AND nation_id = ?",
            (world_id, nation_id)).fetchone()
    except sqlite3.OperationalError:
        return None


def can_build_warheads(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> bool:
    """Whether this country has crossed the threshold and may add to its pile.

    **Having an arsenal is the licence**, rather than holding the technology. A scenario may
    seed a nuclear power without seeding it a research row -- most do -- and a rule keyed on
    the technology would tell the United States in 1972 that it could not build warheads.
    """
    row = _arsenal_row(conn, world_id, nation_id)
    return row is not None and str(row["delivery"] or "none") != "none"


def arsenal_diffs(
    conn: sqlite3.Connection,
    world_id: str,
    nation_id: str,
    *,
    warheads: int,
    event_id: int,
    scope: Visibility = Visibility.SECRET,
    tactical: bool = False,
) -> list[Diff]:
    """Add ``warheads`` to this nation's pile, minting the arsenal if it has none.

    **Insert or adjust, decided here rather than by the caller**, because both callers get
    it wrong in different ways: the technology path is always a first arsenal and the
    production path is never one, and each would grow its own copy of this check.

    Secret by default. A warhead is the one thing every power would rather its rivals
    counted late, and the completion path already renders the atomic programme itself under
    `Visibility.SECRET`.
    """
    row = _arsenal_row(conn, world_id, nation_id)
    if row is None:
        return [
            Diff("arsenal", nation_id, "__insert__", DiffOp.INSERT,
                 {
                     "world_id": world_id, "nation_id": nation_id,
                     "warheads": int(warheads),
                     "delivery": FIRST_ARSENAL_DELIVERY,
                 },
                 reason_event_id=event_id, visibility_scope=scope)
        ]
    diffs = [
        Diff("arsenal", nation_id, "warheads", DiffOp.ADJUST, int(warheads),
             reason_event_id=event_id, visibility_scope=scope)
    ]
    if tactical:
        # A subset, so both move together: `warheads` is the whole pile and `tactical` says
        # how much of it is battlefield. Moving one without the other is how an arsenal
        # comes to disagree with itself.
        diffs.append(
            Diff("arsenal", nation_id, "tactical", DiffOp.ADJUST, int(warheads),
                 reason_event_id=event_id, visibility_scope=scope))
    return diffs


def warhead_lines(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> list[str]:
    """What this country's arsenal is, and what adding to it would cost.

    Silent for a power that cannot: a country with no atomic programme behind it is not
    being offered a warhead, and telling it the price of one it cannot order is the kind of
    noise that makes a briefing worth skimming.
    """
    if not can_build_warheads(conn, world_id, nation_id):
        return []
    row = _arsenal_row(conn, world_id, nation_id)
    held = int(row["warheads"] or 0)
    purse = conn.execute(
        "SELECT treasury, industry FROM nation WHERE world_id = ? AND id = ?",
        (world_id, nation_id)).fetchone()
    treasury = float(purse["treasury"]) if purse else 0.0
    industry = float(purse["industry"]) if purse else 0.0
    return [
        "## Your arsenal",
        f"You hold **{held} warhead(s)**, delivered by {row['delivery']}.",
        f"Name `{WARHEAD_PROJECT}` in `build_orders` to commission another, or "
        f"`{TACTICAL_PROJECT}` for a battlefield weapon. It costs about "
        f"{WARHEAD_TREASURY:.0f} treasury and {WARHEAD_INDUSTRY:.0f} industry and takes "
        f"{WARHEAD_BUILD_DAYS / 30:.0f} months in the same queue as everything else -- so a "
        f"warhead is a steelworks you did not build. You have {treasury:.0f} treasury and "
        f"{industry:.0f} industry.",
        "**The two kinds are the same price and do opposite jobs.** Only strategic warheads "
        "deter -- they are what makes your own ground harder to take, because they threaten "
        "what an attacker cannot accept losing. A battlefield weapon threatens the "
        "battlefield, which an attacker is already standing on: it helps you win a contest "
        "anywhere you are fighting, and it deters nobody. Every one you build is one your "
        "deterrent does not have.",
        "An arsenal is counted by your rivals late and imperfectly, and it costs you every "
        "month you hold it whether or not you ever use it.",
        "",
    ]


def order_warhead(
    conn: sqlite3.Connection, world_id: str, *, nation_id: str, turn: int, date: str,
    tactical: bool = False, repeat: int = 0,
) -> WarheadReport:
    """Put one more warhead in the queue, charged now and delivered in nine months.

    Refuses a country that has not crossed the threshold, with the reason -- a staff told
    only "no" orders the same thing again next turn. Charged at the moment it is laid down,
    like everything else in this queue, so a programme abandoned halfway is money spent.

    ``repeat`` is how many identical orders came before this one this turn -- the same
    ordinal the gambit path takes from ``orders._REPEAT``, and here for the same reason.
    The build id encodes the turn, so without it a second warhead of the same kind in one
    turn collided with the first, the whole batch was refused, and a country that ordered
    three got one. Nobody may build more than one strategic and one tactical warhead per
    turn was never a rule this game has; it was an id.
    """
    from ..state.apply import apply_batch, record_event, unrecord_events
    from ..state.diff import DiffBatch
    from ..state.models import EventKind, EventOrigin, Role

    report = WarheadReport()
    if not can_build_warheads(conn, world_id, nation_id):
        report.problems.append(
            "you have no atomic programme behind you; a warhead is not something a country "
            "orders, it is something it spends sixteen turns learning how to make")
        return report

    row = conn.execute(
        "SELECT name, treasury, industry, capital_region_id FROM nation"
        " WHERE world_id = ? AND id = ?", (world_id, nation_id)).fetchone()
    if row is None:
        return report
    if float(row["treasury"]) < WARHEAD_TREASURY or float(row["industry"]) < WARHEAD_INDUSTRY:
        report.problems.append(
            f"{row['name']} cannot commission a warhead: it costs {WARHEAD_TREASURY:.0f} "
            f"treasury and {WARHEAD_INDUSTRY:.0f} industry, and you have "
            f"{float(row['treasury']):.0f} and {float(row['industry']):.0f}")
        return report

    where = row["capital_region_id"] or conn.execute(
        "SELECT id FROM region WHERE world_id = ? AND owner_nation_id = ?"
        "   AND kind <> 'ocean' ORDER BY id LIMIT 1", (world_id, nation_id)).fetchone()
    region_id = str(where["id"]) if hasattr(where, "keys") else str(where)
    if not region_id:
        report.problems.append(f"{row['name']} has nowhere to build one")
        return report

    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.CONSTRUCTION, origin=EventOrigin.ENGINE, actor_nation_id=nation_id,
        text=(f"{row['name']} lays down a "
              f"{'battlefield' if tactical else 'strategic'} warhead."),
        visibility=Visibility.SECRET, visible_to=(nation_id,))

    build_id = f"c_{nation_id}_{'tac' if tactical else 'str'}warhead_{turn}"
    # Suffixed only from the second one on, so the id of every warhead any existing world
    # already laid down is byte-for-byte the id it had. A fix that renamed the first build
    # would move every fingerprint to solve a problem the first build never had.
    if repeat:
        build_id = f"{build_id}_{repeat + 1}"
    diffs = [
        Diff("construction", build_id, "__insert__", DiffOp.INSERT,
             {
                 "world_id": world_id, "nation_id": nation_id, "region_id": region_id,
                 "project_kind": WARHEAD_PROJECT,
                 "kind": TACTICAL_PROJECT if tactical else WARHEAD_PROJECT,
                 "turns_remaining": 0, "days_remaining": WARHEAD_BUILD_DAYS,
                 "started_turn": turn,
             },
             reason_event_id=event_id, visibility_scope=Visibility.SECRET),
        Diff("nation", nation_id, "treasury", DiffOp.ADJUST, -WARHEAD_TREASURY,
             reason_event_id=event_id, visibility_scope=Visibility.SECRET),
        Diff("nation", nation_id, "industry", DiffOp.ADJUST, -WARHEAD_INDUSTRY,
             reason_event_id=event_id, visibility_scope=Visibility.SECRET),
    ]
    outcome = apply_batch(
        conn,
        DiffBatch(world_id=world_id, turn=turn, proposer_role=Role.ENGINE,
                  proposer_model=None, diffs=tuple(diffs)))
    if outcome.applied:
        report.commissioned.append(
            f"{row['name']} laid down a "
            f"{'battlefield' if tactical else 'strategic'} warhead.")
    else:
        # The "lays down" went on the record first so the diffs could cite it, as in
        # `production.order_construction`; a refused warhead was never laid down.
        unrecord_events(conn, [event_id])
        conn.commit()
        # **`ApplyOutcome` has no `problems`.** This line was an `AttributeError` waiting
        # for a refused warhead, and the id collision above was the thing that reached it:
        # the second warhead of a turn did not "fail silently", it raised -- through a
        # dispatcher whose `except` clause does not name `AttributeError`. Found by
        # reproducing the collision on the unfixed tree; the collision is gone and the
        # refusal path still has to be able to say why.
        report.problems.append(outcome.reason)
    return report
