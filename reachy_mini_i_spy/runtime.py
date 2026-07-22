"""Cancellable, single-owner Reachy Mini game runtime."""

from __future__ import annotations

import io
import json
import logging
import math
import queue
import secrets
import sys
import threading
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass
from types import FrameType
from typing import Protocol, TypeVar
from urllib import request

import numpy as np
from scipy.signal import resample_poly
from scipy.spatial.transform import Rotation

from .config import load_config
from .game import AgeBand, GameMachine, GameState, Language
from .provider import ProviderClient, ProviderError

_LOGGER = logging.getLogger(__name__)
_T = TypeVar("_T")


def _head_pose(*, yaw: float = 0.0, pitch: float = 0.0) -> np.ndarray:
    """Create the SDK's 4x4 head pose without importing its optional media stack."""
    pose = np.eye(4, dtype=np.float64)
    pose[:3, :3] = Rotation.from_euler("xyz", [0.0, pitch, yaw], degrees=True).as_matrix()
    return pose


class Robot(Protocol):
    media: object
    client: object

    def goto_target(self, **kwargs: object) -> object: ...

    def goto_sleep(self) -> object: ...

    def wake_up(self) -> object: ...

    def enable_motors(self) -> object: ...

    def disable_motors(self) -> object: ...

    def get_current_head_pose(self) -> np.ndarray: ...


@dataclass(frozen=True)
class GameEvent:
    kind: str
    generation: int
    value: str = ""


@dataclass
class OperationLease:
    """Revocable permission plus a local completion barrier for external I/O."""

    generation: int
    epoch: int
    identifier: int = 0
    revoked: threading.Event | None = None
    local_done: threading.Event | None = None
    owner_ident: int = 0
    return_boundary: FrameType | None = None


@dataclass
class StopCycle:
    """One joined Stop attempt and its single success or fail-closed outcome."""

    owner_ident: int
    done: threading.Event
    restart_safe: threading.Event
    public_callers: int = 1
    result: dict[str, object] | None = None
    diagnostic_code: str | None = None
    failure: BaseException | None = None
    media_worker_ident: int | None = None
    return_boundary: FrameType | None = None


class PrivacySafeRuntimeError(RuntimeError):
    """Runtime failure carrying only an allowlisted diagnostic code."""

    def __init__(self, diagnostic_code: str) -> None:
        super().__init__(diagnostic_code)
        self.diagnostic_code = diagnostic_code


class MotionOwner:
    """The only class allowed to issue robot motion commands."""

    INIT_HEAD_POSE = np.eye(4, dtype=np.float64)
    SLEEP_HEAD_POSE = np.array(
        [
            [0.911, 0.004, 0.413, -0.021],
            [-0.004, 1.0, -0.001, 0.001],
            [-0.413, -0.001, 0.911, -0.044],
            [0.0, 0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    # Match SDK v1.9's pose-distance semantics: millimetres of translation plus
    # degrees of rotation. Its sleep sequence treats <=30 magic-mm as near the
    # initial pose, while app teardown uses <=10 magic-mm for a verified fold.
    WAKE_POSE_MAGIC_DISTANCE = 30.0
    SLEEP_POSE_MAGIC_DISTANCE = 10.0
    # SDK task completion and measured feedback are separate boundaries. On the
    # live robot, the final wake task can complete just before pose telemetry
    # converges; keep this grace bounded and cancellable.
    WAKE_POSE_TIMEOUT_SECONDS = 2.0
    WAKE_STOP_BARRIER_TIMEOUT_SECONDS = 3.0
    POSE_TIMEOUT_SECONDS = 1.0

    SEARCH_POSES = (
        (-24.0, -30.0, -5.0),
        (-12.0, -16.0, 4.0),
        (0.0, 0.0, -6.0),
        (12.0, 16.0, 3.0),
        (24.0, 30.0, -4.0),
    )
    FRAME_CAPTURE_TIMEOUT_SECONDS = 3.0
    FRAME_CAPTURE_RETRY_SECONDS = 0.05

    def __init__(
        self,
        robot: Robot,
        machine: GameMachine,
        cancel_event: threading.Event,
        ownership_lock: threading.RLock | None = None,
        acquire_lease: Callable[[int], OperationLease | None] | None = None,
        lease_is_current: Callable[[OperationLease], bool] | None = None,
        run_untrusted: Callable[[OperationLease, Callable[[], _T]], _T | None] | None = None,
        run_protected_sync: Callable[[OperationLease, Callable[[], _T]], _T | None] | None = None,
        release_lease: Callable[[OperationLease], None] | None = None,
        complete_lease: Callable[[OperationLease], None] | None = None,
    ) -> None:
        self.robot = robot
        self.machine = machine
        self.cancel_event = cancel_event
        self._ownership_lock = ownership_lock or threading.RLock()
        self._acquire_runtime_lease = acquire_lease
        self._runtime_lease_is_current = lease_is_current
        self._run_runtime_untrusted = run_untrusted
        self._run_runtime_sync = run_protected_sync
        self._release_runtime_lease = release_lease
        self._complete_runtime_lease = complete_lease
        self._state_lock = threading.Lock()
        self._state = "unknown"
        self._message = "Motor and fold state have not been verified."
        self._hardware_lock = threading.RLock()
        self._wake_active = threading.Event()

    def status(self) -> dict[str, str]:
        with self._state_lock:
            return {"motor_state": self._state, "motor_message": self._message}

    def _set_state(self, state: str, message: str) -> None:
        with self._state_lock:
            self._state = state
            self._message = message

    def _is_current(self, generation: int) -> bool:
        return not self.cancel_event.is_set() and self.machine.is_current(generation)

    def _acquire_lease(self, generation: int) -> OperationLease | None:
        if self._acquire_runtime_lease is not None:
            return self._acquire_runtime_lease(generation)
        with self._ownership_lock:
            return OperationLease(generation, 0) if self._is_current(generation) else None

    def _lease_is_current(self, lease: OperationLease) -> bool:
        if self._runtime_lease_is_current is not None:
            return self._runtime_lease_is_current(lease)
        return self._is_current(lease.generation)

    def _run_untrusted(self, lease: OperationLease, operation: Callable[[], _T]) -> _T | None:
        if self._run_runtime_untrusted is not None:
            return self._run_runtime_untrusted(lease, operation)
        return operation() if self._lease_is_current(lease) else None

    def _run_protected_sync(self, lease: OperationLease, operation: Callable[[], _T]) -> _T | None:
        if self._run_runtime_sync is not None:
            return self._run_runtime_sync(lease, operation)
        return operation() if self._lease_is_current(lease) else None

    def _release_lease(self, lease: OperationLease) -> None:
        if self._release_runtime_lease is not None:
            self._release_runtime_lease(lease)

    def _complete_lease(self, lease: OperationLease) -> None:
        if self._complete_runtime_lease is not None:
            self._complete_runtime_lease(lease)

    def _motor_mode(self) -> str:
        status = self.robot.client.get_status(wait=True, timeout=2.0)  # type: ignore[attr-defined]
        mode = status.backend_status.motor_control_mode
        return str(getattr(mode, "value", mode)).lower()

    def _wait_for_motor_mode(self, expected: str) -> bool:
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if self._motor_mode() == expected:
                return True
            time.sleep(0.05)
        return self._motor_mode() == expected

    def _pose_magic_distance(self, expected: np.ndarray) -> float:
        measured = np.asarray(self.robot.get_current_head_pose(), dtype=np.float64)
        rotation = measured[:3, :3] if measured.shape == (4, 4) else np.empty((0, 0))
        if (
            measured.shape != (4, 4)
            or not np.isfinite(measured).all()
            or not np.allclose(measured[3], [0.0, 0.0, 0.0, 1.0], atol=1e-6)
            or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-3)
            or not math.isclose(float(np.linalg.det(rotation)), 1.0, abs_tol=1e-3)
        ):
            return math.inf
        translation_mm = float(np.linalg.norm(measured[:3, 3] - expected[:3, 3]) * 1000.0)
        relative_rotation = rotation @ expected[:3, :3].T
        cosine = float(np.clip((np.trace(relative_rotation) - 1.0) / 2.0, -1.0, 1.0))
        rotation_degrees = math.degrees(math.acos(cosine))
        return translation_mm + rotation_degrees

    def _pose_is(self, expected: np.ndarray, max_magic_distance: float) -> bool:
        return self._pose_magic_distance(expected) <= max_magic_distance

    def _wait_for_pose(
        self,
        expected: np.ndarray,
        max_magic_distance: float,
        generation: int | None = None,
        timeout_seconds: float = POSE_TIMEOUT_SECONDS,
    ) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if generation is not None and not self._is_current(generation):
                return False
            pose_matches = self._pose_is(expected, max_magic_distance)
            if generation is not None and not self._is_current(generation):
                return False
            if pose_matches:
                return True
            time.sleep(0.05)
        if generation is not None and not self._is_current(generation):
            return False
        pose_matches = self._pose_is(expected, max_magic_distance)
        if generation is not None and not self._is_current(generation):
            return False
        return pose_matches

    def wake(self, generation: int) -> bool:
        """Enable torque, run the SDK wake sequence, and verify the measured pose."""
        if not self._is_current(generation):
            return False
        # Publish activity before acquiring a lease: if Stop observes this bit it
        # joins the bounded hardware command; if Stop wins first, lease acquire
        # fails and no wake side effect can begin afterward.
        self._wake_active.set()
        lease: OperationLease | None = None
        try:
            with self._hardware_lock:
                try:
                    lease = self._acquire_lease(generation)
                    if lease is None:
                        return False
                    self._set_state("waking", "Preparing Reachy safely…")
                    if self._motor_mode() != "enabled" and self._run_protected_sync(
                        lease,
                        lambda: (self.robot.enable_motors(), True)[1],
                    ) is not True:
                        return False
                    if not self._wait_for_motor_mode("enabled"):
                        raise RuntimeError("Motor enable was not confirmed")
                    if not self._lease_is_current(lease):
                        return False
                    if self._run_protected_sync(
                        lease,
                        lambda: (self.robot.wake_up(), True)[1],
                    ) is not True:
                        return False
                    if not self._lease_is_current(lease):
                        return False
                    pose_confirmed = self._wait_for_pose(
                        self.INIT_HEAD_POSE,
                        self.WAKE_POSE_MAGIC_DISTANCE,
                        generation,
                        self.WAKE_POSE_TIMEOUT_SECONDS,
                    )
                    if not self._lease_is_current(lease):
                        return False
                    if not pose_confirmed:
                        raise PrivacySafeRuntimeError("wake_pose_unconfirmed")
                except Exception:
                    if lease is not None and self._lease_is_current(lease):
                        self._set_state("error", "Reachy could not wake safely; torque remains on.")
                        raise
                    return False
                with self._ownership_lock:
                    if not self._lease_is_current(lease):
                        return False
                    self._set_state("awake", "Reachy is awake and ready.")
                return self._lease_is_current(lease)
        finally:
            self._wake_active.clear()
            if lease is not None:
                self._release_lease(lease)
                lease.return_boundary = sys._getframe()
                self._complete_lease(lease)

    def quiesce_wake_and_fold(self, wake_was_active: bool = False) -> bool:
        """Join an active wake command and restore safe sleep before Stop returns."""
        if not wake_was_active and not self._wake_active.is_set():
            return False
        if not self._hardware_lock.acquire(timeout=self.WAKE_STOP_BARRIER_TIMEOUT_SECONDS):
            raise PrivacySafeRuntimeError("wake_stop_barrier_timeout")
        try:
            self.revoke_outputs()
            self.fold_and_disable()
            return True
        finally:
            self._hardware_lock.release()

    def revoke_outputs(self) -> None:
        """Synchronously remove any stale awake publication at Stop's boundary."""
        self._set_state("stopping", "Stopping and folding Reachy safely…")

    def fold_and_disable(self) -> bool:
        """Fold with measured verification, then and only then disable all torque."""
        with self._hardware_lock:
            return self._fold_and_disable_locked()

    def _fold_and_disable_locked(self) -> bool:
        if self._motor_mode() == "disabled" and self._pose_is(
            self.SLEEP_HEAD_POSE,
            self.SLEEP_POSE_MAGIC_DISTANCE,
        ):
            self._set_state("asleep", "Reachy is folded and motors are safely disabled.")
            return True
        self._set_state("folding", "Stopping and folding Reachy safely…")
        try:
            if self._motor_mode() != "enabled":
                self.robot.enable_motors()
            if not self._wait_for_motor_mode("enabled"):
                raise RuntimeError("Motor enable was not confirmed")
            self.robot.goto_sleep()
            if not self._wait_for_pose(
                self.SLEEP_HEAD_POSE,
                self.SLEEP_POSE_MAGIC_DISTANCE,
            ):
                raise RuntimeError("Fold pose was not confirmed")
        except Exception:
            self._set_state("error", "Reachy could not confirm a safe fold; torque remains on.")
            raise

        try:
            self.robot.disable_motors()
            if not self._wait_for_motor_mode("disabled"):
                raise RuntimeError("Motor disable was not confirmed")
        except Exception:
            self._set_state("error", "Reachy is folded, but motor shutdown was not confirmed.")
            raise
        self._set_state("asleep", "Reachy is folded and motors are safely disabled.")
        return True

    def _goto(
        self,
        body: float,
        yaw: float,
        pitch: float,
        *,
        duration: float = 0.75,
        generation: int | None = None,
    ) -> bool:
        if abs(body) > 30 or abs(yaw) > 38 or abs(pitch) > 10 or abs(yaw - body) > 20:
            raise ValueError("Search pose exceeds conservative app bounds")
        lease = self._acquire_lease(generation) if generation is not None else None
        if generation is not None and lease is None:
            return False

        def move() -> bool:
            self.robot.goto_target(
                head=_head_pose(yaw=yaw, pitch=pitch),
                body_yaw=np.deg2rad(body),
                antennas=np.deg2rad(np.asarray([10.0, -10.0])),
                duration=duration,
                method="minjerk",
            )
            return True

        if lease is None:
            return move()
        try:
            return self._run_untrusted(lease, move) is True
        finally:
            self._release_lease(lease)
            lease.return_boundary = sys._getframe()
            self._complete_lease(lease)

    def search(self, generation: int) -> list[bytes]:
        """Perform 5.5s choreography and retain only three in-memory JPEGs."""
        if self.status()["motor_state"] != "awake":
            raise RuntimeError("Search motion requires a verified wake")
        frames: list[bytes] = []
        for index, (body, yaw, pitch) in enumerate(self.SEARCH_POSES):
            if not self._is_current(generation):
                return []
            if not self._goto(body, yaw, pitch, generation=generation):
                return []
            if not self._wait(generation, 0.35):
                return []
            if index in {0, 2, 4}:
                frame = self._capture_frame(generation, "search")
                if frame is None:
                    return []
                frames.append(frame)
        return frames

    def _capture_frame(self, generation: int, stage: str) -> bytes | None:
        """Wait boundedly for one ready frame without retaining transient misses."""
        last_error: RuntimeError | None = None
        deadline = time.monotonic() + self.FRAME_CAPTURE_TIMEOUT_SECONDS
        while True:
            if not self._is_current(generation):
                return None
            lease = self._acquire_lease(generation)
            if lease is None:
                return None
            accepted_frame: bytes | None = None
            try:
                # Camera SDK work is untrusted and may block. Never make Stop wait
                # on it; only acceptance of its result is serialized with revoke.
                frame = self._run_untrusted(
                    lease,
                    self.robot.media.get_frame_jpeg,  # type: ignore[attr-defined]
                )
                frame = bytes(frame) if frame else None
            except RuntimeError as exc:
                last_error = exc
            else:
                with self._ownership_lock:
                    if not self._lease_is_current(lease):
                        frame = None
                    elif frame:
                        accepted_frame = frame
            finally:
                self._release_lease(lease)
            if accepted_frame is not None:
                try:
                    return accepted_frame if self._lease_is_current(lease) else None
                finally:
                    lease.return_boundary = sys._getframe()
                    self._complete_lease(lease)
            else:
                lease.return_boundary = sys._getframe()
                self._complete_lease(lease)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if not self._wait(generation, min(self.FRAME_CAPTURE_RETRY_SECONDS, remaining)):
                return None
        if last_error is not None:
            raise PrivacySafeRuntimeError(f"{stage}_frame_capture_failed") from last_error
        raise PrivacySafeRuntimeError(f"{stage}_frame_unavailable")

    def neutral(self, generation: int | None = None) -> bool:
        lease = self._acquire_lease(generation) if generation is not None else None
        if generation is not None and lease is None:
            return False

        def move() -> bool:
            self.robot.goto_target(
                head=_head_pose(),
                body_yaw=0.0,
                antennas=np.asarray([0.0, 0.0]),
                duration=0.8,
                method="minjerk",
            )
            return True

        if lease is None:
            return move()
        try:
            return self._run_untrusted(lease, move) is True
        finally:
            self._release_lease(lease)
            lease.return_boundary = sys._getframe()
            self._complete_lease(lease)

    def capture_target(self, frame_index: int, generation: int) -> bytes | None:
        """Revisit the retained viewpoint before checking target presence."""
        pose_index = (0, 2, 4)[frame_index]
        body, yaw, pitch = self.SEARCH_POSES[pose_index]
        if not self._is_current(generation):
            return None
        if not self._goto(body, yaw, pitch, duration=0.6, generation=generation):
            return None
        if not self._wait(generation, 0.25):
            return None
        frame = self._capture_frame(generation, "target")
        self.neutral(generation)
        return frame

    def _wait(self, generation: int, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if not self._is_current(generation):
                return False
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        return True


class GameRuntime:
    """Serializes all robot I/O and rejects results from replaced sessions."""

    MAX_GUESSES = 6
    LOCAL_COMPLETION_TIMEOUT_SECONDS = 0.1

    def __init__(self, robot: Robot, stop_event: threading.Event) -> None:
        self.robot = robot
        self.stop_event = stop_event
        self.machine = GameMachine()
        self._ownership_lock = threading.RLock()
        self._revocation_requested = threading.Event()
        self._epoch = 0
        self._lease_sequence = 0
        self._active_leases: dict[int, OperationLease] = {}
        self._audio_lock = threading.Lock()
        self._audio_epoch = 0
        self._audio_active_epoch: int | None = None
        self._audio_quiescence_path: tuple[object, object, str] | None = None
        self._stop_lock = threading.Lock()
        self._stop_cycle: StopCycle | None = None
        self._media_cleanup_failed = threading.Event()
        self._media_callbacks_pending = threading.Event()
        self._media_callbacks_lock = threading.Lock()
        self._media_callback_count = 0
        self._media_start_pending = threading.Event()
        self._media_start_count = 0
        self._media_backend_quarantined = threading.Event()
        self._return_boundaries_pending = threading.Event()
        self._runtime_nonce = secrets.token_hex(12)
        self.motion = MotionOwner(
            robot,
            self.machine,
            stop_event,
            self._ownership_lock,
            self._acquire_lease,
            self._lease_is_current,
            self._run_untrusted,
            self._run_protected_sync,
            self._release_lease,
            self._complete_lease,
        )
        self._events: queue.Queue[GameEvent] = queue.Queue(maxsize=16)
        self._ready = threading.Event()
        self._shutdown_complete = threading.Event()
        self._shutdown_failed = threading.Event()
        self._shutdown_started = threading.Event()
        self._worker_ident: int | None = None
        self._providers: dict[int, ProviderClient] = {}
        self._providers_lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        self._pending_cleanup_epoch: int | None = None

    def snapshot(self) -> dict[str, object]:
        return {**self.machine.snapshot(), **self.motion.status(), "runtime_ready": self._ready.is_set()}

    def _is_current(self, generation: int) -> bool:
        return (
            not self._revocation_requested.is_set()
            and not self.stop_event.is_set()
            and self.machine.is_current(generation)
        )

    def _acquire_lease(self, generation: int) -> OperationLease | None:
        with self._ownership_lock:
            if not self._is_current(generation):
                return None
            self._lease_sequence += 1
            lease = OperationLease(
                generation,
                self._epoch,
                self._lease_sequence,
                threading.Event(),
                threading.Event(),
                threading.get_ident(),
            )
            self._active_leases[lease.identifier] = lease
            return lease

    def _lease_is_current(self, lease: OperationLease) -> bool:
        with self._ownership_lock:
            return (
                lease.revoked is not None
                and not lease.revoked.is_set()
                and self._active_leases.get(lease.identifier) is lease
                and self._epoch == lease.epoch
                and self._is_current(lease.generation)
            )

    def _release_lease(self, lease: OperationLease) -> None:
        """Finish operation work; ownership remains until the public boundary."""

    def _complete_lease(self, lease: OperationLease) -> None:
        """Acknowledge only after the leased public frame has actually returned."""
        boundary = lease.return_boundary or sys._getframe(1)
        lease.return_boundary = boundary

        def acknowledge_after_return() -> None:
            while True:
                frame = sys._current_frames().get(lease.owner_ident)
                while frame is not None and frame is not boundary:
                    frame = frame.f_back
                if frame is None:
                    break
                time.sleep(0)
            with self._ownership_lock:
                self._active_leases.pop(lease.identifier, None)
                revoked_return_remains = any(
                    active.revoked is not None and active.revoked.is_set()
                    for active in self._active_leases.values()
                )
                if not revoked_return_remains:
                    self._return_boundaries_pending.clear()
            lease.return_boundary = None
            if lease.local_done is not None:
                lease.local_done.set()

        threading.Thread(
            target=acknowledge_after_return,
            daemon=True,
            name="ispy-return-boundary",
        ).start()

    def _run_untrusted(self, lease: OperationLease, operation: Callable[[], _T]) -> _T | None:
        """Run untrusted work on a daemon and abandon it immediately on revocation."""
        completed = threading.Event()
        outcome: list[tuple[bool, object]] = []

        def invoke() -> None:
            try:
                outcome.append((True, operation()))
            except BaseException as exc:
                outcome.append((False, exc))
            finally:
                completed.set()

        worker = threading.Thread(target=invoke, daemon=True, name="ispy-untrusted-operation")
        # Validate and launch atomically against Stop/revoke. If Stop wins the
        # lock first no protected side effect is launched.
        with self._ownership_lock:
            if not self._lease_is_current(lease):
                return None
            worker.start()
        while not completed.wait(0.01):
            if lease.revoked is not None and lease.revoked.is_set():
                return None
        if not self._lease_is_current(lease):
            return None
        succeeded, value = outcome[0]
        if not succeeded:
            raise value  # type: ignore[misc]
        return value  # type: ignore[return-value]

    def _run_protected_sync(self, lease: OperationLease, operation: Callable[[], _T]) -> _T | None:
        """Atomically launch protected hardware work and join its true completion."""
        completed = threading.Event()
        outcome: list[tuple[bool, object]] = []

        def invoke() -> None:
            try:
                outcome.append((True, operation()))
            except BaseException as exc:
                outcome.append((False, exc))
            finally:
                completed.set()

        worker = threading.Thread(target=invoke, daemon=True, name="ispy-protected-operation")
        with self._ownership_lock:
            if not self._lease_is_current(lease):
                return None
            worker.start()
        completed.wait()
        succeeded, value = outcome[0]
        if not succeeded:
            raise value  # type: ignore[misc]
        return value if self._lease_is_current(lease) else None  # type: ignore[return-value]

    def _run_untrusted_media_start(self, lease: OperationLease, operation: Callable[[], _T]) -> _T | None:
        """Track a launched start callback until its true completion after revocation."""
        completed = threading.Event()
        outcome: list[tuple[bool, object]] = []

        def invoke() -> None:
            try:
                outcome.append((True, operation()))
            except BaseException as exc:
                outcome.append((False, exc))
            finally:
                with self._media_callbacks_lock:
                    self._media_start_count -= 1
                    if self._media_start_count == 0:
                        self._media_start_pending.clear()
                    self._media_callback_count -= 1
                    if self._media_callback_count == 0:
                        self._media_callbacks_pending.clear()
                completed.set()

        worker = threading.Thread(target=invoke, daemon=True, name="ispy-media-start")
        with self._ownership_lock:
            if not self._lease_is_current(lease):
                return None
            with self._media_callbacks_lock:
                self._media_start_count += 1
                self._media_start_pending.set()
                self._media_callback_count += 1
                self._media_callbacks_pending.set()
            worker.start()
        while not completed.wait(0.01):
            if lease.revoked is not None and lease.revoked.is_set():
                return None
        if not self._lease_is_current(lease):
            return None
        succeeded, value = outcome[0]
        if not succeeded:
            raise value  # type: ignore[misc]
        return value  # type: ignore[return-value]

    def start(self, language: Language, age_band: AgeBand, camera_consent: bool) -> dict[str, object]:
        # Keep Stop-cycle selection and generation replacement in one lock
        # order. A returned leader remains joinable until its frame-exit
        # acknowledgement; no new generation may enter that window.
        with self._stop_lock:
            cycle = self._stop_cycle
            if cycle is not None and not cycle.restart_safe.is_set():
                raise RuntimeError("Safe Stop cleanup is pending")
            with self._ownership_lock:
                if not self._ready.is_set() or self.stop_event.is_set():
                    raise RuntimeError("Game runtime is not ready")
                if (
                    self._cleanup_pending()
                    or self._media_cleanup_failed.is_set()
                    or self._media_callbacks_pending.is_set()
                    or self._media_backend_quarantined.is_set()
                    or self._return_boundaries_pending.is_set()
                ):
                    raise RuntimeError("Safe Stop cleanup is pending")
                self._revocation_requested.clear()
                generation = self.machine.start(
                    language=language, age_band=age_band, camera_consent=camera_consent
                )
                self._replace_pending(GameEvent("start", generation))
        return self.snapshot()

    def guess(self, text: str) -> dict[str, object]:
        snapshot = self.machine.snapshot()
        if snapshot["state"] != GameState.GUESSING:
            raise RuntimeError("No round is waiting for a guess")
        clean = " ".join(text.split())
        if not clean or len(clean) > 80:
            raise ValueError("Guess must contain 1-80 characters")
        try:
            self._events.put_nowait(GameEvent("guess", int(snapshot["generation"]), clean))
        except queue.Full as exc:
            raise RuntimeError("Please wait for the current guess") from exc
        return self.snapshot()

    def stop(self, reason: str = "caregiver") -> dict[str, object]:
        # Invocation gate: operations already queued for publication lose
        # before Stop waits to acquire the linearization lock.
        self._revocation_requested.set()
        caller_ident = threading.get_ident()
        with self._stop_lock:
            cycle = self._stop_cycle
            if cycle is not None and (
                not cycle.done.is_set() or self._media_callbacks_pending.is_set()
            ):
                if cycle.media_worker_ident == caller_ident:
                    cycle.diagnostic_code = "reentrant_media_stop"
                    raise PrivacySafeRuntimeError(cycle.diagnostic_code)
                cycle.public_callers += 1
                cycle.restart_safe.clear()
                leader = False
            else:
                cycle = StopCycle(
                    caller_ident,
                    threading.Event(),
                    threading.Event(),
                    return_boundary=sys._getframe(),
                )
                self._stop_cycle = cycle
                leader = True

        if not leader:
            boundary = sys._getframe()
            try:
                join_timeout = self.motion.WAKE_STOP_BARRIER_TIMEOUT_SECONDS + 1.0
                if not cycle.done.wait(join_timeout):
                    raise PrivacySafeRuntimeError("stop_join_timeout")
                return self._joined_stop_outcome(cycle)
            finally:
                self._ack_stop_caller_after_public_return(cycle, boundary)

        try:
            cycle.result = self._stop_once(reason, cycle)
        except PrivacySafeRuntimeError as exc:
            cycle.diagnostic_code = exc.diagnostic_code
        except BaseException as exc:
            cycle.failure = exc
        finally:
            self._ack_stop_after_public_return(cycle)
        return self._joined_stop_outcome(cycle)

    @staticmethod
    def _joined_stop_outcome(cycle: StopCycle) -> dict[str, object]:
        if cycle.diagnostic_code is not None:
            raise PrivacySafeRuntimeError(cycle.diagnostic_code)
        if cycle.failure is not None:
            raise cycle.failure
        if cycle.result is None:
            raise PrivacySafeRuntimeError("stop_outcome_missing")
        return cycle.result

    def _ack_stop_after_public_return(self, cycle: StopCycle) -> None:
        """Release joined callers only after the leader's Stop frame has exited."""
        boundary = cycle.return_boundary

        def acknowledge() -> None:
            self._wait_until_frame_returns(cycle.owner_ident, boundary)
            cycle.return_boundary = None
            cycle.done.set()
            self._release_stop_public_caller(cycle)

        threading.Thread(target=acknowledge, daemon=True, name="ispy-stop-return-boundary").start()

    def _ack_stop_caller_after_public_return(self, cycle: StopCycle, boundary: FrameType) -> None:
        owner_ident = threading.get_ident()

        def acknowledge() -> None:
            self._wait_until_frame_returns(owner_ident, boundary)
            self._release_stop_public_caller(cycle)

        threading.Thread(target=acknowledge, daemon=True, name="ispy-stop-joiner-return-boundary").start()

    @staticmethod
    def _wait_until_frame_returns(owner_ident: int, boundary: FrameType | None) -> None:
        while boundary is not None:
            frame = sys._current_frames().get(owner_ident)
            while frame is not None and frame is not boundary:
                frame = frame.f_back
            if frame is None:
                return
            time.sleep(0)

    def _release_stop_public_caller(self, cycle: StopCycle) -> None:
        with self._stop_lock:
            cycle.public_callers -= 1
            if cycle.public_callers == 0:
                cycle.restart_safe.set()

    def _stop_once(self, reason: str, cycle: StopCycle) -> dict[str, object]:
        wake_was_active = self.motion._wake_active.is_set()
        with self._ownership_lock:
            generation = self.machine.stop()
            self._epoch += 1
            revoked_leases = list(self._active_leases.values())
            for lease in revoked_leases:
                if lease.revoked is not None:
                    lease.revoked.set()
            cleanup_epoch = self._epoch
            revoked_providers = self._revoke_providers_locked()
            self.motion.revoke_outputs()
            self._request_cleanup(cleanup_epoch)
            self._replace_pending(GameEvent("stop", generation, reason))
        remotely_cancel = self._cancel_local_providers(revoked_providers)
        self._schedule_remote_cancels(remotely_cancel)
        with self._audio_lock:
            self._audio_epoch += 1
            self._audio_active_epoch = None

        media_error: PrivacySafeRuntimeError | None = None
        try:
            self._bounded_media_stop(cycle)
        except PrivacySafeRuntimeError as exc:
            self._media_cleanup_failed.set()
            media_error = exc
        else:
            self._media_cleanup_failed.clear()
        if self._media_start_pending.is_set() and media_error is None:
            start_deadline = time.monotonic() + self.LOCAL_COMPLETION_TIMEOUT_SECONDS
            while self._media_start_pending.is_set() and time.monotonic() < start_deadline:
                time.sleep(0.001)
            if self._media_start_pending.is_set():
                self._media_cleanup_failed.set()
                media_error = PrivacySafeRuntimeError("media_start_pending")

        if self._worker_ident in {None, threading.get_ident()}:
            # Unit-level callers may use the state machine without starting
            # run(); a Stop raised by run() is already on the motion owner.
            self.motion.fold_and_disable()
            self._ack_cleanup(cleanup_epoch)
        elif not self._wait_for_cleanup(
            cleanup_epoch,
            self.motion.WAKE_STOP_BARRIER_TIMEOUT_SECONDS,
        ):
            diagnostic_code = "wake_stop_barrier_timeout" if wake_was_active else "stop_cleanup_timeout"
            raise PrivacySafeRuntimeError(diagnostic_code)

        if media_error is not None:
            if any(lease.local_done is not None and not lease.local_done.is_set() for lease in revoked_leases):
                self._return_boundaries_pending.set()
            raise media_error
        completion_deadline = time.monotonic() + self.LOCAL_COMPLETION_TIMEOUT_SECONDS
        for lease in revoked_leases:
            if lease.local_done is None:
                continue
            remaining = completion_deadline - time.monotonic()
            if remaining <= 0 or not lease.local_done.wait(remaining):
                self._return_boundaries_pending.set()
                raise PrivacySafeRuntimeError("local_completion_timeout")
        return self.snapshot()

    def _bounded_media_stop(self, cycle: StopCycle | None = None) -> None:
        """Require positive bounded completion from the untrusted media callback."""
        completed = threading.Event()
        failures: list[BaseException] = []
        with self._audio_lock:
            positive_proof_required = self._audio_quiescence_path is not None
        positive_proof_required = positive_proof_required or self._media_cleanup_failed.is_set()

        def invoke() -> None:
            if cycle is not None:
                with self._stop_lock:
                    cycle.media_worker_ident = threading.get_ident()
            try:
                self.robot.media.stop_playing()  # type: ignore[attr-defined]
            except BaseException as exc:
                failures.append(exc)
            finally:
                if not failures and (cycle is None or cycle.diagnostic_code != "reentrant_media_stop"):
                    self._media_cleanup_failed.clear()
                with self._media_callbacks_lock:
                    self._media_callback_count -= 1
                    if self._media_callback_count == 0:
                        self._media_callbacks_pending.clear()
                completed.set()

        with self._media_callbacks_lock:
            self._media_callback_count += 1
            self._media_callbacks_pending.set()
        threading.Thread(target=invoke, daemon=True, name="ispy-media-stop").start()
        if not completed.wait(self.LOCAL_COMPLETION_TIMEOUT_SECONDS):
            if self._bounded_backend_silence():
                return
            raise PrivacySafeRuntimeError("media_stop_timeout")
        if cycle is not None and cycle.diagnostic_code == "reentrant_media_stop":
            self._bounded_backend_silence()
            raise PrivacySafeRuntimeError(cycle.diagnostic_code)
        if failures:
            if self._bounded_backend_silence() is True:
                return
            _LOGGER.warning("Media stop and backend silence failed closed (%s)", type(failures[0]).__name__)
            raise PrivacySafeRuntimeError("media_stop_failed")
        backend_silence = self._bounded_backend_silence()
        if backend_silence is False or (positive_proof_required and backend_silence is not True):
            raise PrivacySafeRuntimeError("media_stop_unconfirmed")

    def _bounded_backend_silence(self) -> bool | None:
        """Observe NULL on the owned playback transport; never trust opaque clears."""
        completed = threading.Event()
        quiescent: list[bool] = []
        available = threading.Event()
        with self._audio_lock:
            owned_path = self._audio_quiescence_path

        def invoke() -> None:
            try:
                path = owned_path or self._media_positive_quiescence_path()
                if path is None:
                    return
                audio, pipeline, pipeline_attribute = path
                available.set()
                if pipeline_attribute == "_pipeline_record" and not self._clear_webrtc_incoming_audio(audio):
                    return
                current = pipeline.get_state(0).state
                null_state = getattr(type(current), "NULL", None)
                if null_state is None:
                    return
                pipeline.set_state(null_state)
                observed = pipeline.get_state(int(self.LOCAL_COMPLETION_TIMEOUT_SECONDS * 1_000_000_000)).state
                quiescent.append(observed == null_state)
                if observed == null_state and pipeline_attribute == "_pipeline_record":
                    # The v1.9 WebRTC pipeline also owns camera/recording. Keep
                    # this process fail-closed after emergency teardown; only a
                    # fresh SDK/app lifecycle may establish a new peer.
                    self._media_backend_quarantined.set()
            except BaseException:
                return
            finally:
                with self._media_callbacks_lock:
                    self._media_callback_count -= 1
                    if self._media_callback_count == 0:
                        self._media_callbacks_pending.clear()
                completed.set()

        with self._media_callbacks_lock:
            self._media_callback_count += 1
            self._media_callbacks_pending.set()
        threading.Thread(target=invoke, daemon=True, name="ispy-backend-silence").start()
        if not completed.wait(self.LOCAL_COMPLETION_TIMEOUT_SECONDS):
            return False
        if not available.is_set():
            return None
        confirmed = quiescent == [True]
        if confirmed and owned_path is not None:
            with self._audio_lock:
                if self._audio_quiescence_path == owned_path:
                    self._audio_quiescence_path = None
        return confirmed

    def _media_positive_quiescence_path(self) -> tuple[object, object, str] | None:
        """Return the observable transport that must remain owned after admission."""
        audio = getattr(self.robot.media, "audio", None)
        for attribute in ("_pipeline", "_pipeline_record"):
            pipeline = getattr(audio, attribute, None)
            if pipeline is not None:
                return audio, pipeline, attribute
        return None

    def _clear_webrtc_incoming_audio(self, audio: object) -> bool:
        """Require the synchronous daemon acknowledgement that its speaker queue was flushed."""
        daemon_url = str(getattr(audio, "daemon_url", "")).rstrip("/")
        if not daemon_url:
            return False
        clear_request = request.Request(
            f"{daemon_url}/api/media/clear_incoming_audio",
            data=b"",
            method="POST",
        )
        with request.urlopen(clear_request, timeout=self.LOCAL_COMPLETION_TIMEOUT_SECONDS) as response:
            if not 200 <= int(response.status) < 300:
                return False
            payload = json.loads(response.read(256))
        return payload == {"status": "ok"}

    def _request_cleanup(self, epoch: int) -> None:
        with self._cleanup_lock:
            if self._pending_cleanup_epoch is None or epoch > self._pending_cleanup_epoch:
                self._pending_cleanup_epoch = epoch

    def _cleanup_pending(self) -> bool:
        with self._cleanup_lock:
            return self._pending_cleanup_epoch is not None

    def _cleanup_epoch(self) -> int | None:
        with self._cleanup_lock:
            return self._pending_cleanup_epoch

    def _ack_cleanup(self, epoch: int) -> None:
        with self._cleanup_lock:
            if self._pending_cleanup_epoch == epoch:
                self._pending_cleanup_epoch = None

    def _wait_for_cleanup(self, epoch: int, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while self._cleanup_epoch() == epoch:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            self._shutdown_complete.wait(min(0.01, remaining))
        return True

    def _revoke_providers_locked(self) -> list[ProviderClient]:
        with self._providers_lock:
            providers = list(self._providers.values())
            self._providers.clear()
        return providers

    @staticmethod
    def _cancel_local_providers(providers: list[ProviderClient]) -> list[ProviderClient]:
        """Revoke provider objects outside the ownership lock."""
        return [provider for provider in providers if provider.cancel_local()]

    def _schedule_remote_cancels(self, providers: list[ProviderClient]) -> None:
        for provider in providers:
            threading.Thread(
                target=provider.cancel_broker,
                daemon=True,
                name="ispy-broker-cancel",
            ).start()

    def _cancel_providers(self) -> None:
        with self._ownership_lock:
            self._epoch += 1
            for lease in self._active_leases.values():
                if lease.revoked is not None:
                    lease.revoked.set()
            revoked_providers = self._revoke_providers_locked()
        remotely_cancel = self._cancel_local_providers(revoked_providers)
        self._schedule_remote_cancels(remotely_cancel)

    def _begin_shutdown(self) -> None:
        """Revoke active work as soon as the SDK requests process shutdown."""
        with self._ownership_lock:
            if self._shutdown_started.is_set():
                return
            self._shutdown_started.set()
            self._ready.clear()
            self._revocation_requested.set()
            self.machine.stop("App stopped.")
            self.motion.revoke_outputs()
            self._epoch += 1
            for lease in self._active_leases.values():
                if lease.revoked is not None:
                    lease.revoked.set()
            revoked_providers = self._revoke_providers_locked()
        remotely_cancel = self._cancel_local_providers(revoked_providers)
        self._schedule_remote_cancels(remotely_cancel)

    def _watch_for_shutdown(self) -> None:
        self.stop_event.wait()
        self._begin_shutdown()

    def _replace_pending(self, event: GameEvent) -> None:
        while True:
            try:
                self._events.get_nowait()
            except queue.Empty:
                break
        self._events.put_nowait(event)

    def run(self) -> None:
        self._worker_ident = threading.get_ident()
        self._ready.set()
        threading.Thread(target=self._watch_for_shutdown, daemon=True, name="ispy-shutdown-watch").start()
        try:
            while not self.stop_event.is_set():
                cleanup_epoch = self._cleanup_epoch()
                if cleanup_epoch is not None:
                    try:
                        self.motion.fold_and_disable()
                    except Exception as fold_exc:
                        _LOGGER.error(
                            "Could not confirm required Stop cleanup; preserving torque (%s)",
                            type(fold_exc).__name__,
                        )
                        time.sleep(0.1)
                    else:
                        self._ack_cleanup(cleanup_epoch)
                    continue
                try:
                    event = self._events.get(timeout=0.1)
                except queue.Empty:
                    continue
                try:
                    if event.kind == "start":
                        self._run_round(event.generation)
                    elif event.kind == "guess":
                        self._handle_guess(event)
                    elif event.kind == "stop":
                        self.motion.fold_and_disable()
                        self._ack_cleanup(self._epoch)
                except Exception as exc:
                    diagnostic_code = (
                        exc.diagnostic_code
                        if isinstance(exc, PrivacySafeRuntimeError)
                        else "unclassified_runtime_failure"
                    )
                    _LOGGER.warning(
                        "Game boundary failed closed: %s diagnostic=%s",
                        type(exc).__name__,
                        diagnostic_code,
                    )
                    with self._ownership_lock:
                        failed_generation = self.machine.fail_if_current(
                            event.generation,
                            "I couldn't safely continue. Camera is off."
                            if self.machine.language == "en"
                            else "Ik kon niet veilig doorgaan. De camera staat uit."
                        )
                        if failed_generation is not None:
                            self._cancel_providers()
                    if event.kind != "stop":
                        try:
                            self.motion.fold_and_disable()
                        except Exception as fold_exc:
                            _LOGGER.error(
                                "Could not confirm safe fold; preserving torque (%s)",
                                type(fold_exc).__name__,
                            )
        finally:
            self._begin_shutdown()
            shutdown_error: BaseException | None = None
            try:
                self.motion.fold_and_disable()
            except Exception as fold_exc:
                shutdown_error = fold_exc
                _LOGGER.error(
                    "Could not confirm safe fold during cleanup; preserving torque (%s)",
                    type(fold_exc).__name__,
                )
            else:
                cleanup_epoch = self._cleanup_epoch()
                if cleanup_epoch is not None:
                    self._ack_cleanup(cleanup_epoch)
            self._worker_ident = None
            if shutdown_error is not None:
                self._shutdown_failed.set()
                raise PrivacySafeRuntimeError("shutdown_fold_unconfirmed") from shutdown_error
            self._shutdown_complete.set()

    def _provider(self, generation: int) -> ProviderClient | None:
        lease = self._acquire_lease(generation)
        if lease is None:
            return None
        accepted: ProviderClient | None = None
        try:
            with self._providers_lock:
                existing = self._providers.get(generation)
                if existing is not None:
                    accepted = existing
            if accepted is None:
                provider = self._run_untrusted(
                    lease,
                    lambda: ProviderClient(
                        load_config(),
                        session_id=f"{self._runtime_nonce}{generation & 0xFFFFFFFF:08x}",
                    ),
                )
                if provider is not None:
                    with self._ownership_lock:
                        if self._lease_is_current(lease):
                            with self._providers_lock:
                                accepted = self._providers.get(generation)
                                if accepted is None:
                                    self._providers[generation] = provider
                                    accepted = provider
                    if accepted is not provider:
                        provider.cancel_local()
        finally:
            self._release_lease(lease)
        try:
            return accepted if self._lease_is_current(lease) else None
        finally:
            lease.return_boundary = sys._getframe()
            self._complete_lease(lease)

    def _call_if_current(self, generation: int, operation: Callable[[], _T]) -> _T | None:
        """Run blocking work unlocked and accept its result only under a live lease."""
        lease = self._acquire_lease(generation)
        if lease is None:
            return None
        try:
            result = self._run_untrusted(lease, operation)
        except BaseException:
            self._release_lease(lease)
            lease.return_boundary = sys._getframe()
            self._complete_lease(lease)
            raise
        self._release_lease(lease)
        try:
            return result if self._lease_is_current(lease) else None
        finally:
            lease.return_boundary = sys._getframe()
            self._complete_lease(lease)

    def _run_round(self, generation: int) -> None:
        if not self._is_current(generation):
            return
        if not self.motion.wake(generation):
            return
        if not self._is_current(generation):
            return
        provider = self._provider(generation)
        if provider is None:
            return
        intro = (
            "I'll look around for a good object."
            if self.machine.language == "en"
            else "Ik ga rondkijken naar een leuk voorwerp."
        )
        self._speak(provider, intro, generation)
        if not self._is_current(generation):
            return
        frames = self.motion.search(generation)
        if not frames or not self.machine.transition(GameState.SELECTING, generation, "Choosing…"):
            return
        target = self._call_if_current(
            generation,
            lambda: provider.select_target(
                frames,
                language=self.machine.language,
                age_band=self.machine.age_band,
            ),
        )
        # Drop all frame references before any speech or waiting for guesses.
        frames.clear()
        if target is None or not self._is_current(generation) or not self.machine.select(target, generation):
            return
        self.motion.neutral(generation)
        self._speak(provider, self.machine.message, generation)

    def _handle_guess(self, event: GameEvent) -> None:
        if not self._is_current(event.generation):
            return
        guess = self.machine.record_guess_if_current(event.generation)
        if guess is None:
            return
        count, target = guess
        provider = self._provider(event.generation)
        if provider is None:
            return
        if count % 2 == 0:
            frame = self.motion.capture_target(target.frame_index, event.generation)
            present = (
                None
                if frame is None
                else self._call_if_current(
                    event.generation,
                    lambda: provider.target_present(frame, target),
                )
            )
            if present is None:
                return
            if not present:
                escaped = (
                    "That object escaped. I'll choose another one."
                    if self.machine.language == "en"
                    else "Dat voorwerp is ontsnapt. Ik kies een nieuwe."
                )
                self._speak(provider, escaped, event.generation)
                with self._ownership_lock:
                    new_generation = self.machine.restart_if_current(event.generation)
                    if new_generation is not None:
                        self._replace_pending(GameEvent("start", new_generation))
                return
        matched = self._call_if_current(
            event.generation,
            lambda: provider.judge_guess(
                event.value,
                target,
                language=self.machine.language,
            ),
        )
        if matched is None:
            return
        if not self._is_current(event.generation):
            return
        if matched:
            self.machine.transition(
                GameState.REVEAL,
                event.generation,
                (f"Yes! It was the {target.object_name}." if self.machine.language == "en"
                 else f"Ja! Het was de {target.object_name}."),
            )
        elif count >= self.MAX_GUESSES:
            self.machine.transition(
                GameState.REVEAL,
                event.generation,
                (f"Good trying! It was the {target.object_name}." if self.machine.language == "en"
                 else f"Goed geprobeerd! Het was de {target.object_name}."),
            )
        else:
            with self._ownership_lock:
                if not self._is_current(event.generation):
                    return
                hint = self.machine.next_hint()
                self.machine.message = (
                    f"Nice guess. Here is a hint: {hint}"
                    if self.machine.language == "en"
                    else f"Goede gok. Hier is een hint: {hint}"
                )
        self._speak(provider, self.machine.message, event.generation)

    def _speak(self, provider: ProviderClient, text: str, generation: int) -> None:
        if not self._is_current(generation):
            return
        wav_data = self._call_if_current(
            generation,
            lambda: provider.synthesize_wav(text, language=self.machine.language),
        )
        if wav_data is None or not self._is_current(generation):
            return
        try:
            with wave.open(io.BytesIO(wav_data), "rb") as wav_file:
                if wav_file.getsampwidth() != 2 or wav_file.getnchannels() not in {1, 2}:
                    raise ProviderError("TTS returned an unsupported WAV format")
                rate = wav_file.getframerate()
                samples = np.frombuffer(wav_file.readframes(wav_file.getnframes()), dtype="<i2")
                channels = wav_file.getnchannels()
                if channels == 2:
                    samples = samples.reshape(-1, 2)
                audio = samples.astype(np.float32) / 32768.0
        except (wave.Error, EOFError) as exc:
            raise ProviderError("TTS returned invalid WAV audio") from exc
        media_lease = self._acquire_lease(generation)
        if media_lease is None:
            return
        try:
            output_rate = self._run_untrusted(
                media_lease,
                self.robot.media.get_output_audio_samplerate,  # type: ignore[attr-defined]
            )
        finally:
            self._release_lease(media_lease)
            media_lease.return_boundary = sys._getframe()
            self._complete_lease(media_lease)
        if output_rate is None:
            return
        if output_rate <= 0:
            raise ProviderError("Reachy audio output is unavailable")
        if rate != output_rate:
            divisor = math.gcd(rate, output_rate)
            audio = resample_poly(
                audio, output_rate // divisor, rate // divisor, axis=0
            ).astype(np.float32)
            rate = output_rate
        lease = self._acquire_lease(generation)
        if lease is None:
            return
        try:
            with self._audio_lock:
                self._audio_epoch += 1
                playback_epoch = self._audio_epoch

            def start_playback() -> tuple[object, object, str]:
                if self._media_positive_quiescence_path() is None:
                    raise PrivacySafeRuntimeError("media_quiescence_unavailable")
                self.robot.media.start_playing()  # type: ignore[attr-defined]
                # start_playing() may replace the pipeline. Bind silence to the
                # transport that will actually accept the subsequent sample,
                # never to the stale preflight object.
                try:
                    actual_path = self._media_positive_quiescence_path()
                except BaseException as exc:
                    self._media_cleanup_failed.set()
                    self._bounded_media_stop()
                    raise PrivacySafeRuntimeError("media_quiescence_unavailable") from exc
                if actual_path is None:
                    self._media_cleanup_failed.set()
                    self._bounded_media_stop()
                    raise PrivacySafeRuntimeError("media_quiescence_unavailable")
                with self._audio_lock:
                    self._audio_quiescence_path = actual_path
                if not self._lease_is_current(lease):
                    self._bounded_media_stop()
                return actual_path

            started = self._run_untrusted_media_start(
                lease,
                start_playback,
            )
            if not isinstance(started, tuple) or len(started) != 3 or not self._lease_is_current(lease):
                self._bounded_media_stop()
                return
            with self._audio_lock:
                publication_revoked = not self._lease_is_current(lease) or playback_epoch != self._audio_epoch
                if not publication_revoked:
                    self._audio_active_epoch = playback_epoch
            if publication_revoked:
                # Media helpers may acquire _audio_lock and invoke blocking or
                # reentrant backend code. Never call one while holding it.
                self._bounded_media_stop()
                return
            def push_audio() -> bool:
                self.robot.media.push_audio_sample(audio)  # type: ignore[attr-defined]
                if not self._lease_is_current(lease):
                    self._bounded_media_stop()
                return True

            pushed = self._run_untrusted(lease, push_audio)
            if pushed is not True or not self._lease_is_current(lease):
                self._bounded_media_stop()
                return
            duration = len(audio) / rate
            deadline = time.monotonic() + duration
            # Yield once after publication so a concurrent Stop can observe and
            # synchronously silence active playback before this wrapper polls.
            time.sleep(0)
            while time.monotonic() < deadline:
                if lease.revoked is not None and lease.revoked.wait(
                    min(0.05, max(0.0, deadline - time.monotonic()))
                ):
                    self._bounded_media_stop()
                    return
                if not self._lease_is_current(lease):
                    self._bounded_media_stop()
                    return
        finally:
            with self._audio_lock:
                if "playback_epoch" in locals() and self._audio_active_epoch == playback_epoch:
                    self._audio_active_epoch = None
            self._release_lease(lease)
            lease.return_boundary = sys._getframe()
            self._complete_lease(lease)
