from datetime import datetime, timezone

from fakes import FakeClock

from mcclaimer.ratelimit import (
    AVAILABILITY_BUDGET_REQUESTS,
    AVAILABILITY_LIMIT_REQUESTS,
    AVAILABILITY_LIMIT_WINDOW_SECONDS,
    MAX_RETRY_AFTER_SECONDS,
    MIN_LIVE_INTERVAL_SECONDS,
    Backoff,
    SlidingWindowLimiter,
    parse_retry_after,
)


class TestParseRetryAfter:
    def test_delta_seconds(self):
        assert parse_retry_after("120") == 120.0

    def test_http_date(self):
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        assert parse_retry_after("Thu, 01 Jan 2026 12:01:30 GMT", now=now) == 90.0

    def test_past_date_means_zero(self):
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        assert parse_retry_after("Thu, 01 Jan 2026 11:00:00 GMT", now=now) == 0.0

    def test_missing_or_garbage_is_none(self):
        assert parse_retry_after(None) is None
        assert parse_retry_after("") is None
        assert parse_retry_after("soon") is None
        assert parse_retry_after("nan") is None

    def test_negative_clamped_and_huge_capped(self):
        assert parse_retry_after("-5") == 0.0
        assert parse_retry_after("99999999") == MAX_RETRY_AFTER_SECONDS


class TestBackoff:
    def test_doubles_from_the_interval_and_caps(self):
        backoff = Backoff(base_seconds=30, max_seconds=200)
        assert [backoff.next_delay() for _ in range(4)] == [60, 120, 200, 200]

    def test_retry_after_wins_when_longer(self):
        backoff = Backoff(base_seconds=30, max_seconds=900)
        assert backoff.next_delay(retry_after=600) == 600

    def test_retry_after_longer_than_cap_is_still_honoured(self):
        backoff = Backoff(base_seconds=30, max_seconds=900)
        assert backoff.next_delay(retry_after=3600) == 3600

    def test_short_retry_after_never_shortens_the_wait(self):
        backoff = Backoff(base_seconds=30)
        assert backoff.next_delay(retry_after=1) == 60

    def test_reset_starts_over(self):
        backoff = Backoff(base_seconds=10, max_seconds=1000)
        backoff.next_delay()
        backoff.next_delay()
        backoff.reset()
        assert backoff.next_delay() == 20

    def test_many_failures_do_not_overflow(self):
        backoff = Backoff(base_seconds=30, max_seconds=900)
        for _ in range(500):
            delay = backoff.next_delay()
        assert delay == 900


class TestSlidingWindowLimiter:
    def test_blocks_after_budget_until_oldest_expires(self):
        clock = FakeClock()
        limiter = SlidingWindowLimiter(3, 100, clock=clock)
        for _ in range(3):
            assert limiter.wait_time() == 0
            limiter.record()
            clock.advance(10)
        assert limiter.wait_time() == 70  # first request at t=0 leaves the window at t=100
        clock.advance(70)
        assert limiter.wait_time() == 0


def test_live_defaults_stay_under_documented_limit():
    checks_per_window = AVAILABILITY_LIMIT_WINDOW_SECONDS / MIN_LIVE_INTERVAL_SECONDS
    assert checks_per_window < AVAILABILITY_LIMIT_REQUESTS
    assert AVAILABILITY_BUDGET_REQUESTS < AVAILABILITY_LIMIT_REQUESTS
