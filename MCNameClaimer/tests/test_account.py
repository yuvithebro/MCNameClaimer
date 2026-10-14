import pytest

from fakes import ScriptedApi, http_error

from mcclaimer.account import ensure_can_claim, verify_account
from mcclaimer.errors import AccountNotEligibleError


def test_owner_with_name_change_allowed():
    info = verify_account(ScriptedApi(profile_name="Steve"))
    assert info.profile_name == "Steve"
    assert info.owns_java_entitlement is True
    ensure_can_claim(info)
    assert "Name change: allowed" in info.describe()


def test_game_pass_style_empty_entitlements_still_eligible():
    api = ScriptedApi()
    api.get_java_entitlements = lambda: []
    info = verify_account(api)
    assert info.owns_java_entitlement is False
    assert "Game Pass" in info.describe()
    ensure_can_claim(info)


def test_entitlement_lookup_failure_is_not_fatal():
    api = ScriptedApi()

    def failing():
        raise http_error(503)

    api.get_java_entitlements = failing
    assert verify_account(api).owns_java_entitlement is None


def test_cooldown_message_includes_reopen_date():
    info = verify_account(ScriptedApi(name_change_allowed=False))
    with pytest.raises(AccountNotEligibleError, match="2025-01-31"):
        ensure_can_claim(info)


def test_no_profile_is_rejected():
    with pytest.raises(AccountNotEligibleError):
        verify_account(ScriptedApi(profiles=[None]))
