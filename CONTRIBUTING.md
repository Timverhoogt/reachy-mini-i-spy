# Contributing

Contributions are welcome, particularly language support, tests, accessibility, and privacy/safety improvements.

## Before changing behavior

Read:

- [Architecture](docs/ARCHITECTURE.md)
- [Safety contract](docs/SAFETY_CONTRACT.md)
- [Adding a language](docs/ADDING_A_LANGUAGE.md)
- [Release provenance](docs/RELEASE_PROVENANCE.md)

Changes to target policy should be reviewed against the integrated [Reachy Mini Hermes](https://github.com/Timverhoogt/reachy-mini-hermes) implementation. Do not silently weaken a rejection rule to accommodate provider output.

## Development gates

```sh
uv sync --extra dev
uv run pytest
uv run ruff check .
uv build
uv run python scripts/check_artifacts.py dist/*
reachy-mini-app-assistant check .
```

Never commit credentials, local configuration, captured frames, audio, transcripts, child information, hostnames, IP addresses, or acceptance artifacts from an unreviewed build.

Motion, camera timing, language, audio, or hardware changes require supervised physical acceptance before release. Pull-request checks are necessary but not sufficient evidence for embodied safety.
