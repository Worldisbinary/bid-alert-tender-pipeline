import json
import logging

import httpx
import pytest

from bidalert import cli
from bidalert.logging_setup import JsonFormatter, configure_logging
from bidalert.sources.http import PoliteHttpClient
from tests.conftest import FIXTURES


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("BIDALERT_DATABASE_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    monkeypatch.setenv("BIDALERT_OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("BIDALERT_SITE_DIR", str(tmp_path / "site"))
    monkeypatch.setenv("BIDALERT_FIXTURES_DIR", str(FIXTURES / "cppp"))
    monkeypatch.setenv("BIDALERT_HTTP_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("BIDALERT_LOG_FORMAT", "text")
    return tmp_path


def fake_client(status: int):
    def factory(settings):
        def handler(req):
            if req.url.path.endswith("robots.txt"):
                return httpx.Response(404)
            if status != 200:
                return httpx.Response(status)
            return httpx.Response(200, text=(FIXTURES / "cppp" / "page1.html").read_text("utf-8"))

        return PoliteHttpClient(
            "ua",
            min_interval_s=0,
            max_retries=0,
            transport=httpx.MockTransport(handler),
            sleep=lambda s: None,
        )

    return factory


def test_init_db_and_run_synthetic(env, capsys):
    assert cli.main(["init-db"]) == 0
    assert cli.main(["run", "--sources", "synthetic"]) == 0
    stats = json.loads(capsys.readouterr().out)
    assert stats["fetched"] == 180 and stats["live"] is False
    assert (env / "site" / "index.html").exists() and (
        env / "out" / "screened_tenders.xlsx"
    ).exists()


def test_require_live_fails_on_demo_data(env):
    assert cli.main(["run", "--sources", "fixtures", "--no-db", "--require-live"]) == 3


def test_live_run_with_mocked_portal(env, monkeypatch, capsys):
    monkeypatch.setattr(cli, "build_http_client", fake_client(200))
    assert (
        cli.main(
            ["run", "--sources", "cppp", "--require-live", "--now", "2026-10-18T17:00:00+00:00"]
        )
        == 0
    )
    stats = json.loads(capsys.readouterr().out)
    assert stats["live"] is True and stats["fetched"] == 10


def test_fallback_when_portal_is_down(env, monkeypatch, capsys):
    monkeypatch.setattr(cli, "build_http_client", fake_client(503))
    assert cli.main(["run", "--sources", "cppp", "--fallback", "synthetic"]) == 0
    stats = json.loads(capsys.readouterr().out)
    assert stats["live"] is False and stats["fetched"] == 180
    html = (env / "site" / "index.html").read_text("utf-8")
    assert (
        "Demo data, not real tenders" in html and "failed" in html
    )  # failed live attempt is shown


def test_record_fixtures(env, monkeypatch):
    monkeypatch.setattr(cli, "build_http_client", fake_client(200))
    out = env / "rec"
    assert cli.main(["record-fixtures", "--pages", "1", "--out", str(out)]) == 0
    meta = json.loads((out / "meta.json").read_text("utf-8"))
    assert meta["pages"][0]["rows"] == 10


def test_worker_stops_gracefully(env, monkeypatch):
    calls = []

    def fake_run_once(settings, args):
        calls.append(1)

    monkeypatch.setattr(cli, "run_once", fake_run_once)
    monkeypatch.setattr(cli.threading.Event, "wait", lambda self, t: self.set())
    assert cli.main(["worker", "--sources", "synthetic", "--interval", "1"]) == 0
    assert calls == [1]


def test_json_logs_include_extra_fields(capsys):
    record = logging.makeLogRecord(
        {"name": "t", "levelname": "INFO", "msg": "hello %s", "args": ("x",)}
    )
    record.source = "cppp"
    payload = json.loads(JsonFormatter().format(record))
    assert payload["msg"] == "hello x" and payload["source"] == "cppp" and "ts" in payload
    configure_logging("INFO", "json")
    logging.getLogger("bidalert.test").info("event", extra={"n": 3})
    line = capsys.readouterr().err.strip().splitlines()[-1]
    assert json.loads(line)["n"] == 3
