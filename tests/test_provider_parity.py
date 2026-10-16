from __future__ import annotations

import json
from copy import deepcopy

import pytest
from PIL import Image
from test_local_provider import _provider
from test_provider import Opener, client, completion, moderation, safe_candidate

from reachy_mini_i_spy import provider as provider_module
from reachy_mini_i_spy.game import object_key
from reachy_mini_i_spy.local_provider import Detection
from reachy_mini_i_spy.provider import TARGET_POOL_SCHEMA, ProviderError


def pool():
    chair = safe_candidate()
    plant = {**chair, "object_name": "plant", "bbox": [0.6, 0.2, 0.2, 0.4]}
    return {"candidates": [chair, plant]}


def test_cloud_variety_prefers_unseen_but_retains_one_safe_choice():
    opener = Opener([completion(pool()), moderation()])
    selected = client(opener).select_target(
        [b"synthetic"], language="en", age_band="7-9", recent_keys=(object_key("chair"),)
    )
    assert selected.object_name == "plant"
    single = Opener([completion({"candidates": [safe_candidate()]}), moderation()])
    assert (
        client(single).select_target([b"synthetic"], language="en", age_band="7-9", recent_keys=("chair",)).object_name
        == "chair"
    )


def test_cloud_language_schema_and_prompt_are_local_and_match_final_wire():
    before = deepcopy(TARGET_POOL_SCHEMA)
    value = {**safe_candidate(), "object_name": "stoel"}
    opener = Opener([completion({"candidates": [value]}), moderation()])
    assert client(opener).select_target([b"synthetic"], language="nl", age_band="4-6").object_name == "stoel"
    wire = json.loads(opener.requests[0][0].data)
    schema = wire["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["candidates"]["maxItems"] == 4
    assert schema["properties"]["candidates"]["items"]["required"]
    assert "Dutch" in schema["properties"]["candidates"]["items"]["properties"]["object_name"]["description"]
    assert "object_name MUST be in Dutch" in wire["messages"][0]["content"][0]["text"]
    assert before == TARGET_POOL_SCHEMA


def test_definite_content_refusal_tries_other_validated_candidate(monkeypatch):
    monkeypatch.setattr(provider_module, "candidate_order", lambda targets, **_: targets)
    opener = Opener([completion(pool()), moderation(flagged=True), moderation()])
    assert client(opener).select_target([b"synthetic"], language="en", age_band="7-9").object_name == "plant"


@pytest.mark.parametrize("bad", [{"results": [{"flagged": "false"}]}, {"results": []}, {}])
def test_malformed_moderation_never_becomes_candidate_fallback(monkeypatch, bad):
    monkeypatch.setattr(provider_module, "candidate_order", lambda targets, **_: targets)
    opener = Opener([completion(pool()), bad, moderation()])
    with pytest.raises(ProviderError):
        client(opener).select_target([b"synthetic"], language="en", age_band="7-9")
    assert len(opener.requests) == 2


def test_old_single_target_envelope_is_rejected():
    opener = Opener([completion(safe_candidate())])
    with pytest.raises(ProviderError, match="pool"):
        client(opener).select_target([b"synthetic"], language="en", age_band="7-9")


def test_stop_during_final_candidate_moderation_cannot_return_a_target(monkeypatch):
    instance = client(Opener([completion(pool())]))
    monkeypatch.setattr(instance, "moderate", lambda _text: instance.cancel())
    with pytest.raises(ProviderError, match="cancelled"):
        instance.select_target([b"synthetic"], language="en", age_band="7-9")


def test_local_variety_and_dutch_name_keep_class_based_guess_and_presence(monkeypatch):
    instance = _provider()
    image = Image.new("RGB", (320, 320), (40, 170, 45))
    monkeypatch.setattr(
        instance,
        "_detect",
        lambda frame, index: (
            image,
            [
                Detection(56, 0.85, (0.1, 0.2, 0.3, 0.5), index),
                Detection(58, 0.65, (0.6, 0.2, 0.2, 0.4), index),
            ],
        ),
    )
    plant = instance.select_target([b"a", b"b", b"c"], language="en", age_band="7-9", recent_keys=("chair",))
    assert plant.object_name == "plant"
    chair = instance.select_target([b"a", b"b", b"c"], language="nl", age_band="7-9", recent_keys=("plant",))
    assert chair.object_name == "stoel"
    assert instance.judge_guess("stoel", chair, language="nl") is True
    assert instance.target_present(b"fresh", chair) is True


def test_local_skips_invalid_best_candidate_and_checks_remaining_objects(monkeypatch):
    instance = _provider()
    image = Image.new("RGB", (320, 320), (40, 170, 45))
    monkeypatch.setattr(
        instance,
        "_detect",
        lambda frame, index: (
            image,
            [
                Detection(56, 0.99, (0.1, 0.2, 0.01, 0.01), index),
                Detection(58, 0.65, (0.6, 0.2, 0.2, 0.4), index),
            ],
        ),
    )
    assert instance.select_target([b"a", b"b", b"c"], language="en", age_band="7-9").object_name == "plant"


def test_local_moderation_uses_same_plural_phrase_policy():
    for word in ("credit cards", "phones", "messen", "children"):
        with pytest.raises(RuntimeError, match="not approved"):
            _provider().moderate(word)
