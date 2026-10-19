"""Pipeline orchestration.

ingest -> normalise -> dedup -> eligibility -> score -> store -> report -> alert.

`screen()` is pure (no I/O) and does the analytical work; `run_pipeline()` wraps it
with sources, storage, reports and alerts, and records the run in `pipeline_runs`.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from bidalert import __version__
from bidalert.alerts import Alert, AlertDispatcher, DispatchResult, Notifier
from bidalert.models import ScreenedTender, Tender
from bidalert.processing.aho_corasick import KeywordClassifier
from bidalert.processing.deadlines import (
    closing_bucket,
    peak_concurrency,
    preparation_window,
    reminders_due,
)
from bidalert.processing.dedup import Deduplicator, fingerprint, token_set
from bidalert.processing.eligibility import check_eligibility
from bidalert.processing.scoring import score_tender
from bidalert.processing.text import clean_display
from bidalert.processing.topk import top_k
from bidalert.profile import CompanyProfile
from bidalert.sources.base import SourceAdapter, SourceError
from bidalert.storage.repository import (
    AlertRepository,
    RunRepository,
    TenderRepository,
    UpsertStats,
)

log = logging.getLogger(__name__)


@dataclass
class SourceReport:
    name: str
    description: str
    is_live: bool
    ok: bool
    fetched: int = 0
    error: str | None = None
    total_listed: int | None = None
    seconds: float = 0.0


@dataclass
class RunResult:
    started_at: datetime
    now: datetime  # the clock tenders were screened against
    sources: list[SourceReport]
    screened: list[ScreenedTender]
    profile_name: str
    top_k: int
    finished_at: datetime | None = None
    upsert: UpsertStats | None = None
    alerts: DispatchResult | None = None
    alerts_built: int = 0
    run_id: int | None = None
    peak_concurrent_bids: int = 0
    peak_at: datetime | None = None
    report_paths: dict[str, str] = field(default_factory=dict)

    # -- derived views ----------------------------------------------------------
    @property
    def unique(self) -> list[ScreenedTender]:
        return [s for s in self.screened if not s.is_duplicate]

    @property
    def duplicates(self) -> list[ScreenedTender]:
        return [s for s in self.screened if s.is_duplicate]

    @property
    def eligible(self) -> list[ScreenedTender]:
        return [s for s in self.unique if s.eligibility and s.eligibility.eligible]

    @property
    def ineligible(self) -> list[ScreenedTender]:
        return [s for s in self.unique if s.eligibility and not s.eligibility.eligible]

    def tier(self, name: str) -> list[ScreenedTender]:
        return [s for s in self.eligible if s.score and s.score.tier == name]

    @property
    def shortlist(self) -> list[ScreenedTender]:
        return top_k(self.eligible, self.top_k, key=lambda s: s.score.total if s.score else 0.0)

    @property
    def live(self) -> bool:
        """True when at least one live (real, network) source succeeded."""
        return any(r.is_live and r.ok and r.fetched for r in self.sources)

    @property
    def status(self) -> str:
        oks = [r.ok for r in self.sources]
        if oks and all(oks):
            return "success"
        return "partial" if any(oks) else "failed"

    def reason_counts(self) -> Counter[str]:
        c: Counter[str] = Counter()
        for s in self.ineligible:
            for r in s.eligibility.reasons if s.eligibility else ():
                c[_reason_family(r)] += 1
        return c

    def keyword_counts(self) -> Counter[str]:
        return Counter(k for s in self.unique for k in s.matched_keywords)

    def closing_buckets(self) -> Counter[str]:
        return Counter(closing_bucket(s.tender.closing_at, self.now) for s in self.eligible)

    def stats(self) -> dict[str, Any]:
        return {
            "version": __version__,
            "fetched": len(self.screened),
            "unique": len(self.unique),
            "duplicates": len(self.duplicates),
            "eligible": len(self.eligible),
            "ineligible": len(self.ineligible),
            "high": len(self.tier("high")),
            "medium": len(self.tier("medium")),
            "low": len(self.tier("low")),
            "inserted": self.upsert.inserted if self.upsert else None,
            "updated": self.upsert.updated if self.upsert else None,
            "alerts_built": self.alerts_built,
            "alerts_sent": dict(self.alerts.sent) if self.alerts else {},
            "peak_concurrent_bids": self.peak_concurrent_bids,
            "live": self.live,
            "sources": [r.__dict__ for r in self.sources],
            "ineligible_reasons": dict(self.reason_counts()),
        }


def _reason_family(reason: str) -> str:
    for prefix, family in (
        ("closed", "closed"),
        ("only ", "too close to deadline"),
        ("excluded keyword", "excluded keyword"),
        ("turnover", "turnover too high"),
        ("needs ", "experience too high"),
        ("value ", "value outside band"),
    ):
        if reason.startswith(prefix):
            return family
    return reason


def normalise_tender(t: Tender) -> Tender:
    """Light, lossless clean-up shared by all sources (whitespace only; wording kept)."""
    t.title = clean_display(t.title)
    t.organisation = clean_display(t.organisation)
    if t.reference_no:
        t.reference_no = clean_display(t.reference_no)
    return t


def screen(
    tenders: Iterable[Tender],
    profile: CompanyProfile,
    now: datetime,
    dedup: Deduplicator | None = None,
    classifier: KeywordClassifier | None = None,
) -> list[ScreenedTender]:
    """Pure screening: dedup, keyword match, eligibility, score.

    O(n * L) overall for n tenders of title length L (Aho-Corasick and MinHash are
    both linear in the title; LSH lookups are O(bands) per tender).
    """
    dedup = dedup or Deduplicator()
    classifier = classifier or KeywordClassifier(profile.watchlist, profile.exclude_keywords)
    out: list[ScreenedTender] = []
    for t in tenders:
        t = normalise_tender(t)
        fp = fingerprint(t.title, t.organisation, t.closing_at.date())
        toks = token_set(t.title, t.organisation)
        keywords, inferred, excluded = classifier.classify(t.title)
        s = ScreenedTender(t, fp, matched_keywords=keywords, inferred_category=inferred)
        canonical, sim = dedup.check(t.key, t.portal, fp, toks, t.closing_at.date())
        if canonical is not None:
            s.duplicate_of, s.duplicate_similarity = canonical, sim
        s.eligibility = check_eligibility(t, profile, now, excluded_keywords=excluded)
        if s.eligibility.eligible:
            s.score = score_tender(
                t, profile, now, inferred_category=inferred, matched_keywords=keywords
            )
        out.append(s)
    return out


def _fetch_all(sources: Sequence[SourceAdapter]) -> tuple[list[Tender], list[SourceReport]]:
    tenders: list[Tender] = []
    reports: list[SourceReport] = []
    for src in sources:
        t0 = time.perf_counter()
        report = SourceReport(src.name, src.description, src.is_live, ok=False)
        try:
            batch = list(src.fetch())
        except (SourceError, OSError, ValueError) as exc:
            report.error = f"{type(exc).__name__}: {exc}"
            log.error("source failed", extra={"source": src.name, "error": report.error})
        else:
            report.ok, report.fetched = True, len(batch)
            tenders.extend(batch)
            log.info("source fetched", extra={"source": src.name, "tenders": len(batch)})
        report.total_listed = getattr(src, "total_listed", None)
        report.seconds = round(time.perf_counter() - t0, 2)
        reports.append(report)
    return tenders, reports


def _screening_clock(sources: Sequence[SourceAdapter], override: datetime | None) -> datetime:
    if override is not None:
        return override
    refs = [s.reference_time() for s in sources]
    if refs and all(r is not None for r in refs):
        return min(r for r in refs if r is not None)
    return datetime.now(UTC)


def build_alerts(
    result: RunResult,
    ids: dict[str, int],
    last_run: datetime | None,
    reminder_days: Sequence[int],
) -> list[Alert]:
    alerts: list[Alert] = []
    for s in result.eligible:
        db_id = ids.get(s.tender.key)
        if db_id is None or s.score is None:
            continue

        def mk(kind: str, s: ScreenedTender = s, db_id: int = db_id) -> Alert:
            return Alert(
                db_id,
                kind,
                s.tender.title,
                s.tender.organisation,
                s.tender.closing_at,
                s.score.total if s.score else None,
                s.tender.url,
            )

        if s.score.tier == "high":
            alerts.append(mk("new_high_priority"))
        if s.score.tier in ("high", "medium"):
            for d in reminders_due(s.tender.closing_at, last_run, result.now, reminder_days):
                alerts.append(mk(f"reminder_{d}d"))
    return alerts


def _record_failure(
    session: Session, started: datetime, names: list[str], exc: BaseException
) -> None:
    """Best effort: a failed run should still be visible in pipeline_runs."""
    try:
        runs = RunRepository(session)
        runs.finish(runs.start(started, names), "failed", {}, error=f"{type(exc).__name__}: {exc}")
        session.commit()
    except Exception:
        session.rollback()
        log.exception("could not record failed run")


def run_pipeline(
    sources: Sequence[SourceAdapter],
    profile: CompanyProfile,
    sessions: sessionmaker[Session] | None = None,
    notifiers: Sequence[Notifier] = (),
    now: datetime | None = None,
    output_dir: Path | None = None,
    site_dir: Path | None = None,
) -> RunResult:
    started = datetime.now(UTC)
    tenders, reports = _fetch_all(sources)
    clock = _screening_clock(sources, now)

    session = sessions() if sessions else None
    try:
        dedup = Deduplicator()
        last_run_at: datetime | None = None
        run_row = None
        if session is not None:
            runs = RunRepository(session)
            last = runs.last_successful()
            last_run_at = last.started_at if last else None
            run_row = runs.start(started, [s.name for s in sources])
            for known in TenderRepository(session).known_open_tenders(clock):
                dedup.seed(known)

        screened = screen(tenders, profile, clock, dedup=dedup)
        result = RunResult(started, clock, reports, screened, profile.name, profile.top_k)
        windows = [
            preparation_window(s.tender.closing_at, clock, profile.bid_preparation_days)
            for s in result.shortlist
        ]
        result.peak_concurrent_bids, result.peak_at = peak_concurrency(windows)

        if session is not None and run_row is not None:
            repo = TenderRepository(session)
            result.upsert = repo.upsert(screened, seen_at=started)
            result.run_id = run_row.id
            if notifiers:
                ids = repo.ids_for_keys(s.tender.key for s in result.eligible)
                alerts = build_alerts(result, ids, last_run_at, profile.reminder_days)
                result.alerts_built = len(alerts)
                result.alerts = AlertDispatcher(notifiers, AlertRepository(session)).dispatch(
                    alerts, clock
                )
            result.finished_at = datetime.now(UTC)
            RunRepository(session).finish(run_row, result.status, result.stats())
            session.commit()
    except Exception as exc:
        if session is not None:
            session.rollback()
            _record_failure(session, started, [s.name for s in sources], exc)
        raise
    finally:
        if session is not None:
            session.close()

    result.finished_at = result.finished_at or datetime.now(UTC)
    write_reports(result, profile, output_dir=output_dir, site_dir=site_dir)
    summary = {
        k: v for k, v in result.stats().items() if k not in ("sources", "ineligible_reasons")
    }
    log.info("pipeline finished", extra={"status": result.status, **summary})
    return result


def write_reports(
    result: RunResult,
    profile: CompanyProfile,
    output_dir: Path | None = None,
    site_dir: Path | None = None,
) -> None:
    """Excel tiers workbook and the static dashboard site."""
    if output_dir is not None:
        from bidalert.reports.excel import write_excel

        result.report_paths["excel"] = str(
            write_excel(result, profile, output_dir / "screened_tenders.xlsx")
        )
    if site_dir is not None:
        from bidalert.reports.dashboard import write_site

        result.report_paths["dashboard"] = str(write_site(result, profile, site_dir))
