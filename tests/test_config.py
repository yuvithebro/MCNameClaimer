import json

from mcclaimer.config import Settings, load_settings, save_settings
from mcclaimer.simulation import DEFAULT_SCENARIO


def test_defaults_start_in_simulation(tmp_path):
    settings = load_settings(tmp_path / "missing.json")
    assert settings.simulation_mode is True
    assert settings.interval_seconds == 30


def test_round_trip(tmp_path):
    path = tmp_path / "settings.json"
    original = Settings(client_id="cid", target_username="Notch", interval_seconds=45,
                        simulation_mode=False, simulation_scenario="rate_limited")
    save_settings(original, path)
    assert load_settings(path) == original


def test_bad_values_fall_back_to_safe_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"interval_seconds": "abc", "simulation_scenario": "bogus", "extra": 1}))
    settings = load_settings(path)
    assert settings.interval_seconds == 30
    assert settings.simulation_scenario == DEFAULT_SCENARIO


def test_corrupt_file_uses_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json")
    assert load_settings(path) == Settings()


def test_client_id_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("MCCLAIMER_CLIENT_ID", "env-client")
    assert load_settings(tmp_path / "missing.json").client_id == "env-client"


def test_settings_never_contain_tokens(tmp_path):
    path = tmp_path / "settings.json"
    save_settings(Settings(client_id="cid"), path)
    assert "token" not in path.read_text().lower()
