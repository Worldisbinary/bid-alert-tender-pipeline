"""Migrations, idempotent upserts and index usage (EXPLAIN) on SQLite.

The same checks run against PostgreSQL in CI (tests/test_postgres.py).
"""

from datetime import timedelta

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import func, select, text

from bidalert.pipeline import screen
from bidalert.storage.repository import AlertRepository, TenderRepository
from bidalert.storage.schema import Base, TenderRow
from tests.conftest import NOW, make_tender


def test_migrations_match_orm_models(sessions):
    with sessions() as s:
        ctx = MigrationContext.configure(s.connection(), opts={"compare_type": True})
        assert compare_metadata(ctx, Base.metadata) == []


def _screened(profile, n=5, **kw):
    tenders = [
        make_tender(source_tender_id=f"T-{i}", title=f"Supply of reagents batch {i}", **kw)
        for i in range(n)
    ]
    return screen(tenders, profile, NOW)


def test_upsert_is_idempotent(sessions, profile):
    with sessions() as s:
        repo = TenderRepository(s)
        first = repo.upsert(_screened(profile), seen_at=NOW)
        s.commit()
        second = repo.upsert(_screened(profile), seen_at=NOW + timedelta(hours=6))
        s.commit()
        assert (first.inserted, first.updated) == (5, 0)
        assert (second.inserted, second.updated) == (0, 5)
        assert s.scalar(select(func.count()).select_from(TenderRow)) == 5
        row = s.scalars(select(TenderRow).limit(1)).one()
        assert row.first_seen_at == NOW and row.last_seen_at == NOW + timedelta(hours=6)
        assert row.closing_at.tzinfo is not None  # tz-aware even on SQLite


def test_duplicates_are_linked_to_canonical_row(sessions, profile):
    tenders = [
        make_tender(
            source_tender_id="A", portal="GeM", title="Supply of laboratory reagents - DRDO"
        ),
        make_tender(
            source_tender_id="B", portal="CPPP", title="SUPPLY OF LABORATORY REAGENTS - DRDO"
        ),
    ]
    with sessions() as s:
        TenderRepository(s).upsert(screen(tenders, profile, NOW), seen_at=NOW)
        s.commit()
        rows = {r.source_tender_id: r for r in s.scalars(select(TenderRow))}
        assert rows["B"].duplicate_of_id == rows["A"].id and rows["B"].duplicate_similarity == 1.0
        # cross-run seeding: only the canonical one is a dedup seed
        assert [k.key for k in TenderRepository(s).known_open_tenders(NOW)] == ["test:A"]


def test_query_filters_and_pagination(sessions, profile):
    with sessions() as s:
        repo = TenderRepository(s)
        repo.upsert(_screened(profile, n=12), seen_at=NOW)
        s.commit()
        rows, total = repo.query(q="REAGENTS", now=NOW, offset=10, limit=5)
        assert total == 12 and len(rows) == 2
        _, none = repo.query(q="no such thing", now=NOW)
        assert none == 0
        _, closed = repo.query(now=NOW + timedelta(days=60))
        assert closed == 0
        _, incl = repo.query(now=NOW + timedelta(days=60), include_closed=True)
        assert incl == 12
        counts = repo.counts(NOW)
        assert counts["tenders_total"] == 12 and counts["open_eligible"] == 12


def test_alert_ledger_is_idempotent(sessions, profile):
    with sessions() as s:
        TenderRepository(s).upsert(_screened(profile, n=1), seen_at=NOW)
        tid = s.scalar(select(TenderRow.id))
        ledger = AlertRepository(s)
        assert not ledger.already_sent(tid, "email", "new_high_priority")
        ledger.record(tid, "email", "new_high_priority", NOW)
        ledger.record(tid, "email", "new_high_priority", NOW)  # ON CONFLICT DO NOTHING
        assert ledger.already_sent(tid, "email", "new_high_priority")


def plan(session, sql):
    return " ".join(str(r[-1]) for r in session.execute(text("EXPLAIN QUERY PLAN " + sql)))


def test_indexes_are_used_by_api_queries(sessions):
    with sessions() as s:
        assert "ix_tenders_eligible_score" in plan(
            s, "SELECT id FROM tenders WHERE eligible = 1 ORDER BY score DESC LIMIT 20"
        )
        assert "ix_tenders_closing_at" in plan(
            s, "SELECT id FROM tenders WHERE closing_at > '2026-01-01'"
        )
        assert "ix_tenders_fingerprint" in plan(s, "SELECT id FROM tenders WHERE fingerprint = 'x'")
        # SQLite backs a UNIQUE constraint with an internal index: sqlite_autoindex_<table>_N.
        assert "COVERING INDEX sqlite_autoindex_tenders" in plan(
            s, "SELECT id FROM tenders WHERE source = 'cppp' AND source_tender_id = 'x'"
        )
        assert "ix_tenders_tier_closing" in plan(
            s, "SELECT id FROM tenders WHERE tier = 'high' ORDER BY closing_at"
        )
