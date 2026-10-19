import random

import httpx
import pytest

from bidalert.sources.http import (
    FetchError,
    PoliteHttpClient,
    RateLimiter,
    RobotsDisallowed,
    backoff_delay,
)

ROBOTS = "User-agent: *\nDisallow: /private/\n"


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


def make_client(handler, tmp_path=None, **kw):
    clock = FakeClock()
    client = PoliteHttpClient(
        user_agent="test-agent/1.0 (+https://example.org)",
        min_interval_s=3.0,
        transport=httpx.MockTransport(handler),
        sleep=clock.sleep,
        clock=clock,
        rng=random.Random(0),
        cache_dir=tmp_path,
        **kw,
    )
    return client, clock


def test_backoff_is_bounded_full_jitter():
    rng = random.Random(1)
    for attempt in range(10):
        d = backoff_delay(attempt, base=2.0, cap=60.0, rng=rng)
        assert 0 <= d <= min(60.0, 2.0 * 2**attempt)


def test_rate_limiter_spaces_requests_per_host():
    clock = FakeClock()
    rl = RateLimiter(3.0, clock=clock, sleep=clock.sleep)
    rl.wait("a")
    clock.t += 1.0
    assert rl.wait("a") == pytest.approx(2.0)
    assert rl.wait("b") == 0.0


def test_retries_transient_errors_then_succeeds_and_sends_user_agent():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS)
        calls.append(req.headers["user-agent"])
        if len(calls) < 3:
            return httpx.Response(503)
        return httpx.Response(200, text="ok")

    client, clock = make_client(handler)
    assert client.get_text("https://portal.test/list") == "ok"
    assert len(calls) == 3 and all(ua.startswith("test-agent/1.0") for ua in calls)
    assert clock.sleeps  # rate limiting and backoff both slept


def test_honours_retry_after():
    n = {"i": 0}

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        n["i"] += 1
        return (
            httpx.Response(429, headers={"Retry-After": "17"})
            if n["i"] == 1
            else httpx.Response(200, text="ok")
        )

    client, clock = make_client(handler)
    client.get_text("https://portal.test/x")
    assert 17 in clock.sleeps


def test_gives_up_after_max_retries():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        raise httpx.ConnectError("boom")

    client, _ = make_client(handler, max_retries=2)
    with pytest.raises(FetchError, match="3 attempts"):
        client.get_text("https://portal.test/x")


def test_does_not_retry_client_errors():
    hits = []

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        hits.append(1)
        return httpx.Response(404)

    client, _ = make_client(handler)
    with pytest.raises(FetchError, match="HTTP 404"):
        client.get_text("https://portal.test/x")
    assert len(hits) == 1


def test_robots_disallow_is_respected():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS)
        raise AssertionError("must not fetch a disallowed URL")

    client, _ = make_client(handler)
    with pytest.raises(RobotsDisallowed):
        client.get_text("https://portal.test/private/page")


def test_unreachable_robots_means_disallow():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(500)
        raise AssertionError("must not fetch")

    client, _ = make_client(handler, max_retries=0)
    assert client.allowed("https://portal.test/page") is False


def test_cache_avoids_second_request(tmp_path):
    hits = []

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        hits.append(1)
        return httpx.Response(200, text="cached body")

    client, _ = make_client(handler, tmp_path=tmp_path)
    assert client.get_text("https://portal.test/a") == "cached body"
    assert client.get_text("https://portal.test/a") == "cached body"
    assert len(hits) == 1 and client.cache_hits == 1
