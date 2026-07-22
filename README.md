# Reachy Mini I Spy

[![CI](https://github.com/Timverhoogt/reachy-mini-i-spy/actions/workflows/ci.yml/badge.svg)](https://github.com/Timverhoogt/reachy-mini-i-spy/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![Reachy Mini](https://img.shields.io/badge/Reachy_Mini-app-5b6ee1)](https://github.com/pollen-robotics/reachy_mini)

A complete standalone English/Dutch “I Spy” app for Reachy Mini. A caregiver explicitly opts in to camera use for every session; Reachy performs a gentle 5.5-second search and chooses one stable, age-appropriate household object.

Reachy Mini I Spy originated from the I Spy experience developed in [Reachy Mini Hermes](https://github.com/Timverhoogt/reachy-mini-hermes). It is now its own full application and project, with an independent Reachy app entry point, UI, dedicated provider broker, safety contract, release artifacts, issue tracker, and development lifecycle. It does not require the full Reachy Mini Hermes app on the robot. Provider credentials remain off-robot behind the standalone app’s narrow broker.

## What it does

- English and Dutch gameplay for age bands 4–6, 7–9, and 10–12.
- A documented end-to-end path for adding any other language.
- Per-session caregiver camera consent.
- Three transient in-memory viewpoints during one bounded search.
- Stable-object, colour, confidence, size, and category validation.
- Moderated guesses, hints, reveal text, and speech.
- Generation-based cancellation so Stop invalidates late network, camera, motion, and audio work.
- Automatic camera revocation, neutral return, fold, and motor disable on Stop, reveal, failure, or shutdown.

## Install

### Reachy Mini app catalog

The accepted `0.1.0` app is available as `reachy_mini_i_spy`, sourced from the public [Reachy Mini app-catalog Space](https://huggingface.co/spaces/Timbo89/reachy_mini_i_spy). Open **Apps** on Reachy Mini and search for **I Spy** or `reachy_mini_i_spy`.

The included I Spy provider broker must run on a separate trusted host. That may be the same machine that runs Hermes Agent, but neither Hermes Agent nor the Reachy Mini Hermes app is required. Follow [the broker deployment guide](deploy/README.md) before starting a game.

### Verified release artifact

The physically accepted wheel is attached to the [v0.1.0 GitHub release](https://github.com/Timverhoogt/reachy-mini-i-spy/releases/tag/v0.1.0) and is also mirrored by the app-catalog Space.

```text
reachy_mini_i_spy-0.1.0-py3-none-any.whl
SHA-256 56e821d241f323144f6b9af2baacd7eb8929ed63de641944eacd32d0d911ca6e
```

Read [release provenance](docs/RELEASE_PROVENANCE.md) before rebuilding or replacing that artifact.

## Privacy and safety

- Camera is off by default; Stop is always available and disables it.
- At most three in-memory JPEG frames are sent to the narrow I Spy broker per search.
- Frames, guesses, audio, and transcripts are not written to disk or included in this repository.
- Faces, people, bodies/clothing, screens, documents, medicine, weapons, private material, tiny objects, and unclear colours are rejected.
- Provider output and every guess are moderated; speech is checked again immediately before TTS.
- Missing moderation, vision, malformed output, stale sessions, and network failures fail closed.
- The app has no personal memory, face recognition, general agent tools, files, messaging, smart-home, or purchasing access.

Reachy stores only a broker URL, broker-bound device ID, and scoped client token in an owner-only local configuration file. Provider credentials stay on the separate broker host. Reachy cannot select provider models, URLs, prompts, or tools.

The complete normative boundary is documented in [Safety contract](docs/SAFETY_CONTRACT.md). Security reports should follow [SECURITY.md](SECURITY.md).

## Origins and related project

Reachy Mini I Spy began as a focused extraction of the I Spy experience from [Reachy Mini Hermes](https://github.com/Timverhoogt/reachy-mini-hermes). The standalone app is maintained, versioned, distributed, and physically accepted independently. Reachy Mini Hermes continues to include its own integrated Kids Mode implementation.

| Capability | Standalone I Spy | Reachy Mini Hermes |
|---|---:|---:|
| Installable without the full Hermes Reachy app | Yes | No |
| English and Dutch | Yes | Yes |
| Caregiver camera opt-in | Yes | Yes |
| Narrow provider broker | Dedicated six-route broker | Hermes child-session bridge |
| Search choreography | Three-frame, narrow search | Five-frame, ±120° desk scan |
| Alternating child/robot chooser roles | No | Yes |
| General agent or private tools during Kids Mode | No | No |

The standalone app has its own runtime and trust boundary. Some target-selection principles remain aligned with their Hermes origin, but cancellation, moderation, camera lifecycle, speech authorization, release provenance, and acceptance are application-specific. A shared safety-policy change should be reviewed in both projects without making either project’s release dependent on the other.

## Development

```sh
uv sync --extra dev
uv run pytest
uv run ruff check .
uv build
uv run python scripts/check_artifacts.py dist/*
reachy-mini-app-assistant check .
```

The Reachy SDK includes native Linux dependencies. See the CI workflow for the tested Ubuntu setup.

To add **any other language**, follow [Adding any language](docs/ADDING_A_LANGUAGE.md). The guide covers locale selection, app/API phrases, clues, provider schemas, moderation vocabulary, browser speech recognition, TTS, tests, packaging, and supervised acceptance. A modified language build requires its own moderation review and physical acceptance; acceptance of the original wheel does not transfer automatically.

## Verified release

Version `0.1.0` was physically accepted on a supervised Reachy Mini Lite using the exact wheel digest above. English and Dutch rounds both reached reveal after six guesses; camera access was revoked after reveal; authoritative Stop cleared the target and blocked late output; rollback installation was exercised; and the robot finished folded with motors disabled and no active moves.

No claim is made that forks, rebuilt wheels, altered languages, different hardware, or future commits inherit that physical acceptance.

## References

- [Reachy Mini SDK](https://github.com/pollen-robotics/reachy_mini)
- [Reachy Mini I Spy app-catalog Space](https://huggingface.co/spaces/Timbo89/reachy_mini_i_spy)
- [Reachy Mini Hermes — project origin and related integrated app](https://github.com/Timverhoogt/reachy-mini-hermes)
- [Add any language](docs/ADDING_A_LANGUAGE.md)
- [Hermes Agent documentation](https://hermes-agent.nousresearch.com/docs/)
- [Architecture](docs/ARCHITECTURE.md)
- [Safety contract](docs/SAFETY_CONTRACT.md)
- [Release provenance](docs/RELEASE_PROVENANCE.md)

## Credits

Built by [Tim Verhoogt](https://github.com/Timverhoogt) with development and testing assistance from [Hermes Agent](https://github.com/NousResearch/hermes-agent) by [Nous Research](https://nousresearch.com/).

## License

Apache License 2.0. See [LICENSE](LICENSE).
