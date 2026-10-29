"""2027 -- Fire in the Gulf. **An alternate history**: the Gulf war that the region has spent
forty years expecting.

Built from :mod:`atlas.scenarios.modern_day` the way :mod:`atlas.scenarios.hot_cold_war` is
built from the Cold War: the same powers, armies, fleets, arsenals, leaders and treaties, and
a small declared divergence that is entirely about *decisions*. Nobody's order of battle is
inflated to make the war more interesting.

**What diverges**

1. **Iran has gone to war with Saudi Arabia, Kuwait and the Emirates**, after closing the
   Strait of Hormuz. Israel, already at daggers drawn with Tehran, is at war with it too. The
   2023 normalisation China brokered between Riyadh and Tehran is gone: Riyadh has declared
   it void, so it is not in force and there is nothing left to break.
2. **Iraq has sided with Tehran.** A militia-dominated government in Baghdad signed a mutual
   defence pact and is in the war on Iran's side. This is what makes the invasion possible
   at all: Iran has no land border with Kuwait or Saudi Arabia, and an army standing on an
   ally's ground is supplied where it stands (:func:`atlas.engine.logistics._depots`), so
   Iraq is the land bridge.
3. **The line is already inside Kuwait and the Saudi Eastern Province**, through
   `occupied`: Iran holds Kuwait and Ash Sharqiyah -- the oil coast, Dhahran, Abqaiq. Saudi
   Arabia still owns both and wants them back, so both sides have something to do on turn 1.
4. **The armies are in contact**, through `deployments`: Iranian divisions in Kuwait and the
   Eastern Province, and the Saudi army massing in Riyadh for the counterattack. The Saudi
   and Kuwaiti garrisons that were already there are still there, holding out behind the
   line -- which is what gives the counterattack somewhere to start.

**What is deliberately not decided.** The United States has carriers in the Gulf and a
treaty with nobody here; whether it fights is a decision. So is Turkey's, Pakistan's (which
has a defence relationship with Riyadh and a border with Iran), India's and China's (which
buys the oil both sides are burning). Every power not named above is exactly where Modern Day
left it.
"""

from __future__ import annotations

from dataclasses import replace

from ..state.models import CommitmentKind, EventKind, TreatyStatus, Visibility
from .modern_day import HISTORY as MODERN_HISTORY
from .modern_day import RELATIONS as MODERN_RELATIONS
from .modern_day import SCENARIO as MODERN_DAY
from .modern_day import TREATIES as MODERN_TREATIES

__all__ = ["BELLIGERENTS", "DEPLOYMENTS", "FRONT", "SCENARIO"]

#: Who is fighting whom, as unordered pairs, expanded into the directed rows the engine keeps.
BELLIGERENTS: tuple[tuple[str, str], ...] = (
    ("irn", "sau"), ("irn", "m_kwt"), ("irn", "m_are"),
    ("m_irq", "sau"), ("m_irq", "m_kwt"),
    ("irn", "isr"),
)

#: The line of contact on 1 March 2027: ``region -> who holds it``. Owner and homeland stay
#: Kuwaiti and Saudi -- an occupation, with something to hand back in a settlement.
FRONT: dict[str, str] = {
    "r_kwt": "irn",
    "r_sau_ash_sharqiyah": "irn",
}

#: ``region -> (power, divisions)``, on top of every army the era already has.
DEPLOYMENTS: dict[str, tuple[str, int]] = {
    "r_kwt": ("irn", 2),
    "r_sau_ash_sharqiyah": ("irn", 3),
    "r_sau_ar_riyad": ("sau", 3),
}

#: Pairs whose relation is rewritten with a treaty: Tehran and Baghdad are allies now.
_ALLIES: tuple[tuple[str, str], ...] = (("irn", "m_irq"),)


def _relations(relations):
    at_war = {pair for a, b in BELLIGERENTS for pair in ((a, b), (b, a))}
    allied = {pair for a, b in _ALLIES for pair in ((a, b), (b, a))}
    kept = tuple(row for row in relations if (row[0], row[1]) not in at_war | allied)
    return (kept
            + tuple((a, b, -95.0, TreatyStatus.NONE, True) for a, b in sorted(at_war))
            + tuple((a, b, 70.0, TreatyStatus.DEFENSIVE, False) for a, b in sorted(allied)))


RELATIONS = _relations(MODERN_RELATIONS)

#: The 2023 normalisation is void; Tehran and Baghdad have a pact.
TREATIES = tuple(
    treaty for treaty in MODERN_TREATIES if treaty[3] != ("sau", "irn")
) + (
    (CommitmentKind.DEFENSIVE_PACT, "active",
     "The Tehran-Baghdad mutual defence agreement of February 2027, signed by a government "
     "in Baghdad that owes its majority to militias Iran armed. Iranian forces may cross "
     "Iraq; Iraq is at war when Iran is.",
     ("irn", "m_irq")),
)

HISTORY: tuple[tuple[EventKind, str, str, Visibility], ...] = MODERN_HISTORY + (
    (EventKind.WAR_DECLARED, "irn",
     "On 27 February Iran declared the Strait of Hormuz closed to the shipping of 'states "
     "hosting the aggressor', mined the approaches, and sent armoured columns south through "
     "Iraq. By 1 March Iranian divisions hold Kuwait City and the Saudi Eastern Province -- "
     "Dhahran, Abqaiq and the oil coast. Tehran calls it a response to Israeli strikes "
     "launched from Gulf airspace; Riyadh calls it what it is. Kuwaiti and Saudi garrisons "
     "are still holding out inside the occupied ground.", Visibility.PUBLIC),
    (EventKind.WAR_DECLARED, "m_irq",
     "Baghdad has honoured its new pact with Tehran. Iraqi units are moving on the Saudi "
     "border, and the government that signed the pact has not survived a vote since.",
     Visibility.PUBLIC),
    (EventKind.CASUS_BELLI, "sau",
     "Saudi Arabia has declared the 2023 normalisation with Iran void and asked its friends "
     "for help. The army is massing at Riyadh for a counterattack on the Eastern Province, "
     "and the oil that funds it is behind the enemy's line.", Visibility.PUBLIC),
    (EventKind.WAR_DECLARED, "isr",
     "Israel has struck Iranian air defences and missile sites and says it will continue. It "
     "has no border with Iran and no army that can reach one; what it can do from the air is "
     "the question.", Visibility.PUBLIC),
    (EventKind.DIPLOMATIC_MESSAGE, "usa",
     "Two American carrier groups are in the Arabian Sea. The White House has condemned the "
     "invasion, called for the Strait to reopen, and has not asked Congress for anything.",
     Visibility.PUBLIC),
    (EventKind.DIPLOMATIC_MESSAGE, "chn",
     "Beijing, which brokered the 2023 normalisation and buys oil from both sides, has called "
     "for an immediate ceasefire and offered to host talks. Its tankers are anchored outside "
     "the Strait like everyone else's.", Visibility.PUBLIC),
)

SCENARIO = replace(
    MODERN_DAY,
    id="fire-in-the-gulf",
    name="2027 - Fire in the Gulf",
    start_date="2027-03-01",
    rng_seed=2027,
    default_world_id="gulf2027",
    relations=RELATIONS,
    treaties=TREATIES,
    history=HISTORY,
    occupied={**MODERN_DAY.occupied, **FRONT},
    deployments=DEPLOYMENTS,
)
