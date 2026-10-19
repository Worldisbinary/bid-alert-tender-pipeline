"""Relational schema (SQLAlchemy 2.0 typed ORM). Works on SQLite and PostgreSQL.

Design notes
* Natural key (source, source_tender_id) is UNIQUE -> upserts are idempotent:
  re-running a pipeline updates rows instead of duplicating them.
* `duplicate_of_id` points at the canonical row when a cross-portal duplicate is found.
* JSON columns hold the explanation (score breakdown, reasons); JSONB on Postgres.
* Indexes match the API's real queries; tests check them with EXPLAIN.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Dialect,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JsonType = JSON().with_variant(JSONB(), "postgresql")


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware datetimes on every backend.

    PostgreSQL stores `timestamptz` natively. SQLite has no timezone support and
    returns naive values, so we normalise to UTC on the way in and re-attach UTC on
    the way out. Naive datetimes are rejected: they are a classic source of bugs.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime; pass a timezone-aware value")
        value = value.astimezone(UTC)
        return value.replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {datetime: UTCDateTime}


class TenderRow(Base):
    __tablename__ = "tenders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32))
    source_tender_id: Mapped[str] = mapped_column(String(128))
    portal: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(Text)
    organisation: Mapped[str] = mapped_column(Text)
    reference_no: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(64))
    category_inferred: Mapped[bool] = mapped_column(Boolean, default=False)
    state: Mapped[str | None] = mapped_column(String(64))
    estimated_value_inr: Mapped[int | None] = mapped_column(BigInteger)
    turnover_requirement_cr: Mapped[float | None] = mapped_column(Float)
    experience_years_required: Mapped[int | None] = mapped_column(Integer)
    msme_preferred: Mapped[bool | None] = mapped_column(Boolean)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    closing_at: Mapped[datetime] = mapped_column(UTCDateTime())
    opening_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)

    fingerprint: Mapped[str] = mapped_column(String(64))
    duplicate_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenders.id", ondelete="SET NULL")
    )
    duplicate_similarity: Mapped[float | None] = mapped_column(Float)

    eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    ineligible_reasons: Mapped[list[str]] = mapped_column(JsonType, default=list)
    unverified: Mapped[list[str]] = mapped_column(JsonType, default=list)
    matched_keywords: Mapped[list[str]] = mapped_column(JsonType, default=list)
    score: Mapped[float | None] = mapped_column(Float)
    tier: Mapped[str | None] = mapped_column(String(8))
    score_breakdown: Mapped[list[dict[str, Any]]] = mapped_column(JsonType, default=list)

    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime())
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime())

    __table_args__ = (
        UniqueConstraint("source", "source_tender_id", name="uq_tenders_source_tender_id"),
        # "best eligible tenders": WHERE eligible ... ORDER BY score DESC
        Index("ix_tenders_eligible_score", "eligible", "score"),
        # "closing soon" and "still open": range scans on closing_at
        Index("ix_tenders_closing_at", "closing_at"),
        # exact-duplicate lookups across sources
        Index("ix_tenders_fingerprint", "fingerprint"),
        # tier filter + deadline ordering on the dashboard / API
        Index("ix_tenders_tier_closing", "tier", "closing_at"),
    )


class PipelineRunRow(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(String(16))  # running | success | partial | failed
    sources: Mapped[list[str]] = mapped_column(JsonType, default=list)
    stats: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


class AlertSentRow(Base):
    __tablename__ = "alerts_sent"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id", ondelete="CASCADE"))
    channel: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(32))
    sent_at: Mapped[datetime] = mapped_column(UTCDateTime())

    __table_args__ = (
        # An alert of a given kind goes to a channel at most once per tender.
        UniqueConstraint("tender_id", "channel", "kind", name="uq_alerts_once"),
    )
