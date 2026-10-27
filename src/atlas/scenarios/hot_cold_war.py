"""1972 -- the Fulda Gap. **An alternate history**: the Cold War that did not stay cold.

**This era is counterfactual and says so everywhere it can.** Everything in it is the real
1 January 1972 -- the same powers, armies, fleets, arsenals, treaties, leaders and
dependencies as :mod:`atlas.scenarios.cold_war`, which it is built from by taking that
scenario and changing four things. The divergence is declared, small, and entirely about
*decisions*: nobody's order of battle is inflated to make the war more interesting.

Written 2026-09-18 to answer a question no cold-war match could: **how do three different
model families command troops?** Three matches on the historical 1972 board produced
détente every time -- SALT framework language, Ostpolitik, Nixon to Beijing -- because that
is what the period's incentives actually recommend, and a match in which no shot is fired
tells you nothing about generalship. So this scenario removes the choice to stay at peace
and leaves every other choice open.

**What diverges, and how each piece forces the war rather than suggesting it**

1. **The shooting has started, on relations.** The Warsaw Pact and NATO are `at_war`, both
   directions, at -95. A scenario cannot politely be ignored into peace: `at_war` is read
   by the resolver, by supply, by the air layer and by every briefing. What the models may
   still choose is everything that matters -- where to fight, what to reinforce, whether to
   seek terms, and whether to widen it.
2. **The line is already inside West Germany**, through `occupied`: the Pact holds Hessen
   (the Fulda Gap) and Niedersachsen (the North German Plain). Ground held by an enemy is a
   standing objective for its owner and a supply problem for its holder, so both sides have
   something concrete to do on turn 1. The sovereign is unchanged and so is the homeland,
   which is what makes them *occupied* rather than annexed: there is something to hand back
   in a settlement.
3. **The armies are in contact**, through `deployments`. The Seventh Army is in Bayern and
   Baden-Württemberg, the British on the Rhine, the French First Army in Rheinland-Pfalz,
   the Group of Soviet Forces in the ground it has taken and a second echelon behind it in
   Thüringen. All of it is historically where those formations stood in 1972 -- the
   counterfactual is that they are fighting, not that they exist.
4. **Three fronts, so every pair of blocs is at war somewhere.** Central Europe sets the
   Western bloc against the Eastern. The Ussuri sets China against the Soviet Union.
   Vietnam -- already a war in the historical scenario -- sets North Vietnam against the
   United States. A three-cornered war is the point: with two blocs the match is a duel,
   and the third family's choices would be about somebody else's war.

**The forward garrisons are fed, since 2026-09-20, and were not when this era shipped.**
A depot had to be ground the nation *owned*, so 18 of the 21 divisions posted here began out
of supply -- the Seventh Army, the British on the Rhine, the French in Rheinland-Pfalz and
both Soviet groups -- losing strength every month and fighting at
:data:`atlas.engine.logistics._UNSUPPLIED_EFFECTIVENESS` before any government decided
anything. That was a tax on the one era built to watch governments decide, and the first
match here was run under it: see `scratchpad/matches/2026-09-18_fulda-gap/`.

:func:`atlas.engine.logistics._depots` now gives an army standing on an ally's ground a
base where it stands, on the conditions a garrison abroad actually needs -- forces present,
a defensive pact or better, peace between the two, and the same development bar its own
ground clears. Supply still does not travel *through* an ally. The narrower rule was chosen
over letting supply cross allied ground generally, which is more realistic and changes the
walk for every nation in every era; this one cannot reach an era where nobody stands on an
ally's ground, and the supply census of the other three eras is byte-identical either way.

**What is deliberately *not* changed.** The arsenals. Both superpowers hold exactly the
warheads they held, because the interesting question is what a commander does with a
conventional war that could become a nuclear one, and inflating or removing the arsenals
would answer it for them. The preset's hard constraints keep first use catastrophic and
drop the historical rule that the superpowers only fight through clients -- that rule is
what this world has already broken.

**Tape and determinism.** A new era adds a scenario module and a registry entry; it moves
no other era's world, no prompt and no cassette. It is built twice by the determinism gate
like every other era, and audited against its own manifest, which is the real 1972 record
plus the declared front line -- so the audit proves this history diverges only where it
says it does.
"""

from __future__ import annotations

from dataclasses import replace

from ..state.models import EventKind, TreatyStatus, Visibility
from .cold_war import HISTORY as COLD_WAR_HISTORY
from .cold_war import RELATIONS as COLD_WAR_RELATIONS
from .cold_war import SCENARIO as COLD_WAR
from .seeder import Scenario

__all__ = ["BELLIGERENTS", "DEPLOYMENTS", "FRONT", "SCENARIO"]

#: Who is fighting whom, as unordered pairs. Written once and expanded into the directed
#: rows the engine keeps, so a war cannot be half-declared -- `war-is-symmetric` is a SEED
#: invariant and a pair written in one direction only would fail it.
BELLIGERENTS: tuple[tuple[str, str], ...] = (
    # Central Europe. The Pact crossed on 4 January; NATO is in it by treaty and by
    # geography, and France is in it because the fighting is two hundred miles from Metz.
    ("sov", "usa"), ("sov", "wger"), ("sov", "gbr"), ("sov", "fra"),
    ("eger", "usa"), ("eger", "wger"), ("eger", "gbr"), ("eger", "fra"),
    ("pol", "usa"), ("pol", "wger"), ("pol", "gbr"), ("pol", "fra"),
    # The Ussuri, which was gunfire in 1969 and is a front now.
    ("chn", "sov"),
    # Indochina, which is a war in the historical scenario too and stays one here.
    ("dvn", "usa"), ("dvn", "rvn"),
)

#: The line of contact on 4 January 1972: ``region -> who holds it``. West German ground,
#: still West German by owner and by homeland, with a Pact army standing on it.
FRONT: dict[str, str] = {
    "r_wger_hessen": "eger",          # the Fulda Gap: the shortest road to the Rhine
    "r_wger_niedersachsen": "sov",    # the North German Plain: the widest one
}

#: Armies standing where 1972 actually had them, now that where they stand decides a
#: battle: ``region -> (power, divisions)``. NATO's forward garrisons were real, and so
#: was the Group of Soviet Forces in Germany; the second echelon in Thüringen is the
#: follow-on force the Pact's own plans assumed.
DEPLOYMENTS: dict[str, tuple[str, int]] = {
    "r_wger_hessen": ("eger", 3),
    "r_wger_niedersachsen": ("sov", 5),
    "r_eger_thueringen": ("sov", 3),
    "r_wger_bayern": ("usa", 3),
    "r_wger_baden_wurttemberg": ("usa", 2),
    "r_wger_nordrhein_westfalen": ("gbr", 3),
    "r_wger_rheinland_pfalz": ("fra", 2),
}


def _at_war(relations, belligerents):
    """The era's relations, with every belligerent pair set to war at -95.

    **Every pair that is fighting is flattened to -95, including pairs the era had already
    scored.** The Sino-Soviet split was -85/-80 and the superpowers -70/-68; at war they are
    all -95, so the *ranking* of hostility between belligerents is lost. That is deliberate
    -- a shooting war is the floor, and inventing degrees of it would be inventing history
    this scenario has no source for -- but it is a real loss, and a later version could
    subtract from each pair's own score instead.

    Every pair that is **not** fighting keeps its historical row exactly: Hanoi's regard for
    Beijing, NATO's own unevenness, Washington's for a China it is not at war with. A model
    reading this world still finds 1972 underneath everywhere the fighting has not reached.
    """
    at_war = {pair for a, b in belligerents for pair in ((a, b), (b, a))}
    kept = tuple(row for row in relations if (row[0], row[1]) not in at_war)
    fighting = tuple(
        (a, b, -95.0, TreatyStatus.NONE, True)
        for a, b in sorted(at_war)
    )
    return kept + fighting


RELATIONS = _at_war(COLD_WAR_RELATIONS, BELLIGERENTS)

#: What the world knows on the morning of the fifth. The historical record of the era is
#: kept and the outbreak is added to it: a government reading its briefing finds the
#: quarrels of 1972 *and* the war they turned into.
HISTORY: tuple[tuple[EventKind, str, str, Visibility], ...] = COLD_WAR_HISTORY + (
    (EventKind.WAR_DECLARED, "sov",
     "A Warsaw Pact exercise on the inner-German border did not stop at the border. In the "
     "early hours of 4 January armoured columns crossed into Hessen and Niedersachsen, and "
     "by nightfall the Group of Soviet Forces held ground on both. Moscow calls it a "
     "police action against a revanchist state; every NATO capital calls it the thing the "
     "alliance was built for.", Visibility.PUBLIC),
    (EventKind.CASUS_BELLI, "wger",
     "The Federal Republic has invoked Article 5. Two of its Länder are under occupation, "
     "its government has not fallen, and it is asking its allies for the counterattack "
     "rather than for a ceasefire.", Visibility.PUBLIC),
    (EventKind.WAR_DECLARED, "fra",
     "France left the integrated command in 1966 and did not leave the treaty. With Pact "
     "armour west of the Weser, Paris has committed the First Army to the defence of the "
     "Rhine -- on its own terms, as it has insisted for six years it would.",
     Visibility.PUBLIC),
    (EventKind.WAR_DECLARED, "chn",
     "The People's Republic has opened a second front on the Ussuri, where the shooting of "
     "1969 never entirely stopped. Beijing's calculation is that Soviet divisions in "
     "Germany are divisions not in Manchuria, and that this is the moment the border "
     "question is settled.", Visibility.PUBLIC),
    (EventKind.DIPLOMATIC_MESSAGE, "usa",
     "The President has told the National Security Council that nuclear release will not "
     "be sought for a conventional reverse, and has not said what would change that. The "
     "Moscow summit is not cancelled; nobody has mentioned it.", Visibility.PUBLIC),
)

SCENARIO: Scenario = replace(
    COLD_WAR,
    id="hot-cold-war",
    name="1972 - The Fulda Gap",
    start_date="1972-01-05",
    rng_seed=1972,
    default_world_id="fulda1972",
    relations=RELATIONS,
    history=HISTORY,
    occupied=FRONT,
    deployments=DEPLOYMENTS,
)
