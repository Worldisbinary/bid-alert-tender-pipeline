"""Text normalisation used by keyword matching, fingerprinting and MinHash.

Portals format the same tender differently ("SUPPLY OF  Lab-Glassware" vs
"Supply of lab glassware"). Every comparison runs on the normalised form:
NFKC unicode folding, lowercase, punctuation to spaces, single spaces.
"""

from __future__ import annotations

import re
import unicodedata

_NON_ALNUM = re.compile(r"[^0-9a-z]+")

# Words that carry no signal for "is this the same tender".
STOPWORDS = frozenset(
    (
        "a", "an", "and", "as", "at", "by", "etc", "for", "from", "in", "item", "items",
        "no", "nos", "of", "on", "or", "per", "sl", "the", "to", "with",
    )
)  # fmt: skip


def normalise(text: str | None) -> str:
    if not text:
        return ""
    folded = unicodedata.normalize("NFKC", text).lower()
    return _NON_ALNUM.sub(" ", folded).strip()


def tokens(text: str | None, drop_stopwords: bool = True) -> list[str]:
    words = normalise(text).split()
    if drop_stopwords:
        return [w for w in words if w not in STOPWORDS]
    return words


def clean_display(text: str | None) -> str:
    """Collapse whitespace for display without changing case or wording."""
    if not text:
        return ""
    return " ".join(text.split())
