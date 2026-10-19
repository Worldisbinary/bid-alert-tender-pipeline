"""PostgreSQL integration: migrations, upsert idempotency and index plans.

Runs in CI against a postgres:16 service container. Locally:
    export BIDALERT_TEST_POSTGRES_URL=postgresql+psycopg://bidalert:bidalert@localhost:5432/bidalert
    pytest -m postgres
"""

import os

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import func, select, text

from bidalert.pipeline import run_pipeline
from bidalert.sources.synthetic import SyntheticAdapter
from bidalert.storage.db import make_engine, migrate, session_factory
from bidalert.storage.schema import Base, TenderRow
from tests.conftest import NOW

URL = os.environ.get("BIDALERT_TEST_POSTGRES_URL")
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not URL, reason="BIDALERT_TEST_POSTGRES_URL not set"),
]


@pytest.fixture
def pg():
    assert URL
    engine = make_engine(URL)
    with engine.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    migrate(URL)
    yield session_factory(engine)
    engine.dispose()


def test_postgres_schema_upsert_and_plans(pg, profile):
    with pg() as s:
        ctx = MigrationContext.configure(s.connection(), opts={"compare_type": True})
        assert compare_metadata(ctx, Base.metadata) == []

    for _ in range(2):
        run_pipeline([SyntheticAdapter(now=NOW)], profile, sessions=pg, now=NOW)
    with pg() as s:
        assert s.scalar(select(func.count()).select_from(TenderRow)) == 180
        row = s.scalars(select(TenderRow).where(TenderRow.eligible.is_(True)).limit(1)).one()
        assert row.closing_at.tzinfo is not None and isinstance(row.score_breakdown, list)

        s.execute(text("ANALYZE tenders"))
        # On a 180-row table the planner rightly prefers a sequential scan; disabling it
        # shows whether an index *can* serve each query.
        s.execute(text("SET enable_seqscan = off"))

        def plan(sql: str) -> str:
            return "\n".join(r[0] for r in s.execute(text("EXPLAIN " + sql)))

        assert "ix_tenders_eligible_score" in plan(
            "SELECT id FROM tenders WHERE eligible ORDER BY score DESC LIMIT 20"
        )
        assert "ix_tenders_closing_at" in plan("SELECT id FROM tenders WHERE closing_at > now()")
        assert "uq_tenders_source_tender_id" in plan(
            "SELECT id FROM tenders WHERE source = 'synthetic' AND source_tender_id = 'x'"
        )
