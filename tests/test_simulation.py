import pytest

from fakes import FakeClock, InstantEvent

from mcclaimer.monitor import SIMULATION_TIMING, MonitorState, NameMonitor
from mcclaimer.ratelimit import SlidingWindowLimiter
from mcclaimer.simulation import SCENARIOS, SimulatedMinecraftAPI


def run_scenario(scenario, max_waits=50):
    clock = FakeClock()
    api = SimulatedMinecraftAPI(scenario)
    monitor = NameMonitor(api, "DreamName", 2, SlidingWindowLimiter(1000, 60, clock=clock),
                          timing=SIMULATION_TIMING, now=clock.now)
    status = monitor.run(InstantEvent(max_waits=max_waits, clock=clock))
    return status, api


@pytest.mark.parametrize(
    ("scenario", "final_state", "change_requests"),
    [
        ("available_after_checks", MonitorState.CLAIMED, 1),
        ("rate_limited", MonitorState.CLAIMED, 1),
        ("ambiguous_claim", MonitorState.CLAIMED, 1),
        ("claim_race_lost", MonitorState.STOPPED, 1),
        ("not_allowed", MonitorState.FAILED, 0),
        ("cooldown", MonitorState.FAILED, 0),
        ("never_available", MonitorState.STOPPED, 0),
    ],
)
def test_every_scenario_end_to_end(scenario, final_state, change_requests):
    status, api = run_scenario(scenario)
    assert status.state is final_state
    assert api.change_requests == change_requests


def test_every_scenario_is_covered_above():
    covered = {"available_after_checks", "rate_limited", "ambiguous_claim", "claim_race_lost",
               "not_allowed", "cooldown", "never_available"}
    assert covered == set(SCENARIOS)


def test_successful_simulation_changes_only_the_fake_profile():
    status, api = run_scenario("available_after_checks")
    assert api.profile_name == "DreamName"
    assert status.claim_result == "Success: your profile name is now DreamName."
    assert status.checks == 4


def test_rate_limited_scenario_waits_for_retry_after():
    clock = FakeClock()
    api = SimulatedMinecraftAPI("rate_limited")
    event = InstantEvent(clock=clock)
    NameMonitor(api, "DreamName", 2, SlidingWindowLimiter(1000, 60, clock=clock),
                timing=SIMULATION_TIMING, now=clock.now).run(event)
    assert event.waits[1] == 5  # Retry-After: 5 beats the 2s interval and the 4s first backoff


def test_unknown_scenario_rejected():
    with pytest.raises(ValueError):
        SimulatedMinecraftAPI("nope")
