import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture(autouse=True)
def isolated_app_home(tmp_path, monkeypatch):
    """Never let a test read or write the real %LOCALAPPDATA%\\MCNameClaimer."""
    monkeypatch.setenv("MCCLAIMER_HOME", str(tmp_path / "app-home"))
    monkeypatch.delenv("MCCLAIMER_CLIENT_ID", raising=False)
