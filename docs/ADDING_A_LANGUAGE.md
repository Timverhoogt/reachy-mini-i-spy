# Add a language in a fork

Reachy Mini I Spy 0.1.0 is intentionally limited to English (`en`) and Dutch (`nl`). A fork can add another language, but it is a coordinated code change: the app API, game phrases, provider contract, hints, TTS, browser speech recognition, and tests must all agree on the same language code.

The examples below use French (`fr` / `fr-FR`). Replace those values with a short lowercase language code and the appropriate BCP 47 speech-recognition locale for your language.

## 1. Fork or duplicate the Space

1. Fork or duplicate `Timbo89/reachy_mini_i_spy` on Hugging Face.
2. Clone your fork locally.
3. Create a branch such as `add-fr`.
4. Keep the privacy and safety boundaries intact: camera consent, three-frame limit, Stop/cancellation, moderation, target validation, no media retention, and final fold/torque-off behavior.

Do not put provider credentials, broker tokens, captured frames, audio, or local configuration in the fork.

## 2. Add the language to the Reachy app

Update `reachy_mini_i_spy/game.py`:

- Add the language to `Language`, for example `Literal["en", "nl", "fr"]`.
- Add a translation for every entry in `COLOURS`.
- Add translated sensitive/disallowed terms to `DISALLOWED_TERMS`; do not remove the existing English and Dutch terms.
- Add the code to the allowed-language check in `GameMachine.start()`.
- Translate all game-state phrases: searching, clue, stopped, reveal, failed round, escaped target, and next-hint wording.
- Extend `Target` and `validate_target()` with `hints_fr`, then make `next_hint()` select the correct hint list.

The current code uses several `if language == "en" else ...` branches because there are only two languages. Replace each affected branch with an explicit phrase table or `match` statement. Otherwise a new language will accidentally receive Dutch or English text.

Update `reachy_mini_i_spy/main.py`:

- Add the code to `StartRequest.language`.

Update `reachy_mini_i_spy/runtime.py`:

- Translate the intro, safe failure, escaped-object, successful reveal, exhausted-guesses reveal, and hint-prefix messages.
- Check every `language == "en"` branch and give the new code an explicit result.

## 3. Add the language to the UI

Update `reachy_mini_i_spy/static/index.html` with another language option, for example:

```html
<option value="fr">Français</option>
```

Update `reachy_mini_i_spy/static/main.js` so browser speech recognition uses the right locale. Prefer an explicit map rather than another two-way conditional:

```js
const speechLocales = { en: "en-US", nl: "nl-NL", fr: "fr-FR" };
recognition.lang = speechLocales[$("language").value];
```

Typed guesses must remain available when browser speech recognition is unsupported.

## 4. Extend the Hermes broker contract

Update `hermes_broker/app.py`:

- Add the language code to `SelectRequest`, `GuessRequest`, and `TTSRequest`.
- Add `hints_fr` to `TargetReference` and `TARGET_RESPONSE_SCHEMA`, including the `required` list.
- Ask target selection for 1–3 child-safe hints in the new language.
- Include the new hints in moderation.
- Map the language code to an explicit TTS language instruction. Do not let it fall through to English or Dutch.
- Keep strict structured output, moderation, safe-target validation, request limits, and fail-closed behavior unchanged.

If your language needs additional target-name localization, make that field explicit in the strict schema and moderate it before use. Do not weaken the existing schema to accept arbitrary provider fields.

## 5. Extend tests

Add tests that prove the new language works across the entire boundary:

- API accepts the new code and rejects unknown codes.
- Every approved colour has a translation.
- Target validation requires and moderates `hints_fr`.
- Search, clue, hint, reveal, Stop, and safe-failure messages use French rather than a fallback language.
- Guess judging receives `fr`.
- TTS receives an explicit French instruction.
- The language selector and `fr-FR` speech locale are present.
- Unsafe French target names, locations, and hints fail closed.
- Stop still revokes camera access, rejects late output, folds Reachy, and disables motors.

Run:

```sh
uv run --extra dev pytest
uv run --extra dev ruff check .
uv build
uv run python scripts/check_artifacts.py dist/*
reachy-mini-app-assistant check .
```

## 6. Physically accept the fork before publishing

On a supervised Reachy Mini, verify at least one complete round in the new language:

1. Caregiver camera consent is required.
2. Search movement and frame capture remain bounded.
3. The colour clue, hints, guesses, reveal, and TTS are in the new language.
4. An unsafe or malformed provider response fails closed.
5. Stop works during search, provider processing, guessing, and speech.
6. Camera access is revoked after reveal or Stop.
7. No late speech or motion occurs after Stop.
8. Reachy finishes folded with motors disabled and no active moves.

Publish a new version from your fork only after its exact artifact passes those checks. Do not describe the original 0.1.0 wheel as physically accepted for your modified language build.
