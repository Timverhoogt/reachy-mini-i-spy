# Vendored Homebody scene renderer

`reachy_mini_i_spy/static/scene.js` and `scene.css` are exact unmodified copies of
`homebody/static/scene.js` and `homebody/static/scene.css` from:

- Project: [Timverhoogt/homebody](https://github.com/Timverhoogt/homebody)
- Commit: `5b77d9b5a94db862463a28a4c9f0653f6ce6b011`
- License: Apache-2.0, the same license as this repository. See `LICENSE`.
- SHA-256 `scene.js`: `796ab6eeeca8aa87fa10d908107e402b548629fd190ba79ffcb3b6a616e71795`
- SHA-256 `scene.css`: `8670d3f4469bac4d6d49bfac73c24669e98bf97fa6cba8efa68025aa26d8c9d4`

The full component is kept intact to preserve its tested observer, visibility,
reduced-motion and destruction behavior. Other scene definitions are inert; the
standalone app mounts only `ispy`. No Homebody package, bridge, credentials,
network endpoint or runtime dependency is introduced.

`static/ispy-scene.js` is the standalone EN/NL categorical view adapter. Do not
use Homebody's `kidsView()` directly: the standalone API has different semantics,
especially for `camera_active` and browser microphone input. See `HOMEBODY_PARITY.md`.

To update, compare the pinned upstream changes, replace both renderer assets
intentionally, update these digests and `tests/test_scene_assets.py`, then run the
unit and explicit Chromium lanes. Increment the asset query revisions when
changing shipped assets. Verify wheel resource bytes against source after building.
Do not introduce actions, storage, fetches, target details or guessed progress in
the renderer or adapter.
