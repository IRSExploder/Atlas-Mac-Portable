"""A peace treaty that ceded a detached province was refused as a teleport.

OWN-001 stops territory changing hands without a reason: the new owner must hold adjacent
ground (conquest has a front) or a ratified cession must name the province (diplomacy has a
paper trail). It read that paper trail in one place only --

    AND c.kind = 'territory_cession'
    AND c.condition_text LIKE ?

-- the commitment *kind* and the agreement's **prose**. `peace.settle` writes a cession as a
`peace_treaty` carrying a `cede_region` peace_term, in the same batch as the transfer
itself, and the comment beside that write calls it "the ratified cession the permission
model reserves for exactly this moment". It was not reserved; it was not recognised.

Where it bites: an overseas or otherwise detached province. Most peace cessions are ground
the winner just fought across and therefore borders, so the adjacency clause carries them
and this never shows -- which is why it survived. A settlement handing over a far island was
refused, and because a batch is all-or-nothing the whole peace went with it.

Read from the structured term rather than the prose, which makes the new clause *narrower*
than the old one: it names the ground and the power it goes to, so it cannot be satisfied
by a region id that merely appears somewhere in a paragraph.
"""

from __future__ import annotations

from atlas.state.apply import apply_batch, record_event
from atlas.state.diff import Diff, DiffBatch
from atlas.state.models import DiffOp, EventKind, EventOrigin, Role, Visibility

WORLD = "sandbox"


def _detached(conn):
    """A province of Veyra's that borders nothing at all -- an island."""
    conn.execute(
        "INSERT INTO region (id, world_id, name, geo_key, kind, owner_nation_id,"
        " controller_nation_id) VALUES ('r_isle', ?, 'Far Isle', 'geo:isle', 'coastal',"
        " 'veyra', 'veyra')", (WORLD,))


def _agreement(conn, kind, *, condition_text="a settlement"):
    commitment_id = int(conn.execute(
        "INSERT INTO commitment (world_id, kind, status, condition_text, created_turn)"
        " VALUES (?, ?, 'active', ?, 0)", (WORLD, kind, condition_text)).lastrowid)
    for nation in ("aurelia", "veyra"):
        conn.execute(
            "INSERT INTO commitment_nation (commitment_id, nation_id, accepted_turn)"
            " VALUES (?, ?, 0)", (commitment_id, nation))
    return commitment_id


def _transfer(conn):
    event_id = record_event(
        conn, world_id=WORLD, turn=1, date="1897-04-01", kind=EventKind.TREATY_SIGNED,
        origin=EventOrigin.ENGINE, actor_nation_id="aurelia",
        text="A settlement is signed.", visibility=Visibility.PUBLIC)
    conn.commit()
    return apply_batch(conn, DiffBatch(
        world_id=WORLD, turn=1, proposer_role=Role.ENGINE, proposer_model=None,
        diffs=(
            Diff("region", "r_isle", "owner_nation_id", DiffOp.SET, "aurelia",
                 reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),
            Diff("region", "r_isle", "controller_nation_id", DiffOp.SET, "aurelia",
                 reason_event_id=event_id, visibility_scope=Visibility.PUBLIC),
        )))


def _hit(outcome):
    return {v.rule_id for v in outcome.validation.violations}


def test_a_peace_treaty_can_cede_a_province_nobody_borders(conn):
    """**The headline.** The peace_term is written in the same batch as the transfer, which
    is how `peace.settle` does it."""
    _detached(conn)
    commitment_id = _agreement(conn, "peace_treaty")
    conn.execute(
        "INSERT INTO peace_term (id, world_id, commitment_id, seq, kind, obliged_id,"
        " beneficiary_id, subject_id, value, outstanding)"
        " VALUES (?, ?, ?, 0, 'cede_region', 'veyra', 'aurelia', 'r_isle', 0, 0)",
        (f"pt_{commitment_id}_0", WORLD, commitment_id))

    assert "OWN-001" not in _hit(_transfer(conn))


def test_a_cession_clause_on_any_agreement_is_a_paper_trail(conn):
    """`treaties` records the same act on `commitment_term`, which names no beneficiary --
    so party membership does that work."""
    _detached(conn)
    commitment_id = _agreement(conn, "territory_cession")
    conn.execute(
        "INSERT INTO commitment_term (commitment_id, seq, world_id, clause, obliged_id,"
        " subject_id, value) VALUES (?, 0, ?, 'cede_region', 'veyra', 'r_isle', 0)",
        (commitment_id, WORLD))

    assert "OWN-001" not in _hit(_transfer(conn))


def test_the_prose_path_still_works(conn):
    """The clause that was already there, kept: an older agreement carries its cession in
    `condition_text` and nothing else."""
    _detached(conn)
    _agreement(conn, "territory_cession", condition_text="Veyra cedes r_isle to Aurelia.")

    assert "OWN-001" not in _hit(_transfer(conn))


def test_a_treaty_that_cedes_nothing_still_cannot_move_a_province(conn):
    """The rule has to keep refusing. An agreement exists, both are party to it, and it
    says nothing about this ground."""
    _detached(conn)
    _agreement(conn, "peace_treaty")

    assert "OWN-001" in _hit(_transfer(conn)), (
        "territory still may not teleport just because the two powers have signed "
        "something"
    )


def test_a_cession_to_somebody_else_does_not_license_this_one(conn):
    """And the beneficiary is checked, not just the ground."""
    _detached(conn)
    commitment_id = _agreement(conn, "peace_treaty")
    conn.execute(
        "INSERT INTO peace_term (id, world_id, commitment_id, seq, kind, obliged_id,"
        " beneficiary_id, subject_id, value, outstanding)"
        " VALUES (?, ?, ?, 0, 'cede_region', 'veyra', 'karthos', 'r_isle', 0, 0)",
        (f"pt_{commitment_id}_0", WORLD, commitment_id))

    assert "OWN-001" in _hit(_transfer(conn))
