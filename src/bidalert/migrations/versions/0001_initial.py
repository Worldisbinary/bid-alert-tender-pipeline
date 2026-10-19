"""Initial schema: tenders, pipeline_runs, alerts_sent.

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "tenders",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_tender_id", sa.String(128), nullable=False),
        sa.Column("portal", sa.String(64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("organisation", sa.Text(), nullable=False),
        sa.Column("reference_no", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("category", sa.String(64), nullable=True),
        sa.Column("category_inferred", sa.Boolean(), nullable=False),
        sa.Column("state", sa.String(64), nullable=True),
        sa.Column("estimated_value_inr", sa.BigInteger(), nullable=True),
        sa.Column("turnover_requirement_cr", sa.Float(), nullable=True),
        sa.Column("experience_years_required", sa.Integer(), nullable=True),
        sa.Column("msme_preferred", sa.Boolean(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closing_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("opening_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "duplicate_of_id",
            sa.Integer(),
            sa.ForeignKey("tenders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("duplicate_similarity", sa.Float(), nullable=True),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("ineligible_reasons", json_type, nullable=False),
        sa.Column("unverified", json_type, nullable=False),
        sa.Column("matched_keywords", json_type, nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("tier", sa.String(8), nullable=True),
        sa.Column("score_breakdown", json_type, nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source", "source_tender_id", name="uq_tenders_source_tender_id"),
    )
    op.create_index("ix_tenders_eligible_score", "tenders", ["eligible", "score"])
    op.create_index("ix_tenders_closing_at", "tenders", ["closing_at"])
    op.create_index("ix_tenders_fingerprint", "tenders", ["fingerprint"])
    op.create_index("ix_tenders_tier_closing", "tenders", ["tier", "closing_at"])

    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("sources", json_type, nullable=False),
        sa.Column("stats", json_type, nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_pipeline_runs_started_at", "pipeline_runs", ["started_at"])

    op.create_table(
        "alerts_sent",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "tender_id",
            sa.Integer(),
            sa.ForeignKey("tenders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tender_id", "channel", "kind", name="uq_alerts_once"),
    )


def downgrade() -> None:
    op.drop_table("alerts_sent")
    op.drop_index("ix_pipeline_runs_started_at", table_name="pipeline_runs")
    op.drop_table("pipeline_runs")
    op.drop_index("ix_tenders_tier_closing", table_name="tenders")
    op.drop_index("ix_tenders_fingerprint", table_name="tenders")
    op.drop_index("ix_tenders_closing_at", table_name="tenders")
    op.drop_index("ix_tenders_eligible_score", table_name="tenders")
    op.drop_table("tenders")
