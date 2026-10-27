"""Who is running each country when the world begins.

A nation had a personality and no *person*. ``stability`` and ``legitimacy`` moved every
turn with nobody's name attached to them, so a coup was a number falling rather than
somebody being removed, and the tracked-agent briefing spoke in the voice of a colour on a
map.

**Nobody is invented.** Two decisions, and both are about what *not* to write:

*The interwar table carries real names*, because 1936 is settled history -- the people, the
offices and what they were on the record as wanting. The alternative was generating a
hundred and fifty plausible statesmen, which needs a name bank per culture and produces a
world where Churchill negotiates with somebody the generator made up. An unnamed government
is honest about what it is; a fabricated one is not.

*The Cold War table carries real names for the same reason the interwar one does.* 1
January 1972 is settled history: the offices, the people in them and what they were on
the record as wanting are all a matter of record, so there is nothing to invent and no
risk of confidently naming the wrong person.

*The modern table shipped with no names at all*, and now has them. Who holds an office
changes between the build and the play, and a game that confidently tells you the wrong
head of government is worse than one that says *the government of Japan* -- so the names
went in only once somebody had checked, and the date they were checked is written next to
the table. Everything else in a modern entry was there from the start, because it is the
part that does not go stale: the real **title**, distinct **traits**, and an **agenda**.

Which office each entry names is a **design** choice and not merely a factual one. China is
the General Secretary rather than the President and Iran is the Supreme Leader rather than
the President, because the agendas are those offices' agendas; correcting one of those
means deciding what the game models, not just looking somebody up.

Every nation not in either table gets an unnamed government too, and it is a first-class
leader in every respect but the name: it has traits, it has an agenda, and it can be
overthrown like anybody else.

Nothing in the engine reads names -- they are display and briefing text, and the mechanics
run entirely off ``traits``. A wrong name would be a typo to fix, never a balance change.

Traits are mechanical and neutral by design -- ``bellicose``, ``technocrat``,
``dealmaker`` -- and never pejorative. They are levers in
:mod:`atlas.engine.leaders`, not characterisations.
"""

from __future__ import annotations

from dataclasses import dataclass

from .era import Era

__all__ = [
    "LEADERS_1936",
    "LEADERS_1972",
    "LEADERS_2026",
    "LeaderSeed",
    "leader_for",
]


@dataclass(frozen=True, slots=True)
class LeaderSeed:
    """One government at world creation."""

    name: str
    title: str
    traits: tuple[str, ...]
    agenda: str


#: The interwar table. Settled history: these are the people in the offices in 1936, and
#: the agendas are what they were on the record as wanting.
LEADERS_1936: dict[str, LeaderSeed] = {
    "ger": LeaderSeed("Adolf Hitler", "Führer und Reichskanzler",
                      ("bellicose", "ideologue", "hardliner"),
                      "overturn the Versailles settlement and take ground in the east"),
    "ita": LeaderSeed("Benito Mussolini", "Duce del Fascismo",
                      ("bellicose", "populist", "ideologue"),
                      "make the Mediterranean an Italian sea and hold Ethiopia"),
    "sov": LeaderSeed("Joseph Stalin", "General Secretary",
                      ("hardliner", "ideologue", "technocrat"),
                      "industrialise at any cost and buy time before the war comes"),
    "gbr": LeaderSeed("Stanley Baldwin", "Prime Minister",
                      ("cautious", "pragmatist", "dealmaker"),
                      "avoid a second European war and rearm slowly enough to afford it"),
    "fra": LeaderSeed("Léon Blum", "Président du Conseil",
                      ("reformer", "cautious", "populist"),
                      "hold the Republic together at home and the alliance abroad"),
    "usa": LeaderSeed("Franklin D. Roosevelt", "President",
                      ("reformer", "dealmaker", "isolationist"),
                      "finish the recovery and stay out of other people's wars"),
    "jpn": LeaderSeed("Kōki Hirota", "Prime Minister",
                      ("bellicose", "hardliner", "pragmatist"),
                      "secure the resources of the mainland before the powers object"),
    "chn": LeaderSeed("Chiang Kai-shek", "Generalissimo",
                      ("hardliner", "pragmatist", "cautious"),
                      "unify the country before answering Japan"),
    "esp": LeaderSeed("Manuel Azaña", "President of the Republic",
                      ("reformer", "populist"),
                      "keep the Republic standing"),
    "pol": LeaderSeed("Edward Rydz-Śmigły", "Marshal of Poland",
                      ("hardliner", "cautious"),
                      "stand between two powers without being crushed by either"),
    "tur": LeaderSeed("Mustafa Kemal Atatürk", "President",
                      ("reformer", "technocrat", "isolationist"),
                      "modernise the republic and stay out of the coming war"),
}

#: The Cold War table: who was in the office on 1 January 1972. Settled history, like the
#: interwar one, and dated for the same reason -- these are the governments of that
#: morning and not of the year in general. Satō resigns in July and Colombo's ministry
#: falls in February; both are correct on the date the world begins, which is the only
#: date a starting position has to be correct on.
#:
#: **The Italian entry had the sequence backwards and the wrong man in the chair.** It read
#: Andreotti, whose *first* government was sworn in on 17 February 1972 -- six weeks after
#: this world starts. Emilio Colombo was President of the Council on 1 January and resigned
#: in January; Andreotti succeeded him. Found by Codex reviewing the era against the record.
#:
#: Which office each entry names is a design choice, as it is in the modern table. The
#: communist states are named by their party office rather than their state office --
#: General Secretary, First Secretary, Chairman -- because that is where the agenda
#: actually sat, and naming Podgorny as head of state would model the wrong person.
LEADERS_1972: dict[str, LeaderSeed] = {
    "usa": LeaderSeed("Richard Nixon", "President",
                      ("dealmaker", "pragmatist", "hardliner"),
                      "open Beijing, leave Vietnam without losing it, and be re-elected"),
    "sov": LeaderSeed("Leonid Brezhnev", "General Secretary",
                      ("cautious", "hardliner", "dealmaker"),
                      "reach parity, hold the bloc together, and negotiate from both"),
    "chn": LeaderSeed("Mao Zedong", "Chairman",
                      ("ideologue", "hardliner", "populist"),
                      "balance against Moscow, even if that means receiving Nixon"),
    "gbr": LeaderSeed("Edward Heath", "Prime Minister",
                      ("technocrat", "dealmaker", "pragmatist"),
                      "take Britain into Europe and hold the economy together"),
    "fra": LeaderSeed("Georges Pompidou", "President",
                      ("pragmatist", "technocrat", "dealmaker"),
                      "keep the deterrent national and France's hands free"),
    "wger": LeaderSeed("Willy Brandt", "Bundeskanzler",
                       ("reformer", "dealmaker", "cautious"),
                       "normalise with the east without loosening the western tie"),
    "eger": LeaderSeed("Erich Honecker", "General Secretary",
                       ("hardliner", "ideologue", "cautious"),
                       "win recognition as a state and keep the border sealed"),
    "pol": LeaderSeed("Edward Gierek", "First Secretary",
                      ("technocrat", "reformer", "cautious"),
                      "buy consent with western credit and consumer goods"),
    "cze": LeaderSeed("Gustáv Husák", "General Secretary",
                      ("hardliner", "technocrat"),
                      "normalisation: nothing that happened in 1968 happens again"),
    "rou": LeaderSeed("Nicolae Ceaușescu", "General Secretary",
                      ("populist", "ideologue", "dealmaker"),
                      "stay inside the Pact and take orders from nobody in it"),
    "ita": LeaderSeed("Emilio Colombo", "Presidente del Consiglio",
                      ("dealmaker", "cautious", "pragmatist"),
                      "hold a centre together against both of its extremes"),
    "tur": LeaderSeed("Nihat Erim", "Prime Minister",
                      ("technocrat", "cautious", "hardliner"),
                      "hold the republic under martial law and keep the alliance"),
    "jpn": LeaderSeed("Eisaku Satō", "Prime Minister",
                      ("cautious", "technocrat", "dealmaker"),
                      "finish the return of Okinawa and stay under the American umbrella"),
    "dvn": LeaderSeed("Lê Duẩn", "First Secretary",
                      ("hardliner", "ideologue", "bellicose"),
                      "unify the country and outlast the Americans, in that order"),
    "rvn": LeaderSeed("Nguyễn Văn Thiệu", "President",
                      ("hardliner", "pragmatist"),
                      "survive the American withdrawal"),
    "prk": LeaderSeed("Kim Il Sung", "General Secretary",
                      ("hardliner", "ideologue", "isolationist"),
                      "unify the peninsula on his terms and depend on neither patron"),
    "kor": LeaderSeed("Park Chung-hee", "President",
                      ("hardliner", "technocrat"),
                      "industrialise fast, and rule by decree if that is what it takes"),
    "cub": LeaderSeed("Fidel Castro", "Prime Minister",
                      ("ideologue", "populist", "bellicose"),
                      "export the revolution and outlast the embargo"),
    "isr": LeaderSeed("Golda Meir", "Prime Minister",
                      ("hardliner", "cautious", "pragmatist"),
                      "hold the 1967 lines until somebody offers peace for them"),
    "egy": LeaderSeed("Anwar Sadat", "President",
                      ("bellicose", "dealmaker", "pragmatist"),
                      "get Sinai back, by war if the diplomacy will not deliver it"),
}

#: The modern table. **Researched and filled 2026-08-04**; see the module docstring for why
#: it shipped blank first and why the date is written down. A nation absent from it gets an
#: unnamed government, which is a better answer than a confidently wrong one.
LEADERS_2026: dict[str, LeaderSeed] = {
    "usa": LeaderSeed("Donald Trump", "President",
                      ("bellicose", "dealmaker", "isolationist"),
                      "put American interests first and make the allies pay their share"),
    "chn": LeaderSeed("Xi Jinping", "General Secretary",
                      ("hardliner", "technocrat", "pragmatist"),
                      "reunify with Taiwan and outlast the American century"),
    "rus": LeaderSeed("Vladimir Putin", "President",
                      ("bellicose", "hardliner", "pragmatist"),
                      "hold what has been taken and break the western consensus"),
    "deu": LeaderSeed("Friedrich Merz", "Chancellor",
                      ("technocrat", "cautious", "dealmaker"),
                      "keep Europe together and rearm without alarming anybody"),
    "fra": LeaderSeed("Emmanuel Macron", "President",
                      ("reformer", "dealmaker", "pragmatist"),
                      "make Europe capable of defending itself without America"),
    "gbr": LeaderSeed("Andy Burnham", "Prime Minister",
                      ("pragmatist", "cautious", "dealmaker"),
                      "stay useful to Washington and relevant in Europe"),
    "ind": LeaderSeed("Narendra Modi", "Prime Minister",
                      ("populist", "pragmatist", "technocrat"),
                      "take nobody's side and grow faster than everyone"),
    "jpn": LeaderSeed("Sanae Takaichi", "Prime Minister",
                      ("cautious", "technocrat", "dealmaker"),
                      "rearm quietly and keep the Americans committed"),
    # **The office holder, not the presumed heir.** This shipped as Mojtaba Khamenei, who
    # is a real person, is widely discussed as a successor, and has never held the office.
    # Every other name in this table is somebody who actually holds the post on the start
    # date, so one forecast among them is not a scenario choice, it is an inconsistency --
    # and the era manifest is a record of what the period *was*, which cannot assert a
    # succession that has not happened. Ali Khamenei has been Supreme Leader since 1989.
    # The traits and the standing goal are unchanged: they described this posture already.
    "irn": LeaderSeed("Ali Khamenei", "Supreme Leader",
                      ("ideologue", "hardliner"),
                      "survive the sanctions and keep the threshold open"),
    # **Pakistan shipped with no leader at all**, which is not the same as a nation the
    # table declines to name: a power with an arsenal and a seat in two of this era's
    # standing quarrels was reading as "the government" in every briefing, and the leader
    # layer's traits were doing nothing for it. The civilian premiership is the office the
    # engine can model; that the army chief is the larger fact about Pakistani power is
    # real, and is not something a single `LeaderSeed` can say.
    # Added with the roster: a power with nobody in office reads as "the government" in
    # every briefing, which is what Pakistan was doing before this table gained it.
    "idn": LeaderSeed("Prabowo Subianto", "President",
                      ("populist", "hardliner", "dealmaker"),
                      "be courted by both sides and joined to neither"),
    "aus": LeaderSeed("Anthony Albanese", "Prime Minister",
                      ("pragmatist", "dealmaker"),
                      "buy the submarines and keep selling the ore"),
    "ita": LeaderSeed("Giorgia Meloni", "Prime Minister",
                      ("populist", "pragmatist", "hardliner"),
                      "be indispensable in Brussels and unembarrassing in Washington"),
    "egy": LeaderSeed("Abdel Fattah el-Sisi", "President",
                      ("hardliner", "pragmatist"),
                      "keep the canal open and the creditors waiting"),
    # **The party post, not the presidency.** Vietnam's paramount office is General
    # Secretary and the state presidency is the junior of the two -- the same arrangement
    # China has, and seated the same way here so the table is consistent about which chair
    # actually decides.
    "vnm": LeaderSeed("To Lam", "General Secretary",
                      ("pragmatist", "hardliner", "technocrat"),
                      "take the factories and give away nothing for them"),
    "twn": LeaderSeed("Lai Ching-te", "President",
                      ("hardliner", "pragmatist"),
                      "make an invasion look expensive and say nothing that invites one"),
    "pak": LeaderSeed("Shehbaz Sharif", "Prime Minister",
                      ("pragmatist", "dealmaker"),
                      "keep the creditors patient and the army onside"),
    "isr": LeaderSeed("Benjamin Netanyahu", "Prime Minister",
                      ("bellicose", "hardliner", "pragmatist"),
                      "act first and explain afterwards"),
    "tur": LeaderSeed("Recep Tayyip Erdoğan", "President",
                      ("populist", "dealmaker", "pragmatist"),
                      "be indispensable to every bloc and beholden to none"),
    "bra": LeaderSeed("Luiz Inácio Lula da Silva", "President",
                      ("populist", "dealmaker"),
                      "lead the global south without picking a fight"),
    "prk": LeaderSeed("Kim Jong Un", "Supreme Leader",
                      ("hardliner", "ideologue", "isolationist"),
                      "keep the arsenal and the dynasty"),
    "kor": LeaderSeed("Lee Jae-myung", "President",
                      ("technocrat", "cautious"),
                      "deter the north without provoking it"),
    "ukr": LeaderSeed("Volodymyr Zelenskyy", "President",
                      ("hardliner", "populist"),
                      "hold the line and keep the west paying for it"),
    "pol": LeaderSeed("Karol Nawrocki", "President",
                      ("hardliner", "bellicose"),
                      "become the strongest army in Europe before it is needed"),
    "sau": LeaderSeed("Mohammed bin Salman", "Crown Prince",
                      ("reformer", "technocrat", "pragmatist"),
                      "spend the oil on whatever comes after the oil"),
}

_TABLES: dict[Era, dict[str, LeaderSeed]] = {
    Era.INTERWAR: LEADERS_1936,
    Era.COLD_WAR: LEADERS_1972,
    Era.MODERN: LEADERS_2026,
}


def leader_for(nation_id: str, *, era: Era) -> LeaderSeed | None:
    """The designed government of one nation, or ``None`` if no table names it.

    ``None`` is not "no leader". :func:`atlas.state.bootstrap.create_world` has already
    seated a placeholder government in every nation on the map, so what this answers is
    the narrower question of whether a scenario has something to *say* about who governs.
    A nation it says nothing about keeps the placeholder, which is a first-class leader in
    every respect but the name -- it has traits, it has an agenda, and it can be
    overthrown.
    """
    table = _TABLES[era]
    key = nation_id[2:] if nation_id.startswith("m_") else nation_id
    return table.get(key)
