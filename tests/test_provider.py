from __future__ import annotations

import io
import json
from email.message import Message
from typing import Any

import pytest

from reachy_mini_i_spy.config import AppConfig
from reachy_mini_i_spy.game import validate_target
from reachy_mini_i_spy.provider import ProviderClient, ProviderError


class Response(io.BytesIO):
    def __init__(self, value: bytes, content_type: str = "application/json", status: int = 200) -> None:
        super().__init__(value)
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class Opener:
    def __init__(self, responses: list[dict[str, object] | bytes]) -> None:
        self.responses = responses
        self.requests: list[Any] = []

    def __call__(self, request: Any, timeout: float) -> Response:
        self.requests.append((request, timeout))
        value = self.responses.pop(0)
        return Response(value if isinstance(value, bytes) else json.dumps(value).encode())


def safe_candidate() -> dict[str, object]:
    return {
        "object_name": "chair", "colour": "blue", "category": "furniture",
        "location": "near the table", "frame_index": 0, "bbox": [0.2, 0.2, 0.3, 0.4],
        "confidence": 0.92, "stable": True, "visible_frame_count": 1,
        "hints_en": ["You can sit on it"], "hints_nl": ["Je kunt erop zitten"],
    }


def client(opener: Opener) -> ProviderClient:
    return ProviderClient(
        AppConfig(provider_url="https://hermes.example/ispy/v1", broker_token="scoped-secret", device_id="reachy-one"),
        opener=opener, session_id="a" * 32,
    )


def test_client_uses_only_bounded_route_and_never_puts_token_or_model_in_body() -> None:
    opener = Opener([{"target": safe_candidate()}])
    target = client(opener).select_target([b"jpeg"], language="en", age_band="7-9")
    assert target.object_name == "chair"
    request, timeout = opener.requests[0]
    assert request.full_url == "https://hermes.example/ispy/v1/select-target"
    assert timeout <= 20
    assert b"scoped-secret" not in request.data
    assert b"model" not in request.data
    assert request.headers["Authorization"] == "Bearer scoped-secret"
    assert request.headers["X-i-spy-device"] == "reachy-one"
    assert request.headers["X-i-spy-session"] == "a" * 32


def test_modified_client_cannot_send_unknown_operation_through_public_methods() -> None:
    opener = Opener([{"allowed": True}])
    client(opener).moderate("safe text")
    payload = json.loads(opener.requests[0][0].data)
    assert payload == {"text": "safe text"}
    assert opener.requests[0][0].full_url.endswith("/moderate")


def test_guess_is_sent_to_single_bounded_judge_operation() -> None:
    opener = Opener([{"match": False}])
    target = validate_target(safe_candidate(), frame_count=1)
    assert client(opener).judge_guess("table", target, language="en") is False
    assert len(opener.requests) == 1
    assert opener.requests[0][0].full_url.endswith("/judge-guess")


def test_cancelled_client_rejects_late_or_new_results() -> None:
    opener = Opener([{"cancelled": True}])
    provider = client(opener)
    provider.cancel()
    with pytest.raises(ProviderError, match="cancelled"):
        provider.moderate("safe")
    assert opener.requests[0][0].full_url.endswith("/cancel")


def test_wrong_content_type_fails_closed() -> None:
    class WrongType(Opener):
        def __call__(self, request: Any, timeout: float) -> Response:
            self.requests.append((request, timeout))
            return Response(b'{"allowed":true}', "text/plain")

    with pytest.raises(ProviderError, match="invalid response"):
        client(WrongType([])).moderate("safe")


def test_oversize_frames_are_rejected_before_network() -> None:
    opener = Opener([])
    with pytest.raises(ProviderError):
        client(opener).select_target([b"x" * 1_500_001], language="en", age_band="7-9")
    assert opener.requests == []