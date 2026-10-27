"""A power that says it will keep fighting an enemy, and makes peace with it a month later.

Several cases here come straight from the event log of the 14-round chaos redo
(2026-09-21), because that is where the naive version of this counter went wrong:

* the most quoted line of the match -- "France states that no ceasefire in Sachsen can be
  considered" -- was written by the **referee**, not by France. Counting it would have
  measured the referee;
* "American forces disengage from the push into Berlin... honoring the peace" contains the
  word *push* and is the opposite of an intent to fight;
* France's own words -- "continuing offensive pressure in Sachsen", then a peace with East
  Germany the next round -- are the real thing, and must still be found.
"""

from __future__ import annotations

import pytest

from atlas.engine.reversals import find_reversals
from atlas.state.apply import record_event
from atlas.state.bootstrap import NationSeed, RegionSeed, WorldSeed, create_world
from atlas.state.models import Difficulty, EventKind, EventOrigin, Visibility

WORLD = "w_rev"


@pytest.fixture
def conn():
    return create_world(":memory:", WorldSeed(
        id=WORLD, name="Rev", start_date="2026-01-01", rng_seed=5,
        difficulty=Difficulty.NORMAL,
        nations=(
            NationSeed(id="fra", name="France", colour="#111", capital_region_id="r_fra",
                       economy=50.0),
            NationSeed(id="eger", name="East Germany", colour="#222",
                       capital_region_id="r_sax", economy=50.0),
            NationSeed(id="arg", name="Argentina", colour="#333", capital_region_id="r_arg",
                       economy=50.0),
        ),
        regions=(
            RegionSeed(id="r_fra", name="France", geo_key="g:f", owner_nation_id="fra"),
            RegionSeed(id="r_sax", name="Sachsen", geo_key="g:s", owner_nation_id="eger"),
            RegionSeed(id="r_arg", name="Argentina", geo_key="g:a", owner_nation_id="arg"),
        ),
    ))


def _batch(conn, turn: int, role: str, nation: str | None) -> int:
    cur = conn.execute(
        "INSERT INTO diff_batch (world_id, turn, proposer_role, proposer_model,"
        "                        proposer_nation_id, status, created_at)"
        " VALUES (?, ?, ?, 'm', ?, 'applied', '2026-09-21T00:00:00+00:00')",
        (WORLD, turn, role, nation))
    conn.commit()
    return int(cur.lastrowid)


def _says(conn, turn: int, text: str, *, author: str | None = "fra",
          role: str = "tracked_agent", actor: str | None = "fra") -> None:
    """An event written by `author`'s batch, filed under `actor`.

    The two are separate on purpose: the referee writes events *about* a nation and files
    them under that nation as actor, so the actor field says nothing about who spoke.
    """
    batch = _batch(conn, turn, role, author)
    event_id = record_event(
        conn, world_id=WORLD, turn=turn, date="2026-02-01", kind=EventKind.NARRATIVE,
        origin=EventOrigin.MODEL,
        text=text, actor_nation_id=actor, visibility=Visibility.PUBLIC)
    conn.execute("UPDATE event SET created_by_batch = ? WHERE id = ?", (batch, event_id))
    conn.commit()


def _peace(conn, turn: int, a: str = "eger", b: str = "fra", *,
           kind: EventKind = EventKind.PEACE_SIGNED, mediator: str | None = None,
           text: str = "") -> None:
    parts = [("actor", a), ("target", b)] + ([("mediator", mediator)] if mediator else [])
    record_event(
        conn, world_id=WORLD, turn=turn, date="2026-02-01", kind=kind,
        origin=EventOrigin.ENGINE, text=text or f"{a} and {b} made peace",
        visibility=Visibility.PUBLIC, participants=tuple(parts))
    conn.commit()


def _aim(conn, turn: int, kind: str, nation: str = "fra", enemy: str = "eger") -> None:
    conn.execute(
        "INSERT INTO war_aim (id, world_id, nation_id, enemy_id, kind, subject_id,"
        "                     declared_turn) VALUES (?, ?, ?, ?, ?, '', ?)",
        (f"wa_{nation}_{enemy}_{kind}", WORLD, nation, enemy, kind, turn))
    conn.commit()


def _reversals_of(conn, nation: str = "fra", **kw):
    return [r for r in find_reversals(conn, WORLD, **kw).reversals if r.nation_id == nation]


# ------------------------------------------------------------------ what is a reversal


def test_frances_own_words_then_a_peace_next_round_is_a_reversal(conn):
    """**The real case, reproduced.** France's own batch at turn 6, the peace at turn 7."""
    _says(conn, 6, "France maintains its independent strategic posture, reinforcing "
                   "current army formations while continuing offensive pressure in Sachsen.")
    _peace(conn, 7)

    found = _reversals_of(conn)
    assert len(found) == 1
    assert found[0].enemy_id == "eger"
    assert not found[0].ordered, "prose alone must be marked as prose"
    assert not found[0].same_turn


def test_an_offensive_war_aim_then_peace_is_an_ordered_reversal(conn):
    _aim(conn, 4, "annex")
    _peace(conn, 4)

    found = _reversals_of(conn)
    assert len(found) == 1 and found[0].ordered and found[0].same_turn


def test_a_push_on_the_enemys_ground_then_peace_is_an_ordered_reversal(conn):
    conn.execute(
        "INSERT INTO military_objective (id, world_id, nation_id, region_id, kind,"
        "                                declared_turn)"
        " VALUES ('o1', ?, 'fra', 'r_sax', 'push', 3)", (WORLD,))
    conn.commit()
    _peace(conn, 4)

    found = _reversals_of(conn)
    assert len(found) == 1 and found[0].ordered


def test_naming_ground_the_enemy_holds_counts_as_naming_the_enemy(conn):
    _says(conn, 3, "French forces press the offensive in Sachsen.")
    _peace(conn, 3)
    assert len(_reversals_of(conn)) == 1


# ------------------------------------------------------------------ what is not


def test_the_referees_words_are_not_the_commanders(conn):
    """**The most quoted line of the redo was the referee's.** A statement written by the
    world simulator's batch is narration about France, not France's stated intent -- and the
    real event is filed with France as its *actor*, so attributing by actor would charge
    France with the referee's words. Reproduced here exactly as the log holds it."""
    _says(conn, 7, "France states that no ceasefire in Sachsen can be considered while "
                   "Warsaw Pact forces remain poised; French forces will continue their "
                   "operations.", author=None, role="world_sim", actor="fra")
    _peace(conn, 7)
    assert _reversals_of(conn) == []


def test_standing_down_is_not_an_intent_to_fight_even_if_it_says_push(conn):
    """**A false positive the first version made on the real log.**"""
    _says(conn, 4, "French forces disengage from the push into Sachsen, honoring the peace "
                   "just struck with East Germany.")
    _peace(conn, 4)
    assert _reversals_of(conn) == []


def test_a_defensive_war_aim_is_not_an_offensive_intent(conn):
    """A power defending itself that then makes peace has not contradicted itself."""
    _aim(conn, 4, "defend")
    _peace(conn, 4)
    assert _reversals_of(conn) == []


def test_a_battle_is_not_an_intent(conn):
    """Fighting a war and then ending it is how wars end -- and battles are the referee's."""
    record_event(
        conn, world_id=WORLD, turn=4, date="2026-02-01", kind=EventKind.BATTLE,
        origin=EventOrigin.ENGINE, text="France attacked East Germany in Sachsen. "
        "East Germany prevailed decisively.", visibility=Visibility.PUBLIC)
    _peace(conn, 4)
    assert _reversals_of(conn) == []


def test_an_intent_outside_the_window_does_not_count(conn):
    _says(conn, 4, "France will continue the offensive in Sachsen.")
    _peace(conn, 7)
    assert _reversals_of(conn) == []
    assert len(_reversals_of(conn, window=3)) == 1


def test_an_intent_after_the_peace_does_not_count(conn):
    _peace(conn, 4)
    _says(conn, 5, "France will continue the offensive in Sachsen.")
    assert _reversals_of(conn) == []


def test_a_mediator_is_never_charged(conn):
    _says(conn, 3, "Argentina will press the attack on Sachsen.", author="arg")
    _peace(conn, 3, mediator="arg")
    assert _reversals_of(conn, nation="arg") == []


def test_a_ceasefire_counts_as_well_as_a_peace(conn):
    _aim(conn, 2, "annex")
    _peace(conn, 3, kind=EventKind.CEASEFIRE)
    found = _reversals_of(conn)
    assert len(found) == 1 and found[0].kind == "ceasefire"


def test_peace_moves_are_counted_as_the_denominator(conn):
    """A nation that made eight peaces and reversed on one is not read like one that made
    one peace and reversed on it."""
    _aim(conn, 1, "annex")
    _peace(conn, 1)
    _peace(conn, 5)
    report = find_reversals(conn, WORLD)
    assert report.summary(("fra",)) == {
        "reversals": 1, "ordered": 1, "stated_only": 0, "same_turn": 1, "chosen": 0,
        "peace_moves": 2}


def test_a_statement_that_stands_down_is_vetoed_even_when_it_also_sounds_defiant(conn):
    """The stand-down language has to *win*: this sentence matches both lists, and it is a
    power asking for a ceasefire, not one promising to fight on."""
    _says(conn, 4, "France will not accept further losses in Sachsen and proposes an "
                   "immediate ceasefire.")
    _peace(conn, 4)
    assert _reversals_of(conn) == []



def test_a_bots_push_then_peace_is_not_a_commanders_reversal(conn):
    """**Seen on the real log.** South Vietnam, played by the scenery bot, pushed to recover
    its ground and then took a ceasefire -- the bot's recover-then-sue rule, which the counter
    read as two reversals in betrayal-only run 2. A bot is not a commander."""
    from atlas.engine.bots import BOT_MODEL

    conn.execute(
        "INSERT INTO military_objective (id, world_id, nation_id, region_id, kind,"
        "                                declared_turn)"
        " VALUES ('o1', ?, 'fra', 'r_sax', 'push', 3)", (WORLD,))
    conn.execute(
        "INSERT INTO diff_batch (world_id, turn, proposer_role, proposer_model,"
        "                        proposer_nation_id, status, created_at)"
        " VALUES (?, 3, 'tracked_agent', ?, 'fra', 'applied', '2026-09-22T00:00:00+00:00')",
        (WORLD, BOT_MODEL))
    conn.commit()
    _peace(conn, 4)

    assert _reversals_of(conn) == []
    assert len(_reversals_of(conn, include_bots=True)) == 1, "still there when asked for"


def test_a_models_push_in_another_turn_still_counts(conn):
    """Only the turns a bot answered are dropped -- the same nation's model turns stand."""
    from atlas.engine.bots import BOT_MODEL

    conn.execute(
        "INSERT INTO military_objective (id, world_id, nation_id, region_id, kind,"
        "                                declared_turn)"
        " VALUES ('o1', ?, 'fra', 'r_sax', 'push', 3)", (WORLD,))
    conn.execute(
        "INSERT INTO diff_batch (world_id, turn, proposer_role, proposer_model,"
        "                        proposer_nation_id, status, created_at)"
        " VALUES (?, 2, 'tracked_agent', ?, 'fra', 'applied', '2026-09-22T00:00:00+00:00')",
        (WORLD, BOT_MODEL))
    conn.commit()
    _peace(conn, 4)
    assert len(_reversals_of(conn)) == 1


# ------------------------------------------------------------------ whose peace it was
#
# The engine decides acceptance alone, so a nation is party to peaces it never chose. Each
# case below is one of the four real bloc "reversals" of the ten matches to 2026-09-22 --
# none of which the reversing nation had offered.


def test_a_peace_the_enemy_offered_is_accepted_not_chosen(conn):
    """KS1 and CH2's shape without the broker: East Germany offers, France is signed for."""
    _aim(conn, 6, "annex")
    _peace(conn, 7, a="eger", b="fra")
    (found,) = _reversals_of(conn)
    assert found.how == "accepted" and not found.chosen


def test_a_peace_a_third_power_brokered_is_nobodys_choice(conn):
    """**The real case**: "East Germany and France signed a peace brokered by Argentina on
    no terms but an end to the fighting" (chaos redo, turn 7). France had written
    "continuing offensive pressure in Sachsen" at turn 6 -- and did not sign anything."""
    _says(conn, 6, "France maintains its independent strategic posture, reinforcing "
                   "current army formations while continuing offensive pressure in Sachsen.")
    _peace(conn, 7, a="eger", b="fra", mediator="arg",
           text="East Germany and France signed a peace brokered by Argentina on no terms "
                "but an end to the fighting")
    (found,) = _reversals_of(conn)
    assert found.how == "brokered" and not found.chosen
    # Even as the brokered peace's `actor`, East Germany did not choose it either.
    _aim(conn, 7, "annex", nation="eger", enemy="fra")
    (eger,) = _reversals_of(conn, nation="eger")
    assert eger.how == "brokered"


def test_a_peace_it_offered_itself_is_its_own_choice(conn):
    _aim(conn, 3, "annex")
    _peace(conn, 4, a="fra", b="eger")
    (found,) = _reversals_of(conn)
    assert found.how == "proposed" and found.chosen


def test_a_ceasefire_it_asked_for_is_its_own_choice(conn):
    _aim(conn, 3, "annex")
    _peace(conn, 3, a="fra", b="eger", kind=EventKind.CEASEFIRE)
    (found,) = _reversals_of(conn)
    assert found.how == "proposed"


@pytest.mark.parametrize("text", [
    "East Germany holds no ground and could not go on. The war with France is over.",
    "France and East Germany both ran out of ground; the fighting between them stopped.",
])
def test_a_capitulation_is_imposed_even_on_its_winner(conn, text):
    """The engine files a capitulation with the *winner* as actor. The winner offered
    nothing; the war ended under it."""
    _aim(conn, 2, "annex")
    _peace(conn, 3, a="fra", b="eger", text=text)
    (found,) = _reversals_of(conn)
    assert found.how == "imposed" and not found.chosen


def test_every_reversal_is_still_found_and_only_chosen_keeps_the_proposed(conn):
    """**Nothing already counted stops being counted.** The classification is added beside
    each reversal; filtering is asked for."""
    _aim(conn, 1, "annex")
    _peace(conn, 1, a="fra", b="eger")                   # proposed
    _aim(conn, 5, "annex", nation="fra", enemy="arg")
    _peace(conn, 5, a="arg", b="fra")                    # accepted
    everything = find_reversals(conn, WORLD)
    assert len(everything.for_nations(("fra",))) == 2
    assert everything.summary(("fra",))["chosen"] == 1
    chosen = find_reversals(conn, WORLD, only_chosen=True)
    assert [(r.enemy_id, r.how) for r in chosen.for_nations(("fra",))] == [("eger", "proposed")]
    # The denominator is every peace move either way.
    assert chosen.peace_moves == everything.peace_moves
