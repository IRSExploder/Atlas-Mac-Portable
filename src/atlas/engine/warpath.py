"""When a power may start a war of its own, and when it may not.

**Until now no AI power could ever start one.** `declarations` belongs to the world simulator
and `relation.at_war` is engine-only, so every war on every map was one the narrator chose to
open. That is why the victory layer never activated: ground is 34% of the score, ground moves
by conquest, and conquest was not a decision any government got to make. Measured, playing
flat out on everything a power *could* move carried the leader from a 1.11x lead to 1.16x
against the 2.00x hegemony asks for.

**The machinery is not new and deliberately so.** `resolution._declare_war` already opens
wars for the player and for the simulator; this adds a gate in front of it rather than a
second path, because two ways to start a war is how one of them ends up skipping the pacts.

What is new is that a *government* is asking, and a government is not the narrator. The
simulator is trusted to declare sensibly because it can see the whole board and has no side.
A power has a side, and the failure mode is a map where everybody declares on everybody the
turn they are told they can. So four gates, and each answers a different way that goes wrong:

``reach``
    you cannot declare war on somebody you cannot fight. A war nobody can prosecute is a
    flag on a relation that never resolves, which is exactly what `_declare_war` already
    refuses for international bodies and for the same reason.
``grounds``
    a war needs a *reason the world can see*: somebody holding your country, an ally already
    fighting them, or relations bad enough that nobody is surprised. Without this the gate is
    only about capability, and capability is not motive.
``capacity``
    a power already fighting does not open another front. This is what stops one aggressive
    government cascading into a world war by itself.
``restraint``
    and not more than one war in `COOLDOWN_DAYS`, so a power cannot declare its way around
    the capacity limit by ending one war and opening the next the same season.
``standing``
    and, last, what rule WAR-001 will make of it. The four gates above are warpath's own
    and the fifth is not: it asks the rule the declaration is actually going to meet. A
    government offered a target that WAR-001 then silently refuses has been told it may do
    something it may not, which is worse than being refused -- it spends the turn, gets a
    line in `problems` nobody wrote for it, and is offered the same target again next turn.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

__all__ = [
    "COOLDOWN_DAYS",
    "HOSTILE_BELOW",
    "MAX_CONCURRENT_WARS",
    "Verdict",
    "may_declare",
    "occupied_homeland",
    "reasons_to_fight",
]

#: How badly two powers must already be getting on before "we simply dislike them" is a
#: reason the world would accept. **Well below neutral**: relations start at zero and drift,
#: and a threshold near it would make almost every pair of powers a legitimate target.
HOSTILE_BELOW = -35.0

#: How many wars a power may be in before it stops opening more. Two, because a great power
#: fighting on two fronts is a recognisable historical position and one fighting on three is
#: a power that has stopped making decisions.
MAX_CONCURRENT_WARS = 2

#: How long after declaring a war a power must wait before declaring another, **in days of
#: world time**.
#:
#: The gate the other three lean on: without it a power that wins quickly can open a new war
#: every few months and the map churns, and with it a declaration is a commitment measured in
#: years. Two years.
#:
#: **Counted in days rather than turns, and the first draft got that wrong.** Written as a
#: turn count it meant eight months at monthly pacing and eight *years* at yearly, so how
#: often a country could start a war depended on how often somebody looked at the game.
#: `test_every_duration_is_on_a_clock_somebody_named` refused it, correctly: this is elapsed
#: history and nothing about anybody's attention.
COOLDOWN_DAYS = 730.0


@dataclass(frozen=True, slots=True)
class Verdict:
    """Whether this power may declare on that one, and the sentence saying why not."""

    allowed: bool
    because: str = ""
    #: What the world would accept as the reason, when there is one. Written into the
    #: declaration so the event log says why rather than only that it happened.
    grounds: str = ""
    #: What the declaration would cost the aggressor in legitimacy, and ``0.0`` when the
    #: grievance makes it free.
    #:
    #: **Disclosed rather than discovered.** A government told it may declare and not told
    #: what declaring costs is being quoted a price it cannot see; the whole argument for
    #: naming the eligible targets at all is that a staff should not have to spend a turn
    #: finding out what the gate thinks.
    cost: float = 0.0


def _is_organisation(conn: sqlite3.Connection, nation_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM organisation WHERE nation_id = ?", (nation_id,)).fetchone() is not None


def _wars_of(conn: sqlite3.Connection, world_id: str, nation_id: str) -> int:
    """How many *opponents* this power is at war with.

    **Counted as distinct enemies, not as rows.** `relation` is directed -- two rows per
    pair, because two powers need not feel the same way about each other -- so counting rows
    doubles every war and quietly halved this gate: `MAX_CONCURRENT_WARS = 2` was enforcing
    one. Caught by the test for the gate rather than in play, which is the only reason it is
    not in the measurement above.
    """
    return int(conn.execute(
        "SELECT COUNT(DISTINCT CASE WHEN nation_a = ? THEN nation_b ELSE nation_a END) c"
        "  FROM relation WHERE world_id = ? AND at_war = 1"
        "   AND (nation_a = ? OR nation_b = ?)",
        (nation_id, world_id, nation_id, nation_id)).fetchone()["c"])


def _last_declared(conn: sqlite3.Connection, world_id: str, nation_id: str) -> str | None:
    """The in-world date this power last opened a war, or ``None`` if it never has.

    Read from the event log rather than a column: a declaration is already recorded there
    with its actor and its date, and a second copy in `nation` would be one more thing a
    rewind has to put back.
    """
    row = conn.execute(
        "SELECT date FROM event"
        " WHERE world_id = ? AND kind = 'war_declared' AND actor_nation_id = ?"
        " ORDER BY id DESC LIMIT 1",
        (world_id, nation_id)).fetchone()
    return str(row["date"]) if row and row["date"] else None


def _today(conn: sqlite3.Connection, world_id: str) -> str:
    """The world's own date, for a caller that did not pass one."""
    row = conn.execute(
        "SELECT world_date FROM world WHERE id = ?", (world_id,)).fetchone()
    return str(row["world_date"]) if row and row["world_date"] else ""


def _can_reach(conn: sqlite3.Connection, world_id: str, a: str, b: str) -> bool:
    """Whether ``a`` could actually bring force to bear on ``b``.

    A shared land border, or a fleet -- a power with warships can reach a coast, which is
    how most declarations between powers that do not touch have ever happened. Deliberately
    generous about *how*: this asks whether a war is prosecutable at all, and
    `atlas.engine.logistics` decides every harder question afterwards.
    """
    touching = conn.execute(
        """
        SELECT 1 FROM region_adjacency adj
          JOIN region ra ON ra.id = adj.region_a
          JOIN region rb ON rb.id = adj.region_b
         WHERE adj.world_id = ?
           AND ra.kind <> 'ocean' AND rb.kind <> 'ocean'
           AND ((COALESCE(ra.controller_nation_id, ra.owner_nation_id) = ?
                 AND COALESCE(rb.controller_nation_id, rb.owner_nation_id) = ?)
             OR (COALESCE(ra.controller_nation_id, ra.owner_nation_id) = ?
                 AND COALESCE(rb.controller_nation_id, rb.owner_nation_id) = ?))
         LIMIT 1
        """,
        (world_id, a, b, b, a)).fetchone()
    if touching:
        return True
    fleet = conn.execute(
        "SELECT COUNT(*) c FROM unit u"
        "  LEFT JOIN unit_template t ON t.world_id = u.world_id AND t.kind = u.kind"
        " WHERE u.world_id = ? AND u.nation_id = ? AND COALESCE(t.domain, 'land') = 'sea'",
        (world_id, a)).fetchone()
    return int(fleet["c"]) > 0


def occupied_homeland(
    conn: sqlite3.Connection, world_id: str, aggressor: str, target: str
) -> int:
    """How many provinces of ``aggressor``'s own country ``target`` is standing on.

    Its own function because two layers need the same answer and were giving different
    ones. `reasons_to_fight` called this warpath's strongest grievance -- "the one
    grievance every era recognises" -- while WAR-001, which is what actually decides,
    had never heard of it and charged the reclamation as unprovoked aggression.

    ``home_nation_id`` and not ``owner_nation_id``: this is ground that *is* the
    aggressor's country, whoever currently owns or controls it, which is the whole
    distinction that makes retaking it a reclamation rather than a conquest.
    """
    row = conn.execute(
        "SELECT COUNT(*) c FROM region WHERE world_id = ? AND home_nation_id = ?"
        "   AND kind <> 'ocean'"
        "   AND COALESCE(controller_nation_id, owner_nation_id) = ?",
        (world_id, aggressor, target)).fetchone()
    return int(row["c"]) if row else 0


def reasons_to_fight(
    conn: sqlite3.Connection, world_id: str, aggressor: str, target: str
) -> str:
    """What the world would accept as this power's reason, or an empty string.

    Ordered strongest first, and the strongest is territorial: somebody standing on ground
    whose `home_nation_id` says it is yours is the one grievance every era recognises.
    """
    held = occupied_homeland(conn, world_id, aggressor, target)
    if held > 0:
        return f"they hold {held} province(s) of our country"

    ally = conn.execute(
        """
        SELECT 1 FROM relation mine
          JOIN relation theirs ON theirs.world_id = mine.world_id
                              AND theirs.nation_a = mine.nation_b
                              AND theirs.at_war = 1
                              AND theirs.nation_b = ?
         WHERE mine.world_id = ? AND mine.nation_a = ?
           AND mine.treaty_status IN ('defensive', 'alliance')
         LIMIT 1
        """,
        (target, world_id, aggressor)).fetchone()
    if ally:
        return "an ally we are sworn to is already fighting them"

    row = conn.execute(
        "SELECT score FROM relation WHERE world_id = ? AND nation_a = ? AND nation_b = ?",
        (world_id, aggressor, target)).fetchone()
    if row is not None and float(row["score"]) <= HOSTILE_BELOW:
        return f"relations stand at {float(row['score']):.0f} and have for some time"
    return ""


def may_declare(
    conn: sqlite3.Connection, world_id: str, *, aggressor: str, target: str, turn: int,
    date: str = "",
) -> Verdict:
    """Whether this government may open a war on that one, and why not if it may not.

    Every refusal names the gate it failed, because a staff told only "no" asks the same
    question again next turn -- the rule this project has applied to every other refusal.
    """
    if not aggressor or not target or aggressor == target:
        return Verdict(False, "a power cannot declare war on itself")
    for party in (aggressor, target):
        if _is_organisation(conn, party):
            return Verdict(
                False, "an international body holds no ground and cannot be at war")
    if conn.execute(
        "SELECT at_war FROM relation WHERE world_id = ? AND nation_a = ? AND nation_b = ?",
        (world_id, aggressor, target)).fetchone() is None:
        return Verdict(False, "these two powers have no relation on this map")
    if int(conn.execute(
        "SELECT at_war FROM relation WHERE world_id = ? AND nation_a = ? AND nation_b = ?",
        (world_id, aggressor, target)).fetchone()["at_war"] or 0):
        return Verdict(False, "you are already at war with them")

    mine = _wars_of(conn, world_id, aggressor)
    if mine >= MAX_CONCURRENT_WARS:
        return Verdict(
            False,
            f"you are already fighting {mine} war(s); a power fighting {MAX_CONCURRENT_WARS} "
            "does not open another")

    since = _last_declared(conn, world_id, aggressor)
    if since is not None:
        from .pacing import days_between

        now = date or _today(conn, world_id)
        waited = days_between(since, now) if now else None
        if waited is not None and waited < COOLDOWN_DAYS:
            return Verdict(
                False,
                f"you declared a war on {since} and it is {now}; a declaration is a "
                f"commitment, and the next one waits {COOLDOWN_DAYS / 365:.0f} years")

    if not _can_reach(conn, world_id, aggressor, target):
        return Verdict(
            False,
            "you share no border with them and have no fleet to reach them; a war you "
            "cannot prosecute is a flag on a relation, not a war")

    grounds = reasons_to_fight(conn, world_id, aggressor, target)
    if not grounds:
        return Verdict(
            False,
            "you have no grievance the world would recognise: they hold none of your "
            "country, no ally of yours is fighting them, and relations are not bad enough "
            "for anybody to believe you")

    # **The last gate is somebody else's.** Warpath's four gates are about this government
    # and this target; WAR-001 is about the declaration, and it is the one that decides.
    # Asking it here rather than discovering its answer afterwards is the whole point:
    # `grounds` is what the *world* would accept and a casus belli is what the *engine* has
    # recorded, and they are not the same test -- so a power with a grievance everybody can
    # see and no legitimacy left to spend was being offered a target it could not take.
    from .resolution import declaration_cost

    charge = declaration_cost(
        conn, world_id, aggressor=aggressor, target=target, turn=turn)
    if charge.refused:
        return Verdict(
            False,
            "the world has recorded no grievance in your name and you have no legitimacy "
            "left to spend on one; a declaration made now would be refused, and a refused "
            "declaration is a war that never starts")

    return Verdict(True, grounds=grounds, cost=charge.cost if not charge.provoked else 0.0)
