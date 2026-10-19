"""Domain objects shared by every stage.

`Tender` is the normalised record every source adapter must produce. Fields a
portal does not publish stay `None`; downstream stages treat `None` as "unknown",
never as zero (an unknown turnover requirement is not a zero requirement).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Tender:
    source: str  # adapter that produced it: "cppp", "fixtures", "synthetic"
    source_tender_id: str  # stable id within that source; (source, id) is the natural key
    portal: str  # portal the listing appeared on, e.g. "CPPP"
    title: str
    organisation: str
    closing_at: datetime  # timezone-aware
    published_at: datetime | None = None
    opening_at: datetime | None = None
    reference_no: str | None = None
    url: str | None = None
    category: str | None = None
    state: str | None = None
    estimated_value_inr: int | None = None
    turnover_requirement_cr: float | None = None
    experience_years_required: int | None = None
    msme_preferred: bool | None = None
    is_synthetic: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.source}:{self.source_tender_id}"


@dataclass(frozen=True)
class FactorContribution:
    """One line of the score explanation: contribution = weight * value."""

    name: str
    weight: float
    value: float  # 0..1
    contribution: float
    known: bool  # False when the input was missing and a neutral value was used
    reason: str


@dataclass(frozen=True)
class ScoreResult:
    total: float
    tier: str  # high | medium | low
    factors: tuple[FactorContribution, ...]


@dataclass(frozen=True)
class EligibilityResult:
    eligible: bool
    reasons: tuple[str, ...] = ()  # why it failed (hard rules)
    unverified: tuple[str, ...] = ()  # rules that could not be checked from the listing


@dataclass
class ScreenedTender:
    tender: Tender
    fingerprint: str
    matched_keywords: tuple[str, ...] = ()
    inferred_category: str | None = None
    duplicate_of: str | None = None  # key of the canonical tender, if this is a duplicate
    duplicate_similarity: float | None = None  # Jaccard similarity to it (1.0 = exact fingerprint)
    eligibility: EligibilityResult | None = None
    score: ScoreResult | None = None

    @property
    def is_duplicate(self) -> bool:
        return self.duplicate_of is not None

    @property
    def category(self) -> str | None:
        return self.tender.category or self.inferred_category
