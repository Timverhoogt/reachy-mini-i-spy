from __future__ import annotations

import pytest

from reachy_mini_i_spy.game import GameMachine, GameState, validate_target


def candidate(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "object_name": "wooden chair",
        "colour": "blue",
        "category": "furniture",
        "location": "beside the table",
        "frame_index": 1,
        "bbox": [0.2, 0.2, 0.3, 0.4],
        "confidence": 0.91,
        "stable": True,
        "visible_frame_count": 2,
        "hints_en": ["You can sit on it", "It has legs"],
        "hints_nl": ["Je kunt erop zitten", "Het heeft poten"],
    }
    value.update(changes)
    return value


def test_camera_consent_is_required_and_starts_off() -> None:
    machine = GameMachine()
    assert machine.snapshot()["camera_active"] is False
    with pytest.raises(PermissionError):
        machine.start(language="en", age_band="7-9", camera_consent=False)
    assert machine.snapshot()["state"] == GameState.IDLE


def test_late_generation_cannot_select_or_transition() -> None:
    machine = GameMachine()
    generation = machine.start(language="en", age_band="7-9", camera_consent=True)
    machine.transition(GameState.SELECTING, generation)
    machine.stop()
    target = validate_target(candidate(), frame_count=3)
    assert machine.select(target, generation) is False
    assert machine.snapshot()["state"] == GameState.STOPPED
    assert machine.snapshot()["target"] is None
    assert machine.snapshot()["camera_active"] is False


def test_stop_between_current_check_and_round_restart_cannot_reenable_camera() -> None:
    machine = GameMachine()
    generation = machine.start(language="en", age_band="7-9", camera_consent=True)

    assert machine.is_current(generation)
    machine.stop()

    assert machine.restart_if_current(generation) is None
    assert machine.camera_consent is False
    assert machine.state == GameState.STOPPED


def test_stop_between_current_check_and_guess_record_cannot_change_stopped_state() -> None:
    machine = GameMachine()
    generation = machine.start(language="en", age_band="7-9", camera_consent=True)
    machine.transition(GameState.SELECTING, generation)
    assert machine.select(validate_target(candidate(), frame_count=3), generation)

    assert machine.is_current(generation)
    machine.stop()

    assert machine.record_guess_if_current(generation) is None
    assert machine.fail_if_current(generation, "stale guess failed") is None
    assert machine.camera_consent is False
    assert machine.state == GameState.STOPPED


def test_target_is_retained_across_hints_and_hidden_until_reveal() -> None:
    machine = GameMachine()
    generation = machine.start(language="nl", age_band="4-6", camera_consent=True)
    machine.transition(GameState.SELECTING, generation)
    target = validate_target(candidate(), frame_count=3)
    assert machine.select(target, generation)
    assert machine.next_hint() == "Je kunt erop zitten"
    machine.record_guess()
    assert machine.target is target
    assert machine.snapshot()["target"] == {"colour": "blue", "confidence": 0.91}
    machine.transition(GameState.REVEAL, generation)
    assert machine.snapshot()["target"]["object_name"] == "wooden chair"  # type: ignore[index]


@pytest.mark.parametrize(
    "changes",
    [
        {"object_name": "person"},
        {"category": "medicine"},
        {"colour": "teal"},
        {"confidence": 0.5},
        {"bbox": [0.1, 0.1, 0.05, 0.05]},
        {"stable": False},
        {"visible_frame_count": 1},
        {"frame_index": 4},
        {"hints_en": ["Look at the face"]},
    ],
)
def test_unsafe_or_unclear_targets_fail_closed(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        validate_target(candidate(**changes), frame_count=3)
