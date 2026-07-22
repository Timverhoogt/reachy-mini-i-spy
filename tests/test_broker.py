from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from hermes_broker.app import BrokerSecrets, BrokerState, CallContext, TargetPayload, create_app

TOKEN = "s" * 40
DEVICE = "reachy-one"
SESSION = "a" * 32
JPEG = b"\xff\xd8jpeg"


class FakeBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def moderate(self, text: str) -> bool:
        self.calls.append(("moderate", text))
        return True

    def select_target(self, frames: list[bytes], language: str, age_band: str) -> dict[str, object]:
        self.calls.append(("select", (frames, language, age_band)))
        return target(include_selection=True)

    def target_present(self, frame: bytes, selected: dict[str, object]) -> bool:
        self.calls.append(("present", (frame, selected)))
        return True

    def judge_guess(self, guess: str, selected: dict[str, object], language: str) -> bool:
        self.calls.append(("judge", (guess, selected, language)))
        return False

    def tts(self, text: str, language: str) -> bytes:
        self.calls.append(("tts", (text, language)))
        return b"RIFF" + b"\0" * 40


def target(*, include_selection: bool = False) -> dict[str, object]:
    value: dict[str, object] = {
        "object_name": "chair", "colour": "blue", "category": "furniture",
        "location": "near table", "frame_index": 0, "bbox": [0.2, 0.2, 0.3, 0.4],
        "confidence": 0.92, "hints_en": ["sit on it"], "hints_nl": ["zit erop"],
    }
    if include_selection:
        value.update(stable=True, visible_frame_count=1)
    return value


def headers(sequence: int = 1, **changes: str) -> dict[str, str]:
    value = {
        "Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json",
        "X-I-Spy-Device": DEVICE, "X-I-Spy-Session": SESSION, "X-I-Spy-Request": str(sequence),
    }
    value.update(changes)
    return value


def make_client(*, rate_limit: int = 30) -> tuple[TestClient, FakeBackend]:
    backend = FakeBackend()
    app = create_app(secrets=BrokerSecrets("provider-secret-must-stay-host-side", {TOKEN: DEVICE}),
                     backend=backend, rate_limit=rate_limit)
    return TestClient(app), backend


def test_exact_operations_only_and_no_general_provider_passthrough() -> None:
    client, backend = make_client()
    response = client.post("/ispy/v1/moderate", headers=headers(), json={"text": "safe"})
    assert response.status_code == 200, response.text
    assert response.json() == {"allowed": True}
    assert backend.calls == [("moderate", "safe")]
    for path in ("chat/completions", "responses", "tools", "models", "agent"):
        blocked = client.post(f"/ispy/v1/{path}", headers=headers(2), json={})
        assert blocked.status_code == 404
    assert "provider-secret" not in response.text


def test_modified_client_cannot_choose_model_prompt_url_or_extra_fields() -> None:
    client, backend = make_client()
    response = client.post("/ispy/v1/moderate", headers=headers(), json={
        "text": "safe", "model": "gpt-99", "url": "https://evil.invalid", "tools": [{"type": "shell"}],
    })
    assert response.status_code == 422
    assert backend.calls == []
    assert "safe" not in response.text


def test_token_is_bound_to_device_and_session_headers_are_strict() -> None:
    client, backend = make_client()
    wrong_device = client.post(
        "/ispy/v1/moderate", headers=headers(**{"X-I-Spy-Device": "other"}), json={"text": "safe"}
    )
    wrong_token = client.post(
        "/ispy/v1/moderate",
        headers={**headers(), "Authorization": "Bearer " + "x" * 40},
        json={"text": "safe"},
    )
    bad_session = client.post(
        "/ispy/v1/moderate", headers=headers(**{"X-I-Spy-Session": "../session"}), json={"text": "safe"}
    )
    assert [wrong_device.status_code, wrong_token.status_code, bad_session.status_code] == [401, 401, 401]
    assert backend.calls == []


def test_strict_content_type_body_and_frame_bounds() -> None:
    client, backend = make_client()
    wrong_type = client.post("/ispy/v1/moderate", headers={**headers(), "Content-Type": "text/plain"}, content="{}")
    invalid_frame = client.post("/ispy/v1/select-target", headers=headers(2), json={
        "frames_jpeg": [base64.b64encode(b"not-jpeg").decode()], "language": "en", "age_band": "7-9",
    })
    assert wrong_type.status_code == 415
    assert invalid_frame.status_code == 422
    assert backend.calls == []


def test_valid_select_has_fixed_contract_and_never_echoes_frames() -> None:
    client, backend = make_client()
    frame = base64.b64encode(JPEG).decode()
    response = client.post("/ispy/v1/select-target", headers=headers(), json={
        "frames_jpeg": [frame], "language": "nl", "age_band": "7-9",
    })
    assert response.status_code == 200
    assert response.json() == {"target": target(include_selection=True)}
    assert frame not in response.text
    assert backend.calls[0][0] == "select"


def test_provider_colour_normalization_is_narrow() -> None:
    gray = target(include_selection=True)
    gray["colour"] = " Gray "
    assert TargetPayload.model_validate(gray).colour == "grey"


def test_replay_cancellation_and_rate_limits_fail_before_backend() -> None:
    client, backend = make_client(rate_limit=3)
    assert client.post("/ispy/v1/moderate", headers=headers(1), json={"text": "one"}).status_code == 200
    assert client.post("/ispy/v1/moderate", headers=headers(1), json={"text": "replay"}).status_code == 409
    assert client.post("/ispy/v1/cancel", headers=headers(2), json={}).status_code == 200
    assert client.post("/ispy/v1/moderate", headers=headers(3), json={"text": "late"}).status_code in {409, 429}
    assert backend.calls == [("moderate", "one")]


def test_newer_request_makes_older_result_late() -> None:
    state = BrokerState({TOKEN: DEVICE})
    first = state.authenticate(f"Bearer {TOKEN}", DEVICE, SESSION, "1")
    second = state.authenticate(f"Bearer {TOKEN}", DEVICE, SESSION, "2")
    assert state.current(first) is False
    assert state.current(second) is True
    state.cancel(CallContext(DEVICE, SESSION, 2))
    assert state.current(second) is False


def test_expired_cancel_state_is_bounded_and_does_not_poison_reused_id(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    now = 1000.0
    monkeypatch.setattr("hermes_broker.app.time.monotonic", lambda: now)
    state = BrokerState({TOKEN: DEVICE})
    first = state.authenticate(f"Bearer {TOKEN}", DEVICE, SESSION, "1")
    state.cancel(first)
    now += BrokerState.SESSION_TTL_SECONDS + 1
    restarted = state.authenticate(f"Bearer {TOKEN}", DEVICE, SESSION, "1")
    assert state.current(restarted) is True
    assert len(state._last_seen) == 1
