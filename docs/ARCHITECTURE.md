# Architecture

Reachy Mini I Spy is split across two machines and two deliberately narrow trust zones.

## Reachy Mini app

The `reachy_mini_i_spy` package runs through the official `reachy_mini_apps` entry-point mechanism. It owns:

- caregiver settings and camera-consent state;
- deterministic game and generation state;
- bounded head/base motion and final folding;
- transient frame capture;
- local UI and status endpoints;
- audio playback and immediate Stop handling.

It does not receive provider credentials and cannot select models, prompts, upstream URLs, or general tools.

## Hermes-host broker

`hermes_broker/` is deployed separately and is intentionally excluded from the Reachy wheel. It exposes only six operations under `/ispy/v1`:

1. input moderation;
2. target selection or presence checking;
3. guess judging;
4. output moderation;
5. text-to-speech;
6. session cancellation.

The broker binds a scoped token to one configured device ID, owns the fixed provider models and prompts, bounds all request and response fields, and rejects redirects and unknown payload fields. It has no general agent, memory, filesystem, messaging, smart-home, purchasing, or provider-passthrough endpoint.

## Data flow

```text
Caregiver consent
      │
      ▼
Reachy: bounded motion + three transient JPEGs
      │ scoped TLS request
      ▼
Hermes I Spy broker: moderation + fixed vision decision
      │ validated target only
      ▼
Reachy: deterministic clue/guess state + local motion/audio
      │
      └── Stop/reveal/error → invalidate generation, cancel broker session,
          stop audio, revoke camera, clear target, fold, disable motors
```

Frames and guesses are processed in memory. Neither component intentionally persists gameplay media or transcripts.

## Related project

[Reachy Mini Hermes](https://github.com/Timverhoogt/reachy-mini-hermes) has its own integrated Kids Mode implementation, child-session bridge, five-frame scan, and alternating roles. It shares target-selection principles with this project but not its runtime authority or speech/session protocol. See [SAFETY_CONTRACT.md](SAFETY_CONTRACT.md) before coordinating changes.
