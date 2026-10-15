"""Minecraft Services API client (api.minecraftservices.com).

Endpoint shapes follow the community documentation at
https://minecraft.wiki/w/Mojang_API; Mojang does not publish an official
third-party reference for them. Every call needs a Minecraft access token.

The client never retries on its own: each method sends exactly one request
and reports the outcome as a return value or a typed exception, so the
monitor decides about every retry explicitly.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Callable
from urllib.parse import quote

import requests

from . import __version__
from .errors import ApiError, RateLimitedError, TransientError, UnauthorizedError
from .logging_setup import LOGGER_NAME, redact
from .ratelimit import parse_retry_after

logger = logging.getLogger(f"{LOGGER_NAME}.api")

API_BASE_URL = "https://api.minecraftservices.com"
REQUEST_TIMEOUT_SECONDS = (10, 20)  # (connect, read)
USER_AGENT = f"MCNameClaimer/{__version__}"
MAX_ERROR_BODY_CHARS = 500
JAVA_ENTITLEMENTS = frozenset({"product_minecraft", "game_minecraft"})

AVAILABLE = "AVAILABLE"
DUPLICATE = "DUPLICATE"
NOT_ALLOWED = "NOT_ALLOWED"


def error_body(response: requests.Response) -> str:
    """The response body, scrubbed of secrets and shortened for display."""
    try:
        text = response.text or ""
    except Exception:  # a broken body must never hide the real error
        text = ""
    return redact(text.strip())[:MAX_ERROR_BODY_CHARS]


def raise_for_response(response: requests.Response, context: str) -> None:
    """Translate an HTTP error status into the matching typed exception."""
    status = response.status_code
    if status < 400:
        return
    body = error_body(response)
    if status == 429:
        retry_after = parse_retry_after(response.headers.get("Retry-After"))
        raise RateLimitedError(f"{context}: rate limited (HTTP 429)", status, body, retry_after)
    if status == 401:
        raise UnauthorizedError(f"{context}: access token rejected (HTTP 401)", status, body)
    if status >= 500:
        raise TransientError(f"{context}: server error (HTTP {status})", status, body)
    raise ApiError(f"{context}: HTTP {status}", status, body)


def parse_json(response: requests.Response, context: str) -> Any:
    try:
        return response.json()
    except ValueError:
        raise ApiError(
            f"{context}: response was not valid JSON", response.status_code, error_body(response)
        ) from None


class MinecraftServicesClient:
    def __init__(
        self,
        token_provider: Callable[[], str],
        on_unauthorized: Callable[[], None] | None = None,
        session: requests.Session | None = None,
        base_url: str = API_BASE_URL,
    ) -> None:
        self._token_provider = token_provider
        self._on_unauthorized = on_unauthorized
        self._session = session or requests.Session()
        self._base_url = base_url.rstrip("/")

    def _request(
        self, method: str, path: str, context: str, allow_not_found: bool = False
    ) -> requests.Response:
        headers = {
            "Authorization": f"Bearer {self._token_provider()}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        try:
            response = self._session.request(
                method, f"{self._base_url}{path}", headers=headers, timeout=REQUEST_TIMEOUT_SECONDS
            )
        except requests.Timeout:
            raise TransientError(f"{context}: request timed out") from None
        except requests.RequestException as exc:
            raise TransientError(f"{context}: network error ({type(exc).__name__})") from None

        if response.status_code == 401 and self._on_unauthorized:
            self._on_unauthorized()
        if allow_not_found and response.status_code == 404:
            return response
        raise_for_response(response, context)
        return response

    def check_availability(self, name: str) -> str:
        """Returns AVAILABLE, DUPLICATE or NOT_ALLOWED."""
        context = "Availability check"
        response = self._request("GET", f"/minecraft/profile/name/{quote(name, safe='')}/available", context)
        data = parse_json(response, context)
        status = data.get("status") if isinstance(data, dict) else None
        if not isinstance(status, str):
            raise ApiError(f"{context}: response had no status field", response.status_code, error_body(response))
        return status

    def get_profile(self) -> dict | None:
        """The account's Java profile, or None when the account has no Java profile (HTTP 404)."""
        context = "Profile lookup"
        response = self._request("GET", "/minecraft/profile", context, allow_not_found=True)
        if response.status_code == 404:
            return None
        return parse_json(response, context)

    def get_name_change_info(self) -> dict:
        """{'changedAt': ..., 'createdAt': ..., 'nameChangeAllowed': bool}"""
        context = "Name-change eligibility"
        return parse_json(self._request("GET", "/minecraft/profile/namechange", context), context)

    def get_java_entitlements(self) -> list[str]:
        """Entitlement names on the account, e.g. ['product_minecraft', 'game_minecraft']."""
        context = "Entitlement lookup"
        response = self._request("GET", f"/entitlements/license?requestId={uuid.uuid4()}", context)
        if not response.content:
            return []  # community docs: empty payload when not owned or when playing via Game Pass
        data = parse_json(response, context)
        items = data.get("items", []) if isinstance(data, dict) else []
        return [item["name"] for item in items if isinstance(item, dict) and "name" in item]

    def change_name(self, name: str) -> dict:
        """Send ONE name-change request. Returns the updated profile on HTTP 200."""
        context = "Name change"
        response = self._request("PUT", f"/minecraft/profile/name/{quote(name, safe='')}", context)
        return parse_json(response, context)
