from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_public_positioning_is_standalone_with_hermes_origin() -> None:
    readme = (ROOT / "README.md").read_text()
    assert "camera-opt-in English/Dutch I Spy game" in readme
    assert "originated from the I Spy experience" in readme
    assert "Hermes Agent and Reachy Mini Hermes are not runtime dependencies" in readme
    assert "Reachy Mini Lite" in readme
    assert "connected Mac/PC" in readme
    assert "Reachy Mini Wireless" in readme
    assert "Raspberry Pi CM4" in readme


def test_language_guide_covers_any_language_end_to_end() -> None:
    guide = (ROOT / "docs" / "ADDING_A_LANGUAGE.md").read_text()
    required = (
        "# Add any language",
        "BCP 47",
        "right-to-left",
        "strict structured output",
        "native-language safety review set",
        "fluent adult",
        "Physically accept the new artifact",
        "The original `0.1.0` English/Dutch acceptance does not transfer",
    )
    for text in required:
        assert text in guide


def test_reachy_settings_present_standalone_provider_and_compute_profiles() -> None:
    page = (ROOT / "reachy_mini_i_spy" / "static" / "index.html").read_text()
    script = (ROOT / "reachy_mini_i_spy" / "static" / "main.js").read_text()
    api = (ROOT / "reachy_mini_i_spy" / "main.py").read_text()
    assert "Standalone vision settings" in page
    assert "OpenAI API key" in page
    assert "provider broker" in page
    assert "connected Mac/PC" in script
    assert "onboard CM4" in script
    assert "broker URL" not in page
    assert "Scoped broker token" not in page
    combined = page + script + api
    for obsolete in ("caregiver", "csrf", "X-I-Spy-CSRF", "CaregiverGuard", "authorizedRequest"):
        assert obsolete.casefold() not in combined.casefold()
