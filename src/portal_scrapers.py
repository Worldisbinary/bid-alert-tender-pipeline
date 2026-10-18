"""
portal_scrapers.py
Modular scraper stubs for each of the 5 procurement portals.
Each scraper normalises raw portal data into a standard schema
before being passed to the screening pipeline.

Note: Actual scraping logic omitted (portal-specific, rate-limited).
      This module documents the interface and normalisation approach.
"""

import pandas as pd
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── Standard schema all scrapers output ──────────────────────────────────────
STANDARD_COLUMNS = [
    "tender_id", "portal", "title", "department", "category",
    "state", "estimated_value_inr", "published_date", "deadline",
    "days_to_deadline", "turnover_requirement_cr", "experience_years_required",
    "msme_preferred", "multi_bid_allowed", "portal_url"
]


@dataclass
class TenderRecord:
    tender_id: str
    portal: str
    title: str
    department: str
    category: str
    state: str
    estimated_value_inr: float
    published_date: str
    deadline: str
    days_to_deadline: int
    turnover_requirement_cr: float
    experience_years_required: int
    msme_preferred: bool
    multi_bid_allowed: bool
    portal_url: str


# ── Base scraper class ────────────────────────────────────────────────────────
class PortalScraper(ABC):
    def __init__(self, portal_name: str):
        self.portal_name = portal_name

    @abstractmethod
    def fetch_listings(self, category: Optional[str] = None) -> list[dict]:
        """Fetch raw tender listings from the portal."""
        pass

    @abstractmethod
    def parse_tender(self, raw: dict) -> TenderRecord:
        """Parse a single raw listing into a TenderRecord."""
        pass

    def scrape(self, category: Optional[str] = None) -> pd.DataFrame:
        logger.info(f"[{self.portal_name}] Starting scrape...")
        raw_listings = self.fetch_listings(category)
        records = []
        for raw in raw_listings:
            try:
                record = self.parse_tender(raw)
                records.append(vars(record))
            except Exception as e:
                logger.warning(f"[{self.portal_name}] Failed to parse tender: {e}")
        df = pd.DataFrame(records, columns=STANDARD_COLUMNS)
        logger.info(f"[{self.portal_name}] Scraped {len(df)} tenders.")
        return df


# ── Portal-specific scrapers ──────────────────────────────────────────────────

class GeMScraper(PortalScraper):
    """
    Government e-Marketplace (GeM) — gem.gov.in
    Primary source for government procurement tenders.
    Uses GeM's Bid/RA search with category filters.
    """
    BASE_URL = "https://bidplus.gem.gov.in/all-bids"

    def __init__(self):
        super().__init__("GeM")

    def fetch_listings(self, category=None):
        # Actual implementation: HTTP GET with session, parse JSON response
        # Rate limit: 1 req/sec, max 100 results/page
        raise NotImplementedError("GeM scraper requires valid session cookie.")

    def parse_tender(self, raw):
        # GeM-specific field mapping
        return TenderRecord(
            tender_id=raw.get("bid_number"),
            portal="GeM",
            title=raw.get("bid_title"),
            department=raw.get("buyer_name"),
            category=raw.get("item_category"),
            state=raw.get("consignee_state"),
            estimated_value_inr=float(raw.get("estimated_bid_value", 0)),
            published_date=raw.get("start_date"),
            deadline=raw.get("end_date"),
            days_to_deadline=raw.get("days_remaining", 0),
            turnover_requirement_cr=float(raw.get("turnover_criteria", 0)),
            experience_years_required=int(raw.get("experience_criteria", 0)),
            msme_preferred=raw.get("msme_exemption", False),
            multi_bid_allowed=raw.get("multiple_bid", True),
            portal_url=f"{self.BASE_URL}?bid_number={raw.get('bid_number')}"
        )


class CPPPScraper(PortalScraper):
    """
    Central Public Procurement Portal (CPPP) — eprocure.gov.in
    For Central Government ministry tenders.
    """
    BASE_URL = "https://eprocure.gov.in/eprocure/app"

    def __init__(self):
        super().__init__("CPPP")

    def fetch_listings(self, category=None):
        # CPPP search is CAPTCHA-protected, so listings are exported manually
        # and loaded through parse_tender(); no automated fetching here.
        raise NotImplementedError("CPPP listings are CAPTCHA-protected; load a manual export instead.")

    def parse_tender(self, raw):
        return TenderRecord(
            tender_id=raw.get("NIT_No"),
            portal="CPPP",
            title=raw.get("Work_Description"),
            department=raw.get("Organisation_Chain"),
            category=self._infer_category(raw.get("Work_Description", "")),
            state=raw.get("State"),
            estimated_value_inr=float(raw.get("Estimated_Cost", 0)),
            published_date=raw.get("Published_Date"),
            deadline=raw.get("Bid_Submission_End_Date"),
            days_to_deadline=raw.get("days_left", 0),
            turnover_requirement_cr=float(raw.get("Turnover_Criteria", 0)),
            experience_years_required=int(raw.get("Experience_Criteria", 0)),
            msme_preferred=False,  # CPPP doesn't expose MSME flag in listing
            multi_bid_allowed=True,
            portal_url=f"{self.BASE_URL}?tenderid={raw.get('NIT_No')}"
        )

    def _infer_category(self, description: str) -> str:
        """Infer category from tender description using keyword matching."""
        keywords = {
            "Chemical Supplies": ["chemical", "reagent", "solvent"],
            "Laboratory Equipment": ["lab", "instrument", "equipment", "analyser"],
            "Pharmaceuticals": ["medicine", "drug", "pharma"],
            "Industrial Machinery": ["machine", "plant", "machinery"],
        }
        desc_lower = description.lower()
        for category, words in keywords.items():
            if any(w in desc_lower for w in words):
                return category
        return "General"


class GovTendersScraper(PortalScraper):
    def __init__(self):
        super().__init__("GovTenders")

    def fetch_listings(self, category=None):
        raise NotImplementedError

    def parse_tender(self, raw):
        raise NotImplementedError


class TenderTigerScraper(PortalScraper):
    def __init__(self):
        super().__init__("TenderTiger")

    def fetch_listings(self, category=None):
        raise NotImplementedError

    def parse_tender(self, raw):
        raise NotImplementedError


class eProcureScraper(PortalScraper):
    def __init__(self):
        super().__init__("eProcure")

    def fetch_listings(self, category=None):
        raise NotImplementedError

    def parse_tender(self, raw):
        raise NotImplementedError


# ── Aggregator — consolidates all portals ────────────────────────────────────
class TenderAggregator:
    """
    Runs all portal scrapers and consolidates results into one DataFrame.
    Handles deduplication across portals (same tender listed on multiple portals).
    """

    SCRAPERS = [
        GeMScraper,
        CPPPScraper,
        GovTendersScraper,
        TenderTigerScraper,
        eProcureScraper,
    ]

    def run(self, category: Optional[str] = None) -> pd.DataFrame:
        frames = []
        for ScraperClass in self.SCRAPERS:
            scraper = ScraperClass()
            try:
                df = scraper.scrape(category)
                frames.append(df)
            except NotImplementedError:
                logger.warning(f"[{scraper.portal_name}] Scraper not implemented — skipping.")
            except Exception as e:
                logger.error(f"[{scraper.portal_name}] Error: {e}")

        if not frames:
            return pd.DataFrame(columns=STANDARD_COLUMNS)

        combined = pd.concat(frames, ignore_index=True)

        # Deduplicate: same title + department + deadline = same tender
        combined = combined.drop_duplicates(
            subset=["title", "department", "deadline"],
            keep="first"
        )
        logger.info(f"[Aggregator] Total unique tenders: {len(combined)}")
        return combined
