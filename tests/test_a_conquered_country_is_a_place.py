"""After a country is annexed, its name means its land.

Reported from play: "I kept trying to invest in Iraq that I have captured but it keeps
saying that Iraq doesn't exist. How else should I name Iraq if not as Iraq?" The orders read
"Iraq" as the state -- which no longer existed -- rather than as the province the player now
owned, and an investment that did go through funded the national economy and left the
province untouched.

Sandbox Karthos stands in for Iraq: one province once its western half goes to Veyra.
"""

from __future__ import annotations

from atlas.engine.conquest import annex
from atlas.engine.gambit import KINDS, _requirements_met
from atlas.engine.orders import apply_order_effects
from atlas.engine.staff_bots import parse_orders

WORLD, ME, THEM = "sandbox", "aurelia", "karthos"


def _annexed(conn) -> None:
    conn.execute("UPDATE region SET owner_nation_id = 'veyra', controller_nation_id = NULL,"
                 " home_nation_id = 'veyra' WHERE id = 'r_west'")
    conn.execute("UPDATE region SET controller_nation_id = ? WHERE id = 'r_south'", (ME,))
    conn.execute("DELETE FROM unit WHERE nation_id = ?", (THEM,))
    conn.execute("UPDATE nation SET treasury = 200 WHERE id = ?", (ME,))
    conn.commit()
    assert annex(conn, WORLD, nation_id=ME, target=THEM, turn=1, date="1897-05-01").done


def test_the_name_of_an_annexed_country_means_its_land(conn):
    _annexed(conn)
    for text in ("Invest in Karthos", "Rebuild Karthos", "Build rail in Karthos"):
        (action,) = parse_orders(conn, WORLD, nation_id=ME, raw_text=text)["actions"]
        assert action["target_nation"] is None, text
        assert action["to_region"] == "r_south", text


def test_investing_in_a_province_of_yours_develops_it(conn):
    _annexed(conn)
    before = conn.execute("SELECT development FROM region WHERE id = 'r_south'").fetchone()[0]
    actions = parse_orders(conn, WORLD, nation_id=ME, raw_text="Invest in Karthos")["actions"]
    effects = apply_order_effects(conn, WORLD, turn=2, date="1897-06-01", actions=actions,
                                  nation_id=ME, days=30)
    after = conn.execute("SELECT development FROM region WHERE id = 'r_south'").fetchone()[0]
    assert not effects.problems and after > before
    name = conn.execute("SELECT name FROM region WHERE id = 'r_south'").fetchone()[0]
    assert effects.headlines == [f"Aurelia invested in {name}."]


def test_an_operation_aimed_at_a_country_that_is_gone_says_where_its_land_went(conn):
    _annexed(conn)
    kind = next(k for k in KINDS if "needs_target" in k.requires)
    refusal = _requirements_met(conn, WORLD, kind, "veyra", THEM, None, "")
    assert "no longer a country" in refusal.because and "Aurelia" in refusal.because
    assert "name the province" in refusal.instead
