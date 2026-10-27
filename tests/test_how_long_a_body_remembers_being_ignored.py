"""How long an international body remembers having been ignored, and on whose clock.

A power that was condemned and is back in front of the same body inside the memory window is
a power that ignored it. That is the observable definition of a body being defied, it carries
the largest authority penalty the layer has, and it is the loop the whole layer exists for.

**It was counted in turns.** Six of them -- so six months of world time at monthly pacing and
six *years* at yearly, from identical events, decided by a control that exists for how much
detail somebody wants to watch. That is precisely the fault `SANCTION_MONTHS` was converted
to fix, one constant away in the same module, and this one outlived that sweep by being named
something the audit could not see: the gate in
`tests/test_which_clock_each_duration_is_on.py` finds durations by their name, and `MEMORY`
was not among the words it looked for. The gate whose entire job is to ask which clock a
duration is on had never been shown this duration.

Each test below holds the turn gap fixed at one and varies only the elapsed history, so what
is being measured is the clock and not the vote.
"""

from __future__ import annotations

from atlas.engine.organisation import (
    AUTH_DEFIED,
    DEFIANCE_MEMORY_MONTHS,
    Trigger,
    convene,
    organisations_of,
)
from atlas.state.apply import record_event
from atlas.state.models import EventKind, EventOrigin, Visibility

WORLD = "sandbox"


# --------------------------------------------------------------------------- fixtures


def _body(conn, org_id="unsc", authority=60.0,
          members=("aurelia", "karthos", "veyra"), permanent=()):
    conn.execute(
        "INSERT INTO nation (id, world_id, name, colour, controller)"
        " VALUES (?, 'sandbox', ?, '#888', 'engine')", (org_id, "The Council"))
    conn.execute(
        "INSERT INTO organisation (nation_id, world_id, kind, charter_text, authority)"
        " VALUES (?, 'sandbox', 'universal', 'An assembly of powers.', ?)",
        (org_id, authority))
    for member in members:
        conn.execute(
            "INSERT INTO organisation_member (organisation_id, nation_id, is_permanent)"
            " VALUES (?, ?, ?)", (org_id, member, int(member in permanent)))
    for other in ("aurelia", "karthos", "veyra"):
        for a, b in ((org_id, other), (other, org_id)):
            conn.execute(
                "INSERT OR IGNORE INTO relation (world_id, nation_a, nation_b, score)"
                " VALUES ('sandbox', ?, ?, 0)", (a, b))
    conn.commit()
    return _reload(conn, org_id)


def _reload(conn, org_id="unsc"):
    return next(o for o in organisations_of(conn, WORLD) if o.nation_id == org_id)


def _declares(conn, actor, target, turn, date):
    return record_event(
        conn, world_id=WORLD, turn=turn, date=date, kind=EventKind.WAR_DECLARED,
        origin=EventOrigin.ENGINE, actor_nation_id=actor,
        text=f"{actor} declares war on {target}.", visibility=Visibility.PUBLIC,
        participants=(("actor", actor), ("target", target)),
    )


# ------------------------------------------------------- how long a body remembers


def _sat_twice(conn, *, second_turn, second_date):
    """Sit about the same power twice; return the second session's authority movement."""
    org = _body(conn, authority=90.0)
    cause = _declares(conn, "aurelia", "veyra", 0, "1896-01-01")
    trigger = Trigger("unprovoked_war", "aurelia", "declared war", 2, cause)
    convene(conn, WORLD, org=org, trigger=trigger, turn=1, date="1897-01-01", seed=4,
            payload={"action": "condemn", "justification": "x", "refused": None})
    again = _reload(conn)
    outcome = convene(conn, WORLD, org=again, trigger=trigger, turn=second_turn,
                      date=second_date, seed=4,
                      payload={"action": "condemn", "justification": "x", "refused": None})
    return outcome.authority_after - again.authority


def _defiance_charge(second_date, request):
    """What the defiance gate alone cost, isolated from the vote it rides on.

    Both worlds run the identical first session and the identical second one -- same turn,
    same seed, same standing, so the same roll and the same result. The whole difference
    between them is the elapsed time between the two sittings, so the whole difference in
    authority is the defiance charge.
    """
    from atlas.scenarios.sandbox import sandbox_seed
    from atlas.state.bootstrap import create_world

    moved = []
    for date in (second_date, "2999-01-01"):
        world = create_world(":memory:", sandbox_seed())
        try:
            moved.append(_sat_twice(world, second_turn=2, second_date=date))
        finally:
            world.close()
    return moved[0] - moved[1]


def test_being_defied_is_measured_in_world_time_and_not_in_turns(conn, request):
    """**Six turns meant six months at monthly pacing and six years at yearly.**

    Whether a condemned power has come back in front of the body is the observable definition
    of the body being ignored, and it is the loop the whole layer exists for. It is a fact
    about the world, so the pace control may not decide it -- the rule `SANCTION_MONTHS` was
    converted for, which this constant outlived by being named something the audit's name
    pattern could not see.

    One turn later either way; two different elapsed spans; two different answers. That is
    the mechanic reading the clock it should.
    """
    assert _defiance_charge("1897-03-01", request) == AUTH_DEFIED, "two months later: defied"


def test_the_same_turn_gap_a_long_time_later_is_not_defiance(conn, request):
    assert _defiance_charge("1899-01-01", request) == 0.0, (
        "one turn later, but two years of world time: the body was not defied")


def test_the_window_is_the_stated_number_of_months(conn, request):
    """Either side of :data:`DEFIANCE_MEMORY_MONTHS`, at one turn's remove throughout."""
    assert DEFIANCE_MEMORY_MONTHS == 6.0
    assert _defiance_charge("1897-06-01", request) == AUTH_DEFIED, "five months: inside"
    assert _defiance_charge("1897-08-01", request) == 0.0, "seven months: outside"


def test_a_session_nobody_can_date_is_not_treated_as_defiance(conn, request):
    """An unreadable date decides nothing rather than deciding the harshest thing.

    `doctrines.settle_shifts` answers the same question the same way: a row nobody can date
    does not act by accident. Under-charging is the safe direction here, because this gate
    applies the largest authority penalty the layer has.
    """
    assert _defiance_charge("not-a-date", request) == 0.0


