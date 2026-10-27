"""What a belligerent says it is fighting for, and what the engine will let it claim.

**A war here has never had a point.** It is two directed ``relation`` rows, opened by a
declaration and closed by a settlement, and between those two moments there is nowhere at
all to record what either side wants out of it. So a peace could only ever be *stop
shooting, and here is some ground*: nothing to measure a settlement against, nothing for a
negotiator to trade away, and no way for a defender to say the one thing every defender in
history has said, which is that it wants its own back.

This is the first layer of the war loop and deliberately only the first. It records aims.
It does not compute exhaustion, does not price leverage, and does not settle anything --
those are the layers above, and each of them will read this table rather than replace it.

**Intent, never a write path**, exactly like :mod:`atlas.engine.objectives`. An aim names a
war and, where the kind is about ground, a province. It carries no outcome, no odds, no
claim about what anybody else wants, and it reaches no territory column. The engine checks
each one against the war and the map, refuses what cannot be true, and writes what survives
as diffs under its own role.

**Per belligerent, per enemy -- which is what makes a coalition work without a coalition.**
Britain and France can fight the same war for different things, and each holds its own rows.
That is also the seam the separate-peace layer will need: settling France's war does not
settle Britain's, because they were never one record.

**Standing until restated, per enemy.** Declaring aims against Germany replaces this power's
aims against Germany and leaves its aims against Italy alone. Silence about a war is not
surrender in it -- the same rule the objectives channel learned the hard way.

**Aims lapse when the war does.** :func:`prune_war_aims` drops the aims of a war that has
ended, because an aim is a thing you are fighting for and there is no longer any fighting.
Nothing here prunes on the *map*: an annexation aim over ground you now hold is achieved
rather than stale, and deciding what achievement is worth belongs to the settlement layer.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..state.apply import apply_batch, record_event
from ..state.diff import Diff, DiffBatch
from ..state.models import DiffOp, EventKind, EventOrigin, Role, Visibility
from . import reference
from .alternatives import phrase
from .causality import Refusal

__all__ = [
    "AIM_KINDS",
    "WAR_AIM_LIMIT",
    "WarAim",
    "WarAimReport",
    "aim_id_for",
    "aims_against",
    "aims_of",
    "declare_war_aims",
    "implied_regions",
    "prune_war_aims",
]

#: The six things a belligerent can be fighting for, and what each one means mechanically
#: once the layers above this one exist. Each names its ground requirement, because that is
#: the difference between an aim the engine can check and a slogan.
#:
#: **Six, and not more.** Every kind here has to be something a settlement could later
#: *contain* -- a term a peace treaty can carry and the engine can enforce. A kind with no
#: possible term is flavour, and flavour in this table would be a war aim that can never be
#: satisfied, conceded or refused.
AIM_KINDS: dict[str, str] = {
    "defend": "hold what you have; the war ends when the invasion does",
    "recover_cores": "take back ground that is your own people's, held by the enemy",
    "annex": "keep named ground of the enemy's when the war ends",
    "regime_change": "the government you are fighting must go",
    "independence": "named ground of the enemy's should become a state of its own",
    "demilitarise": "named ground of the enemy's must be emptied of its forces",
}

#: Kinds that name a province, and kinds that are about the war itself.
_NEEDS_GROUND = frozenset({"annex", "independence", "demilitarise"})
_FORBIDS_GROUND = frozenset({"defend", "regime_change"})

#: How many aims one power may hold against one enemy. A war fought for eleven things is a
#: war whose settlement cannot be reasoned about, and the cap is the same shape as
#: ``OBJECTIVE_LIMIT``: enough to be expressive, small enough to be answerable.
WAR_AIM_LIMIT = 5


@dataclass(frozen=True, slots=True)
class WarAim:
    """One aim: whose, against whom, of what kind, over what ground."""

    nation_id: str
    enemy_id: str
    kind: str
    subject_id: str = ""
    declared_turn: int = 0

    def describe(self, names: dict[str, str] | None = None) -> str:
        names = names or {}
        enemy = names.get(self.enemy_id, self.enemy_id)
        if not self.subject_id:
            return f"{self.kind.replace('_', ' ')} (against {enemy})"
        ground = names.get(self.subject_id, self.subject_id)
        return f"{self.kind.replace('_', ' ')}: {ground} (against {enemy})"


@dataclass(slots=True)
class WarAimReport:
    """What was accepted and what was refused, with a reason the power can act on."""

    nation_id: str
    accepted: list[WarAim] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    #: Wars whose aims were replaced by this declaration, so the caller can say so.
    wars_restated: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.accepted)


def aim_id_for(nation_id: str, enemy_id: str, kind: str, subject_id: str = "") -> str:
    """Derived, not autoincremented, so an aim survives a rewind with its identity."""
    return f"wa_{nation_id}_{enemy_id}_{kind}" + (f"_{subject_id}" if subject_id else "")


def aims_of(
    conn: sqlite3.Connection, world_id: str, nation_id: str, *, enemy_id: str | None = None
) -> dict[str, tuple[WarAim, ...]]:
    """What ``nation_id`` is fighting for, grouped by the enemy it is fighting."""
    sql = ("SELECT nation_id, enemy_id, kind, subject_id, declared_turn FROM war_aim"
           " WHERE world_id = ? AND nation_id = ?")
    params: list[str] = [world_id, nation_id]
    if enemy_id:
        sql += " AND enemy_id = ?"
        params.append(enemy_id)
    return _grouped(conn.execute(sql + " ORDER BY enemy_id, kind, subject_id", params),
                    by="enemy_id")


def aims_against(
    conn: sqlite3.Connection, world_id: str, nation_id: str
) -> dict[str, tuple[WarAim, ...]]:
    """What everybody else says they are fighting ``nation_id`` for, grouped by who.

    **Public, and that is the design rather than an oversight.** A war aim is a thing
    powers announce: it is the demand, and a demand nobody has heard cannot be met,
    refused or negotiated down. The private version of this is an ultimatum, which the
    world already has.
    """
    return _grouped(
        conn.execute(
            "SELECT nation_id, enemy_id, kind, subject_id, declared_turn FROM war_aim"
            " WHERE world_id = ? AND enemy_id = ? ORDER BY nation_id, kind, subject_id",
            (world_id, nation_id)),
        by="nation_id")


def implied_regions(
    conn: sqlite3.Connection, world_id: str, aim: WarAim
) -> tuple[str, ...]:
    """The ground this aim is about, including the ground it does not name.

    **Three of the six kinds name a province and three do not, and the three that do not
    are still about ground.** `recover_cores` is a claim on every region of your own
    people's the enemy is holding; `regime_change` is a march on a capital;  `defend` is
    whatever of yours they are currently standing on. Deriving those here rather than
    asking a model for them is the same rule the whole engine runs on: a power says what it
    wants, and the engine works out where that is.

    Empty is a real answer and a useful one -- a power fighting to `defend` against an
    invasion that has not landed yet has no front to draw.
    """
    if aim.kind in _NEEDS_GROUND:
        return (aim.subject_id,) if aim.subject_id else ()
    if aim.kind == "recover_cores":
        return tuple(
            str(row["id"]) for row in conn.execute(
                "SELECT id FROM region WHERE world_id = ? AND home_nation_id = ?"
                "   AND COALESCE(controller_nation_id, owner_nation_id) = ? ORDER BY id",
                (world_id, aim.nation_id, aim.enemy_id)))
    if aim.kind == "regime_change":
        row = conn.execute(
            "SELECT capital_region_id FROM nation WHERE world_id = ? AND id = ?",
            (world_id, aim.enemy_id)).fetchone()
        return (str(row["capital_region_id"]),) if row and row["capital_region_id"] else ()
    if aim.kind == "defend":
        return tuple(
            str(row["id"]) for row in conn.execute(
                "SELECT id FROM region WHERE world_id = ? AND owner_nation_id = ?"
                "   AND controller_nation_id = ? ORDER BY id",
                (world_id, aim.nation_id, aim.enemy_id)))
    return ()


def _grouped(rows, *, by: str) -> dict[str, tuple[WarAim, ...]]:
    found: dict[str, list[WarAim]] = {}
    for row in rows:
        aim = WarAim(
            nation_id=str(row["nation_id"]), enemy_id=str(row["enemy_id"]),
            kind=str(row["kind"]), subject_id=str(row["subject_id"] or ""),
            declared_turn=int(row["declared_turn"]))
        found.setdefault(str(row[by]), []).append(aim)
    return {key: tuple(aims) for key, aims in found.items()}


def declare_war_aims(
    conn: sqlite3.Connection,
    world_id: str,
    *,
    nation_id: str,
    declared,
    turn: int,
    date: str,
) -> WarAimReport:
    """Check a power's declared war aims against its wars and the map, and record them.

    ``declared`` is a list of ``{"against": nation, "kind": kind, "region": region?}``.

    Every refusal is reported rather than dropped, for the reason the objectives channel
    reports its own: a power told nothing believes it is fighting for something it is not,
    and will keep believing it for the rest of the war.
    """
    report = WarAimReport(nation_id=nation_id)
    if not declared:
        return report
    if not isinstance(declared, (list, tuple)):
        report.refused.append("war aims must be a list of aims")
        return report

    enemies = _enemies_of(conn, world_id, nation_id)
    holder = _holders(conn, world_id)
    owner = _owners(conn, world_id)
    homes = _homes(conn, world_id)

    wanted: dict[str, list[WarAim]] = {}
    seen: set[tuple[str, str, str]] = set()
    for entry in list(declared)[: WAR_AIM_LIMIT * 4]:
        if not isinstance(entry, dict):
            report.refused.append(f"an aim was not an object: {entry!r}")
            continue
        kind = str(entry.get("kind") or "").strip().lower()
        if kind not in AIM_KINDS:
            report.refused.append(
                f"{kind or '(none)'} is not a war aim; the kinds are "
                + ", ".join(sorted(AIM_KINDS)))
            continue

        against = reference.resolve(conn, world_id, "nation", entry.get("against"))
        if against.ambiguous:
            report.refused.append(against.problem or f"{entry.get('against')!r} is unclear")
            continue
        enemy_id = against.value or str(entry.get("against") or "")
        subject = entry.get("region") or entry.get("subject") or ""
        subject_id = ""
        if subject:
            found = reference.resolve(conn, world_id, "region", subject)
            if found.ambiguous:
                report.refused.append(found.problem or f"{subject!r} is unclear")
                continue
            subject_id = found.value or str(subject)

        problem = _refusal(
            nation_id, enemy_id, kind, subject_id, enemies, holder, owner, homes)
        if problem is not None:
            report.refused.append(problem.rendered())
            continue
        if (enemy_id, kind, subject_id) in seen:
            report.refused.append(
                f"the same aim against {enemy_id} was declared twice")
            continue
        if len(wanted.get(enemy_id, ())) >= WAR_AIM_LIMIT:
            report.refused.append(
                f"only the first {WAR_AIM_LIMIT} aims against {enemy_id} were kept")
            continue
        seen.add((enemy_id, kind, subject_id))
        wanted.setdefault(enemy_id, []).append(
            WarAim(nation_id=nation_id, enemy_id=enemy_id, kind=kind,
                   subject_id=subject_id, declared_turn=turn))

    if not wanted:
        return report

    diffs = _diffs_for(conn, world_id, nation_id, wanted, turn)
    report.accepted = [aim for aims in wanted.values() for aim in aims]
    report.wars_restated = sorted(wanted)
    if not diffs:
        return report

    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        # There is no `war_aim_declared` kind, and adding one means rebuilding a table
        # with a closed CHECK. `order_issued` is what the objectives channel uses and
        # means the same thing here: a power stated its intent, and the engine wrote it.
        kind=EventKind.ORDER_ISSUED, origin=EventOrigin.ENGINE, actor_nation_id=nation_id,
        text=_describe(conn, nation_id, report.accepted),
        # Public, for the reason `aims_against` gives: a demand nobody has heard is not a
        # demand. What a power will *settle* for stays private, and is not this.
        visibility=Visibility.PUBLIC,
    )
    outcome = apply_batch(
        conn,
        DiffBatch(
            world_id=world_id, turn=turn, proposer_role=Role.ENGINE, proposer_model=None,
            diffs=tuple(
                Diff(d.target_table, d.target_id, d.field, d.op, d.new_value,
                     reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
                for d in diffs
            ),
        ),
    )
    if not outcome.applied:
        report.refused.append(f"the war aims could not be recorded: {outcome.reason}")
        report.accepted = []
        report.wars_restated = []
    return report


def prune_war_aims(
    conn: sqlite3.Connection, world_id: str, *, turn: int, date: str
) -> list[str]:
    """Drop every aim whose war has ended. Returns what lapsed.

    An aim is what you are fighting for, so it does not outlive the fighting. Run after
    resolution, when settlements for the turn have been applied, and written as diffs so a
    rewind puts the aims back exactly as they stood.
    """
    standing = list(conn.execute(
        "SELECT nation_id, enemy_id, kind, subject_id FROM war_aim"
        " WHERE world_id = ? ORDER BY nation_id, enemy_id, kind, subject_id",
        (world_id,)))
    if not standing:
        return []

    at_war = {
        (str(row["nation_a"]), str(row["nation_b"]))
        for row in conn.execute(
            "SELECT nation_a, nation_b FROM relation WHERE world_id = ? AND at_war = 1",
            (world_id,))
    }
    stale = [row for row in standing
             if (str(row["nation_id"]), str(row["enemy_id"])) not in at_war]
    if not stale:
        return []

    event_id = record_event(
        conn, world_id=world_id, turn=turn, date=date,
        kind=EventKind.ORDER_ISSUED, origin=EventOrigin.ENGINE,
        text=f"{len(stale)} war aim(s) lapsed with the war they belonged to.",
        visibility=Visibility.PUBLIC,
    )
    outcome = apply_batch(
        conn,
        DiffBatch(
            world_id=world_id, turn=turn, proposer_role=Role.ENGINE, proposer_model=None,
            diffs=tuple(
                Diff("war_aim",
                     aim_id_for(str(row["nation_id"]), str(row["enemy_id"]),
                                str(row["kind"]), str(row["subject_id"] or "")),
                     "__delete__", DiffOp.DELETE, None,
                     reason_event_id=event_id, visibility_scope=Visibility.PUBLIC)
                for row in stale
            ),
        ),
    )
    if not outcome.applied:
        return []
    return [
        f"{row['nation_id']} is no longer at war with {row['enemy_id']}; its "
        f"{str(row['kind']).replace('_', ' ')} aim there has lapsed"
        for row in stale
    ]


# --------------------------------------------------------------------------- internals


def _theirs(holder: dict[str, str | None], enemy_id: str) -> list[str]:
    """The ground this enemy is standing on, out of the map the gate is already using.

    Read from the same dict the refusal tests against rather than re-queried, so a
    suggestion cannot describe a world the gate did not see.
    """
    return sorted(region for region, held in holder.items() if held == enemy_id)


def _refusal(  # noqa: PLR0913 - one call site, and each set answers a different question
    nation_id: str,
    enemy_id: str,
    kind: str,
    subject_id: str,
    enemies: set[str],
    holder: dict[str, str | None],
    owner: dict[str, str | None],
    homes: dict[str, str | None],
) -> Refusal | None:
    """``None`` if the aim stands, otherwise why not and what to declare instead.

    Every branch names the gate it failed. A refusal that only says no is one the power
    will make again next turn, having learned nothing.
    """
    if not enemy_id or enemy_id == nation_id:
        return Refusal(
            "a_real_enemy",
            f"{enemy_id or '(nobody)'} is not somebody you can hold aims against.",
            # Drawn from the very set the next branch tests against, so the suggestion and
            # the gate cannot disagree.
            phrase(sorted(enemies), lead="you are at war with"))
    if enemy_id not in enemies:
        # **Naming them beats telling them to go and find one.** "Declare war first" is
        # true and useless to a power that is already fighting somebody: the actionable
        # answer is the war it actually has. The generic line stays as the fallback for a
        # nation at peace, where there is no list to give.
        return Refusal(
            "at_war",
            f"you are not at war with {enemy_id}, so there is nothing to fight them for.",
            phrase(sorted(enemies), lead="you are at war with")
            or "declare war first; a war aim belongs to a war.")

    if kind in _FORBIDS_GROUND and subject_id:
        return Refusal(
            "about_the_war",
            f"a {kind.replace('_', ' ')} aim is about the war, not about a province.",
            f"declare it against {enemy_id} without naming ground.")
    if kind in _NEEDS_GROUND and not subject_id:
        return Refusal(
            "names_ground",
            f"a {kind.replace('_', ' ')} aim has to name the ground it is about.",
            phrase(_theirs(holder, enemy_id), lead=f"{enemy_id} is standing on"))
    if not subject_id:
        return None

    if subject_id not in owner:
        return Refusal(
            "known_region", f"{subject_id} is not a region on this map.",
            phrase(_theirs(holder, enemy_id), lead=f"{enemy_id} is standing on"))
    held_by = holder.get(subject_id)
    owned_by = owner.get(subject_id)
    home_of = homes.get(subject_id)

    if kind == "recover_cores":
        if home_of != nation_id:
            return Refusal(
                "your_own_people",
                f"{subject_id} is not your ground to recover -- it is home to "
                f"{home_of or 'nobody'}.",
                f"declare `annex` over {subject_id} if you mean to keep somebody else's.")
        if held_by == nation_id:
            return Refusal(
                "not_already_yours", f"you already hold {subject_id}.",
                phrase([region for region in _theirs(holder, enemy_id)
                        if homes.get(region) == nation_id],
                       lead="your people's ground still in their hands is"))
        return None

    # **Owner, not holder, and the distinction is the whole point of the aim.** Ground you
    # have taken and do not own is exactly what a war aim is *for*: an occupation is
    # temporary and an annexation is a settlement, and the sentence "we intend to keep what
    # we have taken" has to be sayable. Tested against who holds it instead, the most
    # common aim in history would have been refused for already being achieved.
    if owned_by == nation_id:
        return Refusal(
            "not_already_yours",
            f"{subject_id} is already yours; a war aim is something you do not yet have.",
            "hold it with a `hold` objective instead -- that is what defends ground.")
    if owned_by != enemy_id:
        return Refusal(
            "their_ground",
            f"{subject_id} belongs to {owned_by or 'nobody'}, not to {enemy_id}, so it is "
            "not theirs to give up.",
            f"name ground {enemy_id} owns, or declare the aim against "
            f"{owned_by} if you are at war with them too.")
    if kind == "independence" and home_of == nation_id:
        return Refusal(
            "somebody_elses_freedom",
            f"{subject_id} is your own ground; independence is an aim for somebody "
            "else's.",
            f"declare `recover_cores` over {subject_id}.")
    return None


def _diffs_for(
    conn: sqlite3.Connection,
    world_id: str,
    nation_id: str,
    wanted: dict[str, list[WarAim]],
    turn: int,
) -> list[Diff]:
    """Replace this power's aims **in the wars it just spoke about**, and no others.

    Declaring nothing for a war it has aims in is how a power keeps them: a declaration
    naming Germany says what this war is for and says nothing at all about Italy. Wholesale
    replacement would make one sentence about one front a renunciation of every other.
    """
    diffs: list[Diff] = []
    for enemy_id, aims in sorted(wanted.items()):
        current = {
            (str(row["kind"]), str(row["subject_id"] or "")): True
            for row in conn.execute(
                "SELECT kind, subject_id FROM war_aim"
                " WHERE world_id = ? AND nation_id = ? AND enemy_id = ?",
                (world_id, nation_id, enemy_id))
        }
        keep = {(aim.kind, aim.subject_id) for aim in aims}
        for kind, subject_id in sorted(set(current) - keep):
            diffs.append(
                Diff("war_aim", aim_id_for(nation_id, enemy_id, kind, subject_id),
                     "__delete__", DiffOp.DELETE, None))
        for aim in aims:
            if (aim.kind, aim.subject_id) in current:
                # Unchanged. Rewriting it would churn the diff log every turn and make a
                # war look busier on paper than it was.
                continue
            diffs.append(
                Diff("war_aim",
                     aim_id_for(nation_id, enemy_id, aim.kind, aim.subject_id),
                     "__insert__", DiffOp.INSERT,
                     {
                         "world_id": world_id,
                         "nation_id": nation_id,
                         "enemy_id": enemy_id,
                         "kind": aim.kind,
                         "subject_id": aim.subject_id,
                         "declared_turn": turn,
                     }))
    return diffs


def _describe(conn: sqlite3.Connection, nation_id: str, aims: list[WarAim]) -> str:
    names = {
        str(row["id"]): str(row["name"])
        for row in conn.execute("SELECT id, name FROM nation")
    }
    names.update({
        str(row["id"]): str(row["name"])
        for row in conn.execute("SELECT id, name FROM region")
    })
    who = names.get(nation_id, nation_id)
    if not aims:
        return f"{who} declared no war aims."
    return (f"{who} stated what it is fighting for: "
            + "; ".join(aim.describe(names) for aim in aims) + ".")


def _enemies_of(conn: sqlite3.Connection, world_id: str, nation_id: str) -> set[str]:
    return {
        str(row["nation_b"])
        for row in conn.execute(
            "SELECT nation_b FROM relation"
            " WHERE world_id = ? AND nation_a = ? AND at_war = 1",
            (world_id, nation_id))
    }


def _holders(conn: sqlite3.Connection, world_id: str) -> dict[str, str | None]:
    return {
        str(row["id"]): row["holder"]
        for row in conn.execute(
            "SELECT id, COALESCE(controller_nation_id, owner_nation_id) AS holder"
            "  FROM region WHERE world_id = ?", (world_id,))
    }


def _owners(conn: sqlite3.Connection, world_id: str) -> dict[str, str | None]:
    """Who a region *belongs* to, which is what a settlement changes."""
    return {
        str(row["id"]): row["owner_nation_id"]
        for row in conn.execute(
            "SELECT id, owner_nation_id FROM region WHERE world_id = ?", (world_id,))
    }


def _homes(conn: sqlite3.Connection, world_id: str) -> dict[str, str | None]:
    return {
        str(row["id"]): row["home_nation_id"]
        for row in conn.execute(
            "SELECT id, home_nation_id FROM region WHERE world_id = ?", (world_id,))
    }
