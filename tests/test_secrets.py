"""A programme a rival cannot see, hidden by the machinery that already existed.

There is no new hiding mechanism here, and that is the whole design. An event is written
``secret`` with only its owner in ``event_visibility``; ``visibility.known_events`` has
always filtered on exactly that; and rule INFO-002 has always guaranteed a diff is never
more observable than the event that caused it. So a hidden atomic programme is hidden by
the same three lines that hide a secret treaty, and there is nowhere for a second
mechanism to be forgotten.

Espionage is the other half: not new state, but *permission to see an event that already
happened*.
"""

from __future__ import annotations

import sqlite3

import pytest

from atlas.engine.orders import apply_order_effects
from atlas.engine.production import advance_construction, order_research
from atlas.engine.research import known_technologies, technology_by_id
from atlas.state import visibility


@pytest.fixture
def secretive(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Aurelia with everything it needs to begin an atomic programme.

    **Including the materials, which raising ``industry`` used to leave behind.** The
    sandbox seeds ``resource.stockpile`` at five months of demand, the same invariant
    `create_world` holds for every nation on every real map -- and demand scales with
    industry, so setting industry to 90 in a world stocked for 12 leaves this country
    holding well under a month. That was invisible while `shortfall_of` reached only the
    *count* of concurrent projects; now that it also scales the build rate it would slow
    this yard by a fifth, and this test is about who can see a programme, not about who can
    supply one.
    """
    conn.execute(
        "UPDATE nation SET treasury = 900, manpower = 500, economy = 90, industry = 90"
    )
    conn.execute(
        "UPDATE resource SET stockpile = 90 * 0.11 * 5, produced = 90 * 0.11"
        " WHERE world_id = 'sandbox' AND nation_id = 'aurelia'"
    )
    for tech in ("mass_production", "synthetic_fuel"):
        conn.execute(
            "INSERT INTO technology (world_id, nation_id, tech_id, completed_turn)"
            " VALUES ('sandbox', 'aurelia', ?, 0)",
            (tech,),
        )
    conn.commit()
    return conn


def _begin_secret(secretive, turn=1):
    return order_research(
        secretive, "sandbox", nation_id="aurelia", tech_id="atomic_programme",
        turn=turn, date="1936-02-01",
    )


def _sees(conn, viewer: str) -> str:
    return " ".join(
        str(event["text"]) for event in visibility.known_events(conn, viewer, limit=200)
    )


# --------------------------------------------------------------------------- hidden


def test_the_programme_is_marked_secret_in_the_catalogue():
    tech = technology_by_id("atomic_programme")
    assert tech is not None and tech.secret
    assert tech.requires == ("mass_production", "synthetic_fuel")


def test_a_rival_cannot_see_it_being_begun(secretive):
    assert _begin_secret(secretive).started

    assert "Atomic programme" in _sees(secretive, "aurelia")
    assert "Atomic programme" not in _sees(secretive, "karthos")
    assert "Atomic programme" not in _sees(secretive, "veyra")


def test_an_ordinary_programme_is_visible_to_everybody(secretive):
    order_research(
        secretive, "sandbox", nation_id="aurelia", tech_id="welding", turn=1,
        date="1936-02-01",
    )
    assert "welded" in _sees(secretive, "karthos").lower()


def test_a_rival_cannot_see_it_being_finished(secretive):
    _begin_secret(secretive)
    for turn in range(2, 20):
        advance_construction(secretive, "sandbox", turn=turn, date="1936-02-01", seed=42)

    assert known_technologies(secretive, "sandbox", "aurelia") >= {"atomic_programme"}
    assert "completes Atomic programme" in _sees(secretive, "aurelia")
    assert "Atomic programme" not in _sees(secretive, "karthos")


def test_the_diffs_it_caused_are_no_more_visible_than_the_event(secretive):
    """Rule INFO-002 is what guarantees this, and it predates the programme by months."""
    _begin_secret(secretive)
    for turn in range(2, 20):
        advance_construction(secretive, "sandbox", turn=turn, date="1936-02-01", seed=42)

    scopes = {
        row["visibility_scope"]
        for row in secretive.execute(
            """
            SELECT d.visibility_scope FROM diff d
              JOIN event e ON e.id = d.reason_event_id
             WHERE e.text LIKE '%Atomic programme%' AND d.status = 'applied'
            """
        )
    }
    assert scopes == {"secret"}


def test_hiding_uses_no_mechanism_of_its_own(secretive):
    """The only thing keeping it hidden is a secret event and one visibility row."""
    _begin_secret(secretive)
    row = secretive.execute(
        "SELECT id, visibility FROM event WHERE text LIKE '%Atomic programme%'"
    ).fetchone()
    assert row["visibility"] == "secret"
    watchers = {
        r["nation_id"]
        for r in secretive.execute(
            "SELECT nation_id FROM event_visibility WHERE event_id = ?", (row["id"],)
        )
    }
    assert watchers == {"aurelia"}


# --------------------------------------------------------------------------- espionage


def _network(conn, spy="karthos", target="aurelia", strength=95.0):
    """**A service good enough to bring the papers home.**

    I1 made both the odds and the *resolution* of espionage a function of the network, and
    only an `excellent` one gets the `event_visibility` grant these tests are about -- below
    it the service comes back with a sentence the engine wrote about a secret it could not
    read. That is the deliberate change, so the tests that assert the grant now stage the
    network that earns it rather than relying on a flat coin.
    """
    from atlas.engine.intelligence import network_id_for

    conn.execute(
        "INSERT OR REPLACE INTO intel_network (id, world_id, nation_id,"
        " target_nation_id, strength, opened_turn, last_turn, because)"
        " VALUES (?, 'sandbox', ?, ?, ?, 0, 0, 'the fixture said so')",
        (network_id_for(spy, target), spy, target, strength))
    conn.commit()


def _spy(conn, spy="karthos", target="aurelia", turn=1):
    _network(conn, spy, target)
    return apply_order_effects(
        conn, "sandbox", turn=turn, date="1936-02-01",
        actions=[{"type": "espionage", "summary": "find out what they are building",
                  "target_nation": target}],
        nation_id=spy,
    )


def test_espionage_reveals_an_event_that_already_happened(secretive):
    """Intelligence is not new state. It is permission to see something that occurred."""
    _begin_secret(secretive)
    assert "Atomic programme" not in _sees(secretive, "karthos")

    # The attempt is a seeded draw, so try successive turns until one lands.
    for turn in range(1, 20):
        if _spy(secretive, turn=turn).applied:
            break
    else:
        raise AssertionError("no espionage attempt ever succeeded")

    assert "Atomic programme" in _sees(secretive, "karthos")
    assert "Atomic programme" not in _sees(secretive, "veyra"), (
        "one service's success is not everybody's"
    )


def test_espionage_against_a_country_with_nothing_to_hide_finds_nothing(conn):
    effects = _spy(conn, spy="karthos", target="veyra")
    assert effects.applied == 0
    assert any("learned nothing" in problem for problem in effects.problems)


def test_espionage_naming_nobody_says_so(conn):
    effects = apply_order_effects(
        conn, "sandbox", turn=1, date="1936-02-01",
        actions=[{"type": "espionage", "summary": "spy on somebody"}],
        nation_id="aurelia",
    )
    assert effects.applied == 0
    assert any("whose secrets" in problem for problem in effects.problems)


def test_a_failed_attempt_is_reproducible(secretive):
    """A rewind replays the same intelligence failure, because the draw is the world's."""
    from atlas.sim.resolve import uncertain

    seed = secretive.execute(
        "SELECT rng_seed FROM world WHERE id = 'sandbox'"
    ).fetchone()[0]
    first = uncertain(int(seed), "sandbox", 3, "espionage:karthos:aurelia", 0.45)
    again = uncertain(int(seed), "sandbox", 3, "espionage:karthos:aurelia", 0.45)
    assert first == again


def test_a_stolen_secret_is_given_back_by_a_rewind(secretive):
    """**A reproduced leak, found by the Codex review of phase 2.**

    Espionage grants sight of an *older* event -- by construction, since that is what there
    is to find out. The grant was a direct write, so a rewind deleted the espionage action
    and the espionage event, both of this turn, and left the grant standing: the spy kept a
    secret it learned in a turn that no longer happened, for the rest of the game.

    Nothing caught it because `event_visibility` was not fingerprinted either, so the one
    check that would have noticed could not see the table. Both halves are fixed, and this
    asserts the digest as well as the grant -- the digest is what would catch the next one.
    """
    from atlas.state.bootstrap import fingerprint
    from atlas.state.replay import rewind_to

    _begin_secret(secretive)
    # The turn rows carry the world's own date. `rewind_to` restores `world.world_date`
    # from the turn it lands on, and `world` is fingerprinted -- so scaffolding that dated
    # the turns differently would move the digest for a reason that has nothing to do with
    # what is being tested. (The PT31 rewind section made exactly this slip.)
    today = secretive.execute(
        "SELECT world_date FROM world WHERE id = 'sandbox'").fetchone()[0]
    for number in range(1, 21):
        secretive.execute(
            "INSERT OR IGNORE INTO turn (world_id, number, phase, status, start_date)"
            " VALUES ('sandbox', ?, 'sealed', 'sealed', ?)", (number, today))
    secretive.commit()

    for turn in range(1, 20):
        secretive.execute(
            "UPDATE world SET turn_number = ? WHERE id = 'sandbox'", (turn - 1,))
        secretive.commit()
        before = fingerprint(secretive, "sandbox")
        seen_before = _sees(secretive, "karthos")

        secretive.execute(
            "UPDATE world SET turn_number = ? WHERE id = 'sandbox'", (turn,))
        secretive.commit()
        if _spy(secretive, turn=turn).applied:
            break
    else:
        raise AssertionError("no espionage attempt ever succeeded")

    assert "Atomic programme" in _sees(secretive, "karthos")
    assert fingerprint(secretive, "sandbox") != before, (
        "a secret changing hands has to move the digest, or nothing can police it"
    )

    assert rewind_to(secretive, "sandbox", turn - 1).ok
    assert "Atomic programme" not in _sees(secretive, "karthos"), (
        "the spy kept a secret it learned in a turn that no longer happened"
    )
    assert _sees(secretive, "karthos") == seen_before
    assert fingerprint(secretive, "sandbox") == before


def test_the_grant_is_no_more_visible_than_the_theft(secretive):
    """INFO-002, on the newest diff in the engine: an intelligence coup that announced
    itself would not be one."""
    _begin_secret(secretive)
    for turn in range(1, 20):
        if _spy(secretive, turn=turn).applied:
            break
    else:
        raise AssertionError("no espionage attempt ever succeeded")

    scope = secretive.execute(
        "SELECT d.visibility_scope FROM diff d"
        "  WHERE d.target_table = 'event_visibility' ORDER BY d.id DESC LIMIT 1"
    ).fetchone()
    assert scope is not None, "the grant did not go through the diff pipeline"
    assert scope[0] == "secret"
