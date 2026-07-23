from __future__ import annotations

import io
import subprocess
import sys
import threading
import wave

import numpy as np
import pytest
from PIL import Image

from reachy_mini_i_spy.game import Target
from reachy_mini_i_spy.local_provider import OBJECTS, Detection, LocalProvider


def _provider() -> LocalProvider:
    provider = object.__new__(LocalProvider)
    provider._cancelled = threading.Event()
    return provider


def test_detector_provider_import_does_not_eagerly_load_sherpa() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import reachy_mini_i_spy.local_provider; "
            "assert 'sherpa_onnx' not in sys.modules",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def _chair() -> Target:
    return Target(
        object_name="chair",
        colour="red",
        category="furniture",
        location="in the middle of the room",
        frame_index=0,
        bbox=(0.2, 0.2, 0.4, 0.5),
        confidence=0.95,
        hints_en=("You can sit on it.",),
        hints_nl=("Je kunt erop zitten.",),
    )


def test_local_policy_and_guess_aliases_fail_closed() -> None:
    provider = _provider()
    provider.moderate("A safe red chair")
    with pytest.raises(RuntimeError, match="not approved"):
        provider.moderate("A private phone")
    assert provider.judge_guess("chair", _chair(), language="en") is True
    assert provider.judge_guess("stoel", _chair(), language="nl") is True
    assert provider.judge_guess("spaceship", _chair(), language="en") is False


def test_local_colour_uses_bounded_central_crop() -> None:
    image = Image.new("RGB", (100, 100), (230, 30, 25))
    assert LocalProvider._colour(image, (0.1, 0.1, 0.8, 0.8)) == "red"


def test_yolox_preprocess_letterboxes_without_aspect_distortion() -> None:
    image = Image.new("RGB", (1280, 720), (255, 0, 0))
    array, ratio = LocalProvider._preprocess(image)
    assert array.shape == (1, 3, 416, 416)
    assert ratio == pytest.approx(0.325)
    assert tuple(array[0, :, 100, 100]) == (0, 0, 255)  # RGB red converted to BGR
    assert tuple(array[0, :, 300, 100]) == (114, 114, 114)  # letterbox padding


def test_yolox_decode_and_nms_are_deterministic() -> None:
    raw = np.zeros((1, 3549, 85), dtype=np.float32)
    decoded = LocalProvider._postprocess(raw)
    assert decoded.shape == (3549, 85)
    assert tuple(decoded[0, :4]) == (0, 0, 8, 8)
    boxes = np.asarray([[0, 0, 100, 100], [5, 5, 95, 95], [200, 200, 250, 250]], dtype=np.float32)
    scores = np.asarray([0.9, 0.8, 0.7], dtype=np.float32)
    assert LocalProvider._nms(boxes, scores, 0.45) == [0, 2]


def test_local_selection_requires_safe_class_stability_across_two_views(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider()
    image = Image.new("RGB", (320, 320), (35, 180, 45))

    def detect(_frame: bytes, frame_index: int):  # type: ignore[no-untyped-def]
        detections = (
            []
            if frame_index == 2
            else [Detection(58, 0.62 + frame_index * 0.08, (0.2, 0.2, 0.4, 0.5), frame_index)]
        )
        return image, detections

    monkeypatch.setattr(provider, "_detect", detect)
    target = provider.select_target([b"a", b"b", b"c"], language="en", age_band="7-9")
    assert OBJECTS[58].en == "plant"
    assert target.object_name == "plant"
    assert target.confidence == pytest.approx(0.70)


def test_multiple_instances_count_once_per_view_and_revalidate_by_overlap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider()
    image = Image.new("RGB", (320, 320), (25, 25, 25))

    def detect(_frame: bytes, frame_index: int):  # type: ignore[no-untyped-def]
        return image, [
            Detection(56, 0.80 - frame_index * 0.05, (0.1, 0.1, 0.3, 0.6), frame_index),
            Detection(56, 0.55, (0.6, 0.2, 0.2, 0.5), frame_index),
        ]

    monkeypatch.setattr(provider, "_detect", detect)
    target = provider.select_target([b"a", b"b", b"c"], language="en", age_band="7-9")
    assert target.object_name == "chair"
    assert target.confidence == pytest.approx(0.80)
    assert provider.target_present(b"fresh", target) is True
    assert LocalProvider._box_iou((0.6, 0.2, 0.2, 0.5), target.bbox) == 0.0


def test_fixed_i_spy_phrase_can_synthesize_but_arbitrary_body_text_cannot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Audio:
        samples = np.linspace(-0.25, 0.25, 1600, dtype=np.float32)
        sample_rate = 16000

    provider = _provider()

    class Engine:
        def generate(self, *_args: object, **kwargs: object) -> Audio:
            callback = kwargs["callback"]
            assert callable(callback)
            assert callback(None, 0.0) == 1
            provider._cancelled.set()
            assert callback(None, 0.0) == 0
            provider._cancelled.clear()
            return Audio()

    monkeypatch.setattr(LocalProvider, "_tts_engine", classmethod(lambda _cls, _language: Engine()))
    wav_data = provider.synthesize_wav(
        "I spy with my little eye, something that is red.",
        language="en",
    )
    with wave.open(io.BytesIO(wav_data), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 16000
        assert wav_file.getnframes() == 1600
    with pytest.raises(RuntimeError, match="not approved"):
        provider.synthesize_wav("Look at this eye.", language="en")


def test_local_speech_observes_cancellation_before_generation() -> None:
    provider = _provider()
    provider._cancelled.set()
    with pytest.raises(RuntimeError, match="cancelled"):
        provider.synthesize_wav("A safe sentence.", language="en")
