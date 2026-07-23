from __future__ import annotations

import io
import threading
import time
import wave
from enum import StrEnum
from typing import Any

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from reachy_mini_i_spy import runtime as runtime_module
from reachy_mini_i_spy.game import GameState, validate_target
from reachy_mini_i_spy.runtime import GameRuntime, MotionOwner, PrivacySafeRuntimeError


class FakeMedia:
    def __init__(self) -> None:
        self.frames = 0
        self.pushed: list[np.ndarray] = []

    def get_frame_jpeg(self) -> bytes:
        self.frames += 1
        return b"jpeg"

    def get_output_audio_samplerate(self) -> int:
        return 16_000

    def start_playing(self) -> None:
        pass

    def stop_playing(self) -> None:
        pass

    def push_audio_sample(self, audio: np.ndarray) -> None:
        self.pushed.append(audio)


class FakeRobot:
    def __init__(self) -> None:
        self.media = FakeMedia()
        self.moves: list[tuple[int, dict[str, Any]]] = []
        self.fold_threads: list[int] = []
        self.operations: list[str] = []
        self.motor_mode = "disabled"
        self.pose = np.eye(4, dtype=np.float64)
        self.pose[2, 3] = -0.20
        self.wake_error: Exception | None = None
        self.fold_error: Exception | None = None
        self.disable_error: Exception | None = None
        self.wake_entered = threading.Event()
        self.wake_release = threading.Event()
        self.wake_release.set()

        class Client:
            def __init__(inner_self, owner: FakeRobot) -> None:
                inner_self.owner = owner

            def get_status(inner_self, **_: object) -> object:
                backend = type("BackendStatus", (), {"motor_control_mode": inner_self.owner.motor_mode})()
                return type("DaemonStatus", (), {"backend_status": backend})()

        self.client = Client(self)

    def goto_target(self, **kwargs: object) -> None:
        self.operations.append("goto_target")
        self.moves.append((threading.get_ident(), kwargs))

    def goto_sleep(self) -> None:
        self.operations.append("goto_sleep")
        self.fold_threads.append(threading.get_ident())
        if self.fold_error is not None:
            raise self.fold_error
        self.pose = MotionOwner.SLEEP_HEAD_POSE.copy()

    def wake_up(self) -> None:
        self.operations.append("wake_up")
        self.wake_entered.set()
        assert self.wake_release.wait(2)
        if self.wake_error is not None:
            raise self.wake_error
        self.pose = np.eye(4, dtype=np.float64)

    def enable_motors(self) -> None:
        self.operations.append("enable_motors")
        self.motor_mode = "enabled"

    def disable_motors(self) -> None:
        self.operations.append("disable_motors")
        if self.disable_error is not None:
            raise self.disable_error
        self.motor_mode = "disabled"

    def get_current_head_pose(self) -> np.ndarray:
        return self.pose.copy()


class PositivePipelineState(StrEnum):
    NULL = "null"
    PLAYING = "playing"


class PositiveAudio:
    """Test audio transport whose transition to silence is directly observable."""

    class Pipeline:
        def __init__(self) -> None:
            self.state = PositivePipelineState.PLAYING

        def set_state(self, state: PositivePipelineState) -> None:
            self.state = state

        def get_state(self, timeout: int) -> object:
            assert timeout >= 0
            return type("StateChange", (), {"state": self.state})()

    def __init__(self) -> None:
        self._pipeline = self.Pipeline()


class FakeProvider:
    def select_target(self, frames: list[bytes], **_: object):
        assert len(frames) == 3
        return validate_target({
            "object_name": "chair", "colour": "blue", "category": "furniture",
            "location": "near the table", "frame_index": 0, "bbox": [0.2, 0.2, 0.3, 0.4],
            "confidence": 0.9, "stable": True, "visible_frame_count": 2,
            "hints_en": ["You sit on it"],
            "hints_nl": ["Je zit erop"],
        }, frame_count=3)


def wait_for(predicate, timeout: float = 2.0) -> None:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not reached")


def test_search_motion_is_bounded_and_captures_only_three_frames(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    robot = FakeRobot()
    runtime = GameRuntime(robot, threading.Event())
    generation = runtime.machine.start(language="en", age_band="7-9", camera_consent=True)
    assert runtime.motion.wake(generation)
    monkeypatch.setattr(runtime.motion, "_wait", lambda generation, seconds: True)
    frames = runtime.motion.search(generation)
    assert frames == [b"jpeg"] * 3
    assert runtime.motion.SEARCH_SETTLE_SECONDS == 1.5
    assert runtime.motion.STOP_CLEANUP_TIMEOUT_SECONDS == 20.0
    assert len(robot.moves) == 5
    for _, move in robot.moves:
        assert abs(float(move["body_yaw"])) <= np.deg2rad(30)
        assert move["duration"] == 0.75
        assert move["method"] == "minjerk"


def test_search_retries_a_transient_missing_camera_frame(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    robot = FakeRobot()
    runtime = GameRuntime(robot, threading.Event())
    generation = runtime.machine.start(language="en", age_band="7-9", camera_consent=True)
    assert runtime.motion.wake(generation)
    responses = iter([None, b"jpeg", b"jpeg", b"jpeg"])
    monkeypatch.setattr(robot.media, "get_frame_jpeg", lambda: next(responses))
    monkeypatch.setattr(runtime.motion, "_wait", lambda generation, seconds: True)

    assert runtime.motion.search(generation) == [b"jpeg"] * 3


def test_search_waits_boundedly_for_camera_readiness(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    robot = FakeRobot()
    runtime = GameRuntime(robot, threading.Event())
    generation = runtime.machine.start(language="en", age_band="7-9", camera_consent=True)
    assert runtime.motion.wake(generation)
    ready_at = time.monotonic() + 0.1

    def warming_frame() -> bytes | None:
        return b"jpeg" if time.monotonic() >= ready_at else None

    monkeypatch.setattr(robot.media, "get_frame_jpeg", warming_frame)
    monkeypatch.setattr(runtime.motion, "FRAME_CAPTURE_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(runtime.motion, "FRAME_CAPTURE_RETRY_SECONDS", 0.02)
    assert runtime.motion._capture_frame(generation, "search") == b"jpeg"


def test_search_bounds_persistent_missing_frame_attempts(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    robot = FakeRobot()
    runtime = GameRuntime(robot, threading.Event())
    generation = runtime.machine.start(language="en", age_band="7-9", camera_consent=True)
    assert runtime.motion.wake(generation)
    attempts = 0

    def missing_frame() -> None:
        nonlocal attempts
        attempts += 1

    monkeypatch.setattr(robot.media, "get_frame_jpeg", missing_frame)
    monkeypatch.setattr(runtime.motion, "FRAME_CAPTURE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(runtime.motion, "FRAME_CAPTURE_RETRY_SECONDS", 0.002)

    started = time.monotonic()
    with pytest.raises(PrivacySafeRuntimeError, match="search_frame_unavailable"):
        runtime.motion._capture_frame(generation, "search")
    assert time.monotonic() - started < 0.1
    assert 1 < attempts < 20


def test_search_camera_readiness_wait_is_cancellable(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    robot = FakeRobot()
    runtime = GameRuntime(robot, threading.Event())
    generation = runtime.machine.start(language="en", age_band="7-9", camera_consent=True)
    assert runtime.motion.wake(generation)
    attempts = 0

    def cancel_during_warmup() -> None:
        nonlocal attempts
        attempts += 1
        runtime.machine.stop()

    monkeypatch.setattr(robot.media, "get_frame_jpeg", cancel_during_warmup)
    assert runtime.motion._capture_frame(generation, "search") is None
    assert attempts == 1


def test_search_failure_logs_only_privacy_safe_diagnostic_code(monkeypatch, caplog) -> None:  # type: ignore[no-untyped-def]
    robot = FakeRobot()
    stop_event = threading.Event()
    runtime = GameRuntime(robot, stop_event)
    monkeypatch.setattr(runtime, "_provider", lambda generation: FakeProvider())
    monkeypatch.setattr(runtime, "_speak", lambda provider, text, generation: None)
    monkeypatch.setattr(runtime.motion, "FRAME_CAPTURE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(runtime.motion, "_wait", lambda generation, seconds: True)
    monkeypatch.setattr(
        robot.media,
        "get_frame_jpeg",
        lambda: (_ for _ in ()).throw(RuntimeError("private scene and camera detail")),
    )
    worker = threading.Thread(target=runtime.run)
    worker.start()
    try:
        runtime.start("en", "7-9", True)
        wait_for(lambda: runtime.machine.state == GameState.ERROR)
        wait_for(lambda: runtime.motion.status()["motor_state"] == "asleep")
    finally:
        stop_event.set()
        worker.join(timeout=2)

    assert "search_frame_capture_failed" in caplog.text
    assert "private scene and camera detail" not in caplog.text
    assert runtime.snapshot()["camera_active"] is False
    assert robot.motor_mode == "disabled"


def test_search_checks_cancellation_between_moves(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    robot = FakeRobot()
    runtime = GameRuntime(robot, threading.Event())
    generation = runtime.machine.start(language="en", age_band="7-9", camera_consent=True)
    assert runtime.motion.wake(generation)

    def cancel(*_: object, **__: object) -> None:
        robot.moves.append((threading.get_ident(), {}))
        runtime.machine.stop()

    monkeypatch.setattr(robot, "goto_target", cancel)
    monkeypatch.setattr(runtime.motion, "_wait", lambda generation, seconds: False)
    assert runtime.motion.search(generation) == []
    assert len(robot.moves) == 1
    assert robot.media.frames == 0


def test_runtime_owns_all_motion_and_folds_on_shutdown(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    robot = FakeRobot()
    stop_event = threading.Event()
    runtime = GameRuntime(robot, stop_event)
    monkeypatch.setattr(runtime, "_provider", lambda generation: FakeProvider())
    monkeypatch.setattr(runtime, "_speak", lambda provider, text, generation: None)
    monkeypatch.setattr(runtime.motion, "_wait", lambda generation, seconds: True)
    worker = threading.Thread(target=runtime.run)
    worker.start()
    runtime.start("en", "7-9", True)
    wait_for(lambda: runtime.machine.state == GameState.GUESSING)
    target = runtime.machine.target
    runtime.stop()
    wait_for(lambda: runtime.motion.status()["motor_state"] == "asleep")
    assert runtime.machine.target is None
    assert target is not None
    stop_event.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert robot.fold_threads == [worker.ident]
    # MotionOwner remains the sole command path, while individual blocking SDK
    # calls run on revocable daemon wrappers so Stop never waits on them.
    assert len(robot.moves) == 6
    assert robot.operations.index("enable_motors") < robot.operations.index("wake_up")
    assert robot.operations.index("goto_sleep") < robot.operations.index("disable_motors")
    assert robot.motor_mode == "disabled"


def test_sdk_shutdown_revokes_blocked_provider_and_completes_safe_fold(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    robot = FakeRobot()
    stop_event = threading.Event()
    runtime = GameRuntime(robot, stop_event)
    provider_entered = threading.Event()
    provider_release = threading.Event()

    def blocked_provider() -> object:
        provider_entered.set()
        provider_release.wait()
        return FakeProvider()

    monkeypatch.setattr(
        runtime,
        "_run_round",
        lambda generation: runtime._call_if_current(generation, blocked_provider),
    )
    worker = threading.Thread(target=runtime.run)
    worker.start()
    runtime.start("en", "7-9", True)
    assert provider_entered.wait(1)

    stop_event.set()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert runtime._shutdown_complete.is_set()
    assert not runtime._shutdown_failed.is_set()
    assert runtime.snapshot()["runtime_ready"] is False
    assert runtime.snapshot()["camera_active"] is False
    assert robot.motor_mode == "disabled"
    provider_release.set()


def test_shutdown_fold_failure_is_reported_and_never_marked_complete() -> None:
    robot = FakeRobot()
    robot.motor_mode = "enabled"
    robot.fold_error = RuntimeError("private servo diagnostics")
    stop_event = threading.Event()
    runtime = GameRuntime(robot, stop_event)
    failures: list[BaseException] = []

    def run() -> None:
        try:
            runtime.run()
        except BaseException as exc:
            failures.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    stop_event.set()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert len(failures) == 1
    assert isinstance(failures[0], PrivacySafeRuntimeError)
    assert failures[0].diagnostic_code == "shutdown_fold_unconfirmed"
    assert runtime._shutdown_failed.is_set()
    assert not runtime._shutdown_complete.is_set()
    assert runtime.snapshot()["runtime_ready"] is False
    assert robot.motor_mode == "enabled"
    assert "disable_motors" not in robot.operations


def test_motion_pose_table_stays_within_app_bounds() -> None:
    assert len(MotionOwner.SEARCH_POSES) == 5
    assert all(abs(body) <= 30 and abs(yaw) <= 38 and abs(pitch) <= 10 for body, yaw, pitch in MotionOwner.SEARCH_POSES)
    assert all(abs(yaw - body) <= 20 for body, yaw, _ in MotionOwner.SEARCH_POSES)


def test_tts_wav_is_resampled_in_memory_for_reachy_output() -> None:
    robot = FakeRobot()
    robot.media.audio = PositiveAudio()
    runtime = GameRuntime(robot, threading.Event())
    generation = runtime.machine.start(language="en", age_band="7-9", camera_consent=True)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(24_000)
        wav_file.writeframes(np.zeros(240, dtype="<i2").tobytes())

    class SpeechProvider:
        def synthesize_wav(self, text: str, *, language: str) -> bytes:
            return buffer.getvalue()

    runtime._speak(SpeechProvider(), "Hello", generation)  # type: ignore[arg-type]
    assert len(robot.media.pushed) == 1
    assert robot.media.pushed[0].dtype == np.float32
    assert len(robot.media.pushed[0]) == 160


def test_start_rejects_until_runtime_worker_is_ready() -> None:
    runtime = GameRuntime(FakeRobot(), threading.Event())
    with pytest.raises(RuntimeError, match="not ready"):
        runtime.start("en", "7-9", True)
    assert runtime.snapshot()["runtime_ready"] is False
    assert runtime.snapshot()["camera_active"] is False


def test_stop_cannot_be_lost_between_start_state_and_event_publication(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    runtime = GameRuntime(FakeRobot(), threading.Event())
    runtime._ready.set()
    start_event_entered = threading.Event()
    start_event_release = threading.Event()
    stop_done = threading.Event()
    original_replace_pending = runtime._replace_pending

    def pause_start_publication(event) -> None:  # type: ignore[no-untyped-def]
        if event.kind == "start":
            start_event_entered.set()
            assert start_event_release.wait(2)
        original_replace_pending(event)

    monkeypatch.setattr(runtime, "_replace_pending", pause_start_publication)
    start_thread = threading.Thread(target=runtime.start, args=("en", "7-9", True))
    start_thread.start()
    assert start_event_entered.wait(1)
    stop_thread = threading.Thread(target=lambda: (runtime.stop(), stop_done.set()))
    stop_thread.start()
    assert not stop_done.wait(0.05)

    start_event_release.set()
    start_thread.join(timeout=2)
    stop_thread.join(timeout=2)

    assert not start_thread.is_alive()
    assert not stop_thread.is_alive()
    event = runtime._events.get_nowait()
    assert event.kind == "stop"
    assert runtime._events.empty()
    assert runtime.machine.state == GameState.STOPPED
    assert runtime.snapshot()["camera_active"] is False


def test_disabled_motor_round_wakes_and_verifies_before_search(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    robot = FakeRobot()
    stop_event = threading.Event()
    runtime = GameRuntime(robot, stop_event)
    monkeypatch.setattr(runtime, "_provider", lambda generation: FakeProvider())
    monkeypatch.setattr(runtime, "_speak", lambda provider, text, generation: None)
    monkeypatch.setattr(runtime.motion, "_wait", lambda generation, seconds: True)
    worker = threading.Thread(target=runtime.run)
    worker.start()
    try:
        runtime.start("en", "7-9", True)
        wait_for(lambda: runtime.machine.state == GameState.GUESSING)
        assert robot.operations[:3] == ["enable_motors", "wake_up", "goto_target"]
        assert runtime.snapshot()["motor_state"] == "awake"
    finally:
        runtime.stop()
        wait_for(lambda: runtime.motion.status()["motor_state"] == "asleep")
        stop_event.set()
        worker.join(timeout=2)
    assert not worker.is_alive()


def test_wake_accepts_sdk_near_init_pose_in_magic_distance() -> None:
    robot = FakeRobot()

    def wake_with_calibrated_measured_pose() -> None:
        robot.operations.append("wake_up")
        robot.pose = np.eye(4, dtype=np.float64)
        robot.pose[:3, :3] = Rotation.from_euler("y", 5, degrees=True).as_matrix()
        robot.pose[0, 3] = 0.020

    robot.wake_up = wake_with_calibrated_measured_pose  # type: ignore[method-assign]
    runtime = GameRuntime(robot, threading.Event())
    generation = runtime.machine.start(language="en", age_band="7-9", camera_consent=True)

    assert runtime.motion.wake(generation)
    assert runtime.motion.status()["motor_state"] == "awake"


def test_wake_allows_bounded_sensor_convergence_after_sdk_command(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    robot = FakeRobot()
    pose_reads = 0
    monotonic_seconds = 0.0

    def wake_before_measured_pose_converges() -> None:
        robot.operations.append("wake_up")
        robot.pose = MotionOwner.SLEEP_HEAD_POSE.copy()

    def delayed_measured_pose() -> np.ndarray:
        nonlocal pose_reads
        pose_reads += 1
        if monotonic_seconds >= 1.1:
            return MotionOwner.INIT_HEAD_POSE.copy()
        return MotionOwner.SLEEP_HEAD_POSE.copy()

    def advance_clock(seconds: float) -> None:
        nonlocal monotonic_seconds
        monotonic_seconds += seconds

    robot.wake_up = wake_before_measured_pose_converges  # type: ignore[method-assign]
    robot.get_current_head_pose = delayed_measured_pose  # type: ignore[method-assign]
    monkeypatch.setattr(runtime_module.time, "monotonic", lambda: monotonic_seconds)
    monkeypatch.setattr(runtime_module.time, "sleep", advance_clock)
    runtime = GameRuntime(robot, threading.Event())
    generation = runtime.machine.start(language="en", age_band="7-9", camera_consent=True)

    assert runtime.motion.wake(generation)
    assert 1.0 < monotonic_seconds < MotionOwner.WAKE_POSE_TIMEOUT_SECONDS
    assert pose_reads > 1
    assert runtime.motion.status()["motor_state"] == "awake"


def test_wake_rejects_folded_pose() -> None:
    robot = FakeRobot()

    def wake_without_leaving_fold() -> None:
        robot.operations.append("wake_up")
        robot.pose = MotionOwner.SLEEP_HEAD_POSE.copy()

    robot.wake_up = wake_without_leaving_fold  # type: ignore[method-assign]
    runtime = GameRuntime(robot, threading.Event())
    generation = runtime.machine.start(language="en", age_band="7-9", camera_consent=True)

    with pytest.raises(PrivacySafeRuntimeError, match="wake_pose_unconfirmed"):
        runtime.motion.wake(generation)

    assert runtime.motion.status()["motor_state"] == "error"


def test_wake_rejects_malformed_measured_pose() -> None:
    robot = FakeRobot()

    def wake_with_invalid_rotation() -> None:
        robot.operations.append("wake_up")
        robot.pose = np.eye(4, dtype=np.float64)
        robot.pose[:3, :3] *= 2.0

    robot.wake_up = wake_with_invalid_rotation  # type: ignore[method-assign]
    runtime = GameRuntime(robot, threading.Event())
    generation = runtime.machine.start(language="en", age_band="7-9", camera_consent=True)

    with pytest.raises(PrivacySafeRuntimeError, match="wake_pose_unconfirmed"):
        runtime.motion.wake(generation)


def test_wake_failure_fails_closed_without_search_or_camera(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    robot = FakeRobot()
    robot.wake_error = RuntimeError("raw hardware detail must not leak")
    stop_event = threading.Event()
    runtime = GameRuntime(robot, stop_event)
    monkeypatch.setattr(runtime, "_provider", lambda generation: FakeProvider())
    monkeypatch.setattr(runtime, "_speak", lambda provider, text, generation: None)
    worker = threading.Thread(target=runtime.run)
    worker.start()
    try:
        runtime.start("en", "7-9", True)
        wait_for(lambda: runtime.machine.state == GameState.ERROR)
        wait_for(lambda: runtime.motion.status()["motor_state"] == "asleep")
        snapshot = runtime.snapshot()
        assert snapshot["camera_active"] is False
        assert "raw hardware detail" not in str(snapshot)
        assert "goto_target" not in robot.operations
        assert robot.media.frames == 0
    finally:
        stop_event.set()
        worker.join(timeout=2)
    assert not worker.is_alive()


def test_wake_pose_timeout_fails_closed_before_every_downstream_boundary(monkeypatch, caplog) -> None:  # type: ignore[no-untyped-def]
    robot = FakeRobot()
    stop_event = threading.Event()
    runtime = GameRuntime(robot, stop_event)
    monkeypatch.setattr(runtime.motion, "WAKE_POSE_TIMEOUT_SECONDS", 0.01)
    provider_calls: list[int] = []
    tts_calls: list[str] = []
    broker_journal: list[str] = []

    def wake_without_leaving_fold() -> None:
        robot.operations.append("wake_up")
        robot.pose = MotionOwner.SLEEP_HEAD_POSE.copy()

    def provider(generation: int) -> FakeProvider:
        provider_calls.append(generation)
        broker_journal.append("provider boundary reached")
        return FakeProvider()

    robot.wake_up = wake_without_leaving_fold  # type: ignore[method-assign]
    monkeypatch.setattr(runtime, "_provider", provider)
    monkeypatch.setattr(
        runtime,
        "_speak",
        lambda provider, text, generation: tts_calls.append(text),
    )
    worker = threading.Thread(target=runtime.run)
    worker.start()
    try:
        started = runtime.start("en", "7-9", True)
        assert started["generation"] == 1
        wait_for(lambda: runtime.machine.state == GameState.ERROR)
        failed = runtime.snapshot()
        assert failed["generation"] == 2
        assert failed["camera_active"] is False
        wait_for(lambda: runtime.motion.status()["motor_state"] == "asleep")
    finally:
        stop_event.set()
        worker.join(timeout=2)

    assert "diagnostic=wake_pose_unconfirmed" in caplog.text
    assert "unclassified_runtime_failure" not in caplog.text
    assert provider_calls == []
    assert broker_journal == []
    assert tts_calls == []
    assert robot.media.frames == 0
    assert robot.media.pushed == []
    assert robot.moves == []
    assert robot.operations == ["enable_motors", "wake_up", "goto_sleep", "disable_motors"]
    assert robot.motor_mode == "disabled"
    assert runtime.motion.status()["motor_state"] == "asleep"
    assert not worker.is_alive()


def test_stop_during_wake_cancels_search_then_folds(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    robot = FakeRobot()
    robot.wake_release.clear()
    stop_event = threading.Event()
    runtime = GameRuntime(robot, stop_event)
    monkeypatch.setattr(runtime, "_provider", lambda generation: FakeProvider())
    monkeypatch.setattr(runtime, "_speak", lambda provider, text, generation: None)
    worker = threading.Thread(target=runtime.run)
    worker.start()
    try:
        runtime.start("en", "7-9", True)
        assert robot.wake_entered.wait(1)
        stopped = runtime.stop()
        assert stopped["camera_active"] is False
        robot.wake_release.set()
        wait_for(lambda: runtime.motion.status()["motor_state"] == "asleep")
        assert "goto_target" not in robot.operations
        assert robot.media.frames == 0
        assert robot.motor_mode == "disabled"
    finally:
        robot.wake_release.set()
        stop_event.set()
        worker.join(timeout=2)
    assert not worker.is_alive()


def test_stop_during_wake_side_effects_waits_for_quiescence_and_folds_before_return(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    robot = FakeRobot()
    robot.wake_release.clear()
    stop_event = threading.Event()
    runtime = GameRuntime(robot, stop_event)
    monkeypatch.setattr(runtime, "_provider", lambda generation: FakeProvider())
    monkeypatch.setattr(runtime, "_speak", lambda provider, text, generation: None)
    worker = threading.Thread(target=runtime.run)
    worker.start()
    stop_done = threading.Event()
    stopped: list[dict[str, object]] = []
    stopper = threading.Thread(target=lambda: (stopped.append(runtime.stop()), stop_done.set()))
    try:
        runtime.start("en", "7-9", True)
        assert robot.wake_entered.wait(1)
        stopper.start()
        assert not stop_done.wait(0.05)
        robot.wake_release.set()
        assert stop_done.wait(1)
        stopper.join(timeout=1)

        assert not stopper.is_alive()
        assert stopped[0]["motor_state"] == "asleep"
        assert runtime.motion.status()["motor_state"] == "asleep"
        assert robot.motor_mode == "disabled"
        assert robot.operations.index("wake_up") < robot.operations.index("goto_sleep")
        assert robot.operations.index("goto_sleep") < robot.operations.index("disable_motors")
        assert "goto_target" not in robot.operations
    finally:
        robot.wake_release.set()
        stop_event.set()
        worker.join(timeout=2)
    assert not worker.is_alive()


def test_stop_wake_barrier_timeout_is_bounded_and_fail_closed(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    robot = FakeRobot()
    robot.wake_release.clear()
    stop_event = threading.Event()
    runtime = GameRuntime(robot, stop_event)
    monkeypatch.setattr(runtime.motion, "WAKE_STOP_BARRIER_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(runtime, "_provider", lambda generation: FakeProvider())
    monkeypatch.setattr(runtime, "_speak", lambda provider, text, generation: None)
    worker = threading.Thread(target=runtime.run)
    worker.start()
    try:
        runtime.start("en", "7-9", True)
        assert robot.wake_entered.wait(1)
        before = time.monotonic()
        with pytest.raises(PrivacySafeRuntimeError, match="wake_stop_barrier_timeout"):
            runtime.stop()
        assert time.monotonic() - before < 0.25
        assert runtime.machine.state == GameState.STOPPED
        assert runtime.snapshot()["camera_active"] is False
        assert runtime.motion.status()["motor_state"] == "stopping"
    finally:
        robot.wake_release.set()
        wait_for(lambda: runtime.motion.status()["motor_state"] == "asleep")
        stop_event.set()
        worker.join(timeout=2)
    assert not worker.is_alive()
    assert robot.motor_mode == "disabled"


def test_start_cannot_replace_pending_stop_cleanup(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    robot = FakeRobot()
    stop_event = threading.Event()
    runtime = GameRuntime(robot, stop_event)
    provider_entered = threading.Event()
    provider_release = threading.Event()

    class BlockingProvider(FakeProvider):
        def select_target(self, frames: list[bytes], **kwargs: object):  # type: ignore[no-untyped-def]
            provider_entered.set()
            assert provider_release.wait(2)
            return super().select_target(frames, **kwargs)

    monkeypatch.setattr(runtime, "_provider", lambda generation: BlockingProvider())
    monkeypatch.setattr(runtime, "_speak", lambda provider, text, generation: None)
    monkeypatch.setattr(runtime.motion, "_wait", lambda generation, seconds: True)
    worker = threading.Thread(target=runtime.run)
    worker.start()
    try:
        runtime.start("en", "7-9", True)
        assert provider_entered.wait(1)
        runtime.stop()

        try:
            restarted = runtime.start("en", "7-9", True)
        except RuntimeError as exc:
            assert "cleanup is pending" in str(exc)
            restarted = None
        else:
            # Revocable provider wrappers may let the worker finish cleanup
            # before Stop returns. A restart is valid only after verified fold.
            assert runtime.motion.status()["motor_state"] == "asleep"

        provider_release.set()
        if restarted is None:
            wait_for(lambda: runtime.motion.status()["motor_state"] == "asleep")
            wait_for(lambda: runtime._stop_cycle is not None and runtime._stop_cycle.restart_safe.is_set())
            restarted = runtime.start("en", "7-9", True)
        assert restarted["state"] == GameState.SEARCHING
        wait_for(lambda: runtime.machine.state == GameState.GUESSING)
        first_fold = robot.operations.index("goto_sleep")
        assert first_fold < robot.operations.index("wake_up", first_fold + 1)
    finally:
        provider_release.set()
        runtime.stop()
        stop_event.set()
        worker.join(timeout=2)
    assert not worker.is_alive()


def test_stop_during_successful_wake_pose_read_cannot_advance(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    robot = FakeRobot()
    stop_event = threading.Event()
    runtime = GameRuntime(robot, stop_event)
    pose_read_entered = threading.Event()
    pose_read_release = threading.Event()
    provider_calls: list[int] = []
    original_pose_is = runtime.motion._pose_is

    def blocked_successful_wake_pose_read(expected: np.ndarray, max_magic_distance: float) -> bool:
        if np.array_equal(expected, MotionOwner.INIT_HEAD_POSE):
            pose_read_entered.set()
            assert pose_read_release.wait(2)
            return True
        return original_pose_is(expected, max_magic_distance)

    def provider(generation: int) -> FakeProvider:
        provider_calls.append(generation)
        return FakeProvider()

    monkeypatch.setattr(runtime.motion, "_pose_is", blocked_successful_wake_pose_read)
    monkeypatch.setattr(runtime, "_provider", provider)
    monkeypatch.setattr(runtime, "_speak", lambda provider, text, generation: None)
    worker = threading.Thread(target=runtime.run)
    worker.start()
    try:
        runtime.start("en", "7-9", True)
        assert pose_read_entered.wait(1)
        stopped = runtime.stop()
        assert stopped["camera_active"] is False
        pose_read_release.set()
        wait_for(lambda: runtime.motion.status()["motor_state"] == "asleep")
        assert provider_calls == []
        assert "goto_target" not in robot.operations
        assert robot.media.frames == 0
        assert robot.media.pushed == []
        assert robot.motor_mode == "disabled"
        assert runtime.snapshot()["camera_active"] is False
    finally:
        pose_read_release.set()
        stop_event.set()
        worker.join(timeout=2)
    assert not worker.is_alive()


def test_stop_after_final_wake_check_cannot_publish_awake(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    robot = FakeRobot()
    runtime = GameRuntime(robot, threading.Event())
    generation = runtime.machine.start(language="en", age_band="7-9", camera_consent=True)
    final_check_entered = threading.Event()
    final_check_release = threading.Event()
    original_set_state = runtime.motion._set_state

    def pause_awake_publication(state: str, message: str) -> None:
        if state == "awake":
            final_check_entered.set()
            assert final_check_release.wait(2)
        original_set_state(state, message)

    monkeypatch.setattr(runtime.motion, "_set_state", pause_awake_publication)
    monkeypatch.setattr(runtime.motion, "_wait_for_pose", lambda *args, **kwargs: True)
    result: list[bool] = []
    wake_thread = threading.Thread(target=lambda: result.append(runtime.motion.wake(generation)))
    wake_thread.start()
    assert final_check_entered.wait(1)

    stop_done = threading.Event()
    stop_thread = threading.Thread(target=lambda: (runtime.stop(), stop_done.set()))
    stop_thread.start()
    assert not stop_done.wait(0.05)
    final_check_release.set()
    stop_thread.join(timeout=2)
    wake_thread.join(timeout=2)

    assert not stop_thread.is_alive()
    assert not wake_thread.is_alive()
    assert result == [False]
    assert runtime.machine.state == GameState.STOPPED
    assert runtime.motion.status()["motor_state"] == "asleep"


def test_stop_after_round_check_cannot_create_or_retain_provider(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    robot = FakeRobot()
    runtime = GameRuntime(robot, threading.Event())
    generation = runtime.machine.start(language="en", age_band="7-9", camera_consent=True)
    provider_boundary_entered = threading.Event()
    provider_boundary_release = threading.Event()
    provider_constructions: list[int] = []
    original_provider = runtime._provider

    def paused_provider(checked_generation: int):  # type: ignore[no-untyped-def]
        provider_boundary_entered.set()
        assert provider_boundary_release.wait(2)
        return original_provider(checked_generation)

    class CancellableProvider(FakeProvider):
        def cancel(self) -> None:
            pass

    def construct_provider(*_: object, **__: object) -> CancellableProvider:
        provider_constructions.append(generation)
        return CancellableProvider()

    monkeypatch.setattr(runtime.motion, "wake", lambda checked_generation: True)
    monkeypatch.setattr(runtime, "_provider", paused_provider)
    monkeypatch.setattr(runtime_module, "load_config", lambda: object())
    monkeypatch.setattr(runtime_module, "ProviderClient", construct_provider)
    round_thread = threading.Thread(target=runtime._run_round, args=(generation,))
    round_thread.start()
    assert provider_boundary_entered.wait(1)

    runtime.stop()
    provider_boundary_release.set()
    round_thread.join(timeout=2)

    assert not round_thread.is_alive()
    assert provider_constructions == []
    assert runtime._providers == {}


def test_stop_revokes_in_process_provider() -> None:
    runtime = GameRuntime(FakeRobot(), threading.Event())
    generation = runtime.machine.start(language="en", age_band="7-9", camera_consent=True)
    local_cancelled = threading.Event()

    class TrackedProvider:
        def cancel_local(self) -> bool:
            local_cancelled.set()
            return True

    runtime._providers[generation] = TrackedProvider()  # type: ignore[assignment]

    runtime.stop()

    assert local_cancelled.is_set()
    assert runtime._providers == {}


def test_stop_after_tts_check_cannot_invoke_provider(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    robot = FakeRobot()
    runtime = GameRuntime(robot, threading.Event())
    generation = runtime.machine.start(language="en", age_band="7-9", camera_consent=True)
    tts_check_entered = threading.Event()
    tts_check_release = threading.Event()
    synth_calls: list[str] = []
    original_is_current = runtime._is_current
    first_check = True

    def pause_after_successful_check(checked_generation: int) -> bool:
        nonlocal first_check
        current = original_is_current(checked_generation)
        if first_check:
            first_check = False
            assert current
            tts_check_entered.set()
            assert tts_check_release.wait(2)
        return current

    class SpeechProvider:
        def synthesize_wav(self, text: str, *, language: str) -> bytes:
            synth_calls.append(text)
            return b""

    monkeypatch.setattr(runtime, "_is_current", pause_after_successful_check)
    speak_thread = threading.Thread(
        target=runtime._speak,
        args=(SpeechProvider(), "private words", generation),  # type: ignore[arg-type]
    )
    speak_thread.start()
    assert tts_check_entered.wait(1)

    runtime.stop()
    tts_check_release.set()
    speak_thread.join(timeout=2)

    assert not speak_thread.is_alive()
    assert synth_calls == []
    assert robot.media.pushed == []


def test_consent_revoke_after_frame_check_cannot_capture_or_return_frame(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    robot = FakeRobot()
    runtime = GameRuntime(robot, threading.Event())
    generation = runtime.machine.start(language="en", age_band="7-9", camera_consent=True)
    frame_check_entered = threading.Event()
    frame_check_release = threading.Event()
    original_is_current = runtime.motion._is_current
    first_check = True

    def pause_after_successful_check(checked_generation: int) -> bool:
        nonlocal first_check
        current = original_is_current(checked_generation)
        if first_check:
            first_check = False
            assert current
            frame_check_entered.set()
            assert frame_check_release.wait(2)
        return current

    monkeypatch.setattr(runtime.motion, "_is_current", pause_after_successful_check)
    captured: list[bytes | None] = []
    capture_thread = threading.Thread(
        target=lambda: captured.append(runtime.motion._capture_frame(generation, "search"))
    )
    capture_thread.start()
    assert frame_check_entered.wait(1)

    runtime.stop("camera_disabled")
    frame_check_release.set()
    capture_thread.join(timeout=2)

    assert not capture_thread.is_alive()
    assert captured == [None]
    assert robot.media.frames == 0


def test_stop_after_search_check_cannot_advance_camera_viewpoint(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    robot = FakeRobot()
    runtime = GameRuntime(robot, threading.Event())
    generation = runtime.machine.start(language="en", age_band="7-9", camera_consent=True)
    runtime.motion._set_state("awake", "test-only verified wake")
    search_check_entered = threading.Event()
    search_check_release = threading.Event()
    original_is_current = runtime.motion._is_current
    first_check = True

    def pause_after_successful_check(checked_generation: int) -> bool:
        nonlocal first_check
        current = original_is_current(checked_generation)
        if first_check:
            first_check = False
            assert current
            search_check_entered.set()
            assert search_check_release.wait(2)
        return current

    monkeypatch.setattr(runtime.motion, "_is_current", pause_after_successful_check)
    frames: list[list[bytes]] = []
    search_thread = threading.Thread(target=lambda: frames.append(runtime.motion.search(generation)))
    search_thread.start()
    assert search_check_entered.wait(1)

    runtime.stop()
    search_check_release.set()
    search_thread.join(timeout=2)

    assert not search_thread.is_alive()
    assert frames == [[]]
    assert robot.moves == []
    assert robot.media.frames == 0


def test_stop_during_search_allows_no_late_motion_or_camera(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    robot = FakeRobot()
    stop_event = threading.Event()
    runtime = GameRuntime(robot, stop_event)
    monkeypatch.setattr(runtime, "_provider", lambda generation: FakeProvider())
    monkeypatch.setattr(runtime, "_speak", lambda provider, text, generation: None)
    move_entered = threading.Event()
    move_release = threading.Event()
    original_goto = robot.goto_target

    def blocked_goto(**kwargs: object) -> None:
        original_goto(**kwargs)
        move_entered.set()
        assert move_release.wait(2)

    monkeypatch.setattr(robot, "goto_target", blocked_goto)
    worker = threading.Thread(target=runtime.run)
    worker.start()
    try:
        runtime.start("en", "7-9", True)
        assert move_entered.wait(1)
        before = time.monotonic()
        runtime.stop()
        assert time.monotonic() - before < 0.25
        move_release.set()
        wait_for(lambda: runtime.motion.status()["motor_state"] == "asleep")
        assert len(robot.moves) == 1
        assert robot.media.frames == 0
        assert runtime.snapshot()["camera_active"] is False
    finally:
        move_release.set()
        stop_event.set()
        worker.join(timeout=2)
    assert not worker.is_alive()


def test_app_shutdown_during_search_cancels_before_frame_and_folds(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    robot = FakeRobot()
    stop_event = threading.Event()
    runtime = GameRuntime(robot, stop_event)
    monkeypatch.setattr(runtime, "_provider", lambda generation: FakeProvider())
    monkeypatch.setattr(runtime, "_speak", lambda provider, text, generation: None)
    move_entered = threading.Event()
    move_release = threading.Event()
    original_goto = robot.goto_target

    def blocked_goto(**kwargs: object) -> None:
        original_goto(**kwargs)
        move_entered.set()
        assert move_release.wait(2)

    monkeypatch.setattr(robot, "goto_target", blocked_goto)
    worker = threading.Thread(target=runtime.run)
    worker.start()
    runtime.start("en", "7-9", True)
    assert move_entered.wait(1)
    stop_event.set()
    move_release.set()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert len(robot.moves) == 1
    assert robot.media.frames == 0
    assert runtime.snapshot()["camera_active"] is False
    assert runtime.motion.status()["motor_state"] == "asleep"
    assert robot.motor_mode == "disabled"


def test_fold_failure_preserves_torque_and_reports_sanitized_error() -> None:
    robot = FakeRobot()
    robot.motor_mode = "enabled"
    robot.pose = MotionOwner.INIT_HEAD_POSE.copy()
    robot.fold_error = RuntimeError("private servo diagnostics")
    runtime = GameRuntime(robot, threading.Event())

    with pytest.raises(RuntimeError, match="private servo diagnostics"):
        runtime.motion.fold_and_disable()

    assert robot.motor_mode == "enabled"
    assert "disable_motors" not in robot.operations
    status = runtime.motion.status()
    assert status["motor_state"] == "error"
    assert "private servo diagnostics" not in str(status)


def test_disable_failure_is_truthful_and_only_attempted_after_verified_fold() -> None:
    robot = FakeRobot()
    robot.motor_mode = "enabled"
    robot.disable_error = RuntimeError("daemon detail")
    runtime = GameRuntime(robot, threading.Event())

    with pytest.raises(RuntimeError, match="daemon detail"):
        runtime.motion.fold_and_disable()

    assert np.allclose(robot.pose, MotionOwner.SLEEP_HEAD_POSE)
    assert robot.operations == ["goto_sleep", "disable_motors"]
    assert runtime.motion.status() == {
        "motor_state": "error",
        "motor_message": "Reachy is folded, but motor shutdown was not confirmed.",
    }


def test_stop_is_prompt_while_provider_call_is_blocked_and_discards_late_result() -> None:
    robot = FakeRobot()
    runtime = GameRuntime(robot, threading.Event())
    generation = runtime.machine.start(language="en", age_band="7-9", camera_consent=True)
    entered = threading.Event()
    release = threading.Event()
    accepted: list[object | None] = []

    def blocked_provider_result() -> object:
        entered.set()
        assert release.wait(2)
        return object()

    caller = threading.Thread(
        target=lambda: accepted.append(runtime._call_if_current(generation, blocked_provider_result))
    )
    caller.start()
    assert entered.wait(1)
    before = time.monotonic()
    stopped = runtime.stop()
    latency = time.monotonic() - before
    assert latency < 0.25
    assert stopped["camera_active"] is False
    caller.join(timeout=0.1)
    assert not caller.is_alive(), "the local operation wrapper did not complete as cancelled"
    assert accepted == [None]
    assert runtime._active_leases == {}
    release.set()
    caller.join(timeout=2)
    assert not caller.is_alive()
    assert accepted == [None]


def test_stop_synchronously_silences_active_tts_before_return() -> None:
    robot = FakeRobot()
    active = threading.Event()
    robot.media.audio = PositiveAudio()
    original_start = robot.media.start_playing
    original_stop = robot.media.stop_playing
    robot.media.start_playing = lambda: (original_start(), active.set())  # type: ignore[method-assign]
    robot.media.stop_playing = lambda: (original_stop(), active.clear())  # type: ignore[method-assign]
    runtime = GameRuntime(robot, threading.Event())
    generation = runtime.machine.start(language="en", age_band="7-9", camera_consent=True)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16_000)
        wav_file.writeframes(np.zeros(16_000, dtype="<i2").tobytes())

    class SpeechProvider:
        def synthesize_wav(self, text: str, *, language: str) -> bytes:
            return buffer.getvalue()

    speaker = threading.Thread(
        target=runtime._speak,
        args=(SpeechProvider(), "hello", generation),  # type: ignore[arg-type]
    )
    speaker.start()
    assert active.wait(1)
    runtime.stop()
    assert not active.is_set()
    speaker.join(timeout=2)
    assert not speaker.is_alive()


def test_consent_revoke_is_prompt_during_blocked_capture_and_discards_late_jpeg() -> None:
    robot = FakeRobot()
    runtime = GameRuntime(robot, threading.Event())
    generation = runtime.machine.start(language="en", age_band="7-9", camera_consent=True)
    entered = threading.Event()
    release = threading.Event()
    captured: list[bytes | None] = []

    def blocked_capture() -> bytes:
        entered.set()
        assert release.wait(2)
        return b"private-jpeg"

    robot.media.get_frame_jpeg = blocked_capture  # type: ignore[method-assign]
    caller = threading.Thread(
        target=lambda: captured.append(runtime.motion._capture_frame(generation, "search"))
    )
    caller.start()
    assert entered.wait(1)
    before = time.monotonic()
    runtime.stop("camera_disabled")
    assert time.monotonic() - before < 0.25
    caller.join(timeout=0.1)
    assert not caller.is_alive(), "the local capture wrapper did not complete as cancelled"
    assert captured == [None]
    assert runtime._active_leases == {}
    release.set()
    caller.join(timeout=2)
    assert not caller.is_alive()
    assert captured == [None]


def test_stop_between_wake_unlock_and_return_revokes_pending_success(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    robot = FakeRobot()
    runtime = GameRuntime(robot, threading.Event())
    generation = runtime.machine.start(language="en", age_band="7-9", camera_consent=True)
    completion_entered = threading.Event()
    completion_release = threading.Event()
    checks = 0
    original = runtime.motion._lease_is_current

    def pause_post_unlock_completion(lease) -> bool:  # type: ignore[no-untyped-def]
        nonlocal checks
        checks += 1
        current = original(lease)
        if checks == 2:
            completion_entered.set()
            assert completion_release.wait(2)
        return current

    monkeypatch.setattr(runtime.motion, "_lease_is_current", pause_post_unlock_completion)
    monkeypatch.setattr(runtime.motion, "_wait_for_pose", lambda *args, **kwargs: True)
    returned: list[bool] = []
    caller = threading.Thread(target=lambda: returned.append(runtime.motion.wake(generation)))
    caller.start()
    assert completion_entered.wait(1)
    stopped: list[dict[str, object]] = []
    stop_done = threading.Event()
    stopper = threading.Thread(target=lambda: (stopped.append(runtime.stop()), stop_done.set()))
    stopper.start()
    assert not stop_done.wait(0.05)
    completion_release.set()
    stopper.join(timeout=2)
    caller.join(timeout=2)
    assert not stopper.is_alive()
    assert not caller.is_alive()
    assert stopped[0]["motor_state"] == "asleep"
    assert returned == [False]
    assert runtime.motion.status()["motor_state"] != "awake"


def test_revoke_between_frame_unlock_and_return_discards_pending_bytes(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    robot = FakeRobot()
    runtime = GameRuntime(robot, threading.Event())
    generation = runtime.machine.start(language="en", age_band="7-9", camera_consent=True)
    completion_entered = threading.Event()
    completion_release = threading.Event()
    checks = 0
    original = runtime.motion._lease_is_current

    def pause_post_unlock_completion(lease) -> bool:  # type: ignore[no-untyped-def]
        nonlocal checks
        checks += 1
        if checks == 2:
            completion_entered.set()
            assert completion_release.wait(2)
        return original(lease)

    monkeypatch.setattr(runtime.motion, "_lease_is_current", pause_post_unlock_completion)
    returned: list[bytes | None] = []
    caller = threading.Thread(
        target=lambda: returned.append(runtime.motion._capture_frame(generation, "search"))
    )
    caller.start()
    assert completion_entered.wait(1)
    stop_done = threading.Event()
    stopper = threading.Thread(target=lambda: (runtime.stop("camera_disabled"), stop_done.set()))
    stopper.start()
    assert not stop_done.wait(0.05)
    completion_release.set()
    caller.join(timeout=2)
    stopper.join(timeout=2)
    assert not caller.is_alive()
    assert not stopper.is_alive()
    assert returned == [None]


def test_stop_is_idempotent_while_provider_never_returns() -> None:
    robot = FakeRobot()
    runtime = GameRuntime(robot, threading.Event())
    generation = runtime.machine.start(language="en", age_band="7-9", camera_consent=True)
    entered = threading.Event()
    never_returns = threading.Event()
    returned: list[object | None] = []

    def blocked_forever() -> object:
        entered.set()
        never_returns.wait()
        return object()

    caller = threading.Thread(
        target=lambda: returned.append(runtime._call_if_current(generation, blocked_forever))
    )
    caller.start()
    assert entered.wait(1)

    for _ in range(2):
        before = time.monotonic()
        stopped = runtime.stop()
        assert time.monotonic() - before < 0.25
        assert stopped["camera_active"] is False

    caller.join(timeout=0.1)
    assert not caller.is_alive()
    assert returned == [None]
    assert runtime._active_leases == {}


def test_late_provider_exception_after_stop_is_discarded() -> None:
    runtime = GameRuntime(FakeRobot(), threading.Event())
    generation = runtime.machine.start(language="en", age_band="7-9", camera_consent=True)
    entered = threading.Event()
    release = threading.Event()
    outcome: list[object | None] = []

    def late_failure() -> object:
        entered.set()
        assert release.wait(2)
        raise RuntimeError("private provider failure")

    caller = threading.Thread(
        target=lambda: outcome.append(runtime._call_if_current(generation, late_failure))
    )
    caller.start()
    assert entered.wait(1)
    runtime.stop()
    caller.join(timeout=0.1)
    assert not caller.is_alive()
    assert outcome == [None]
    assert runtime._active_leases == {}
    release.set()


def test_runtime_nonce_makes_provider_session_unique_across_process_restarts(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    session_ids: list[str] = []

    class SessionProvider:
        def __init__(self, _config: object, *, session_id: str) -> None:
            session_ids.append(session_id)

        def cancel_local(self) -> bool:
            return True

    monkeypatch.setattr(runtime_module, "load_config", lambda: object())
    monkeypatch.setattr(runtime_module, "ProviderClient", SessionProvider)
    for _ in range(2):
        runtime = GameRuntime(FakeRobot(), threading.Event())
        generation = runtime.machine.start(language="en", age_band="7-9", camera_consent=True)
        assert runtime._provider(generation) is not None
    assert len(session_ids) == 2
    assert session_ids[0] != session_ids[1]
    assert all(len(value) == 32 for value in session_ids)


def test_stop_waits_for_explicit_completion_after_lease_release(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    runtime = GameRuntime(FakeRobot(), threading.Event())
    generation = runtime.machine.start(language="en", age_band="7-9", camera_consent=True)
    completion_entered = threading.Event()
    completion_release = threading.Event()
    original_complete = runtime._complete_lease

    def paused_completion(lease) -> None:  # type: ignore[no-untyped-def]
        completion_entered.set()
        assert completion_release.wait(2)
        original_complete(lease)

    monkeypatch.setattr(runtime, "_complete_lease", paused_completion)
    caller = threading.Thread(target=runtime._call_if_current, args=(generation, lambda: object()))
    caller.start()
    assert completion_entered.wait(1)
    stop_done = threading.Event()
    stopper = threading.Thread(target=lambda: (runtime.stop(), stop_done.set()))
    stopper.start()
    assert not stop_done.wait(0.05)
    completion_release.set()
    caller.join(timeout=2)
    stopper.join(timeout=2)
    assert not caller.is_alive()
    assert not stopper.is_alive()


@pytest.mark.parametrize("boundary", ["jpeg", "provider_result", "provider_object", "wake", "motion"])
def test_stop_cannot_complete_before_true_public_return_boundary(monkeypatch, boundary: str) -> None:  # type: ignore[no-untyped-def]
    robot = FakeRobot()
    runtime = GameRuntime(robot, threading.Event())
    generation = runtime.machine.start(language="en", age_band="7-9", camera_consent=True)
    completion_acknowledged = threading.Event()
    allow_public_return = threading.Event()
    original_complete = runtime._complete_lease

    def pause_after_real_completion_acknowledgement(lease) -> None:  # type: ignore[no-untyped-def]
        original_complete(lease)
        completion_acknowledged.set()
        assert allow_public_return.wait(2)

    monkeypatch.setattr(runtime, "_complete_lease", pause_after_real_completion_acknowledgement)
    runtime.motion._complete_runtime_lease = pause_after_real_completion_acknowledgement
    sentinel = object()

    class CachedProvider:
        def cancel_local(self) -> bool:
            return False

    runtime._providers[generation] = CachedProvider()  # type: ignore[assignment]
    monkeypatch.setattr(runtime.motion, "_wait_for_pose", lambda *args, **kwargs: True)

    def invoke() -> object:
        if boundary == "jpeg":
            return runtime.motion._capture_frame(generation, "search")
        if boundary == "provider_result":
            return runtime._call_if_current(generation, lambda: sentinel)
        if boundary == "provider_object":
            return runtime._provider(generation)
        if boundary == "wake":
            return runtime.motion.wake(generation)
        return runtime.motion.neutral(generation)

    returned: list[object] = []
    caller = threading.Thread(target=lambda: returned.append(invoke()))
    caller.start()
    assert completion_acknowledged.wait(1)
    stop_done = threading.Event()
    stop_outcome: list[dict[str, object]] = []
    stopper = threading.Thread(target=lambda: (stop_outcome.append(runtime.stop()), stop_done.set()))
    stopper.start()

    assert not stop_done.wait(0.05), "Stop acknowledged before the public operation returned"
    allow_public_return.set()
    caller.join(timeout=2)
    stopper.join(timeout=2)
    assert not caller.is_alive()
    assert not stopper.is_alive()
    assert len(returned) == 1
    assert stop_outcome[0]["state"] == GameState.STOPPED


@pytest.mark.parametrize("failure", ["blocked", "throws"])
@pytest.mark.parametrize("pipeline_attribute", ["_pipeline", "_pipeline_record"])
def test_stop_uses_backend_owned_failsafe_and_returns_only_after_playback_is_quiescent(
    failure: str,
    pipeline_attribute: str,
    monkeypatch,
) -> None:
    robot = FakeRobot()
    runtime = GameRuntime(robot, threading.Event())
    runtime._ready.set()
    media_entered = threading.Event()
    media_release = threading.Event()
    playback_active = threading.Event()
    playback_active.set()

    class PipelineState(StrEnum):
        NULL = "null"
        PLAYING = "playing"

    class Pipeline:
        def __init__(self) -> None:
            self.state = PipelineState.PLAYING

        def set_state(self, state: str) -> None:
            self.state = state
            if state == PipelineState.NULL:
                playback_active.clear()

        def get_state(self, timeout: int) -> object:
            assert timeout >= 0
            return type("StateChange", (), {"state": self.state})()

    robot.media.audio = type("Audio", (), {pipeline_attribute: Pipeline()})()
    if pipeline_attribute == "_pipeline_record":
        monkeypatch.setattr(runtime, "_clear_webrtc_incoming_audio", lambda audio: True)

    def blocked_stop_playing() -> None:
        media_entered.set()
        media_release.wait()
        playback_active.clear()

    if failure == "blocked":
        robot.media.stop_playing = blocked_stop_playing  # type: ignore[method-assign]
    else:
        robot.media.stop_playing = lambda: (_ for _ in ()).throw(RuntimeError("backend detail"))  # type: ignore[method-assign]
    before = time.monotonic()
    try:
        stopped = runtime.stop()
        assert stopped["state"] == GameState.STOPPED
        assert not playback_active.is_set()
        if failure == "blocked":
            with pytest.raises(RuntimeError, match="cleanup is pending"):
                runtime.start("en", "7-9", True)
    finally:
        media_release.set()
    wait_for(lambda: not runtime._media_callbacks_pending.is_set())
    wait_for(lambda: runtime._stop_cycle is not None and runtime._stop_cycle.restart_safe.is_set())
    if pipeline_attribute == "_pipeline_record":
        with pytest.raises(RuntimeError, match="cleanup is pending"):
            runtime.start("en", "7-9", True)
    else:
        assert runtime.start("en", "7-9", True)["state"] == GameState.SEARCHING
    assert failure == "throws" or media_entered.is_set()
    assert time.monotonic() - before < 0.25


@pytest.mark.parametrize("failure", ["blocked", "throws"])
def test_speech_is_refused_before_launch_when_only_unacknowledged_public_clear_exists(failure: str) -> None:
    robot = FakeRobot()
    runtime = GameRuntime(robot, threading.Event())
    generation = runtime.machine.start(language="en", age_band="7-9", camera_consent=True)
    playback_active = threading.Event()
    clear_calls: list[int] = []

    class Audio:
        def clear_player(self) -> None:
            clear_calls.append(1)
            # Reachy SDK 1.9 swallows a failed daemon flush and returns None.
            # Accepted output would therefore remain active.

    robot.media.audio = Audio()
    original_push = robot.media.push_audio_sample
    robot.media.push_audio_sample = lambda audio: (original_push(audio), playback_active.set())  # type: ignore[method-assign]
    stop_calls: list[int] = []

    def blocked_stop_playing() -> None:
        stop_calls.append(1)
        threading.Event().wait(2)

    if failure == "blocked":
        robot.media.stop_playing = blocked_stop_playing  # type: ignore[method-assign]
    else:
        robot.media.stop_playing = lambda: (_ for _ in ()).throw(RuntimeError("backend detail"))  # type: ignore[method-assign]

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16_000)
        wav_file.writeframes(np.zeros(160, dtype="<i2").tobytes())

    class SpeechProvider:
        def synthesize_wav(self, text: str, *, language: str) -> bytes:
            return buffer.getvalue()

    with pytest.raises(PrivacySafeRuntimeError, match="media_quiescence_unavailable"):
        runtime._speak(SpeechProvider(), "Hello", generation)  # type: ignore[arg-type]
    assert robot.media.pushed == []
    assert not playback_active.is_set()
    assert clear_calls == []
    assert stop_calls == []


def test_speech_is_rejected_before_start_when_backend_has_no_positive_quiescence_path() -> None:
    robot = FakeRobot()
    robot.media.audio = object()
    runtime = GameRuntime(robot, threading.Event())
    generation = runtime.machine.start(language="en", age_band="7-9", camera_consent=True)
    started = threading.Event()
    robot.media.start_playing = started.set  # type: ignore[method-assign]

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16_000)
        wav_file.writeframes(np.zeros(160, dtype="<i2").tobytes())

    class SpeechProvider:
        def synthesize_wav(self, text: str, *, language: str) -> bytes:
            return buffer.getvalue()

    with pytest.raises(PrivacySafeRuntimeError, match="media_quiescence_unavailable"):
        runtime._speak(SpeechProvider(), "Hello", generation)  # type: ignore[arg-type]
    assert not started.is_set()


def test_stop_retains_admitted_pipeline_when_backend_reference_disappears() -> None:
    robot = FakeRobot()
    runtime = GameRuntime(robot, threading.Event())
    generation = runtime.machine.start(language="en", age_band="7-9", camera_consent=True)
    playback_active = threading.Event()

    class PipelineState(StrEnum):
        NULL = "null"
        PLAYING = "playing"

    class Pipeline:
        def __init__(self) -> None:
            self.state = PipelineState.PLAYING

        def set_state(self, state: PipelineState) -> None:
            self.state = state
            if state == PipelineState.NULL:
                playback_active.clear()

        def get_state(self, timeout: int) -> object:
            assert timeout >= 0
            return type("StateChange", (), {"state": self.state})()

    audio = type("Audio", (), {})()
    audio._pipeline = Pipeline()
    robot.media.audio = audio
    original_push = robot.media.push_audio_sample
    robot.media.push_audio_sample = lambda samples: (original_push(samples), playback_active.set())  # type: ignore[method-assign]

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16_000)
        wav_file.writeframes(np.zeros(160, dtype="<i2").tobytes())

    class SpeechProvider:
        def synthesize_wav(self, text: str, *, language: str) -> bytes:
            return buffer.getvalue()

    runtime._speak(SpeechProvider(), "Hello", generation)  # type: ignore[arg-type]
    assert playback_active.is_set()
    del audio._pipeline

    stopped = runtime.stop()

    assert stopped["state"] == GameState.STOPPED
    assert not playback_active.is_set()


def test_revocation_after_start_before_publication_does_not_reenter_audio_lock() -> None:
    robot = FakeRobot()
    runtime = GameRuntime(robot, threading.Event())
    generation = runtime.machine.start(language="en", age_band="7-9", camera_consent=True)
    playback_active = threading.Event()
    playback_started = threading.Event()
    speaker_ident: list[int] = []
    original_is_current = runtime._lease_is_current

    class Pipeline:
        def __init__(self) -> None:
            self.state = PositivePipelineState.NULL

        def set_state(self, state: PositivePipelineState) -> None:
            self.state = state
            if state == PositivePipelineState.NULL:
                playback_active.clear()

        def get_state(self, timeout: int) -> object:
            assert timeout >= 0
            return type("StateChange", (), {"state": self.state})()

    pipeline = Pipeline()
    robot.media.audio = type("Audio", (), {"_pipeline": pipeline})()

    def start_playing() -> None:
        pipeline.state = PositivePipelineState.PLAYING
        playback_active.set()
        playback_started.set()

    robot.media.start_playing = start_playing  # type: ignore[method-assign]

    def revoke_at_locked_publication(lease) -> bool:  # type: ignore[no-untyped-def]
        current = original_is_current(lease)
        if (
            current
            and speaker_ident == [threading.get_ident()]
            and runtime._audio_lock.locked()
            and playback_active.is_set()
        ):
            runtime._revocation_requested.set()
            if lease.revoked is not None:
                lease.revoked.set()
            return False
        return current

    runtime._lease_is_current = revoke_at_locked_publication  # type: ignore[method-assign]
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16_000)
        wav_file.writeframes(np.zeros(160, dtype="<i2").tobytes())

    class SpeechProvider:
        def synthesize_wav(self, text: str, *, language: str) -> bytes:
            return buffer.getvalue()

    def speak() -> None:
        speaker_ident.append(threading.get_ident())
        runtime._speak(SpeechProvider(), "Hello", generation)  # type: ignore[arg-type]
        speech_done.set()

    speech_done = threading.Event()
    speaker = threading.Thread(
        target=speak,
        daemon=True,
    )
    speaker.start()
    wait_for(playback_started.is_set)
    stop_done = threading.Event()
    stopper = threading.Thread(target=lambda: (runtime.stop(), stop_done.set()), daemon=True)
    stopper.start()

    assert speech_done.wait(1), "revoked speech deadlocked while recursively acquiring _audio_lock"
    assert stop_done.wait(1), "user Stop could not join the revoked playback cleanup"
    assert not playback_active.is_set()


def test_start_playing_replacement_binds_silence_to_actual_transport() -> None:
    robot = FakeRobot()
    runtime = GameRuntime(robot, threading.Event())
    generation = runtime.machine.start(language="en", age_band="7-9", camera_consent=True)
    playback_active = threading.Event()

    class Pipeline(PositiveAudio.Pipeline):
        def set_state(self, state: PositivePipelineState) -> None:
            super().set_state(state)
            if state == PositivePipelineState.NULL:
                playback_active.clear()

    stale_pipeline = Pipeline()
    replacement_pipeline = Pipeline()
    replacement_pipeline.state = PositivePipelineState.NULL
    audio = type("Audio", (), {"_pipeline": stale_pipeline})()
    robot.media.audio = audio

    def start_playing() -> None:
        audio._pipeline = replacement_pipeline

    def push_audio_sample(samples: np.ndarray) -> None:
        robot.media.pushed.append(samples)
        replacement_pipeline.state = PositivePipelineState.PLAYING
        playback_active.set()

    robot.media.start_playing = start_playing  # type: ignore[method-assign]
    robot.media.push_audio_sample = push_audio_sample  # type: ignore[method-assign]
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16_000)
        wav_file.writeframes(np.zeros(160, dtype="<i2").tobytes())

    class SpeechProvider:
        def synthesize_wav(self, text: str, *, language: str) -> bytes:
            return buffer.getvalue()

    runtime._speak(SpeechProvider(), "Hello", generation)  # type: ignore[arg-type]
    assert playback_active.is_set()

    stopped = runtime.stop()

    assert stopped["state"] == GameState.STOPPED
    assert replacement_pipeline.state == PositivePipelineState.NULL
    assert not playback_active.is_set()


def test_blocked_replacing_start_makes_stop_return_bounded_fail_closed() -> None:
    robot = FakeRobot()
    runtime = GameRuntime(robot, threading.Event())
    generation = runtime.machine.start(language="en", age_band="7-9", camera_consent=True)
    start_entered = threading.Event()
    start_release = threading.Event()
    playback_active = threading.Event()

    class Pipeline(PositiveAudio.Pipeline):
        def set_state(self, state: PositivePipelineState) -> None:
            super().set_state(state)
            if state == PositivePipelineState.NULL:
                playback_active.clear()

    audio = type("Audio", (), {"_pipeline": Pipeline()})()
    replacement = Pipeline()
    replacement.state = PositivePipelineState.NULL
    robot.media.audio = audio

    def start_playing() -> None:
        start_entered.set()
        assert start_release.wait(2)
        audio._pipeline = replacement
        replacement.state = PositivePipelineState.PLAYING
        playback_active.set()

    robot.media.start_playing = start_playing  # type: ignore[method-assign]
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16_000)
        wav_file.writeframes(np.zeros(160, dtype="<i2").tobytes())

    class SpeechProvider:
        def synthesize_wav(self, text: str, *, language: str) -> bytes:
            return buffer.getvalue()

    speaker = threading.Thread(
        target=lambda: runtime._speak(SpeechProvider(), "Hello", generation),  # type: ignore[arg-type]
        daemon=True,
    )
    speaker.start()
    assert start_entered.wait(1)

    before = time.monotonic()
    with pytest.raises(PrivacySafeRuntimeError, match="media_start_pending"):
        runtime.stop()
    assert time.monotonic() - before < 0.5
    start_release.set()
    speaker.join(timeout=2)
    assert not speaker.is_alive()
    assert replacement.state == PositivePipelineState.NULL
    assert not playback_active.is_set()


def test_concurrent_stop_calls_join_one_media_cleanup_outcome() -> None:
    robot = FakeRobot()
    runtime = GameRuntime(robot, threading.Event())
    media_entered = threading.Event()
    media_release = threading.Event()
    calls = 0

    def blocked_stop_playing() -> None:
        nonlocal calls
        calls += 1
        media_entered.set()
        assert media_release.wait(2)

    robot.media.stop_playing = blocked_stop_playing  # type: ignore[method-assign]
    outcomes: list[dict[str, object]] = []
    first = threading.Thread(target=lambda: outcomes.append(runtime.stop()))
    second_done = threading.Event()
    second = threading.Thread(target=lambda: (outcomes.append(runtime.stop()), second_done.set()))
    first.start()
    assert media_entered.wait(1)
    second.start()
    assert not second_done.wait(0.05)
    media_release.set()
    first.join(timeout=2)
    second.join(timeout=2)
    assert not first.is_alive()
    assert not second.is_alive()
    assert calls == 1
    assert len(outcomes) == 2
    assert all(outcome["state"] == GameState.STOPPED for outcome in outcomes)


def test_start_is_excluded_until_returned_stop_cycle_is_no_longer_joinable(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    runtime = GameRuntime(FakeRobot(), threading.Event())
    runtime._ready.set()
    pending_cycles = []

    def defer_cycle_acknowledgement(cycle) -> None:  # type: ignore[no-untyped-def]
        pending_cycles.append(cycle)

    monkeypatch.setattr(runtime, "_ack_stop_after_public_return", defer_cycle_acknowledgement)

    first_stop = runtime.stop()
    assert first_stop["state"] == GameState.STOPPED
    assert len(pending_cycles) == 1
    assert not pending_cycles[0].done.is_set()

    with pytest.raises(RuntimeError, match="cleanup is pending"):
        runtime.start("en", "7-9", True)

    pending_cycles[0].done.set()
    pending_cycles[0].restart_safe.set()
    restarted = runtime.start("en", "7-9", True)
    assert restarted["state"] == GameState.SEARCHING
    assert restarted["camera_active"] is True

    second_stop = runtime.stop()
    actual = runtime.snapshot()
    assert second_stop["generation"] > restarted["generation"]
    assert second_stop["state"] == GameState.STOPPED
    assert actual["state"] == GameState.STOPPED
    assert actual["camera_active"] is False


def test_start_is_excluded_until_joined_stop_caller_has_publicly_returned(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    robot = FakeRobot()
    runtime = GameRuntime(robot, threading.Event())
    runtime._ready.set()
    media_entered = threading.Event()
    media_release = threading.Event()
    joined_acknowledgements = []

    def blocked_stop_playing() -> None:
        media_entered.set()
        assert media_release.wait(2)

    def acknowledge_leader(cycle) -> None:  # type: ignore[no-untyped-def]
        cycle.done.set()
        runtime._release_stop_public_caller(cycle)

    def defer_joined_acknowledgement(cycle, boundary) -> None:  # type: ignore[no-untyped-def]
        joined_acknowledgements.append(cycle)

    robot.media.stop_playing = blocked_stop_playing  # type: ignore[method-assign]
    monkeypatch.setattr(runtime, "_ack_stop_after_public_return", acknowledge_leader)
    monkeypatch.setattr(runtime, "_ack_stop_caller_after_public_return", defer_joined_acknowledgement)
    outcomes = []
    leader = threading.Thread(target=lambda: outcomes.append(runtime.stop()))
    joiner = threading.Thread(target=lambda: outcomes.append(runtime.stop()))
    leader.start()
    assert media_entered.wait(1)
    joiner.start()
    media_release.set()
    leader.join(timeout=2)
    joiner.join(timeout=2)

    assert not leader.is_alive()
    assert not joiner.is_alive()
    assert len(outcomes) == 2
    assert len(joined_acknowledgements) == 1
    cycle = joined_acknowledgements[0]
    assert cycle.public_callers == 1
    assert not cycle.restart_safe.is_set()
    with pytest.raises(RuntimeError, match="cleanup is pending"):
        runtime.start("en", "7-9", True)

    runtime._release_stop_public_caller(cycle)
    assert runtime.start("en", "7-9", True)["state"] == GameState.SEARCHING


def test_concurrent_stop_calls_share_one_blocked_media_failure() -> None:
    robot = FakeRobot()
    runtime = GameRuntime(robot, threading.Event())
    media_entered = threading.Event()
    media_release = threading.Event()
    calls = 0

    def blocked_stop_playing() -> None:
        nonlocal calls
        calls += 1
        media_entered.set()
        media_release.wait()

    def capture_stop_error(diagnostics: list[str]) -> None:
        try:
            runtime.stop()
        except PrivacySafeRuntimeError as exc:
            diagnostics.append(exc.diagnostic_code)

    robot.media.stop_playing = blocked_stop_playing  # type: ignore[method-assign]
    diagnostics: list[str] = []
    first = threading.Thread(target=capture_stop_error, args=(diagnostics,))
    second = threading.Thread(target=capture_stop_error, args=(diagnostics,))
    first.start()
    assert media_entered.wait(1)
    second.start()
    first.join(timeout=1)
    second.join(timeout=1)
    try:
        assert not first.is_alive()
        assert not second.is_alive()
        assert calls == 1
        assert diagnostics == ["media_stop_timeout", "media_stop_timeout"]
    finally:
        media_release.set()


def test_reentrant_stop_during_media_cleanup_shares_fail_closed_outcome() -> None:
    robot = FakeRobot()
    runtime = GameRuntime(robot, threading.Event())
    nested_diagnostics: list[str] = []

    def reentrant_stop_playing() -> None:
        try:
            runtime.stop()
        except PrivacySafeRuntimeError as exc:
            nested_diagnostics.append(exc.diagnostic_code)

    robot.media.stop_playing = reentrant_stop_playing  # type: ignore[method-assign]
    with pytest.raises(PrivacySafeRuntimeError, match="reentrant_media_stop") as outer:
        runtime.stop()
    assert nested_diagnostics == [outer.value.diagnostic_code]


def test_revoked_return_boundary_timeout_is_bounded_and_fail_closed(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    runtime = GameRuntime(FakeRobot(), threading.Event())
    runtime._ready.set()
    generation = runtime.machine.start(language="en", age_band="7-9", camera_consent=True)
    completion_acknowledged = threading.Event()
    allow_public_return = threading.Event()
    original_complete = runtime._complete_lease

    def blocked_after_completion_acknowledgement(lease) -> None:  # type: ignore[no-untyped-def]
        original_complete(lease)
        completion_acknowledged.set()
        allow_public_return.wait()

    monkeypatch.setattr(runtime, "_complete_lease", blocked_after_completion_acknowledgement)
    caller = threading.Thread(target=runtime._call_if_current, args=(generation, lambda: object()))
    caller.start()
    assert completion_acknowledged.wait(1)
    before = time.monotonic()
    try:
        with pytest.raises(PrivacySafeRuntimeError, match="local_completion_timeout"):
            runtime.stop()
        assert caller.is_alive()
        assert time.monotonic() - before < 0.25
        assert runtime.machine.state == GameState.STOPPED
        with pytest.raises(RuntimeError, match="cleanup is pending"):
            runtime.start("en", "7-9", True)
    finally:
        allow_public_return.set()
        caller.join(timeout=2)
    assert not caller.is_alive()
