from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_public_positioning_is_standalone_with_hermes_origin() -> None:
    readme = (ROOT / "README.md").read_text()
    assert "complete standalone" in readme.lower()
    assert "originated from the I Spy experience" in readme
    assert "its own full application and project" in readme
    assert "neither Hermes Agent nor the Reachy Mini Hermes app is required" in readme


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


def test_reachy_settings_present_the_dedicated_ispy_broker() -> None:
    page = (ROOT / "reachy_mini_i_spy" / "static" / "index.html").read_text()
    assert "Caregiver I Spy broker settings" in page
    assert "I Spy broker URL" in page
    assert "Caregiver Hermes broker settings" not in page
    assert "Hermes broker URL" not in page
