"""Client for the standalone app's narrow off-robot provider broker."""

from __future__ import annotations

import base64
import contextlib
import json
import re
import threading
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable

from .config import AppConfig
from .game import AgeBand, Language, Target, validate_target


class ProviderError(RuntimeError):
    """A fail-closed provider boundary error."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: object, fp: object, code: int, msg: str, headers: object, newurl: str) -> None:
        return None


def _safe_opener(request: urllib.request.Request, *, timeout: float) -> object:
    return urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout)


class ProviderClient:
    """Expose only fixed I Spy operations; models and provider protocol never reach Reachy."""

    def __init__(
        self,
        config: AppConfig,
        *,
        timeout: float = 15.0,
        opener: Callable[..., object] = _safe_opener,
        session_id: str | None = None,
    ) -> None:
        if not config.configured:
            raise ProviderError("Broker configuration is incomplete")
        self.config = config
        self.timeout = min(max(timeout, 1.0), 20.0)
        self._opener = opener
        self._session_id = session_id or uuid.uuid4().hex
        self._sequence = 0
        self._cancelled = threading.Event()
        self._lock = threading.Lock()

    def _headers(self, sequence: int, accept: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.broker_token}",
            "Content-Type": "application/json",
            "Accept": accept,
            "X-I-Spy-Device": self.config.device_id,
            "X-I-Spy-Session": self._session_id,
            "X-I-Spy-Request": str(sequence),
        }

    def _request(
        self,
        path: str,
        payload: dict[str, object],
        *,
        maximum: int = 256_000,
        accept: str = "application/json",
        allow_cancelled: bool = False,
    ) -> bytes:
        if self._cancelled.is_set() and not allow_cancelled:
            raise ProviderError("Provider session was cancelled")
        encoded = json.dumps(payload, separators=(",", ":")).encode()
        if len(encoded) > 5_000_000:
            raise ProviderError("Broker request exceeded the safety limit")
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
        request = urllib.request.Request(
            f"{self.config.provider_url}/{path}",
            data=encoded,
            headers=self._headers(sequence, accept),
            method="POST",
        )
        try:
            response = self._opener(request, timeout=self.timeout)
            with response:  # type: ignore[attr-defined]
                status = getattr(response, "status", 200)
                content_type = response.headers.get_content_type()  # type: ignore[attr-defined]
                data = response.read(maximum + 1)  # type: ignore[attr-defined]
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            raise ProviderError("Broker request failed") from exc
        expected = "audio/wav" if accept == "audio/wav" else "application/json"
        if status != 200 or content_type != expected:
            raise ProviderError("Broker returned an invalid response")
        if len(data) > maximum:
            raise ProviderError("Broker response exceeded the safety limit")
        if self._cancelled.is_set() and not allow_cancelled:
            raise ProviderError("Cancelled provider result was rejected")
        return data

    def _json_request(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        try:
            decoded = json.loads(self._request(path, payload))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProviderError("Broker returned invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise ProviderError("Broker returned an invalid response shape")
        return decoded

    def cancel(self) -> None:
        """Reject local late results immediately and best-effort cancel the broker session."""
        if not self.cancel_local():
            return
        self.cancel_broker()

    def cancel_local(self) -> bool:
        """Synchronously revoke local result acceptance; return whether this call won."""
        with self._lock:
            if self._cancelled.is_set():
                return False
            self._cancelled.set()
            return True

    def cancel_broker(self) -> None:
        """Best-effort remote cancellation after local revocation has linearized."""
        with contextlib.suppress(ProviderError):
            self._request("cancel", {}, maximum=1024, allow_cancelled=True)

    def moderate(self, text: str) -> None:
        if not text.strip() or len(text) > 500 or re.search(r"[\x00-\x1f]", text):
            raise ProviderError("Text is outside moderation bounds")
        result = self._json_request("moderate", {"text": text})
        if result != {"allowed": True}:
            raise ProviderError("Content was not approved by moderation")

    def select_target(self, frames_jpeg: list[bytes], *, language: Language, age_band: AgeBand) -> Target:
        if not 1 <= len(frames_jpeg) <= 3 or any(not frame or len(frame) > 1_500_000 for frame in frames_jpeg):
            raise ProviderError("Camera frame bounds were not met")
        result = self._json_request("select-target", {
            "frames_jpeg": [base64.b64encode(frame).decode("ascii") for frame in frames_jpeg],
            "language": language,
            "age_band": age_band,
        })
        if set(result) != {"target"} or not isinstance(result["target"], dict):
            raise ProviderError("Broker returned an invalid target")
        return validate_target(result["target"], frame_count=len(frames_jpeg))

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

    def judge_guess(self, guess: str, target: Target, *, language: Language) -> bool:
        guess = " ".join(guess.split())
        if not guess or len(guess) > 80 or re.search(r"[\x00-\x1f]", guess):
            raise ProviderError("Guess is outside accepted bounds")
        result = self._json_request("judge-guess", {
            "guess": guess,
            "target": self._target_payload(target),
            "language": language,
        })
        if set(result) != {"match"} or not isinstance(result["match"], bool):
            raise ProviderError("Broker returned an invalid guess result")
        return result["match"]

    def target_present(self, frame_jpeg: bytes, target: Target) -> bool:
        if not frame_jpeg or len(frame_jpeg) > 1_500_000:
            raise ProviderError("Camera frame bounds were not met")
        result = self._json_request("target-present", {
            "frame_jpeg": base64.b64encode(frame_jpeg).decode("ascii"),
            "target": self._target_payload(target),
        })
        if set(result) != {"present"} or not isinstance(result["present"], bool):
            raise ProviderError("Broker returned an invalid presence result")
        return result["present"]

    def synthesize_wav(self, text: str, *, language: Language) -> bytes:
        if not text.strip() or len(text) > 500 or re.search(r"[\x00-\x1f]", text):
            raise ProviderError("Speech text is outside accepted bounds")
        return self._request("tts", {"text": text, "language": language}, maximum=8_000_000, accept="audio/wav")