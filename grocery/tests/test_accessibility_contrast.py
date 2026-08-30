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


def test_rendered_text_palette_meets_wcag_aa_including_gradient_extremes() -> None:
    css = Path(settings.BASE_DIR, "grocery", "static", "grocery", "app.css").read_text(
        encoding="utf-8"
    )
    colors = {match.group("name"): match.group("value") for match in _CUSTOM_PROPERTY.finditer(css)}
    pairs = (
        (colors["color-text"], colors["color-canvas"]),
        (colors["color-text"], colors["color-surface"]),
        (colors["color-muted"], colors["color-canvas"]),
        (colors["color-muted"], colors["color-surface"]),
        (colors["color-brand"], colors["color-surface"]),
        (colors["color-brand-strong"], colors["color-surface"]),
        (colors["color-info"], colors["color-info-soft"]),
        (colors["color-warning"], colors["color-warning-soft"]),
        (colors["color-error"], colors["color-error-soft"]),
        (colors["color-text"], colors["color-brand-soft"]),
        (colors["color-brand-strong"], colors["color-brand-soft"]),
        ("#235783", colors["color-surface"]),
        ("#8b1e24", colors["color-surface"]),
        ("#ffffff", colors["color-brand-strong"]),
        ("#68141a", colors["color-error-soft"]),
    )

    assert min(_contrast(foreground, background) for foreground, background in pairs) >= 4.5
