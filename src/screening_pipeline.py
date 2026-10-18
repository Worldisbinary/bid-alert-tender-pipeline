"""
screening_pipeline.py
Core tender screening pipeline — reads raw tenders from 5 portals,
applies eligibility filters and a custom scoring model, outputs ranked shortlist.
"""

import os
import pandas as pd
import numpy as np
from datetime import datetime


# ── Company profile (configurable) ───────────────────────────────────────────
COMPANY_PROFILE = {
    "annual_turnover_cr": 2.5,       # Company's annual turnover in Crores
    "years_in_business": 4,
    "is_msme": True,
    "preferred_categories": ["Chemical Supplies", "Laboratory Equipment",
                              "Raw Materials", "Pharmaceuticals"],
    "preferred_states": ["Rajasthan", "Gujarat", "Maharashtra", "Andhra Pradesh"],
    "min_value_inr": 500_000,         # Ignore tenders below ₹5L
    "max_value_inr": 50_000_000,      # Ignore tenders above ₹5Cr
    "min_days_to_deadline": 7,        # Need at least 7 days to prepare bid
}


# ── Step 1: Load & consolidate from 5 portals ────────────────────────────────
def load_tenders(filepath: str) -> pd.DataFrame:
    df = pd.read_csv(filepath)
    df["published_date"] = pd.to_datetime(df["published_date"])
    df["deadline"] = pd.to_datetime(df["deadline"])
    print(f"[Load] {len(df)} tenders loaded from {df['portal'].nunique()} portals.")
    return df


# ── Step 2: Hard eligibility filters ─────────────────────────────────────────
def apply_eligibility_filters(df: pd.DataFrame, profile: dict) -> pd.DataFrame:
    original = len(df)

    # Turnover requirement
    df = df[df["turnover_requirement_cr"] <= profile["annual_turnover_cr"]]

    # Experience requirement
    df = df[df["experience_years_required"] <= profile["years_in_business"]]

    # Value range
    df = df[df["estimated_value_inr"].between(
        profile["min_value_inr"], profile["max_value_inr"]
    )]

    # Deadline filter — enough time to prepare
    df = df[df["days_to_deadline"] >= profile["min_days_to_deadline"]]

    print(f"[Filter] {original} → {len(df)} tenders after eligibility filters "
          f"({original - len(df)} removed).")
    return df.copy()


# ── Step 3: Scoring model ─────────────────────────────────────────────────────
def score_tenders(df: pd.DataFrame, profile: dict) -> pd.DataFrame:
    """
    Weighted scoring across 5 dimensions:
      - Category match     (30%)
      - State preference   (20%)
      - MSME advantage     (15%)
      - Value sweet spot   (20%)
      - Deadline comfort   (15%)
    """
    scores = pd.DataFrame(index=df.index)

    # Category match score (0 or 1)
    scores["category_score"] = df["category"].isin(
        profile["preferred_categories"]
    ).astype(float)

    # State preference score (0 or 1)
    scores["state_score"] = df["state"].isin(
        profile["preferred_states"]
    ).astype(float)

    # MSME advantage — higher score if MSME preferred and company is MSME
    scores["msme_score"] = (
        df["msme_preferred"] & profile["is_msme"]
    ).astype(float)

    # Value sweet spot — peak score at ₹25L–₹1Cr, tapering at extremes
    def value_score(val):
        if 2_500_000 <= val <= 10_000_000:
            return 1.0
        elif 1_000_000 <= val < 2_500_000:
            return 0.7
        elif 10_000_000 < val <= 25_000_000:
            return 0.5
        else:
            return 0.2

    scores["value_score"] = df["estimated_value_inr"].apply(value_score)

    # Deadline comfort — more days = less rush = higher score
    scores["deadline_score"] = np.clip(
        (df["days_to_deadline"] - 7) / 30, 0, 1
    )

    # Weighted composite
    weights = {
        "category_score": 0.30,
        "state_score":    0.20,
        "msme_score":     0.15,
        "value_score":    0.20,
        "deadline_score": 0.15,
    }

    df = df.copy()
    df["composite_score"] = sum(
        scores[col] * w for col, w in weights.items()
    ).round(3)

    # Score breakdown for transparency
    for col in scores.columns:
        df[f"_{col}"] = scores[col]

    return df.sort_values("composite_score", ascending=False)


# ── Step 4: Export to Excel ───────────────────────────────────────────────────
def export_to_excel(df: pd.DataFrame, output_path: str):
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    # Split into tiers
    high   = df[df["composite_score"] >= 0.70].copy()
    medium = df[(df["composite_score"] >= 0.40) & (df["composite_score"] < 0.70)].copy()
    low    = df[df["composite_score"] < 0.40].copy()

    display_cols = [
        "tender_id", "portal", "title", "department", "category", "state",
        "estimated_value_inr", "deadline", "days_to_deadline",
        "msme_preferred", "composite_score", "portal_url"
    ]

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        # Summary sheet
        summary = pd.DataFrame({
            "Tier": ["High Priority", "Medium Priority", "Low Priority", "Total Screened"],
            "Count": [len(high), len(medium), len(low), len(df)],
            "Avg Score": [
                high["composite_score"].mean().round(3),
                medium["composite_score"].mean().round(3),
                low["composite_score"].mean().round(3),
                df["composite_score"].mean().round(3),
            ]
        })
        summary.to_excel(writer, sheet_name="Summary", index=False)

        # Tiered sheets
        high[display_cols].to_excel(writer, sheet_name="High Priority", index=False)
        medium[display_cols].to_excel(writer, sheet_name="Medium Priority", index=False)
        low[display_cols].to_excel(writer, sheet_name="Low Priority", index=False)

        # Full scored list
        df[display_cols + ["_category_score", "_state_score", "_msme_score",
                           "_value_score", "_deadline_score"]].to_excel(
            writer, sheet_name="All Scored", index=False
        )

    print(f"[Export] Saved to {output_path}")
    print(f"         High: {len(high)}  |  Medium: {len(medium)}  |  Low: {len(low)}")


# ── Main pipeline ─────────────────────────────────────────────────────────────
def run_pipeline(input_path: str, output_path: str):
    print("=" * 55)
    print("  Bid Alert — Tender Screening Pipeline")
    print("=" * 55)

    df = load_tenders(input_path)
    df = apply_eligibility_filters(df, COMPANY_PROFILE)
    df = score_tenders(df, COMPANY_PROFILE)
    export_to_excel(df, output_path)

    print("\nTop 5 Tenders:")
    top5_cols = ["tender_id", "title", "composite_score", "days_to_deadline"]
    print(df[top5_cols].head(5).to_string(index=False))
    print("=" * 55)
    return df


if __name__ == "__main__":
    run_pipeline("data/raw_tenders.csv", "output/screened_tenders.xlsx")
