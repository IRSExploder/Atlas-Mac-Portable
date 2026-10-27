"""The briefing's event window has to be spent on events the briefing will show.

`known_events` takes the most recent N. `_thin_construction` then dropped *other* nations'
public works. Done in that order the cap is not a cap on the briefing at all: the master AI
develops every unattended nation every turn, those events are written late and so hold the
high ids, and the window is ordered by recency -- so the cap filled with construction, the
thinning deleted it, and what was left was whatever few real events had squeezed in. The wars
sat outside the window entirely and no amount of thinning could reach them.

Measured across the recorded runs before the fix:

    pt34e: 243/243 briefings filled the cap and every one showed fewer than the cap
           -- 11.1 events of an allowed 25
    pt34g: 219/219, showing 4.1 of 25; 4573 window slots spent on construction
           that was then thrown away, and 1314 displacements of in-run news --
           including "Germany repudiates the Rhineland demilitarisation
           provisions of Locarno" falling out of Austria's briefing, in a 1936 game

After: 25.0 of 25 on both, with 12.9 pieces of genuine news per briefing on pt34e.

**The citable set is deliberately not narrowed.** `visible_event_ids` answers *what may this
nation cite*, which is a wider question than *what is worth printing*; narrowing it would turn
a display fix into diffs refused by CORE-001. It grew from 25 to ~39-46 per briefing.
"""

from __future__ import annotations

from atlas.engine.views import _briefing_events
from atlas.state import visibility

WORLD = "sandbox"
LIMIT = 25


def _event(conn, kind: str, actor: str | None, text: str, turn: int = 1) -> int:
    cursor = conn.execute(
        "INSERT INTO event (world_id, turn, date, kind, origin, visibility, text,"
        " actor_nation_id) VALUES (?, ?, '1897-01-01', ?, 'ENGINE', 'public', ?, ?)",
        (WORLD, turn, kind, text, actor))
    return int(cursor.lastrowid)


def _spam(conn, count: int, *, turn: int = 1) -> None:
    """The master AI developing everyone else, which is what fills a real window."""
    for i in range(count):
        conn.execute("INSERT OR IGNORE INTO nation (id, world_id, name, colour)"
                     " VALUES (?, ?, ?, '#404040')", (f"n{i}", WORLD, f"Nation {i}"))
        _event(conn, "construction", f"n{i}", f"Nation {i} begins a river port.", turn)
    conn.commit()


# --------------------------------------------------------------- the cap is spent properly


def test_real_news_survives_a_window_full_of_public_works(conn):
    """The regression, in the shape that produced it: the news is written *first* and so has
    the low ids, which is exactly why recency-ordering buried it."""
    war = _event(conn, "war_declared", "karthos", "Karthos declared war on Aurelia.")
    treaty = _event(conn, "treaty_signed", "karthos", "Karthos and Aurelia signed a pact.")
    conn.commit()
    _spam(conn, 40)

    shown, routine, _ = _briefing_events(conn, "aurelia", limit=LIMIT)

    texts = {e["id"] for e in shown}
    assert war in texts, "the war declaration was crowded out by public works"
    assert treaty in texts
    assert routine == 40


def test_the_window_is_filled_with_showable_events(conn):
    """Before the fix this showed 0 of 25 -- the cap was spent and then emptied."""
    for i in range(30):
        _event(conn, "battle", "karthos", f"Fighting in the north, day {i}.")
    conn.commit()
    _spam(conn, 60)

    shown, _routine, _ = _briefing_events(conn, "aurelia", limit=LIMIT)

    assert len(shown) == LIMIT
    assert all(e["kind"] != "construction" for e in shown)


def test_the_viewers_own_construction_still_reaches_it(conn):
    """A nation is deciding what to build, so its own yards are not noise to it."""
    mine = _event(conn, "construction", "aurelia", "Aurelia begins a naval yard.")
    conn.commit()
    _spam(conn, 40)

    shown, routine, _ = _briefing_events(conn, "aurelia", limit=LIMIT)

    assert mine in {e["id"] for e in shown}
    assert routine == 40, "the viewer's own yard was counted as somebody else's"


# ------------------------------------------------------------------ and nothing was lost


def test_everything_previously_citable_is_still_citable(conn):
    """The constraint that keeps this a display fix. `visible_event_ids` is what CORE-001
    checks a diff's citation against, so anything dropped here becomes a refused diff."""
    _spam(conn, 40)
    before = frozenset(
        e["id"] for e in visibility.known_events(conn, "aurelia", limit=LIMIT))

    _shown, _routine, citable = _briefing_events(conn, "aurelia", limit=LIMIT)

    assert before <= citable, f"no longer citable: {sorted(before - citable)}"
    assert len(citable) > len(before), "the citable set should have widened, not merely held"


def test_the_count_covers_the_span_the_briefing_covers(conn):
    """Not the whole game. A footnote counting ten turns of public works beside a window
    reaching two would be a number about a different period."""
    _spam(conn, 5, turn=1)
    for i in range(30):
        _event(conn, "battle", "karthos", f"Fighting, day {i}.", turn=9)
    conn.commit()
    _spam(conn, 6, turn=9)

    shown, routine, _ = _briefing_events(conn, "aurelia", limit=LIMIT)

    assert min(e["turn"] for e in shown) == 9
    assert routine == 6, "the count reached back past the window into turn 1"


def test_the_underlying_reader_is_unchanged_by_default(conn):
    """`known_events` still answers the question it always did unless asked otherwise --
    other callers must not silently start losing events."""
    _spam(conn, 10)

    plain = visibility.known_events(conn, "aurelia", limit=60)
    thinned = visibility.known_events(
        conn, "aurelia", limit=60, without_routine_construction=True)

    assert len(plain) - len(thinned) == 10
