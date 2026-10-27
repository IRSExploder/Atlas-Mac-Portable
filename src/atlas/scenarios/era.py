"""Which period a scenario is set in, for the three tables that cannot be era-neutral.

Most of what makes an era an era is already carried on the scenario itself: its powers,
its unit templates, its ship classes, its research catalogue. Three things are not,
because they are keyed by *nation* rather than by scenario and one entry has to serve a
country whether a scenario promoted it to a power or left it a minor:

* who governs it (:mod:`atlas.scenarios.leaders`),
* what it digs out of the ground (:mod:`atlas.scenarios.resources`),
* how many people live in it (:mod:`atlas.scenarios.demography`).

Those three were selected by ``era_1936 = scenario.start_date < "1950"`` -- a boolean,
which is exactly enough for two eras and not for three. A 1972 world falling through to
the modern branch would be governed by the politicians of the 2020s and populated with
2020s census figures, and ``sov`` is not in the modern population table at all, so the
Soviet Union would have taken the three-million default for an unnamed minor.

**An enum rather than a date comparison**, because the boundary between two eras is a
judgement and not a fact about a number: 1946 and 1989 are both "after 1950" and neither
belongs in the same table as 2026. A scenario states which era it is; nothing infers it.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["Era"]


class Era(StrEnum):
    """The period a scenario is set in.

    Values are stable strings because they may end up in a saved preset one day; nothing
    stores them today, and no database CHECK constraint mentions them.
    """

    #: Between the wars. 1936 - Gathering Storm.
    INTERWAR = "interwar"
    #: The bipolar decades. 1972 - Cold War.
    COLD_WAR = "cold_war"
    #: The present. Modern Day, and the default for a world with no designed scenario.
    MODERN = "modern"
