"""A polite HTTP client for public portals.

Politeness rules, all enforced here so adapters cannot forget them:

* identify ourselves with a descriptive User-Agent that links to this repo;
* honour robots.txt (an unreachable robots.txt means "do not crawl", RFC 9309 s2.3.1.4;
  a 404 means "no rules");
* at most one request per `min_interval_s` per host (default 3 s);
* retry only transient failures (connection errors, 429, 5xx) with capped
  exponential backoff and *full jitter*, and honour `Retry-After`;
* cache responses on disk for `cache_ttl_s` so re-runs and debugging do not
  hit the portal again.

It never submits forms, solves CAPTCHAs or sends credentials.
"""

from __future__ import annotations

import hashlib
import logging
import random
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

log = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class FetchError(RuntimeError):
    pass


class RobotsDisallowed(FetchError):
    pass


def backoff_delay(attempt: int, base: float, cap: float, rng: random.Random) -> float:
    """Full-jitter exponential backoff: uniform(0, min(cap, base * 2**attempt)).

    Jitter spreads retries from many clients so they do not hammer a recovering
    server in lock-step (AWS Architecture Blog, "Exponential Backoff and Jitter").
    """
    return rng.uniform(0.0, min(cap, base * (2**attempt)))


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None  # HTTP-date form; fall back to our own backoff


class RateLimiter:
    """Minimum interval between requests to the same host."""

    def __init__(
        self,
        min_interval_s: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.min_interval_s = min_interval_s
        self._clock = clock
        self._sleep = sleep
        self._last: dict[str, float] = {}

    def wait(self, host: str) -> float:
        now = self._clock()
        last = self._last.get(host)
        waited = 0.0
        if last is not None:
            waited = max(0.0, last + self.min_interval_s - now)
            if waited:
                self._sleep(waited)
        self._last[host] = self._clock()
        return waited


class PoliteHttpClient:
    def __init__(
        self,
        user_agent: str,
        min_interval_s: float = 3.0,
        max_retries: int = 4,
        timeout_s: float = 30.0,
        cache_dir: Path | None = None,
        cache_ttl_s: int = 3600,
        backoff_base_s: float = 2.0,
        backoff_cap_s: float = 60.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        rng: random.Random | None = None,
    ) -> None:
        self.user_agent = user_agent
        self.max_retries = max_retries
        self.cache_dir = cache_dir
        self.cache_ttl_s = cache_ttl_s
        self.backoff_base_s = backoff_base_s
        self.backoff_cap_s = backoff_cap_s
        self._sleep = sleep
        self._wall_clock = wall_clock
        self._rng = rng or random.Random()
        self._limiter = RateLimiter(min_interval_s, clock=clock, sleep=sleep)
        self._robots: dict[str, RobotFileParser] = {}
        self.requests_made = 0
        self.cache_hits = 0
        self._client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept": "text/html,application/xhtml+xml"},
            timeout=timeout_s,
            follow_redirects=True,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PoliteHttpClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- robots.txt -------------------------------------------------------------
    def _robots_for(self, robots_url: str) -> RobotFileParser:
        parser = self._robots.get(robots_url)
        if parser is not None:
            return parser
        parser = RobotFileParser(robots_url)
        disallow_all = ["User-agent: *", "Disallow: /"]
        try:
            resp = self._request(robots_url)
        except FetchError:
            parser.parse(disallow_all)  # unreachable -> assume complete disallow
        else:
            if resp.status_code in (401, 403):
                parser.parse(disallow_all)
            elif 400 <= resp.status_code < 500:
                parser.parse([])  # 404 etc. -> no rules, everything allowed
            else:
                parser.parse(resp.text.splitlines())
        self._robots[robots_url] = parser
        return parser

    def allowed(self, url: str, robots_url: str | None = None) -> bool:
        if robots_url is None:
            parts = urlsplit(url)
            robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        return self._robots_for(robots_url).can_fetch(self.user_agent, url)

    # -- cache ------------------------------------------------------------------
    def _cache_path(self, url: str) -> Path | None:
        if self.cache_dir is None:
            return None
        return self.cache_dir / (hashlib.sha256(url.encode()).hexdigest() + ".html")

    def _cache_get(self, url: str) -> str | None:
        path = self._cache_path(url)
        if path is None or not path.exists():
            return None
        if self._wall_clock() - path.stat().st_mtime > self.cache_ttl_s:
            return None
        return path.read_text("utf-8")

    def _cache_put(self, url: str, text: str) -> None:
        path = self._cache_path(url)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text, "utf-8")
        tmp.replace(path)  # atomic rename: readers never see a half-written file

    # -- fetching ---------------------------------------------------------------
    def _request(self, url: str) -> httpx.Response:
        host = urlsplit(url).netloc
        last_error: str = ""
        for attempt in range(self.max_retries + 1):
            self._limiter.wait(host)
            self.requests_made += 1
            try:
                resp = self._client.get(url)
            except httpx.TransportError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                retry_after = None
            else:
                if resp.status_code not in RETRYABLE_STATUS:
                    return resp
                last_error = f"HTTP {resp.status_code}"
                retry_after = _retry_after_seconds(resp.headers.get("Retry-After"))
            if attempt == self.max_retries:
                break
            delay = backoff_delay(attempt, self.backoff_base_s, self.backoff_cap_s, self._rng)
            if retry_after is not None:
                delay = max(delay, min(retry_after, self.backoff_cap_s))
            log.warning(
                "retrying request",
                extra={
                    "url": url,
                    "attempt": attempt + 1,
                    "error": last_error,
                    "delay_s": round(delay, 2),
                },
            )
            self._sleep(delay)
        raise FetchError(f"giving up on {url} after {self.max_retries + 1} attempts: {last_error}")

    def get_text(self, url: str, robots_url: str | None = None) -> str:
        cached = self._cache_get(url)
        if cached is not None:
            self.cache_hits += 1
            return cached
        if not self.allowed(url, robots_url):
            raise RobotsDisallowed(f"robots.txt disallows {url}")
        resp = self._request(url)
        if resp.status_code != 200:
            raise FetchError(f"HTTP {resp.status_code} for {url}")
        text = resp.text
        self._cache_put(url, text)
        return text
