import re
from pathlib import Path

from django.conf import settings

_CUSTOM_PROPERTY = re.compile(r"--(?P<name>[a-z-]+):\s*(?P<value>#[0-9a-fA-F]{6});")


def _luminance(hex_color: str) -> float:
    channels = [int(hex_color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(foreground: str, background: str) -> float:
    lighter, darker = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _colors() -> dict[str, str]:
    css = Path(settings.BASE_DIR, "grocery", "static", "grocery", "app.css").read_text(
        encoding="utf-8"
    )
    return {match.group("name"): match.group("value") for match in _CUSTOM_PROPERTY.finditer(css)}


def test_rendered_text_palette_meets_wcag_aa_across_ledger_and_state_surfaces() -> None:
    colors = _colors()
    pairs = (
        (colors["color-text"], colors["color-canvas"]),
        (colors["color-text"], colors["color-surface"]),
        (colors["color-text"], colors["color-surface-muted"]),
        (colors["color-text"], colors["color-neutral-soft"]),
        (colors["color-muted"], colors["color-canvas"]),
        (colors["color-muted"], colors["color-surface"]),
        (colors["color-brand"], colors["color-surface"]),
        (colors["color-brand-strong"], colors["color-surface"]),
        (colors["color-brand-strong"], colors["color-brand-soft"]),
        (colors["color-info"], colors["color-info-soft"]),
        (colors["color-warning"], colors["color-warning-soft"]),
        (colors["color-error"], colors["color-error-soft"]),
        (colors["color-text"], colors["color-brand-soft"]),
        (colors["color-lower"], colors["color-surface"]),
        (colors["color-higher"], colors["color-surface"]),
        (colors["color-on-brand"], colors["color-brand"]),
        (colors["color-on-brand"], colors["color-brand-strong"]),
    )

    assert min(_contrast(foreground, background) for foreground, background in pairs) >= 4.5


def test_focus_and_interactive_boundaries_meet_non_text_contrast() -> None:
    colors = _colors()
    pairs = (
        (colors["color-focus"], colors["color-canvas"]),
        (colors["color-focus"], colors["color-surface"]),
        (colors["color-focus"], colors["color-brand-soft"]),
        (colors["color-focus-on-dark"], colors["color-brand-strong"]),
        (colors["color-border-strong"], colors["color-canvas"]),
        (colors["color-border-strong"], colors["color-surface"]),
    )

    assert min(_contrast(foreground, background) for foreground, background in pairs) >= 3


def test_selected_segment_and_header_link_hover_text_remains_legible() -> None:
    colors = _colors()

    # Both selected segments and the masthead selection link use this hover pair.
    assert _contrast(colors["color-on-brand"], colors["color-brand"]) >= 4.5


def test_price_direction_tokens_use_one_neutral_data_color() -> None:
    colors = _colors()

    assert colors["color-lower"] == colors["color-higher"] == colors["color-data"]
