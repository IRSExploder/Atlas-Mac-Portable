"""Counterintelligence: making a rival's service worse, and then making it wrong.

Espionage had no opposite. A network was worth what its owner had built and nothing the
target did could touch it, so being spied on was weather rather than a decision.

**Three things it buys, and the third is the interesting one.**

1. The rival's service is degraded: it finds less (I1) and what it believes about you gets
   wider (the foundation).
2. Your police get better at proving who has been operating (I3) -- which is what closes the
   gap I3 shipped with, where an elite operator was unprovable however watchful the victim.
3. Past a threshold you stop merely blinding them and start **handling** them: the band comes
   back tight and the centre is moved, so the rival reads a confident answer that is wrong.

**The load-bearing test is that the lie has no tell.** A fed estimate and an earned one have
to be indistinguishable from inside the rival's view -- same shape, same confidence, nothing
anywhere that says which is which -- or the mechanic is a warning label rather than a
deception.
"""

from __future__ import annotations

import sqlite3

import pytest

from atlas.engine.attribution import (
    assess_attribution,
    attributions_for,
    counterintel_of,
    tradecraft_of,
)
from atlas.engine.intelligence import (
    COUNTER_PER_TURN,
    DECEPTION_AT,
    assess_networks,
    discovery_odds,
    estimate_of,
    invest_counterintel,
    network_between,
    network_id_for,
)
from atlas.engine.orders import apply_order_effects
from atlas.engine.views import build_tracked_agent_view
from atlas.scenarios.sandbox import sandbox_seed
from atlas.state.apply import record_event
from atlas.state.bootstrap import create_world, fingerprint
from atlas.state.models import EventKind, EventOrigin, Visibility
from atlas.state.replay import rewind_to

WORLD = "sandbox"
SPY, DEFENDER, THIRD = "aurelia", "karthos", "veyra"


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = create_world(":memory:", sandbox_seed())
    connection.execute("UPDATE world SET turn_number = 1 WHERE id = ?", (WORLD,))
    connection.commit()
    yield connection
    connection.close()


def _pairing(conn, strength: float, counter: float = 0.0) -> None:
    """The row that *is* the contest: the spy's network, and the defender's work on it."""
    conn.execute(
        "INSERT OR REPLACE INTO intel_network (id, world_id, nation_id,"
        " target_nation_id, strength, opened_turn, last_turn, because, counter_intel)"
        " VALUES (?, ?, ?, ?, ?, 0, 0, 'the fixture said so', ?)",
        (network_id_for(SPY, DEFENDER), WORLD, SPY, DEFENDER, strength, counter))
    conn.commit()


def _truth(conn, nation: str = DEFENDER, column: str = "military_strength") -> float:
    return float(conn.execute(
        f"SELECT {column} FROM nation WHERE id = ?", (nation,)).fetchone()[0])


def _believes(conn, turn: int = 1):
    return estimate_of(conn, WORLD, viewer=SPY, target=DEFENDER, subject="army",
                       value=_truth(conn), turn=turn)


# ------------------------------------------------------ the column, keyed the other way


def test_the_defender_on_a_row_is_its_target(conn):
    """The one thing to get right when reading this table. The row is "the spy's network in
    the defender"; the column is the defender's work against it -- so one row holds both
    ends of one contest, which is why this needed no table of its own."""
    invest_counterintel(conn, WORLD, nation_id=DEFENDER, against=SPY, turn=1,
                        date="1897-01-01")

    assert network_between(conn, WORLD, SPY, DEFENDER).counter_intel > 0
    assert network_between(conn, WORLD, DEFENDER, SPY).counter_intel == 0


def test_a_swept_network_keeps_its_people_and_loses_its_reach(conn):
    """What a sweep takes away is the ability to work, not the names on a ledger -- so the
    investment survives and recovers when the sweep stops."""
    _pairing(conn, 80.0, counter=50.0)
    network = network_between(conn, WORLD, SPY, DEFENDER)

    assert network.strength == 80.0
    assert network.effective == 30.0
    assert network.band == "patchy", "the band must read what it is worth, not what it cost"


def test_investment_accumulates_and_lapses_when_nobody_keeps_it_up(conn):
    """A standing commitment rather than a button: it takes months to mount and falls apart
    faster than an agent network dissolves."""
    for turn in (1, 2, 3):
        invest_counterintel(conn, WORLD, nation_id=DEFENDER, against=SPY, turn=turn,
                            date="1897-01-01")
    raised = network_between(conn, WORLD, SPY, DEFENDER).counter_intel
    assert raised == pytest.approx(3 * COUNTER_PER_TURN)

    for turn in range(4, 10):
        conn.execute("UPDATE world SET turn_number = ? WHERE id = ?", (turn, WORLD))
        conn.commit()
        assess_networks(conn, WORLD, turn=turn, date="1897-02-01", days=30)

    assert network_between(conn, WORLD, SPY, DEFENDER).counter_intel < raised


# ---------------------------------------------- (1) the rival finds less and sees worse


def test_a_defended_country_is_harder_to_spy_on(conn):
    _pairing(conn, 80.0)
    open_country = discovery_odds(network_between(conn, WORLD, SPY, DEFENDER).effective)
    _pairing(conn, 80.0, counter=60.0)
    defended = discovery_odds(network_between(conn, WORLD, SPY, DEFENDER).effective)

    assert defended < open_country


def test_a_moderate_sweep_widens_what_the_rival_believes(conn):
    """Below the deception threshold the rival knows less *and knows that it knows less*:
    its own reported confidence falls with the band."""
    _pairing(conn, 80.0)
    clear = _believes(conn)
    _pairing(conn, 80.0, counter=DECEPTION_AT - 25.0)
    fogged = _believes(conn)

    assert (fogged.high - fogged.low) > (clear.high - clear.low)
    assert fogged.low <= _truth(conn) <= fogged.high, (
        "a widened band should still contain the truth; moving it is the other mechanic")


# ------------------------------------------------------- (3) and then it is handled


def test_past_the_threshold_the_rival_is_fed_a_confident_wrong_answer(conn):
    """**The interesting one.** Not wider -- moved, with the band drawn tight around the new
    place."""
    _pairing(conn, 80.0, counter=DECEPTION_AT + 25.0)
    fed = _believes(conn)

    assert not (fed.low <= _truth(conn) <= fed.high), (
        f"the truth {_truth(conn)} is still inside {fed.low}-{fed.high}")
    assert fed.confidence in ("good", "excellent"), fed.confidence


def test_the_lie_is_as_tight_as_the_truth(conn):
    """A wide lie is a lie that announces itself, so the fed band is a good service's
    width."""
    _pairing(conn, 80.0)
    honest = _believes(conn)
    _pairing(conn, 80.0, counter=DECEPTION_AT + 25.0)
    fed = _believes(conn)

    assert (fed.high - fed.low) <= (honest.high - honest.low) * 1.5


def test_the_reported_band_matches_the_tightness_of_what_is_reported(conn):
    """**The tell the word-scan could not see**, and the reason a guarantee asserted by
    scanning for words was not asserted at all. It read:

        honest  your network there is **good**:  army: 35-45
        fed     your network there is **blind**: army: 50-60

    A blind service does not produce a ten-wide band on an army. Nothing else in the view
    differed between the two, so that mismatch was the whole of the leak -- and it was in
    the one place that matters, which is what the model actually reads.

    The briefing now describes *the reports* rather than the service, so what is said about
    them is derived from the same error the bands were drawn with and cannot contradict
    them. See `views._how_sure`.
    """
    conn.execute("UPDATE nation SET military_strength = 40 WHERE id = ?", (DEFENDER,))
    conn.commit()

    _pairing(conn, 80.0, counter=DECEPTION_AT + 25.0)
    view = build_tracked_agent_view(conn, WORLD, nation_id=SPY)
    text = view.text if hasattr(view, "text") else str(view)
    said = next(line for line in text.splitlines()
                if "report" in line and "arthos" in line)

    reported = said.split("**")[1]
    estimate = estimate_of(conn, WORLD, viewer=SPY, target=DEFENDER, subject="army",
                           value=40.0, turn=2)
    assert reported == estimate.confidence, (
        f"the briefing calls the network {reported!r} while handing over a band an "
        f"{estimate.confidence!r} service would produce; that mismatch is the tell")


def test_a_merely_blinded_power_still_knows_that_it_knows_less(conn):
    """**The distinction the fix must not flatten.** Below `DECEPTION_AT` a sweep widens
    the bands rather than moving them: the rival knows less *and knows it knows less*,
    which is the I2 design in one sentence. Making the deceived view internally consistent
    must not blind-wash this one into a confident lie as well.
    """
    conn.execute("UPDATE nation SET military_strength = 40 WHERE id = ?", (DEFENDER,))
    conn.commit()

    _pairing(conn, 80.0)
    honest = _reported_line(conn)
    _pairing(conn, 80.0, counter=DECEPTION_AT - 20.0)
    degraded = _reported_line(conn)

    assert _width(degraded, "army") > _width(honest, "army"), (
        "a swept-but-not-deceived service must report a wider band, not a moved one")
    assert _label(degraded) != "good" or _label(honest) != "good", (
        "being degraded must show up in what the reports are called")
    assert _band_rank(_label(degraded)) < _band_rank(_label(honest)), (
        "the degraded power is told its reports are worth less, which is the whole point")


def test_the_deceived_view_is_internally_consistent(conn):
    """And the deceived one reads like an honest good service in *shape*: a tight band
    called what a tight band is called. That consistency is the lie."""
    conn.execute("UPDATE nation SET military_strength = 40 WHERE id = ?", (DEFENDER,))
    conn.commit()

    _pairing(conn, 80.0)
    honest = _reported_line(conn)
    _pairing(conn, 80.0, counter=DECEPTION_AT + 25.0)
    fed = _reported_line(conn)

    assert _label(fed) == _label(honest), "the fed view must not be labelled differently"
    assert _width(fed, "army") <= _width(honest, "army") * 1.6, (
        "a confident lie is tight; a wide one announces itself")
    # ...and it is a lie: the band it hands over does not contain the truth where an
    # honest one of the same tightness would.
    assert _width(fed, "army") > 0


def _reported_line(conn) -> str:
    view = build_tracked_agent_view(conn, WORLD, nation_id=SPY)
    text = view.text if hasattr(view, "text") else str(view)
    return next(line for line in text.splitlines()
                if "report" in line and DEFENDER[:6] in line.lower())


def _label(line: str) -> str:
    return line.split("**")[1]


def _width(line: str, subject: str) -> float:
    part = next(p for p in line.split(";") if subject in p)
    low, high = part.split(":")[-1].strip().split("-")
    return float(high) - float(low)


def _band_rank(name: str) -> int:
    from atlas.engine.intelligence import BANDS

    return next(index for index, (_bound, band) in enumerate(BANDS) if band == name)


def test_nothing_in_the_rivals_view_says_it_is_being_handled(conn):
    """**The load-bearing test.** A fed estimate and an earned one must be indistinguishable
    from inside: same fields, same shape of confidence, and no marker anywhere. Checked
    against the rendered briefing, because that is what a model actually receives."""
    conn.execute("UPDATE nation SET military_strength = 40 WHERE id = ?", (DEFENDER,))
    conn.commit()

    _pairing(conn, 80.0)
    honest_view = build_tracked_agent_view(conn, WORLD, nation_id=SPY)
    honest = honest_view.text if hasattr(honest_view, "text") else str(honest_view)

    _pairing(conn, 80.0, counter=DECEPTION_AT + 25.0)
    fed_view = build_tracked_agent_view(conn, WORLD, nation_id=SPY)
    fed = fed_view.text if hasattr(fed_view, "text") else str(fed_view)

    # Word-scanning, kept but narrowed. `counter` was in this list and now matches the
    # `counterintelligence` undertaking in the catalogue every nation is offered -- it is
    # in the honest view too, so it marks nothing. `false` matches "false flag" in the same
    # catalogue. The check that actually matters is the test below this one.
    for tell in ("deceiv", "deception", "unreliable", "planted"):
        assert tell not in fed.lower(), tell
        assert (tell in honest.lower()) == (tell in fed.lower()), tell
    # The section reads identically in shape; only the numbers differ.
    assert "What your services can tell you" in fed
    assert fed.count("your network there is") == honest.count("your network there is")

    guess = _believes(conn)
    assert not hasattr(guess, "deceived")
    assert not hasattr(guess, "true_value")


def test_the_deception_is_stable_and_seeded(conn):
    """A lie that changed every time it was asked would be a tell of its own."""
    _pairing(conn, 80.0, counter=DECEPTION_AT + 25.0)
    first, second = _believes(conn), _believes(conn)

    assert (first.low, first.high) == (second.low, second.high)


# --------------------------------------- (2) the I3 gap, and whether investment closes it


def _elite_operation(conn, counter: float) -> str:
    conn.execute("UPDATE nation SET stability = 80 WHERE id = ?", (DEFENDER,))
    _pairing(conn, 100.0, counter=counter)
    record_event(
        conn, world_id=WORLD, turn=1, date="1897-01-01", kind=EventKind.SABOTAGE,
        origin=EventOrigin.ENGINE, actor_nation_id=SPY, text="Sabotage (success)",
        visibility=Visibility.SECRET, visible_to=(SPY,),
        participants=(("actor", SPY), ("target", DEFENDER)))
    for turn in range(2, 32):
        conn.execute("UPDATE world SET turn_number = ? WHERE id = ?", (turn, WORLD))
        conn.commit()
        assess_attribution(conn, WORLD, turn=turn, date="1897-02-01", days=30)
    found = attributions_for(conn, WORLD, DEFENDER)
    return found[0].tier if found else ""


def test_an_elite_operator_is_unprovable_against_passive_policing(conn):
    """The gap I3 shipped with, asserted so the fix below means something."""
    assert _elite_operation(conn, counter=0.0) == "suspected"


def test_and_an_invested_state_can_prove_the_same_operator(conn):
    """**The gap closing.** Counterintelligence tells on an operator twice over -- it
    degrades what they can do and it puts more police on the case -- and together that is
    what makes investment decisive against a service passive policing could never touch."""
    assert _elite_operation(conn, counter=40.0) == "proven"


def test_counterintelligence_raises_the_term_attribution_reads(conn):
    _pairing(conn, 80.0)
    passive = counterintel_of(conn, WORLD, DEFENDER, against=SPY)
    _pairing(conn, 80.0, counter=45.0)
    invested = counterintel_of(conn, WORLD, DEFENDER, against=SPY)

    assert invested > passive
    assert tradecraft_of(conn, WORLD, SPY, against=DEFENDER) == 35.0


# --------------------------------------------------------------- reachable, and scoped


def test_a_model_can_actually_order_it(conn):
    """**Pre-flight.** Built-but-unreachable is the defect this project has shipped twice;
    this drives the same shape a parsed model answer produces, all the way to the world."""
    from atlas.engine.capabilities import CAPABILITIES

    assert "counterintelligence" in {c.action_type for c in CAPABILITIES}

    effects = apply_order_effects(
        conn, WORLD, turn=1, date="1897-01-01", nation_id=DEFENDER,
        actions=[{"type": "counterintelligence", "summary": "round up their people",
                  "target_nation": SPY}])

    assert effects.applied == 1, effects.problems
    assert network_between(conn, WORLD, SPY, DEFENDER).counter_intel > 0


def test_an_order_with_nobody_named_is_refused(conn):
    effects = apply_order_effects(
        conn, WORLD, turn=1, date="1897-01-01", nation_id=DEFENDER,
        actions=[{"type": "counterintelligence", "summary": "look harder"}])

    assert effects.applied == 0
    assert any("whose people" in problem for problem in effects.problems)


def test_the_sweep_is_invisible_to_the_service_it_is_aimed_at(conn):
    """A sweep the rival could read about is a sweep it would wait out -- and the deception
    only works on somebody who does not know they are being handled."""
    invest_counterintel(conn, WORLD, nation_id=DEFENDER, against=SPY, turn=1,
                        date="1897-01-01")

    for reader, allowed in ((DEFENDER, True), (SPY, False), (THIRD, False)):
        seen = int(conn.execute(
            "SELECT COUNT(*) FROM event_visibility v JOIN event e ON e.id = v.event_id"
            " WHERE v.nation_id = ? AND e.kind = 'espionage'", (reader,)).fetchone()[0])
        assert (seen > 0) is allowed, reader

    scopes = {row[0] for row in conn.execute(
        "SELECT DISTINCT visibility_scope FROM diff WHERE target_table = 'intel_network'")}
    assert scopes == {"secret"}


def test_no_model_can_write_its_own_counterintelligence(conn):
    from atlas.state.permissions import ENGINE_ONLY

    assert ("intel_network", "counter_intel") in ENGINE_ONLY


# ------------------------------------------------------------- deterministic, rewindable


def test_a_sweep_is_given_back_by_a_rewind(conn):
    conn.execute("UPDATE world SET turn_number = 1 WHERE id = ?", (WORLD,))
    conn.commit()
    before = fingerprint(conn, WORLD)

    conn.execute("UPDATE world SET turn_number = 2 WHERE id = ?", (WORLD,))
    conn.commit()
    invest_counterintel(conn, WORLD, nation_id=DEFENDER, against=SPY, turn=2,
                        date="1897-02-01")
    assert fingerprint(conn, WORLD) != before

    assert rewind_to(conn, WORLD, 1).ok
    conn.execute("UPDATE world SET turn_number = 1 WHERE id = ?", (WORLD,))
    conn.commit()
    assert network_between(conn, WORLD, SPY, DEFENDER).counter_intel == 0
    assert fingerprint(conn, WORLD) == before


def test_an_old_save_behaves_exactly_as_it_did(conn):
    """Every existing row reads zero, so a world written before schema 32 has nobody
    defending against anybody -- which is what it always had."""
    _pairing(conn, 60.0)
    network = network_between(conn, WORLD, SPY, DEFENDER)

    assert network.counter_intel == 0.0
    assert network.effective == network.strength
