"""A stale pack is told to re-bake, rather than reported as broken geometry.

**The failure this closes cost a review cycle.** Packs live outside the repository, under
``%LOCALAPPDATA%``. A geometry fix therefore changes the *baker* and not the artefact: the
pack on disk keeps whatever the baker was on the day it was made, and nothing anywhere
noticed. The Gibraltar seam was fixed at `2ea080a`/`3b6c326`; a reviewer running the audit
against a pack baked before it saw a shared-border failure at LOD 1 and reported a
regression in code that had been correct for days. The code was fine. The pack was old.

So a pack now carries the version that wrote it. The stamp is applied by `write_pack`
rather than by the baker, because that is the function which produces the artefact and a
caller cannot forget to say so. `verify_pack` checks it **first**, before any geometry
check: geometry produced by an old writer is not evidence about this tree, and every line
below would be describing the wrong artefact. The message names the command that fixes it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

#: **Every test here bakes**, and baking is the one thing in this package that needs the
#: geometry stack. `shapely` lives in the optional `bake` extra rather than in `dev`,
#: deliberately: it is a heavy dependency used only by the offline pipeline, and requiring
#: it to run the suite would make every contributor install it to exercise tests that have
#: nothing to do with geometry.
#:
#: So a checkout without it **skips these six and runs the rest green**. It used to raise
#: six collection errors instead, which is a plain dev checkout failing to distinguish "you
#: have not installed the optional extra" from "something is broken" -- and Codex hit
#: exactly that. The sibling files already skip on their own preconditions
#: (`importorskip("atlas.map.pack")`, `skipif(not _baked(...))`); this is the same idea for
#: the one precondition that is a dependency rather than an artefact.
pytest.importorskip(
    "shapely",
    reason="baking needs the geometry stack: pip install -e '.[bake]'")

from atlas.map.bake import bake  # noqa: E402
from atlas.map.pack import BAKER_VERSION, GeometryPack  # noqa: E402
from atlas.map.verify import verify_pack  # noqa: E402


@pytest.fixture(scope="module")
def fresh(tmp_path_factory) -> Path:
    """A pack baked from current source, which is the only kind that proves anything."""
    return bake("admin0-110m", destination=tmp_path_factory.mktemp("packs") / "p.pack").pack_path


def _restamp(source: Path, target: Path, baked_by: object) -> Path:
    """The same pack with a different baker stamp, so the check can be exercised without
    keeping a museum of obsolete binaries in the repository."""
    import struct

    from atlas.map.pack import MAGIC

    raw = source.read_bytes()
    header_struct = struct.Struct("<8sI32s")
    magic, length, digest = header_struct.unpack_from(raw, 0)
    assert magic == MAGIC
    start = header_struct.size
    header = json.loads(raw[start:start + length])
    if baked_by is None:
        header.pop("baked_by", None)
    else:
        header["baked_by"] = baked_by
    rewritten = json.dumps(header, separators=(",", ":")).encode("utf-8")
    target.write_bytes(
        header_struct.pack(magic, len(rewritten), digest)
        + rewritten
        + raw[start + length:])
    return target


# ------------------------------------------------------------------ what a bake stamps


def test_a_fresh_bake_carries_the_current_baker(fresh):
    with GeometryPack(fresh) as pack:
        assert pack.baked_by == BAKER_VERSION


def test_a_fresh_bake_verifies_clean(fresh):
    """The whole point of the investigation: current source produces a pack with no
    problems, so a problem on disk is a fact about the disk."""
    with GeometryPack(fresh) as pack:
        assert verify_pack(pack).problems == []


# ------------------------------------------------------------- and what an old one gets


def test_a_pack_with_no_stamp_is_treated_as_ancient(fresh, tmp_path):
    """Every pack baked before this existed. Absent reads as zero, which is older than any
    version that carries one and therefore compares correctly."""
    old = _restamp(fresh, tmp_path / "unstamped.pack", None)

    with GeometryPack(old) as pack:
        assert pack.baked_by == 0
        problems = verify_pack(pack).problems

    assert len(problems) == 1
    assert "re-bake" in problems[0].lower() or "bake --source" in problems[0]


def test_the_message_names_the_command_that_fixes_it(fresh, tmp_path):
    """A gate that says *your geometry is broken* sends a reviewer to debug a simplifier.
    One that says *re-bake this* sends them to a shell."""
    old = _restamp(fresh, tmp_path / "old.pack", BAKER_VERSION - 1)

    with GeometryPack(old) as pack:
        [problem] = verify_pack(pack).problems

    assert f"atlas map bake --source {pack.pack_id}" in problem
    assert str(BAKER_VERSION) in problem


def test_staleness_is_checked_before_the_geometry_is(fresh, tmp_path):
    """**The ordering is the point.** An old pack must report exactly one thing -- that it
    is old -- and not a list of topology findings about an artefact this tree did not
    produce. Those findings are what read as a regression."""
    old = _restamp(fresh, tmp_path / "old.pack", 1)

    with GeometryPack(old) as pack:
        report = verify_pack(pack)

    assert len(report.problems) == 1
    assert report.warnings == []
    assert report.shared_borders_checked == 0, "it should not have got as far as borders"


def test_a_pack_from_the_future_is_not_refused(fresh, tmp_path):
    """Only *older* is a problem. A pack from a newer baker is somebody else's tree being
    ahead, which is not this check's business and not a reason to refuse to draw a map."""
    ahead = _restamp(fresh, tmp_path / "ahead.pack", BAKER_VERSION + 1)

    with GeometryPack(ahead) as pack:
        assert verify_pack(pack).problems == []


# ---------------------------------------------------------------------- and it is wired


def test_the_shipped_packs_are_current():
    """The packs this machine actually audits against. Fails loudly the day somebody bumps
    `BAKER_VERSION` and forgets to re-bake, which is the whole failure being closed."""
    from atlas.testing.release import _scenario_packs

    wanted = sorted({p for paths in _scenario_packs().values() for p in paths})
    present = [path for path in wanted if path.exists()]
    if not present:
        pytest.skip("no packs baked on this machine")

    for path in present:
        with GeometryPack(path) as pack:
            assert pack.baked_by >= BAKER_VERSION, (
                f"{path.name} is stale: atlas map bake --source {pack.pack_id}")


def test_bumping_the_version_is_what_makes_this_work():
    """A guard on the guard. The number only helps if it moves when the geometry does; it
    is documented as such where it is defined, and this asserts the doc is there to read."""
    import inspect

    from atlas.map import pack as module

    source = inspect.getsource(module)
    at = source.index("BAKER_VERSION = ")
    preamble = source[max(0, at - 1400):at]
    assert "Bump this whenever" in preamble
