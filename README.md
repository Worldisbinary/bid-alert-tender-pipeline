# Bid Alert — Tender Screening Pipeline

Python-Excel pipeline for automated government procurement tender discovery and scoring across 5 portals. Built during internship at Bid Alert (Tender Tracking & Procurement Intelligence), Guntur.

**Impact:** Reduced manual tender discovery effort by ~70%. Contributed to securing 2 government procurement contracts from a pool of 150+ tenders evaluated.

---

## What It Does

Government procurement tenders are scattered across 5+ portals (GeM, CPPP, GovTenders, TenderTiger, eProcure). Manually checking each portal, reading eligibility criteria, and shortlisting relevant tenders takes hours per day.

This pipeline:
1. **Consolidates** tender listings from 5 portals into a single dataset
2. **Filters** tenders that fail hard eligibility criteria (turnover, experience, value range, deadline)
3. **Scores** remaining tenders on 5 weighted dimensions
4. **Exports** a tiered Excel report (High / Medium / Low priority) for decision-making

---

## Pipeline Architecture

```
5 Portals (GeM, CPPP, GovTenders, TenderTiger, eProcure)
        │
        ▼
  TenderAggregator          ← portal_scrapers.py
  (normalise + deduplicate)
        │
        ▼
  Eligibility Filter        ← screening_pipeline.py
  (turnover, experience,
   value range, deadline)
        │
        ▼
  Scoring Model             ← screening_pipeline.py
  (5 weighted dimensions)
        │
        ▼
  Excel Output              ← output/screened_tenders.xlsx
  (Summary + 3 priority tiers)
```

---

## Scoring Model

| Dimension | Weight | Logic |
|---|---|---|
| Category match | 30% | 1.0 if tender category is in company's preferred list |
| Value sweet spot | 20% | Peak score at ₹25L–₹1Cr; tapers at extremes |
| State preference | 20% | 1.0 if state is in company's operational states |
| MSME advantage | 15% | 1.0 if tender prefers MSME and company qualifies |
| Deadline comfort | 15% | Scaled 0→1 over 7–37 days remaining |

Composite score ranges from 0.0 to 1.0. Tenders ≥ 0.70 are flagged High Priority.

---

## Output

The pipeline exports a multi-sheet Excel file:

| Sheet | Contents |
|---|---|
| Summary | Tier counts and average scores |
| High Priority | Score ≥ 0.70 — immediate action |
| Medium Priority | 0.40 ≤ score < 0.70 — worth reviewing |
| Low Priority | Score < 0.40 — deprioritised |
| All Scored | Full list with score breakdowns |

---

## Usage

```bash
pip install -r requirements.txt

# Generate synthetic demo data
python src/generate_data.py

# Run the screening pipeline
python src/screening_pipeline.py
```

Output saved to `output/screened_tenders.xlsx`.

---

## Project Structure

```
bid-alert/
├── src/
│   ├── generate_data.py       # Synthetic tender data generator (demo)
│   ├── portal_scrapers.py     # Modular scrapers for each portal
│   └── screening_pipeline.py  # Eligibility filters + scoring + Excel export
├── data/
│   └── raw_tenders.csv        # Generated demo data
├── output/
│   └── screened_tenders.xlsx  # Scored output
├── notebooks/
│   └── scoring_analysis.ipynb # Score distribution and validation
├── requirements.txt
└── README.md
```

---

## Tech Stack

- Python 3.10+
- pandas, numpy — data processing
- openpyxl — Excel export with multiple sheets
- requests, BeautifulSoup — portal scraping (per-portal)

---

## Configuration

Edit `COMPANY_PROFILE` in `screening_pipeline.py` to match any company's eligibility parameters:

```python
COMPANY_PROFILE = {
    "annual_turnover_cr": 2.5,
    "years_in_business": 4,
    "is_msme": True,
    "preferred_categories": ["Chemical Supplies", "Laboratory Equipment"],
    "preferred_states": ["Rajasthan", "Gujarat"],
    "min_value_inr": 500_000,
    "max_value_inr": 50_000_000,
    "min_days_to_deadline": 7,
}
```
