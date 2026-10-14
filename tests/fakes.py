"""Test doubles: a scripted HTTP session, an in-memory credential store, a fake clock."""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from requests.structures import CaseInsensitiveDict

from mcclaimer.api import AVAILABLE, DUPLICATE
from mcclaimer.errors import ApiError


class FakeResponse:
    def __init__(self, status_code: int = 200, json_data: Any = None, text: str | None = None,
                 headers: dict | None = None) -> None:
        self.status_code = status_code
        self.headers = CaseInsensitiveDict(headers or {})
        if text is None:
            text = "" if json_data is None else json.dumps(json_data)
        self.text = text
        self.content = text.encode("utf-8")

    def json(self) -> Any:
        return json.loads(self.text)


@dataclass
class RecordedCall:
    method: str
    url: str
    kwargs: dict


@dataclass
class FakeSession:
    """Routes requests to scripted responses. Each route is a list consumed in order;
    the last response repeats. A route value may also be a callable or an exception."""

    routes: list[tuple[str, re.Pattern, list]] = field(default_factory=list)
    calls: list[RecordedCall] = field(default_factory=list)

    def add(self, method: str, url_pattern: str, *responses: Any) -> "FakeSession":
        self.routes.append((method.upper(), re.compile(url_pattern), list(responses)))
        return self

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(RecordedCall(method.upper(), url, kwargs))
        for route_method, pattern, responses in self.routes:
            if route_method == method.upper() and pattern.search(url):
                response = responses.pop(0) if len(responses) > 1 else responses[0]
                if isinstance(response, BaseException):
                    raise response
                if callable(response):
                    return response(url, kwargs)
                return response
        raise AssertionError(f"Unexpected request: {method} {url}")

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        return self.request("POST", url, **kwargs)

    def calls_to(self, method: str, url_fragment: str) -> list[RecordedCall]:
        return [call for call in self.calls if call.method == method and url_fragment in call.url]


class MemoryStore:
    def __init__(self, data: dict | None = None) -> None:
        self.data = dict(data) if data else None
        self.saves = 0

    def save(self, data: dict) -> None:
        self.data = dict(data)
        self.saves += 1

    def load(self) -> dict | None:
        return dict(self.data) if self.data else None

    def clear(self) -> None:
        self.data = None


class FakeClock:
    """Monotonic seconds plus a matching wall-clock datetime."""

    def __init__(self, start: float = 1000.0) -> None:
        self.value = start
        self._epoch = datetime(2026, 1, 1, 12, 0, 0)

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds

    def now(self) -> datetime:
        return self._epoch + timedelta(seconds=self.value)


class InstantEvent(threading.Event):
    """An Event whose wait() returns immediately, so run() loops don't sleep in tests.
    Sets itself after `max_waits` waits to stop loops that would otherwise run forever."""

    def __init__(self, max_waits: int = 1000, clock: FakeClock | None = None) -> None:
        super().__init__()
        self.max_waits = max_waits
        self.waits: list[float] = []
        self._clock = clock

    def wait(self, timeout: float | None = None) -> bool:
        if timeout is not None:
            self.waits.append(timeout)
            if self._clock is not None:
                self._clock.advance(timeout)
        if len(self.waits) >= self.max_waits:
            self.set()
        return self.is_set()


class ScriptedApi:
    """Minimal MonitorApi whose responses are scripted per test."""

    def __init__(self, availability: list[Any] | None = None, claim: list[Any] | None = None,
                 profiles: list[Any] | None = None, name_change_allowed: bool = True,
                 profile_name: str = "OldName") -> None:
        self.availability = list(availability or [DUPLICATE])
        self.claim = list(claim or [None])
        self.profiles = list(profiles) if profiles is not None else None
        self.name_change_allowed = name_change_allowed
        self.profile_name = profile_name
        self.availability_calls = 0
        self.change_calls: list[str] = []
        self.profile_calls = 0

    @staticmethod
    def _next(script: list[Any]) -> Any:
        item = script.pop(0) if len(script) > 1 else script[0]
        if isinstance(item, BaseException):
            raise item
        return item

    def check_availability(self, name: str) -> str:
        self.availability_calls += 1
        return self._next(self.availability)

    def change_name(self, name: str) -> dict:
        self.change_calls.append(name)
        result = self._next(self.claim)
        if result is None:
            self.profile_name = name
            return {"id": "abc", "name": name}
        return result

    def get_profile(self) -> dict | None:
        self.profile_calls += 1
        if self.profiles is not None:
            return self._next(self.profiles)
        return {"id": "abc", "name": self.profile_name}

    def get_java_entitlements(self) -> list[str]:
        return ["product_minecraft", "game_minecraft"]

    def get_name_change_info(self) -> dict:
        return {"changedAt": "2025-01-01T00:00:00Z", "nameChangeAllowed": self.name_change_allowed}


def http_error(status: int, body: str = "", retry_after: float | None = None) -> ApiError:
    from mcclaimer.errors import RateLimitedError, TransientError, UnauthorizedError

    if status == 429:
        return RateLimitedError("rate limited", status, body, retry_after)
    if status == 401:
        return UnauthorizedError("unauthorized", status, body)
    if status >= 500:
        return TransientError("server error", status, body)
    return ApiError(f"HTTP {status}", status, body)


__all__ = [
    "AVAILABLE", "DUPLICATE", "FakeClock", "FakeResponse", "FakeSession", "InstantEvent",
    "MemoryStore", "ScriptedApi", "http_error",
]
