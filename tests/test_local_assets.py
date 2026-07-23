from __future__ import annotations

import hashlib
import io
import shutil
import tarfile
from pathlib import Path

import pytest

from reachy_mini_i_spy import local_assets
from reachy_mini_i_spy.local_assets import VoiceAsset


def _voice_archive(path: Path, root: str, model: str) -> None:
    entries = {
        f"{root}/{model}": b"model",
        f"{root}/tokens.txt": b"token 1\n",
        f"{root}/espeak-ng-data/en_dict": b"dictionary",
    }
    with tarfile.open(path, "w:bz2") as bundle:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            bundle.addfile(info, io.BytesIO(data))


def test_local_asset_installer_marks_only_complete_atomic_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "fixture.tar.bz2"
    root = "voice-test"
    model = "voice.onnx"
    _voice_archive(archive, root, model)
    asset = VoiceAsset("en", "https://fixed.invalid/voice", "a" * 64, root, model)
    monkeypatch.setenv("REACHY_MINI_I_SPY_MODEL_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(local_assets, "VOICE_ASSETS", {"en": asset})
    monkeypatch.setattr(local_assets, "_download", lambda _asset, destination: shutil.copyfile(archive, destination))

    assert local_assets.local_assets_status()["ready"] is False
    status = local_assets.install_local_assets()
    assert status["ready"] is True
    installed = local_assets.model_cache() / root
    assert (installed / model).read_bytes() == b"model"
    assert (installed / ".archive-sha256").read_text().strip() == "a" * 64
    assert not list(local_assets.model_cache().glob("ispy-model-*"))


def test_safe_extract_rejects_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.tar.bz2"
    with tarfile.open(archive, "w:bz2") as bundle:
        data = b"no"
        info = tarfile.TarInfo("../escape")
        info.size = len(data)
        bundle.addfile(info, io.BytesIO(data))
    with pytest.raises(ValueError, match="unsafe member"):
        local_assets._safe_extract(archive, tmp_path / "out", "voice")


def test_download_rejects_checksum_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = b"not the expected archive"
    asset = VoiceAsset("en", "https://fixed.invalid/voice", hashlib.sha256(b"different").hexdigest(), "v", "m")
    monkeypatch.setattr(local_assets.urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(payload))
    with pytest.raises(ValueError, match="checksum"):
        local_assets._download(asset, tmp_path / "archive")


def test_download_bytes_are_derived_from_missing_asset_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    detector = tmp_path / "detector.onnx"
    detector.write_bytes(b"model")
    assets = {
        "en": VoiceAsset("en", "https://fixed.invalid/en", "a" * 64, "en", "en.onnx", 11),
        "nl": VoiceAsset("nl", "https://fixed.invalid/nl", "b" * 64, "nl", "nl.onnx", 22),
    }
    monkeypatch.setattr(local_assets, "VOICE_ASSETS", assets)
    monkeypatch.setattr(local_assets, "detector_path", lambda: detector)
    monkeypatch.setattr(local_assets, "_detector_ready", lambda: True)
    monkeypatch.setattr(local_assets, "_voice_ready", lambda asset: asset.language == "en")
    assert local_assets.local_assets_status()["download_bytes"] == 22
