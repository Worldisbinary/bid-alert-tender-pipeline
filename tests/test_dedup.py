from datetime import UTC, date, datetime

import pytest

from bidalert.pipeline import screen
from bidalert.processing.dedup import (
    Deduplicator,
    LshIndex,
    MinHasher,
    estimated_jaccard,
    fingerprint,
    jaccard,
    token_set,
)
from bidalert.sources.synthetic import SyntheticAdapter

D = date(2026, 10, 20)


def test_fingerprint_ignores_case_and_punctuation():
    a = fingerprint("Supply of Lab-Glassware", "CSIR", D)
    b = fingerprint("SUPPLY OF LAB GLASSWARE", "csir.", D)
    assert a == b
    assert a != fingerprint("Supply of lab glassware", "CSIR", date(2026, 10, 21))


def test_minhash_estimates_jaccard():
    hasher = MinHasher(num_perm=256, seed=3)
    a = frozenset(f"w{i}" for i in range(40))
    b = frozenset(f"w{i}" for i in range(10, 50))  # J = 30/50 = 0.6
    est = estimated_jaccard(hasher.signature(a), hasher.signature(b))
    assert abs(est - jaccard(a, b)) < 0.1


def test_lsh_index_rejects_wrong_signature_length():
    with pytest.raises(ValueError):
        LshIndex(bands=4, rows=4).add("k", (1, 2, 3))


def check(d, key, portal, title, org="DRDO", closing=D):
    return d.check(key, portal, fingerprint(title, org, closing), token_set(title, org), closing)


def test_exact_and_near_duplicates_across_portals():
    d = Deduplicator()
    assert check(d, "a", "GeM", "Supply of laboratory reagents - DRDO, Uttar Pradesh") == (
        None,
        None,
    )
    assert check(d, "b", "CPPP", "SUPPLY OF LABORATORY REAGENTS - DRDO, UTTAR PRADESH") == (
        "a",
        1.0,
    )
    key, sim = check(
        d, "c", "eProcure", "Procurement of supply of laboratory reagents: DRDO, Uttar Pradesh"
    )
    assert key == "a" and sim is not None and 0.75 <= sim < 1


def test_same_portal_is_never_a_duplicate():
    d = Deduplicator()
    check(d, "a", "CPPP", "Supply of medicines lot 1")
    assert check(d, "b", "CPPP", "Supply of medicines lot 1") == (None, None)


def test_different_items_and_dates_are_not_merged():
    d = Deduplicator()
    check(d, "a", "GeM", "Supply of laboratory reagents - DRDO, Uttar Pradesh")
    assert check(d, "b", "CPPP", "Supply of laboratory glassware - DRDO, Uttar Pradesh") == (
        None,
        None,
    )
    assert check(
        d,
        "c",
        "eProcure",
        "Supply of laboratory reagents - DRDO, Uttar Pradesh",
        closing=date(2026, 11, 30),
    ) == (None, None)


def test_recall_and_precision_on_synthetic_relistings(profile):
    now = datetime(2026, 10, 1, tzinfo=UTC)
    tenders = list(SyntheticAdapter(now=now).fetch())
    truth = {t.source_tender_id for t in tenders if t.raw.get("synthetic_duplicate_of")}
    found = {s.tender.source_tender_id for s in screen(tenders, profile, now) if s.duplicate_of}
    assert len(truth) == 20
    assert found == truth  # 100% recall, 0 false positives on this generator
