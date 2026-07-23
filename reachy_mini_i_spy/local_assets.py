"""Pinned, verified model assets for no-key local I Spy mode."""

from __future__ import annotations

import hashlib
import os
import shutil
import tarfile
import tempfile
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from pathlib import Path

DETECTOR_SHA256 = "c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d"
_MAX_ARCHIVE_BYTES = 80_000_000
_MAX_EXTRACTED_BYTES = 180_000_000


@dataclass(frozen=True)
class VoiceAsset:
    language: str
    url: str
    sha256: str
    directory: str
    model: str


VOICE_ASSETS = {
    "en": VoiceAsset(
        language="en",
        url=(
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/"
            "vits-piper-en_US-ljspeech-medium-int8.tar.bz2"
        ),
        sha256="24dc3bd77dd48c291e52c297878d3437c9492f245d823d7f6a06c4bbb67f4b6b",
        directory="vits-piper-en_US-ljspeech-medium-int8",
        model="en_US-ljspeech-medium.onnx",
    ),
    "nl": VoiceAsset(
        language="nl",
        url=(
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/"
            "vits-piper-nl_BE-nathalie-x_low-int8.tar.bz2"
        ),
        sha256="5cf6ae08ab9339455433f1718896b403622a1bdd9f911f1189421d0df34d83f3",
        directory="vits-piper-nl_BE-nathalie-x_low-int8",
        model="nl_BE-nathalie-x_low.onnx",
    ),
}


def model_cache() -> Path:
    override = os.environ.get("REACHY_MINI_I_SPY_MODEL_CACHE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".cache" / "reachy-mini-i-spy" / "models"


def detector_path() -> Path:
    return Path(str(files("reachy_mini_i_spy").joinpath("models/yolox_nano.onnx")))


def voice_path(language: str) -> Path:
    asset = VOICE_ASSETS[language]
    return model_cache() / asset.directory


def _voice_ready(asset: VoiceAsset) -> bool:
    root = model_cache() / asset.directory
    marker = root / ".archive-sha256"
    try:
        return (
            marker.is_file()
            and marker.read_text(encoding="ascii").strip() == asset.sha256
            and (root / asset.model).is_file()
            and (root / "tokens.txt").is_file()
            and (root / "espeak-ng-data").is_dir()
        )
    except (OSError, UnicodeError):
        return False


@lru_cache(maxsize=1)
def _detector_ready() -> bool:
    detector = detector_path()
    try:
        digest = hashlib.sha256()
        with detector.open("rb") as model_file:
            while chunk := model_file.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest() == DETECTOR_SHA256
    except OSError:
        return False


def local_assets_status() -> dict[str, object]:
    detector = detector_path()
    detector_verified = _detector_ready()
    voices = {language: _voice_ready(asset) for language, asset in VOICE_ASSETS.items()}
    return {
        "detector_present": detector.is_file(),
        "detector_verified": detector_verified,
        "voices": voices,
        "ready": detector_verified and all(voices.values()),
        "download_bytes": 34_483_424,
    }


def _download(asset: VoiceAsset, destination: Path) -> None:
    digest = hashlib.sha256()
    total = 0
    request = urllib.request.Request(asset.url, headers={"User-Agent": "reachy-mini-i-spy/0.2"})
    with urllib.request.urlopen(request, timeout=30) as response, destination.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > _MAX_ARCHIVE_BYTES:
                raise ValueError("Local voice archive exceeded its size limit")
            digest.update(chunk)
            output.write(chunk)
    if digest.hexdigest() != asset.sha256:
        raise ValueError("Local voice archive checksum did not match")


def _safe_extract(archive: Path, destination: Path, expected_root: str) -> None:
    total = 0
    with tarfile.open(archive, mode="r:bz2") as bundle:
        members = bundle.getmembers()
        if not members or len(members) > 1000:
            raise ValueError("Local voice archive has an invalid member count")
        for member in members:
            path = Path(member.name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or not path.parts
                or path.parts[0] != expected_root
                or member.issym()
                or member.islnk()
                or member.isdev()
            ):
                raise ValueError("Local voice archive contains an unsafe member")
            total += max(member.size, 0)
            if total > _MAX_EXTRACTED_BYTES:
                raise ValueError("Local voice archive exceeded its extracted size limit")
        bundle.extractall(destination, members=members, filter="data")


def install_local_assets() -> dict[str, object]:
    """Download, verify and atomically install both offline voices."""
    cache = model_cache()
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    cache.chmod(0o700)
    for asset in VOICE_ASSETS.values():
        if _voice_ready(asset):
            continue
        with tempfile.TemporaryDirectory(prefix="ispy-model-", dir=cache) as temporary:
            temporary_path = Path(temporary)
            archive = temporary_path / "voice.tar.bz2"
            _download(asset, archive)
            _safe_extract(archive, temporary_path, asset.directory)
            extracted = temporary_path / asset.directory
            if not (
                (extracted / asset.model).is_file()
                and (extracted / "tokens.txt").is_file()
                and (extracted / "espeak-ng-data").is_dir()
            ):
                raise ValueError("Local voice archive was incomplete")
            (extracted / ".archive-sha256").write_text(asset.sha256 + "\n", encoding="ascii")
            target = cache / asset.directory
            shutil.rmtree(target, ignore_errors=True)
            extracted.replace(target)
    return local_assets_status()
