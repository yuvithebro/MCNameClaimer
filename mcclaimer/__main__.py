"""Entry point: `python -m mcclaimer` (the Windows launcher runs this with pythonw)."""

from __future__ import annotations

import logging
import sys
import tkinter as tk
from tkinter import messagebox

from . import APP_NAME, __version__
from .config import load_settings
from .gui import ClaimerApp
from .logging_setup import LOGGER_NAME, configure_logging

logger = logging.getLogger(LOGGER_NAME)


def _enable_high_dpi() -> None:
    """Sharp text on scaled Windows displays instead of a blurry bitmap-stretched window."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


def main() -> None:
    log_file = configure_logging()
    logger.info("%s %s starting (unofficial third-party utility).", APP_NAME, __version__)
    _enable_high_dpi()
    root = tk.Tk()

    def report_callback_exception(exc_type, exc_value, exc_traceback) -> None:
        logger.error("Unexpected UI error", exc_info=(exc_type, exc_value, exc_traceback))
        messagebox.showerror("Unexpected error", f"{exc_value}\n\nDetails were written to:\n{log_file}")

    root.report_callback_exception = report_callback_exception
    ClaimerApp(root, load_settings(), log_file=log_file)
    root.mainloop()


if __name__ == "__main__":
    main()
