# 0.3.0 release acceptance

## Publication gate

The `0.3.0` candidate changes gameplay, provider selection and final-round cleanup.
Prepare and verify the exact wheel, source archive and `SHA256SUMS.txt` as GitHub
**draft** release assets after automated checks and independent review. Do not
publish the stable release, update the catalog mirror, or replace an accepted
robot installation merely because source tests pass.

`0.2.0` remains the latest published physically accepted Wireless artifact. Its
acceptance does not transfer to this candidate. Lite-host acceptance is separate.

## Before physical testing

- [ ] Record candidate source commit, wheel filename and verified SHA-256 from the
      draft release. Test those exact downloaded bytes, not a fresh rebuild.
- [ ] Automated unit/runtime, Chromium, lint, package-boundary and SDK checks pass
      at the exact source head; inspect each required CI job's actual conclusion.
- [ ] Complete independent code/safety/privacy review and resolve blockers.
- [ ] Obtain an explicit supervised test window with clear robot space and a
      reachable Stop. Saved camera opt-in for another app is not consent here.
- [ ] Identify the actual app lifecycle owner, safe idle/fold/torque state and
      rollback artifact before any separately authorized installation.

## Supervised gameplay (each claimed hardware/provider profile)

- [ ] A fresh start without camera consent is refused and captures nothing.
- [ ] Explicitly consented search remains bounded and natural; the app shows
      searching and selecting without inventing progress or revealing the target.
- [ ] In English and Dutch, clue and final reveal are understandable and use the
      selected language. Confirm local Dutch target names, not an English name in
      otherwise Dutch speech.
- [ ] A wrong meaningful guess uses one attempt and gets a useful new hint;
      exactly the third meaningful wrong guess reveals the fixed object.
- [ ] Neither a wrong guess nor a detector miss triggers another capture, re-aim
      or automatic selection restart; an explicit new round is required to re-scan.
- [ ] Hint requests do not consume attempts or trigger a camera re-check; hints
      and the colour reminder do not repeat. Exhausted explicit hints reveal.
- [ ] Give-up / don't-know reveals immediately, without another scan.
- [ ] Hint and I give up buttons, typed Enter and optional browser speech input
      reach the same policy path. Speech capture must remain explicit.
- [ ] Multiple rounds in a room containing several eligible safe objects prefer
      alternatives. A sole eligible object may honestly repeat. No target changes
      during an ordinary guessing round.
- [ ] Cloud mode accepts the actual 1–4 candidate schema and selected-language
      contract. Use only explicitly consented frames; synthetic wire tests alone
      do not establish actual model compliance.
- [ ] Local mode works offline after the pinned assets are installed. No cloud
      fallback or new model/voice selection is introduced.
- [ ] At reveal, camera authority is revoked and the target is cleared; verified
      folding/motor disable completes before another Start is admitted.
- [ ] Stop works during search, selection, a held reply and speech. Late output
      never resumes movement, capture, audio or UI activity; Stop clears recent
      generic target keys and no private gameplay data is persisted.
- [ ] Connection failure is visibly unconfirmed, not false proof of camera off
      or completed Stop. A fresh state read permits ordinary recovery.
- [ ] Verify final safe fold and disabled torque independently, then exercise
      the documented rollback path if installation was part of this test window.

## After acceptance

Record the actual tested hardware/provider/language scope and exact artifact
identity in `RELEASE_PROVENANCE.md`, retaining untested profiles explicitly. Publish
only the already accepted immutable assets; do not rebuild or silently replace
files under an accepted version. Catalog synchronization and robot installation
remain separate actions with their own readback verification.
