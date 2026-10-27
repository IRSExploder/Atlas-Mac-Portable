"""Everything a model is shown a place by, it is shown the id of.

Phase-2 handoff §7.2, in its own words: *"The `label` round-trip is proved over vocabularies,
not over prompts. The tests assert every label resolves and that no view uses the old form.
They do not assert that everything a model is shown **is** a label -- a new renderer could
still print a bare name and pass."*

That is the gap. `test_region_labels.py` checks the renderer and the resolver against each
other; this checks the **prompts**, which is where the failure actually happened. PT33 watched
two independent staffs write *Coastal Reaches* every turn for six turns for a province named
**The Reaches**, because a model had been shown the name with no id beside it, invented a
plausible full name in prose, and every later staff read that prose back as history.

So: build every model-facing view and assert that no region is named in it without its id.

**Event prose is excluded, deliberately.** A model's own words legitimately carry bare names
-- that is the *input* the gazetteer exists to correct, not a renderer's output. What is
checked is everything the engine itself writes.
"""

from __future__ import annotations

import re

import pytest

from atlas.engine.reference import vocabulary
from atlas.engine.views import (
    build_diplomacy_view,
    build_parser_view,
    build_tracked_agent_view,
    build_world_sim_view,
)
from atlas.scenarios.sandbox import sandbox_seed
from atlas.state.bootstrap import create_world

WORLD = "sandbox"

#: Where a model's own prose begins. Everything after these headings is quoted history.
_PROSE = ("## What you know has happened", "## What has happened", "## Recent events",
          "## Earlier history")


@pytest.fixture
def rendered():
    """Every model-facing view of one world, with the world given a name of its own.

    The sandbox calls both the world and one of its regions *The Narrow Sea*, so a scan for
    bare region names finds the world's own title and reports a renderer that did nothing
    wrong. Renaming the world is what makes a hit here mean something.
    """
    conn = create_world(":memory:", sandbox_seed())
    conn.execute("UPDATE world SET name = 'A Quiet Corner' WHERE id = ?", (WORLD,))
    conn.execute("UPDATE nation SET is_tracked = 1")
    conn.commit()
    views = {
        "world_sim": build_world_sim_view(conn, WORLD).text,
        "tracked_agent": build_tracked_agent_view(conn, WORLD, nation_id="karthos").text,
        "diplomacy": build_diplomacy_view(
            conn, WORLD, nation_id="karthos", counterpart_id="aurelia").text,
        "parser": build_parser_view(
            conn, WORLD, nation_id="karthos", raw_text="march north").text,
    }
    regions = dict(
        (name, entry_id) for entry_id, name in vocabulary(conn, WORLD, "region"))
    conn.close()
    return views, regions


def _bare(text: str, regions: dict[str, str]) -> list[tuple[str, str]]:
    """Every place the engine names a region without its id, outside quoted prose."""
    body = text
    for heading in _PROSE:
        body = body.split(heading)[0]

    found: list[tuple[str, str]] = []
    for name, entry_id in regions.items():
        # `Name [id]` and ``Name (`id`)`` are both labels, and either may be wrapped in the
        # bold markers the briefings use for emphasis.
        after = re.compile(r"\**\s*[\[(]`?" + re.escape(entry_id) + r"`?[\])]")
        for match in re.finditer(r"(?<![\w'])" + re.escape(name) + r"(?![\w'])", body):
            if after.match(body[match.end():match.end() + len(entry_id) + 8]):
                continue
            line = body[body.rfind("\n", 0, match.start()) + 1:
                        body.find("\n", match.end())]
            found.append((name, line.strip()))
    return found


@pytest.mark.parametrize(
    "view", ["world_sim", "tracked_agent", "diplomacy", "parser"])
def test_no_view_names_a_region_without_its_id(rendered, view):
    """The property §7.2 said was unasserted. It holds in all four views."""
    views, regions = rendered
    bare = _bare(views[view], regions)

    assert not bare, (
        f"{view} names a region with no id beside it, which is how *Coastal Reaches* got "
        f"invented:\n" + "\n".join(f"    {name}: {line}" for name, line in bare[:6])
    )


def test_the_scan_would_notice_a_renderer_that_printed_a_bare_name(rendered):
    """**A guard on the guard.** Four views passing is only worth something if the scan can
    fail -- and the failure it exists for is one line of one renderer regressing to a bare
    name, which is exactly what it looked like the first time."""
    views, regions = rendered
    # Injected *before* the quoted history, because that is where a renderer lives. Appending
    # it to the end would land inside the prose the scan deliberately ignores, and the guard
    # would pass by looking away -- which this test exists to make impossible.
    doctored = views["tracked_agent"].replace(
        "## Your position",
        "## Your position\n- the front line runs through The Reaches", 1)
    assert "runs through The Reaches" in doctored, "the injection point moved"

    bare = _bare(doctored, regions)

    assert [name for name, _line in bare] == ["The Reaches"], bare


def test_a_labelled_name_in_either_shape_is_accepted(rendered):
    """Both forms the engine has ever shown, so the scan does not report the older one as a
    regression while it is still legal to resolve."""
    _views, regions = rendered

    assert not _bare("- The Reaches [r_coast] -- quiet", regions)
    assert not _bare("- The Reaches (`r_coast`) -- quiet", regions)
    assert not _bare("- **The Reaches** [r_coast] -- quiet", regions)


def test_quoted_history_is_left_alone(rendered):
    """A model's own words are the input the gazetteer corrects, not a renderer's output.
    Scanning them would report the engine for faithfully quoting what a model wrote."""
    _views, regions = rendered
    text = (
        "## Your position\n- The Reaches [r_coast] -- quiet\n"
        "## What you know has happened\n- [7] turn 3 (public): fighting reached The Reaches"
    )

    assert not _bare(text, regions)
