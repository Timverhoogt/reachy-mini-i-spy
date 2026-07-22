"""Narrow, authenticated Hermes-host broker for the I Spy app.

This component is intentionally outside the Reachy package. It owns provider
credentials and translates five fixed game operations into fixed OpenAI calls.
"""
# FastAPI dependencies intentionally use Depends() defaults; fixed prompts stay visually contiguous.
# ruff: noqa: B008, E501

from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import json
import logging
import os
import re
import stat
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from reachy_mini_i_spy.game import DISALLOWED_TERMS, validate_target

_LOGGER = logging.getLogger("hermes_ispy_broker")
BASE_PATH = "/ispy/v1"
MAX_BODY = 5_000_000
OPERATION_TIMEOUT = 18.0
VISION_MODEL = "gpt-4.1-mini"
MODERATION_MODEL = "omni-moderation-latest"
TTS_MODEL = "gpt-4o-mini-tts"
TTS_VOICE = "coral"
_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_SESSION = re.compile(r"^[a-f0-9]{32}$")


class BrokerError(RuntimeError):
    """Sanitized provider/broker failure."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ModerateRequest(StrictModel):
    text: str = Field(min_length=1, max_length=500)


class SelectRequest(StrictModel):
    frames_jpeg: list[str] = Field(min_length=1, max_length=3)
    language: Literal["en", "nl"]
    age_band: Literal["4-6", "7-9", "10-12"]

    @field_validator("frames_jpeg")
    @classmethod
    def valid_frames(cls, values: list[str]) -> list[str]:
        for value in values:
            _decode_frame(value)
        return values


class TargetReference(StrictModel):
    object_name: str = Field(min_length=1, max_length=60)
    colour: Literal["red", "orange", "yellow", "green", "blue", "purple", "pink", "brown", "black", "white", "grey"]
    category: str = Field(min_length=1, max_length=40)
    location: str = Field(min_length=1, max_length=100)
    frame_index: int = Field(ge=0, le=2)
    bbox: tuple[float, float, float, float]
    confidence: float = Field(ge=0, le=1)
    hints_en: list[str] = Field(min_length=1, max_length=3)
    hints_nl: list[str] = Field(min_length=1, max_length=3)

    @field_validator("colour", mode="before")
    @classmethod
    def normalize_colour(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower()
        return "grey" if normalized == "gray" else normalized


class TargetPayload(TargetReference):
    stable: bool
    visible_frame_count: int = Field(ge=1, le=3)


TARGET_RESPONSE_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "object_name": {"type": "string", "minLength": 1, "maxLength": 60},
        "colour": {"type": "string", "enum": ["red", "orange", "yellow", "green", "blue", "purple", "pink", "brown", "black", "white", "grey"]},
        "category": {"type": "string", "minLength": 1, "maxLength": 40},
        "location": {"type": "string", "minLength": 1, "maxLength": 100},
        "frame_index": {"type": "integer", "minimum": 0, "maximum": 2},
        "bbox": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
        "confidence": {"type": "number", "minimum": 0.78, "maximum": 1.0},
        "stable": {"type": "boolean"},
        "visible_frame_count": {"type": "integer", "minimum": 1, "maximum": 3},
        "hints_en": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 3},
        "hints_nl": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 3},
    },
    "required": ["object_name", "colour", "category", "location", "frame_index", "bbox", "confidence", "stable", "visible_frame_count", "hints_en", "hints_nl"],
}


class PresenceRequest(StrictModel):
    frame_jpeg: str
    target: TargetReference

    @field_validator("frame_jpeg")
    @classmethod
    def valid_frame(cls, value: str) -> str:
        _decode_frame(value)
        return value


class GuessRequest(StrictModel):
    guess: str = Field(min_length=1, max_length=80)
    target: TargetReference
    language: Literal["en", "nl"]


class TTSRequest(StrictModel):
    text: str = Field(min_length=1, max_length=500)
    language: Literal["en", "nl"]


class CancelRequest(StrictModel):
    pass


def _decode_frame(value: str) -> bytes:
    if len(value) > 2_000_000:
        raise ValueError("frame exceeds encoded limit")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("frame is not strict base64") from exc
    if not 4 <= len(decoded) <= 1_500_000 or not decoded.startswith(b"\xff\xd8"):
        raise ValueError("frame is not a bounded JPEG")
    return decoded


@dataclass(frozen=True)
class BrokerSecrets:
    provider_key: str
    clients: dict[str, str]

    @classmethod
    def load(cls, path: Path | None = None) -> BrokerSecrets:
        location = path or Path(os.environ.get("ISPY_BROKER_SECRETS", "/etc/hermes-ispy-broker/secrets.json"))
        mode = stat.S_IMODE(location.stat().st_mode)
        if mode & 0o077:
            raise BrokerError("Broker secrets file must be owner-only")
        payload = json.loads(location.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or set(payload) != {"openai_api_key", "clients"}:
            raise BrokerError("Broker secrets file has an invalid shape")
        key, clients = payload["openai_api_key"], payload["clients"]
        if not isinstance(key, str) or not key or not isinstance(clients, dict) or not clients:
            raise BrokerError("Broker secrets file is incomplete")
        if any(not isinstance(token, str) or len(token) < 32 or not isinstance(device, str) or not _ID.fullmatch(device)
               for token, device in clients.items()):
            raise BrokerError("Broker client binding is invalid")
        return cls(key, clients)


class Backend(Protocol):
    def moderate(self, text: str) -> bool: ...
    def select_target(self, frames: list[bytes], language: str, age_band: str) -> dict[str, object]: ...
    def target_present(self, frame: bytes, target: dict[str, object]) -> bool: ...
    def judge_guess(self, guess: str, target: dict[str, object], language: str) -> bool: ...
    def tts(self, text: str, language: str) -> bytes: ...


class OpenAIBackend:
    """Fixed-policy OpenAI adapter. Callers cannot choose URL, model, prompt, or tools."""

    def __init__(self, api_key: str, *, api_base: str = "https://api.openai.com/v1") -> None:
        self._api_key = api_key
        self._api_base = api_base.rstrip("/")
        self._opener = urllib.request.build_opener(_NoRedirect)

    def _call(self, path: str, payload: dict[str, object], *, accept: str = "application/json", maximum: int = 256_000) -> bytes:
        request = urllib.request.Request(
            f"{self._api_base}/{path}", data=json.dumps(payload, separators=(",", ":")).encode(), method="POST",
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json", "Accept": accept},
        )
        try:
            with self._opener.open(request, timeout=15) as response:
                content_type = response.headers.get_content_type()
                data = response.read(maximum + 1)
                status = response.status
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            raise BrokerError("Provider request failed") from exc
        expected = "audio/wav" if accept == "audio/wav" else "application/json"
        if status != 200 or content_type != expected or len(data) > maximum:
            raise BrokerError("Provider returned an invalid bounded response")
        return data

    def _json(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        try:
            value = json.loads(self._call(path, payload))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise BrokerError("Provider returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise BrokerError("Provider response shape was invalid")
        return value

    @staticmethod
    def _assistant_json(value: dict[str, object]) -> dict[str, object]:
        try:
            choices = value["choices"]
            assert isinstance(choices, list) and len(choices) == 1
            message = choices[0]["message"]  # type: ignore[index]
            content = message["content"]  # type: ignore[index]
            assert isinstance(content, str) and len(content) <= 4000
            result = json.loads(content)
            assert isinstance(result, dict)
            return result
        except (KeyError, TypeError, AssertionError, json.JSONDecodeError) as exc:
            raise BrokerError("Provider model result was invalid") from exc

    def moderate(self, text: str) -> bool:
        result = self._json("moderations", {"model": MODERATION_MODEL, "input": text}).get("results")
        return isinstance(result, list) and len(result) == 1 and isinstance(result[0], dict) and result[0].get("flagged") is False

    def _vision_json(
        self,
        content: object,
        max_tokens: int,
        *,
        schema: dict[str, object] | None = None,
    ) -> dict[str, object]:
        response_format: dict[str, object] = {"type": "json_object"}
        if schema is not None:
            response_format = {
                "type": "json_schema",
                "json_schema": {"name": "ispy_target", "strict": True, "schema": schema},
            }
        return self._assistant_json(self._json("chat/completions", {
            "model": VISION_MODEL, "temperature": 0, "max_tokens": max_tokens,
            "response_format": response_format, "messages": [{"role": "user", "content": content}],
        }))

    def select_target(self, frames: list[bytes], language: str, age_band: str) -> dict[str, object]:
        forbidden = ", ".join(sorted(DISALLOWED_TERMS))
        prompt = (
            "Select exactly one fixed child-safe household object visible clearly and consistently in at least two supplied frames. "
            "Never select a person, face, body, clothing, screen, document, medicine, weapon, private/sensitive, reflective, tiny, or unstable item. "
            "Return strict JSON with exactly these fields: object_name, colour, category, location, frame_index, bbox, confidence, stable, visible_frame_count, hints_en, hints_nl. "
            "colour must be exactly one of red, orange, yellow, green, blue, purple, pink, brown, black, white, grey. "
            "Set stable=true, visible_frame_count between 2 and the number of supplied frames, and confidence between 0.78 and 1.0. "
            "bbox must be normalized [x,y,width,height], fully inside the image, with width and height at least 0.12 and area between 0.025 and 0.65. "
            "Provide 1-3 short child-safe hints in both English and Dutch. "
            "Do not use any of these forbidden words in object_name, category, location, hints_en, or hints_nl—even to describe what the target is near: "
            f"{forbidden}. Use a generic safe location such as 'in the room' instead. "
            f"Age band {age_band}; language {language}."
        )
        content: list[dict[str, object]] = [{"type": "text", "text": prompt}]
        content.extend({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(frame).decode(), "detail": "low"}} for frame in frames)
        target = TargetPayload.model_validate(
            self._vision_json(content, 500, schema=TARGET_RESPONSE_SCHEMA)
        )
        if target.frame_index >= len(frames) or target.visible_frame_count > len(frames):
            raise BrokerError("Provider selected an invalid frame")
        target_payload = target.model_dump(mode="json")
        try:
            validate_target(target_payload, frame_count=len(frames))
        except ValueError as exc:
            _LOGGER.warning("I Spy provider candidate failed app policy: %s", str(exc))
            raise BrokerError("Provider candidate failed app policy") from exc
        moderation_text = " ".join((target.object_name, target.category, target.location, *target.hints_en, *target.hints_nl))
        if not self.moderate(moderation_text):
            raise BrokerError("Generated target was not approved")
        return target_payload

    def target_present(self, frame: bytes, target: dict[str, object]) -> bool:
        content = [{"type": "text", "text": f"Is the same {target['colour']} {target['object_name']} still clearly visible near {target['location']}? Return strict JSON with present boolean and confidence number."},
                   {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(frame).decode(), "detail": "low"}}]
        result = self._vision_json(content, 40)
        confidence = result.get("confidence")
        return result.get("present") is True and isinstance(confidence, (int, float)) and confidence >= 0.72

    def judge_guess(self, guess: str, target: dict[str, object], language: str) -> bool:
        if not self.moderate(guess):
            raise BrokerError("Guess was not approved")
        prompt = ("Decide only whether the guess names the same ordinary object, allowing simple synonyms and singular/plural. "
                  f"Language: {language}. Target: {target['object_name']}. Guess: {guess}. Return strict JSON: {{\"match\": true|false}}.")
        result = self._vision_json(prompt, 30)
        if set(result) != {"match"} or not isinstance(result["match"], bool):
            raise BrokerError("Guess result was invalid")
        return result["match"]

    def tts(self, text: str, language: str) -> bytes:
        if not self.moderate(text):
            raise BrokerError("Speech was not approved")
        return self._call("audio/speech", {"model": TTS_MODEL, "voice": TTS_VOICE, "input": text, "response_format": "wav",
                                           "instructions": "Speak warmly, clearly, briefly, and child-appropriately in " + ("Dutch." if language == "nl" else "English.")},
                          accept="audio/wav", maximum=8_000_000)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: object, fp: object, code: int, msg: str, headers: object, newurl: str) -> None:
        return None


@dataclass(frozen=True)
class CallContext:
    device: str
    session: str
    sequence: int


class BrokerState:
    SESSION_TTL_SECONDS = 900.0
    MAX_TRACKED_SESSIONS = 4096

    def __init__(self, clients: dict[str, str], *, rate_limit: int = 30) -> None:
        self.clients = clients
        self.rate_limit = rate_limit
        self._requests: dict[tuple[str, str], int] = {}
        self._cancelled: dict[tuple[str, str], float] = {}
        self._last_seen: dict[tuple[str, str], float] = {}
        self._rates: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def authenticate(self, authorization: str, device: str, session: str, sequence: str) -> CallContext:
        if not authorization.startswith("Bearer ") or not _ID.fullmatch(device) or not _SESSION.fullmatch(session):
            raise HTTPException(401, "Unauthorized")
        token = authorization[7:]
        bound = next((expected for candidate, expected in self.clients.items() if hmac.compare_digest(candidate, token)), None)
        if bound is None or not hmac.compare_digest(bound, device):
            raise HTTPException(401, "Unauthorized")
        try:
            number = int(sequence)
        except ValueError as exc:
            raise HTTPException(400, "Invalid request sequence") from exc
        if not 1 <= number <= 2_147_483_647:
            raise HTTPException(400, "Invalid request sequence")
        now = time.monotonic()
        with self._lock:
            expired = [key for key, seen in self._last_seen.items() if seen < now - self.SESSION_TTL_SECONDS]
            for key in expired:
                self._last_seen.pop(key, None)
                self._requests.pop(key, None)
                self._cancelled.pop(key, None)
            while len(self._last_seen) >= self.MAX_TRACKED_SESSIONS:
                oldest = min(self._last_seen, key=self._last_seen.get)
                self._last_seen.pop(oldest, None)
                self._requests.pop(oldest, None)
                self._cancelled.pop(oldest, None)
            rate = self._rates[device]
            while rate and rate[0] < now - 60:
                rate.popleft()
            if len(rate) >= self.rate_limit:
                raise HTTPException(429, "Rate limit exceeded")
            rate.append(now)
            key = (device, session)
            if key in self._cancelled:
                raise HTTPException(409, "Session cancelled")
            previous = self._requests.get(key, 0)
            if number <= previous:
                raise HTTPException(409, "Stale request rejected")
            self._requests[key] = number
            self._last_seen[key] = now
        return CallContext(device, session, number)

    def cancel(self, context: CallContext) -> None:
        with self._lock:
            key = (context.device, context.session)
            now = time.monotonic()
            self._cancelled[key] = now
            self._last_seen[key] = now

    def current(self, context: CallContext) -> bool:
        with self._lock:
            key = (context.device, context.session)
            return key not in self._cancelled and self._requests.get(key) == context.sequence


def create_app(*, secrets: BrokerSecrets | None = None, backend: Backend | None = None, rate_limit: int = 30) -> FastAPI:
    secrets = secrets or BrokerSecrets.load()
    backend = backend or OpenAIBackend(secrets.provider_key)
    state = BrokerState(secrets.clients, rate_limit=rate_limit)
    app = FastAPI(title="Hermes I Spy Broker", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def request_policy(request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.url.path.startswith(BASE_PATH):
            if request.method != "POST" or request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                return JSONResponse({"detail": "Strict JSON POST required"}, status_code=415)
            try:
                length = int(request.headers.get("content-length", "0"))
            except ValueError:
                return JSONResponse({"detail": "Invalid request size"}, status_code=400)
            if length > MAX_BODY:
                return JSONResponse({"detail": "Request too large"}, status_code=413)
            body = await request.body()
            if len(body) > MAX_BODY:
                return JSONResponse({"detail": "Request too large"}, status_code=413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def context(
        authorization: Annotated[str, Header()],
        x_i_spy_device: Annotated[str, Header()],
        x_i_spy_session: Annotated[str, Header()],
        x_i_spy_request: Annotated[str, Header()],
    ) -> CallContext:
        return state.authenticate(authorization, x_i_spy_device, x_i_spy_session, x_i_spy_request)

    async def bounded(ctx: CallContext, operation, *args):  # type: ignore[no-untyped-def]
        try:
            result = await asyncio.wait_for(run_in_threadpool(operation, *args), timeout=OPERATION_TIMEOUT)
        except TimeoutError as exc:
            raise HTTPException(504, "Provider operation timed out") from exc
        except Exception as exc:
            if isinstance(exc, ValidationError):
                details = [
                    {"loc": list(error["loc"]), "type": error["type"]}
                    for error in exc.errors(include_url=False, include_context=False, include_input=False)
                ]
                _LOGGER.warning("I Spy provider response validation failed closed: %s", details)
            else:
                _LOGGER.warning("I Spy provider operation failed closed (%s)", type(exc).__name__)
            raise HTTPException(502, "Provider operation failed safely") from exc
        if not state.current(ctx):
            raise HTTPException(409, "Late provider result rejected")
        return result

    @app.post(f"{BASE_PATH}/moderate")
    async def moderate(payload: ModerateRequest, ctx: CallContext = Depends(context)) -> dict[str, bool]:
        return {"allowed": bool(await bounded(ctx, backend.moderate, payload.text))}

    @app.post(f"{BASE_PATH}/select-target")
    async def select_target(payload: SelectRequest, ctx: CallContext = Depends(context)) -> dict[str, object]:
        frames = [_decode_frame(value) for value in payload.frames_jpeg]
        return {"target": await bounded(ctx, backend.select_target, frames, payload.language, payload.age_band)}

    @app.post(f"{BASE_PATH}/target-present")
    async def target_present(payload: PresenceRequest, ctx: CallContext = Depends(context)) -> dict[str, bool]:
        result = await bounded(ctx, backend.target_present, _decode_frame(payload.frame_jpeg), payload.target.model_dump())
        return {"present": bool(result)}

    @app.post(f"{BASE_PATH}/judge-guess")
    async def judge_guess(payload: GuessRequest, ctx: CallContext = Depends(context)) -> dict[str, bool]:
        result = await bounded(ctx, backend.judge_guess, payload.guess, payload.target.model_dump(), payload.language)
        return {"match": bool(result)}

    @app.post(f"{BASE_PATH}/tts")
    async def tts(payload: TTSRequest, ctx: CallContext = Depends(context)) -> Response:
        audio = await bounded(ctx, backend.tts, payload.text, payload.language)
        if not isinstance(audio, bytes) or not 44 <= len(audio) <= 8_000_000 or not audio.startswith(b"RIFF"):
            raise HTTPException(502, "Provider audio failed validation")
        return Response(audio, media_type="audio/wav", headers={"Cache-Control": "no-store"})

    @app.post(f"{BASE_PATH}/cancel")
    async def cancel(payload: CancelRequest, ctx: CallContext = Depends(context)) -> dict[str, bool]:
        state.cancel(ctx)
        return {"cancelled": True}

    return app


def app_factory() -> FastAPI:
    """Uvicorn factory that loads owner-only secrets on the Hermes host."""
    return create_app()
