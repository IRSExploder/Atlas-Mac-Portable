"""Cloning a scenario and changing it, without editing a line of Python.

A scenario is three artefacts: a :class:`~atlas.scenarios.presets.Preset` (already data,
already saveable, already listable), an optional territory file (already data), and a seed
module holding a :class:`Scenario` -- code, but code whose *contents* are almost entirely
tuples and dicts of primitives. 110 relations, six arsenals, seventeen treaties: nothing in
there needs to be Python except the fact that somebody wrote it in a ``.py`` file.

So an authored scenario is the base plus **an overlay of data edits**, and that is the whole
design. Cloning writes two files -- a preset and an overlay -- and
:func:`authored_scenario` reconstructs the edited scenario by loading the base and applying
the overlay with ``dataclasses.replace``. Everything downstream receives an ordinary
``Scenario`` and cannot tell the difference, which is why the clone builds, audits, rewinds
and plays through exactly the pipeline a shipped scenario does.

**No schema change, and nothing new in a save.** The overlay is a file; the world it builds
is an ordinary world at the current schema. A save from a clone is a save.

**The edits are a deliberately small set.** Relations, arsenals, deterrent boats and the
seed are values with no referential tail: changing a relation score cannot leave a dangling
reference, because both nations already exist. Powers, sovereignty and treaties are the
interesting ones and are *not* offered here -- see :data:`EDITABLE` -- because each of them
can produce a scenario that refers to something that is not there, and an authoring tool
whose first failure mode is an incoherent world is worse than one that does less.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path

from ..config.paths import app_data_root
from .presets import Preset, load_preset, save_preset
from .seeder import Scenario

__all__ = [
    "EDITABLE",
    "NOT_EDITABLE",
    "base_of",
    "Overlay",
    "Verdict",
    "authored_ids",
    "authored_scenario",
    "check",
    "clone_scenario",
    "load_overlay",
    "overlay_path",
    "save_overlay",
    "workshop_root",
]

#: What v1 lets a player change, and why only this much.
#:
#: Each of these is a *value* with no referential tail: a relation names two nations that
#: already exist, an arsenal names a power that already has one. Nothing here can produce a
#: scenario that points at something absent.
#:
#: **Not offered, and each for a reason worth stating.** ``powers`` adds or removes a nation
#: that sovereignty, relations, treaties and fleets all refer to. ``sovereignty`` decides who
#: holds what, and an edit that leaves a region unclaimed or doubly claimed is a broken map
#: rather than a different one. ``treaties`` name their members, so an edit can sign a pact
#: on behalf of a country that is not in the scenario. Those want validation of their own,
#: which is a bigger piece than cloning.
EDITABLE: dict[str, str] = {
    "relation": "how two powers regard each other, and whether they are at war",
    "arsenal": "how many warheads a power has, and what carries them",
    "deterrent_boats": "how many missile submarines a power keeps at sea",
    "rng_seed": "the seed the world is built from",
}


#: The other half of the same promise: what is *not* offered, and what each would need
#: before it could be. The reasons are the ones in :data:`EDITABLE`'s note, stated as data
#: so the workshop panel can show the ceiling rather than leave a player guessing whether
#: the missing controls are a decision or a bug. Every key is a field of
#: :class:`~atlas.scenarios.seeder.Scenario`, and no key is in :data:`EDITABLE`.
NOT_EDITABLE: dict[str, str] = {
    "powers": "adding or removing a nation leaves sovereignty, relations, treaties and "
              "fleets naming somebody who is not there",
    "sovereignty": "who holds what — an edit that leaves ground unclaimed or claimed "
                   "twice is a broken map rather than a different one",
    "treaties": "an agreement names its members, so an edit can sign a pact on behalf of "
                "a country this scenario does not have",
}


def workshop_root() -> Path:
    return app_data_root() / "workshop"


def overlay_path(preset_id: str) -> Path:
    return workshop_root() / f"{preset_id}.json"


@dataclass(slots=True)
class Overlay:
    """A base scenario, and the edits made to it."""

    base: str
    #: ``[a, b, score, at_war]`` -- the pair is matched, the rest replaces.
    relations: list[list] = field(default_factory=list)
    #: ``{power: [warheads, delivery]}``
    arsenals: dict[str, list] = field(default_factory=dict)
    #: ``{power: boats}``
    deterrent_boats: dict[str, int] = field(default_factory=dict)
    rng_seed: int | None = None

    def to_dict(self) -> dict:
        return {
            "base": self.base,
            "relations": self.relations,
            "arsenals": self.arsenals,
            "deterrent_boats": self.deterrent_boats,
            "rng_seed": self.rng_seed,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Overlay:
        return cls(
            base=str(data.get("base") or ""),
            relations=[list(entry) for entry in data.get("relations") or ()],
            arsenals={str(k): list(v) for k, v in (data.get("arsenals") or {}).items()},
            deterrent_boats={
                str(k): int(v) for k, v in (data.get("deterrent_boats") or {}).items()},
            rng_seed=data.get("rng_seed"),
        )

    @property
    def edit_count(self) -> int:
        return (len(self.relations) + len(self.arsenals) + len(self.deterrent_boats)
                + (1 if self.rng_seed is not None else 0))


def _apply(base: Scenario, overlay: Overlay, preset_id: str) -> Scenario:
    """The base scenario with the overlay's edits in it. Pure: the base is not touched."""
    changed = dict(base.arsenals)
    for power, value in overlay.arsenals.items():
        if power in changed:
            warheads, delivery = value[0], value[1] if len(value) > 1 else changed[power][1]
            changed[power] = (int(warheads), str(delivery))

    boats = dict(base.deterrent_boats)
    for power, count in overlay.deterrent_boats.items():
        if power in boats:
            boats[power] = int(count)

    wanted = {(str(a), str(b)): entry for entry in overlay.relations
              for a, b in ((entry[0], entry[1]),)}
    relations = tuple(
        (a, b, float(wanted[(a, b)][2]), status, bool(wanted[(a, b)][3]))
        if (a, b) in wanted else (a, b, score, status, war)
        for (a, b, score, status, war) in base.relations
    )

    return dataclasses.replace(
        base,
        id=preset_id,
        arsenals=changed,
        deterrent_boats=boats,
        relations=relations,
        rng_seed=int(overlay.rng_seed) if overlay.rng_seed is not None else base.rng_seed,
    )


def base_of(preset_id: str | None) -> str:
    """The scenario an authored preset was cloned from, or ``""`` if it is not a clone.

    Deliberately cheap and exception-free: it is called from the territory loader, which
    runs for every scenario including the shipped ones, and must not make a missing or
    malformed overlay anybody else's problem.
    """
    if not preset_id:
        return ""
    path = overlay_path(preset_id)
    if not path.exists():
        return ""
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("base") or "")
    except (OSError, ValueError):
        return ""


def authored_ids() -> set[str]:
    """Preset ids that have an overlay on disk."""
    root = workshop_root()
    if not root.exists():
        return set()
    return {path.stem for path in root.glob("*.json")}


def authored_scenario(preset_id: str | None):
    """The scenario an authored preset means, or ``None`` if it is not one.

    Consulted by :func:`atlas.scenarios.registry.scenario_for` before the built-in table, so
    a clone resolves to its edited scenario everywhere a shipped one resolves to its module
    -- which is why nothing downstream needs to know the difference.
    """
    if not preset_id:
        return None
    path = overlay_path(preset_id)
    if not path.exists():
        return None
    try:
        overlay = Overlay.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None

    from .registry import builtin_scenario_for

    base = builtin_scenario_for(overlay.base)
    if base is None:
        return None
    return _apply(base, overlay, preset_id)


def clone_scenario(
    base_id: str, *, new_id: str, name: str, overlay: Overlay | None = None
) -> Preset:
    """Copy a scenario into an editable one of its own, and save both halves.

    The base is read and never written -- the same discipline a forked world keeps -- so a
    clone cannot cost a player the scenario they cloned.
    """
    base_preset = load_preset(base_id)
    if base_preset is None:
        raise ValueError(f"there is no scenario called {base_id!r}")
    if load_preset(new_id) is not None:
        raise FileExistsError(f"there is already a scenario called {new_id!r}")

    edits = overlay or Overlay(base=base_id)
    edits.base = base_id

    clone = dataclasses.replace(
        base_preset, id=new_id, name=name,
        description=f"Cloned from {base_preset.name}. " + base_preset.description,
        builtin=False)
    save_preset(clone)

    root = workshop_root()
    root.mkdir(parents=True, exist_ok=True)
    overlay_path(new_id).write_text(
        json.dumps(edits.to_dict(), indent=2), encoding="utf-8")
    return clone


def load_overlay(preset_id: str) -> Overlay | None:
    path = overlay_path(preset_id)
    if not path.exists():
        return None
    try:
        return Overlay.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def save_overlay(preset_id: str, overlay: Overlay) -> Path:
    root = workshop_root()
    root.mkdir(parents=True, exist_ok=True)
    path = overlay_path(preset_id)
    path.write_text(json.dumps(overlay.to_dict(), indent=2), encoding="utf-8")
    return path


@dataclass(slots=True)
class Verdict:
    """What the workshop can say about an authored scenario beyond "it loaded"."""

    built: bool = False
    #: The built world's digest, and the digest of building it a second time.
    fingerprint: str = ""
    reproducible: bool = False
    #: Invariant violations: the engine's own account of an incoherent world.
    problems: list[str] = field(default_factory=list)
    nations: int = 0
    regions: int = 0

    @property
    def coherent(self) -> bool:
        return self.built and not self.problems

    def sentence(self) -> str:
        if not self.built:
            return "This scenario could not be built: " + "; ".join(self.problems[:2])
        if self.problems:
            return (f"Built, but {len(self.problems)} thing(s) are not coherent — "
                    "the world is playable and something in it is wrong.")
        if not self.reproducible:
            return "Built and coherent, but it did not build the same way twice."
        return (f"Coherent and reproducible — {self.nations} nations, {self.regions} "
                "provinces, and the same world every time.")


def check(preset_id: str) -> Verdict:
    """Build an authored scenario twice and report what the engine makes of it.

    **This is what the workshop has that a text editor does not.** Anyone can write a
    scenario file; the question that matters is whether the world it produces holds
    together, and the engine already answers that -- the seed-phase invariants are the same
    ones the release audit runs against the shipped eras. Building twice answers the other
    half: an authored scenario that does not reproduce is one whose games cannot be replayed
    or rewound, which would be a broken save waiting to happen rather than a design choice.
    """
    from ..map.pack import PackSet
    from ..state.bootstrap import create_world, fingerprint
    from ..testing.invariants import Phase, violations
    from .registry import preset_politics, preset_seed
    from .territory import pack_paths

    verdict = Verdict()
    preset = load_preset(preset_id)
    if preset is None:
        verdict.problems.append(f"there is no scenario called {preset_id!r}")
        return verdict

    # **An overlay that resolves to nothing must not pass.** `scenario_for` answers `None`
    # for an unknown scenario and the seeder obligingly builds a *generic* world from it --
    # which is a perfectly coherent world and not remotely the one that was authored. Found
    # by pointing an overlay at a base that does not exist and being told it was fine.
    overlay = load_overlay(preset_id)
    if overlay is not None and authored_scenario(preset_id) is None:
        verdict.problems.append(
            f"this is built on {overlay.base!r}, which is not a scenario on this machine; "
            "nothing it edits can be applied")
        return verdict

    world = f"check-{preset_id}"[:40]
    digests: list[str] = []
    for pass_number in range(2):
        try:
            with PackSet.open(pack_paths(preset.id, preset.map_source)) as packs:
                seed = preset_seed(preset, packs, world_id=world, player=None)
            conn = create_world(":memory:", seed)
            preset_politics(conn, world, preset)
        except Exception as exc:  # noqa: BLE001 - an authored scenario may fail anywhere
            verdict.problems.append(f"{type(exc).__name__}: {exc}")
            return verdict
        digests.append(fingerprint(conn, world))
        if pass_number == 0:
            verdict.built = True
            verdict.problems.extend(violations(conn, world, Phase.SEED))
            verdict.nations = conn.execute(
                "SELECT COUNT(*) FROM nation WHERE world_id = ?", (world,)).fetchone()[0]
            verdict.regions = conn.execute(
                "SELECT COUNT(*) FROM region WHERE world_id = ?", (world,)).fetchone()[0]
        conn.close()

    verdict.fingerprint = digests[0]
    verdict.reproducible = len(digests) == 2 and digests[0] == digests[1]
    return verdict
