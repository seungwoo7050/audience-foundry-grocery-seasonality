async (page) => {
  const baseUrl = "http://127.0.0.1:8000";
  const outputDirectory = "output/playwright/vnext-redesign-v2";
  const screenshotPaths = [];
  const consoleErrors = [];
  const externalRequests = [];
  const failedRequests = [];
  const failedSubresources = [];
  let recentFactSet = null;
  let historicalFactSet = null;

  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push("console-error");
  });
  page.on("request", (request) => {
    const requestUrl = request.url();
    if (!requestUrl.startsWith("http://") && !requestUrl.startsWith("https://")) return;
    if (requestUrl !== baseUrl && !requestUrl.startsWith(`${baseUrl}/`)) {
      externalRequests.push("external-origin");
    }
  });
  page.on("requestfailed", (request) => {
    failedRequests.push(request.resourceType());
  });
  page.on("response", (response) => {
    const request = response.request();
    if (response.status() < 400 || request.resourceType() === "document") return;
    failedSubresources.push({
      resourceType: request.resourceType(),
      status: response.status(),
    });
  });

  function assert(condition, message) {
    if (!condition) throw new Error(message);
  }

  function relativeUrl(value) {
    assert(typeof value === "string", "navigation target must be a string");
    if (value === baseUrl) return "/";
    if (value.startsWith(`${baseUrl}/`)) return value.slice(baseUrl.length);
    assert(
      value.startsWith("/") && !value.startsWith("//") && !/[\r\n]/.test(value),
      "navigation escaped the loopback origin",
    );
    return value;
  }

  async function waitForFonts() {
    await page.evaluate(async () => {
      if (document.fonts) await document.fonts.ready;
    });
  }

  async function assertResponse(response, label, scope = "recent", expectedStatus = 200) {
    assert(response !== null, `${label} navigation response missing`);
    assert(
      response.status() === expectedStatus,
      `${label} returned ${response.status()}, expected ${expectedStatus}`,
    );
    const headers = await response.allHeaders();
    const cacheDirectives = (headers["cache-control"] || "")
      .split(",")
      .map((directive) => directive.trim().split("=", 1)[0].toLowerCase());
    assert(cacheDirectives.includes("no-store"), `${label} must be no-store`);
    assert(headers["referrer-policy"] === "no-referrer", `${label} referrer policy changed`);
    assert(
      (headers["content-security-policy"] || "").includes("script-src 'none'"),
      `${label} script CSP changed`,
    );
    assert(headers["x-content-type-options"] === "nosniff", `${label} nosniff missing`);
    assert(headers["x-frame-options"] === "DENY", `${label} frame boundary changed`);
    assert(!("set-cookie" in headers), `${label} must not create a session cookie`);

    const recent = headers["x-publication-fact-set"] || "";
    assert(/^[0-9a-f]{64}$/.test(recent), `${label} recent fact-set header missing`);
    if (recentFactSet === null) recentFactSet = recent;
    assert(recent === recentFactSet, `${label} recent publication changed during the flow`);

    if (scope === "both") {
      const historical = headers["x-historical-publication-fact-set"] || "";
      assert(/^[0-9a-f]{64}$/.test(historical), `${label} historical fact-set header missing`);
      if (historicalFactSet === null) historicalFactSet = historical;
      assert(
        historical === historicalFactSet,
        `${label} historical publication changed during the flow`,
      );
    } else {
      assert(
        !("x-historical-publication-fact-set" in headers),
        `${label} unexpectedly mixed historical publication state`,
      );
    }
    await waitForFonts();
    return headers;
  }

  async function goto(path, label, scope = "recent", expectedStatus = 200) {
    let response;
    try {
      response = await page.goto(`${baseUrl}${path}`, { waitUntil: "networkidle" });
    } catch (_error) {
      throw new Error(`${label} navigation failed`);
    }
    await assertResponse(response, label, scope, expectedStatus);
    return response;
  }

  async function follow(locator, label, scope = "recent") {
    assert((await locator.count()) === 1, `${label} must have one navigation target`);
    const [response] = await Promise.all([
      page.waitForNavigation({ waitUntil: "networkidle" }),
      locator.click(),
    ]);
    await assertResponse(response, label, scope);
    return relativeUrl(page.url());
  }

  async function submit(button, label, scope = "recent") {
    const [response] = await Promise.all([
      page.waitForNavigation({ waitUntil: "networkidle" }),
      button.click(),
    ]);
    await assertResponse(response, label, scope);
    return relativeUrl(page.url());
  }

  async function resolveHistoricalDetailPath(label) {
    const candidates = await page.locator(".ledger-entry__link").evaluateAll((links) =>
      links
        .slice(0, 30)
        .map((link) => link.getAttribute("href"))
        .filter((value) => typeof value === "string" && value.length > 0),
    );
    assert(candidates.length >= 1, `${label} has no catalog detail candidates`);
    for (let index = 0; index < candidates.length; index += 1) {
      let response;
      try {
        response = await page.goto(`${baseUrl}${relativeUrl(candidates[index])}`, {
          waitUntil: "networkidle",
        });
      } catch (_error) {
        throw new Error(`${label} candidate ${index + 1} navigation failed`);
      }
      assert(response !== null, `${label} candidate ${index + 1} response missing`);
      const candidateHeaders = await response.allHeaders();
      const hasHistoricalHeader = "x-historical-publication-fact-set" in candidateHeaders;
      await assertResponse(
        response,
        `${label} candidate ${index + 1}`,
        hasHistoricalHeader ? "both" : "recent",
      );
      const hasHistoryNavigation =
        (await page.getByRole("link", { name: "월별 기록", exact: true }).count()) === 1;
      assert(
        hasHistoricalHeader === hasHistoryNavigation,
        `${label} candidate ${index + 1} historical navigation/header mismatch`,
      );
      if (hasHistoricalHeader) return relativeUrl(page.url());
    }
    throw new Error(`${label} bounded candidates contain no historical publication`);
  }

  async function assertNoOverflow(label) {
    const dimensions = await page.evaluate(() => ({
      clientWidth: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
    }));
    assert(
      dimensions.scrollWidth <= dimensions.clientWidth,
      `${label} horizontal overflow ${dimensions.scrollWidth}/${dimensions.clientWidth}`,
    );
  }

  async function assertDocumentContract(label) {
    const result = await page.evaluate(() => {
      const targets = [
        ...document.querySelectorAll(
          'a, button, input:not([type="hidden"]), select, summary',
        ),
      ]
        .map((element) => ({ element, rectangle: element.getBoundingClientRect() }))
        .filter(({ rectangle }) => rectangle.width > 0 && rectangle.height > 0);
      const undersized = targets
        .filter(({ rectangle }) => rectangle.width < 44 || rectangle.height < 44)
        .map(({ element, rectangle }) => ({
          height: Math.round(rectangle.height),
          tag: element.tagName,
          width: Math.round(rectangle.width),
        }));
      const externalResources = [
        ...document.querySelectorAll("img[src], link[href], source[src], source[srcset]"),
      ]
        .map(
          (element) =>
            element.getAttribute("src") ||
            element.getAttribute("srcset") ||
            element.getAttribute("href"),
        )
        .filter(Boolean)
        .filter((value) => {
          const resolved = new URL(value, document.baseURI);
          return (
            ["http:", "https:"].includes(resolved.protocol) &&
            resolved.origin !== location.origin
          );
        });
      const eventHandlers = [...document.querySelectorAll("*")].flatMap((element) =>
        [...element.attributes]
          .filter((attribute) => attribute.name.toLowerCase().startsWith("on"))
          .map((attribute) => `${element.tagName.toLowerCase()}[${attribute.name}]`),
      );
      return {
        eventHandlers,
        externalResources,
        h1Count: document.querySelectorAll("h1").length,
        lang: document.documentElement.lang,
        mainCount: document.querySelectorAll("main").length,
        positiveTabIndexes: [...document.querySelectorAll("[tabindex]")]
          .map((element) => Number(element.getAttribute("tabindex")))
          .filter((value) => value > 0),
        scripts: document.scripts.length,
        undersized,
      };
    });
    await assertNoOverflow(label);
    assert(result.mainCount === 1, `${label} must have one main landmark`);
    assert(result.h1Count === 1, `${label} must have one h1`);
    assert(result.lang === "ko", `${label} must declare Korean language`);
    assert(result.scripts === 0, `${label} must remain server-rendered without scripts`);
    assert(
      result.eventHandlers.length === 0,
      `${label} inline event handlers ${JSON.stringify(result.eventHandlers)}`,
    );
    assert(result.positiveTabIndexes.length === 0, `${label} must keep natural keyboard order`);
    assert(
      result.undersized.length === 0,
      `${label} undersized targets ${JSON.stringify(result.undersized)}`,
    );
    assert(
      result.externalResources.length === 0,
      `${label} external resources ${JSON.stringify(result.externalResources)}`,
    );
  }

  async function assertSkipLink(label) {
    await page.evaluate(() => {
      if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
      window.scrollTo(0, 0);
    });
    await page.keyboard.press("Tab");
    const focus = await page.evaluate(() => {
      const element = document.activeElement;
      if (!(element instanceof HTMLElement)) return null;
      const style = getComputedStyle(element);
      const rectangle = element.getBoundingClientRect();
      return {
        className: element.className,
        focusVisible: element.matches(":focus-visible"),
        height: rectangle.height,
        outlineStyle: style.outlineStyle,
        outlineWidth: style.outlineWidth,
        top: rectangle.top,
        width: rectangle.width,
      };
    });
    assert(focus !== null && focus.className.includes("skip-link"), `${label} skip link not first`);
    assert(focus.focusVisible, `${label} skip link is not visibly keyboard-focused`);
    assert(
      focus.outlineStyle !== "none" && focus.outlineWidth !== "0px",
      `${label} skip link focus ring missing`,
    );
    assert(
      focus.top >= 0 && focus.width >= 44 && focus.height >= 44,
      `${label} focused skip link is not visible and touch-sized`,
    );
    await page.keyboard.press("Enter");
    assert(
      (await page.locator("#main-content:focus").count()) === 1,
      `${label} skip target missing`,
    );
  }

  async function tabUntil(selector, label, maximumTabs = 64) {
    await page.evaluate(() => {
      if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
    });
    for (let index = 0; index < maximumTabs; index += 1) {
      await page.keyboard.press("Tab");
      const matched = await page.evaluate(
        (target) =>
          document.activeElement instanceof Element && document.activeElement.matches(target),
        selector,
      );
      if (matched) return;
    }
    throw new Error(`${label} keyboard target was not reached`);
  }

  async function assertFocusedLink(label) {
    const focus = await page.evaluate(() => {
      const element = document.activeElement;
      if (!(element instanceof HTMLAnchorElement)) return null;
      const rectangle = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return {
        focusVisible: element.matches(":focus-visible"),
        height: rectangle.height,
        outlineStyle: style.outlineStyle,
        outlineWidth: style.outlineWidth,
        width: rectangle.width,
      };
    });
    assert(focus !== null, `${label} catalog item link did not receive focus`);
    assert(focus.focusVisible, `${label} catalog item link is not focus-visible`);
    assert(
      focus.outlineStyle !== "none" && focus.outlineWidth !== "0px",
      `${label} catalog item link focus outline missing`,
    );
    assert(
      focus.width >= 44 && focus.height >= 44,
      `${label} focused catalog item link is not touch-sized`,
    );
  }

  async function capture(name) {
    assert(screenshotPaths.length < 8, "redesign v2 screenshot budget exceeded");
    const path = `${outputDirectory}/${name}.png`;
    await page.screenshot({ path, fullPage: true });
    screenshotPaths.push(path);
  }

  async function runReadyFlow(viewport, knownDetailPath = null) {
    const mobileEvidence = viewport.width === 390;
    await page.setViewportSize({ width: viewport.width, height: viewport.height });

    await goto("/", `${viewport.label} catalog`);
    assert((await page.locator(".ledger-entry").count()) >= 2, `${viewport.label} catalog incomplete`);
    await assertDocumentContract(`${viewport.label} catalog`);
    if (mobileEvidence) {
      const firstRecord = await page.locator(".ledger-row").first().boundingBox();
      assert(firstRecord !== null, `${viewport.label} first catalog record missing`);
      assert(firstRecord.y >= 0, `${viewport.label} first catalog record begins above the fold`);
      assert(
        firstRecord.y + firstRecord.height <= viewport.height,
        `${viewport.label} first catalog record is not fully visible before scrolling`,
      );
    }
    await capture(`${viewport.label}-catalog`);
    await assertSkipLink(`${viewport.label} catalog`);

    const detailPath =
      knownDetailPath || (await resolveHistoricalDetailPath(`${viewport.label} historical detail`));
    await goto("/", `${viewport.label} catalog keyboard restart`);
    const detailSelector = `.ledger-entry__link[href="${detailPath}"]`;
    assert((await page.locator(detailSelector).count()) === 1, `${viewport.label} detail link missing`);
    await tabUntil(detailSelector, `${viewport.label} historical catalog item`);
    await assertFocusedLink(`${viewport.label} historical catalog item`);
    const [detailResponse] = await Promise.all([
      page.waitForNavigation({ waitUntil: "networkidle" }),
      page.keyboard.press("Enter"),
    ]);
    await assertResponse(detailResponse, `${viewport.label} detail keyboard navigation`, "both");
    assert(relativeUrl(page.url()) === detailPath, `${viewport.label} detail target changed`);
    assert(
      (await page.locator(".comparison-ledger").count()) === 1,
      `${viewport.label} detail missing`,
    );
    await assertDocumentContract(`${viewport.label} detail`);
    await capture(`${viewport.label}-detail`);

    const historyPath = await follow(
      page.getByRole("link", { name: "월별 기록", exact: true }),
      `${viewport.label} history selection`,
      "both",
    );
    assert(historyPath.startsWith(`${detailPath}history/`), `${viewport.label} history URL changed`);
    const regionSelect = page.locator("#history-region");
    const regionValue = await regionSelect
      .locator('option[value]:not([value=""])')
      .first()
      .getAttribute("value");
    assert(regionValue !== null, `${viewport.label} history region option missing`);
    await regionSelect.selectOption(regionValue);
    const readyHistoryPath = await submit(
      page.getByRole("button", { name: "월별 기록 보기", exact: true }),
      `${viewport.label} history ready`,
      "both",
    );
    assert((await page.locator(".history-chart").count()) === 1, `${viewport.label} chart missing`);
    assert((await page.locator(".month-row").count()) >= 1, `${viewport.label} month rows missing`);
    await assertDocumentContract(`${viewport.label} history`);
    await capture(`${viewport.label}-history`);

    const regionsPath = await follow(
      page.getByRole("link", { name: "지역별 조사값", exact: true }),
      `${viewport.label} regions`,
      "both",
    );
    assert((await page.locator(".region-row").count()) >= 1, `${viewport.label} regions empty`);
    await assertDocumentContract(`${viewport.label} regions`);

    const marketsPath = await follow(
      page.getByRole("link", { name: /시장별 값 보기/ }).first(),
      `${viewport.label} markets`,
      "both",
    );
    assert((await page.locator(".market-row").count()) >= 1, `${viewport.label} markets empty`);
    await assertDocumentContract(`${viewport.label} markets`);

    await follow(
      page.getByRole("link", { name: "최근 조사값", exact: true }),
      `${viewport.label} detail return`,
      "both",
    );
    await follow(
      page.getByRole("link", { name: "선택 목록에 담기", exact: true }),
      `${viewport.label} selection first item`,
    );
    assert(
      (await page.locator(".selection-row").count()) === 1,
      `${viewport.label} first selection missing`,
    );
    const candidateValue = await page
      .locator("#selection-add-item")
      .locator('option[value]:not([value=""])')
      .first()
      .getAttribute("value");
    assert(candidateValue !== null, `${viewport.label} selection candidate missing`);
    await page.locator("#selection-add-item").selectOption(candidateValue);
    const selectionPath = await submit(
      page.getByRole("button", { name: "선택 목록에 추가", exact: true }),
      `${viewport.label} selection add`,
    );
    assert((await page.locator(".selection-row").count()) === 2, `${viewport.label} add failed`);
    const selectedItemCount = await page.evaluate(
      () => new URLSearchParams(window.location.search).getAll("series").length,
    );
    assert(selectedItemCount === 2, `${viewport.label} selection URL must carry two items`);
    await assertDocumentContract(`${viewport.label} selection`);
    await capture(`${viewport.label}-selection`);

    return [
      { name: "catalog", path: "/", scope: "recent" },
      { name: "detail", path: detailPath, scope: "both" },
      { name: "history", path: readyHistoryPath, scope: "both" },
      { name: "regions", path: regionsPath, scope: "both" },
      { name: "markets", path: marketsPath, scope: "both" },
      { name: "selection", path: selectionPath, scope: "recent" },
    ];
  }

  const mobilePaths = await runReadyFlow({ width: 390, height: 844, label: "390x844" });
  for (const viewport of [
    { width: 360, height: 800, label: "360x800" },
    { width: 768, height: 1024, label: "768x1024" },
  ]) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    for (const surface of mobilePaths) {
      await goto(surface.path, `${viewport.label} ${surface.name} overflow`, surface.scope);
      await assertNoOverflow(`${viewport.label} ${surface.name}`);
    }
  }
  const historicalDetailSurface = mobilePaths.find((surface) => surface.name === "detail");
  assert(historicalDetailSurface !== undefined, "historical detail surface missing");
  await runReadyFlow(
    { width: 1440, height: 900, label: "1440x900" },
    historicalDetailSurface.path,
  );

  assert(
    screenshotPaths.length === 8,
    `redesign v2 screenshot matrix incomplete: ${screenshotPaths.length}/8`,
  );
  assert(consoleErrors.length === 0, `browser console error count: ${consoleErrors.length}`);
  assert(externalRequests.length === 0, `external request count: ${externalRequests.length}`);
  assert(failedRequests.length === 0, `failed request count: ${failedRequests.length}`);
  assert(
    failedSubresources.length === 0,
    `failed subresource count: ${failedSubresources.length}`,
  );
  return {
    externalRequests: 0,
    fixtureKind: "live-public-api-normalized-test-publication",
    outputDirectory,
    overflowOnlyViewports: ["360x800", "768x1024"],
    readyFlowViewports: ["390x844", "1440x900"],
    screenshotCount: screenshotPaths.length,
    screenshotSurfaces: ["catalog", "detail", "history", "selection"],
    surfaces: ["catalog", "detail", "history", "regions", "markets", "selection"],
  };
}
