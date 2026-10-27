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


def test_a_war_is_not_announced_again_every_turn(conn):
    """Standing conditions go in the war's status once, not in "For you" every turn."""
    _war(conn, "aurelia", "karthos")
    report = _report(significant=["Aurelia in North is cut off and going short (2.3 -> 2.1)"])
    at_war = SimpleNamespace(subject="You are at war with Karthos", source_event_ids=[],
                             nation_id="aurelia")
    brief = build_brief(report, [], conn=conn, world_id="sandbox", viewer="aurelia",
                        affected=[at_war])

    assert brief.for_you == []
    assert brief.wars[0].lines[-1] == ("Cut off from supply: your forces in North are "
                                       "running short.")


def test_one_coup_is_one_line_however_many_ways_it_was_reported(conn):
    report = _report(fell=["Veyra: the government was removed"])
    report.sessions = ["The government no longer governs Veyra."]
    brief = build_brief(report, [], conn=conn, world_id="sandbox", viewer="aurelia")
    assert [line.text for line in brief.world] == ["Veyra: the government was removed"]


def test_the_feed_drops_air_bulletins_even_about_you():
    from atlas.ui.plain import routine_event

    assert routine_event("battle", "Fighting in the air over Iraq. Turkey holds the sky.",
                         "tur", "tur")
    assert not routine_event("battle", "Turkey attacked Iran in Iran.", "tur", "tur")
    assert routine_event("move", "Russia moved 8 formation(s) toward the fighting.",
                         "rus", "tur")
    assert not routine_event("move", "Turkey moved 4 formation(s) toward the fighting.",
                             "tur", "tur")
