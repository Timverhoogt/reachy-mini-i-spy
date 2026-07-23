"""No-key local object detection, deterministic game decisions, and offline TTS."""
# Fixed bilingual policy tables stay visually reviewable.
# ruff: noqa: E501

from __future__ import annotations

import io
import re
import threading
import wave
from dataclasses import dataclass
from typing import ClassVar

import numpy as np
import onnxruntime as ort
import sherpa_onnx
from PIL import Image

from .game import DISALLOWED_TERMS, AgeBand, Language, Target, validate_target
from .local_assets import VOICE_ASSETS, detector_path, local_assets_status, voice_path


@dataclass(frozen=True)
class ObjectInfo:
    en: str
    nl: str
    category: str
    hint_en: str
    hint_nl: str
    aliases_en: tuple[str, ...] = ()
    aliases_nl: tuple[str, ...] = ()


# Explicit child-safe subset of the exact TorchVision COCO label map used by
# ssdlite320_mobilenet_v3_large. Everything not listed fails closed.
OBJECTS: dict[int, ObjectInfo] = {
    15: ObjectInfo("bench", "bank", "furniture", "You can sit on it.", "Je kunt erop zitten."),
    34: ObjectInfo("frisbee", "frisbee", "toy", "You can throw it.", "Je kunt hem gooien."),
    37: ObjectInfo("ball", "bal", "toy", "It can roll.", "Hij kan rollen.", ("sports ball",), ("sportbal",)),
    47: ObjectInfo("cup", "beker", "tableware", "You can drink from it.", "Je kunt eruit drinken.", ("mug",), ("mok", "kopje")),
    51: ObjectInfo("bowl", "kom", "tableware", "It can hold food.", "Er kan eten in.", (), ("schaal",)),
    52: ObjectInfo("banana", "banaan", "food", "It is a fruit.", "Het is fruit."),
    53: ObjectInfo("apple", "appel", "food", "It grows on a tree.", "Het groeit aan een boom."),
    55: ObjectInfo("orange", "sinaasappel", "food", "It is a round fruit.", "Het is rond fruit."),
    56: ObjectInfo("broccoli", "broccoli", "food", "It is a vegetable.", "Het is groente."),
    57: ObjectInfo("carrot", "wortel", "food", "It is a vegetable.", "Het is groente."),
    59: ObjectInfo("pizza", "pizza", "food", "It is usually round.", "Het is meestal rond."),
    60: ObjectInfo("donut", "donut", "food", "It often has a hole.", "Er zit vaak een gat in."),
    61: ObjectInfo("cake", "taart", "food", "It is a sweet treat.", "Het is een zoete traktatie."),
    62: ObjectInfo("chair", "stoel", "furniture", "You can sit on it.", "Je kunt erop zitten."),
    63: ObjectInfo("sofa", "bank", "furniture", "More than one person can sit on it.", "Je kunt er samen op zitten.", ("couch",), ("zetel",)),
    64: ObjectInfo("plant", "plant", "decoration", "It has leaves.", "Het heeft bladeren.", ("potted plant",), ("kamerplant",)),
    67: ObjectInfo("table", "tafel", "furniture", "You can put things on it.", "Je kunt er dingen op zetten.", ("dining table",), ("eettafel",)),
    78: ObjectInfo("microwave", "magnetron", "appliance", "It can warm food.", "Het kan eten verwarmen."),
    79: ObjectInfo("oven", "oven", "appliance", "It can bake food.", "Je kunt er eten in bakken."),
    80: ObjectInfo("toaster", "broodrooster", "appliance", "It can toast bread.", "Het kan brood roosteren."),
    81: ObjectInfo("sink", "gootsteen", "fixture", "You can wash things in it.", "Je kunt er dingen in wassen."),
    82: ObjectInfo("refrigerator", "koelkast", "appliance", "It keeps food cold.", "Het houdt eten koud.", ("fridge",), ()),
    85: ObjectInfo("clock", "klok", "household object", "It shows the time.", "Het geeft de tijd aan."),
    86: ObjectInfo("vase", "vaas", "decoration", "Flowers can go in it.", "Er kunnen bloemen in."),
    88: ObjectInfo("teddy bear", "knuffelbeer", "toy", "It is soft and cuddly.", "Hij is zacht om te knuffelen.", ("teddy",), ("beer", "knuffel")),
}


@dataclass(frozen=True)
class Detection:
    label: int
    score: float
    bbox: tuple[float, float, float, float]
    frame_index: int


class LocalProvider:
    _detector: ort.InferenceSession | None = None
    _detector_lock = threading.Lock()
    _tts: ClassVar[dict[str, sherpa_onnx.OfflineTts]] = {}
    _tts_lock = threading.Lock()

    def __init__(self, cancelled: threading.Event) -> None:
        if not local_assets_status()["ready"]:
            raise RuntimeError("Local provider assets are not installed")
        self._cancelled = cancelled

    def _check_current(self) -> None:
        if self._cancelled.is_set():
            raise RuntimeError("Local provider session was cancelled")

    @classmethod
    def _detector_session(cls) -> ort.InferenceSession:
        with cls._detector_lock:
            if cls._detector is None:
                options = ort.SessionOptions()
                options.intra_op_num_threads = 2
                options.inter_op_num_threads = 1
                options.log_severity_level = 3
                cls._detector = ort.InferenceSession(
                    str(detector_path()),
                    sess_options=options,
                    providers=["CPUExecutionProvider"],
                )
            return cls._detector

    @staticmethod
    def _decode(frame_jpeg: bytes) -> Image.Image:
        if not frame_jpeg or len(frame_jpeg) > 1_500_000:
            raise RuntimeError("Camera frame bounds were not met")
        try:
            image = Image.open(io.BytesIO(frame_jpeg))
            image.load()
            return image.convert("RGB")
        except Exception as exc:
            raise RuntimeError("Camera frame was not a valid image") from exc

    def _detect(self, frame_jpeg: bytes, frame_index: int) -> tuple[Image.Image, list[Detection]]:
        self._check_current()
        image = self._decode(frame_jpeg)
        resized = image.resize((320, 320), Image.Resampling.BILINEAR)
        array = np.asarray(resized, dtype=np.float32).transpose(2, 0, 1)[None, ...] / 255.0
        outputs = self._detector_session().run(None, {"images": array})
        boxes, labels, scores = (np.asarray(output) for output in outputs)
        self._check_current()
        detections: list[Detection] = []
        for box, label_raw, score_raw in zip(boxes, labels, scores, strict=True):
            label = int(label_raw)
            score = float(score_raw)
            if score < 0.78:
                break
            if label not in OBJECTS:
                continue
            x1, y1, x2, y2 = (float(value) / 320.0 for value in box)
            width, height = x2 - x1, y2 - y1
            if min(x1, y1, width, height) < 0 or x2 > 1 or y2 > 1:
                continue
            area = width * height
            if area < 0.025 or area > 0.65 or min(width, height) < 0.12:
                continue
            detections.append(Detection(label, score, (x1, y1, width, height), frame_index))
        return image, detections

    @staticmethod
    def _colour(image: Image.Image, bbox: tuple[float, float, float, float]) -> str:
        x, y, width, height = bbox
        # Use the central 64% of the box to reduce background contamination.
        x += width * 0.1
        y += height * 0.1
        width *= 0.8
        height *= 0.8
        left, top = int(x * image.width), int(y * image.height)
        right, bottom = int((x + width) * image.width), int((y + height) * image.height)
        crop = np.asarray(image.crop((left, top, right, bottom)).resize((48, 48)), dtype=np.float32) / 255.0
        if not crop.size:
            raise RuntimeError("Object colour could not be measured")
        maximum = crop.max(axis=2)
        minimum = crop.min(axis=2)
        delta = maximum - minimum
        saturation = np.divide(delta, maximum, out=np.zeros_like(delta), where=maximum > 0)
        hue = np.zeros_like(maximum)
        mask = delta > 1e-6
        red, green, blue = crop[..., 0], crop[..., 1], crop[..., 2]
        red_max = mask & (maximum == red)
        green_max = mask & (maximum == green)
        blue_max = mask & (maximum == blue)
        hue[red_max] = ((green[red_max] - blue[red_max]) / delta[red_max]) % 6
        hue[green_max] = (blue[green_max] - red[green_max]) / delta[green_max] + 2
        hue[blue_max] = (red[blue_max] - green[blue_max]) / delta[blue_max] + 4
        hue *= 60

        names = np.full(maximum.shape, "grey", dtype="<U8")
        names[maximum < 0.18] = "black"
        names[(saturation < 0.16) & (maximum > 0.82)] = "white"
        chromatic = saturation >= 0.16
        names[chromatic & ((hue < 15) | (hue >= 345))] = "red"
        names[chromatic & (hue >= 15) & (hue < 45)] = "orange"
        names[chromatic & (hue >= 45) & (hue < 70)] = "yellow"
        names[chromatic & (hue >= 70) & (hue < 165)] = "green"
        names[chromatic & (hue >= 165) & (hue < 255)] = "blue"
        names[chromatic & (hue >= 255) & (hue < 290)] = "purple"
        names[chromatic & (hue >= 290) & (hue < 345)] = "pink"
        names[chromatic & (hue >= 15) & (hue < 45) & (maximum < 0.62)] = "brown"
        values, counts = np.unique(names, return_counts=True)
        index = int(np.argmax(counts))
        if counts[index] / names.size < 0.34:
            raise RuntimeError("Object colour was unclear")
        return str(values[index])

    @staticmethod
    def _location(bbox: tuple[float, float, float, float], language: Language) -> str:
        x, _y, width, _height = bbox
        horizontal = "left" if x + width / 2 < 0.4 else "right" if x + width / 2 > 0.6 else "centre"
        if language == "nl":
            return {"left": "aan de linkerkant van de kamer", "right": "aan de rechterkant van de kamer", "centre": "in het midden van de kamer"}[horizontal]
        return {"left": "on the left side of the room", "right": "on the right side of the room", "centre": "in the middle of the room"}[horizontal]

    def moderate(self, text: str) -> None:
        text = " ".join(text.split())
        if not text or len(text) > 500 or any(ord(character) < 32 for character in text):
            raise RuntimeError("Text is outside accepted bounds")
        words = set(re.findall(r"[\wÀ-ÿ]+", text.casefold()))
        if words & DISALLOWED_TERMS:
            raise RuntimeError("Content was not approved by local policy")

    def select_target(self, frames_jpeg: list[bytes], *, language: Language, age_band: AgeBand) -> Target:
        del age_band
        if not 1 <= len(frames_jpeg) <= 3:
            raise RuntimeError("Camera frame bounds were not met")
        images: list[Image.Image] = []
        by_label: dict[int, list[Detection]] = {}
        ambiguous: set[int] = set()
        for frame_index, frame in enumerate(frames_jpeg):
            image, detections = self._detect(frame, frame_index)
            images.append(image)
            per_frame: dict[int, list[Detection]] = {}
            for detection in detections:
                per_frame.setdefault(detection.label, []).append(detection)
            for label, matches in per_frame.items():
                if len(matches) != 1:
                    ambiguous.add(label)
                else:
                    by_label.setdefault(label, []).append(matches[0])
        candidates = [
            (label, matches)
            for label, matches in by_label.items()
            if label not in ambiguous and len(matches) >= min(2, len(frames_jpeg))
        ]
        if not candidates:
            raise RuntimeError("No stable child-safe local target was found")
        label, matches = max(candidates, key=lambda item: (len(item[1]), sum(match.score for match in item[1])))
        selected = max(matches, key=lambda match: match.score)
        info = OBJECTS[label]
        colour = self._colour(images[selected.frame_index], selected.bbox)
        location_en = self._location(selected.bbox, "en")
        payload: dict[str, object] = {
            "object_name": info.en,
            "colour": colour,
            "category": info.category,
            "location": location_en,
            "frame_index": selected.frame_index,
            "bbox": list(selected.bbox),
            "confidence": selected.score,
            "stable": True,
            "visible_frame_count": len(matches),
            "hints_en": [info.hint_en, f"It is {location_en}."],
            "hints_nl": [info.hint_nl, f"Het is {self._location(selected.bbox, 'nl')}."],
        }
        target = validate_target(payload, frame_count=len(frames_jpeg))
        self.moderate(" ".join((target.object_name, target.category, target.location, *target.hints_en, *target.hints_nl)))
        return target

    @staticmethod
    def _label_for_target(target: Target) -> int | None:
        normalized = target.object_name.casefold()
        return next((label for label, info in OBJECTS.items() if info.en.casefold() == normalized), None)

    def target_present(self, frame_jpeg: bytes, target: Target) -> bool:
        label = self._label_for_target(target)
        if label is None:
            return False
        _, detections = self._detect(frame_jpeg, 0)
        return sum(detection.label == label for detection in detections) == 1

    def judge_guess(self, guess: str, target: Target, *, language: Language) -> bool:
        self.moderate(guess)
        label = self._label_for_target(target)
        if label is None:
            return False
        info = OBJECTS[label]
        accepted = (info.en, *info.aliases_en) if language == "en" else (info.nl, *info.aliases_nl)

        def normalize(value: str) -> str:
            return " ".join(re.findall(r"[\wÀ-ÿ]+", value.casefold()))

        normalized = normalize(guess)
        return normalized in {normalize(value) for value in accepted}

    @classmethod
    def _tts_engine(cls, language: Language) -> sherpa_onnx.OfflineTts:
        with cls._tts_lock:
            engine = cls._tts.get(language)
            if engine is not None:
                return engine
            asset = VOICE_ASSETS[language]
            root = voice_path(language)
            config = sherpa_onnx.OfflineTtsConfig(
                model=sherpa_onnx.OfflineTtsModelConfig(
                    vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                        model=str(root / asset.model),
                        tokens=str(root / "tokens.txt"),
                        data_dir=str(root / "espeak-ng-data"),
                    ),
                    num_threads=2,
                    provider="cpu",
                ),
                max_num_sentences=1,
            )
            engine = sherpa_onnx.OfflineTts(config)
            cls._tts[language] = engine
            return engine

    def synthesize_wav(self, text: str, *, language: Language) -> bytes:
        moderation_text = text
        if language == "en" and re.fullmatch(
            r"I spy with my little eye, something that is [A-Za-z]+\.", text
        ):
            # "eye" is forbidden for selected targets, but it is also part of
            # the fixed English game idiom. Only this bounded template gets an
            # exception; arbitrary body-part text still fails closed.
            moderation_text = text.replace("little eye", "little look", 1)
        self.moderate(moderation_text)
        self._check_current()
        audio = self._tts_engine(language).generate(
            text,
            sid=0,
            speed=1.0,
            callback=lambda _samples, _progress: int(self._cancelled.is_set()),
        )
        self._check_current()
        samples = np.asarray(audio.samples, dtype=np.float32)
        if not 0 < len(samples) <= int(audio.sample_rate * 30) or not bool(np.isfinite(samples).all()):
            raise RuntimeError("Local speech returned invalid audio")
        pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
        output = io.BytesIO()
        with wave.open(output, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(audio.sample_rate)
            wav_file.writeframes(pcm)
        return output.getvalue()
