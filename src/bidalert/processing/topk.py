"""Top-k selection with a bounded min-heap.

Sorting n tenders to show the best k costs O(n log n). Keeping a min-heap of
size k costs O(n log k) time and O(k) memory: the heap root is the weakest of
the current top-k, so each new item is compared with it in O(1) and replaces it
in O(log k) only when better. With n = 10^5 tenders and k = 15 that is ~4x fewer
comparisons than a full sort, and memory stays constant while streaming.
"""

from __future__ import annotations

import heapq
from collections.abc import Callable, Iterable
from typing import TypeVar

T = TypeVar("T")


def top_k(items: Iterable[T], k: int, key: Callable[[T], float]) -> list[T]:
    """Return the k items with the largest key, best first.

    Ties keep input order (earlier wins), which makes the output deterministic.
    """
    if k <= 0:
        return []
    heap: list[tuple[float, int, T]] = []
    for i, item in enumerate(items):
        # Tie-break with -i so that, among equal keys, the *earlier* item ranks higher
        # and the later one is the first to be evicted from the min-heap.
        entry = (key(item), -i, item)
        if len(heap) < k:
            heapq.heappush(heap, entry)
        elif entry[:2] > heap[0][:2]:
            heapq.heapreplace(heap, entry)
    return [item for _, _, item in sorted(heap, key=lambda e: (e[0], e[1]), reverse=True)]
