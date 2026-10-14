"""The monitoring loop: check availability on a schedule, claim once, verify.

Safety rules enforced here:
- One name-change request per AVAILABLE result, and at most
  `max_claim_attempts` per monitoring run.
- HTTP 429, 5xx and network errors back off exponentially and always honour
  the server's Retry-After.
- An unclear claim result (timeout, 5xx) is resolved by reading the profile,
  never by sending the name change again.
- A local sliding-window budget keeps availability checks below Mojang's
  documented per-account limit, even across Stop/Start.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum
from typing import Callable, Protocol

from .account import AccountApi, ensure_can_claim, verify_account
from .api import AVAILABLE, DUPLICATE, NOT_ALLOWED
from .errors import (
    AccountNotEligibleError,
    ApiError,
    AuthError,
    RateLimitedError,
    TransientError,
    UnauthorizedError,
)
from .logging_setup import LOGGER_NAME
from .ratelimit import MAX_BACKOFF_SECONDS, Backoff, SlidingWindowLimiter

logger = logging.getLogger(f"{LOGGER_NAME}.monitor")

PROFILE_PAGE_URL = "https://www.minecraft.net/msaprofile"
MAX_UNAUTHORIZED_IN_A_ROW = 2


class MonitorState(str, Enum):
    IDLE = "Idle"
    VERIFYING_ACCOUNT = "Verifying account"
    MONITORING = "Monitoring"
    BACKING_OFF = "Backing off"
    CLAIMING = "Claiming"
    VERIFYING_CLAIM = "Verifying claim"
    CLAIMED = "Claimed"
    STOPPED = "Stopped"
    FAILED = "Failed"


FINAL_STATES = frozenset({MonitorState.CLAIMED, MonitorState.STOPPED, MonitorState.FAILED})


@dataclass(frozen=True)
class MonitorStatus:
    state: MonitorState
    target: str
    availability: str = "-"
    last_check: datetime | None = None
    next_check: datetime | None = None
    claim_result: str = "-"
    message: str = ""
    checks: int = 0
    claim_attempts: int = 0


@dataclass(frozen=True)
class MonitorTiming:
    max_claim_attempts: int = 3
    # Minimum pause after a failed claim before the next availability check.
    claim_retry_cooldown_seconds: float = 60.0
    # Pause before reading the profile after a claim (lets the change propagate).
    verify_delay_seconds: float = 3.0
    max_verify_attempts: int = 3
    max_backoff_seconds: float = MAX_BACKOFF_SECONDS


LIVE_TIMING = MonitorTiming()
SIMULATION_TIMING = MonitorTiming(
    claim_retry_cooldown_seconds=3.0, verify_delay_seconds=0.5, max_backoff_seconds=30.0
)


class MonitorApi(AccountApi, Protocol):
    def check_availability(self, name: str) -> str: ...
    def change_name(self, name: str) -> dict: ...


def _response_suffix(exc: ApiError) -> str:
    return f" Response: {exc.body}" if exc.body else ""


def _is_duplicate_rejection(body: str) -> bool:
    try:
        details = json.loads(body).get("details") or {}
        return details.get("status") == DUPLICATE
    except (ValueError, AttributeError):
        return DUPLICATE in body


class NameMonitor:
    def __init__(
        self,
        api: MonitorApi,
        target: str,
        interval_seconds: float,
        limiter: SlidingWindowLimiter,
        *,
        timing: MonitorTiming = LIVE_TIMING,
        on_status: Callable[[MonitorStatus], None] | None = None,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._api = api
        self.target = target
        self.interval = float(interval_seconds)
        self._limiter = limiter
        self._timing = timing
        self._on_status = on_status or (lambda status: None)
        self._now = now
        self._backoff = Backoff(self.interval, timing.max_backoff_seconds)
        self._status = MonitorStatus(state=MonitorState.IDLE, target=target)
        self._unauthorized_in_a_row = 0
        self._verifying_claim = False
        self._claim_accepted = False
        self._verify_attempts = 0

    @property
    def status(self) -> MonitorStatus:
        return self._status

    @property
    def finished(self) -> bool:
        return self._status.state in FINAL_STATES

    def _update(self, **changes: object) -> None:
        self._status = replace(self._status, **changes)
        self._on_status(self._status)

    def _finish(self, state: MonitorState, message: str, **changes: object) -> None:
        self._update(state=state, message=message, next_check=None, **changes)
        log = logger.info if state is not MonitorState.FAILED else logger.error
        if "claim_result" in changes:
            log("%s | Claim result: %s", message, changes["claim_result"])
        else:
            log("%s", message)

    # ----------------------------------------------------------------- loop
    def run(self, stop_event: threading.Event) -> MonitorStatus:
        logger.info("Monitoring started for '%s' (every %gs).", self.target, self.interval)
        if not self._verify_account():
            return self._status
        while not stop_event.is_set():
            delay = self.step()
            if delay is None:
                return self._status
            delay = max(0.0, delay)
            self._update(next_check=self._now() + timedelta(seconds=delay))
            if stop_event.wait(delay):
                break
        self._finish(MonitorState.STOPPED, f"Stopped after {self._status.checks} checks.")
        return self._status

    def _verify_account(self) -> bool:
        self._update(state=MonitorState.VERIFYING_ACCOUNT, message="Checking your Minecraft account...")
        try:
            info = verify_account(self._api)
            ensure_can_claim(info)
        except AccountNotEligibleError as exc:
            self._finish(MonitorState.FAILED, str(exc))
            return False
        except AuthError as exc:
            self._finish(MonitorState.FAILED, f"Sign-in problem: {exc}")
            return False
        except ApiError as exc:
            self._finish(MonitorState.FAILED, f"Couldn't verify your account: {exc}.{_response_suffix(exc)}")
            return False
        logger.info(info.describe())
        if info.profile_name.casefold() == self.target.casefold():
            self._finish(
                MonitorState.CLAIMED,
                "Nothing to do: this account already uses that name.",
                claim_result=f"Already yours ({info.profile_name}).",
            )
            return False
        self._update(
            state=MonitorState.MONITORING,
            message=f"Account OK ({info.profile_name}). Watching '{self.target}'.",
        )
        return True

    def step(self) -> float | None:
        """Do one unit of work. Returns seconds until the next step, or None when finished."""
        if self.finished:
            return None
        if self._verifying_claim:
            return self._verify_claim()

        budget_wait = self._limiter.wait_time()
        if budget_wait > 0:
            self._update(
                state=MonitorState.BACKING_OFF,
                message=(
                    f"Safety budget reached ({self._limiter.max_requests} checks per "
                    f"{self._limiter.window_seconds:g}s). Waiting {budget_wait:.0f}s."
                ),
            )
            return budget_wait

        self._limiter.record()
        try:
            availability = self._api.check_availability(self.target)
        except RateLimitedError as exc:
            return self._back_off(exc, "Availability check was rate limited (HTTP 429)")
        except UnauthorizedError as exc:
            return self._handle_unauthorized(exc)
        except TransientError as exc:
            return self._back_off(exc, "Temporary problem reaching Minecraft Services")
        except ApiError as exc:
            return self._finish(
                MonitorState.FAILED,
                f"Unexpected response to the availability check: {exc}.{_response_suffix(exc)}",
            )
        except AuthError as exc:
            return self._finish(MonitorState.FAILED, f"Sign-in problem: {exc}")

        self._backoff.reset()
        self._unauthorized_in_a_row = 0
        checks = self._status.checks + 1
        logger.info("Check #%d: '%s' is %s", checks, self.target, availability)
        self._update(
            state=MonitorState.MONITORING,
            availability=availability,
            last_check=self._now(),
            checks=checks,
            message=f"Watching '{self.target}'.",
        )
        if availability == AVAILABLE:
            return self._claim()
        if availability == NOT_ALLOWED:
            return self._finish(
                MonitorState.FAILED,
                "Mojang reports this name is NOT_ALLOWED (blocked or reserved), so it can't be "
                "claimed. Monitoring stopped.",
            )
        if availability != DUPLICATE:
            logger.warning("Unrecognised availability status '%s'; continuing to monitor.", availability)
        return self.interval

    def _back_off(self, exc: ApiError, reason: str) -> float:
        delay = self._backoff.next_delay(exc.retry_after)
        retry_note = f" (server asked for {exc.retry_after:.0f}s)" if exc.retry_after is not None else ""
        logger.warning("%s%s. Waiting %.0fs.%s", reason, retry_note, delay, _response_suffix(exc))
        self._update(
            state=MonitorState.BACKING_OFF,
            message=f"{reason}{retry_note}. Waiting {delay:.0f}s before the next request.",
        )
        return delay

    def _handle_unauthorized(self, exc: ApiError) -> float | None:
        self._unauthorized_in_a_row += 1
        if self._unauthorized_in_a_row >= MAX_UNAUTHORIZED_IN_A_ROW:
            return self._finish(
                MonitorState.FAILED,
                "Minecraft rejected the access token twice in a row. Sign out, sign in again, then Start.",
            )
        logger.warning("%s. The token will be refreshed before the next check.", exc)
        self._update(state=MonitorState.MONITORING, message="Access token expired; refreshing before the next check.")
        return self.interval

    # ---------------------------------------------------------------- claim
    def _claim(self) -> float | None:
        attempts = self._status.claim_attempts
        if attempts >= self._timing.max_claim_attempts:
            return self._finish(
                MonitorState.FAILED,
                f"Stopped after {attempts} name-change attempts so the app never loops on name "
                "changes. Press Start to keep trying.",
            )
        attempts += 1
        label = f"{attempts}/{self._timing.max_claim_attempts}"
        logger.info("'%s' is AVAILABLE. Sending one name-change request (attempt %s).", self.target, label)
        self._update(
            state=MonitorState.CLAIMING,
            claim_attempts=attempts,
            message=f"'{self.target}' is AVAILABLE. Sending name-change request ({label})...",
        )
        cooldown = max(self.interval, self._timing.claim_retry_cooldown_seconds)
        try:
            profile = self._api.change_name(self.target)
        except RateLimitedError as exc:
            result = f"Not claimed: name change was rate limited (HTTP 429).{_response_suffix(exc)}"
            logger.warning(result)
            self._update(claim_result=result)
            return max(self._back_off(exc, "Name change was rate limited (HTTP 429)"), cooldown)
        except UnauthorizedError as exc:
            self._unauthorized_in_a_row += 1
            result = (
                "Not claimed: access token rejected (HTTP 401). It will be refreshed; the app tries "
                "again only if the next check still says AVAILABLE."
            )
            logger.warning("%s %s", exc, _response_suffix(exc))
            self._update(state=MonitorState.MONITORING, claim_result=result, message=result)
            return cooldown
        except TransientError as exc:
            result = f"No clear answer ({exc}). Checking your profile instead of re-sending..."
            logger.warning("%s%s", result, _response_suffix(exc))
            self._update(claim_result=result)
            return self._begin_verification(accepted=False)
        except ApiError as exc:
            return self._handle_claim_rejection(exc, cooldown)
        except AuthError as exc:
            return self._finish(MonitorState.FAILED, f"Sign-in problem during the claim: {exc}")

        returned_name = profile.get("name", "?") if isinstance(profile, dict) else "?"
        result = f"Name change accepted (HTTP 200, profile name '{returned_name}'). Verifying..."
        logger.info(result)
        self._update(claim_result=result)
        return self._begin_verification(accepted=True)

    def _handle_claim_rejection(self, exc: ApiError, cooldown: float) -> float | None:
        logger.error("Name change refused: HTTP %s.%s", exc.status, _response_suffix(exc))
        if exc.status == 403 and _is_duplicate_rejection(exc.body):
            result = f"Not claimed: HTTP 403, someone else took the name first.{_response_suffix(exc)}"
            self._update(
                state=MonitorState.MONITORING,
                claim_result=result,
                message=f"Lost the race for '{self.target}'. Still watching in case it frees up again.",
            )
            return cooldown
        if exc.status == 400:
            explanation = "Mojang rejected the name as invalid."
        elif exc.status == 403:
            explanation = (
                "Mojang refused the change. Usual causes: the 30-day name-change cooldown or a reserved name."
            )
        else:
            explanation = "Unexpected response."
        return self._finish(
            MonitorState.FAILED,
            f"{explanation} Monitoring stopped so nothing repeats automatically.",
            claim_result=f"Failed: HTTP {exc.status}.{_response_suffix(exc)}",
        )

    # --------------------------------------------------------------- verify
    def _begin_verification(self, accepted: bool) -> float:
        self._verifying_claim = True
        self._claim_accepted = accepted
        self._verify_attempts = 0
        self._update(state=MonitorState.VERIFYING_CLAIM, message="Confirming the result on your profile...")
        return self._timing.verify_delay_seconds

    def _verify_claim(self) -> float | None:
        self._verify_attempts += 1
        max_attempts = self._timing.max_verify_attempts
        self._update(
            state=MonitorState.VERIFYING_CLAIM,
            message=f"Confirming the result on your profile (check {self._verify_attempts}/{max_attempts})...",
        )
        try:
            profile = self._api.get_profile()
        except (ApiError, AuthError) as exc:
            logger.warning("Profile check after the claim failed: %s", exc)
            if self._verify_attempts < max_attempts:
                return self._timing.verify_delay_seconds * (2**self._verify_attempts)
            self._verifying_claim = False
            return self._finish(
                MonitorState.FAILED,
                f"Couldn't confirm the result. Check {PROFILE_PAGE_URL} before starting again. "
                "Monitoring stopped so no second name change is sent.",
                claim_result=f"Unknown: {exc}",
            )

        current_name = str((profile or {}).get("name", ""))
        if current_name.casefold() == self.target.casefold():
            self._verifying_claim = False
            return self._finish(
                MonitorState.CLAIMED,
                f"Claimed '{current_name}'. Monitoring stopped.",
                claim_result=f"Success: your profile name is now {current_name}.",
            )
        if self._verify_attempts < max_attempts:
            return self._timing.verify_delay_seconds

        self._verifying_claim = False
        if self._claim_accepted:
            return self._finish(
                MonitorState.FAILED,
                f"Check {PROFILE_PAGE_URL} before doing anything else. Monitoring stopped so no "
                "second name change is sent.",
                claim_result=(
                    f"Unconfirmed: the API accepted the change, but your profile still shows '{current_name}'."
                ),
            )
        result = f"Not claimed: your profile still shows '{current_name}'. Resuming monitoring."
        logger.info(result)
        self._update(state=MonitorState.MONITORING, claim_result=result, message=result)
        return max(self.interval, self._timing.claim_retry_cooldown_seconds)
