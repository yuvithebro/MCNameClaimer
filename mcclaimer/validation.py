"""Username rules for Minecraft: Java Edition."""

from __future__ import annotations

import re

MIN_USERNAME_LENGTH = 3
MAX_USERNAME_LENGTH = 16
_ALLOWED_CHARACTERS = re.compile(r"^[A-Za-z0-9_]+$")


def validate_username(name: str) -> str | None:
    """Return a human-readable problem with `name`, or None when it is valid."""
    if not name:
        return "Enter a username."
    if len(name) < MIN_USERNAME_LENGTH:
        return f"Too short: usernames need at least {MIN_USERNAME_LENGTH} characters."
    if len(name) > MAX_USERNAME_LENGTH:
        return f"Too long: usernames can have at most {MAX_USERNAME_LENGTH} characters."
    if not _ALLOWED_CHARACTERS.match(name):
        return "Only letters (A-Z), digits (0-9) and underscore (_) are allowed."
    return None
