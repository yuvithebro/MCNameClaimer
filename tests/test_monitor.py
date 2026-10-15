import json

import pytest

from fakes import AVAILABLE, DUPLICATE, FakeClock, InstantEvent, ScriptedApi, http_error

from mcclaimer.errors import AppNotApprovedError, AuthError
from mcclaimer.monitor import MonitorState, MonitorTiming, NameMonitor
from mcclaimer.ratelimit import SlidingWindowLimiter

TIMING = MonitorTiming(max_claim_attempts=3, claim_retry_cooldown_seconds=60, verify_delay_seconds=3,
                       max_verify_attempts=3, max_backoff_seconds=900)
DUPLICATE_403 = json.dumps({"details": {"status": "DUPLICATE"}, "errorMessage": "Could not change name for profile"})


def make_monitor(api, interval=30, budget=(16, 300), clock=None):
    clock = clock or FakeClock()
    limiter = SlidingWindowLimiter(*budget, clock=clock)
    statuses = []
    monitor = NameMonitor(api, "Target", interval, limiter, timing=TIMING, on_status=statuses.append, now=clock.now)
    return monitor, statuses, clock


def run_steps(monitor, clock, limit=50):
    """Drive step() like run() does, advancing the fake clock by each delay."""
    delays = []
    for _ in range(limit):
        delay = monitor.step()
        if delay is None:
            break
        delays.append(delay)
        clock.advance(delay)
    return delays


class TestChecking:
    def test_duplicate_waits_one_interval(self):
        monitor, statuses, _ = make_monitor(ScriptedApi([DUPLICATE]))
        assert monitor.step() == 30
        assert monitor.status.availability == DUPLICATE
        assert monitor.status.checks == 1
        assert monitor.status.last_check is not None

    def test_not_allowed_stops(self):
        api = ScriptedApi(["NOT_ALLOWED"])
        monitor, _, _ = make_monitor(api)
        assert monitor.step() is None
        assert monitor.status.state is MonitorState.FAILED
        assert "NOT_ALLOWED" in monitor.status.message
        assert api.change_calls == []

    def test_429_honours_retry_after(self):
        api = ScriptedApi([http_error(429, retry_after=120), DUPLICATE])
        monitor, _, _ = make_monitor(api)
        assert monitor.step() == 120
        assert monitor.status.state is MonitorState.BACKING_OFF
        assert api.change_calls == []

    def test_429_without_retry_after_backs_off_exponentially_then_resets(self):
        api = ScriptedApi([http_error(429), http_error(429), http_error(429), DUPLICATE, http_error(429)])
        monitor, _, _ = make_monitor(api)
        assert [monitor.step() for _ in range(5)] == [60, 120, 240, 30, 60]

    def test_server_errors_back_off(self):
        api = ScriptedApi([http_error(503), http_error(503), DUPLICATE])
        monitor, _, _ = make_monitor(api)
        assert [monitor.step() for _ in range(3)] == [60, 120, 30]

    def test_unexpected_4xx_stops_and_shows_body(self):
        api = ScriptedApi([http_error(400, body='{"error":"bad"}')])
        monitor, _, _ = make_monitor(api)
        assert monitor.step() is None
        assert monitor.status.state is MonitorState.FAILED
        assert '{"error":"bad"}' in monitor.status.message

    def test_single_401_retries_after_interval_but_two_in_a_row_stop(self):
        api = ScriptedApi([http_error(401), http_error(401)])
        monitor, _, _ = make_monitor(api)
        assert monitor.step() == 30
        assert monitor.step() is None
        assert monitor.status.state is MonitorState.FAILED

    def test_failed_token_refresh_stops(self):
        api = ScriptedApi([AuthError("expired", requires_sign_in=True)])
        monitor, _, _ = make_monitor(api)
        assert monitor.step() is None
        assert "Sign-in problem" in monitor.status.message

    def test_local_budget_blocks_without_calling_api(self):
        api = ScriptedApi([DUPLICATE])
        monitor, _, clock = make_monitor(api, interval=1, budget=(2, 300))
        monitor.step()
        clock.advance(1)
        monitor.step()
        clock.advance(1)
        wait = monitor.step()
        assert api.availability_calls == 2
        assert wait == pytest.approx(298)
        assert monitor.status.state is MonitorState.BACKING_OFF


class TestClaiming:
    def test_available_claims_once_and_verifies(self):
        api = ScriptedApi([DUPLICATE, AVAILABLE])
        monitor, _, clock = make_monitor(api)
        delays = run_steps(monitor, clock)
        assert delays == [30, 3]
        assert api.change_calls == ["Target"]
        assert monitor.status.state is MonitorState.CLAIMED
        assert monitor.status.claim_result.startswith("Success")
        assert api.profile_calls == 1

    def test_ambiguous_claim_is_verified_not_resent(self):
        api = ScriptedApi([AVAILABLE], claim=[http_error(503)], profiles=[{"name": "Target"}])
        monitor, _, clock = make_monitor(api)
        run_steps(monitor, clock)
        assert api.change_calls == ["Target"]
        assert monitor.status.state is MonitorState.CLAIMED

    def test_ambiguous_claim_that_did_not_apply_resumes_after_cooldown(self):
        api = ScriptedApi([AVAILABLE, DUPLICATE], claim=[http_error(503)], profiles=[{"name": "OldName"}])
        monitor, _, clock = make_monitor(api)
        delays = run_steps(monitor, clock, limit=6)
        assert api.change_calls == ["Target"]
        assert delays[:4] == [3, 3, 3, 60]  # verify x3, then the claim cooldown
        assert monitor.status.state is MonitorState.MONITORING
        assert monitor.status.claim_result.startswith("Not claimed")

    def test_lost_race_403_duplicate_keeps_monitoring(self):
        api = ScriptedApi([AVAILABLE, DUPLICATE], claim=[http_error(403, body=DUPLICATE_403)])
        monitor, _, _ = make_monitor(api)
        assert monitor.step() == 60
        assert monitor.status.state is MonitorState.MONITORING
        assert "someone else" in monitor.status.claim_result
        assert DUPLICATE_403 in monitor.status.claim_result

    def test_other_403_stops(self):
        api = ScriptedApi([AVAILABLE], claim=[http_error(403, body='{"errorMessage":"Could not change name"}')])
        monitor, _, _ = make_monitor(api)
        assert monitor.step() is None
        assert monitor.status.state is MonitorState.FAILED
        assert "HTTP 403" in monitor.status.claim_result
        assert "cooldown" in monitor.status.message

    def test_claim_429_waits_at_least_the_cooldown(self):
        api = ScriptedApi([AVAILABLE], claim=[http_error(429, retry_after=5)])
        monitor, _, _ = make_monitor(api)
        assert monitor.step() == 60
        assert "HTTP 429" in monitor.status.claim_result

    def test_claim_attempts_are_capped(self):
        api = ScriptedApi([AVAILABLE], claim=[http_error(403, body=DUPLICATE_403)])
        monitor, _, clock = make_monitor(api)
        run_steps(monitor, clock)
        assert len(api.change_calls) == TIMING.max_claim_attempts
        assert monitor.status.state is MonitorState.FAILED
        assert "never loops" in monitor.status.message

    def test_accepted_but_profile_unchanged_stops_without_resending(self):
        api = ScriptedApi([AVAILABLE], profiles=[{"name": "OldName"}])
        api.change_name = lambda name: (api.change_calls.append(name), {"name": name})[1]
        monitor, _, clock = make_monitor(api)
        run_steps(monitor, clock)
        assert api.change_calls == ["Target"]
        assert monitor.status.state is MonitorState.FAILED
        assert monitor.status.claim_result.startswith("Unconfirmed")

    def test_verification_errors_retry_with_growing_delay_then_give_up(self):
        api = ScriptedApi([AVAILABLE], claim=[http_error(503)], profiles=[http_error(503)])
        monitor, _, clock = make_monitor(api)
        delays = run_steps(monitor, clock)
        assert delays == [3, 6, 12]
        assert api.change_calls == ["Target"]
        assert monitor.status.state is MonitorState.FAILED
        assert monitor.status.claim_result.startswith("Unknown")


class TestRun:
    def test_stops_when_event_set(self):
        clock = FakeClock()
        monitor, _, _ = make_monitor(ScriptedApi([DUPLICATE]), clock=clock)
        status = monitor.run(InstantEvent(max_waits=3, clock=clock))
        assert status.state is MonitorState.STOPPED
        assert status.checks == 3

    def test_refuses_to_start_during_cooldown(self):
        api = ScriptedApi([AVAILABLE], name_change_allowed=False)
        monitor, _, _ = make_monitor(api)
        status = monitor.run(InstantEvent())
        assert status.state is MonitorState.FAILED
        assert "can't change its name" in status.message
        assert api.availability_calls == 0

    def test_refuses_without_java_profile(self):
        api = ScriptedApi([AVAILABLE], profiles=[None])
        monitor, _, _ = make_monitor(api)
        status = monitor.run(InstantEvent())
        assert status.state is MonitorState.FAILED
        assert "no Minecraft: Java Edition profile" in status.message
        assert api.availability_calls == 0

    def test_refused_client_id_stops_before_any_check(self):
        api = ScriptedApi([AVAILABLE], profiles=[AppNotApprovedError("Minecraft Services refused this Azure app (HTTP 403)")])
        monitor, _, _ = make_monitor(api)
        status = monitor.run(InstantEvent())
        assert status.state is MonitorState.FAILED
        assert "HTTP 403" in status.message
        assert api.availability_calls == 0
        assert api.change_calls == []

    def test_refused_client_id_mid_run_stops_without_claiming(self):
        api = ScriptedApi([DUPLICATE, AppNotApprovedError("refused (HTTP 403)"), AVAILABLE])
        monitor, _, clock = make_monitor(api)
        run_steps(monitor, clock)
        assert monitor.status.state is MonitorState.FAILED
        assert api.availability_calls == 2
        assert api.change_calls == []

    def test_already_owned_name_needs_nothing(self):
        api = ScriptedApi([AVAILABLE], profile_name="target")
        monitor, _, _ = make_monitor(api)
        status = monitor.run(InstantEvent())
        assert status.state is MonitorState.CLAIMED
        assert api.availability_calls == 0

    def test_next_check_is_published(self):
        clock = FakeClock()
        monitor, statuses, _ = make_monitor(ScriptedApi([DUPLICATE]), clock=clock)
        monitor.run(InstantEvent(max_waits=1, clock=clock))
        scheduled = [s.next_check for s in statuses if s.next_check is not None]
        assert scheduled and (scheduled[0] - statuses[-1].last_check).total_seconds() == 30
