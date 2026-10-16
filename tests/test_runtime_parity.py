from __future__ import annotations

import threading

import pytest
from test_game import candidate
from test_runtime import FakeRobot

from reachy_mini_i_spy.game import GameState, validate_target
from reachy_mini_i_spy.provider import ProviderError
from reachy_mini_i_spy.runtime import GameEvent, GameRuntime


class PolicyProvider:
    def __init__(self):
        self.calls = []
        self.error = False
        self.entered = None
        self.release = None

    def judge_guess(self, text, target, **kwargs):
        self.calls.append("judge")
        if self.entered:
            self.entered.set()
            assert self.release.wait(5)
        if self.error:
            raise ProviderError("synthetic bounded failure")
        return False

    def moderate(self, text):
        self.calls.append("moderate")
        if self.entered:
            self.entered.set()
            assert self.release.wait(5)

    def target_present(self, frame, target):
        self.calls.append("present")
        return True


def active_runtime(monkeypatch, language="en"):
    robot = FakeRobot()
    runtime = GameRuntime(robot, threading.Event())
    generation = runtime.machine.start(language=language, age_band="7-9", camera_consent=True)
    runtime.machine.transition(GameState.SELECTING, generation)
    name = "stoel" if language == "nl" else "chair"
    target = validate_target(candidate(object_name=name), frame_count=3)
    assert runtime.machine.select(target, generation)
    provider = PolicyProvider()
    spoken = []
    captures = []
    monkeypatch.setattr(runtime, "_provider", lambda _generation: provider)
    monkeypatch.setattr(runtime, "_speak", lambda _provider, text, _generation: spoken.append(text))
    monkeypatch.setattr(runtime.motion, "capture_target", lambda *_: (captures.append(True), b"fresh")[1])
    return runtime, generation, target, provider, spoken, captures


@pytest.mark.parametrize("language", ["en", "nl"])
def test_runtime_three_guesses_reveal_clear_target_and_fold(monkeypatch, language):
    runtime, gen, target, provider, spoken, captures = active_runtime(monkeypatch, language)
    for _ in range(3):
        runtime._handle_guess(GameEvent("guess", gen, "table"))
    assert len(captures) == 1  # only second meaningful guess
    assert provider.calls.count("judge") == 3
    assert runtime.machine.guess_count == 3
    assert runtime.machine.state == GameState.REVEAL
    assert runtime.machine.target is None
    assert runtime.machine.camera_consent is False
    assert target.object_name in spoken[-1]
    assert runtime.motion.status()["motor_state"] == "asleep"
    assert runtime.robot.motor_mode == "disabled"
    assert runtime.machine.recent_keys()  # remains bounded RAM-only until Stop/error


@pytest.mark.parametrize("text", ["hint", "een hint", "I give up", "ik weet het niet"])
def test_commands_never_use_attempt_judge_or_camera(monkeypatch, text):
    runtime, gen, _target, provider, spoken, captures = active_runtime(monkeypatch)
    runtime._handle_guess(GameEvent("guess", gen, text))
    assert runtime.machine.guess_count == 0
    assert provider.calls == ["moderate"]
    assert not captures and spoken


def test_judge_failure_does_not_spend_guess_or_end_the_round(monkeypatch):
    runtime, gen, target, provider, spoken, captures = active_runtime(monkeypatch)
    provider.error = True
    runtime._handle_guess(GameEvent("guess", gen, "table"))
    assert runtime.machine.guess_count == 0
    assert runtime.machine.target is target
    assert runtime.machine.state == GameState.GUESSING
    assert "try again" in spoken[-1]
    assert not captures


@pytest.mark.parametrize("command", ["table", "hint", "I give up"])
def test_stop_during_held_judge_or_command_moderation_discards_result(monkeypatch, command):
    runtime, gen, _target, provider, spoken, captures = active_runtime(monkeypatch)
    provider.entered = threading.Event()
    provider.release = threading.Event()
    errors = []

    def run():
        try:
            runtime._handle_guess(GameEvent("guess", gen, command))
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=run)
    worker.start()
    assert provider.entered.wait(2)
    try:
        runtime.stop()
    finally:
        provider.release.set()
        worker.join(5)
    assert not worker.is_alive() and not errors
    assert runtime.machine.state == GameState.STOPPED
    assert runtime.machine.target is None and not runtime.machine.camera_consent
    assert not spoken and not captures


def test_start_cannot_enter_terminal_fold_cleanup(monkeypatch):
    runtime, gen, _target, _provider, _spoken, _captures = active_runtime(monkeypatch)
    runtime._ready.set()
    runtime.machine.reply_if_current(gen, intent="give_up")
    entered, release = threading.Event(), threading.Event()
    original = runtime.motion.fold_and_disable

    def held_fold():
        entered.set()
        assert release.wait(5)
        return original()

    monkeypatch.setattr(runtime.motion, "fold_and_disable", held_fold)
    worker = threading.Thread(target=lambda: runtime._finish_round(gen))
    worker.start()
    assert entered.wait(2)
    try:
        assert runtime.snapshot()["cleanup_pending"] is True
        with pytest.raises(RuntimeError, match="cleanup"):
            runtime.start("en", "7-9", True)
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert runtime.snapshot()["cleanup_pending"] is False
    assert runtime.start("en", "7-9", True)["state"] == GameState.SEARCHING
