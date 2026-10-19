import itertools
import random
from datetime import UTC, datetime, timedelta

import pytest

from bidalert.processing.deadlines import (
    closing_bucket,
    peak_concurrency,
    preparation_window,
    reminders_due,
)
from bidalert.processing.topk import top_k

T0 = datetime(2026, 10, 1, tzinfo=UTC)


def test_top_k_matches_sorting():
    rng = random.Random(1)
    for _ in range(100):
        xs = [rng.randint(0, 50) for _ in range(rng.randint(0, 60))]
        k = rng.randint(0, 70)
        expected = sorted(xs, reverse=True)[:k]
        assert top_k(xs, k, key=float) == expected


def test_top_k_ties_keep_input_order():
    items = [("a", 1), ("b", 2), ("c", 2), ("d", 2)]
    assert top_k(items, 2, key=lambda x: x[1]) == [("b", 2), ("c", 2)]


@pytest.mark.parametrize(
    ("delta", "bucket"),
    [
        (-1, "closed"),
        (0, "closed"),
        (12, "closing_24h"),
        (48, "closing_3d"),
        (100, "closing_7d"),
        (500, "later"),
    ],
)
def test_closing_bucket(delta, bucket):
    assert closing_bucket(T0 + timedelta(hours=delta), T0) == bucket


def test_reminders_fire_once_across_consecutive_runs():
    closing = T0 + timedelta(days=10)
    runs = [T0 + timedelta(hours=6 * i) for i in range(45)]  # every 6 h for ~11 days
    fired = []
    for prev, cur in itertools.pairwise(runs):
        fired += reminders_due(closing, prev, cur)
    assert sorted(fired) == [1, 3, 7]


def test_first_run_sends_only_most_urgent_reminder():
    closing = T0 + timedelta(hours=20)
    assert reminders_due(closing, None, T0) == [1]
    assert reminders_due(T0 - timedelta(hours=1), None, T0) == []


def brute_peak(intervals):
    points = sorted({p for iv in intervals for p in iv})
    return max((sum(1 for s, e in intervals if s <= p < e) for p in points), default=0)


def test_peak_concurrency_matches_brute_force():
    rng = random.Random(5)
    for _ in range(100):
        ivs = []
        for _ in range(rng.randint(0, 25)):
            s = T0 + timedelta(days=rng.randint(0, 30))
            ivs.append((s, s + timedelta(days=rng.randint(0, 10))))
        peak, _ = peak_concurrency(ivs)
        assert peak == brute_peak([iv for iv in ivs if iv[1] > iv[0]])


def test_back_to_back_intervals_do_not_overlap():
    a = (T0, T0 + timedelta(days=1))
    b = (T0 + timedelta(days=1), T0 + timedelta(days=2))
    assert peak_concurrency([a, b]) == (1, T0)


def test_preparation_window_never_starts_in_the_past():
    closing = T0 + timedelta(days=3)
    assert preparation_window(closing, T0, 10) == (T0, closing)
    assert preparation_window(T0 + timedelta(days=30), T0, 10)[0] == T0 + timedelta(days=20)
