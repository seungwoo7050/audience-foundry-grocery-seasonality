async (page) => {
  const baseUrl = "http://127.0.0.1:8000";
  const requiredAxeVersion = "4.13.0";
  const expectedScanCount = 7;
  const configuredAxePath =
    typeof process !== "undefined" && process.env && process.env.AXE_CORE_PATH
      ? process.env.AXE_CORE_PATH
      : ".cache/axe/axe.min.js";
  const externalRequests = [];

  function assert(condition, message) {
    if (!condition) throw new Error(message);
  }

  assert(configuredAxePath.length > 0, "AXE_CORE_PATH must not be empty");
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

  function relativeUrl(value) {
    const parsed = new URL(value, baseUrl);
    assert(parsed.origin === baseUrl, "axe navigation escaped the loopback origin");
    return `${parsed.pathname}${parsed.search}`;
  }

  async function goto(path, expectedStatus, label) {
    const response = await page.goto(`${baseUrl}${path}`, { waitUntil: "networkidle" });
    assert(response !== null, `${label} navigation response missing`);
    assert(
      response.status() === expectedStatus,
      `${label} returned ${response.status()}, expected ${expectedStatus}`,
    );
    const headers = await response.allHeaders();
    assert(
      (headers["cache-control"] || "")
        .split(",")
        .some((directive) => directive.trim().toLowerCase() === "no-store"),
      `${label} must be no-store`,
    );
    await page.evaluate(async () => {
      if (document.fonts) await document.fonts.ready;
    });
  }

  async function scan(name, path, expectedStatus = 200) {
    await goto(path, expectedStatus, name);
    const report = await page.evaluate(async (expectedVersion) => {
      if (!window.axe) throw new Error("local axe-core did not load");
      if (window.axe.version !== expectedVersion) {
        throw new Error(`axe-core ${expectedVersion} required, received ${window.axe.version}`);
      }
      window.axe.configure({ rules: [{ id: "target-size", enabled: true }] });
      const report = await window.axe.run(document, {
        resultTypes: ["violations", "incomplete", "passes"],
        runOnly: {
          type: "tag",
          values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"],
        },
      });
      const reviewedIncomplete = [];
      const unexpectedIncomplete = [];
      for (const result of report.incomplete) {
        const isReviewedContrast =
          result.id === "color-contrast" &&
          result.nodes.every((node) =>
            node.target
              .flat(Number.POSITIVE_INFINITY)
              .filter((selector) => typeof selector === "string")
              .every((selector) => {
                const element = document.querySelector(selector);
                return (
                  element?.getAttribute("aria-hidden") === "true" ||
                  element?.matches("text.history-chart__label") === true
                );
              }),
          );
        (isReviewedContrast ? reviewedIncomplete : unexpectedIncomplete).push(result);
      }
      return {
        incomplete: unexpectedIncomplete,
        passes: report.passes,
        reviewedIncomplete,
        violations: report.violations,
      };
    }, requiredAxeVersion);
    const violations = report.violations.map((violation) => ({
      help: violation.help,
      id: violation.id,
      impact: violation.impact,
      nodes: violation.nodes.map((node) => ({
        failureSummary: node.failureSummary,
        target: node.target,
      })),
    }));
    const incomplete = report.incomplete.map((result) => ({
      id: result.id,
      impact: result.impact,
      nodes: result.nodes.map((node) => ({
        failureSummary: node.failureSummary,
        target: node.target,
      })),
    }));
    assert(violations.length === 0, `${name} axe violations: ${JSON.stringify(violations)}`);
    assert(
      incomplete.length === 0,
      `${name} axe incomplete results require review: ${JSON.stringify(incomplete)}`,
    );
    return {
      name,
      passes: report.passes.length,
      reviewedContrastNodes: report.reviewedIncomplete.reduce(
        (total, result) => total + result.nodes.length,
        0,
      ),
      viewport: "390x844",
    };
  }

  await page.setViewportSize({ width: 390, height: 844 });
  const scans = [];
  scans.push(await scan("catalog-ready", "/"));
  const detailPath = await page.locator(".ledger-entry__link").first().getAttribute("href");
  assert(detailPath !== null && detailPath.startsWith("/series/"), "detail URL missing");

  scans.push(await scan("detail-ready", detailPath));
  const historyPath = await page
    .getByRole("link", { name: "월별 기록", exact: true })
    .getAttribute("href");
  assert(historyPath !== null, "history URL missing");
  await goto(historyPath, 200, "history-region-selection");
  const regionSelect = page.locator("#history-region");
  const regionValue = await regionSelect
    .locator('option[value]:not([value=""])')
    .first()
    .getAttribute("value");
  assert(regionValue !== null, "history region option missing");
  await regionSelect.selectOption(regionValue);
  const [historyResponse] = await Promise.all([
    page.waitForNavigation({ waitUntil: "networkidle" }),
    page.getByRole("button", { name: "월별 기록 보기", exact: true }).click(),
  ]);
  assert(historyResponse !== null && historyResponse.status() === 200, "history submit failed");
  const readyHistoryPath = relativeUrl(page.url());
  scans.push(await scan("history-ready", readyHistoryPath));

  const regionsPath = await page
    .getByRole("link", { name: "지역별 조사값", exact: true })
    .getAttribute("href");
  assert(regionsPath !== null, "regions URL missing");
  scans.push(await scan("regions-ready", regionsPath));
  const marketsPath = await page
    .getByRole("link", { name: /시장별 값 보기/ })
    .first()
    .getAttribute("href");
  assert(marketsPath !== null, "markets URL missing");
  scans.push(await scan("markets-ready", marketsPath));

  await goto(detailPath, 200, "detail-selection-link");
  const selectionPath = await page
    .getByRole("link", { name: "선택 목록에 담기", exact: true })
    .getAttribute("href");
  assert(selectionPath !== null, "selection URL missing");
  await goto(selectionPath, 200, "selection-add-form");
  const candidateSelect = page.locator("#selection-add-item");
  const candidateValue = await candidateSelect
    .locator('option[value]:not([value=""])')
    .first()
    .getAttribute("value");
  assert(candidateValue !== null, "selection candidate missing");
  await candidateSelect.selectOption(candidateValue);
  const [selectionResponse] = await Promise.all([
    page.waitForNavigation({ waitUntil: "networkidle" }),
    page.getByRole("button", { name: "선택 목록에 추가", exact: true }).click(),
  ]);
  assert(
    selectionResponse !== null && selectionResponse.status() === 200,
    "selection add failed",
  );
  const readySelectionPath = relativeUrl(page.url());
  scans.push(await scan("selection-ready", readySelectionPath));
  assert((await page.locator(".selection-row").count()) === 2, "selection must show two rows");
  assert(
    new URL(page.url()).searchParams.getAll("series").length === 2,
    "selection URL must carry both items",
  );
  scans.push(await scan("catalog-validation", "/?page=01", 400));

  assert(
    scans.length === expectedScanCount,
    `vNext axe scan matrix incomplete: ${scans.length}/${expectedScanCount}`,
  );
  assert(
    externalRequests.length === 0,
    `external requests observed: ${JSON.stringify([...new Set(externalRequests)])}`,
  );
  return {
    axeSource: configuredAxePath,
    axeVersion: requiredAxeVersion,
    scanCount: scans.length,
    scans,
    viewport: "390x844",
  };
}
