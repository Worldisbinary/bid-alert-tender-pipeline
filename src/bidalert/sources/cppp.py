"""Central Public Procurement Portal (CPPP) - "Latest Active Tenders" (central).

What we fetch and why it is allowed:

* URL: https://eprocure.gov.in/cppp/latestactivetendersnew/cpppdata - a public
  listing served without login. Pages 2..N use the portal's own pagination links
  (`?url=<base64 of ...?page=N>`), exactly what a browser follows.
* The keyword *search* form on that page is CAPTCHA-protected. We never submit it
  and never attempt to solve or bypass a CAPTCHA.
* robots.txt: https://eprocure.gov.in/cppp/robots.txt does not disallow this path
  (checked on every run by PoliteHttpClient). The host-root robots.txt is a 404.
* Terms: the portal's copyright policy asks that reproduced material be accurate,
  not misleading, and that the source be prominently acknowledged; the dashboard
  shows titles unmodified with "Source: CPPP, eprocure.gov.in" on every view.
  See docs/DATA_SOURCES.md.

What the listing contains: published / closing / opening dates, title, reference
number, tender ID and organisation. It does NOT contain value, location,
turnover or experience criteria; those stay None and are reported as
"unverified" by the eligibility stage.

Detail-page links on the listing embed a timestamp and stop working shortly
after, so we link to the listing page and show the stable tender ID instead.
"""

from __future__ import annotations

import base64
import logging
import re
from collections.abc import Iterator
from datetime import datetime
from urllib.parse import quote

from bs4 import BeautifulSoup
from bs4.element import Tag

from bidalert.models import Tender
from bidalert.processing.deadlines import IST
from bidalert.processing.text import clean_display
from bidalert.sources.base import SourceAdapter, SourceError
from bidalert.sources.http import FetchError, PoliteHttpClient

log = logging.getLogger(__name__)

BASE_URL = "https://eprocure.gov.in/cppp/latestactivetendersnew/cpppdata"
ROBOTS_URL = "https://eprocure.gov.in/cppp/robots.txt"
DATE_FORMAT = "%d-%b-%Y %I:%M %p"  # 18-Oct-2026 10:01 PM, IST
# The header is "Title/Ref.No./Tender Id": the last "/" segment is the tender ID.
# Most look like 2026_BPCL_27071, but some buyers (e.g. railways) use plain numbers
# such as 107218225, so accept any single token.
TENDER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_&.\-]{2,63}$")


def page_url(page: int) -> str:
    """URL of listing page `page` (1-based), in the form the portal's own pager uses."""
    if page < 1:
        raise ValueError("page is 1-based")
    if page == 1:
        return BASE_URL
    inner = f"{BASE_URL}?page={page}".encode()
    return f"{BASE_URL}?url={quote(base64.b64encode(inner).decode(), safe='')}"


def parse_date(text: str) -> datetime | None:
    text = clean_display(text)
    if not text or text == "--":
        return None
    try:
        return datetime.strptime(text, DATE_FORMAT).replace(tzinfo=IST)
    except ValueError:
        return None


def _split_title_cell(cell: Tag) -> tuple[str, str | None, str | None]:
    """Title cell looks like: <a>TITLE</a>/REF/NO/WITH/SLASHES/TENDER_ID."""
    link = cell.find("a")
    if isinstance(link, Tag):
        title = clean_display(link.get_text(" "))
        rest = clean_display(
            "".join(s.get_text(" ") if isinstance(s, Tag) else str(s) for s in link.next_siblings)
        )
    else:
        title, rest = clean_display(cell.get_text(" ")), ""
    rest = rest.strip().lstrip("/").strip()
    if not rest:
        return title, None, None
    head, _, last = rest.rpartition("/")
    if TENDER_ID_RE.match(last.strip()):
        return title, (head.strip() or None), last.strip()
    return title, rest or None, None


def parse_total(html: str) -> int | None:
    m = re.search(r"Total Tenders\s*:\s*([\d,]+)", html)
    return int(m.group(1).replace(",", "")) if m else None


def parse_listing(html: str, page_link: str = BASE_URL, source: str = "cppp") -> list[Tender]:
    """Parse one listing page. Rows that do not look like tenders are skipped and logged."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="table")
    if not isinstance(table, Tag):
        raise SourceError("CPPP listing table not found; page layout may have changed")
    tenders: list[Tender] = []
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 6:
            continue  # header row or layout row
        published = parse_date(cells[1].get_text())
        closing = parse_date(cells[2].get_text())
        opening = parse_date(cells[3].get_text())
        title, reference, tender_id = _split_title_cell(cells[4])
        organisation = clean_display(cells[5].get_text(" "))
        if not (title and closing and tender_id):
            log.warning(
                "skipping unparseable CPPP row",
                extra={"row": clean_display(row.get_text(" "))[:200]},
            )
            continue
        corrigendum = clean_display(cells[6].get_text(" ")) if len(cells) > 6 else ""
        tenders.append(
            Tender(
                source=source,
                source_tender_id=tender_id,
                portal="CPPP",
                title=title,
                organisation=organisation,
                reference_no=reference,
                published_at=published,
                closing_at=closing,
                opening_at=opening,
                url=page_link,
                raw={"corrigendum": corrigendum if corrigendum not in ("", "--") else None},
            )
        )
    return tenders


class CpppAdapter(SourceAdapter):
    name = "cppp"
    is_live = True
    description = (
        "Central Public Procurement Portal (eprocure.gov.in), Latest Active Tenders - central"
    )

    def __init__(self, client: PoliteHttpClient, max_pages: int = 5) -> None:
        self.client = client
        self.max_pages = max_pages
        self.total_listed: int | None = None

    def fetch(self) -> Iterator[Tender]:
        seen: set[str] = set()
        for page in range(1, self.max_pages + 1):
            url = page_url(page)
            try:
                html = self.client.get_text(url, robots_url=ROBOTS_URL)
            except FetchError as exc:
                if page == 1:
                    raise SourceError(str(exc)) from exc
                log.warning(
                    "stopping CPPP pagination early", extra={"page": page, "error": str(exc)}
                )
                return
            if page == 1:
                self.total_listed = parse_total(html)
            batch = parse_listing(html, page_link=url, source=self.name)
            if not batch:
                return
            for t in batch:
                # New tenders are published while we paginate, which shifts rows onto
                # the next page; skip ones we already yielded.
                if t.source_tender_id not in seen:
                    seen.add(t.source_tender_id)
                    yield t
