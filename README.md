# Bid Alert: tender screening pipeline

A Python pipeline that takes government procurement tenders from several portals, drops
the ones a company is not eligible for, scores the rest on how well they fit, and writes
a tiered Excel shortlist plus a static HTML dashboard.

It reconstructs the screening workflow I worked on as an operations intern at Bid Alert
(tender tracking and procurement intelligence, Guntur) in May to July 2024. The
company's tender data, portal sessions and client profiles are not part of this
repository, so it runs on **synthetic tenders** produced by `src/generate_data.py`.

Live demo dashboard (synthetic data): https://worldisbinary.github.io/bid-alert-tender-pipeline/

## Why

Tenders relevant to a small supplier are spread across GeM, CPPP and state or aggregator
portals. Each listing has hard eligibility rules (turnover, years in business, value
band, deadline), and most tenders fail at least one of them. Encoding those rules and a
simple fit score turns a daily manual sweep into reviewing a short, ranked list.

## Pipeline

```
portal listings ──> normalise to one schema ──> eligibility filters ──> weighted score ──> Excel tiers + HTML dashboard
 (portal_scrapers.py)                          (screening_pipeline.py)                    (screening_pipeline.py, dashboard.py)
```

1. **Normalise.** Each portal has its own field names. `portal_scrapers.py` defines one
   `TenderRecord` schema and a per-portal `parse_tender()` mapping. The network-fetch
   methods are interface stubs: GeM needs a logged-in session and CPPP is behind a
   CAPTCHA, so automated fetching is out of scope here.
2. **Filter.** A tender is dropped if its turnover or experience requirement is above
   the company profile, its value is outside the configured band, or fewer than 7 days
   remain to the deadline.
3. **Score.** Each remaining tender gets a weighted score from 0 to 1:

   | Dimension | Weight | Rule |
   |---|---|---|
   | Category match | 30 % | 1 if the category is in the company's preferred list |
   | Value band | 20 % | 1.0 for ₹25 L to ₹1 Cr, 0.7 for ₹10 to 25 L, 0.5 for ₹1 to 2.5 Cr, 0.2 otherwise |
   | State preference | 20 % | 1 if the state is one the company operates in |
   | MSME advantage | 15 % | 1 if the tender prefers MSMEs and the company is one |
   | Deadline comfort | 15 % | scales linearly from 0 at 7 days to 1 at 37 days |

4. **Report.** Tenders scoring 0.70 or more are high priority, 0.40 to 0.70 medium, and
   below 0.40 low. The Excel workbook has a summary sheet, one sheet per tier and a full
   list with the per-dimension scores, so every ranking can be explained.

The weights and thresholds are hand-set business rules, not fitted to outcome data.

## How to run

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python src/generate_data.py        # writes data/raw_tenders.csv (160 synthetic tenders, seeded)
python src/screening_pipeline.py   # writes output/screened_tenders.xlsx
python src/dashboard.py            # writes output/index.html
```

Run the commands from the repository root. To publish the dashboard, copy
`output/index.html` to `docs/index.html` (GitHub Pages serves `docs/`).

Change `COMPANY_PROFILE` in `src/screening_pipeline.py` to screen for a different company.

## Project structure

```
src/
  generate_data.py       synthetic tenders across 5 portals (seeded)
  portal_scrapers.py     common schema, per-portal field mapping, aggregator; fetching is stubbed
  screening_pipeline.py  eligibility filters, scoring, Excel export
  dashboard.py           static HTML dashboard from the Excel output
docs/index.html          published dashboard (GitHub Pages)
output/index.html        latest locally generated dashboard
```

## Limitations

- Data is synthetic. Nothing here measures performance on real tenders.
- Portal fetching is not implemented. Only the normalisation layer is.
- Scoring weights are judgement calls and have not been validated against win or loss outcomes.
- No automated tests yet.

## Licence

MIT, see [LICENSE](LICENSE).
