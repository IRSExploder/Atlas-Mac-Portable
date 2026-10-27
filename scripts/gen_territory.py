"""Generate a scenario's territory file from Natural Earth admin-1.

    python scripts/gen_territory.py gathering-storm
    python scripts/gen_territory.py modern-day --path /tmp/diff-me.json

Build-time only: it needs the 40 MB source GeoJSON, which the pack does not carry.
Run once, commit the JSON, never run it during play.

**An :class:`Era` is the only per-scenario part.** Which countries are drawn province by
province and who holds them, where each government sits, which borders the modern map has
in the wrong place, and three knobs on how provinces are grouped into regions. Everything
below :class:`Era` is machinery and works for any year.

**Hand-added nations survive a regeneration.** Ethiopia, Eritrea and Italian Somaliland
are in the 1936 file by hand and are the whole reason the Abyssinian war is fightable
province by province; the writer keeps any nation the era's config does not name, and
prints what it kept.
"""
from __future__ import annotations

import argparse
import json
import math
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from atlas.map.sources import SOURCES, fetch
from atlas.scenarios.territory import GROUP_TARGET, territory_path

SOURCE = "admin1-10m"

#: How far apart, in degrees, two groups may be and still be merged into one region.
#: Wide enough that a Siberian sweep of neighbouring oblasts stays one region, narrow
#: enough that nothing merges across an ocean.
MERGE_GAP = 12.0


@dataclass(frozen=True, slots=True)
class Era:
    """One scenario's answer to "which ground is drawn province by province, and whose".

    Everything below this class is machinery and works for any era. Everything in an
    ``Era`` is a claim about a particular year that somebody had to decide.
    """

    scenario: str
    note: str
    #: Nation id (as the scenario module spells it) -> the modern country codes whose
    #: ground it holds. Only the powers a campaign is fought *across* belong here: a
    #: country listed is partitioned completely, and everything unlisted stays one region.
    countries: dict[str, list[str]]
    #: Where each government sits, as ``(adm0_a3, province name Natural Earth's way)``.
    capitals: dict[str, tuple[str, str]]
    #: Provinces the mechanical pass puts in the wrong country, because the border moved
    #: inside what is now one state. Empty for an era whose borders *are* the map's.
    overrides: dict[tuple[str, str], tuple[str, str]] = field(default_factory=dict)
    #: Group provinces by Natural Earth's own ``region`` column where it has one. Right
    #: when that column is finer than the band we want and wrong when it is coarser --
    #: see the note at the call site.
    native_grouping: bool = True
    #: How far apart two groups may be and still merge into one region. Wider suits
    #: an era whose promoted powers are continental: sweeping Russia's eighty-six
    #: federal subjects at the 1936 gap breaks the chunk at every Siberian jump and
    #: leaves twenty-seven regions, most of them ground no campaign ever crosses.
    merge_gap: float = MERGE_GAP
    #: How groups are chunked into regions. ``sweep`` orders west to east and cuts;
    #: ``nearest`` grows each region outward from a seed. See the note at the call site --
    #: the sweep is only safe on a country wider than it is tall.
    clustering: str = "sweep"
    source: str = SOURCE


#: 1936 powers, as unions of the modern countries that hold their ground today. This is
#: the mechanical half: most of the map falls out of this table.
#:
#: **The ids are the ones :mod:`atlas.scenarios.gathering_storm` uses**, and the codes are
#: a subset of what that power holds there. Both halves matter. A power named here that
#: the scenario does not know would produce provinces nobody owns; a *code* named here
#: that the power does not hold would hand it ground it never had, which is how an earlier
#: draft gave the Soviet Union the Baltic states five years early.
#:
#: Only the powers a campaign is fought *across* are listed. Everything else on the map --
#: Belgium, the Netherlands, the Baltics, Turkey, the whole world outside Europe -- stays
#: one region per country, which is what every scenario did before provinces existed. A
#: country listed here is partitioned completely: the seeder reads that as "this ground is
#: drawn at province granularity" and creates no country-level region for it.
_STORM_COUNTRIES: dict[str, list[str]] = {
    "ger": ["DEU"],
    "fra": ["FRA"],
    "gbr": ["GBR"],
    "ita": ["ITA"],
    "pol": ["POL"],
    "cze": ["CZE", "SVK"],          # Czechoslovakia
    "aut": ["AUT"],
    "hun": ["HUN"],
    "rou": ["ROU", "MDA"],          # Romania, holding Bessarabia in 1936
    "yug": ["SRB", "HRV", "BIH", "SVN", "MNE", "MKD", "KOS"],
    "sov": ["RUS", "UKR", "BLR"],   # the European USSR; Central Asia stays country-level
    "esp": ["ESP"],
}

#: Where each power's government sits, as the province Natural Earth's own spelling puts
#: it in. Resolved to a group *after* merging, because merging is what decides which
#: region a province ends up inside. Without this a promoted power's capital would land
#: in whichever region sorted first, and Germany would be governed from Baden-Wurttemberg.
_STORM_CAPITALS: dict[str, tuple[str, str]] = {
    "ger": ("DEU", "Berlin"),
    "fra": ("FRA", "Paris"),
    "gbr": ("GBR", "Westminster"),
    "ita": ("ITA", "Roma"),
    "pol": ("POL", "Masovian"),
    "cze": ("CZE", "Prague"),
    "aut": ("AUT", "Wien"),
    "hun": ("HUN", "Budapest"),
    "rou": ("ROU", "Bucharest"),
    "yug": ("SRB", "Grad Beograd"),
    "sov": ("RUS", "Moskva"),
    "esp": ("ESP", "Madrid"),
}

#: The hand-corrected half: provinces the mechanical pass puts in the wrong 1936 country,
#: because the border moved inside what is now a single state. Keyed by province name as
#: Natural Earth spells it (accent-folded), valued by the power that held it in 1936.
#:
#: Each of these is a real 1939-45 flashpoint, which is why they are worth the hand work.
_STORM_OVERRIDES: dict[tuple[str, str], tuple[str, str]] = {
    # (modern adm0_a3, province name as Natural Earth spells it) -> (1936 power, region)
    #
    # Natural Earth uses English exonyms for some countries and endonyms for others --
    # Poland's provinces are "Warmian-Masurian", not "Warminsko-Mazurskie". Every Polish
    # override here was silently matching nothing until the guard below was added.
    ("POL", "Warmian-Masurian"): ("ger", "East Prussia"),
    ("RUS", "Kaliningrad"): ("ger", "East Prussia"),
    ("POL", "West Pomeranian"): ("ger", "Pomerania"),
    ("POL", "Lubusz"): ("ger", "Brandenburg East"),
    ("POL", "Lower Silesian"): ("ger", "Silesia"),
    ("POL", "Opole"): ("ger", "Silesia"),
    # The Polish Corridor and Danzig: the proximate cause of the war. Danzig itself was a
    # free city inside Pomeranian province and has no shape of its own at this
    # granularity, so the corridor carries it.
    ("POL", "Pomeranian"): ("pol", "Polish Corridor"),
    ("POL", "Kuyavian-Pomeranian"): ("pol", "Polish Corridor"),
    # Alsace-Lorraine: German 1871-1918, French in 1936, German again 1940.
    ("FRA", "Bas-Rhin"): ("fra", "Alsace-Lorraine"),
    ("FRA", "Haute-Rhin"): ("fra", "Alsace-Lorraine"),
    ("FRA", "Moselle"): ("fra", "Alsace-Lorraine"),
    ("FRA", "Meurthe-et-Moselle"): ("fra", "Alsace-Lorraine"),
    # The Sudetenland: Czech border districts Germany annexed in 1938.
    ("CZE", "Karlovarsky"): ("cze", "Sudetenland"),
    ("CZE", "Ustecky"): ("cze", "Sudetenland"),
    ("CZE", "Liberecky"): ("cze", "Sudetenland"),
    ("CZE", "Moravskoslezsky"): ("cze", "Sudetenland"),
    # Eastern Poland: Soviet after 1939, Polish in 1936.
    ("UKR", "L'viv"): ("pol", "Eastern Borderlands"),
    ("UKR", "Volyn"): ("pol", "Eastern Borderlands"),
    ("UKR", "Ternopil'"): ("pol", "Eastern Borderlands"),
    ("UKR", "Ivano-Frankivs'k"): ("pol", "Eastern Borderlands"),
    ("UKR", "Rivne"): ("pol", "Eastern Borderlands"),
    ("BLR", "Grodno"): ("pol", "Eastern Borderlands"),
    ("BLR", "Brest"): ("pol", "Eastern Borderlands"),
    # Vilnius was Polish from 1920 to 1939 and is not carved out here: Lithuania is drawn
    # at country granularity, and an override can only move a province between powers the
    # province map covers. The Kresy are otherwise intact.
}


#: The 2026 flashpoints, and only those. Same rule as 1936: promote the ground a campaign
#: actually crosses and leave the rest of the world one region per country.
#:
#: Four pairs, each an argument that could become a war and each currently unfightable at
#: country granularity -- a country-level Ukraine falls to one contest, which is what PT17
#: found. Everything else on the map, including every NATO member other than the ones
#: already listed, stays as it was.
_MODERN_COUNTRIES: dict[str, list[str]] = {
    "rus": ["RUS"],
    "ukr": ["UKR"],
    "chn": ["CHN"],
    "m_twn": ["TWN"],   # a minor on this map, promoted so the strait is a campaign
    "kor": ["KOR"],
    "prk": ["PRK"],
    "ind": ["IND"],
    "pak": ["PAK"],
    # The Middle East and Europe, where most of the wars a player starts are fought. At
    # country granularity Turkey invading Iran was one contest that decided the whole
    # country in two turns; drawn by province it is a campaign with a front.
    "tur": ["TUR"],
    "irn": ["IRN"],
    "isr": ["ISR"],
    "sau": ["SAU"],
    "egy": ["EGY"],
    "pol": ["POL"],
    "deu": ["DEU"],
    "fra": ["FRA"],
    "ita": ["ITA"],
    "gbr": ["GBR"],
    "jpn": ["JPN"],
    "vnm": ["VNM"],
}

#: Where the province map is not the political map, in 2026.
#:
#: This used to be empty, on the argument that a scenario drawn on a 2020s map has no moved
#: border to put back. That was wrong about one thing and the scenario auditor found it:
#: **Natural Earth files Crimea and Sevastopol under RUS**, and taking that mechanically
#: made annexation into sovereignty. It is Ukrainian ground under Russian control, and the
#: engine can say exactly that -- owner, controller and home are three columns.
#:
#: The other four entries **split the oblasts the war runs through into regions of their
#: own**, because they were bucketed three-to-a-region with neighbours the front does not
#: touch. Donetsk shared a region with Kharkiv and Zaporizhzhia with Dnipropetrovsk, so
#: there was no region that could be marked occupied without handing Russia two cities it
#: has never entered. Naming them individually is the whole of what makes the line of
#: control expressible at all.
#:
#: Kashmir needs nothing: Natural Earth already splits it along the Line of Control, Jammu
#: and Kashmir and Ladakh to India and Azad Kashmir and the Northern Areas to Pakistan,
#: which is the disputed starting position.
_MODERN_OVERRIDES: dict[tuple[str, str], tuple[str, str]] = {
    # Ukrainian ground, Russian-held since 2014. Sovereignty is not conferred by holding.
    ('RUS', 'Crimea'): ('ukr', 'Crimea'),
    ('RUS', 'Sevastopol'): ('ukr', 'Crimea'),
    # One region per oblast the front runs through, so a line of control has somewhere
    # to be drawn. Which of them is *held* is the scenario's business, not the map's.
    ('UKR', "Donets'k"): ('ukr', "Donets'k"),
    ('UKR', "Luhans'k"): ('ukr', "Luhans'k"),
    ('UKR', 'Zaporizhzhya'): ('ukr', 'Zaporizhzhya'),
    ('UKR', 'Kherson'): ('ukr', 'Kherson'),
    # ...and the two the front does not run through but sits against. Without these the
    # mechanical clustering swept Kharkiv and Dnipropetrovsk -- the two largest Ukrainian
    # industrial oblasts after Kyiv -- into a region named after Sumy, which is not a name
    # anybody reading a briefing about this war would recognise.
    ('UKR', 'Kharkiv'): ('ukr', 'Kharkiv'),
    ('UKR', "Dnipropetrovs'k"): ('ukr', "Dnipropetrovs'k"),
}

_MODERN_CAPITALS: dict[str, tuple[str, str]] = {
    "rus": ("RUS", "Moskva"),
    "ukr": ("UKR", "Kiev City"),
    "chn": ("CHN", "Beijing"),
    "m_twn": ("TWN", "Taipei City"),
    "kor": ("KOR", "Seoul"),
    "prk": ("PRK", "P'yongyang"),
    "ind": ("IND", "Delhi"),
    "pak": ("PAK", "F.C.T."),   # the Federal Capital Territory: Islamabad
    "tur": ("TUR", "Ankara"),
    "irn": ("IRN", "Tehran"),
    "isr": ("ISR", "Jerusalem"),
    "sau": ("SAU", "Ar Riyad"),
    "egy": ("EGY", "Al Qahirah"),
    "pol": ("POL", "Masovian"),
    "deu": ("DEU", "Berlin"),
    "fra": ("FRA", "Paris"),
    "ita": ("ITA", "Roma"),
    "gbr": ("GBR", "Westminster"),
    "jpn": ("JPN", "Tokyo"),
    "vnm": ("VNM", "Ha Noi"),
}



#: The 1972 flashpoints. Same rule again: promote the ground a campaign actually crosses.
#:
#: Ten modern country codes and twelve nations, because two of those countries were two
#: countries in 1972 -- which is the whole reason this era needs a territory file at all.
#: The central front (both Germanys, Poland, Czechoslovakia), the two divided peninsulas,
#: Cuba, and the ground between Egypt and Israel. **The Soviet interior is deliberately
#: not here**: the campaign of this era crosses the inner German border, not the Volga,
#: and the USSR already holds fifteen country-level regions.
_COLD_COUNTRIES: dict[str, list[str]] = {
    "wger": ["DEU"],                # the five eastern Lander go to `eger` by override
    "pol": ["POL"],
    "cze": ["CZE", "SVK"],          # Czechoslovakia
    "kor": ["KOR"],
    "prk": ["PRK"],
    "dvn": ["VNM"],                 # everything below the 17th parallel goes to `rvn`
    "cub": ["CUB"],
    "isr": ["ISR"],
    "egy": ["EGY"],                 # less the two Sinai governorates, held by `isr`
}

#: Where each government sits. `eger` is governed from Brandenburg because Natural Earth
#: has **one** Berlin shape and it has gone to the Western garrison: the city cannot be
#: divided on this map, so the region that surrounds it carries the seat and says so in
#: its name. `wger` sits in Nordrhein-Westfalen, which is where Bonn is.
_COLD_CAPITALS: dict[str, tuple[str, str]] = {
    "wger": ("DEU", "Nordrhein-Westfalen"),
    "eger": ("DEU", "Brandenburg"),
    "m_wbe": ("DEU", "Berlin"),
    "pol": ("POL", "Masovian"),
    "cze": ("CZE", "Prague"),
    "kor": ("KOR", "Seoul"),
    "prk": ("PRK", "P'yongyang"),
    "dvn": ("VNM", "Ha Noi"),
    "rvn": ("VNM", "Ho Chi Minh city"),
    "cub": ("CUB", "Ciudad de la Habana"),
    "isr": ("ISR", "Jerusalem"),
    "egy": ("EGY", "Al Qahirah"),
}

#: The hand-corrected half, and 1972 needs more of it than either shipped era because two
#: of its borders run *inside* a modern country and one runs across an occupied desert.
#:
#: **Germany.** The five eastern Lander are the German Democratic Republic. Berlin is one
#: shape at this granularity and cannot be halved, so it becomes `m_wbe` -- West Berlin,
#: an enclave the scenario hands to the United States as a colonial holding, which is what
#: it was: occupied ground garrisoned by the Western powers and constitutionally closed to
#: the Bundeswehr.
#:
#: **Vietnam.** The 17th parallel, province by province. Quang Tri is in the South: the
#: demilitarised zone ran through it and the Republic administered what was below.
#: The southern groups are named for the corps tactical zones and the towns a war was
#: reported from rather than for provinces, because those are the names anybody reading a
#: briefing about this war would recognise.
#:
#: **Sinai.** Egyptian ground held by Israel since 1967, which is the position on
#: 1 January 1972 and the thing the next war is fought to reverse. The Golan and the West
#: Bank are occupied on the same date and are *not* modelled: Syria and Palestine are
#: country-level on this map, and an override can only move a province between powers the
#: province map covers.
_COLD_OVERRIDES: dict[tuple[str, str], tuple[str, str]] = {
    ('DEU', 'Brandenburg'): ('eger', 'Berlin and Brandenburg'),
    ('DEU', 'Mecklenburg-Vorpommern'): ('eger', 'Mecklenburg'),
    ('DEU', 'Sachsen'): ('eger', 'Sachsen'),
    ('DEU', 'Sachsen-Anhalt'): ('eger', 'Sachsen-Anhalt'),
    ('DEU', 'Thuringen'): ('eger', 'Thueringen'),
    ('EGY', "Janub Sina'"): ('isr', 'Southern Sinai'),
    ('EGY', "Shamal Sina'"): ('isr', 'Northern Sinai'),
    ('DEU', 'Berlin'): ('m_wbe', 'West Berlin'),
    ('VNM', 'An Giang'): ('rvn', 'Mekong Delta North'),
    ('VNM', 'Ba Ria - Vung Tau'): ('rvn', 'Bien Hoa'),
    ('VNM', 'Bac Lieu'): ('rvn', 'Mekong Delta South'),
    ('VNM', 'Ben Tre'): ('rvn', 'Mekong Delta North'),
    ('VNM', 'Binh Duong'): ('rvn', 'Tay Ninh'),
    ('VNM', 'Binh Phuoc'): ('rvn', 'Tay Ninh'),
    ('VNM', 'Binh Thuan'): ('rvn', 'Nha Trang'),
    ('VNM', 'Binh Đinh'): ('rvn', 'Binh Dinh'),
    ('VNM', 'Ca Mau'): ('rvn', 'Mekong Delta South'),
    ('VNM', 'Can Tho'): ('rvn', 'Mekong Delta South'),
    ('VNM', 'Gia Lai'): ('rvn', 'Central Highlands'),
    ('VNM', 'Hau Giang'): ('rvn', 'Mekong Delta South'),
    ('VNM', 'Ho Chi Minh city'): ('rvn', 'Saigon'),
    ('VNM', 'Khanh Hoa'): ('rvn', 'Nha Trang'),
    ('VNM', 'Kien Giang'): ('rvn', 'Mekong Delta South'),
    ('VNM', 'Kon Tum'): ('rvn', 'Central Highlands'),
    ('VNM', 'Lam Đong'): ('rvn', 'Central Highlands'),
    ('VNM', 'Long An'): ('rvn', 'Mekong Delta North'),
    ('VNM', 'Ninh Thuan'): ('rvn', 'Nha Trang'),
    ('VNM', 'Phu Yen'): ('rvn', 'Binh Dinh'),
    ('VNM', 'Quang Nam'): ('rvn', 'Da Nang'),
    ('VNM', 'Quang Ngai'): ('rvn', 'Quang Ngai'),
    ('VNM', 'Quang Tri'): ('rvn', 'Quang Tri'),
    ('VNM', 'Soc Trang'): ('rvn', 'Mekong Delta South'),
    ('VNM', 'Tay Ninh'): ('rvn', 'Tay Ninh'),
    ('VNM', 'Thua Thien - Hue'): ('rvn', 'Hue'),
    ('VNM', 'Tien Giang'): ('rvn', 'Mekong Delta North'),
    ('VNM', 'Tra Vinh'): ('rvn', 'Mekong Delta South'),
    ('VNM', 'Vinh Long'): ('rvn', 'Mekong Delta South'),
    ('VNM', 'Ðong Thap'): ('rvn', 'Mekong Delta North'),
    ('VNM', 'Đa Nang'): ('rvn', 'Da Nang'),
    ('VNM', 'Đak Lak'): ('rvn', 'Central Highlands'),
    ('VNM', 'Đak Nong'): ('rvn', 'Central Highlands'),
    ('VNM', 'Đong Nam Bo'): ('rvn', 'Bien Hoa'),
}

ERAS: dict[str, Era] = {
    "gathering-storm": Era(
        scenario="gathering-storm",
        note=(
            "Generated from Natural Earth admin-1 by scripts/gen_territory.py, then "
            "corrected by the OVERRIDES table for borders that moved inside what is now "
            "one country. Regenerating overwrites hand edits made here."
        ),
        countries=_STORM_COUNTRIES,
        capitals=_STORM_CAPITALS,
        overrides=_STORM_OVERRIDES,
    ),
    "modern-day": Era(
        scenario="modern-day",
        note=(
            "Generated from Natural Earth admin-1 by scripts/gen_territory.py, then "
            "corrected by the OVERRIDES table: Natural Earth files Crimea under Russia, "
            "and the four oblasts the front runs through are given regions of their own "
            "so a line of control can be drawn. Regenerating overwrites hand edits."
        ),
        countries=_MODERN_COUNTRIES,
        capitals=_MODERN_CAPITALS,
        overrides=_MODERN_OVERRIDES,
        native_grouping=False,
        clustering="nearest",
        merge_gap=25.0,
    ),
    "cold-war": Era(
        scenario="cold-war",
        note=(
            "Generated from Natural Earth admin-1 by scripts/gen_territory.py, then "
            "corrected by the OVERRIDES table for the two borders that run inside what "
            "is now one country -- the inner German border and the 17th parallel -- and "
            "for the Sinai. Regenerating overwrites hand edits made here."
        ),
        countries=_COLD_COUNTRIES,
        capitals=_COLD_CAPITALS,
        overrides=_COLD_OVERRIDES,
        native_grouping=False,
        clustering="nearest",
        merge_gap=25.0,
    ),
}


def fold(text: str) -> str:
    """Accent-fold, so an override matches whatever encoding the source ships."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(c)
    )


def build(era: Era) -> dict:
    """Generate one era's territory payload from the admin-1 source."""
    data = json.loads(fetch(SOURCES[era.source]).read_text(encoding="utf-8"))
    of_country = {code: nation for nation, codes in era.countries.items() for code in codes}

    # nation -> group name -> [geo_key]
    groups: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    # Everything a nation holds that no override placed, bucketed by modern country so
    # the automatic grouping can split sensibly.
    loose: dict[str, dict[str, list[tuple[str, str, str]]]] = defaultdict(lambda: defaultdict(list))
    centroid: dict[str, tuple[float, float]] = {}
    matched: set[tuple[str, str]] = set()
    # nation -> the geo_key of the province its capital stands in, resolved to a group
    # once merging has decided which group that is.
    capital_shape: dict[str, str] = {}

    for feature in data["features"]:
        props = feature["properties"]
        adm0 = props.get("adm0_a3")
        nation = of_country.get(adm0)
        if nation is None:
            continue

        code = props.get("adm1_code")
        if not code or code in {"-99", "-1"}:
            continue
        geo_key = f"{era.source}:{code}"
        name = fold(props.get("name") or props.get("gn_name") or code)

        # **The capital is resolved against the *effective* owner, not the mechanical one.**
        # `nation` is whoever holds this modern country wholesale; an override may hand
        # this particular province to somebody else, and for a divided country that
        # somebody else is exactly who governs from it. Checking against `nation` meant a
        # file could not express two nations sharing one modern country with two
        # capitals -- which is what a divided Germany and a divided Vietnam are, and the
        # only shape of era this had never been asked for. Neither shipped era changes:
        # no 1936 or 2026 override names a capital province, so `owner` is `nation` for
        # every province either of them resolves a capital from.
        override = era.overrides.get((adm0, name))
        owner = override[0] if override is not None else nation

        if era.capitals.get(owner) == (adm0, name):
            capital_shape[owner] = geo_key

        if override is not None:
            matched.add((adm0, name))
            groups[owner][override[1]].append(geo_key)
            continue

        # Natural Earth's own `region` groups provinces the way the country does --
        # French departments into regions, Italian provinces into regions, Slovenian
        # municipalities into statistical regions. It is populated exactly where the
        # granularity is too fine and empty where it is already coarse (Germany's 16
        # states have none), which makes it the right grouping key wherever it exists.
        # Chunking alphabetically instead produced regions of nine departments scattered
        # across France, which would make adjacency and fronts nonsense.
        #
        # An era may switch this off, and Modern Day does. The column is only *usable*
        # where it is finer than the band we want: China's 32 provinces collapse to six
        # native regions, Russia's 86 to six, India's 36 to six -- and the merge below can
        # only make regions coarser, so those three would have ended up at half Ukraine's
        # count with no way back. With it off, every province stands alone and the merge
        # brings each country down to the band from above.
        native = fold(props.get("region") or "") if era.native_grouping else ""
        # Natural Earth carries a label point per province; good enough to sort by.
        lon = float(props.get("longitude") or 0.0)
        lat = float(props.get("latitude") or 0.0)
        loose[nation][adm0].append((geo_key, name, native))
        centroid[geo_key] = (lon, lat)

    for nation, by_country in loose.items():
        for adm0, entries in sorted(by_country.items()):
            named = [e for e in entries if e[2]]
            unnamed = [e for e in entries if not e[2]]
            label = "" if len(by_country) == 1 else f"{adm0} "

            # Where the source knows how the country groups itself, use that.
            by_native: dict[str, list[str]] = defaultdict(list)
            for geo_key, _name, native in named:
                by_native[native].append(geo_key)
            for native, keys in by_native.items():
                group = f"{label}{native}"
                groups[nation][group].extend(sorted(keys))

            # Where it does not, the shapes are already at a usable size (Germany's
            # states, Poland's voivodeships) and each stands as its own region.
            for geo_key, name, _ in sorted(unnamed, key=lambda e: e[1]):
                group = f"{label}{name}"
                suffix = 1
                unique = group
                while unique in groups[nation]:
                    suffix += 1
                    unique = f"{group} {suffix}"
                groups[nation][unique] = [geo_key]

    # Some countries subdivide their own `region` at county level -- Romania has 42,
    # Yugoslavia's successors 112 between them -- so grouping natively is coherent but
    # still far too fine. Merge neighbours until the power is inside the target band,
    # sweeping west to east so a merged region stays one piece of ground rather than a
    # scatter.
    #
    # Overrides are left alone -- they exist precisely because their extent is a
    # deliberate historical fact -- and so is whichever group holds a capital: merging
    # takes the first chunk member's name, so an unprotected capital ends up in a region
    # named after somewhere else entirely, and Czechoslovakia is governed from "CZE
    # Plzensky" with Prague somewhere inside it.
    protected = {group for _, group in era.overrides.values()}
    protected |= {
        group
        for nation, shape_key in capital_shape.items()
        for group, keys in groups[nation].items()
        if shape_key in keys
    }
    for nation, by_group in groups.items():
        if len(by_group) <= GROUP_TARGET:
            continue
        movable = [g for g in by_group if g not in protected]
        movable.sort(key=lambda g: min(centroid.get(k, (0.0, 0.0)) for k in by_group[g]))
        room = max(1, GROUP_TARGET - (len(by_group) - len(movable)))
        size = max(1, -(-len(movable) // room))
        merged: dict[str, list[str]] = {g: by_group[g] for g in by_group if g in protected}
        at = {g: min(centroid.get(k, (0.0, 0.0)) for k in by_group[g]) for g in movable}

        def close(chunk: list[str], by_group=by_group, merged=merged) -> None:
            if chunk:
                merged[chunk[0]] = [key for group in chunk for key in by_group[group]]

        if era.clustering == "nearest":
            # Grow each region outward from a seed, always taking the nearest group left.
            #
            # **A west-to-east sweep is only safe on a country wider than it is tall.**
            # India spans thirty degrees of longitude and thirty of latitude, so ordering
            # by longitude alone put Jammu and Kashmir in a region with Kerala, and
            # Russia's sweep put Murmansk in one with Crimea. Neither is ground any front
            # can cross. Growing by proximity instead keeps a region compact in both axes,
            # which is what makes naming it after a member honest.
            pool = list(movable)
            while pool:
                chunk = [pool.pop(0)]
                here = at[chunk[0]]
                while len(chunk) < size and pool:
                    nearest = min(pool, key=lambda g: (math.dist(at[g], here), g))
                    if math.dist(at[nearest], here) > era.merge_gap:
                        break
                    pool.remove(nearest)
                    chunk.append(nearest)
                    # Track the running centre, so a chunk grows as a blob rather than a
                    # chain that can walk arbitrarily far from where it started.
                    here = (
                        sum(at[g][0] for g in chunk) / len(chunk),
                        sum(at[g][1] for g in chunk) / len(chunk),
                    )
                # Named after whichever member sits closest to the middle of it, not the
                # seed it grew from. The seed is whatever was furthest west, which named
                # southern Russia "Sevastopol" and southern India "Lakshadweep" -- an
                # island and an enclave standing for the mainland behind them. Region ids
                # are what objectives and orders are written in, so a name that points at
                # the wrong end of the region is a name a staff cannot use.
                chunk.sort(key=lambda g: (math.dist(at[g], here), g))
                close(chunk)
        else:
            chunk = []
            previous: tuple[float, float] | None = None
            for group in movable:
                here = at[group]
                # Sweeping west to east keeps a merged region one piece of ground only
                # while the sweep is over ground. Crossing the Atlantic mid-chunk gave
                # France a region made of Guyane and four Breton departments, which no
                # front can reach and no border makes sense of.
                if chunk and (
                    len(chunk) >= size or math.dist(here, previous or here) > era.merge_gap
                ):
                    close(chunk)
                    chunk = []
                chunk.append(group)
                previous = here
            close(chunk)
        groups[nation] = merged

    # An override that matches nothing is worse than no override: it silently leaves the
    # province with whoever holds it today and creates an empty region nobody notices.
    # Every Polish entry did exactly that until this ran.
    unmatched = sorted(set(era.overrides) - matched)
    if unmatched:
        raise SystemExit(
            "these overrides matched no province -- check the spelling Natural Earth "
            "uses:\n  " + "\n  ".join(f"{a} / {n}" for a, n in unmatched)
        )

    # A capital that resolved to nothing is the same class of failure as an unmatched
    # override: the power keeps playing, from a seat of government chosen by sort order.
    capitals: dict[str, str] = {}
    for nation, shape_key in capital_shape.items():
        for group, keys in groups[nation].items():
            if shape_key in keys:
                capitals[nation] = group
                break
    lost = sorted(set(era.capitals) - set(capitals))
    if lost:
        raise SystemExit(
            "these capitals matched no province -- check the spelling Natural Earth "
            "uses:\n  " + "\n  ".join(f"{n}: {era.capitals[n]}" for n in lost)
        )

    # A nation that exists only because an override put provinces in it, and that the era
    # forgot to give a capital to, would be written out with a `KeyError` one line below.
    # Named here instead, because "you added an override owner and no capital" is an
    # actionable message and a traceback out of a dict comprehension is not.
    orphan = sorted(set(groups) - set(capitals))
    if orphan:
        raise SystemExit(
            "these nations hold ground and have no capital -- every nation an override "
            "creates needs an entry in `capitals`:\n  " + "\n  ".join(orphan)
        )

    return {
        "scenario": era.scenario,
        "source": era.source,
        "note": era.note,
        # Every modern country this file partitions completely. The seeder reads it as
        # "this ground is drawn at province granularity" and creates no country-level
        # region for any of them -- which is what stops a promoted power's ground being
        # claimed twice, once as provinces and once as a country.
        "countries": sorted({code for codes in era.countries.values() for code in codes}),
        "nations": {
            nation: {
                "capital": capitals[nation],
                "regions": {
                    name: sorted(keys) for name, keys in sorted(groups[nation].items())
                },
            }
            for nation in sorted(groups)
        },
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("scenario", nargs="?", default="gathering-storm", choices=sorted(ERAS))
    parser.add_argument(
        "--path", help="write here instead of the scenario's territory file (for diffing)"
    )
    args = parser.parse_args(argv)

    era = ERAS[args.scenario]
    payload = build(era)

    out = Path(args.path) if args.path else territory_path(era.scenario)

    # **Nations already in the file that this era's config does not name are kept.**
    # Gathering Storm has three -- Ethiopia, Eritrea and Italian Somaliland, added by
    # hand, and between them the whole reason the Abyssinian war is fightable province by
    # province. Overwriting used to delete all three silently and revert that war to a
    # single siege; the suite caught it, but only after the fact.
    if out.exists():
        existing = json.loads(out.read_text(encoding="utf-8"))
        kept = {
            nation: entry
            for nation, entry in existing.get("nations", {}).items()
            if nation not in payload["nations"]
        }
        if kept:
            print(f"keeping {len(kept)} hand-added nation(s): {', '.join(sorted(kept))}")
            payload["nations"] = dict(sorted((payload["nations"] | kept).items()))
            payload["countries"] = sorted(
                set(payload["countries"]) | set(existing.get("countries", ()))
            )

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")

    print(f"wrote {out}  ({out.stat().st_size / 1000:.0f} kB)")
    print(f"{'power':<8} {'regions':>8} {'shapes':>8}  capital")
    for nation in sorted(payload["nations"]):
        regions = payload["nations"][nation]["regions"]
        print(f"{nation:<8} {len(regions):>8} {sum(len(v) for v in regions.values()):>8}"
              f"  {payload['nations'][nation]['capital']}")


if __name__ == "__main__":
    main()
