import json

import pytest

from app.settings import Settings


def test_settings_use_defaults_without_options(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    settings = Settings.load()

    assert settings.data_dir == tmp_path
    assert settings.database_path == tmp_path / "ai_bubble_monitor.db"
    assert settings.collection_interval_minutes == 60
    assert settings.scoring_interval_minutes == 60


def test_settings_read_home_assistant_options(tmp_path, monkeypatch):
    (tmp_path / "options.json").write_text(
        json.dumps({"log_level": "debug", "collection_interval_minutes": 30, "fred_api_key": "test-key"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    settings = Settings.load()

    assert settings.log_level == "debug"
    assert settings.collection_interval_minutes == 30
    assert settings.fred_api_key == "test-key"
    assert settings.historical_window_days == 1825


def test_fred_key_environment_overrides_options(tmp_path, monkeypatch):
    (tmp_path / "options.json").write_text(json.dumps({"fred_api_key": "options-key"}), encoding="utf-8")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FRED_API_KEY", "environment-key")

    assert Settings.load().fred_api_key == "environment-key"


def test_fiscal_key_reads_options_and_environment_overrides(tmp_path, monkeypatch):
    (tmp_path / "options.json").write_text(json.dumps({"fiscal_api_key": "options-key"}), encoding="utf-8")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert Settings.load().fiscal_api_key == "options-key"
    monkeypatch.setenv("FISCAL_API_KEY", "environment-key")
    assert Settings.load().fiscal_api_key == "environment-key"


def test_settings_reject_invalid_interval(tmp_path, monkeypatch):
    (tmp_path / "options.json").write_text(
        json.dumps({"scoring_interval_minutes": 1}), encoding="utf-8"
    )
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    with pytest.raises(ValueError, match="scoring_interval_minutes"):
        Settings.load()


def test_auto_collection_can_be_disabled_for_maintenance(tmp_path, monkeypatch):
    (tmp_path / "options.json").write_text(json.dumps({"auto_collection_enabled": False}), encoding="utf-8")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert Settings.load().auto_collection_enabled is False


def test_auto_collection_is_enabled_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert Settings.load().auto_collection_enabled is True


def test_auto_collection_environment_value_overrides_options(tmp_path, monkeypatch):
    (tmp_path / "options.json").write_text(json.dumps({"auto_collection_enabled": True}), encoding="utf-8")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AUTO_COLLECTION_ENABLED", "false")
    assert Settings.load().auto_collection_enabled is False
