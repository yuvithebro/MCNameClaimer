"""Offline stand-in for MinecraftServicesClient.

It has the same methods and raises the same exceptions, so the monitor, the
claim logic and the GUI run exactly the code paths they use in Live mode,
without any network request and without touching your real account.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .api import AVAILABLE, DUPLICATE, NOT_ALLOWED
from .errors import ApiError, RateLimitedError, TransientError


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    description: str


SCENARIOS: dict[str, Scenario] = {
    scenario.key: scenario
    for scenario in (
        Scenario(
            "available_after_checks",
            "Name frees up, claim succeeds",
            "Taken for 3 checks, AVAILABLE on the 4th. The name-change request succeeds and the profile check confirms it.",
        ),
        Scenario(
            "rate_limited",
            "Rate limited (HTTP 429), then success",
            "The 2nd check returns HTTP 429 with Retry-After: 5. The app backs off, then claims the name.",
        ),
        Scenario(
            "claim_race_lost",
            "Someone else claims it first",
            "The name shows AVAILABLE, but the name change gets HTTP 403 DUPLICATE. Monitoring continues; stop it yourself.",
        ),
        Scenario(
            "ambiguous_claim",
            "Server error, but the change went through",
            "The name change returns HTTP 503. The app checks your profile instead of retrying, and finds the name was claimed.",
        ),
        Scenario(
            "not_allowed",
            "Blocked name (NOT_ALLOWED)",
            "Mojang reports the name is NOT_ALLOWED. Monitoring stops with an explanation.",
        ),
        Scenario(
            "cooldown",
            "Account on 30-day name-change cooldown",
            "The account changed its name 10 days ago, so Start is refused before any availability check.",
        ),
        Scenario(
            "never_available",
            "Name never frees up",
            "Always taken. Use this to watch the schedule, timestamps and the Stop button.",
        ),
    )
}
DEFAULT_SCENARIO = "available_after_checks"

SIMULATED_PROFILE_ID = "5f3a1c2e9b8d4e7fa6c1d2e3f4a5b6c7"
SIMULATED_STARTING_NAME = "SimPlayer"
_RACE_LOST_BODY = (
    '{"path":"/minecraft/profile/name/{name}","errorType":"FORBIDDEN","error":"FORBIDDEN",'
    '"details":{"status":"DUPLICATE"},"errorMessage":"Could not change name for profile",'
    '"developerMessage":"Could not change name for profile"}'
)
_RATE_LIMITED_ON_CHECK = 2
_SIMULATED_RETRY_AFTER_SECONDS = 5.0


class SimulatedMinecraftAPI:
    """Scripted fake of the Minecraft Services API. Makes no network requests."""

    def __init__(
        self,
        scenario: str = DEFAULT_SCENARIO,
        starting_name: str = SIMULATED_STARTING_NAME,
        available_on_check: int = 4,
    ) -> None:
        if scenario not in SCENARIOS:
            raise ValueError(f"Unknown simulation scenario: {scenario}")
        self.scenario = scenario
        self.profile_name = starting_name
        self.available_on_check = available_on_check
        self.check_count = 0
        self.change_requests = 0
        self._taken_by_someone_else = False

    def check_availability(self, name: str) -> str:
        self.check_count += 1
        if self.scenario == "not_allowed":
            return NOT_ALLOWED
        if self.scenario == "never_available":
            return DUPLICATE
        if self.scenario == "rate_limited" and self.check_count == _RATE_LIMITED_ON_CHECK:
            raise RateLimitedError(
                "Simulated availability check: rate limited (HTTP 429)",
                429,
                '{"error":"TOO_MANY_REQUESTS"}',
                retry_after=_SIMULATED_RETRY_AFTER_SECONDS,
            )
        if self._taken_by_someone_else or self.profile_name.casefold() == name.casefold():
            return DUPLICATE
        return AVAILABLE if self.check_count >= self.available_on_check else DUPLICATE

    def get_profile(self) -> dict | None:
        return {"id": SIMULATED_PROFILE_ID, "name": self.profile_name, "skins": [], "capes": []}

    def get_name_change_info(self) -> dict:
        now = datetime.now(timezone.utc)
        changed_days_ago = 10 if self.scenario == "cooldown" else 400
        return {
            "changedAt": (now - timedelta(days=changed_days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "createdAt": (now - timedelta(days=1000)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "nameChangeAllowed": self.scenario != "cooldown",
        }

    def get_java_entitlements(self) -> list[str]:
        return ["product_minecraft", "game_minecraft"]

    def change_name(self, name: str) -> dict:
        self.change_requests += 1
        if self.scenario == "claim_race_lost":
            self._taken_by_someone_else = True
            raise ApiError("Simulated name change: HTTP 403", 403, _RACE_LOST_BODY.replace("{name}", name))
        if self.scenario == "ambiguous_claim":
            self.profile_name = name  # the change "went through" even though the response failed
            raise TransientError("Simulated name change: server error (HTTP 503)", 503, '{"error":"Service Unavailable"}')
        self.profile_name = name
        return self.get_profile()
