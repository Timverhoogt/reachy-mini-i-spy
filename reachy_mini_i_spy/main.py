"""Reachy Mini I Spy app entry point and bounded local control API."""

from __future__ import annotations

import logging
import threading
from typing import Literal

from fastapi import Header, HTTPException, Request
from pydantic import BaseModel, Field
from reachy_mini import ReachyMini, ReachyMiniApp

from .auth import CaregiverGuard
from .config import load_config, merge_config, save_config
from .runtime import GameRuntime

_LOGGER = logging.getLogger(__name__)


class SettingsUpdate(BaseModel):
    provider_url: str | None = Field(default=None, max_length=300)
    broker_token: str | None = Field(default=None, max_length=512)
    device_id: str | None = Field(default=None, min_length=1, max_length=64)


class StartRequest(BaseModel):
    language: Literal["en", "nl"] = "en"
    age_band: Literal["4-6", "7-9", "10-12"] = "7-9"
    camera_consent: bool


class GuessRequest(BaseModel):
    text: str = Field(min_length=1, max_length=80)


class ReachyMiniISpy(ReachyMiniApp):
    """A camera-opt-in, child-safe embodied guessing game."""

    custom_app_url: str | None = "http://0.0.0.0:8042"
    request_media_backend: str | None = "local"

    def __init__(self, running_on_wireless: bool = False) -> None:
        super().__init__(running_on_wireless=running_on_wireless)
        self._runtime: GameRuntime | None = None
        self._caregiver = CaregiverGuard()
        self._register_routes()

    def _runtime_or_409(self) -> GameRuntime:
        if self._runtime is None:
            raise HTTPException(status_code=409, detail="Game runtime has not started")
        return self._runtime

    def _register_routes(self) -> None:
        if self.settings_app is None:
            return

        @self.settings_app.get("/api/status")
        def status(request: Request) -> dict[str, object]:
            try:
                config = load_config().public_dict(privileged=self._caregiver.authenticated(request))
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
            return {"app": "reachy_mini_i_spy", "config": config, "config_error": config_error, "game": game}

        @self.settings_app.post("/api/caregiver/session")
        def caregiver_session(request: Request) -> dict[str, object]:
            return {"ok": True, "csrf_token": self._caregiver.issue(request)}

        @self.settings_app.post("/api/settings")
        def update_settings(
            update: SettingsUpdate,
            request: Request,
            x_i_spy_csrf: str | None = Header(default=None),
        ) -> dict[str, object]:
            next_csrf = self._caregiver.authorize(request, x_i_spy_csrf)
            try:
                current = load_config()
                merged = merge_config(current, update.model_dump(exclude_none=True))
                save_config(merged)
            except (OSError, ValueError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            _LOGGER.info("I Spy provider settings updated (credentials redacted)")
            return {"ok": True, "config": merged.public_dict(privileged=True), "csrf_token": next_csrf}

        @self.settings_app.post("/api/game/start")
        def start_game(
            payload: StartRequest,
            request: Request,
            x_i_spy_csrf: str | None = Header(default=None),
        ) -> dict[str, object]:
            next_csrf = self._caregiver.authorize(request, x_i_spy_csrf)
            if not load_config().configured:
                raise HTTPException(status_code=409, detail="Configure the scoped Hermes broker first")
            try:
                return {"ok": True, "game": self._runtime_or_409().start(
                    payload.language, payload.age_band, payload.camera_consent
                ), "csrf_token": next_csrf}
            except PermissionError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            except RuntimeError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        @self.settings_app.post("/api/game/guess")
        def submit_guess(
            payload: GuessRequest,
            request: Request,
            x_i_spy_csrf: str | None = Header(default=None),
        ) -> dict[str, object]:
            next_csrf = self._caregiver.authorize(request, x_i_spy_csrf)
            try:
                return {
                    "ok": True,
                    "game": self._runtime_or_409().guess(payload.text),
                    "csrf_token": next_csrf,
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