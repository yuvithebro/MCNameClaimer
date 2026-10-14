import pytest
import requests

from fakes import FakeResponse, FakeSession

from mcclaimer.api import MinecraftServicesClient
from mcclaimer.errors import ApiError, RateLimitedError, TransientError, UnauthorizedError

TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.c2lnbmF0dXJl"


def make_client(session, invalidations=None):
    return MinecraftServicesClient(
        token_provider=lambda: TOKEN,
        on_unauthorized=(lambda: invalidations.append(1)) if invalidations is not None else None,
        session=session,
    )


def test_availability_sends_bearer_token_and_parses_status():
    session = FakeSession().add("GET", r"/minecraft/profile/name/Notch/available$",
                                FakeResponse(200, {"status": "AVAILABLE"}))
    assert make_client(session).check_availability("Notch") == "AVAILABLE"
    call = session.calls[0]
    assert call.kwargs["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert call.kwargs["timeout"]


def test_429_raises_rate_limited_with_retry_after():
    session = FakeSession().add("GET", "available", FakeResponse(429, text="", headers={"Retry-After": "42"}))
    with pytest.raises(RateLimitedError) as caught:
        make_client(session).check_availability("Notch")
    assert caught.value.retry_after == 42.0
    assert caught.value.status == 429


def test_401_raises_and_invalidates_token():
    invalidations = []
    session = FakeSession().add("GET", "available", FakeResponse(401, {"error": "UNAUTHORIZED"}))
    with pytest.raises(UnauthorizedError):
        make_client(session, invalidations).check_availability("Notch")
    assert invalidations == [1]


@pytest.mark.parametrize("failure", [requests.Timeout(), requests.ConnectionError("boom")])
def test_network_failures_are_transient(failure):
    session = FakeSession().add("GET", "available", failure)
    with pytest.raises(TransientError):
        make_client(session).check_availability("Notch")


def test_5xx_is_transient():
    session = FakeSession().add("GET", "available", FakeResponse(503, text="unavailable"))
    with pytest.raises(TransientError):
        make_client(session).check_availability("Notch")


def test_profile_404_means_no_java_profile():
    session = FakeSession().add("GET", r"/minecraft/profile$", FakeResponse(404, {"errorType": "NOT_FOUND"}))
    assert make_client(session).get_profile() is None


def test_change_name_403_keeps_server_body():
    body = {"details": {"status": "DUPLICATE"}, "errorMessage": "Could not change name for profile"}
    session = FakeSession().add("PUT", r"/minecraft/profile/name/Notch$", FakeResponse(403, body))
    with pytest.raises(ApiError) as caught:
        make_client(session).change_name("Notch")
    assert caught.value.status == 403
    assert "DUPLICATE" in caught.value.body


def test_change_name_returns_profile():
    session = FakeSession().add("PUT", r"/minecraft/profile/name/Notch$", FakeResponse(200, {"id": "x", "name": "Notch"}))
    assert make_client(session).change_name("Notch")["name"] == "Notch"
    assert len(session.calls_to("PUT", "/name/Notch")) == 1


def test_entitlements_empty_payload_means_none():
    session = FakeSession().add("GET", "entitlements/license", FakeResponse(200, text=""))
    assert make_client(session).get_java_entitlements() == []


def test_entitlements_lists_names():
    payload = {"items": [{"name": "product_minecraft"}, {"name": "game_minecraft"}], "signature": "x"}
    session = FakeSession().add("GET", r"entitlements/license\?requestId=", FakeResponse(200, payload))
    assert make_client(session).get_java_entitlements() == ["product_minecraft", "game_minecraft"]


def test_error_body_is_truncated_and_redacted():
    body = '{"access_token": "' + TOKEN + '", "pad": "' + "x" * 2000 + '"}'
    session = FakeSession().add("GET", "available", FakeResponse(400, text=body))
    with pytest.raises(ApiError) as caught:
        make_client(session).check_availability("Notch")
    assert TOKEN not in caught.value.body
    assert len(caught.value.body) <= 500
