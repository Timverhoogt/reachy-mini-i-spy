"""Reachy Mini I Spy app entry point and bounded local control API."""

from __future__ import annotations

import json
import logging
import threading
import urllib.request
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field
from reachy_mini import ReachyMini, ReachyMiniApp

from .config import load_config, merge_config, save_config
from .local_assets import install_local_assets
from .runtime import GameRuntime

_LOGGER = logging.getLogger(__name__)


def _running_on_wireless(requested: bool) -> bool:
    """Recover the daemon's SKU flag when its app launcher omits constructor arguments."""
    if requested:
        return True
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/daemon/status", timeout=1.0) as response:
            payload = json.load(response)
        return isinstance(payload, dict) and payload.get("wireless_version") is True
    except (OSError, ValueError):
        return False


class SettingsUpdate(BaseModel):
    provider: Literal["openai", "local"] | None = None
    api_key: str | None = Field(default=None, max_length=512)


class StartRequest(BaseModel):
    language: Literal["en", "nl"] = "en"
    age_band: Literal["4-6", "7-9", "10-12"] = "7-9"
    camera_consent: bool


class GuessRequest(BaseModel):
    text: str = Field(min_length=1, max_length=80)


class ReachyMiniISpy(ReachyMiniApp):
    """A camera-opt-in, child-safe embodied guessing game."""

    custom_app_url: str | None = "http://127.0.0.1:8042"
    request_media_backend: str | None = "local"

    def __init__(self, running_on_wireless: bool = False) -> None:
        super().__init__(running_on_wireless=running_on_wireless)
        self._deployment_profile = "wireless" if _running_on_wireless(running_on_wireless) else "lite_host"
        self._runtime: GameRuntime | None = None
        self._register_routes()

    def _runtime_or_409(self) -> GameRuntime:
        if self._runtime is None:
            raise HTTPException(status_code=409, detail="Game runtime has not started")
        return self._runtime

    def _register_routes(self) -> None:
        if self.settings_app is None:
            return

        @self.settings_app.get("/api/status")
        def status() -> dict[str, object]:
            try:
                config = load_config().public_dict()
                config_error = ""
            except Exception:
                config = {}
                config_error = "Local provider configuration is unavailable"
            game = (
                self._runtime.snapshot()
                if self._runtime is not None
                else {
                    "state": "not_started",
                    "camera_active": False,
                    "message": "Starting app…",
                    "target": None,
                }
            )
            return {
                "app": "reachy_mini_i_spy",
                "deployment_profile": self._deployment_profile,
                "compute_host": "Reachy Mini CM4" if self._deployment_profile == "wireless" else "connected Mac/PC",
                "config": config,
                "config_error": config_error,
                "game": game,
            }

        @self.settings_app.post("/api/settings")
        def update_settings(update: SettingsUpdate) -> dict[str, object]:
            try:
                current = load_config()
                merged = merge_config(current, update.model_dump(exclude_none=True))
                save_config(merged)
            except (OSError, ValueError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            _LOGGER.info("I Spy in-process provider settings updated (credentials redacted)")
            return {"ok": True, "config": merged.public_dict()}

        @self.settings_app.post("/api/local/setup")
        def setup_local_provider() -> dict[str, object]:
            if self._runtime is not None and self._runtime.snapshot()["camera_active"]:
                raise HTTPException(status_code=409, detail="Stop the active game before installing local models")
            try:
                assets = install_local_assets()
            except (OSError, ValueError) as exc:
                _LOGGER.warning("Local I Spy model setup failed safely (%s)", type(exc).__name__)
                raise HTTPException(status_code=502, detail="Local model setup failed safely") from exc
            _LOGGER.info("Local I Spy model assets installed and verified")
            return {"ok": True, "assets": assets}

        @self.settings_app.post("/api/game/start")
        def start_game(payload: StartRequest) -> dict[str, object]:
            if not load_config().configured:
                raise HTTPException(status_code=409, detail="Configure a supported standalone provider first")
            try:
                return {
                    "ok": True,
                    "game": self._runtime_or_409().start(
                        payload.language, payload.age_band, payload.camera_consent
                    ),
                }
            except PermissionError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            except RuntimeError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        @self.settings_app.post("/api/game/guess")
        def submit_guess(payload: GuessRequest) -> dict[str, object]:
            try:
                return {
                    "ok": True,
                    "game": self._runtime_or_409().guess(payload.text),
                }
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except RuntimeError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        @self.settings_app.post("/api/game/stop")
        def stop_game() -> dict[str, object]:
            """Prominent Stop is intentionally ungated and needs no request body."""
            return {"ok": True, "game": self._runtime_or_409().stop()}

        @self.settings_app.post("/api/camera/disable")
        def disable_camera() -> dict[str, object]:
            return {"ok": True, "game": self._runtime_or_409().stop("camera_disabled")}

    def run(self, reachy_mini: ReachyMini, stop_event: threading.Event) -> None:
        self._runtime = GameRuntime(reachy_mini, stop_event)
        self._runtime.run()


def run_cli() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = ReachyMiniISpy()
    try:
        app.wrapped_run()
    except KeyboardInterrupt:
        app.stop()


if __name__ == "__main__":
    run_cli()