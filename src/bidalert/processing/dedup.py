"""Cross-portal duplicate detection: exact fingerprints + MinHash/LSH near-duplicates.

The same tender is often listed on CPPP, a state portal and an aggregator with
small differences in case, punctuation, a prefix ("Procurement of ...") or a
suffix ("(re-published)").

1. **Exact fingerprint**: SHA-256 of normalised (title | organisation | closing date).
   Hash-set lookup, O(1) expected per tender.

2. **MinHash (Broder, 1997)**: estimates Jaccard similarity J(A, B) = |A ∩ B| / |A ∪ B|
   of two token sets. For each of P random hash functions h_i, sig_i(A) = min h_i(a);
   P[sig_i(A) == sig_i(B)] = J(A, B). P = 64 here.

3. **LSH banding**: split the signature into b bands of r rows and bucket on each
   band. Two sets become candidates if any band matches, with probability
   1 - (1 - J^r)^b. With b = 16, r = 4: J = 0.8 -> 99.98%, J = 0.5 -> 64%,
   J = 0.3 -> 12%. Candidates are then verified with the *exact* Jaccard, so LSH
   only decides what to compare (no false positives from the sketch itself).
   Cost: O(P * |tokens|) to sign, O(b) bucket lookups per tender, instead of
   O(n) comparisons each (O(n^2) overall).

Why not SimHash? It was the first implementation. On 8-12 word titles, adding a
single word moved the 64-bit SimHash by 3-15 bits, overlapping the distance of
unrelated titles, so recall on the synthetic re-listings was 9/20. MinHash works
on set overlap, which is what "same tender, slightly different title" means.

Rules that keep precision high:
* Only *different portals* can duplicate each other. Within one portal its own
  tender ID is authoritative (two lots with near-identical titles are two tenders).
* Closing dates must be within `date_tolerance_days`.
"""

from __future__ import annotations

import hashlib
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from bidalert.processing.text import normalise, tokens

MERSENNE_61 = (1 << 61) - 1


def fingerprint(title: str, organisation: str, closing: date) -> str:
    key = f"{normalise(title)}|{normalise(organisation)}|{closing.isoformat()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def token_set(title: str, organisation: str) -> frozenset[str]:
    return frozenset(tokens(f"{title} {organisation}"))


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def _hash64(token: str) -> int:
    return int.from_bytes(hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest(), "big")


class MinHasher:
    """P universal hash functions h_i(x) = (a_i * x + b_i) mod p, p = 2^61 - 1."""

    def __init__(self, num_perm: int = 64, seed: int = 1) -> None:
        rng = random.Random(seed)
        self.num_perm = num_perm
        self._params = [
            (rng.randrange(1, MERSENNE_61), rng.randrange(0, MERSENNE_61)) for _ in range(num_perm)
        ]

    def signature(self, toks: frozenset[str]) -> tuple[int, ...]:
        if not toks:
            return tuple([MERSENNE_61] * self.num_perm)
        xs = [_hash64(t) for t in toks]
        return tuple(min((a * x + b) % MERSENNE_61 for x in xs) for a, b in self._params)


def estimated_jaccard(sig_a: tuple[int, ...], sig_b: tuple[int, ...]) -> float:
    return sum(x == y for x, y in zip(sig_a, sig_b, strict=True)) / len(sig_a)


@dataclass(frozen=True)
class KnownTender:
    key: str
    portal: str
    fingerprint: str
    tokens: frozenset[str]
    closing: date


class LshIndex:
    """Banded LSH over MinHash signatures."""

    def __init__(self, bands: int = 16, rows: int = 4) -> None:
        self.bands, self.rows = bands, rows
        self._buckets: dict[tuple[int, tuple[int, ...]], list[str]] = defaultdict(list)

    def _keys(self, sig: tuple[int, ...]) -> list[tuple[int, tuple[int, ...]]]:
        if len(sig) != self.bands * self.rows:
            raise ValueError("signature length must equal bands * rows")
        return [(i, sig[i * self.rows : (i + 1) * self.rows]) for i in range(self.bands)]

    def add(self, key: str, sig: tuple[int, ...]) -> None:
        for bk in self._keys(sig):
            self._buckets[bk].append(key)

    def candidates(self, sig: tuple[int, ...]) -> set[str]:
        out: set[str] = set()
        for bk in self._keys(sig):
            out.update(self._buckets.get(bk, ()))
        return out


class Deduplicator:
    """Streams tenders through exact and near-duplicate checks.

    `seed()` loads tenders from earlier runs (open, canonical ones from the DB) so a
    re-listing is caught even when the original was seen days ago.
    """

    def __init__(
        self,
        threshold: float = 0.75,
        date_tolerance_days: int = 1,
        bands: int = 16,
        rows: int = 4,
        seed: int = 1,
    ) -> None:
        self.threshold = threshold
        self.date_tolerance_days = date_tolerance_days
        self._hasher = MinHasher(num_perm=bands * rows, seed=seed)
        self._index = LshIndex(bands=bands, rows=rows)
        self._by_fp: dict[str, list[str]] = defaultdict(list)
        self._known: dict[str, KnownTender] = {}

    def __len__(self) -> int:
        return len(self._known)

    def seed(self, known: KnownTender) -> None:
        if known.key in self._known:
            return
        self._known[known.key] = known
        self._by_fp[known.fingerprint].append(known.key)
        self._index.add(known.key, self._hasher.signature(known.tokens))

    def check(
        self, key: str, portal: str, fp: str, toks: frozenset[str], closing: date
    ) -> tuple[str | None, float | None]:
        """Return (canonical key, Jaccard similarity) if a duplicate; otherwise register it."""
        for other_key in self._by_fp.get(fp, ()):
            if other_key != key and self._known[other_key].portal != portal:
                return other_key, 1.0
        best: tuple[float, str] | None = None
        for other_key in self._index.candidates(self._hasher.signature(toks)):
            other = self._known[other_key]
            if other_key == key or other.portal == portal:
                continue
            if abs((other.closing - closing).days) > self.date_tolerance_days:
                continue
            sim = jaccard(toks, other.tokens)
            if sim >= self.threshold and (best is None or (sim, other_key) > best):
                best = (sim, other_key)
        if best is not None:
            return best[1], round(best[0], 3)
        self.seed(KnownTender(key, portal, fp, toks, closing))
        return None, None
