"""Verify standalone model notices and reject broker code or plausible secrets."""

from __future__ import annotations

import hashlib
import re
import sys
import tarfile
import zipfile
from pathlib import Path

SECRET_PATTERNS = (
    re.compile(rb"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(rb'(?i)(openai_api_key|anthropic_api_key)\s*["\'=:\s]+[A-Za-z0-9_-]{16,}'),
)
FORBIDDEN_MEMBERS = (
    "hermes_broker/",
    "deploy/",
    "reachy_mini_i_spy/auth.py",
    ".env",
    "config.json",
    "secrets.json",
)
DETECTOR_SUFFIX = "reachy_mini_i_spy/models/ssdlite320_mobilenet_v3_large_coco.onnx"
DETECTOR_SHA256 = "ee09b0d9f02d938780280eea55794275d592782bdcfd1c1b88aa8308cddc120f"
REQUIRED_SUFFIXES = (
    DETECTOR_SUFFIX,
    "reachy_mini_i_spy/models/README.md",
    "reachy_mini_i_spy/models/TORCHVISION_LICENSE.txt",
)


def members(path: Path) -> list[tuple[str, bytes]]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return [(name, archive.read(name)) for name in archive.namelist() if not name.endswith("/")]
    with tarfile.open(path) as archive:
        result = []
        for member in archive.getmembers():
            handle = archive.extractfile(member) if member.isfile() else None
            if handle is not None:
                result.append((member.name, handle.read()))
        return result


def main() -> int:
    paths = [Path(value) for value in sys.argv[1:]]
    if not paths:
        raise SystemExit("usage: check_artifacts.py DIST...")
    failures: list[str] = []
    canary = __import__("os").environ.get("ISPY_SECRET_CANARY", "").encode()
    for path in paths:
        artifact_members = members(path)
        normalized_members = {name.replace("\\", "/"): data for name, data in artifact_members}
        for suffix in REQUIRED_SUFFIXES:
            if not any(name.endswith(suffix) for name in normalized_members):
                failures.append(f"{path}: required standalone asset missing: {suffix}")
        for name, data in normalized_members.items():
            normalized = name.replace("\\", "/")
            if any(part in normalized for part in FORBIDDEN_MEMBERS):
                failures.append(f"{path}: forbidden member {name}")
            if any(pattern.search(data) for pattern in SECRET_PATTERNS) or (canary and canary in data):
                failures.append(f"{path}: secret-like content in {name}")
            if normalized.endswith(DETECTOR_SUFFIX) and hashlib.sha256(data).hexdigest() != DETECTOR_SHA256:
                failures.append(f"{path}: local detector checksum mismatch")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"secret/package boundary check passed for {len(paths)} artifact(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
