"""Deterministic game rules and child-safe target validation."""

from __future__ import annotations

import math
import random
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

Language = Literal["en", "nl"]
AgeBand = Literal["4-6", "7-9", "10-12"]
MAX_GUESSES = 3
MAX_CANDIDATES = 4

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
    "wapen", "mes", "privé", "telefoon", "mensen", "knives", "children", "kinderen",
    "lichamen", "messen", "bodies", "creditcard",
}
_DISALLOWED_PHRASES = tuple(
    re.compile(r"\b" + r"[\W_]+".join(map(re.escape, term.split())) + r"(?:s|es|en)?\b")
    for term in sorted(DISALLOWED_TERMS)
    if " " in term
)


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
    """Reject whole words, simple EN/NL plurals and separated multi-word terms."""
    folded = text.casefold()
    words = set(re.findall(r"[\wÀ-ÿ]+", folded))
    stems = {word[:-len(end)] for word in words for end in ("s", "es", "en") if word.endswith(end)}
    return bool((words | stems) & DISALLOWED_TERMS) or any(
        phrase.search(folded) for phrase in _DISALLOWED_PHRASES
    )


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
    visible_frame_count = payload.get("visible_frame_count", 0)
    if type(visible_frame_count) is not int:
        raise ValueError("Invalid visibility count")
    if visible_frame_count < min(2, frame_count) or visible_frame_count > frame_count:
        raise ValueError("Candidate was not visible in enough viewpoints")
    name = _clean_text(payload.get("object_name"), maximum=60)
    category = _clean_text(payload.get("category"), maximum=40)
    location = _clean_text(payload.get("location"), maximum=100)
    colour = _clean_text(payload.get("colour"), maximum=16).casefold()
    if colour not in COLOURS:
        raise ValueError("Candidate does not have an approved clear colour")
    if _contains_disallowed(" ".join((name, category, location))):
        raise ValueError("Candidate belongs to a disallowed class")
    raw_confidence = payload.get("confidence", 0.0)
    if type(raw_confidence) not in {int, float}:
        raise ValueError("Invalid confidence type")
    confidence = float(raw_confidence)
    if not math.isfinite(confidence) or confidence < minimum_confidence or confidence > 1.0:
        raise ValueError("Candidate confidence is too low")
    frame_index = payload.get("frame_index", -1)
    if type(frame_index) is not int or not 0 <= frame_index < frame_count:
        raise ValueError("Invalid candidate frame")
    bbox_raw = payload.get("bbox")
    if not isinstance(bbox_raw, list) or len(bbox_raw) != 4:
        raise ValueError("Invalid bounding box")
    if any(type(value) not in {int, float} or not math.isfinite(value) for value in bbox_raw):
        raise ValueError("Invalid bounding box coordinates")
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


def object_key(name: str) -> str:
    """Bounded RAM-only diversity key, not a persistent room/object identity."""
    words = [word for word in re.findall(r"[^\W_]+", name.casefold())
             if word not in {"a", "an", "the", "een", "de", "het"}]
    return " ".join(words)


def _same_spot(first: Target, second: Target) -> bool:
    if first.frame_index != second.frame_index:
        return False
    x, y, w, h = first.bbox
    xx, yy, ww, hh = second.bbox
    overlap = max(0.0, min(x + w, xx + ww) - max(x, xx)) * max(0.0, min(y + h, yy + hh) - max(y, yy))
    return overlap / (w * h + ww * hh - overlap) >= 0.6


def validate_candidate_pool(payload: object, *, frame_count: int) -> list[Target]:
    """Validate the entire bounded pool before deduplication or random choice."""
    if not isinstance(payload, dict) or set(payload) != {"candidates"}:
        raise ValueError("Invalid candidate pool shape")
    raw = payload["candidates"]
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_CANDIDATES:
        raise ValueError("Invalid candidate pool size")
    fields = {"object_name", "colour", "category", "location", "frame_index", "bbox", "confidence",
              "stable", "visible_frame_count", "hints_en", "hints_nl"}
    validated = []
    for value in raw:
        if not isinstance(value, dict) or set(value) != fields:
            continue
        try:
            validated.append(validate_target(value, frame_count=frame_count))
        except (ValueError, TypeError, OverflowError):
            continue
    unique: list[Target] = []
    for target in validated:
        if not any(object_key(target.object_name) == object_key(old.object_name) or _same_spot(target, old)
                   for old in unique):
            unique.append(target)
    if not unique:
        raise ValueError("No eligible candidate")
    return unique


def candidate_order(targets: list[Target], *, recent: tuple[str, ...] = (),
                    shuffle: Callable[[list[Target]], None] | None = None) -> list[Target]:
    """Randomize in code; prefer unplayed alternatives without weakening policy."""
    unseen = [target for target in targets if object_key(target.object_name) not in recent]
    repeated = [target for target in targets if object_key(target.object_name) in recent]
    shuffle = shuffle or random.SystemRandom().shuffle
    shuffle(unseen)
    shuffle(repeated)
    return unseen + repeated


def _phrase_key(text: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", text.casefold().replace("'", "").replace("\u2019", "")))


def guess_intent(text: str) -> str:
    """Only whole, short commands have host intent; ordinary object guesses do not."""
    key = _phrase_key(text)
    if key in {"hint", "a hint", "a hint please", "hint please", "help", "help me", "een hint",
               "hint graag", "mag ik een hint", "help mij"}:
        return "hint"
    if key in {"i dont know", "dont know", "i do not know", "i give up", "give up", "no idea",
               "ik weet het niet", "weet ik niet", "geen idee", "ik geef op", "geef op"}:
        return "give_up"
    return "guess"


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
        self._recent: list[str] = []

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
            key = object_key(target.object_name)
            self._recent = [*[old for old in self._recent if old != key][-2:], key]
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
            self._recent.clear()
            self.message = message or ("Game stopped." if self.language == "en" else "Spel gestopt.")
            return self.generation

    def fail(self, message: str) -> int:
        with self._lock:
            self.generation += 1
            self.camera_consent = False
            self.target = None
            self.state = GameState.ERROR
            self._recent.clear()
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

    def next_hint(self) -> str | None:
        with self._lock:
            if self.target is None:
                raise RuntimeError("No target")
            hints = self.target.hints_en if self.language == "en" else self.target.hints_nl
            colour = COLOURS[self.target.colour][self.language]
            reminder = f"It is {colour}." if self.language == "en" else f"De kleur is {colour}."
            unique: dict[str, str] = {}
            for hint in (*hints, reminder):
                unique.setdefault(_phrase_key(hint), hint)
            ladder = list(unique.values())
            if self.hint_index >= len(ladder):
                return None
            hint = ladder[self.hint_index]
            self.hint_index += 1
            return hint

    def recent_keys(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._recent)

    def current_target(self, generation: int) -> Target | None:
        with self._lock:
            if self._is_current_unlocked(generation) and self.state == GameState.GUESSING:
                return self.target
            return None

    def reply_if_current(self, generation: int, *, intent: str, matched: bool = False) -> tuple[str, bool] | None:
        """Commit only judged guesses; command hints/give-up never spend an attempt."""
        with self._lock:
            target = self.current_target(generation)
            if target is None:
                return None
            if intent not in {"guess", "hint", "give_up"} or type(matched) is not bool:
                raise ValueError("Invalid game reply")
            if intent == "guess":
                self.guess_count += 1
            terminal = intent == "give_up" or matched or self.guess_count >= MAX_GUESSES
            hint = None if terminal else self.next_hint()
            if intent == "hint" and hint is None:
                terminal = True
            if terminal:
                self.state = GameState.REVEAL
                if self.language == "en":
                    self.message = ("Yes! " if matched else "Good trying! ") + f"It was {target.object_name}."
                else:
                    self.message = ("Ja! " if matched else "Goed geprobeerd! ") + f"Het was: {target.object_name}."
            elif hint:
                self.message = (f"Here is a hint: {hint}" if self.language == "en" else f"Hier is een hint: {hint}")
            else:
                left = MAX_GUESSES - self.guess_count
                self.message = (f"Not quite. You have {left} guesses left." if self.language == "en"
                                else f"Nog niet. Je mag nog {left} keer raden.")
            return self.message, terminal

    def finish_if_current(self, generation: int) -> bool:
        """Revoke a revealed round after its final approved speech, retaining no target."""
        with self._lock:
            if not self._is_current_unlocked(generation) or self.state != GameState.REVEAL:
                return False
            self.generation += 1
            self.camera_consent = False
            self.target = None
            return True

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
