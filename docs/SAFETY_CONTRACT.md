# Safety contract

This document is normative for the standalone Reachy Mini I Spy app. UI text and provider prompts supplement these rules; they do not replace enforcement in code.

## Consent and camera lifecycle

- Every session starts with camera access disabled.
- A caregiver must explicitly opt in for each start.
- Capture is limited to three transient JPEG viewpoints during a bounded search.
- Stop, reveal, error, shutdown, or consent revocation invalidates the active generation and disables camera use.
- Late provider, motion, speech, or selection results from an invalid generation are discarded.
- Frames are held in memory only and cleared after target selection or failure.

## Target-selection invariant

A candidate is accepted only when all of the following hold:

- provider marks it stable;
- it is visible in at least two retained viewpoints and no more than the supplied frame count;
- object name, category, location, and hints are bounded printable text;
- colour is one of the approved English/Dutch colour keys;
- confidence is between `0.78` and `1.0`;
- frame index addresses a retained frame;
- normalized bounding box stays inside the image;
- box area is between `0.025` and `0.65`, with width and height each at least `0.12`;
- one to three bounded hints exist in both English and Dutch;
- object, category, location, and hints contain no disallowed person, body, clothing, screen, document, medicine, weapon, credential, address, or private-material terms.

Provider JSON is untrusted. Malformed, missing, ambiguous, or out-of-range values fail closed.

The executable source of these invariants is `reachy_mini_i_spy/game.py::validate_target`, backed by negative and adversarial tests.

## Motion and embodied safety

- Search motion is software-bounded and owned by one cancellable motion controller.
- Out-of-range poses are rejected before reaching the SDK.
- Stop has authority over search, selection, guessing, speech, and motion.
- Every terminal path attempts a neutral return, fold, and motor disable.
- Physical acceptance applies only to the exact documented artifact and tested hardware setup.

## Moderation and speech

- Cloud guesses and generated output are moderated through fixed in-process provider operations.
- Local mode accepts only an explicit COCO target allowlist, reviewed bilingual hints/aliases and bounded text that passes the deterministic deny policy.
- Spoken output is checked immediately before synthesis.
- The caregiver UI cannot choose provider URLs, models, system prompts, tools, or voices.
- Missing cloud moderation, missing local model assets or failed synthesis authorization fails closed.

## Privacy and authority boundaries

The standalone app has no personal memory, face recognition, general agent tools, private-file access, messaging, smart-home control, purchasing, or unrestricted provider API. In cloud mode the provider key is stored owner-only on the daemon host, is used only with fixed endpoints and models, and is never returned through the status API or written to logs. Local mode needs no provider credential and sends no gameplay content to an inference service.

On Reachy Mini Lite, the daemon host is the connected Mac/PC. On Reachy Mini Wireless, it is Reachy's onboard CM4. No second host or Hermes service is part of the runtime boundary.

## Origin and coordination with Reachy Mini Hermes

This standalone project originated from I Spy work in [Reachy Mini Hermes](https://github.com/Timverhoogt/reachy-mini-hermes), but it now owns its runtime, safety contract, releases, and acceptance independently. Reachy Mini Hermes retains a separate integrated implementation with different frame counts, session ownership, camera choreography, moderation capabilities, and speech authorization.

When a shared target-policy principle changes:

1. update this standalone contract and tests;
2. review the related Hermes implementation for equivalent policy impact;
3. update Hermes tests/documentation when applicable;
4. rerun each affected project’s own full gates;
5. repeat supervised physical acceptance for each affected app if motion, camera timing, language, audio, or hardware behavior changes.

A release in either project does not automatically validate or accept a release in the other.
