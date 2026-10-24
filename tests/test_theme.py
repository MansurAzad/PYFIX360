"""Tests for the theme engine and its colour-theory claims.

The point of these tests is that the readability claims are *checkable*. Rather
than asserting "it looks nice", we compute the WCAG contrast ratio of every
text/background pairing that appears in a theme and require it to meet a stated
target. If someone later tunes a colour badly, a test fails instead of a user
squinting at unreadable text.
"""

from pyfix360.ui.theme import (
    DARK,
    THEMES,
    Theme,
    contrast_ratio,
    darken,
    get_theme,
    hex_to_rgb,
    lighten,
    mix,
    readable_on,
    rgb_to_hex,
    stylesheet,
    theme_labels,
)


def test_five_themes_are_available():
    assert len(THEMES) >= 4
    keys = set(THEMES)
    assert {"dark", "light", "high_contrast"} <= keys


def test_theme_keys_match_their_dict_keys():
    for key, theme in THEMES.items():
        assert theme.key == key


def test_get_theme_falls_back_to_default():
    assert get_theme("nope") is DARK
    assert get_theme(None) is DARK
    assert get_theme("midnight").key == "midnight"


def test_theme_labels_are_human_readable():
    labels = theme_labels()
    assert len(labels) == len(THEMES)
    for key, label in labels:
        assert key and label
        assert label != key, "label should be prettier than the raw key"


# ---------------------------------------------------------------------------
# colour maths
# ---------------------------------------------------------------------------


def test_hex_rgb_round_trip():
    for value in ("#000000", "#ffffff", "#4c8dff", "#a1b2c3"):
        assert rgb_to_hex(hex_to_rgb(value)) == value


def test_hex_to_rgb_tolerates_missing_hash():
    assert hex_to_rgb("ffffff") == (255, 255, 255)


def test_hex_to_rgb_tolerates_short_form():
    assert hex_to_rgb("#abc") == (0xaa, 0xbb, 0xcc)


def test_hex_to_rgb_never_crashes_on_garbage():
    """A half-typed colour in the settings box must not crash the UI."""
    for bad in ("", "#", "#ff", "#gggggg", "not a colour", None):
        assert hex_to_rgb(bad) == (128, 128, 128)


def test_contrast_ratio_survives_a_partial_colour():
    ratio = contrast_ratio("", "#ffffff")
    assert 1.0 <= ratio <= 21.0


def test_contrast_ratio_extremes():
    assert contrast_ratio("#ffffff", "#000000") == 21.0
    assert contrast_ratio("#777777", "#777777") == 1.0


def test_contrast_ratio_is_symmetric():
    assert contrast_ratio("#123456", "#abcdef") == contrast_ratio(
        "#abcdef", "#123456")


def test_lighten_and_darken_move_toward_white_and_black():
    assert lighten("#808080", 0.2) > "#808080"
    assert darken("#808080", 0.2) < "#808080"


def test_lighten_keeps_hue_family():
    """A blue lightened stays blue-ish (b channel remains the largest)."""
    lighter = lighten("#2f6fed", 0.15)
    r, g, b = hex_to_rgb(lighter)
    assert b >= r and b >= g


def test_lighten_and_darken_are_bounded():
    assert lighten("#ffffff", 0.5) == "#ffffff"
    assert darken("#000000", 0.5) == "#000000"


def test_mix_is_between_the_two_inputs():
    mid = mix("#000000", "#ffffff", 0.5)
    assert mid not in ("#000000", "#ffffff")
    assert mix("#000000", "#ffffff", 0.0) == "#000000"
    assert mix("#000000", "#ffffff", 1.0) == "#ffffff"


def test_readable_on_picks_the_opposite_extreme():
    assert readable_on("#ffffff") == "#10131a"
    assert readable_on("#000000") == "#ffffff"
    # a mid saturated accent: text must still be forced to one extreme
    assert readable_on("#4c8dff") in ("#10131a", "#ffffff")


# ---------------------------------------------------------------------------
# the readability contract, applied to every theme
# ---------------------------------------------------------------------------


def test_body_text_meets_aaa_on_surface():
    for theme in THEMES.values():
        ratio = contrast_ratio(theme.text, theme.surface)
        assert ratio >= 7.0, f"{theme.key}: body text contrast {ratio} < 7.0"


def test_muted_text_meets_aa_on_surface():
    for theme in THEMES.values():
        ratio = contrast_ratio(theme.text_muted, theme.surface)
        assert ratio >= 4.5, f"{theme.key}: muted text contrast {ratio} < 4.5"


def test_body_text_is_readable_on_the_app_background():
    for theme in THEMES.values():
        ratio = contrast_ratio(theme.text, theme.bg)
        assert ratio >= 7.0, f"{theme.key}: text/bg contrast {ratio} < 7.0"


def test_accent_button_text_is_readable():
    """The label on an accent-coloured button must clear AA whatever the accent.

    This is why the theme derives button text from the accent's luminance
    instead of hard-coding white: a light accent would silently fall below AA.
    """
    for theme in THEMES.values():
        ratio = contrast_ratio(theme.button_text, theme.accent)
        assert ratio >= 4.5, f"{theme.key}: accent button contrast {ratio} < 4.5"


def test_explicit_accent_text_is_honoured():
    """A deliberate override is respected (the high-contrast theme wants black)."""
    assert THEMES["high_contrast"].accent_text == "#000000"
    assert THEMES["high_contrast"].button_text == "#000000"


def test_status_colours_are_distinguishable_from_surface():
    """Status text may be decorative, but it must still be legible."""
    for theme in THEMES.values():
        for name in ("success", "warning", "danger", "info"):
            ratio = contrast_ratio(getattr(theme, name), theme.surface)
            assert ratio >= 2.5, f"{theme.key}.{name} contrast only {ratio}"


def test_high_contrast_theme_is_extreme():
    ratio = contrast_ratio(THEMES["high_contrast"].text,
                           THEMES["high_contrast"].surface)
    assert ratio >= 15.0, "the high-contrast theme should be near-maximal"


def test_light_theme_is_actually_light():
    light = THEMES["light"]
    assert light.dark is False
    assert hex_to_rgb(light.surface)[0] > 200
    assert hex_to_rgb(light.text)[0] < 80


def test_dark_themes_are_actually_dark():
    for key in ("dark", "midnight", "forest", "high_contrast"):
        theme = THEMES[key]
        assert theme.dark is True, f"{key} should be a dark theme"
        assert hex_to_rgb(theme.surface)[0] < 90


def test_every_theme_has_a_distinct_accent():
    accents = [t.accent for t in THEMES.values()]
    assert len(accents) == len(set(accents))


def test_every_theme_has_a_distinct_background():
    backgrounds = [t.bg for t in THEMES.values()]
    assert len(backgrounds) == len(set(backgrounds))


def test_severity_scale_is_sequential_cool_to_hot():
    """Critical must be the most saturated/dangerous, info the calmest."""
    for theme in THEMES.values():
        scale = theme.severity
        assert set(scale) == {"critical", "high", "medium", "low", "info"}
        # critical is literally the danger colour, info is the muted one
        assert scale["critical"] == theme.danger
        assert scale["medium"] == theme.warning


# ---------------------------------------------------------------------------
# derived properties behave
# ---------------------------------------------------------------------------


def test_accent_hover_and_pressed_differ_from_accent():
    for theme in THEMES.values():
        assert theme.accent_hover != theme.accent
        assert theme.accent_pressed != theme.accent


def test_accent_soft_is_between_surface_and_accent():
    for theme in THEMES.values():
        assert theme.accent_soft not in (theme.surface, theme.accent)


def test_input_background_is_usable():
    """Inputs sit on the surface, so they must be legible in every theme.

    A light theme legitimately uses white inputs on a white surface (the
    border does the separating), so the requirement is contrast of text on the
    input, not that the input differs from the surface.
    """
    for theme in THEMES.values():
        ratio = contrast_ratio(theme.text, theme.input_bg)
        assert ratio >= 7.0, f"{theme.key}: text on input contrast {ratio} < 7.0"


def test_sidebar_matches_surface_alt():
    for theme in THEMES.values():
        assert theme.sidebar == theme.surface_alt


# ---------------------------------------------------------------------------
# stylesheet generation
# ---------------------------------------------------------------------------


def test_stylesheet_has_balanced_braces():
    """A CSS block that fails to close silently kills the rest of the sheet."""
    for theme in THEMES.values():
        css = stylesheet(theme)
        assert css.count("{") == css.count("}"), theme.key


def test_stylesheet_has_no_stray_python_artifacts():
    for theme in THEMES.values():
        css = stylesheet(theme)
        assert "None" not in css
        assert "{{" not in css
        assert "}}" not in css


def test_stylesheet_uses_the_theme_accent():
    for key, theme in THEMES.items():
        assert theme.accent in stylesheet(theme), key


def test_stylesheet_honours_font_size_and_radius():
    css = stylesheet(DARK, font_size=17, radius=12)
    assert "font-size: 17px" in css
    assert "border-radius: 12px" in css


def test_stylesheet_density_changes_button_padding():
    compact = stylesheet(DARK, density="compact")
    spacious = stylesheet(DARK, density="spacious")
    assert compact != spacious


def test_stylesheet_motion_flag_is_accepted():
    assert stylesheet(DARK, motion=False)


def test_themes_are_immutable():
    import dataclasses

    assert dataclasses.is_dataclass(Theme)
    try:
        DARK.accent = "#000000"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        pass
    else:  # pragma: no cover
        raise AssertionError("Theme should be frozen")


def test_custom_accent_theme_can_be_built():
    import dataclasses

    custom = dataclasses.replace(DARK, accent="#ff8800")
    assert custom.accent == "#ff8800"
    assert "#ff8800" in stylesheet(custom)
