import pytest

from fakes import FakeClock, FakeResponse, FakeSession, InstantEvent, MemoryStore

from mcclaimer.auth import (
    APP_REVIEW_URL,
    DEVICE_CODE_URL,
    MC_LOGIN_URL,
    TOKEN_URL,
    XBL_AUTH_URL,
    XSTS_AUTH_URL,
    AuthSession,
    DeviceCode,
    MicrosoftAuthenticator,
    MicrosoftTokens,
)
from mcclaimer.errors import AuthCancelled, AuthError, TransientError

CLIENT_ID = "00000000-1111-2222-3333-444444444444"
DEVICE = DeviceCode("dev-code", "ABCD-EFGH", "https://www.microsoft.com/link", 900, 5, "msg")


def xbox_routes(session, mc_token="eyJmYWtl.eyJtYw.sig", expires_in=86400, mc_status=200):
    session.add("POST", XBL_AUTH_URL, FakeResponse(200, {"Token": "xbl-token", "DisplayClaims": {"xui": [{"uhs": "hash1"}]}}))
    session.add("POST", XSTS_AUTH_URL, FakeResponse(200, {"Token": "xsts-token", "DisplayClaims": {"xui": [{"uhs": "hash1"}]}}))
    if mc_status == 200:
        session.add("POST", MC_LOGIN_URL, FakeResponse(200, {"access_token": mc_token, "expires_in": expires_in}))
    else:
        session.add("POST", MC_LOGIN_URL, FakeResponse(mc_status, {"errorMessage": "Invalid app registration"}))
    return session


class TestDeviceFlow:
    def test_start_uses_consumers_tenant_and_xbox_scope(self):
        session = FakeSession().add("POST", DEVICE_CODE_URL, FakeResponse(200, {
            "device_code": "d", "user_code": "ABCD-EFGH", "verification_uri": "https://www.microsoft.com/link",
            "expires_in": 900, "interval": 5, "message": "go"}))
        code = MicrosoftAuthenticator(CLIENT_ID, session).start_device_flow()
        assert code.user_code == "ABCD-EFGH"
        call = session.calls[0]
        assert "/consumers/" in call.url
        assert "XboxLive.signin" in call.kwargs["data"]["scope"]
        assert "password" not in str(call.kwargs)

    def test_polls_until_authorised_and_slows_down_when_asked(self):
        session = FakeSession().add(
            "POST", TOKEN_URL,
            FakeResponse(400, {"error": "authorization_pending"}),
            FakeResponse(400, {"error": "slow_down"}),
            FakeResponse(200, {"access_token": "ms-access", "refresh_token": "ms-refresh"}),
        )
        event = InstantEvent()
        tokens = MicrosoftAuthenticator(CLIENT_ID, session).wait_for_device_flow(DEVICE, event)
        assert tokens == MicrosoftTokens("ms-access", "ms-refresh")
        assert event.waits == [5, 5, 10]  # interval grows by 5s after slow_down

    def test_cancel_raises(self):
        event = InstantEvent()
        event.set()
        with pytest.raises(AuthCancelled):
            MicrosoftAuthenticator(CLIENT_ID, FakeSession()).wait_for_device_flow(DEVICE, event)

    def test_declined_raises(self):
        session = FakeSession().add("POST", TOKEN_URL, FakeResponse(400, {"error": "authorization_declined"}))
        with pytest.raises(AuthError, match="declined"):
            MicrosoftAuthenticator(CLIENT_ID, session).wait_for_device_flow(DEVICE, InstantEvent())

    def test_public_client_flows_hint(self):
        session = FakeSession().add("POST", DEVICE_CODE_URL, FakeResponse(400, {
            "error": "invalid_client", "error_description": "AADSTS7000218: The request body must contain..."}))
        with pytest.raises(AuthError, match="Allow public client flows"):
            MicrosoftAuthenticator(CLIENT_ID, session).start_device_flow()

    def test_empty_client_id_rejected(self):
        with pytest.raises(AuthError):
            MicrosoftAuthenticator("  ")


class TestMinecraftLogin:
    def test_full_chain_builds_identity_token(self):
        session = xbox_routes(FakeSession())
        token = MicrosoftAuthenticator(CLIENT_ID, session).login_minecraft("ms-access")
        assert token.expires_in == 86400
        assert session.calls_to("POST", "user.auth.xboxlive.com")[0].kwargs["json"]["Properties"]["RpsTicket"] == "d=ms-access"
        xsts_body = session.calls_to("POST", "xsts.auth.xboxlive.com")[0].kwargs["json"]
        assert xsts_body["RelyingParty"] == "rp://api.minecraftservices.com/"
        assert session.calls_to("POST", "login_with_xbox")[0].kwargs["json"] == {"identityToken": "XBL3.0 x=hash1;xsts-token"}

    def test_unapproved_azure_app_explains_review_form(self):
        session = xbox_routes(FakeSession(), mc_status=403)
        with pytest.raises(AuthError) as caught:
            MicrosoftAuthenticator(CLIENT_ID, session).login_minecraft("ms-access")
        assert APP_REVIEW_URL in str(caught.value)

    @pytest.mark.parametrize(("xerr", "fragment"), [
        (2148916233, "no Xbox profile"),
        (2148916238, "child account"),
        (2148916235, "country"),
    ])
    def test_xsts_errors_are_explained(self, xerr, fragment):
        session = FakeSession()
        session.add("POST", XBL_AUTH_URL, FakeResponse(200, {"Token": "t", "DisplayClaims": {"xui": [{"uhs": "h"}]}}))
        session.add("POST", XSTS_AUTH_URL, FakeResponse(401, {"XErr": xerr, "Message": ""}))
        with pytest.raises(AuthError, match=fragment):
            MicrosoftAuthenticator(CLIENT_ID, session).login_minecraft("ms-access")

    def test_xbox_5xx_is_transient(self):
        session = FakeSession().add("POST", XBL_AUTH_URL, FakeResponse(503, text=""))
        with pytest.raises(TransientError):
            MicrosoftAuthenticator(CLIENT_ID, session).login_minecraft("ms-access")


class TestAuthSession:
    def stored(self, **overrides):
        data = {"version": 1, "client_id": CLIENT_ID, "refresh_token": "old-refresh",
                "mc_access_token": "cached-mc", "mc_expires_at": 2000.0}
        data.update(overrides)
        return MemoryStore(data)

    def test_uses_cached_token_while_valid(self):
        clock = FakeClock(1000.0)
        session = AuthSession(MicrosoftAuthenticator(CLIENT_ID, FakeSession()), self.stored(), clock=clock)
        assert session.is_signed_in
        assert session.get_token() == "cached-mc"

    def test_refreshes_near_expiry_and_saves_rotated_refresh_token(self):
        clock = FakeClock(1800.0)  # 200s before expiry, inside the 300s margin
        http = FakeSession().add("POST", TOKEN_URL, FakeResponse(200, {"access_token": "ms-new", "refresh_token": "new-refresh"}))
        xbox_routes(http, mc_token="fresh-mc", expires_in=86400)
        store = self.stored()
        session = AuthSession(MicrosoftAuthenticator(CLIENT_ID, http), store, clock=clock)
        assert session.get_token() == "fresh-mc"
        assert store.data["refresh_token"] == "new-refresh"
        assert store.data["mc_access_token"] == "fresh-mc"
        assert store.data["mc_expires_at"] == 1800.0 + 86400
        assert http.calls_to("POST", TOKEN_URL)[0].kwargs["data"]["grant_type"] == "refresh_token"

    def test_rotated_refresh_token_saved_even_if_minecraft_step_fails(self):
        http = FakeSession().add("POST", TOKEN_URL, FakeResponse(200, {"access_token": "ms-new", "refresh_token": "new-refresh"}))
        http.add("POST", XBL_AUTH_URL, FakeResponse(503, text=""))
        store = self.stored(mc_expires_at=0)
        session = AuthSession(MicrosoftAuthenticator(CLIENT_ID, http), store, clock=FakeClock())
        with pytest.raises(TransientError):
            session.get_token()
        assert store.data["refresh_token"] == "new-refresh"

    def test_revoked_refresh_token_signs_out(self):
        http = FakeSession().add("POST", TOKEN_URL, FakeResponse(400, {"error": "invalid_grant"}))
        store = self.stored(mc_expires_at=0)
        session = AuthSession(MicrosoftAuthenticator(CLIENT_ID, http), store, clock=FakeClock())
        with pytest.raises(AuthError) as caught:
            session.get_token()
        assert caught.value.requires_sign_in
        assert not session.is_signed_in
        assert store.data is None

    def test_credentials_for_another_client_id_are_ignored(self):
        session = AuthSession(MicrosoftAuthenticator("other-client", FakeSession()), self.stored())
        assert not session.is_signed_in

    def test_invalidate_forces_refresh(self):
        http = FakeSession().add("POST", TOKEN_URL, FakeResponse(200, {"access_token": "ms-new", "refresh_token": "r2"}))
        xbox_routes(http, mc_token="after-401")
        session = AuthSession(MicrosoftAuthenticator(CLIENT_ID, http), self.stored(), clock=FakeClock(1000.0))
        session.invalidate()
        assert session.get_token() == "after-401"

    def test_complete_sign_in_persists(self):
        store = MemoryStore()
        http = xbox_routes(FakeSession(), mc_token="first-mc")
        session = AuthSession(MicrosoftAuthenticator(CLIENT_ID, http), store, clock=FakeClock(0))
        session.complete_sign_in(MicrosoftTokens("ms-access", "ms-refresh"))
        assert session.is_signed_in
        assert store.data["refresh_token"] == "ms-refresh"
        assert session.get_token() == "first-mc"
