from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from reachy_mini_i_spy.config import AppConfig, load_config, merge_config, save_config


def test_secret_config_is_private_and_redacted(tmp_path: Path) -> None:
    path = tmp_path / "private" / "config.json"
    config = AppConfig(provider_url="https://hermes.example/ispy/v1", broker_token="scoped-secret")
    save_config(config, path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert load_config(path).broker_token == "scoped-secret"
    public = load_config(path).public_dict()
    assert public["broker_token_configured"] is True
    assert public["provider_credentials_on_reachy"] is False
    assert public["provider_boundary"] == "hermes_scoped_ispy_broker"
    assert "scoped-secret" not in json.dumps(public)
    assert "broker_token" not in public
    assert not ({"vision_model", "moderation_model", "tts_model", "tts_voice"} & public.keys())


def test_blank_token_keeps_existing_scoped_token() -> None:
    current = AppConfig(provider_url="https://hermes.example/ispy/v1", broker_token="keep-me")
    assert merge_config(current, {"broker_token": ""}).broker_token == "keep-me"


def test_insecure_remote_provider_is_rejected() -> None:
    with pytest.raises(ValueError):
        merge_config(AppConfig(broker_token="x"), {"provider_url": "http://example.com/v1"})


def test_local_http_provider_is_allowed() -> None:
    merged = merge_config(AppConfig(broker_token="x"), {"provider_url": "http://127.0.0.1:8001/ispy/v1/"})
    assert merged.provider_url == "http://127.0.0.1:8001/ispy/v1"


def test_direct_provider_endpoint_is_rejected() -> None:
    with pytest.raises(ValueError, match="I Spy provider broker"):
        merge_config(AppConfig(broker_token="x"), {"provider_url": "https://api.openai.com/v1"})


def test_provider_key_environment_is_ignored_on_reachy(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("OPENAI_API_KEY", "must-never-be-consumed")
    monkeypatch.delenv("REACHY_MINI_I_SPY_BROKER_URL", raising=False)
    monkeypatch.delenv("REACHY_MINI_I_SPY_BROKER_TOKEN", raising=False)
    config = load_config(tmp_path / "missing.json")
    assert config.configured is False
    assert "must-never-be-consumed" not in repr(config)


def test_environment_broker_url_is_validated_like_file_config(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("REACHY_MINI_I_SPY_BROKER_URL", "http://attacker.example/ispy/v1?leak=1")
    monkeypatch.setenv("REACHY_MINI_I_SPY_BROKER_TOKEN", "scoped-token")

    with pytest.raises(ValueError, match="HTTPS"):
        load_config(tmp_path / "missing.json")


def test_legacy_provider_key_is_not_loaded_from_robot_config(tmp_path: Path) -> None:
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps({
        "provider_url": "https://api.openai.com/v1",
        "api_key": "provider-secret",
    }))
    with pytest.raises(ValueError) as exc_info:
        load_config(path)
    assert "provider-secret" not in str(exc_info.value)
