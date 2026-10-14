"""Exception types shared by the auth, API and monitor layers."""

from __future__ import annotations


class ApiError(Exception):
    """A Minecraft Services or Xbox request returned an unexpected response.

    `body` is already truncated and scrubbed of secrets, so it is safe to show
    in the UI and write to the log.
    """

    def __init__(
        self,
        message: str,
        status: int | None = None,
        body: str = "",
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.body = body
        self.retry_after = retry_after


class RateLimitedError(ApiError):
    """HTTP 429. `retry_after` holds the server's Retry-After in seconds, if sent."""


class UnauthorizedError(ApiError):
    """HTTP 401: the Minecraft access token is missing, expired or revoked."""


class TransientError(ApiError):
    """Network failure, timeout or HTTP 5xx: the request's outcome is unknown."""


class AuthError(Exception):
    """Sign-in failed in a way the user has to fix (not a temporary glitch)."""

    def __init__(self, message: str, requires_sign_in: bool = False) -> None:
        super().__init__(message)
        self.requires_sign_in = requires_sign_in


class AuthCancelled(AuthError):
    """The user closed the sign-in dialog."""


class AccountNotEligibleError(Exception):
    """The account can't claim a name: no Java profile, or a name change isn't allowed yet."""
