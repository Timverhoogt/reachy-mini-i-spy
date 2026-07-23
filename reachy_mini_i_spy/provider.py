"""In-process fixed-policy providers for standalone I Spy.

No broker, model URL, prompt, or tool surface is exposed to the app UI. The
user selects a supported provider mode and, for cloud mode, supplies one
owner-only API key on the machine running the Reachy daemon.
"""
# Fixed prompts stay visually contiguous for policy review.
# ruff: noqa: E501

from __future__ import annotations

import base64
import json
import logging
import re
import threading
import urllib.error
import urllib.request
from collections.abc import Callable

from .config import AppConfig
from .game import DISALLOWED_TERMS, AgeBand, Language, Target, validate_target

_LOGGER = logging.getLogger(__name__)
VISION_MODEL = "gpt-4.1-mini"
MODERATION_MODEL = "omni-moderation-latest"
TTS_MODEL = "gpt-4o-mini-tts"
TTS_VOICE = "coral"
_API_BASE = "https://api.openai.com/v1"
_TARGET_FIELDS = {
    "object_name",
    "colour",
    "category",
    "location",
    "frame_index",
    "bbox",
    "confidence",
    "stable",
    "visible_frame_count",
    "hints_en",
    "hints_nl",
}
TARGET_RESPONSE_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "object_name": {"type": "string", "minLength": 1, "maxLength": 60},
        "colour": {
            "type": "string",
            "enum": ["red", "orange", "yellow", "green", "blue", "purple", "pink", "brown", "black", "white", "grey"],
        },
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
    "required": sorted(_TARGET_FIELDS),
}


class ProviderError(RuntimeError):
    """A sanitized, fail-closed provider error."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: object, fp: object, code: int, msg: str, headers: object, newurl: str) -> None:
        return None


def _safe_open(request: urllib.request.Request, *, timeout: float) -> object:
    return urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout)


class ProviderClient:
    """Fixed OpenAI adapter that runs inside the Reachy app process.

    The historical class name is retained to minimize risk in the already
    accepted cancellation/runtime code. There is no remote broker client.
    """

    def __init__(
        self,
        config: AppConfig,
        *,
        timeout: float = 18.0,
        opener: Callable[..., object] = _safe_open,
        session_id: str | None = None,
    ) -> None:
        del session_id  # Runtime generations still own cancellation; no remote session exists.
        self.config = config
        self.timeout = min(max(timeout, 1.0), 20.0)
        self._opener = opener
        self._cancelled = threading.Event()
        self._local = None
        if config.provider == "local":
            from .local_assets import local_assets_status

            if not local_assets_status()["ready"]:
                raise ProviderError("Local provider assets are not installed")
            try:
                from .local_provider import LocalProvider

                self._local = LocalProvider(self._cancelled)
            except RuntimeError as exc:
                raise ProviderError(str(exc)) from exc
            return
        if not config.configured or config.provider != "openai":
            raise ProviderError("OpenAI provider configuration is incomplete")

    def cancel_local(self) -> bool:
        if self._cancelled.is_set():
            return False
        self._cancelled.set()
        return True

    def cancel(self) -> None:
        self.cancel_local()

    def _check_current(self) -> None:
        if self._cancelled.is_set():
            raise ProviderError("Provider session was cancelled")

    def _request(
        self,
        path: str,
        payload: dict[str, object],
        *,
        accept: str = "application/json",
        maximum: int = 256_000,
    ) -> bytes:
        self._check_current()
        encoded = json.dumps(payload, separators=(",", ":")).encode()
        if len(encoded) > 5_000_000:
            raise ProviderError("Provider request exceeded the safety limit")
        request = urllib.request.Request(
            f"{_API_BASE}/{path}",
            data=encoded,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
                "Accept": accept,
            },
        )
        try:
            response = self._opener(request, timeout=self.timeout)
            with response:  # type: ignore[attr-defined]
                status = getattr(response, "status", 200)
                content_type = response.headers.get_content_type()  # type: ignore[attr-defined]
                data = response.read(maximum + 1)  # type: ignore[attr-defined]
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            raise ProviderError("Provider request failed") from exc
        expected = "audio/wav" if accept == "audio/wav" else "application/json"
        if status != 200 or content_type != expected or len(data) > maximum:
            raise ProviderError("Provider returned an invalid bounded response")
        self._check_current()
        return data

    def _json(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        try:
            value = json.loads(self._request(path, payload))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProviderError("Provider returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise ProviderError("Provider response shape was invalid")
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
            raise ProviderError("Provider model result was invalid") from exc

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
        response = self._json(
            "chat/completions",
            {
                "model": VISION_MODEL,
                "temperature": 0,
                "max_tokens": max_tokens,
                "response_format": response_format,
                "messages": [{"role": "user", "content": content}],
            },
        )
        return self._assistant_json(response)

    @staticmethod
    def _validate_text(text: str, *, maximum: int) -> str:
        text = " ".join(text.split())
        if not text or len(text) > maximum or re.search(r"[\x00-\x1f]", text):
            raise ProviderError("Text is outside accepted bounds")
        return text

    def moderate(self, text: str) -> None:
        if self._local is not None:
            try:
                self._local.moderate(text)
            except RuntimeError as exc:
                raise ProviderError(str(exc)) from exc
            return
        text = self._validate_text(text, maximum=500)
        results = self._json("moderations", {"model": MODERATION_MODEL, "input": text}).get("results")
        if not (
            isinstance(results, list)
            and len(results) == 1
            and isinstance(results[0], dict)
            and results[0].get("flagged") is False
        ):
            raise ProviderError("Content was not approved by moderation")

    def select_target(self, frames_jpeg: list[bytes], *, language: Language, age_band: AgeBand) -> Target:
        if self._local is not None:
            try:
                return self._local.select_target(frames_jpeg, language=language, age_band=age_band)
            except RuntimeError as exc:
                raise ProviderError(str(exc)) from exc
        if not 1 <= len(frames_jpeg) <= 3 or any(not frame or len(frame) > 1_500_000 for frame in frames_jpeg):
            raise ProviderError("Camera frame bounds were not met")
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
        content.extend(
            {
                "type": "image_url",
                "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(frame).decode(), "detail": "low"},
            }
            for frame in frames_jpeg
        )
        result = self._vision_json(content, 500, schema=TARGET_RESPONSE_SCHEMA)
        if set(result) != _TARGET_FIELDS:
            raise ProviderError("Provider returned an invalid target shape")
        try:
            target = validate_target(result, frame_count=len(frames_jpeg))
        except (TypeError, ValueError) as exc:
            _LOGGER.warning("I Spy provider candidate failed app policy (%s)", type(exc).__name__)
            raise ProviderError("Provider candidate failed app policy") from exc
        self.moderate(
            " ".join(
                (
                    target.object_name,
                    target.category,
                    target.location,
                    *target.hints_en,
                    *target.hints_nl,
                )
            )
        )
        return target

    @staticmethod
    def _target_payload(target: Target) -> dict[str, object]:
        return {
            "object_name": target.object_name,
            "colour": target.colour,
            "category": target.category,
            "location": target.location,
            "frame_index": target.frame_index,
            "bbox": list(target.bbox),
            "confidence": target.confidence,
            "hints_en": list(target.hints_en),
            "hints_nl": list(target.hints_nl),
        }

    def target_present(self, frame_jpeg: bytes, target: Target) -> bool:
        if self._local is not None:
            try:
                return self._local.target_present(frame_jpeg, target)
            except RuntimeError as exc:
                raise ProviderError(str(exc)) from exc
        if not frame_jpeg or len(frame_jpeg) > 1_500_000:
            raise ProviderError("Camera frame bounds were not met")
        content = [
            {
                "type": "text",
                "text": (
                    f"Is the same {target.colour} {target.object_name} still clearly visible near "
                    f"{target.location}? Return strict JSON with present boolean and confidence number."
                ),
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/jpeg;base64," + base64.b64encode(frame_jpeg).decode(),
                    "detail": "low",
                },
            },
        ]
        result = self._vision_json(content, 40)
        confidence = result.get("confidence")
        return result.get("present") is True and isinstance(confidence, (int, float)) and confidence >= 0.72

    def judge_guess(self, guess: str, target: Target, *, language: Language) -> bool:
        if self._local is not None:
            try:
                return self._local.judge_guess(guess, target, language=language)
            except RuntimeError as exc:
                raise ProviderError(str(exc)) from exc
        guess = self._validate_text(guess, maximum=80)
        self.moderate(guess)
        prompt = (
            "Decide only whether the guess names the same ordinary object, allowing simple synonyms and singular/plural. "
            f"Language: {language}. Target: {target.object_name}. Guess: {guess}. "
            'Return strict JSON: {"match": true|false}.'
        )
        result = self._vision_json(prompt, 30)
        if set(result) != {"match"} or not isinstance(result["match"], bool):
            raise ProviderError("Guess result was invalid")
        return result["match"]

    def synthesize_wav(self, text: str, *, language: Language) -> bytes:
        if self._local is not None:
            try:
                return self._local.synthesize_wav(text, language=language)
            except RuntimeError as exc:
                raise ProviderError(str(exc)) from exc
        text = self._validate_text(text, maximum=500)
        self.moderate(text)
        return self._request(
            "audio/speech",
            {
                "model": TTS_MODEL,
                "voice": TTS_VOICE,
                "input": text,
                "response_format": "wav",
                "instructions": (
                    "Speak warmly, clearly, briefly, and child-appropriately in "
                    + ("Dutch." if language == "nl" else "English.")
                ),
            },
            accept="audio/wav",
            maximum=8_000_000,
        )
