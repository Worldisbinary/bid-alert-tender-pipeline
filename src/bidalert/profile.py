"""Company profile: what the screening is *for*.

Loaded from TOML so a non-programmer can change eligibility limits, preferred
categories, watchlist keywords and scoring weights without touching code.
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

FACTORS = ("category", "state", "msme", "value", "deadline")


@dataclass(frozen=True)
class CompanyProfile:
    name: str
    annual_turnover_cr: float
    years_in_business: int
    is_msme: bool
    preferred_categories: tuple[str, ...]
    preferred_states: tuple[str, ...]
    min_value_inr: int
    max_value_inr: int
    min_days_to_deadline: int
    bid_preparation_days: int
    watchlist: dict[str, tuple[str, ...]]  # category -> keywords
    exclude_keywords: tuple[str, ...]
    weights: dict[str, float]
    high_tier: float = 0.70
    medium_tier: float = 0.40
    reminder_days: tuple[int, ...] = (7, 3, 1)
    top_k: int = 15
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        missing = set(FACTORS) - set(self.weights)
        unknown = set(self.weights) - set(FACTORS)
        if missing or unknown:
            raise ValueError(
                f"weights must cover exactly {FACTORS}; missing={missing} unknown={unknown}"
            )
        if any(w < 0 for w in self.weights.values()):
            raise ValueError("weights must be non-negative")
        if not math.isclose(sum(self.weights.values()), 1.0, abs_tol=1e-6):
            raise ValueError(f"weights must sum to 1.0, got {sum(self.weights.values()):.4f}")
        if not 0 <= self.medium_tier <= self.high_tier <= 1:
            raise ValueError("need 0 <= medium_tier <= high_tier <= 1")
        if self.min_value_inr > self.max_value_inr:
            raise ValueError("min_value_inr must be <= max_value_inr")


def _from_dict(d: dict[str, Any]) -> CompanyProfile:
    c = d["company"]
    e = d["eligibility"]
    s = d["scoring"]
    a = d.get("alerts", {})
    return CompanyProfile(
        name=c["name"],
        annual_turnover_cr=float(c["annual_turnover_cr"]),
        years_in_business=int(c["years_in_business"]),
        is_msme=bool(c["is_msme"]),
        preferred_categories=tuple(c["preferred_categories"]),
        preferred_states=tuple(c["preferred_states"]),
        min_value_inr=int(e["min_value_inr"]),
        max_value_inr=int(e["max_value_inr"]),
        min_days_to_deadline=int(e["min_days_to_deadline"]),
        bid_preparation_days=int(e.get("bid_preparation_days", 10)),
        exclude_keywords=tuple(e.get("exclude_keywords", ())),
        watchlist={cat: tuple(words) for cat, words in d["watchlist"].items()},
        weights={k: float(v) for k, v in s["weights"].items()},
        high_tier=float(s.get("high_tier", 0.70)),
        medium_tier=float(s.get("medium_tier", 0.40)),
        reminder_days=tuple(int(x) for x in a.get("reminder_days", (7, 3, 1))),
        top_k=int(a.get("top_k", 15)),
    )


def load_profile(path: Path | None = None) -> CompanyProfile:
    if path is None:
        text = resources.files("bidalert").joinpath("default_profile.toml").read_text("utf-8")
    else:
        text = path.read_text("utf-8")
    return _from_dict(tomllib.loads(text))
