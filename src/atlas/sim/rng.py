"""Deterministic randomness for ATLAS.

The model narrates outcomes; it never chooses them. Contested results -- battles,
sieges, coup attempts, whether a bluff is called -- are drawn here, in Python, weighted
by real force ratios, terrain and supply, from a stream derived from the world's stored
seed. Same seed plus same inputs always yields the same outcome, which is what makes
rewind trustworthy and bug reports reproducible.

**Why not one shared Random object.** A single stream makes every draw depend on how
many draws happened before it. Under an async orchestrator the call order is not stable,
and worse, adding or removing *any* provider call anywhere in the turn shifts every
subsequent battle result. Rewind-and-replay would diverge from the original run for
reasons entirely unrelated to the game. Instead each call site derives its own
independent stream from ``(seed, world, turn, call_key)``, so a draw depends only on
*where and when* it happens -- never on what else happened first.

**Why only ``.random()`` and ``.getrandbits()``.** The internal algorithms behind
``sample``, ``shuffle`` and ``choice`` are not guaranteed stable across CPython
versions; ``random()`` and ``getrandbits()`` are. A saved world must replay identically
after a Python upgrade, so the helpers here are built from those two primitives only.
``ruff`` bans the ambient ``random`` module inside the engine to keep this honest.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from hashlib import blake2b
from typing import TypeVar

__all__ = ["derive", "chance", "pick", "pick_index", "shuffled", "jitter"]

T = TypeVar("T")


def _seed_int(seed: int, world_id: str, turn: int, call_key: str) -> int:
    """Hash the coordinates of a draw into a 128-bit seed.

    blake2b rather than ``hash()``: the built-in is salted per process for strings, so it
    would give a different stream on every run -- silently destroying reproducibility in
    exactly the way that is hardest to notice.
    """
    payload = f"{seed}\x1f{world_id}\x1f{turn}\x1f{call_key}".encode()
    return int.from_bytes(blake2b(payload, digest_size=16).digest(), "big")


def derive(seed: int, world_id: str, turn: int, call_key: str) -> random.Random:
    """Return the independent stream for one specific decision point.

    ``call_key`` must identify the decision, not the order it occurred in: use
    ``"battle:r14:jpn-vs-usa"``, never ``"battle:3"``. An ordinal key reintroduces exactly
    the call-order dependence this design exists to remove.
    """
    return random.Random(_seed_int(seed, world_id, turn, call_key))


def chance(rng: random.Random, probability: float) -> bool:
    """Draw a weighted coin. ``probability`` is clamped, so callers may pass raw scores."""
    p = min(1.0, max(0.0, probability))
    if p <= 0.0:
        return False
    if p >= 1.0:
        return True
    return rng.random() < p


def pick_index(rng: random.Random, weights: Sequence[float]) -> int:
    """Weighted index selection built from ``random()`` alone.

    Negative weights are treated as zero rather than raising: weights are computed from
    game state and a transient negative (a modifier overshooting) should bias the draw
    away from that option, not abort the turn.
    """
    cleaned = [max(0.0, float(w)) for w in weights]
    total = sum(cleaned)
    if total <= 0.0:
        # Every option is impossible; fall back to a uniform draw so the caller still
        # gets a legal answer instead of an exception mid-resolution.
        return int(rng.random() * len(cleaned)) if cleaned else 0
    threshold = rng.random() * total
    cumulative = 0.0
    for index, weight in enumerate(cleaned):
        cumulative += weight
        if threshold < cumulative:
            return index
    return len(cleaned) - 1


def pick(rng: random.Random, options: Sequence[T], weights: Sequence[float] | None = None) -> T:
    if not options:
        raise ValueError("pick() needs at least one option")
    if weights is None:
        return options[int(rng.random() * len(options)) % len(options)]
    if len(weights) != len(options):
        raise ValueError("weights must be the same length as options")
    return options[pick_index(rng, weights)]


def shuffled(rng: random.Random, items: Sequence[T]) -> list[T]:
    """Fisher-Yates using ``random()`` only, so the order is stable across versions."""
    result = list(items)
    for i in range(len(result) - 1, 0, -1):
        j = int(rng.random() * (i + 1))
        result[i], result[j] = result[j], result[i]
    return result


def jitter(rng: random.Random, value: float, spread: float) -> float:
    """Apply +/- ``spread`` proportional noise. Used to blur outcomes without inverting them."""
    return value * (1.0 + (rng.random() * 2.0 - 1.0) * spread)
