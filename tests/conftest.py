from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from bidalert.models import Tender
from bidalert.profile import CompanyProfile, load_profile
from bidalert.storage.db import make_engine, migrate, session_factory

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


@pytest.fixture
def profile() -> CompanyProfile:
    return load_profile()


@pytest.fixture
def now() -> datetime:
    return NOW


def make_tender(**overrides: Any) -> Tender:
    base: dict[str, Any] = {
        "source": "test",
        "source_tender_id": "T-1",
        "portal": "CPPP",
        "title": "Supply of laboratory reagents",
        "organisation": "CSIR",
        "closing_at": NOW + timedelta(days=20),
    }
    base.update(overrides)
    return Tender(**base)


@pytest.fixture
def sqlite_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'test.db'}"


@pytest.fixture
def sessions(sqlite_url: str) -> Iterator[sessionmaker[Session]]:
    migrate(sqlite_url)
    engine = make_engine(sqlite_url)
    yield session_factory(engine)
    engine.dispose()
