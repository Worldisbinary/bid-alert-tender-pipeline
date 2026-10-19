"""Command line entry point: `bidalert <command>`.

bidalert init-db                       apply database migrations
bidalert run [--sources cppp]          one pipeline run (fetch -> ... -> reports -> alerts)
bidalert worker --interval 21600       run forever on an interval (docker-compose worker)
bidalert serve --port 8000             start the API
bidalert record-fixtures --pages 2     save real CPPP pages for offline tests
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType

from bidalert import __version__
from bidalert.alerts import build_notifiers
from bidalert.config import Settings
from bidalert.logging_setup import configure_logging
from bidalert.pipeline import RunResult, run_pipeline, write_reports
from bidalert.profile import load_profile
from bidalert.sources import KNOWN_SOURCES, build_http_client, build_sources

log = logging.getLogger("bidalert.cli")


def _parse_now(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def cmd_init_db(settings: Settings, _args: argparse.Namespace) -> int:
    from bidalert.storage.db import migrate

    migrate(settings.database_url)
    log.info("database migrated", extra={"url": _redact(settings.database_url)})
    return 0


def _redact(url: str) -> str:
    from sqlalchemy.engine.url import make_url

    return make_url(url).render_as_string(hide_password=True)


def run_once(settings: Settings, args: argparse.Namespace) -> RunResult:
    from bidalert.storage.db import make_engine, migrate, session_factory

    profile = load_profile(settings.profile_path)
    names = tuple(args.sources.split(",")) if args.sources else settings.sources
    now = _parse_now(getattr(args, "now", None))
    client = build_http_client(settings) if "cppp" in names else None
    sessions = None
    engine = None
    try:
        if not getattr(args, "no_db", False):
            migrate(settings.database_url)
            engine = make_engine(settings.database_url)
            sessions = session_factory(engine)
        sources = build_sources(names, settings, client=client, now=now)
        result = run_pipeline(
            sources, profile, sessions=sessions, notifiers=build_notifiers(settings), now=now
        )
        fallback = getattr(args, "fallback", None)
        if fallback and not result.live:
            log.warning("no live data; re-running on fallback source", extra={"fallback": fallback})
            attempted = [r for r in result.sources if r.is_live]
            # No notifiers: demo data must never trigger real alerts.
            result = run_pipeline(
                build_sources(tuple(fallback.split(",")), settings, now=now),
                profile,
                sessions=sessions,
                now=now,
            )
            result.sources[:0] = attempted  # keep the failed live attempt visible on the dashboard
        write_reports(result, profile, output_dir=settings.output_dir, site_dir=settings.site_dir)
        return result
    finally:
        if client is not None:
            client.close()
        if engine is not None:
            engine.dispose()


def cmd_run(settings: Settings, args: argparse.Namespace) -> int:
    result = run_once(settings, args)
    stats = result.stats()
    print(json.dumps({k: v for k, v in stats.items() if k != "sources"}, indent=2, default=str))
    if result.status == "failed":
        return 2
    if args.require_live and not result.live:
        log.error("--require-live set but no live source returned data")
        return 3
    return 0


def cmd_worker(settings: Settings, args: argparse.Namespace) -> int:
    """Run the pipeline every `interval` seconds until SIGTERM/SIGINT (graceful stop)."""
    stop = threading.Event()

    def _stop(signum: int, _frame: FrameType | None) -> None:
        log.info("shutdown requested", extra={"signal": signum})
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    while not stop.is_set():
        try:
            run_once(settings, args)
        except Exception:
            # A failed run is recorded in pipeline_runs; the worker keeps its schedule.
            log.exception("pipeline run failed")
        stop.wait(args.interval)
    return 0


def cmd_serve(settings: Settings, args: argparse.Namespace) -> int:
    import uvicorn

    from bidalert.api.app import create_app

    uvicorn.run(
        create_app(settings),
        host=args.host,
        port=args.port,
        log_config=None,
        proxy_headers=True,
        # Behind nginx at /api/, so /docs links to /api/openapi.json.
        root_path=args.root_path,
    )
    return 0


def cmd_record_fixtures(settings: Settings, args: argparse.Namespace) -> int:
    from bs4 import BeautifulSoup

    from bidalert.sources.cppp import ROBOTS_URL, page_url, parse_listing, parse_total

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    pages = []
    with build_http_client(settings) as client:
        for n in range(1, args.pages + 1):
            url = page_url(n)
            html = client.get_text(url, robots_url=ROBOTS_URL)
            soup = BeautifulSoup(html, "html.parser")
            table = soup.find("table", id="table")
            if table is None:
                raise SystemExit(f"no listing table on page {n}")
            total = parse_total(html)
            # Keep only the listing table (and the total), not the whole portal page.
            snippet = (
                f"<!-- recorded from {url} -->\n<div>Total Tenders : {total or ''}</div>\n{table}\n"
            )
            name = f"page{n}.html"
            (out / name).write_text(snippet, encoding="utf-8")
            pages.append({"file": name, "url": url, "rows": len(parse_listing(snippet))})
    meta = {
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": "https://eprocure.gov.in/cppp/latestactivetendersnew/cpppdata",
        "note": "Public CPPP listing (Source: Central Public Procurement Portal). "
        "Used as an offline test fixture.",
        "pages": pages,
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(meta, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bidalert", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--version", action="version", version=f"bidalert {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="apply database migrations").set_defaults(func=cmd_init_db)

    def add_run_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "--sources",
            help=f"comma-separated, from {', '.join(KNOWN_SOURCES)} (default: BIDALERT_SOURCES)",
        )
        sp.add_argument(
            "--fallback", help="source(s) to use if no live source returns data, e.g. synthetic"
        )
        sp.add_argument(
            "--now",
            help="ISO timestamp to screen against (default: now, or the fixtures' capture time)",
        )
        sp.add_argument("--no-db", action="store_true", help="skip the database (reports only)")

    run = sub.add_parser("run", help="run the pipeline once")
    add_run_args(run)
    run.add_argument(
        "--require-live", action="store_true", help="exit 3 if no live source returned data"
    )
    run.set_defaults(func=cmd_run)

    worker = sub.add_parser("worker", help="run the pipeline on an interval")
    add_run_args(worker)
    worker.add_argument(
        "--interval", type=int, default=6 * 3600, help="seconds between runs (default 6 h)"
    )
    worker.set_defaults(func=cmd_worker)

    serve = sub.add_parser("serve", help="start the HTTP API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--root-path", default="", help="URL prefix when behind a proxy, e.g. /api")
    serve.set_defaults(func=cmd_serve)

    rec = sub.add_parser("record-fixtures", help="save real CPPP listing pages as offline fixtures")
    rec.add_argument("--pages", type=int, default=2)
    rec.add_argument("--out", default="tests/fixtures/cppp")
    rec.set_defaults(func=cmd_record_fixtures)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings.from_env()
    configure_logging(settings.log_level, settings.log_format)
    return int(args.func(settings, args))


if __name__ == "__main__":
    sys.exit(main())
