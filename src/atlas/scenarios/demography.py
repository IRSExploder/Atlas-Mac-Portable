"""How many people a country has, and what a minor's economy is worth.

The last two places where the map's *shape* stood in for its *substance*. Both were land
area, and both were measurably wrong:

* ``population = min(120, land_area * 0.4)``. Tibet carried 112 against Henan's 16 -- Tibet
  has about 3.6 million people and Henan about 99. Worse than the ratio was the **clamp**:
  every large country saturated at 120, so the United States, Australia, Brazil, Canada
  and *Antarctica* all had identical populations and identical manpower ceilings. Russia's
  ceiling was 809 against India's 204.
* ``minor economy = 4 + area * 0.02``. **Antarctica was the richest minor on the map in
  both eras.** Taiwan's whole economy was 4.9, which is why Hsinchu -- where a serious
  share of the world's semiconductors is made -- seeded at 0.43 development however large
  a multiplier the heartland table gave it.

Population is not cosmetic. ``budget`` caps manpower at ``population * 2``, and manpower
is what units cost to raise, so this is a **combat rebalance** as much as an economic one:
it decides how big an army each country can field at all.

**Normalised, not invented.** Both tables are scaled so the world total is what it was
before -- so aggregate manpower capacity and aggregate minor wealth are unchanged and only
their *distribution* moves. The same discipline as the development table: redistribute
inside a fixed pool, never conjure. That also means the tables can be edited freely
without inflating the world.

**Within a country, population follows the same weights as development**
(:func:`atlas.scenarios.development.region_weights`), because the rich provinces and the
populous ones are overwhelmingly the same provinces. One table, so the two cannot disagree.
"""

from __future__ import annotations

from dataclasses import replace

from ..state.bootstrap import NationSeed, RegionSeed
from .development import region_weights
from .era import Era

__all__ = [
    "MINOR_ECONOMY",
    "POPULATION",
    "TERRITORIES",
    "TERRITORIES_1972",
    "POPULATION_1936",
    "POPULATION_1972",
    "assign_population",
    "rescale_minor_economies",
]


#: Population in millions, keyed by nation id with any ``m_`` prefix stripped -- so one
#: entry serves a country whether the scenario promoted it to a power or left it a minor
#: (India is `ind` in 2026 and `m_ind` in 1936).
#:
#: 2026 figures. The 1936 overrides below carry the ones that differ enough to matter.
#: Only the *ratios* survive normalisation, so precision beyond two significant figures
#: buys nothing.
POPULATION: dict[str, float] = {
    # the giants
    "chn": 1410, "ind": 1450, "usa": 340, "idn": 280, "pak": 250, "nga": 230,
    "bra": 215, "bgd": 173, "rus": 144, "mex": 130, "eth": 128, "jpn": 123,
    "phl": 118, "egy": 114, "cod": 105, "vnm": 100, "irn": 90, "tur": 85,
    "deu": 84, "tha": 72, "gbr": 68, "fra": 68, "tza": 67, "zaf": 60,
    "ita": 59, "mmr": 55, "kor": 52, "col": 52, "ken": 55, "esp": 48,
    "arg": 46, "dza": 45, "sdn": 48, "uga": 49, "irq": 45, "ukr": 38,
    "afg": 42, "pol": 37, "can": 40, "mar": 38, "sau": 37, "uzb": 36,
    "per": 34, "mys": 34, "ago": 36, "gha": 34, "moz": 33, "yem": 34,
    "npl": 31, "ven": 28, "mdg": 30, "civ": 28, "cmr": 28, "ner": 27,
    "aus": 27, "prk": 26, "twn": 23, "syr": 23, "mli": 23, "bfa": 23,
    "lka": 22, "mwi": 21, "zmb": 20, "kaz": 20, "tcd": 18, "som": 18,
    "sen": 18, "zwe": 16, "gtm": 18, "ecu": 18, "khm": 17,
    "rwa": 14, "gin": 14, "bdi": 13, "bol": 12, "tun": 12, "bel": 12,
    "hti": 12, "jor": 11, "cub": 11, "ben": 13, "grc": 10, "prt": 10,
    "cze": 11, "swe": 11, "aze": 10, "hun": 10, "are": 10, "blr": 9,
    "isr": 9, "aut": 9, "che": 9, "hnd": 10, "tjk": 10, "srb": 7,
    "pry": 7, "lao": 8, "bgr": 6, "svk": 5, "fin": 6, "nor": 5,
    "dnk": 6, "sgp": 6, "nzl": 5, "irl": 5, "hrv": 4, "geo": 4,
    "mng": 3, "arm": 3, "alb": 3, "ltu": 3, "svn": 2,
    "lva": 2, "est": 1, "isl": 0.4,
}

#: **Dependent territories, mid-century, in millions.** The colonies, mandates, protectorates
#: and islands that are somebody else's ground rather than their own country.
#:
#: **Why this table has to exist.** Population is shared out among the nations that hold
#: ground, and then *within* a holder by development weight -- so before this, every British
#: possession from India to Anguilla was sized by how developed it was and by nothing else.
#: British India read 0.85 and American Samoa read 24.52, because the United States is rich
#: and Britain's holdings were many. Per-territory population was fiction.
#:
#: Two thirds of these were missing from :data:`POPULATION` entirely, so they fell to
#: ``DEFAULT_POPULATION`` -- three million for Pitcairn -- and the handful that did resolve
#: fell back to *2026* figures, which is why Britain's home islands came out at 5% of the
#: Empire's counted people instead of a defensible ~14%.
#:
#: **Spliced into the era tables rather than into** :data:`POPULATION`, deliberately: many of
#: these codes are sovereign countries in 2026 -- Jamaica, Cyprus, Malta, Fiji -- and writing
#: a 1936 figure into the modern table would change *their* population and with it their
#: manpower ceiling. The eras must not leak into each other.
#:
#: Figures are order-of-magnitude right rather than precise, which is all normalisation
#: preserves. Entries marked ``# ?`` are the ones worth arguing with: either the territory's
#: borders differ from the modern country whose code it borrows, or the census is thin.
TERRITORIES: dict[str, float] = {
    # --- British Caribbean and Atlantic
    "jam": 1.1, "tto": 0.45, "brb": 0.19, "guy": 0.33, "bhs": 0.07,
    "blz": 0.06, "lca": 0.07, "vct": 0.06, "grd": 0.08, "dma": 0.05,
    "kna": 0.04, "atg": 0.04, "msr": 0.014, "vgb": 0.007, "cym": 0.007,
    "tca": 0.006, "aia": 0.006, "bmu": 0.03, "flk": 0.002, "sgs": 0.001,
    "shn": 0.004,
    # --- British Africa
    "sle": 1.8, "gmb": 0.2, "bwa": 0.27, "lso": 0.56, "swz": 0.16,
    "sol": 0.35, "mus": 0.4, "syc": 0.03, "sds": 1.5,  # ? Sudan's south, split from sdn
    # --- British Mediterranean, Middle East and Indian Ocean
    "cyp": 0.36, "cyn": 0.1,   # ? cyn is the north of the same island, not a 1936 entity
    "mlt": 0.27, "psx": 1.4, "bhr": 0.09, "mdv": 0.08,
    "iot": 0.001, "kas": 0.001,  # ? Siachen is a glacier; a floor, not a census
    "ioa": 0.001,
    # --- British Asia and Pacific
    "hkg": 1.0, "brn": 0.03, "fji": 0.2, "slb": 0.09, "kir": 0.03,
    "tuv": 0.004, "ton": 0.03, "vut": 0.045, "pcn": 0.001,
    # --- British Isles dependencies
    "imn": 0.05, "ggy": 0.04, "jey": 0.05,
    # --- American
    "pri": 1.7, "vir": 0.025, "gum": 0.022, "asm": 0.013,
    # --- French Africa and Levant
    "lbn": 1.0, "mrt": 0.4, "tgo": 0.78, "gab": 0.4, "cog": 0.7,
    "caf": 0.9, "dji": 0.05, "com": 0.12,
    # --- French Pacific and Atlantic
    "ncl": 0.05, "pyf": 0.04, "wlf": 0.01, "spm": 0.004, "maf": 0.005,
    "blm": 0.002, "atf": 0.001,
    # --- Dutch
    "sur": 0.17, "cuw": 0.06, "abw": 0.03, "sxm": 0.002,
    # --- Portuguese
    "cpv": 0.15, "gnb": 0.35, "stp": 0.06, "mac": 0.16, "tls": 0.46,
    # --- Spanish
    "gnq": 0.15, "sah": 0.02,
    # --- Danish, Finnish
    "grl": 0.017, "fro": 0.026, "ald": 0.027,
    # --- Japanese South Seas Mandate
    "fsm": 0.05, "mhl": 0.01, "mnp": 0.04, "plw": 0.02,
    # --- Australian and New Zealand
    "png": 1.5, "nru": 0.003, "nfk": 0.001, "hmd": 0.001, "atc": 0.001,
    "cok": 0.012, "niu": 0.004, "wsm": 0.06,
    # --- South African
    "nam": 0.35,
    # --- Italian East Africa. The map splits both into provinces; each province takes a
    #     share of the whole, which is what grouping by territory code is for.
    "eri": 0.6, "som": 1.0,
    # --- Yugoslavia's constituent lands, which the map holds as dependencies of `m_yug`.
    #     Not colonies, but the same problem: without these they were sized by development.
    "srb": 4.8, "hrv": 3.4, "bih": 2.3, "svn": 1.1, "mkd": 0.95,
    "mne": 0.36, "kos": 0.65,  # ? Kosovo was not counted separately in 1936
    # --- and the one that is neither, but is held rather than owned.
    "wbe": 2.2,   # West Berlin, Cold War only
}

#: Where a territory had moved on materially by 1972. Only the ones that changed enough to
#: matter: the rest are small enough that the difference is lost in normalisation anyway.
TERRITORIES_1972: dict[str, float] = {
    "hkg": 4.0, "pri": 2.7, "png": 2.5, "psx": 2.7,  # ? Palestine's 1972 successor borders
    "lbn": 2.7, "mac": 0.25, "tls": 0.6, "gnq": 0.29, "sol": 0.75,
    "sds": 3.0, "nam": 0.75, "som": 2.9, "eri": 2.0, "fji": 0.54,
    "mus": 0.85, "cyp": 0.63, "tto": 0.98, "jam": 1.9, "guy": 0.75,
    "sle": 2.6, "tgo": 2.0, "caf": 1.8, "cog": 1.3, "gab": 0.6,
    "mrt": 1.2, "gnb": 0.6, "cpv": 0.27, "grl": 0.05,
}

#: Where 1936 was materially different. Absent entries use the modern figure, which for
#: most of the world is the wrong *level* but close to the right *share* -- and share is
#: all that survives normalisation.
POPULATION_1936: dict[str, float] = {
    "chn": 500, "ind": 350, "sov": 170, "rus": 170, "usa": 128, "jpn": 70,
    "deu": 67, "ger": 67, "gbr": 47, "fra": 42, "ita": 43, "idn": 60,
    "bra": 40, "pol": 34, "esp": 25, "tur": 16, "mex": 18, "pak": 0,
    "bgd": 0, "kor": 22, "prk": 0, "twn": 5, "ukr": 0, "vnm": 18,
    "egy": 16, "irn": 15, "tha": 14, "phl": 15, "eth": 12, "arg": 13,
    "can": 11, "aus": 7, "zaf": 10, "cze": 15, "yug": 15, "rou": 19,
    "hun": 9, "aut": 7, "grc": 7, "prt": 7, "bel": 8, "nld": 8,
    "swe": 6, "che": 4, "fin": 4, "nor": 3, "dnk": 4, "isr": 0,
    "sau": 3, "irq": 4, "syr": 3, "nga": 20, "cod": 10, "sdn": 6,
    # The countries that grew most between the eras. Without these the 1936 map hands
    # Tanzania 68 million -- the modern figure -- and makes it the seventh most populous
    # nation in the world, which is the same class of error this module exists to remove.
    "tza": 5, "ken": 4, "uga": 4, "moz": 5, "ago": 3, "civ": 4,
    "gha": 4, "npl": 6, "mmr": 15, "mdg": 4, "cmr": 3, "ner": 2,
    "mli": 3, "bfa": 3, "som": 2, "sen": 2, "zwe": 1.5, "zmb": 1.5,
    "mwi": 2, "tcd": 1.5, "rwa": 2, "bdi": 2, "gin": 2,
    "yem": 4, "are": 0.1, "kwt": 0.1, "qat": 0.03, "omn": 0.5,
    "jor": 0.3, "lka": 6, "khm": 3, "lao": 1, "afg": 8, "uzb": 6,
    "kaz": 6, "tjk": 1.3, "aze": 3, "arm": 1.1, "geo": 3.5, "blr": 0,
    "mys": 4, "sgp": 0.6, "col": 9, "ven": 3.5, "per": 6, "chl": 4.5,
    "ecu": 2.5, "bol": 3, "gtm": 2, "hnd": 1, "cub": 4, "hti": 3,
    "dza": 7, "mar": 7, "tun": 2.6, "lby": 0.8, "isl": 0.1, "nzl": 1.6,
}

#: 1972. Same rule as the interwar table: only where the figure differs enough to change a
#: *share*, since share is all that survives normalisation.
#:
#: **The five keys no other table can supply are the reason this one exists.** ``sov``,
#: ``wger``, ``eger``, ``dvn`` and ``rvn`` are nations on the 1972 map and on no other, so
#: without an entry here the Soviet Union would take the three-million default for an
#: unnamed minor and be handed an army smaller than Belgium's. The rest of the table is the
#: world halfway between the other two: China at 862 million rather than 1,410, and the
#: countries that have tripled since -- Nigeria, Ethiopia, Pakistan, Bangladesh, most of
#: Africa -- back at what they actually were.
POPULATION_1972: dict[str, float] = {
    # the nations that exist in this era and no other
    "sov": 248, "wger": 61, "eger": 17, "dvn": 22, "rvn": 19,
    # the giants
    "chn": 862, "ind": 563, "usa": 210, "idn": 122, "jpn": 108, "bra": 98,
    "bgd": 71, "pak": 65, "nga": 58, "gbr": 56, "mex": 54, "ita": 54,
    "fra": 52, "phl": 39, "tha": 38, "tur": 37, "egy": 35, "esp": 34,
    "kor": 33, "pol": 33, "irn": 30, "mmr": 29, "eth": 28, "arg": 24,
    "zaf": 23, "can": 22, "cod": 22, "col": 22, "rou": 20.5, "vnm": 41,
    "yug": 21, "cze": 14.5, "prk": 15, "twn": 15, "afg": 12, "npl": 12,
    "sdn": 16, "mar": 16, "dza": 15, "per": 14, "tza": 14, "mys": 11,
    "ken": 12, "lka": 13, "nld": 13, "aus": 13, "chl": 10, "ven": 11,
    "hun": 10, "irq": 10, "uga": 10, "bel": 9.7, "moz": 9, "gha": 9,
    "grc": 8.9, "cub": 8.8, "prt": 8.6, "bgr": 8.6, "swe": 8.1, "aut": 7.5,
    "khm": 7.1, "mdg": 7, "syr": 6.7, "sau": 6.7, "che": 6.4, "cmr": 6.5,
    "ecu": 6.3, "yem": 6, "ago": 6, "bfa": 5.6, "zwe": 5.6, "tun": 5.4,
    "civ": 5.3, "mli": 5.3, "gtm": 5.5, "hti": 5, "dnk": 5, "bol": 5,
    "mwi": 4.6, "fin": 4.6, "zmb": 4.4, "dom": 4.4, "ner": 4.3, "sen": 4.3,
    "gin": 4, "nor": 3.9, "tcd": 3.8, "rwa": 3.8, "bdi": 3.5, "som": 3.4,
    "isr": 3.1, "irl": 3, "nzl": 2.9, "ben": 2.9, "lao": 2.9, "hnd": 2.7,
    "lby": 2.1, "sgp": 2.1, "jor": 1.8, "mng": 1.3, "kwt": 0.9, "omn": 0.7,
    "are": 0.2, "qat": 0.1, "isl": 0.2,
    # inside the Soviet Union in 1972, so they are ground rather than nations
    "ukr": 0, "blr": 0, "kaz": 0, "uzb": 0, "tkm": 0, "tjk": 0, "kgz": 0,
    "geo": 0, "arm": 0, "aze": 0, "mda": 0, "lva": 0, "ltu": 0, "est": 0,
    # and the two Germanys are `wger` / `eger` here, never `deu`
    "deu": 0,
}

#: A country the tables do not name. Deliberately small: the ~150 unnamed nations are the
#: genuinely minor ones, and a generous default would hand them the world.
DEFAULT_POPULATION = 3.0

#: Economy for a *minor*, on the same arbitrary scale the scenarios use for powers, keyed
#: the same way. Powers keep the figure their own scenario file hand-authored; this only
#: replaces the land-area formula for everyone else.
MINOR_ECONOMY: dict[str, float] = {
    "can": 38, "aus": 30, "kor": 30, "esp": 28, "mex": 26, "idn": 26,
    "nld": 22, "twn": 22, "che": 20, "pol": 20, "bel": 14, "swe": 14,
    "irl": 14, "are": 14, "sgp": 13, "isr": 13, "aut": 12, "nor": 12,
    "tha": 12, "arg": 12, "nga": 12, "dnk": 11, "mys": 11, "zaf": 11,
    "phl": 11, "col": 10, "vnm": 10, "bgd": 10, "dza": 9, "chl": 9,
    "fin": 8, "rou": 8, "cze": 8, "prt": 8, "per": 8, "nzl": 8,
    "grc": 7, "irq": 7, "qat": 7, "kaz": 7, "hun": 6, "kwt": 6,
    "eth": 6, "mar": 5, "ecu": 5, "svk": 5, "ken": 5, "ago": 5,
    "omn": 5, "gtm": 5, "bgr": 5, "ven": 5, "uzb": 5, "hrv": 4,
    "lka": 4, "cub": 4, "ltu": 4, "srb": 4, "tun": 4, "gha": 4,
    "civ": 4, "svn": 4, "lva": 3, "est": 3, "npl": 3, "khm": 3,
    "isl": 2, "mng": 2, "geo": 2, "arm": 2, "alb": 2, "mlt": 2,
    # ...and the ground with no economy in it at all, which is the whole point.
    #
    # **Five entries left here when Modern Day stopped being their era.** Greenland, the
    # French Southern Lands, the Falklands, Heard Island and the Chagos were nations in
    # exactly one shipped scenario -- this one -- and none of the three now seats them as
    # states: 1936 and 1972 held them as dependencies all along, and 2026 does too as of
    # `modern_day.SOVEREIGNTY`. A key that resolves to no nation in any era is a typo doing
    # nothing, so they are gone rather than kept for a caller that cannot arrive.
    # `sah` stays: Western Sahara is still its own state on the modern map, held down by
    # Morocco rather than owned by it, which is the distinction `OCCUPIED` exists for.
    "sah": 0.3,
}

#: A minor the table does not name.
DEFAULT_MINOR_ECONOMY = 2.5


def _key(nation_id: str) -> str:
    """The lookup key: a nation id with any ``m_`` prefix stripped."""
    return nation_id[2:] if nation_id.startswith("m_") else nation_id


#: The era-specific overrides, in front of the modern table rather than replacing it: an
#: entry absent from an era's own table falls back to the modern figure, which for most of
#: the world is the wrong *level* but close to the right *share*.
_OVERRIDES: dict[Era, dict[str, float]] = {
    Era.INTERWAR: POPULATION_1936,
    Era.COLD_WAR: POPULATION_1972,
    Era.MODERN: {},
}

#: The same tables again with :data:`TERRITORIES` behind them, **for territories only**.
#:
#: **The two lookups are deliberately separate.** Splicing the territory figures into
#: `_OVERRIDES` also answered the question "how many people does this *nation* have", and
#: many of these codes are sovereign countries in 1972 -- Jamaica, Cyprus, Fiji, Nauru. Nauru
#: went from a 3.0 default to its real 0.003 and, because the world total is fixed, dragged
#: every other nation's share with it: 143 of 148 Cold War manpower ceilings moved, the
#: United States by 1.5%. Correct in isolation and a combat rebalance of the whole map
#: arriving through a table about colonies.
#:
#: So a nation is sized by the nation tables and a dependency by these. A micro-state that
#: governs itself and is missing from :data:`POPULATION` still takes
#: :data:`DEFAULT_POPULATION`, which is far too generous for Nauru -- **a real defect, left
#: alone here**, because fixing it moves manpower and belongs in a change that says so.
#: **The modern entry was empty and was right to be, until it was not.** An era override
#: exists to correct the base table, and the base table *is* the modern one -- so for most
#: of this file "modern needs no override" is simply true. Territories are the exception,
#: and the reason is structural rather than demographic: the seed shares a holder's people
#: across the regions it holds by development weight, so this lookup is the only thing that
#: stops a territory being sized like a slice of its metropole. While the modern map had no
#: dependencies there was nothing to size and an empty dict cost nothing. The moment it
#: gained thirty-seven, the same emptiness gave Guam thirteen million people and cut
#: Britain's home island from 30.15 to 9.59 -- measured, before either change was made.
#:
#: So the two changes ship together and must stay together: `modern_day.SOVEREIGNTY` says
#: who holds what, and this says how many people are actually there.
_TERRITORY_OVERRIDES: dict[Era, dict[str, float]] = {
    Era.INTERWAR: {**TERRITORIES, **POPULATION_1936},
    Era.COLD_WAR: {**TERRITORIES, **TERRITORIES_1972, **POPULATION_1972},
    Era.MODERN: {**TERRITORIES},
}


def _territory_code(region_id: str, owner: str, era: Era) -> str | None:
    """The territory's own demographic identity, or ``None`` for the holder's own ground.

    A region id carries the country it belongs to -- ``r_ind`` is India, ``r_gbr_wales`` is
    Wales -- so the question is only whether that country is the one holding it. India held
    by Britain is its own people; Wales held by Britain is Britain's.

    The compound form is the same question one level down: the map splits Italian Eritrea
    into six provinces and Italian Somaliland into thirteen, and ``r_eri_anseba`` is a piece
    of Eritrea rather than a country called ``eri_anseba``. They resolve to the same code and
    therefore share one territory's people between them, which is the point.

    **Deliberately blind to occupation**, because it is asked while the world is still a seed
    and nobody has been occupied yet. A province whose prefix is its own owner is home ground
    whatever happens to it later -- and if it is taken, the taker inherits its people through
    ``controller_nation_id`` at the point `budget` reads them, which is the right place for
    that to happen rather than here.
    """
    owner_key = _key(owner)
    code = _key(region_id.removeprefix("r_"))
    if code == owner_key:
        return None
    if _has_population(code, era):
        return code
    head = code.split("_", 1)[0]
    if head != owner_key and _has_population(head, era):
        return head
    return None


def _has_population(key: str, era: Era) -> bool:
    """Whether anything actually knows this territory, as opposed to guessing a default."""
    return key in _TERRITORY_OVERRIDES[era] or key in POPULATION


def _territory_population(key: str, *, era: Era) -> float:
    """What a *dependency* is sized by. See :data:`_TERRITORY_OVERRIDES` for why this is not
    the same question as :func:`_population_of`."""
    override = _TERRITORY_OVERRIDES[era].get(key)
    if override is not None:
        return override
    return POPULATION.get(key, DEFAULT_POPULATION)


def _population_of(nation_id: str, *, era: Era) -> float:
    key = _key(nation_id)
    override = _OVERRIDES[era].get(key)
    if override is not None:
        return override
    return POPULATION.get(key, DEFAULT_POPULATION)


def rescale_minor_economies(nations: list[NationSeed]) -> list[NationSeed]:
    """Replace the land-area economy of every minor with a real one, same total.

    A *power* keeps whatever its own scenario file says -- those are hand-authored and
    were never the problem. Only the minors were sized by how much of the map they cover,
    and only they are touched.

    Preserving the total is what makes this safe: the minors collectively hold about 60%
    of world economy in every era, and that share is a balance decision somebody made.
    Moving it would be a different change wearing this one's clothes.

    **Era-neutral, and that is a known approximation rather than a claim.** :data:`POPULATION`
    and the endowments both have per-era tables and this does not; a minor's *share* of
    minor wealth is taken from the modern table whatever year it is. 1936 has lived with
    that since it was written and 1972 inherits it. Fixing it would mean re-authoring the
    table for each era, which moves a shipped era's balance -- a different change.
    """
    minors = [n for n in nations if n.id.startswith("m_")]
    if not minors:
        return nations

    before = sum(float(n.economy or 0.0) for n in minors)
    raw = {
        n.id: MINOR_ECONOMY.get(_key(n.id), DEFAULT_MINOR_ECONOMY) for n in minors
    }
    total_raw = sum(raw.values()) or 1.0
    scale = before / total_raw

    out: list[NationSeed] = []
    for nation in nations:
        if nation.id in raw:
            out.append(replace(nation, economy=round(raw[nation.id] * scale, 1)))
        else:
            out.append(nation)
    return out


def assign_population(
    regions: list[RegionSeed],
    nations: list[NationSeed],
    *,
    era: Era = Era.MODERN,
) -> list[RegionSeed]:
    """Give every province a population drawn from its country's, not from its size.

    Two steps, and the second is the one that matters. Each nation gets a share of the
    world's people proportional to how many it really has; then that share is spread
    across its provinces by the *development* weights, because the populous provinces and
    the valuable ones are the same provinces almost everywhere.

    The world total is preserved exactly, so the manpower this map can raise in aggregate
    is unchanged -- what changes is who can raise it.
    """
    land = [r for r in regions if r.kind != "ocean" and r.owner_nation_id]
    if not land:
        return regions
    before = sum(float(r.population or 0.0) for r in land)

    weights, total_weight = region_weights(regions, nations)
    per_nation = {
        n.id: _population_of(n.id, era=era) for n in nations
    }
    # Only nations that actually hold ground can hold people.
    holders = {r.owner_nation_id for r in land}

    # **The world total is preserved only if the two halves agree about who exists.**
    # `total_raw` was summed over `per_nation`, which is built from `nations`; the loop
    # below then fell back to `DEFAULT_POPULATION` for an owner it could not find. An
    # unknown owner therefore took people out of the numerator and put nothing into the
    # denominator, and the total this function documents as exact quietly inflated.
    #
    # Stated as an error rather than papered over with a wider denominator, because the
    # fallback was never the real problem: a region owned by a nation the seed does not
    # list is a broken scenario. `region.owner_nation_id` is a foreign key, so
    # `create_world` would refuse it moments later with a message about an integrity
    # constraint -- this says which scenario entry is wrong while the answer is still
    # nearby.
    unknown = sorted(holder for holder in holders if holder not in per_nation)
    if unknown:
        raise ValueError(
            f"these regions are owned by nations the scenario does not define: {unknown}. "
            "Population is shared out among the nations that exist, so an owner that is "
            "not one of them cannot be given a share of it."
        )

    total_raw = sum(v for k, v in per_nation.items() if k in holders) or 1.0
    scale = before / total_raw

    # **Within a holder, by territory and only then by development.** The nation-level
    # allocation above is untouched, so every nation's total -- and therefore the manpower
    # ceiling `budget` derives from it -- is exactly what it was. What changes is who inside
    # a holder's borders those people are.
    #
    # Development weight alone made per-territory population fiction: it sized British India
    # and Anguilla by how developed they were and by nothing else, so India read 0.85 while
    # American Samoa read 24.52 on the strength of belonging to the United States. Grouping
    # by territory first puts the people where the people were, and leaves the aggregate --
    # the only thing combat reads -- alone.
    groups: dict[tuple[str, str], list[RegionSeed]] = {}
    for region in regions:
        owner = region.owner_nation_id
        if weights.get(region.id) is None or owner is None:
            continue
        groups.setdefault((owner, _territory_code(region.id, owner, era) or ""), []).append(region)

    # Each group's claim on its holder: a territory claims its own people, and everything
    # that is the holder's own ground claims the holder's.
    claim: dict[tuple[str, str], float] = {
        key: (_population_of(key[0], era=era) if not key[1]
              else _territory_population(key[1], era=era))
        for key in groups
    }
    by_owner: dict[str, float] = {}
    for (owner, _code), value in claim.items():
        by_owner[owner] = by_owner.get(owner, 0.0) + value

    # **The holder's total is the thing that must not move**, because `budget` caps manpower
    # at twice the population of everything a nation holds -- so any drift here is a combat
    # rebalance of the whole map arriving as a rounding artefact. Each owner's target is what
    # it had before, to the cent, and the redistribution happens strictly inside it.
    target: dict[str, float] = {}
    for region in regions:
        owner = region.owner_nation_id
        weight = weights.get(region.id)
        if weight is None or owner is None:
            continue
        share = total_weight.get(owner) or 1.0
        target[owner] = target.get(owner, 0.0) + round(
            max(_FLOOR, per_nation[owner] * scale * weight / share), 2)

    settled: dict[str, float] = {}
    for owner, total in target.items():
        mine = [key for key in groups if key[0] == owner]
        if len(mine) == 1:
            # **Nothing to redistribute**, so nothing is touched. Every nation that holds no
            # dependencies keeps the exact figures it had, province for province -- which is
            # all of Modern Day and most of the other two eras.
            #
            # Not merely tidy: `budget` reads population by *controller*, and an occupied
            # province counts for whoever is sitting on it. Re-deriving an untouched holder's
            # provinces from a rounded total moved a hundredth of a million between two of
            # them, and in Modern Day one of those two is Crimea -- so a cent left Ukraine's
            # ceiling and arrived on Russia's, from a change that has nothing to say about
            # either.
            continue
        raw: dict[str, float] = {}
        for key, members in groups.items():
            if key[0] != owner:
                continue
            pool = total * (claim[key] / (by_owner[owner] or 1.0))
            inner = sum(weights[r.id] for r in members) or 1.0
            for region in members:
                raw[region.id] = pool * weights[region.id] / inner
        settled.update(_hold_the_total(raw, total))

    out: list[RegionSeed] = []
    for region in regions:
        owner = region.owner_nation_id
        weight = weights.get(region.id)
        if weight is None or owner is None:
            out.append(region)
            continue
        people = settled.get(region.id)
        if people is None:
            share = total_weight.get(owner) or 1.0
            people = round(max(_FLOOR, per_nation[owner] * scale * weight / share), 2)
        out.append(replace(region, population=people))
    return out


#: A province at zero is one no later mechanic can reason about -- the same floor the
#: development table and the strike path keep, and for the same reason.
_FLOOR = 0.05


def _hold_the_total(raw: dict[str, float], total: float) -> dict[str, float]:
    """Round to the cent so the parts still add to ``total`` exactly.

    Two things push a sum off its target and both had to be answered, because a 1% drift in
    a holder's population is a 1% drift in the army it may raise:

    * **the floor.** Real per-territory population puts seventy of the map's islands below
      five hundredths of a million, and raising each to the floor conjured 2.5 million people
      out of nothing and handed most of them to Britain. The floor is kept -- the alternative
      is provinces at zero -- and paid for out of the territories that are above it.
    * **rounding.** Two decimal places over three hundred provinces does not land on the
      total by itself. The largest remainders take the leftover cents, which is the ordinary
      apportionment rule and is deterministic given a stable order.
    """
    if not raw:
        return {}
    floored = {k: v for k, v in raw.items() if v <= _FLOOR}
    free = {k: v for k, v in raw.items() if v > _FLOOR}
    # Buy the floor from those who can afford it. Twice, because paying for it can push
    # another province under -- and no further, because a third round moves nothing that
    # matters and an unbounded loop over a pathological holder is worse than a cent.
    for _ in range(2):
        room = sum(free.values())
        owed = total - len(floored) * _FLOOR
        if room <= 0 or owed <= 0:
            break
        free = {k: v * owed / room for k, v in free.items()}
        sunk = {k: v for k, v in free.items() if v <= _FLOOR}
        if not sunk:
            break
        floored.update(sunk)
        free = {k: v for k, v in free.items() if v > _FLOOR}

    exact = {**{k: _FLOOR for k in floored}, **free}
    out = {k: round(v, 2) for k, v in exact.items()}
    cents = int(round((total - sum(out.values())) * 100))
    if cents:
        # Cents go to the largest remainders, and ties break on region id so the same world
        # is built the same way every time. **Walked until every cent is placed**, not sliced
        # once: a province already at the floor cannot give one back, so a single pass can
        # leave a cent stranded -- and a stranded cent is a nation's manpower ceiling off by
        # a hundredth, which is exactly the drift this function exists to prevent.
        step = 1 if cents > 0 else -1
        order = sorted(exact, key=lambda k: (-(exact[k] * 100 % 1.0), k), reverse=cents < 0)
        while cents:
            spent = 0
            for key in order:
                if not cents:
                    break
                if round(out[key] + step * 0.01, 2) < _FLOOR:
                    continue
                out[key] = round(out[key] + step * 0.01, 2)
                cents -= step
                spent += 1
            if not spent:
                break   # everything is on the floor; nothing can absorb the remainder
    return out
