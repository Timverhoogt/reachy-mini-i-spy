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
| Guess policy | Six guesses; repeats the final hint | Three meaningful guesses; hint/give-up intents and deduplicated hint ladder | **Next priority**: port the EN/NL deterministic policy with dedicated runtime regressions. Not changed here. |
| Target variety | OpenAI returns one candidate; local detector chooses highest-ranked stable target | Validated randomized candidate pool with bounded recent-target avoidance | **Next priority**: implement a pool in both standalone providers, preserving local mode and full validation. Not changed here. |
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

## Follow-up acceptance before gameplay parity

1. Port explicit hint/give-up handling and the three-guess policy together. Hint
   requests must not consume a guess or trigger the periodic target re-check.
   Retain the exact selected target, deduplicate hints and test exhausted hints.
2. Implement bounded candidate pools across OpenAI schemas/parser/moderation and
   local detector selection. Validate every admitted candidate, prefer recent
   alternatives in RAM only, and fail closed with zero safe candidates. A single
   eligible object cannot promise variety.
3. Verify cancellation during held provider/moderation work and the complete existing
   Stop/motion/audio suite. Test local and cloud paths separately.
4. Review whether recoverable guess-judge failures should retry rather than terminate,
   and fix Dutch object-name reveal localization as its own provider contract change.
5. Build a new canonical version only after review. Supervised Wireless and Lite
   acceptance are independent of source tests and of historical `0.2.0` acceptance.

This `0.2.1.dev0` source candidate is intentionally limited to the illustration,
associated UI lifecycle safeguards, deterministic filter repair, tests and docs.
It is not a published release and does not claim gameplay parity.
