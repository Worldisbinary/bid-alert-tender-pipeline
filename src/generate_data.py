"""
generate_data.py
Generates synthetic tender data simulating 5 procurement portals.
Used for demonstration purposes — mirrors real pipeline structure.
"""

import pandas as pd
import numpy as np
import random
from datetime import datetime, timedelta

random.seed(42)
np.random.seed(42)

PORTALS = ["GeM", "CPPP", "GovTenders", "TenderTiger", "eProcure"]

CATEGORIES = [
    "Chemical Supplies", "Laboratory Equipment", "Pharmaceuticals",
    "Industrial Machinery", "Safety Equipment", "Raw Materials",
    "Packaging", "IT Equipment", "Consulting Services", "Logistics"
]

STATES = [
    "Andhra Pradesh", "Gujarat", "Maharashtra", "Rajasthan",
    "Tamil Nadu", "Telangana", "Uttar Pradesh", "Karnataka"
]

DEPARTMENTS = [
    "Ministry of Chemicals", "DRDO", "CSIR", "Dept of Pharmaceuticals",
    "Ministry of Defence", "NTPC", "ONGC", "BHEL", "Railways", "NHM"
]


def generate_tenders(n=160):
    records = []
    base_date = datetime(2024, 5, 1)

    for i in range(n):
        portal = random.choice(PORTALS)
        category = random.choice(CATEGORIES)
        state = random.choice(STATES)
        dept = random.choice(DEPARTMENTS)

        value = round(np.random.lognormal(mean=13.5, sigma=1.2))  # INR
        deadline_days = random.randint(5, 45)
        deadline = base_date + timedelta(days=random.randint(0, 60) + deadline_days)
        published = deadline - timedelta(days=deadline_days)

        # Simulate eligibility flags
        turnover_req = round(random.uniform(0.5, 5.0), 1)  # Cr
        experience_years = random.randint(1, 7)
        msme_preferred = random.choice([True, False, False])  # 33% MSME
        multi_bid_allowed = random.choice([True, True, False])

        records.append({
            "tender_id": f"{portal[:3].upper()}-{2024}-{str(i+1).zfill(4)}",
            "portal": portal,
            "title": f"{category} Supply - {dept} ({state})",
            "department": dept,
            "category": category,
            "state": state,
            "estimated_value_inr": value,
            "published_date": published.strftime("%Y-%m-%d"),
            "deadline": deadline.strftime("%Y-%m-%d"),
            "days_to_deadline": deadline_days,
            "turnover_requirement_cr": turnover_req,
            "experience_years_required": experience_years,
            "msme_preferred": msme_preferred,
            "multi_bid_allowed": multi_bid_allowed,
            "portal_url": f"https://www.{portal.lower()}.gov.in/tender/{i+10001}",
        })

    return pd.DataFrame(records)


if __name__ == "__main__":
    df = generate_tenders(160)
    df.to_csv("data/raw_tenders.csv", index=False)
    print(f"Generated {len(df)} tender records across {df['portal'].nunique()} portals.")
    print(df["portal"].value_counts())
