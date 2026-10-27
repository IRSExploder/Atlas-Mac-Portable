"""Whether a scenario built the era it says it built.

An invariant in :mod:`atlas.testing.invariants` says *this cannot be true of a world that
is not broken* -- it is about the engine, and it holds in every era. This says something
different and narrower: **the world this scenario builds is the world it claims to be
describing.** Article 5 is a fact about the engine; that fifteen named states were parties
to the North Atlantic Treaty on 1 January 1972 is a fact about 1972, and no invariant can
know it.

Nothing else was checking it. The era's own test file pins the handful of facts whoever
wrote it happened to think of, and every fact nobody thought of is unguarded -- which is
how South Vietnam shipped as ``svn`` and quietly took Slovenia's population, and how the
Security Council shipped with fourteen members. Both were found by a person reading, and a
person reading is not a mechanism.

**The manifest is JSON and imports nothing.** That is the entire design and it is worth
being blunt about, because the obvious implementation is worthless: an auditor that reads
``SCENARIO.organisations`` and checks the database matches proves only that
:func:`~atlas.scenarios.seeder.apply_politics` can copy a tuple. It would have passed on
the day the Council had fourteen members, because the scenario said fourteen and the world
had fourteen. So the manifest is a **separately written record of what the period was**,
kept out of Python where it cannot reach the scenario module even by accident, and the
audit is a comparison of two independent statements about the same year.

Where the two disagree, the manifest is not automatically right -- it is a second opinion,
and the disagreement is the finding. What is *not* allowed is for the manifest to be edited
into agreement without somebody deciding which of the two was wrong.

**Silence is a finding too.** A manifest that names nothing passes trivially, so
:func:`audit` reports a section that is empty in an era that should have one, and
:func:`coverage` says how much of each era is actually spoken for.

Read-only, like every other instrument here: each check is a query and nothing writes.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .laboratory import Finding

__all__ = [
    "CHECKS",
    "Check",
    "Manifest",
    "audit",
    "audit_world",
    "coverage",
    "load_manifest",
    "manifest_ids",
]

MANIFESTS = Path(__file__).with_name("manifests")

#: The sections an era is expected to have something to say about. An era may legitimately
#: be silent on some of these -- 1936 has no arsenals and no organisations, because there
#: were none -- and a silence has to be *declared*, in ``silent_on``, with a reason. An
#: undeclared silence is reported, because "we forgot to write the Security Council down"
#: and "there was no Security Council" look identical in an empty list.
SECTIONS: tuple[str, ...] = (
    "alliances", "organisations", "dependencies", "cores", "occupied", "ungoverned",
    "not_yet", "leaders", "treaties", "arsenals", "packs", "delivery", "doctrines",
)


@dataclass(frozen=True, slots=True)
class Manifest:
    """What an era *was*, written down independently of what any scenario builds.

    Loaded from JSON and nothing else. The fields are deliberately loose -- lists of plain
    dicts rather than a dataclass per section -- because the manifest is a historian's
    record and the schema will grow sideways as eras want to say new kinds of thing. What
    keeps it honest is :func:`load_manifest`, which refuses a section it does not
    recognise rather than ignoring it: a typo in a key must not read as "nothing to check".
    """

    era: str
    start_date: str
    #: Where the facts came from, in a sentence. Not decoration: the next person to
    #: disagree with a line here needs to know what it was written against.
    sources: str = ""
    alliances: tuple[dict[str, Any], ...] = ()
    organisations: tuple[dict[str, Any], ...] = ()
    #: Ground held by somebody other than the polity whose name it bears.
    dependencies: tuple[dict[str, Any], ...] = ()
    #: Ground that is somebody's home even though they do not hold it.
    cores: tuple[dict[str, Any], ...] = ()
    occupied: tuple[dict[str, Any], ...] = ()
    #: Ground that is nobody's state at all. Antarctica, after 1961.
    ungoverned: tuple[dict[str, Any], ...] = ()
    #: Polities that must **not** exist in this year. The other half of sovereignty, and
    #: the half a scenario cannot state: a scenario lists who exists, and an anachronism
    #: is something it never mentions.
    not_yet: tuple[dict[str, Any], ...] = ()
    leaders: tuple[dict[str, Any], ...] = ()
    treaties: tuple[dict[str, Any], ...] = ()
    arsenals: tuple[dict[str, Any], ...] = ()
    #: What each power was BUILT to do, as ``{"nation": id, "axis": id, "stance": id}``.
    #: A second opinion on the seeding: the scenario says France was conscript in 1936 and
    #: this says so independently, and where they disagree somebody has to decide which was
    #: wrong rather than editing one into the other.
    doctrines: tuple[dict[str, Any], ...] = ()
    #: Geometry sources that must be baked for this era to be the era it claims. A
    #: scenario drawn at province granularity **degrades silently** to country granularity
    #: when its finer pack is missing -- it still builds, still plays, and is no longer the
    #: scenario. See :func:`atlas.scenarios.territory.pack_paths`.
    packs: tuple[str, ...] = ()
    #: The delivery-range boundary, as ``{"aircraft": 6}`` -- and the launchers to walk it
    #: from. Checked against the world's own adjacency graph rather than against the
    #: constant, so it measures the boundary a warhead actually meets.
    delivery: dict[str, Any] = field(default_factory=dict)
    #: ``section -> why this era has nothing to say``. A declared silence.
    silent_on: dict[str, str] = field(default_factory=dict)


def manifest_ids() -> tuple[str, ...]:
    """Every era with a manifest, sorted. Empty if the package data did not ship."""
    if not MANIFESTS.is_dir():
        return ()
    return tuple(sorted(path.stem for path in MANIFESTS.glob("*.json")))


def load_manifest(era: str) -> Manifest | None:
    """The manifest for ``era``, or ``None`` if there is none.

    A key the dataclass does not know is a hard error rather than a shrug. The failure this
    prevents is the quiet one: ``"organizations"`` spelled the American way loads clean,
    checks nothing, and reports a passing audit forever.
    """
    path = MANIFESTS / f"{era}.json"
    if not path.exists():
        return None
    blob: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    blob.pop("$comment", None)
    known = set(Manifest.__dataclass_fields__)
    unknown = sorted(set(blob) - known)
    if unknown:
        raise ValueError(f"{path.name}: unknown manifest section(s): {', '.join(unknown)}")
    for key, value in list(blob.items()):
        if isinstance(value, list):
            blob[key] = tuple(value)
    return Manifest(**blob)


# ------------------------------------------------------------------------ small helpers


def _rows(conn: sqlite3.Connection, sql: str, *args: Any) -> list[sqlite3.Row]:
    return list(conn.execute(sql, args))


def _named(entry: dict[str, Any], *keys: str) -> str:
    """A short label for a manifest entry, for the finding line."""
    for key in keys:
        if entry.get(key):
            return str(entry[key])
    return "?"


def _diff(expected: set[str], actual: set[str]) -> str:
    """The two directions, both of which matter and only one of which is usually looked at."""
    parts = []
    if missing := sorted(expected - actual):
        parts.append(f"missing {', '.join(missing)}")
    if extra := sorted(actual - expected):
        parts.append(f"unexpected {', '.join(extra)}")
    return "; ".join(parts)


# ------------------------------------------------------------------------------ the checks


def _the_right_era(conn: sqlite3.Connection, world_id: str, manifest: Manifest) -> list[str]:
    row = conn.execute(
        "SELECT preset_id, world_date FROM world WHERE id = ?", (world_id,)
    ).fetchone()
    if row is None:
        return [f"no world {world_id!r} to audit"]
    out = []
    if str(row["preset_id"]) != manifest.era:
        out.append(f"world is preset {row['preset_id']!r}, manifest describes {manifest.era!r}")
    if str(row["world_date"]) != manifest.start_date:
        out.append(f"world begins {row['world_date']}, manifest describes {manifest.start_date}")
    return out


def _alliances(conn: sqlite3.Connection, world_id: str, manifest: Manifest) -> list[str]:
    """Every named pact, by its exact party set.

    Matched on the party set rather than on the prose, because the prose is the one part of
    a commitment a model may later rewrite and the parties are what Article 5 reads. An
    alliance whose membership is one nation off is an alliance that will decline to defend
    somebody, and nothing in the engine will say why.
    """
    parties: dict[int, set[str]] = {}
    for row in _rows(conn, "SELECT commitment_id, nation_id FROM commitment_nation"):
        parties.setdefault(int(row["commitment_id"]), set()).add(str(row["nation_id"]))
    live = {
        int(row["id"]): (str(row["kind"]), str(row["status"]), str(row["condition_text"]))
        for row in _rows(conn, "SELECT id, kind, status, condition_text FROM commitment"
                               " WHERE world_id = ?", world_id)
    }

    out = []
    for entry in manifest.alliances:
        want = set(entry["members"])
        kind = str(entry["kind"])
        found = [cid for cid, (k, _s, _t) in live.items()
                 if k == kind and parties.get(cid, set()) == want]
        name = _named(entry, "name")
        if not found:
            nearest = sorted(
                ((len(want ^ parties.get(cid, set())), cid) for cid, (k, *_r) in live.items()
                 if k == kind),
            )
            detail = ""
            if nearest and nearest[0][0] <= max(3, len(want) // 2):
                detail = f" -- nearest {kind} differs: {_diff(want, parties[nearest[0][1]])}"
            out.append(f"{name}: no {kind} with exactly these {len(want)} parties{detail}")
            continue
        status = entry.get("status")
        if status and live[found[0]][1] != status:
            out.append(f"{name}: is {live[found[0]][1]!r}, should be {status!r}"
                       f" -- {entry.get('because', '')}".rstrip(" -"))
    return out


def _organisations(conn: sqlite3.Connection, world_id: str, manifest: Manifest) -> list[str]:
    """Membership and, separately, the veto.

    Two facts and they fail independently: a Council with the right fifteen members and the
    wrong five permanent ones is a Council that cannot be vetoed by the powers it exists to
    be deadlocked by, and the member count looks perfect.
    """
    out = []
    for entry in manifest.organisations:
        org_id = str(entry["id"])
        row = conn.execute(
            "SELECT kind FROM organisation WHERE world_id = ? AND nation_id = ?",
            (world_id, org_id),
        ).fetchone()
        if row is None:
            out.append(f"{org_id}: not in this world at all")
            continue
        if entry.get("kind") and str(row["kind"]) != entry["kind"]:
            out.append(f"{org_id}: kind is {row['kind']!r}, should be {entry['kind']!r}")

        seats = _rows(conn, "SELECT nation_id, is_permanent FROM organisation_member"
                            " WHERE organisation_id = ?", org_id)
        members = {str(r["nation_id"]) for r in seats}
        permanent = {str(r["nation_id"]) for r in seats if int(r["is_permanent"])}
        if "members" in entry and (bad := _diff(set(entry["members"]), members)):
            out.append(f"{org_id} membership: {bad}")
        if "permanent" in entry and (bad := _diff(set(entry["permanent"]), permanent)):
            out.append(f"{org_id} permanent seats (the veto): {bad}"
                       f" -- {entry.get('because', '')}".rstrip(" -"))
    return out


def _territory(conn: sqlite3.Connection, world_id: str, manifest: Manifest) -> list[str]:
    """Dependencies, cores, occupation, ungoverned ground, and who does not exist yet.

    One check because they are one question asked four ways -- *who holds this, and on what
    footing* -- and a finding is more useful next to its neighbours than filed under a
    heading of its own.
    """
    held = {
        str(row["id"]): (row["owner_nation_id"], row["controller_nation_id"],
                         row["home_nation_id"], str(row["name"]))
        for row in _rows(conn, "SELECT id, name, owner_nation_id, controller_nation_id,"
                               "       home_nation_id FROM region WHERE world_id = ?", world_id)
    }
    out = []

    for entry in manifest.dependencies:
        region = str(entry["region"])
        if region not in held:
            out.append(f"{_named(entry, 'name', 'region')}: no such region {region!r}")
            continue
        owner = held[region][0]
        if owner != entry["held_by"]:
            out.append(f"{_named(entry, 'name', 'region')} is held by {owner!r}, should be "
                       f"{entry['held_by']!r} -- {entry.get('because', '')}".rstrip(" -"))

    for entry in manifest.cores:
        region = str(entry["region"])
        if region in held and held[region][2] != entry["home"]:
            out.append(f"{region}: home is {held[region][2]!r}, should be {entry['home']!r}")
        elif region not in held:
            out.append(f"core {region!r}: no such region")

    for entry in manifest.occupied:
        region = str(entry["region"])
        if region not in held:
            out.append(f"occupied {region!r}: no such region")
            continue
        owner, controller, *_rest = held[region]
        if (owner, controller) != (entry["owner"], entry["controller"]):
            out.append(f"{region}: owned {owner!r} controlled {controller!r}, should be "
                       f"owned {entry['owner']!r} controlled {entry['controller']!r}")

    for entry in manifest.ungoverned:
        region = str(entry["region"])
        if region in held and held[region][0] is not None:
            out.append(f"{region} has a government ({held[region][0]!r}) and should have "
                       f"none -- {entry.get('because', '')}".rstrip(" -"))

    if manifest.not_yet:
        alive = {str(r["name"]).lower(): str(r["id"])
                 for r in _rows(conn, "SELECT id, name FROM nation WHERE world_id = ?", world_id)}
        for entry in manifest.not_yet:
            name = str(entry["name"]).lower()
            hit = next((nid for nm, nid in alive.items() if name in nm), None)
            if hit is not None:
                out.append(f"{entry['name']} exists as {hit!r} and should not in "
                           f"{manifest.start_date[:4]} -- {entry.get('because', '')}".rstrip(" -"))
    return out


def _leaders(conn: sqlite3.Connection, world_id: str, manifest: Manifest) -> list[str]:
    """Who was actually in office on the start date.

    The narrow one, and the one that catches a whole era being off by a government: a
    scenario keyed by nation rather than by year will hand 1972 the wrong prime minister
    without any structure being wrong anywhere.
    """
    seated = {
        str(row["nation_id"]): (str(row["name"]), str(row["title"]))
        for row in _rows(conn, "SELECT nation_id, name, title FROM leader"
                               " WHERE world_id = ? AND is_current = 1", world_id)
    }
    out = []
    for entry in manifest.leaders:
        nation = str(entry["nation"])
        if nation not in seated:
            out.append(f"{nation}: nobody is in office")
            continue
        name, title = seated[nation]
        if name != entry["name"]:
            out.append(f"{nation}: {name!r} is in office, should be {entry['name']!r}"
                       f" -- {entry.get('because', '')}".rstrip(" -"))
        elif entry.get("title") and title != entry["title"]:
            out.append(f"{nation}: {name} is {title!r}, should be {entry['title']!r}")
    return out


def _treaties(conn: sqlite3.Connection, world_id: str, manifest: Manifest) -> list[str]:
    """Signed is not in force, and the manifest is where that distinction is kept.

    On 1 January 1972 the Treaty of Moscow had been signed for sixteen months and ratified
    by nobody. ``pending`` against ``active`` is the difference between an engine that can
    report a treaty broken and one that cannot, and nothing but a date says which is right.
    """
    parties: dict[int, set[str]] = {}
    for row in _rows(conn, "SELECT commitment_id, nation_id FROM commitment_nation"):
        parties.setdefault(int(row["commitment_id"]), set()).add(str(row["nation_id"]))

    out = []
    for entry in manifest.treaties:
        needle = str(entry["match"]).lower()
        found = [
            row for row in _rows(conn, "SELECT id, kind, status, condition_text FROM commitment"
                                       " WHERE world_id = ?", world_id)
            if needle in str(row["condition_text"]).lower()
        ]
        label = _named(entry, "name", "match")
        if not found:
            out.append(f"{label}: no commitment mentions it")
            continue
        if len(found) > 1:
            out.append(f"{label}: {len(found)} commitments mention it, so which one is meant"
                       " cannot be decided")
            continue
        row = found[0]
        if entry.get("kind") and str(row["kind"]) != entry["kind"]:
            out.append(f"{label}: kind is {row['kind']!r}, should be {entry['kind']!r}")
        if entry.get("status") and str(row["status"]) != entry["status"]:
            out.append(f"{label}: is {row['status']!r} on {manifest.start_date}, should be "
                       f"{entry['status']!r} -- {entry.get('because', '')}".rstrip(" -"))
        if "parties" in entry and (bad := _diff(set(entry["parties"]), parties.get(int(row["id"]), set()))):
            out.append(f"{label} parties: {bad}")
    return out


def _arsenals(conn: sqlite3.Connection, world_id: str, manifest: Manifest) -> list[str]:
    """Warheads, tier, missile boats -- and **who has none**, which is the load-bearing half.

    An arsenal the era did not have is the worst thing this instrument can find: it is
    invisible until somebody uses it, and by then the world has had a nuclear exchange
    nobody can account for. So the check is exhaustive in both directions.
    """
    actual = {
        str(row["nation_id"]): row
        for row in _rows(conn, "SELECT nation_id, warheads, delivery, deterrent_boats"
                               " FROM arsenal WHERE world_id = ?", world_id)
    }
    out = []
    for entry in manifest.arsenals:
        nation = str(entry["nation"])
        row = actual.pop(nation, None)
        if row is None:
            out.append(f"{nation}: no arsenal, manifest says {entry.get('warheads')} warheads")
            continue
        if "warheads" in entry and int(row["warheads"]) != int(entry["warheads"]):
            out.append(f"{nation}: {row['warheads']} warheads, manifest says {entry['warheads']}")
        if entry.get("delivery") and str(row["delivery"]) != entry["delivery"]:
            out.append(f"{nation}: delivery is {row['delivery']!r}, should be "
                       f"{entry['delivery']!r} -- {entry.get('because', '')}".rstrip(" -"))
        if "deterrent_boats" in entry and row["deterrent_boats"] != entry["deterrent_boats"]:
            out.append(f"{nation}: {row['deterrent_boats']} missile boats declared, manifest "
                       f"says {entry['deterrent_boats']}")
    for nation in sorted(actual):
        out.append(f"{nation} is armed and the manifest does not say it was "
                   f"({actual[nation]['warheads']} warheads, {actual[nation]['delivery']})")
    return out


def _doctrines(conn: sqlite3.Connection, world_id: str, manifest: Manifest) -> list[str]:
    """What each power was built to do -- and, in both directions, what it was not.

    **Exhaustive both ways, like the arsenals**, and for a version of the same reason. A
    doctrine the era did not have is not invisible the way a warhead is, but it is worse in
    one respect: it moves the manpower pool, what a formation is worth, and how a country
    feels its casualties, *every turn from the first one*. An era seeded with the wrong
    stance plays wrong from the day it is created and never announces it.
    """
    actual = {
        (str(row["nation_id"]), str(row["axis"])): str(row["stance"])
        for row in _rows(conn, "SELECT nation_id, axis, stance FROM doctrine_stance"
                               " WHERE world_id = ?", world_id)
    }
    out = []
    for entry in manifest.doctrines:
        nation, axis = str(entry["nation"]), str(entry["axis"])
        want = str(entry.get("stance", ""))
        found = actual.pop((nation, axis), None)
        if found is None:
            out.append(f"{nation}: no {axis} doctrine, manifest says {want!r}"
                       + (f" -- {entry['because']}" if entry.get("because") else ""))
            continue
        if want and found != want:
            out.append(f"{nation}: {axis} is {found!r}, manifest says {want!r}"
                       + (f" -- {entry['because']}" if entry.get("because") else ""))
    for nation, axis in sorted(actual):
        out.append(f"{nation} has a {axis} doctrine ({actual[(nation, axis)]!r}) and the "
                   "manifest does not say it did")
    return out


def _delivery_range(conn: sqlite3.Connection, world_id: str, manifest: Manifest) -> list[str]:
    """Where a bomber stops, measured on the map rather than read off the constant.

    Asserting ``_TIER_REACH["aircraft"] == 6`` proves a dict has not been edited. What
    matters is the boundary a warhead actually meets, so this walks the world's own
    adjacency out of the launcher's held ground, finds a region at exactly *n* hops and one
    at exactly *n+1*, and requires
    :func:`~atlas.engine.nuclear.within_delivery_range` to say yes to the first and no to
    the second. Both sides: a reach that is too short and one that is unbounded fail the
    same check from opposite directions.
    """
    from ..engine.nuclear import within_delivery_range

    if not manifest.delivery:
        return []
    reach: dict[str, int] = dict(manifest.delivery.get("reach") or {})
    launchers = list(manifest.delivery.get("launchers") or ())
    if not reach or not launchers:
        return []

    graph: dict[str, list[str]] = {}
    for row in _rows(conn, "SELECT region_a, region_b FROM region_adjacency"
                           " WHERE world_id = ?", world_id):
        a, b = str(row["region_a"]), str(row["region_b"])
        graph.setdefault(a, []).append(b)
        graph.setdefault(b, []).append(a)

    out = []
    for launcher in launchers:
        held = {str(r["id"]) for r in _rows(
            conn, "SELECT id FROM region WHERE world_id = ?"
                  "   AND COALESCE(controller_nation_id, owner_nation_id) = ?", world_id, launcher)}
        if not held:
            out.append(f"{launcher}: holds no ground, so delivery range cannot be measured")
            continue

        # Ring n is the set first reached at exactly n steps -- the same breadth-first walk
        # the engine does, so the two cannot disagree about what "a hop" means.
        seen, frontier, rings = set(held), sorted(held), {0: sorted(held)}
        for step in range(1, max(reach.values()) + 2):
            nxt = sorted({n for r in frontier for n in graph.get(r, ()) if n not in seen})
            seen.update(nxt)
            rings[step] = nxt
            frontier = nxt
            if not nxt:
                break

        for tier, hops in reach.items():
            inside, outside = rings.get(hops) or [], rings.get(hops + 1) or []
            if not inside or not outside:
                out.append(f"{launcher}/{tier}: the map has no region at exactly "
                           f"{hops if not inside else hops + 1} hops, so the boundary is untested")
                continue
            if not within_delivery_range(conn, world_id, launcher=launcher,
                                         region_id=inside[0], delivery=tier):
                out.append(f"{launcher}/{tier}: {inside[0]} is {hops} hops away and out of "
                           f"range; the boundary should be exactly {hops}")
            if within_delivery_range(conn, world_id, launcher=launcher,
                                     region_id=outside[0], delivery=tier):
                out.append(f"{launcher}/{tier}: {outside[0]} is {hops + 1} hops away and in "
                           f"range; the boundary should be exactly {hops}")
    return out


def _packs(conn: sqlite3.Connection, world_id: str, manifest: Manifest) -> list[str]:
    """Every geometry source this era needs, baked and on disk.

    **The silent one.** A scenario drawn province by province still builds when its finer
    pack is missing: ``pack_paths`` drops it, every region resolves at country granularity,
    and the world plays. Two Germanys become one, four flashpoints become four countries,
    and no test fails -- the era's own suite skips itself, which reads green.
    """
    from ..config.paths import packs_root

    root = packs_root()
    return [
        f"{source}: not baked ({root / (source + '.pack')}) -- this era is drawn with it, "
        "and without it the world silently degrades to country granularity"
        for source in manifest.packs
        if not (root / f"{source}.pack").exists()
    ]


def _sections_are_spoken_for(
    conn: sqlite3.Connection, world_id: str, manifest: Manifest
) -> list[str]:
    """An empty section is either "there were none" or "nobody wrote it down".

    They are indistinguishable in the data and opposite in meaning, so the manifest has to
    say which. This is the check that stops the auditor being satisfiable by deleting
    things from it.
    """
    out = []
    for section in SECTIONS:
        if getattr(manifest, section):
            continue
        if section not in manifest.silent_on:
            out.append(f"{section}: nothing to check and no reason given -- add entries, or "
                       f"declare the silence in `silent_on`")
    for section in sorted(set(manifest.silent_on) - set(SECTIONS)):
        out.append(f"silent_on names {section!r}, which is not a section")
    for section in sorted(set(manifest.silent_on) & set(SECTIONS)):
        if getattr(manifest, section):
            out.append(f"{section}: declared silent and has entries")
    return out


@dataclass(frozen=True, slots=True)
class Check:
    """One question asked of a world, and why it is worth asking."""

    name: str
    run: Callable[[sqlite3.Connection, str, Manifest], list[str]]
    because: str = ""


CHECKS: tuple[Check, ...] = (
    Check("era", _the_right_era,
          "a manifest audited against the wrong world passes or fails for no reason"),
    Check("alliances", _alliances,
          "collective defence is membership; one nation off and somebody is not defended"),
    Check("organisations", _organisations,
          "membership and the veto fail independently, and the veto is the whole mechanic"),
    Check("territory", _territory,
          "who holds this, and on what footing -- including who does not exist yet"),
    Check("leaders", _leaders,
          "a scenario keyed by nation rather than by year seats the wrong government"),
    Check("treaties", _treaties,
          "signed is not in force, and only a date says which a treaty was"),
    Check("arsenals", _arsenals,
          "an arsenal the era did not have is invisible until somebody uses it"),
    Check("doctrines", _doctrines,
          "a doctrine the era did not have moves the arithmetic from the first turn"),
    Check("delivery-range", _delivery_range,
          "the boundary a warhead meets on this map, not the constant it was set from"),
    Check("packs", _packs,
          "a missing finer pack degrades an era silently and reads as green"),
    Check("manifest", _sections_are_spoken_for,
          "an empty section must not be able to mean both 'none' and 'unwritten'"),
)


def audit(conn: sqlite3.Connection, world_id: str, manifest: Manifest) -> list[str]:
    """Every way this world is not the era the manifest describes. Empty means it is.

    A check that raises is reported rather than allowed to end the sweep -- the same rule
    :func:`~atlas.testing.invariants.violations` follows, and for the same reason: a broken
    check must not hide the nine that work.
    """
    out: list[str] = []
    for check in CHECKS:
        try:
            found = check.run(conn, world_id, manifest)
        except (sqlite3.Error, KeyError, ValueError, TypeError, OSError) as exc:
            out.append(f"[{check.name}] could not run: {type(exc).__name__}: {exc}")
            continue
        out.extend(f"[{check.name}] {line}" for line in found)
    return out


def audit_world(conn: sqlite3.Connection, world_id: str, manifest: Manifest,
                *, turn: int = 0) -> list[Finding]:
    """The same sweep as laboratory findings, so it reads like every other verdict."""
    return [Finding("scenario-audit", turn, line) for line in audit(conn, world_id, manifest)]


def coverage(manifest: Manifest) -> dict[str, int]:
    """How many facts each section actually asserts. ``-1`` for a declared silence.

    Reported by the CLI because a passing audit means nothing without it: the cheapest way
    to a green audit is an empty manifest, and this is the number that makes that visible.
    """
    out: dict[str, int] = {}
    for section in SECTIONS:
        value = getattr(manifest, section)
        if not value and section in manifest.silent_on:
            out[section] = -1
        elif isinstance(value, dict):
            out[section] = len(value.get("launchers") or ()) * len(value.get("reach") or ())
        else:
            out[section] = len(value)
    return out
