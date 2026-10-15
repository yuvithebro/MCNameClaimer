"""Rate-limit helpers: Retry-After parsing, exponential backoff and a local request budget.

The numbers come from the community-maintained Mojang API documentation
(https://minecraft.wiki/w/Mojang_API), not from an official Mojang source.
Mojang does not publish a third-party contract for these endpoints, so the real
limits may differ; the app stays well below the community figures and always
obeys HTTP 429 and Retry-After from the server.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable

# Community documentation (not official): the name availability endpoint "has a
# per-account ratelimit of 20 requests per 5 minutes".
AVAILABILITY_LIMIT_REQUESTS = 20
AVAILABILITY_LIMIT_WINDOW_SECONDS = 300

# Our own hard budget is 80% of that, so restarts or clock jitter can't tip us over.
AVAILABILITY_BUDGET_REQUESTS = 16

# 20 s between checks = 15 checks per 5 minutes = 75% of the community-documented limit.
MIN_LIVE_INTERVAL_SECONDS = 20
DEFAULT_INTERVAL_SECONDS = 30
MIN_SIMULATION_INTERVAL_SECONDS = 1
MAX_INTERVAL_SECONDS = 3600

MAX_BACKOFF_SECONDS = 900
# A Retry-After longer than a day is almost certainly a bug; cap it so the app
# doesn't appear frozen forever.
MAX_RETRY_AFTER_SECONDS = 24 * 3600
_MAX_BACKOFF_EXPONENT = 16


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Parse a Retry-After header (delta-seconds or HTTP-date) into seconds from now."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        seconds = float(text)
    except ValueError:
        try:
            when = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            return None
        if when is None:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = (when - (now or datetime.now(timezone.utc))).total_seconds()
    if not math.isfinite(seconds):
        return None
    return min(max(seconds, 0.0), MAX_RETRY_AFTER_SECONDS)


class Backoff:
    """Exponential backoff that never waits less than the normal check interval.

    Each consecutive failure doubles the wait (base x 2, x 4, ...) up to
    `max_seconds`. A server-provided Retry-After is always honoured, even when
    it is longer than our cap.
    """

    def __init__(self, base_seconds: float, max_seconds: float = MAX_BACKOFF_SECONDS) -> None:
        self.base_seconds = base_seconds
        self.max_seconds = max(max_seconds, base_seconds)
        self.consecutive_failures = 0

    def reset(self) -> None:
        self.consecutive_failures = 0

    def next_delay(self, retry_after: float | None = None) -> float:
        self.consecutive_failures += 1
        exponent = min(self.consecutive_failures, _MAX_BACKOFF_EXPONENT)
        exponential = min(self.max_seconds, self.base_seconds * (2**exponent))
        if retry_after is None:
            return exponential
        return max(retry_after, exponential)


class SlidingWindowLimiter:
    """Allows at most `max_requests` within any rolling `window_seconds`.

    Kept for the lifetime of the app (not per monitoring run), so pressing
    Stop/Start repeatedly can never burst past the community-documented limit.
    """

    def __init__(
        self,
        max_requests: int,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._clock = clock
        self._timestamps: deque[float] = deque()
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        while self._timestamps and now - self._timestamps[0] >= self.window_seconds:
            self._timestamps.popleft()

    def wait_time(self) -> float:
        """Seconds until another request fits in the budget (0 when one fits now)."""
        with self._lock:
            now = self._clock()
            self._prune(now)
            if len(self._timestamps) < self.max_requests:
                return 0.0
            return max(0.0, self._timestamps[0] + self.window_seconds - now)

    def record(self) -> None:
        with self._lock:
            now = self._clock()
            self._prune(now)
            self._timestamps.append(now)
