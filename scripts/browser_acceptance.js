async (page) => {
  const baseUrl = "http://127.0.0.1:8000";
  const outputDirectory = "output/playwright/phase0";
  const viewports = [
    { width: 360, height: 800, label: "360x800" },
    { width: 390, height: 844, label: "390x844" },
    { width: 768, height: 1024, label: "768x1024" },
    { width: 1440, height: 900, label: "1440x900" },
  ];
  const catalogStates = [
    { name: "loading", path: "/__qa__/catalog/loading/", status: 200, text: "자료를 불러오는 중" },
    { name: "empty", path: "/__qa__/catalog/empty/", status: 200, text: "조건에 맞는 항목 없음" },
    { name: "unavailable", path: "/__qa__/catalog/unavailable/", status: 200, text: "공개 조사값 없음" },
    { name: "stale", path: "/__qa__/catalog/stale/", status: 200, text: "마지막 검토 자료 표시 중" },
    { name: "server-error", path: "/__qa__/catalog/server_error/", status: 503, text: "자료를 표시하지 못함" },
  ];
  const detailStates = [
    { name: "loading", path: "/__qa__/detail/loading/", status: 200, text: "자료를 불러오는 중" },
    { name: "unavailable", path: "/__qa__/detail/unavailable/", status: 200, text: "공개 조사값 없음" },
    { name: "stale", path: "/__qa__/detail/stale/", status: 200, text: "마지막 검토 자료 표시 중" },
    { name: "server-error", path: "/__qa__/detail/server_error/", status: 503, text: "자료를 표시하지 못함" },
  ];
  const representativeState = {
    "360x800": catalogStates[0],
    "390x844": catalogStates[1],
    "768x1024": catalogStates[2],
    "1440x900": catalogStates[4],
  };
  const consoleErrors = [];
  page.on("console", (message) => {
    const text = message.text();
    const expectedErrorResponse = text.includes("status of 400") || text.includes("status of 503");
    if (message.type() === "error" && !expectedErrorResponse) consoleErrors.push(text);
  });

  function assert(condition, message) {
    if (!condition) throw new Error(message);
  }

  async function goto(path, expectedStatus) {
    const response = await page.goto(`${baseUrl}${path}`, { waitUntil: "networkidle" });
    assert(response !== null, `missing navigation response for ${path}`);
    assert(response.status() === expectedStatus, `${path} returned ${response.status()}, expected ${expectedStatus}`);
  }

  async function assertLayout(label) {
    const result = await page.evaluate(() => {
      const root = document.documentElement;
      const visibleTargets = [...document.querySelectorAll("a, button, input")]
        .map((element) => ({ element, rectangle: element.getBoundingClientRect() }))
        .filter(({ rectangle }) => rectangle.width > 0 && rectangle.height > 0 && rectangle.bottom > 0);
      const undersized = visibleTargets
        .filter(({ rectangle }) => rectangle.height < 44 || rectangle.width < 44)
        .map(({ element, rectangle }) => ({
          tag: element.tagName,
          text: (element.textContent || element.getAttribute("aria-label") || "").trim().slice(0, 40),
          width: Math.round(rectangle.width),
          height: Math.round(rectangle.height),
        }));
      return {
        horizontalOverflow: root.scrollWidth > root.clientWidth,
        clientWidth: root.clientWidth,
        scrollWidth: root.scrollWidth,
        undersized,
        mainCount: document.querySelectorAll("main").length,
        h1Count: document.querySelectorAll("h1").length,
        lang: root.lang,
        positiveTabIndexes: [...document.querySelectorAll("[tabindex]")]
          .map((element) => Number(element.getAttribute("tabindex")))
          .filter((value) => value > 0),
      };
    });
    assert(!result.horizontalOverflow, `${label} horizontal overflow ${result.scrollWidth}/${result.clientWidth}`);
    assert(result.undersized.length === 0, `${label} undersized targets ${JSON.stringify(result.undersized)}`);
    assert(result.mainCount === 1, `${label} must have one main landmark`);
    assert(result.h1Count === 1, `${label} must have one h1`);
    assert(result.lang === "ko", `${label} must declare Korean language`);
    assert(result.positiveTabIndexes.length === 0, `${label} must not reorder keyboard focus`);
  }

  async function assertKeyboardFocus(label) {
    await goto("/", 200);
    await page.evaluate(() => {
      if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
    });
    await page.keyboard.press("Tab");
    const focus = await page.evaluate(() => {
      const element = document.activeElement;
      if (!(element instanceof HTMLElement)) return null;
      const style = getComputedStyle(element);
      const rectangle = element.getBoundingClientRect();
      return {
        className: element.className,
        outlineStyle: style.outlineStyle,
        outlineWidth: style.outlineWidth,
        top: rectangle.top,
        height: rectangle.height,
      };
    });
    assert(focus !== null && focus.className.includes("skip-link"), `${label} first focus must be skip link`);
    assert(focus.outlineStyle !== "none" && focus.outlineWidth !== "0px", `${label} focus must be visible`);
    assert(focus.top >= 0 && focus.height >= 44, `${label} focused skip link must be visible and touch sized`);
    await page.keyboard.press("Enter");
    assert((await page.locator("#main-content").count()) === 1, `${label} skip link target missing`);
  }

  async function assertMobileCorrection(label, firstItemName) {
    await goto("/", 200);
    const invalidQuery = "검증오류\u200b표시";
    await page.getByLabel("공식 품목명").fill(invalidQuery);
    const [invalidResponse] = await Promise.all([
      page.waitForNavigation({ waitUntil: "networkidle" }),
      page.getByRole("button", { name: "검색" }).click(),
    ]);
    assert(invalidResponse !== null && invalidResponse.status() === 400, `${label} invalid search must return 400`);
    const invalidBody = await page.content();
    assert(!invalidBody.includes(invalidQuery), `${label} invalid query was reflected`);
    assert((await page.getByLabel("공식 품목명").inputValue()) === "", `${label} invalid input must be blank`);
    assert((await page.getByLabel("공식 품목명").getAttribute("aria-invalid")) === "true", `${label} invalid input association missing`);
    assert(await page.getByText("검색어에는 줄바꿈이나 제어 문자를 사용할 수 없습니다.").isVisible(), `${label} validation copy missing`);
    await page.screenshot({ path: `${outputDirectory}/${label}-validation.png`, fullPage: true });

    await page.getByLabel("공식 품목명").fill(firstItemName);
    const [correctedResponse] = await Promise.all([
      page.waitForNavigation({ waitUntil: "networkidle" }),
      page.getByRole("button", { name: "검색" }).click(),
    ]);
    assert(correctedResponse !== null && correctedResponse.status() === 200, `${label} corrected search must return 200`);
    assert((await page.locator(".result-card").count()) >= 1, `${label} corrected search has no result`);
    assert((await page.getByLabel("공식 품목명").inputValue()) === "", `${label} valid query must not be echoed`);
  }

  const evidence = [];
  for (const viewport of viewports) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await goto("/", 200);
    assert((await page.getByRole("search").count()) === 1, `${viewport.label} search landmark missing`);
    assert((await page.getByLabel("공식 품목명").count()) === 1, `${viewport.label} search label missing`);
    assert((await page.getByRole("heading", { level: 1 }).count()) === 1, `${viewport.label} heading hierarchy invalid`);
    const firstResult = page.locator(".result-card__link").first();
    assert((await firstResult.count()) === 1, `${viewport.label} actual publication has no catalog result`);
    const firstItemName = (await firstResult.locator("h3").innerText()).trim();
    const detailPath = await firstResult.getAttribute("href");
    assert(detailPath !== null && detailPath.startsWith("/series/"), `${viewport.label} stable detail URL missing`);
    await assertLayout(`${viewport.label} actual catalog`);
    await page.screenshot({ path: `${outputDirectory}/${viewport.label}-catalog.png`, fullPage: true });

    await goto(detailPath, 200);
    assert(await page.getByText("비교 대상의 정확한 조건").isVisible(), `${viewport.label} identity panel missing`);
    assert(await page.getByText("출처와 자료 상태").isVisible(), `${viewport.label} provenance missing`);
    assert(await page.getByText("source가 비교 기준일을 별도로 제공하지 않음").first().isVisible(), `${viewport.label} unavailable reference-date copy missing`);
    await assertLayout(`${viewport.label} actual detail`);
    await page.screenshot({ path: `${outputDirectory}/${viewport.label}-detail.png`, fullPage: true });

    for (const state of catalogStates) {
      await goto(state.path, state.status);
      assert(await page.getByText(state.text, { exact: false }).first().isVisible(), `${viewport.label} catalog ${state.name} copy missing`);
      await assertLayout(`${viewport.label} catalog ${state.name}`);
    }
    for (const state of detailStates) {
      await goto(state.path, state.status);
      assert(await page.getByText(state.text, { exact: false }).first().isVisible(), `${viewport.label} detail ${state.name} copy missing`);
      await assertLayout(`${viewport.label} detail ${state.name}`);
    }

    await goto("/__qa__/detail/stale/", 200);
    assert(await page.getByRole("heading", { level: 1, name: "아주긴한국어공식품목명이작은화면에서도잘려서는안되는품목" }).isVisible(), `${viewport.label} long Korean item missing`);
    assert(await page.getByText("아주긴원문판매단위표시 포기 × 100").first().isVisible(), `${viewport.label} long unit missing`);
    assert(await page.getByText("낮음", { exact: false }).first().isVisible(), `${viewport.label} text direction missing`);
    await assertLayout(`${viewport.label} long stale detail`);
    await page.screenshot({ path: `${outputDirectory}/${viewport.label}-long-stale-detail.png`, fullPage: true });

    const representative = representativeState[viewport.label];
    await goto(representative.path, representative.status);
    await page.screenshot({ path: `${outputDirectory}/${viewport.label}-${representative.name}.png`, fullPage: true });
    await assertKeyboardFocus(viewport.label);
    if (viewport.width <= 390) await assertMobileCorrection(viewport.label, firstItemName);

    evidence.push({
      viewport: viewport.label,
      actualCatalog: "passed",
      actualDetail: "passed",
      stateMatrix: "passed",
      longKorean: "passed",
      horizontalOverflow: "none",
      touchTargets: "at-least-44px",
      keyboardFocus: "passed",
      mobileCorrection: viewport.width <= 390 ? "passed" : "not-applicable",
    });
  }

  assert(consoleErrors.length === 0, `browser console errors: ${JSON.stringify(consoleErrors)}`);
  return evidence;
}
