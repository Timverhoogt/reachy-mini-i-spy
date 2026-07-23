"""Adversarial gate tests: stale-work rejection, revoke-wins, fail-closed.

Authored by the security-reviewer gate for t_8fd3db92. These are negative /
concurrency probes that supplement the builder's regression suite. If any of
these ever fail the physical/child-safety invariants have regressed.
"""
from __future__ import annotations

import threading

import numpy as np
import pytest

from reachy_mini_i_spy.game import GameMachine, GameState
from reachy_mini_i_spy.runtime import GameRuntime, MotionOwner
from tests.test_runtime import FakeProvider, FakeRobot, PositiveAudio, wait_for


def _running_runtime(monkeypatch, robot, *, speak=None):  # type: ignore[no-untyped-def]
    stop_event = threading.Event()
    runtime = GameRuntime(robot, stop_event)
    monkeypatch.setattr(runtime, "_provider", lambda generation: FakeProvider())
    monkeypatch.setattr(runtime, "_speak", speak or (lambda provider, text, generation: None))
    monkeypatch.setattr(runtime.motion, "_wait", lambda generation, seconds: True)
    worker = threading.Thread(target=runtime.run)
    worker.start()
    return runtime, stop_event, worker


def test_camera_disable_revoke_mid_round_folds_and_kills_camera(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """/api/camera/disable must behave exactly like a user Stop."""
    robot = FakeRobot()
    select_entered = threading.Event()
    release = threading.Event()

    class BlockingProvider(FakeProvider):
        def select_target(self, frames, **kw):  # type: ignore[no-untyped-def]
            select_entered.set()
            assert release.wait(2)
            return super().select_target(frames, **kw)

    runtime, stop_event, worker = _running_runtime(monkeypatch, robot)
    monkeypatch.setattr(runtime, "_provider", lambda generation: BlockingProvider())
    try:
        runtime.start("en", "7-9", True)
        assert select_entered.wait(2)
        # The user revokes camera consent while the vision call is in flight.
        revoked = runtime.stop("camera_disabled")
        assert revoked["camera_active"] is False
        release.set()
        wait_for(lambda: runtime.motion.status()["motor_state"] == "asleep")
        snap = runtime.snapshot()
        # No target may be selected from a revoked session, camera stays off.
        assert snap["camera_active"] is False
        assert snap["target"] is None
        assert snap["state"] in {GameState.STOPPED, GameState.ERROR}
        assert robot.motor_mode == "disabled"
    finally:
        stop_event.set()
        worker.join(timeout=2)
    assert not worker.is_alive()


def test_freshly_created_provider_after_stop_still_yields_no_stale_selection(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A provider re-created after a Stop must not produce a live selection.

    Attacks the _provider/_cancel_providers race: even if the worker builds a
    brand-new (uncancelled) provider for a generation that Stop already bumped,
    the generation guard must discard its result.
    """
    machine_stop_done = threading.Event()
    robot = FakeRobot()
    search_done = threading.Event()

    runtime, stop_event, worker = _running_runtime(monkeypatch, robot)

    real_search = runtime.motion.search

    def search_then_stop(generation):  # type: ignore[no-untyped-def]
        frames = real_search(generation)
        # Stop lands exactly between search completing and select_target.
        runtime.stop()
        machine_stop_done.set()
        search_done.set()
        return frames

    monkeypatch.setattr(runtime.motion, "search", search_then_stop)
    try:
        runtime.start("en", "7-9", True)
        assert search_done.wait(2)
        wait_for(lambda: runtime.motion.status()["motor_state"] == "asleep")
        snap = runtime.snapshot()
        assert snap["target"] is None
        assert snap["camera_active"] is False
        assert snap["state"] in {GameState.STOPPED, GameState.ERROR}
    finally:
        stop_event.set()
        worker.join(timeout=2)
    assert not worker.is_alive()


def test_stop_during_speech_playback_halts_audio_immediately(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Barge-in: a Stop during TTS playback stops audio via the generation guard."""
    import io
    import wave

    robot = FakeRobot()
    robot.media.audio = PositiveAudio()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16_000)
        wav_file.writeframes(np.zeros(16_000 * 5, dtype="<i2").tobytes())  # 5s of audio

    class SpeechProvider:
        def synthesize_wav(self, text, *, language):  # type: ignore[no-untyped-def]
            return buffer.getvalue()

    stop_playing_calls: list[int] = []
    original_stop = robot.media.stop_playing
    robot.media.stop_playing = lambda: (stop_playing_calls.append(1), original_stop())  # type: ignore[assignment]

    runtime = GameRuntime(robot, threading.Event())
    generation = runtime.machine.start(language="en", age_band="7-9", camera_consent=True)

    speak_thread = threading.Thread(
        target=runtime._speak, args=(SpeechProvider(), "hello", generation)  # type: ignore[arg-type]
    )
    speak_thread.start()
    # Let playback start, then revoke.
    wait_for(lambda: len(robot.media.pushed) == 1)
    runtime.machine.stop()
    speak_thread.join(timeout=2)
    assert not speak_thread.is_alive()
    # Playback was interrupted rather than draining the full 5 seconds.
    assert stop_playing_calls, "stop_playing was never called on barge-in"


def test_no_motion_command_ever_exceeds_conservative_bounds() -> None:
    """_goto is the only search-motion path and it hard-rejects out-of-bounds poses."""
    robot = FakeRobot()
    machine = GameMachine()
    motion = MotionOwner(robot, machine, threading.Event())
    for bad in [(40.0, 0.0, 0.0), (0.0, 45.0, 0.0), (0.0, 0.0, 20.0), (0.0, 25.0, 0.0)]:
        with pytest.raises(ValueError):
            motion._goto(*bad)
    assert robot.moves == []
