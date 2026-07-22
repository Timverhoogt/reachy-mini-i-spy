"""Local, secret-safe configuration for I Spy."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class AppConfig:
    provider_url: str = ""
    broker_token: str = ""
    device_id: str = "reachy-mini"

    @property
    def configured(self) -> bool:
        return bool(self.broker_token and self.device_id and self.provider_url)

    def public_dict(self, *, privileged: bool = False) -> dict[str, object]:
        payload: dict[str, object] = {
            "broker_token_configured": bool(self.broker_token),
            "provider_boundary": "hermes_scoped_ispy_broker",
            "provider_credentials_on_reachy": False,
        }
        if privileged:
            payload.update(broker_url=self.provider_url, device_id=self.device_id)
        return payload


def config_path() -> Path:
    override = os.environ.get("REACHY_MINI_I_SPY_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "reachy-mini-i-spy" / "config.json"


def _validate(config: AppConfig) -> AppConfig:
    parsed = urlparse(config.provider_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Broker URL must be an absolute HTTP(S) URL")
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Remote broker URLs must use HTTPS")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Broker URL must not contain credentials, query parameters, or fragments")
    if parsed.hostname in {"api.openai.com", "api.anthropic.com"} or not parsed.path.rstrip("/").endswith("/ispy/v1"):
        raise ValueError("Use the narrow I Spy provider broker endpoint ending in /ispy/v1")
    if not config.device_id or len(config.device_id) > 64 or not config.device_id.replace("-", "").isalnum():
        raise ValueError("Invalid device_id")
    if len(config.broker_token) > 512 or any(ch in config.broker_token for ch in "\r\n"):
        raise ValueError("Invalid broker token")
    return replace(config, provider_url=config.provider_url.rstrip("/"))


def load_config(path: Path | None = None) -> AppConfig:
    path = path or config_path()
    if not path.exists():
        config = AppConfig(
            provider_url=os.environ.get("REACHY_MINI_I_SPY_BROKER_URL", ""),
            broker_token=os.environ.get("REACHY_MINI_I_SPY_BROKER_TOKEN", ""),
        )
        return _validate(config) if config.provider_url else config
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Configuration must be an object")
    allowed = set(AppConfig.__dataclass_fields__)
    return _validate(AppConfig(**{key: value for key, value in payload.items() if key in allowed}))


def save_config(config: AppConfig, path: Path | None = None) -> Path:
    path = path or config_path()
    config = _validate(config)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(asdict(config), handle, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def merge_config(current: AppConfig, changes: dict[str, object]) -> AppConfig:
    allowed = set(AppConfig.__dataclass_fields__)
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError("Unknown configuration field")
    clean = {key: value for key, value in changes.items() if value is not None}
    if clean.get("broker_token") == "":
        clean.pop("broker_token")
    return _validate(replace(current, **clean))
