"""1972 - Cold War. The political state of the world on 1 January 1972.

**Why this year and not 1962.** The full argument is the design note in the engine
changelog; the short version is that every mechanism this era exists to exercise is at its
historical maximum on this date. Both superpowers hold a full triad at rough parity and
SALT I is signed in May, so mutual assured destruction is a treaty obligation rather than a
description. The People's Republic took the China seat in October 1971 and tested in 1964,
so the five permanent members of the Security Council are exactly the five *declared*
nuclear powers -- Israel, seeded undeclared, is the sixth armed state and holds no seat --
and no seat has to be fudged. Both blocs exist at full extent. And the Sino-Soviet split is
total, so the world has *three* nuclear poles rather than two, which is what makes a veto
and a pact interesting rather than symmetrical.

**A starting position, not a script**, in exactly the sense 1936 is one. Nixon lands in
Beijing in February, the Easter Offensive opens in March, SALT is signed in May and the
Yom Kippur War is twenty-one months away. All four are near; nothing in the engine makes
any of them happen.

**Twenty powers, and the two Germanys and two Vietnams among them.** A proxy war fought by
a one-division generic minor resolves in a single contest and teaches nothing -- the
argument that made Ukraine a power in Modern Day. Everything else on the map is a minor,
including the Republic of China on Taiwan, which has just lost the seat and not the island.

``economy`` is what a state can **mobilise**, not its nominal product. That matters more
here than in either shipped era: Soviet output is somewhere near half the American figure
and Soviet *military* output is close to parity, because a command economy pointed at the
army converts a smaller product into more divisions. Japan is the mirror image -- the
second-largest economy on earth behind a force that is constitutionally forbidden to be
one.

**The blocs are commitments, not organisations, and the Council is the other way round.**
Seeding NATO twice would put two mechanisms on one treaty: an attack on a member would hand
every other member a casus belli *and* convene a body whose strongest action is to hand
every member a casus belli. So NATO and the Warsaw Pact are ``defensive_pact`` commitments
whose Article 5 the engine already reads, and the Security Council is the one convening
body -- because it has what a pact does not, which is a veto and jurisdiction over powers
that are not its friends.

**This module is data.** The machinery lives in :mod:`atlas.scenarios.seeder`.
"""

from __future__ import annotations

from ..engine.production import BUILD_DAYS
from ..engine.research import TechEffect, Technology, upgrade_programmes
from ..map.pack import GeometryPack, PackSet
from ..state.bootstrap import UnitTemplateSeed, WorldSeed
from ..state.models import CommitmentKind, Difficulty, EventKind, TreatyStatus, Visibility
from .era import Era
from .seeder import Power, Scenario, ShipClass, apply_politics, build_world

__all__ = [
    "ABSENT",
    "ARSENALS",
    "DETERRENT_BOATS",
    "FLEETS",
    "HISTORY",
    "ORGANISATIONS",
    "POWERS",
    "RELATIONS",
    "SCENARIO",
    "SHIP_CLASSES",
    "SOVEREIGNTY",
    "TECHNOLOGIES",
    "TREATIES",
    "UNIT_TEMPLATES",
    "apply_1972_politics",
    "cold_war_seed",
]


#: Figures are relative and deliberately coarse; what has to be right is the shape. Two
#: powers can end the world and everybody else is arranging themselves around that fact.
#: The United States has the larger economy and is spending it in Vietnam; the Soviet Union
#: has the larger army and a smaller product to pay for it. Britain and France are
#: second-rank powers with first-rank deterrents. West Germany and Japan are large economies
#: that are not allowed to be military powers and are the richer for it. Everybody from
#: Hanoi down is an army out of all proportion to what pays for it, which is what living on
#: a fault line does.
POWERS: tuple[Power, ...] = (
    Power(
        id="usa", name="United States", colour="#3B6EA5",
        regions=("USA",), capital="USA",
        character=(
            "Winding down a war it cannot win and will not admit to losing, and about to "
            "open a door to Beijing that nobody expected it to touch. Its guarantees still "
            "hold everywhere and its currency and its patience are both running out."
        ),
        goals=(
            "leave Vietnam without the alliance system concluding it will leave anywhere",
            "split Beijing from Moscow and make the split permanent",
            "get an arms agreement that freezes the Soviet buildup where it is",
            "hold Western Europe together while paying less for it",
        ),
        military=100, economy=100, stability=38, legitimacy=54, treasury=200, manpower=140,
    ),
    Power(
        id="sov", name="Soviet Union", colour="#A5453B",
        regions=("RUS", "UKR", "BLR", "KAZ", "UZB", "TKM", "TJK", "KGZ", "GEO", "ARM",
                 "AZE", "MDA", "LVA", "LTU", "EST"),
        capital="RUS",
        character=(
            "Has reached strategic parity for the first time and intends to be paid for it "
            "in recognition. Holds Eastern Europe with tanks and the memory of 1968, and "
            "watches its longest border with a nuclear China it no longer speaks to."
        ),
        goals=(
            "convert parity into a treaty that acknowledges it",
            "keep the bloc intact and the Germans divided",
            "make sure the Chinese frontier never becomes a second front",
            "supply Hanoi and Cairo enough to matter and not enough to be blamed",
        ),
        military=96, economy=78, stability=62, legitimacy=42, treasury=130, manpower=175,
    ),
    Power(
        id="chn", name="China", colour="#B0453B",
        regions=("CHN",), capital="CHN",
        character=(
            "Coming out of a decade of self-inflicted chaos with a bomb, a permanent seat "
            "and a million Soviet troops on its northern border. Has concluded that the "
            "more dangerous of the two superpowers is the one it shares a frontier with."
        ),
        goals=(
            "make the Soviet border unattractive to cross",
            "convert the American opening into recognition and room to manoeuvre",
            "keep Hanoi dependent on Beijing rather than on Moscow",
            "rebuild an economy and a party that both nearly stopped working",
        ),
        military=58, economy=30, stability=44, legitimacy=46, treasury=32, manpower=240,
    ),
    Power(
        id="gbr", name="United Kingdom", colour="#7A4A6E",
        regions=("GBR",), capital="GBR",
        character=(
            "Has signed the accession treaty and joins Europe in twelve months, having "
            "withdrawn from east of Suez and kept a submarine deterrent it can barely "
            "afford. A serious army, a shrinking navy and an economy in open dispute."
        ),
        goals=(
            "get into the European Communities and make it look like a choice",
            "stay the ally Washington calls first",
            "hold the deterrent and the Rhine Army on one budget",
        ),
        military=40, economy=30, stability=42, legitimacy=58, treasury=52, manpower=45,
    ),
    Power(
        id="fra", name="France", colour="#4A6FA5",
        regions=("FRA",), capital="FRA",
        character=(
            "In the Atlantic alliance and out of its command structure since 1966, with a "
            "national deterrent that answers to nobody else and a settled conviction that "
            "this is what independence costs and is worth."
        ),
        goals=(
            "keep the deterrent national and the command structure at arm's length",
            "lead a Europe that is not an American province",
            "sell weapons and reactors to states the superpowers will not supply",
        ),
        military=42, economy=33, stability=52, legitimacy=58, treasury=58, manpower=45,
    ),
    Power(
        id="wger", name="West Germany", colour="#5F6B70",
        regions=("DEU",), capital="DEU",
        character=(
            "The alliance's front line and its largest European army, run by a chancellor "
            "who has just signed treaties recognising the borders his own party says are "
            "provisional. Rich, exposed, and arguing with itself about both."
        ),
        goals=(
            "make the eastern treaties stick without conceding that the division is final",
            "keep the American garrison and its nuclear guarantee on German soil",
            "hold West Berlin, which is indefensible and non-negotiable",
        ),
        military=38, economy=40, stability=60, legitimacy=60, treasury=88, manpower=55,
    ),
    Power(
        id="ita", name="Italy", colour="#4C8C5A",
        regions=("ITA",), capital="ITA",
        character=(
            "NATO's Mediterranean anchor, governed by a centre that reforms itself every "
            "few months and contains the largest communist party in the West. The bombs "
            "have started and nobody agrees who is setting them."
        ),
        goals=(
            "hold the centre together against both of its extremes",
            "keep the Sixth Fleet's bases and the American subsidy that comes with them",
            "matter in the Mediterranean while Libya and Egypt change sides",
        ),
        military=26, economy=24, stability=32, legitimacy=44, treasury=34, manpower=45,
    ),
    Power(
        id="tur", name="Turkey", colour="#B06A4A",
        regions=("TUR",), capital="TUR",
        character=(
            "Holds the straits the Soviet Black Sea Fleet has to pass through, and is under "
            "martial law after an army ultimatum removed the government last spring. "
            "Indispensable to the alliance and increasingly resentful of it."
        ),
        goals=(
            "hold the straits and the leverage they are",
            "restore civilian order without losing the army's confidence",
            "extract more from Washington than lectures about Cyprus and opium",
        ),
        military=30, economy=14, stability=36, legitimacy=40, treasury=16, manpower=55,
    ),
    Power(
        id="jpn", name="Japan", colour="#B07070",
        regions=("JPN",), capital="JPN",
        character=(
            "The second economy in the world with an army it is not allowed to call one, "
            "about to receive Okinawa back and unsettled by having learned of the American "
            "opening to Beijing from the news."
        ),
        goals=(
            "complete the return of Okinawa without losing the American guarantee",
            "trade with Beijing and Moscow without choosing between Washington and either",
            "keep growing at a rate nobody else has managed",
        ),
        military=16, economy=46, stability=70, legitimacy=64, treasury=100, manpower=75,
    ),
    Power(
        id="eger", name="East Germany", colour="#8C7A5A",
        regions=("DEU",), capital="DEU",
        character=(
            "Has sealed its border with concrete and its population with paperwork, and is "
            "about to be recognised by the country that says it does not exist. The most "
            "militarised and the most closely watched of the Soviet Union's allies."
        ),
        goals=(
            "be recognised as a state by everyone, starting with Bonn",
            "keep the border sealed and West Berlin isolated",
            "outproduce the Federal Republic per head, which is the whole argument",
        ),
        military=30, economy=17, stability=58, legitimacy=28, treasury=20, manpower=25,
    ),
    Power(
        id="pol", name="Poland", colour="#C0A050",
        regions=("POL",), capital="POL",
        character=(
            "Has a new leadership installed a year ago after shipyard strikes were put down "
            "with live rounds, and is buying consent with western credit and consumer goods "
            "it will have to pay for later."
        ),
        goals=(
            "keep the shipyards quiet and the shops stocked",
            "hold the western border the Germans have only just recognised",
            "borrow from the West without answering to it",
        ),
        military=32, economy=17, stability=44, legitimacy=32, treasury=18, manpower=45,
    ),
    Power(
        id="cze", name="Czechoslovakia", colour="#6FA8A0",
        regions=("CZE", "SVK"), capital="CZE",
        character=(
            "Four years into normalisation, with a leadership installed by the tanks that "
            "ended the reform and a Soviet garrison that has not left. Industrially "
            "serious, politically exhausted."
        ),
        goals=(
            "make sure nothing that happened in 1968 happens again",
            "keep the arms industry earning hard currency",
            "be a loyal ally loudly enough that the garrison becomes unnecessary",
        ),
        military=24, economy=15, stability=42, legitimacy=24, treasury=16, manpower=25,
    ),
    Power(
        id="rou", name="Romania", colour="#9E8AB8",
        regions=("ROU",), capital="ROU",
        character=(
            "In the Warsaw Pact, refused to join the invasion of Czechoslovakia, keeps "
            "relations with Israel and Beijing, and is courted by Washington for exactly "
            "those reasons. Independent abroad and rigid at home."
        ),
        goals=(
            "stay inside the Pact and take orders from nobody in it",
            "be the channel every side needs and none controls",
            "industrialise on borrowed money without borrowing the politics",
        ),
        military=20, economy=10, stability=52, legitimacy=38, treasury=11, manpower=30,
    ),
    Power(
        id="dvn", name="North Vietnam", colour="#8AB88A",
        regions=("VNM",), capital="VNM",
        character=(
            "Has absorbed seven years of the heaviest bombing in history and is about to "
            "launch the largest offensive of the war. Takes tanks from Moscow and rice from "
            "Beijing and accepts instructions from neither."
        ),
        goals=(
            "unify the country, and outlast whoever is in the way of that",
            "break the southern army before the Americans finish leaving",
            "keep both patrons supplying and neither deciding",
        ),
        military=44, economy=7, stability=62, legitimacy=66, treasury=18, manpower=35,
    ),
    Power(
        id="rvn", name="South Vietnam", colour="#C8A93B",
        regions=("VNM",), capital="VNM",
        character=(
            "A large, well-equipped army with a hollow officer corps, watching its ally "
            "withdraw on a schedule it did not set. Everything it has was given to it and "
            "the giving is ending."
        ),
        goals=(
            "survive the American withdrawal",
            "hold the northern provinces, which is where any offensive comes",
            "keep the aid arriving after the troops have gone",
        ),
        military=34, economy=9, stability=26, legitimacy=26, treasury=22, manpower=30,
    ),
    Power(
        id="prk", name="North Korea", colour="#6E5A8C",
        regions=("PRK",), capital="PRK",
        character=(
            "The most heavily armed country per head on earth, playing its two patrons "
            "against each other with more skill than either likes to admit, and talking to "
            "Seoul for the first time in twenty years."
        ),
        goals=(
            "unify the peninsula on its own terms and no one else's",
            "take from Moscow and Beijing without becoming either one's client",
            "keep the Americans convinced that the cost of a second war is unacceptable",
        ),
        military=30, economy=10, stability=58, legitimacy=32, treasury=16, manpower=22,
    ),
    Power(
        id="kor", name="South Korea", colour="#4F8C8C",
        regions=("KOR",), capital="KOR",
        character=(
            "Industrialising at a speed nobody has seen before under a president who is "
            "about to abolish the constitution that limits his terms. Two divisions of its "
            "army are in Vietnam and it is being paid for them."
        ),
        goals=(
            "industrialise faster than the North can rearm",
            "keep the American divisions on the peninsula",
            "talk to Pyongyang without conceding anything by talking",
        ),
        military=28, economy=11, stability=38, legitimacy=30, treasury=14, manpower=25,
    ),
    Power(
        id="cub", name="Cuba", colour="#C08A5A",
        regions=("CUB",), capital="CUB",
        character=(
            "Ninety miles from an embargo that has not moved it in a decade, kept solvent "
            "by Soviet oil and a sugar price nobody else would pay, and exporting soldiers "
            "and doctors to arguments on three continents."
        ),
        goals=(
            "survive the embargo and outlast the government imposing it",
            "keep the Soviet subsidy without becoming a Soviet garrison",
            "support revolutions elsewhere, which is what the subsidy is for",
        ),
        military=16, economy=6, stability=56, legitimacy=48, treasury=9, manpower=12,
    ),
    Power(
        id="isr", name="Israel", colour="#6E8CB0",
        regions=("ISR",), capital="ISR",
        character=(
            "Holds the Sinai, the Golan and the West Bank after six days in 1967 and has "
            "concluded from the quiet since that the lines will hold. Militarily dominant "
            "and increasingly certain that dominance is the same thing as security."
        ),
        goals=(
            "hold the 1967 lines until somebody offers peace in exchange for them",
            "keep the American supply of aircraft flowing",
            "make certain no Arab army believes it can win",
        ),
        military=34, economy=12, stability=52, legitimacy=58, treasury=30, manpower=9,
    ),
    Power(
        id="egy", name="Egypt", colour="#A58C4A",
        regions=("EGY",), capital="EGY",
        character=(
            "Lost Sinai in six days, has spent four years rebuilding the army that lost it, "
            "and is running out of patience with Soviet advisors who will supply defensive "
            "weapons and not offensive ones."
        ),
        goals=(
            "get Sinai back, by war if the diplomacy will not deliver it",
            "get weapons Moscow does not want to give and a hearing Washington will not",
            "lead the Arab states without paying for all of them",
        ),
        military=30, economy=12, stability=40, legitimacy=40, treasury=26, manpower=40,
    ),
)


#: Ground a great power holds that is not its own nation: ``minor id -> (owner, divisions)``.
#:
#: **West Berlin is the whole of it, and it is the era's flashpoint made mechanical.** The
#: territory file draws the city as ``m_wbe``; this says who holds it and how hard. Natural
#: Earth has one Berlin shape, so the city cannot be divided on this map -- the scenario
#: says so rather than inventing geometry, and gives the shape to the Western garrison,
#: which is the half of it that could be attacked.
#:
#: Owned by the **United States** rather than by the Federal Republic, because that is what
#: it was: occupied ground garrisoned by the three Western powers, and closed to the
#: Bundeswehr by the same occupation statute. It also means an East German or Soviet move on
#: the city is an attack on ground the United States owns, so Article 5 is invoked by the
#: shipped mechanism and not by a special case written for this scenario.
#:
#: Three divisions: the American, British and French brigades were about a division between
#: them, and the scenario rounds up because the garrison is the point of it. They cannot be
#: reinforced by land -- the enclave touches nothing but East Germany -- which is exactly
#: the problem every plan for defending Berlin ever had.
COLONIAL_HOLDINGS: dict[str, tuple[str, int]] = {
    "m_wbe": ("usa", 3),
}


#: **Who the rest of the world was in 1972**, as ``(holder, display name, modern codes)``.
#:
#: Without this the seeder makes an independent state of every modern map entry no power
#: claims, which is right for a scenario set today and wrong for one set in 1972. The first
#: build of this era had **209 minors**, among them Bosnia, Croatia, Slovenia, Eritrea,
#: East Timor, Micronesia, Namibia, Zimbabwe and Belize -- none of them a state on 1 January
#: 1972 -- and Antarctica with a treasury, a government and a border guard. The preset's own
#: hard constraints say *do not invent nations*, and the scenario was inventing sixty of
#: them.
#:
#: Three kinds of entry, one mechanism. **Successors merged back into their parent**, which
#: is what 1936 already does when it composes Czechoslovakia and the Soviet Union out of
#: successor regions -- done here for minors instead of powers. **Dependencies given to
#: whoever held them**, which is also the honest answer to who a war over one would be
#: against. And **renames**, where the modern name is a different polity: the state on that
#: ground in 1972 was Rhodesia, Zaire, Burma.
#:
#: **What is deliberately left independent**, because it already was by this date: Bangladesh
#: (December 1971, three weeks old), Bahrain, Qatar and the United Arab Emirates (all 1971),
#: Fiji, Tonga, Nauru, Mauritius, Barbados, Guyana, Malta, Jamaica, Trinidad and Samoa.
#:
#: **Judgement calls, flagged rather than buried.** Rhodesia is treated as a state because it
#: had an army, a government and eleven years of UDI, though de jure it was a British colony
#: in rebellion -- the alternative would hand Salisbury's brigades to London, which is worse.
#: The New Hebrides were an Anglo-French condominium and are given to Britain because the
#: engine has no shared sovereignty. Rhodesia, Namibia and the Portuguese territories all had
#: live independence wars in 1972 that this does not model; they are ground held, not ground
#: quiet.
#: **Yugoslavia is made of its republics; it does not hold them.** The only entry in any
#: era whose holder is a minor and whose own code appears in none of its constituents --
#: there is no YUG province, so the coreness test in `_disown_dependencies` disowned all
#: seven. See `Scenario.composites`.
COMPOSITES: frozenset[str] = frozenset({"m_yug"})

SOVEREIGNTY: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    # --- successor states, merged into the state they were part of
    ("m_yug", "Yugoslavia", ("SRB", "HRV", "BIH", "SVN", "MNE", "MKD", "KOS")),
    ("m_eth", "Ethiopia", ("ETH", "ERI")),           # Eritrea annexed 1962
    ("m_som", "Somalia", ("SOM", "SOL")),            # British and Italian, united 1960
    ("m_sdn", "Sudan", ("SDN", "SDS")),
    ("m_cyp", "Cyprus", ("CYP", "CYN")),             # the invasion is two years away
    ("m_ind", "India", ("IND", "KAS")),
    ("m_fin", "Finland", ("FIN", "ALD")),

    # --- the last European empire in Africa, and it is fighting for it
    ("m_prt", "Portugal", ("PRT", "AGO", "MOZ", "GNB", "CPV", "STP", "TLS", "MAC")),

    # --- what is left of the British one
    ("gbr", "United Kingdom",
     ("BLZ", "HKG", "BHS", "GRD", "DMA", "LCA", "VCT", "KNA", "ATG", "MSR", "AIA",
      "VGB", "CYM", "TCA", "BMU", "FLK", "SGS", "SHN", "IOT", "SLB", "KIR", "TUV",
      "SYC", "BRN", "VUT", "PCN", "GGY", "JEY", "IMN")),
    ("fra", "France", ("DJI", "COM", "NCL", "PYF", "WLF", "SPM", "MAF", "BLM", "ATF")),
    # The Trust Territory of the Pacific Islands, administered for the United Nations.
    ("usa", "United States", ("PRI", "VIR", "GUM", "ASM", "MNP", "FSM", "MHL", "PLW")),
    ("m_nld", "Netherlands", ("NLD", "SUR", "ABW", "CUW", "SXM")),
    ("m_aus", "Australia", ("AUS", "PNG", "NFK", "ATC", "HMD", "IOA")),
    ("m_nzl", "New Zealand", ("NZL", "COK", "NIU")),
    ("m_dnk", "Denmark", ("DNK", "GRL", "FRO")),
    ("m_zaf", "South Africa", ("ZAF", "NAM")),       # South West Africa
    ("m_esp", "Spain", ("ESP", "SAH")),              # Spanish Sahara
    # Occupied since 1967, and the other half of the Sinai entry in the territory file.
    ("isr", "Israel", ("PSX",)),

    # --- the same ground, under the name it had
    ("m_zwe", "Rhodesia", ("ZWE",)),
    ("m_cod", "Zaire", ("COD",)),
    ("m_mmr", "Burma", ("MMR",)),
    ("m_lka", "Ceylon", ("LKA",)),
    ("m_ben", "Dahomey", ("BEN",)),
    ("m_bfa", "Upper Volta", ("BFA",)),
    ("m_swz", "Swaziland", ("SWZ",)),
    ("m_khm", "Khmer Republic", ("KHM",)),
    ("m_twn", "Republic of China", ("TWN",)),        # it has just lost the seat, not the island
)

#: Ground that is nobody's state. The Antarctic Treaty entered into force in 1961 and
#: suspends every claim on the continent, which is a better answer than a nation with a
#: treasury and a border guard on the ice.
ABSENT: frozenset[str] = frozenset({"ATA"})


#: Armoured formations. Everybody has tanks by 1972; what varies is how many are worth
#: counting. The Warsaw Pact's whole theory of the central front is armour, and the
#: distribution says so.
_ARMOUR = {
    "sov": 6, "usa": 4, "chn": 3, "wger": 3, "eger": 2, "pol": 2, "cze": 2,
    "isr": 2, "egy": 2, "rvn": 2, "prk": 2, "kor": 1, "gbr": 1, "fra": 1,
    "ita": 1, "tur": 1, "rou": 1, "dvn": 1, "cub": 1,
}

#: Air power. The United States is flying the largest air campaign since Korea this year
#: and it shows; the Soviet Union answers with numbers; and Israel's air force is the
#: single most decisive instrument in its neighbourhood.
_AIR = {
    "usa": 6, "sov": 5, "chn": 3, "isr": 3, "gbr": 2, "fra": 2, "wger": 2,
    "egy": 2, "rvn": 2, "dvn": 1, "ita": 1, "tur": 1, "jpn": 1, "eger": 1,
    "pol": 1, "cze": 1, "kor": 1, "prk": 1, "rou": 1, "cub": 1,
}


#: What a fleet is in 1972. The battleship is thirty years gone and the missile has
#: replaced the gun in every navy that matters, which is the argument this era's fleets are
#: about: the carrier is the only way to put sustained air power over an ocean, and the
#: guided-missile cruiser and the nuclear boat are the two answers to it that a power
#: without carriers can afford. That is Gorshkov's entire thesis and it is why the Soviet
#: line here has one shape and the American another.
#:
#: Two of the names are load-bearing rather than flavour. ``transport`` is what
#: :func:`atlas.engine.naval.sea_lift` looks for -- read it as an amphibious ready group.
#: ``submarine`` is what :func:`atlas.engine.nuclear.survivability` counts at sea, so a
#: second strike genuinely weakens when the boats carrying it are sunk.
SHIP_CLASSES: tuple[ShipClass, ...] = (
    ShipClass("carrier", 11.0, 15.0),
    ShipClass("cruiser", 9.0, 14.0),
    ShipClass("submarine", 8.0, 18.0),
    ShipClass("transport", 2.5),
)

#: Hulls in commission as ``(carriers, cruisers, submarines, amphibious)``.
#:
#: Carriers are near-real and everything else is roughly a tenth, exactly as Modern Day
#: scales its fleet, and for the same reason: there were about twenty carriers in the world
#: and each one changed what its owner could attempt, while a United States fielding two
#: hundred escorts would be most of the units on the map and would bankrupt itself paying
#: for them.
#:
#: The two lines that matter are the top two. **Fifteen American carriers against two
#: Soviet** -- the Moskvas, which carried helicopters and were built to hunt Polaris boats
#: rather than to strike a coast. **Eight Soviet cruisers and twelve boats against eight and
#: nine** -- a navy built to deny an ocean rather than to command one. Britain has two
#: carriers and is about to have none; France has Clemenceau and Foch; nobody else has any.
FLEETS: dict[str, tuple[int, ...]] = {
    "usa": (15, 8, 9, 4),
    "sov": (2, 8, 12, 3),
    "gbr": (2, 3, 3, 2),
    "fra": (2, 2, 2, 1),
    "jpn": (0, 3, 1, 1),
    "chn": (0, 1, 4, 1),
    "ita": (0, 2, 1, 1),
    "wger": (0, 1, 2, 1),
    "tur": (0, 1, 1, 1),
    "kor": (0, 1, 0, 1),
    "isr": (0, 1, 1, 0),
    "egy": (0, 1, 1, 1),
    "pol": (0, 0, 1, 1),
    "eger": (0, 0, 1, 0),
    "prk": (0, 0, 2, 0),
    "cub": (0, 0, 1, 0),
    "rou": (0, 0, 0, 1),
    "rvn": (0, 0, 0, 1),
}


#: Who is armed, as ``(warheads, delivery tier)``. Six powers, which is the real number if
#: Israel is counted and five if it is not.
#:
#: **Counts are scaled the way Modern Day scales them** -- roughly one per fifty real
#: warheads, with the deployed-versus-stockpiled distinction folded in. Note that this puts
#: the 1972 superpowers *above* the 2026 ones, which is correct: the peak is 1986, and a
#: player moving between eras should feel that the world used to hold more of these.
#:
#: **The tier is about surviving a first strike, not about airframes**, and Britain is the
#: hard case. Its 1972 deterrent is four Resolution-class boats and nothing else: the
#: V-force handed the role to Polaris in 1969, and there was never a British land-based
#: missile. It was seeded ``triad`` on the strength of *survivability* -- a boat on patrol
#: cannot be caught -- and Codex was right that this is dishonest, because ``triad`` in
#: :data:`atlas.engine.nuclear.DELIVERY_TIERS` means "aircraft, land-based missiles and
#: boats at sea" and is read to the player in exactly those words.
#:
#: It is ``sea`` -- the tier added at schema 19 for exactly this force. Four Resolution
#: boats, nothing else, and the numbers say so: survival above ``missile`` because a boat
#: on patrol cannot be caught, below ``triad`` because there is no second basket if the
#: boats are found. The paragraph below is what it read before that tier existed, and is
#: kept because the reasoning is why the tier was built.
#:
#: It was ``missile`` for one commit, and the argument was mechanical rather than cosmetic.
#: :func:`~atlas.engine.nuclear.survivability` is *tier plus boats*: at ``triad`` Britain
#: sits at 0.80 before a single submarine is counted and reaches the 0.95 ceiling whether
#: or not it has a navy, so **its deterrent would be insensitive to its own boats**. At
#: ``missile`` it is 0.45 plus three boats -- 0.60, with the submarines doing the work, and
#: an anti-submarine campaign against them actually weakening it. That is the right
#: mechanism for a submarine-only force, in the vocabulary that exists. The vocabulary
#: itself is the thing that should change; see the note on it in the review handoff.
#:
#: France is ``missile`` and means it: Mirage IVs and the Plateau d'Albion silos, with the
#: first boat commissioned three weeks ago and not yet a deterrent. China is ``aircraft``,
#: which is the entire reason Beijing spent 1969-72 looking for a third position: it is
#: armed, and it could be disarmed in an afternoon.
#:
#: Israel is seeded at one warhead, undeclared. Modern Day already makes this call and this
#: is the same one; a scenario that left it out would be asserting the opposite just as
#: firmly.
#: How many of each power's submarines carry ballistic missiles.
#:
#: The fleet table gives a power a number of ``submarine`` hulls and nothing distinguishes
#: a boomer from an attack boat -- one kind per era, one template. These counts do, and they
#: are the difference between "Britain has three submarines" and "Britain's deterrent is
#: two boats and the third is a fleet submarine".
#:
#: Scaled with the fleet, so they mean the same thing the hull counts mean. The United
#: States had 41 Polaris and Poseidon boats against about sixty attack submarines in 1972,
#: which is where four of nine comes from; the Soviet Union was building Yankees fast and
#: had a very large conventional fleet underneath them.
#:
#: **China is nought of four, explicitly.** Its tier already denies it a sea leg, so this
#: changes nothing today -- and it is written down because the People's Republic had no
#: ballistic-missile submarine until 1981, and a scenario that only said so through a tier
#: would lose the fact the moment somebody edited the tier.
DETERRENT_BOATS: dict[str, int] = {
    "usa": 4,   # of 9 hulls: Polaris and Poseidon against the attack fleet
    "sov": 5,   # of 12: Yankees and Hotels, under a very large conventional fleet
    "gbr": 2,   # of 3: the Resolutions are the whole deterrent
    "fra": 1,   # of 2: Le Redoutable, three weeks old, and the tier says `missile` anyway
    "chn": 0,   # of 4: no Chinese SSBN went to sea until 1981
    "isr": 0,   # of 1
}


ARSENALS: dict[str, tuple[int, str]] = {
    "usa": (46, "triad"),
    "sov": (38, "triad"),
    "gbr": (4, "sea"),
    "fra": (3, "missile"),
    "chn": (2, "aircraft"),
    "isr": (1, "aircraft"),
}


#: International bodies: ``(id, name, kind, charter, members, permanent members)``.
#:
#: **One body, and the blocs are deliberately not in it.** NATO and the Warsaw Pact are
#: seeded as ``defensive_pact`` commitments, whose Article 5 the engine reads directly; a
#: bloc that also convened would have an attack on a member hand every other member a casus
#: belli twice over. The Security Council is the flagship because it has what a pact does
#: not: a veto, and jurisdiction over powers who are not its friends.
#:
#: **The membership is the Council's, not the United Nations'.** Fifteen seats: five
#: permanent and ten elected for two-year terms, which is what the body is and what decides
#: a vote. Neither Germany joins until September 1973;
#: neither Korea nor either Vietnam until later still. So the Council's reach into three of
#: this era's four flashpoints is indirect -- it can act on the powers behind them and not
#: on the states doing the fighting -- which is exactly the position it was in.
#:
#: The five permanent members are the real ones, and this is the first year in which they
#: are also exactly the five *declared* nuclear powers. Israel is the sixth armed power on
#: this map and holds no seat, which is the whole weight the word declared is carrying. Note what falls out of that without a
#: line of code: **the Council is structurally unable to act against either superpower**,
#: because each can veto a resolution against itself.
ORGANISATIONS: tuple[
    tuple[str, str, str, str, tuple[str, ...], tuple[str, ...]], ...
] = (
    (
        "unsc", "the United Nations Security Council", "universal",
        "Charter of the United Nations. The Council bears primary responsibility for the "
        "maintenance of international peace and security. Its permanent members may veto "
        "any substantive resolution, including one against themselves.",
        # **Fifteen: the five permanent and the ten actually elected for 1972.** The first
        # version seeded fourteen members chosen by which powers the scenario had designed,
        # which is not how the Council is composed and is not who was on it -- Turkey,
        # Poland, Czechoslovakia, Romania, Cuba, Israel and Egypt all had a vote they did
        # not have. `_support` scores every member, so the room was wrong by seven.
        #
        # Serving 1971-72: Argentina, Belgium, Italy, Japan, Somalia.
        # Serving 1972-73: Guinea, India, Panama, Sudan, Yugoslavia.
        ("usa", "sov", "gbr", "fra", "chn",
         "m_arg", "m_bel", "ita", "jpn", "m_som",
         "m_gin", "m_ind", "m_pan", "m_sdn", "m_yug"),
        ("usa", "sov", "gbr", "fra", "chn"),
    ),
)


#: Re-priced for a 1972 treasury and re-shaped for what this era's war rewards. A carrier is
#: the most expensive thing any state builds and takes the best part of four years; a
#: nuclear boat is not far behind and is the cheapest way to make an ocean dangerous; an
#: air wing costs more than an armoured division and decides more.
#:
#: Build times are in *days*, so they mean the same thing whether the player is jumping a
#: week or a year at a time.
UNIT_TEMPLATES: tuple[UnitTemplateSeed, ...] = (
    UnitTemplateSeed("infantry", domain="land", cost_treasury=8, cost_manpower=8,
                     base_strength=4.2, move_range=1, build_days=BUILD_DAYS["infantry"]),
    UnitTemplateSeed("armour", domain="land", cost_treasury=18, cost_manpower=5,
                     base_strength=7.0, move_range=2, build_days=BUILD_DAYS["armour"]),
    UnitTemplateSeed("air_wing", domain="air", cost_treasury=30, cost_manpower=3,
                     base_strength=7.0, move_range=3, build_days=75.0),
    UnitTemplateSeed("cruiser", domain="sea", cost_treasury=40, cost_manpower=3,
                     base_strength=9.0, move_range=3, build_days=150.0),
    UnitTemplateSeed("carrier", domain="sea", cost_treasury=120, cost_manpower=7,
                     base_strength=11.0, move_range=3, build_days=BUILD_DAYS["carrier"]),
    UnitTemplateSeed("submarine", domain="sea", cost_treasury=52, cost_manpower=2,
                     base_strength=8.0, move_range=3, build_days=200.0),
    UnitTemplateSeed("transport", domain="sea", cost_treasury=24, cost_manpower=2,
                     base_strength=2.5, move_range=3, build_days=BUILD_DAYS["transport"]),
)


#: Directed relations on 1 January 1972, as ``(from, to, score, treaty, at_war)``.
#: Directed because the feeling is rarely mutual: Hanoi's regard for Beijing is not
#: Beijing's for Hanoi, and Bonn's for Washington is warmer than the reverse.
RELATIONS: tuple[tuple[str, str, float, TreatyStatus, bool], ...] = (
    # The central antagonism. Hostile, in negotiation, and neither of those is a pose.
    ("usa", "sov", -70, TreatyStatus.NONE, False),
    ("sov", "usa", -68, TreatyStatus.NONE, False),

    # The split that reorganised the world, and the opening that followed it.
    ("chn", "sov", -85, TreatyStatus.NONE, False),
    ("sov", "chn", -80, TreatyStatus.NONE, False),
    ("chn", "usa", -30, TreatyStatus.NONE, False),
    ("usa", "chn", -20, TreatyStatus.NONE, False),

    # NATO. France is in the treaty and out of the command, and the numbers say so.
    ("usa", "gbr", 88, TreatyStatus.DEFENSIVE, False),
    ("gbr", "usa", 90, TreatyStatus.DEFENSIVE, False),
    ("usa", "wger", 78, TreatyStatus.DEFENSIVE, False),
    ("wger", "usa", 84, TreatyStatus.DEFENSIVE, False),
    ("usa", "fra", 35, TreatyStatus.DEFENSIVE, False),
    ("fra", "usa", 22, TreatyStatus.DEFENSIVE, False),
    ("usa", "ita", 66, TreatyStatus.DEFENSIVE, False),
    ("ita", "usa", 70, TreatyStatus.DEFENSIVE, False),
    ("usa", "tur", 60, TreatyStatus.DEFENSIVE, False),
    ("tur", "usa", 38, TreatyStatus.DEFENSIVE, False),
    ("gbr", "fra", 40, TreatyStatus.DEFENSIVE, False),
    ("fra", "gbr", 30, TreatyStatus.DEFENSIVE, False),
    ("wger", "fra", 70, TreatyStatus.DEFENSIVE, False),
    ("fra", "wger", 66, TreatyStatus.DEFENSIVE, False),
    ("wger", "gbr", 58, TreatyStatus.DEFENSIVE, False),
    ("gbr", "wger", 52, TreatyStatus.DEFENSIVE, False),
    ("wger", "ita", 60, TreatyStatus.DEFENSIVE, False),
    ("ita", "wger", 58, TreatyStatus.DEFENSIVE, False),

    # The Warsaw Pact, and the member that will not take orders.
    ("sov", "eger", 82, TreatyStatus.DEFENSIVE, False),
    ("eger", "sov", 90, TreatyStatus.DEFENSIVE, False),
    ("sov", "pol", 74, TreatyStatus.DEFENSIVE, False),
    ("pol", "sov", 62, TreatyStatus.DEFENSIVE, False),
    ("sov", "cze", 70, TreatyStatus.DEFENSIVE, False),
    ("cze", "sov", 48, TreatyStatus.DEFENSIVE, False),
    ("sov", "rou", 30, TreatyStatus.DEFENSIVE, False),
    ("rou", "sov", 18, TreatyStatus.DEFENSIVE, False),
    ("eger", "pol", 55, TreatyStatus.DEFENSIVE, False),
    ("pol", "eger", 50, TreatyStatus.DEFENSIVE, False),
    ("eger", "cze", 52, TreatyStatus.DEFENSIVE, False),
    ("cze", "eger", 50, TreatyStatus.DEFENSIVE, False),
    ("pol", "cze", 48, TreatyStatus.DEFENSIVE, False),
    ("cze", "pol", 40, TreatyStatus.DEFENSIVE, False),
    ("rou", "chn", 55, TreatyStatus.TRADE, False),
    ("chn", "rou", 50, TreatyStatus.TRADE, False),
    ("rou", "isr", 30, TreatyStatus.TRADE, False),

    # The inner German border: two states that do not recognise each other, negotiating.
    ("wger", "eger", -55, TreatyStatus.NONE, False),
    ("eger", "wger", -70, TreatyStatus.NONE, False),
    # **`NONE`, because the eastern treaties are not in force yet.** They are signed and
    # `pending` in TREATIES below and they bind nobody until 3 June 1972. A scenario is
    # authoritative about `treaty_status` and `_reconcile_seeded_treaties` only ever raises
    # it, so asserting `NON_AGGRESSION` here would have quietly given Bonn and Moscow a
    # standing pact the commitment table says they do not have -- the same
    # column-disagrees-with-the-paperwork shape the phase-2 handoff flags in Modern Day.
    ("wger", "sov", -35, TreatyStatus.NONE, False),
    ("sov", "wger", -40, TreatyStatus.NONE, False),
    ("wger", "pol", -20, TreatyStatus.NONE, False),
    ("pol", "wger", -35, TreatyStatus.NONE, False),
    ("eger", "usa", -72, TreatyStatus.NONE, False),
    ("usa", "eger", -60, TreatyStatus.NONE, False),
    ("gbr", "sov", -65, TreatyStatus.NONE, False),
    ("sov", "gbr", -60, TreatyStatus.NONE, False),
    ("fra", "sov", -30, TreatyStatus.NONE, False),
    ("sov", "fra", -20, TreatyStatus.NONE, False),
    ("ita", "sov", -50, TreatyStatus.NONE, False),
    ("tur", "sov", -62, TreatyStatus.NONE, False),
    ("sov", "tur", -55, TreatyStatus.NONE, False),

    # Indochina: the war, and the two patrons who will not let each other own it.
    ("dvn", "rvn", -95, TreatyStatus.NONE, True),
    ("rvn", "dvn", -95, TreatyStatus.NONE, True),
    ("dvn", "usa", -95, TreatyStatus.NONE, True),
    ("usa", "dvn", -85, TreatyStatus.NONE, True),
    ("rvn", "usa", 70, TreatyStatus.ALLIANCE, False),
    ("usa", "rvn", 55, TreatyStatus.ALLIANCE, False),
    ("dvn", "sov", 60, TreatyStatus.ALLIANCE, False),
    ("sov", "dvn", 55, TreatyStatus.ALLIANCE, False),
    ("dvn", "chn", 45, TreatyStatus.ALLIANCE, False),
    ("chn", "dvn", 62, TreatyStatus.ALLIANCE, False),

    # Korea: an armistice, two patrons, and the first talks in twenty years.
    ("prk", "kor", -80, TreatyStatus.NONE, False),
    ("kor", "prk", -78, TreatyStatus.NONE, False),
    ("prk", "usa", -88, TreatyStatus.NONE, False),
    ("usa", "prk", -70, TreatyStatus.NONE, False),
    ("kor", "usa", 78, TreatyStatus.DEFENSIVE, False),
    ("usa", "kor", 68, TreatyStatus.DEFENSIVE, False),
    ("prk", "sov", 45, TreatyStatus.DEFENSIVE, False),
    ("sov", "prk", 40, TreatyStatus.DEFENSIVE, False),
    ("prk", "chn", 55, TreatyStatus.DEFENSIVE, False),
    ("chn", "prk", 58, TreatyStatus.DEFENSIVE, False),
    ("kor", "jpn", -25, TreatyStatus.TRADE, False),
    ("jpn", "kor", 20, TreatyStatus.TRADE, False),

    # The western Pacific: an alliance nobody in Tokyo was consulted about.
    ("usa", "jpn", 74, TreatyStatus.DEFENSIVE, False),
    ("jpn", "usa", 62, TreatyStatus.DEFENSIVE, False),
    ("jpn", "chn", -20, TreatyStatus.NONE, False),
    ("chn", "jpn", -35, TreatyStatus.NONE, False),
    ("jpn", "sov", -45, TreatyStatus.NONE, False),
    ("sov", "jpn", -30, TreatyStatus.NONE, False),

    # The Caribbean: an embargo that has changed nothing and a subsidy that has.
    ("usa", "cub", -85, TreatyStatus.NONE, False),
    ("cub", "usa", -90, TreatyStatus.NONE, False),
    ("cub", "sov", 78, TreatyStatus.TRADE, False),
    ("sov", "cub", 62, TreatyStatus.TRADE, False),
    ("cub", "chn", 15, TreatyStatus.NONE, False),

    # The Middle East: a ceasefire, an occupation, and an ally running out of patience.
    ("egy", "isr", -92, TreatyStatus.NONE, False),
    ("isr", "egy", -85, TreatyStatus.NONE, False),
    ("egy", "sov", 45, TreatyStatus.TRADE, False),
    ("sov", "egy", 50, TreatyStatus.TRADE, False),
    ("egy", "usa", -55, TreatyStatus.NONE, False),
    ("usa", "egy", -40, TreatyStatus.NONE, False),
    ("isr", "usa", 82, TreatyStatus.TRADE, False),
    ("usa", "isr", 68, TreatyStatus.TRADE, False),
    ("isr", "fra", -45, TreatyStatus.NONE, False),
    ("fra", "isr", -25, TreatyStatus.NONE, False),
    ("isr", "sov", -80, TreatyStatus.NONE, False),
    ("sov", "isr", -70, TreatyStatus.NONE, False),
    ("egy", "gbr", -35, TreatyStatus.NONE, False),

    # Europe's own arrangement, which Britain joins in twelve months.
    ("wger", "jpn", 40, TreatyStatus.TRADE, False),
    ("jpn", "wger", 42, TreatyStatus.TRADE, False),
    ("gbr", "ita", 45, TreatyStatus.DEFENSIVE, False),
    ("ita", "gbr", 42, TreatyStatus.DEFENSIVE, False),
    ("fra", "ita", 52, TreatyStatus.DEFENSIVE, False),
    ("ita", "fra", 50, TreatyStatus.DEFENSIVE, False),
    ("tur", "ita", 20, TreatyStatus.DEFENSIVE, False),
    ("tur", "gbr", 25, TreatyStatus.DEFENSIVE, False),
)


#: Standing commitments. Every one is either honoured or broken during play, and breaking
#: one is a legitimacy event -- which is the whole reason a treaty is a row and not a
#: sentence in a briefing.
#:
#: **The two blocs are the first two entries and they are the era.** An attack on any member
#: of either draws the rest in, through the same `defensive_pact` machinery Modern Day's
#: NATO uses, with no scenario-specific code anywhere.
#:
#: **Three of these are `pending`, and that is a date rather than a doubt.** The Moscow
#: Treaty, the Warsaw Treaty and the Four Power Agreement on Berlin were all *signed* when
#: this world begins and none of them was in force: all three entered into force on 3 June
#: 1972, after the Bundestag ratification fight that nearly brought Brandt down. `pending`
#: is exactly what the engine means by a document that exists and does not yet bind, which
#: is the same status SALT carries and the same one 1936 gives Locarno. Seeding them
#: `active` was a real error and Codex found it.
#:
#: **The Four Power Agreement is `military_access` on purpose.** It is a guarantee of the
#: Western presence in Berlin, not a pact between its signatories -- and reading it as one
#: would put the United States and the Soviet Union in a non-aggression pact, which would
#: make the central antagonism of the era unfightable. The same reasoning, and the same
#: kind, that 1936 uses for Locarno.
#:
#: **The Sino-Soviet Treaty of Friendship is deliberately absent.** It was signed in 1950,
#: does not expire until 1980, and by 1972 is a dead letter with a million troops on the
#: frontier it covers. Seeding it would raise `treaty_status` to `alliance` between two
#: powers that shot at each other three years ago. It is in the history instead, which is
#: where a document nobody intends to honour belongs.
TREATIES: tuple[tuple[CommitmentKind, str, str, tuple[str, ...]], ...] = (
    (CommitmentKind.DEFENSIVE_PACT, "active",
     "North Atlantic Treaty, Article 5: an attack on one member is an attack on all. "
     "France withdrew from the integrated military command in 1966 and never left the "
     "treaty, which is a distinction Paris considers the whole point. Fifteen members in "
     "1972; the nine that are minors on this map are parties exactly as the six powers "
     "are, because an alliance that forgets its small members is not an alliance.",
     ("usa", "gbr", "fra", "wger", "ita", "tur",
      # **The members who are minors on this map are still members.** Leaving them out
      # meant Belgium could be attacked and the alliance owed it nothing:
      # `defensive_pact_allies` reads `commitment_nation` and does not care whether a party
      # is a designed power or a one-region minor. Greece and Turkey acceded in 1952, the
      # Federal Republic in 1955, and all fifteen are here.
      "m_bel", "m_can", "m_dnk", "m_grc", "m_isl", "m_lux", "m_nld", "m_nor", "m_prt")),
    (CommitmentKind.DEFENSIVE_PACT, "active",
     "Warsaw Treaty of Friendship, Co-operation and Mutual Assistance (1955): an armed "
     "attack on one member is an attack on all. Its only operation to date was against a "
     "member. Seven parties: Albania stopped participating after the invasion of "
     "Czechoslovakia in 1968 and formally left in 1968, so it is not one of them.",
     ("sov", "eger", "pol", "cze", "rou", "m_bgr", "m_hun")),
    (CommitmentKind.DEFENSIVE_PACT, "active",
     "Treaty of Mutual Cooperation and Security between the United States and Japan "
     "(1960). American forces are based on Japanese soil; Okinawa returns in May.",
     ("usa", "jpn")),
    (CommitmentKind.DEFENSIVE_PACT, "active",
     "United States-Republic of Korea Mutual Defense Treaty (1953). Two American "
     "divisions remain on the peninsula.",
     ("usa", "kor")),
    (CommitmentKind.DEFENSIVE_PACT, "active",
     "Treaty of Friendship, Co-operation and Mutual Assistance between China and the "
     "Democratic People's Republic of Korea (1961). Automatic, and Beijing dislikes "
     "discussing it.",
     ("chn", "prk")),
    (CommitmentKind.DEFENSIVE_PACT, "active",
     "Treaty of Friendship, Co-operation and Mutual Assistance between the Soviet Union "
     "and the Democratic People's Republic of Korea (1961). Pyongyang holds both and "
     "honours neither exclusively.",
     ("sov", "prk")),
    (CommitmentKind.ALLIANCE, "active",
     "The American commitment to the Republic of Vietnam, under the Southeast Asia "
     "Treaty and a decade of assurances. The ground troops are leaving on a published "
     "schedule; the aircraft, the money and the assurances are not.",
     ("usa", "rvn")),
    (CommitmentKind.ALLIANCE, "active",
     "Soviet military and economic assistance to the Democratic Republic of Vietnam, "
     "renewed annually. The missiles, the radars and the tanks are Soviet.",
     ("sov", "dvn")),
    (CommitmentKind.ALLIANCE, "active",
     "Chinese assistance to the Democratic Republic of Vietnam: engineer troops on the "
     "northern railways, rice, and small arms. The older relationship, and the one Hanoi "
     "resents more.",
     ("chn", "dvn")),
    (CommitmentKind.NON_AGGRESSION_PACT, "pending",
     "Treaty of Moscow (1970): the Federal Republic and the Soviet Union renounce the use "
     "of force and accept the existing frontiers as inviolable. Ratification is still "
     "being fought over in Bonn. Signed, ratified by nobody, and not yet in force.",
     ("wger", "sov")),
    (CommitmentKind.NON_AGGRESSION_PACT, "pending",
     "Treaty of Warsaw (1970): the Federal Republic accepts the Oder-Neisse line. The "
     "chancellor knelt at the ghetto memorial and half his country has not forgiven "
     "him. Signed, and awaiting the same ratification fight.",
     ("wger", "pol")),
    (CommitmentKind.MILITARY_ACCESS, "pending",
     "Four Power Agreement on Berlin (1971): the three Western powers keep their garrison "
     "and their access to the city, and the Soviet Union guarantees the transit routes. "
     "Signed, and never yet tested.",
     ("usa", "gbr", "fra", "sov")),
    (CommitmentKind.CEASEFIRE, "active",
     "Korean Armistice Agreement (1953). A ceasefire, not a peace: no political settlement "
     "was ever reached and both armies stand where they stopped.",
     ("prk", "kor")),
    (CommitmentKind.CEASEFIRE, "active",
     "The Rogers ceasefire (August 1970) ended the War of Attrition along the Suez Canal. "
     "It has held for seventeen months and neither army has moved back from the water.",
     ("egy", "isr")),
    (CommitmentKind.TRADE_AGREEMENT, "active",
     "The Council for Mutual Economic Assistance: a bloc economy in which prices are "
     "negotiated rather than discovered, and the Soviet Union sells oil below the world "
     "price to allies who have nowhere else to buy it.",
     ("sov", "eger", "pol", "cze", "rou", "m_bgr", "m_hun")),
    (CommitmentKind.TRADE_AGREEMENT, "active",
     "The Soviet-Cuban sugar and oil protocols: Moscow buys the crop above the world price "
     "and sells the fuel below it. Without them the embargo would have worked.",
     ("sov", "cub")),
    (CommitmentKind.TRADE_AGREEMENT, "active",
     "The European Communities. Britain, Ireland and Denmark accede on 1 January 1973, "
     "twelve months from now, having been vetoed twice by a France that has changed its "
     "mind.",
     ("wger", "fra", "ita")),
)


#: Engine-authored history the world begins with. Diffs must cite a cause, so a world with
#: no past would have nothing legal to point at; these give the causal graph a real root.
HISTORY: tuple[tuple[EventKind, str, str, Visibility], ...] = (
    (EventKind.TREATY_SIGNED, "usa",
     "The United States and the Soviet Union have been negotiating limits on strategic "
     "arms since 1969. Both sides now hold enough warheads to destroy the other after "
     "being struck first, and both have concluded that defending against the other's "
     "missiles would only make the other build more of them.", Visibility.PUBLIC),
    (EventKind.ALLIANCE_BROKEN, "chn",
     "The alliance between the Soviet Union and the People's Republic collapsed over the "
     "course of the 1960s and became gunfire on the Ussuri in 1969. The 1950 treaty of "
     "friendship does not expire until 1980 and neither side believes a word of it.",
     Visibility.PUBLIC),
    (EventKind.DIPLOMATIC_MESSAGE, "usa",
     "The American President will visit Beijing in February, an announcement that reached "
     "Tokyo through the press. The People's Republic took the Chinese seat on the Security "
     "Council in October and the Republic of China left the United Nations rather than "
     "accept a second one.", Visibility.PUBLIC),
    # **The `war_declared` event of this pair is Hanoi's, and which of the two carries that
    # kind is a mechanical decision as well as an authorial one.** `organisation.
    # triggers_this_turn` reads turn-0 `war_declared` events to find the wars that were
    # already running when the world began, and names their *actor* as the power a body
    # would sit about. Filing the American withdrawal as the declaration would put the
    # United States in front of the Security Council for a war it is leaving, which is a
    # claim this scenario does not want to make by accident.
    (EventKind.WAR_DECLARED, "dvn",
     "The Democratic Republic has pursued unification by force since the partition of "
     "1954, through an insurgency in the south and then through its own army. Neither "
     "Vietnamese government accepts that the other is a state.", Visibility.PUBLIC),
    (EventKind.DIPLOMATIC_MESSAGE, "usa",
     "The American ground commitment in Vietnam peaked at half a million men in 1969 and "
     "is being withdrawn on a published schedule. The bombing has not stopped, the southern "
     "army has been equipped to replace the troops leaving, and nobody in Saigon believes "
     "the arithmetic works.", Visibility.PUBLIC),
    (EventKind.CONSTRUCTION, "eger",
     "The German Democratic Republic closed the Berlin sector boundary in August 1961 and "
     "built a wall along it. Emigration stopped, the state survived, and the city has been "
     "the place where a European war would start ever since.", Visibility.PUBLIC),
    (EventKind.TREATY_SIGNED, "wger",
     "The Federal Republic signed treaties with Moscow and Warsaw in 1970 renouncing force "
     "and accepting the eastern frontiers, and the four occupying powers guaranteed access "
     "to Berlin in 1971. The opposition calls it capitulation and has not been able to stop "
     "it.", Visibility.PUBLIC),
    (EventKind.WAR_DECLARED, "sov",
     "Warsaw Pact armies invaded Czechoslovakia in August 1968 and ended the reform "
     "programme there. Romania refused to take part. The doctrine that followed says the "
     "Soviet Union may intervene wherever socialism is threatened, and every capital in "
     "Eastern Europe has read it.", Visibility.PUBLIC),
    (EventKind.TERRITORY_TRANSFER, "isr",
     "Israel took the Sinai, the Gaza Strip, the West Bank, East Jerusalem and the Golan "
     "Heights in six days in June 1967. No Arab state has recognised any of it and Israel "
     "has offered to return most of it for a peace nobody will sign.", Visibility.PUBLIC),
    (EventKind.CEASEFIRE, "egy",
     "The War of Attrition along the Suez Canal ended in a ceasefire in August 1970. Egypt "
     "moved its air defences up to the water during it, which both sides understood as "
     "preparation rather than as peace.", Visibility.PUBLIC),
    (EventKind.CEASEFIRE, "prk",
     "The Korean War ended in an armistice in July 1953 and in no treaty at all. The "
     "demilitarised zone is the most heavily fortified border in the world and the two "
     "governments have just opened their first talks since it was drawn.",
     Visibility.PUBLIC),
    (EventKind.ECONOMIC_REFORM, "usa",
     "The United States suspended the convertibility of the dollar into gold in August "
     "1971, ending the monetary system it built in 1944, and imposed an import surcharge "
     "without consulting anybody. Its allies learned of both from the television.",
     Visibility.PUBLIC),
    (EventKind.CASUS_BELLI, "cub",
     "The United States has embargoed Cuba since 1962 and attempted to invade it in 1961. "
     "The missiles were withdrawn in exchange for a promise not to try again, and the "
     "island has been kept solvent by Soviet oil and Soviet sugar prices ever since.",
     Visibility.PUBLIC),
    (EventKind.UNIT_RAISED, "fra",
     "France tested a fission device in 1960 and a thermonuclear one in 1968, and left "
     "NATO's integrated command in between. The deterrent answers to the President of the "
     "Republic and to nobody in Brussels or Washington.", Visibility.PUBLIC),
    (EventKind.UNIT_RAISED, "chn",
     "The People's Republic tested a fission device in 1964 and a thermonuclear one in "
     "1967, and can deliver neither further than its own neighbourhood. Everyone concerned "
     "knows which of those two facts is the more important.", Visibility.PUBLIC),
)


#: What a state can work out how to do between here and the 1980s.
#:
#: **Same test as both shipped eras**: a programme has to move something shipped code
#: already reads -- a build time the yards consult, the base strength every delivered
#: formation is stamped with, the industry that sets the size of the queue, or a national
#: figure. Two of these move a lever read at *resolution* time instead, which the air layer
#: made possible and which is the honest implementation of what they actually do.
#:
#: The shape of the tree is this era's argument about what a war between the blocs would
#: be decided by, and it is a missile argument at every level: whether a warhead can be
#: intercepted, whether one missile can carry several, whether a boat can stay down long
#: enough to matter. The integrated circuit sits upstream of all of it, because in 1972 it
#: had just become the thing every guidance package is built out of.
TECHNOLOGIES: tuple[Technology, ...] = (
    Technology(
        id="integrated_circuits",
        name="Integrated circuits",
        blurb=(
            "A processor on a single piece of silicon, which arrived last November and "
            "which almost nobody has yet worked out the consequences of. Guidance packages "
            "that fitted in a room now fit in a nose cone."
        ),
        turns=6,
        cost_treasury=48.0,
        effects=(
            TechEffect("nation", "industry", 8.0),
            TechEffect("nation", "economy", 5.0),
        ),
    ),
    Technology(
        id="main_battle_tank",
        name="The main battle tank",
        blurb=(
            "One vehicle instead of three: composite armour, a stabilised gun that can be "
            "fired on the move, and night sights. The central front is flat, and this is "
            "what both sides intend to cross it with."
        ),
        turns=5,
        cost_treasury=40.0,
        effects=(
            TechEffect("unit_template", "base_strength", 2.5, "armour"),
            TechEffect("unit_template", "base_strength", 0.8, "infantry"),
        ),
    ),
    Technology(
        id="sam_network",
        name="Integrated surface-to-air missile belt",
        blurb=(
            "Batteries, acquisition radars and a control net that hands a target between "
            "them. It does not win a war; it decides whether the other side's air force "
            "can be used at all, which is the argument Hanoi's sky is settling this year."
        ),
        turns=6,
        cost_treasury=52.0,
        # **No completion effect, deliberately**, and the same shape as Modern Day's
        # `integrated_air_defence`. What it buys is an interception term read at the moment
        # a raid or a warhead arrives (`air.INTERCEPT_TECHS`), and nothing at all on
        # offence. Implementing it as a flat strength bump would be winning the war a
        # little, which is precisely what its own blurb says it does not do.
        effects=(),
    ),
    # **Re-described, and the effects are untouched.** The blurb was a silo: "a missile that
    # can sit fuelled for years and fly on a minute's notice". What the programme buys, and
    # has always bought, is `+2.5` to a cruiser and `+2.0` to a submarine -- an armament, not
    # a deterrent, and there is no strategic-missile mechanic in this engine for the old
    # prose to have been about. A player who read it and researched it got something else,
    # which is the strict definition of undiscoverable. Inventing the silo would have been
    # the other repair and is explicitly not this one's job.
    #
    # **Described rather than renamed, and the reason is a recorded prompt.** The research
    # catalogue a tracked power is briefed on carries each programme's *name* and its
    # effects, never its blurb -- so the blurb is read by the player alone and can be made
    # honest for free, while renaming this one would change 50 lines of the active cassette
    # (`scratchpad/cassettes/pt75-cold-war.jsonl`) and redden the release gate until
    # somebody re-records it against live models. That is a real cost and not one this
    # change has to spend: solid fuel is what put a ready-to-fire missile in a launch tube
    # at sea, so the name was never the lie. The blurb was.
    Technology(
        id="solid_fuel_missiles",
        name="Solid-fuel missiles",
        blurb=(
            "Propellant that sits in the tube for years instead of being pumped in while "
            "somebody watches you do it. In practice it is what lets a cruiser or a "
            "submarine carry a missile it can actually fire on notice: both of them fight "
            "harder, and neither gains any reach beyond the action it is already in. There "
            "is no silo here — this is the fleet's armament, not a deterrent."
        ),
        turns=6,
        cost_treasury=56.0,
        requires=("integrated_circuits",),
        effects=(
            TechEffect("unit_template", "base_strength", 2.5, "cruiser"),
            TechEffect("unit_template", "base_strength", 2.0, "submarine"),
        ),
    ),
    Technology(
        id="nuclear_propulsion",
        name="Nuclear propulsion at sea",
        blurb=(
            "A reactor instead of a bunker: a boat that need not surface and a carrier that "
            "need not be refuelled. It is the difference between a navy that visits an "
            "ocean and one that lives in it."
        ),
        turns=8,
        cost_treasury=72.0,
        effects=(
            TechEffect("unit_template", "base_strength", 2.5, "submarine"),
            TechEffect("unit_template", "build_days", -40.0, "submarine"),
            TechEffect("unit_template", "build_days", -60.0, "carrier"),
        ),
    ),
    # **Re-described, not renamed: the name is right and the blurb stopped halfway.** What
    # this buys is `air.PENETRATION_TECHS` -- what you send through somebody's air defence
    # gets through more often -- and that applies to a bomber's raid exactly as it applies
    # to a warhead, because the engine has one interception term and both are met by it.
    # The old blurb described the arsenal arithmetic and never said what the programme
    # actually does to a strike, so a power that researched it could not tell what it had
    # bought. The effects are untouched.
    Technology(
        id="mirv",
        name="Multiple independent re-entry vehicles",
        blurb=(
            "One missile, several warheads, each aimed somewhere else — an arsenal "
            "multiplied without building a single new rocket, which is why the agreement "
            "being negotiated this year counts launchers and cannot count these. What it "
            "buys you in the field is survivability on the way in: raids and warheads you "
            "send are harder to intercept, and the hulls that carry them are stronger."
        ),
        turns=9,
        cost_treasury=88.0,
        requires=("solid_fuel_missiles",),
        # The strength bump is the hull carrying them; the rest is `air.PENETRATION_TECHS`,
        # read when a strike or a warhead has to get through what the target has to stop it.
        effects=(
            TechEffect("nation", "military_strength", 8.0),
            TechEffect("unit_template", "base_strength", 1.5, "submarine"),
        ),
    ),
    Technology(
        id="thermonuclear_arsenal",
        name="Thermonuclear arsenal",
        blurb=(
            "Not a first device -- the powers who would pursue this have had those for "
            "years -- but the programme that turns a demonstration into a deterrent: "
            "yields that fit a warhead, a production line, and a test nobody admits to. "
            "Pursued out of sight, because the point of it is what other capitals believe."
        ),
        turns=13,
        cost_treasury=130.0,
        requires=("integrated_circuits", "mirv"),
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


#: Everything above, as the one object the seeder and the registry both read.
#: **What each power was built to do in 1972.**
#:
#: The entry worth arguing with is the American one, and it is right: **the draft did not end
#: until 1973**, so the United States at the start of 1972 is fighting a war with a conscript
#: army, and an all-volunteer force is a year away. Seeding this era from what America is
#: famous for rather than from what it was doing would get the wrong answer.
#:
#: France is on neither pole of the alignment axis and is left off it: it withdrew from the
#: integrated command in 1966 and stayed in the alliance, which is a third thing this axis
#: does not model and should not pretend to.
DOCTRINES: dict[str, dict[str, str]] = {
    "usa": {"mobilisation": "mass", "nuclear_posture": "retaliation",
            "alignment": "collective_security"},
    "sov": {"mobilisation": "mass", "nuclear_posture": "retaliation",
            "alignment": "collective_security"},
    # **All-volunteer since 1963, and Polaris.** Both halves of the textbook small-and-good
    # answer, and the reason a British deterrent of four boats deterred anybody at all.
    "gbr": {"mobilisation": "professional", "nuclear_posture": "minimum",
            "alignment": "collective_security"},
    # Conscription until 1996, and the force de frappe -- dissuasion du faible au fort is
    # the phrase minimum deterrence was invented to translate.
    "fra": {"mobilisation": "mass", "nuclear_posture": "minimum"},
    # People's War, and a no-first-use pledge made the day the first device went off.
    "chn": {"mobilisation": "mass", "nuclear_posture": "minimum"},
    # The definitional non-aligned power, which is what the word was coined for.
    #
    # **`m_ind`, not `ind`.** India is a minor in this era rather than one of its powers,
    # and a doctrine keyed by the wrong id is dropped in silence by the `living` filter in
    # `apply_politics`. Found by the auditor, which is the one thing that could have found
    # it: the scenario and the manifest disagreed, and the manifest was right about the
    # history and this file was wrong about the id.
    "m_ind": {"alignment": "neutrality"},
    "jpn": {"mobilisation": "professional"},
    "isr": {"mobilisation": "mass", "nuclear_posture": "minimum"},
    "egy": {"mobilisation": "mass"},
    "prk": {"mobilisation": "mass"},
    "kor": {"mobilisation": "mass"},
    "tur": {"alignment": "collective_security"},
    "pol": {"mobilisation": "mass", "alignment": "collective_security"},
}


SCENARIO = Scenario(
    id="cold-war",
    name="1972 - Cold War",
    start_date="1972-01-01",
    rng_seed=1972,
    default_world_id="cold1972",
    era=Era.COLD_WAR,
    powers=POWERS,
    armour=_ARMOUR,
    air=_AIR,
    ship_classes=SHIP_CLASSES,
    fleets=FLEETS,
    colonies=COLONIAL_HOLDINGS,
    sovereignty=SOVEREIGNTY,
    composites=COMPOSITES,
    absent=ABSENT,
    unit_templates=UNIT_TEMPLATES,
    technologies=TECHNOLOGIES,
    arsenals=ARSENALS,
    deterrent_boats=DETERRENT_BOATS,
    organisations=ORGANISATIONS,
    relations=RELATIONS,
    treaties=TREATIES,
    doctrines=DOCTRINES,
    history=HISTORY,
)


def cold_war_seed(
    pack: GeometryPack | PackSet,
    *,
    world_id: str = "cold1972",
    player: str | None = None,
    difficulty: Difficulty = Difficulty.NORMAL,
    seed: int = 1972,
    include_minors: bool = True,
) -> WorldSeed:
    """Build the 1 January 1972 world. See :func:`~atlas.scenarios.seeder.build_world`."""
    return build_world(
        SCENARIO, pack, world_id=world_id, player=player, difficulty=difficulty,
        seed=seed, include_minors=include_minors,
    )


def apply_1972_politics(conn, world_id: str) -> dict[str, int]:
    """Lay the real 1972 political state over a freshly created world."""
    return apply_politics(conn, world_id, SCENARIO)
