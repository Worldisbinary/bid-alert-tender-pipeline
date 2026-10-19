"""Source adapters and the registry that builds them from settings."""

from __future__ import annotations

from datetime import datetime

from bidalert.config import Settings
from bidalert.sources.base import SourceAdapter, SourceError
from bidalert.sources.cppp import CpppAdapter
from bidalert.sources.fixtures import FixtureAdapter
from bidalert.sources.http import PoliteHttpClient
from bidalert.sources.synthetic import SyntheticAdapter

__all__ = [
    "CpppAdapter",
    "FixtureAdapter",
    "PoliteHttpClient",
    "SourceAdapter",
    "SourceError",
    "SyntheticAdapter",
    "build_http_client",
    "build_sources",
]

KNOWN_SOURCES = ("cppp", "fixtures", "synthetic")


def build_http_client(settings: Settings) -> PoliteHttpClient:
    return PoliteHttpClient(
        user_agent=settings.user_agent,
        min_interval_s=settings.http_min_interval_s,
        max_retries=settings.http_max_retries,
        timeout_s=settings.http_timeout_s,
        cache_dir=settings.http_cache_dir,
        cache_ttl_s=settings.http_cache_ttl_s,
    )


def build_sources(
    names: tuple[str, ...],
    settings: Settings,
    client: PoliteHttpClient | None = None,
    now: datetime | None = None,
) -> list[SourceAdapter]:
    adapters: list[SourceAdapter] = []
    for name in names:
        if name == "cppp":
            if client is None:
                raise ValueError("the cppp source needs an HTTP client")
            adapters.append(CpppAdapter(client, max_pages=settings.cppp_max_pages))
        elif name == "fixtures":
            adapters.append(FixtureAdapter(settings.fixtures_dir))
        elif name == "synthetic":
            adapters.append(SyntheticAdapter(now=now))
        else:
            raise ValueError(f"unknown source {name!r}; choose from {KNOWN_SOURCES}")
    return adapters
