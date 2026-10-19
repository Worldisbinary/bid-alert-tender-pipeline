import random

import pytest

from bidalert.processing.aho_corasick import AhoCorasick, KeywordClassifier


def brute_force(keywords: list[str], text: str) -> set[tuple[int, str]]:
    out = set()
    for k in keywords:
        start = text.find(k)
        while start != -1:
            out.add((start, k))
            start = text.find(k, start + 1)
    return out


def test_classic_example_finds_overlapping_matches():
    ac = AhoCorasick(["he", "she", "his", "hers"])
    found = {(m.start, m.keyword) for m in ac.search("ushers", whole_words=False)}
    assert found == {(1, "she"), (2, "he"), (2, "hers")}


def test_matches_brute_force_on_random_inputs():
    rng = random.Random(7)
    for _ in range(200):
        keywords = ["".join(rng.choice("ab") for _ in range(rng.randint(1, 4))) for _ in range(6)]
        text = "".join(rng.choice("ab") for _ in range(40))
        ac = AhoCorasick(keywords)
        got = {(m.start, m.keyword) for m in ac.iter_matches(text, whole_words=False)}
        assert got == brute_force(sorted(set(keywords)), text)


def test_whole_words_rejects_substrings():
    ac = AhoCorasick(["acid", "lab"])
    assert ac.found_keywords("Placid lake; lab acid") == ("acid", "lab")
    assert ac.found_keywords("placid laboratory") == ()


def test_normalises_case_and_punctuation():
    ac = AhoCorasick(["fume hood"])
    assert ac.found_keywords("Supply of FUME-HOOD (2 nos.)") == ("fume hood",)


def test_empty_keywords_and_text():
    assert AhoCorasick([]).search("anything") == []
    assert AhoCorasick(["x"]).search("") == []
    assert AhoCorasick(["", "  "]).keywords == ()


@pytest.mark.parametrize(
    ("title", "category"),
    [
        ("Supply of laboratory reagents and glassware", "Laboratory Equipment"),
        ("Procurement of sodium hypochlorite", "Chemical Supplies"),
        ("Supply of medicines", "Pharmaceuticals"),
        ("Hiring of vehicles", None),
    ],
)
def test_classifier_infers_category(profile, title, category):
    clf = KeywordClassifier(profile.watchlist, profile.exclude_keywords)
    _, inferred, _ = clf.classify(title)
    assert inferred == category


def test_classifier_reports_exclusions_separately(profile):
    clf = KeywordClassifier(profile.watchlist, profile.exclude_keywords)
    watch, inferred, excluded = clf.classify("Construction of chemical store building")
    assert "chemical" in watch and inferred == "Chemical Supplies"
    assert set(excluded) == {"construction of", "building"}
