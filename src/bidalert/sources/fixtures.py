"""Recorded fixtures: real CPPP listing pages saved to disk, replayed offline.

Tests and CI must not depend on a government website being up. `bidalert
record-fixtures` saves the listing table from real pages plus a `meta.json` with
the recording time; this adapter parses them with the same parser as the live
adapter, so parser regressions are caught without network access.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from bidalert.models import Tender
from bidalert.sources.base import SourceAdapter, SourceError
from bidalert.sources.cppp import parse_listing


class FixtureAdapter(SourceAdapter):
    name = "fixtures"
    is_live = False

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        meta_path = directory / "meta.json"
        if not meta_path.exists():
            raise SourceError(f"no fixtures at {directory} (expected meta.json)")
        self.meta = json.loads(meta_path.read_text("utf-8"))
        self.recorded_at = datetime.fromisoformat(self.meta["recorded_at"])
        self.description = (
            f"Recorded CPPP listing pages (captured {self.recorded_at:%d %b %Y %H:%M %Z}); "
            "replayed offline, deadlines evaluated as of the capture time"
        )

    def reference_time(self) -> datetime:
        return self.recorded_at

    def fetch(self) -> Iterator[Tender]:
        seen: set[str] = set()
        for page in self.meta["pages"]:
            html = (self.directory / page["file"]).read_text("utf-8")
            for t in parse_listing(html, page_link=page["url"], source=self.name):
                if t.source_tender_id not in seen:
                    seen.add(t.source_tender_id)
                    yield t
