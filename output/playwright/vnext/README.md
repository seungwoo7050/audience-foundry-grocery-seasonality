# vNext local browser evidence

생성 시각은 `2026-08-31T06:25:05Z`이며, 검증 대상 exact candidate는
`cb0d4264ceee434fd66ff230cac0c29fe28308a2`다. 이 자료는 production evidence가 아니라
loopback PostgreSQL의 disposable synthetic fixture로 만든 local implementation evidence다.
KAMIS source API, production database, credential, domain·DNS와 외부 asset에는 접근하지 않았다.

fixture는 5개 recent series, 36개월 monthly fact, 2개 region과 31개 market을 정상
review·seal·activation service로 생성했다. 실제 KAMIS coverage, production mapping 승인이나 첫
production publication을 증명하지 않는다.

## 도구와 실행 범위

- Playwright CLI `0.1.5`, Playwright core `1.60.0-alpha-1775237291000`
- Google Chrome `151.0.7922.174`, Node.js `23.11.0`
- local axe-core `4.13.0`; 파일 SHA-256
  `c24f097bd2f451d4f933e8bc7d8d539f8672a2ebcb5cc9f9f3eec8ca9470a0c1`
- ready flow 전체: `390×844`, `1440×900`
- 동일 6개 surface의 horizontal overflow: `360×800`, `768×1024`
- surface: catalog → detail → history의 region GET 선택 → regions → markets → detail →
  query-string selection의 두 번째 품목 GET 추가
- axe `390×844`: 위 6개 ready surface와 catalog validation 400, 총 7회

[browser receipt](browser-results.json)은 8개 PNG, 외부 요청 0, 모든 문서의 client script 0,
inline event handler 0, 한 개의 `main`·`h1`, 자연스러운 keyboard 순서, visible skip-link focus,
44px target, no horizontal overflow와 recent/historical fact-set header 분리를 확인했다. 응답은
no-store, no-referrer, `script-src 'none'`, nosniff, frame deny이며 cookie를 만들지 않았다.

[axe receipt](axe-results.json)은 WCAG 2/2.1/2.2 A·AA violation 0과 unexpected incomplete 0이다.
자동 판정이 불가능한 incomplete 중 `aria-hidden` 장식 기호와 SVG
`.history-chart__label`만 수동검토 대상으로 분리했다. 전자는 인접한 정확한 텍스트가 상태를
제공하고, 후자의 `color-muted`/`color-surface` 조합을 포함한 실제 palette는
`test_accessibility_contrast.py`의 4.5:1 gate를 통과한다.

[60초 smoke receipt](load-smoke-results.json)은 recent catalog/list/search 420건과 active detail
180건, 합계 600/600 성공, error·5xx 0, p95 74.8ms, 9.999rps, 단일 revision을 확인했다. 이름 그대로
`SMOKE_NON_ACCEPTANCE`이며 Phase 0의 고정 900초 profile이나 historical route capacity를
재검증한 결과가 아니다.

## 제품·카피·브랜드·UI·구현 리뷰

- Product: 부류 탐색과 목록을 우선하고 품목명 검색을 no-JS disclosure로 낮췄다. `390×844`
  ready 화면의 첫 record는 `y=657.25`, `height=177.36`으로 844px 안에 완전히 보인다.
- Copy: KAMIS 제공 사실, 조사 조건, 날짜와 개별 판매처 금액이 아니라는 caveat를 우선하며
  source row·series·응답 같은 내부 표현을 공개 화면에 두지 않았다.
- Brand: 따뜻한 종이, 작은 장부선, 단색 장부·새싹 mark와 절제된 green/data blue를 사용한다.
  사진·gradient·glass·반복 feature card·대시보드 grid는 없다.
- UI: desktop ledger 열과 mobile stacked record가 같은 정보 순서를 유지한다. 긴 history와 market
  목록도 pagination·native control·semantic list를 사용하며 가로 overflow가 없다.
- Engineering: Django SSR, no-JS, canonical GET state, local font/static, active-publication-only read,
  fail-closed fact validation과 44px/focus/forced-colors/reduced-motion 경계를 유지한다.

첫 실제 render에서는 390px catalog record가 `y=830.95`에서 시작했고, detail은 `392/390`,
360px regions는 `364/360` overflow였다. 검색 disclosure·compact publication metadata와 mobile
comparison/region stack으로 수정했다. axe가 지적한 generic `div`의 불필요한
`aria-labelledby`도 제거한 뒤 최종 matrix를 새 SHA에서 다시 생성했다.

## 파일 무결성

| 파일 | SHA-256 |
|---|---|
| `390x844-catalog.png` | `f6c435f084e16345ff892c27a02204185df3eb12ba4b330acb60a40024e80453` |
| `390x844-detail.png` | `0efbef272d521fd9850b3245bb091513ccf47760dbb3571d322311fe0760b59c` |
| `390x844-history.png` | `f6c251467221abe1398ad3214483b6dc7f08704c65a37a2651c279212b87bcb8` |
| `390x844-regions.png` | `f5fc45e5cb297eddf79fba4de17f3542f69c2f32f66583d843d4a26db2e048a3` |
| `390x844-markets.png` | `e46386413845b0c30f384f49e457a9419b498579d71a3e6eac49cd30822e7f78` |
| `390x844-selection.png` | `317d7894457653365370c00452f51fd79ced04699319501f52b96284b5edd109` |
| `1440x900-catalog.png` | `de823877f4f1f839b5cbbc8330faa8dfa827e239d3bf262d6e9e327bfe90d29d` |
| `1440x900-selection.png` | `c775ab780ca17c1986e92b08edf26f2487d832743beda30363831dc5c0ae6d0c` |
| `browser-results.json` | `573ff9f0b59737ca2bb000b963301ee262999379025455caeab10b72c6ec0db7` |
| `axe-results.json` | `7f449f4cbc642e02d5789c9d86c98a939d334f52d5032d5be207f8f17a45ebed` |
| `load-smoke-results.json` | `436d6972e20fcb7e7d4094dbb04a5f177af036d876ddc02ccb16316d32ac620a` |

이 matrix는 vNext의 stale/unavailable/503 전체 상태 화면, live source data, historical
authoritative inspection·health·backup restore·rollback compatibility 또는 900초 historical
성능을 검증하지 않는다. 기존 [Phase 0 evidence](../phase0/README.md)는 대체하거나 재작성하지
않았다.
