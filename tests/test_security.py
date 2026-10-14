"""Secrets must never reach the log file, and saved credentials must be encrypted."""

import logging
import sys

import pytest

from fakes import FakeClock, FakeResponse, FakeSession, InstantEvent, MemoryStore

from mcclaimer.api import MinecraftServicesClient
from mcclaimer.auth import MC_LOGIN_URL, TOKEN_URL, XBL_AUTH_URL, XSTS_AUTH_URL, AuthSession, MicrosoftAuthenticator
from mcclaimer.logging_setup import LOGGER_NAME, configure_logging, redact
from mcclaimer.monitor import LIVE_TIMING, MonitorState, NameMonitor
from mcclaimer.ratelimit import SlidingWindowLimiter
from mcclaimer.secure_store import DPAPI_AVAILABLE, SecureStore

MC_TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJTRUNSRVRfTUNfMTExIn0.SECRETSIGNATURE111"
MS_ACCESS = "EwBoA8l6BAAU-SECRET-MS-ACCESS-222"
MS_REFRESH = "M.C527_BAY.0.U.-SECRETREFRESH333abcdefghijklmnop"
XBL_TOKEN = "SECRET-XBL-444"
XSTS_TOKEN = "SECRET-XSTS-555"
SECRETS = [MC_TOKEN, MS_ACCESS, MS_REFRESH, XBL_TOKEN, XSTS_TOKEN, "SECRET"]


@pytest.mark.parametrize(
    "text",
    [
        f"Authorization: Bearer {MC_TOKEN}",
        f'{{"access_token": "{MS_ACCESS}", "refresh_token": "{MS_REFRESH}"}}',
        f"identityToken=XBL3.0 x=userhash;{XSTS_TOKEN}",
        f'{{"Token": "{XBL_TOKEN}"}}',
        f"refresh_token={MS_REFRESH}&client_id=abc",
        f"stray token {MS_REFRESH} in a message",
        f"jwt {MC_TOKEN} in a message",
    ],
)
def test_redact_scrubs_known_token_shapes(text):
    cleaned = redact(text)
    assert "SECRET" not in cleaned
    assert "[REDACTED]" in cleaned


def test_redact_leaves_normal_text_alone():
    line = "Check #3: 'Notch' is DUPLICATE (token_type Bearer explained in README)"
    assert redact("Check #3: 'Notch' is DUPLICATE") == "Check #3: 'Notch' is DUPLICATE"
    assert "Notch" in redact(line)


def test_full_live_flow_never_writes_tokens_to_the_log(tmp_path):
    log_file = configure_logging(tmp_path)
    http = FakeSession()
    http.add("POST", TOKEN_URL, FakeResponse(200, {"access_token": MS_ACCESS, "refresh_token": MS_REFRESH}))
    http.add("POST", XBL_AUTH_URL, FakeResponse(200, {"Token": XBL_TOKEN, "DisplayClaims": {"xui": [{"uhs": "h"}]}}))
    http.add("POST", XSTS_AUTH_URL, FakeResponse(200, {"Token": XSTS_TOKEN, "DisplayClaims": {"xui": [{"uhs": "h"}]}}))
    http.add("POST", MC_LOGIN_URL, FakeResponse(200, {"access_token": MC_TOKEN, "expires_in": 86400}))
    http.add("GET", r"/minecraft/profile$", FakeResponse(200, {"id": "u", "name": "OldName"}),
             FakeResponse(200, {"id": "u", "name": "Target"}))
    http.add("GET", "entitlements", FakeResponse(200, {"items": [{"name": "game_minecraft"}]}))
    http.add("GET", "namechange", FakeResponse(200, {"nameChangeAllowed": True, "changedAt": "2020-01-01T00:00:00Z"}))
    # Error bodies that echo a token must still be scrubbed before they're logged.
    http.add("GET", "available", FakeResponse(429, {"echo": f"Bearer {MC_TOKEN}"}), FakeResponse(200, {"status": "AVAILABLE"}))
    http.add("PUT", "/minecraft/profile/name/Target", FakeResponse(200, {"id": "u", "name": "Target"}))

    store = MemoryStore({"client_id": "cid", "refresh_token": "M.C527_BAY.0.U.-SECRETOLDREFRESHabcdefghijkl", "mc_expires_at": 0})
    session = AuthSession(MicrosoftAuthenticator("cid", http), store, clock=FakeClock())
    api = MinecraftServicesClient(session.get_token, session.invalidate, session=http)
    clock = FakeClock()
    monitor = NameMonitor(api, "Target", 30, SlidingWindowLimiter(16, 300, clock=clock), timing=LIVE_TIMING, now=clock.now)
    status = monitor.run(InstantEvent(clock=clock))
    logging.getLogger(LOGGER_NAME).exception("Simulated crash with %s", MC_TOKEN, exc_info=RuntimeError(f"Bearer {MC_TOKEN}"))

    for handler in logging.getLogger(LOGGER_NAME).handlers:
        handler.flush()
    content = log_file.read_text(encoding="utf-8")
    assert status.state is MonitorState.CLAIMED
    assert "Check #1" in content and "Success" in content
    for secret in SECRETS:
        assert secret not in content


@pytest.mark.skipif(not DPAPI_AVAILABLE, reason="DPAPI is Windows-only")
class TestSecureStore:
    def test_round_trip_and_ciphertext_hides_values(self, tmp_path):
        path = tmp_path / "credentials.bin"
        store = SecureStore(path)
        store.save({"refresh_token": MS_REFRESH})
        assert MS_REFRESH.encode() not in path.read_bytes()
        assert b"refresh_token" not in path.read_bytes()
        assert SecureStore(path).load() == {"refresh_token": MS_REFRESH}

    def test_tampered_file_is_ignored(self, tmp_path):
        path = tmp_path / "credentials.bin"
        store = SecureStore(path)
        store.save({"refresh_token": "x"})
        data = bytearray(path.read_bytes())
        data[-5] ^= 0xFF
        path.write_bytes(bytes(data))
        assert store.load() is None

    def test_clear_deletes_file(self, tmp_path):
        path = tmp_path / "credentials.bin"
        store = SecureStore(path)
        store.save({"refresh_token": "x"})
        store.clear()
        assert not path.exists()
        assert store.load() is None
        store.clear()  # idempotent


@pytest.mark.skipif(DPAPI_AVAILABLE, reason="only meaningful off Windows")
def test_non_windows_keeps_credentials_in_memory_only(tmp_path):
    store = SecureStore(tmp_path / "credentials.bin")
    store.save({"refresh_token": "x"})
    assert not (tmp_path / "credentials.bin").exists()
    assert store.load() == {"refresh_token": "x"}
