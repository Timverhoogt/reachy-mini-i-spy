# Release provenance

## Accepted artifact

`reachy_mini_i_spy` version `0.1.0` was physically accepted using:

```text
File: reachy_mini_i_spy-0.1.0-py3-none-any.whl
SHA-256: 56e821d241f323144f6b9af2baacd7eb8929ed63de641944eacd32d0d911ca6e
Hardware: supervised Reachy Mini Lite
```

The same bytes are published in the [v0.1.0 GitHub release](https://github.com/Timverhoogt/reachy-mini-i-spy/releases/tag/v0.1.0) and the [Reachy app-catalog Space](https://huggingface.co/spaces/Timbo89/reachy_mini_i_spy), whose verified source revision at publication was `1a5231d2178e5c2d703ca06c245bec3a63e11914`.

## Acceptance evidence

- English and Dutch rounds reached reveal after six guesses.
- Camera access was revoked after reveal.
- Authoritative Stop cleared the target and rejected late output.
- Rollback installation was exercised.
- Final state was folded, motors disabled, with no active move.

## Source correspondence

The Python, HTML, JavaScript, and CSS application members in the accepted wheel match this imported standalone source payload. A later rebuild is **not** claimed to be byte-for-byte reproducible: wheel archive timestamps and embedded project/README metadata can differ, and the public documentation and repository references were improved after physical acceptance.

Only the exact digest above inherits the recorded physical acceptance. Rebuilt wheels, forks, language changes, provider changes, scan/motion changes, different hardware, or later releases require their own verification and, where embodied behavior changes, supervised physical acceptance.

## Distribution boundary

The Reachy wheel intentionally excludes:

- `hermes_broker/`;
- `deploy/`;
- tests and scripts;
- local configuration;
- provider or device credentials;
- captured frames, audio, guesses, or transcripts.

Run `scripts/check_artifacts.py` against every release artifact before publication.
