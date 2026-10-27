"""1936 - Gathering Storm. The political state of the world on 1 January 1936.

**The map is modern; the politics are not.** Natural Earth has no Czechoslovakia, no
Yugoslavia and no Soviet Union, so those powers are composed from their successor regions
-- Czechoslovakia holds Czechia and Slovakia, the USSR holds Russia, Ukraine, Belarus and
the Central Asian republics. This works because the engine already lets one nation hold
many regions, and it is honest about what it is: 1936 borders approximated by 1990s ones.
Interwar Poland and Germany in particular are noticeably the wrong shape. A hand-authored
period geometry would fix that; nothing else in the design has to change when it arrives.

**Everything here is a starting position, not a script.** Relations, treaties and force
levels are set to what they actually were at the start of 1936; what happens afterwards is
the simulation's business. The Rhineland is still demilitarised, Italy is bogged down in
Abyssinia, the Spanish Civil War has not begun, and the Anti-Comintern Pact does not exist
yet. All three are near, and none is inevitable.

**The historical events are engine-authored on purpose.** Every diff must cite a cause, so
a world that began with no history would have nothing legal to point at. Seeding Versailles,
Locarno, Manchuria and the rest gives the causal graph a real root -- and because they are
written with ``origin='ENGINE'``, a nation cannot forge a grievance and cite it next turn.

**This module is data.** Everything that reads geometry, spreads an army over ground or
writes rows lives in :mod:`atlas.scenarios.seeder`; what is here is the 1936 *position*,
and the last thirty lines hand it over. The split is what lets a second era be a second
data file rather than a second copy of six hundred lines.
"""

from __future__ import annotations

from ..map.pack import GeometryPack, PackSet
from ..state.bootstrap import WorldSeed
from ..state.models import CommitmentKind, Difficulty, EventKind, TreatyStatus, Visibility
from .era import Era
from .seeder import SEAGOING_UNIT_TEMPLATES, Power, Scenario, apply_politics, build_world

__all__ = [
    "ABSENT",
    "HISTORY",
    "POWERS",
    "SCENARIO",
    "SOVEREIGNTY",
    "RELATIONS",
    "TREATIES",
    "apply_1936_politics",
    "gathering_storm_seed",
]


#: Force levels are relative and deliberately coarse. What matters is the shape: France
#: has the larger standing army but is defensively minded, Germany is rearming from a low
#: base and climbing fast, the United States has an enormous economy behind a token army,
#: and the Soviet Union is huge and busy shooting its own officers.
POWERS: tuple[Power, ...] = (
    Power(
        id="ger", name="Germany", colour="#5A5A5A",
        regions=("DEU",), capital="DEU",
        character=(
            "Rearming openly and daring anyone to stop it. Believes the 1919 settlement was "
            "an injustice imposed by force and can be undone the same way, and reads every "
            "concession as evidence that the other powers will not fight."
        ),
        goals=(
            "overturn the Versailles settlement piece by piece",
            "remilitarise the Rhineland before France can react",
            "bring the German-speaking lands into the Reich",
            "avoid a war on two fronts until rearmament is complete",
        ),
        military=52, economy=66, stability=74, legitimacy=34, treasury=70, manpower=120,
    ),
    Power(
        id="fra", name="France", colour="#3B6EA5",
        regions=("FRA",), capital="FRA",
        character=(
            "Haunted by 1914 and determined never to fight on its own soil again. Has the "
            "larger army and the smaller appetite for using it, and will not move against "
            "Germany without Britain beside it."
        ),
        goals=(
            "keep Britain committed to the continent",
            "hold the eastern alliances together",
            "avoid another war on French soil at almost any cost",
        ),
        military=55, economy=54, stability=42, legitimacy=68, treasury=80, manpower=95,
    ),
    Power(
        id="gbr", name="United Kingdom", colour="#8C3B3B",
        regions=("GBR",), capital="GBR",
        character=(
            "Convinced that another European war would cost the Empire more than any "
            "settlement could gain, and that German grievances are partly legitimate. "
            "Hopes concession buys time to rearm, and is not yet sure what it is buying "
            "time for."
        ),
        goals=(
            "avoid a continental commitment for as long as possible",
            "preserve the Empire and the naval balance",
            "rearm quietly while negotiating loudly",
        ),
        military=46, economy=72, stability=76, legitimacy=74, treasury=110, manpower=90,
    ),
    Power(
        id="ita", name="Italy", colour="#4C8C5A",
        regions=("ITA",), capital="ITA",
        character=(
            "Chasing an empire in Abyssinia and finding it more expensive than advertised. "
            "Sanctioned by the League and resentful of the powers that imposed them, which "
            "is pushing it toward the one great power that did not."
        ),
        goals=(
            "finish the Abyssinian campaign and be recognised for it",
            "break the sanctions and the isolation they created",
            "be treated as a great power rather than a junior partner",
        ),
        military=41, economy=36, stability=62, legitimacy=28, treasury=45, manpower=80,
    ),
    Power(
        id="sov", name="Soviet Union", colour="#A5453B",
        regions=("RUS", "UKR", "BLR", "KAZ", "UZB", "TKM", "TJK", "KGZ", "GEO", "ARM", "AZE"),
        capital="RUS",
        character=(
            "Enormous, industrialising fast, and hollowing out its own officer corps. "
            "Distrusted by every capital in Europe and distrusting in return, it has signed "
            "pacts with France and Czechoslovakia that nobody quite believes will be honoured."
        ),
        goals=(
            "buy time to industrialise and rearm",
            "keep the capitalist powers divided among themselves",
            "avoid a two-front war with Germany and Japan",
        ),
        military=62, economy=58, stability=52, legitimacy=38, treasury=60, manpower=180,
    ),
    Power(
        id="jpn", name="Japan", colour="#B0625A",
        regions=("JPN", "KOR"), capital="JPN",
        character=(
            "Established in Manchuria and looking further south. The army increasingly "
            "makes policy the government then explains, and the League's disapproval has "
            "already been discounted."
        ),
        goals=(
            "consolidate the position on the Asian mainland",
            "secure oil, iron and rubber the home islands do not have",
            "keep the Soviet Union out of Manchuria",
        ),
        military=54, economy=42, stability=58, legitimacy=32, treasury=55, manpower=105,
    ),
    Power(
        id="usa", name="United States", colour="#4F7A9E",
        regions=("USA",), capital="USA",
        character=(
            "The largest economy in the world behind a small army and a neutrality law. "
            "Watching Europe with distaste and no intention of entering its quarrels."
        ),
        goals=(
            "stay out of European and Asian wars",
            "recover from the Depression",
            "keep trade open without taking sides",
        ),
        military=24, economy=96, stability=58, legitimacy=72, treasury=140, manpower=130,
    ),
    Power(
        id="pol", name="Poland", colour="#C0A050",
        regions=("POL",), capital="POL",
        character=(
            "Wedged between two powers that have partitioned it before and may again. Has a "
            "non-aggression pact with Germany and deep distrust of the Soviet Union, and "
            "refuses to let Soviet troops cross its territory on any pretext."
        ),
        goals=(
            "stay independent of both neighbours",
            "avoid becoming the corridor through which they fight",
            "keep the French alliance alive without inviting Soviet 'help'",
        ),
        military=31, economy=26, stability=54, legitimacy=56, treasury=35, manpower=70,
    ),
    Power(
        id="cze", name="Czechoslovakia", colour="#6FA8A0",
        regions=("CZE", "SVK"), capital="CZE",
        character=(
            "Well-armed, well-defended, industrially serious, and holding a German-speaking "
            "borderland that Berlin has begun to take an interest in. Its security rests "
            "entirely on treaties with France and the Soviet Union."
        ),
        goals=(
            "hold the frontier fortifications and the alliances behind them",
            "keep the Sudeten question an internal matter",
            "avoid standing alone",
        ),
        military=29, economy=33, stability=60, legitimacy=64, treasury=40, manpower=45,
    ),
    Power(
        id="esp", name="Spain", colour="#B58A4A",
        regions=("ESP",), capital="ESP",
        character=(
            "A republic pulling apart between left and right, with an army that increasingly "
            "regards the government as the problem. Everyone can see where this is going and "
            "nobody is willing to say so."
        ),
        goals=("hold the republic together", "keep the army out of politics"),
        military=22, economy=24, stability=22, legitimacy=44, treasury=25, manpower=55,
    ),
    Power(
        id="yug", name="Yugoslavia", colour="#7A9A6B",
        regions=("SRB", "HRV", "BIH", "SVN", "MNE", "MKD", "KOS"), capital="SRB",
        character=(
            "A kingdom of quarrelling nationalities held together by the crown and not much "
            "else. Aligned with France through the Little Entente and increasingly courted "
            "by German trade."
        ),
        goals=("hold the kingdom together", "avoid Italian pressure in the Adriatic"),
        military=24, economy=20, stability=40, legitimacy=52, treasury=25, manpower=50,
    ),
    Power(
        id="rou", name="Romania", colour="#9E8AB8",
        regions=("ROU", "MDA"), capital="ROU",
        character=(
            "Holding Transylvania against Hungarian revisionism and Bessarabia against "
            "Soviet claims, with oil that everyone wants and an army that cannot defend it "
            "alone."
        ),
        goals=("keep Transylvania", "keep Bessarabia", "sell oil to whoever protects it"),
        military=23, economy=22, stability=48, legitimacy=54, treasury=28, manpower=48,
    ),
    Power(
        id="hun", name="Hungary", colour="#A8705A",
        regions=("HUN",), capital="HUN",
        character=(
            "Stripped of two thirds of its territory at Trianon and unreconciled to it. "
            "Looking for any great power willing to help revise the settlement, and finding "
            "only one that is interested."
        ),
        goals=("revise Trianon", "recover the lost territories", "find a great-power patron"),
        military=18, economy=19, stability=52, legitimacy=44, treasury=22, manpower=32,
    ),
    Power(
        id="aut", name="Austria", colour="#C4B18A",
        regions=("AUT",), capital="AUT",
        character=(
            "Independent on paper and under steady pressure from Berlin in practice. Its "
            "protector Italy is distracted in Africa and drifting toward Germany, which "
            "leaves very little between Vienna and the Anschluss."
        ),
        goals=("remain independent", "keep Italian protection alive"),
        military=13, economy=18, stability=42, legitimacy=50, treasury=18, manpower=22,
    ),
    Power(
        id="tur", name="Turkey", colour="#8AA88A",
        regions=("TUR",), capital="TUR",
        character=(
            "Rebuilt as a secular republic and determined to stay out of other people's "
            "wars, while quietly rearming the Straits."
        ),
        goals=("neutrality", "control of the Straits", "modernise without foreign debt"),
        military=26, economy=20, stability=58, legitimacy=58, treasury=25, manpower=55,
    ),
    Power(
        id="chn", name="China", colour="#C08A5A",
        regions=("CHN", "MNG"), capital="CHN",
        character=(
            "Nominally unified under the Nationalists, actually divided between them, the "
            "communists, regional warlords and a Japanese army in the north. Fighting on "
            "several fronts at once, none of them by choice."
        ),
        goals=(
            "unify the country before Japan moves again",
            "delay a full war with Japan",
            "obtain arms from anyone who will sell them",
        ),
        military=28, economy=22, stability=26, legitimacy=48, treasury=20, manpower=200,
    ),
    Power(
        id="eth", name="Ethiopia", colour="#8AB88A",
        regions=("ETH",), capital="ETH",
        character=(
            "Invaded, appealing to a League of Nations that has voted sanctions and sent no "
            "soldiers. Fighting a modern army with courage and very little else."
        ),
        goals=("survive the invasion", "hold the highlands", "shame the League into acting"),
        military=8, economy=6, stability=30, legitimacy=70, treasury=8, manpower=40,
    ),
)

#: Promoted minors a great power held as a colony in 1936, as ``minor_id -> (owner,
#: divisions)``. A colony's ground and its garrison belong to the colonial power; no
#: independent nation is created for it, and its provinces begin as that power's own core
#: (an Eritrea held since 1890 is not restive ground the way a fresh conquest is).
#:
#: This is what makes the one starting war fightable: landlocked Ethiopia is met along a
#: *land* frontier from Italy's East-African colonies, rather than only across an ocean.
#: Eritrea (Badoglio's invasion base) and Italian Somaliland were Italian; British
#: Somaliland was a British protectorate and is British ground in :data:`SOVEREIGNTY`,
#: which is a change from the first build of this era -- it was an independent minor, and
#: it was not a country.
#: The division counts are a colonial army sized to actually prosecute the campaign against
#: Ethiopia's few divisions -- the East African theatre was Italy's main military effort of
#: 1935-36 -- without stripping the metropole's European order of battle.
COLONIAL_HOLDINGS: dict[str, tuple[str, int]] = {
    "m_eri": ("ita", 5),  # Eritrea: the main front, Addis Ababa taken from the north
    "m_som": ("ita", 3),  # Italian Somaliland: Graziani's secondary southern front
}


#: **Who the rest of the world was in 1936**, as ``(holder, display name, modern codes)``.
#:
#: The oldest era was the last to get this, and it needed it most. Without it the seeder
#: makes an independent state of every modern map entry no power claims -- which put
#: **Israel, Pakistan, Bangladesh, Zimbabwe, Namibia, Ghana, Indonesia, Malaysia,
#: Singapore, Algeria, Libya, Burma and Zaire** on the 1936 map as sovereign nations, along
#: with a hundred and thirty others, in a scenario whose own hard constraints say *do not
#: invent nations*. The scenario auditor found it; this is the same treatment the 1972
#: :data:`~atlas.scenarios.cold_war.SOVEREIGNTY` table gave the Cold War, and it uses the
#: same machinery.
#:
#: **1936 is the hardest year on the map for this**, because it is the high-water mark of
#: European empire: roughly a third of the world's land was somebody's dependency, and the
#: modern pack draws every one of those dependencies as a country. So this table is mostly
#: one kind of entry -- *this code was that power's ground* -- and the interesting decisions
#: are the ones where it is not.
#:
#: **A dependency is ground, not a nation.** The holder owns the region and pays no separate
#: garrison for it (see :func:`~atlas.scenarios.seeder.build_world`), which is right twice
#: over: a colony's defence *was* the metropole's army, and giving each of Britain's ninety
#: holdings a border guard would have charged London ninety formations of upkeep.
#:
#: **What stays independent, because it was.** The Dominions -- Canada, Australia, New
#: Zealand, South Africa, and the Irish Free State -- are self-governing states under a
#: shared crown, not colonies, and the Statute of Westminster was five years old. Iraq
#: (1932) and Egypt (1922) are independent with British treaties over them. Latin America,
#: Iran, Afghanistan, Saudi Arabia, Yemen, Nepal, Bhutan, Muscat and Oman, Liberia, Siam,
#: and every European neutral are their own states.
#:
#: **Judgement calls, flagged rather than buried:**
#:
#: * **Southern Rhodesia** is a British dependency here. It was a self-governing colony with
#:   its own parliament and army, and the 1972 table treats its successor as a state for
#:   exactly that reason -- but in 1936 London still held the reserved powers, and the
#:   alternative invents a nation.
#: * **Cameroon and Togo** were split into French and British mandates. One code each, and
#:   France held the larger share of both.
#: * **The New Hebrides** were an Anglo-French condominium; the engine has no shared
#:   sovereignty, and Britain gets them as it does in 1972.
#: * **Anglo-Egyptian Sudan** was also a condominium and is British here, which is what it
#:   was in practice.
#: * **Iceland** is left independent. It was a sovereign kingdom in personal union with
#:   Denmark, which handled its foreign affairs -- closer to a state than to a possession.
#: * **Mandatory Palestine is two codes**, ``PSX`` and ``ISR``, and both are British. There
#:   was no Israel in 1936 and this is the single entry the auditor most needed.
#: * **The South Seas Mandate** (the Marianas, Carolines, Marshalls and Palau) is Japanese,
#:   which is why the Pacific war has islands to fight over.
SOVEREIGNTY: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    # --- the British Empire at its greatest extent, and this is only the part the modern
    #     map draws as separate countries.
    ("gbr", "United Kingdom",
     (# British India, one raj: the codes are three future states and a fourth that was
      # separated from it in April 1937, fifteen months after this world begins.
      "IND", "PAK", "BGD", "MMR", "KAS", "LKA", "MDV",
      # the Far East and the Straits
      "MYS", "SGP", "BRN", "HKG",
      # Africa
      "NGA", "GHA", "SLE", "GMB", "KEN", "UGA", "TZA", "ZMB", "ZWE", "MWI", "BWA",
      "LSO", "SWZ", "SOL", "SDN", "SDS", "MUS", "SYC", "SHN",
      # the Middle East: two mandates and the Gulf protectorates
      "PSX", "ISR", "JOR", "KWT", "BHR", "QAT", "ARE",
      # the Mediterranean
      "MLT", "CYP", "CYN",
      # the Caribbean and the Atlantic
      "BHS", "JAM", "TTO", "BRB", "GRD", "DMA", "LCA", "VCT", "KNA", "ATG", "MSR",
      "AIA", "VGB", "CYM", "TCA", "BMU", "BLZ", "GUY", "FLK", "SGS",
      # the Pacific and the Indian Ocean
      "FJI", "TON", "SLB", "KIR", "TUV", "VUT", "PCN", "IOT", "IOA",
      # the crown dependencies, which are not part of the United Kingdom and never were
      "GGY", "JEY", "IMN")),

    # --- the French Empire, second largest and the one with three Mediterranean
    #     départements rather than colonies.
    ("fra", "France",
     ("DZA", "MAR", "TUN",
      "MRT", "MLI", "NER", "TCD", "SEN", "CIV", "BFA", "BEN", "GIN", "TGO", "CMR",
      "GAB", "COG", "CAF", "DJI", "MDG", "COM",
      "SYR", "LBN",                      # the Levant mandates
      "VNM", "LAO", "KHM",               # French Indochina
      "NCL", "PYF", "WLF", "SPM", "MAF", "BLM", "ATF")),

    # --- Italy's, beyond the two East African colonies the war is being fought from.
    ("ita", "Italy", ("LBY",)),

    # --- what Spain still held in Africa. Spanish Morocco has no separate code; the whole
    #     of Morocco is French here, which understates Spain and is the closest the map
    #     allows.
    ("esp", "Spain", ("SAH", "GNQ")),

    # --- the American possessions. The Philippines became a Commonwealth in 1935 with
    #     independence promised for 1946, so it is American ground with a date on it.
    ("usa", "United States", ("PHL", "PRI", "VIR", "GUM", "ASM")),

    # --- Japan's, on top of Korea and Taiwan. The South Seas Mandate is League territory
    #     administered by Japan and fortified against the terms of the mandate.
    ("jpn", "Japan", ("TWN", "PRK", "MNP", "FSM", "MHL", "PLW")),

    # --- the smaller European empires, each a minor holding far more than itself.
    ("m_prt", "Portugal", ("PRT", "AGO", "MOZ", "GNB", "CPV", "STP", "TLS", "MAC")),
    ("m_nld", "Netherlands", ("NLD", "IDN", "SUR", "ABW", "CUW", "SXM")),
    ("m_bel", "Belgium", ("BEL", "COD", "RWA", "BDI")),
    ("m_dnk", "Denmark", ("DNK", "GRL", "FRO")),

    # --- the Dominions, which are states and hold territory of their own.
    ("m_aus", "Australia", ("AUS", "PNG", "NRU", "NFK", "ATC", "HMD")),
    ("m_nzl", "New Zealand", ("NZL", "WSM", "COK", "NIU")),
    ("m_zaf", "South Africa", ("ZAF", "NAM")),
    ("m_irl", "Irish Free State", ("IRL",)),

    ("m_fin", "Finland", ("FIN", "ALD")),

    # --- the same ground, under the name it had. Only the states: a dependency's name
    #     belongs to its holder's entry, because it has no nation row to carry one.
    ("m_tha", "Siam", ("THA",)),         # renamed Thailand in 1939
)

#: Ground that is nobody's state, in every era this engine ships. Antarctica had five
#: overlapping claims in 1936, no permanent inhabitants and no government, and the treaty
#: that suspended the claims is twenty-five years away -- but "claimed by several and
#: governed by none" is much better modelled as ownerless ground than as a nation with a
#: treasury and a border guard on the ice. The 1972 table reached the same answer from the
#: treaty; the three eras now agree.
ABSENT: frozenset[str] = frozenset({"ATA"})

_power_by_id: dict[str, Power] = {power.id: power for power in POWERS}

#: Directed relations on 1 January 1936, as ``(from, to, score, treaty, at_war)``.
#: Directed because the feeling is rarely mutual: Austria's regard for Germany is not
#: Germany's regard for Austria, and Poland distrusts Moscow far more than the reverse.
RELATIONS: tuple[tuple[str, str, float, TreatyStatus, bool], ...] = (
    # Germany and the powers that might stop it.
    ("ger", "fra", -55, TreatyStatus.NONE, False),
    ("fra", "ger", -60, TreatyStatus.NONE, False),
    ("ger", "gbr", -15, TreatyStatus.NONE, False),
    ("gbr", "ger", -25, TreatyStatus.NONE, False),
    ("ger", "sov", -70, TreatyStatus.NONE, False),
    ("sov", "ger", -65, TreatyStatus.NONE, False),
    ("ger", "pol", 15, TreatyStatus.NON_AGGRESSION, False),
    ("pol", "ger", 10, TreatyStatus.NON_AGGRESSION, False),
    ("ger", "ita", 20, TreatyStatus.NONE, False),
    ("ita", "ger", 15, TreatyStatus.NONE, False),
    ("ger", "aut", -20, TreatyStatus.NONE, False),
    ("aut", "ger", -45, TreatyStatus.NONE, False),
    ("ger", "cze", -45, TreatyStatus.NONE, False),
    ("cze", "ger", -50, TreatyStatus.NONE, False),
    ("ger", "hun", 30, TreatyStatus.NONE, False),
    ("hun", "ger", 35, TreatyStatus.NONE, False),

    # The Anglo-French relationship: cordial, and each privately wishes the other were
    # braver.
    ("fra", "gbr", 45, TreatyStatus.NONE, False),
    ("gbr", "fra", 40, TreatyStatus.NONE, False),

    # France's eastern system.
    ("fra", "pol", 40, TreatyStatus.DEFENSIVE, False),
    ("pol", "fra", 35, TreatyStatus.DEFENSIVE, False),
    ("fra", "cze", 50, TreatyStatus.DEFENSIVE, False),
    ("cze", "fra", 55, TreatyStatus.DEFENSIVE, False),
    ("fra", "sov", 25, TreatyStatus.DEFENSIVE, False),
    ("sov", "fra", 20, TreatyStatus.DEFENSIVE, False),
    ("sov", "cze", 30, TreatyStatus.DEFENSIVE, False),
    ("cze", "sov", 25, TreatyStatus.DEFENSIVE, False),

    # Poland will not have Soviet troops on its soil under any circumstances.
    ("pol", "sov", -60, TreatyStatus.NON_AGGRESSION, False),
    ("sov", "pol", -40, TreatyStatus.NON_AGGRESSION, False),

    # Abyssinia, and the sanctions that followed.
    ("ita", "eth", -80, TreatyStatus.NONE, True),
    ("eth", "ita", -95, TreatyStatus.NONE, True),
    ("ita", "gbr", -45, TreatyStatus.NONE, False),
    ("gbr", "ita", -40, TreatyStatus.NONE, False),
    ("ita", "fra", -30, TreatyStatus.NONE, False),
    ("fra", "ita", -20, TreatyStatus.NONE, False),
    ("ita", "aut", 30, TreatyStatus.NONE, False),
    ("aut", "ita", 40, TreatyStatus.NONE, False),

    # The Little Entente, aimed squarely at Hungarian revisionism.
    ("cze", "rou", 45, TreatyStatus.DEFENSIVE, False),
    ("rou", "cze", 45, TreatyStatus.DEFENSIVE, False),
    ("cze", "yug", 45, TreatyStatus.DEFENSIVE, False),
    ("yug", "cze", 40, TreatyStatus.DEFENSIVE, False),
    ("rou", "yug", 40, TreatyStatus.DEFENSIVE, False),
    ("yug", "rou", 40, TreatyStatus.DEFENSIVE, False),
    ("hun", "rou", -55, TreatyStatus.NONE, False),
    ("rou", "hun", -50, TreatyStatus.NONE, False),
    ("hun", "cze", -50, TreatyStatus.NONE, False),
    ("cze", "hun", -40, TreatyStatus.NONE, False),
    ("hun", "yug", -40, TreatyStatus.NONE, False),
    ("yug", "hun", -35, TreatyStatus.NONE, False),
    ("rou", "sov", -45, TreatyStatus.NONE, False),
    ("sov", "rou", -30, TreatyStatus.NONE, False),
    ("ita", "yug", -30, TreatyStatus.NONE, False),
    ("yug", "ita", -35, TreatyStatus.NONE, False),

    # Asia.
    ("jpn", "chn", -70, TreatyStatus.NONE, False),
    ("chn", "jpn", -85, TreatyStatus.NONE, False),
    ("jpn", "sov", -55, TreatyStatus.NONE, False),
    ("sov", "jpn", -50, TreatyStatus.NONE, False),
    ("jpn", "usa", -30, TreatyStatus.NONE, False),
    ("usa", "jpn", -35, TreatyStatus.NONE, False),
    ("jpn", "gbr", -20, TreatyStatus.NONE, False),
    ("sov", "chn", 20, TreatyStatus.NONE, False),
    ("chn", "sov", 15, TreatyStatus.NONE, False),

    # The United States, at arm's length from all of it.
    ("usa", "gbr", 35, TreatyStatus.NONE, False),
    ("gbr", "usa", 30, TreatyStatus.NONE, False),
    ("usa", "fra", 25, TreatyStatus.NONE, False),
    ("usa", "ger", -25, TreatyStatus.NONE, False),
    ("usa", "sov", -30, TreatyStatus.NONE, False),
    ("usa", "ita", -25, TreatyStatus.NONE, False),

    # Spain, weeks away from an election and months from a war.
    ("esp", "fra", 20, TreatyStatus.NONE, False),
    ("esp", "ita", -15, TreatyStatus.NONE, False),
    ("esp", "ger", -15, TreatyStatus.NONE, False),

    # Turkey's careful neutrality.
    ("tur", "sov", 15, TreatyStatus.NONE, False),
    ("tur", "gbr", 20, TreatyStatus.NONE, False),
    ("tur", "ita", -35, TreatyStatus.NONE, False),
)

#: Treaties in force on 1 January 1936, as ``(kind, status, condition, parties)``.
TREATIES: tuple[tuple[CommitmentKind, str, str, tuple[str, ...]], ...] = (
    (CommitmentKind.NON_AGGRESSION_PACT, "active",
     "German-Polish Non-Aggression Pact (1934): ten years, renouncing force between them.",
     ("ger", "pol")),
    (CommitmentKind.DEFENSIVE_PACT, "active",
     "Franco-Soviet Treaty of Mutual Assistance (1935): each aids the other against "
     "unprovoked attack. Neither side entirely trusts it.",
     ("fra", "sov")),
    (CommitmentKind.DEFENSIVE_PACT, "active",
     "Soviet-Czechoslovak Treaty (1935): Soviet aid is conditional on France acting first.",
     ("sov", "cze")),
    (CommitmentKind.DEFENSIVE_PACT, "active",
     "Franco-Czechoslovak Alliance: France guarantees Czechoslovak security.",
     ("fra", "cze")),
    (CommitmentKind.DEFENSIVE_PACT, "active",
     "Franco-Polish Alliance: mutual assistance against German attack.",
     ("fra", "pol")),
    (CommitmentKind.DEFENSIVE_PACT, "active",
     "The Little Entente: Czechoslovakia, Romania and Yugoslavia against Hungarian revision.",
     ("cze", "rou", "yug")),
    (CommitmentKind.TRADE_AGREEMENT, "active",
     "Anglo-German Naval Agreement (1935): the German fleet limited to 35% of the Royal "
     "Navy. Concluded bilaterally, to French fury.",
     ("gbr", "ger")),
    (CommitmentKind.MILITARY_ACCESS, "pending",
     "Locarno (1925): the Rhineland is to remain demilitarised, guaranteed by Britain and "
     "Italy. Untested.",
     ("ger", "fra", "gbr", "ita")),
)

#: Engine-authored history the world begins with. Diffs must cite a cause, so a world with
#: no past would have nothing legal to point at; these give the causal graph a real root.
HISTORY: tuple[tuple[EventKind, str, str, Visibility], ...] = (
    (EventKind.TREATY_SIGNED, "ger",
     "The Treaty of Versailles (1919) stripped Germany of territory, colonies and armed "
     "forces, and assigned it sole guilt for the war. No German government has accepted it.",
     Visibility.PUBLIC),
    (EventKind.TREATY_SIGNED, "fra",
     "The Locarno treaties (1925) fixed Germany's western border and demilitarised the "
     "Rhineland, guaranteed by Britain and Italy.", Visibility.PUBLIC),
    (EventKind.WAR_DECLARED, "jpn",
     "Japan seized Manchuria (1931) and left the League of Nations rather than withdraw. "
     "The League's censure changed nothing.", Visibility.PUBLIC),
    (EventKind.ECONOMIC_REFORM, "ger",
     "Germany reintroduced conscription (March 1935) in open breach of Versailles. Britain, "
     "France and Italy protested at Stresa and did nothing further.", Visibility.PUBLIC),
    (EventKind.TREATY_SIGNED, "gbr",
     "The Anglo-German Naval Agreement (June 1935) let Germany build to 35% of British "
     "tonnage. Britain negotiated it alone, and the Stresa Front did not survive it.",
     Visibility.PUBLIC),
    (EventKind.TREATY_SIGNED, "fra",
     "France and the Soviet Union signed a treaty of mutual assistance (May 1935). Neither "
     "public is enthusiastic and the general staffs have not spoken.", Visibility.PUBLIC),
    (EventKind.WAR_DECLARED, "ita",
     "Italy invaded Abyssinia (October 1935). The League voted sanctions, omitted oil, and "
     "the campaign continues.", Visibility.PUBLIC),
    (EventKind.CASUS_BELLI, "ger",
     "German rearmament has passed the point where the western powers could reverse it "
     "cheaply. Berlin has noticed that every violation so far has been protested and "
     "accepted.", Visibility.PUBLIC),
)


#: The 1936 army and fleet are the engine's seagoing default set unchanged -- the costs
#: there were written against a 1936 treasury in the first place. An era whose ships are
#: priced differently defines its own; see
#: :data:`~atlas.scenarios.seeder.SEAGOING_UNIT_TEMPLATES`.
UNIT_TEMPLATES = SEAGOING_UNIT_TEMPLATES

#: The fleets that actually existed on 1 January 1936, as
#: ``(capital ships, carriers, sealift)``.
#:
#: Capital ships are battleships and battlecruisers in commission: the Royal Navy's
#: fifteen are the Queen Elizabeths, Revenges, Nelsons, Hood, Renown and Repulse; Japan's
#: ten are the Nagatos, Ises, Fusos and Kongos; Germany's three are the pocket
#: battleships, with Scharnhorst and Gneisenau still building. Carriers likewise: Britain
#: six, the United States four, Japan four, France one -- Bearn -- and nobody else any.
#:
#: The imbalance is the point and it is not a bug in the scenario. The Royal Navy in 1936
#: was larger than the next two fleets together, and a 1936 that does not say so is not
#: 1936. Sealift is the one figure with no order of battle behind it: it stands for
#: merchant tonnage a power could requisition, scaled from its economy and its history of
#: doing so.
FLEETS_1936: dict[str, tuple[int, int, int]] = {
    "gbr": (15, 6, 6),
    "usa": (15, 4, 5),
    "jpn": (10, 4, 4),
    "fra": (7, 1, 3),
    "ita": (4, 0, 2),
    "sov": (3, 0, 1),
    "ger": (3, 0, 2),
    "esp": (2, 0, 1),
    "tur": (1, 0, 1),
}

#: Powers whose 1936 armies were mechanising rather than merely large.
_ARMOUR = {"ger": 2, "sov": 2, "fra": 1, "gbr": 1, "usa": 1, "ita": 1, "jpn": 1, "cze": 1}

#: Powers with an air arm capable of independent operations.
_AIR = {"ger": 2, "gbr": 2, "sov": 2, "fra": 1, "ita": 1, "usa": 1, "jpn": 1}




#: Everything above, as the one object the seeder and the registry both read.
#: **What each power was built to do in 1936.** Two axes only: nobody has warheads, so
#: nuclear posture is not a question anybody in this world has answered.
#:
#: **Deliberately partial.** Germany is on neither pole of the alignment axis -- it was not
#: keeping out of other people's wars and it was not answering anybody's call -- and Spain
#: is a month from a civil war that decides both questions for it. An absent entry is not an
#: omission here; it is the answer, and it reads as the behaviour every world had before
#: doctrines existed.
#:
#: Each of these is a claim about 1936 and is checked against the era's manifest rather than
#: against this table.
DOCTRINES: dict[str, dict[str, str]] = {
    # Conscription, a reserve army of five million on mobilisation, and a defence built to
    # hold a line until they arrive.
    "fra": {"mobilisation": "mass", "alignment": "collective_security"},
    # Conscription restored in 1935 and the whole point of the year.
    "ger": {"mobilisation": "mass"},
    # **The regular army was 200,000 volunteers on long engagements** -- the smallest force
    # of any great power, and the reason the British answer to a continental war was the
    # fleet and somebody else's infantry.
    "gbr": {"mobilisation": "professional", "alignment": "collective_security"},
    "ita": {"mobilisation": "mass"},
    "jpn": {"mobilisation": "mass"},
    "sov": {"mobilisation": "mass", "alignment": "collective_security"},
    # **The Neutrality Acts, and the most defensible entry in this table.** A regular army
    # of 180,000 and a statute forbidding the country to take a side.
    "usa": {"mobilisation": "professional", "alignment": "neutrality"},
    "pol": {"mobilisation": "mass", "alignment": "collective_security"},
    # Montreux was signed this year; the policy it belonged to was staying out.
    "tur": {"alignment": "neutrality"},
}


SCENARIO = Scenario(
    id="gathering-storm",
    name="1936 - Gathering Storm",
    start_date="1936-01-01",
    rng_seed=1936,
    default_world_id="storm1936",
    era=Era.INTERWAR,
    powers=POWERS,
    armour=_ARMOUR,
    air=_AIR,
    fleets=FLEETS_1936,
    colonies=COLONIAL_HOLDINGS,
    sovereignty=SOVEREIGNTY,
    absent=ABSENT,
    unit_templates=UNIT_TEMPLATES,
    relations=RELATIONS,
    treaties=TREATIES,
    doctrines=DOCTRINES,
    history=HISTORY,
)


def gathering_storm_seed(
    pack: GeometryPack | PackSet,
    *,
    world_id: str = "storm1936",
    player: str | None = None,
    difficulty: Difficulty = Difficulty.NORMAL,
    seed: int = 1936,
    include_minors: bool = True,
) -> WorldSeed:
    """Build the 1 January 1936 world. See :func:`~atlas.scenarios.seeder.build_world`.

    Kept as a named function rather than folded into the registry because the 1936 world
    is what most of the test suite is written against, and `gathering_storm_seed(pack)`
    says what it builds in a way `build_world(SCENARIO, pack)` does not.
    """
    return build_world(
        SCENARIO, pack, world_id=world_id, player=player, difficulty=difficulty,
        seed=seed, include_minors=include_minors,
    )


def apply_1936_politics(conn, world_id: str) -> dict[str, int]:
    """Lay the real 1936 political state over a freshly created world."""
    return apply_politics(conn, world_id, SCENARIO)
