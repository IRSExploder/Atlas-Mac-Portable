"""Covert proxy support: deniability made mechanical instead of flavour.

`support.py` already priced advisors and intelligence as "the deniable ones", and already
reasoned in its own comments about the point where the other side stops treating a patron as
a neutral -- and every shipment it wrote was `Visibility.PUBLIC`, so there was nothing to
deny and nothing for anybody to work out. Its comment said so: *secret support would be a
second mechanic -- flagged, not built.*

**Deniability is attribution.** A covert backing is a secret event shaped like an operation:
the patron as actor, the client as beneficiary, and every one of the client's enemies as a
target. That is the triple `attribution._open_acts` already recognises, so the tier climbs
while the backing is sustained without widening any kind list -- and once it is worn through,
`exposure` prices the scandal exactly as it prices any other operation that was blown.

**What deniability buys is time, not immunity.** And it never buys belligerence either way:
nothing on this path can reach `relation.at_war`, which is the guarantee proxy support has
had since it was built.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from atlas.engine.attribution import assess_attribution, attributions_for
from atlas.engine.exposure import assess_exposure, fallout_of
from atlas.engine.intelligence import network_id_for
from atlas.engine.support import DENIABLE_KINDS, declare_support, support_of
from atlas.scenarios.sandbox import sandbox_seed
from atlas.state.bootstrap import create_world, fingerprint
from atlas.state.diff import diff_batch_schema, parse_batch
from atlas.state.models import Role
from atlas.state.replay import rewind_to

WORLD = "sandbox"
PATRON, CLIENT, ENEMY = "aurelia", "karthos", "veyra"


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = create_world(":memory:", sandbox_seed())
    connection.execute("UPDATE world SET turn_number = 1 WHERE id = ?", (WORLD,))
    for a, b in ((CLIENT, ENEMY), (ENEMY, CLIENT)):
        connection.execute(
            "UPDATE relation SET at_war = 1 WHERE world_id = ? AND nation_a = ?"
            "   AND nation_b = ?", (WORLD, a, b))
    connection.execute("UPDATE nation SET treasury = 500, industry = 60")
    connection.commit()
    yield connection
    connection.close()


def _back(conn, *, covert: bool, kind: str = "advisors", level: float = 2.0,
          turn: int = 1):
    return declare_support(
        conn, WORLD, patron_id=PATRON, declared={CLIENT: {kind: level}},
        turn=turn, date="1897-01-01", covert=covert)


def _sees(conn, viewer: str) -> int:
    return int(conn.execute(
        "SELECT COUNT(*) FROM event e WHERE e.world_id = ? AND e.kind = 'narrative'"
        "   AND e.actor_nation_id = ? AND (e.visibility = 'public'"
        "        OR EXISTS (SELECT 1 FROM event_visibility v"
        "                    WHERE v.event_id = e.id AND v.nation_id = ?))",
        (WORLD, PATRON, viewer)).fetchone()[0])


# ------------------------------------------------------------------ what may go quietly


def test_only_the_kinds_this_module_already_called_deniable_may(conn):
    """Not a new judgement: `support.DENIABLE_KINDS` has said which two since it was
    written, and this is the first thing to act on it."""
    assert set(DENIABLE_KINDS) == {"advisors", "intelligence"}

    for kind in sorted(DENIABLE_KINDS):
        assert _back(conn, covert=True, kind=kind).refused == []


def test_materiel_and_money_cannot_be_sent_quietly(conn):
    """A tank is a tank the other side can photograph, and money leaves a ledger."""
    for kind in ("equipment", "air_defence", "funding"):
        report = _back(conn, covert=True, kind=kind)
        assert report.refused, kind
        assert any("quietly" in problem for problem in report.refused), kind


def test_an_overt_shipment_is_still_overt(conn):
    """The default is unchanged, which is what stops this being a rebalance."""
    _back(conn, covert=False)

    assert _sees(conn, ENEMY) == 1


# ------------------------------------------------------- and what quiet actually means


def test_a_covert_shipment_is_read_by_nobody_it_is_hidden_from(conn):
    assert _back(conn, covert=True).refused == []

    assert _sees(conn, PATRON) == 1, "a patron cannot read its own orders"
    assert _sees(conn, CLIENT) == 1, "a client does not know who is arming it"
    assert _sees(conn, ENEMY) == 0, "the whole point"


def test_the_backing_still_reaches_the_client(conn):
    """Deniability is about who reads it, not about whether the crates arrive."""
    _back(conn, covert=True)

    assert support_of(conn, WORLD, CLIENT) == {"advisors": 2.0}


def test_covert_backing_never_makes_the_patron_a_belligerent(conn):
    """The guarantee proxy support has had since it was built, kept."""
    _back(conn, covert=True)

    assert conn.execute(
        "SELECT COUNT(*) FROM relation WHERE world_id = ? AND nation_a = ?"
        "   AND at_war = 1", (WORLD, PATRON)).fetchone()[0] == 0


def test_the_diffs_take_the_scope_of_the_shipment(conn):
    """INFO-002: a covert cause cannot produce overt state, and a public support row would
    be the shipment announcing itself through the back door."""
    _back(conn, covert=True)

    scopes = {row[0] for row in conn.execute(
        "SELECT DISTINCT visibility_scope FROM diff WHERE target_table = 'support'")}
    assert scopes == {"secret"}


# --------------------------------------------------- at some point they stop pretending


def test_the_enemy_works_it_out_while_the_backing_is_sustained(conn):
    """**Deniability is time, not immunity.** The tier climbs exactly as it does for any
    other covert operation, because a covert backing *is* one as far as attribution is
    concerned."""
    conn.execute("UPDATE nation SET stability = 85 WHERE id = ?", (ENEMY,))
    conn.execute(
        "INSERT OR REPLACE INTO intel_network (id, world_id, nation_id,"
        " target_nation_id, strength, opened_turn, last_turn, because, counter_intel)"
        " VALUES (?, ?, ?, ?, 10, 0, 0, 'staged', 30)",
        (network_id_for(PATRON, ENEMY), WORLD, PATRON, ENEMY))
    conn.commit()
    _back(conn, covert=True)

    seen = []
    for turn in range(2, 16):
        conn.execute("UPDATE world SET turn_number = ? WHERE id = ?", (turn, WORLD))
        conn.commit()
        assess_attribution(conn, WORLD, turn=turn, date="1897-02-01", days=30)
        held = attributions_for(conn, WORLD, ENEMY)
        if held:
            seen.append(held[0].tier)

    assert seen, "the enemy never formed any view of who was arming its opponent"
    assert seen[0] in ("none", "suspected")
    assert seen[-1] == "proven", seen[-1]


def test_an_overt_shipment_is_not_something_to_attribute(conn):
    """There is nothing to work out about a war somebody is fighting openly."""
    _back(conn, covert=False)
    for turn in range(2, 10):
        conn.execute("UPDATE world SET turn_number = ? WHERE id = ?", (turn, WORLD))
        conn.commit()
        assess_attribution(conn, WORLD, turn=turn, date="1897-02-01", days=30)

    assert not attributions_for(conn, WORLD, ENEMY)


# ------------------------------------------------------------ and if it is blown


def test_a_blown_backing_runs_the_exposure_fallout(conn):
    conn.execute("UPDATE nation SET stability = 85 WHERE id = ?", (ENEMY,))
    conn.execute(
        "INSERT OR REPLACE INTO intel_network (id, world_id, nation_id,"
        " target_nation_id, strength, opened_turn, last_turn, because, counter_intel)"
        " VALUES (?, ?, ?, ?, 10, 0, 0, 'staged', 80)",
        (network_id_for(PATRON, ENEMY), WORLD, PATRON, ENEMY))
    conn.commit()
    _back(conn, covert=True)
    before = conn.execute(
        "SELECT score FROM relation WHERE world_id = ? AND nation_a = ? AND nation_b = ?",
        (WORLD, ENEMY, PATRON)).fetchone()[0]

    for turn in range(2, 14):
        conn.execute("UPDATE world SET turn_number = ? WHERE id = ?", (turn, WORLD))
        conn.commit()
        if assess_exposure(conn, WORLD, turn=turn, date="1897-02-01", seed=5).blown:
            break
    else:
        pytest.fail("a backing against a service like that was never blown")

    assert conn.execute(
        "SELECT COUNT(*) FROM exposure WHERE world_id = ?", (WORLD,)).fetchone()[0] == 1
    assert conn.execute(
        "SELECT score FROM relation WHERE world_id = ? AND nation_a = ? AND nation_b = ?",
        (WORLD, ENEMY, PATRON)).fetchone()[0] < before
    assert conn.execute(
        "SELECT COUNT(*) FROM world_fact WHERE world_id = ? AND subject_id = ?",
        (WORLD, PATRON)).fetchone()[0] > 0


def test_being_caught_arming_somebody_is_weighed_as_such(conn):
    """A patron running a war it denied is not a bribe. It has its own weight on the
    importance scale, above every operation but wrecking something."""
    plain = fallout_of(conn, WORLD, event_id=1, kind="narrative", actor=PATRON,
                       victim=ENEMY)
    caught = fallout_of(conn, WORLD, event_id=1, kind="narrative", actor=PATRON,
                        victim=ENEMY, backing=True)

    assert caught.importance > plain.importance


# ----------------------------------------------------------- reachable, and rewindable


def test_a_model_can_choose_covert_over_overt():
    """**Pre-flight.** Its own property rather than a flag inside `support`: the existing
    channel is {nation: {kind: number}} and a boolean in there would break the shape a
    provider is handed."""
    schema = diff_batch_schema(role=Role.TRACKED_AGENT)
    assert "covert_support" in schema["properties"], "no model can back anybody quietly"

    described = json.dumps(schema["properties"]["covert_support"])
    for kind in sorted(DENIABLE_KINDS):
        assert kind in described
    assert "does NOT put you at war" in described

    batch = parse_batch(
        json.dumps({"diffs": [], "events": [],
                    "covert_support": {CLIENT: {"advisors": 2}}}),
        world_id=WORLD, turn=1, role=Role.TRACKED_AGENT, model="test",
        nation_id=PATRON)
    assert batch.covert_support == {CLIENT: {"advisors": 2}}
    assert batch.support == {}, "the two channels are separate decisions"


def test_the_turn_engine_actually_calls_it():
    """A channel nothing reads is the defect this project shipped twice."""
    from pathlib import Path

    source = Path("src/atlas/engine/turn_engine.py").read_text(encoding="utf-8")
    assert "batch.covert_support" in source
    assert "covert=True" in source


def test_a_covert_backing_is_given_back_by_a_rewind(conn):
    conn.execute("UPDATE world SET turn_number = 1 WHERE id = ?", (WORLD,))
    conn.commit()
    before = fingerprint(conn, WORLD)

    conn.execute("UPDATE world SET turn_number = 2 WHERE id = ?", (WORLD,))
    conn.commit()
    _back(conn, covert=True, turn=2)
    assert fingerprint(conn, WORLD) != before

    assert rewind_to(conn, WORLD, 1).ok
    conn.execute("UPDATE world SET turn_number = 1 WHERE id = ?", (WORLD,))
    conn.commit()
    assert support_of(conn, WORLD, CLIENT) == {}
    assert fingerprint(conn, WORLD) == before
