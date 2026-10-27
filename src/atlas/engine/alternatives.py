"""What a refused proposal could have said instead, read off the same state that refused it.

`causality.Refusal` has carried three fields since it was written, and the third is the one
that changes behaviour: the *legal equivalent*, empty rather than invented when there is
none. Thirty of the forty-four refusal sites fill it. The fourteen that do not are the ones
where the answer is not a sentence but a **list** -- which powers this undertaking is open
against, which provinces this aim could name, which undertakings a country with no warheads
can actually attempt -- and writing that list by hand at each site would have been fourteen
copies of a query that has to agree with the gate beside it.

So each function here answers exactly one refusal's question, using the same predicate the
refusal used. **Nothing is suggested that the rules would not accept**: every list is drawn
by running the real requirement, and an empty list renders as no suggestion at all rather
than as encouragement to try something that would also fail. A suggestion the engine would
refuse is worse than silence -- it teaches a proposer that the refusals are noise.

Bounded on purpose. Each returns at most a handful of names: a refusal that answers with
two hundred provinces has not helped anybody.
"""

from __future__ import annotations

import sqlite3

__all__ = [
    "MOST",
    "open_undertakings",
    "phrase",
    "reachable_regions",
    "standing_enemies",
    "ground_held_by",
    "live_nations",
]

#: How many names a refusal may offer. Enough to be a choice, few enough to be read.
MOST = 4


def phrase(names: list[str], *, lead: str, tail: str = "") -> str:
    """Render a list as a suggestion, or nothing at all when the list is empty.

    **The empty case is the point.** A refusal with nothing legal to offer must say only
    that it refused; `Refusal.instead` is documented as empty rather than invented, and this
    is where that promise is kept for every caller at once.
    """
    if not names:
        return ""
    shown = names[:MOST]
    listed = shown[0] if len(shown) == 1 else (
        ", ".join(shown[:-1]) + " or " + shown[-1])
    return f"{lead} {listed}{tail}."


def _named(conn: sqlite3.Connection, world_id: str, ids: list[str]) -> list[str]:
    """``name (`id`)`` for each, in the order given, so a proposer can copy either."""
    if not ids:
        return []
    rows = {
        str(row["id"]): str(row["name"])
        for row in conn.execute(
            f"SELECT id, name FROM nation WHERE world_id = ?"  # noqa: S608 - fixed shape
            f"   AND id IN ({','.join('?' * len(ids))})",
            (world_id, *ids))
    }
    return [f"{rows[i]} (`{i}`)" for i in ids if i in rows]


def live_nations(
    conn: sqlite3.Connection, world_id: str, *, besides: str = "", limit: int = MOST
) -> list[str]:
    """Powers still standing, largest first -- the answer to *there is no nation called X*.

    Ordered by economy rather than alphabetically: a proposer that typed a name the map does
    not have is usually reaching for somebody who matters.
    """
    return _named(conn, world_id, [
        str(row["id"]) for row in conn.execute(
            "SELECT id FROM nation WHERE world_id = ? AND is_alive = 1 AND id <> ?"
            " ORDER BY economy DESC, id LIMIT ?", (world_id, besides, limit))
    ])


def standing_enemies(
    conn: sqlite3.Connection, world_id: str, nation_id: str, *, limit: int = MOST
) -> list[str]:
    """Who this power is actually at war with. The answer to *a war aim belongs to a war*."""
    return _named(conn, world_id, [
        str(row["nation_b"]) for row in conn.execute(
            "SELECT r.nation_b FROM relation r JOIN nation n ON n.id = r.nation_b"
            " WHERE r.world_id = ? AND r.nation_a = ? AND r.at_war = 1 AND n.is_alive = 1"
            " ORDER BY r.nation_b LIMIT ?", (world_id, nation_id, limit))
    ])


def ground_held_by(
    conn: sqlite3.Connection, world_id: str, holder: str, *, limit: int = MOST,
    home_of: str | None = None,
) -> list[str]:
    """Provinces ``holder`` is standing on, as ``name (`id`)``.

    ``home_of`` narrows it to ground that is somebody's own people's, which is what
    `recover_cores` means and the only list that aim can honestly be offered.
    """
    sql = ("SELECT id, name FROM region WHERE world_id = ? AND kind <> 'ocean'"
           "   AND COALESCE(controller_nation_id, owner_nation_id) = ?")
    params: list[object] = [world_id, holder]
    if home_of is not None:
        sql += " AND home_nation_id = ?"
        params.append(home_of)
    sql += " ORDER BY id LIMIT ?"
    params.append(limit)
    return [f"{row['name']} (`{row['id']}`)" for row in conn.execute(sql, params)]


def reachable_regions(
    conn: sqlite3.Connection, world_id: str, nation_id: str, *, limit: int = MOST
) -> list[str]:
    """Ground this power could plausibly aim something at: what it holds and what borders
    it. The answer to *there is no region called X* and to *name the region*.

    Deliberately the same walk the front uses -- what you hold and what touches it -- rather
    than a cleverer notion of reach, because a suggestion that needed its own definition of
    reachable would be a second thing to keep in step with the first.
    """
    rows = conn.execute(
        """
        SELECT DISTINCT r.id AS id, r.name AS name FROM region r
         WHERE r.world_id = ? AND r.kind <> 'ocean'
           AND (COALESCE(r.controller_nation_id, r.owner_nation_id) = ?
                OR EXISTS (SELECT 1 FROM region_adjacency a
                             JOIN region mine ON mine.id = CASE
                                 WHEN a.region_a = r.id THEN a.region_b ELSE a.region_a END
                            WHERE (a.region_a = r.id OR a.region_b = r.id)
                              AND COALESCE(mine.controller_nation_id,
                                           mine.owner_nation_id) = ?))
         ORDER BY r.id LIMIT ?
        """,
        (world_id, nation_id, nation_id, limit),
    ).fetchall()
    return [f"{row['name']} (`{row['id']}`)" for row in rows]


def open_undertakings(
    conn: sqlite3.Connection, world_id: str, nation_id: str, *, target: str | None = None,
    limit: int = MOST,
) -> list[str]:
    """Which undertakings this power could actually attempt right now.

    **Answered by running the real gate**, not by a second table of what is usually
    possible: each candidate is put through `gambit._requirements_met` exactly as a real
    attempt would be, so nothing is offered that the next call would refuse. That is the
    whole discipline of this module in one function.
    """
    from .directives import active, favour
    from .gambit import KINDS, _requirements_met

    policy = active(conn)
    open_to: list[tuple[float, str]] = []
    for kind in KINDS:
        aim = "forces" if kind.id == "air_strike" else ""
        if _requirements_met(conn, world_id, kind, nation_id, target, None, aim) is None:
            open_to.append((favour(policy, kind.id, target or ""), f"`{kind.id}`"))

    # **Ordered by the government's own standing policy, then by the catalogue.** A refusal
    # that offers four alternatives should offer the four this government would actually
    # consider first -- and the cut to `limit` happens *after* the sort, so a policy
    # reorders which four are shown rather than only shuffling four already chosen.
    open_to.sort(key=lambda entry: -entry[0])
    return [name for _weight, name in open_to[:limit]]
