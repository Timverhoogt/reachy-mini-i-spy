---
title: Reachy Mini I Spy
emoji: 🔎
colorFrom: blue
colorTo: purple
sdk: static
pinned: false
short_description: A private bilingual I Spy game for Reachy Mini Lite and Wireless
tags:
  - reachy_mini
  - reachy_mini_python_app
---

# Reachy Mini I Spy

[![CI](https://github.com/Timverhoogt/reachy-mini-i-spy/actions/workflows/ci.yml/badge.svg)](https://github.com/Timverhoogt/reachy-mini-i-spy/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![Reachy Mini](https://img.shields.io/badge/Reachy_Mini-app-5b6ee1)](https://github.com/pollen-robotics/reachy_mini)

A camera-opt-in English/Dutch I Spy game for Reachy Mini Lite and Reachy Mini Wireless. Reachy performs a bounded search and chooses one stable, age-appropriate household object.

Reachy Mini I Spy originated from the I Spy experience developed in [Reachy Mini Hermes](https://github.com/Timverhoogt/reachy-mini-hermes). This repository is now an independent app with its own Reachy entry point, UI, safety contract, releases and acceptance lifecycle. **Hermes Agent and Reachy Mini Hermes are not runtime dependencies.**

## Temporarily delisted

Version `0.1.0` was removed from the Reachy Mini app catalog because it still required a separately deployed provider broker. Its source and accepted artifact remain available for provenance, but it should not be presented as a one-click standalone setup.

The in-development release moves all provider work into the app process. The user chooses either direct OpenAI with one API key or local no-key ONNX processing. There is no broker URL, scoped broker token, separate Linux server or Hermes setup.

The app will be relisted only after the new Lite and Wireless deployment paths are tested and physically accepted.

## Where compute runs

| Reachy model | Runtime host | Vision/provider work |
|---|---|---|
| **Reachy Mini Lite** | The Mac/PC running Reachy Mini Control and the daemon | Runs on that connected Mac/PC |
| **Reachy Mini Wireless** | Reachy's Raspberry Pi CM4 | Runs onboard; cloud mode calls OpenAI directly and local mode stays onboard |

This is one application and one wheel. The UI detects the deployment profile and shows where compute runs.

## Provider modes

### Direct cloud key — implemented

- One OpenAI API key entered in the local app UI.
- Fixed OpenAI URL, vision model, moderation model, TTS model, prompts and response schemas; callers cannot override them.
- Vision frames, moderated guesses and speech requests go directly from the app to OpenAI.
- The key is stored with mode `0600` in `~/.config/reachy-mini-i-spy/config.json` and is never returned by the status API or written to logs.
- Late responses are rejected after Stop through the existing generation/cancellation boundary.

### Local no-key mode — implemented

- A bundled 3.5 MB official YOLOX-Nano ONNX model performs aspect-preserving object detection on the daemon host.
- Only an explicit child-safe COCO class allowlist can become a target. Cross-view class stability, exact target boxes, size, colour, location, hints and guesses are handled deterministically.
- The user clicks **Install / verify local models** once. The app downloads about 35 MB of pinned English/Dutch voice archives, verifies exact SHA-256 digests and safely extracts them into `~/.cache/reachy-mini-i-spy/models`.
- Offline speech uses Apache-2.0 `sherpa-onnx`, not the GPL Piper runtime. The English LJSpeech dataset is public domain; the Dutch Nathalie dataset is CC0.
- After setup, frames, guesses and speech stay on the Lite Mac/PC or Wireless CM4. No API key or HF token is needed.

On the actual aarch64 Wireless hardware, three repeated validation frames completed local target selection in about 4.36 seconds. Cold English/Dutch speech includes a 10–12 second model load; engines are cached afterward. Cloud mode remains the faster option, while local mode is the private/offline option. Hugging Face hosted inference is not used as a fallback: free users currently receive only $0.10/month in credits and still need an HF token.

## What it does

- English and Dutch gameplay for age bands 4–6, 7–9 and 10–12.
- Per-game camera opt-in.
- Three transient in-memory viewpoints during one bounded search.
- Stable-object, colour, confidence, size and category validation.
- Moderated guesses, hints, reveal text and speech.
- Generation-based cancellation so Stop invalidates late network, camera, motion and audio work.
- Automatic camera revocation, neutral return, fold and motor disable on Stop, reveal, failure or shutdown.

## Privacy and safety

- Camera is off by default; Stop is always available and disables it.
- At most three in-memory JPEG frames are sent during one cloud-assisted search; local mode sends none.
- Frames, guesses, audio and transcripts are not intentionally written to disk.
- Faces, people, bodies/clothing, screens, documents, medicine, weapons, private material, tiny objects and unclear colours are rejected.
- Cloud provider output and every guess are moderated. Local mode uses a fixed allowlist, deterministic aliases and a fail-closed text policy; speech is checked again immediately before TTS.
- Missing moderation, vision, malformed output, stale sessions and network failures fail closed.
- The app has no personal memory, face recognition, agent tools, files, messaging, smart-home or purchasing access.

See the normative [safety contract](docs/SAFETY_CONTRACT.md) and [architecture](docs/ARCHITECTURE.md).

## Development

```sh
uv sync --extra dev
uv run pytest
uv run ruff check .
uv build
uv run python scripts/check_artifacts.py dist/*
reachy-mini-app-assistant check .
```

The Reachy SDK includes native Linux dependencies. See CI for the tested Ubuntu setup.

To add another language, follow [Adding any language](docs/ADDING_A_LANGUAGE.md). A modified language or provider build requires its own moderation review and physical acceptance; acceptance of an older artifact does not transfer.

## Historical accepted release

The delisted `0.1.0` wheel was physically accepted on a supervised Reachy Mini Lite:

```text
reachy_mini_i_spy-0.1.0-py3-none-any.whl
SHA-256 56e821d241f323144f6b9af2baacd7eb8929ed63de641944eacd32d0d911ca6e
```

That acceptance covers only the historical broker-based artifact. It does **not** validate this provider redesign or the Wireless path.

## Credits

Built by [Tim Verhoogt](https://github.com/Timverhoogt) with development and testing assistance from [Hermes Agent](https://github.com/NousResearch/hermes-agent) by [Nous Research](https://nousresearch.com/).

## License

Apache License 2.0. See [LICENSE](LICENSE).
