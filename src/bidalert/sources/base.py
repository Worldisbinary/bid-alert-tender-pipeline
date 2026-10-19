"""The one interface every source adapter implements.

Adding a portal means writing one class with a `fetch()` that yields normalised
`Tender` objects. Nothing downstream changes (open/closed principle).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from datetime import datetime

from bidalert.models import Tender


class SourceError(RuntimeError):
    """A source could not be fetched or parsed. The pipeline logs it and carries on."""


class SourceAdapter(ABC):
    #: short identifier stored in tenders.source
    name: str = "base"
    #: True only for adapters that fetch real, current listings from the network
    is_live: bool = False
    #: human description shown on the dashboard banner
    description: str = ""

    @abstractmethod
    def fetch(self) -> Iterator[Tender]:
        """Yield normalised tenders. May raise SourceError."""

    def reference_time(self) -> datetime | None:
        """Clock to screen against. Recorded fixtures return their recording time so
        deadlines are evaluated as they were when recorded; live sources return None."""
        return None
