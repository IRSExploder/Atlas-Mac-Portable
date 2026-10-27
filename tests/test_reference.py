"""The reference boundary: one resolver, five vocabularies, three answers.

The engine is strict about ids and shows people names, and every naming bug this project
has had lives in that gap. What is pinned here:

* the boundary knows all five vocabularies, including the three that are not tables;
* **ambiguous is a different answer from unknown**, because it is the useful one -- the
  vocabulary was right and the phrase was not specific enough;
* the engine stays as strict as it was: a phrase that resolves to nothing is refused, and
  refused by the spelling that was written;
* a whole-word rule, because the substring test it replaces matched `rail` inside any
  sentence containing the letters.
"""

from __future__ import annotations

import pytest

from atlas.engine.reference import (
    VOCABULARIES,
    mentioned_in,
    resolve,
    vocabulary,
)


def _r(conn, kind, text):
    return resolve(conn, "sandbox", kind, text)


# --------------------------------------------------------------- the five vocabularies


def test_every_vocabulary_can_be_listed(conn):
    """A vocabulary the boundary cannot enumerate is one it cannot resolve into."""
    for kind in VOCABULARIES:
        entries = vocabulary(conn, "sandbox", kind)
        assert isinstance(entries, tuple)
        for entry_id, name in entries:
            assert entry_id and isinstance(entry_id, str)
            assert isinstance(name, str)


def test_an_unknown_vocabulary_is_a_programming_error(conn):
    with pytest.raises(ValueError, match="unknown vocabulary"):
        vocabulary(conn, "sandbox", "sandwiches")


@pytest.mark.parametrize("kind", ["region", "nation", "project", "technology"])
def test_an_id_resolves_to_itself(conn, kind):
    entries = vocabulary(conn, "sandbox", kind)
    if not entries:
        pytest.skip(f"the sandbox has no {kind} to resolve")
    entry_id, _name = entries[0]
    assert _r(conn, kind, entry_id).value == entry_id


@pytest.mark.parametrize("kind", ["region", "nation", "project", "technology"])
def test_a_displayed_name_resolves_to_its_id(conn, kind):
    """**The bug class this exists for.** Briefings show names; the engine wants ids."""
    entries = vocabulary(conn, "sandbox", kind)
    if not entries:
        pytest.skip(f"the sandbox has no {kind} to resolve")
    entry_id, name = entries[0]
    assert _r(conn, kind, name).value == entry_id


def test_a_technology_resolves_by_the_name_a_player_actually_reads(conn):
    """The specific failure: *All-welded construction* was ordered, and `build_project`
    had no id to give it, so it became `other` and was refused as anachronistic."""
    assert _r(conn, "technology", "All-welded construction").value == "welding"
    assert _r(conn, "technology", "welding").value == "welding"


def test_case_and_spacing_do_not_matter(conn):
    assert _r(conn, "nation", "KARTHOS").value == "karthos"
    assert _r(conn, "project", "Housing Programme").value == "housing"


# ------------------------------------------------------------------- the three answers


def test_an_unknown_reference_is_refused_by_what_was_written(conn):
    """That message is the only thing telling the writer what to say instead, so it must
    quote them rather than a resolver's best guess at them."""
    found = _r(conn, "region", "Ruritania")
    assert not found.ok and not found.ambiguous
    assert "Ruritania" in (found.problem or "")


def test_ambiguity_is_reported_as_ambiguity_and_names_the_candidates(conn):
    """`None` used to mean both *no such place* and *seven places match*. The second is
    the useful answer: the vocabulary was right and the phrase was not specific enough."""
    conn.execute("UPDATE region SET name = 'North Marches' WHERE id = 'r_north'")
    conn.execute("UPDATE region SET name = 'North Reaches' WHERE id = 'r_coast'")
    conn.commit()

    found = _r(conn, "region", "North")
    assert found.ambiguous, found
    assert not found.ok
    assert set(found.candidates) >= {"r_north", "r_coast"}
    assert "r_north" in (found.problem or "")


def test_nothing_written_resolves_to_nothing(conn):
    for empty in (None, "", "   "):
        found = _r(conn, "region", empty)
        assert not found.ok and not found.ambiguous


def test_a_resolved_reference_has_no_problem_to_report(conn):
    assert _r(conn, "nation", "Karthos").problem is None


# ------------------------------------------------------------------ names inside prose


def test_a_name_inside_a_sentence_is_found(conn):
    """A parser that leaves the field empty still wrote a sentence, and *begin work on
    radar* has to reach the queue."""
    assert mentioned_in(conn, "sandbox", "technology", "begin work on radar").value == (
        "radar"
    )


def test_only_whole_words_count(conn):
    """**The looseness this replaces.** `entry.id in haystack` matched `rail` inside
    *derailed* and inside any sentence that happened to contain the letters."""
    assert not mentioned_in(conn, "sandbox", "project", "the train derailed").ok
    assert mentioned_in(conn, "sandbox", "project", "build rail lines").value == "rail"


def test_two_names_in_one_sentence_is_ambiguous_not_first_wins(conn):
    found = mentioned_in(conn, "sandbox", "project", "build rail and housing")
    assert found.ambiguous, found
    assert set(found.candidates) == {"rail", "housing"}


def test_a_sentence_naming_nothing_known_resolves_to_nothing(conn):
    assert not mentioned_in(conn, "sandbox", "project", "do something splendid").ok


# ------------------------------------------------- the manifest and the boundary agree


def test_every_capability_target_is_a_vocabulary_the_boundary_knows():
    """The manifest names target kinds; this resolves them. If the two ever disagree, a
    capability declares a target nobody can resolve."""
    from atlas.engine.capabilities import CAPABILITIES

    for capability in CAPABILITIES:
        for target in capability.targets:
            assert target in VOCABULARIES, capability.action_type


# ------------------------------------------------ can a real name be read as a label?
#
# Handoff phase 2, section 6.4: `_LABELLED` reads an id out of brackets and returns it
# before any name matching, and *"I checked all 917 regions round-trip, which is not the
# same as checking no name is misread."* These are the second question.
#
# Swept exhaustively once, off-line, over every vocabulary of both eras -- 1421 entries,
# **not one of which the label regex matches at all**, and every label round-trips. That
# sweep needs the baked packs and thirty seconds; what is kept here is the shape of the
# hazard, so a name that acquires a bracketed tail is caught by the suite rather than by
# a war in the wrong province.


def test_no_name_in_the_world_reads_as_a_label(conn):
    """A name ending in a bracketed token would be read as `Name [id]` and its tail taken
    for an id. None does."""
    from atlas.engine.reference import _LABELLED, VOCABULARIES, vocabulary

    offenders = [
        (kind, name)
        for kind in VOCABULARIES
        for _entry_id, name in vocabulary(conn, "sandbox", kind)
        if _LABELLED.match(name)
    ]
    assert not offenders, offenders


def test_a_name_with_a_bracketed_tail_that_is_not_an_id_still_resolves_by_name(conn):
    """The regex matching is not the hazard; the id being real is. A place genuinely
    called *Sector [7]* has no id `7` behind it, so the tier declines and the ordinary
    name match answers."""
    conn.execute(
        "INSERT INTO region (id, world_id, name, geo_key, kind, owner_nation_id, terrain,"
        " population, development) VALUES ('r_seven', 'sandbox', 'Sector [7]', 'geo:7',"
        " 'land', 'veyra', 'plains', 1.0, 1.0)")
    conn.commit()

    found = resolve(conn, "sandbox", "region", "Sector [7]")
    assert found.value == "r_seven", found


def test_a_name_whose_bracketed_tail_is_a_real_id_comes_back_ambiguous(conn):
    """The sharp case, and the one worth having a name for.

    A region genuinely called *Sector [r_east]* is indistinguishable, as written, from a
    label for the Veyran Plateau. Taking the id would act on the plateau while the sentence
    says Sector; taking the name would ignore an id that is real. It comes back ambiguous
    naming both -- *these are two different things* is the useful message, and it is the
    same answer `Germany [fra]` gets.
    """
    conn.execute(
        "INSERT INTO region (id, world_id, name, geo_key, kind, owner_nation_id, terrain,"
        " population, development) VALUES ('r_odd', 'sandbox', 'Sector [r_east]',"
        " 'geo:odd', 'land', 'veyra', 'plains', 1.0, 1.0)")
    conn.commit()

    found = resolve(conn, "sandbox", "region", "Sector [r_east]")
    assert found.value is None, f"it picked {found.value!r} out of two real readings"
    assert set(found.candidates) >= {"r_east", "r_odd"}, found.candidates


def test_every_label_this_world_can_print_resolves_back(conn):
    """The round trip, over every entry of every vocabulary. Cheap here, and it is the
    property the gazetteer and every briefing rest on."""
    from atlas.engine.reference import VOCABULARIES, label_of, vocabulary

    for kind in VOCABULARIES:
        for entry_id, _name in vocabulary(conn, "sandbox", kind):
            shown = label_of(conn, "sandbox", kind, entry_id)
            assert resolve(conn, "sandbox", kind, shown).value == entry_id, shown
