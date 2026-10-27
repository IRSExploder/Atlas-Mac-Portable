"""A peace term reads as a clause -- who gives what to whom -- and cannot be read backwards.

The motivating case, from the Three Fronts match (2026-09-23): South Korea won Kangwon-do
by a treaty its briefing printed as "North Korea: cede region Kangwon-do", read it beside
"Kangwon-do (North Korea's core)", and announced three times that it was honouring "the
peace treaty's cession to North Korea". The receiving side was nowhere in the sentence.
"""

from __future__ import annotations

import pytest

from atlas.engine.peace import ProposedTerm

NAMES = {"prk": "North Korea", "kor": "South Korea", "r_kw": "Kangwon-do"}


def _says(kind: str) -> str:
    return ProposedTerm(kind, obliged="prk", beneficiary="kor", subject="r_kw").describe(NAMES)


def test_a_cession_names_the_side_that_receives_it():
    assert _says("cede_region") == "North Korea cedes Kangwon-do to South Korea"


@pytest.mark.parametrize(("kind", "sentence"), [
    ("restore_region", "North Korea hands Kangwon-do back to South Korea"),
    ("withdraw", "North Korea withdraws its forces from Kangwon-do"),
    ("demilitarise", "North Korea keeps its forces out of Kangwon-do"),
])
def test_every_region_term_is_a_sentence_with_the_obliged_side_as_its_subject(kind, sentence):
    assert _says(kind) == sentence


def test_no_region_term_is_printed_as_a_bare_label():
    for kind in ("cede_region", "restore_region", "withdraw", "demilitarise"):
        assert ":" not in _says(kind)
        assert _says(kind).startswith("North Korea ")


def test_the_terms_that_already_read_as_sentences_are_unchanged():
    assert ProposedTerm("reparations", obliged="prk", beneficiary="kor",
                        value=12).describe(NAMES) == "North Korea pays South Korea 12"
    assert ProposedTerm("recognise", obliged="prk", beneficiary="kor"
                        ).describe(NAMES) == "North Korea recognises South Korea"
