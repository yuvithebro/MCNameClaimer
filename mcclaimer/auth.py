"""Microsoft -> Xbox Live -> Minecraft sign-in chain.

1. Microsoft identity platform, OAuth 2.0 device authorization grant
   (RFC 8628) against the `consumers` tenant with scope XboxLive.signin.
   You type a short code at microsoft.com/link; this app never sees your
   password.
2. Xbox Live user token  (user.auth.xboxlive.com)
3. XSTS token for rp://api.minecraftservices.com/
4. Minecraft access token (api.minecraftservices.com/authentication/login_with_xbox),
   valid for 24 hours.

Only the Microsoft refresh token and the current Minecraft access token are
kept, encrypted with DPAPI (see secure_store.py). When the Minecraft token is
about to expire, steps 2-4 run again from a refreshed Microsoft token.

Prerequisite (cannot be bypassed): Mojang must approve your Azure app's
client ID for Minecraft API access (https://aka.ms/mce-reviewappid).
Unapproved apps get HTTP 403 at step 4. The app treats that 403 as final: it
raises AppNotApprovedError, deletes the saved sign-in, and the GUI locks Live
mode again. It never retries with, or falls back to, any other client ID.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

import requests

from .api import REQUEST_TIMEOUT_SECONDS, USER_AGENT, error_body
from .errors import AppNotApprovedError, AuthCancelled, AuthError, RateLimitedError, TransientError
from .logging_setup import LOGGER_NAME
from .ratelimit import parse_retry_after
from .secure_store import SecureStore

logger = logging.getLogger(f"{LOGGER_NAME}.auth")

MS_AUTHORITY = "https://login.microsoftonline.com/consumers/oauth2/v2.0"
DEVICE_CODE_URL = f"{MS_AUTHORITY}/devicecode"
TOKEN_URL = f"{MS_AUTHORITY}/token"
SCOPES = "XboxLive.signin offline_access"
DEVICE_CODE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
XBL_AUTH_URL = "https://user.auth.xboxlive.com/user/authenticate"
XSTS_AUTH_URL = "https://xsts.auth.xboxlive.com/xsts/authorize"
MC_LOGIN_URL = "https://api.minecraftservices.com/authentication/login_with_xbox"
APP_REVIEW_URL = "https://aka.ms/mce-reviewappid"

# Refresh the Minecraft token this long before it expires.
TOKEN_REFRESH_MARGIN_SECONDS = 300
SLOW_DOWN_EXTRA_SECONDS = 5
CREDENTIALS_FORMAT_VERSION = 1

APP_NOT_APPROVED_MESSAGE = (
    "Minecraft Services refused this Azure app (HTTP 403): Mojang has not approved this client ID "
    "for Minecraft API access. The saved sign-in was deleted and Live mode is locked again. "
    f"Approval is requested through Mojang's AppID review ({APP_REVIEW_URL}) and is not "
    "guaranteed. Simulation mode works without approval."
)

XSTS_ERRORS = {
    2148916227: "This Microsoft account is banned from Xbox services.",
    2148916233: (
        "This Microsoft account has no Xbox profile yet. Sign in once at https://www.xbox.com "
        "or in the Minecraft Launcher to create one, then try again."
    ),
    2148916235: "Xbox Live is not available in this account's country or region.",
    2148916236: "This account needs adult verification on xbox.com (South Korea).",
    2148916237: "This account needs adult verification on xbox.com (South Korea).",
    2148916238: (
        "This is a child account. An adult must add it to a Microsoft Family group "
        "before it can sign in to Xbox services."
    ),
}

# Hints for common Azure app-registration mistakes, keyed by AADSTS error code.
_AADSTS_HINTS = {
    "AADSTS7000218": "In the Azure portal, open your app > Authentication and set 'Allow public client flows' to Yes.",
    "AADSTS700016": (
        "The client ID wasn't found for personal accounts. Check the ID and make sure the app's "
        "'Supported account types' includes personal Microsoft accounts."
    ),
    "AADSTS70002": "In the Azure portal, open your app > Authentication and set 'Allow public client flows' to Yes.",
    "AADSTS50059": "Make sure the app's 'Supported account types' includes personal Microsoft accounts.",
}


@dataclass(frozen=True)
class DeviceCode:
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int
    message: str


@dataclass(frozen=True)
class MicrosoftTokens:
    access_token: str
    refresh_token: str


@dataclass(frozen=True)
class MinecraftToken:
    access_token: str
    expires_in: int


def describe_microsoft_error(data: dict, status: int) -> str:
    """A readable message for an OAuth error body. Never includes tokens."""
    error = str(data.get("error") or f"HTTP {status}")
    description = str(data.get("error_description") or "")
    for code, hint in _AADSTS_HINTS.items():
        if code in description:
            return f"Microsoft sign-in failed ({error}). {hint}"
    first_line = description.splitlines()[0][:300] if description else ""
    return f"Microsoft sign-in failed ({error}). {first_line}".strip()


class MicrosoftAuthenticator:
    def __init__(self, client_id: str, session: requests.Session | None = None) -> None:
        if not client_id.strip():
            raise AuthError("An Azure application (client) ID is required for Live mode.")
        self.client_id = client_id.strip()
        self._session = session or requests.Session()

    def _post(self, url: str, context: str, **kwargs: Any) -> requests.Response:
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        try:
            return self._session.post(url, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS, **kwargs)
        except requests.RequestException as exc:
            raise TransientError(f"{context}: network error ({type(exc).__name__})") from None

    @staticmethod
    def _json_or_empty(response: requests.Response) -> dict:
        try:
            data = response.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _microsoft_tokens(data: dict, previous_refresh_token: str | None = None) -> MicrosoftTokens:
        access_token = data.get("access_token")
        refresh_token = data.get("refresh_token") or previous_refresh_token
        if not access_token or not refresh_token:
            raise AuthError("Microsoft returned an incomplete token response. Please sign in again.")
        return MicrosoftTokens(access_token=access_token, refresh_token=refresh_token)

    # Step 1a: ask Microsoft for a device code to show the user.
    def start_device_flow(self) -> DeviceCode:
        response = self._post(
            DEVICE_CODE_URL, "Microsoft sign-in", data={"client_id": self.client_id, "scope": SCOPES}
        )
        data = self._json_or_empty(response)
        if response.status_code >= 500:
            raise TransientError("Microsoft sign-in is temporarily unavailable. Try again shortly.", response.status_code)
        if response.status_code != 200:
            raise AuthError(describe_microsoft_error(data, response.status_code))
        try:
            return DeviceCode(
                device_code=data["device_code"],
                user_code=data["user_code"],
                verification_uri=data.get("verification_uri", "https://www.microsoft.com/link"),
                expires_in=int(data.get("expires_in", 900)),
                interval=int(data.get("interval", 5)),
                message=data.get("message", ""),
            )
        except (KeyError, TypeError, ValueError):
            raise AuthError("Microsoft returned an unexpected device-code response.") from None

    # Step 1b: poll until the user finishes signing in, at the pace Microsoft asks for.
    def wait_for_device_flow(self, device_code: DeviceCode, cancel_event: threading.Event) -> MicrosoftTokens:
        interval = max(device_code.interval, 1)
        deadline = time.monotonic() + device_code.expires_in
        while True:
            if cancel_event.wait(interval):
                raise AuthCancelled("Sign-in cancelled.")
            if time.monotonic() > deadline:
                raise AuthError("The sign-in code expired before it was used. Click Sign in to get a new one.")
            try:
                response = self._post(
                    TOKEN_URL,
                    "Microsoft sign-in",
                    data={
                        "grant_type": DEVICE_CODE_GRANT,
                        "client_id": self.client_id,
                        "device_code": device_code.device_code,
                    },
                )
            except TransientError:
                interval += SLOW_DOWN_EXTRA_SECONDS  # network blip: keep waiting, but slower
                continue
            data = self._json_or_empty(response)
            if response.status_code == 200:
                return self._microsoft_tokens(data)
            error = data.get("error", "")
            if error == "authorization_pending":
                continue
            if error == "slow_down" or response.status_code == 429 or response.status_code >= 500:
                interval += SLOW_DOWN_EXTRA_SECONDS
                continue
            if error in ("authorization_declined", "access_denied"):
                raise AuthError("Sign-in was declined in the browser.")
            if error in ("expired_token", "code_expired"):
                raise AuthError("The sign-in code expired before it was used. Click Sign in to get a new one.")
            raise AuthError(describe_microsoft_error(data, response.status_code))

    def refresh(self, refresh_token: str) -> MicrosoftTokens:
        response = self._post(
            TOKEN_URL,
            "Microsoft token refresh",
            data={
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "refresh_token": refresh_token,
                "scope": SCOPES,
            },
        )
        data = self._json_or_empty(response)
        if response.status_code == 200:
            return self._microsoft_tokens(data, previous_refresh_token=refresh_token)
        if response.status_code >= 500 or response.status_code == 429:
            raise TransientError("Microsoft token refresh: temporary error", response.status_code)
        if data.get("error") in ("invalid_grant", "interaction_required", "consent_required"):
            raise AuthError(
                "Your saved Microsoft sign-in expired or was revoked. Click 'Sign in with Microsoft' again.",
                requires_sign_in=True,
            )
        raise AuthError(describe_microsoft_error(data, response.status_code))

    # Steps 2-4.
    def login_minecraft(self, microsoft_access_token: str) -> MinecraftToken:
        xbox_token, user_hash = self._xbox_live_token(microsoft_access_token)
        xsts_token, user_hash = self._xsts_token(xbox_token, user_hash)
        return self._minecraft_token(user_hash, xsts_token)

    def _xbox_live_token(self, microsoft_access_token: str) -> tuple[str, str]:
        response = self._post(
            XBL_AUTH_URL,
            "Xbox Live sign-in",
            json={
                "Properties": {
                    "AuthMethod": "RPS",
                    "SiteName": "user.auth.xboxlive.com",
                    "RpsTicket": f"d={microsoft_access_token}",
                },
                "RelyingParty": "http://auth.xboxlive.com",
                "TokenType": "JWT",
            },
        )
        if response.status_code >= 500:
            raise TransientError("Xbox Live sign-in: temporary error", response.status_code)
        if response.status_code != 200:
            raise AuthError(f"Xbox Live rejected the Microsoft sign-in (HTTP {response.status_code}).")
        data = self._json_or_empty(response)
        try:
            return data["Token"], data["DisplayClaims"]["xui"][0]["uhs"]
        except (KeyError, IndexError, TypeError):
            raise AuthError("Xbox Live returned an unexpected response.") from None

    def _xsts_token(self, xbox_token: str, user_hash: str) -> tuple[str, str]:
        response = self._post(
            XSTS_AUTH_URL,
            "Xbox Live authorization",
            json={
                "Properties": {"SandboxId": "RETAIL", "UserTokens": [xbox_token]},
                "RelyingParty": "rp://api.minecraftservices.com/",
                "TokenType": "JWT",
            },
        )
        data = self._json_or_empty(response)
        if response.status_code == 401:
            xerr = data.get("XErr")
            try:
                xerr = int(xerr)
            except (TypeError, ValueError):
                pass
            raise AuthError(XSTS_ERRORS.get(xerr, f"Xbox Live refused authorization (XErr {xerr})."))
        if response.status_code >= 500:
            raise TransientError("Xbox Live authorization: temporary error", response.status_code)
        if response.status_code != 200:
            raise AuthError(f"Xbox Live authorization failed (HTTP {response.status_code}).")
        try:
            token = data["Token"]
            claims = data.get("DisplayClaims", {}).get("xui", [])
            return token, (claims[0].get("uhs") if claims else None) or user_hash
        except (KeyError, AttributeError, TypeError):
            raise AuthError("Xbox Live returned an unexpected authorization response.") from None

    def _minecraft_token(self, user_hash: str, xsts_token: str) -> MinecraftToken:
        response = self._post(
            MC_LOGIN_URL,
            "Minecraft sign-in",
            json={"identityToken": f"XBL3.0 x={user_hash};{xsts_token}"},
        )
        status = response.status_code
        if status == 403:
            raise AppNotApprovedError(f"{APP_NOT_APPROVED_MESSAGE}\nServer said: {error_body(response) or '(empty)'}")
        if status == 429:
            raise RateLimitedError(
                "Minecraft sign-in: rate limited (HTTP 429)",
                status,
                error_body(response),
                parse_retry_after(response.headers.get("Retry-After")),
            )
        if status >= 500:
            raise TransientError("Minecraft sign-in: temporary error", status, error_body(response))
        if status != 200:
            raise AuthError(f"Minecraft sign-in failed (HTTP {status}): {error_body(response)}")
        data = self._json_or_empty(response)
        if not data.get("access_token"):
            raise AuthError("Minecraft sign-in returned no access token.")
        return MinecraftToken(access_token=data["access_token"], expires_in=int(data.get("expires_in", 86400)))


class AuthSession:
    """Hands out a valid Minecraft access token, refreshing it when needed. Thread-safe."""

    def __init__(
        self,
        authenticator: MicrosoftAuthenticator,
        store: SecureStore,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.authenticator = authenticator
        self._store = store
        self._clock = clock
        self._lock = threading.RLock()
        self._refresh_token: str | None = None
        self._access_token: str | None = None
        self._expires_at = 0.0
        # Set once Minecraft Services refuses this client ID; the GUI then locks Live mode.
        self.app_not_approved = False
        self._load()

    @property
    def client_id(self) -> str:
        return self.authenticator.client_id

    @property
    def is_signed_in(self) -> bool:
        with self._lock:
            return self._refresh_token is not None

    def _load(self) -> None:
        data = self._store.load()
        if not data or data.get("client_id") != self.client_id or not data.get("refresh_token"):
            return
        self._refresh_token = data["refresh_token"]
        self._access_token = data.get("mc_access_token")
        self._expires_at = float(data.get("mc_expires_at", 0.0))
        logger.info("Loaded saved Microsoft sign-in (encrypted with Windows DPAPI).")

    def _persist(self) -> None:
        try:
            self._store.save(
                {
                    "version": CREDENTIALS_FORMAT_VERSION,
                    "client_id": self.client_id,
                    "refresh_token": self._refresh_token,
                    "mc_access_token": self._access_token,
                    "mc_expires_at": self._expires_at,
                }
            )
        except OSError as exc:
            logger.warning("Could not save credentials securely (%s). You'll need to sign in next launch.", type(exc).__name__)

    def complete_sign_in(self, tokens: MicrosoftTokens) -> None:
        """Finish the device-code sign-in: exchange the Microsoft token for a Minecraft one."""
        with self._lock:
            self._refresh_token = tokens.refresh_token
            self._access_token = None
            self._persist()
            self._login_minecraft(tokens.access_token)
        logger.info("Signed in with Microsoft; Minecraft access token obtained.")

    def _login_minecraft(self, microsoft_access_token: str) -> None:
        try:
            token = self.authenticator.login_minecraft(microsoft_access_token)
        except AppNotApprovedError:
            # A Microsoft sign-in this client ID can't use is not worth keeping.
            self.app_not_approved = True
            self.sign_out()
            raise
        self.app_not_approved = False
        self._set_minecraft_token(token)

    def _set_minecraft_token(self, token: MinecraftToken) -> None:
        self._access_token = token.access_token
        self._expires_at = self._clock() + token.expires_in
        self._persist()

    def get_token(self) -> str:
        with self._lock:
            if self._access_token and self._clock() < self._expires_at - TOKEN_REFRESH_MARGIN_SECONDS:
                return self._access_token
            if not self._refresh_token:
                raise AuthError("Not signed in. Click 'Sign in with Microsoft' first.", requires_sign_in=True)
            logger.info("Refreshing the Minecraft access token.")
            try:
                tokens = self.authenticator.refresh(self._refresh_token)
            except AuthError as exc:
                if exc.requires_sign_in:
                    self.sign_out()
                raise
            # Microsoft may rotate the refresh token; save it before anything else can fail.
            self._refresh_token = tokens.refresh_token
            self._access_token = None
            self._persist()
            self._login_minecraft(tokens.access_token)
            return self._access_token

    def invalidate(self) -> None:
        """Forget the Minecraft token so the next call refreshes it (after an HTTP 401)."""
        with self._lock:
            self._access_token = None
            self._expires_at = 0.0

    def sign_out(self) -> None:
        with self._lock:
            self._refresh_token = None
            self._access_token = None
            self._expires_at = 0.0
            self._store.clear()
        logger.info("Signed out; saved credentials deleted.")
