"""Data access. All writes are idempotent so a pipeline run can be retried safely."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, and_, func, or_, select, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from bidalert.models import ScreenedTender
from bidalert.processing.dedup import KnownTender, token_set
from bidalert.storage.schema import AlertSentRow, PipelineRunRow, TenderRow

UPSERT_CHUNK = 200  # rows per INSERT statement; keeps bound parameters well under SQLite's limit

# Columns refreshed on every sighting. first_seen_at and id are deliberately absent.
_UPDATABLE = (
    "portal",
    "title",
    "organisation",
    "reference_no",
    "url",
    "category",
    "category_inferred",
    "state",
    "estimated_value_inr",
    "turnover_requirement_cr",
    "experience_years_required",
    "msme_preferred",
    "published_at",
    "closing_at",
    "opening_at",
    "is_synthetic",
    "fingerprint",
    "duplicate_similarity",
    "eligible",
    "ineligible_reasons",
    "unverified",
    "matched_keywords",
    "score",
    "tier",
    "score_breakdown",
    "last_seen_at",
)


@dataclass(frozen=True)
class UpsertStats:
    inserted: int
    updated: int


def _utc(dt: datetime | None) -> datetime | None:
    return dt.astimezone(UTC) if dt is not None else None


def to_row(s: ScreenedTender, seen_at: datetime) -> dict[str, Any]:
    t = s.tender
    return {
        "source": t.source,
        "source_tender_id": t.source_tender_id,
        "portal": t.portal,
        "title": t.title,
        "organisation": t.organisation,
        "reference_no": t.reference_no,
        "url": t.url,
        "category": s.category,
        "category_inferred": t.category is None and s.inferred_category is not None,
        "state": t.state,
        "estimated_value_inr": t.estimated_value_inr,
        "turnover_requirement_cr": t.turnover_requirement_cr,
        "experience_years_required": t.experience_years_required,
        "msme_preferred": t.msme_preferred,
        "published_at": _utc(t.published_at),
        "closing_at": _utc(t.closing_at),
        "opening_at": _utc(t.opening_at),
        "is_synthetic": t.is_synthetic,
        "fingerprint": s.fingerprint,
        "duplicate_similarity": s.duplicate_similarity,
        "eligible": bool(s.eligibility and s.eligibility.eligible),
        "ineligible_reasons": list(s.eligibility.reasons) if s.eligibility else [],
        "unverified": list(s.eligibility.unverified) if s.eligibility else [],
        "matched_keywords": list(s.matched_keywords),
        "score": s.score.total if s.score else None,
        "tier": s.score.tier if s.score else None,
        "score_breakdown": [asdict(f) for f in s.score.factors] if s.score else [],
        "first_seen_at": seen_at,
        "last_seen_at": seen_at,
    }


def _insert_for(session: Session) -> Any:
    name = session.get_bind().dialect.name
    if name == "postgresql":
        return postgresql.insert
    if name == "sqlite":
        return sqlite.insert
    raise NotImplementedError(f"upsert not implemented for {name}")


class TenderRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # -- writes -----------------------------------------------------------------
    def upsert(self, screened: Sequence[ScreenedTender], seen_at: datetime) -> UpsertStats:
        """INSERT ... ON CONFLICT (source, source_tender_id) DO UPDATE.

        Running it twice with the same input leaves the table unchanged except for
        last_seen_at: that is what makes pipeline retries safe.
        """
        if not screened:
            return UpsertStats(0, 0)
        insert = _insert_for(self.session)
        rows = [to_row(s, seen_at) for s in screened]
        existing = self._existing_keys({(r["source"], r["source_tender_id"]) for r in rows})
        for i in range(0, len(rows), UPSERT_CHUNK):
            stmt = insert(TenderRow).values(rows[i : i + UPSERT_CHUNK])
            stmt = stmt.on_conflict_do_update(
                index_elements=["source", "source_tender_id"],
                set_={c: getattr(stmt.excluded, c) for c in _UPDATABLE},
            )
            self.session.execute(stmt)
        self._link_duplicates(screened)
        inserted = sum(1 for r in rows if (r["source"], r["source_tender_id"]) not in existing)
        return UpsertStats(inserted=inserted, updated=len(rows) - inserted)

    def _existing_keys(self, keys: set[tuple[str, str]]) -> set[tuple[str, str]]:
        found: set[tuple[str, str]] = set()
        by_source: dict[str, list[str]] = {}
        for source, sid in keys:
            by_source.setdefault(source, []).append(sid)
        for source, ids in by_source.items():
            for i in range(0, len(ids), 500):
                q = select(TenderRow.source, TenderRow.source_tender_id).where(
                    TenderRow.source == source, TenderRow.source_tender_id.in_(ids[i : i + 500])
                )
                found.update((s, sid) for s, sid in self.session.execute(q))
        return found

    def _link_duplicates(self, screened: Sequence[ScreenedTender]) -> None:
        dupes = [s for s in screened if s.duplicate_of]
        if not dupes:
            return
        wanted = {s.tender.key for s in dupes} | {s.duplicate_of for s in dupes if s.duplicate_of}
        ids = self.ids_for_keys(wanted)
        for s in dupes:
            canonical = ids.get(s.duplicate_of or "")
            own = ids.get(s.tender.key)
            if canonical and own and canonical != own:
                self.session.execute(
                    update(TenderRow).where(TenderRow.id == own).values(duplicate_of_id=canonical)
                )

    def ids_for_keys(self, keys: Iterable[str]) -> dict[str, int]:
        out: dict[str, int] = {}
        pairs = [k.split(":", 1) for k in keys]
        by_source: dict[str, list[str]] = {}
        for source, sid in pairs:
            by_source.setdefault(source, []).append(sid)
        for source, sids in by_source.items():
            q = select(TenderRow.id, TenderRow.source_tender_id).where(
                TenderRow.source == source, TenderRow.source_tender_id.in_(sids)
            )
            for row_id, sid in self.session.execute(q):
                out[f"{source}:{sid}"] = row_id
        return out

    # -- reads used by the pipeline ---------------------------------------------
    def known_open_tenders(self, now: datetime) -> list[KnownTender]:
        """Canonical tenders still open: the seed set for cross-run duplicate detection."""
        q = select(
            TenderRow.source,
            TenderRow.source_tender_id,
            TenderRow.portal,
            TenderRow.fingerprint,
            TenderRow.title,
            TenderRow.organisation,
            TenderRow.closing_at,
        ).where(TenderRow.closing_at > now, TenderRow.duplicate_of_id.is_(None))
        return [
            KnownTender(f"{src}:{sid}", portal, fp, token_set(title, org), closing.date())
            for src, sid, portal, fp, title, org, closing in self.session.execute(q)
        ]

    # -- reads used by the API ---------------------------------------------------
    def query(
        self,
        *,
        q: str | None = None,
        tier: str | None = None,
        source: str | None = None,
        eligible: bool | None = None,
        min_score: float | None = None,
        closing_within_days: int | None = None,
        include_duplicates: bool = False,
        include_closed: bool = False,
        now: datetime | None = None,
        sort: str = "score",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[TenderRow], int]:
        now = now or datetime.now(UTC)
        conds = []
        if q:
            like = f"%{q.lower()}%"
            conds.append(
                or_(
                    func.lower(TenderRow.title).like(like),
                    func.lower(TenderRow.organisation).like(like),
                )
            )
        if tier:
            conds.append(TenderRow.tier == tier)
        if source:
            conds.append(TenderRow.source == source)
        if eligible is not None:
            conds.append(TenderRow.eligible.is_(eligible))
        if min_score is not None:
            conds.append(TenderRow.score >= min_score)
        if not include_closed:
            conds.append(TenderRow.closing_at > now)
        if closing_within_days is not None:
            conds.append(TenderRow.closing_at <= now + timedelta(days=closing_within_days))
        if not include_duplicates:
            conds.append(TenderRow.duplicate_of_id.is_(None))
        where = and_(*conds) if conds else None

        base = select(TenderRow)
        count_q = select(func.count()).select_from(TenderRow)
        if where is not None:
            base = base.where(where)
            count_q = count_q.where(where)
        # Tie-break on id so pagination is stable (no row appears on two pages).
        order: dict[str, list[ColumnElement[Any]]] = {
            "score": [TenderRow.score.desc().nulls_last(), TenderRow.id.asc()],
            "closing": [TenderRow.closing_at.asc(), TenderRow.id.asc()],
            "newest": [TenderRow.first_seen_at.desc(), TenderRow.id.asc()],
        }
        rows = list(self.session.scalars(base.order_by(*order[sort]).offset(offset).limit(limit)))
        total = int(self.session.scalar(count_q) or 0)
        return rows, total

    def get(self, tender_id: int) -> TenderRow | None:
        return self.session.get(TenderRow, tender_id)

    def counts(self, now: datetime) -> dict[str, int]:
        open_canonical = and_(TenderRow.closing_at > now, TenderRow.duplicate_of_id.is_(None))
        tiers = dict(
            self.session.execute(
                select(TenderRow.tier, func.count())
                .where(open_canonical, TenderRow.eligible.is_(True))
                .group_by(TenderRow.tier)
            ).all()
        )
        return {
            "tenders_total": int(
                self.session.scalar(select(func.count()).select_from(TenderRow)) or 0
            ),
            "open": int(self.session.scalar(select(func.count()).where(open_canonical)) or 0),
            "open_eligible": int(
                self.session.scalar(
                    select(func.count()).where(open_canonical, TenderRow.eligible.is_(True))
                )
                or 0
            ),
            "high": int(tiers.get("high", 0)),
            "medium": int(tiers.get("medium", 0)),
            "low": int(tiers.get("low", 0)),
        }


class RunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def start(self, started_at: datetime, sources: Sequence[str]) -> PipelineRunRow:
        run = PipelineRunRow(
            started_at=started_at, status="running", sources=list(sources), stats={}
        )
        self.session.add(run)
        self.session.flush()
        return run

    def finish(
        self, run: PipelineRunRow, status: str, stats: dict[str, Any], error: str | None = None
    ) -> None:
        run.status = status
        run.stats = stats
        run.error = error
        run.finished_at = datetime.now(UTC)

    def last_successful(self) -> PipelineRunRow | None:
        q = (
            select(PipelineRunRow)
            .where(PipelineRunRow.status.in_(("success", "partial")))
            .order_by(PipelineRunRow.started_at.desc())
            .limit(1)
        )
        return self.session.scalars(q).first()

    def recent(self, limit: int = 20) -> list[PipelineRunRow]:
        q = select(PipelineRunRow).order_by(PipelineRunRow.started_at.desc()).limit(limit)
        return list(self.session.scalars(q))


class AlertRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def already_sent(self, tender_id: int, channel: str, kind: str) -> bool:
        q = select(AlertSentRow.id).where(
            AlertSentRow.tender_id == tender_id,
            AlertSentRow.channel == channel,
            AlertSentRow.kind == kind,
        )
        return self.session.scalar(q) is not None

    def record(self, tender_id: int, channel: str, kind: str, sent_at: datetime) -> None:
        insert = _insert_for(self.session)
        stmt = insert(AlertSentRow).values(
            tender_id=tender_id, channel=channel, kind=kind, sent_at=sent_at
        )
        self.session.execute(
            stmt.on_conflict_do_nothing(index_elements=["tender_id", "channel", "kind"])
        )
