from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from reachy_mini_i_spy.config import AppConfig, load_config, merge_config, save_config


def test_api_key_config_is_private_and_redacted(tmp_path: Path) -> None:
    path = tmp_path / "private" / "config.json"
    config = AppConfig(provider="openai", api_key="provider-secret")
    save_config(config, path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert load_config(path).api_key == "provider-secret"
    public = load_config(path).public_dict()
    assert public["provider"] == "openai"
    assert public["api_key_configured"] is True
    assert public["provider_boundary"] == "in_process_fixed_policy"
    assert public["separate_broker_required"] is False
    assert public["local_assets"]["detector_present"] is True
    assert public["local_assets"]["ready"] is False
    assert "provider-secret" not in json.dumps(public)
    assert "api_key" not in public


def test_blank_key_keeps_existing_key() -> None:
    current = AppConfig(provider="openai", api_key="keep-me")
    assert merge_config(current, {"api_key": ""}).api_key == "keep-me"


def test_provider_url_and_model_controls_are_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown configuration"):
        merge_config(AppConfig(api_key="x"), {"provider_url": "https://attacker.example/v1"})
    with pytest.raises(ValueError, match="Unknown configuration"):
        merge_config(AppConfig(api_key="x"), {"model": "caller-controlled"})


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(ValueError, match="openai or local"):
        merge_config(AppConfig(api_key="x"), {"provider": "other"})


def test_local_mode_drops_cloud_key_and_is_not_yet_startable() -> None:
    merged = merge_config(AppConfig(api_key="secret"), {"provider": "local"})
    assert merged.api_key == ""
    assert merged.configured is False


def test_direct_provider_environment_is_supported_and_broker_environment_is_ignored(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("REACHY_MINI_I_SPY_API_KEY", "direct-secret")
    monkeypatch.setenv("REACHY_MINI_I_SPY_PROVIDER", "openai")
    monkeypatch.setenv("REACHY_MINI_I_SPY_BROKER_URL", "http://attacker.example/ispy/v1")
    monkeypatch.setenv("REACHY_MINI_I_SPY_BROKER_TOKEN", "legacy-token")
    config = load_config(tmp_path / "missing.json")
    assert config == AppConfig(provider="openai", api_key="direct-secret")


def test_legacy_broker_file_fails_closed_without_reusing_old_token(tmp_path: Path) -> None:
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps({
        "provider_url": "https://broker.example/ispy/v1",
        "broker_token": "legacy-secret",
        "device_id": "reachy-one",
    }))
    config = load_config(path)
    assert config == AppConfig()
    assert config.configured is False
    assert "legacy-secret" not in repr(config)
