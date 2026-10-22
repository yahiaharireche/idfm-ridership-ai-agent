# Automatic tests for the evaluation's automatic checks, evaluation/grade.py.
#
# If these checks were wrong, every score built on them would be wrong too, so
# they are tested on answers written by hand. No model is called.
#
# Run from the project root:  .venv/bin/python -m pytest tests

import pytest

from evaluation import grade
from evaluation.questions import QUESTIONS


@pytest.mark.parametrize("text", [
    "1,695,721,547 taps",
    "1 695 721 547 taps",
    "1 695 721 547 taps",     # narrow non-breaking spaces
    "1.695.721.547 taps",
    "**1695721547** taps",
])
def test_numbers_are_found_however_they_are_written(text):
    assert grade.has_number(text, 1695721547)


def test_a_number_inside_a_longer_one_does_not_count():
    assert not grade.has_number("16957215470 taps", 1695721547)
    assert not grade.has_number("4636", 463)


@pytest.mark.parametrize("text, value, found", [
    ("about 36% of taps", 36.0, True),
    ("35.97 % of taps", 36.0, True),
    ("36,0 % des validations", 36.0, True),
    ("down 98.5%", 98.5, True),
    ("about 40% of taps", 36.0, False),
    ("36 taps", 36.0, False),             # a number without % is not a percentage
])
def test_percentages(text, value, found):
    assert grade.has_percent(text, value, 0.3) == found


def test_names_ignore_special_hyphens():
    item = {"names": ["Saint-Lazare"]}
    assert grade.checks(item, [], "Gare Saint‑Lazare")["names"]


@pytest.mark.parametrize("text, lang", [
    ("There were 1,251,011,175 taps in 2021 on the rail network.", "en"),
    ("Il y a eu 1 251 011 175 validations sur le réseau ferré en 2021.", "fr"),
])
def test_language(text, lang):
    assert grade.language(text) == lang


def test_refusal():
    item = {"refuse": True}
    assert grade.checks(item, [], "This cannot be answered from this data.")["refusal"]
    assert not grade.checks(item, [], "There will be 2.3 billion taps.")["refusal"]


def test_mentions_need_one_word_from_each_group():
    item = {"mentions": [["hidden", "suppress"], ["48", "916"]]}
    result = grade.checks(item, [], "916M taps were not attributed; counts were hidden.")
    assert result == {"mentions_1": True, "mentions_2": True}
    result = grade.checks(item, [], "Counts were hidden.")
    assert result == {"mentions_1": True, "mentions_2": False}


def test_every_question_is_complete():
    ids = []
    for item in QUESTIONS:
        ids.append(item["id"])
        assert item["group"] in ["planner", "analyst", "communications", "trap", "refusal", "robustness"]
        assert item["question"] and item["notes"] and item["language"]
    assert len(ids) == len(set(ids))       # no id used twice
