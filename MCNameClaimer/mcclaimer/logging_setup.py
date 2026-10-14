"""Log file setup with a last-line-of-defence secret scrubber.

The code never passes tokens to the logger in the first place. The redacting
formatter exists so that a future mistake (or a library exception message)
still can't leak a token into the log file or the on-screen log.
"""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .paths import log_dir

LOGGER_NAME = "mcclaimer"
LOG_FORMAT = "%(asctime)s  %(levelname)-7s  %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
LOG_FILE_NAME = "claimer.log"
LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 5

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # JSON Web Tokens (Minecraft access tokens, Xbox tokens, id_tokens).
    (re.compile(r"eyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]*"), "[REDACTED]"),
    # Authorization: Bearer <token>
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1[REDACTED]"),
    # Minecraft identity token: XBL3.0 x=<userhash>;<xsts token>
    (re.compile(r"XBL3\.0 x=[^\s\"']+"), "XBL3.0 x=[REDACTED]"),
    # Token-bearing fields in JSON, form bodies or reprs.
    (
        re.compile(
            r"(?i)(\"?\b(?:access_token|refresh_token|id_token|device_code|identityToken"
            r"|RpsTicket|Token|client_secret|password)\b\"?\s*[:=]\s*\"?)([^\"\s,}&]+)"
        ),
        r"\1[REDACTED]",
    ),
    # Microsoft consumer refresh/access tokens, e.g. M.C5xx_BAY.0.U.-Cb...
    (re.compile(r"\bM\.[A-Za-z0-9]{2,}_[A-Za-z0-9!*$._-]{16,}"), "[REDACTED]"),
)


def redact(text: str) -> str:
    """Replace anything that looks like a credential with [REDACTED]."""
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFormatter(logging.Formatter):
    """Formats normally (including tracebacks), then scrubs secrets from the result."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def configure_logging(directory: Path | None = None) -> Path:
    """Send the app's log to a rotating file and return the file's path.

    Safe to call more than once: a previous file handler is replaced.
    """
    directory = directory or log_dir()
    directory.mkdir(parents=True, exist_ok=True)
    log_file = directory / LOG_FILE_NAME

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for existing in list(logger.handlers):
        if getattr(existing, "is_mcclaimer_file_handler", False):
            logger.removeHandler(existing)
            existing.close()

    handler = RotatingFileHandler(
        log_file, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
    )
    handler.is_mcclaimer_file_handler = True
    handler.setFormatter(RedactingFormatter(LOG_FORMAT, LOG_DATE_FORMAT))
    logger.addHandler(handler)
    return log_file
