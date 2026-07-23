from __future__ import annotations

import io
import threading
import wave

import numpy as np
import pytest
from PIL import Image

from reachy_mini_i_spy.game import Target
from reachy_mini_i_spy.local_provider import LocalProvider


def _provider() -> LocalProvider:
    provider = object.__new__(LocalProvider)
    provider._cancelled = threading.Event()
    return provider


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


def test_fixed_i_spy_phrase_can_synthesize_but_arbitrary_body_text_cannot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Audio:
        samples = np.linspace(-0.25, 0.25, 1600, dtype=np.float32)
        sample_rate = 16000

    class Engine:
        def generate(self, *_args: object, **_kwargs: object) -> Audio:
            return Audio()

    monkeypatch.setattr(LocalProvider, "_tts_engine", classmethod(lambda _cls, _language: Engine()))
    provider = _provider()
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
