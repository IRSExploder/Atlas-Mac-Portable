"""Prompt text that was written, reviewed and tested, and has since been given a tape.

**The problem this exists for.** Any string that reaches a model prompt is gated by the one
ACTIVE cassette in ``scratchpad/cassettes/INDEX.json``: the tape is replayed against HEAD and
a changed prompt is a MISS by construction. Fixing a MISS means re-recording against live
models, which is ~99 provider calls over twelve turns and an hour of somebody's subscription
quota. That is a real cost, it is the user's call, and it is worth paying **once** for a
batch rather than once per sentence.

So a change to briefing text has three bad options and one good one. It can be written and
merged, which reddens the gate for everyone until somebody pays. It can be written and
reverted, which loses the review. It can be left as a note in a markdown file, which drifts
from the call site it describes and gets re-derived from scratch by whoever picks it up. Or
-- this module -- it can be **written at the call site, exercised by tests, and switched
off**, so that landing it is one boolean and one recording session instead of a rewrite.

**What may be gated here and what may not.** Only text. Every entry below changes what a
model is *told* and nothing about what the engine *does*: no coefficient, no stored column,
no ordering, no diff. That is the invariant that makes the switch safe to leave off in the
shipped build and safe to turn on in a test in the same process --
``test_the_air_situation_is_a_reading`` asserts the world's fingerprint is identical under
both settings, which is the assertion that this module is text and nothing else.

**Reading it.** **On by default, because the batch has been paid for**: the tape named by
``scratchpad/cassettes/INDEX.json`` was recorded with these strings in the prompts, so a
build that ships them off is the one the gate would now redden. ``ATLAS_AIR_REBRIEF=off``
takes them back out, which is a way to read a superseded tape and nothing a player should
want; tests move the flag through :func:`set_enabled` and put it back. Read at import like
:mod:`atlas.engine.consent`'s flag, because a value that could change between two turns of
one game breaks replay.

**What the switch is still for.** It is the seam that made landing the batch one boolean
rather than a rewrite, and it is kept because it is what lets a test assert -- in one
process, both ways -- that this module is text and nothing else. :data:`PENDING` is the
batch that was bought, kept as the record of what that hour of quota was spent on.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

__all__ = [
    "PENDING",
    "Pending",
    "TECH_RENAMES",
    "STAGED",
    "enabled",
    "set_enabled",
    "set_staged",
    "staged_enabled",
]


@dataclass(frozen=True, slots=True)
class Pending:
    """One prompt-text change waiting for a tape, and why it is worth a recording."""

    key: str
    #: The call site it is written at, so this list cannot drift from the code.
    where: str
    what: str
    why: str


#: What the re-record bought. Every entry is live in the tree behind :func:`enabled`, which
#: is now on, and every one of them is in the active tape's prompts.
PENDING: tuple[Pending, ...] = (
    Pending(
        key="air-interdiction",
        where="atlas.engine.air.air_rules_block",
        what="An interdiction bullet: an enemy-held sky adds two hops to a three-hop "
             "supply budget, and the corridor runs out in a third of the distance.",
        why="The strongest air effect in the engine and the only one the model's own air "
            "rules block never mentioned. A staff that cannot see it reads a collapsed "
            "corridor as the engine being broken -- which is the exact failure the block "
            "was written to prevent for interception, one bullet further down.",
    ),
    Pending(
        key="air-lapse",
        where="atlas.engine.air.air_rules_block",
        what="A lapse bullet: an unflown sky loses a quarter of a point of margin a month "
             "and is let go of under the dead band.",
        why="Air superiority is maintained, not owned, and nothing told the model that. A "
            "power that thinks a sky won in 1947 is still its own in 1950 plans around a "
            "bonus that has already gone.",
    ),
    Pending(
        key="solid-fuel-rename",
        where="atlas.engine.research.catalogue_for",
        what="`Solid-fuel missiles` -> `Solid-fuel naval missiles`.",
        why="The blurb has already been corrected for free -- a technology's blurb is the "
            "player's alone and is not on the tape. Its *name* is, with its effects, and "
            "the name is what a model reads: `Solid-fuel missiles ... -> cruiser "
            "base_strength +2.5, submarine base_strength +2` invites a strategic reading "
            "of a naval armament. One word fixes it and only a recording can land it.",
    ),
)

#: Programme names the batch corrects, by ``Technology.id``. Applied in
#: :func:`atlas.engine.research.catalogue_for`, which is the one place a catalogue is read
#: from, and nowhere else -- the ``technology`` table stores ``tech_id`` and never a name,
#: so this is render-time text and cannot reach a saved world.
TECH_RENAMES: dict[str, str] = {
    "solid_fuel_missiles": "Solid-fuel naval missiles",
}


def _flag_from_env() -> bool:
    """``ATLAS_AIR_REBRIEF=off`` takes the batch back out of the prompts.

    **Default on**, and the parse is now the negative reading -- :mod:`atlas.engine.consent`'s
    -- for the reason that module gives. The inverse was right while the batch was unpaid: a
    typo then must not silently redden the release gate. The tape has since been recorded
    with these strings in it, so the failure to guard against is the opposite one, and a typo
    must leave them in.
    """
    raw = os.environ.get("ATLAS_AIR_REBRIEF", "on").strip().lower()
    return raw not in {"0", "off", "false", "no"}


_ENABLED: bool = _flag_from_env()


def enabled() -> bool:
    return _ENABLED


def set_enabled(value: bool) -> bool:
    """Move the flag, returning what it was."""
    global _ENABLED
    was, _ENABLED = _ENABLED, bool(value)
    return was


# ------------------------------------------------------------------ the next batch, unpaid

#: Written, reviewed and tested since the last recording, and **off** until the next one --
#: the state :data:`PENDING` was in before its batch was bought. Landing these is flipping
#: ``ATLAS_STAGED_REBRIEF`` on for a live re-record and then making it the default.
STAGED: tuple[Pending, ...] = (
    Pending(
        key="truce-breach-brief",
        where="atlas.engine.gambit._at_war_clause",
        what="Each wartime-only undertaking says that, used on a ceasefire partner, it "
             "breaks the ceasefire (covert kinds: if it is caught).",
        why="Finding W1 of the 2026-09-26 bug scan made a strike through a truce break "
            "it, as an ordered ground attack already did. An undisclosed consequence is "
            "decision 50's failure, so the rules block should say so -- but the mechanic "
            "is live without it, and the sentence alone is not worth a recording.",
    ),
)


def _staged_from_env() -> bool:
    """``ATLAS_STAGED_REBRIEF=on`` puts the unpaid batch into the prompts.

    **Default off**, and the parse is the positive reading, for the reason
    :func:`_flag_from_env` gives for the batch while it was unpaid: a typo must not
    silently redden the release gate.
    """
    raw = os.environ.get("ATLAS_STAGED_REBRIEF", "off").strip().lower()
    return raw in {"1", "on", "true", "yes"}


_STAGED: bool = _staged_from_env()


def staged_enabled() -> bool:
    return _STAGED


def set_staged(value: bool) -> bool:
    """Move the staged flag, returning what it was."""
    global _STAGED
    was, _STAGED = _STAGED, bool(value)
    return was
