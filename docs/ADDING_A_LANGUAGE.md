# Add any language

Reachy Mini I Spy `0.1.0` ships with English (`en`) and Dutch (`nl`). You can add any other language either as an upstream contribution to this project or in an independently maintained fork.

A language is not just a translated menu label. The Reachy app, API enums, game-state phrases, colours, hints, safety vocabulary, provider schema, moderation flow, guess judging, browser speech recognition, TTS, tests, package metadata, and physical acceptance must all agree on the same language contract.

The examples below use French:

- internal language code: `fr`
- BCP 47 browser-speech locale: `fr-FR`
- display name: `Français`

Replace those values with the correct code, locale, and native display name for your language.

## 1. Choose the contribution path

### Contribute upstream

1. Fork [`Timverhoogt/reachy-mini-i-spy`](https://github.com/Timverhoogt/reachy-mini-i-spy) on GitHub.
2. Clone your fork.
3. Create a focused branch such as `feat/add-french`.
4. Implement one language per pull request.
5. Include automated evidence and a language-review checklist in the pull request.

### Maintain an independent language fork

1. Fork the GitHub repository.
2. Keep the Apache-2.0 license and provenance notices.
3. Use a distinct package version and release notes.
4. Publish only your newly built artifact; never replace or relabel the accepted upstream `0.1.0` wheel.
5. Record your own moderation review and supervised physical acceptance.

Do not use the Hugging Face catalog mirror as the source-development repository. GitHub is canonical; the Space is the app-catalog and accepted-artifact mirror.

## 2. Define the language contract first

Before editing code, record:

| Item | Example | Requirement |
|---|---|---|
| Internal code | `fr` | Short, lowercase, stable identifier used by APIs and game state |
| Native display name | `Français` | What the user sees in the selector |
| Browser locale | `fr-FR` | Valid BCP 47 locale supported by target browsers |
| TTS language instruction | `French (fr-FR)` | Explicit fixed-provider instruction; never infer from user text |
| Writing direction | `ltr` or `rtl` | Required for UI layout and text fields |
| Reviewer | Native/fluent adult | Reviews child-facing language and safety vocabulary |
| Provider support | moderation, vision, guess judging, TTS | Must be verified for the selected language |

Also decide:

- whether the language uses a non-Latin script;
- how case, accents, punctuation, apostrophes, and Unicode normalization affect guess matching;
- whether grammatical gender, noun classes, inflection, or plural forms require phrase templates rather than string concatenation;
- whether browser speech recognition supports the locale on your target device;
- whether the selected TTS model and voice pronounce the language acceptably;
- which translated words describe people, bodies, clothing, documents, screens, medicine, weapons, private material, and other forbidden targets.

If moderation, speech recognition, or TTS does not support the language reliably, typed guesses must remain available and the unsupported capability must fail closed or be explicitly disabled. Do not silently fall back to English or Dutch.

## 3. Inventory every language-dependent branch

From the repository root, search before changing anything:

```sh
rg 'Language|language ==|language !=|Literal\[|hints_en|hints_nl|en-US|nl-NL|English|Dutch' \
  reachy_mini_i_spy tests
```

The current two-language implementation contains `if language == "en" else ...` branches. Before adding a third language, replace every affected two-way branch with an explicit phrase table, locale map, or `match` statement. Otherwise the new language can accidentally receive Dutch text.

Recommended pattern:

```python
PHRASES = {
    "en": {"stopped": "Stopped.", "searching": "I am looking around."},
    "nl": {"stopped": "Gestopt.", "searching": "Ik kijk even rond."},
    "fr": {"stopped": "Arrêté.", "searching": "Je regarde autour de moi."},
}
```

Use explicit indexed access such as `PHRASES[language]["stopped"]`. Do not use a default language for an accepted language code; a missing phrase should be caught by tests rather than exposed to a child.

## 4. Extend the Reachy app contract

Update `reachy_mini_i_spy/game.py`:

1. Add the code everywhere the `Language` type or allowed-language set is defined.
2. Add a translation for every entry in `COLOURS`.
3. Add translated sensitive and disallowed terms to `DISALLOWED_TERMS`; retain all existing languages.
4. Translate every game-state phrase, including:
   - searching;
   - initial colour clue;
   - next-hint wording;
   - stopped;
   - target escaped;
   - safe failure;
   - correct guess;
   - reveal after exhausted guesses.
5. Extend `Target` and `validate_target()` with the new strict hint field, for example `hints_fr`.
6. Make `next_hint()` select the new hint list explicitly.
7. Review normalization and guess matching for the language’s script and diacritics.

Update `reachy_mini_i_spy/main.py`:

- add the code to `StartRequest.language`;
- keep unknown language values rejected by Pydantic rather than falling back.

Update `reachy_mini_i_spy/runtime.py`:

- add explicit text for the intro, clue, safe failure, escaped target, successful reveal, exhausted-guesses reveal, and hint prefix;
- check every language branch found by the inventory command;
- keep generation checks, Stop behavior, camera revocation, neutral return, fold, and torque-off unchanged.

Do not weaken camera limits, motion bounds, target validation, cancellation, or fail-closed behavior to simplify localization.

## 5. Extend the UI and browser speech path

Update `reachy_mini_i_spy/static/index.html` with the language’s native name:

```html
<option value="fr">Français</option>
```

Update `reachy_mini_i_spy/static/main.js` with an explicit BCP 47 locale map:

```js
const speechLocales = {
  en: "en-US",
  nl: "nl-NL",
  fr: "fr-FR",
};

recognition.lang = speechLocales[$("language").value];
```

Also:

- keep typed guesses available when browser speech recognition is missing or inaccurate;
- set `dir="rtl"` or apply equivalent layout handling for right-to-left languages;
- test native-script input, punctuation, accents, and mobile keyboards;
- do not send an unsupported locale and pretend recognition succeeded;
- preserve the visible Stop control in every layout and viewport.

Translate additional user-facing UI labels if the contribution claims a localized UI rather than localized gameplay only. State the scope clearly in release notes.

## 6. Extend the fixed in-process provider schema

Update `reachy_mini_i_spy/provider.py`:

1. Add `hints_fr`—using your language code—to the target schema and game target representation.
2. Add the same field to `TARGET_RESPONSE_SCHEMA` and its strict `required` list.
3. Extend the provider method type annotations with the explicit language code.
4. Instruct target selection to return one to three child-safe hints in the new language.
5. Include the target name, colour clue, location, and new hints in moderation checks.
6. Pass the explicit language code to guess judging.
7. Map the language code to an explicit app-controlled TTS instruction.
8. Verify the configured provider/model supports moderation, structured vision output, guess judging, and speech for the language.

Keep these invariants unchanged:

- strict structured output with unknown fields rejected;
- no caller-selected provider, model, prompt, voice, or upstream URL;
- bounded request bodies and timeouts;
- owner-only provider credentials that never appear in status or logs;
- generation cancellation and rejection of late provider results;
- target-category, visibility, confidence, size, colour, and location checks;
- moderation before selection and immediately before speech;
- fail-closed behavior when any required provider capability is unavailable.

If the language needs a localized target name in addition to the existing target identifier, add a named field to the strict schema and moderate it before storage or speech. Do not accept arbitrary provider fields.

## 7. Build a native-language safety review set

Automated provider moderation is not enough. Create a review set with a fluent adult that includes:

- normal household objects;
- people and family-role words;
- faces, body parts, clothing, and identity terms;
- screens, documents, addresses, names, and other private information;
- medicine, alcohol, tobacco, weapons, and hazards;
- ambiguous words with both safe and unsafe meanings;
- slang, diminutives, misspellings, accents, transliteration, and mixed-language phrases;
- prompt-injection attempts inside target names, locations, hints, and guesses.

Add deterministic vocabulary where appropriate, but do not rely on keyword matching alone. Every unsafe or uncertain case must reject or fail closed.

Do not commit real captured frames, child audio, transcripts, names, or household details as fixtures. Use synthetic text and non-private test assets only.

## 8. Extend automated tests

Add tests proving the new language works across the complete boundary:

### API and game state

- API accepts the new code and rejects unknown codes.
- Every approved colour has a translation.
- Every required phrase exists; no English/Dutch fallback occurs.
- Search, clue, hint, correct guess, reveal, Stop, escaped-target, and safe-failure messages use the selected language.
- Guess normalization handles the language’s script and diacritics intentionally.

### Provider and safety boundary

- target validation requires and moderates the new hints field;
- strict schema still rejects missing and extra fields;
- guess judging receives the correct code;
- TTS receives the explicit language instruction;
- unsafe target names, locations, hints, and guesses fail closed;
- unsupported provider capability does not trigger a fallback provider or language.

### UI and speech

- the language selector contains the native display name;
- the correct BCP 47 locale is selected;
- typed guesses still work without browser speech recognition;
- right-to-left or native-script behavior is covered when applicable;
- Stop remains visible and authoritative.

### Embodied lifecycle

- camera capture remains capped at three transient frames;
- Stop revokes camera access and invalidates late results;
- no late speech or motion occurs after cancellation;
- reveal/failure returns safely and ends with camera off;
- shutdown folds Reachy and disables motors.

Run the full gates:

```sh
uv sync --extra dev
uv run pytest
uv run ruff check .
uv build
uv run python scripts/check_artifacts.py dist/*
reachy-mini-app-assistant check .
```

Inspect the built wheel and sdist for credentials, local configuration, captured media, private identifiers, caches, and unintended files.

## 9. Perform human language QA

Before hardware acceptance, have a fluent adult review:

- every UI and game phrase in context;
- age appropriateness for all three age bands;
- colour and object vocabulary;
- hint naturalness and whether clues accidentally reveal the answer;
- pronunciation, pace, and intelligibility of the configured TTS voice;
- recognition accuracy for child and adult speech where browser speech is supported;
- grammatical agreement after substitutions;
- mixed-language and malformed provider output;
- safety terms, slang, and regional variants.

Machine translation alone is not sufficient for a child-facing release.

## 10. Physically accept the new artifact

Build a new version and record its exact digest. On a supervised Reachy Mini, verify at least one complete round and the critical failure paths in the new language:

1. Camera opt-in is required for each start.
2. Search movement and three-frame capture remain bounded.
3. Intro, colour clue, hints, guesses, reveal, and TTS use the selected language.
4. Typed guesses work even if speech recognition is unavailable.
5. Unsafe, malformed, untranslated, or mixed-language provider responses fail closed.
6. Stop works during search, provider processing, guessing, and speech.
7. Camera access is revoked after reveal, failure, or Stop.
8. No late speech or motion occurs after Stop.
9. Rollback installation works.
10. Reachy finishes folded with motors disabled and no active movement.

Record:

- package version and filename;
- SHA-256 digest;
- Reachy hardware and SDK version;
- language code and BCP 47 locale;
- provider models and voice used;
- automated gate results;
- fluent reviewer;
- physical acceptance date and observed outcomes.

Publish only the exact artifact that passed those checks. The original `0.1.0` English/Dutch acceptance does not transfer to a translated build, fork, rebuilt wheel, or later source commit.

## Pull-request checklist

- [ ] One new language and one stable internal code
- [ ] Native display name and valid BCP 47 locale
- [ ] Explicit phrase table with no two-language fallthrough
- [ ] Complete colour and hint translations
- [ ] Native-language safety vocabulary and review set
- [ ] Strict broker schema and moderation updated
- [ ] Guess judging and TTS receive an explicit language
- [ ] Typed-input fallback retained
- [ ] RTL/native-script behavior handled when applicable
- [ ] Full automated gates pass
- [ ] Built artifacts pass the publication-boundary scan
- [ ] Fluent adult review completed
- [ ] New exact artifact physically accepted before release claims
