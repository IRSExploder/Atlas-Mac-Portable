"""The plain turn summary: your news first, your orders with their outcomes, your wars in
provinces, and only the gravest of the world -- never the engine's plumbing."""

from __future__ import annotations

from types import SimpleNamespace

from atlas.engine.causality import OrderOutcome
from atlas.ui.brief import build_brief


def _report(**fields):
    lines = fields.pop("significant", [])
    base = dict(turn=0, order_outcomes=[], order_records=[], your_orders=[], annexed=[],
                eliminated=[], fell=[], sessions=[])
    base.update(fields)
    return SimpleNamespace(significant=lambda: list(lines), **base)


def _war(conn, a: str, b: str, at_war: int = 1) -> None:
    for x, y in ((a, b), (b, a)):
        conn.execute(
            "INSERT INTO relation (world_id, nation_a, nation_b, at_war) VALUES"
            " ('sandbox', ?, ?, ?) ON CONFLICT (nation_a, nation_b)"
            " DO UPDATE SET at_war = excluded.at_war", (x, y, at_war))


def test_your_news_leads_and_the_plumbing_stays_out(conn):
    report = _report(
        significant=[
            "Aurelia took the sky over Karthos",
            "Karthos could not go on against Aurelia",
            "Rus -> pol (oil): the buyer stopped taking oil at 0.28 a unit",
        ],
        fell=["Veyra: the government was removed"],
    )
    brief = build_brief(report, [], conn=conn, world_id="sandbox", viewer="aurelia")

    assert [line.text for line in brief.for_you] == ["Karthos could not go on against Aurelia"]
    assert [line.text for line in brief.world] == ["Veyra: the government was removed"]


def test_orders_carry_their_outcome_once(conn):
    done = OrderOutcome("aurelia:1:0", order_text="annex Karthos", action_type="annex",
                        result="Aurelia annexed Karthos.")
    refused = OrderOutcome("aurelia:1:1", order_text="raise two armoured divisions",
                           action_type="raise_units", status="refused",
                           reason="the yards hold no more")
    report = _report(significant=["Aurelia annexed Karthos."],
                     order_records=[done, refused], order_outcomes=[refused.rendered()])
    brief = build_brief(report, [], conn=conn, world_id="sandbox", viewer="aurelia")

    assert [(o.mark, o.text, o.detail) for o in brief.orders] == [
        ("✓", "Annex Karthos", ""),  # already told under "For you"
        ("✗", "Raise two armoured divisions", "The yards hold no more."),
    ]
    assert not any("refused" in line.text for line in brief.for_you)


def test_a_war_is_told_in_provinces_and_a_beaten_enemy_can_be_annexed(conn):
    _war(conn, "aurelia", "karthos")
    conn.execute("UPDATE region SET controller_nation_id = 'aurelia' WHERE id = 'r_south'")
    brief = build_brief(_report(), [], conn=conn, world_id="sandbox", viewer="aurelia")

    (war,) = brief.wars
    assert war.enemy == "Karthos"
    assert war.lines[0] == "You occupy 1 of its 2 provinces. It occupies none of yours."
    assert war.trend == "winning"
    assert brief.next_steps == []  # still at war, and Karthos still holds r_west

    conn.execute("UPDATE region SET controller_nation_id = 'aurelia' WHERE id = 'r_west'")
    _war(conn, "aurelia", "karthos", at_war=0)
    brief = build_brief(_report(), [], conn=conn, world_id="sandbox", viewer="aurelia")
    assert brief.wars == []
    assert brief.next_steps == ["You can annex Karthos now: write “Annex Karthos”."]
