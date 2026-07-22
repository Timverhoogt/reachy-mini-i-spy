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

## Standalone provider broker

`hermes_broker/` is deployed on a separate trusted host and is intentionally excluded from the Reachy wheel. The directory keeps its historical name for compatibility, but the component does not require Hermes Agent or the Reachy Mini Hermes app. It exposes only six operations under `/ispy/v1`:

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
I Spy provider broker: moderation + fixed vision decision
      │ validated target only
      ▼
Reachy: deterministic clue/guess state + local motion/audio
      │
      └── Stop/reveal/error → invalidate generation, cancel broker session,
          stop audio, revoke camera, clear target, fold, disable motors
```

Frames and guesses are processed in memory. Neither component intentionally persists gameplay media or transcripts.

## Origin and related project

Reachy Mini I Spy originated as a focused extraction of the I Spy experience developed in [Reachy Mini Hermes](https://github.com/Timverhoogt/reachy-mini-hermes). It is now a complete standalone app with its own Reachy entry point, UI, dedicated broker, safety contract, releases, and acceptance lifecycle.

Reachy Mini Hermes retains a separate integrated Kids Mode implementation, child-session bridge, five-frame scan, and alternating roles. The projects share some target-selection principles, but neither app shares the other’s runtime authority, speech/session protocol, release status, or physical acceptance. See [SAFETY_CONTRACT.md](SAFETY_CONTRACT.md) when a policy change could affect both.
