"""An order chosen on the map is written into the orders box as a sentence, and Advance reads
the box. That only works if every sentence the map writes reads back as the order it was
chosen as -- checked here for every action the map offers, against the bot parser.

The map used to file its orders in a list the box never showed, so the box said one thing
and the turn carried another; reading a typed order then replaced that list and dropped the
map's orders without a word.
"""

from __future__ import annotations

import pytest

from atlas.engine import bots
from atlas.engine.mapactions import actions_for_region
from atlas.engine.parser import parse_orders
from atlas.engine.routing import Router
from atlas.providers.cli_provider import default_role_models
from atlas.providers.mock import MockProvider

ME = "aurelia"


@pytest.fixture(autouse=True)
def bot_parser():
    was = bots.set_bots(roles=bots.SCOPES | bots.STAFF_SCOPES)
    yield
    bots.set_bots(*was)


def _sentence(conn, entry) -> str:
    if entry.needs == "from_region":
        to = conn.execute("SELECT name FROM region WHERE id = ?",
                          (entry.action["to_region"],)).fetchone()["name"]
        return f"Move forces from Northmark into {to}"
    return entry.action["summary"]


async def _read(conn, text: str) -> dict:
    mock = MockProvider()
    parsed = await parse_orders(
        conn, "sandbox", nation_id=ME, raw_text=text,
        providers={k: mock for k in ("claude", "codex", "antigravity")},
        router=Router(pins=default_role_models()))
    assert parsed.actions, (text, parsed.rejected)
    return dict(parsed.actions[0])


def _war(conn, at_war: int) -> None:
    for a, b in ((ME, "karthos"), ("karthos", ME)):
        conn.execute(
            "INSERT INTO relation (world_id, nation_a, nation_b, at_war) VALUES"
            " ('sandbox', ?, ?, ?) ON CONFLICT (nation_a, nation_b)"
            " DO UPDATE SET at_war = excluded.at_war", (a, b, at_war))


@pytest.mark.parametrize("at_war", [0, 1])
async def test_every_map_order_reads_back_as_itself(conn, at_war):
    _war(conn, at_war)
    conn.execute("INSERT INTO unit (id, world_id, nation_id, region_id, kind, strength)"
                 " VALUES ('u_test', 'sandbox', ?, 'r_north', 'infantry', 5)", (ME,))
    if at_war:
        conn.execute("UPDATE region SET controller_nation_id = ? WHERE id = 'r_south'", (ME,))
    else:
        # Karthos beaten and out of the war: all its ground held by Aurelia.
        conn.execute("UPDATE region SET controller_nation_id = ?"
                     " WHERE owner_nation_id = 'karthos'", (ME,))
    seen: set[str] = set()
    for region in ("r_north", "r_coast", "r_south", "r_west", "r_east"):
        for entry in actions_for_region(conn, "sandbox", ME, region):
            if entry.needs and region == "r_north":
                continue  # the forces stand in Northmark; moving them into it is no move
            text = _sentence(conn, entry)
            got = await _read(conn, text)
            want = entry.action
            assert got["type"] == want["type"], (text, got)
            for key in ("target_nation", "gambit_kind"):
                if want.get(key):
                    assert got.get(key) == want[key], (text, key, got)
            place = want.get("to_region") or want.get("region")
            if place and want["type"] not in ("economic_investment",):
                assert place in (got.get("to_region"), got.get("region")), (text, got)
            seen.add(want["type"])
    expected = {"move_units", "fortify", "gambit", "diplomatic_message"}
    expected |= {"sue_for_peace"} if at_war else {"declare_war", "annex"}
    assert expected <= seen, seen
