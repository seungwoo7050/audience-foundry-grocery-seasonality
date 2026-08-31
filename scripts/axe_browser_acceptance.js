async (page) => {
  const baseUrl = "http://127.0.0.1:8000";
  const outputDirectory = "output/playwright/redesign-v1";
  const requiredAxeVersion = "4.13.0";
  const expectedScanCount = 62;
  const configuredAxePath =
    typeof process !== "undefined" && process.env && process.env.AXE_CORE_PATH
      ? process.env.AXE_CORE_PATH
      : ".cache/axe/axe.min.js";
  const viewports = [
    { width: 360, height: 800, label: "360x800" },
    { width: 390, height: 844, label: "390x844" },
    { width: 768, height: 1024, label: "768x1024" },
    { width: 1440, height: 900, label: "1440x900" },
  ];
  const catalogStates = [
    { name: "loading", path: "/__qa__/catalog/loading/", status: 200 },
    { name: "empty", path: "/__qa__/catalog/empty/", status: 200 },
    { name: "unavailable", path: "/__qa__/catalog/unavailable/", status: 200 },
    { name: "stale", path: "/__qa__/catalog/stale/", status: 200 },
    { name: "server-error", path: "/__qa__/catalog/server_error/", status: 503 },
  ];
  const detailStates = [
    { name: "loading", path: "/__qa__/detail/loading/", status: 200 },
    { name: "unavailable", path: "/__qa__/detail/unavailable/", status: 200 },
    { name: "stale", path: "/__qa__/detail/stale/", status: 200 },
    { name: "server-error", path: "/__qa__/detail/server_error/", status: 503 },
  ];
  const errorPages = [
    { name: "400", path: "/__qa__/catalog/error_400/", status: 400 },
    { name: "403", path: "/__qa__/catalog/error_403/", status: 403 },
    { name: "404", path: "/__qa__/catalog/error_404/", status: 404 },
    { name: "500", path: "/__qa__/catalog/error_500/", status: 500 },
  ];
  const externalRequests = [];

  function assert(condition, message) {
    if (!condition) throw new Error(message);
  }

  assert(
    !/^(?:https?:)?\/\//i.test(configuredAxePath),
    "AXE_CORE_PATH must be a local filesystem path; CDN injection is prohibited",
  );
  await page.addInitScript({ path: configuredAxePath });
  page.on("request", (request) => {
    const requestUrl = request.url();
    if (!requestUrl.startsWith("http://") && !requestUrl.startsWith("https://")) return;
    const parsed = new URL(requestUrl);
    if (parsed.origin !== baseUrl) externalRequests.push(parsed.origin);
  });

  async function goto(path, expectedStatus) {
    const response = await page.goto(`${baseUrl}${path}`, { waitUntil: "networkidle" });
    assert(response !== null, `missing navigation response for ${path}`);
    assert(
      response.status() === expectedStatus,
      `${path} returned ${response.status()}, expected ${expectedStatus}`,
    );
    await page.evaluate(async () => {
      if (document.fonts) await document.fonts.ready;
    });
  }

  async function scan(name, path, expectedStatus, viewport) {
    await goto(path, expectedStatus);
    const report = await page.evaluate(async (expectedVersion) => {
      if (!window.axe) throw new Error("local axe-core did not load");
      if (window.axe.version !== expectedVersion) {
        throw new Error(`axe-core ${expectedVersion} required, received ${window.axe.version}`);
      }
      window.axe.configure({ rules: [{ id: "target-size", enabled: true }] });
      return window.axe.run(document, {
        runOnly: {
          type: "tag",
          values: [
            "wcag2a",
            "wcag2aa",
            "wcag21a",
            "wcag21aa",
            "wcag22aa",
          ],
        },
        resultTypes: ["violations", "incomplete", "passes"],
      });
    }, requiredAxeVersion);

    const violations = report.violations.map((violation) => ({
      id: violation.id,
      impact: violation.impact,
      help: violation.help,
      nodes: violation.nodes.map((node) => ({
        target: node.target,
        failureSummary: node.failureSummary,
      })),
    }));
    assert(
      violations.length === 0,
      `${viewport.label} ${name} axe violations: ${JSON.stringify(violations)}`,
    );
    const incomplete = report.incomplete.map((result) => ({
      id: result.id,
      impact: result.impact,
      nodes: result.nodes.map((node) => ({
        target: node.target,
        failureSummary: node.failureSummary,
      })),
    }));
    assert(
      incomplete.length === 0,
      `${viewport.label} ${name} axe incomplete results require review: ${JSON.stringify(incomplete)}`,
    );

    return {
      name,
      path,
      status: expectedStatus,
      viewport: viewport.label,
      violations: 0,
      incomplete: [],
      passes: report.passes.length,
    };
  }

  const scans = [];
  for (const viewport of viewports) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    scans.push(await scan("catalog-ready", "/", 200, viewport));
    const detailPath = await page.locator(".ledger-entry__link").first().getAttribute("href");
    assert(
      detailPath !== null && detailPath.startsWith("/series/"),
      `${viewport.label} stable detail URL missing`,
    );
    scans.push(await scan("detail-ready", detailPath, 200, viewport));

    for (const state of catalogStates) {
      scans.push(await scan(`catalog-${state.name}`, state.path, state.status, viewport));
    }
    for (const state of detailStates) {
      scans.push(await scan(`detail-${state.name}`, state.path, state.status, viewport));
    }
    for (const errorPage of errorPages) {
      scans.push(await scan(`error-${errorPage.name}`, errorPage.path, errorPage.status, viewport));
    }

    if (viewport.width <= 390) {
      const invalidPath = `/?q=${encodeURIComponent("검증오류\u200b표시")}`;
      scans.push(await scan("catalog-validation", invalidPath, 400, viewport));
    }
  }

  assert(
    externalRequests.length === 0,
    `external requests observed: ${JSON.stringify([...new Set(externalRequests)])}`,
  );
  assert(
    scans.length === expectedScanCount,
    `axe scan matrix incomplete: ${scans.length}/${expectedScanCount}`,
  );
  return {
    axeVersion: requiredAxeVersion,
    axeSource: configuredAxePath,
    outputDirectory,
    scanCount: scans.length,
    scans,
  };
}
