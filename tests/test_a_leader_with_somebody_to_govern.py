"""Five groups a government answers to, and what it costs to disappoint them.

**The point is that decisions become politically different, not numerically different.** A
battleship and a land reform both moved the same two numbers before this: stability and
legitimacy, for reasons the country never had an opinion about. Now the officer corps and
the people disagree about which one to build, and a leader can see both.

Foundation only. Support exists, moves for real reasons and is visible; nothing here fires a
coup, calls a strike or obstructs a policy. Those read this table and are the next brick.
"""

from __future__ import annotations

import sqlite3

import pytest

from atlas.engine.constituencies import (
    CONSTITUENCIES,
    PER_TURN_CAP,
    REACTIONS,
    assess_constituencies,
    band_of,
    constituency_id_for,
    standing_of,
)
from atlas.scenarios.sandbox import sandbox_seed
from atlas.state.apply import PRIMARY_KEY, record_event
from atlas.state.bootstrap import create_world, fingerprint
from atlas.state.models import EventKind, EventOrigin, Visibility
from atlas.state.replay import rewind_to

WORLD = "sandbox"
A, B = "aurelia", "karthos"


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = create_world(":memory:", sandbox_seed())
    connection.execute("UPDATE world SET turn_number = 1 WHERE id = ?", (WORLD,))
    connection.commit()
    yield connection
    connection.close()


def _support(conn, nation: str = A) -> dict[str, float]:
    return {s.kind: s.support for s in standing_of(conn, WORLD, nation)}


def _happened(conn, kind: EventKind, *, actor: str = A, turn: int = 1,
              text: str = "something happened", participants=()) -> int:
    return record_event(
        conn, world_id=WORLD, turn=turn, date=f"1897-{turn + 3:02d}-01", kind=kind,
        origin=EventOrigin.ENGINE, actor_nation_id=actor, text=text,
        visibility=Visibility.PUBLIC, participants=participants)


def _turn(conn, turn: int = 1, *, days: int = 30) -> list[str]:
    return assess_constituencies(
        conn, WORLD, turn=turn, date=f"1897-{turn + 3:02d}-01", days=days)


# ------------------------------------------------------------------ they exist, per nation


def test_every_nation_has_the_five_from_the_first_turn(conn):
    for nation in (A, B, "veyra"):
        standing = standing_of(conn, WORLD, nation)
        assert [s.kind for s in standing] == [c.kind for c in CONSTITUENCIES]
        assert all(0 <= s.support <= 100 for s in standing)


def test_a_fresh_save_reads_sensible_defaults_and_writes_nothing(conn):
    """**Lazily written.** Five rows for each of 243 nations is twelve hundred rows of
    nothing having happened, and the answer is the same either way."""
    assert conn.execute("SELECT COUNT(*) FROM constituency").fetchone()[0] == 0
    for standing in standing_of(conn, WORLD, A):
        assert 25 <= standing.support <= 75, standing.describe()
        assert standing.band in {"restive", "uneasy", "content"}


def test_the_defaults_follow_from_the_country_rather_than_a_constant(conn):
    """A heavily armed country starts with a pleased officer corps and a poor one with a
    restive population -- derived, so every era and every mod gets it for nothing."""
    conn.execute("UPDATE nation SET military_strength = 60, economy = 20 WHERE id = ?", (A,))
    conn.execute("UPDATE nation SET military_strength = 5, economy = 20 WHERE id = ?", (B,))
    conn.execute("UPDATE nation SET stability = 20 WHERE id = ?", (B,))
    conn.commit()

    assert _support(conn, A)["army"] > _support(conn, B)["army"]
    assert _support(conn, B)["people"] < _support(conn, A)["people"]


def test_the_five_are_five_and_each_says_what_it_wants(conn):
    assert len(CONSTITUENCIES) == 5
    assert {c.kind for c in CONSTITUENCIES} == {
        "army", "industry", "people", "patriots", "establishment"}
    for spec in CONSTITUENCIES:
        assert len(spec.wants) > 30, f"{spec.kind} does not say what it wants"


# ------------------------------------------------------ ...and they react to what you do


def test_mobilisation_pleases_the_army_and_strains_the_people(conn):
    """The clearest case in the layer, and the one the whole design rests on: one act,
    two constituencies, opposite directions."""
    before = _support(conn)
    _happened(conn, EventKind.UNIT_RAISED, text="Aurelia raised three divisions.")
    _turn(conn)
    after = _support(conn)

    assert after["army"] > before["army"]
    assert after["people"] < before["people"]
    assert after["industry"] > before["industry"], "somebody is paid to build them"


def test_a_land_reform_and_a_steelworks_please_different_people(conn):
    """**Politically different, not numerically different.** Two public works, the same
    treasury, opposite politics."""
    conn.execute("UPDATE nation SET is_player = 0 WHERE world_id = ?", (WORLD,))
    conn.commit()
    _happened(conn, EventKind.CONSTRUCTION, actor=A,
              text="Aurelia completed a land_reform in Northmark.")
    _happened(conn, EventKind.CONSTRUCTION, actor=B,
              text="Karthos completed a steelworks in the Basin.")
    before_a, before_b = _support(conn, A), _support(conn, B)
    _turn(conn)
    after_a, after_b = _support(conn, A), _support(conn, B)

    assert after_a["people"] > before_a["people"], "a land reform pleases the people"
    assert after_a["industry"] < before_a["industry"], "and not the men who own the land"
    assert after_b["industry"] > before_b["industry"], "a steelworks pleases industry"
    assert after_b["people"] == pytest.approx(before_b["people"], abs=0.6), (
        "and the people are indifferent to it")


def test_a_declaration_of_war_rallies_the_nationalists_and_frightens_the_people(conn):
    before = _support(conn)
    _happened(conn, EventKind.WAR_DECLARED, text="Aurelia declared war on Karthos.")
    _turn(conn)
    after = _support(conn)

    assert after["patriots"] > before["patriots"] and after["army"] > before["army"]
    assert after["people"] < before["people"]


def test_a_peace_pleases_the_people_and_disappoints_the_officers(conn):
    before = _support(conn)
    _happened(conn, EventKind.PEACE_SIGNED, text="Aurelia and Karthos signed a peace.")
    _turn(conn)
    after = _support(conn)

    assert after["people"] > before["people"]
    assert after["army"] < before["army"]


def test_a_peace_that_signs_ground_away_is_a_different_peace(conn):
    """Read from the treaty's own terms rather than guessed from the kind: the
    nationalists do not mind a war ending, they mind a province going with it."""
    plain = _peace_reaction(conn, ceded=0)
    costly = _peace_reaction(conn, ceded=1)

    assert plain["patriots"] > costly["patriots"], (
        "signing provinces away read the same as an ordinary peace")
    assert costly["patriots"] < 0, "and it should actually anger them"


def _peace_reaction(conn, *, ceded: int) -> dict[str, float]:
    """One turn in a scratch world: what a peace with ``ceded`` provinces does."""
    from atlas.engine.peace import ProposedTerm, settle
    from atlas.engine.wareffort import war_effort_id_for

    scratch = create_world(":memory:", sandbox_seed())
    scratch.execute("UPDATE world SET turn_number = 1 WHERE id = ?", (WORLD,))
    scratch.execute(
        "UPDATE relation SET at_war = 1 WHERE world_id = ? AND nation_a IN (?, ?)"
        "   AND nation_b IN (?, ?)", (WORLD, A, B, A, B))
    for region in ("r_south", "r_west")[:max(1, ceded)]:
        scratch.execute("UPDATE region SET controller_nation_id = ? WHERE id = ?",
                        (A, region))
    scratch.execute("UPDATE nation SET military_strength = 40 WHERE id = ?", (A,))
    scratch.execute("UPDATE nation SET military_strength = 3 WHERE id = ?", (B,))
    for nation, enemy, tired in ((B, A, 95.0), (A, B, 10.0)):
        scratch.execute(
            "INSERT OR REPLACE INTO war_effort (id, world_id, nation_id, enemy_id,"
            " opened_turn, last_turn, casualties, charged_casualties, exhaustion)"
            " VALUES (?, ?, ?, ?, 1, 1, 20, 20, ?)",
            (war_effort_id_for(nation, enemy), WORLD, nation, enemy, tired))
    scratch.commit()

    terms = [ProposedTerm("cede_region", obliged=B, beneficiary=A, subject=region)
             for region in ("r_south", "r_west")[:ceded]]
    before = {s.kind: s.support for s in standing_of(scratch, WORLD, B)}
    settle(scratch, WORLD, proposer=A, target=B, terms=terms, turn=1,
           date="1897-04-01", check_acceptance=False)
    assess_constituencies(scratch, WORLD, turn=1, date="1897-04-01")
    after = {s.kind: s.support for s in standing_of(scratch, WORLD, B)}
    scratch.close()
    return {kind: after[kind] - before[kind] for kind in before}


def test_tearing_up_an_agreement_costs_the_establishment(conn):
    before = _support(conn)
    _happened(conn, EventKind.TREATY_BROKEN, text="Aurelia repudiated the pact.")
    _turn(conn)

    assert _support(conn)["establishment"] < before["establishment"] - 3


def test_nothing_political_happening_moves_nobody_much(conn):
    """A kind absent from the mapping moves nobody, which is the honest default: most of
    what happens in a month is not political."""
    before = _support(conn)
    _happened(conn, EventKind.MOVE, text="A division marched east.")
    _turn(conn)
    after = _support(conn)

    for kind in before:
        assert abs(after[kind] - before[kind]) < 1.0, kind


def test_the_reaction_table_only_names_real_constituencies_and_real_events(conn):
    kinds = {c.kind for c in CONSTITUENCIES}
    for name, reaction in REACTIONS.items():
        assert EventKind(name), f"{name} is not an event kind"
        assert set(reaction.moves) <= kinds, name
        assert reaction.because, name


# ---------------------------------------------------------------------- how it settles


def test_support_drifts_back_toward_the_resting_point(conn):
    _happened(conn, EventKind.WAR_DECLARED, text="Aurelia declared war.")
    _turn(conn, 1)
    angry = _support(conn)["people"]

    for turn in range(2, 26):
        _turn(conn, turn)
    settled = _support(conn)["people"]

    assert settled > angry, "a grievance nobody renews should fade"
    assert abs(settled - 50.0) < abs(angry - 50.0)


def test_one_turn_cannot_move_a_constituency_further_than_the_cap(conn):
    for _ in range(12):
        _happened(conn, EventKind.UNIT_RAISED, text="Aurelia raised a division.")
    before = _support(conn)["army"]
    _turn(conn)

    assert _support(conn)["army"] - before <= PER_TURN_CAP + 0.01


def test_the_bands_are_where_they_say_they_are(conn):
    assert band_of(0) == "hostile"
    assert band_of(19.9) == "hostile" and band_of(20.0) == "restive"
    assert band_of(39.9) == "restive" and band_of(40.0) == "uneasy"
    assert band_of(59.9) == "uneasy" and band_of(60.0) == "content"
    assert band_of(79.9) == "content" and band_of(80.0) == "devoted"


def test_crossing_a_band_is_news_and_a_tenth_of_a_point_is_not(conn):
    quiet = _turn(conn)
    assert quiet == [], "a month in which nothing happened is not news"

    for _ in range(6):
        _happened(conn, EventKind.TREATY_BROKEN, text="Aurelia tore up another pact.")
    said = _turn(conn)
    assert any("establishment" in line for line in said), said


# -------------------------------------------------------------------------- the plumbing


def test_it_rewinds_exactly(conn):
    _happened(conn, EventKind.UNIT_RAISED, turn=1, text="Aurelia raised a division.")
    _turn(conn, 1)
    before = fingerprint(conn, WORLD)
    settled = _support(conn)

    conn.execute("UPDATE world SET turn_number = 2 WHERE id = ?", (WORLD,))
    conn.commit()
    _happened(conn, EventKind.WAR_DECLARED, turn=2, text="Aurelia declared war.")
    _turn(conn, 2)
    assert _support(conn)["patriots"] != settled["patriots"]

    report = rewind_to(conn, WORLD, 1)

    assert report.ok, report.problems
    assert _support(conn) == pytest.approx(settled)
    assert fingerprint(conn, WORLD) == before


def test_the_table_is_diffed_and_fingerprinted(conn):
    from atlas.state.bootstrap import _FINGERPRINT_TABLES

    assert PRIMARY_KEY["constituency"] == ("id",)
    assert "constituency" in {table for table, _order, _scope in _FINGERPRINT_TABLES}
    assert constituency_id_for(A, "army") == constituency_id_for(A, "army")


def test_no_model_may_write_its_own_support(conn):
    """A power that could would govern by declaring itself popular -- the same exploit as
    writing its own exhaustion, with the coup machinery on the other end of it."""
    from atlas.state.diff import diff_batch_schema
    from atlas.state.models import Role
    from atlas.state.permissions import is_engine_only

    for column in ("kind", "nation_id", "support", "last_turn", "because"):
        assert is_engine_only("constituency", column)
    for role in (Role.TRACKED_AGENT, Role.WORLD_SIM, Role.DIPLOMACY):
        assert "constituency" not in str(diff_batch_schema(role=role))


def test_asking_changes_nothing(conn):
    before = fingerprint(conn, WORLD)
    for _ in range(3):
        standing_of(conn, WORLD, A)
    assert fingerprint(conn, WORLD) == before


# --------------------------------------------------------------------------- the briefing


def test_a_leader_is_shown_who_it_has_to_govern(conn):
    from atlas.engine.views import build_tracked_agent_view

    _happened(conn, EventKind.UNIT_RAISED, text="Aurelia raised three divisions.")
    _turn(conn)

    text = build_tracked_agent_view(conn, WORLD, nation_id=A).text
    assert "## Who you have to govern" in text
    for spec in CONSTITUENCIES:
        assert spec.name in text
    assert "not dials you can set" in text, (
        "a staff must not read its own people as a slider to optimise")
    assert "the army was enlarged" in text, "and it should say why they last moved"
