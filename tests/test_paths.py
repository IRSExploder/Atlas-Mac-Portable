"""The isolation policy.

These guard the standing rule that no model call ever runs inside Quartz, Council, or
the ATLAS repo -- and the rule that a live database never lands in a synced folder.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from atlas.config.paths import (
    FORBIDDEN_ROOTS,
    IsolationError,
    assert_grantable,
    assert_isolated,
    is_forbidden,
    runs_root,
)
from atlas.state.db import SyncedPathError, connect


def test_a_sandbox_under_the_runs_root_is_accepted():
    target = runs_root() / "call-0001"
    assert assert_isolated(target) == target.resolve()


@pytest.mark.parametrize(
    "path",
    [
        Path.home() / "OneDrive" / "Desktop" / "Chess Engine competition" / "quartz-engine",
        Path.home() / "OneDrive" / "Desktop" / "Multi-AI app" / "council",
        Path.home() / "OneDrive" / "Desktop" / "Atlas Game",
        Path.home(),
        Path("C:/Windows/System32"),
    ],
)
def test_forbidden_and_unlisted_locations_are_refused(path):
    """The allowlist is what does the work.

    Note the last case: ``C:/Windows/System32`` is not in ``FORBIDDEN_ROOTS`` at all and
    is still refused, because it is not under the runs root. That is the difference
    between an allowlist and a denylist -- a denylist only knows what it remembered.
    """
    with pytest.raises(IsolationError):
        assert_isolated(path)


def test_quartz_and_council_are_named_explicitly():
    """Belt to the allowlist's braces."""
    names = " ".join(str(p).lower() for p in FORBIDDEN_ROOTS)
    assert "quartz-engine" in names
    assert "quartz_reference" in names
    assert "council" in names


def test_a_repo_subdirectory_is_forbidden_recursively():
    inside = Path.home() / "OneDrive" / "Desktop" / "Multi-AI app" / "council" / "providers"
    assert is_forbidden(inside) is not None


def test_home_is_matched_exactly_not_recursively():
    """Home must not be recursive, or it would forbid the runs root itself.

    Everything interesting lives under the user profile, including ``%LOCALAPPDATA%``.
    The recursive repo entries are what keep an agent out of a source tree.
    """
    assert is_forbidden(Path.home()) is not None
    assert is_forbidden(runs_root() / "call-1") is None


def test_add_dir_grants_are_held_to_the_same_standard():
    """``--add-dir`` is the lever that actually constrains agy, since it ignores cwd."""
    assert assert_grantable(runs_root() / "call-2")
    with pytest.raises(IsolationError):
        assert_grantable(Path.home() / "OneDrive" / "Desktop" / "Multi-AI app" / "council")


def test_a_live_database_is_refused_inside_onedrive(tmp_path):
    fake = tmp_path / "OneDrive" / "Desktop" / "Atlas Game" / "world.db"
    fake.parent.mkdir(parents=True)
    with pytest.raises(SyncedPathError, match="sync root"):
        connect(fake)


def test_an_export_may_be_read_from_a_synced_folder(tmp_path):
    """Reading a file the user deliberately saved there is fine; the live DB is not."""
    fake = tmp_path / "OneDrive" / "save.db"
    fake.parent.mkdir(parents=True)
    conn = connect(fake, allow_synced=True)
    conn.close()


# ------------------------------------------------------------------- the eleventh case
#
# The phase-2 handoff's list of riskiest surfaces put `_contains` third and said: *ten cases
# pinned; look for an eleventh.* These are two, and both are about the check being right for
# a reason rather than right by accident.


def test_the_sandbox_root_itself_is_not_a_sandbox():
    """`_contains` accepts identity, so `assert_isolated(runs_root())` passed.

    A call whose working directory is the root can read **every other sandbox in it** --
    another nation's briefing, another role's prompt, another turn's answer. The central
    architectural claim of this project is that models never see each other's output, and
    handing one the drawer that holds all of it is the sharpest possible version of what
    this function exists to prevent. `SandboxDir.create` always makes a child, so nothing
    does it today; the guard should refuse it regardless.
    """
    with pytest.raises(IsolationError, match="sandbox root itself"):
        assert_isolated(runs_root())


def test_the_sandbox_root_may_not_be_granted_to_agy_either():
    """And this is the one that matters more: `agy` ignores `cwd`, so `--add-dir` is the
    only containment it has. One argument would hand it every call of the run."""
    with pytest.raises(IsolationError, match="sandbox root"):
        assert_grantable(runs_root())


def test_a_child_of_the_root_is_still_accepted(tmp_path, monkeypatch):
    """The refusal above must not have closed the ordinary case with it."""
    root = tmp_path / "runs"
    (root / "atlas-run-abc123").mkdir(parents=True)
    monkeypatch.setattr("atlas.config.paths.runs_root", lambda: root)
    assert assert_isolated(root / "atlas-run-abc123")


def test_the_source_tree_is_forbidden_by_identity_and_not_by_spelling():
    """`_default_forbidden` named ATLAS's own tree by the absolute path it happens to sit
    at, so moving the checkout -- or cloning it to review a branch -- would silently stop
    the source tree being a forbidden root. `project_root()` computes it correctly and was
    exported and used by nothing.

    This is the same mistake the module refuses for the MSIX redirect, pointing the other
    way: containment decided by how a path is written rather than by which directory it is.
    """
    from atlas.config.paths import project_root

    assert is_forbidden(project_root()) is not None
    assert is_forbidden(project_root() / "src" / "atlas") is not None


def test_the_forbidden_list_does_not_name_the_same_place_twice():
    """On the machine this was written for, `project_root()` and the hardcoded desktop
    entry are the same directory. A list that names it twice reads like two rules."""
    resolved = [p.resolve() for p in FORBIDDEN_ROOTS]
    assert len(resolved) == len(set(resolved)), resolved
