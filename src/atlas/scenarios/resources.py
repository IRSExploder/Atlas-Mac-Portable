"""What each country digs out of the ground.

Three goods, and the smallness is a design decision rather than a shortcut. **Every
resource is a number a model has to reason about inside a prompt**, and the tracked-agent
briefing is already the largest object in the engine. A dozen goods would buy spreadsheet
depth and pay for it in the thing that actually makes ATLAS work -- that a model can hold
the whole position in its head. Three is enough for the shape that matters: a country that
has what it needs, one that must buy it, and one that can be cut off from it.

**The era decides what they mean, not the table.** ``oil`` is what an army runs on in 1936,
1972 and 2026 alike; ``rare`` is tungsten and rubber in the first, tin, nickel and cobalt
in the second, and semiconductors and lithium in the third. One set of ids, three sets of
endowments.

**There is no total to preserve here**, and that is the one way this differs from
:mod:`atlas.scenarios.demography`. Population and minor economies were *redistributions* of
a pool that already existed, so the guard could be "the world total is unchanged". Resources are
new state, so that guard is unavailable. What replaces it: endowments are meaningful only
*relative to demand*, and demand is derived from `industry` -- so what the balance turns on
is the ratio between what a country produces and what its factories need, and both sides of
that ratio come from tables in this repository rather than from the map's shape. A number
here being wrong makes one country short; it cannot inflate the world.
"""

from __future__ import annotations

from .era import Era

__all__ = [
    "ENDOWMENT_1936",
    "ENDOWMENT_1972",
    "ENDOWMENT_2026",
    "GOODS",
    "endowment_for",
]

#: The three goods. Ids are stable and are what `resource.good` and `trade_route.good`
#: check against; the schema's CHECK constraints name the same three.
GOODS: tuple[str, ...] = ("oil", "steel", "rare")

#: Monthly production per nation, keyed by nation id with any ``m_`` prefix stripped -- the
#: same keying :mod:`atlas.scenarios.demography` and :mod:`atlas.scenarios.leaders` use, so
#: one entry serves a country whether a scenario promoted it to a power or left it a minor.
#:
#: Units are arbitrary and only the ratio to demand means anything. Calibrated so a major
#: industrial power needs roughly 10 of each: a country at 10 is self-sufficient, one at 3
#: must import two-thirds of what it burns, and one at 25 has a surplus worth selling.
ENDOWMENT_2026: dict[str, dict[str, float]] = {
    "usa": {"oil": 30, "steel": 14, "rare": 6},
    "chn": {"oil": 6, "steel": 40, "rare": 30},
    "rus": {"oil": 28, "steel": 12, "rare": 8},
    "sau": {"oil": 34, "steel": 1, "rare": 0.2},
    "irn": {"oil": 16, "steel": 3, "rare": 1},
    "irq": {"oil": 14, "steel": 0.5, "rare": 0.2},
    "are": {"oil": 12, "steel": 1, "rare": 0.2},
    "kwt": {"oil": 10, "steel": 0.3, "rare": 0.1},
    "ven": {"oil": 8, "steel": 1, "rare": 0.3},
    "nga": {"oil": 8, "steel": 0.5, "rare": 0.5},
    "can": {"oil": 14, "steel": 3, "rare": 2},
    "bra": {"oil": 8, "steel": 6, "rare": 5},
    "nor": {"oil": 8, "steel": 0.5, "rare": 0.3},
    "kaz": {"oil": 7, "steel": 2, "rare": 3},
    "dza": {"oil": 6, "steel": 0.5, "rare": 0.2},
    "lby": {"oil": 5, "steel": 0.2, "rare": 0.1},
    "mex": {"oil": 5, "steel": 3, "rare": 1},
    "aus": {"oil": 2, "steel": 12, "rare": 12},
    "ind": {"oil": 2, "steel": 16, "rare": 4},
    "jpn": {"oil": 0.2, "steel": 12, "rare": 2},
    "kor": {"oil": 0.1, "steel": 9, "rare": 1},
    "deu": {"oil": 0.5, "steel": 10, "rare": 1},
    "gbr": {"oil": 3, "steel": 3, "rare": 0.5},
    "fra": {"oil": 0.2, "steel": 4, "rare": 0.5},
    "ita": {"oil": 0.3, "steel": 4, "rare": 0.3},
    "tur": {"oil": 0.3, "steel": 6, "rare": 2},
    "ukr": {"oil": 0.5, "steel": 6, "rare": 1},
    "zaf": {"oil": 0.2, "steel": 3, "rare": 8},
    "cod": {"oil": 0.2, "steel": 1, "rare": 10},
    "chl": {"oil": 0.2, "steel": 2, "rare": 7},
    "per": {"oil": 0.5, "steel": 2, "rare": 5},
    "idn": {"oil": 3, "steel": 3, "rare": 5},
    "mys": {"oil": 2, "steel": 1, "rare": 3},
    "twn": {"oil": 0.1, "steel": 3, "rare": 6},
    "mng": {"oil": 0.5, "steel": 1, "rare": 4},
    "pol": {"oil": 0.3, "steel": 5, "rare": 0.5},
    "esp": {"oil": 0.1, "steel": 3, "rare": 0.5},
    "egy": {"oil": 2, "steel": 2, "rare": 0.3},
    "isr": {"oil": 0.1, "steel": 1, "rare": 2},
    "prk": {"oil": 0.1, "steel": 2, "rare": 3},
    "vnm": {"oil": 1, "steel": 3, "rare": 4},
    "arg": {"oil": 2, "steel": 2, "rare": 2},
    "aze": {"oil": 4, "steel": 0.3, "rare": 0.2},
    "omn": {"oil": 4, "steel": 0.2, "rare": 0.1},
    "qat": {"oil": 6, "steel": 0.5, "rare": 0.1},
}

#: 1936. Different world: American and Soviet oil dominate, Britain draws on an empire,
#: Germany and Japan are the two great powers that must import nearly everything they burn
#: -- which is the single most consequential economic fact of the period and the reason
#: this table is worth writing separately rather than reusing the modern one.
ENDOWMENT_1936: dict[str, dict[str, float]] = {
    "usa": {"oil": 40, "steel": 24, "rare": 6},
    "sov": {"oil": 12, "steel": 10, "rare": 6},
    "ger": {"oil": 0.5, "steel": 14, "rare": 1},
    # Britain's line is the Empire's, because in 1936 the Empire is what it was: Indian
    # steel and Malayan tin and rubber were separate entries here until the sovereignty
    # map made India and Malaya British ground, and a resource cannot be endowed to a
    # nation that does not exist. Folded in rather than deleted -- the tin was real.
    "gbr": {"oil": 3, "steel": 11, "rare": 16},
    "fra": {"oil": 0.2, "steel": 5, "rare": 3},
    "jpn": {"oil": 0.3, "steel": 4, "rare": 1},
    "ita": {"oil": 0.1, "steel": 2, "rare": 0.5},
    "chn": {"oil": 0.3, "steel": 1, "rare": 4},
    "ven": {"oil": 12, "steel": 0.2, "rare": 0.1},
    "irn": {"oil": 10, "steel": 0.2, "rare": 0.1},
    "irq": {"oil": 5, "steel": 0.1, "rare": 0.1},
    "mex": {"oil": 8, "steel": 1, "rare": 0.3},
    "rou": {"oil": 7, "steel": 1, "rare": 0.2},
    # The Netherlands East Indies, for the same reason: the sixth-largest oil
    # producer in the world was Dutch, and that is most of why Japan looked south.
    "nld": {"oil": 6, "steel": 1, "rare": 4},
    "can": {"oil": 2, "steel": 4, "rare": 3},
    "aus": {"oil": 0.2, "steel": 3, "rare": 4},
    "bra": {"oil": 0.3, "steel": 1, "rare": 4},
    "chl": {"oil": 0.1, "steel": 1, "rare": 5},
    "zaf": {"oil": 0.1, "steel": 2, "rare": 6},
    "esp": {"oil": 0.1, "steel": 2, "rare": 3},
    "swe": {"oil": 0.1, "steel": 6, "rare": 1},
    "pol": {"oil": 0.5, "steel": 3, "rare": 0.3},
    "cze": {"oil": 0.1, "steel": 4, "rare": 0.5},
    "bel": {"oil": 0.1, "steel": 3, "rare": 0.5},
    "tur": {"oil": 0.2, "steel": 1, "rare": 2},
    "nor": {"oil": 0.1, "steel": 1, "rare": 0.5},
}

#: 1972. A third world again, and three facts about it are worth the separate table.
#:
#: **Oil is not yet a weapon and is about to become one.** The embargo is October 1973, so
#: on this map the Gulf producers are large, undisciplined suppliers rather than a cartel,
#: and the importers -- Japan and Western Europe above all -- are more exposed than they
#: have noticed. North Sea oil does not flow until 1975, so Britain imports.
#:
#: **Both blocs are close to self-sufficient in oil and neither is in anything else.** The
#: Soviet Union and the United States each dig most of what they burn; the People's
#: Republic has Daqing and is briefly an exporter. That is what makes an embargo in this
#: era a weapon against clients rather than against superpowers -- which is exactly how it
#: was used.
#:
#: **``rare`` is Malayan tin and rubber, Cuban nickel, Zairean cobalt and Chilean copper.**
#: Three of those four are why the flashpoints are where they are.
ENDOWMENT_1972: dict[str, dict[str, float]] = {
    "usa": {"oil": 36, "steel": 24, "rare": 6},
    "sov": {"oil": 24, "steel": 20, "rare": 8},
    "sau": {"oil": 20, "steel": 0.3, "rare": 0.1},
    "irn": {"oil": 18, "steel": 1, "rare": 0.2},
    "ven": {"oil": 14, "steel": 1, "rare": 0.3},
    "kwt": {"oil": 12, "steel": 0.2, "rare": 0.1},
    "lby": {"oil": 9, "steel": 0.2, "rare": 0.1},
    "irq": {"oil": 8, "steel": 0.3, "rare": 0.1},
    "nga": {"oil": 6, "steel": 0.4, "rare": 0.4},
    "can": {"oil": 6, "steel": 5, "rare": 3},
    "rou": {"oil": 6, "steel": 3, "rare": 0.2},
    "idn": {"oil": 5, "steel": 0.5, "rare": 3},
    "chn": {"oil": 4, "steel": 6, "rare": 5},
    "dza": {"oil": 4, "steel": 0.3, "rare": 0.2},
    "are": {"oil": 4, "steel": 0.1, "rare": 0.1},
    "mex": {"oil": 3, "steel": 2, "rare": 1},
    "arg": {"oil": 2, "steel": 1, "rare": 1},
    "egy": {"oil": 1.5, "steel": 0.5, "rare": 0.2},
    "jpn": {"oil": 0.1, "steel": 20, "rare": 1},
    "wger": {"oil": 0.3, "steel": 14, "rare": 1},
    "gbr": {"oil": 0.4, "steel": 8, "rare": 1},
    "fra": {"oil": 0.2, "steel": 6, "rare": 1},
    "pol": {"oil": 0.2, "steel": 6, "rare": 0.4},
    "ita": {"oil": 0.2, "steel": 5, "rare": 0.3},
    "cze": {"oil": 0.1, "steel": 5, "rare": 0.4},
    "ind": {"oil": 0.3, "steel": 4, "rare": 2},
    "aus": {"oil": 0.5, "steel": 4, "rare": 8},
    "eger": {"oil": 0.1, "steel": 3, "rare": 0.3},
    "bra": {"oil": 0.4, "steel": 3, "rare": 4},
    "zaf": {"oil": 0.1, "steel": 3, "rare": 7},
    "esp": {"oil": 0.1, "steel": 3, "rare": 0.5},
    "swe": {"oil": 0.1, "steel": 3, "rare": 1},
    "bel": {"oil": 0.1, "steel": 3, "rare": 0.3},
    "prk": {"oil": 0.05, "steel": 2, "rare": 2},
    "tur": {"oil": 0.3, "steel": 1, "rare": 1},
    "kor": {"oil": 0.05, "steel": 1, "rare": 0.5},
    "chl": {"oil": 0.1, "steel": 1, "rare": 6},
    "per": {"oil": 0.4, "steel": 1, "rare": 4},
    "nor": {"oil": 0.2, "steel": 1, "rare": 0.5},
    "mys": {"oil": 0.5, "steel": 0.2, "rare": 6},
    "cod": {"oil": 0.1, "steel": 0.5, "rare": 6},
    "cub": {"oil": 0.05, "steel": 0.2, "rare": 3},
    "zmb": {"oil": 0.05, "steel": 0.2, "rare": 4},
    "twn": {"oil": 0.05, "steel": 0.5, "rare": 0.5},
    "dvn": {"oil": 0.05, "steel": 0.3, "rare": 1},
    "rvn": {"oil": 0.05, "steel": 0.2, "rare": 0.3},
    "isr": {"oil": 0.05, "steel": 0.3, "rare": 0.3},
}

#: A country neither table names. Small and even: an unnamed minor produces a little of
#: everything, so it is never the world's swing supplier and never a black hole either.
_DEFAULT: dict[str, float] = {"oil": 0.4, "steel": 0.4, "rare": 0.4}


_TABLES: dict[Era, dict[str, dict[str, float]]] = {
    Era.INTERWAR: ENDOWMENT_1936,
    Era.COLD_WAR: ENDOWMENT_1972,
    Era.MODERN: ENDOWMENT_2026,
}


def endowment_for(nation_id: str, *, era: Era) -> dict[str, float]:
    """Monthly production of each good for one nation. Never empty."""
    table = _TABLES[era]
    key = nation_id[2:] if nation_id.startswith("m_") else nation_id
    found = table.get(key)
    return dict(found) if found else dict(_DEFAULT)
