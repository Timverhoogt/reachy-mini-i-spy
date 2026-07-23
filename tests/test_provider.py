from __future__ import annotations

import io
import json
from email.message import Message
from typing import Any

import pytest

from reachy_mini_i_spy.config import AppConfig
from reachy_mini_i_spy.game import validate_target
from reachy_mini_i_spy.provider import MODERATION_MODEL, VISION_MODEL, ProviderClient, ProviderError


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
        "object_name": "chair",
        "colour": "blue",
        "category": "furniture",
        "location": "in the room",
        "frame_index": 0,
        "bbox": [0.2, 0.2, 0.3, 0.4],
        "confidence": 0.92,
        "stable": True,
        "visible_frame_count": 1,
        "hints_en": ["You can sit on it"],
        "hints_nl": ["Je kunt erop zitten"],
    }


def completion(content: dict[str, object]) -> dict[str, object]:
    return {"choices": [{"message": {"content": json.dumps(content)}}]}


def moderation(*, flagged: bool = False) -> dict[str, object]:
    return {"results": [{"flagged": flagged}]}


def client(opener: Opener) -> ProviderClient:
    return ProviderClient(AppConfig(provider="openai", api_key="provider-secret"), opener=opener)


def test_client_uses_fixed_openai_endpoint_model_and_authorization_header() -> None:
    opener = Opener([completion(safe_candidate()), moderation()])
    target = client(opener).select_target([b"jpeg"], language="en", age_band="7-9")
    assert target.object_name == "chair"
    request, timeout = opener.requests[0]
    payload = json.loads(request.data)
    assert request.full_url == "https://api.openai.com/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer provider-secret"
    assert timeout <= 20
    assert payload["model"] == VISION_MODEL
    assert payload["response_format"]["type"] == "json_schema"
    assert b"provider-secret" not in request.data


def test_moderation_uses_fixed_endpoint_and_model() -> None:
    opener = Opener([moderation()])
    client(opener).moderate("safe text")
    request = opener.requests[0][0]
    payload = json.loads(request.data)
    assert request.full_url == "https://api.openai.com/v1/moderations"
    assert payload == {"model": MODERATION_MODEL, "input": "safe text"}


def test_guess_is_moderated_then_sent_to_fixed_judge() -> None:
    opener = Opener([moderation(), completion({"match": False})])
    target = validate_target(safe_candidate(), frame_count=1)
    assert client(opener).judge_guess("table", target, language="en") is False
    assert [item[0].full_url.rsplit("/", 1)[-1] for item in opener.requests] == ["moderations", "completions"]


def test_cancelled_client_rejects_new_and_late_results_without_remote_cancel() -> None:
    opener = Opener([])
    provider = client(opener)
    provider.cancel()
    with pytest.raises(ProviderError, match="cancelled"):
        provider.moderate("safe")
    assert opener.requests == []


def test_wrong_content_type_fails_closed() -> None:
    class WrongType(Opener):
        def __call__(self, request: Any, timeout: float) -> Response:
            self.requests.append((request, timeout))
            return Response(b'{"results":[{"flagged":false}]}', "text/plain")

    with pytest.raises(ProviderError, match="invalid bounded response"):
        client(WrongType([])).moderate("safe")


def test_oversize_frames_are_rejected_before_network() -> None:
    opener = Opener([])
    with pytest.raises(ProviderError):
        client(opener).select_target([b"x" * 1_500_001], language="en", age_band="7-9")
    assert opener.requests == []


def test_local_mode_fails_closed_until_assets_are_installed() -> None:
    with pytest.raises(ProviderError, match="Local provider assets"):
        ProviderClient(AppConfig(provider="local"))
