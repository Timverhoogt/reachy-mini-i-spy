"""Deterministic parity policy; no models, media or robot required."""

from __future__ import annotations

from dataclasses import replace

import pytest
from test_game import candidate

from reachy_mini_i_spy.game import (
    GameMachine,
    GameState,
    candidate_order,
    guess_intent,
    object_key,
    validate_candidate_pool,
    validate_target,
)


def active(language="en", hints=None):
    machine = GameMachine()
    generation = machine.start(language=language, age_band="7-9", camera_consent=True)
    machine.transition(GameState.SELECTING, generation)
    target = validate_target(candidate(**(hints or {})), frame_count=3)
    assert machine.select(target, generation)
    return machine, generation, target


@pytest.mark.parametrize(
    "text,intent",
    [
        ("hint", "hint"),
        ("a hint please!", "hint"),
        ("help", "hint"),
        ("een hint", "hint"),
        ("help me", "hint"),
        ("mag ik een hint", "hint"),
        ("I don't know", "give_up"),
        ("I give up", "give_up"),
        ("geen idee", "give_up"),
        ("ik weet het niet", "give_up"),
        ("ik geef op", "give_up"),
        ("a hint book", "guess"),
        ("a blue chair", "guess"),
    ],
)
def test_intents_are_whole_commands(text, intent):
    assert guess_intent(text) == intent


@pytest.mark.parametrize("language", ["en", "nl"])
def test_three_real_guesses_reveal_without_switching_target(language):
    machine, generation, target = active(language)
    for index in range(3):
        result = machine.reply_if_current(generation, intent="guess", matched=False)
        assert result is not None
        assert machine.guess_count == index + 1
        assert machine.target is target
        assert machine.state == (GameState.REVEAL if index == 2 else GameState.GUESSING)
    assert target.object_name in machine.message
    assert machine.reply_if_current(generation, intent="guess", matched=False) is None


def test_hints_deduplicate_punctuation_case_whitespace_and_colour_reminder():
    machine, generation, _ = active(hints={"hints_en": ["It is blue!", " IT is BLUE. ", "You sit on it."]})
    one = machine.reply_if_current(generation, intent="hint")
    two = machine.reply_if_current(generation, intent="hint")
    assert one != two
    assert machine.guess_count == 0
    # Duplicate colour reminder must not become a third hint.
    three = machine.reply_if_current(generation, intent="hint")
    assert three is not None and machine.state == GameState.REVEAL
    assert machine.guess_count == 0


def test_give_up_reveals_and_stale_reply_cannot_change_stop():
    machine, generation, target = active()
    assert machine.reply_if_current(generation, intent="give_up") is not None
    assert machine.state == GameState.REVEAL
    assert machine.guess_count == 0
    assert target.object_name in machine.message
    machine.stop()
    assert machine.reply_if_current(generation, intent="hint") is None
    assert machine.target is None and machine.camera_consent is False


def test_recent_keys_are_bounded_private_preserved_across_rounds_and_cleared_on_stop():
    machine, generation, target = active()
    assert machine.recent_keys() == (object_key(target.object_name),)
    for name in ["plant", "cup", "table", "vase"]:
        generation = machine.start(language="en", age_band="7-9", camera_consent=True)
        machine.transition(GameState.SELECTING, generation)
        machine.select(replace(target, object_name=name), generation)
    assert machine.recent_keys() == ("cup", "table", "vase")
    assert "recent_keys" not in machine.snapshot()
    machine.stop()
    assert machine.recent_keys() == ()


def test_pool_validates_all_entries_and_prefers_unseen_candidates():
    values = [
        candidate(object_name="chair"),
        candidate(object_name="phone"),
        candidate(object_name="plant", frame_index=2),
    ]
    pool = validate_candidate_pool({"candidates": values}, frame_count=3)
    assert {item.object_name for item in pool} == {"chair", "plant"}
    ordered = candidate_order(pool, recent=(object_key("chair"),), shuffle=lambda items: items.reverse())
    assert ordered[0].object_name == "plant"
    assert candidate_order(pool[:1], recent=(object_key("chair"),))[0] == pool[0]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"candidates": []},
        {"candidates": [candidate()] * 5},
        candidate(),
        {"candidates": [candidate(object_name="phone")]},
        {"candidates": "no"},
    ],
)
def test_invalid_empty_oversized_and_legacy_pools_fail_closed(payload):
    with pytest.raises(ValueError):
        validate_candidate_pool(payload, frame_count=3)


def test_pool_deduplicates_same_name_and_same_box_without_truncating_before_validation():
    pool = validate_candidate_pool(
        {
            "candidates": [
                candidate(object_name="chair"),
                candidate(object_name="the chair"),
                candidate(object_name="blue seat"),
                candidate(object_name="plant", frame_index=2),
            ]
        },
        frame_count=3,
    )
    assert len(pool) == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"confidence": float("nan")},
        {"confidence": True},
        {"bbox": [0.2, float("nan"), 0.3, 0.4]},
        {"frame_index": True},
        {"visible_frame_count": 2.8},
    ],
)
def test_pool_cannot_admit_nonfinite_or_coerced_numeric_values(changes):
    with pytest.raises(ValueError):
        validate_candidate_pool({"candidates": [candidate(**changes)]}, frame_count=3)
