"""Aho-Corasick automaton for matching many keywords in one pass.

Why: the watchlist has ~80 keywords and ~15 exclusions. Checking each keyword
with `in` costs O(n * m) per title (n = text length, m = total keyword length).
Aho-Corasick builds a trie of all keywords plus "failure links" (like KMP's
prefix function, generalised to a trie) and then scans the text once:

    build:  O(m * sigma) time, O(m) states   (sigma = alphabet seen, dict-backed)
    search: O(n + z)                          (z = number of matches reported)

The automaton runs over *normalised* text (see text.normalise), so matching is
case- and punctuation-insensitive. `whole_words=True` rejects hits inside a
longer word ("acid" must not match "placid").
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass

from bidalert.processing.text import normalise


@dataclass(frozen=True)
class Match:
    start: int  # index into the normalised text
    end: int  # exclusive
    keyword: str  # normalised keyword


class AhoCorasick:
    def __init__(self, keywords: Iterable[str]) -> None:
        # State 0 is the root. goto[s] maps a character to the next state.
        self._goto: list[dict[str, int]] = [{}]
        self._fail: list[int] = [0]
        # out[s]: keywords that end exactly at state s.
        self._out: list[list[str]] = [[]]
        # dict_link[s]: nearest state on the failure chain that has output. Following
        # these instead of the whole failure chain keeps reporting O(z).
        self._dict_link: list[int] = [0]
        self.keywords: tuple[str, ...] = tuple(
            sorted({k for k in (normalise(w) for w in keywords) if k})
        )
        for word in self.keywords:
            self._insert(word)
        self._build_links()

    def _insert(self, word: str) -> None:
        state = 0
        for ch in word:
            nxt = self._goto[state].get(ch)
            if nxt is None:
                nxt = len(self._goto)
                self._goto.append({})
                self._fail.append(0)
                self._out.append([])
                self._dict_link.append(0)
                self._goto[state][ch] = nxt
            state = nxt
        self._out[state].append(word)

    def _build_links(self) -> None:
        # BFS so that every state's failure target (a shorter suffix) is final first.
        queue: deque[int] = deque()
        for child in self._goto[0].values():
            self._fail[child] = 0
            queue.append(child)
        while queue:
            state = queue.popleft()
            for ch, child in self._goto[state].items():
                queue.append(child)
                f = self._fail[state]
                while f and ch not in self._goto[f]:
                    f = self._fail[f]
                target = self._goto[f].get(ch, 0)
                self._fail[child] = target if target != child else 0
                fc = self._fail[child]
                self._dict_link[child] = fc if self._out[fc] else self._dict_link[fc]

    @property
    def state_count(self) -> int:
        return len(self._goto)

    def iter_matches(self, normalised_text: str, whole_words: bool = True) -> list[Match]:
        text = normalised_text
        matches: list[Match] = []
        state = 0
        for i, ch in enumerate(text):
            while state and ch not in self._goto[state]:
                state = self._fail[state]
            state = self._goto[state].get(ch, 0)
            s = state if self._out[state] else self._dict_link[state]
            while s:
                for word in self._out[s]:
                    start = i - len(word) + 1
                    end = i + 1
                    if not whole_words or _is_word_boundary(text, start, end):
                        matches.append(Match(start, end, word))
                s = self._dict_link[s]
        return matches

    def search(self, text: str, whole_words: bool = True) -> list[Match]:
        """Normalise `text` and return every keyword occurrence, in text order."""
        found = self.iter_matches(normalise(text), whole_words=whole_words)
        return sorted(found, key=lambda m: (m.start, -m.end))

    def found_keywords(self, text: str, whole_words: bool = True) -> tuple[str, ...]:
        """Distinct keywords present in `text`, sorted."""
        return tuple(sorted({m.keyword for m in self.search(text, whole_words)}))


def _is_word_boundary(text: str, start: int, end: int) -> bool:
    # Normalised text only contains [0-9a-z ] so a boundary is a space or an edge.
    before_ok = start == 0 or text[start - 1] == " "
    after_ok = end == len(text) or text[end] == " "
    return before_ok and after_ok


class KeywordClassifier:
    """Maps watchlist hits to categories, and flags exclusion hits.

    One automaton holds both watchlist and exclusion keywords so each title is
    scanned exactly once.
    """

    def __init__(self, watchlist: dict[str, tuple[str, ...]], exclusions: Iterable[str]) -> None:
        self._category_of: dict[str, str] = {}
        for category, words in watchlist.items():
            for w in words:
                self._category_of.setdefault(normalise(w), category)
        self._exclusions = frozenset(normalise(w) for w in exclusions if normalise(w))
        self.automaton = AhoCorasick([*self._category_of, *self._exclusions])

    def classify(self, title: str) -> tuple[tuple[str, ...], str | None, tuple[str, ...]]:
        """Return (watchlist keywords hit, inferred category, exclusion keywords hit).

        The inferred category is the one with the most distinct keyword hits; ties go
        to the category whose first hit appears earliest in the title.
        """
        hits = self.automaton.search(title)
        watch: list[str] = []
        excluded: list[str] = []
        counts: dict[str, int] = {}
        first_pos: dict[str, int] = {}
        for m in hits:
            if m.keyword in self._exclusions:
                if m.keyword not in excluded:
                    excluded.append(m.keyword)
                continue
            if m.keyword in watch:
                continue
            watch.append(m.keyword)
            cat = self._category_of[m.keyword]
            counts[cat] = counts.get(cat, 0) + 1
            first_pos.setdefault(cat, m.start)
        category = None
        if counts:
            category = min(counts, key=lambda c: (-counts[c], first_pos[c]))
        return tuple(sorted(watch)), category, tuple(sorted(excluded))
