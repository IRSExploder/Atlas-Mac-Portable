"""The Council layer, driven through a hundred and twenty randomly-generated histories.

The case-by-case tests next door each pin one thing that went wrong. This is the other half:
a sweep that does not know what it is looking for, over worlds nobody designed. Wars declared
and settled and declared again, grievances that appear and lapse, ground taken, powers that
are members and powers that are not, bodies universal and allied, standing anywhere from
nothing to a hundred, and pacing that changes underneath it from monthly to yearly.

**What it does and does not catch is worth stating, because the first draft of this file
caught nothing at all and passed anyway.** Of the five faults this layer was carrying, it
reproduces exactly one on its own -- the sanction that announces a bite it did not take --
and only after the sweep was made to start some powers with nothing left to take, which is
where that fault lives. The casus-belli leak needs a batch to be refused, which nothing here
induces, so that invariant stands as a guard rather than as a reproduction. And all three
trigger faults are invisible to it by nature: which war is the right war is a judgement no
invariant can make.

So what it checks is what is true of *any* history:

* a body is never put in the dock, and the accused is always a power that exists;
* authority and odds stay inside their ranges, and the gravest trigger sorts first, because
  `turn_engine` takes `triggers[0]` and nothing else;
* a sanction never announces a bite it did not take, and never takes an economy below the
  floor or a treasury below nothing;
* a casus belli exists only alongside the resolution that granted it -- the leak that let a
  refused batch arm every member of the Council;
* and no relation leaves the range its CHECK constraint permits, which is what a condemnation
  against a universally-loathed power would do if it were not clamped.

Seeded, so the hundred and twenty worlds are the same hundred and twenty every run.
"""

from __future__ import annotations

import random

import pytest

from atlas.engine.organisation import (
    ACTIONS,
    apply_sanctions,
    convene,
    has_jurisdiction,
    organisations_of,
    triggers_this_turn,
)
from atlas.engine.pacing import after_months
from atlas.scenarios.sandbox import sandbox_seed
from atlas.state.apply import record_event
from atlas.state.bootstrap import create_world
from atlas.state.models import EventKind, EventOrigin, Visibility

WORLD = "sandbox"
POWERS = ("aurelia", "karthos", "veyra")

#: Worlds, and turns in each. Small enough to be a second of the suite, wide enough that the
#: outcome census below is not luck.
WORLDS, TURNS = 120, 24


def _seed_body(conn, rng):
    kind = rng.choice(("universal", "universal", "alliance"))
    conn.execute(
        "INSERT INTO nation (id, world_id, name, colour, controller)"
        " VALUES ('unsc','sandbox','Council','#888','engine')")
    conn.execute(
        "INSERT INTO organisation (nation_id, world_id, kind, charter_text, authority)"
        " VALUES ('unsc','sandbox',?,'charter',?)", (kind, rng.uniform(0, 100)))
    for member in rng.sample(POWERS, rng.randint(1, 3)):
        conn.execute(
            "INSERT INTO organisation_member (organisation_id, nation_id, is_permanent)"
            " VALUES ('unsc', ?, ?)", (member, int(rng.random() < 0.4)))
    for a in (*POWERS, "unsc"):
        for b in (*POWERS, "unsc"):
            if a != b:
                conn.execute(
                    "INSERT OR IGNORE INTO relation (world_id, nation_a, nation_b, score)"
                    " VALUES ('sandbox', ?, ?, ?)", (a, b, rng.uniform(-100, 100)))

    # **Some of these powers begin with nothing left to take**, which is not decoration.
    # The interesting end of a sanctions regime is the end where it has already taken
    # everything, and a sweep that only ever starts powers at a comfortable economy never
    # reaches it -- so the invariant about announcements with no charge behind them would
    # hold for want of a case rather than because the code was right. Measured: without
    # this the sweep passes against the unfixed engine.
    for power in POWERS:
        if rng.random() < 0.5:
            conn.execute(
                "UPDATE nation SET economy = ?, treasury = ? WHERE id = ?",
                (rng.choice((1.0, 1.0, 1.2)), rng.choice((0.0, 0.0, 0.4)), power))
    conn.commit()


def _at_war(conn, a, b, fighting):
    for x, y in ((a, b), (b, a)):
        conn.execute(
            "UPDATE relation SET at_war = ? WHERE nation_a = ? AND nation_b = ?",
            (int(fighting), x, y))


def _some_history(conn, rng, turn, date):
    """Whatever a turn might throw up: a war, a peace, a grievance, ground taken."""
    for _ in range(rng.randint(0, 2)):
        a, b = rng.sample(POWERS, 2)
        roll = rng.random()
        if roll < 0.45:
            record_event(conn, world_id=WORLD, turn=turn, date=date,
                         kind=EventKind.WAR_DECLARED, origin=EventOrigin.ENGINE,
                         actor_nation_id=a, text=f"{a} declares war on {b}.",
                         visibility=Visibility.PUBLIC,
                         participants=(("actor", a), ("target", b)))
            _at_war(conn, a, b, True)
        elif roll < 0.65:
            _at_war(conn, a, b, False)
        elif roll < 0.85:
            record_event(conn, world_id=WORLD, turn=turn, date=date,
                         kind=EventKind.CASUS_BELLI, origin=EventOrigin.ENGINE,
                         actor_nation_id=a, text="a grievance",
                         visibility=Visibility.PUBLIC,
                         participants=(("actor", a), ("target", b)))
        else:
            record_event(conn, world_id=WORLD, turn=turn, date=date,
                         kind=EventKind.TERRITORY_TRANSFER, origin=EventOrigin.ENGINE,
                         actor_nation_id=a, text="ground was taken",
                         visibility=Visibility.PUBLIC)
    conn.commit()


def _run_one_world(rng, census, problems):
    conn = create_world(":memory:", sandbox_seed())
    try:
        _seed_body(conn, rng)
        date = "1897-01-01"
        for turn in range(1, TURNS + 1):
            _some_history(conn, rng, turn, date)

            triggers = triggers_this_turn(conn, WORLD, turn)
            for trigger in triggers:
                if trigger.accused == "unsc":
                    problems.append("a body was put in its own dock")
                if trigger.accused not in POWERS:
                    problems.append(f"accused {trigger.accused!r} is not a power")
                if trigger.severity not in (1, 2, 3):
                    problems.append(f"severity {trigger.severity} is outside 1..3")
                if trigger.cause_event_id <= 0:
                    problems.append(f"a {trigger.kind} trigger cites no cause")
            if list(triggers) != sorted(triggers, key=lambda t: -t.severity):
                problems.append("triggers are not gravest-first")

            if triggers:
                org = next(o for o in organisations_of(conn, WORLD)
                           if o.nation_id == "unsc")
                trigger = triggers[0]
                if org.credible and has_jurisdiction(
                        conn, "unsc", org.kind, trigger.accused):
                    action = rng.choice([a.id for a in ACTIONS] + ["none"])
                    outcome = convene(
                        conn, WORLD, org=org, trigger=trigger, turn=turn, date=date,
                        seed=rng.randrange(10_000),
                        payload={"action": action, "justification": "a communique",
                                 "refused": None if action != "none" else "not ours"})
                    census["sessions"] += 1
                    census[outcome.result or "declined"] = census.get(
                        outcome.result or "declined", 0) + 1
                    if not 0.0 <= outcome.authority_after <= 100.0:
                        problems.append(f"authority {outcome.authority_after}")
                    if not 0.0 <= outcome.odds <= 1.0:
                        problems.append(f"odds {outcome.odds}")

            apply_sanctions(conn, WORLD, turn=turn, date=date,
                            days=rng.choice((30, 90, 365)))
            date = after_months(date, rng.choice((1, 3, 12)))

        _check_the_world(conn, problems)
    finally:
        conn.close()


def _check_the_world(conn, problems):
    authority = float(conn.execute(
        "SELECT authority FROM organisation WHERE nation_id = 'unsc'").fetchone()[0])
    if not 0.0 <= authority <= 100.0:
        problems.append(f"a body ended on authority {authority}")

    for row in conn.execute("SELECT id, economy, treasury FROM nation WHERE id <> 'unsc'"):
        if float(row["economy"]) < 1.0:
            problems.append(f"{row['id']} sanctioned below the economy floor: {row['economy']}")
        if float(row["treasury"]) < 0.0:
            problems.append(f"{row['id']} sanctioned into debt: {row['treasury']}")

    for row in conn.execute("SELECT nation_a, nation_b, score FROM relation"):
        if not -100.0 <= float(row["score"]) <= 100.0:
            problems.append(
                f"regard {row['nation_a']}->{row['nation_b']} is {row['score']}")

    hollow = conn.execute(
        """
        SELECT COUNT(*) FROM event e
         WHERE e.kind = 'economic_reform'
           AND NOT EXISTS (SELECT 1 FROM diff d
                            WHERE d.reason_event_id = e.id AND d.status = 'applied')
        """).fetchone()[0]
    if hollow:
        problems.append(f"{hollow} sanction line(s) with no charge behind them")

    orphan = conn.execute(
        """
        SELECT COUNT(*) FROM event e
         WHERE e.kind = 'casus_belli' AND e.origin = 'ENGINE'
           AND EXISTS (SELECT 1 FROM event_cause c WHERE c.event_id = e.id)
           AND NOT EXISTS (SELECT 1 FROM event_cause c JOIN event s ON s.id = c.cause_id
                            WHERE c.event_id = e.id AND s.actor_nation_id = 'unsc')
        """).fetchone()[0]
    if orphan:
        problems.append(f"{orphan} casus belli granted by no recorded resolution")


@pytest.fixture(scope="module")
def swept():
    census: dict[str, int] = {"sessions": 0}
    problems: list[str] = []
    outer = random.Random(20260822)
    for _ in range(WORLDS):
        _run_one_world(random.Random(outer.randrange(1 << 30)), census, problems)
    return census, problems


def test_no_history_makes_the_layer_write_something_impossible(swept):
    census, problems = swept

    assert not problems, (
        f"{len(problems)} invariant violation(s) across {WORLDS} worlds; "
        f"first few: {problems[:5]}")


def test_the_sweep_actually_swept(swept):
    """A sweep that finds nothing because it did nothing passes for the wrong reason.

    Every outcome a session can have must have happened, or the census is measuring a code
    path that never opened rather than a layer that never broke.
    """
    census, _problems = swept

    assert census["sessions"] > 200, census
    for result in ("passed", "watered_down", "failed", "vetoed"):
        assert census.get(result, 0) > 10, (result, census)
