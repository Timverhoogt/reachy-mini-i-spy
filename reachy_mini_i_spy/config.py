"""Owner-only configuration for standalone I Spy providers."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal

ProviderMode = Literal["openai", "local"]


@dataclass(frozen=True)
class AppConfig:
    """Private provider settings stored on the machine running the Reachy daemon."""

    provider: ProviderMode = "openai"
    api_key: str = ""

    @property
    def configured(self) -> bool:
        if self.provider == "local":
            from .local_assets import local_assets_status

            return bool(local_assets_status()["ready"])
        return self.provider == "openai" and bool(self.api_key)

    def public_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "provider": self.provider,
            "api_key_configured": bool(self.api_key),
            "provider_boundary": "in_process_fixed_policy",
            "separate_broker_required": False,
        }
        from .local_assets import local_assets_status

        payload["local_assets"] = local_assets_status()
        return payload


def config_path() -> Path:
    override = os.environ.get("REACHY_MINI_I_SPY_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "reachy-mini-i-spy" / "config.json"


def _validate(config: AppConfig) -> AppConfig:
    if config.provider not in {"openai", "local"}:
        raise ValueError("Provider must be openai or local")
    if not isinstance(config.api_key, str) or len(config.api_key) > 512 or any(ch in config.api_key for ch in "\r\n"):
        raise ValueError("Invalid API key")
    if config.provider == "local" and config.api_key:
        # Do not retain an unrelated cloud credential while local-only mode is selected.
        return replace(config, api_key="")
    return config


def load_config(path: Path | None = None) -> AppConfig:
    path = path or config_path()
    if not path.exists():
        key = os.environ.get("REACHY_MINI_I_SPY_API_KEY", "")
        provider = os.environ.get("REACHY_MINI_I_SPY_PROVIDER", "openai")
        return _validate(AppConfig(provider=provider, api_key=key))  # type: ignore[arg-type]
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Configuration must be an object")

    # Old broker-only files fail closed into an unconfigured direct provider.
    # Their token and URL are ignored and disappear on the next settings save.
    allowed = set(AppConfig.__dataclass_fields__)
    clean = {key: value for key, value in payload.items() if key in allowed}
    return _validate(AppConfig(**clean))


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
    if clean.get("api_key") == "":
        clean.pop("api_key")
    return _validate(replace(current, **clean))
