"""A siege of your own ground is called off in the turn it becomes one.

**The bug was a gate, not a mechanic.** `decay_idle_contests` has always dropped a
self-besieging contest whoever pressed it and whenever it was last pressed. Its caller asked
the wrong question first:

    SELECT COUNT(*) FROM contest WHERE world_id = ? AND last_turn < ?

A contest pressed *this* turn has ``last_turn == turn``, so a province that changed hands
while it was being fought over left a contest whose attacker now held it, pressed this turn,
therefore not idle, therefore never looked at -- until the next pass, when it had gone quiet
and was dropped. That is why it always self-corrected and never stuck.

For that one turn the invariant was right: the world contained a province besieging itself,
and the briefing rendered it as *"you hold it, you are 55% of the way to taking it"*, which
is a sentence a staff acts on. `_resolve_one` refuses to *open* such a contest -- PT34d found
what that costs -- and ground changes hands by half a dozen routes that never touch the
resolver: a recapture, a ceded province going home in a peace, a colony taken back, an
armistice restoring what it restores. PT43, PT45, PT46 and PT48 all caught it live on South
Vietnam at Quang Tri.

The gate now also asks whether any contest's attacker holds the ground, so the
reconciliation runs against post-transfer ownership in the same pass that moved it.
"""

from __future__ import annotations

import sqlite3

import pytest

from atlas.engine.resolution import ResolutionReport, _decay_unpressed, decay_idle_contests
from atlas.scenarios.sandbox import sandbox_seed
from atlas.state.bootstrap import create_world, fingerprint
from atlas.state.replay import rewind_to
from atlas.testing.invariants import Phase, violations

WORLD = "sandbox"
ATTACKER, DEFENDER, THIRD = "aurelia", "karthos", "veyra"
TURN = 5


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = create_world(":memory:", sandbox_seed())
    connection.execute("UPDATE world SET turn_number = ? WHERE id = ?", (TURN, WORLD))
    connection.commit()
    yield connection
    connection.close()


def _ground(conn, owner: str) -> str:
    return str(conn.execute(
        "SELECT id FROM region WHERE world_id = ? AND owner_nation_id = ?"
        "   AND kind <> 'ocean' ORDER BY id LIMIT 1", (WORLD, owner)).fetchone()[0])


def _contest(conn, region: str, *, attacker: str, defender: str, pressed: int,
             pressure: float = 0.29) -> None:
    conn.execute(
        "INSERT INTO contest (id, world_id, region_id, attacker_id, defender_id,"
        " pressure, opened_turn, last_turn) VALUES ('k_test', ?, ?, ?, ?, ?, 4, ?)",
        (WORLD, region, attacker, defender, pressure, pressed))
    conn.commit()


def _hand_over(conn, region: str, to: str) -> None:
    """Ground changes hands, by any of the routes that do not go through the resolver."""
    conn.execute("UPDATE region SET controller_nation_id = ? WHERE world_id = ? AND id = ?",
                 (to, WORLD, region))
    conn.commit()


def _sweep(conn, turn: int = TURN) -> None:
    _decay_unpressed(conn, WORLD, turn=turn, date="1897-05-01", report=ResolutionReport())


def _contests(conn) -> int:
    return int(conn.execute(
        "SELECT COUNT(*) FROM contest WHERE world_id = ?", (WORLD,)).fetchone()[0])


# ------------------------------------------------------------------ the case it came from


def test_ground_taken_while_it_was_being_fought_over_leaves_no_self_siege(conn):
    """**The Quang Tri shape, minimally.** A contest pressed this turn, and the ground under
    it changes hands in the same turn."""
    region = _ground(conn, DEFENDER)
    _contest(conn, region, attacker=ATTACKER, defender=DEFENDER, pressed=TURN)
    _hand_over(conn, region, ATTACKER)
    assert violations(conn, WORLD, Phase.REST), "the fixture did not reproduce the case"

    _sweep(conn)

    assert violations(conn, WORLD, Phase.REST) == []
    assert _contests(conn) == 0


def test_the_old_gate_is_the_thing_that_was_wrong(conn):
    """Pins the fix at the place it was made. The gate has to notice a contest whose
    attacker holds the ground even when nothing at all is idle -- which is exactly the
    condition the old query could not express."""
    region = _ground(conn, DEFENDER)
    _contest(conn, region, attacker=ATTACKER, defender=DEFENDER, pressed=TURN)
    _hand_over(conn, region, ATTACKER)

    idle = int(conn.execute(
        "SELECT COUNT(*) FROM contest WHERE world_id = ? AND last_turn < ?",
        (WORLD, TURN)).fetchone()[0])
    assert idle == 0, "the old gate would have counted zero and returned"

    _sweep(conn)
    assert _contests(conn) == 0, "the new gate has to look anyway"


def test_it_is_reconciled_in_the_same_turn_and_not_the_next(conn):
    """The whole of the fix: no one-turn window, however brief."""
    region = _ground(conn, DEFENDER)
    _contest(conn, region, attacker=ATTACKER, defender=DEFENDER, pressed=TURN)
    _hand_over(conn, region, ATTACKER)

    _sweep(conn, turn=TURN)

    assert violations(conn, WORLD, Phase.REST) == [], (
        "the self-siege survived the turn it was created in")


# --------------------------------------------------------- and a real siege is untouched


def test_a_genuine_siege_still_stands(conn):
    """An enemy besieging ground it does not hold is the mechanic, and nothing here may
    touch it."""
    region = _ground(conn, DEFENDER)
    _contest(conn, region, attacker=ATTACKER, defender=DEFENDER, pressed=TURN)

    _sweep(conn)

    assert _contests(conn) == 1
    assert float(conn.execute(
        "SELECT pressure FROM contest WHERE id = 'k_test'").fetchone()[0]) == 0.29


def test_a_genuine_siege_left_alone_still_fades_exactly_as_before(conn):
    """The idle path is unchanged: a front nobody stood on this turn loses momentum."""
    region = _ground(conn, DEFENDER)
    _contest(conn, region, attacker=ATTACKER, defender=DEFENDER, pressed=TURN - 1)

    _sweep(conn)

    after = conn.execute("SELECT pressure FROM contest WHERE id = 'k_test'").fetchone()
    assert after is not None, "an idle contest must fade, not vanish, while it has pressure"
    assert float(after[0]) < 0.29


def test_ground_handed_to_a_third_party_leaves_the_siege_alone(conn):
    """Only the *attacker* holding it makes a contest meaningless. A province changing
    hands between two other powers is still ground somebody is fighting for."""
    region = _ground(conn, DEFENDER)
    _contest(conn, region, attacker=ATTACKER, defender=DEFENDER, pressed=TURN)
    _hand_over(conn, region, THIRD)

    _sweep(conn)

    assert _contests(conn) == 1


def test_an_ordinary_transfer_with_no_contest_invents_nothing(conn):
    region = _ground(conn, DEFENDER)
    _hand_over(conn, region, ATTACKER)

    _sweep(conn)

    assert _contests(conn) == 0
    assert violations(conn, WORLD, Phase.REST) == []


def test_a_quiet_turn_writes_no_event(conn):
    """The gate still returns early when there is nothing to say, so a turn with no idle
    front and no self-siege does not narrate one."""
    before = int(conn.execute(
        "SELECT COUNT(*) FROM event WHERE world_id = ?", (WORLD,)).fetchone()[0])

    _sweep(conn)

    assert int(conn.execute(
        "SELECT COUNT(*) FROM event WHERE world_id = ?", (WORLD,)).fetchone()[0]) == before


def test_the_sentence_matches_what_actually_happened(conn):
    """A turn whose only finding is a siege that ate its own ground did not have a front go
    quiet, and the old sentence said it did."""
    region = _ground(conn, DEFENDER)
    _contest(conn, region, attacker=ATTACKER, defender=DEFENDER, pressed=TURN)
    _hand_over(conn, region, ATTACKER)

    _sweep(conn)

    said = str(conn.execute(
        "SELECT text FROM event WHERE world_id = ? ORDER BY id DESC LIMIT 1",
        (WORLD,)).fetchone()[0])
    assert "called off" in said
    assert "unpressed" not in said


def test_a_turn_with_both_still_reads_as_it_always_did(conn):
    """Every turn that does have idle fronts keeps its original sentence, which is what
    stops this being a prompt change to the whole game."""
    region = _ground(conn, DEFENDER)
    _contest(conn, region, attacker=ATTACKER, defender=DEFENDER, pressed=TURN - 1)
    _hand_over(conn, region, ATTACKER)

    _sweep(conn)

    said = str(conn.execute(
        "SELECT text FROM event WHERE world_id = ? ORDER BY id DESC LIMIT 1",
        (WORLD,)).fetchone()[0])
    assert said == "Fronts left unpressed this season lose their momentum."


# ------------------------------------------------------ deterministic and rewindable


def test_the_drop_is_a_diff_and_a_rewind_puts_it_back(conn):
    region = _ground(conn, DEFENDER)
    _contest(conn, region, attacker=ATTACKER, defender=DEFENDER, pressed=TURN)
    conn.execute("UPDATE world SET turn_number = ? WHERE id = ?", (TURN, WORLD))
    conn.commit()
    before = fingerprint(conn, WORLD)

    conn.execute("UPDATE world SET turn_number = ? WHERE id = ?", (TURN + 1, WORLD))
    conn.commit()
    _hand_over(conn, region, ATTACKER)
    _sweep(conn, turn=TURN + 1)
    assert _contests(conn) == 0

    assert rewind_to(conn, WORLD, TURN).ok
    conn.execute("UPDATE world SET turn_number = ? WHERE id = ?", (TURN, WORLD))
    conn.execute("UPDATE region SET controller_nation_id = NULL WHERE id = ?", (region,))
    conn.commit()
    assert _contests(conn) == 1, "the contest was not restored by the rewind"
    assert fingerprint(conn, WORLD) == before


def test_the_same_world_reconciles_the_same_way(conn):
    other = create_world(":memory:", sandbox_seed())
    try:
        digests = []
        for world in (conn, other):
            world.execute("UPDATE world SET turn_number = ? WHERE id = ?", (TURN, WORLD))
            world.commit()
            region = _ground(world, DEFENDER)
            _contest(world, region, attacker=ATTACKER, defender=DEFENDER, pressed=TURN)
            _hand_over(world, region, ATTACKER)
            _sweep(world)
            digests.append(fingerprint(world, WORLD))
        assert digests[0] == digests[1]
    finally:
        other.close()


def test_the_dropper_itself_never_needed_changing():
    """`decay_idle_contests` was right all along -- its self-siege branch runs before the
    idleness check. Pinned so a later tidy-up cannot fold the two together and quietly
    reintroduce the window."""
    import inspect

    source = inspect.getsource(decay_idle_contests)
    self_siege = source.index('if str(row["holder"] or "") == str(row["attacker_id"]):')
    idle_check = source.index('if int(row["last_turn"]) >= turn:')
    assert self_siege < idle_check, (
        "the self-siege drop must be decided before idleness, or a pressed contest on "
        "ground its attacker now holds is skipped again")


# ========================================================== and the half that was missed

# **The first fix moved the reconciliation to the end of the RESOLUTION and its docstring
# called that "the end of the turn". It is not.** Three passes move a province after the
# fighting is resolved, and each can make a live contest self-besieging with nothing left
# to look again:
#
#   * `unrest.assess_partisans`    -- the flag goes back up in occupied ground
#   * `peace.honour_peace_terms`   -- an occupied province goes home in a settlement
#   * `decolonisation.decolonise`  -- a colony becomes a country, reachable from a tracked
#                                     nation's own turn, which is the last thing in a jump
#
# That gap is why PT51 reported South Vietnam besieging Quang Tri after the first fix had
# shipped, exactly as PT42, PT45, PT46 and PT48 had before it.


def _name(conn, region: str) -> str:
    return str(conn.execute("SELECT name FROM region WHERE id = ?", (region,)).fetchone()[0])


def _partisans_rise(conn, region: str, home: str) -> None:
    """Exactly what `unrest.assess_partisans` writes when an uprising succeeds."""
    from atlas.state.apply import apply_batch, record_event
    from atlas.state.diff import Diff, DiffBatch
    from atlas.state.models import DiffOp, EventKind, EventOrigin, Role, Visibility

    event_id = record_event(
        conn, world_id=WORLD, turn=TURN, date="1897-05-01", kind=EventKind.NARRATIVE,
        origin=EventOrigin.ENGINE,
        text="Partisans rose in occupied territory and drove the occupier's flag back down.",
        visibility=Visibility.PUBLIC)
    apply_batch(conn, DiffBatch(
        world_id=WORLD, turn=TURN, proposer_role=Role.ENGINE, proposer_model=None,
        diffs=(Diff("region", region, "controller_nation_id", DiffOp.SET, home,
                    reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),)))


def _reconcile(conn, turn: int = TURN) -> list[str]:
    from atlas.engine.resolution import reconcile_self_sieges

    return reconcile_self_sieges(conn, WORLD, turn=turn, date="1897-05-01")


def test_the_live_case_reproduced_partisans_hand_back_ground_being_retaken(conn):
    """**The Quang Tri case, as it actually happens.**

    South Vietnam's own core is occupied by North Vietnam and South Vietnam is fighting to
    take it back -- a perfectly ordinary siege. Then partisans rise in that province and put
    the flag back up, *after* the resolution has already reconciled its contests. South
    Vietnam now holds the ground it is besieging, and `captured` requires that the attacker
    does not hold it, so that contest can never end.
    """
    region = _ground(conn, ATTACKER)
    _hand_over(conn, region, DEFENDER)          # the occupation
    _contest(conn, region, attacker=ATTACKER, defender=DEFENDER, pressed=TURN,
             pressure=0.55)
    _sweep(conn)                                 # the resolution reconciles: nothing wrong
    assert _contests(conn) == 1
    assert not violations(conn, WORLD, Phase.REST)

    _partisans_rise(conn, region, ATTACKER)      # ...and then the ground goes home

    assert violations(conn, WORLD, Phase.REST), "the world is now in the broken state"

    assert _reconcile(conn) == [
        f"the siege of {_name(conn, region)} ended: it is already held"]
    assert not violations(conn, WORLD, Phase.REST)
    assert _contests(conn) == 0


def test_a_peace_term_sending_a_province_home_is_reconciled_too(conn):
    """The second of the three late routes: a settlement clears the occupation."""
    region = _ground(conn, ATTACKER)
    _hand_over(conn, region, DEFENDER)
    _contest(conn, region, attacker=ATTACKER, defender=DEFENDER, pressed=TURN)
    # `peace.honour_peace_terms` clears `controller_nation_id`, so the owner holds it again.
    conn.execute("UPDATE region SET controller_nation_id = NULL WHERE id = ?", (region,))
    conn.commit()

    assert violations(conn, WORLD, Phase.REST)
    _reconcile(conn)

    assert not violations(conn, WORLD, Phase.REST)


def test_a_colony_becoming_a_country_is_reconciled_too(conn):
    """The third, and the latest of all: `decolonise` is reachable from a tracked nation's
    own turn, which is the last thing that happens in a jump."""
    region = _ground(conn, DEFENDER)
    _contest(conn, region, attacker=ATTACKER, defender=DEFENDER, pressed=TURN)
    conn.execute("UPDATE region SET owner_nation_id = ?, controller_nation_id = NULL"
                 " WHERE id = ?", (ATTACKER, region))
    conn.commit()

    assert violations(conn, WORLD, Phase.REST)
    _reconcile(conn)

    assert not violations(conn, WORLD, Phase.REST)


def test_the_reconciliation_runs_after_every_pass_that_can_move_a_province():
    """**The structural half, so a fourth mover cannot reopen this.**

    Five modules write a region's holder. Every one of them must be called before the
    end-of-turn reconciliation, or a province can move with nothing left to look again --
    which is the exact defect, and it survived one fix because the ordering was assumed
    rather than checked.
    """
    import pathlib
    import re

    source = pathlib.Path("src/atlas/engine/turn_engine.py").read_text(encoding="utf-8")
    reconcile_at = source.index("reconcile_self_sieges(\n")
    for pass_name in ("assess_partisans(", "honour_peace_terms(", "_run_tracked_agents("):
        assert source.index(pass_name) < reconcile_at, (
            f"{pass_name} moves a province after the reconciliation and nothing looks again")

    # And the writers are the five we believe they are: a sixth would need this list
    # extended and the ordering above checked against it.
    writers = {
        path.name for path in pathlib.Path("src/atlas/engine").glob("*.py")
        if re.search(r'Diff\(\s*"region",[^)]*"(controller|owner)_nation_id"',
                     path.read_text(encoding="utf-8"), re.S)
    }
    assert writers == {"decolonisation.py", "objectives.py", "peace.py", "resolution.py",
                       "unrest.py"}, writers

    # **The fifth, checked rather than assumed.** `objectives._hand_back` (behind
    # ATLAS_VOLUNTARY_RETURN) returns occupied ground to its owner. It is reached only
    # through `declare_objectives`, and the turn engine calls that only inside
    # `_run_tracked_agents` -- already required above to run before the reconciliation.
    # (EVE's call is in the round, before the jump that reconciles.)
    calls = [m.start() for m in re.finditer(r"declare_objectives\(", source)]
    tracked = source.index("async def _run_tracked_agents(")
    # Its end: the next method, or the next module-level function (it is the class's last).
    after = re.compile(r"\n(    )?(async )?def ").search(source, tracked + 1).start()
    assert calls and all(tracked < c < after for c in calls), (
        "declare_objectives is called outside _run_tracked_agents, and it can move a "
        "province (objectives._hand_back); check it runs before the reconciliation")


def test_a_genuine_siege_survives_the_end_of_turn_pass(conn):
    """The reconciliation must not tidy away a war."""
    region = _ground(conn, DEFENDER)
    _contest(conn, region, attacker=ATTACKER, defender=DEFENDER, pressed=TURN)

    assert _reconcile(conn) == []
    assert _contests(conn) == 1


def test_the_end_of_turn_pass_writes_no_event_on_a_quiet_turn(conn):
    """It runs every turn of every game, so it must be silent when there is nothing to say
    -- an event per turn saying nothing happened is noise in every briefing."""
    before = int(conn.execute(
        "SELECT COUNT(*) FROM event WHERE world_id = ?", (WORLD,)).fetchone()[0])

    assert _reconcile(conn) == []

    assert int(conn.execute(
        "SELECT COUNT(*) FROM event WHERE world_id = ?", (WORLD,)).fetchone()[0]) == before


def test_the_end_of_turn_drop_is_a_diff_and_a_rewind_puts_the_siege_back(conn):
    """It is fingerprinted state, so it goes through the pipeline like everything else.

    The region change is put back by hand, exactly as the sibling test above does it: the
    helper moves the ground with raw SQL because the real movers are four other modules,
    and a rewind can only undo what went through the pipeline.
    """
    region = _ground(conn, DEFENDER)
    _contest(conn, region, attacker=ATTACKER, defender=DEFENDER, pressed=TURN)
    before = fingerprint(conn, WORLD)

    conn.execute("UPDATE world SET turn_number = ? WHERE id = ?", (TURN + 1, WORLD))
    conn.commit()
    _hand_over(conn, region, ATTACKER)
    _reconcile(conn, turn=TURN + 1)
    assert _contests(conn) == 0

    assert rewind_to(conn, WORLD, TURN).ok
    conn.execute("UPDATE world SET turn_number = ? WHERE id = ?", (TURN, WORLD))
    conn.execute("UPDATE region SET controller_nation_id = NULL WHERE id = ?", (region,))
    conn.commit()

    assert _contests(conn) == 1, "the contest was not restored by the rewind"
    assert fingerprint(conn, WORLD) == before


def test_the_end_of_turn_pass_reconciles_the_same_way_every_run():
    """Ordered by contest id, so a replay is identical every run."""
    def run() -> list[str]:
        world = create_world(":memory:", sandbox_seed())
        world.execute("UPDATE world SET turn_number = ? WHERE id = ?", (TURN, WORLD))
        for index, owner in enumerate((DEFENDER, THIRD)):
            region = _ground(world, owner)
            world.execute(
                "INSERT INTO contest (id, world_id, region_id, attacker_id, defender_id,"
                " pressure, opened_turn, last_turn) VALUES (?, ?, ?, ?, ?, 0.3, 4, ?)",
                (f"k_{index}", WORLD, region, ATTACKER, owner, TURN))
            world.execute("UPDATE region SET controller_nation_id = ? WHERE id = ?",
                          (ATTACKER, region))
        world.commit()
        try:
            return _reconcile(world)
        finally:
            world.close()

    first, second = run(), run()

    assert len(first) == 2
    assert first == second
