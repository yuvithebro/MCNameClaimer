"""Drives the real Tk window in Simulation mode, the way a user would."""

import time

import pytest

tk = pytest.importorskip("tkinter")

from mcclaimer import APP_TITLE, DISCLAIMER  # noqa: E402
from mcclaimer.config import Settings, load_settings  # noqa: E402
from mcclaimer.gui import LIVE_LOCKED_MESSAGE, ClaimerApp  # noqa: E402
from mcclaimer.monitor import MonitorState  # noqa: E402
from mcclaimer.secure_store import SecureStore  # noqa: E402

TIMEOUT_SECONDS = 30
CLIENT_ID = "00000000-1111-2222-3333-444444444444"  # placeholder, not a real app


@pytest.fixture(scope="session")
def tk_root():
    # One Tcl interpreter per test session: creating many tk.Tk() instances in one
    # process intermittently fails on Windows ("tk wasn't installed properly").
    # The real app only ever creates one.
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"No display available for Tk: {exc}")
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def app(tk_root, tmp_path):
    window = tk.Toplevel(tk_root)
    window.withdraw()
    settings = Settings(simulation_mode=True, simulation_scenario="available_after_checks", interval_seconds=1)
    application = ClaimerApp(window, settings, settings_file=tmp_path / "settings.json",
                             store=SecureStore(tmp_path / "credentials.bin"))
    yield application
    if not application._closing:
        application.close()


def pump_until(app, predicate):
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        app.root.update()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_simulation_claims_name_end_to_end(app):
    app.username_var.set("DreamName")
    app.start()
    assert str(app.stop_button["state"]) == "normal"
    assert pump_until(app, lambda: app.state_var.get() == MonitorState.CLAIMED.value)
    assert pump_until(app, lambda: not app._running)
    assert app.claim_var.get() == "Success: your profile name is now DreamName."
    assert app.availability_var.get() == "AVAILABLE"
    assert app.last_check_var.get() != "-"
    assert str(app.start_button["state"]) == "normal"
    assert str(app.stop_button["state"]) == "disabled"
    log = app.log_text.get("1.0", "end")
    assert "Check #4" in log and "AVAILABLE" in log


def test_stop_button_stops_monitoring(app):
    app.scenario_var.set("Name never frees up")
    app.username_var.set("NeverFree")
    app.start()
    assert pump_until(app, lambda: app.state_var.get() == MonitorState.MONITORING.value)
    assert pump_until(app, lambda: app.next_check_var.get() != "-")
    app.stop()
    assert pump_until(app, lambda: app.state_var.get() == MonitorState.STOPPED.value)
    assert pump_until(app, lambda: not app._running)


def test_invalid_username_does_not_start(app):
    app.username_var.set("no spaces")
    app.start()
    assert not app._running
    assert "Only letters" in app.form_error_var.get()


def confirm_approval(app, client_id=CLIENT_ID):
    app.client_id_var.set(client_id)
    app.approval_var.set(True)
    app._on_approval_toggle()


def switch_to_live(app):
    app.mode_var.set("live")
    app._on_mode_change()


def test_live_mode_enforces_minimum_interval_lock_and_sign_in(app):
    switch_to_live(app)
    app.username_var.set("Notch")
    app.interval_var.set("5")
    app.start()
    assert not app._running
    assert "at most every 20 seconds" in app.form_error_var.get()
    app.interval_var.set("30")
    app.start()
    assert not app._running
    assert "locked" in app.form_error_var.get()
    confirm_approval(app)
    app.start()
    assert not app._running
    assert "Sign in" in app.form_error_var.get()


def test_live_mode_is_locked_until_approval_is_confirmed(app):
    switch_to_live(app)
    app.client_id_var.set(CLIENT_ID)
    assert str(app.signin_button["state"]) == "disabled"
    assert app.account_var.get() == LIVE_LOCKED_MESSAGE
    app.sign_in()
    assert not app._signing_in and app._dialog is None and app._session is None
    confirm_approval(app)
    assert str(app.signin_button["state"]) == "normal"


def test_editing_the_client_id_withdraws_the_approval(app):
    switch_to_live(app)
    confirm_approval(app)
    app.client_id_var.set(CLIENT_ID + "0")
    assert app.approval_var.get() is False
    assert str(app.signin_button["state"]) == "disabled"


def test_refused_client_id_locks_live_mode_again(app, tmp_path):
    switch_to_live(app)
    confirm_approval(app)
    app._on_app_not_approved("Minecraft Services refused this Azure app (HTTP 403)")
    assert app.approval_var.get() is False
    assert str(app.signin_button["state"]) == "disabled"
    assert "HTTP 403" in app.account_var.get()
    assert not load_settings(tmp_path / "settings.json").live_mode_unlocked


def test_approval_is_saved_for_that_client_id(app, tmp_path):
    switch_to_live(app)
    confirm_approval(app)
    app.close()
    saved = load_settings(tmp_path / "settings.json")
    assert saved.client_id == CLIENT_ID
    assert saved.live_mode_unlocked


def test_window_shows_unofficial_title_and_disclaimer(app):
    assert app.root.title() == APP_TITLE
    assert not APP_TITLE.startswith("Minecraft")
    assert " ".join(app.disclaimer_label.cget("text").split()) == DISCLAIMER


def test_close_saves_settings(app, tmp_path):
    app.username_var.set("SavedName")
    app.close()
    assert load_settings(tmp_path / "settings.json").target_username == "SavedName"
