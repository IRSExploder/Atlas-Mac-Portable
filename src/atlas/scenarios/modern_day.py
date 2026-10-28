"""Modern Day. The political state of the world on 1 January 2026.

**Sixteen powers, everything else a minor.** The named powers are the states whose
decisions move other states' decisions: the two that can project force anywhere, the
regional powers who set the terms in their own neighbourhood, and the four flashpoint
pairs the era is actually about. Everything else on the map is a minor with a border
guard, exactly as in 1936 -- including Taiwan, which is a nation on this map because it
functions as one, and whose status is the argument rather than the answer.

**Ukraine is a power and the war is on.** The plan called for fourteen; this is sixteen,
and the extra two are Ukraine and Germany. A 2026 that has to pretend the largest land war
in Europe since 1945 is not happening is not the present, and the alternative -- Ukraine as
a one-division generic minor -- would have Russia through it in a single contest, which
teaches nothing. Ukraine's figures are those of a state with a small economy, a very large
army and no navy left. See the decision log.

**The scale is compressed on purpose, and the compression is stated.** Real 2026 GDP spans
three orders of magnitude between the United States and North Korea; reproducing that
faithfully would leave twelve of the sixteen unable to build anything, which is a worse lie
than the compression. Ordering is right, ratios are squeezed.

``economy`` is what a state can *mobilise*, not its nominal GDP -- closer to purchasing
power than to dollars, because what the number does in this game is pay for divisions and
fill shipyards. That is why Russia is not a tenth of the United States here and North
Korea is not a two-hundredth: a command economy directing most of its output at the army
converts a small GDP into more military capacity than the exchange rate suggests.

**The figures were calibrated against the budget, twice, exactly as 1936 was.** The first
pass looked right on paper and put every one of the sixteen into structural deficit --
North Korea bankrupt on turn one, Russia on turn six -- because modern hulls cost 0.6 a
turn each and a country-level power draws revenue from one region. Fleets came down to
about a tenth of real escort and submarine numbers (carriers stayed near-real: there are
few enough that each one changes what a power can attempt) and the economies moved to the
mobilisation reading above. Every power is now solvent at peace except Pakistan, which
runs -0.4 a turn and has about thirty-seven turns of reserves -- the same escapable
structural deficit 1936 gives Japan, and for the same reason: it is true about the country.

**Nuclear weapons are state, and still not a unit.** Nine of these powers are armed and
none of them has a warhead in the order of battle: an arsenal is a row in ``arsenal``, not
a formation standing in a province, because a warhead is not something an army can be
ordered to march. What that row buys is a deterrent -- their homelands are measurably
harder to take, before anybody launches -- and a `nuclear_launch` a staff can propose and
almost never should. See :mod:`atlas.engine.nuclear` and the decision log. The preset's
hard constraints still say what they said; they now sit on top of a mechanism rather than
in place of one.

**The history is engine-authored, as it must be.** Every diff must cite a cause, so a world
that began with no past would have nothing legal to point at. Crimea, the 2022 invasion,
AUKUS, the Taiwan question and the rest give the causal graph a real root -- written with
``origin='ENGINE'``, so a nation cannot forge a grievance and cite it next turn.

**This module is data.** The machinery lives in :mod:`atlas.scenarios.seeder`.
"""

from __future__ import annotations

from ..engine.goals import Goal
from ..engine.production import BUILD_DAYS
from ..engine.research import TechEffect, Technology, upgrade_programmes
from ..map.pack import GeometryPack, PackSet
from ..state.bootstrap import UnitTemplateSeed, WorldSeed
from ..state.models import CommitmentKind, Difficulty, EventKind, TreatyStatus, Visibility
from .era import Era
from .seeder import Power, Scenario, ShipClass, apply_politics, build_world

__all__ = [
    "ABSENT",
    "OCCUPIED",
    "HISTORY",
    "POWERS",
    "RELATIONS",
    "DETERRENT_BOATS",
    "SCENARIO",
    "SOVEREIGNTY",
    "TECHNOLOGIES",
    "ORGANISATIONS",
    "TREATIES",
    "apply_2026_politics",
    "modern_day_seed",
]


#: Figures are relative and deliberately coarse; what has to be right is the shape. The
#: United States has the only globally deployable force and an economy nothing else
#: approaches. China is close behind on both and ahead on hulls. Russia is a serious army
#: on a small economy. Germany and Japan are large economies that chose not to be military
#: powers and are re-examining that. Ukraine, Israel, the Koreas and Pakistan are armies
#: out of all proportion to what pays for them, which is what living on a fault line does.
POWERS: tuple[Power, ...] = (
    Power(
        id="usa", name="United States", colour="#3B6EA5",
        regions=("USA",), capital="USA",
        character=(
            "Still the only power that can put force anywhere on earth, and increasingly "
            "unsure it wants to. Its alliances are older than most of the people staffing "
            "them, and its partners have begun quietly pricing in its absence."
        ),
        goals=(
            "keep any single power from dominating Europe or the western Pacific",
            "hold the alliance network together at a cost the public will tolerate",
            "deny China the technology that would close the gap",
            "avoid a war it would have to fight on two oceans at once",
        ),
        aims=(
            Goal("outweigh", subject="chn",
                 because="keep any single power from dominating"),
            Goal("stay_out_of_war",
                 because="avoid a war it would have to fight on two oceans at once"),
        ),
        military=95, economy=100, stability=46, legitimacy=60, treasury=220, manpower=130,
    ),
    Power(
        id="chn", name="China", colour="#B0453B",
        regions=("CHN",), capital="CHN",
        character=(
            "Convinced the current order was built without it and is maintained against "
            "it. Patient by preference and increasingly impatient about Taiwan, which it "
            "treats as an internal matter other states have no standing to discuss."
        ),
        goals=(
            "resolve the Taiwan question on its own terms, by preference without war",
            "break the technological containment before it bites",
            "make the first island chain untenable for the United States Navy",
            "keep the economy growing fast enough to justify the arrangement",
        ),
        aims=(
            Goal("outweigh", subject="usa",
                 because="be answerable to no other power's order"),
            Goal("economy_above", value=110.0,
                 because="keep the economy growing fast enough to justify the arrangement"),
        ),
        military=78, economy=92, stability=66, legitimacy=54, treasury=190, manpower=200,
    ),
    Power(
        id="rus", name="Russia", colour="#8C6E5A",
        regions=("RUS",), capital="RUS",
        character=(
            "Fighting a war it expected to win in a week and has not finished. Its economy "
            "is a fraction of the coalition arming its opponent and it has adapted to that "
            "better than anyone predicted. Reads NATO enlargement as the original offence."
        ),
        goals=(
            "hold what it has taken and be recognised as holding it",
            "outlast Western support for Kyiv rather than defeat it",
            "split the European members of NATO from Washington",
            "keep China a partner without becoming its client",
        ),
        aims=(
            Goal("keep_home",
                 because="hold what it has taken and be recognised as holding it"),
            Goal("hold_at_least", value=16.0,
                 because="hold what it has taken"),
        ),
        military=64, economy=45, stability=54, legitimacy=44, treasury=55, manpower=110,
    ),
    Power(
        id="ukr", name="Ukraine", colour="#C8A93B",
        regions=("UKR",), capital="UKR",
        character=(
            "Four years into an existential defence, with the most combat-experienced army "
            "in Europe and an economy that only functions because other people pay for it. "
            "Every calculation it makes runs through how long that will last."
        ),
        goals=(
            "hold the line and make holding it cost Russia more than it will pay",
            "keep Western money and munitions arriving",
            "turn association with Europe into membership of it",
            "recover occupied ground, by negotiation if not by force",
        ),
        aims=(
            Goal("keep_home", because="get its country back"),
            Goal("hold_at_least", value=9.0, because="hold the country it still has"),
        ),
        military=50, economy=14, stability=42, legitimacy=68, treasury=12, manpower=55,
    ),
    Power(
        id="ind", name="India", colour="#C98A3B",
        regions=("IND",), capital="IND",
        character=(
            "Courted by everyone and committed to no one, which it regards as the whole "
            "point. Buys Russian weapons, American technology and Gulf energy without "
            "accepting anybody's framing of what that obliges it to do."
        ),
        goals=(
            "become a pole of the system rather than a member of somebody's bloc",
            "keep China off the Himalayan frontier and out of the Indian Ocean",
            "grow the economy faster than the population",
            "manage Pakistan without another war",
        ),
        aims=(
            Goal("economy_above", value=70.0,
                 because="grow the economy faster than the population"),
            Goal("keep_home",
                 because="keep China off the Himalayan frontier"),
        ),
        military=56, economy=55, stability=56, legitimacy=64, treasury=75, manpower=220,
    ),
    Power(
        id="gbr", name="United Kingdom", colour="#7A4A6E",
        regions=("GBR",), capital="GBR",
        character=(
            "Punching at the edge of its weight and aware of it. Still one of the few "
            "states that will actually deploy, still spending on the instruments of a "
            "larger country, and still working out what it is for since leaving Europe."
        ),
        goals=(
            "stay the ally Washington calls first",
            "hold a naval and nuclear position the economy no longer obviously funds",
            "matter in European security without rejoining European institutions",
        ),
        military=43, economy=35, stability=52, legitimacy=60, treasury=70, manpower=45,
    ),
    Power(
        id="fra", name="France", colour="#4A6FA5",
        regions=("FRA",), capital="FRA",
        character=(
            "The only nuclear power inside the European Union and the loudest advocate of "
            "a Europe that does not need Washington. Its neighbours suspect the argument "
            "is partly about who would lead such a Europe, and they are not wrong."
        ),
        goals=(
            "build a European defence identity with France at the centre of it",
            "keep the Sahel and the Mediterranean from becoming somebody else's sphere",
            "hedge against an American withdrawal without provoking one",
        ),
        military=44, economy=34, stability=48, legitimacy=56, treasury=65, manpower=48,
    ),
    Power(
        id="deu", name="Germany", colour="#5F6B70",
        regions=("DEU",), capital="DEU",
        character=(
            "Europe's largest economy and its most reluctant military power, halfway "
            "through a rearmament it announced and has not finished. Thirty years of "
            "cheap Russian energy and export-led growth ended at once and it is still "
            "adjusting."
        ),
        goals=(
            "rebuild a Bundeswehr that can actually deploy",
            "keep the European economy from decoupling faster than it can adapt",
            "hold the EU together on Ukraine without leading on it",
        ),
        aims=(
            Goal("industry_above", value=30.0,
                 because="rebuild a Bundeswehr that can actually deploy"),
            Goal("stay_out_of_war",
                 because="hold the European economy together rather than fight for it"),
        ),
        military=26, economy=44, stability=58, legitimacy=66, treasury=95, manpower=60,
    ),
    Power(
        id="jpn", name="Japan", colour="#B07070",
        regions=("JPN",), capital="JPN",
        character=(
            "Rearming faster than at any time since 1945, having concluded that the "
            "American guarantee is real but no longer sufficient. Every capability it "
            "acquires is described as defensive and read otherwise in Beijing."
        ),
        goals=(
            "double defence spending without a constitutional crisis",
            "keep the United States committed to the western Pacific",
            "deter a move on Taiwan, which is four hundred kilometres from Okinawa",
        ),
        aims=(
            Goal("industry_above", value=28.0,
                 because="double defence spending without a constitutional crisis"),
            Goal("stay_out_of_war",
                 because="deter a move on Taiwan rather than answer one"),
        ),
        military=34, economy=42, stability=72, legitimacy=68, treasury=85, manpower=50,
    ),
    Power(
        id="kor", name="South Korea", colour="#4F8C8C",
        regions=("KOR",), capital="KOR",
        character=(
            "A first-rank industrial economy with a hostile state twenty-five miles from "
            "its capital and a growing arms export business. Public opinion has begun "
            "asking, out loud, why it does not have its own deterrent."
        ),
        goals=(
            "deter the North without triggering it",
            "keep the American alliance while hedging against its withdrawal",
            "sell weapons to everybody rearming, which is now everybody",
        ),
        military=48, economy=30, stability=54, legitimacy=62, treasury=55, manpower=45,
    ),
    Power(
        id="prk", name="North Korea", colour="#6E5A8C",
        regions=("PRK",), capital="PRK",
        character=(
            "Has the arsenal it spent forty years and its entire economy acquiring, and "
            "will not be talked out of it by anyone. Selling shells to Russia has bought "
            "it more leverage than a decade of negotiation ever did."
        ),
        goals=(
            "be accepted as a nuclear state in fact if not in law",
            "convert the Russian relationship into food, fuel and technology",
            "outlast sanctions, which it has already done once",
        ),
        military=34, economy=18, stability=56, legitimacy=28, treasury=6, manpower=60,
    ),
    Power(
        id="irn", name="Iran", colour="#5A8C5A",
        regions=("IRN",), capital="IRN",
        character=(
            "Its network of proxies has been degraded and its nuclear programme is closer "
            "to a weapon than it has ever been. Neither fact has changed a strategy built "
            "on the belief that the alternative to deterrence is what happened to Iraq."
        ),
        goals=(
            "hold the nuclear threshold as leverage without crossing it",
            "rebuild the regional network faster than it is being dismantled",
            "get the sanctions lifted without conceding the programme",
        ),
        military=36, economy=22, stability=42, legitimacy=30, treasury=18, manpower=70,
    ),
    Power(
        id="isr", name="Israel", colour="#6E8CB0",
        regions=("ISR",), capital="ISR",
        character=(
            "Militarily dominant in its neighbourhood and more diplomatically isolated "
            "than at any point in fifty years. Domestic politics and security policy have "
            "become the same argument, and neither is settled."
        ),
        goals=(
            "keep Iran from a weapon, by force if the alternative is worse",
            "hold the American relationship through a generational shift against it",
            "normalise with the Gulf without settling the Palestinian question",
        ),
        military=46, economy=18, stability=40, legitimacy=46, treasury=35, manpower=22,
    ),
    Power(
        id="sau", name="Saudi Arabia", colour="#A58C4A",
        regions=("SAU",), capital="SAU",
        character=(
            "Spending oil money on becoming something other than an oil economy, on a "
            "deadline set by the oil. Hedges openly between Washington and Beijing and "
            "has stopped pretending otherwise."
        ),
        goals=(
            "diversify before the revenue peaks",
            "extract an American security guarantee at an acceptable price",
            "keep Iran contained without fighting it directly",
        ),
        military=30, economy=24, stability=60, legitimacy=46, treasury=90, manpower=25,
    ),
    Power(
        id="tur", name="Turkey", colour="#B06A4A",
        regions=("TUR",), capital="TUR",
        character=(
            "In NATO, frequently against it, and indispensable to it -- it holds the "
            "straits and the second-largest army in the alliance. Sells drones to both "
            "sides of several arguments and mediates the rest."
        ),
        goals=(
            "be the power that other powers have to go through",
            "keep the straits as leverage and the Kurdish question suppressed",
            "buy from Russia and belong to NATO, indefinitely",
        ),
        military=42, economy=28, stability=50, legitimacy=48, treasury=30, manpower=70,
    ),
    # --- the four added when this era's roster was brought up to the Cold War's twenty.
    # Each is here because it decides something in 2026, not because it is large: Poland
    # is the front line of the war already on the map, Indonesia owns the straits every
    # Pacific plan runs through, Australia is a party to a treaty this scenario already
    # ships, and Brazil is the only power in its hemisphere that is not the United States.
    Power(
        id="pol", name="Poland", colour="#C4534F",
        regions=("POL",), capital="POL",
        character=(
            "Rearming faster than any other European state and saying plainly why. It has "
            "believed the Russian threat was real for longer than its allies have, and is "
            "spending accordingly -- with a president and a government from opposing camps "
            "who agree on the army and on very little else."
        ),
        goals=(
            "field the largest land army in Europe and be treated accordingly",
            "keep American forces on Polish soil whatever Washington decides elsewhere",
            "make sure Ukraine does not lose, without being drawn in directly",
            "turn defence spending into a domestic arms industry rather than a shopping list",
        ),
        military=38, economy=22, stability=58, legitimacy=56, treasury=40, manpower=35,
    ),
    Power(
        id="idn", name="Indonesia", colour="#C98A3E",
        regions=("IDN",), capital="IDN",
        character=(
            "The fourth most populous country on earth, sitting astride every route between "
            "the Indian and Pacific oceans, and determined to be courted by both sides "
            "without joining either. Non-alignment here is not indecision; it is the "
            "founding policy and it has paid."
        ),
        goals=(
            "be the pivot everyone bids for and nobody owns",
            "keep the straits open and under Indonesian control",
            "grow the economy fast enough to match the population's expectations",
            "keep the South China Sea dispute at the level of a dispute",
        ),
        military=28, economy=26, stability=58, legitimacy=56, treasury=55, manpower=150,
    ),
    Power(
        id="aus", name="Australia", colour="#4E8C72",
        regions=("AUS",), capital="AUS",
        character=(
            "A small population with a rich economy and a very large amount of ocean to "
            "watch. Its largest trading partner and the country its defence planning is "
            "about are the same country, which is the whole of its strategic problem."
        ),
        goals=(
            "get the submarines into the water before the window everyone is worried about",
            "keep the Americans engaged in the south-west Pacific",
            "sell to China without depending on it",
            "hold the island states' attention against a rival that is bidding for it",
        ),
        military=30, economy=26, stability=66, legitimacy=64, treasury=70, manpower=20,
    ),
    Power(
        id="bra", name="Brazil", colour="#5F9E5C",
        regions=("BRA",), capital="BRA",
        character=(
            "The one power in the western hemisphere that is not the United States and does "
            "not intend to behave as though it were. It talks to everyone, signs up to "
            "nothing binding, and treats the Amazon as a matter of sovereignty rather than "
            "of climate."
        ),
        goals=(
            "speak for the global south without being anybody's proxy",
            "keep South America free of outside military presence, including Washington's",
            "convert size into a permanent seat and the standing that comes with it",
            "trade with China and Washington on terms set in Brasilia",
        ),
        military=28, economy=30, stability=50, legitimacy=54, treasury=60, manpower=120,
    ),
    # --- the second tranche, seated on the same test: does it decide something in 2026.
    # Italy and Egypt were both powers in 1972 and the era gains nothing by disagreeing;
    # Vietnam is here because the South China Sea has two claimants that matter and only
    # one of them was on the board.
    Power(
        id="ita", name="Italy", colour="#6E9E6B",
        regions=("ITA",), capital="ITA",
        character=(
            "A G7 economy with a defence budget that has never matched it and a coastline "
            "that puts it first in the way of everything coming north. Governs the "
            "Mediterranean file for Europe by default, because the countries further from "
            "it would rather not."
        ),
        goals=(
            "keep the central Mediterranean routes manageable rather than solved",
            "hold a seat at the top table its spending does not quite buy",
            "keep Libya's factions from resolving the question by force",
            "stay useful to Washington without paying for it twice",
        ),
        military=32, economy=30, stability=52, legitimacy=54, treasury=65, manpower=55,
    ),
    Power(
        id="egy", name="Egypt", colour="#C2A24B",
        regions=("EGY",), capital="EGY",
        character=(
            "The largest army in the Arab world attached to an economy that cannot "
            "comfortably pay for it, sitting on the canal through which a tenth of world "
            "trade passes. Takes American money and Gulf money and declines to be owned "
            "by either."
        ),
        goals=(
            "keep the canal open and the revenue flowing",
            "stay indispensable to everyone with an interest in Gaza",
            "hold the Nile water question short of a war with Ethiopia",
            "service the debt without the subsidies that keep the cities quiet",
        ),
        military=34, economy=22, stability=48, legitimacy=42, treasury=25, manpower=110,
    ),
    Power(
        id="vnm", name="Vietnam", colour="#B5654A",
        regions=("VNM",), capital="VNM",
        character=(
            "Runs the most disciplined hedge on the map: a communist party that is "
            "courted by Washington, armed by Moscow, economically fused to Beijing and "
            "openly disputing the sea with it. Has fought all three within living memory "
            "and mentions it to none of them."
        ),
        goals=(
            "take the factories leaving China without becoming anyone's platform",
            "hold the features it occupies in the Spratlys without a shooting incident",
            "keep the four no's intact -- no alliances, no bases, no siding, no force",
            "grow fast enough that the arrangement at home keeps its argument",
        ),
        military=32, economy=18, stability=60, legitimacy=56, treasury=30, manpower=100,
    ),
    # --- and the island the era's largest quarrel is about.
    #
    # **Seating Taiwan is a statement about who decides, not about who is sovereign.** A
    # power here is a government the engine gives its own briefing to; it is not a seat at
    # the United Nations, and nothing about this entry says the dispute is settled. What it
    # says is that the twenty-three million people on the island have a government that
    # makes its own choices, which is a fact whatever anybody thinks of its status -- and
    # the alternative, leaving it as a minor, meant the flashpoint of the era was decided
    # entirely by Beijing and Washington with the island as terrain.
    #
    # `home_nation_id` on every Taiwanese province stays `twn` and the manifest pins it.
    # That guard is the whole reason this is safe: a claim still cannot become a home, so
    # `nuclear.deterrence` will never extend a Chinese guarantee over Taipei.
    Power(
        id="twn", name="Taiwan", colour="#7A9EC2",
        regions=("TWN",), capital="TWN",
        character=(
            "Makes the chips the rest of the world's economy is assembled from, and is "
            "claimed in its entirety by the state next door. Its politics are a live "
            "argument about how much to say out loud, conducted under the assumption that "
            "the answer changes nothing about the threat."
        ),
        goals=(
            "make an invasion look expensive enough that it is not attempted",
            "keep the semiconductor lead, which is the reason anyone would intervene",
            "stay unambiguous at home and deliberately ambiguous abroad",
            "hold the outlying islands without letting them start the war",
        ),
        aims=(
            Goal("keep_home", because="still be here, governing itself"),
            Goal("stay_out_of_war", because="not be the war everyone expects"),
        ),
        military=30, economy=28, stability=60, legitimacy=66, treasury=60, manpower=25,
    ),
    Power(
        id="pak", name="Pakistan", colour="#5A8C6E",
        regions=("PAK",), capital="PAK",
        character=(
            "A nuclear state with a chronic fiscal crisis, an army that outranks its "
            "government, and a border with India that has produced four wars. China pays "
            "for the infrastructure and expects the alignment that goes with it."
        ),
        goals=(
            "keep strategic parity with a country eight times its size",
            "stay solvent, which currently means staying useful to Beijing and the Gulf",
            "hold Afghanistan at arm's length without losing influence over it",
        ),
        military=41, economy=20, stability=36, legitimacy=38, treasury=15, manpower=95,
    ),
)


#: A modern armoured force is a fact about every one of these powers, not a distinction
#: the way it was in 1936. What varies is how many formations are worth counting.
_ARMOUR = {
    "usa": 4, "chn": 4, "rus": 3, "ind": 3, "ukr": 2, "prk": 2, "isr": 2, "tur": 2,
    "pak": 2, "kor": 2, "pol": 3, "irn": 1, "deu": 1, "fra": 1, "gbr": 1, "jpn": 1,
    "sau": 1, "bra": 1, "idn": 1, "aus": 1, "ita": 1, "vnm": 1, "twn": 1,
    # Conscript mass, and more tanks than any other Arab army.
    "egy": 2,
}

#: Air power is the modern era's decisive arm, so almost everyone has some and the
#: spread between the top and the bottom is what matters.
_AIR = {
    "usa": 5, "chn": 4, "rus": 3, "ind": 3, "isr": 2, "jpn": 2, "kor": 2, "fra": 2,
    "gbr": 2, "tur": 2, "sau": 2, "pol": 2, "aus": 2, "deu": 1, "irn": 1, "pak": 1,
    "prk": 1, "ukr": 1, "bra": 1, "idn": 1, "ita": 2, "egy": 1, "vnm": 1, "twn": 2,
}


#: What a fleet is in 2026. The battleship is gone; the carrier is the capital ship and
#: the destroyer is the workhorse that escorts it. The submarine is the genuinely new
#: entry -- the arm a weak naval power invests in precisely because it denies a sea it
#: could never control, which is Iran's whole maritime strategy and North Korea's.
#:
#: ``transport`` keeps the engine's name because :func:`atlas.engine.naval.sea_lift` looks
#: for it. Read it as an amphibious ready group: the thing that puts a brigade ashore.
SHIP_CLASSES: tuple[ShipClass, ...] = (
    ShipClass("destroyer", 9.0, 14.0),
    ShipClass("carrier", 11.0, 16.0),
    ShipClass("submarine", 8.5, 20.0),
    ShipClass("transport", 2.5),
)

#: Hulls in commission as ``(destroyers, carriers, submarines, amphibious)``.
#:
#: Escorts and submarines are roughly a tenth of real numbers; carriers are near-real.
#: Both halves of that are deliberate. A United States fielding ninety destroyers would be
#: most of the units on the map and would bankrupt itself paying their upkeep -- but there
#: are only about twenty aircraft carriers in the world, each one changes what its owner
#: can attempt, and rounding them off would erase the single clearest fact about the
#: distribution of naval power.
#:
#: Japan's two are the Izumos, converted; Turkey's one is Anadolu; Britain's are the two
#: Queen Elizabeths. Ukraine has no line at all -- the Black Sea Fleet it might have
#: contested is a matter for missiles and drones, not hulls it does not have.
FLEETS: dict[str, tuple[int, ...]] = {
    "usa": (8, 10, 6, 3),
    "chn": (6, 3, 6, 2),
    "jpn": (5, 2, 3, 2),
    "rus": (3, 1, 6, 2),
    "ind": (4, 2, 2, 1),
    "gbr": (3, 2, 3, 1),
    "fra": (3, 1, 3, 1),
    "kor": (3, 0, 3, 1),
    "tur": (2, 1, 2, 1),
    "deu": (2, 0, 2, 1),
    "irn": (1, 0, 3, 1),
    "pak": (2, 0, 2, 0),
    "prk": (1, 0, 3, 0),
    "sau": (2, 0, 0, 1),
    "isr": (1, 0, 2, 0),
    # An archipelago needs hulls whatever else it has; a continent with twenty-seven
    # million people needs them too, and buys quality instead of numbers.
    "idn": (3, 0, 2, 2),
    "aus": (3, 0, 2, 1),
    "bra": (2, 0, 1, 1),
    "pol": (1, 0, 1, 1),
    # Italy keeps a real navy with a real flight deck; Egypt's two amphibious ships are
    # read as lift rather than as a carrier; Vietnam's six Kilos are the whole point of
    # its fleet, which is a sea-denial force and not a sea-control one.
    #
    # **Egypt's lift is one group and Pakistan's is none**, corrected after the twenty-four
    # power roster left both underwater: Egypt at -0.76 a turn and Pakistan at -0.12, with
    # sea upkeep at 0.6 a hull the largest line in either budget. Egypt's two Mistrals are
    # *one* amphibious ready group, which is what this class means -- the docstring above
    # says so -- and counting them as two was charging a second establishment that does not
    # exist. Pakistan has no amphibious group at all; North Korea already had none on the
    # same economy, and the entry was simply wrong.
    "ita": (3, 1, 2, 1),
    "egy": (2, 0, 2, 1),
    "vnm": (1, 0, 2, 1),
    # Destroyers and frigates for the strait, and the first domestically built submarines.
    "twn": (2, 0, 2, 1),
}


#: Who is armed, as ``(warheads, delivery tier)``. Nine powers, which is the real number.
#:
#: **Counts are scaled the way ``economy`` is**, and for the same reason: what the figure
#: has to do in this game is decide how many strikes a power can mount and how much of its
#: arsenal survives being hit first, and five thousand warheads answers both questions the
#: same way forty does while making the arithmetic meaningless. Roughly one per fifty real
#: warheads, with the deployed-versus-stockpiled distinction folded in -- so Russia and the
#: United States are the top of the table by a wide margin, China is a distant third and
#: climbing, and North Korea is a real nuclear state with a small arsenal, which is exactly
#: the shape of the thing.
#:
#: **The tier is about surviving a first strike, not about airframes.** ``triad`` means
#: there is a leg that cannot be caught on the ground; ``missile`` means a land-based force
#: that can be dispersed but is findable; ``aircraft`` means it has to be got off the
#: ground first. **Britain and France are ``sea``**, the tier added at schema 19: Britain's
#: deterrent is submarine-only and France retired its land-based silos in 1996, so neither
#: is a triad and both said so in this comment while being seeded as one. Before that tier
#: existed the choice was between claiming two legs they do not have and claiming a
#: land-based force they do not have; now it is neither. Israel and Pakistan are listed as ``missile``
#: deliberately: their sea legs are real and undeclared, and a scenario should not resolve
#: an argument the world has not resolved.
#: **Which of a navy's boats are carrying the deterrent.**
#:
#: There is one ``submarine`` kind per era, so nothing on a unit distinguishes a
#: ballistic-missile boat from an attack boat -- and with this table absent, every hull
#: counted toward `nuclear.survivability`. That made an American attack submarine part of
#: the deterrent and, worse, made the deterrent proportional to the size of the navy
#: rather than to the number of boats that carry missiles.
#:
#: The figures are the SSBN share of each fleet rather than the real hull count, because
#: the fleets themselves are scaled: the United States has fourteen Ohios among sixty-odd
#: submarines, and this map gives it six boats in total. What has to be right is the
#: *proportion* -- a fifth of the American and Russian fleets, a tenth of the Chinese, and
#: close to half of the British and French, whose attack fleets are small and whose entire
#: deterrent is at sea.
#:
#: **Zero is a statement, not an omission.** Pakistan, Israel and North Korea are all
#: `missile` tier, where `SEA_LEG_TIERS` already means no hull counts -- so these three
#: entries change no arithmetic at all. They are here because the alternative is a table
#: that reads as though nobody had looked: Pakistan's sea leg is cruise missiles on
#: conventional boats, Israel's Dolphins are conventional, and North Korea has one
#: experimental hull that has never deterred anything.
DETERRENT_BOATS: dict[str, int] = {
    "usa": 2,   # of 6: fourteen Ohios among a very large attack fleet
    "rus": 2,   # of 6: roughly a dozen boomers, similarly outnumbered
    "chn": 1,   # of 6: six Jin-class, the smallest SSBN share of any triad
    "gbr": 2,   # of 3: four Vanguards against six attack boats, and the only leg there is
    "fra": 2,   # of 3: four Triomphants, the same shape as Britain
    "ind": 1,   # of 2: two Arihants, new and few
    "pak": 0,   # cruise missiles on conventional hulls is not a boomer
    "isr": 0,   # the Dolphins are conventional
    "prk": 0,   # one experimental boat
}


ARSENALS: dict[str, tuple[int, str]] = {
    "rus": (40, "triad"),
    "usa": (38, "triad"),
    "chn": (18, "triad"),
    "fra": (12, "sea"),
    "ind": (9, "triad"),
    "gbr": (8, "sea"),
    "pak": (8, "missile"),
    "isr": (5, "missile"),
    "prk": (3, "missile"),
}


#: International bodies: ``(id, name, kind, charter, members, permanent members)``.
#:
#: **One body, not two, and the choice is deliberate.** NATO is already modelled — as an
#: active ``defensive_pact`` commitment whose Article 5 the engine reads (decision 71),
#: which is the *automatic* half of what an alliance does. Seeding it a second time as a
#: convening organisation would put two mechanisms on one treaty: an attack on a member
#: would hand every other member a casus belli **and** convene a body whose strongest
#: action is to hand every member a casus belli. The same event, counted twice, with the
#: second pass adding nothing the first did not already do.
#:
#: So the Security Council is the flagship and the only one seeded. It is the body that
#: has something the pact does not: **a veto**, and jurisdiction over powers who are not
#: its friends. NATO's own deliberation is exactly the thing Article 5 already models.
#: The `kind='alliance'` arm of the schema stays, because it costs nothing and a scenario
#: with a bloc that has no automatic trigger will want it.
#:
#: The five permanent members are the real ones. Note what that buys immediately: Russia
#: can veto any resolution against itself, so the Council is structurally unable to act
#: against a permanent member — which is the single most important true thing about it,
#: and it falls out of the data rather than being coded.
ORGANISATIONS: tuple[
    tuple[str, str, str, str, tuple[str, ...], tuple[str, ...]], ...
] = (
    (
        "unsc", "the United Nations Security Council", "universal",
        "Charter of the United Nations. The Council bears primary responsibility for the "
        "maintenance of international peace and security. Its permanent members may veto "
        "any substantive resolution, including one against themselves.",
        ("usa", "chn", "rus", "gbr", "fra", "deu", "jpn", "ind", "kor", "tur",
         "sau", "isr", "irn", "pak", "ukr", "prk"),
        ("usa", "chn", "rus", "gbr", "fra"),
    ),
)


#: Re-priced for a modern treasury, and re-shaped for what modern war rewards. Air power
#: costs more than armour and is worth more; a carrier is the most expensive thing a state
#: can build and takes years; a submarine is the cheap way to make a sea dangerous.
#: Infantry is a mechanised brigade, not a 1916 division.
#:
#: Build times are in *days*, so they mean the same thing whether the player is jumping
#: a week or a year at a time.
UNIT_TEMPLATES: tuple[UnitTemplateSeed, ...] = (
    UnitTemplateSeed("infantry", domain="land", cost_treasury=9, cost_manpower=8,
                     base_strength=4.5, move_range=1, build_days=BUILD_DAYS["infantry"]),
    UnitTemplateSeed("armour", domain="land", cost_treasury=20, cost_manpower=5,
                     base_strength=7.5, move_range=2, build_days=BUILD_DAYS["armour"]),
    UnitTemplateSeed("air_wing", domain="air", cost_treasury=34, cost_manpower=3,
                     base_strength=7.5, move_range=3, build_days=90.0),
    UnitTemplateSeed("destroyer", domain="sea", cost_treasury=42, cost_manpower=3,
                     base_strength=9.0, move_range=3, build_days=120.0),
    UnitTemplateSeed("carrier", domain="sea", cost_treasury=130, cost_manpower=6,
                     base_strength=12.0, move_range=3, build_days=BUILD_DAYS["carrier"]),
    UnitTemplateSeed("submarine", domain="sea", cost_treasury=58, cost_manpower=2,
                     base_strength=8.5, move_range=3, build_days=180.0),
    UnitTemplateSeed("transport", domain="sea", cost_treasury=26, cost_manpower=2,
                     base_strength=2.5, move_range=3, build_days=BUILD_DAYS["transport"]),
)


#: Directed relations on 1 January 2026, as ``(from, to, score, treaty, at_war)``.
#: Directed because the feeling is rarely mutual: India's wariness of the United States is
#: not America's wariness of India, and Pyongyang's regard for Beijing is not returned at
#: the same temperature.
RELATIONS: tuple[tuple[str, str, float, TreatyStatus, bool], ...] = (
    # --- Taiwan, and the quarrel the era is largely about
    # **Not at war, and that matters.** The relationship is as bad as any on this map that
    # is not a shooting war, and the engine must not read it as one: an `at_war` flag here
    # would start the invasion at turn zero, which is the one thing the scenario is meant
    # to leave open.
    ("twn", "chn", -85, TreatyStatus.NONE, False),
    ("chn", "twn", -70, TreatyStatus.NONE, False),
    ("twn", "usa", 78, TreatyStatus.NONE, False),
    ("usa", "twn", 62, TreatyStatus.NONE, False),
    ("twn", "jpn", 72, TreatyStatus.TRADE, False),
    ("jpn", "twn", 65, TreatyStatus.TRADE, False),
    ("twn", "kor", 40, TreatyStatus.TRADE, False),
    ("kor", "twn", 35, TreatyStatus.TRADE, False),
    ("twn", "aus", 45, TreatyStatus.TRADE, False),
    ("aus", "twn", 40, TreatyStatus.TRADE, False),
    ("twn", "idn", 25, TreatyStatus.TRADE, False),
    ("idn", "twn", 20, TreatyStatus.TRADE, False),

    # --- the second tranche
    # Italy: a NATO member with a Mediterranean file nobody else wants.
    ("ita", "usa", 62, TreatyStatus.DEFENSIVE, False),
    ("usa", "ita", 58, TreatyStatus.DEFENSIVE, False),
    ("ita", "fra", 45, TreatyStatus.DEFENSIVE, False),
    ("fra", "ita", 42, TreatyStatus.DEFENSIVE, False),
    ("ita", "deu", 52, TreatyStatus.DEFENSIVE, False),
    ("deu", "ita", 50, TreatyStatus.DEFENSIVE, False),
    ("ita", "gbr", 50, TreatyStatus.DEFENSIVE, False),
    ("gbr", "ita", 48, TreatyStatus.DEFENSIVE, False),
    ("ita", "tur", 25, TreatyStatus.DEFENSIVE, False),
    ("tur", "ita", 22, TreatyStatus.DEFENSIVE, False),
    ("ita", "egy", 30, TreatyStatus.TRADE, False),
    ("egy", "ita", 32, TreatyStatus.TRADE, False),

    # Egypt: takes American money and Gulf money and is owned by neither.
    ("egy", "usa", 45, TreatyStatus.TRADE, False),
    ("usa", "egy", 48, TreatyStatus.TRADE, False),
    ("egy", "sau", 55, TreatyStatus.TRADE, False),
    ("sau", "egy", 58, TreatyStatus.TRADE, False),
    # Cold, correct, and forty-seven years old. A peace nobody enjoys and nobody breaks.
    ("egy", "isr", 10, TreatyStatus.NON_AGGRESSION, False),
    ("isr", "egy", 20, TreatyStatus.NON_AGGRESSION, False),
    ("egy", "tur", 15, TreatyStatus.NONE, False),
    ("tur", "egy", 18, TreatyStatus.NONE, False),
    ("egy", "chn", 35, TreatyStatus.TRADE, False),
    ("chn", "egy", 38, TreatyStatus.TRADE, False),

    # Vietnam: courted by Washington, armed by Moscow, fused to and disputing with Beijing.
    ("vnm", "chn", -25, TreatyStatus.TRADE, False),
    ("chn", "vnm", -10, TreatyStatus.TRADE, False),
    ("vnm", "usa", 45, TreatyStatus.TRADE, False),
    ("usa", "vnm", 50, TreatyStatus.TRADE, False),
    ("vnm", "rus", 50, TreatyStatus.TRADE, False),
    ("rus", "vnm", 45, TreatyStatus.TRADE, False),
    ("vnm", "jpn", 55, TreatyStatus.TRADE, False),
    ("jpn", "vnm", 52, TreatyStatus.TRADE, False),
    ("vnm", "ind", 50, TreatyStatus.TRADE, False),
    ("ind", "vnm", 48, TreatyStatus.TRADE, False),
    ("vnm", "idn", 35, TreatyStatus.TRADE, False),
    ("idn", "vnm", 33, TreatyStatus.TRADE, False),

    # --- the four powers this roster gained, and the quarrels they are actually in
    # Poland's are the sharpest on the map after the war itself.
    ("pol", "rus", -85, TreatyStatus.NONE, False),
    ("rus", "pol", -75, TreatyStatus.NONE, False),
    ("pol", "ukr", 72, TreatyStatus.NONE, False),
    ("ukr", "pol", 78, TreatyStatus.NONE, False),
    ("pol", "usa", 80, TreatyStatus.DEFENSIVE, False),
    ("usa", "pol", 70, TreatyStatus.DEFENSIVE, False),
    ("pol", "deu", 35, TreatyStatus.DEFENSIVE, False),
    ("deu", "pol", 45, TreatyStatus.DEFENSIVE, False),
    ("pol", "gbr", 60, TreatyStatus.DEFENSIVE, False),
    ("gbr", "pol", 58, TreatyStatus.DEFENSIVE, False),
    ("pol", "fra", 40, TreatyStatus.DEFENSIVE, False),
    ("fra", "pol", 42, TreatyStatus.DEFENSIVE, False),
    ("pol", "tur", 30, TreatyStatus.DEFENSIVE, False),
    ("tur", "pol", 28, TreatyStatus.DEFENSIVE, False),

    # Australia: the treaty partner and the trading partner are different countries.
    ("aus", "usa", 82, TreatyStatus.ALLIANCE, False),
    ("usa", "aus", 78, TreatyStatus.ALLIANCE, False),
    ("aus", "gbr", 74, TreatyStatus.ALLIANCE, False),
    ("gbr", "aus", 70, TreatyStatus.ALLIANCE, False),
    ("aus", "chn", -20, TreatyStatus.TRADE, False),
    ("chn", "aus", -15, TreatyStatus.TRADE, False),
    ("aus", "jpn", 62, TreatyStatus.TRADE, False),
    ("jpn", "aus", 60, TreatyStatus.TRADE, False),
    ("aus", "idn", 35, TreatyStatus.TRADE, False),
    ("idn", "aus", 30, TreatyStatus.TRADE, False),

    # Indonesia: courted by both, joined to neither.
    ("idn", "chn", 25, TreatyStatus.TRADE, False),
    ("chn", "idn", 35, TreatyStatus.TRADE, False),
    ("idn", "usa", 30, TreatyStatus.TRADE, False),
    ("usa", "idn", 35, TreatyStatus.TRADE, False),
    ("idn", "ind", 30, TreatyStatus.TRADE, False),
    ("ind", "idn", 28, TreatyStatus.TRADE, False),
    ("idn", "jpn", 40, TreatyStatus.TRADE, False),
    ("jpn", "idn", 38, TreatyStatus.TRADE, False),

    # Brazil: talks to everyone, signs nothing binding.
    ("bra", "usa", 30, TreatyStatus.TRADE, False),
    ("usa", "bra", 32, TreatyStatus.TRADE, False),
    ("bra", "chn", 45, TreatyStatus.TRADE, False),
    ("chn", "bra", 48, TreatyStatus.TRADE, False),
    ("bra", "ind", 50, TreatyStatus.TRADE, False),
    ("ind", "bra", 48, TreatyStatus.TRADE, False),
    ("bra", "rus", 25, TreatyStatus.NONE, False),
    ("rus", "bra", 30, TreatyStatus.NONE, False),

    # The war.
    ("rus", "ukr", -95, TreatyStatus.NONE, True),
    ("ukr", "rus", -100, TreatyStatus.NONE, True),
    ("ukr", "usa", 70, TreatyStatus.NONE, False),
    ("usa", "ukr", 55, TreatyStatus.NONE, False),
    ("ukr", "deu", 62, TreatyStatus.NONE, False),
    ("deu", "ukr", 58, TreatyStatus.NONE, False),
    ("ukr", "gbr", 74, TreatyStatus.NONE, False),
    ("gbr", "ukr", 68, TreatyStatus.NONE, False),
    ("ukr", "fra", 55, TreatyStatus.NONE, False),
    ("fra", "ukr", 50, TreatyStatus.NONE, False),
    ("ukr", "tur", 40, TreatyStatus.TRADE, False),
    ("tur", "ukr", 35, TreatyStatus.TRADE, False),

    # Washington and Moscow.
    ("usa", "rus", -78, TreatyStatus.NONE, False),
    ("rus", "usa", -80, TreatyStatus.NONE, False),
    ("deu", "rus", -70, TreatyStatus.NONE, False),
    ("rus", "deu", -60, TreatyStatus.NONE, False),
    ("gbr", "rus", -82, TreatyStatus.NONE, False),
    ("rus", "gbr", -80, TreatyStatus.NONE, False),
    ("fra", "rus", -62, TreatyStatus.NONE, False),
    ("rus", "fra", -55, TreatyStatus.NONE, False),

    # Washington and Beijing: rivals who cannot afford to stop trading.
    ("usa", "chn", -58, TreatyStatus.NONE, False),
    ("chn", "usa", -55, TreatyStatus.NONE, False),

    # The partnership without limits, and its actual limits.
    ("chn", "rus", 62, TreatyStatus.NON_AGGRESSION, False),
    ("rus", "chn", 70, TreatyStatus.NON_AGGRESSION, False),
    ("prk", "rus", 68, TreatyStatus.NON_AGGRESSION, False),
    ("rus", "prk", 55, TreatyStatus.NON_AGGRESSION, False),
    ("prk", "chn", 45, TreatyStatus.DEFENSIVE, False),
    ("chn", "prk", 38, TreatyStatus.DEFENSIVE, False),

    # The alliance network.
    ("usa", "gbr", 85, TreatyStatus.ALLIANCE, False),
    ("gbr", "usa", 88, TreatyStatus.ALLIANCE, False),
    ("usa", "fra", 62, TreatyStatus.ALLIANCE, False),
    ("fra", "usa", 48, TreatyStatus.ALLIANCE, False),
    ("usa", "deu", 68, TreatyStatus.ALLIANCE, False),
    ("deu", "usa", 62, TreatyStatus.ALLIANCE, False),
    ("usa", "jpn", 82, TreatyStatus.ALLIANCE, False),
    ("jpn", "usa", 84, TreatyStatus.ALLIANCE, False),
    ("usa", "kor", 76, TreatyStatus.ALLIANCE, False),
    ("kor", "usa", 72, TreatyStatus.ALLIANCE, False),
    ("usa", "tur", 22, TreatyStatus.ALLIANCE, False),
    ("tur", "usa", 8, TreatyStatus.ALLIANCE, False),
    ("gbr", "fra", 55, TreatyStatus.ALLIANCE, False),
    ("fra", "gbr", 52, TreatyStatus.ALLIANCE, False),
    ("deu", "fra", 72, TreatyStatus.ALLIANCE, False),
    ("fra", "deu", 70, TreatyStatus.ALLIANCE, False),
    ("deu", "gbr", 58, TreatyStatus.ALLIANCE, False),
    ("gbr", "deu", 56, TreatyStatus.ALLIANCE, False),

    # The western Pacific.
    ("chn", "jpn", -52, TreatyStatus.TRADE, False),
    ("jpn", "chn", -48, TreatyStatus.TRADE, False),
    ("jpn", "kor", 32, TreatyStatus.TRADE, False),
    ("kor", "jpn", 18, TreatyStatus.TRADE, False),
    ("kor", "prk", -80, TreatyStatus.NONE, False),
    ("prk", "kor", -85, TreatyStatus.NONE, False),
    ("usa", "prk", -75, TreatyStatus.NONE, False),
    ("prk", "usa", -90, TreatyStatus.NONE, False),
    ("chn", "kor", -12, TreatyStatus.TRADE, False),
    ("kor", "chn", -20, TreatyStatus.TRADE, False),

    # South Asia.
    ("ind", "pak", -72, TreatyStatus.NONE, False),
    ("pak", "ind", -75, TreatyStatus.NONE, False),
    ("ind", "chn", -55, TreatyStatus.TRADE, False),
    ("chn", "ind", -45, TreatyStatus.TRADE, False),
    ("chn", "pak", 74, TreatyStatus.DEFENSIVE, False),
    ("pak", "chn", 80, TreatyStatus.DEFENSIVE, False),
    ("ind", "usa", 45, TreatyStatus.TRADE, False),
    ("usa", "ind", 55, TreatyStatus.TRADE, False),
    ("ind", "rus", 40, TreatyStatus.TRADE, False),
    ("rus", "ind", 48, TreatyStatus.TRADE, False),
    ("ind", "jpn", 52, TreatyStatus.TRADE, False),
    ("jpn", "ind", 50, TreatyStatus.TRADE, False),

    # The Middle East.
    ("isr", "irn", -92, TreatyStatus.NONE, False),
    ("irn", "isr", -95, TreatyStatus.NONE, False),
    ("usa", "isr", 72, TreatyStatus.DEFENSIVE, False),
    ("isr", "usa", 80, TreatyStatus.DEFENSIVE, False),
    ("usa", "irn", -76, TreatyStatus.NONE, False),
    ("irn", "usa", -85, TreatyStatus.NONE, False),
    ("sau", "irn", -62, TreatyStatus.NON_AGGRESSION, False),
    ("irn", "sau", -55, TreatyStatus.NON_AGGRESSION, False),
    ("usa", "sau", 45, TreatyStatus.TRADE, False),
    ("sau", "usa", 38, TreatyStatus.TRADE, False),
    ("sau", "chn", 32, TreatyStatus.TRADE, False),
    ("chn", "sau", 40, TreatyStatus.TRADE, False),
    ("sau", "isr", -18, TreatyStatus.NONE, False),
    ("isr", "sau", 10, TreatyStatus.NONE, False),
    ("tur", "isr", -55, TreatyStatus.NONE, False),
    ("isr", "tur", -40, TreatyStatus.NONE, False),
    ("tur", "rus", 20, TreatyStatus.TRADE, False),
    ("rus", "tur", 25, TreatyStatus.TRADE, False),
    ("irn", "rus", 55, TreatyStatus.TRADE, False),
    ("rus", "irn", 50, TreatyStatus.TRADE, False),
    ("irn", "chn", 48, TreatyStatus.TRADE, False),
    ("chn", "irn", 42, TreatyStatus.TRADE, False),
)


#: Standing commitments. Every one of these is either honoured or broken during play, and
#: breaking one is a legitimacy event -- which is the whole reason a treaty is a row and
#: not a sentence in a briefing.
TREATIES: tuple[tuple[CommitmentKind, str, str, tuple[str, ...]], ...] = (
    (CommitmentKind.DEFENSIVE_PACT, "active",
     "North Atlantic Treaty, Article 5: an attack on one member is an attack on all.",
     ("usa", "gbr", "fra", "deu", "tur", "pol", "ita")),
    (CommitmentKind.ALLIANCE, "active",
     "AUKUS: Australia, the United Kingdom and the United States on nuclear submarine "
     "propulsion and advanced capabilities. Read in Beijing as containment.",
     ("usa", "gbr", "aus")),
    (CommitmentKind.DEFENSIVE_PACT, "active",
     "US-Japan Treaty of Mutual Cooperation and Security. American forces are based on "
     "Japanese soil and the guarantee covers the Senkakus.",
     ("usa", "jpn")),
    (CommitmentKind.DEFENSIVE_PACT, "active",
     "US-ROK Mutual Defense Treaty. Twenty-eight thousand American troops remain in "
     "South Korea.",
     ("usa", "kor")),
    (CommitmentKind.DEFENSIVE_PACT, "active",
     "DPRK-Russia Treaty on Comprehensive Strategic Partnership (2024): mutual military "
     "assistance if either is attacked. North Korean shells and troops have already gone "
     "the other way.",
     ("prk", "rus")),
    (CommitmentKind.DEFENSIVE_PACT, "active",
     "China-DPRK Treaty of Friendship, Cooperation and Mutual Assistance -- the oldest "
     "standing alliance either party has, and the one Beijing least likes discussing.",
     ("chn", "prk")),
    (CommitmentKind.ALLIANCE, "active",
     "The China-Pakistan strategic partnership: the economic corridor, the arms "
     "relationship, and a shared interest in India being occupied elsewhere.",
     ("chn", "pak")),
    (CommitmentKind.NON_AGGRESSION_PACT, "active",
     "The China-Russia partnership 'without limits' (2022). Not a defence pact and "
     "deliberately not called one; both sides prefer the ambiguity.",
     ("chn", "rus")),
    (CommitmentKind.NON_AGGRESSION_PACT, "active",
     "The Egypt-Israel Treaty of Peace (1979). The first Arab recognition of Israel, the "
     "demilitarisation of Sinai, and the reason the southern front has been quiet for "
     "forty-seven years. Cold, unpopular in Egypt, and unbroken.",
     ("egy", "isr")),
    (CommitmentKind.NON_AGGRESSION_PACT, "active",
     "The Saudi-Iranian normalisation brokered by China (2023). Diplomatic relations "
     "restored; none of the underlying quarrels settled.",
     ("sau", "irn")),
    (CommitmentKind.TRADE_AGREEMENT, "active",
     "The Abraham Accords framework. Normalisation between Israel and several Gulf "
     "states, with Saudi accession repeatedly close and never signed.",
     ("isr", "sau")),
)


#: Engine-authored history the world begins with. Diffs must cite a cause, so a world with
#: no past would have nothing legal to point at; these give the causal graph a real root.
HISTORY: tuple[tuple[EventKind, str, str, Visibility], ...] = (
    (EventKind.TERRITORY_TRANSFER, "rus",
     "Russia annexed Crimea (2014) after an occupation and a referendum almost nobody "
     "recognised. The sanctions imposed then were the first of many and reversed nothing.",
     Visibility.PUBLIC),
    (EventKind.WAR_DECLARED, "rus",
     "Russia invaded Ukraine in February 2022, expecting Kyiv to fall in days. Four years "
     "later the front is largely static, both armies are exhausted, and neither government "
     "can accept the terms the other would sign.",
     Visibility.PUBLIC),
    (EventKind.ALLIANCE_FORMED, "usa",
     "Finland and Sweden joined NATO (2023-24), doubling the alliance's border with "
     "Russia. Moscow's stated reason for the invasion produced the opposite of its "
     "stated aim.", Visibility.PUBLIC),
    (EventKind.ALLIANCE_FORMED, "gbr",
     "AUKUS (2021) gave Australia nuclear submarine propulsion and gave Beijing a "
     "permanent grievance. France was not told in advance and has not entirely forgiven "
     "it.", Visibility.PUBLIC),
    (EventKind.CASUS_BELLI, "chn",
     "China regards Taiwan as a province and its eventual reunification as settled. "
     "Taiwan is governed entirely separately, defends itself, and has never been ruled by "
     "the People's Republic. The United States neither recognises the claim nor promises "
     "to fight it -- an ambiguity everyone calls strategic and nobody finds comfortable.",
     Visibility.PUBLIC),
    (EventKind.ECONOMIC_REFORM, "usa",
     "The United States restricted advanced semiconductor and equipment exports to China "
     "(2022 onward), and pressed its allies to do the same. Beijing calls it containment "
     "and is building the alternative.", Visibility.PUBLIC),
    (EventKind.WAR_DECLARED, "isr",
     "The war that began with the Hamas attacks of 7 October 2023 reshaped the region: "
     "Gaza devastated, Hezbollah's leadership destroyed, the Assad government gone, and "
     "Israel more secure militarily and more isolated diplomatically than before.",
     Visibility.PUBLIC),
    (EventKind.CASUS_BELLI, "irn",
     "Iran's enrichment has passed every threshold the 2015 agreement set and the "
     "agreement itself is dead. It has not built a weapon. Israel has said repeatedly "
     "that it will not permit one.", Visibility.PUBLIC),
    (EventKind.UNIT_RAISED, "prk",
     "North Korea is a nuclear state in fact, with warheads, road-mobile launchers and "
     "an intercontinental delivery system it has tested. No negotiation has reversed any "
     "part of it.", Visibility.PUBLIC),
    (EventKind.BATTLE, "ind",
     "India and Pakistan exchanged air strikes and missile fire in 2025 before pulling "
     "back. Both are nuclear-armed, both claimed victory, and the underlying dispute over "
     "Kashmir is exactly where it was.", Visibility.PUBLIC),
    (EventKind.ECONOMIC_REFORM, "deu",
     "Germany announced a fundamental turn in defence and energy policy after 2022. "
     "Russian gas is gone; the rearmament is real, slow, and watched closely by "
     "neighbours who remember why it was avoided.", Visibility.PUBLIC),
    (EventKind.DIPLOMATIC_MESSAGE, "usa",
     "Washington's allies have begun planning for an America that does not show up. None "
     "of them says so publicly and all of them are doing it, which is itself the most "
     "consequential fact about the alliance system.", Visibility.PUBLIC),
)


#: What a state can work out how to do between here and the 2030s.
#:
#: **Same four levers as 1936, and the same test to be in the catalogue at all**: a
#: programme has to move something shipped code already reads -- a build time the yards
#: consult, the base strength every delivered formation is stamped with, the industry that
#: sets the size of the queue, or the economy. A technology that unlocked a capability
#: nothing consults would be a line in a menu.
#:
#: **Every subject is a kind this era actually fields.** The interwar catalogue names
#: ``battleship`` three times; Modern Day has no battleships, and before the catalogue was
#: era-gated those effects vanished silently at completion -- welding delivered two thirds
#: of what it promised and radar became an air-only technology, both reporting success.
#: The kinds below are exactly the seven in :data:`UNIT_TEMPLATES`.
#:
#: The shape of the tree is the era's argument about what wins a modern war: cheap mass
#: (drones) against exquisite quality (hypersonics, stealth), with the semiconductor
#: programme upstream of both because in 2026 it is upstream of everything -- which is also
#: what the scenario's own history says the export controls are about.
TECHNOLOGIES: tuple[Technology, ...] = (
    Technology(
        id="uncrewed_systems",
        name="Uncrewed systems at scale",
        blurb=(
            "Attritable drones by the ten thousand, built in shipping containers by people "
            "who were making agricultural equipment last year. The cheapest way anybody "
            "has found to make a modern army expensive to be."
        ),
        turns=4,
        cost_treasury=28.0,
        effects=(
            TechEffect("unit_template", "base_strength", 1.5, "infantry"),
            TechEffect("unit_template", "base_strength", 1.5, "armour"),
            TechEffect("unit_template", "build_days", -30.0, "air_wing"),
        ),
    ),
    Technology(
        id="advanced_semiconductors",
        name="Advanced semiconductors",
        blurb=(
            "Sub-five-nanometre fabrication and the tooling behind it. Four companies on "
            "earth can do this, one island makes most of it, and the export controls in "
            "everyone's history are about exactly that."
        ),
        turns=8,
        cost_treasury=70.0,
        effects=(
            TechEffect("nation", "industry", 10.0),
            TechEffect("nation", "economy", 5.0),
        ),
    ),
    Technology(
        id="integrated_air_defence",
        name="Integrated air and missile defence",
        blurb=(
            "Layered interceptors on one picture, cued by sensors nobody has to own. It "
            "does not win a war; it decides whether the other side's opening move works."
        ),
        turns=6,
        cost_treasury=52.0,
        # **No template effects at all, deliberately.** Its own blurb says it "does not
        # win a war; it decides whether the other side's opening move works" -- and it
        # was implemented as two flat strength bumps, which is exactly winning the war a
        # little. It is now purely an interception term (`air.INTERCEPT_TECHS`), worth
        # 0.15 against every incoming strike and every incoming warhead, and worth
        # nothing whatever on offence. The first technology in ATLAS that is read at
        # resolution time rather than paid out once at completion.
        effects=(),
    ),
    # **Re-described, not renamed.** "Too fast to intercept" was the right claim attached
    # to nothing a player could see: what the programme buys is `air.PENETRATION_TECHS`,
    # which makes what you *send* -- a bomber's raid, a warhead -- survive the interception
    # term, plus the two hull bonuses a ship carrying these really does earn. It is
    # delivery survivability rather than a separate missile arm, and the blurb now says so.
    # Effects untouched; the name was never the problem.
    Technology(
        id="hypersonic_strike",
        name="Hypersonic strike",
        blurb=(
            "Manoeuvring warheads too fast to intercept and too fast to think about. In "
            "practice it buys two things: raids and warheads you send are harder to shoot "
            "down on the way in, and the destroyers and submarines carrying them fight "
            "harder. It is a way through somebody's air defence, not a new arm."
        ),
        turns=9,
        cost_treasury=85.0,
        requires=("advanced_semiconductors",),
        # The hulls keep their bonus -- a ship carrying these missiles really is more
        # dangerous -- but the missiles now also do the thing the name promises, through
        # `air.PENETRATION_TECHS`. Until now `hypersonic_strike` was read by nothing at
        # all except as a prerequisite, and "too fast to intercept" bought a better gun.
        effects=(
            TechEffect("unit_template", "base_strength", 3.5, "destroyer"),
            TechEffect("unit_template", "base_strength", 2.5, "submarine"),
        ),
    ),
    Technology(
        id="modular_shipbuilding",
        name="Modular shipbuilding",
        blurb=(
            "Whole sections finished ashore and welded up in sequence. It is why one "
            "country can launch a frigate a quarter and another takes six years over a "
            "hull it has built before."
        ),
        turns=6,
        cost_treasury=48.0,
        effects=(
            TechEffect("unit_template", "build_days", -120.0, "carrier"),
            TechEffect("unit_template", "build_days", -60.0, "destroyer"),
            TechEffect("unit_template", "build_days", -30.0, "transport"),
        ),
    ),
    Technology(
        id="autonomous_undersea",
        name="Autonomous undersea warfare",
        blurb=(
            "Uncrewed hulls that sit on the bottom for months and cannot be told from a "
            "rock until they are not. The seabed is where the cables are."
        ),
        turns=7,
        cost_treasury=60.0,
        requires=("uncrewed_systems",),
        effects=(
            TechEffect("unit_template", "base_strength", 3.0, "submarine"),
            TechEffect("unit_template", "build_days", -30.0, "submarine"),
        ),
    ),
    Technology(
        id="warhead_modernisation",
        name="Warhead modernisation",
        blurb=(
            "Not a first bomb -- these states have had those for decades -- but the "
            "programme that keeps an ageing deterrent credible: new physics packages, new "
            "delivery, and a test nobody admits to. Pursued out of sight, because the "
            "point of it is what other capitals believe."
        ),
        turns=14,
        cost_treasury=140.0,
        requires=("advanced_semiconductors", "hypersonic_strike"),
        secret=True,
        effects=(
            TechEffect("nation", "military_strength", 10.0),
            TechEffect("nation", "legitimacy", 2.0),
        ),
    ),
)

#: And the upgrade lines every era has: Mk II and Mk III of each family it fields.
TECHNOLOGIES = TECHNOLOGIES + upgrade_programmes(
    tuple(template.kind for template in UNIT_TEMPLATES))


#: Ground that is nobody's state. The Antarctic Treaty has been in force since 1961 and
#: suspends every claim on the continent; seven of them are still asserted and none is
#: recognised by the others.
#:
#: **This era used to give Antarctica a government, an army and a treasury**, which the 1972
#: table already refused to do -- the scenario auditor found the two eras disagreeing, and
#: only one of them can be right. All three now agree, including 1936: five overlapping
#: claims, no permanent inhabitants and no government is ownerless ground, not a nation.
#: **Who actually holds the ground that is not its own country.**
#:
#: The modern map draws every overseas territory as its own entry, and until now the
#: scenario let every one of them govern itself. That put roughly forty sovereign states on
#: the 2026 map that do not exist: Britain did not hold the Falklands, China did not hold
#: Hong Kong, the United States did not hold Guam, and Denmark did not hold Greenland. It
#: also mattered mechanically rather than only cosmetically -- air reach and delivery range
#: are both counted in hops from ground a power *holds*, so an America without Guam is a
#: different Pacific power.
#:
#: **A holder need not be one of this era's powers.** Denmark, the Netherlands, New Zealand
#: and Australia are minors here, and a minor holder lists its own code first: the seat of
#: government is ``codes[0]``, and a nation row is built for it from everything it ends up
#: with. A power already has a row, so a power lists only what it holds *besides* home.
#:
#: **Jersey, Guernsey and the Isle of Man are constitutionally not British territory.**
#: They are Crown Dependencies -- possessions of the Crown, not of the United Kingdom, with
#: their own legislatures and outside the UK for most purposes. They are filed under `gbr`
#: anyway, following the 1972 table, because the engine has one relationship to offer and
#: "held by Britain" is far closer than "sovereign state". Recorded here so the next person
#: to notice finds the decision rather than the mistake.
#:
#: Antarctica is not here: it is in :data:`ABSENT`, because the Antarctic Treaty suspends
#: every claim and ground nobody governs is a better answer than a nation with a treasury
#: and a border guard on the ice. Ashmore and Cartier (``ATC``) is Australian in spite of
#: the code, and the French Southern and Antarctic Lands (``ATF``) are French for the same
#: reason the Kerguelen are: the treaty suspends the *continental* claim, not the islands.
SOVEREIGNTY: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    # --- powers, which already have a nation row, so only the ground beyond home
    ("gbr", "United Kingdom",
     ("FLK", "SGS", "BMU", "CYM", "VGB", "AIA", "MSR", "TCA", "SHN", "IOT", "PCN",
      "GGY", "JEY", "IMN")),
    ("fra", "France", ("PYF", "NCL", "WLF", "SPM", "MAF", "BLM", "ATF")),
    ("usa", "United States", ("PRI", "VIR", "GUM", "ASM", "MNP")),
    ("chn", "China", ("HKG", "MAC")),

    # --- minor holders, which list their own seat first and are built from the whole
    ("m_dnk", "Denmark", ("DNK", "GRL", "FRO")),
    ("m_nld", "Netherlands", ("NLD", "ABW", "CUW", "SXM")),
    ("m_nzl", "New Zealand", ("NZL", "COK", "NIU")),
    ("aus", "Australia", ("NFK", "HMD", "ATC")),
)


ABSENT: frozenset[str] = frozenset({"ATA"})


#: **The line of control in Ukraine**, as ``region id -> who actually holds it``.
#:
#: The last thing the scenario auditor was still reporting about this era. The war was
#: modelled as a relation and a set of national strengths and nothing else, so no region
#: anywhere on the 2026 map had a controller that differed from its owner -- Russia was at
#: war with Ukraine over ground it did not, as far as the database was concerned, hold.
#:
#: Sovereignty stays Ukrainian and so does the core. That is the point of using
#: ``controller_nation_id`` rather than moving the region: a settlement has something to
#: hand back, and `nuclear.deterrence` does not extend a Russian nuclear guarantee over
#: ground Ukraine is trying to retake. See :attr:`~atlas.scenarios.seeder.Scenario.occupied`.
#:
#: **Where the map is coarser than the war, and what was done about it.** A region is held
#: by one nation or the other; there is no half. Four of the five oblasts here are divided
#: in reality, so each entry is a rounding, and the rule applied throughout is **whoever
#: holds the oblast capital**:
#:
#: * **Crimea** is exact. Russian-held entire since 2014, and the only entry here that is
#:   not an approximation at all. Natural Earth files it under Russia and the generator
#:   used to take that mechanically, which turned an annexation into sovereignty.
#: * **Luhansk** is very nearly exact -- effectively the whole oblast.
#: * **Donetsk** rounds *toward* Russia: roughly four fifths of the oblast including the
#:   city, against a Ukrainian-held northern corner around Kramatorsk and Sloviansk that
#:   this map cannot draw.
#: * **Zaporizhzhia and Kherson round toward Ukraine, and this under-credits Russia.**
#:   Russia holds most of both by area -- Melitopol, Berdiansk, the nuclear plant, the east
#:   bank of the Dnipro -- and Ukraine holds both oblast capitals: Zaporizhzhia city was
#:   never entered, and Kherson city was retaken in November 2022. Marking either region
#:   Russian would hand Moscow a city it has never held, which is a worse error than
#:   under-crediting ground, so they are Ukrainian and this note is the flag.
#:
#: **What genuinely cannot be expressed:** a province contested by both. The engine has one
#: controller per region and the map has one shape per oblast, so "the front runs through
#: here" has nowhere to live at seed time. Splitting Zaporizhzhia and Kherson along the
#: Dnipro would need geometry Natural Earth does not carry at admin-1. Stated rather than
#: forced.
OCCUPIED: dict[str, str] = {
    "r_ukr_crimea": "rus",     # 2014, entire
    "r_ukr_luhans_k": "rus",   # effectively the whole oblast
    "r_ukr_donets_k": "rus",   # ~4/5, including the city; Kramatorsk is not drawable
    # --- the older lines of control, which did not stop being lines because they got old
    "r_sah": "m_mar",          # Morocco, west of the berm, since 1975
    "r_cyn": "tur",            # the Turkish garrison, since 1974
    "r_psx": "isr",            # military occupation since 1967
}

#: **What is contested in 2026 and is not in the table above, and why.**
#:
#: Two different reasons, and conflating them is how a map starts lying.
#:
#: *Not an occupation.* Taiwan, Kosovo and Somaliland are governed by the people who live
#: in them and claimed by somebody else. That is a dispute about sovereignty, and the
#: engine already says it correctly with ordinary ownership -- `m_twn` owns Taiwan and
#: China does not. Writing a Chinese controller onto Taipei would not model the claim, it
#: would model an invasion that has not happened. What these need is a *guard* that their
#: `home_nation_id` never drifts to the claimant, which the era manifest now carries: were
#: Taiwan's home to become `chn`, `nuclear.deterrence` would extend a Chinese nuclear
#: guarantee over Taipei -- the same defect the Crimea entry exists to prevent, pointing
#: the other way.
#:
#: *Not drawable.* The Golan Heights, Abkhazia, South Ossetia, Transnistria and
#: Nagorno-Karabakh are real lines of control with no shape of their own at admin-0, so
#: there is no region to write a controller onto. Stated rather than forced, exactly as
#: Zaporizhzhia and Kherson are above.
#:
#: One entry above is a genuine approximation and is flagged as one: `r_psx` is a single
#: shape covering both the West Bank and Gaza. The West Bank has been under Israeli
#: military occupation since 1967 and is the larger share of the ground, so the region
#: takes an Israeli controller; that is the closest single answer this geometry allows to
#: a question that has two.
CONTESTED_BUT_NOT_OCCUPIED: tuple[str, ...] = (
    "r_twn_taipei_city", "r_twn_kinmen", "r_kos", "r_sol",
)


#: Everything above, as the one object the seeder and the registry both read.
#: **What each power is built to do now.**
#:
#: The three western entries are the same shape and it is not a coincidence: Britain, France
#: and Germany all ended conscription within fifteen years of each other, and all three field
#: small professional armies they have recently discovered are too small.
#:
#: China is left off the alignment axis and Saudi Arabia off all three, because neither
#: belongs on either pole of anything here. Pakistan is left off the nuclear axis
#: deliberately: it repudiated "credible minimum deterrence" for full-spectrum deterrence in
#: 2013, and that is a third posture this axis does not carry.
DOCTRINES: dict[str, dict[str, str]] = {
    "usa": {"mobilisation": "professional", "nuclear_posture": "retaliation",
            "alignment": "collective_security"},
    "rus": {"mobilisation": "mass", "nuclear_posture": "retaliation",
            "alignment": "collective_security"},
    # Two million under arms, and the largest standing army in the world.
    "chn": {"mobilisation": "mass", "nuclear_posture": "minimum"},
    # General mobilisation, which is the whole of this country's last four years.
    "ukr": {"mobilisation": "mass"},
    "gbr": {"mobilisation": "professional", "nuclear_posture": "minimum",
            "alignment": "collective_security"},
    "fra": {"mobilisation": "professional", "nuclear_posture": "minimum",
            "alignment": "collective_security"},
    "deu": {"mobilisation": "professional", "alignment": "collective_security"},
    "jpn": {"mobilisation": "professional"},
    "kor": {"mobilisation": "mass", "alignment": "collective_security"},
    "prk": {"mobilisation": "mass", "nuclear_posture": "minimum"},
    "irn": {"mobilisation": "mass"},
    "isr": {"mobilisation": "mass", "nuclear_posture": "minimum"},
    "ind": {"nuclear_posture": "minimum", "alignment": "neutrality"},
    "tur": {"alignment": "collective_security"},
    # Poland is the clearest `collective_security` case on the map and the only new entry
    # with a mobilisation stance worth arguing about: the army is professional and is being
    # grown at a rate that only makes sense if you expect to need it.
    "pol": {"mobilisation": "professional", "alignment": "collective_security"},
    "aus": {"mobilisation": "professional", "alignment": "collective_security"},
    # Both are non-aligned by long-standing choice rather than by having nobody to join.
    "idn": {"alignment": "neutrality"},
    "bra": {"alignment": "neutrality"},
    "ita": {"mobilisation": "professional", "alignment": "collective_security"},
    # Conscription and an army sized accordingly, in both cases.
    "egy": {"mobilisation": "mass", "alignment": "neutrality"},
    # The `four no's` is about as explicit a statement of non-alignment as a state makes.
    "vnm": {"mobilisation": "mass", "alignment": "neutrality"},
    # **Mobilisation only, and the omission is the point.** Conscription was restored to a
    # full year in 2024 and the whole defence concept is reserve depth, so `mass` is right.
    # There is no alignment stance because Taiwan is not non-aligned -- it is unaligned by
    # other states' choice, which is a different thing this axis cannot say. China is left
    # off the same axis for its own reasons; both are absences somebody decided on.
    "twn": {"mobilisation": "mass"},
}


SCENARIO = Scenario(
    id="modern-day",
    name="Modern Day",
    start_date="2026-01-01",
    rng_seed=2026,
    default_world_id="modern2026",
    era=Era.MODERN,
    powers=POWERS,
    armour=_ARMOUR,
    air=_AIR,
    ship_classes=SHIP_CLASSES,
    fleets=FLEETS,
    unit_templates=UNIT_TEMPLATES,
    technologies=TECHNOLOGIES,
    arsenals=ARSENALS,
    deterrent_boats=DETERRENT_BOATS,
    absent=ABSENT,
    sovereignty=SOVEREIGNTY,
    occupied=OCCUPIED,
    organisations=ORGANISATIONS,
    relations=RELATIONS,
    treaties=TREATIES,
    doctrines=DOCTRINES,
    history=HISTORY,
)


def modern_day_seed(
    pack: GeometryPack | PackSet,
    *,
    world_id: str = "modern2026",
    player: str | None = None,
    difficulty: Difficulty = Difficulty.NORMAL,
    seed: int = 2026,
    include_minors: bool = True,
) -> WorldSeed:
    """Build the 1 January 2026 world. See :func:`~atlas.scenarios.seeder.build_world`."""
    return build_world(
        SCENARIO, pack, world_id=world_id, player=player, difficulty=difficulty,
        seed=seed, include_minors=include_minors,
    )


def apply_2026_politics(conn, world_id: str) -> dict[str, int]:
    """Lay the real 2026 political state over a freshly created world."""
    return apply_politics(conn, world_id, SCENARIO)
