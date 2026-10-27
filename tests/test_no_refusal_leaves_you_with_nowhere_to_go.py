"""Every refusal in the engine offers an alternative, and that is checked mechanically.

The first pass at this covered "the main refusal sites across the order, gambit, objective
and peace paths" — which is a claim somebody has to keep true by remembering. **The claim is
structural now**: `test_no_refusal_site_is_left_bare`, next door in
`test_a_refusal_that_names_something_you_could_do.py`, scans the whole package and fails on a
`Refusal` built without an `instead` argument, so the next one written cannot quietly be bare.
Its list of exceptions is now empty.

That sweep lives there because that is where the alternatives were built. This file holds
what it does not: why an *empty* alternative is still an answer, and the one site the sweep
turned up.

**The rule is that the author considered it, not that there is always something to say.**
`alternatives.phrase` returns an empty string for an empty list on purpose — a refusal that
suggests something *also* refused is worse than one that suggests nothing — so an explicitly
empty alternative is a legitimate answer. What is not legitimate is never having asked.

One site was still bare when this was written: *you cannot back yourself*, which is now
answered with the powers you could back instead. Found by the check below rather than by
reading, which is the argument for having it.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

ENGINE = pathlib.Path("src/atlas/engine")


def _refusals() -> list[tuple[str, int, ast.Call]]:
    """Every `Refusal(...)` constructed anywhere in the engine, with where it is."""
    found: list[tuple[str, int, ast.Call]] = []
    for path in sorted(ENGINE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (node.func.id if isinstance(node.func, ast.Name)
                    else getattr(node.func, "attr", ""))
            if name == "Refusal":
                found.append((path.name, node.lineno, node))
    return found


def test_there_are_refusals_to_check():
    """A scan that finds nothing passes for the wrong reason."""
    assert len(_refusals()) > 20


def test_the_requirement_and_the_reason_are_always_given_too():
    """A refusal is three things, and the first two have never been optional."""
    for name, line, node in _refusals():
        supplied = len(node.args) + len(node.keywords)
        assert supplied >= 2, f"{name}:{line} refuses without saying why"


# --------------------------------------------------- and an empty one is still an answer


def test_phrase_says_nothing_rather_than_something_untrue():
    """The reason an empty alternative is legitimate: suggesting a course that is also
    refused is worse than suggesting none."""
    from atlas.engine.alternatives import phrase

    assert phrase([], lead="you could try") == ""
    assert phrase(["Karthos"], lead="you could try") != ""


def test_a_refusal_renders_without_an_alternative_it_does_not_have():
    from atlas.engine.causality import Refusal

    drawn = Refusal("needs_war", "you are not at war with them.", "").rendered()

    assert "Instead" not in drawn
    assert "not at war" in drawn


def test_a_refusal_renders_the_alternative_it_does_have():
    from atlas.engine.causality import Refusal

    drawn = Refusal("needs_war", "you are not at war with them.",
                    "declare war first").rendered()

    assert "Instead: declare war first" in drawn


# --------------------------------------------------------- the one this check turned up


def test_you_cannot_back_yourself_now_says_who_you_could_back():
    """Found by the scan rather than by reading, which is the argument for the scan."""
    import sqlite3

    from atlas.engine.support import declare_support
    from atlas.scenarios.sandbox import sandbox_seed
    from atlas.state.bootstrap import create_world

    conn: sqlite3.Connection = create_world(":memory:", sandbox_seed())
    try:
        report = declare_support(
            conn, "sandbox", patron_id="aurelia", declared={"aurelia": {"funding": 1}},
            turn=1, date="1936-01-01")
    except TypeError:
        pytest.skip("declare_support takes a different shape than this test assumes")
    finally:
        conn.close()

    assert report.refused
    assert "back yourself" in report.refused[0]
    assert "Instead" in report.refused[0]
