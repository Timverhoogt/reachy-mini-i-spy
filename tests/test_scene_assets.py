"""Pin the dependency-free scene import and verify its packaged resource boundary."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "reachy_mini_i_spy" / "static"


@pytest.mark.parametrize(
    ("name", "digest"),
    [
        ("scene.js", "796ab6eeeca8aa87fa10d908107e402b548629fd190ba79ffcb3b6a616e71795"),
        ("scene.css", "8670d3f4469bac4d6d49bfac73c24669e98bf97fa6cba8efa68025aa26d8c9d4"),
    ],
)
def test_vendored_homebody_scene_bytes(name: str, digest: str) -> None:
    assert hashlib.sha256((STATIC / name).read_bytes()).hexdigest() == digest


def test_scene_adapter_is_view_only_and_does_not_read_private_game_text() -> None:
    adapter = (STATIC / "ispy-scene.js").read_text()
    for forbidden in ("fetch(", "localStorage", "sessionStorage", "game.target", "game.message", "game.guess"):
        assert forbidden not in adapter
    html = (STATIC / "index.html").read_text()
    assert html.index("/static/scene.js?v=1") < html.index("/static/ispy-scene.js?v=1")
    assert html.index("/static/ispy-scene.js?v=1") < html.index("/static/main.js?v=1")
    assert 'id="game-scene"' in html
