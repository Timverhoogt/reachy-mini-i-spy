"""Deterministic game rules and child-safe target validation."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

Language = Literal["en", "nl"]
AgeBand = Literal["4-6", "7-9", "10-12"]

COLOURS: dict[str, dict[Language, str]] = {
    "red": {"en": "red", "nl": "rood"},
    "orange": {"en": "orange", "nl": "oranje"},
    "yellow": {"en": "yellow", "nl": "geel"},
    "green": {"en": "green", "nl": "groen"},
    "blue": {"en": "blue", "nl": "blauw"},
    "purple": {"en": "purple", "nl": "paars"},
    "pink": {"en": "pink", "nl": "roze"},
    "brown": {"en": "brown", "nl": "bruin"},
    "black": {"en": "black", "nl": "zwart"},
    "white": {"en": "white", "nl": "wit"},
    "grey": {"en": "grey", "nl": "grijs"},
}
DISALLOWED_TERMS = {
    "face", "person", "people", "child", "body", "skin", "hair", "eye", "hand",
    "shirt", "dress", "clothing", "screen", "monitor", "television", "phone", "tablet",
    "document", "paper", "letter", "photo", "medicine", "pill", "drug", "weapon", "gun",
    "knife", "private", "underwear", "passport", "credit card", "password", "address",
    "gezicht", "persoon", "kind", "lichaam", "kleding", "scherm", "medicijn",
    "wapen", "mes", "privé",
}


class GameState(StrEnum):
    IDLE = "idle"
    SEARCHING = "searching"
    SELECTING = "selecting"
    GUESSING = "guessing"
    REVEAL = "reveal"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass(frozen=True)
class Target:
    object_name: str
    colour: str
    category: str
    location: str
    frame_index: int
    bbox: tuple[float, float, float, float]
    confidence: float
    hints_en: tuple[str, ...]
    hints_nl: tuple[str, ...]

    def public_dict(self, *, reveal: bool = False) -> dict[str, object]:
        payload: dict[str, object] = {"colour": self.colour, "confidence": self.confidence}
        if reveal:
            payload.update(object_name=self.object_name, category=self.category, location=self.location)
        return payload


def _clean_text(value: object, *, maximum: int = 80) -> str:
    if not isinstance(value, str):
        raise ValueError("Expected text")
    value = " ".join(value.strip().split())
    if not value or len(value) > maximum or any(ord(ch) < 32 for ch in value):
        raise ValueError("Invalid text")
    return value


def _contains_disallowed(text: str) -> bool:
    words = set(re.findall(r"[\wÀ-ÿ]+", text.casefold()))
    return bool(words & DISALLOWED_TERMS)


def validate_target(
    payload: object,
    *,
    frame_count: int,
    minimum_confidence: float = 0.78,
) -> Target:
    """Validate a provider candidate. Any ambiguity fails closed."""
    if not 0.0 <= minimum_confidence <= 1.0:
        raise ValueError("Candidate confidence policy is invalid")
    if not isinstance(payload, dict) or payload.get("stable") is not True:
        raise ValueError("Candidate is not stable")
    visible_frame_count = int(payload.get("visible_frame_count", 0))
    if visible_frame_count < min(2, frame_count) or visible_frame_count > frame_count:
        raise ValueError("Candidate was not visible in enough viewpoints")
    name = _clean_text(payload.get("object_name"))
    category = _clean_text(payload.get("category"))
    location = _clean_text(payload.get("location"))
    colour = _clean_text(payload.get("colour"), maximum=16).casefold()
    if colour not in COLOURS:
        raise ValueError("Candidate does not have an approved clear colour")
    if _contains_disallowed(" ".join((name, category, location))):
        raise ValueError("Candidate belongs to a disallowed class")
    confidence = float(payload.get("confidence", 0.0))
    if confidence < minimum_confidence or confidence > 1.0:
        raise ValueError("Candidate confidence is too low")
    frame_index = int(payload.get("frame_index", -1))
    if not 0 <= frame_index < frame_count:
        raise ValueError("Invalid candidate frame")
    bbox_raw = payload.get("bbox")
    if not isinstance(bbox_raw, list) or len(bbox_raw) != 4:
        raise ValueError("Invalid bounding box")
    bbox = tuple(float(value) for value in bbox_raw)
    x, y, width, height = bbox
    if min(bbox) < 0 or x + width > 1 or y + height > 1:
        raise ValueError("Bounding box is outside the image")
    area = width * height
    if area < 0.025 or area > 0.65 or min(width, height) < 0.12:
        raise ValueError("Candidate is too small or too broad")

    def hints(language: Language) -> tuple[str, ...]:
        raw = payload.get(f"hints_{language}")
        if not isinstance(raw, list) or not 1 <= len(raw) <= 3:
            raise ValueError("Candidate needs bounded bilingual hints")
        cleaned = tuple(_clean_text(item, maximum=100) for item in raw)
        if any(_contains_disallowed(item) for item in cleaned):
            raise ValueError("Unsafe hint")
        return cleaned

    return Target(name, colour, category, location, frame_index, bbox, confidence, hints("en"), hints("nl"))


class GameMachine:
    """Thread-safe state and generation token; no hardware or network calls."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.state = GameState.IDLE
        self.language: Language = "en"
        self.age_band: AgeBand = "7-9"
        self.camera_consent = False
        self.generation = 0
        self.target: Target | None = None
        self.guess_count = 0
        self.hint_index = 0
        self.message = "Camera is off."

    def start(self, *, language: Language, age_band: AgeBand, camera_consent: bool) -> int:
        if language not in {"en", "nl"} or age_band not in {"4-6", "7-9", "10-12"}:
            raise ValueError("Unsupported game settings")
        if not camera_consent:
            raise PermissionError("Camera opt-in is required for each game session")
        with self._lock:
            self.generation += 1
            self.language, self.age_band = language, age_band
            self.camera_consent = True
            self.target = None
            self.guess_count = self.hint_index = 0
            self.state = GameState.SEARCHING
            self.message = "Searching…" if language == "en" else "Aan het zoeken…"
            return self.generation

    def _is_current_unlocked(self, generation: int) -> bool:
        return self.generation == generation and self.camera_consent and self.state not in {
            GameState.STOPPED, GameState.ERROR
        }

    def is_current(self, generation: int) -> bool:
        with self._lock:
            return self._is_current_unlocked(generation)

    def restart_if_current(self, generation: int) -> int | None:
        """Atomically re-arm the current consented session, or reject stale work."""
        with self._lock:
            if not self._is_current_unlocked(generation):
                return None
            self.generation += 1
            self.target = None
            self.guess_count = self.hint_index = 0
            self.state = GameState.SEARCHING
            self.message = "Searching…" if self.language == "en" else "Aan het zoeken…"
            return self.generation

    def transition(self, state: GameState, generation: int, message: str = "") -> bool:
        with self._lock:
            if not self.is_current(generation):
                return False
            self.state = state
            if message:
                self.message = message
            return True

    def select(self, target: Target, generation: int) -> bool:
        with self._lock:
            if not self.is_current(generation) or self.state != GameState.SELECTING:
                return False
            self.target = target
            self.state = GameState.GUESSING
            colour = COLOURS[target.colour][self.language]
            self.message = (
                f"I spy with my little eye, something that is {colour}."
                if self.language == "en"
                else f"Ik zie, ik zie wat jij niet ziet, en de kleur is {colour}."
            )
            return True

    def stop(self, message: str | None = None) -> int:
        with self._lock:
            self.generation += 1
            self.camera_consent = False
            self.target = None
            self.state = GameState.STOPPED
            self.message = message or ("Game stopped." if self.language == "en" else "Spel gestopt.")
            return self.generation

    def fail(self, message: str) -> int:
        with self._lock:
            self.generation += 1
            self.camera_consent = False
            self.target = None
            self.state = GameState.ERROR
            self.message = message
            return self.generation

    def fail_if_current(self, generation: int, message: str) -> int | None:
        """Fail only the active generation so a concurrent user Stop always wins."""
        with self._lock:
            if not self._is_current_unlocked(generation):
                return None
            return self.fail(message)

    def record_guess(self) -> int:
        with self._lock:
            if self.state != GameState.GUESSING or self.target is None:
                raise RuntimeError("No active guessing round")
            self.guess_count += 1
            return self.guess_count

    def record_guess_if_current(self, generation: int) -> tuple[int, Target] | None:
        """Atomically validate and record a guess without raising for stale work."""
        with self._lock:
            if (
                not self._is_current_unlocked(generation)
                or self.state != GameState.GUESSING
                or self.target is None
            ):
                return None
            self.guess_count += 1
            return self.guess_count, self.target

    def next_hint(self) -> str:
        with self._lock:
            if self.target is None:
                raise RuntimeError("No target")
            hints = self.target.hints_en if self.language == "en" else self.target.hints_nl
            hint = hints[min(self.hint_index, len(hints) - 1)]
            self.hint_index += 1
            return hint

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            reveal = self.state == GameState.REVEAL
            return {
                "state": self.state,
                "language": self.language,
                "age_band": self.age_band,
                "camera_active": self.camera_consent and self.state in {
                    GameState.SEARCHING, GameState.SELECTING, GameState.GUESSING
                },
                "message": self.message,
                "guess_count": self.guess_count,
                "target": self.target.public_dict(reveal=reveal) if self.target else None,
                "generation": self.generation,
            }
