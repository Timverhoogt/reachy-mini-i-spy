# Homebody and standalone I Spy: source comparison

## Baselines

- Standalone: [`73b01db95ce85eced4563af04b115ff86b8e7857`](https://github.com/Timverhoogt/reachy-mini-i-spy/tree/73b01db95ce85eced4563af04b115ff86b8e7857), published `0.2.0` source.
- Homebody: [`5b77d9b5a94db862463a28a4c9f0653f6ce6b011`](https://github.com/Timverhoogt/homebody/tree/5b77d9b5a94db862463a28a4c9f0653f6ce6b011), fetched `origin/main` for this comparison.

This compares source, not the currently installed robot versions. Neither running
app was changed, and no camera, microphone, motion or provider test was initiated.

## Findings and candidate scope

| Area | Standalone baseline | Homebody baseline | This candidate / recommendation |
| --- | --- | --- | --- |
| Illustration | Text-only game card | Authored animated detective-desk scene | **Added** the exact dependency-free renderer/CSS with a standalone state adapter. |
| Visual lifecycle | Polls could repaint stale state after Stop; browser speech results had no cancellation fence | Scene mount/update/reset/destroy, deduplication, visibility and reduced-motion gates | **Added** scene lifecycle and UI request-generation fences, serialized polling and browser recognition teardown. |
| Object text filter | Exact single words only; multi-word entries never matched | Whole words, simple plurals and separated phrases | **Fixed** the standalone filter; added Dutch terms and explicit irregular `knives`. |
| Guess policy | Six guesses; repeats the final hint | Three meaningful guesses; hint/give-up intents and deduplicated hint ladder | **Added** the EN/NL three-guess policy, non-counting hint/give-up commands and dedicated UI buttons. |
| Target variety | OpenAI returns one candidate; local detector chooses highest-ranked stable target | Validated randomized candidate pool with bounded recent-target avoidance | **Added** a 1–4 cloud candidate pool and randomized safe local choices, preferring alternatives to the last three name keys in RAM. |
| Languages | English/Dutch | English/Dutch/Spanish/German/French/Italian/Portuguese | Separate provider/voice/local-recognition work, not a table-copy exercise. Not changed here. |
| Input | Typed guess and opt-in browser speech-to-text | Robot voice and typed Kids turns | Preserve the standalone browser disclosure; do not inherit Homebody's voice stack. |
| Providers | Direct fixed-policy OpenAI or onboard/connected-host YOLOX + sherpa-onnx | Host bridge and Kids speech/moderation protocols | Intentional difference. **Preserved** standalone independence and offline mode. |
| Physical lifecycle | Own leases, search, target re-checks, verified fold and Stop cycle | Homebody power/Kids/session lifecycle | Keep separate. Do not transplant Homebody's robot ownership or middleware. |

## Visual contract

The standalone adapter consumes only categorical `state` and `language`:

- `idle` → preview; `searching` → searching; `selecting` → thinking.
- `guessing` → waiting for input, **not microphone listening**. The browser microphone
  only opens after Speak, and no measured microphone state is exposed by the server.
- `reveal`/`stopped` → neutral ended still, **not victory or confirmed audible speech**.
- `error` → trouble; unknown → paused/unconfirmed; connection failure → offline.
- Stop immediately cancels old UI producers and shows Stop requested, without claiming
  that the physical robot has already stopped. Failed Stop remains unconfirmed until
  a fresh non-active state is returned, or Stop is successfully retried.

The scene receives no target, clue, guess, generated reply, frame, confidence or
progress estimate. It performs no I/O or robot actions. Captions are fixed EN/NL
copy. The camera badge labels the existing consent/session flag **Camera allowed**,
not measured capture activity. Offline is **Camera unconfirmed**, not Camera off.

## Implemented follow-up and remaining acceptance

The `0.3.0` candidate implements the recommended EN/NL gameplay follow-up:

- Only judged guesses spend an attempt. Hint/help and give-up/don't-know commands
  take the same moderated input route without invoking a judge or capturing a frame.
- Hints are deduplicated across case, whitespace and punctuation; the colour reminder
  is not repeated if already given. Explicit exhausted-hint requests reveal the answer.
- Failed judge checks use only deterministic policy-checked retry copy and spend no
  attempt. They never speak an unapproved model response or echo rejected input.
- All admitted bounded cloud candidates are validated before de-duplication and code-side
  randomization. Only a definite moderation refusal permits trying the next candidate;
  missing/malformed verdicts and transport failures fail closed. Local choices use the
  same recent-key preference without cloud inference or lower confidence thresholds.
- Recent generic name keys are RAM-only, bounded to three and cleared on Stop/error.
  One eligible object may repeat, and changing language can change its name key.
- Local Dutch targets retain Dutch names while presence/guess matching still resolves
  the same safe detector class. Cloud prompt and per-request schema require the selected
  language for `object_name`; automated wire checks do not prove model compliance.
- Completed rounds retain no active target and use the existing verified fold/torque
  cleanup barrier before a new Start. Stop/replacement still wins over late work.

Hardware-free provider, runtime and browser regressions cover these contracts. The
release must remain a draft until the exact built wheel passes supervised gameplay,
camera/Stop/fold/audio acceptance. See `RELEASE_ACCEPTANCE.md`; historical `0.2.0`
Wireless acceptance is not acceptance of this candidate, and Lite is a separate gate.
No additional languages, child-picker role or Homebody bridge dependency are added.
