"""An order in a player's own words reaches a mechanic, and the mechanic moves something.

Three gaps, found by asking a no-AI game to do what a player would:

* "Fund the rebels in Karthos" and "host a festival" came back as *no order recognised*,
  with a list of verbs to use instead. Anything no verb matched was refused.
* A player's undertaking that *worked* moved nothing. The player's path handed the gambit
  channel an empty list of consequences, so a successful bribe charged its stake and
  changed no number anywhere.
* The bot's freeform answer never nudged, so imagination was narration only.

Every number here still comes from the engine's own closed vocabularies and caps.
"""

from __future__ import annotations

import pytest

from atlas.engine import staff_bots
from atlas.engine.orders import PLAYER_CONSEQUENCES, _order_evidence, _order_stake
from atlas.engine.gambit import EFFECTS, KINDS

ME, THEM = "aurelia", "karthos"


def _parse(conn, text: str) -> dict:
    return staff_bots.parse_orders(conn, "sandbox", nation_id=ME, raw_text=text)


@pytest.mark.parametrize(("text", "kind"), [
    ("Fund the rebels in Karthos", "incite"),
    ("Launch a disinformation campaign against Karthos", "incite"),
    ("Leak Karthos's secret treaty to the press", "expose"),
    ("Get Karthian generals to defect", "bribe"),
    ("Hack the Karthian power grid", "sabotage"),
])
def test_a_scheme_in_plain_words_is_an_undertaking(conn, text, kind):
    parsed = _parse(conn, text)
    assert not parsed["rejected"], parsed
    (action,) = parsed["actions"]
    assert action["type"] == "gambit" and action["gambit_kind"] == kind
    assert action["target_nation"] == THEM


@pytest.mark.parametrize("text", [
    "Host the Olympic games in the capital",
    "Build a statue of the founder in the capital",
    "Launch a propaganda campaign at home",
])
def test_anything_else_is_still_an_order(conn, text):
    parsed = _parse(conn, text)
    assert not parsed["rejected"], parsed
    assert [a["type"] for a in parsed["actions"]] == ["freeform"]


def test_a_word_or_two_is_not_an_order(conn):
    assert _parse(conn, "asdf")["rejected"]


def test_a_wall_is_a_fortification_not_an_army(conn):
    for (region,) in conn.execute(
            "SELECT id FROM region WHERE owner_nation_id = ? AND kind <> 'ocean'", (ME,)
    ).fetchall():
        conn.execute("INSERT INTO unit (id, world_id, nation_id, region_id, kind, strength)"
                     " VALUES (?, 'sandbox', ?, ?, 'infantry', 5)", (f"u_{region}", ME, region))
    # Aurelia meets Karthos only across the sea; its land border is with Veyra.
    (action,) = _parse(conn, "Build a wall on the Veyran border")["actions"]
    assert action["type"] == "fortify" and not action["target_nation"]
    veyran = {r for (r,) in conn.execute(
        "SELECT id FROM region WHERE owner_nation_id = 'veyra'")}
    assert veyran & staff_bots._adjacent(conn, "sandbox", action["to_region"])


def test_every_undertaking_a_player_can_order_has_consequences_from_the_closed_list():
    kinds = {k.id for k in KINDS}
    for kind, (won, lost) in PLAYER_CONSEQUENCES.items():
        assert kind in kinds
        assert set(won) | set(lost) <= set(EFFECTS)
    assert all(PLAYER_CONSEQUENCES[k][0] for k in ("bribe", "incite", "expose", "sabotage"))


def test_the_wording_sets_the_stake_and_the_circumstances():
    assert _order_stake("Bribe a Karthian minister") == "minor"
    assert _order_stake("A major bribery operation in Karthos") == "serious"
    assert _order_stake("Topple Karthos's government at any cost") == "grave"
    assert "well_funded" in _order_evidence("Fund the rebels in Karthos")
    assert "local_sympathisers" in _order_evidence("Fund the rebels in Karthos")
    assert "rushed" in _order_evidence("Bribe them immediately")


def test_a_successful_bribe_moves_the_target(conn):
    from atlas.engine import orders as orders_module

    conn.execute("UPDATE nation SET treasury = 500 WHERE id = ?", (ME,))
    before = conn.execute(
        "SELECT legitimacy FROM nation WHERE id = ?", (THEM,)).fetchone()[0]
    effects = orders_module.OrderEffects()
    action = {"type": "gambit", "gambit_kind": "bribe", "target_nation": THEM,
              "summary": "Buy a Karthian minister"}
    # Try a few turns' worth of seeds until one lands; the odds are the engine's.
    moved = False
    for turn in range(1, 30):
        orders_module._gambit(conn, "sandbox", turn, "1897-05-01", action, ME, effects)
        after = conn.execute(
            "SELECT legitimacy FROM nation WHERE id = ?", (THEM,)).fetchone()[0]
        if after < before:
            moved = True
            break
    assert moved, effects.problems


def test_a_bot_freeform_answer_moves_the_mood_slightly_and_only_when_it_should(conn):
    festival = staff_bots.freeform_answer(
        conn, "sandbox", nation_id=ME, order_text="Hold a great festival in the capital")
    assert [(n["field"], n["direction"], n["magnitude"]) for n in festival["nudges"]] == [
        ("stability", "up", "slight")]
    crackdown = staff_bots.freeform_answer(
        conn, "sandbox", nation_id=ME, order_text="Impose martial law on the coast")
    assert {(n["field"], n["direction"]) for n in crackdown["nudges"]} == {
        ("stability", "up"), ("legitimacy", "down")}
    aid = staff_bots.freeform_answer(
        conn, "sandbox", nation_id=ME, order_text="Send humanitarian aid to Karthos")
    assert {(n["field"], n["subject"]) for n in aid["nudges"]} == {
        ("relation", f"{ME}|{THEM}")}
    colour = staff_bots.freeform_answer(
        conn, "sandbox", nation_id=ME, order_text="Paint the ministry doors blue")
    assert colour["nudges"] == []
