import base64
from datetime import UTC, datetime
from urllib.parse import unquote

import httpx
import pytest

from bidalert.config import Settings
from bidalert.sources import build_sources
from bidalert.sources.base import SourceError
from bidalert.sources.cppp import (
    BASE_URL,
    CpppAdapter,
    page_url,
    parse_date,
    parse_listing,
    parse_total,
)
from bidalert.sources.fixtures import FixtureAdapter
from bidalert.sources.http import PoliteHttpClient
from bidalert.sources.synthetic import SyntheticAdapter
from tests.conftest import FIXTURES

CPPP = FIXTURES / "cppp"


def test_page_url_matches_portal_pagination_link():
    # Exact link copied from the portal's own pager for page 2.
    expected = (
        "https://eprocure.gov.in/cppp/latestactivetendersnew/cpppdata?url="
        "aHR0cHM6Ly9lcHJvY3VyZS5nb3YuaW4vY3BwcC9sYXRlc3RhY3RpdmV0ZW5kZXJzbmV3L2NwcHBkYXRhP3BhZ2U9Mg%3D%3D"
    )
    assert page_url(2) == expected
    assert page_url(1) == BASE_URL
    inner = base64.b64decode(unquote(page_url(37).split("url=")[1])).decode()
    assert inner.endswith("?page=37")
    with pytest.raises(ValueError):
        page_url(0)


def test_parse_date_is_ist():
    dt = parse_date("18-Oct-2026 10:01 PM")
    assert dt is not None and dt.astimezone(UTC) == datetime(2026, 10, 18, 16, 31, tzinfo=UTC)
    assert parse_date("--") is None and parse_date("garbage") is None


def test_parse_recorded_cppp_page():
    html = (CPPP / "page1.html").read_text("utf-8")
    tenders = parse_listing(html)
    assert len(tenders) == 10
    first = tenders[0]
    assert first.source_tender_id == "2026_BPCL_27071"
    assert first.reference_no == "1000467266"
    assert first.title == "OEM CONTRACT FOR REFURBISHMENT OF MBC GTM-1307"
    assert first.organisation == "Bharat Petroleum Corporation Limited"
    assert first.closing_at > first.published_at  # type: ignore[operator]
    assert parse_total(html) and parse_total(html) > 1000
    # unknowns stay None, never zero
    assert first.estimated_value_inr is None and first.state is None


def test_numeric_railway_tender_ids_parse():
    tenders = parse_listing((CPPP / "page2.html").read_text("utf-8"))
    assert len(tenders) == 10
    assert any(
        t.source_tender_id.isdigit() and t.organisation == "Ministry of Railways" for t in tenders
    )


def test_layout_change_raises():
    with pytest.raises(SourceError):
        parse_listing("<html><body>maintenance</body></html>")


def test_fixture_adapter_replays_pages_and_capture_time():
    a = FixtureAdapter(CPPP)
    tenders = list(a.fetch())
    assert len(tenders) == 20 and len({t.source_tender_id for t in tenders}) == 20
    assert a.reference_time().tzinfo is not None
    with pytest.raises(SourceError):
        FixtureAdapter(CPPP / "missing")


def test_cppp_adapter_paginates_and_dedups_shifted_rows():
    pages = {
        BASE_URL: (CPPP / "page1.html").read_text("utf-8"),
        page_url(2): (CPPP / "page1.html").read_text("utf-8"),  # rows shifted: same rows again
        page_url(3): (CPPP / "page2.html").read_text("utf-8"),
    }
    seen_ua = set()

    def handler(req: httpx.Request) -> httpx.Response:
        seen_ua.add(req.headers["user-agent"])
        if req.url.path.endswith("robots.txt"):
            return httpx.Response(200, text="User-agent: *\nDisallow: /cppp/admin/\n")
        return httpx.Response(200, text=pages[str(req.url)])

    client = PoliteHttpClient(
        "ua-test/1", min_interval_s=0, transport=httpx.MockTransport(handler), sleep=lambda s: None
    )
    adapter = CpppAdapter(client, max_pages=3)
    tenders = list(adapter.fetch())
    assert len(tenders) == 20
    assert adapter.total_listed and adapter.total_listed > 1000
    assert seen_ua == {"ua-test/1"}


def test_cppp_adapter_first_page_failure_is_source_error():
    def handler(req):
        if req.url.path.endswith("robots.txt"):
            return httpx.Response(404)
        return httpx.Response(503)

    client = PoliteHttpClient(
        "ua",
        min_interval_s=0,
        max_retries=1,
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
    )
    with pytest.raises(SourceError):
        list(CpppAdapter(client, max_pages=2).fetch())


def test_synthetic_is_labelled_and_reproducible():
    now = datetime(2026, 10, 1, tzinfo=UTC)
    a = list(SyntheticAdapter(now=now).fetch())
    b = list(SyntheticAdapter(now=now).fetch())
    assert [t.title for t in a] == [t.title for t in b]
    assert all(t.is_synthetic and t.url is None for t in a)
    assert all(t.closing_at > now for t in a)


def test_registry(tmp_path):
    settings = Settings(fixtures_dir=CPPP)
    names = [s.name for s in build_sources(("synthetic", "fixtures"), settings)]
    assert names == ["synthetic", "fixtures"]
    with pytest.raises(ValueError, match="HTTP client"):
        build_sources(("cppp",), settings)
    with pytest.raises(ValueError, match="unknown source"):
        build_sources(("nope",), settings)
