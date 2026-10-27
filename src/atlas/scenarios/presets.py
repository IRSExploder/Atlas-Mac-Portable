"""Scenario presets.

**Lore and rules are separate fields, and conflating them makes presets much harder to
write.** They are read by the engine in different ways and they fail in different ways.

``lore_text`` is grounding: the mood of the period, who resents whom, what everyone
assumes. A simulation that drifts from it produces a world that feels wrong but is still
playable.

``simulation_rules`` are constraints: no anachronistic technology, no inventing nations,
respect the tech era. A simulation that ignores *these* produces tanks in 1750, which is
not a tonal problem but a broken game. They are injected as hard constraints in every
world-sim call, phrased as prohibitions rather than preferences.

Merging the two means either treating flavour as inviolable or treating a hard constraint
as a suggestion, and both are worse than keeping two fields.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..config.paths import app_data_root
from ..map.sources import SOURCES
from ..state.models import Difficulty

__all__ = ["Preset", "builtin_presets", "delete_preset", "has_map", "list_presets",
           "load_preset", "playable_presets", "save_preset"]


@dataclass(frozen=True, slots=True)
class Preset:
    id: str
    name: str
    start_date: str
    description: str = ""
    #: Grounding context. The simulation should stay consistent with it.
    lore_text: str = ""
    #: Hard constraints. The simulation must not violate them.
    simulation_rules: tuple[str, ...] = ()
    map_source: str = "admin0-50m"
    difficulty: Difficulty = Difficulty.NORMAL
    seed: int = 1936
    #: Nations the player may choose, as geo keys. Empty means any.
    playable: tuple[str, ...] = ()
    builtin: bool = False

    def rules_block(self) -> str:
        """The constraints, phrased for a prompt. Prohibitions, not preferences."""
        if not self.simulation_rules:
            return ""
        lines = ["## Hard constraints on this world", "You must not violate these:"]
        lines.extend(f"- {rule}" for rule in self.simulation_rules)
        return "\n".join(lines)

    def lore_block(self) -> str:
        if not self.lore_text:
            return ""
        return f"## The world as it stands\n{self.lore_text}"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "start_date": self.start_date,
            "description": self.description,
            "lore_text": self.lore_text,
            "simulation_rules": list(self.simulation_rules),
            "map_source": self.map_source,
            "difficulty": self.difficulty.value,
            "seed": self.seed,
            "playable": list(self.playable),
        }

    @classmethod
    def from_dict(cls, data: dict, *, builtin: bool = False) -> Preset:
        return cls(
            id=str(data["id"]),
            name=str(data.get("name", data["id"])),
            start_date=str(data.get("start_date", "1936-01-01")),
            description=str(data.get("description", "")),
            lore_text=str(data.get("lore_text", "")),
            simulation_rules=tuple(data.get("simulation_rules") or ()),
            map_source=str(data.get("map_source", "admin0-50m")),
            difficulty=Difficulty(data.get("difficulty", "normal")),
            seed=int(data.get("seed", 1936)),
            playable=tuple(data.get("playable") or ()),
            builtin=builtin,
        )


_BUILTIN: tuple[Preset, ...] = (
    Preset(
        id="gathering-storm",
        name="1936 - Gathering Storm",
        start_date="1936-01-01",
        description="Europe rearms. The old settlement is failing and everyone can see it.",
        lore_text=(
            "The peace imposed in 1919 is visibly coming apart. Germany has begun rearming "
            "openly and the powers that could have stopped it are unwilling to act alone. "
            "France fears another war on its soil above all else; Britain hopes concession "
            "will buy time; the Soviet Union watches, distrusted by everyone and "
            "distrusting in return. Italy is looking for a cheap empire. The smaller states "
            "of central Europe are calculating who will protect them and finding no "
            "satisfactory answer. Nobody wants a general war, and nobody is behaving in a "
            "way that prevents one."
        ),
        simulation_rules=(
            "Technology must remain within the 1930s. No jet aircraft, no nuclear weapons, "
            "no guided missiles, no computers.",
            "Do not invent nations that did not exist. Territory changes hands between the "
            "powers present on the map.",
            "Communication takes time. A message between capitals is not instantaneous, and "
            "an army cannot be redirected the moment its government changes its mind.",
            "Economies are industrial but finite. No nation can rearm at a pace its economy "
            "does not support.",
        ),
        playable=("admin0-50m:FRA", "admin0-50m:DEU", "admin0-50m:GBR",
                  "admin0-50m:ITA", "admin0-50m:RUS", "admin0-50m:POL"),
        seed=1936,
        builtin=True,
    ),
    Preset(
        id="hot-cold-war",
        name="1972 - The Fulda Gap",
        start_date="1972-01-05",
        description="An alternate 1972: the Pact crossed the border and the Cold War is being fought.",
        lore_text=(
            "This is not the 1972 that happened. On 4 January a Warsaw Pact exercise "
            "crossed the inner-German border in strength, and by the morning of the fifth "
            "there are Soviet divisions in Niedersachsen and East German ones in Hessen. "
            "Everything else about the year is as it was: the same armies, the same "
            "fleets, the same arsenals, the same quarrel between Moscow and Beijing, the "
            "same war in Indochina, the same treaties waiting in the Bundestag. The "
            "Federal Republic has invoked Article 5 and is asking for the counterattack "
            "rather than for a ceasefire. France has committed the First Army on its own "
            "terms. China has opened a second front on the Ussuri on the calculation that "
            "Soviet divisions in Germany are divisions not in Manchuria. Both superpowers "
            "still hold enough warheads to end the other, and every capital is now "
            "discovering what that is worth in a war that has already started."
        ),
        simulation_rules=(
            "The war is under way and cannot be wished away. A government may fight it, "
            "widen it, or seek terms, and may lose ground doing any of those -- but no "
            "power may simply declare the invasion over, and nobody's army evaporates "
            "because a briefing was persuasive.",
            "Nuclear weapons exist in quantity and both superpowers would survive a first "
            "strike well enough to answer it. First use is the catastrophe the whole "
            "period is organised around avoiding, and a conventional reverse is not a "
            "reason for it. Treat any move toward release as the gravest decision "
            "available, with consequences that do not stay local.",
            "Technology is of the 1970s: jet aircraft, guided missiles, satellites, "
            "mainframe computers, nuclear propulsion at sea. No stealth aircraft, no "
            "precision weapons in quantity, no networked warfare, no personal computers.",
            "Do not invent nations. Germany, Korea and Vietnam are each two states, and "
            "which of them exists is not in question -- only what happens to them.",
            "Communication is fast and verification is not. Satellites see where missiles "
            "are and cannot see what a government intends; a signal sent is not a signal "
            "believed.",
        ),
        map_source="admin0-50m",
        seed=1972,
        playable=("usa", "sov", "wger", "gbr", "fra", "chn", "eger", "pol", "dvn"),
        builtin=True,
    ),
    Preset(
        id="cold-war",
        name="1972 - Cold War",
        start_date="1972-01-01",
        description="Two blocs, three nuclear poles, and a peace kept by arithmetic.",
        lore_text=(
            "Two alliances face each other across a line drawn through Germany, and both "
            "know that a war between them would not stay conventional and would not stay "
            "in Europe. Strategic parity has just been reached and both superpowers are "
            "negotiating about it, which is the closest thing to trust the arrangement "
            "allows. The quarrel that has actually reorganised the world is between Moscow "
            "and Beijing, and Washington has noticed. Everywhere outside Europe the two "
            "blocs fight through other people's armies: in Indochina, along a Korean "
            "armistice line, across a Sinai ceasefire, and ninety miles from Florida. "
            "Everybody involved believes they are the reasonable party and nobody wants "
            "the war that all of their preparations are for."
        ),
        simulation_rules=(
            "Nuclear weapons exist in quantity and both superpowers would survive a first "
            "strike well enough to answer it. First use is the thing the entire period is "
            "organised around avoiding; treat any move toward it as catastrophic, and "
            "treat the certainty of retaliation as the reason the blocs do not fight each "
            "other directly.",
            "Technology is of the 1970s: jet aircraft, guided missiles, satellites, "
            "mainframe computers, nuclear propulsion at sea. No stealth aircraft, no "
            "precision weapons in quantity, no networked warfare, no personal computers.",
            "Do not invent nations. Germany, Korea and Vietnam are each two states, and "
            "which of them exists is not in question -- only what happens to them.",
            "The superpowers fight each other through clients, not directly. A great power "
            "supplies, advises and threatens; it commits its own troops only where it "
            "already has them.",
            "Communication is fast and verification is not. Satellites see where missiles "
            "are and nothing sees what anybody intends by them.",
        ),
        seed=1972,
        builtin=True,
    ),
    Preset(
        id="modern-day",
        name="Modern Day",
        start_date="2026-01-01",
        description="The present, with all its unresolved arguments.",
        lore_text=(
            "Great-power competition has returned without anyone declaring it. Economic "
            "interdependence makes open war expensive and has not made it unthinkable. "
            "Alliances built for a previous era are being tested by members who no longer "
            "agree about what they are for. Most conflict is conducted through trade, "
            "influence and proxies, and every capital is aware that this could change."
        ),
        simulation_rules=(
            "Technology is contemporary. Nuclear weapons exist and their use is close to "
            "unthinkable; treat any move toward it as a catastrophic escalation.",
            "Do not invent nations. Existing borders are the starting point.",
            "Information moves instantly and is contested. Nothing stays secret for long "
            "between major powers.",
            "Economic coercion is usually the first instrument, not force.",
        ),
        seed=2026,
        builtin=True,
    ),
    Preset(
        id="narrow-sea",
        name="The Narrow Sea",
        start_date="1897-03-01",
        description="A small fictional world. Three powers, one contested sea.",
        lore_text=(
            "Three powers share a narrow sea and a long memory. Aurelia grew rich on the "
            "northern trade and would rather buy an outcome than fight for it. Karthos is "
            "convinced its neighbours are conspiring against it, and has built an army to "
            "prove the point. Veyra, inland and outnumbered, has survived by making itself "
            "useful to whoever is winning. None of them trusts the other two, and all three "
            "believe they are the reasonable party."
        ),
        simulation_rules=(
            "Technology is late nineteenth century: railways, telegraph, steel warships. "
            "No aircraft, no radio, no automatic weapons.",
            "There are exactly three powers. Do not introduce a fourth.",
            "The sea is the only route between north and south. Land campaigns must go "
            "through the eastern plateau.",
        ),
        # Not a Natural Earth source, so `has_map` is false and this preset is kept out of
        # the new-game screen and `atlas map world`. The world it describes is
        # `scenarios.sandbox` -- the hand-built engine world, whose regions no pack holds --
        # reached headless through `atlas newgame`. Kept here rather than deleted because
        # the lore is the same world's, and a sandbox pack would make it playable as is.
        map_source="sandbox",
        seed=42,
        builtin=True,
    ),
)


def presets_root() -> Path:
    return app_data_root() / "presets"


def builtin_presets() -> tuple[Preset, ...]:
    return _BUILTIN


def list_presets() -> list[Preset]:
    """Built-ins plus anything the user has saved. User presets shadow built-ins by id."""
    found: dict[str, Preset] = {preset.id: preset for preset in _BUILTIN}
    root = presets_root()
    if root.exists():
        for path in sorted(root.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                preset = Preset.from_dict(data)
            except (OSError, ValueError, KeyError):
                continue
            found[preset.id] = preset
    return sorted(found.values(), key=lambda p: (not p.builtin, p.name))


def has_map(preset: Preset) -> bool:
    """Whether ``preset``'s map is a source `atlas map bake` can make a pack from.

    A preset without one cannot be played: the game window draws regions from a pack,
    and asking the player to bake a source that does not exist ends in a traceback.
    """
    return preset.map_source in SOURCES


def playable_presets() -> list[Preset]:
    """The presets a new game can be started from: :func:`list_presets` with a map."""
    return [preset for preset in list_presets() if has_map(preset)]


def load_preset(preset_id: str) -> Preset | None:
    return next((preset for preset in list_presets() if preset.id == preset_id), None)


def save_preset(preset: Preset) -> Path:
    root = presets_root()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{preset.id}.json"
    path.write_text(json.dumps(preset.to_dict(), indent=2), encoding="utf-8")
    return path


def delete_preset(preset_id: str) -> bool:
    """Remove a user preset. Built-ins cannot be deleted, only shadowed."""
    path = presets_root() / f"{preset_id}.json"
    if path.exists():
        path.unlink()
        return True
    return False
