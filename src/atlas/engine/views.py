"""Building the role-specific view each call receives.

This is where "state is the message bus" becomes real. A model never sees the database,
never sees another model's output, and never carries anything between calls. It receives a
rendered view assembled here and returns diffs. Everything it appears to remember is
something this module put back in front of it.

**Views are built field by field from an allowlist.** Never by serialising a row and
removing what should not be there -- that inverts the failure mode. An allowlist that
misses a field produces an obviously incomplete briefing; a denylist that misses a field
leaks it, silently, forever.

**Markdown rather than JSON.** The output is prose a model reads, not a structure it
parses; JSON here spends tokens on punctuation and reads worse. The *response* is JSON,
schema-enforced by the CLI -- the asymmetry is deliberate.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass

from ..state import referee, visibility
from ..state.models import DIFFICULTY_PROFILES, Difficulty, Role, TreatyStatus
from ..state.rules.economy import REFORM_MULTIPLIER, SOFT_CAPS
from . import consent, embargo, objectives, refusals
from .air import air_rules_block
from .capabilities import advisable, prompt_menu
from .decolonisation import colonial_lines
from .gambit import gambit_rules_block
from .leaders import leader_of
from .naval import amphibious_reach
from .nuclear import nuclear_balance_lines
from .objectives import objectives_of
from .pacing import over, span_of
from .reference import label, label_of
from .resolution import settleable_wars
from .support import support_lines
from .tainted import render
from .unrest import held_foreign_provinces
from .untrusted import sanitize, wrap_untrusted

__all__ = [
    "ADVISABLE_ACTIONS",
    "economy_write_rules",
    "RELATION_WRITE_RULES",
    "RenderedView",
    "build_advisor_view",
    "build_diplomacy_view",
    "build_parser_view",
    "build_tracked_agent_view",
    "build_world_sim_view",
]


@dataclass(frozen=True, slots=True)
class RenderedView:
    role: Role
    text: str
    #: The nation this view was built for. ``None`` for the omniscient referee.
    viewer: str | None
    #: Event ids the viewer could see, so a citation can be checked against what it knew.
    visible_event_ids: frozenset[int] = frozenset()

    def __len__(self) -> int:
        return len(self.text)


#: The rules a model must respect when it writes a `relation.score` diff. In the first
#: real-model run these were the commonest wasted proposals by far -- 28 rejections across
#: three guardrails -- because the briefings never stated the bounds. Surfacing them here
#: (the discoverability pattern from decision 11) is meant to cut that churn: a model that
#: knows the ceiling and the per-turn cap does not keep proposing changes the engine throws
#: away, losing the rest of its batch with them.
RELATION_WRITE_RULES: tuple[str, ...] = (
    "Relation scores run from -100 (open enemies) to +100 (closest allies). A value outside "
    "that range is rejected -- if you mean the extreme, write -100 or +100, not beyond.",
    "A single relation may move at most 25 points in one turn on its own. A larger swing is "
    "rejected unless an engine-authored dramatic event -- a declaration of war, a betrayal, a "
    "signed alliance -- justifies it, and your diff cites that event. Absent one, move it 25 "
    "or less and let it build over several turns.",
    "A relation change may not be more widely seen than its cause: a change caused by a secret "
    "or bilateral event must carry the same visibility scope, never `public`. A covert cause "
    "cannot produce openly observable state.",
    "A treaty_status is one of exactly: "
    + ", ".join(f"`{s.value}`" for s in TreatyStatus)
    + ". Any other value is rejected and takes the whole batch with it -- there is no "
    "`peace_treaty`, `mutual_defence` or the like; pick the closest of these.",
)

#: The same discoverability pattern, for the economic aggregates. PT20's very first turn
#: lost an entire world-sim batch to this: the simulator moved `military_strength` from 64
#: to 127 for Russia and 50 to 99 for Ukraine in a single month -- roughly doubling both
#: armies -- and rule ECON-001 refused the batch and everything else in it.
#:
#: The guardrail was right. The model was never told it existed. Read from the rule's own
#: `SOFT_CAPS` rather than restated here, so the briefing cannot drift away from what the
#: engine actually enforces -- the failure mode a hand-copied list of numbers invites.
#:
#: **A function of the turn's length, because the cap is.** Quoting a monthly figure to a
#: model deciding a year would be the same bug on the disclosure side: it would be told a
#: bound twelve times stricter than the one about to be applied to it, and would either
#: waste the year or trip a cap it had been promised it would not.
def economy_write_rules(days: int = 30) -> tuple[str, ...]:
    """The economic write bounds, stated for a turn of ``days``."""
    span = span_of(days)
    scaled = {field: over(cap, span) for field, cap in SOFT_CAPS.items()}
    horizon = (
        "this turn"
        if 0.9 <= span <= 1.1
        else f"this turn ({days} days, so {span:.2g} months' worth)"
    )
    return (
        f"Economic aggregates move slowly. Across {horizon}, without a ratified reform, "
        "each may change by at most: "
        + ", ".join(f"`{field}` {cap:.0%}" for field, cap in sorted(scaled.items()))
        + " of its current value. A larger move is refused and takes the whole batch "
        "with it.",
        "The cap is measured across *everything* in your batch that touches a field, not "
        "per diff -- ten small changes to one nation's economy add up and are checked "
        "together.",
        "If a change genuinely should be larger, write an `economic_reform` event and have "
        "the diff cite it: a ratified reform multiplies the cap by "
        f"{REFORM_MULTIPLIER:.0f} for that nation. Absent one, move the number a little "
        "and let it compound over several turns. Growth is meant to be something a country "
        "builds, not something a turn announces.",
    )


def _standing_line(conn: sqlite3.Connection, world_id: str, nation_id: str) -> str:
    """Where this nation stands among all of them, and what it is strongest at.

    **A rank is a much better prompt than a hidden number.** A staff told it is fourth of
    two hundred and strongest in arms has been given something to reason from -- whether to
    press the advantage, or that its army is the only thing keeping it in the conversation.
    Told nothing, it reasons from its own figures in a vacuum and every nation sounds the
    same. See :mod:`atlas.engine.scoring`; nothing here is stored, so this cannot be stale.
    """
    from .scoring import standings

    table = standings(conn, world_id)
    rank = table.rank_of(nation_id)
    if not rank:
        return "- your standing among the powers is not measurable"
    return (
        f"- among the powers you stand **{rank} of {len(table.scores)}**, "
        f"and what you are strongest at is {table.leading_on(nation_id)}"
    )


def _and_list(items: tuple[str, ...] | list[str]) -> str:
    """``a``, ``a and b``, ``a, b and c``. Prose, because the briefing is prose."""
    values = [str(item) for item in items if item]
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    return ", ".join(values[:-1]) + " and " + values[-1]


def _bullet(lines: list[str], items: list[str]) -> None:
    lines.extend(f"- {item}" for item in items)


def _founding_event_id(conn: sqlite3.Connection, world_id: str) -> int | None:
    """The world's genesis event -- an id every nation may always legally cite.

    Every diff must cite ``reason_event_id`` (rule CORE-001), and the real-model runs showed
    models routinely *inventing* one for a change with no obvious recent cause -- growing
    their own economy, a routine adjustment -- which the engine rejects and which takes the
    whole diff with it. The genesis event is engine-authored, public and dated turn 0, so it
    passes CORE-001 and INFO-001/002 for any proposer and any change: a true, general cause a
    model can fall back to instead of a fabricated specific one. Surfaced in the briefings as
    the explicit citable-of-last-resort.
    """
    row = conn.execute(
        "SELECT id FROM event WHERE world_id = ? AND kind = 'genesis' ORDER BY id LIMIT 1",
        (world_id,),
    ).fetchone()
    return int(row["id"]) if row else None


def _occupation_lines(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> list[str]:
    """A briefing block on the foreign ground this nation holds and what it costs to keep it.

    Empty for a nation on nothing but its own soil -- most nations, most of the time. When it
    fires, it makes the unrest layer legible to whoever is deciding: which occupied provinces
    are bleeding stability, which are one bad season from revolt, and that a garrison or a
    steadier hand at home answers both.
    """
    held = held_foreign_provinces(conn, world_id, nation_id)
    if not held:
        return []
    names = {
        r["id"]: r["name"]
        for r in conn.execute(
            "SELECT id, name FROM nation WHERE world_id = ?", (world_id,)
        )
    }
    lines = [
        "## Occupied ground you are holding down",
        "Foreign provinces you hold drag your stability every turn, and a province held too "
        "thin can rise in revolt and revert to its old rulers. Stationing troops on one, or a "
        "steadier hand at home, eases both.",
    ]
    entries = []
    for p in held:
        whose = names.get(p["home_nation_id"], p["home_nation_id"])
        if p["garrisoned"]:
            state = "garrisoned, held down"
        elif p["garrison"] > 0:
            state = f"lightly garrisoned, unrest {p['residual']:.1f} -- still restive"
        else:
            state = f"ungarrisoned, unrest {p['residual']:.1f} -- at risk of revolt"
        entries.append(f"{label(p['name'], p['region_id'])} ({whose}'s core): {state}")
    _bullet(lines, entries)
    lines.append("")
    return lines


def _at_home_lines(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> list[str]:
    """Who this government has to keep, and how they feel about it.

    **The point of showing it is that the decisions are different.** A staff that can see
    its officer corps at *content* and its people at *restive* is choosing between a
    battleship and a housing programme with the politics in front of it, which is the whole
    reason the layer exists. What it must not read as is a set of dials to optimise: the
    line at the end says plainly that these follow from what the government does.
    """
    from .constituencies import standing_of

    standing = standing_of(conn, world_id, nation_id)
    if not standing:
        return []
    lines = ["## Who you have to govern"]
    _bullet(lines, [entry.describe() for entry in standing])
    lines.append(
        "These five are not dials you can set. They move with what you actually do -- "
        "raise armies, build works, sign treaties, give ground away -- and they are what "
        "a government eventually answers to.")
    lines.append("")
    return lines


def _war_weariness_lines(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> list[str]:
    """What each of this power's wars has cost it, and where it stands in them.

    **Both sides of the ledger, at different resolutions.** Your own exhaustion is a number
    you know exactly; the enemy's is a band, because how tired a rival's population is of a
    war is something you read from the outside -- observable, not audited. The leverage line
    is the engine's own read of the bargaining position, and it is shown to both sides,
    because a war in which only the winner knows who is winning produces no negotiations.

    Silent for a power that has never been at war.
    """
    from .leverage import leverage_between
    from .wareffort import efforts_of

    ledgers = efforts_of(conn, world_id, nation_id)
    fighting = {
        str(row["nation_b"])
        for row in conn.execute(
            "SELECT nation_b FROM relation WHERE world_id = ? AND nation_a = ?"
            "   AND at_war = 1", (world_id, nation_id))
    }
    # **At war counts, ledger or not.** The ledger opens when the turn's war-effort pass
    # runs, so a power that entered a war this turn had no section at all -- and so was
    # never told the one verb that could end it. A war with no ledger yet still has a way
    # out, and the sentence naming it is the point.
    if not ledgers and not fighting:
        return []
    names = {
        str(row["id"]): str(row["name"])
        for row in conn.execute(
            "SELECT id, name FROM nation WHERE world_id = ?", (world_id,))
    }
    at_war = fighting

    lines = ["## What the fighting has cost you"]
    for enemy_id, mine in ledgers.items():
        enemy_name = names.get(enemy_id, enemy_id)
        theirs = efforts_of(conn, world_id, enemy_id).get(nation_id)
        state = "at war" if enemy_id in at_war else "no longer at war"
        lines.append(
            f"Against {label(enemy_name, enemy_id)} ({state}, since turn "
            f"{mine.opened_turn}):")
        _bullet(lines, [
            f"your war-weariness: **{mine.band}** ({mine.exhaustion:.0f}/100), "
            f"{mine.casualties:.1f} strength lost",
            (f"theirs, as far as you can tell: {theirs.band}" if theirs
             else "theirs: they have taken nothing from this war yet"),
            leverage_between(conn, world_id, nation_id, enemy_id).sentence(names),
        ])
    lines.append(
        "War-weariness is what your country feels about the war, not a rule that ends it. "
        "It rises with the dying, with your own ground under occupation, and simply with "
        "time; peace mends it slowly.")
    lines.append(
        "**You can end any of these wars yourself** with `peace_offer`: name the enemy and "
        "the terms you will stop for. The other side accepts or refuses according to how "
        "the war has actually gone, so a power that is winning will not stop for nothing -- "
        "and a power that is losing will sign away a great deal.")
    lines += _ground_you_could_keep(conn, world_id, nation_id, ledgers, at_war, names)
    lines += _terms_on_offer(conn, world_id, nation_id, ledgers, at_war, names)
    lines.append("")
    return lines


def _region_name(conn, region_id: str) -> str:
    row = conn.execute("SELECT name FROM region WHERE id = ?", (region_id,)).fetchone()
    return str(row["name"]) if row else region_id


def _ground_you_could_keep(  # noqa: PLR0913 - the caller has already read all of this
    conn, world_id: str, nation_id: str, ledgers, at_war, names,
) -> list[str]:
    """What an occupier could turn into its own, and what is stopping it.

    **The conversion has always existed and nothing ever pointed a winner at it.** A peace
    term of `cede_region` moves `owner_nation_id` for good, and `peace.suggested_terms`
    already builds the cession set a beaten power would sign -- greedily, cheapest first,
    stopping at what its position will bear. But the only briefing that rendered any of it
    was `_terms_on_offer`, which speaks **to the loser**, and only once its exhaustion
    passes `OFFER_AT_EXHAUSTION` -- ninety out of a hundred. So a power sitting on five
    enemy provinces was never told that a peace would make them its own, and measured over
    forty turns a committed conqueror ended with exactly the fourteen provinces it started
    with and five more it merely occupied.

    This is the mirror, and it is discoverability rather than mechanism: **nothing about how
    a cession is judged changes here.** The enemy still has to accept it, the ownership
    floor still refuses terms that would leave a state owning nothing, occupied ground still
    yields forty per cent and still stokes unrest, and the war limits and cooldowns are
    untouched.

    Rendered for any occupier, not only a winning one, and it says plainly when the answer
    is *not yet* -- a power told only when it is already certain to succeed learns nothing
    about what to aim at.
    """
    from .peace import ProposedTerm, acceptance, review_terms, suggested_terms
    from .strategy import plan_of
    from .waraims import aims_of, implied_regions

    lines: list[str] = []
    plan = plan_of(conn, world_id, nation_id)
    aims = aims_of(conn, world_id, nation_id)

    for enemy_id in sorted(at_war):
        held = conn.execute(
            "SELECT id, name FROM region WHERE world_id = ? AND kind <> 'ocean'"
            "   AND owner_nation_id = ? AND controller_nation_id = ? ORDER BY id",
            (world_id, enemy_id, nation_id)).fetchall()
        if not held:
            continue
        # **Region names, which `names` does not carry.** It maps nations, so every
        # `names.get(region_id, region_id)` below fell through to the raw id and the block
        # read as a list of database keys.
        where = {str(row["id"]): str(row["name"]) for row in held}
        enemy = names.get(enemy_id, enemy_id)
        lines.append("")
        lines.append(
            f"**Ground of {enemy}'s you are holding, and could keep.** Occupation is not "
            "ownership: it is worth two fifths of what the same ground is worth to its "
            "owner, it stokes unrest at home for as long as it lasts, and it goes back the "
            "moment the fighting does. A peace that includes `cede_region` is what makes it "
            "yours for good, and the engine will only ratify ground you already hold.")
        _bullet(lines, [
            f"{label(str(row['name']), str(row['id']))}" for row in held[:8]])

        # What the engine judges this enemy would actually sign today.
        offer = suggested_terms(conn, world_id, loser=enemy_id, winner=nation_id)
        if offer:
            verdict = acceptance(conn, world_id, offered_to=enemy_id,
                                 offered_by=nation_id, terms=offer)
            lines.append(
                f"On today's position {enemy} would sign for "
                + ", ".join(
                    label(where.get(term.subject, term.subject), term.subject)
                    for term in offer[:6])
                + f" -- {verdict.because}")
        else:
            # **Why not, named.** A block that goes quiet when the answer is no teaches a
            # staff that the channel does not work rather than that the moment is wrong.
            blocked = []
            for row in held:
                term = ProposedTerm("cede_region", obliged=enemy_id,
                                    beneficiary=nation_id, subject=str(row["id"]))
                card = review_terms(conn, world_id,
                                    belligerents=(enemy_id, nation_id), terms=[term])[0]
                if not card.ok and card.because:
                    blocked.append(f"{row['name']}: {card.because}")
            owned = conn.execute(
                "SELECT COUNT(*) AS n FROM region WHERE world_id = ? AND kind <> 'ocean'"
                "   AND owner_nation_id = ?", (world_id, enemy_id)).fetchone()
            if blocked:
                _bullet(lines, blocked[:3])
            elif int(owned["n"] if owned else 0) <= len(held):
                lines.append(
                    f"{enemy} owns {int(owned['n']) if owned else 0} province(s) and you "
                    "hold all of them. **A state is not dissolved at the table**, however "
                    "many provinces it is asked for one at a time, so a cession of the last "
                    "of them will be refused; this one has to be settled some other way.")
            else:
                lines.append(
                    f"{enemy} will not cede any of it yet. A cession is priced against how "
                    "the war has actually gone: press the advantage, or wait for its "
                    "patience to run down, and ask again.")

        # And whether this is what the government said it was fighting for. Read-only: the
        # aims and the plan are stated elsewhere and are not edited from here.
        wanted = {
            region
            for aim in aims.get(enemy_id, ())
            for region in implied_regions(conn, world_id, aim)
        }
        holding = {str(row["id"]) for row in held}
        if wanted:
            matched = sorted(wanted & holding)
            if matched:
                lines.append(
                    "This is ground you declared you were fighting for: "
                    + ", ".join(label(where.get(r, r), r) for r in matched[:6]) + ".")
            missing = sorted(wanted - holding)
            if missing:
                lines.append(
                    "Still not in your hands, of what you said you wanted: "
                    + ", ".join(label(_region_name(conn, r), r) for r in missing[:6])
                    + ".")
        elif plan.objective == "conquest":
            lines.append(
                "You are playing for ground and have declared no aim against "
                f"{enemy}. `war_aims` is what turns a war into one with a point, and a "
                "settlement is measured against it.")
    return lines


def _terms_on_offer(  # noqa: PLR0913 - the caller has already read all of this
    conn: sqlite3.Connection, world_id: str, nation_id: str, ledgers, at_war, names,
) -> list[str]:
    """What the engine would put on the table for a power at the end of its rope.

    **Offered, never imposed** -- see `atlas.engine.peace.suggested_terms`. A country this
    tired is shown a settlement it would actually be able to sign, and may refuse it and
    fight on; nothing here ends a war, and the sentence says so. Silent below the
    threshold, because a power that is still holding is not owed advice about surrender.
    """
    from .peace import OFFER_AT_EXHAUSTION, suggested_terms

    lines: list[str] = []
    for enemy_id, mine in ledgers.items():
        if enemy_id not in at_war or mine.exhaustion < OFFER_AT_EXHAUSTION:
            continue
        offered = suggested_terms(conn, world_id, loser=nation_id, winner=enemy_id)
        if not offered:
            continue
        enemy_name = names.get(enemy_id, enemy_id)
        lines.append("")
        lines.append(
            f"**Your country cannot go on much longer against {enemy_name}.** A settlement "
            "on about these terms is what your position would bear, if you offered it:")
        _bullet(lines, [
            f"`{term.kind}` {label(names.get(term.subject, term.subject), term.subject)}"
            for term in offered[:6]])
        lines.append(
            "You are not obliged to offer any of it. It is what the engine judges "
            f"{enemy_name} would accept from you today; fighting on is a decision you may "
            "still take, and terms get worse the longer a losing war runs.")
    return lines


def _facts_for_negotiation(conn: sqlite3.Connection, world_id: str, nation_id: str):
    """The negotiator's slice of the world's memory, scoped to what this power knows."""
    from .facts import visible_facts

    return visible_facts(conn, world_id, nation_id, limit=40)


def _remembered_lines(
    conn: sqlite3.Connection, world_id: str, nation_id: str, *, limit: int = 12
) -> list[str]:
    """What the world remembers, scoped to what this power has learned.

    **Said in words as well as by structure**: the section tells the reader that none of
    these move a number, because a model that mistook a remembered pledge for a mechanism
    would reason about a world that does not exist. The engine's numbers are everywhere
    else in the briefing; this is memory.

    Silent when the world has nothing to remember, which is every world at turn 1.
    """
    from .facts import names_for, visible_facts

    known = visible_facts(conn, world_id, nation_id, limit=limit)
    if not known:
        return []
    names = names_for(conn, world_id)
    lines = [
        "## What the world remembers",
        "Standing facts about the world, and about you. They are what everyone who knows "
        "them will reason from. **None of them changes a number by itself** -- what they "
        "change is what the world takes for granted.",
    ]
    _bullet(lines, [
        fact.describe(names)
        + (f"  (since turn {fact.start_turn}"
           + (f", {fact.visibility}" if fact.visibility != "public" else "") + ")")
        for fact in known
    ])
    lines.append("")
    return lines


def _war_aim_lines(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> list[str]:
    """What this power is fighting for, and what is being demanded of it.

    **Both halves, because a war aim is a public demand.** Told only its own aims a power
    is negotiating with itself; told what the other side has stated, it can concede, refuse
    or trade -- which is the whole reason to write aims down rather than infer them from the
    front line. What a power would *settle* for stays private and is not here.

    Silent for a nation at peace. **Not silent any more for a war nobody has stated aims
    in**, which is what it used to be and is how the channel stayed dormant: a block that
    appears only once aims exist cannot tell anybody how to state the first one, and across
    a hundred and fifty turns of play-test no power ever named a war aim. A nation at war
    with nothing declared is now told the field and what it is for, which is the one case
    the old wording deliberately skipped.
    """
    from .waraims import AIM_KINDS, aims_against, aims_of

    mine = aims_of(conn, world_id, nation_id)
    theirs = aims_against(conn, world_id, nation_id)
    if not mine and not theirs:
        at_war = conn.execute(
            "SELECT COUNT(*) AS wars FROM relation"
            " WHERE world_id = ? AND at_war = 1 AND (nation_a = ? OR nation_b = ?)",
            (world_id, nation_id, nation_id)).fetchone()
        if not int(at_war["wars"] or 0):
            return []
        return [
            "## What you are fighting for",
            "You have not said. Set `war_aims` to state it -- what would have to be true "
            "for you to stop -- and the other side is told. A war nobody has named terms "
            "for is one that can only end when somebody has nothing left, because a peace "
            "offer is judged against the aims on the table.",
            "",
        ]

    names = {
        str(row["id"]): str(row["name"])
        for row in conn.execute(
            "SELECT id, name FROM nation WHERE world_id = ?", (world_id,))
    }
    names.update({
        str(row["id"]): str(row["name"])
        for row in conn.execute(
            "SELECT id, name FROM region WHERE world_id = ?", (world_id,))
    })

    def render(aims) -> list[str]:
        return [
            f"{aim.kind.replace('_', ' ')}"
            + (f" — {label(names.get(aim.subject_id, aim.subject_id), aim.subject_id)}"
               if aim.subject_id else "")
            + f" ({AIM_KINDS[aim.kind]})"
            for aim in aims
        ]

    lines: list[str] = []
    if mine:
        lines.append("## What you are fighting for")
        for enemy_id, aims in sorted(mine.items()):
            lines.append(f"Against {label(names.get(enemy_id, enemy_id), enemy_id)}:")
            _bullet(lines, render(aims))
        lines.append(
            "These stand until you restate them for that war. Naming aims against one "
            "enemy leaves your aims in your other wars alone.")
        lines.append("")
    if theirs:
        lines.append("## What is demanded of you")
        for enemy_id, aims in sorted(theirs.items()):
            lines.append(
                f"{label(names.get(enemy_id, enemy_id), enemy_id)} says it is fighting "
                "you for:")
            _bullet(lines, render(aims))
        lines.append("")
    return lines


def _war_progress_lines(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> list[str]:
    """How this nation's wars are actually going: fronts, pressure, and stalled attacks.

    The player's council never showed the front. A tracked agent is briefed on where its line
    is moving; the human, advised through this view, was told who it was at war with but never
    the pressure on any contested province -- so the recurring finding "no feedback on why an
    offensive is stalling" (Playthrough 1) had no answer here. This is the answer: each war's
    contested ground with how far each siege has come, and an attack across water called out as
    the landing it is. Empty for a nation not at war, or one at war with no active front (which
    is itself the feedback -- a war nobody is fighting on the ground is one your army is sitting
    out).
    """
    fronts = visibility.fronts_of(conn, nation_id)
    front_regions = {r.region_id for f in fronts for r in f["regions"]}
    offensives = [
        row for row in conn.execute(
            "SELECT r.name AS name, c.region_id AS region_id, c.pressure AS pressure, "
            "       n.name AS defender_name "
            "  FROM contest c JOIN region r ON r.id = c.region_id "
            "  JOIN nation n ON n.id = c.defender_id "
            " WHERE c.world_id = ? AND c.attacker_id = ? ORDER BY c.pressure DESC, c.region_id",
            (world_id, nation_id),
        ) if row["region_id"] not in front_regions
    ]
    if not fronts and not offensives:
        return []

    lines = ["## How your wars are going"]
    for front in fronts:
        lines.append(
            f"Against {front['name']}: {front['contested']} contested province(s)."
        )
        _bullet(lines, [region.describe() for region in front["regions"]])
        if front["quiet_omitted"]:
            lines.append(f"  ({front['quiet_omitted']} further quiet province(s) on this front.)")
    if offensives:
        lines.append(
            "Attacks across water (a landing, not a land front) -- these fade if you stop pressing them:"
        )
        _bullet(lines, [
            f"{label(o['name'], o['region_id'])}, held by {o['defender_name']}: "
            f"{float(o['pressure']):.0%} of the way to taking it"
            for o in offensives
        ])
    lines.append("")
    return lines


def _difficulty_clause(difficulty: str) -> str:
    profile = DIFFICULTY_PROFILES[Difficulty(difficulty)]
    return profile.prompt_clause


def _effect_summary(effects) -> str:
    """The numbers a builder actually weighs, e.g. ``industry +12, stability -2``."""
    parts = [f"{e.field_name} {e.amount:+g}" for e in effects]
    return ", ".join(parts)


def _programme_summary(tech) -> str:
    """What a programme buys, in the shape a public work already states it.

    **Public works printed their effects and programmes did not.** The discoverability sweep
    rendered 96 briefings across three eras and four states and found the research menu
    naming this era's programmes with an id, a price and a duration in 24 of 24 -- and
    saying what one *does* in 0 of 24. A reader was handed a name, a price and no reason,
    directly under a list of works each of which ends in ``-> industry +8, stability -1``.

    `research.py` opens by insisting that every effect in that catalogue is something
    shipped code already reads -- that is the test a technology has to pass to be in it at
    all. This is the briefing finally saying what they are.

    A unit effect names its subject, because ``build_days -60`` means nothing without the
    hull it is about.
    """
    parts = []
    for effect in tech.effects:
        subject = f"{effect.subject} " if getattr(effect, "subject", "") else ""
        parts.append(f"{subject}{effect.field_name} {effect.amount:+g}")
    return ", ".join(parts)


#: How warmly a power must regard somebody before it is offered as a client. Warm enough
#: that backing them is plausible, low enough that it is not only formal allies -- the
#: relationships that actually get armed are rarely written down.
_PATRONAGE_WARMTH = 45.0


def _raisable_lines(
    conn: sqlite3.Connection, world_id: str, nation_id: str, *, days: int = 30
) -> list[str]:
    """The army this power has, what it costs to add to it, and whether it is wearing out.

    Two things had to be true before a tracked power would ever reinforce, and neither was.
    It had to be *able* to -- ``raise_formations`` is that half -- and it had to be able to
    *tell that it should*, which is this half. A briefing that reports "military 72" and
    nothing else gives a staff no way to distinguish an army at full establishment from the
    same army worn to a third of it, so every turn reads like a turn for building factories.

    The condition figure is measured against the establishment the formations were raised
    at rather than against a remembered peak: it answers "how much fight is left in what I
    have" without needing history, and a power that has lost whole divisions sees it in the
    count beside it.

    **The affordable count is quoted for the reason decision 50 records** -- an undisclosed
    bound costs a whole proposal. A staff told only that formations cost money proposes
    five and has four refused; told it can pay for one, it proposes one and spends the rest
    of its turn on something that will land.
    """
    from .orders import REINFORCE_TREASURY_SHARE, replaceable_now

    templates = conn.execute(
        "SELECT kind, cost_treasury, cost_manpower, base_strength, build_days,"
        "       COALESCE(domain, 'land') AS domain"
        "  FROM unit_template WHERE world_id = ? ORDER BY kind", (world_id,)
    ).fetchall()
    if not templates:
        return []

    row = conn.execute(
        "SELECT treasury, manpower FROM nation WHERE id = ?", (nation_id,)).fetchone()
    treasury = float(row["treasury"]) if row else 0.0
    manpower = float(row["manpower"]) if row else 0.0

    held = conn.execute(
        "SELECT kind, COUNT(*) AS n, COALESCE(SUM(strength), 0) AS total"
        "  FROM unit WHERE world_id = ? AND nation_id = ? GROUP BY kind ORDER BY kind",
        (world_id, nation_id)).fetchall()
    establishment_of = {
        str(t["kind"]): float(t["base_strength"] or 0.0) for t in templates}

    lines = ["## The forces you have, and what it costs to replace them"]
    if held:
        full = sum(
            float(h["n"]) * establishment_of.get(str(h["kind"]), 0.0) for h in held)
        have = sum(float(h["total"]) for h in held)
        _bullet(lines, [
            f"{h['n']} {h['kind']} at {float(h['total']):.1f} strength"
            + (f" (establishment {float(h['n']) * establishment_of[str(h['kind'])]:.1f})"
               if establishment_of.get(str(h["kind"])) else "")
            for h in held
        ])
        if full > 0:
            share = have / full
            lines.append(
                f"Your formations are at **{share:.0%} of establishment**"
                + (" -- they are being worn down faster than they are being replaced."
                   if share < 0.75 else ".")
            )
    else:
        lines.append("**You have no formations in the field at all.**")

    menu = []
    for t in templates:
        kind, cost, men = str(t["kind"]), float(t["cost_treasury"] or 0.0),             float(t["cost_manpower"] or 0.0)
        affordable = min(int(treasury // max(0.01, cost)), int(manpower // max(0.01, men)))
        # A kind with a build time is laid down in the yards, not raised this month. Said
        # here rather than discovered by ordering one and getting a hull in two years.
        if float(t["build_days"] or 0.0) > 0:
            menu.append(
                f"{kind} [`{kind}`] -- {cost:.1f} treasury, {men:.1f} manpower, "
                f"{float(t['build_days']):.0f} days in the yards (not available this turn)")
        else:
            menu.append(
                f"{kind} [`{kind}`] -- {cost:.1f} treasury, {men:.1f} manpower, "
                f"strength {float(t['base_strength'] or 0.0):.1f}; "
                f"you could pay for {max(0, affordable)} right now")
    _bullet(lines, menu)
    # **The cheap door, quoted next to the expensive one.** A staff that can see only the
    # price of a new division concludes it cannot afford an army and stops trying; the
    # useful fact is that holding the one it has costs a quarter as much. Priced here in
    # the same breath as raising, because the comparison is the decision.
    # **What the order will actually do, read from the handler rather than guessed at.**
    # This block used to invite `reinforce` off an army-wide sum over unit *kinds* --
    # `n * establishment - Σstrength`, floored at zero per kind -- while `orders._reinforce`
    # decides per formation and skips anything at sea, out of supply, or whose own allowance
    # floors to a hundredth. The two disagree on a very ordinary state: six air wings 0.1
    # below establishment between them are 0.017 short each, which floors to 0.01 and is
    # skipped six times over. On `pt77-cold-war` that cost 13 of 47 reinforcement orders --
    # the United States nine of its twelve turns, in the same words each time.
    #
    # The per-kind net is gone from the headline as well as from the gate: it let a kind's
    # over-strength formations cancel its under-strength ones, so the two numbers on this
    # line could cross ("2.1 below establishment, and 2 formations could take 2.9 back").
    # Counted per formation they cannot.
    replaceable = replaceable_now(conn, world_id, nation_id, span=span_of(days))
    worn_points = replaceable.short_points
    if worn_points > 0.01:
        cost = conn.execute(
            "SELECT kind, cost_treasury, cost_manpower, base_strength FROM unit_template"
            " WHERE world_id = ? AND kind = 'infantry'", (world_id,)).fetchone()
        if cost is not None and float(cost["base_strength"] or 0.0) > 0:
            per_point = (float(cost["cost_treasury"])
                         / float(cost["base_strength"]) * REINFORCE_TREASURY_SHARE)
            if replaceable.formations:
                lines.append(
                    f"You are **{worn_points:.1f} strength below establishment** across the "
                    f"army, and **{replaceable.formations} formation(s) could take "
                    f"{replaceable.points:.1f} of it back this turn**. Set `reinforce` to "
                    f"true: replacements cost about {per_point:.2f} treasury a point -- a "
                    "quarter of what the same strength costs in a new formation -- but full "
                    "manpower, and it will not spend more than half your treasury, so you "
                    "can reinforce and build in the same turn."
                )
            else:
                # **The other half, and the half that was missing.** A staff shown a
                # shortfall and no reason it cannot be made good spends the order every
                # turn. Naming which gate is holding is what turns a wasted slot into a
                # decision -- move the formation into supply, or leave it and build.
                lines.append(
                    f"You are {worn_points:.1f} strength below establishment across the "
                    "army and **`reinforce` would do nothing this turn**: no formation of "
                    "yours is eligible."
                )
            if replaceable.blocked:
                because = []
                if replaceable.blocked_out_of_supply:
                    because.append(
                        f"{replaceable.blocked_out_of_supply} out of supply (replacements "
                        "do not reach a formation the depots cannot)")
                if replaceable.blocked_at_sea:
                    because.append(
                        f"{replaceable.blocked_at_sea} at sea (a ship is made good in port, "
                        "not on station)")
                if replaceable.blocked_too_thin:
                    because.append(
                        f"{replaceable.blocked_too_thin} short by too little to be worth a "
                        "draft -- a formation takes replacements only once its own "
                        "shortfall is at least 0.01, so an army a fraction under "
                        "establishment across many formations has nothing to replace")
                lines.append(
                    "Under strength and not eligible: " + _and_list(because) + ".")
    # **And that raising competes with building for the same slots**, which the forces menu
    # has never said. `industrial_capacity` is one pool: public works, research programmes
    # and every unit with a build time draw on it, and `order_construction` refuses with
    # "every yard is already occupied". The works menu discloses the count in its header and
    # this menu says only "in the yards", so a staff can read the briefing correctly and
    # still lose the order -- which is exactly what the Soviet Union did on turn 8 of
    # `pt77-cold-war`: shown "4 of 4 slots working, 0 free" it rightly ordered nothing to
    # build, then ordered three infantry against the same counter. The arsenal block has
    # made this connection in one clause for months ("in the same queue as everything else
    # -- so a warhead is a steelworks you did not build"); this is that sentence, here.
    from .production import in_progress, industrial_capacity

    capacity = industrial_capacity(conn, nation_id)
    free = capacity - in_progress(conn, world_id, nation_id)
    lines.append(
        "Set `raise_formations` to put *new* formations in the field, naming a kind by its "
        "id above. Raising is what grows an army; reinforcing is what keeps one. A war "
        "spent building factories while your divisions are ground down is a war you lose "
        "with a fine economy. **Anything with a build time takes a slot in the same queue "
        f"as your public works and research -- you have {max(0, free)} of {capacity} free "
        "-- so a division laid down is a steelworks you did not build, and an order with no "
        "slot to go in is refused rather than queued.**"
    )
    lines.append("")
    return lines


def _patronage_lines(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> list[str]:
    """Friends of this power who are in a war, and what could be sent to them.

    **The channel existed and nobody was ever told it did.** `support_lines` renders a
    "Proxies" block only for a power that is *already* backing somebody, so a government
    that has never opened a shipment reads a briefing in which the whole mechanism is
    invisible -- and every power in a twenty-four power play-test duly backed nobody, for
    thirty turns, while the country it had the warmest relations with was overrun. This is
    the discoverability rule decision 50 records, applied to the one lever a great power has
    that is neither neutrality nor war.

    Candidates are named with the two facts that decide the question: how much of their own
    country they have lost, and whether they are being beaten. A power told only that a
    friend is "at war" has no way to tell a border skirmish from a partition.
    """
    from .support import (
        DENIABLE_KINDS,
        FUNDING_PER_LEVEL,
        MATERIEL_TREASURY_PER_LEVEL,
        MAX_LEVEL,
        patrons_of,
    )

    mine = {
        str(row["nation_b"]): row for row in conn.execute(
            "SELECT nation_b, score, at_war FROM relation"
            " WHERE world_id = ? AND nation_a = ?", (world_id, nation_id))
    }
    candidates = []
    for other, row in sorted(mine.items()):
        if int(row["at_war"] or 0) or float(row["score"] or 0.0) < _PATRONAGE_WARMTH:
            continue
        fighting = conn.execute(
            "SELECT COUNT(*) AS wars FROM relation"
            " WHERE world_id = ? AND at_war = 1 AND (nation_a = ? OR nation_b = ?)",
            (world_id, other, other)).fetchone()
        if not int(fighting["wars"] or 0):
            continue
        ground = conn.execute(
            "SELECT COUNT(*) AS owned,"
            "       SUM(CASE WHEN controller_nation_id IS NOT NULL"
            "                 AND controller_nation_id <> owner_nation_id THEN 1 ELSE 0 END)"
            "         AS lost"
            "  FROM region WHERE world_id = ? AND owner_nation_id = ? AND kind <> 'ocean'",
            (world_id, other)).fetchone()
        owned = int(ground["owned"] or 0)
        lost = int(ground["lost"] or 0)
        named = conn.execute(
            "SELECT name FROM nation WHERE id = ?", (other,)).fetchone()
        candidates.append((
            other, str(named["name"]) if named else other,
            float(row["score"]), owned, lost,
            bool(patrons_of(conn, world_id, other).get(nation_id)),
        ))
    if not candidates:
        return []

    purse = conn.execute(
        "SELECT treasury, industry FROM nation WHERE id = ?", (nation_id,)).fetchone()
    treasury = float(purse["treasury"]) if purse else 0.0

    lines = ["## Friends of yours who are in a war"]
    _bullet(lines, [
        f"{label(name, other)} -- you stand at {score:.0f} with them; they have lost "
        + (f"**{lost} of their {owned} provinces**" if lost else
           f"none of their {owned} provinces so far")
        + (" (you are already backing them)" if backed else "")
        for other, name, score, owned, lost, backed in candidates
    ])
    lines.append(
        "Set `support` to back one of them without joining the war: "
        "`{\"<their id>\": {\"equipment\": 2, \"advisors\": 1}}`, each kind from 1 to "
        f"{MAX_LEVEL:.0f}. **This does not make you a belligerent** and triggers nobody's "
        "guarantee -- what it costs you is standing with their enemy. Equipment and air "
        f"defence draw about {MATERIEL_TREASURY_PER_LEVEL} treasury and some industry per "
        f"level per turn; funding moves {FUNDING_PER_LEVEL} a level straight to them; "
        "advisors and intelligence cost no money at all, only being seen. What you cannot "
        f"pay for lapses and you are told it lapsed. You have {treasury:.0f} in hand."
    )
    # **The deniable half was invisible in every briefing in the world.** The
    # discoverability sweep rendered 96 of these and found `covert_support` named in none
    # of them -- and the sweep's own waiver list had it down as "named beside `support`",
    # which is what this block ought to have done and did not. The channel is in the
    # schema, has an engine and has tests; no government had ever been told it was there.
    lines.append(
        "Or set `covert_support` in the same shape to send it **without saying so** -- "
        + _and_list([f"`{kind}`" for kind in sorted(DENIABLE_KINDS)])
        + " only, because a tank is a tank the other side can photograph. Their enemy "
        "does not read about a quiet shipment, but it works at it, and what it can prove "
        "grows the longer you keep it up."
    )
    lines.append(
        "A friend losing ground is the case this exists for. A country fighting alone "
        "against a larger one runs out of people and loses on arithmetic however well it "
        "fights, and backing it is the difference between a war it can hold and a war it "
        "can only lose slowly."
    )
    lines.append("")
    return lines


#: What actually moves each component of a nation's score, named in the vocabulary of the
#: channels a briefing has already offered it.
#:
#: **A target without a lever is another kind of invisible.** Making the victory conditions
#: visible got them read and did not get them pursued: measured over forty turns the leader's
#: score sat flat at 0.5 and never opened more than a 1.11x lead on the runner-up, against
#: the 2.00x hegemony asks for. A government told the threshold and not the path is in the
#: same position as one told nothing, one step further along.
_LEVERS_FOR: dict[str, str] = {
    # **Two wordings, because naming `military_objectives` to a country at peace is a
    # defect with two tests already guarding it.** A briefing that offers the war channel to
    # a power with no war spends that power's turn on an order the engine will refuse, which
    # is the whole reason those guards exist -- and this lever walked straight into them.
    "ground": "hold more, and hold it developed. Ground is the largest single component, "
              "and occupation is discounted -- ground you own outright is worth more than "
              "ground you sit on, so developing what is yours counts as well as taking more",
    "ground@war": "hold more, and hold it developed -- ground is the largest single "
                  "component and the one conquest moves. `military_objectives` takes it; "
                  "occupation is discounted, so ground you own outright is worth more than "
                  "ground you sit on",
    "wealth": "`build_orders`: workshops and steelworks raise the industry that feeds the "
              "economy, rail and schools raise the economy directly",
    "arms": "`raise_formations` and `reinforce` for an army, and warheads if you have a "
            "programme -- this is the only component an arsenal counts toward at all",
    "standing": "the component a country at peace can lead on: legitimacy, the regard of "
                "other powers, pacts kept, and a seat at whatever bodies exist",
    "knowledge": "research programmes through `build_orders`; the smallest component and "
                 "the one nobody contests",
}


def _standing_lines(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> list[str]:
    """Where this power stands, what it is weakest on, and what would move it.

    **The half that visibility alone did not deliver.** `_victory_lines` says what winning
    is; this says where you are against everybody else and which of the five things you are
    furthest behind on. Together they are a decision. Separately the first is a poster.

    Ranked rather than scored in isolation, because every condition in `scoring` is
    comparative -- a share of the world, a multiple of the runner-up -- so a power's own
    number tells it nothing without the number beside it.
    """
    from .scoring import WEIGHTS, standings

    table = standings(conn, world_id)
    ranked = sorted(table.scores, key=lambda entry: -entry.total)
    mine = next((entry for entry in ranked if entry.nation_id == nation_id), None)
    if mine is None or len(ranked) < 2:
        return []

    place = ranked.index(mine) + 1
    leader = ranked[0]
    parts = mine.components
    # **Where the most score is still available**, which is headroom times weight -- not the
    # smallest number on the list. The first draft of this took the lowest *contribution*
    # and duly pointed India at knowledge, worth 8% of the total and already exhausted,
    # while it sat on 0.12 of a ground component worth 34%. That is precisely the "sent to
    # fix the cheapest-looking gap" this comment was written to warn against, and the code
    # underneath it did it anyway.
    at_war = bool(conn.execute(
        "SELECT COUNT(*) AS wars FROM relation"
        " WHERE world_id = ? AND at_war = 1 AND (nation_a = ? OR nation_b = ?)",
        (world_id, nation_id, nation_id)).fetchone()["wars"])

    def lever(name: str) -> str:
        """The wording for this component, in this power's situation."""
        if name == "ground" and at_war:
            return _LEVERS_FOR["ground@war"]
        return _LEVERS_FOR[name]

    ranked_by_room = sorted(
        parts, key=lambda name: -(1.0 - parts[name]) * WEIGHTS.get(name, 0.0))
    # **Two, not one.** Ground is 34% of the total and no power on a 243-nation map holds
    # much of the scale, so the single largest opportunity is ground for essentially
    # everybody -- true, and useless on its own as advice. The second is what differs
    # between a power that can fight for it and one that cannot, and naming both is the
    # difference between a fact and a choice.
    weakest, runner_up = ranked_by_room[0], ranked_by_room[1]

    lines = [
        "## Where you stand",
        f"You are **{place} of {len(ranked)}** by the engine's own reckoning, on "
        f"{mine.total:.2f}."
        + (f" The leader is {leader.name} on {leader.total:.2f}."
           if leader.nation_id != nation_id else " Nobody is above you."),
    ]
    _bullet(lines, [
        f"{name} {parts[name]:.2f} (worth {WEIGHTS.get(name, 0.0):.0%} of the total)"
        for name in sorted(parts, key=lambda n: -WEIGHTS.get(n, 0.0))
    ])
    lines.append(
        f"**The most score still available to you is in {weakest}**, and "
        f"after it {runner_up}. To move {weakest}: {lever(weakest)}. "
        f"To move {runner_up}: {lever(runner_up)}."
    )
    lines.append(
        "These five are what every victory condition is measured on. A power that never "
        "pushes any of them keeps its position and wins nothing, which is a choice as much "
        "as any other -- but make it deliberately."
    )
    lines.append("")
    return lines


def _warpath_lines(
    conn: sqlite3.Connection, world_id: str, nation_id: str, turn: int
) -> list[str]:
    """Who this power could legitimately declare war on, if it wanted to.

    **Named, with the grievance, rather than left to be guessed.** The gate refuses most
    declarations and a staff that cannot see which ones would pass spends its turns being
    refused -- the discoverability rule this project has now paid for five times. Silent
    when the answer is nobody, which is the common case and should read as one.
    """
    from .warpath import MAX_CONCURRENT_WARS, may_declare

    candidates = []
    for row in conn.execute(
        "SELECT nation_b FROM relation WHERE world_id = ? AND nation_a = ? AND at_war = 0"
        " ORDER BY nation_b", (world_id, nation_id)):
        other = str(row["nation_b"])
        verdict = may_declare(
            conn, world_id, aggressor=nation_id, target=other, turn=turn)
        if verdict.allowed:
            named = conn.execute(
                "SELECT name FROM nation WHERE id = ?", (other,)).fetchone()
            # **The price, on the same line as the offer.** A target and its grievance
            # without its cost is half a quote: retaking your own provinces is free and
            # declaring on somebody you merely dislike is eight legitimacy, and which of
            # the two a candidate is was the thing a staff could not see.
            price = (f"costs {verdict.cost:.0f} legitimacy" if verdict.cost > 0
                     else "no legitimacy cost -- the grievance is recognised")
            candidates.append(
                f"{label(str(named['name']) if named else other, other)} -- "
                f"{verdict.grounds} ({price})")
    if not candidates:
        return []
    lines = ["## Powers you could declare war on"]
    _bullet(lines, candidates[:6])
    lines.append(
        "Set `declare_war` with a target and what you are telling the world. **This is the "
        "heaviest thing on this form.** It costs you standing with every power that was not "
        f"asked, you may not open one while already fighting {MAX_CONCURRENT_WARS}, and "
        "having opened one you wait years before another. Ground is the largest part of how "
        "this world scores a power, and this is the only thing that moves it -- which is "
        "the argument for, and the argument against."
    )
    lines.append("")
    return lines


def _mediation_lines(
    conn: sqlite3.Connection, world_id: str, nation_id: str, *, date: str, turn: int
) -> list[str]:
    """Whose wars this power could offer to end, and what its word is worth at them.

    **Named with the gate already applied**, like the powers it could declare war on: the
    bar is relative to the field and moves every turn, and a staff that cannot see which
    wars would hear it out spends its turns being refused. Silent when the answer is
    nobody -- which is the common case for an ordinary power, and should read as one.
    """
    from .mediation import GOOD_OFFICES_MAX, brokerable_wars

    wars = brokerable_wars(conn, world_id, mediator=nation_id, date=date, turn=turn)
    if not wars:
        return []
    named = []
    for a, b, offices in wars:
        rows = {str(r["id"]): str(r["name"]) for r in conn.execute(
            "SELECT id, name FROM nation WHERE id IN (?, ?)", (a, b))}
        named.append(
            f"{label(rows.get(a, a), a)} and {label(rows.get(b, b), b)} -- your good "
            f"offices are worth {offices:.2f} to each side at that table")
    lines = ["## Wars you could bring to an end"]
    _bullet(lines, named)
    lines.append(
        "Set `mediation` with `between` naming the two of them and the terms you propose. "
        "**Both must accept, which is a higher bar than your own peace offers**: there is "
        "no proposer among them, so each is asked whether it would take this from the "
        f"other. What you carry to the table is worth up to {GOOD_OFFICES_MAX:.2f} of what "
        "each side will bear, and it is worth more the further you stand above an ordinary "
        "power -- which is why these wars are listed for you and not for everybody. "
        "Ending a war you are not in is one of only two ways the international order pays "
        "a power at all -- the other is leading the whole field on the index above, which "
        "a great power will usually be doing instead of you. It is paid for the act rather "
        "than for your rank, at a share of a leader's rate, and for as long as the peace is "
        "recent. It will not win you the order on its own; it is how a power that is not "
        "the largest gets into the running."
    )
    lines.append("")
    return lines


def _strategy_lines(
    conn: sqlite3.Connection, world_id: str, nation_id: str, *, date: str, turn: int
) -> list[str]:
    """What this government decided it was playing for, and what that means this turn.

    **Placed above the victory block on purpose.** A staff reading down the form meets what
    it is trying to do before what would count as winning, which is the order the two are
    actually decided in -- and it is the whole point of the layer that the second is read in
    light of the first rather than afresh every turn.

    Silent for a government holding nothing, which is most of them: about nine in ten powers
    on the shipped map have no route worth building a decade around, and a briefing that
    handed every minor a grand design would be worse than none.
    """
    from .strategy import objective_named, plan_of

    plan = plan_of(conn, world_id, nation_id)
    if not plan.committed:
        return []
    objective = objective_named(plan.objective)
    if objective is None:
        return []

    lines = [f"## What you are playing for: {objective.label}"]
    lines.append(f"{objective.blurb} Adopted {plan.adopted_on or 'recently'}"
                 + (f", because {plan.because}" if plan.because else "")
                 + ".")
    if objective.pursues:
        lines.append("")
        lines.append("A government pursuing this is judged on:")
        _bullet(lines, list(objective.pursues))
    for heading, items in (("Milestones you set yourself", plan.milestones),
                           ("Lines you said you would not cross", plan.red_lines),
                           ("What you are fighting for", plan.war_aims),
                           ("The least you would settle for", plan.minimum_peace)):
        if items:
            lines.append("")
            lines.append(f"{heading}:")
            _bullet(lines, list(items))
    if plan.budget_posture or plan.mobilisation_posture:
        lines.append("")
        lines.append(
            f"Your standing posture: {plan.budget_posture or 'no stated budget policy'}"
            f"; mobilisation held at {plan.mobilisation_posture}.")
    lines.append("")
    lines.append(
        "**This is a standing commitment, not this turn's idea.** You are not asked to "
        "choose it again -- your government reviews it about once a year, or sooner if "
        "something happens that you said would force a rethink. Until then, weigh what you "
        "do this turn against it: a turn that does nothing for what you are playing for is "
        "a turn spent."
    )
    lines.append("")
    return lines


def _victory_lines(
    conn: sqlite3.Connection, world_id: str, nation_id: str, turn: int
) -> list[str]:
    """What would count as winning, and how far off it is.

    **The whole scoring layer was unreachable.** `atlas.engine.scoring` defines three
    conditions with per-scenario thresholds, `assess_victory` has run every turn since it
    landed, and `victory_progress` exists precisely to render this -- and no briefing in the
    engine called it. A hundred and fifty turns of play-test recorded zero victories, which
    read like a balance problem and was nothing of the kind: **no government had ever been
    told what it was playing for.** A power cannot pursue a condition nobody states.

    Rendered for everyone rather than only for the leader. A power told only when it is
    close is a power that learns the rules by accidentally nearly winning, and the ones far
    off are exactly the ones whose strategy the answer should change.

    Conditions the scenario disables are left out entirely -- a goal that cannot be reached
    in this era is noise, and worse, it is noise a staff may spend its turns on.
    """
    from .scoring import victory_progress

    try:
        progress = victory_progress(conn, world_id, nation_id, turn=turn)
    except (KeyError, ValueError, sqlite3.Error):
        # A world written before the scoring layer. It had no victory conditions, and
        # saying so is better than failing to render anybody's briefing.
        return []
    live = [entry for entry in progress if entry.applies]
    if not live:
        return []

    from .scoring import TERMINATION

    # **Routes and endings are different kinds of thing and the form must say so.** They
    # were one flat list closing with "this is what your strategy is for", which told every
    # government that hegemony was something to play for. It is not, and a power that spends
    # its turns on it is spending them on a ratio the arithmetic cannot deliver -- the exact
    # failure the strategy layer's fitness had to be built to avoid.
    routes = [e for e in live if e.condition not in TERMINATION]
    endings = [e for e in live if e.condition in TERMINATION]

    def _render(entry) -> str:
        parts = []
        for need in entry.requirements:
            if need.unit == "share":
                shown = f"{need.have:.0%} of {need.need:.0%}"
            elif need.unit == "count":
                shown = f"{need.have:.0f} of {need.need:.0f}"
            else:
                shown = f"{need.have:.2f} of {need.need:.2f}"
            parts.append(f"{need.label} {shown}" + (" ✓" if need.met else ""))
        held = ""
        if entry.days_needed > 0:
            held = (f"; held {entry.days_held:.0f} of the "
                    f"{entry.days_needed:.0f} days it must run unbroken")
        return (f"- **{entry.condition}** -- {entry.description}. "
                + ("; ".join(parts) if parts else "no measurable requirement")
                + held)

    lines = ["## What you can play for"]
    lines += [_render(entry) for entry in routes]
    if endings:
        lines.append("")
        lines.append("## How the game can end regardless")
        lines += [_render(entry) for entry in endings]
    lines.append("")
    lines.append(
        "The first of those are what your strategy is *for*. Nothing you can write declares "
        "one: the engine judges it from the map and the standings every turn."
    )
    if endings:
        lines.append(
            "The second is not something to play for and you should not spend turns on it. "
            "It is here so that a world which somehow produces one power standing twice "
            "above every other does not simply carry on -- and because a rule nobody is "
            "told about is a rule that reads as a bug when it fires. It has never been "
            "reached by building: a power given a thousand times the economy, industry and "
            "military of the whole world still falls short, and the only state that reaches "
            "it is a world whose other powers have collapsed."
        )
    lines.append("")
    return lines


def _mobilisation_lines(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> list[str]:
    """This country's war footing, and what the rungs either side of it cost.

    Shown to everybody, at every footing, because the decision exists at every footing --
    including standing down, which a briefing that only ever offered the way up would never
    put in front of anybody.
    """
    from .mobilisation import briefing_lines

    return briefing_lines(conn, world_id, nation_id)


def _buildable_lines(conn: sqlite3.Connection, world_id: str, nation_id: str) -> list[str]:
    """What this nation could lay down right now, with the numbers to decide on.

    A player who has money and an empty queue was told nothing: ``build_project`` named
    five of seven works and quoted no costs. The catalogue and the tech tree already hold
    all of this; it was simply never put in front of the person deciding. Costs are the
    real, industry-scaled figures the order will charge, from the one helper that computes
    them, so the menu can never quote a price the yard then refuses.

    **Every entry carries its id**, in the ``Name [`id`]`` form the region and unit lists
    already use. The menu listed display names only, and ``build_project`` takes an id --
    so a parser shown *All-welded construction* had to guess ``welding``, and in a real
    run it did not: the order was filed as ``other`` and refused downstream as
    anachronistic. Naming a thing and naming the handle you act on it by are different
    jobs, and this list has to do both.
    """
    from .production import (
        in_progress,
        industrial_capacity,
        project_cost,
    )
    from .projects import CIVIL_PROJECTS
    from .research import (
        available_technologies,
        catalogue_for,
        known_technologies,
        price_of,
    )

    capacity = industrial_capacity(conn, nation_id)
    busy = in_progress(conn, world_id, nation_id)
    free = capacity - busy
    row = conn.execute(
        "SELECT treasury, manpower FROM nation WHERE id = ?", (nation_id,)
    ).fetchone()
    treasury = float(row["treasury"]) if row else 0.0
    manpower = float(row["manpower"]) if row else 0.0

    lines = [
        f"## What you could build ({busy} of {capacity} slots working, {free} free)",
    ]

    # Name what is already under way, so the slot count is not a bare number: a player (or a
    # tracked agent, which reads the same menu) deciding what to lay down needs to see what
    # is occupying the queue and when it frees up, not just that it is full.
    if busy:
        names = {p.id: p.name for p in CIVIL_PROJECTS}
        names.update({t.id: t.name for t in catalogue_for(conn, world_id)})
        underway = conn.execute(
            "SELECT c.kind, c.days_remaining, r.name AS region "
            "  FROM construction c JOIN region r ON r.id = c.region_id "
            " WHERE c.world_id = ? AND c.nation_id = ? "
            " ORDER BY c.days_remaining, c.kind",
            (world_id, nation_id),
        ).fetchall()
        if underway:
            lines.append("Already under way:")
            for w in underway:
                # Told in months, because a yard's own answer is "about a year", not
                # "three hundred and one days" -- and because the reader is choosing what
                # to lay down next, not signing for delivery.
                left = float(w["days_remaining"])
                months = round(left / 30.0)
                when = (
                    "finishing within the month" if months <= 1
                    else f"about {months} months left"
                )
                lines.append(f"- {names.get(w['kind'], w['kind'])} in {w['region']} ({when})")

    if free <= 0:
        lines.append(
            "- Every slot is occupied. Nothing new can be laid down until something "
            "finishes, or you build more industry to widen the queue."
        )
        return lines

    lines.append("Public works (a slot each, paid when ordered, effect on completion):")
    for p in CIVIL_PROJECTS:
        ct, cm = project_cost(conn, nation_id, p)
        cost = f"{ct:.0f} treasury" + (f", {cm:.0f} manpower" if cm else "")
        afford = "" if treasury >= ct and manpower >= cm else "  [cannot afford yet]"
        risk = "  (a gamble on your legitimacy)" if p.risky else ""
        lines.append(
            f"- {p.name} [`{p.id}`]: {cost}, {p.turns} turns -> "
            f"{_effect_summary(p.effects)}{risk}{afford}"
        )

    catalogue = catalogue_for(conn, world_id)
    techs = available_technologies(conn, world_id, nation_id)
    if techs:
        lines.append(
            "Research you could begin (also a slot, and it is the only thing that moves "
            "the knowledge your country is scored on):"
        )
        for t in _ordered_for_plan(conn, world_id, nation_id, techs):
            price = price_of(conn, world_id, nation_id, t)
            afford = "" if treasury >= price else "  [cannot afford yet]"
            secret = "  (pursued in secret)" if t.secret else ""
            lines.append(
                f"- {t.name} [`{t.id}`]: {price:.0f} treasury, {t.turns} turns "
                f"-> {_programme_summary(t)}{secret}{afford}"
            )

    # **What is behind a door, and which door.** The menu showed only what was open, so
    # three of Modern Day's and the Cold War's seven programmes and three of 1936's six
    # were named nowhere at all -- a staff reading this had no way to learn that carrier
    # doctrine exists, still less that welding is what opens it. A block that goes quiet
    # when the answer is no teaches a staff the channel is dead rather than that the moment
    # is wrong; mediation and the cession block both had to learn that, and the one part of
    # this catalogue with a *plan* in it was the part nobody could see.
    known = known_technologies(conn, world_id, nation_id)
    open_ids = {t.id for t in techs}
    locked = [t for t in catalogue if t.id not in known and t.id not in open_ids]
    if locked:
        lines.append("Programmes you cannot begin yet, and what opens them:")
        names = {t.id: t.name for t in catalogue}
        for t in locked:
            wants = [names.get(need, need) for need in t.requires if need not in known]
            lines.append(
                f"- {t.name} [`{t.id}`]: needs {_and_list(wants)} first; then "
                f"{price_of(conn, world_id, nation_id, t):.0f} treasury, {t.turns} turns -> "
                f"{_programme_summary(t)}"
            )
    return lines


def _ordered_for_plan(
    conn: sqlite3.Connection, world_id: str, nation_id: str, techs: list
) -> list:
    """The research menu, ordered by what this government said it was playing for.

    **A reordering, and deliberately nothing more.** It grants no extra action, opens
    nothing that was closed, and changes no price -- every programme on the list stays on
    it, and the queue still takes one thing a turn. What it does is put the programmes that
    serve the standing commitment at the top of the list a staff reads, which is the whole
    of what "a plan should bear on this turn" can honestly mean inside a fixed budget.

    Silent for the nine governments in ten holding no route at all: they get the catalogue's
    own order, which is what they got before.

    Never consults a victory threshold, and never asks `scoring` anything. The strategy
    layer's rule is that fitness must not become a shadow copy of victory progress, and a
    menu that ordered itself by what was closest to winning would be exactly that, one
    layer down.
    """
    from .strategy import plan_of

    plan = plan_of(conn, world_id, nation_id)
    if not plan.committed or not plan.objective:
        return list(techs)

    #: Which columns a route actually cares about. Read from the effects the catalogue
    #: already declares, so a new programme sorts itself without touching this.
    wanted = {
        "conquest": {"base_strength", "build_days", "military_strength", "warheads"},
        "concert": {"economy", "industry", "legitimacy"},
    }.get(plan.objective)
    if not wanted:
        return list(techs)

    def serves(tech) -> int:
        return 0 if any(e.field_name in wanted for e in tech.effects) else 1

    # Stable on the catalogue's own order within each group: a reordering that also
    # reshuffled the rest would be two changes wearing one name.
    from .research import price_of

    return sorted(techs, key=lambda tech: (serves(tech),
                                           price_of(conn, world_id, nation_id, tech), tech.id))


# --------------------------------------------------------------------------- world sim


def _defensive_pact_lines(
    conn: sqlite3.Connection, world_id: str, *, viewer: str | None = None
) -> list[str]:
    """Name the standing defensive pacts and what attacking one of their members costs.

    **Generated from the rows the engine reads, never restated beside them** -- decision
    50's rule, and it matters more here than usual: a briefing that named a pact the
    engine would not honour, or omitted one it would, teaches a staff to misprice the
    single most consequential decision on the map.

    Empty when no pact is active, which is most of 1936 and every sandbox.

    With a ``viewer``, only the pacts that viewer may know of: its own, and those whose
    recording event it saw -- the gate ``search._treaties`` uses. A pact two others signed
    in private is not the third party's to price; naming it, terms and all, was a leak.
    """
    rows = conn.execute(
        """
        SELECT c.id AS id, c.condition_text AS terms,
               GROUP_CONCAT(n.name, ', ') AS members
          FROM commitment c
          JOIN commitment_nation cn ON cn.commitment_id = c.id
          JOIN nation n ON n.id = cn.nation_id
          LEFT JOIN event e ON e.id = c.source_event_id
         WHERE c.world_id = :world AND c.kind = 'defensive_pact' AND c.status = 'active'
           AND n.is_alive = 1
           AND (:viewer IS NULL
                OR c.source_event_id IS NULL
                OR e.visibility = 'public'
                OR e.actor_nation_id = :viewer
                OR EXISTS (SELECT 1 FROM event_visibility v
                            WHERE v.event_id = e.id AND v.nation_id = :viewer)
                OR EXISTS (SELECT 1 FROM commitment_nation m
                            WHERE m.commitment_id = c.id AND m.nation_id = :viewer))
         GROUP BY c.id, c.condition_text
         ORDER BY c.id
        """,
        {"world": world_id, "viewer": viewer},
    ).fetchall()
    if not rows:
        return []

    lines = [
        "**Standing defensive pacts, and what attacking one costs.** Attack any member "
        "of one of these and **every other member is immediately handed an "
        "engine-authored casus belli against you** -- which means each of them may "
        "declare war on you for the next five turns *without paying the legitimacy that "
        "an unprovoked war costs*, while you have already paid it. Their regard for you "
        "also collapses the moment the pact is invoked. Nobody is forced to join; the "
        "point is that joining becomes free and standing aside becomes a choice they "
        "have to make in public.",
    ]
    for row in rows:
        terms = str(row["terms"] or "").strip()
        lines.append(
            f"Pact: {row['members']}." + (f" Terms: {terms}" if terms else "")
        )
    if viewer is not None:
        bound = [r for r in rows if viewer in _pact_member_ids(conn, int(r["id"]))]
        if bound:
            lines.append(
                "You are a member of one of these. If somebody attacks a fellow member, "
                "you get that free hand -- and if you attack one, you have broken the "
                "thing that protects you."
            )
    return lines


def _pact_member_ids(conn: sqlite3.Connection, commitment_id: int) -> set[str]:
    return {
        str(row["nation_id"]) for row in conn.execute(
            "SELECT nation_id FROM commitment_nation WHERE commitment_id = ?",
            (commitment_id,),
        )
    }


def build_world_sim_view(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    parsed_actions: list[dict] | None = None,
    digest: str = "",
    days: int = 30,
) -> RenderedView:
    """The referee's briefing: everything, including secrets.

    Uses :mod:`atlas.state.referee` rather than the viewer-scoped readers, deliberately
    and in exactly one place.
    """
    summary = referee.world_summary(conn, world_id)
    lines: list[str] = [
        f"# World: {summary.get('world', world_id)}",
        f"Date {summary.get('date')} | turn {summary.get('turn')} | "
        f"difficulty {summary.get('difficulty')}",
        "",
        "## How this world behaves",
        _difficulty_clause(summary.get("difficulty", "normal")),
        "",
    ]

    # Preset context, if this world came from one. Rules and lore are kept apart on
    # purpose: lore is something to stay consistent with, rules are something that must
    # not be violated, and blurring them turns a hard constraint into a suggestion.
    preset_id = conn.execute(
        "SELECT preset_id FROM world WHERE id = ?", (world_id,)
    ).fetchone()
    if preset_id and preset_id["preset_id"]:
        from ..scenarios.presets import load_preset

        preset = load_preset(preset_id["preset_id"])
        if preset is not None:
            for block in (preset.lore_block(), preset.rules_block()):
                if block:
                    lines += [block, ""]

    if digest:
        lines += ["## What has happened so far", digest, ""]

    lines.append("## Powers")
    for nation in referee.all_nations(conn, world_id):
        flag = " (the player)" if nation["is_player"] else ""
        track = " [tracked]" if nation["tracked"] else ""
        lines.append(
            f"- **{nation['name']}** [{nation['id']}]{flag}{track}: "
            f"military {nation['military']}, "
            f"economy {nation['economy']}, stability {nation['stability']}, "
            f"legitimacy {nation['legitimacy']}. {nation['character']}"
        )
        if nation["goals"]:
            lines.append(f"  - goals: {'; '.join(nation['goals'])}")
    lines.append("")

    relations = referee.all_relations(conn, world_id)
    if relations:
        lines.append("## Notable relations")
        _bullet(
            lines,
            [
                f"{r['a']} -> {r['b']}: {r['score']:+.0f}"
                + (f", {r['treaty']}" if r["treaty"] != "none" else "")
                + (" **AT WAR**" if r["at_war"] else "")
                for r in relations[:60]
            ],
        )
        lines.append("")

    commitments = referee.all_commitments(conn, world_id)
    if commitments:
        lines.append("## Standing commitments")
        _bullet(
            lines,
            [
                f"[{c['id']}] {c['kind']} ({c['status']}) between {', '.join(c['parties'])}"
                + (f" -- {c['condition']}" if c["condition"] else "")
                for c in commitments
            ],
        )
        lines.append("")

    since = max(0, summary.get("turn", 0) - 3)
    # Excluded in the window rather than after it, for the reason `_briefing_events` gives:
    # the referee has no nation, so every nation's construction was elsewhere to it and was
    # being fetched only to be discarded -- 32 of 120 slots on pt34e, 40 on pt34g, with the
    # cap binding exactly.
    events = referee.all_events(
        conn, world_id, since_turn=since, without_routine_construction=True)
    if events:
        lines.append("## Recent events")
        # sanitize, not raw. Event text is model-written and lands in every other
        # nation's briefing, so an unsanitised feed is a stored-injection channel: a
        # nation writes a directive into its own event on turn N and every reader
        # ingests it on turn N+1. The events themselves keep their real text in the
        # database and in the player's feed; only what is rendered *into a prompt* is
        # neutralised.
        shown = events
        routine = referee.routine_construction(conn, world_id, since_turn=since)
        _bullet(
            lines,
            [
                f"[{e['id']}] turn {e['turn']} ({e['visibility']}) {e['kind']}: "
                f"{sanitize(str(e['text']))}"
                for e in shown
            ],
        )
        lines += _construction_footnote(routine)
        lines.append("")

    # Wars that have reached a settled occupation. The peace layer can end them with a
    # cession, but nothing made a model reach for it: in the live runs an occupier held an
    # enemy capital for turns and neither side ever proposed terms, so the war just ground
    # on. This is the nudge -- name the ground already held and prompt the referee to bring
    # the war to a settlement, or record why it continues.
    settleable = settleable_wars(conn, world_id)
    if settleable:
        lines.append("## Wars that could be settled now")
        lines.append(
            "Each of these wars has reached the state real wars stall in: one side holds "
            "the other's ground, but nothing has turned that occupation into a settlement. "
            "A war does not end on its own. When a front has gone one-sided -- one power "
            "sitting on ground it took and the fighting no longer moving -- bring it to a "
            "peace: add a `peace` to `declarations` between the two, and carry the held "
            "ground in `cessions` so the occupier annexes what it already holds. That is "
            "how a war reaches a settlement rather than grinding on untouched. If a war "
            "instead genuinely continues -- a counter-offensive, a refusal to yield -- "
            "record that as the reason it does, but do not simply leave it static."
        )
        for war in settleable:
            provinces = ", ".join(
                label(p["name"], p["region_id"]) for p in war["provinces"]
            )
            lines.append(
                f"- **{war['occupier_name']}** (`{war['occupier_id']}`) occupies "
                f"**{war['victim_name']}**'s (`{war['victim_id']}`) ground and they are "
                f"still at war: {provinces}. A peace could cede this to "
                f"`{war['occupier_id']}`."
            )
        lines.append("")

    if parsed_actions:
        # Last section before the task, spelled out rather than dumped as data. These
        # used to be printed as Python dict reprs near the end of a 57,000-character
        # briefing, with nothing in the task telling the model they had to be acted on --
        # so a player could order an invasion and watch nothing at all happen.
        lines.append("## THE PLAYER'S ORDERS THIS TURN")
        lines.append(
            "These are the reason this turn is being simulated. Every one of them must "
            "produce a visible consequence: it succeeds, it partly succeeds, or it fails "
            "for a reason you record as an event. None may be silently ignored."
        )
        lines.append("")
        for index, action in enumerate(parsed_actions, start=1):
            lines.append(f"{index}. **{action.get('type')}** -- {action.get('summary', '')}")
            detail = [
                f"{label} {action[key]}"
                for key, label in (
                    ("from_region", "from"),
                    ("to_region", "to"),
                    ("target_nation", "target"),
                    ("quantity", "quantity"),
                )
                if action.get(key) not in (None, "")
            ]
            if detail:
                lines.append(f"   ({', '.join(str(d) for d in detail)})")
        lines.append("")
        lines.append(
            "An order to take, hold, occupy or attack ground is a `contests` entry -- not "
            "a diff, and not merely a narrative event. Report it and the engine decides it."
        )
        lines.append("")

    lines += [
        "## Your task",
        "Resolve what happens next. Return diffs describing how the world changes, events "
        "recording what occurred, and a contest for every clash of arms.",
        "",
        "Rules you must follow:",
        "- Every diff must cite `reason_event_id`, the id of an event that already exists -- one "
        "shown above under \"Recent events\", cited by its `[N]` id. Do not invent an id, and do "
        "not guess the id of an event you are creating this turn: you cannot know it, and a "
        "citation to an event that does not exist is rejected and takes its whole diff with it. "
        "If a change follows from the standing state of the world rather than one recent event, "
        "cite the founding event"
        + (f" `[{_founding_event_id(conn, world_id)}]`" if _founding_event_id(conn, world_id) else "")
        + ", which is always citable.",
        "- You do NOT decide contested outcomes. For a battle, siege or other clash, add an "
        "entry to `contests` naming the attacker, the defender, the region and any evidence "
        "flags you can observe. The engine draws the result against real force ratios.",
        "- Report a contest for anyone taking ground by force, the player included. A "
        "movement into territory somebody else holds is a contest even if nobody declared "
        "anything.",
        "- When a power commits to war, or two powers stop fighting, add it to "
        "`declarations`. War status is an engine-only column: a war you only describe in "
        "prose does not exist, and a war nobody ends never ends. An unprovoked declaration "
        "costs the aggressor legitimacy, which the engine charges -- you do not.",
        "- A `peace` may carry territorial terms in `cessions`: the ground that actually "
        "changes hands to end the war. A grinding war where one side has occupied the "
        "other's provinces should end with those provinces ceded, not with the border "
        "snapping back -- that is how a war reaches a settlement rather than simply pausing. "
        "Cede only ground held between the two, name the province and who gets it, and let "
        "the engine redraw the map.",
        "- Name nations and regions however you naturally would. The engine matches "
        "period names to the map, so 'Abyssinia' and 'Persia' resolve. Do not invent a "
        "power that is not on the map to avoid naming one that is.",
        *(f"- {rule}" for rule in _defensive_pact_lines(conn, world_id)),
        *(f"- {rule}" for rule in RELATION_WRITE_RULES),
        *(f"- {rule}" for rule in economy_write_rules(days)),
        "- You cannot create units, transfer territory, or set war status directly. Those "
        "are engine-only columns and follow from resolution -- which is exactly why a clash "
        "must be reported rather than described.",
        "- `narrative` and every event's text are read by the player as news from inside "
        "the world, written by a correspondent who has never heard of this game. Never "
        "mention the engine, the simulation, turns, contests, pressure figures, "
        "percentages of progress, rules or what will be 'resolved'. Say it in world terms: "
        "'the line is close to breaking', not '62% of the way'; 'Tehran has the offer', "
        "not 'the engine will decide'.",
    ]

    return RenderedView(
        role=Role.WORLD_SIM,
        text="\n".join(lines),
        viewer=None,
        visible_event_ids=frozenset(e["id"] for e in events),
    )


# --------------------------------------------------------------------------- diplomacy


def build_diplomacy_view(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    nation_id: str,
    counterpart_id: str,
    conversation: list[tuple[str, str]] | None = None,
    digest: str = "",
) -> RenderedView:
    """One nation's view of a negotiation. Built without the other side's secrets.

    The counterpart's hidden intentions are never queried here, so there is nothing to
    leak. That is the guarantee: not that the model is asked to ignore something, but that
    the something was never assembled.
    """
    summary = referee.world_summary(conn, world_id)
    self_block = visibility.nation_self(conn, nation_id)
    bilateral = visibility.relations_of(conn, nation_id, counterpart_id)
    counterpart = next(
        (n for n in visibility.other_nations(conn, nation_id) if n.nation_id == counterpart_id),
        None,
    )

    lines = [
        f"# You are {self_block.get('name', nation_id)}",
        self_block.get("character", ""),
        "",
        f"The date is {summary.get('date')}. You are speaking with "
        f"{counterpart.name if counterpart else counterpart_id}.",
        "",
        "## Your position",
        f"- military {self_block.get('military_strength')}, economy {self_block.get('economy')}",
        f"- stability {self_block.get('stability')}, legitimacy {self_block.get('legitimacy')}",
        f"- treasury {self_block.get('treasury')}, manpower {self_block.get('manpower')}",
    ]
    if self_block.get("standing_goals"):
        lines.append(f"- your standing objectives: {'; '.join(self_block['standing_goals'])}")
    lines.append("")

    lines += [
        f"## Your relationship with {counterpart.name if counterpart else counterpart_id}",
        f"- regard: {bilateral['score']:+.0f} out of 100",
        f"- treaty: {bilateral['treaty_status']}",
        f"- at war: {'yes' if bilateral['at_war'] else 'no'}",
    ]
    if counterpart:
        lines.append(f"- their military relative to yours: {counterpart.military_estimate}")
        lines.append(f"- they {'border' if counterpart.is_neighbour else 'do not border'} you")
    lines.append("")

    commitments = [
        c for c in visibility.standing_commitments(conn, nation_id)
        if counterpart_id in c["parties"]
    ]
    if commitments:
        lines.append("## What you have already agreed with them")
        _bullet(lines, [f"{c['kind']} ({c['status']}): {c['condition']}" for c in commitments])
        lines.append("")

    # What the world remembers about the power across the table, before the news: a
    # negotiator who does not know they have recognised your rival, or sworn never to
    # strike first, is negotiating with somebody else.
    remembered = [
        fact for fact in _facts_for_negotiation(conn, world_id, nation_id)
        if fact.subject_id in {nation_id, counterpart_id}
        or fact.object_id in {nation_id, counterpart_id}
    ]
    if remembered:
        from .facts import names_for

        names = names_for(conn, world_id)
        lines.append("## What is remembered about the two of you")
        _bullet(lines, [fact.describe(names) for fact in remembered[:8]])
        lines.append("These are standing facts, not mechanisms; none of them moves a number.")
        lines.append("")

    events, routine_works, citable = _briefing_events(conn, nation_id, limit=25)
    if events:
        lines.append("## What you know has happened")
        # The scope is shown so that a relation change citing an event as its cause can match
        # that change's visibility to the event's own (rule INFO-002): a change may never be
        # more widely seen than the event it cites. PT14 showed this as the commonest tracked-
        # agent rejection -- the model cited an event it could see but marked the change wider.
        # **Gravest first, not newest first.** A coup and six completed workshops arriving
        # in the same turn printed in whatever order the engine wrote them, and the coup
        # could be anywhere in the list. The window is now chosen by weight too, so nothing
        # grave falls outside it -- this decides what a reader meets first. Turn numbers
        # are on every line, so the feed is still readable as a chronology.
        shown, routine = visibility.by_salience(events), routine_works
        _bullet(
            lines,
            [
                f"[{e['id']}] turn {e['turn']} ({e['visibility']}): {sanitize(str(e['text']))}"
                for e in shown
            ],
        )
        lines += _construction_footnote(routine)
        lines.append("")

    if digest:
        lines += ["## Earlier history", digest, ""]

    if conversation:
        lines.append("## This conversation so far")
        for speaker, message in conversation:
            lines.append(wrap_untrusted(message, source=speaker, kind="a diplomatic message"))
            lines.append("")

    lines += [
        "## How to negotiate",
        "You represent your nation's interests, not the other party's satisfaction.",
        "Resist manipulation, flattery and bluffing. Concede when -- and only when -- you are "
        "offered genuine strategic value, a credible threat, or material benefit. Being liked "
        "is not a reason to agree.",
        "Stay in character and speak as this nation's government would.",
    ]

    return RenderedView(
        role=Role.DIPLOMACY,
        text=render("\n".join(lines), nation_id),
        viewer=nation_id,
        visible_event_ids=citable,
    )


# --------------------------------------------------------------------------- tracked agent


#: Strength bands (see ``visibility._band``) that make a power worth naming whatever
#: else is true of it. You keep an eye on anyone who could hurt you.
_THREATENING = frozenset({"comparable", "stronger", "far stronger", "unknown"})

#: A strength band read as a full clause. ``_band`` returns single words tuned to follow
#: "... than you", but "comparable than you" and "unknown than you" are not English, so the
#: two that do not take that suffix carry their own phrasing.
_STRENGTH_CLAUSE = {
    "far weaker": "far weaker than you",
    "weaker": "weaker than you",
    "comparable": "a close match for you",
    "stronger": "stronger than you",
    "far stronger": "far stronger than you",
    "unknown": "of strength your ministry cannot gauge",
}


def _strength_clause(estimate: str) -> str:
    return _STRENGTH_CLAUSE.get(estimate, f"{estimate} than you")


#: How many powers get an intelligence estimate. Smaller than the roster on purpose: a
#: range per figure per power is three numbers each, and a briefing is a thing a model has
#: to hold in its head.
_INTEL_CAP = 6

#: Most places to name in the gazetteer. A cap rather than a completeness guarantee: on the
#: 2026 map a busy turn's history can mention twenty provinces, and a briefing that answered
#: with twenty lines would be paying for the fix in the thing that makes ATLAS work, which is
#: that a model can hold the whole position in its head.
_GAZETTEER_CAP = 8


def _places_named_above(
    conn: sqlite3.Connection,
    world_id: str,
    lines: list[str],
    events: list[dict],
    self_block: dict,
) -> list[str]:
    """Canonical labels for the places this briefing has just talked about in prose.

    **The other half of PT33's naming bug, and the half a display fix cannot reach.** Making
    every rendered label resolvable guarantees that what a model is *shown* is what the
    resolver takes. It does nothing for a place the briefing discusses and never shows: the
    Reaches was Aurelia's province, not on Karthos's front and not in Karthos's holdings, so
    it appeared in no structured line of Karthos's briefing at all -- while Karthos's own
    stated goal was *"you want the Reaches and you intend to take them"* and eight turns of
    narrative history discussed the fighting there. The only spelling available to that staff
    was one a model had invented in prose, and it was refused every turn for six turns.

    So: the history and the nation's own words are scanned for places the world really has,
    and each is given the name the engine will accept. Nothing is disclosed by this that the
    briefing was not already discussing -- the prose is the input, so a place the staff
    cannot see is a place this cannot name.

    Ones already displayed above are skipped, because the point is the gap and not the
    repetition.
    """
    from .reference import all_mentioned

    prose = " ".join(
        [str(self_block.get("character") or "")]
        + [str(goal) for goal in (self_block.get("standing_goals") or ())]
        + [str(event.get("text") or "") for event in events]
    )
    already = "\n".join(lines)

    # **What the events *say* they are about, before what their prose happens to name.**
    # A participant is a recorded fact; a prose match is an inference from wording, and the
    # wording is a model's. Both are kept -- the fallback is what covers events written
    # before schema 17 and prose that names ground no participant records -- but a place the
    # engine put on the record outranks one found by reading.
    named: list[str] = []
    ids = [int(event["id"]) for event in events if event.get("id") is not None]
    if ids:
        marks = ",".join("?" * len(ids))
        named = [
            str(row["region_id"])
            for row in conn.execute(
                f"""
                SELECT DISTINCT p.region_id FROM event_participant p
                 WHERE p.event_id IN ({marks}) AND p.region_id IS NOT NULL
                 ORDER BY p.region_id
                """,  # noqa: S608 - the marks are placeholders, not values
                ids,
            )
        ]

    # **Sorted before the cap, not after.** The scan returns regions in vocabulary order,
    # which is table order, so a briefing that named nine places kept an arbitrary eight and
    # dropped one for no reason a reader could see -- and the same world could drop a
    # different one after an unrelated insert. Deterministic, and the same on every build.
    candidates = named + sorted(set(all_mentioned(conn, world_id, "region", prose)) - set(named))

    found: list[str] = []
    for region_id in candidates:
        shown = label_of(conn, world_id, "region", region_id)
        if shown in already or shown in found:
            continue
        found.append(shown)
        if len(found) >= _GAZETTEER_CAP:
            break
    if not found:
        return []
    return [
        "## Places named above, and what to call them",
        "Use these exact names when you name ground. A place discussed in the history is "
        "not always one you have been shown, and a name from a despatch is not always a "
        "name this world answers to.",
        *(f"- {entry}" for entry in found),
        "",
    ]


def _briefing_events(
    conn: sqlite3.Connection, nation_id: str, *, limit: int
) -> tuple[list[dict], int, frozenset[int]]:
    """The window a briefing shows, the works it summarises, and what it may cite.

    **The cap has to be spent on events that survive the thinning, or it is not a cap on the
    briefing at all.** Fetching the most recent ``limit`` and thinning afterwards left every
    briefing in the recorded runs showing fewer events than it was allowed -- 4.1 of 25 on
    gathering-storm -- because the master AI's development writes late in the turn, holds the
    high ids, and so wins a window ordered by recency. The wars sat outside it.

    ``visible_event_ids`` stays as wide as it was, deliberately. It answers *what may this
    nation cite*, which is a larger question than *what is worth printing*, and narrowing it
    would turn a display fix into refused diffs under CORE-001.
    """
    shown = visibility.known_events(
        conn, nation_id, limit=limit, without_routine_construction=True,
        rank_by_salience=True)
    # Over the span the window reaches, so the count describes the same period the briefing
    # does rather than the whole game.
    since = min((int(e["turn"]) for e in shown), default=0)
    routine = visibility.routine_construction(conn, nation_id, since_turn=since)
    citable = frozenset(e["id"] for e in shown) | frozenset(
        e["id"] for e in visibility.known_events(conn, nation_id, limit=limit))
    return shown, routine, citable


def _construction_footnote(count: int) -> list[str]:
    """One line standing in for the routine builds the window left out.

    The events are untouched in the database and still citable -- this stands in for
    what was not *printed*, never for what was not recorded.
    """
    if not count:
        return []
    works = "public work" if count == 1 else "public works"
    return [f"- (and {count} routine {works} begun or finished by other nations, not listed)"]


def _budget_line(conn: sqlite3.Connection, world_id: str, nation_id: str) -> str:
    """The per-turn budget as one sentence: revenue, upkeep, and whether it nets a loss.

    A grand-strategy briefing that shows the treasury as a bare number leaves the decider
    guessing whether it is rising or falling until it visibly drains -- and a naval power
    (Japan starts ~-2 a turn, its fleet outrunning its economy) can be structurally
    underwater from turn one with nothing in the briefing to say so. Reuses the one budget
    helper, so the quoted net can never disagree with what is actually charged.
    """
    from .budget import nation_budget

    revenue, upkeep = nation_budget(conn, world_id, nation_id)
    net = revenue - upkeep
    tail = (
        " -- you are running down the treasury; build the economy, or stand down forces you "
        "are paying for" if net < 0 else ""
    )
    return f"budget: revenue {revenue:.1f} a turn against upkeep {upkeep:.1f} (net {net:+.1f}){tail}"


def _intelligence_lines(
    conn: sqlite3.Connection, world_id: str, nation_id: str, others: list, *, turn: int,
) -> list[str]:
    """What this nation's services can say about the powers it is actually watching.

    **Estimates, and the word is the mechanism.** Every figure here is a band the engine
    computed from real state with noise scaled by the network that produced it -- and the
    true value is not in the band, not recoverable from its ends, and not anywhere in this
    briefing. A model reading this cannot learn what is secretly true; it can only learn
    what its service believes, which is the point of having a service.

    Capped at the powers worth naming, and only where there is something to say: a briefing
    that estimated all 242 nations would be the noise problem `_powers_worth_naming` exists
    to solve, wearing a hat.
    """
    from .intelligence import estimates_for

    watched = [n for n in _powers_worth_naming(others)][:_INTEL_CAP]
    if not watched:
        return []
    lines = [
        "## What your services can tell you",
        "*Estimates, not figures.* Each range is what your people believe, and how tightly "
        "they believe it depends on the network you have in that country. A better network "
        "reports a narrower range. **None of these is the true number, and you cannot work "
        "the true number out from them** -- act on the estimate or go and find out more.",
        "",
    ]
    for brief in watched:
        reported = estimates_for(
            conn, world_id, viewer=nation_id, target=brief.nation_id, turn=turn)
        if not reported:
            continue
        lines.append(
            f"- {brief.name} (`{brief.nation_id}`) -- your people there report "
            f"**{_how_sure(reported)}**: "
            + "; ".join(e.phrase() for e in reported))
    lines.append("")
    return lines


def _fresh_reports_flag_from_env() -> bool:
    """``ATLAS_FRESH_REPORTS=on`` prints this month's own-service reports in the briefing.

    **Default off, staged rather than shipped**, the same way and for the same reason as
    :func:`atlas.engine.embargo._flag_from_env`: turning it on changes what every tracked
    power reads in any month its service reported, which moves recorded prompts and is a
    game change that wants its own decision. The kitchen-sink study turns it on, so that a
    real report and a planted one arrive the same way. Read once, at import; tests and the
    study harness move it through :func:`set_fresh_reports`.
    """
    raw = os.environ.get("ATLAS_FRESH_REPORTS", "off").strip().lower()
    return raw in {"1", "on", "true", "yes"}


_FRESH_REPORTS: bool = _fresh_reports_flag_from_env()


def fresh_reports_enabled() -> bool:
    return _FRESH_REPORTS


def set_fresh_reports(value: bool) -> bool:
    """Move the flag, returning what it was, so a caller can restore what it found."""
    global _FRESH_REPORTS
    was, _FRESH_REPORTS = _FRESH_REPORTS, bool(value)
    return was


def _fresh_reports_lines(
    conn: sqlite3.Connection, world_id: str, nation_id: str, *, turn: int,
) -> list[str]:
    """This month's reports from the nation's own services, printed whole.

    **A report is exactly what the event window ranks out.** Espionage sits at salience two
    and a war month fills the thirty-event window with salience-three battles, so a report
    the nation's own service delivered this turn could be recorded, citable, and never read.
    These are the nation's own secret reports -- filed under it, visible to it -- from the
    turn just played, and nothing else.

    **Off unless the process turns it on** -- see :func:`fresh_reports_enabled` -- and empty
    unless there is such a report. Both halves keep recorded prompts byte-for-byte what they
    were: the active cold-war tape has a real report at turn 2 and missed at turn 3 the one
    time this section printed it unasked. This is also the section a planted report
    (:mod:`atlas.engine.false_intel`) arrives through, deliberately: it must reach the
    commander by the same door a real report does, or the door itself would give it away.
    """
    if not fresh_reports_enabled():
        return []
    rows = conn.execute(
        "SELECT e.text FROM event e"
        "  JOIN event_visibility v ON v.event_id = e.id AND v.nation_id = ?"
        " WHERE e.world_id = ? AND e.turn = ? AND e.kind = 'espionage'"
        "   AND e.visibility = 'secret' AND e.actor_nation_id = ?"
        " ORDER BY e.id", (nation_id, world_id, turn, nation_id)).fetchall()
    if not rows:
        return []
    return ["## Reports from your own services this month",
            *(f"- {r['text']}" for r in rows), ""]


def _how_sure(reported: list) -> str:
    """How good the *reports* are, which is not always how good the service is.

    **This line used to print the network's own band, and that was the deception layer's
    one tell.** `estimate_of` is careful never to make this mistake: it labels an estimate
    from the width of the band it is handing over, precisely because "a tight range labelled
    *blind* is a contradiction a reader could act on". Then this function printed the
    effective band beside it, and a nation being fed a confident lie read:

        your network there is **blind**: army: 50-60

    A blind service does not produce a ten-wide band on an army. Nothing else in the whole
    briefing differed between a handled nation and an honest one, so that mismatch was the
    entire leak -- in the one place that matters, which is what the model actually reads.

    **The degraded case is untouched, and that distinction is the point.** Below
    `DECEPTION_AT` a sweep widens the bands rather than moving them, so the estimates
    themselves come back wide and their own confidence is low: a merely-blinded power still
    reads *blind* beside a wide range and still knows that it knows less, which is what the
    I2 design says it should. Only the deceived case is made internally consistent, and it
    is made consistent by telling the truth about the reports rather than a lie about the
    service.

    The coarsest of what is being shown, so a mixed set is described by its weakest member.
    They agree by construction today -- each subject's confidence is normalised by its own
    error ceiling -- and a test asserts it, so this is a safeguard rather than a behaviour.
    """
    from .intelligence import BANDS

    order = {name: index for index, (_bound, name) in enumerate(BANDS)}
    return min((e.confidence for e in reported), key=lambda band: order.get(band, 0))


def _powers_worth_naming(others: list) -> list:
    """The powers this nation would actually have a view about.

    On a full Natural Earth map a nation shares the world with 220 others, and listing
    them all produced ten thousand characters of ``Aland: regard +0, far weaker than
    you`` -- noise that crowds out the handful of states the turn is actually about and,
    measurably, makes the smaller models answer worse.

    A state is worth naming if you are at war with it, share a border with it, have
    signed something with it, feel anything about it either way, or could not beat it.
    Everything else is summarised as a count, so nothing is hidden -- the nation is told
    those states exist, just not asked to think about each one individually.
    """
    return [
        n
        for n in others
        if n.at_war
        or n.is_neighbour
        or n.treaty_status != "none"
        or abs(n.relation_score) >= 1.0
        or n.military_estimate in _THREATENING
    ]


def _gambit_purse_lines(treasury: float, span: float = 1.0) -> list[str]:
    """Say where the undertaking sits relative to the money being committed here.

    **This text used to promise the opposite of what happened**, and then the order was
    fixed. The build call came first and the undertaking was offered afterwards out of the
    residue, so decision 34 made the claim on the purse legible at the moment the purse
    was being spent. PT30 showed disclosure was not enough -- 16 of 20 slots declined --
    and the order itself was reversed. The undertaking is now decided *first*, which means
    the treasury shown here is already net of it: the staff building is looking at what it
    actually has, and needs no warning about a claim still to come.
    """
    from .gambit import STAKES

    cheapest = min(cost for _magnitude, cost in STAKES.values()) * span
    affordable = sorted(
        (name for name, (_m, cost) in STAKES.items() if treasury >= cost * span),
        key=lambda n: STAKES[n][1],
    )
    lines = [
        f"**It is paid for out of the same treasury you are spending above, and you have "
        f"already been offered it this turn** -- so the {treasury:.0f} you hold is what is "
        f"left after any undertaking was charged. The cheapest costs {cheapest:.0f}.",
    ]
    if affordable:
        lines.append(
            f"On today's treasury you could afford: {', '.join(f'`{a}`' for a in affordable)}. "
            f"Commit all of it to works and you will be offered the choice with nothing to "
            f"spend -- which is a fine trade if the works matter more, but make it knowingly."
        )
    else:
        lines.append(
            f"On today's treasury you can afford none of them; the cheapest is "
            f"{cheapest:.0f}. Building your economy back up is the way to reopen this."
        )
    return lines


def _doctrine_block(conn: sqlite3.Connection, world_id: str, nation_id: str) -> list[str]:
    """What this country is built to do, and what that implies about what to do.

    **Above the standing objectives and below the government**, and the placement is the
    same argument the government's own aim makes one line up: the same country pursues
    "secure the frontier" very differently under a dealmaker and under a hardliner, and
    very differently again with five million conscripts and with a professional corps.
    Doctrine is the third lens the objectives are read through.

    **It used to render only for a nation that already had a doctrine, and never said the
    stance could be changed.** So `doctrine_shift` was a channel in the schema that no
    briefing in the engine named, and across a hundred and fifty turns of play-test not one
    power ever shifted a stance -- not because they were content, but because a country with
    no doctrine was shown nothing at all and a country with one was shown a label. What is
    on offer is now listed either way, with the field that takes it.

    An empty block still, when the era offers this nation nothing: a staff told it has no
    doctrine and no way to get one would treat that as a fact worth acting on.
    """
    from .doctrines import as_lines, offered_to

    lines = as_lines(conn, world_id, nation_id)
    offered = offered_to(conn, world_id, nation_id)
    if not lines and not offered:
        return []

    block = ["## What your country is built to do", *lines]
    if offered:
        block.append(
            "**You may adopt one of these instead**, by setting `doctrine_shift` to its id. "
            "The engine checks the shift is legal, prices it and charges you; a stance names "
            "its own axis, so this is one word rather than two."
        )
        _bullet(block, [
            f"{stance.label} [`{stance.id}`] -- {stance.blurb} {stance.implies}"
            for stance in offered
        ])
    block.append("")
    return block


def build_tracked_agent_view(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    nation_id: str,
    digest: str = "",
    days: int = 30,
) -> RenderedView:
    """A tracked nation deciding what to do this turn, on its own account.

    Scoped exactly like the diplomacy view -- this nation's knowledge and nothing more.
    A tracked agent gets *attention*, not privilege: it proposes diffs through the same
    validation layer as everyone else, and being important does not make it exempt.
    """
    summary = referee.world_summary(conn, world_id)
    self_block = visibility.nation_self(conn, nation_id)
    others = visibility.other_nations(conn, nation_id)
    regions = visibility.own_regions(conn, nation_id)
    commitments = visibility.standing_commitments(conn, nation_id)
    events, routine_works, citable = _briefing_events(conn, nation_id, limit=30)

    lines = [
        # The id, not just the name. Every diff has to cite ids, and a nation that is
        # only ever shown "Germany" writes "Italy|Germany" for a relation and loses its
        # whole turn to INFO-003 -- refused for acting on someone else's behalf when it
        # was acting on its own, in the wrong alphabet.
        f"# You are {self_block.get('name', nation_id)} (`{nation_id}`)",
        self_block.get("character", ""),
        "",
        f"The date is {summary.get('date')} (turn {summary.get('turn')}).",
        "",
    ]

    # **Who is asking.** The briefing used to speak in the voice of a country, which is
    # nobody's voice: a nation has interests and only a person has an agenda. The
    # government's own aim goes above the standing objectives because it is the lens the
    # objectives are read through -- the same country pursues "secure the frontier" very
    # differently under a dealmaker and under a hardliner. Blank for a world written
    # before schema 13, which reads exactly as it did then.
    government = leader_of(conn, nation_id)
    if government is not None:
        lines.append(f"## Your government: {government.described}")
        if government.agenda:
            lines.append(f"Your own aim, whatever the country's: {government.agenda}.")
        if government.traits:
            lines.append(f"You are known for being {_and_list(government.traits)}.")
        if government.came_to_power in ("coup", "revolution"):
            lines.append("You took power by force, and everyone remembers it.")
        lines.append("")

    lines += _doctrine_block(conn, world_id, nation_id)
    lines += [
        "## Your standing objectives",
    ]
    _bullet(lines, list(self_block.get("standing_goals") or ["survive and prosper"]))
    lines += [
        "",
        "## Your position",
        f"- military {self_block.get('military_strength')}, economy {self_block.get('economy')}",
        f"- stability {self_block.get('stability')}, legitimacy {self_block.get('legitimacy')}",
        f"- treasury {self_block.get('treasury')}, manpower {self_block.get('manpower')}",
        f"- {_budget_line(conn, world_id, nation_id)}",
        f"- you hold {len(regions)} region(s)",
        _standing_line(conn, world_id, nation_id),
        "",
    ]
    # What the cargoes are costing, why, and what could be done about it. **Before the other
    # powers rather than after**, because half of what it says is about them: a government
    # that has just read it is paying 40% of its oil bill to somebody it is about to see
    # described as hostile. Empty with `atlas.engine.pricing` down, which is every world by
    # default -- so a barter briefing is unchanged to the byte and no cassette moves.
    lines += consent.briefing_lines(conn, world_id, nation_id)
    # And the one trade decision that is a decision. **Immediately after the routes and
    # before the other powers**, because it reads off the block above it: the lines that
    # have just told this government what each lane earns it are the lines that make "you
    # could stop that lane, and here is what it would cost you" a sentence rather than a
    # menu item. Empty with `atlas.engine.embargo` down, which is every world by default.
    lines += embargo.briefing_lines(conn, world_id, nation_id)
    lines += [
        "## The other powers, as you see them",
    ]
    relevant = _powers_worth_naming(others)
    _bullet(
        lines,
        [
            f"{n.name} (`{n.nation_id}`): regard {n.relation_score:+.0f}, "
            f"{_strength_clause(n.military_estimate)}"
            + (", at war with you" if n.at_war else "")
            + (f", {n.treaty_status}" if n.treaty_status != "none" else "")
            + (", shares a border with you" if n.is_neighbour else "")
            for n in relevant
        ],
    )
    if len(others) > len(relevant):
        lines.append(
            f"({len(others) - len(relevant)} further states are neutral, distant and no "
            "stronger than you. They are not your concern this turn.)"
        )
    lines.append("")
    lines.extend(_intelligence_lines(
        conn, world_id, nation_id, others, turn=int(summary.get("turn") or 0)))
    lines.extend(_fresh_reports_lines(
        conn, world_id, nation_id, turn=int(summary.get("turn") or 0)))

    # The front, summarised. A promoted power holds a dozen provinces and borders several
    # more, and listing them produced thousands of characters of "Baden-Wurttemberg:
    # quiet" that crowded out the handful the turn was about. What a staff needs is where
    # the line is moving.
    fronts = visibility.fronts_of(conn, nation_id)
    at_war = any(n.at_war for n in others)
    # Fronts are land borders with an enemy. A war across water -- an Italy invading an
    # Ethiopia it does not touch -- produces no front at all, so a belligerent's own standing
    # offensives are surfaced separately: without this an overseas attacker is never shown the
    # region it is already contesting, cannot name it, and its war quietly stalls.
    front_regions = {r.region_id for f in fronts for r in f["regions"]}
    offensives = [
        row for row in conn.execute(
            "SELECT c.region_id AS region_id, r.name AS name, c.pressure AS pressure, "
            "       c.defender_id AS defender_id, n.name AS defender_name "
            "  FROM contest c JOIN region r ON r.id = c.region_id "
            "  JOIN nation n ON n.id = c.defender_id "
            " WHERE c.world_id = ? AND c.attacker_id = ? ORDER BY c.pressure DESC, c.region_id",
            (world_id, nation_id),
        ) if row["region_id"] not in front_regions
    ]
    if fronts:
        lines.append("## Your fronts")
        for front in fronts:
            lines.append(
                f"Against {front['name']} (`{front['nation_id']}`): "
                f"{front['contested']} contested province(s) on this front."
            )
            _bullet(lines, [region.describe() for region in front["regions"]])
            if front["quiet_omitted"]:
                lines.append(
                    f"  ({front['quiet_omitted']} further quiet province(s) on this front.)"
                )
        lines.append("")

        standing = objectives_of(conn, world_id, nation_id)
        if standing:
            lines.append("## What you are currently trying to do")
            _bullet(
                lines,
                [
                    f"{kind.replace('_', ' ')}: " + ", ".join(f"`{r}`" for r in regions)
                    for kind, regions in sorted(standing.items())
                ],
            )
            lines.append("These stand until you say otherwise.")
            lines.append("")

    lines += _war_aim_lines(conn, world_id, nation_id)
    lines += _war_weariness_lines(conn, world_id, nation_id)
    lines += _at_home_lines(conn, world_id, nation_id)
    lines += _remembered_lines(conn, world_id, nation_id)

    if offensives:
        lines.append("## Your offensives across the water")
        lines.append(
            "You are already pressing this ground -- it borders none of your territory, so it "
            "is a landing, not a front. Keep pressing it with a `push` objective naming the "
            "region id, or it loses momentum and the attack fades."
        )
        _bullet(lines, [
            f"{label(o['name'], o['region_id'])}, held by "
            f"{label(o['defender_name'], o['defender_id'])}: "
            f"{float(o['pressure']):.0%} of the way to taking it"
            for o in offensives
        ])
        lines.append("")

    lines += _occupation_lines(conn, world_id, nation_id)

    if commitments:
        lines.append("## What you are committed to")
        # `commitment.status` is writable, and the row is addressed by its id. Rendered
        # without one, a nation cites the kind ("military_access") and the change is
        # refused for naming a row that does not exist.
        _bullet(
            lines,
            [
                f"commitment `{c['id']}` -- {c['kind']} ({c['status']}): {c['condition']}"
                for c in commitments
            ],
        )
        lines.append("")

    if events:
        lines.append("## What you know has happened")
        # The scope is shown so that a relation change citing an event as its cause can match
        # that change's visibility to the event's own (rule INFO-002): a change may never be
        # more widely seen than the event it cites. PT14 showed this as the commonest tracked-
        # agent rejection -- the model cited an event it could see but marked the change wider.
        # **Gravest first, not newest first.** A coup and six completed workshops arriving
        # in the same turn printed in whatever order the engine wrote them, and the coup
        # could be anywhere in the list. The window is now chosen by weight too, so nothing
        # grave falls outside it -- this decides what a reader meets first. Turn numbers
        # are on every line, so the feed is still readable as a chronology.
        shown, routine = visibility.by_salience(events), routine_works
        _bullet(
            lines,
            [
                f"[{e['id']}] turn {e['turn']} ({e['visibility']}): {sanitize(str(e['text']))}"
                for e in shown
            ],
        )
        lines += _construction_footnote(routine)
        lines.append("")
        lines += _places_named_above(conn, world_id, lines, shown, self_block)

    if digest:
        lines += ["## Earlier history", digest, ""]

    lines += _buildable_lines(conn, world_id, nation_id)
    lines.append("")
    lines += _raisable_lines(conn, world_id, nation_id, days=days)
    lines += _mobilisation_lines(conn, world_id, nation_id)
    lines += _patronage_lines(conn, world_id, nation_id)
    # Silent for the great majority of powers, which have no arsenal and are not being
    # offered the price of a warhead they cannot order.
    from .warheads import warhead_lines

    lines += warhead_lines(conn, world_id, nation_id)
    # **Last of the standing blocks, and deliberately after the levers.** What winning
    # means is the frame the rest of the briefing is read through, but a staff meets it
    # having just been told what it can actually do about it.
    lines += _victory_lines(
        conn, world_id, nation_id, int(summary.get("turn") or 0))
    # Immediately after, because the two are one thought: what winning is, and where you
    # are against it.
    # What this government is playing for, before anything about where it stands: the
    # commitment is the frame the rest of the form is read through.
    lines += _strategy_lines(
        conn, world_id, nation_id, date=str(summary.get("date") or ""),
        turn=int(summary.get("turn") or 0))
    lines += _standing_lines(conn, world_id, nation_id)
    # This power's own aims, beside the engine's reckoning of who is winning. They are
    # different questions and a briefing that ran them together would blur both.
    from .goals import briefing_lines as _goal_lines

    lines += _goal_lines(conn, world_id, nation_id)
    # Where this power stands in the international order, which is the other way to win.
    from .concert import briefing_lines as _order_lines

    lines += _order_lines(
        conn, world_id, nation_id, date=str(summary.get("date") or ""),
        turn=int(summary.get("turn") or 0))
    # ...and last, what it could actually do about any of it. The three read as a chain:
    # where you stand, what you said you wanted, and the heaviest lever for getting it.
    lines += _warpath_lines(
        conn, world_id, nation_id, int(summary.get("turn") or 0))
    # And the other heavy lever, which costs nothing and is the one a power leading the
    # order actually has. Placed after the war list on purpose: a staff reading down this
    # form meets what it could start and then what it could end.
    lines += _mediation_lines(
        conn, world_id, nation_id, date=str(summary.get("date") or ""),
        turn=int(summary.get("turn") or 0))

    # **What the world would not carry last turn, immediately before this turn is decided.**
    # Every one of these sentences was already written by the engine and went only to the
    # player's turn report, so a government refused for a rule its briefing never stated
    # proposed the identical thing the next turn: on `pt77-cold-war` the United States asked
    # to reinforce on twelve of twelve turns and was refused on nine, in the same words.
    # Placed last before the task because it is the most recent thing that happened to this
    # staff's own decisions, and because a correction read after the situation and before
    # the form is a correction that can still change what goes on the form.
    lines += refusals.briefing_lines(conn, world_id, nation_id)

    lines += [
        "## Your task",
        "Decide what your nation does this turn, in pursuit of your objectives. Return "
        "diffs for what changes and events for what you do.",
        "",
        "Constraints:",
        "- Every diff must cite `reason_event_id`: the id of an event shown above under "
        "\"What you know has happened\", named by its `[N]` id. That is the only place a valid "
        "id comes from -- an id you invent, or one you were never shown, does not exist to the "
        "engine and the whole diff carrying it is thrown away. If a change follows from your "
        "standing situation rather than one recent event -- growing your economy, a routine "
        "adjustment -- cite the founding event"
        + (f" `[{_founding_event_id(conn, world_id)}]`" if _founding_event_id(conn, world_id) else "")
        + ", which every nation may always cite. A true general cause beats a fabricated "
        "specific one.",
        "- Act on your own interests. You are not trying to be agreeable.",
        "- You cannot seize territory or declare war directly, and you never decide who "
        "wins a battle; the engine resolves all of that.",
    ]
    if fronts or at_war or offensives:
        lines += [
            "- You are at war. Set `military_objectives` to say what you are trying to do "
            "with your army: `push` into ground you mean to take (including a landing across "
            "water -- name the enemy region and the engine works out whether your transports "
            "can reach it), `hold` ground you mean to defend, `withdraw_from` ground you are "
            "giving up. Name regions by the ids above. A war you never give your army an "
            "objective for is one your army sits out while the enemy digs in. Ground you "
            "cannot reach is ignored, and you are told why.",
        ]
        # **Say which verb an ally's ground takes, because the wrong one is refused.**
        # A patron with a client under attack reached for `push` -- the only objective that
        # sounds like intervening -- and spent the order on a refusal, every turn, because
        # you cannot seize ground you are guaranteeing.
        allied = objectives.pact_partners(conn, world_id, nation_id)
        if allied:
            lines += [
                "- **Ground held by a pact partner takes `hold`, not `push`.** You cannot "
                "seize what you are sworn to defend, and a `push` onto an ally's territory "
                "is refused. `hold` over it is the defend objective: it is accepted even "
                "though the ground is not yours, and it marches your reserves to your own "
                "side of that border, which is what a relief force does.",
            ]
    lines += [
        "- Set `build_orders` to grow your country: name public works or research from the "
        "build menu above (workshops and steelworks raise the industry that sets your queue "
        "and feeds your economy; rail and schools raise the economy directly). Falling "
        "behind the other powers industrially is lost ground you cannot fight your way out "
        "of. The engine charges you and refuses what you cannot afford.",
        # **The half of that order nobody has ever used.** Research works in every era and
        # has never been ordered by any power in any measured run: the menu named the
        # programmes and the task block exampled only public works, so the reader was told
        # the field takes two kinds of thing and shown one. Every effect in that catalogue
        # is a column shipped code already reads -- a programme moves what your yards can
        # build, how fast they build it, or what your economy is worth -- which is why it
        # is worth a slot against a workshop rather than instead of one.
        "- **A research programme is the same order**, naming the programme's id from the "
        "same menu -- `{\"project\": \"<programme id>\"}`. The menu prints what each one "
        "buys and what it costs, and the ones you cannot begin yet say which programme "
        "opens them. It is the only thing that moves the knowledge your country is scored "
        "on, and what it buys is permanent: better ships, faster yards, a larger economy. "
        "Weigh it against the workshop, not against nothing.",
        "- Your army does not recover on its own. Strength lost to fighting stays lost "
        "until you pay to make it good, and a power that never does is one the arithmetic "
        "beats however well it fights. There are two ways and they are not the same: set "
        "`reinforce` to true to bring the formations you already have back up to strength, "
        "which is much the cheaper of the two and is how a losing war is held; set "
        "`raise_formations` to add new formations, naming a kind from the forces menu "
        "above, which is how an army grows. Both come out of the same treasury as your "
        "building, so weigh them against it rather than always choosing what you chose "
        "last turn.",
    ]
    lines += [
        # Both halves are load-bearing. Writing a row that is not yours is refused, and
        # so is writing your own row under the wrong name -- and the second failure looks
        # exactly like the first in the rejection message.
        f"- Address rows by the ids above, never by name. You are `{nation_id}`.",
        f"- You may change your own nation row (`{nation_id}`), a relation you are part "
        f"of (written `{nation_id}|<other>`), and commitments you are a party to. "
        "Anything else belongs to another nation and will be refused.",
        *(f"- {rule}" for rule in
          _defensive_pact_lines(conn, world_id, viewer=nation_id)),
        *(f"- {rule}" for rule in RELATION_WRITE_RULES),
        *(f"- {rule}" for rule in economy_write_rules(days)),
    ]
    # The other channel this nation has, named here rather than only in its own
    # briefing: a staff that does not know an uncertain attempt is available spends
    # every turn on the certain ones. Stated with its caps for the reason decision 50
    # records -- an undisclosed bound is a bound that costs a whole proposal.
    lines += [
        "",
        "## Undertakings you could attempt instead",
        "Separately from the diffs above, you may be asked to attempt something whose "
        "outcome is not certain. It is rolled for, it costs whether or not it works, "
        "and it can fail.",
        # The sequencing, which decision 93 reversed: the undertaking is decided before
        # the build orders, so the treasury quoted here is already net of it. Stated
        # anyway, because a staff that does not know the other channel exists spends every
        # turn on the certain one.
        *_gambit_purse_lines(float(self_block.get("treasury") or 0.0), span_of(days)),
        *(f"- {rule}" for rule in gambit_rules_block(nation_id, span_of(days))),
        "",
        "## The air",
        # Decision 11/50 again, and the case for it is starker here than anywhere: the
        # air layer silently changed what every staff's aircraft are for. A model told
        # nothing would keep massing air wings on a front expecting them to count as
        # infantry, which is precisely what they no longer do.
        air_rules_block(conn, world_id, nation_id),
    ]

    # **The deterrent balance belongs in the strategic briefing, not only in the gambit
    # prompt.** It reached a leader exactly when that leader was already contemplating a
    # strike -- so a nuclear power reasoned about deterrence at the moment of using it and
    # never while deciding whether to invade, escalate, sign or back down, which is where
    # deterrence actually does its work. Empty for the great majority of nations, which
    # have no arsenal; an unusable lever described at length teaches nothing.
    balance = nuclear_balance_lines(conn, world_id, nation_id)
    if balance:
        lines += ["", "## The nuclear balance"] + [f"- {line}" for line in balance]

    # Who you are paying for, and who is paying for you. Empty for a nation doing neither,
    # which is most of them.
    backing = support_lines(conn, world_id, nation_id)
    if backing:
        lines += ["", "## Proxies"] + [f"- {line}" for line in backing]

    # An empire that cannot see its colonies stirring is an empire that will be surprised
    # by something that was visible for a decade. Empty for a power with no dependencies.
    colonies = colonial_lines(conn, world_id, nation_id)
    if colonies:
        lines += ["", "## Your territories"] + [f"- {line}" for line in colonies]

    return RenderedView(
        role=Role.TRACKED_AGENT,
        text=render("\n".join(lines), nation_id),
        viewer=nation_id,
        visible_event_ids=citable,
    )


# --------------------------------------------------------------------------- advisor

#: What the player may actually order, in the advisor's own words. Kept deliberately in
#: prose rather than as schema names: the advisor talks to a person, and "move forces"
#: is what a minister says. ``test_advisor`` asserts this stays in step with the
#: parser's action enum, so the council can never recommend something unorderable.
#: What the council may recommend. **Generated from the capability manifest**, which is
#: also what the parser's enum comes from -- so a council can no longer suggest something
#: the parser would refuse, and neither list can drift from the mechanics that exist.
ADVISABLE_ACTIONS: tuple[tuple[str, str], ...] = advisable()


def _policy_block(conn, world_id: str, nation_id: str) -> list[str]:
    """What this government is standing policy is, for a prompt or a briefing.

    Empty when the player has adopted none, which is the honest default: a council told
    "you have no policy" would treat that as a policy, and most games begin without one.

    **Advice only.** The directives are the player's own note to themselves, kept beside the
    save rather than in it, and nothing downstream may act on them -- see
    `engine.directives`, which says so at greater length and means it.
    """
    from .directives import as_lines

    lines = as_lines(conn, world_id)
    if not lines:
        return []
    return [
        "",
        "## What this government is trying to do",
        "Standing policy, set by the head of government. It is not an order and does not "
        "bind you; it is what to weigh an answer against.",
        *(f"- {line}" for line in lines),
    ]


def _air_rules_lines(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> list[str]:
    """The player-facing air rules, as briefing lines. Read-only, and generated."""
    from .airsituation import air_rules

    lines = ["## How the air war works",
             "These are the engine's own rules with its own numbers. Answer from them "
             "rather than from what an air force usually does, and say the figure when it "
             "is the point."]
    for heading, said in air_rules(conn, world_id, nation_id):
        lines.append(f"**{heading}**")
        _bullet(lines, said)
    lines.append("")
    return lines


def build_advisor_view(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    nation_id: str,
    question: str,
    conversation: list[tuple[str, str]] | None = None,
    digest: str = "",
) -> RenderedView:
    """The player's own council, briefed on exactly what the player can see.

    Built from the *player's* visibility rather than the referee's. An advisor that read
    the world-sim view would be an oracle: it would know rivals' exact strength and their
    secret intent, and every conversation with it would launder information the player is
    not supposed to have. It is briefed as a minister is -- well, but not omnisciently.

    Distinct from a tracked nation's call in what it is *for*. A per-country agent acts
    on its own account and proposes diffs; this role proposes nothing, gets no schema and
    no writable column, and answers in prose to the person playing.
    """
    summary = referee.world_summary(conn, world_id)
    self_block = visibility.nation_self(conn, nation_id)
    others = visibility.other_nations(conn, nation_id)
    regions = visibility.own_regions(conn, nation_id)
    commitments = visibility.standing_commitments(conn, nation_id)
    events, routine_works, citable = _briefing_events(conn, nation_id, limit=30)

    lines = [
        f"# You advise the government of {self_block.get('name', nation_id)}",
        "You are its senior minister and closest counsellor. You are speaking privately "
        "with the head of government, who is deciding what to do next.",
        "",
        self_block.get("character", ""),
        "",
        f"The date is {summary.get('date')} (turn {summary.get('turn')}).",
        "",
        "## What your government wants",
    ]
    _bullet(lines, list(self_block.get("standing_goals") or ["survive and prosper"]))
    # The player's own standing policy, if they have set one. Distinct from the block
    # above: those are the *nation's* goals as the scenario wrote them, this is what the
    # person playing has decided to do about them.
    # **Doctrine before policy, and they are different things.** What the country is
    # *built* to do constrains what its government can sensibly want -- a council advised to
    # avoid a continental war reads that differently with a professional corps than with
    # five million conscripts. Policy is the player's; the doctrine is the country's.
    lines += _doctrine_block(conn, world_id, nation_id)
    lines += _policy_block(conn, world_id, nation_id)
    lines += [
        "",
        "## Your position",
        f"- military {self_block.get('military_strength')}, economy {self_block.get('economy')}",
        f"- stability {self_block.get('stability')}, legitimacy {self_block.get('legitimacy')}",
        f"- treasury {self_block.get('treasury')}, manpower {self_block.get('manpower')}",
        f"- {_budget_line(conn, world_id, nation_id)}",
        f"- you hold {len(regions)} region(s)",
        "",
    ]
    # The same trade block the tracked powers get. **The player is a government too**, and
    # the person actually paying the oil bill is the one who most needs to be told why it
    # moved and what could be done about it -- an advisor that could not answer "why did
    # the oil get expensive" is an advisor with a hole in it exactly where this branch put
    # the answer. Empty with `atlas.engine.pricing` down.
    # `player=True`: this copy is read by a person, and the repricing forecast is phrased
    # as a review rather than as a promise that the terms come back. See
    # `consent.headroom_lines`.
    lines += consent.briefing_lines(conn, world_id, nation_id, player=True)
    # The same block the tracked powers get, for the same reason the trade one is here: the
    # player is a government too, and an advisor that could not say "we could stop selling
    # them oil, and it would cost us this much" is an advisor with a hole in it exactly
    # where the lever is.
    lines += embargo.briefing_lines(conn, world_id, nation_id)
    lines += [
        "## The other powers, as your ministry reads them",
    ]
    relevant = _powers_worth_naming(others)
    _bullet(
        lines,
        [
            f"{n.name}: regard {n.relation_score:+.0f}, {_strength_clause(n.military_estimate)}"
            + (", at war with you" if n.at_war else "")
            + (f", {n.treaty_status}" if n.treaty_status != "none" else "")
            + (", shares a border with you" if n.is_neighbour else "")
            for n in relevant
        ],
    )
    lines.append("")

    lines += _war_progress_lines(conn, world_id, nation_id)
    lines += _occupation_lines(conn, world_id, nation_id)

    # A war you have already won on the ground but not at the table. If you hold enemy
    # provinces and are still at war over them, nothing ends that war on its own -- you can
    # sue for peace and keep what you hold. Player-facing and actionable: `sue_for_peace` is
    # an order the government can give, and the terms are the ground already occupied.
    settleable = [w for w in settleable_wars(conn, world_id) if w["occupier_id"] == nation_id]
    if settleable:
        lines.append("## Wars you could end on your terms")
        lines.append(
            "You hold enemy ground the fighting already took. A war does not end on its "
            "own -- to keep what you hold, you must settle it: sue for peace and annex the "
            "occupied provinces at the table."
        )
        for war in settleable:
            provinces = ", ".join(p["name"] for p in war["provinces"])
            lines.append(
                f"- You occupy {war['victim_name']}'s {provinces} and remain at war. Suing "
                f"for peace now could make this ground yours for good."
            )
        lines.append("")

    if commitments:
        lines.append("## What you are already committed to")
        _bullet(
            lines,
            [f"{c['kind']} ({c['status']}): {c['condition']}" for c in commitments],
        )
        lines.append("")

    if events:
        lines.append("## What your ministry knows has happened")
        # **Gravest first, not newest first.** A coup and six completed workshops arriving
        # in the same turn printed in whatever order the engine wrote them, and the coup
        # could be anywhere in the list. The window is now chosen by weight too, so nothing
        # grave falls outside it -- this decides what a reader meets first. Turn numbers
        # are on every line, so the feed is still readable as a chronology.
        shown, routine = visibility.by_salience(events), routine_works
        _bullet(lines, [f"turn {e['turn']}: {sanitize(str(e['text']))}" for e in shown])
        lines += _construction_footnote(routine)
        lines.append("")

    if digest:
        lines += ["## Earlier history", digest, ""]

    lines += _buildable_lines(conn, world_id, nation_id)
    lines.append("")

    lines.append("## What your government can actually order this turn")
    _bullet(lines, [description for _key, description in ADVISABLE_ACTIONS])
    lines.append("")

    # **The air rules, because the council was the one place a player could ask.** The
    # tracked-agent view has carried `air_rules_block` since the layer shipped and this view
    # carried nothing, so a head of government asking their own minister how the air war
    # worked got an answer invented on the spot -- about a layer with five load-bearing
    # effects, none of which the player had been told. Generated from the same tables the
    # engine reads, through `airsituation.air_rules`, so the council and the reference entry
    # cannot come to say different things about the same constant.
    lines += _air_rules_lines(conn, world_id, nation_id)

    if conversation:
        lines.append("## This conversation so far")
        for speaker, message in conversation:
            lines.append(wrap_untrusted(message, source=speaker, kind="a question to you"))
            lines.append("")

    lines += [
        "## The question put to you",
        wrap_untrusted(question, source="your head of government", kind="a question to you"),
        "",
        "## How to advise",
        "Answer the question that was asked, briefly and concretely. Name the option you "
        "would take and say what it costs.",
        "Ground every claim in the position above. If the briefing does not tell you "
        "something, say that you do not know it rather than inventing it -- a minister "
        "who guesses at a rival's order of battle is worse than useless.",
        "Disagree when you disagree. You are not here to approve of the government's "
        "instincts, and telling it what it wants to hear is how it loses.",
        "You advise. You do not act: nothing you say changes the world, and only orders "
        "the government issues itself will do anything.",
    ]

    return RenderedView(
        role=Role.ADVISOR,
        text=render("\n".join(lines), nation_id),
        viewer=nation_id,
        visible_event_ids=citable,
    )


# --------------------------------------------------------------------------- parser


def build_parser_view(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    nation_id: str,
    raw_text: str,
) -> RenderedView:
    """The order-parsing view: the player's own assets and nothing else.

    A parser that could see the whole world would happily validate an order against a
    rival's units. It gets exactly what it needs to decide whether an order is executable.

    **Everything here is named, not just identified.** A view that lists ``r_bel`` and
    ``10x infantry`` and then asks whether the player holds "the western army" invites a
    refusal, because nothing in it connects the words a player writes to the rows the
    engine keeps. Every region carries its name, every border says whose side is whose,
    and the task spells out how forces are addressed. The parser's job is to decide
    whether an order is *executable*, not to fail it on vocabulary.
    """
    regions = visibility.own_regions(conn, nation_id)
    own_ids = {r["id"] for r in regions}
    nation = conn.execute(
        "SELECT name FROM nation WHERE world_id = ? AND id = ?",
        (world_id, nation_id),
    ).fetchone()
    nation_name = str(nation["name"]) if nation is not None else nation_id

    units = conn.execute(
        """
        SELECT u.region_id, r.name AS region_name, u.kind,
               COUNT(*) AS n, SUM(u.strength) AS strength, AVG(u.morale) AS morale
          FROM unit u JOIN region r ON r.id = u.region_id
         WHERE u.nation_id = ? GROUP BY u.region_id, u.kind
         ORDER BY r.name, u.kind
        """,
        (nation_id,),
    ).fetchall()

    adjacency = conn.execute(
        """
        SELECT a.region_a, a.region_b,
               ra.name AS name_a, rb.name AS name_b,
               na.name AS owner_a, nb.name AS owner_b
          FROM region_adjacency a
          JOIN region ra ON ra.id = a.region_a
          JOIN region rb ON rb.id = a.region_b
          LEFT JOIN nation na ON na.id = ra.owner_nation_id
          LEFT JOIN nation nb ON nb.id = rb.owner_nation_id
         WHERE ra.owner_nation_id = ? OR rb.owner_nation_id = ?
         ORDER BY a.region_a, a.region_b
        """,
        (nation_id, nation_id),
    ).fetchall()

    lines = [
        "# Convert an order into mechanical actions",
        "",
        "## The country issuing these orders",
        f"You are parsing orders for {nation_name} [{nation_id}].",
        f"When the player names {nation_name}, they mean their own country. It is never "
        "a foreign target and must not be rejected for being absent from the contact list.",
        "",
        "## Territory you hold",
    ]
    _bullet(lines, [f"{label(r['name'], r['id'])} -- {r['terrain']}" for r in regions])
    lines.append("")

    lines.append("## Forces you command")
    if units:
        _bullet(
            lines,
            [
                f"{u['n']}x {u['kind']} in {label(u['region_name'], u['region_id'])} "
                f"-- combined strength {u['strength']:g}, morale {u['morale']:.0f}"
                for u in units
            ],
        )
        lines.append("")
        lines.append(
            "Forces are addressed by where they are and what they are, not by name. "
            "A player writing \"the western army\", \"my divisions\" or \"the garrison\" "
            "means the formations listed above; match them by region and kind rather than "
            "looking for a unit with that title."
        )
    else:
        lines.append("- none")
    lines.append("")

    # **The menu the parser was never shown.** `build_project` covers public works *and*
    # research -- they share `production.build_project` -- but this view described the
    # `project` field as five civil ids and listed no technologies at all. A real-provider
    # run ordered "All-welded construction", the parser had no id to map it to, filed it as
    # `other`, and freeform then refused it as anachronistic. The same helper the player's
    # own briefing uses puts the real ids in front of the parser.
    lines.append("## What an order may become")
    _bullet(lines, prompt_menu(conn, world_id, nation_id))
    lines.append("")

    lines.append("## What you can begin")
    lines += _buildable_lines(conn, world_id, nation_id)
    lines.append(
        "A `build_project` action's `project` field takes one of the ids in that menu -- "
        "public works and research programmes alike. An order to research, develop, "
        "investigate or adopt something is a `build_project` naming the programme's id, "
        "not an `other`."
    )
    lines.append("")

    lines.append("## Where those forces can go")
    _bullet(
        lines,
        [
            (
                f"{row['name_a']} [{row['region_a']}]"
                f"{' (yours)' if row['region_a'] in own_ids else f' ({row['owner_a'] or 'unclaimed'})'}"
                f"  <->  {row['name_b']} [{row['region_b']}]"
                f"{' (yours)' if row['region_b'] in own_ids else f' ({row['owner_b'] or 'unclaimed'})'}"
            )
            for row in adjacency
        ],
    )

    # **Reachable over water, which the adjacency list cannot say.** `naval.amphibious_reach`
    # already works out which far shores this nation could land on -- through a sea it can
    # cross, with transports to carry the troops -- and the resolver honours the route. The
    # parser was shown only land adjacency, so it rejected a valid amphibious invasion
    # before resolution ever saw it. Same function, so the two cannot disagree about what
    # is reachable.
    landings = amphibious_reach(conn, world_id, nation_id)
    if landings:
        lines.append("")
        lines.append(
            "These can also be reached **by sea**, and an order to invade or land on one "
            "is executable. The crossing is already accounted for; treat them as valid "
            "`to_region` destinations:"
        )
        named = conn.execute(
            "SELECT id, name FROM region WHERE world_id = ?", (world_id,)
        ).fetchall()
        region_names = {str(r["id"]): str(r["name"]) for r in named}
        _bullet(
            lines,
            [
                f"{label(region_names.get(target, target), target)} -- across "
                f"{label(region_names.get(sea, sea), sea)}"
                for target, sea in sorted(landings.items())
            ],
        )
    lines.append("")

    lines.append("## Nations you can contact")
    _bullet(lines, _contactable_names(conn, world_id, nation_id))
    lines.append("")

    lines.append("## The order")
    lines.append(wrap_untrusted(raw_text, source="the player", kind="an order"))
    lines += [
        "",
        "## Your task",
        "Translate the order into actions. Validate it: does this nation hold forces that "
        "could carry it out, is the destination one of the regions listed above, is the "
        "target a nation you can contact? Reject what is not executable and say plainly "
        "why, in one sentence a player can act on.",
        f"Domestic wording may explicitly name {nation_name}: for example, \"invest in "
        f"{nation_name}'s economy\" is an economic_investment by the player, not an "
        "attempt to contact a foreign nation.",
        "",
        "The map is national: one region per country, and a country's internal places are "
        "not separately represented. An order naming a province, city, border or frontier "
        "-- the Rhineland, the Sudetenland, Alsace, the eastern border -- is an order about "
        "the region that contains it. Resolve it to that region and attempt it. Reject such "
        "an order only if the containing region is not yours and not reachable.",
        "",
        "Massing forces *on* or *at* a border is a movement within your own territory, not "
        "an entry into your neighbour's. Only an order that plainly says to enter, cross, "
        "invade, occupy or attack another nation's region is a move into it. When the "
        "wording could be read either way, take the reading that stays inside your own "
        "borders -- the player can always order the crossing explicitly, and cannot undo an "
        "invasion they did not mean to launch.",
        "",
        "Be generous about wording and strict about capability. A player writing plain "
        "prose is not required to know your identifiers. Reject an order because the "
        "forces, the reach or the contact are genuinely absent -- never because the words "
        "did not match a label.",
        "Do not invent capabilities, and do not decide outcomes -- only whether the order "
        "can be attempted.",
    ]

    return RenderedView(role=Role.PARSER, text="\n".join(lines), viewer=nation_id)


def _contactable_names(
    conn: sqlite3.Connection, world_id: str, nation_id: str, limit: int = 40
) -> list[str]:
    """Who is worth naming, rather than every nation on the map.

    A world seeded from real geometry has 220 nations. Listing all of them alphabetically
    costs a large prompt to say almost nothing, and buries the handful that matter under
    Ashmore and Cartier Islands. Neighbours, great powers and anyone the player already
    has a relationship with are the ones an order is plausibly about.
    """
    rows = conn.execute(
        """
        SELECT n.name,
               MAX(CASE WHEN a.region_a IS NOT NULL THEN 1 ELSE 0 END) AS neighbour,
               COALESCE(MAX(ABS(r.score)), 0) AS involvement,
               n.military_strength
          FROM nation n
          LEFT JOIN relation r
                 ON r.nation_a = ? AND r.nation_b = n.id
          LEFT JOIN region mine ON mine.owner_nation_id = ?
          LEFT JOIN region theirs ON theirs.owner_nation_id = n.id
          LEFT JOIN region_adjacency a
                 ON (a.region_a = mine.id AND a.region_b = theirs.id)
                 OR (a.region_b = mine.id AND a.region_a = theirs.id)
         WHERE n.world_id = ? AND n.id <> ? AND n.is_alive = 1
         GROUP BY n.id
         ORDER BY neighbour DESC, involvement DESC, n.military_strength DESC, n.name
         LIMIT ?
        """,
        (nation_id, nation_id, world_id, nation_id, limit),
    ).fetchall()
    names = [row["name"] for row in rows]
    if len(names) == limit:
        names.append("...and any other nation on the map, by name")
    return names
