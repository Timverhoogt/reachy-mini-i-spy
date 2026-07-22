from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from reachy_mini_i_spy.auth import CaregiverGuard
from reachy_mini_i_spy.config import AppConfig

TOKEN = "caregiver-" + "x" * 32


def request(*, token: str = "", origin: str | None = None) -> Request:
    headers = []
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    return Request({
        "type": "http",
        "method": "POST",
        "scheme": "http",
        "server": ("reachy.local", 8042),
        "path": "/api/game/start",
        "raw_path": b"/api/game/start",
        "query_string": b"",
        "headers": headers,
    })


def test_caregiver_authority_fails_closed_and_rejects_cross_origin() -> None:
    guard = CaregiverGuard(TOKEN)
    with pytest.raises(HTTPException) as missing:
        guard.issue(request())
    with pytest.raises(HTTPException) as wrong_origin:
        guard.issue(request(token=TOKEN, origin="https://evil.example"))
    assert missing.value.status_code == 401
    assert wrong_origin.value.status_code == 401


def test_caregiver_csrf_is_one_time_and_replay_is_rejected() -> None:
    guard = CaregiverGuard(TOKEN)
    authenticated = request(token=TOKEN, origin="http://reachy.local:8042")
    nonce = guard.issue(authenticated)
    replacement = guard.authorize(authenticated, nonce)
    assert replacement != nonce
    with pytest.raises(HTTPException) as replay:
        guard.authorize(authenticated, nonce)
    assert replay.value.status_code == 403


def test_unauthenticated_config_never_discloses_broker_or_device_identity() -> None:
    config = AppConfig(
        provider_url="https://hermes.example/ispy/v1",
        broker_token="secret",
        device_id="private-reachy",
    )
    public = config.public_dict()
    assert "broker_url" not in public
    assert "device_id" not in public
    assert config.public_dict(privileged=True)["device_id"] == "private-reachy"
