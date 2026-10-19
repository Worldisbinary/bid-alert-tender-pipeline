"""Weighted, explainable fit score in [0, 1].

score = sum_i weight_i * value_i, with each value_i in [0, 1]. Every factor
returns its value *and* a human-readable reason, so the dashboard, Excel sheet
and API can show exactly why a tender ranked where it did.

Missing inputs get a neutral 0.5 and `known=False` instead of 0 or 1: a CPPP
listing that does not publish the tender value should neither be rewarded nor
punished for it. The weights are the hand-set business rules from the 2024
internship model; they have not been fitted to win/loss outcomes.

Relevance gate (a deliberate change from the internship model): a tender whose
category factor is 0 - nothing the company sells - is capped at the "low" tier
whatever its other factors say. Without it, the first live run ranked "Licensing
of Kiosk spaces at Metro Stations" as medium priority for a chemicals supplier,
purely on neutral unknowns plus a comfortable deadline.
"""

from __future__ import annotations

from datetime import datetime

from bidalert.models import FactorContribution, ScoreResult, Tender
from bidalert.processing.deadlines import days_remaining
from bidalert.profile import CompanyProfile

NEUTRAL = 0.5


def _value_band(value_inr: int) -> tuple[float, str]:
    lakh = value_inr / 100_000
    if 2_500_000 <= value_inr <= 10_000_000:
        return 1.0, f"INR {lakh:.1f} L is in the 25 L-1 Cr sweet spot"
    if 1_000_000 <= value_inr < 2_500_000:
        return 0.7, f"INR {lakh:.1f} L is in the 10-25 L band"
    if 10_000_000 < value_inr <= 25_000_000:
        return 0.5, f"INR {lakh:.1f} L is in the 1-2.5 Cr band"
    return 0.2, f"INR {lakh:.1f} L is outside the preferred bands"


def tier_for(score: float, profile: CompanyProfile) -> str:
    if score >= profile.high_tier:
        return "high"
    if score >= profile.medium_tier:
        return "medium"
    return "low"


def score_tender(
    tender: Tender,
    profile: CompanyProfile,
    now: datetime,
    inferred_category: str | None = None,
    matched_keywords: tuple[str, ...] = (),
) -> ScoreResult:
    w = profile.weights
    factors: list[FactorContribution] = []

    def add(name: str, value: float, known: bool, reason: str) -> None:
        value = min(1.0, max(0.0, value))
        factors.append(
            FactorContribution(name, w[name], value, round(w[name] * value, 4), known, reason)
        )

    # Category: structured category if the portal gives one, else watchlist inference.
    category = tender.category or inferred_category
    if category is None:
        add("category", 0.0, True, "no watchlist keyword in title")
    elif category in profile.preferred_categories:
        how = "portal category" if tender.category else f"keywords {', '.join(matched_keywords)}"
        add("category", 1.0, True, f"{category} is preferred ({how})")
    else:
        add("category", 0.0, True, f"{category} is not a preferred category")

    if tender.state is None:
        add("state", NEUTRAL, False, "state not in listing (neutral)")
    elif tender.state in profile.preferred_states:
        add("state", 1.0, True, f"{tender.state} is a preferred state")
    else:
        add("state", 0.0, True, f"{tender.state} is not a preferred state")

    if tender.msme_preferred is None:
        add("msme", NEUTRAL, False, "MSME preference not in listing (neutral)")
    elif tender.msme_preferred and profile.is_msme:
        add("msme", 1.0, True, "tender prefers MSMEs and the company is one")
    else:
        add("msme", 0.0, True, "no MSME advantage")

    if tender.estimated_value_inr is None:
        add("value", NEUTRAL, False, "value not in listing (neutral)")
    else:
        v, why = _value_band(tender.estimated_value_inr)
        add("value", v, True, why)

    left = days_remaining(tender.closing_at, now)
    floor = profile.min_days_to_deadline
    add(
        "deadline",
        (left - floor) / 30.0,
        True,
        f"{left:.1f} days left (full marks at {floor + 30})",
    )

    total = round(sum(f.contribution for f in factors), 4)
    tier = tier_for(total, profile)
    if factors[0].value == 0.0 and tier != "low":
        tier = "low"
        cat = factors[0]
        factors[0] = FactorContribution(
            cat.name, cat.weight, cat.value, cat.contribution, cat.known,
            cat.reason + "; relevance gate caps the tier at low",
        )  # fmt: skip
    return ScoreResult(total=total, tier=tier, factors=tuple(factors))
