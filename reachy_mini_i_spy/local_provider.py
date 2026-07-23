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


# Explicit child-safe subset of YOLOX's contiguous COCO-80 label map.
# Everything not listed fails closed.
OBJECTS: dict[int, ObjectInfo] = {
    13: ObjectInfo("bench", "bank", "furniture", "You can sit on it.", "Je kunt erop zitten."),
    29: ObjectInfo("frisbee", "frisbee", "toy", "You can throw it.", "Je kunt hem gooien."),
    32: ObjectInfo("ball", "bal", "toy", "It can roll.", "Hij kan rollen.", ("sports ball",), ("sportbal",)),
    41: ObjectInfo("cup", "beker", "tableware", "You can drink from it.", "Je kunt eruit drinken.", ("mug",), ("mok", "kopje")),
    45: ObjectInfo("bowl", "kom", "tableware", "It can hold food.", "Er kan eten in.", (), ("schaal",)),
    46: ObjectInfo("banana", "banaan", "food", "It is a fruit.", "Het is fruit."),
    47: ObjectInfo("apple", "appel", "food", "It grows on a tree.", "Het groeit aan een boom."),
    49: ObjectInfo("orange", "sinaasappel", "food", "It is a round fruit.", "Het is rond fruit."),
    50: ObjectInfo("broccoli", "broccoli", "food", "It is a vegetable.", "Het is groente."),
    51: ObjectInfo("carrot", "wortel", "food", "It is a vegetable.", "Het is groente."),
    53: ObjectInfo("pizza", "pizza", "food", "It is usually round.", "Het is meestal rond."),
    54: ObjectInfo("donut", "donut", "food", "It often has a hole.", "Er zit vaak een gat in."),
    55: ObjectInfo("cake", "taart", "food", "It is a sweet treat.", "Het is een zoete traktatie."),
    56: ObjectInfo("chair", "stoel", "furniture", "You can sit on it.", "Je kunt erop zitten."),
    57: ObjectInfo("sofa", "bank", "furniture", "More than one person can sit on it.", "Je kunt er samen op zitten.", ("couch",), ("zetel",)),
    58: ObjectInfo("plant", "plant", "decoration", "It has leaves.", "Het heeft bladeren.", ("potted plant",), ("kamerplant",)),
    60: ObjectInfo("table", "tafel", "furniture", "You can put things on it.", "Je kunt er dingen op zetten.", ("dining table",), ("eettafel",)),
    68: ObjectInfo("microwave", "magnetron", "appliance", "It can warm food.", "Het kan eten verwarmen."),
    69: ObjectInfo("oven", "oven", "appliance", "It can bake food.", "Je kunt er eten in bakken."),
    70: ObjectInfo("toaster", "broodrooster", "appliance", "It can toast bread.", "Het kan brood roosteren."),
    71: ObjectInfo("sink", "gootsteen", "fixture", "You can wash things in it.", "Je kunt er dingen in wassen."),
    72: ObjectInfo("refrigerator", "koelkast", "appliance", "It keeps food cold.", "Het houdt eten koud.", ("fridge",), ()),
    74: ObjectInfo("clock", "klok", "household object", "It shows the time.", "Het geeft de tijd aan."),
    75: ObjectInfo("vase", "vaas", "decoration", "Flowers can go in it.", "Er kunnen bloemen in."),
    77: ObjectInfo("teddy bear", "knuffelbeer", "toy", "It is soft and cuddly.", "Hij is zacht om te knuffelen.", ("teddy",), ("beer", "knuffel")),
}


@dataclass(frozen=True)
class Detection:
    label: int
    score: float
    bbox: tuple[float, float, float, float]
    frame_index: int


class LocalProvider:
    INPUT_SIZE = 416
    SCORE_THRESHOLD = 0.30
    NMS_THRESHOLD = 0.45
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

    @classmethod
    def _preprocess(cls, image: Image.Image) -> tuple[np.ndarray, float]:
        """Letterbox one RGB frame using YOLOX's official BGR/0-255 contract."""
        ratio = min(cls.INPUT_SIZE / image.height, cls.INPUT_SIZE / image.width)
        width, height = int(image.width * ratio), int(image.height * ratio)
        resized = np.asarray(image.resize((width, height), Image.Resampling.BILINEAR), dtype=np.uint8)
        padded = np.full((cls.INPUT_SIZE, cls.INPUT_SIZE, 3), 114, dtype=np.uint8)
        padded[:height, :width] = resized[..., ::-1]
        return np.ascontiguousarray(padded.transpose(2, 0, 1), dtype=np.float32)[None, ...], ratio

    @classmethod
    def _postprocess(cls, raw: np.ndarray) -> np.ndarray:
        predictions = np.asarray(raw, dtype=np.float32)
        if predictions.shape != (1, 3549, 85):
            raise RuntimeError("Detector output shape was invalid")
        grids: list[np.ndarray] = []
        expanded_strides: list[np.ndarray] = []
        for stride in (8, 16, 32):
            size = cls.INPUT_SIZE // stride
            x_values, y_values = np.meshgrid(np.arange(size), np.arange(size))
            grid = np.stack((x_values, y_values), axis=2).reshape(1, -1, 2)
            grids.append(grid)
            expanded_strides.append(np.full((*grid.shape[:2], 1), stride))
        grid = np.concatenate(grids, axis=1)
        strides = np.concatenate(expanded_strides, axis=1)
        decoded = predictions.copy()
        decoded[..., :2] = (decoded[..., :2] + grid) * strides
        decoded[..., 2:4] = np.exp(decoded[..., 2:4]) * strides
        return decoded[0]

    @staticmethod
    def _nms(boxes: np.ndarray, scores: np.ndarray, threshold: float) -> list[int]:
        if not len(boxes):
            return []
        x1, y1, x2, y2 = boxes.T
        areas = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
        order = scores.argsort()[::-1]
        keep: list[int] = []
        while order.size:
            index = int(order[0])
            keep.append(index)
            xx1 = np.maximum(x1[index], x1[order[1:]])
            yy1 = np.maximum(y1[index], y1[order[1:]])
            xx2 = np.minimum(x2[index], x2[order[1:]])
            yy2 = np.minimum(y2[index], y2[order[1:]])
            intersection = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
            union = areas[index] + areas[order[1:]] - intersection
            overlap = np.divide(intersection, union, out=np.zeros_like(intersection), where=union > 0)
            order = order[np.where(overlap <= threshold)[0] + 1]
        return keep

    def _detect(self, frame_jpeg: bytes, frame_index: int) -> tuple[Image.Image, list[Detection]]:
        self._check_current()
        image = self._decode(frame_jpeg)
        array, ratio = self._preprocess(image)
        session = self._detector_session()
        outputs = session.run(None, {session.get_inputs()[0].name: array})
        if len(outputs) != 1:
            raise RuntimeError("Detector output count was invalid")
        predictions = self._postprocess(np.asarray(outputs[0]))
        self._check_current()

        centre_boxes = predictions[:, :4]
        class_scores = predictions[:, 4:5] * predictions[:, 5:]
        boxes = np.empty_like(centre_boxes)
        boxes[:, 0] = centre_boxes[:, 0] - centre_boxes[:, 2] / 2
        boxes[:, 1] = centre_boxes[:, 1] - centre_boxes[:, 3] / 2
        boxes[:, 2] = centre_boxes[:, 0] + centre_boxes[:, 2] / 2
        boxes[:, 3] = centre_boxes[:, 1] + centre_boxes[:, 3] / 2
        boxes /= ratio

        detections: list[Detection] = []
        for label in OBJECTS:
            scores = class_scores[:, label]
            selected = np.where(scores >= self.SCORE_THRESHOLD)[0]
            for relative_index in self._nms(boxes[selected], scores[selected], self.NMS_THRESHOLD):
                index = int(selected[relative_index])
                x1, y1, x2, y2 = (float(value) for value in boxes[index])
                x1, x2 = x1 / image.width, x2 / image.width
                y1, y2 = y1 / image.height, y2 / image.height
                width, height = x2 - x1, y2 - y1
                if min(x1, y1, width, height) < 0 or x2 > 1 or y2 > 1:
                    continue
                area = width * height
                if area < 0.025 or area > 0.65 or min(width, height) < 0.12:
                    continue
                detections.append(
                    Detection(label, float(scores[index]), (x1, y1, width, height), frame_index)
                )
        detections.sort(key=lambda detection: detection.score, reverse=True)
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
        for frame_index, frame in enumerate(frames_jpeg):
            image, detections = self._detect(frame, frame_index)
            images.append(image)
            per_frame: dict[int, list[Detection]] = {}
            for detection in detections:
                per_frame.setdefault(detection.label, []).append(detection)
            for label, matches in per_frame.items():
                # Count at most one instance of a class per viewpoint. Several
                # chairs in one room must not inflate stability or disqualify
                # the class; the retained target box disambiguates the object.
                by_label.setdefault(label, []).append(max(matches, key=lambda match: match.score))
        candidates = [
            (label, matches)
            for label, matches in by_label.items()
            if len(matches) >= min(2, len(frames_jpeg))
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
        target = validate_target(payload, frame_count=len(frames_jpeg), minimum_confidence=self.SCORE_THRESHOLD)
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
        return any(
            detection.label == label and self._box_iou(detection.bbox, target.bbox) >= 0.25
            for detection in detections
        )

    @staticmethod
    def _box_iou(
        first: tuple[float, float, float, float],
        second: tuple[float, float, float, float],
    ) -> float:
        first_x, first_y, first_width, first_height = first
        second_x, second_y, second_width, second_height = second
        left = max(first_x, second_x)
        top = max(first_y, second_y)
        right = min(first_x + first_width, second_x + second_width)
        bottom = min(first_y + first_height, second_y + second_height)
        intersection = max(0.0, right - left) * max(0.0, bottom - top)
        union = first_width * first_height + second_width * second_height - intersection
        return intersection / union if union > 0 else 0.0

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
