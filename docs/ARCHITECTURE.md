# Architecture

Reachy Mini I Spy runs as one application on the same machine as the Reachy daemon.

## Deployment profiles

### Reachy Mini Lite

Reachy Mini Control and the daemon run on the connected Mac/PC. The I Spy app, camera-frame processing and direct provider calls run on that same host. The Lite robot supplies camera, microphones, speaker and motion hardware over its normal USB connection.

### Reachy Mini Wireless

The daemon and I Spy app run on Reachy's onboard Raspberry Pi CM4. Cloud provider calls leave directly from the app; local ONNX detection and offline speech run on the CM4. No second server is required.

## Application boundary

The `reachy_mini_i_spy` package is installed through the official `reachy_mini_apps` entry point. It owns:

- game settings and camera-opt-in state;
- owner-only provider credentials;
- fixed provider URL, models, prompts and response schemas;
- deterministic game and generation state;
- bounded head/base motion and final folding;
- transient frame capture;
- local UI and status endpoints;
- moderation, audio playback and immediate Stop handling.

The UI cannot configure arbitrary URLs, models, prompts, voices or tools. Provider credentials are never returned through the status API.

## Direct cloud data flow

```text
Camera opt-in
      │
      ▼
App host: bounded motion + up to three transient JPEGs
      │ fixed HTTPS endpoint, fixed schema, owner-only API key
      ▼
OpenAI: moderation + fixed vision/TTS operations
      │ untrusted bounded result
      ▼
App host: deterministic validation + clue/guess state + motion/audio
      │
      └── Stop/reveal/error → invalidate generation, reject late results,
          stop audio, revoke camera, clear target, fold, disable motors
```

Frames and guesses are processed in memory. The app does not intentionally persist gameplay media or transcripts.

## Local-mode data flow

```text
Camera opt-in
      │
      ▼
App host: bounded motion + up to three transient JPEGs
      │ no network inference
      ▼
Bundled ONNX detector: boxes + COCO labels + confidence
      │ untrusted bounded detections
      ▼
Deterministic allowlist + stability + colour + hints + aliases
      │
      ▼
Pinned sherpa-onnx English/Dutch speech → Reachy speaker
```

The local path replaces both cloud vision and cloud speech synthesis backends, not the safety state machine:

- ONNX object detection executes on the daemon host;
- object classes are restricted to an explicit child-safe allowlist;
- stability across viewpoints, bounding-box rules and colour extraction are deterministic;
- hints and guess aliases come from reviewed English/Dutch tables;
- Stop and generation rules remain unchanged;
- speech uses Apache-2.0 `sherpa-onnx` with pinned, checksummed English public-domain and Dutch CC0 voice datasets;
- voice archives are installed atomically after path-traversal and size checks;
- model engines are cached in memory after their first load.

Version `0.2.0` local mode is clean-install validated and physically accepted on Wireless. The redesigned Lite-host profile remains explicitly marked as awaiting same-version physical acceptance.

## Origin and related project

Reachy Mini I Spy originated as a focused extraction of the I Spy experience developed in [Reachy Mini Hermes](https://github.com/Timverhoogt/reachy-mini-hermes). It now owns its runtime, provider integration, safety contract, releases and acceptance lifecycle.

Reachy Mini Hermes retains a separate integrated Kids Mode implementation with different frame counts, camera choreography and alternating roles. The two projects may share safety principles, but neither inherits the other's release or physical acceptance.
