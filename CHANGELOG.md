# Changelog

All notable changes to this project are documented here.

## [Unreleased] — 0.3.0 candidate

### Added

- Homebody's illustrated I Spy scene, vendored without a Homebody runtime dependency,
  with English/Dutch categorical captions and reduced-motion/offscreen/page lifecycle handling.
- Hardware-free Chromium regressions for animation, Stop, stale replies, speech teardown,
  connection failure, text safety and phone/desktop control hit-testing.
- Pinned scene provenance and a source-level comparison with Homebody.

### Fixed

- Plural and multi-word forbidden target names, including `credit card`, `phones`,
  `knives`, and Dutch `telefoon`/`mensen`, now fail the deterministic object filter.
- Stop invalidates old status, Start, guess and browser-recognition callbacks in the UI.
- Lost connections show unknown camera status instead of retaining a live animation.
- Ordinary guesses no longer re-aim/capture or automatically replace the target
  after a noisy periodic presence check. All three guesses use the exact retained
  object, and the advertised three-frame search bound applies to the whole round.

### Gameplay changes

- Three meaningful guesses per round, rather than six.
- Explicit English/Dutch hint, help, don't-know and give-up commands. Hint requests
  do not consume an attempt or trigger another camera capture.
- Deduplicated hints and colour reminders, with an explicit reveal on hint exhaustion.
- Hint / I give up buttons routed through the same moderated guess endpoint.
- A validated 1–4 candidate cloud pool with code-side randomization and moderation
  of the selected candidate; definite content refusals may try another safe candidate,
  but missing/malformed moderation and transport failures fail closed.
- Random choice among validated local detections, with the same bounded RAM-only
  recent-object preference. A sole eligible object may repeat; Stop/error clears history.
- Judge failures produce only a policy-checked deterministic retry without using an attempt.
- Dutch local targets retain Dutch names for reveal and bilingual class-based matching.
- Terminal rounds revoke old work, clear the retained target, and finish verified fold/
  motor cleanup before a new Start is admitted.

`0.3.0` remains a candidate pending exact-artifact supervised acceptance. No robot
installation or catalog update is implied by source publication or a draft release.

## [0.2.0] - 2026-07-23

### Added

- Canonical GitHub source repository.
- Apache-2.0 licensing, security policy, contribution guide, architecture, safety contract, and release provenance.
- Explicit relationship and coordinated-change references for Reachy Mini Hermes.
- GitHub CI for source, policy, and artifact-boundary checks.
- In-process fixed-policy OpenAI provider configured with one owner-only API key.
- Automatic Lite-host versus Wireless-host compute guidance in the app UI.
- No-key local ONNX object detection with an explicit child-safe class allowlist and deterministic bilingual gameplay policy.
- Pinned Apache-2.0 YOLOX-Nano detection with aspect-preserving preprocessing, class-aware NMS, multi-view stability and exact-box revalidation.
- Pinned, checksummed English/Dutch offline TTS setup using Apache-2.0 `sherpa-onnx`, public-domain LJSpeech and CC0 Nathalie voice data.
- Safe atomic local-model installer with archive size, member and path-traversal enforcement.

### Changed

- Positioning now records Reachy Mini Hermes as the project origin while presenting I Spy as a complete standalone app with its own lifecycle.
- The language-extension guide now covers adding any language end to end, either upstream or in an independent fork.
- Removed the provider broker, separate-host service, broker URL, scoped token, and Hermes-adjacent deployment path.
- Relisted the standalone app after exact-artifact Wireless acceptance; the redesigned Lite profile remains clearly marked as awaiting same-version physical acceptance.

## [0.1.0] - 2026-07-22

### Added

- Standalone English/Dutch I Spy game for Reachy Mini.
- Per-session camera opt-in and three-frame bounded search.
- Dedicated narrow off-robot provider broker with fixed provider decisions and scoped device authentication.
- Deterministic game state, generation-based cancellation, moderated clues/guesses/speech, authoritative Stop, folding, and motor disable.
- Physically accepted wheel with SHA-256 `56e821d241f323144f6b9af2baacd7eb8929ed63de641944eacd32d0d911ca6e`.

[Unreleased]: https://github.com/Timverhoogt/reachy-mini-i-spy/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Timverhoogt/reachy-mini-i-spy/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Timverhoogt/reachy-mini-i-spy/releases/tag/v0.1.0
