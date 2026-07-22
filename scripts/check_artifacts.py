"""Fail if Reachy distribution artifacts contain broker code or plausible secrets."""

from __future__ import annotations

import re
import sys
import tarfile
import zipfile
from pathlib import Path

SECRET_PATTERNS = (
    re.compile(rb"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(rb'(?i)(openai_api_key|anthropic_api_key)\s*["\'=:\s]+[A-Za-z0-9_-]{16,}'),
)
FORBIDDEN_MEMBERS = ("hermes_broker/", "deploy/", ".env", "config.json", "secrets.json")


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
        for name, data in members(path):
            normalized = name.replace("\\", "/")
            if any(part in normalized for part in FORBIDDEN_MEMBERS):
                failures.append(f"{path}: forbidden member {name}")
            if any(pattern.search(data) for pattern in SECRET_PATTERNS) or (canary and canary in data):
                failures.append(f"{path}: secret-like content in {name}")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"secret/package boundary check passed for {len(paths)} artifact(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
