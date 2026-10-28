"""Standing replacements: worn formations in supply fill back up a little every month.

**What changed that needed this.** On a map of one region per country a war was decided in
two turns, and an army never had time to wear out. Drawn province by province it is a
campaign of ten or twenty, and losses that were incidental became decisive: a play-test had
Turkey's army bleed from 54 to 12 strength across ten months of "capture Iran" while Iran's
grew from 41 to 61, and the whole army was gone by the eleventh. Nothing refilled it unless
the player knew to write "reinforce" -- which every engine power was being told to do, and a
player was not.

**This is the same replacement the Reinforce order buys, at the same prices and under the
same supply rule**, run for every power each month at a fraction of the rate. A depot keeps
sending men; an order to reinforce is what sends them *faster*. Formations out of supply get
nothing, so a pocket still dies, and every power is treated alike, the player included.
"""

from __future__ import annotations

import sqlite3

from .orders import _UNIT_STRENGTH, OrderEffects, _reinforce
from .research import strength_bonus

__all__ = ["REFIT_BUDGET_SHARE", "REFIT_FRACTION", "refit"]

#: How much of a formation's establishment comes back each month on its own. A tenth: a
#: division mauled to half strength is whole again in about five quiet months, and one in
#: the line every month is slowed down rather than kept topped up.
REFIT_FRACTION = 0.10

#: How much of the treasury a month's standing replacements may spend. Less than an order
#: to reinforce, so that ordering it still matters.
REFIT_BUDGET_SHARE = 0.2


def refit(conn: sqlite3.Connection, world_id: str, *, turn: int, date: str,
          span: float = 1.0) -> OrderEffects:
    """Refit every living power's worn formations that are in supply. Quiet when there is
    nothing to do; a refusal by the rules is reported like any other."""
    effects = OrderEffects(span=span)
    base = {str(r[0]): float(r[1] or 0.0) for r in conn.execute(
        "SELECT kind, base_strength FROM unit_template WHERE world_id = ?", (world_id,))}
    worn: list[str] = []
    for nation_id, units in _units_by_nation(conn, world_id).items():
        bonus = strength_bonus(conn, world_id, nation_id)
        # A world with no template for a kind prices it as the Reinforce order does.
        if any(strength < base.get(kind, _UNIT_STRENGTH) + bonus.get(kind, 0.0) - 0.01
               for kind, strength in units):
            worn.append(nation_id)
    for nation_id in worn:
        _reinforce(conn, world_id, turn, date, {}, str(nation_id), effects,
                   max_fraction=REFIT_FRACTION, budget_share=REFIT_BUDGET_SHARE,
                   quiet=True)
    return effects


def _units_by_nation(conn: sqlite3.Connection, world_id: str) -> dict[str, list]:
    out: dict[str, list] = {}
    for nation_id, kind, strength in conn.execute(
            "SELECT u.nation_id, u.kind, u.strength FROM unit u"
            "  JOIN nation n ON n.id = u.nation_id AND n.world_id = u.world_id"
            " WHERE u.world_id = ? AND n.is_alive = 1 ORDER BY u.nation_id, u.id",
            (world_id,)):
        out.setdefault(str(nation_id), []).append((str(kind), float(strength)))
    return out
