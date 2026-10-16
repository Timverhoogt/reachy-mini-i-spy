"""Regression for a live second-guess recheck unexpectedly replacing the round."""
from __future__ import annotations

import pytest
from test_runtime_parity import active_runtime

from reachy_mini_i_spy.game import GameState
from reachy_mini_i_spy.runtime import GameEvent


@pytest.mark.parametrize("language", ["en", "nl"])
def test_three_guesses_keep_exact_target_without_camera_rechecks(monkeypatch, language):
    runtime, generation, target, provider, spoken, captures = active_runtime(monkeypatch, language)

    def unexpected_capture(*_args):
        pytest.fail("Ordinary guesses must not capture or restart target selection")

    monkeypatch.setattr(runtime.motion, "capture_target", unexpected_capture)
    monkeypatch.setattr(provider, "target_present", lambda *_args: False)
    for attempt in range(1, 4):
        runtime._handle_guess(GameEvent("guess", generation, "spaceship"))
        assert runtime.machine.guess_count == attempt
        if attempt < 3:
            assert runtime.machine.state == GameState.GUESSING
            assert runtime.machine.current_target(generation) is target
            assert runtime.machine.is_current(generation)
    assert provider.calls == ["judge"] * 3
    assert not captures
    assert runtime.machine.state == GameState.REVEAL
    assert runtime.machine.target is None
    assert not runtime.machine.camera_consent
    assert target.object_name in spoken[-1]
    assert runtime.motion.status()["motor_state"] == "asleep"
    assert runtime.motion._motor_mode() == "disabled"
