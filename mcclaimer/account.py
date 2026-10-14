"""Checks that the signed-in account can actually claim a Java Edition name."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from .api import JAVA_ENTITLEMENTS
from .errors import AccountNotEligibleError, ApiError

# Mojang allows one Java Edition name change every 30 days.
NAME_CHANGE_COOLDOWN_DAYS = 30


class AccountApi(Protocol):
    def get_profile(self) -> dict | None: ...
    def get_java_entitlements(self) -> list[str]: ...
    def get_name_change_info(self) -> dict: ...


@dataclass(frozen=True)
class AccountInfo:
    profile_name: str
    profile_id: str
    # None when the entitlement lookup itself failed.
    owns_java_entitlement: bool | None
    name_change_allowed: bool
    name_changed_at: str | None

    def describe(self) -> str:
        if self.owns_java_entitlement:
            ownership = "Java Edition: owned"
        elif self.owns_java_entitlement is None:
            ownership = "Java Edition: profile found (entitlement check unavailable)"
        else:
            ownership = "Java Edition: profile found, no purchase entitlement listed (normal for Xbox Game Pass)"
        if self.name_change_allowed:
            eligibility = "Name change: allowed"
        else:
            eligibility = f"Name change: NOT allowed yet{_next_change_hint(self.name_changed_at)}"
        return f"Signed in as {self.profile_name}  |  {ownership}  |  {eligibility}"


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _next_change_hint(changed_at: str | None) -> str:
    changed = _parse_timestamp(changed_at)
    if changed is None:
        return ""
    next_allowed = changed + timedelta(days=NAME_CHANGE_COOLDOWN_DAYS)
    return f" (last changed {changed:%Y-%m-%d}; expected to reopen around {next_allowed:%Y-%m-%d})"


def verify_account(api: AccountApi) -> AccountInfo:
    """Look up the profile, ownership and name-change eligibility.

    Raises AccountNotEligibleError when the account has no Java profile at all.
    """
    profile = api.get_profile()
    if profile is None:
        raise AccountNotEligibleError(
            "This Microsoft account has no Minecraft: Java Edition profile. Buy Java Edition "
            "(or redeem Game Pass) and create your profile at minecraft.net first."
        )
    try:
        entitlements = set(api.get_java_entitlements())
        owns_java: bool | None = bool(entitlements & JAVA_ENTITLEMENTS)
    except ApiError:
        owns_java = None  # informational only; the Java profile above is the hard requirement
    name_change = api.get_name_change_info()
    return AccountInfo(
        profile_name=str(profile.get("name", "")),
        profile_id=str(profile.get("id", "")),
        owns_java_entitlement=owns_java,
        name_change_allowed=bool(name_change.get("nameChangeAllowed")),
        name_changed_at=name_change.get("changedAt"),
    )


def ensure_can_claim(info: AccountInfo) -> None:
    if not info.name_change_allowed:
        raise AccountNotEligibleError(
            "Mojang says this account can't change its name right now (one change every "
            f"{NAME_CHANGE_COOLDOWN_DAYS} days){_next_change_hint(info.name_changed_at)}."
        )
