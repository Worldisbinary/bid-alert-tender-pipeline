import dataclasses
from datetime import timedelta

import pytest

from bidalert.processing.eligibility import check_eligibility
from bidalert.processing.scoring import NEUTRAL, score_tender, tier_for
from bidalert.profile import load_profile
from tests.conftest import NOW, make_tender


def test_listing_without_requirements_is_eligible_but_unverified(profile):
    r = check_eligibility(make_tender(), profile, NOW)
    assert r.eligible
    assert set(r.unverified) == {"turnover requirement", "experience requirement", "tender value"}


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"closing_at": NOW - timedelta(hours=1)}, "closed"),
        ({"closing_at": NOW + timedelta(days=3)}, "days to deadline"),
        ({"turnover_requirement_cr": 9.0}, "turnover"),
        ({"experience_years_required": 10}, "experience"),
        ({"estimated_value_inr": 100}, "outside"),
    ],
)
def test_hard_rules(profile, overrides, fragment):
    r = check_eligibility(make_tender(**overrides), profile, NOW)
    assert not r.eligible
    assert any(fragment in reason for reason in r.reasons)


def test_excluded_keywords_fail(profile):
    r = check_eligibility(make_tender(), profile, NOW, excluded_keywords=("civil work",))
    assert not r.eligible and "civil work" in r.reasons[0]


def test_contributions_sum_to_total_and_unknowns_are_neutral(profile):
    s = score_tender(
        make_tender(),
        profile,
        NOW,
        inferred_category="Chemical Supplies",
        matched_keywords=("reagents",),
    )
    assert s.total == pytest.approx(sum(f.contribution for f in s.factors), abs=1e-3)
    unknown = {f.name for f in s.factors if not f.known}
    assert unknown == {"state", "msme", "value"}
    assert all(f.value == NEUTRAL for f in s.factors if not f.known)
    # category 0.30 + neutral (0.20 + 0.15 + 0.20) * 0.5 + deadline 0.15 * 13/30
    assert s.total == pytest.approx(0.30 + 0.275 + 0.15 * 13 / 30, abs=1e-3)


def test_fully_known_best_case_scores_one(profile):
    t = make_tender(
        category="Chemical Supplies",
        state="Gujarat",
        msme_preferred=True,
        estimated_value_inr=5_000_000,
        closing_at=NOW + timedelta(days=60),
    )
    s = score_tender(t, profile, NOW)
    assert s.total == pytest.approx(1.0) and s.tier == "high"


def test_non_preferred_category_and_state(profile):
    t = make_tender(
        category="Logistics", state="Kerala", msme_preferred=False, estimated_value_inr=200_000_000
    )
    s = score_tender(t, profile, NOW)
    by = {f.name: f for f in s.factors}
    assert by["category"].value == 0 and by["state"].value == 0 and by["msme"].value == 0
    assert by["value"].value == 0.2


@pytest.mark.parametrize(
    ("score", "tier"), [(0.70, "high"), (0.6999, "medium"), (0.40, "medium"), (0.39, "low")]
)
def test_tier_boundaries(profile, score, tier):
    assert tier_for(score, profile) == tier


def test_profile_validates_weights():
    p = load_profile()
    with pytest.raises(ValueError, match=r"sum to 1.0"):
        dataclasses.replace(p, weights={**p.weights, "state": 0.5})
    with pytest.raises(ValueError, match="cover exactly"):
        dataclasses.replace(p, weights={"category": 1.0})
    with pytest.raises(ValueError, match="medium_tier"):
        dataclasses.replace(p, high_tier=0.3, medium_tier=0.5)
