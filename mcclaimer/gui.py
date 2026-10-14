"""Desktop window (tkinter).

All network work runs on background threads. Those threads never touch Tk;
they post events to a queue that the UI thread drains, so the window never
freezes and Tk is only used from the thread that created it.
"""

from __future__ import annotations

import logging
import os
import queue
import sys
import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any, Callable

from . import __version__
from .account import verify_account
from .api import AVAILABLE, DUPLICATE, NOT_ALLOWED, MinecraftServicesClient
from .auth import AuthSession, DeviceCode, MicrosoftAuthenticator
from .config import Settings, save_settings
from .errors import AccountNotEligibleError, ApiError, AuthCancelled, AuthError
from .logging_setup import LOGGER_NAME, RedactingFormatter
from .monitor import LIVE_TIMING, SIMULATION_TIMING, MonitorState, MonitorStatus, NameMonitor
from .paths import credentials_path, settings_path
from .ratelimit import (
    AVAILABILITY_BUDGET_REQUESTS,
    AVAILABILITY_LIMIT_REQUESTS,
    AVAILABILITY_LIMIT_WINDOW_SECONDS,
    DEFAULT_INTERVAL_SECONDS,
    MAX_INTERVAL_SECONDS,
    MIN_LIVE_INTERVAL_SECONDS,
    MIN_SIMULATION_INTERVAL_SECONDS,
    SlidingWindowLimiter,
)
from .secure_store import SecureStore
from .simulation import SCENARIOS, SimulatedMinecraftAPI
from .validation import MAX_USERNAME_LENGTH, validate_username

logger = logging.getLogger(f"{LOGGER_NAME}.gui")

EVENT_POLL_MS = 150
CLOCK_TICK_MS = 1000
MAX_LOG_LINES = 500
THREAD_JOIN_TIMEOUT_SECONDS = 2.0
# Simulation makes no requests, so its budget only guards against runaway loops.
SIMULATION_BUDGET = (1000, 60)
INITIAL_WRAP = "5i"
MIN_WRAP_PIXELS = 120
WRAP_MARGIN_PIXELS = 4
LOG_HEIGHT_LINES = 7

COLOR_MUTED = "#57606a"
COLOR_GOOD = "#1a7f37"
COLOR_WARN = "#9a6700"
COLOR_BAD = "#cf222e"
COLOR_INFO = "#0969da"

AVAILABILITY_COLORS = {AVAILABLE: COLOR_GOOD, DUPLICATE: COLOR_WARN, NOT_ALLOWED: COLOR_BAD}
STATE_COLORS = {
    MonitorState.CLAIMED: COLOR_GOOD,
    MonitorState.FAILED: COLOR_BAD,
    MonitorState.BACKING_OFF: COLOR_WARN,
    MonitorState.MONITORING: COLOR_INFO,
    MonitorState.CLAIMING: COLOR_INFO,
    MonitorState.VERIFYING_CLAIM: COLOR_INFO,
}
_GOOD_CLAIM_PREFIXES = ("Success", "Already yours")
_BAD_CLAIM_PREFIXES = ("Failed", "Not claimed", "Unconfirmed", "Unknown")


def _format_time(moment: datetime | None) -> str:
    if moment is None:
        return "-"
    if moment.date() == datetime.now().date():
        return f"{moment:%H:%M:%S}"
    return f"{moment:%Y-%m-%d %H:%M:%S}"


def _display_path(path: Path) -> str:
    """Shorten %LOCALAPPDATA% so a long profile path doesn't stretch the window."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    text = str(path)
    if local_app_data and text.lower().startswith(local_app_data.lower()):
        return "%LOCALAPPDATA%" + text[len(local_app_data):]
    return text


def _format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


class QueueLogHandler(logging.Handler):
    """Forwards log lines to the UI thread through the event queue."""

    def __init__(self, events: queue.Queue) -> None:
        super().__init__(level=logging.INFO)
        self._events = events

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._events.put(("log", self.format(record)))
        except Exception:
            self.handleError(record)


class DeviceCodeDialog:
    """Shows the Microsoft sign-in code. The password is typed on Microsoft's site, never here."""

    def __init__(self, parent: tk.Misc, on_cancel: Callable[[], None]) -> None:
        self._on_cancel = on_cancel
        self._code: DeviceCode | None = None
        self.window = tk.Toplevel(parent)
        self.window.title("Sign in with Microsoft")
        self.window.transient(parent)
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", self.cancel)

        frame = ttk.Frame(self.window, padding=20)
        frame.grid(sticky="nsew")
        ttk.Label(frame, text="Sign in with Microsoft", style="Title.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        self._instructions = tk.StringVar(value="Requesting a sign-in code from Microsoft...")
        ttk.Label(frame, textvariable=self._instructions, wraplength="4i", justify="left").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(8, 12)
        )
        self._code_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self._code_var, style="Code.TLabel").grid(
            row=2, column=0, columnspan=2, pady=(0, 14)
        )
        self._open_button = ttk.Button(
            frame, text="Copy code and open sign-in page", command=self._copy_and_open, state="disabled"
        )
        self._open_button.grid(row=3, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(frame, text="Cancel", command=self.cancel).grid(row=3, column=1, sticky="ew")
        ttk.Label(
            frame,
            text="You type your password on Microsoft's website. This app never sees it.",
            style="Muted.TLabel",
            wraplength="4i",
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(14, 0))

    def show_code(self, code: DeviceCode) -> None:
        self._code = code
        self._code_var.set(code.user_code)
        self._instructions.set(
            "1. Click the button below. It copies the code and opens Microsoft's sign-in page.\n"
            f"2. Paste the code at {code.verification_uri} and sign in.\n"
            "3. Come back here. This window closes by itself."
        )
        self._open_button.configure(state="normal")

    def _copy_and_open(self) -> None:
        if self._code is None:
            return
        self.window.clipboard_clear()
        self.window.clipboard_append(self._code.user_code)
        webbrowser.open(self._code.verification_uri)

    def cancel(self) -> None:
        self._on_cancel()
        self.close()

    def close(self) -> None:
        try:
            self.window.destroy()
        except tk.TclError:
            pass  # already closed


class ClaimerApp:
    def __init__(
        self,
        root: tk.Tk | tk.Toplevel,
        settings: Settings,
        *,
        settings_file: Path | None = None,
        store: SecureStore | None = None,
        log_file: Path | None = None,
    ) -> None:
        self.root = root
        self._settings_file = settings_file or settings_path()
        self._store = store or SecureStore(credentials_path())
        self._log_file = log_file
        self._events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._live_limiter = SlidingWindowLimiter(AVAILABILITY_BUDGET_REQUESTS, AVAILABILITY_LIMIT_WINDOW_SECONDS)
        self._simulation_limiter = SlidingWindowLimiter(*SIMULATION_BUDGET)
        self._session: AuthSession | None = None
        self._live_api: MinecraftServicesClient | None = None
        self._account_checked_for: str | None = None
        self._live_account_text: tuple[str, str] | None = None
        self._running = False
        self._signing_in = False
        self._stop_event: threading.Event | None = None
        self._signin_cancel: threading.Event | None = None
        self._monitor_thread: threading.Thread | None = None
        self._signin_thread: threading.Thread | None = None
        self._dialog: DeviceCodeDialog | None = None
        self._next_check: datetime | None = None
        self._closing = False
        self._event_handlers: dict[str, Callable[[Any], None]] = {
            "log": self._append_log,
            "status": self._render_status,
            "monitor_done": self._on_monitor_done,
            "device_code": self._on_device_code,
            "signin_done": self._on_signin_done,
            "signin_failed": self._on_signin_failed,
            "account": self._on_account_text,
        }

        self._create_variables(settings)
        self._build_ui()

        app_logger = logging.getLogger(LOGGER_NAME)
        if app_logger.level == logging.NOTSET:
            app_logger.setLevel(logging.INFO)
        self._log_handler = QueueLogHandler(self._events)
        self._log_handler.setFormatter(RedactingFormatter("%(asctime)s  %(message)s", "%H:%M:%S"))
        app_logger.addHandler(self._log_handler)

        self._on_mode_change()
        self._on_username_change()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(EVENT_POLL_MS, self._drain_events)
        self.root.after(CLOCK_TICK_MS, self._tick)

    # ------------------------------------------------------------ building
    def _create_variables(self, settings: Settings) -> None:
        self.mode_var = tk.StringVar(value="simulation" if settings.simulation_mode else "live")
        self.scenario_var = tk.StringVar(value=SCENARIOS[settings.simulation_scenario].title)
        self.scenario_description_var = tk.StringVar()
        self.client_id_var = tk.StringVar(value=settings.client_id)
        self.account_var = tk.StringVar()
        self.username_var = tk.StringVar(value=settings.target_username)
        self.username_hint_var = tk.StringVar()
        self.interval_var = tk.StringVar(value=str(settings.interval_seconds))
        self.interval_hint_var = tk.StringVar()
        self.form_error_var = tk.StringVar()
        self.state_var = tk.StringVar(value=MonitorState.IDLE.value)
        self.target_var = tk.StringVar(value="-")
        self.availability_var = tk.StringVar(value="-")
        self.last_check_var = tk.StringVar(value="-")
        self.next_check_var = tk.StringVar(value="-")
        self.claim_var = tk.StringVar(value="-")
        self.message_var = tk.StringVar(value="Enter a username and press Start.")

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure(".", font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 16))
        style.configure("Muted.TLabel", foreground=COLOR_MUTED)
        style.configure("Key.TLabel", foreground=COLOR_MUTED)
        style.configure("Value.TLabel", font=("Segoe UI Semibold", 10))
        style.configure("Big.TLabel", font=("Segoe UI Semibold", 13))
        style.configure("Error.TLabel", foreground=COLOR_BAD)
        style.configure("Code.TLabel", font=("Consolas", 26, "bold"))
        style.configure("TLabelframe.Label", font=("Segoe UI Semibold", 10))

    @staticmethod
    def _wrapping_label(parent: tk.Misc, **options: Any) -> ttk.Label:
        """A label that re-wraps its text to the width its container actually has."""
        label = ttk.Label(parent, wraplength=INITIAL_WRAP, justify="left", **options)

        def rewrap(event: tk.Event) -> None:
            available = event.width - label.winfo_x() - WRAP_MARGIN_PIXELS
            label.configure(wraplength=max(MIN_WRAP_PIXELS, available))

        parent.bind("<Configure>", rewrap, add="+")
        return label

    def _build_ui(self) -> None:
        self.root.title(f"Minecraft Name Claimer {__version__}")
        self._configure_styles()
        outer = ttk.Frame(self.root, padding=(16, 12))
        outer.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)

        ttk.Label(outer, text="Minecraft Name Claimer", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        self._wrapping_label(
            outer,
            text="Watches one Java Edition username and claims it for your account as soon as Mojang reports it AVAILABLE.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(0, 10))

        sections = (
            self._build_mode_section(outer),
            self._build_account_section(outer),
            self._build_target_section(outer),
            self._build_status_section(outer),
        )
        for row, section in enumerate(sections, start=2):
            section.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        log_row = len(sections) + 2
        self._build_log_section(outer).grid(row=log_row, column=0, sticky="nsew")
        outer.rowconfigure(log_row, weight=1)
        self._fit_to_screen()

    def _fit_to_screen(self) -> None:
        """Open at the natural size, but never taller than the screen; the log shrinks first."""
        self.root.update_idletasks()
        inch = int(self.root.winfo_fpixels("1i"))
        width = self.root.winfo_reqwidth()
        height = min(self.root.winfo_reqheight(), self.root.winfo_screenheight() - inch)
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(min(width, int(6.5 * inch)), min(height, int(5 * inch)))

    def _build_mode_section(self, parent: ttk.Frame) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(parent, text="Mode", padding=10)
        frame.columnconfigure(0, weight=1)
        radios = ttk.Frame(frame)
        radios.grid(row=0, column=0, sticky="w")
        self._mode_radios = [
            ttk.Radiobutton(radios, text="Simulation (no real requests)", value="simulation",
                            variable=self.mode_var, command=self._on_mode_change),
            ttk.Radiobutton(radios, text="Live (your Microsoft account)", value="live",
                            variable=self.mode_var, command=self._on_mode_change),
        ]
        self._mode_radios[0].grid(row=0, column=0, sticky="w", padx=(0, 20))
        self._mode_radios[1].grid(row=0, column=1, sticky="w")

        scenario_row = ttk.Frame(frame)
        scenario_row.grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Label(scenario_row, text="Scenario:").grid(row=0, column=0, padx=(0, 8))
        self.scenario_combo = ttk.Combobox(
            scenario_row, textvariable=self.scenario_var, state="readonly", width=40,
            values=[scenario.title for scenario in SCENARIOS.values()],
        )
        self.scenario_combo.grid(row=0, column=1)
        self.scenario_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_scenario_change())
        self._wrapping_label(frame, textvariable=self.scenario_description_var, style="Muted.TLabel").grid(
            row=2, column=0, sticky="w", pady=(4, 0)
        )
        return frame

    def _build_account_section(self, parent: ttk.Frame) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(parent, text="Microsoft account (Live mode)", padding=10)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="Azure client ID:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.client_entry = ttk.Entry(frame, textvariable=self.client_id_var, width=38)
        self.client_entry.grid(row=0, column=1, sticky="ew")
        self.signin_button = ttk.Button(frame, text="Sign in with Microsoft", command=self.sign_in)
        self.signin_button.grid(row=0, column=2, padx=(8, 0))
        self.signout_button = ttk.Button(frame, text="Sign out", command=self.sign_out)
        self.signout_button.grid(row=0, column=3, padx=(8, 0))
        self.account_label = self._wrapping_label(frame, textvariable=self.account_var)
        self.account_label.grid(row=1, column=0, columnspan=4, sticky="w", pady=(8, 0))
        return frame

    def _build_target_section(self, parent: ttk.Frame) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(parent, text="Target", padding=10)
        frame.columnconfigure(0, weight=1)
        inputs = ttk.Frame(frame)
        inputs.grid(row=0, column=0, sticky="w")
        ttk.Label(inputs, text="Username:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.username_entry = ttk.Entry(inputs, textvariable=self.username_var, width=20, font=("Consolas", 12))
        self.username_entry.grid(row=0, column=1, sticky="w")
        ttk.Label(inputs, textvariable=self.username_hint_var, style="Muted.TLabel", width=6).grid(
            row=0, column=2, sticky="w", padx=(8, 16)
        )
        ttk.Label(inputs, text="Check every").grid(row=0, column=3, sticky="e", padx=(0, 6))
        self.interval_spin = ttk.Spinbox(
            inputs, textvariable=self.interval_var, from_=MIN_SIMULATION_INTERVAL_SECONDS,
            to=MAX_INTERVAL_SECONDS, width=6,
        )
        self.interval_spin.grid(row=0, column=4, sticky="w")
        ttk.Label(inputs, text="seconds").grid(row=0, column=5, sticky="w", padx=(6, 0))
        self._wrapping_label(frame, textvariable=self.interval_hint_var, style="Muted.TLabel").grid(
            row=1, column=0, sticky="w", pady=(6, 0)
        )

        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=0, sticky="w", pady=(10, 0))
        self.start_button = ttk.Button(buttons, text="Start", command=self.start, width=12)
        self.start_button.grid(row=0, column=0, padx=(0, 8))
        self.stop_button = ttk.Button(buttons, text="Stop", command=self.stop, width=12)
        self.stop_button.grid(row=0, column=1)
        self._form_error_label = self._wrapping_label(frame, textvariable=self.form_error_var, style="Error.TLabel")
        self._form_error_label.grid(row=3, column=0, sticky="w", pady=(6, 0))

        self.username_var.trace_add("write", lambda *_args: self._on_username_change())
        self.username_entry.bind("<Return>", lambda _event: self.start())
        return frame

    def _build_status_section(self, parent: ttk.Frame) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(parent, text="Status", padding=10)
        frame.columnconfigure(1, weight=1)
        rows = (
            ("State", self.state_var, "Big.TLabel"),
            ("Target username", self.target_var, "Value.TLabel"),
            ("Availability", self.availability_var, "Big.TLabel"),
            ("Last check", self.last_check_var, "Value.TLabel"),
            ("Next check", self.next_check_var, "Value.TLabel"),
            ("Claim result", self.claim_var, "Value.TLabel"),
        )
        value_labels = []
        for row, (key, variable, style) in enumerate(rows):
            ttk.Label(frame, text=key, style="Key.TLabel").grid(row=row, column=0, sticky="nw", padx=(0, 16), pady=1)
            label = self._wrapping_label(frame, textvariable=variable, style=style)
            label.grid(row=row, column=1, sticky="w", pady=1)
            value_labels.append(label)
        self._state_label, _, self._availability_label, _, _, self._claim_label = value_labels
        self._wrapping_label(frame, textvariable=self.message_var, style="Muted.TLabel").grid(
            row=len(rows), column=0, columnspan=2, sticky="w", pady=(6, 0)
        )
        return frame

    def _build_log_section(self, parent: ttk.Frame) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(parent, text="Activity log", padding=10)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.log_text = ScrolledText(frame, height=LOG_HEIGHT_LINES, wrap="word", state="disabled",
                                     font=("Consolas", 9), relief="flat", borderwidth=1)
        self.log_text.grid(row=0, column=0, columnspan=2, sticky="nsew")
        location = _display_path(self._log_file) if self._log_file else "not saved (test mode)"
        ttk.Label(frame, text=f"Also saved to {location}", style="Muted.TLabel").grid(
            row=1, column=0, sticky="w", pady=(6, 0)
        )
        self.open_log_button = ttk.Button(frame, text="Open log folder", command=self.open_log_folder)
        self.open_log_button.grid(row=1, column=1, sticky="e", pady=(6, 0))
        if self._log_file is None:
            self.open_log_button.configure(state="disabled")
        return frame

    # ------------------------------------------------------------ helpers
    @property
    def is_live(self) -> bool:
        return self.mode_var.get() == "live"

    def _scenario_key(self) -> str:
        title = self.scenario_var.get()
        return next((key for key, scenario in SCENARIOS.items() if scenario.title == title), next(iter(SCENARIOS)))

    def _set_account_text(self, text: str, color: str = "") -> None:
        self.account_var.set(text)
        self.account_label.configure(foreground=color)

    def _show_form_error(self, message: str) -> None:
        self.form_error_var.set(message)
        if message:
            self._form_error_label.grid()
        else:
            self._form_error_label.grid_remove()

    def _sync_controls(self) -> None:
        busy = self._running or self._signing_in
        live = self.is_live

        def enable(widget: ttk.Widget, enabled: bool) -> None:
            widget.configure(state="normal" if enabled else "disabled")

        for radio in self._mode_radios:
            enable(radio, not busy)
        self.scenario_combo.configure(state="readonly" if not live and not busy else "disabled")
        enable(self.client_entry, live and not busy)
        enable(self.signin_button, live and not busy)
        signed_in = self._session is not None and self._session.is_signed_in
        enable(self.signout_button, live and not busy and signed_in)
        enable(self.username_entry, not self._running)
        enable(self.interval_spin, not self._running)
        enable(self.start_button, not busy)
        enable(self.stop_button, self._running)

    def _current_settings(self) -> Settings:
        try:
            interval = int(self.interval_var.get().strip())
        except ValueError:
            interval = DEFAULT_INTERVAL_SECONDS
        return Settings(
            client_id=self.client_id_var.get().strip(),
            target_username=self.username_var.get().strip(),
            interval_seconds=interval,
            simulation_mode=not self.is_live,
            simulation_scenario=self._scenario_key(),
        )

    def _save_settings(self) -> None:
        save_settings(self._current_settings(), self._settings_file)

    def _session_for(self, client_id: str) -> AuthSession:
        if self._session is None or self._session.client_id != client_id:
            self._session = AuthSession(MicrosoftAuthenticator(client_id), self._store)
            self._live_api = MinecraftServicesClient(
                token_provider=self._session.get_token, on_unauthorized=self._session.invalidate
            )
        return self._session

    def _api_for_current_session(self) -> MinecraftServicesClient:
        assert self._live_api is not None
        return self._live_api

    # ------------------------------------------------------------ reactions
    def _on_mode_change(self) -> None:
        if self.is_live:
            self.interval_hint_var.set(
                f"Live minimum: {MIN_LIVE_INTERVAL_SECONDS}s. Mojang allows {AVAILABILITY_LIMIT_REQUESTS} "
                f"availability checks per 5 minutes per account; the app also caps itself at "
                f"{AVAILABILITY_BUDGET_REQUESTS} per 5 minutes and backs off on HTTP 429."
            )
            self._refresh_live_account()
        else:
            self.interval_hint_var.set(
                f"Simulation minimum: {MIN_SIMULATION_INTERVAL_SECONDS}s. Nothing is sent to Microsoft or Mojang."
            )
            self._set_account_text(
                "Simulation mode uses a fake account named SimPlayer. Your real account is never touched.",
                COLOR_MUTED,
            )
        self._on_scenario_change()
        self._sync_controls()

    def _on_scenario_change(self) -> None:
        description = SCENARIOS[self._scenario_key()].description
        self.scenario_description_var.set(description if not self.is_live else "Scenarios apply to Simulation mode only.")

    def _on_username_change(self) -> None:
        name = self.username_var.get().strip()
        self.username_hint_var.set(f"{len(name)}/{MAX_USERNAME_LENGTH}")
        problem = validate_username(name) if name else None
        self._show_form_error(problem or "")

    def _refresh_live_account(self) -> None:
        client_id = self.client_id_var.get().strip()
        if not client_id:
            self._set_account_text(
                "Enter your Azure app's client ID, then click Sign in. Setup steps: README.md, 'Live mode setup'."
            )
            return
        session = self._session_for(client_id)
        if not session.is_signed_in:
            self._set_account_text("Not signed in.")
            return
        if self._live_account_text and self._account_checked_for == client_id:
            self._set_account_text(*self._live_account_text)
            return
        self._account_checked_for = client_id
        self._set_account_text("Signed in with saved credentials. Checking your account...")
        api = self._api_for_current_session()
        threading.Thread(target=self._check_account_worker, args=(api,), name="account-check", daemon=True).start()

    # ------------------------------------------------------------ actions
    def start(self) -> None:
        if self._running or self._signing_in:
            return
        name = self.username_var.get().strip()
        problem = validate_username(name)
        if problem:
            self._show_form_error(problem)
            return
        interval, problem = self._read_interval()
        if problem:
            self._show_form_error(problem)
            return

        if self.is_live:
            client_id = self.client_id_var.get().strip()
            if not client_id or self._session is None or self._session.client_id != client_id \
                    or not self._session.is_signed_in:
                self._show_form_error("Sign in with Microsoft first (Microsoft account section above).")
                return
            api: Any = self._api_for_current_session()
            limiter, timing = self._live_limiter, LIVE_TIMING
        else:
            api = SimulatedMinecraftAPI(self._scenario_key())
            limiter, timing = self._simulation_limiter, SIMULATION_TIMING

        self._show_form_error("")
        self._save_settings()
        monitor = NameMonitor(
            api, name, interval, limiter, timing=timing,
            on_status=lambda status: self._events.put(("status", status)),
        )
        self._stop_event = threading.Event()
        self._running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_worker, args=(monitor, self._stop_event), name="name-monitor", daemon=True
        )
        self._render_status(MonitorStatus(state=MonitorState.VERIFYING_ACCOUNT, target=name,
                                          message="Checking your Minecraft account..."))
        self._sync_controls()
        if self.is_live:
            logger.info("Start pressed (Live mode).")
        else:
            logger.info("Start pressed (Simulation mode, scenario: %s).", SCENARIOS[self._scenario_key()].title)
        self._monitor_thread.start()

    def _read_interval(self) -> tuple[int, str | None]:
        try:
            interval = int(self.interval_var.get().strip())
        except ValueError:
            return 0, "The interval must be a whole number of seconds."
        if self.is_live and interval < MIN_LIVE_INTERVAL_SECONDS:
            return 0, (
                f"Live mode checks at most every {MIN_LIVE_INTERVAL_SECONDS} seconds. Mojang allows "
                f"{AVAILABILITY_LIMIT_REQUESTS} availability checks per 5 minutes per account."
            )
        if interval < MIN_SIMULATION_INTERVAL_SECONDS:
            return 0, f"The interval must be at least {MIN_SIMULATION_INTERVAL_SECONDS} second."
        if interval > MAX_INTERVAL_SECONDS:
            return 0, f"The interval can be at most {MAX_INTERVAL_SECONDS} seconds."
        return interval, None

    def stop(self) -> None:
        if self._stop_event is not None and not self._stop_event.is_set():
            self._stop_event.set()
            self.state_var.set("Stopping...")
            logger.info("Stop pressed.")

    def sign_in(self) -> None:
        if self._signing_in or self._running:
            return
        client_id = self.client_id_var.get().strip()
        if not client_id:
            self._set_account_text(
                "Enter your Azure application (client) ID first. See README.md, 'Live mode setup'.", COLOR_BAD
            )
            return
        session = self._session_for(client_id)
        api = self._api_for_current_session()
        self._save_settings()
        self._signin_cancel = threading.Event()
        self._dialog = DeviceCodeDialog(self.root, on_cancel=self._signin_cancel.set)
        self._signing_in = True
        self._set_account_text("Waiting for you to finish signing in on Microsoft's website...")
        self._signin_thread = threading.Thread(
            target=self._sign_in_worker, args=(session, api, self._signin_cancel), name="sign-in", daemon=True
        )
        self._sync_controls()
        self._signin_thread.start()

    def sign_out(self) -> None:
        if self._session is not None:
            self._session.sign_out()
        self._live_account_text = None
        self._account_checked_for = None
        self._set_account_text("Signed out. Saved credentials were deleted from this PC.")
        self._sync_controls()

    def open_log_folder(self) -> None:
        if self._log_file is None:
            return
        folder = self._log_file.parent
        if sys.platform == "win32":
            os.startfile(folder)  # noqa: S606 - opening our own log folder in Explorer
        else:
            webbrowser.open(folder.as_uri())

    def close(self) -> None:
        self._closing = True
        if self._stop_event is not None:
            self._stop_event.set()
        if self._signin_cancel is not None:
            self._signin_cancel.set()
        for thread in (self._monitor_thread, self._signin_thread):
            if thread is not None and thread.is_alive():
                thread.join(THREAD_JOIN_TIMEOUT_SECONDS)
        self._save_settings()
        logging.getLogger(LOGGER_NAME).removeHandler(self._log_handler)
        self.root.destroy()

    # ------------------------------------------------------------ workers (no Tk calls here)
    def _monitor_worker(self, monitor: NameMonitor, stop_event: threading.Event) -> None:
        try:
            monitor.run(stop_event)
        except Exception:
            logger.exception("The monitor stopped because of an unexpected error.")
            self._events.put(("status", MonitorStatus(
                state=MonitorState.FAILED, target=monitor.target,
                message="Unexpected error. Details are in the log file.",
            )))
        finally:
            self._events.put(("monitor_done", None))

    def _sign_in_worker(self, session: AuthSession, api: MinecraftServicesClient, cancel: threading.Event) -> None:
        try:
            code = session.authenticator.start_device_flow()
            self._events.put(("device_code", code))
            tokens = session.authenticator.wait_for_device_flow(code, cancel)
            session.complete_sign_in(tokens)
        except AuthCancelled:
            self._events.put(("signin_failed", ("Sign-in cancelled.", "")))
            return
        except (AuthError, ApiError) as exc:
            logger.warning("Sign-in failed: %s", exc)
            self._events.put(("signin_failed", (str(exc), COLOR_BAD)))
            return
        except Exception:
            logger.exception("Unexpected error during sign-in.")
            self._events.put(("signin_failed", ("Unexpected error during sign-in. Details are in the log file.", COLOR_BAD)))
            return
        self._events.put(("signin_done", session.client_id))
        self._check_account_worker(api)

    def _check_account_worker(self, api: MinecraftServicesClient) -> None:
        try:
            info = verify_account(api)
            text = info.describe()
            color = COLOR_GOOD if info.name_change_allowed else COLOR_WARN
            logger.info(text)
        except AccountNotEligibleError as exc:
            text, color = str(exc), COLOR_BAD
        except (AuthError, ApiError) as exc:
            text, color = f"Signed in, but the account check failed: {exc}", COLOR_WARN
        except Exception:
            logger.exception("Unexpected error while checking the account.")
            text, color = "Account check failed unexpectedly. Details are in the log file.", COLOR_WARN
        self._events.put(("account", (text, color)))

    # ------------------------------------------------------------ event handlers (UI thread)
    def _drain_events(self) -> None:
        if self._closing:
            return
        try:
            while True:
                kind, payload = self._events.get_nowait()
                self._event_handlers[kind](payload)
        except queue.Empty:
            pass
        self.root.after(EVENT_POLL_MS, self._drain_events)

    def _tick(self) -> None:
        if self._closing:
            return
        self._render_next_check()
        self.root.after(CLOCK_TICK_MS, self._tick)

    def _append_log(self, line: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        excess = int(self.log_text.index("end-1c").split(".")[0]) - MAX_LOG_LINES
        if excess > 0:
            self.log_text.delete("1.0", f"{excess + 1}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _render_status(self, status: MonitorStatus) -> None:
        self.state_var.set(status.state.value)
        self._state_label.configure(foreground=STATE_COLORS.get(status.state, ""))
        self.target_var.set(status.target)
        self.availability_var.set(status.availability)
        self._availability_label.configure(foreground=AVAILABILITY_COLORS.get(status.availability, ""))
        self.last_check_var.set(_format_time(status.last_check))
        self._next_check = status.next_check
        self._render_next_check()
        self.claim_var.set(status.claim_result)
        if status.claim_result.startswith(_GOOD_CLAIM_PREFIXES):
            claim_color = COLOR_GOOD
        elif status.claim_result.startswith(_BAD_CLAIM_PREFIXES):
            claim_color = COLOR_BAD
        else:
            claim_color = ""
        self._claim_label.configure(foreground=claim_color)
        self.message_var.set(status.message)

    def _render_next_check(self) -> None:
        if self._next_check is None:
            self.next_check_var.set("-")
            return
        remaining = max(0, round((self._next_check - datetime.now()).total_seconds()))
        self.next_check_var.set(f"{_format_time(self._next_check)}   (in {_format_duration(remaining)})")

    def _on_monitor_done(self, _payload: Any) -> None:
        self._running = False
        self._stop_event = None
        self._next_check = None
        self._render_next_check()
        if self.is_live and self._session is not None and not self._session.is_signed_in:
            # The session signs itself out when Microsoft rejects the saved refresh token.
            self._live_account_text = None
            self._account_checked_for = None
            self._set_account_text("Your Microsoft sign-in expired or was revoked. Sign in again.", COLOR_BAD)
        self._sync_controls()

    def _on_device_code(self, code: DeviceCode) -> None:
        if self._dialog is not None:
            self._dialog.show_code(code)
        logger.info("Microsoft sign-in code issued; waiting for you to finish in the browser.")

    def _close_dialog(self) -> None:
        if self._dialog is not None:
            self._dialog.close()
            self._dialog = None

    def _on_signin_done(self, client_id: str) -> None:
        self._close_dialog()
        self._signing_in = False
        self._account_checked_for = client_id
        self._set_account_text("Signed in. Checking your account...")
        self._sync_controls()

    def _on_signin_failed(self, payload: tuple[str, str]) -> None:
        message, color = payload
        self._close_dialog()
        self._signing_in = False
        self._set_account_text(message, color)
        self._sync_controls()

    def _on_account_text(self, payload: tuple[str, str]) -> None:
        self._live_account_text = payload
        if self.is_live:
            self._set_account_text(*payload)
        self._sync_controls()
