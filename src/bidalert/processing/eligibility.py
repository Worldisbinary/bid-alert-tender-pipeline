"""Hard eligibility rules. A tender failing any rule is not worth scoring.

Rules whose input is missing from the listing are not failed; they are recorded
as "unverified" so a person knows to check the tender document. Treating a
missing turnover requirement as zero would silently pass tenders the company
cannot bid on.
"""

from __future__ import annotations

from datetime import datetime

from bidalert.models import EligibilityResult, Tender
from bidalert.processing.deadlines import days_remaining
from bidalert.profile import CompanyProfile


def check_eligibility(
    tender: Tender,
    profile: CompanyProfile,
    now: datetime,
    excluded_keywords: tuple[str, ...] = (),
) -> EligibilityResult:
    reasons: list[str] = []
    unverified: list[str] = []

    left = days_remaining(tender.closing_at, now)
    if left <= 0:
        reasons.append("closed")
    elif left < profile.min_days_to_deadline:
        reasons.append(f"only {left:.1f} days to deadline (< {profile.min_days_to_deadline})")

    if excluded_keywords:
        reasons.append("excluded keyword: " + ", ".join(repr(k) for k in excluded_keywords))

    if tender.turnover_requirement_cr is None:
        unverified.append("turnover requirement")
    elif tender.turnover_requirement_cr > profile.annual_turnover_cr:
        reasons.append(
            f"turnover requirement {tender.turnover_requirement_cr} Cr "
            f"> company {profile.annual_turnover_cr} Cr"
        )

    if tender.experience_years_required is None:
        unverified.append("experience requirement")
    elif tender.experience_years_required > profile.years_in_business:
        reasons.append(
            f"needs {tender.experience_years_required} years experience "
            f"> company {profile.years_in_business}"
        )

    if tender.estimated_value_inr is None:
        unverified.append("tender value")
    elif not profile.min_value_inr <= tender.estimated_value_inr <= profile.max_value_inr:
        reasons.append(
            f"value INR {tender.estimated_value_inr:,} outside "
            f"{profile.min_value_inr:,}-{profile.max_value_inr:,}"
        )

    return EligibilityResult(
        eligible=not reasons, reasons=tuple(reasons), unverified=tuple(unverified)
    )
