from pathlib import Path

from django.conf import settings


def _script(name: str) -> str:
    return Path(settings.BASE_DIR, "scripts", name).read_text(encoding="utf-8")


def test_redesign_browser_acceptance_covers_viewports_states_and_interactions() -> None:
    script = _script("browser_acceptance.js")

    assert "output/playwright/redesign-v1" in script
    assert "output/playwright/phase0" not in script
    for viewport in ("360x800", "390x844", "768x1024", "1440x900"):
        assert viewport in script
    for state in ("loading", "empty", "unavailable", "stale", "server_error"):
        assert f"/__qa__/catalog/{state}/" in script
    for state in ("loading", "unavailable", "stale", "server_error"):
        assert f"/__qa__/detail/{state}/" in script
    for status in (400, 403, 404, 500):
        assert f"/__qa__/catalog/error_{status}/" in script
    for contract in (
        "horizontalOverflow",
        "undersized",
        "externalRequests",
        "failedRequests.length === 0",
        "failedSubresources.length === 0",
        "eventHandlers",
        "rasterImages",
        "assertKeyboardFocus",
        "assertDesktopInteractions",
        "assertComparisonRowsAreNonInteractive",
        "rowGeometry.height <= 168",
        "rowGeometry.bottom <= viewport.height",
        ".direction--lower",
        ".direction--higher",
        ".direction--equal",
        "KAMIS에서 제공하지 않음",
        "품목명은 한 줄로 입력하세요.",
    ):
        assert contract in script


def test_axe_acceptance_is_pinned_local_and_covers_the_same_surface() -> None:
    script = _script("axe_browser_acceptance.js")

    assert 'requiredAxeVersion = "4.13.0"' in script
    assert "process.env.AXE_CORE_PATH" in script
    assert '".cache/axe/axe.min.js"' in script
    assert "page.addInitScript({ path: configuredAxePath })" in script
    assert 'id: "target-size", enabled: true' in script
    assert "page.addScriptTag({ url:" not in script
    assert "https://cdn" not in script.lower()
    assert "output/playwright/redesign-v1" in script
    for viewport in ("360x800", "390x844", "768x1024", "1440x900"):
        assert viewport in script
    for tag in ("wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"):
        assert f'"{tag}"' in script
    for status in (400, 403, 404, 500):
        assert f"/__qa__/catalog/error_{status}/" in script
    assert "incomplete.length === 0" in script
    assert "scans.length === expectedScanCount" in script
    assert "expectedScanCount = 62" in script
    assert "violations.length === 0" in script
    assert "externalRequests.length === 0" in script
