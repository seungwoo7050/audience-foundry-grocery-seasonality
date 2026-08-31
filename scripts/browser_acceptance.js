async (page) => {
  const baseUrl = "http://127.0.0.1:8000";
  const outputDirectory = "output/playwright/redesign-v1";
  const viewports = [
    { width: 360, height: 800, label: "360x800" },
    { width: 390, height: 844, label: "390x844" },
    { width: 768, height: 1024, label: "768x1024" },
    { width: 1440, height: 900, label: "1440x900" },
  ];
  const catalogStates = [
    {
      name: "loading",
      path: "/__qa__/catalog/loading/",
      status: 200,
      text: "조사 자료를 불러오고 있습니다",
    },
    {
      name: "empty",
      path: "/__qa__/catalog/empty/",
      status: 200,
      text: "검색 결과가 없습니다",
    },
    {
      name: "unavailable",
      path: "/__qa__/catalog/unavailable/",
      status: 200,
      text: "아직 공개된 조사 자료가 없습니다",
    },
    {
      name: "stale",
      path: "/__qa__/catalog/stale/",
      status: 200,
      text: "마지막 공개 자료를 표시합니다",
    },
    {
      name: "server-error",
      path: "/__qa__/catalog/server_error/",
      status: 503,
      text: "조사 자료를 불러오지 못했습니다",
    },
  ];
  const detailStates = [
    {
      name: "loading",
      path: "/__qa__/detail/loading/",
      status: 200,
      text: "조사 자료를 불러오고 있습니다",
    },
    {
      name: "unavailable",
      path: "/__qa__/detail/unavailable/",
      status: 200,
      text: "아직 공개된 조사 자료가 없습니다",
    },
    {
      name: "stale",
      path: "/__qa__/detail/stale/",
      status: 200,
      text: "마지막 공개 자료를 표시합니다",
    },
    {
      name: "server-error",
      path: "/__qa__/detail/server_error/",
      status: 503,
      text: "조사 자료를 불러오지 못했습니다",
    },
  ];
  const errorPages = [
    {
      name: "400",
      path: "/__qa__/catalog/error_400/",
      status: 400,
      heading: "요청 내용을 확인하세요",
    },
    {
      name: "403",
      path: "/__qa__/catalog/error_403/",
      status: 403,
      heading: "이 페이지를 볼 수 없습니다",
    },
    {
      name: "404",
      path: "/__qa__/catalog/error_404/",
      status: 404,
      heading: "페이지를 찾을 수 없습니다",
    },
    {
      name: "500",
      path: "/__qa__/catalog/error_500/",
      status: 500,
      heading: "페이지를 표시하지 못했습니다",
    },
  ];
  const consoleErrors = [];
  const externalRequests = [];
  const failedRequests = [];
  const failedSubresources = [];

  page.on("console", (message) => {
    const messageText = message.text();
    const expectedErrorResponse = [400, 403, 404, 500, 503].some((status) =>
      messageText.includes(`status of ${status}`),
    );
    if (message.type() === "error" && !expectedErrorResponse) consoleErrors.push(messageText);
  });
  page.on("request", (request) => {
    const requestUrl = request.url();
    if (!requestUrl.startsWith("http://") && !requestUrl.startsWith("https://")) return;
    const parsed = new URL(requestUrl);
    if (parsed.origin !== baseUrl) externalRequests.push(parsed.origin);
  });
  page.on("requestfailed", (request) => {
    failedRequests.push({
      path: new URL(request.url()).pathname,
      resourceType: request.resourceType(),
    });
  });
  page.on("response", (response) => {
    const request = response.request();
    if (response.status() < 400 || request.resourceType() === "document") return;
    failedSubresources.push({
      path: new URL(response.url()).pathname,
      resourceType: request.resourceType(),
      status: response.status(),
    });
  });

  function assert(condition, message) {
    if (!condition) throw new Error(message);
  }

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

  async function assertDocumentContract(label) {
    const result = await page.evaluate(() => {
      const root = document.documentElement;
      const visibleTargets = [...document.querySelectorAll("a, button, input")]
        .map((element) => ({ element, rectangle: element.getBoundingClientRect() }))
        .filter(({ rectangle }) => rectangle.width > 0 && rectangle.height > 0);
      const undersized = visibleTargets
        .filter(({ rectangle }) => rectangle.height < 44 || rectangle.width < 44)
        .map(({ element, rectangle }) => ({
          tag: element.tagName,
          text: (element.textContent || element.getAttribute("aria-label") || "")
            .trim()
            .slice(0, 48),
          width: Math.round(rectangle.width),
          height: Math.round(rectangle.height),
        }));
      const eventHandlers = [...document.querySelectorAll("*")]
        .flatMap((element) =>
          [...element.attributes]
            .filter((attribute) => attribute.name.toLowerCase().startsWith("on"))
            .map((attribute) => `${element.tagName.toLowerCase()}[${attribute.name}]`),
        )
        .slice(0, 20);
      const externalResources = [
        ...document.querySelectorAll("img[src], link[href], source[src], source[srcset]"),
      ]
        .map((element) => element.getAttribute("src") || element.getAttribute("srcset") || element.getAttribute("href"))
        .filter(Boolean)
        .filter((value) => {
          const resolved = new URL(value, document.baseURI);
          return (resolved.protocol === "http:" || resolved.protocol === "https:") && resolved.origin !== location.origin;
        });
      const rasterImages = [...document.querySelectorAll("img[src], source[src], source[srcset]")]
        .map((element) => element.getAttribute("src") || element.getAttribute("srcset") || "")
        .filter((value) => /\.(?:avif|gif|jpe?g|png|webp)(?:\?|$)/i.test(value));

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
        scripts: document.querySelectorAll("script").length,
        eventHandlers,
        externalResources,
        rasterImages,
        pictures: document.querySelectorAll("picture").length,
      };
    });

    assert(
      !result.horizontalOverflow,
      `${label} horizontal overflow ${result.scrollWidth}/${result.clientWidth}`,
    );
    assert(
      result.undersized.length === 0,
      `${label} undersized targets ${JSON.stringify(result.undersized)}`,
    );
    assert(result.mainCount === 1, `${label} must have one main landmark`);
    assert(result.h1Count === 1, `${label} must have one h1`);
    assert(result.lang === "ko", `${label} must declare Korean language`);
    assert(result.positiveTabIndexes.length === 0, `${label} must not reorder keyboard focus`);
    assert(result.scripts === 0, `${label} must remain server-rendered without scripts`);
    assert(
      result.eventHandlers.length === 0,
      `${label} has inline event handlers ${JSON.stringify(result.eventHandlers)}`,
    );
    assert(
      result.externalResources.length === 0,
      `${label} has external resources ${JSON.stringify(result.externalResources)}`,
    );
    assert(
      result.rasterImages.length === 0 && result.pictures === 0,
      `${label} must not use catalog photos or raster imagery`,
    );
  }

  async function assertBrand(label) {
    assert(
      (await page.locator(".brand__name").innerText()).trim() === "초록장부",
      `${label} brand name missing`,
    );
    assert(
      (await page.locator(".brand__description").innerText()).trim() ===
        "채소·과일 소매 조사값",
      `${label} brand descriptor missing`,
    );
    const mark = await page.locator(".brand-mark").evaluate((element) => ({
      source: element.getAttribute("src"),
      width: element instanceof HTMLImageElement ? element.naturalWidth : 0,
    }));
    assert(mark.source !== null && mark.source.endsWith("/grocery/brand-mark.svg"), `${label} local brand mark missing`);
    assert(mark.width > 0, `${label} brand mark did not load`);
  }

  async function assertAtomicValueTokens(label) {
    const result = await page.evaluate(() => {
      const selectors = [
        "data",
        "time",
        ".ledger-fact--price dd",
        ".comparison-field--reference strong",
        ".ledger-entry__identity span:last-child",
        ".detail-signature div:nth-child(3) dd",
      ];
      const values = [...document.querySelectorAll(selectors.join(","))]
        .filter((element) => element.getClientRects().length > 0)
        .map((element) => {
          const range = document.createRange();
          range.selectNodeContents(element);
          const style = getComputedStyle(element);
          return {
            text: (element.textContent || "").trim(),
            lineFragments: range.getClientRects().length,
            overflow: style.overflow,
            textOverflow: style.textOverflow,
          };
        });

      const percentages = [];
      for (const element of document.querySelectorAll(".direction")) {
        const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
        for (let node = walker.nextNode(); node; node = walker.nextNode()) {
          const matches = [...(node.textContent || "").matchAll(/\([+-]?\d+(?:\.\d+)?%\)/g)];
          for (const match of matches) {
            const range = document.createRange();
            range.setStart(node, match.index);
            range.setEnd(node, match.index + match[0].length);
            percentages.push({ text: match[0], lineFragments: range.getClientRects().length });
          }
        }
      }
      return { percentages, values };
    });

    const splitValues = result.values.filter((value) => value.text && value.lineFragments > 1);
    const splitPercentages = result.percentages.filter((value) => value.lineFragments > 1);
    const truncatedValues = result.values.filter(
      (value) => value.overflow === "hidden" || value.textOverflow === "ellipsis",
    );
    assert(splitValues.length === 0, `${label} split atomic values ${JSON.stringify(splitValues)}`);
    assert(
      splitPercentages.length === 0,
      `${label} split percentage tokens ${JSON.stringify(splitPercentages)}`,
    );
    assert(
      truncatedValues.length === 0,
      `${label} truncated value tokens ${JSON.stringify(truncatedValues)}`,
    );
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
        width: rectangle.width,
        height: rectangle.height,
      };
    });
    assert(focus !== null && focus.className.includes("skip-link"), `${label} first focus must be skip link`);
    assert(
      focus.outlineStyle !== "none" && focus.outlineWidth !== "0px",
      `${label} focus must be visible`,
    );
    assert(
      focus.top >= 0 && focus.width >= 44 && focus.height >= 44,
      `${label} focused skip link must be visible and touch sized`,
    );
    await page.keyboard.press("Enter");
    assert((await page.locator("#main-content:focus").count()) === 1, `${label} skip link target missing`);
  }

  async function tabUntil(selector, label, maximumTabs = 24) {
    await page.evaluate(() => {
      if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
    });
    for (let index = 0; index < maximumTabs; index += 1) {
      await page.keyboard.press("Tab");
      const matched = await page.evaluate(
        (target) => document.activeElement instanceof Element && document.activeElement.matches(target),
        selector,
      );
      if (matched) return;
    }
    throw new Error(`${label} could not reach ${selector} by Tab`);
  }

  async function assertRepresentativeKeyboardFlow(label, firstItemName) {
    await goto("/", 200);
    await tabUntil(".segment", `${label} category`);
    const [categoryResponse] = await Promise.all([
      page.waitForNavigation({ waitUntil: "networkidle" }),
      page.keyboard.press("Enter"),
    ]);
    assert(categoryResponse !== null && categoryResponse.status() === 200, `${label} category Enter failed`);

    await goto("/", 200);
    await tabUntil("#catalog-query", `${label} search`);
    await page.keyboard.type(firstItemName);
    const [searchResponse] = await Promise.all([
      page.waitForNavigation({ waitUntil: "networkidle" }),
      page.keyboard.press("Enter"),
    ]);
    assert(searchResponse !== null && searchResponse.status() === 200, `${label} search Enter failed`);
    assert((await page.locator(".ledger-entry").count()) >= 1, `${label} keyboard search has no result`);

    await goto("/", 200);
    await tabUntil(".ledger-entry__link", `${label} item link`);
    const expectedDetailPath = await page.locator(":focus").getAttribute("href");
    const [detailResponse] = await Promise.all([
      page.waitForNavigation({ waitUntil: "networkidle" }),
      page.keyboard.press("Enter"),
    ]);
    assert(
      detailResponse !== null &&
        detailResponse.status() === 200 &&
        new URL(detailResponse.url()).pathname === expectedDetailPath,
      `${label} item-link Enter failed`,
    );

    await goto("/__qa__/catalog/error_500/", 500);
    await tabUntil(".error-page .button", `${label} error recovery`);
    const [recoveryResponse] = await Promise.all([
      page.waitForNavigation({ waitUntil: "networkidle" }),
      page.keyboard.press("Enter"),
    ]);
    assert(
      recoveryResponse !== null && recoveryResponse.status() === 200,
      `${label} error recovery Enter failed`,
    );
  }

  async function assertCatalogLedger(viewport) {
    const rows = page.locator(".ledger-entry");
    assert((await rows.count()) >= 1, `${viewport.label} actual publication has no ledger entry`);
    const firstRow = rows.first();
    assert((await firstRow.locator("h4").count()) === 1, `${viewport.label} ledger item heading missing`);
    assert(
      (await firstRow.locator(".ledger-fact--price").count()) === 1 &&
        (await firstRow.locator(".ledger-fact--comparison").count()) === 1 &&
        (await firstRow.locator(".ledger-fact--date").count()) === 1,
      `${viewport.label} ledger row must show price, one-week comparison, and date`,
    );
    const bodyText = await firstRow.innerText();
    assert(bodyText.includes("1주 전 비교"), `${viewport.label} one-week comparison preview missing`);
    assert(
      bodyText.includes("1주 전 제공값") || bodyText.includes("1주 전 비교값 없음"),
      `${viewport.label} one-week comparison text missing`,
    );
    const catalogText = await page.locator(".catalog-ledger").innerText();
    assert(!catalogText.includes("1개월 전") && !catalogText.includes("1년 전"), `${viewport.label} catalog must preview one week only`);
    for (const selector of [
      ".ledger-fact--comparison .direction--lower",
      ".ledger-fact--comparison .direction--higher",
      ".ledger-fact--comparison .direction--equal",
      ".ledger-fact--comparison .status-text--unavailable",
    ]) {
      const matches = page.locator(selector);
      assert((await matches.count()) >= 1, `${viewport.label} missing ${selector}`);
      assert(await matches.first().isVisible(), `${viewport.label} hidden ${selector}`);
    }

    if (viewport.width === 390) {
      const rowGeometry = await firstRow.locator("xpath=..").evaluate((element) => {
        const rectangle = element.getBoundingClientRect();
        const itemName = element.querySelector("h4")?.textContent?.trim() || "";
        const identity = element.querySelector(".ledger-entry__identity")?.textContent?.trim() || "";
        return {
          top: rectangle.top,
          bottom: rectangle.bottom,
          height: rectangle.height,
          itemNameLength: itemName.length,
          identityLength: identity.length,
        };
      });
      assert(rowGeometry.top >= 0, `${viewport.label} first ledger record begins above the viewport`);
      if (rowGeometry.itemNameLength <= 12 && rowGeometry.identityLength <= 45) {
        assert(
          rowGeometry.height <= 168,
          `${viewport.label} normal ledger record is ${Math.round(rowGeometry.height)}px tall, expected at most 168px`,
        );
        assert(
          rowGeometry.bottom <= viewport.height,
          `${viewport.label} first normal ledger record must be fully visible before scrolling`,
        );
      }
    }

    if (viewport.width === 768) {
      const tabletLayout = await firstRow.evaluate((element) => ({
        columns: getComputedStyle(element).gridTemplateColumns.split(" ").length,
        priceTop: element.querySelector(".ledger-fact--price")?.getBoundingClientRect().top,
        comparisonTop: element
          .querySelector(".ledger-fact--comparison")
          ?.getBoundingClientRect().top,
        dateTop: element.querySelector(".ledger-fact--date")?.getBoundingClientRect().top,
      }));
      assert(tabletLayout.columns === 2, `${viewport.label} catalog ledger must use two columns`);
      assert(
        tabletLayout.priceTop === tabletLayout.dateTop &&
          tabletLayout.comparisonTop > tabletLayout.priceTop,
        `${viewport.label} comparison must span the row below price and date`,
      );
    }

    if (viewport.width >= 1024) {
      const columnText = await page.locator(".ledger-column-head").innerText();
      for (const label of ["품목·조건", "소매 조사 평균", "1주 비교", "조사일", "이동"]) {
        assert(columnText.includes(label), `${viewport.label} desktop ledger column ${label} missing`);
      }
    }
  }

  async function assertDetailLedger(viewport) {
    assert((await page.locator(".comparison-ledger").count()) === 1, `${viewport.label} comparison ledger missing`);
    const rows = page.locator(".comparison-row");
    assert((await rows.count()) === 3, `${viewport.label} detail must show three comparison periods`);
    assert((await page.locator(".comparison-card").count()) === 0, `${viewport.label} comparison must not use cards`);
    for (let index = 0; index < (await rows.count()); index += 1) {
      const row = rows.nth(index);
      for (const className of ["period", "reference", "difference", "date"]) {
        assert(
          (await row.locator(`.comparison-field--${className}`).count()) === 1,
          `${viewport.label} comparison row ${index + 1} missing ${className} field`,
        );
      }
    }
    if (viewport.width >= 1024) {
      const columnText = await page.locator(".comparison-column-head").innerText();
      for (const label of ["기간", "KAMIS 제공값", "조사일 평균과 차이", "비교 기준일"]) {
        assert(columnText.includes(label), `${viewport.label} comparison column ${label} missing`);
      }
      const desktopOrder = await page.evaluate(() => {
        const rectangle = (selector) => {
          const value = document.querySelector(selector)?.getBoundingClientRect();
          if (!value) return null;
          return {
            bottom: value.bottom,
            left: value.left,
            top: value.top,
            width: value.width,
          };
        };
        const layout = rectangle(".detail-layout");
        const comparison = rectangle(".comparison-ledger");
        const aside = rectangle(".detail-aside");
        const identity = rectangle(".detail-aside .identity-panel");
        const provenance = rectangle(".detail-aside .provenance");
        const current = rectangle(".current-price");
        return { aside, comparison, current, identity, layout, provenance };
      });
      assert(
        desktopOrder.comparison.width >= desktopOrder.layout.width - 1,
        `${viewport.label} comparison ledger must use the full detail width`,
      );
      assert(
        desktopOrder.current.bottom < desktopOrder.comparison.top &&
          desktopOrder.comparison.bottom < desktopOrder.aside.top,
        `${viewport.label} detail order must be current, comparison, then metadata`,
      );
      assert(
        Math.abs(desktopOrder.identity.top - desktopOrder.provenance.top) < 1 &&
          desktopOrder.identity.left < desktopOrder.provenance.left,
        `${viewport.label} conditions and provenance must form the lower two-column ledger`,
      );
    }
    const text = await page.locator("main").innerText();
    assert(text.includes("조사 조건"), `${viewport.label} identity panel missing`);
    assert(text.includes("출처와 자료 정보"), `${viewport.label} provenance missing`);
    assert(!/\bsource\b/i.test(text), `${viewport.label} leaked source-facing jargon`);
  }

  async function assertDesktopInteractions(label) {
    const itemLink = page.locator(".ledger-entry__link").first();
    const row = page.locator(".ledger-row").first();
    const beforeHover = await Promise.all([
      row.evaluate((element) => getComputedStyle(element).backgroundColor),
      itemLink.evaluate((element) => getComputedStyle(element).backgroundColor),
      itemLink.evaluate((element) => getComputedStyle(element).textDecorationThickness),
    ]);
    await itemLink.hover();
    const afterHover = await Promise.all([
      row.evaluate((element) => getComputedStyle(element).backgroundColor),
      itemLink.evaluate((element) => getComputedStyle(element).backgroundColor),
      itemLink.evaluate((element) => getComputedStyle(element).textDecorationThickness),
    ]);
    assert(beforeHover[0] === afterHover[0], `${label} ledger row must not imply a full-row hit area`);
    assert(beforeHover[1] !== afterHover[1], `${label} item-link hover background is not distinct`);
    assert(beforeHover[2] !== afterHover[2], `${label} link hover underline is not distinct`);
    await page.screenshot({ path: `${outputDirectory}/${label}-hover.png`, fullPage: true });

    const searchInput = page.getByLabel("품목명");
    await searchInput.focus();
    const focusStyle = await searchInput.evaluate((element) => {
      const style = getComputedStyle(element);
      return {
        color: style.outlineColor,
        style: style.outlineStyle,
        width: style.outlineWidth,
      };
    });
    assert(focusStyle.style === "solid", `${label} search focus outline must be solid`);
    assert(focusStyle.width === "3px", `${label} search focus outline must be 3px`);
    assert(
      focusStyle.color === "rgb(0, 95, 204)",
      `${label} search focus outline must use the focus-blue token`,
    );
    await page.screenshot({ path: `${outputDirectory}/${label}-focus.png`, fullPage: true });

    const selected = page.locator(".segment--selected").first();
    const unselected = page.locator(".segment:not(.segment--selected)").first();
    const segmentStyles = await Promise.all(
      [selected, unselected].map((locator) =>
        locator.evaluate((element) => {
          const style = getComputedStyle(element);
          return {
            background: style.backgroundColor,
            border: style.borderTopColor,
            color: style.color,
          };
        }),
      ),
    );
    assert(
      segmentStyles[0].background !== segmentStyles[1].background &&
        segmentStyles[0].border !== segmentStyles[1].border &&
        segmentStyles[0].color !== segmentStyles[1].color,
      `${label} selected segment must differ from unselected in more than one cue`,
    );

    await unselected.hover();
    await page.mouse.down();
    const activeStyle = await unselected.evaluate((element) => {
      const style = getComputedStyle(element);
      return { border: style.borderTopColor, transform: style.transform };
    });
    await page.mouse.up();
    assert(activeStyle.transform !== "none", `${label} pressed segment state is not distinct`);
    assert(
      activeStyle.border === "rgb(29, 40, 32)",
      `${label} pressed segment must use the text-color boundary`,
    );
  }

  async function assertComparisonRowsAreNonInteractive(label) {
    const row = page.locator(".comparison-row").first();
    const before = await row.evaluate((element) => getComputedStyle(element).backgroundColor);
    await row.hover();
    const after = await row.evaluate((element) => getComputedStyle(element).backgroundColor);
    assert(before === after, `${label} comparison row hover must not imply interaction`);
  }

  async function assertErrorPage(viewport, errorPage) {
    await goto(errorPage.path, errorPage.status);
    assert(
      await page.getByRole("heading", { level: 1, name: errorPage.heading }).isVisible(),
      `${viewport.label} ${errorPage.name} heading missing`,
    );
    const technicalLeak = await page.evaluate(() => ({
      codeElements: document.querySelectorAll("code, pre").length,
      technicalText: /traceback|exception|debug\s*=|stack trace/i.test(document.body.innerText),
    }));
    assert(
      technicalLeak.codeElements === 0 && !technicalLeak.technicalText,
      `${viewport.label} ${errorPage.name} exposed technical error details`,
    );
    const recovery = page.locator(".error-page .button");
    assert(
      (await recovery.count()) === 1 && (await recovery.isVisible()),
      `${viewport.label} ${errorPage.name} recovery action missing`,
    );
    assert((await recovery.getAttribute("href")) === "/", `${viewport.label} ${errorPage.name} recovery target invalid`);
    await assertDocumentContract(`${viewport.label} error ${errorPage.name}`);
    await page.screenshot({
      path: `${outputDirectory}/${viewport.label}-error-${errorPage.name}.png`,
      fullPage: true,
    });
  }

  async function assertMobileCorrection(label, firstItemName) {
    await goto("/", 200);
    const invalidQuery = "검증오류\u200b표시";
    await page.getByLabel("품목명").fill(invalidQuery);
    const [invalidResponse] = await Promise.all([
      page.waitForNavigation({ waitUntil: "networkidle" }),
      page.getByRole("button", { name: "검색" }).click(),
    ]);
    assert(invalidResponse !== null && invalidResponse.status() === 400, `${label} invalid search must return 400`);
    await page.evaluate(async () => {
      if (document.fonts) await document.fonts.ready;
    });
    const invalidBody = await page.content();
    assert(!invalidBody.includes(invalidQuery), `${label} invalid query was reflected`);
    assert((await page.getByLabel("품목명").inputValue()) === "", `${label} invalid input must be blank`);
    assert(
      (await page.getByLabel("품목명").getAttribute("aria-invalid")) === "true",
      `${label} invalid input association missing`,
    );
    assert(
      await page.getByText("품목명은 한 줄로 입력하세요.", { exact: true }).isVisible(),
      `${label} validation copy missing`,
    );
    assert(
      await page.getByText("입력 내용을 확인하세요", { exact: true }).isVisible(),
      `${label} validation summary missing`,
    );
    await assertDocumentContract(`${label} validation`);
    await page.screenshot({ path: `${outputDirectory}/${label}-validation.png`, fullPage: true });

    await page.getByLabel("품목명").fill(firstItemName);
    const [correctedResponse] = await Promise.all([
      page.waitForNavigation({ waitUntil: "networkidle" }),
      page.getByRole("button", { name: "검색" }).click(),
    ]);
    assert(correctedResponse !== null && correctedResponse.status() === 200, `${label} corrected search must return 200`);
    assert((await page.locator(".ledger-entry").count()) >= 1, `${label} corrected search has no result`);
    assert((await page.getByLabel("품목명").inputValue()) === "", `${label} valid query must not be echoed`);
  }

  const evidence = [];
  for (const viewport of viewports) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await goto("/", 200);
    await assertBrand(`${viewport.label} actual catalog`);
    assert((await page.getByRole("search").count()) === 1, `${viewport.label} search landmark missing`);
    assert((await page.getByLabel("품목명").count()) === 1, `${viewport.label} search label missing`);
    await assertCatalogLedger(viewport);
    const firstItemName = (await page.locator(".ledger-entry h4").first().innerText()).trim();
    const detailPath = await page.locator(".ledger-entry__link").first().getAttribute("href");
    assert(
      detailPath !== null && detailPath.startsWith("/series/"),
      `${viewport.label} stable detail URL missing`,
    );
    await assertDocumentContract(`${viewport.label} actual catalog`);
    await assertAtomicValueTokens(`${viewport.label} actual catalog`);
    await page.screenshot({ path: `${outputDirectory}/${viewport.label}-catalog.png`, fullPage: true });
    if (viewport.width === 1440) await assertDesktopInteractions(viewport.label);

    await goto(detailPath, 200);
    await assertBrand(`${viewport.label} actual detail`);
    await assertDetailLedger(viewport);
    await assertDocumentContract(`${viewport.label} actual detail`);
    await assertAtomicValueTokens(`${viewport.label} actual detail`);
    if (viewport.width === 1440) {
      await assertComparisonRowsAreNonInteractive(viewport.label);
    }
    await page.screenshot({ path: `${outputDirectory}/${viewport.label}-detail.png`, fullPage: true });

    for (const state of catalogStates) {
      await goto(state.path, state.status);
      assert(
        await page.getByText(state.text, { exact: true }).isVisible(),
        `${viewport.label} catalog ${state.name} copy missing`,
      );
      if (["loading", "unavailable", "server-error"].includes(state.name)) {
        assert(
          (await page.getByRole("search").count()) === 0 &&
            (await page.locator(".catalog-ledger").count()) === 0,
          `${viewport.label} catalog ${state.name} must hide controls and results`,
        );
      }
      if (state.name === "empty") {
        const recovery = page.getByRole("link", { name: "전체 항목 보기" });
        assert(
          (await recovery.count()) === 1 && (await recovery.isVisible()),
          `${viewport.label} catalog empty recovery missing`,
        );
      }
      if (state.name === "server-error") {
        const retry = page.getByRole("link", { name: "다시 불러오기" });
        assert(
          (await retry.count()) === 1 && (await retry.isVisible()),
          `${viewport.label} catalog retry missing`,
        );
      }
      if (state.name === "stale") {
        assert(await page.locator(".catalog-ledger").isVisible(), `${viewport.label} stale ledger missing`);
      }
      await assertDocumentContract(`${viewport.label} catalog ${state.name}`);
      await page.screenshot({
        path: `${outputDirectory}/${viewport.label}-catalog-${state.name}.png`,
        fullPage: true,
      });
    }
    for (const state of detailStates) {
      await goto(state.path, state.status);
      assert(
        await page.getByText(state.text, { exact: true }).isVisible(),
        `${viewport.label} detail ${state.name} copy missing`,
      );
      if (["loading", "unavailable", "server-error"].includes(state.name)) {
        assert(
          (await page.locator(".current-price").count()) === 0 &&
            (await page.locator(".comparison-ledger").count()) === 0,
          `${viewport.label} detail ${state.name} must hide publication values`,
        );
      }
      if (state.name === "server-error") {
        const retry = page.getByRole("link", { name: "다시 불러오기" });
        assert(
          (await retry.count()) === 1 && (await retry.isVisible()),
          `${viewport.label} detail retry missing`,
        );
      }
      await assertDocumentContract(`${viewport.label} detail ${state.name}`);
      await page.screenshot({
        path: `${outputDirectory}/${viewport.label}-detail-${state.name}.png`,
        fullPage: true,
      });
    }
    for (const errorPage of errorPages) {
      await assertErrorPage(viewport, errorPage);
    }

    await goto("/__qa__/detail/stale/", 200);
    assert(
      await page
        .getByRole("heading", {
          level: 1,
          name: "아주긴한국어공식품목명이작은화면에서도잘려서는안되는품목",
        })
        .isVisible(),
      `${viewport.label} long Korean item missing`,
    );
    assert(
      await page.getByText("아주긴원문판매단위표시 포기 × 100", { exact: true }).first().isVisible(),
      `${viewport.label} long unit missing`,
    );
    assert(
      await page.getByText("낮음", { exact: false }).first().isVisible(),
      `${viewport.label} text direction missing`,
    );
    const providedReferenceDate = page.getByText("2026년 8월 22일", { exact: true });
    assert(
      (await providedReferenceDate.count()) === 1 && (await providedReferenceDate.isVisible()),
      `${viewport.label} provided reference date must appear once`,
    );
    const unavailableReferenceDates = page.getByText("KAMIS에서 제공하지 않음", {
      exact: true,
    });
    assert(
      (await unavailableReferenceDates.count()) === 2 &&
        (await unavailableReferenceDates.first().isVisible()) &&
        (await unavailableReferenceDates.nth(1).isVisible()),
      `${viewport.label} unavailable reference date must appear twice`,
    );
    await assertDetailLedger(viewport);
    await assertDocumentContract(`${viewport.label} long stale detail`);
    await page.screenshot({
      path: `${outputDirectory}/${viewport.label}-long-stale-detail.png`,
      fullPage: true,
    });

    await assertKeyboardFocus(viewport.label);
    if (viewport.width <= 390) await assertMobileCorrection(viewport.label, firstItemName);

    evidence.push({
      viewport: viewport.label,
      actualCatalog: "passed",
      actualDetail: "passed",
      catalogStateMatrix: catalogStates.map((state) => state.name),
      detailStateMatrix: detailStates.map((state) => state.name),
      errorPageMatrix: errorPages.map((errorPage) => errorPage.name),
      longKorean: "passed",
      horizontalOverflow: "none",
      touchTargets: "at-least-44px",
      keyboardFocus: "passed",
      noClientScript: "passed",
      noExternalResources: "passed",
      noPhotos: "passed",
      mobileCorrection: viewport.width <= 390 ? "passed" : "not-applicable",
    });
  }

  assert(consoleErrors.length === 0, `browser console errors: ${JSON.stringify(consoleErrors)}`);
  assert(
    failedSubresources.length === 0,
    `subresource error responses: ${JSON.stringify(failedSubresources)}`,
  );
  assert(failedRequests.length === 0, `failed requests: ${JSON.stringify(failedRequests)}`);
  assert(
    externalRequests.length === 0,
    `external requests observed: ${JSON.stringify([...new Set(externalRequests)])}`,
  );
  return { outputDirectory, evidence };
}
