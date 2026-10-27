"""ATLAS -- an AI-driven alternate-history grand strategy sandbox.

Three model providers, driven as local CLI subprocesses, sit behind a deterministic
engine. The models propose; the engine decides. World state lives in SQLite and is the
only channel between calls -- no model ever sees another model's output, and no model
remembers anything between turns.
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
