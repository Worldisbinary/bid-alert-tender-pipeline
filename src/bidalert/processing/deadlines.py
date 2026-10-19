"""Deadline and interval logic.

* `closing_bucket` - which "closing soon" window a tender is in.
* `reminders_due` - which reminders (T-7d, T-3d, T-1d) fall in the half-open
  interval (last_run, now]. Half-open intervals mean consecutive runs never send
  the same reminder twice and never skip one, whatever the schedule.
* `peak_concurrency` - sweep line over bid-preparation intervals: how many bids
  the team would be preparing at once if it pursued every shortlisted tender.
  Sort 2n events, scan once: O(n log n).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30), name="IST")  # India has no DST

BUCKETS: tuple[tuple[str, timedelta], ...] = (
    ("closing_24h", timedelta(hours=24)),
    ("closing_3d", timedelta(days=3)),
    ("closing_7d", timedelta(days=7)),
)


def days_remaining(closing_at: datetime, now: datetime) -> float:
    return (closing_at - now).total_seconds() / 86400.0


def closing_bucket(closing_at: datetime, now: datetime) -> str:
    left = closing_at - now
    if left <= timedelta(0):
        return "closed"
    for name, horizon in BUCKETS:
        if left <= horizon:
            return name
    return "later"


def reminders_due(
    closing_at: datetime,
    last_run: datetime | None,
    now: datetime,
    reminder_days: Sequence[int] = (7, 3, 1),
) -> list[int]:
    """Reminder offsets (in days) whose fire time lies in (last_run, now].

    With no previous run, only reminders whose time has passed but whose tender is
    still open fire, and only the most urgent one, to avoid a burst on first start.
    """
    if closing_at <= now:
        return []
    due = []
    for d in sorted(reminder_days, reverse=True):
        fire_at = closing_at - timedelta(days=d)
        lower_ok = last_run is None or fire_at > last_run
        if lower_ok and fire_at <= now:
            due.append(d)
    if last_run is None and due:
        return [min(due)]
    return due


def peak_concurrency(intervals: Iterable[tuple[datetime, datetime]]) -> tuple[int, datetime | None]:
    """Maximum number of overlapping half-open intervals [start, end) and when it first occurs.

    Ends sort before starts at the same instant, so back-to-back intervals do not overlap.
    """
    events: list[tuple[datetime, int]] = []
    for start, end in intervals:
        if end <= start:
            continue
        events.append((start, 1))
        events.append((end, -1))
    events.sort(key=lambda e: (e[0], e[1]))  # -1 (end) before +1 (start) on ties
    current = peak = 0
    peak_at: datetime | None = None
    for at, delta in events:
        current += delta
        if current > peak:
            peak, peak_at = current, at
    return peak, peak_at


def preparation_window(
    closing_at: datetime, now: datetime, prep_days: int
) -> tuple[datetime, datetime]:
    """When work on a bid would happen: the last `prep_days` before closing, not before now."""
    return max(now, closing_at - timedelta(days=prep_days)), closing_at
