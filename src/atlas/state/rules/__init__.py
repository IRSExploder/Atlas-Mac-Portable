"""Validation rules, one concern per module.

Importing this package registers every rule. Each rule is an independent predicate over
the post-apply database with a stable id, so it can be tested alone and named in a
rejection message the player actually reads.

Rule id prefixes:

``CORE``   causation, provenance, write permissions, compare-and-swap
``INFO``   information asymmetry -- what a proposer could legitimately know
``UNIT``   military units: creation cost, movement, domain legality
``ECON``   economic magnitudes and the reforms that justify them
``DIP``    relations, treaties, alliances
``WAR``    declarations, conquest, peace
``FOCUS``  Focus Mode tracking cap
"""

from . import core, diplomacy, economy, information, military  # noqa: F401

__all__ = ["core", "diplomacy", "economy", "information", "military"]
