"""Proper nouns from the world in front of the player, for copy that would otherwise be
invented.

Interface text that gives an *example* has to name something, and there are only two
places the name can come from: a string written when the code was written, or the world
the player is actually looking at. The first is what the orders box did --
``Reoccupy the Rhineland. Sound out Italy about Austria.`` -- which is a good example of an
order and the wrong century for two of the three shipped eras. A player opening Modern Day
was shown a 1936 crisis in the one box whose entire job is *write what you want, in your
own words*.

So this reads the second. A province the player actually holds, the power they are most
obviously at odds with, and the one they are most obviously on good terms with. Nothing
here is authored: no scenario has to remember to supply an example, and an era somebody
writes in the workshop tomorrow gets a grounded one for free.

**Every read is one statement and returns one row.** This is called from `bind`, which
runs after every jump and every rewind, and the interface has already been taken apart
once for asking a question per nation. The obvious call for *who are the other powers* --
:func:`atlas.state.visibility.other_nations` -- carries an adjacency EXISTS per nation and
is 242 of them on the Modern Day map; it is the right read for a briefing and much too
much for a placeholder.

Everything degrades to a neutral prompt rather than to a wrong one. A spectator has no
provinces, a one-power world has nobody to sound out, and a sentence naming neither is
better than a sentence naming a country that is not there.
"""

from __future__ import annotations

import sqlite3

__all__ = ["NEUTRAL_PROMPT", "a_friend", "a_province", "a_rival", "example_order"]

#: What a box says when the world cannot ground it. Era-agnostic on purpose, and phrased
#: as the instruction rather than as an example: with nothing real to name, an example is
#: a made-up one.
NEUTRAL_PROMPT = "Say what you want to do this turn, in plain English."

#: One relation, picked by an ordering this module supplies.
#:
#: Keyed on ``nation_a`` alone: `relation` holds both directions of every pair, which is
#: the invariant :func:`atlas.state.visibility.other_nations` already joins on. Matching
#: either side would read each pair twice for a result that is ``LIMIT 1`` anyway.
_RELATION = """
    SELECT n.name AS name
      FROM relation r
      JOIN nation n ON n.world_id = r.world_id AND n.id = r.nation_b
     WHERE r.world_id = ?2 AND r.nation_a = ?1 AND n.is_alive = 1
     ORDER BY {order}
     LIMIT 1
"""


def a_rival(conn: sqlite3.Connection, world_id: str, nation_id: str | None) -> str:
    """Whoever this government is most obviously at odds with, in one query.

    War first, then the worst regard -- because a power you are fighting is the answer
    whatever the number beside it says, and a war is the one relation a player never has to
    be told about.
    """
    return _one(conn, world_id, nation_id, "r.at_war DESC, r.score ASC, n.id")


def a_friend(conn: sqlite3.Connection, world_id: str, nation_id: str | None) -> str:
    """Whoever it is most obviously on good terms with. The mirror of :func:`a_rival`.

    A power you are at war with is excluded before regard is considered, for the same
    reason and in the opposite direction: warm relations with somebody you are fighting is
    a state the world can reach, and *sound out our enemy about our enemy* is not an
    example of anything.
    """
    return _one(conn, world_id, nation_id, "r.at_war ASC, r.score DESC, n.id")


#: The pack sources that draw whole countries. A ``geo_key`` names the source it came from
#: -- ``admin0-50m:USA``, ``admin1-10m:VNM-462`` -- which is the decoupling `PackSet` is
#: built on, and it is also the only *exact* answer to "is this region a country or a
#: province in one". `region.kind` is terrain, and matching the region's name against the
#: nation's catches *Germany* while missing *United States of America* against *United
#: States*. Synthetic worlds key their regions differently (`geo:northmark`) and keep them.
_COUNTRY_SHAPED = "admin0-%"


def a_province(conn: sqlite3.Connection, world_id: str, nation_id: str | None) -> str:
    """A province of theirs a player would recognise: the one with the most people in it.

    Largest rather than first, because the point of naming a place is that the reader
    knows it without looking it up. Ties broken by id, so the same world always shows the
    same example.

    **A country is not one of its own provinces.** Only some countries are drawn at
    province granularity -- Modern Day gives Ukraine twenty-odd oblasts and the United
    States a single region called *United States of America* -- and the whole country
    coming back from this reads as `Reinforce Germany.` to the player of Germany. So a
    country-shaped region is not a candidate, whoever holds it: the United States of 1972
    holds Guam and American Samoa as well as itself, and none of the three is a province.
    A power with nothing else gets no place named and the caller says something else.
    """
    if not nation_id:
        return ""
    row = conn.execute(
        "SELECT name FROM region"
        " WHERE world_id = ? AND owner_nation_id = ? AND geo_key NOT LIKE ?"
        " ORDER BY population DESC, id LIMIT 1",
        (world_id, nation_id, _COUNTRY_SHAPED),
    ).fetchone()
    return str(row["name"]) if row else ""


def example_order(conn: sqlite3.Connection, world_id: str,
                  nation_id: str | None) -> str:
    """A plausible order for *this* world, in the shape the orders box has to teach.

    Two clauses, one unilateral and one diplomatic, because what the box needs to get
    across is not any particular order -- it is the *range* of what it will take. That
    shape is the one thing kept from the sentence this replaces.

    The words are ordinary English rather than terms from the capability manifest,
    deliberately. A placeholder built out of the parser's own aliases would teach a player
    that there are magic words, and the whole argument for a free-text box is that there
    are not.
    """
    if not nation_id:
        return NEUTRAL_PROMPT

    # A place if there is one to name, and something a country can always do if there is
    # not. Either way the box gets its unilateral half.
    province = a_province(conn, world_id, nation_id)
    clauses = [f"Reinforce {province}." if province else "Raise more troops."]

    rival = a_rival(conn, world_id, nation_id)
    friend = a_friend(conn, world_id, nation_id)
    if rival and friend and friend != rival:
        clauses.append(f"Sound out {friend} about {rival}.")
    elif rival:
        # A world with one other power in it, or one where the only relation on file is
        # the quarrel. Still an example, and still names somebody real.
        clauses.append(f"Send an envoy to {rival}.")

    return " ".join(clauses) or NEUTRAL_PROMPT


def _one(conn: sqlite3.Connection, world_id: str, nation_id: str | None,
         order: str) -> str:
    if not nation_id:
        return ""
    # `order` is a literal from this module, never from a caller's data.
    row = conn.execute(
        _RELATION.format(order=order), (nation_id, world_id)  # noqa: S608
    ).fetchone()
    return str(row["name"]) if row else ""
