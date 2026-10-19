"""FastAPI service: query screened tenders, explain scores, expose health and metrics.

    uvicorn bidalert.api.app:app        # reads settings from env
    bidalert serve --port 8000          # same, via the CLI

Endpoints: /health (liveness), /ready (DB reachable), /metrics (Prometheus),
/tenders (filter, search, paginate), /tenders/{id}, /tenders/{id}/score,
/runs, /stats. Interactive docs at /docs.
"""

from __future__ import annotations

import logging
import math
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from bidalert import __version__
from bidalert.api.schemas import (
    FactorOut,
    Health,
    Page,
    RunOut,
    ScoreBreakdown,
    Stats,
    TenderDetail,
    TenderOut,
)
from bidalert.config import Settings
from bidalert.logging_setup import configure_logging
from bidalert.storage.db import make_engine, session_factory
from bidalert.storage.repository import RunRepository, TenderRepository

log = logging.getLogger("bidalert.api")


class Metrics:
    """Per-app registry so tests can build many apps without duplicate-metric errors."""

    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.requests = Counter(
            "http_requests_total",
            "HTTP requests",
            ["method", "route", "status"],
            registry=self.registry,
        )
        self.latency = Histogram(
            "http_request_duration_seconds",
            "Request latency",
            ["route"],
            registry=self.registry,
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5),
        )
        self.tenders = Gauge(
            "bidalert_tenders", "Tenders in the database by view", ["view"], registry=self.registry
        )
        self.last_run = Gauge(
            "bidalert_last_run_timestamp_seconds",
            "Start time of the last successful pipeline run",
            registry=self.registry,
        )
        self.last_run_ok = Gauge(
            "bidalert_last_run_success",
            "1 if the most recent run succeeded or was partial",
            registry=self.registry,
        )


def get_session(request: Request) -> Iterator[Session]:
    """One session per request, always closed (dependency with cleanup)."""
    session: Session = request.app.state.sessions()
    try:
        yield session
    finally:
        session.close()


# Module level: with `from __future__ import annotations`, FastAPI resolves
# annotations by name in module globals, so this alias cannot live inside create_app.
SessionDep = Annotated[Session, Depends(get_session)]


def create_app(
    settings: Settings | None = None, sessions: sessionmaker[Session] | None = None
) -> FastAPI:
    settings = settings or Settings.from_env()
    if sessions is None:
        sessions = session_factory(make_engine(settings.database_url))
    metrics = Metrics()

    app = FastAPI(
        title="Bid Alert tender API",
        version=__version__,
        description="Screened government tenders with explainable scores. "
        "See https://github.com/Worldisbinary/bid-alert-tender-pipeline",
    )
    app.state.sessions = sessions
    app.state.metrics = metrics

    @app.middleware("http")
    async def observe(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
        finally:
            elapsed = time.perf_counter() - start
            route = request.scope.get("route")
            # Label by route template (/tenders/{tender_id}), not raw path, to bound cardinality.
            label = getattr(route, "path", "unmatched")
            metrics.requests.labels(request.method, label, str(status)).inc()
            metrics.latency.labels(label).observe(elapsed)
            log.info(
                "request",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": status,
                    "duration_ms": round(elapsed * 1000, 1),
                },
            )
        response.headers["x-request-id"] = request_id
        return response

    @app.get("/health", response_model=Health, tags=["ops"])
    def health() -> Health:
        """Liveness: the process is up. Does not touch the database."""
        return Health(status="ok", version=__version__)

    @app.get("/ready", response_model=Health, tags=["ops"], responses={503: {"model": Health}})
    def ready(session: SessionDep) -> Response:
        """Readiness: the database answers and the schema is migrated."""
        try:
            session.execute(text("SELECT 1 FROM tenders LIMIT 1"))
        except Exception as exc:
            body = Health(status="unavailable", version=__version__, detail=type(exc).__name__)
            return JSONResponse(body.model_dump(), status_code=503)
        return JSONResponse(Health(status="ready", version=__version__).model_dump())

    @app.get("/metrics", tags=["ops"], include_in_schema=False)
    def prometheus(session: SessionDep) -> Response:
        now = datetime.now(UTC)
        try:
            for view, n in TenderRepository(session).counts(now).items():
                metrics.tenders.labels(view).set(n)
            last = RunRepository(session).recent(1)
            if last:
                metrics.last_run_ok.set(1 if last[0].status in ("success", "partial") else 0)
            ok = RunRepository(session).last_successful()
            if ok:
                metrics.last_run.set(ok.started_at.timestamp())
        except Exception:  # metrics must still render if the DB is down
            log.warning("could not refresh DB gauges", exc_info=True)
        return Response(generate_latest(metrics.registry), media_type=CONTENT_TYPE_LATEST)

    @app.get("/tenders", response_model=Page[TenderOut], tags=["tenders"])
    def list_tenders(
        session: SessionDep,
        q: Annotated[
            str | None, Query(max_length=100, description="Search title and organisation")
        ] = None,
        tier: Literal["high", "medium", "low"] | None = None,
        source: str | None = None,
        eligible: bool | None = None,
        min_score: Annotated[float | None, Query(ge=0, le=1)] = None,
        closing_within_days: Annotated[int | None, Query(ge=0, le=365)] = None,
        include_closed: bool = False,
        include_duplicates: bool = False,
        sort: Literal["score", "closing", "newest"] = "score",
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> Page[TenderOut]:
        rows, total = TenderRepository(session).query(
            q=q,
            tier=tier,
            source=source,
            eligible=eligible,
            min_score=min_score,
            closing_within_days=closing_within_days,
            include_closed=include_closed,
            include_duplicates=include_duplicates,
            sort=sort,
            offset=(page - 1) * page_size,
            limit=page_size,
        )
        return Page[TenderOut](
            items=[TenderOut.model_validate(r) for r in rows],
            total=total,
            page=page,
            page_size=page_size,
            pages=math.ceil(total / page_size) if total else 0,
        )

    @app.get("/tenders/{tender_id}", response_model=TenderDetail, tags=["tenders"])
    def get_tender(tender_id: int, session: SessionDep) -> TenderDetail:
        row = TenderRepository(session).get(tender_id)
        if row is None:
            raise HTTPException(404, "tender not found")
        return TenderDetail.model_validate(row)

    @app.get("/tenders/{tender_id}/score", response_model=ScoreBreakdown, tags=["tenders"])
    def get_score(tender_id: int, session: SessionDep) -> ScoreBreakdown:
        row = TenderRepository(session).get(tender_id)
        if row is None:
            raise HTTPException(404, "tender not found")
        return ScoreBreakdown(
            tender_id=row.id,
            score=row.score,
            tier=row.tier,
            eligible=row.eligible,
            ineligible_reasons=row.ineligible_reasons,
            unverified=row.unverified,
            factors=[FactorOut(**f) for f in row.score_breakdown],
            explanation="score = sum(weight * value) over the factors; inputs missing from the "
            "listing use a neutral value of 0.5 and are marked known=false",
        )

    @app.get("/runs", response_model=list[RunOut], tags=["pipeline"])
    def runs(session: SessionDep, limit: Annotated[int, Query(ge=1, le=100)] = 20) -> list[RunOut]:
        return [RunOut.model_validate(r) for r in RunRepository(session).recent(limit)]

    @app.get("/stats", response_model=Stats, tags=["pipeline"])
    def stats(session: SessionDep) -> Stats:
        counts = TenderRepository(session).counts(datetime.now(UTC))
        last = RunRepository(session).recent(1)
        return Stats(**counts, last_run=RunOut.model_validate(last[0]) if last else None)

    return app


def _app_from_env() -> FastAPI:
    settings = Settings.from_env()
    configure_logging(settings.log_level, settings.log_format)
    return create_app(settings)


def __getattr__(name: str) -> FastAPI:
    # `uvicorn bidalert.api.app:app` builds the app lazily so importing this module
    # (e.g. in tests) does not connect to a database.
    if name == "app":
        return _app_from_env()
    raise AttributeError(name)
