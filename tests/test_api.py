import pytest
from fastapi.testclient import TestClient

from bidalert.api.app import create_app
from bidalert.config import Settings
from bidalert.pipeline import run_pipeline
from bidalert.sources.synthetic import SyntheticAdapter
from bidalert.storage.db import make_engine, session_factory


@pytest.fixture
def client(sessions, profile, sqlite_url):
    # Synthetic tenders relative to the real clock so they are "open" for the API.
    run_pipeline([SyntheticAdapter()], profile, sessions=sessions)
    app = create_app(Settings(database_url=sqlite_url), sessions=sessions)
    with TestClient(app) as c:
        yield c


def test_health_and_ready(client):
    assert client.get("/health").json()["status"] == "ok"
    r = client.get("/ready")
    assert r.status_code == 200 and r.json()["status"] == "ready"
    assert r.headers["x-request-id"]


def test_ready_fails_without_schema(tmp_path):
    url = f"sqlite:///{tmp_path / 'empty.db'}"
    engine = make_engine(url)
    app = create_app(Settings(database_url=url), sessions=session_factory(engine))
    with TestClient(app) as c:
        assert c.get("/ready").status_code == 503
        assert c.get("/metrics").status_code == 200  # metrics still render without a schema
    engine.dispose()


def test_list_paginates_and_filters(client):
    page1 = client.get("/tenders", params={"page_size": 5}).json()
    assert page1["total"] == 160 and len(page1["items"]) == 5 and page1["pages"] == 32
    scores = [t["score"] for t in page1["items"] if t["score"] is not None]
    assert scores == sorted(scores, reverse=True)

    high = client.get("/tenders", params={"tier": "high", "eligible": True}).json()
    assert all(t["tier"] == "high" and t["eligible"] for t in high["items"])

    search = client.get("/tenders", params={"q": "reagents"}).json()
    assert search["total"] > 0 and all("reagents" in t["title"].lower() for t in search["items"])

    with_dupes = client.get("/tenders", params={"include_duplicates": True}).json()
    assert with_dupes["total"] == 180

    soon = client.get("/tenders", params={"closing_within_days": 3, "sort": "closing"}).json()
    closes = [t["closing_at"] for t in soon["items"]]
    assert closes == sorted(closes)


def test_validation_errors(client):
    assert client.get("/tenders", params={"page_size": 1000}).status_code == 422
    assert client.get("/tenders", params={"tier": "urgent"}).status_code == 422


def test_detail_and_score_breakdown(client):
    item = client.get("/tenders", params={"eligible": True, "page_size": 1}).json()["items"][0]
    detail = client.get(f"/tenders/{item['id']}").json()
    assert detail["title"] == item["title"] and isinstance(detail["unverified"], list)
    score = client.get(f"/tenders/{item['id']}/score").json()
    assert len(score["factors"]) == 5
    assert sum(f["contribution"] for f in score["factors"]) == pytest.approx(
        score["score"], abs=1e-3
    )
    assert client.get("/tenders/999999").status_code == 404
    assert client.get("/tenders/999999/score").status_code == 404


def test_runs_stats_and_metrics(client):
    runs = client.get("/runs").json()
    assert runs[0]["status"] == "success" and runs[0]["stats"]["fetched"] == 180
    stats = client.get("/stats").json()
    assert stats["open"] == 160 and stats["last_run"]["id"] == runs[0]["id"]
    client.get("/tenders/123456")
    body = client.get("/metrics").text
    assert 'http_requests_total{method="GET",route="/tenders/{tender_id}",status="404"}' in body
    assert 'bidalert_tenders{view="open"} 160.0' in body
    assert "bidalert_last_run_timestamp_seconds" in body
