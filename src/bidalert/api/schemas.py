"""Pydantic response models: the API's public contract (also drives /docs)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class FactorOut(BaseModel):
    name: str
    weight: float
    value: float
    contribution: float
    known: bool
    reason: str


class TenderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    source_tender_id: str
    portal: str
    title: str
    organisation: str
    reference_no: str | None
    url: str | None
    category: str | None
    category_inferred: bool
    state: str | None
    estimated_value_inr: int | None
    published_at: datetime | None
    closing_at: datetime
    eligible: bool
    score: float | None
    tier: str | None
    is_synthetic: bool
    duplicate_of_id: int | None
    first_seen_at: datetime
    last_seen_at: datetime


class TenderDetail(TenderOut):
    opening_at: datetime | None
    turnover_requirement_cr: float | None
    experience_years_required: int | None
    msme_preferred: bool | None
    ineligible_reasons: list[str]
    unverified: list[str]
    matched_keywords: list[str]
    duplicate_similarity: float | None


class ScoreBreakdown(BaseModel):
    tender_id: int
    score: float | None
    tier: str | None
    eligible: bool
    ineligible_reasons: list[str]
    unverified: list[str]
    factors: list[FactorOut]
    explanation: str = Field(
        description="score = sum(weight * value); unknown inputs use a neutral 0.5"
    )


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    started_at: datetime
    finished_at: datetime | None
    status: str
    sources: list[str]
    stats: dict[str, Any]
    error: str | None


class Stats(BaseModel):
    tenders_total: int
    open: int
    open_eligible: int
    high: int
    medium: int
    low: int
    last_run: RunOut | None


class Health(BaseModel):
    status: str
    version: str
    detail: str | None = None
