# Frontend redesign v2 local browser evidence

생성 시각은 `2026-08-31T08:12:03Z`이며 검증 대상 frontend candidate는
`6a0118272675a92ef28db7a76f870646f0fbbb56`다. 기존 Phase 0와 vNext evidence는 수정하지
않았다.

이 matrix의 ready 화면은 synthetic fixture가 아니다. 승인된 로컬 실행에서 KAMIS 공공 API의
최근·월별·지역별·시장별 자료를 가져와 기존 source adapter, typed persistence, 검토·seal·activation
service를 통과시킨 뒤 같은 active publication을 Django SSR로 조회했다. raw response는 hash-only
정책에 따라 보존하지 않았고 source credential, request query와 원응답은 evidence에 기록하지 않았다.

fixture의 mapping과 approval은 browser acceptance용으로 자동 생성한 test publication이다. 사람이
검토한 production publication이나 production activation 증거가 아니며, historical coverage는 catalog
첫 품목과 한 지역의 대표 소비자 흐름으로 제한된다. 최근 catalog의 10개 항목은 모두 실제 API에서
정규화한 공개 행이다.

## 실행 결과

- [live source receipt](live-source-results.json): recent 10행, monthly 36행, regional 1행,
  market 9행을 정규화·test-publish하고 5개 SSR route에서 source 재호출 0을 확인했다.
- [browser receipt](browser-results.json): catalog → detail → history → regions → markets → detail →
  두 품목 selection의 no-JS 흐름을 `390×844`와 `1440×900`에서 완료했다.
- `390×844` catalog의 첫 record는 `y=635.80`, `height=156.53`으로 scroll 전 viewport 안에
  완전히 표시됐다.
- 같은 6개 surface를 `360×800`, `768×1024`에서 확인했고 horizontal overflow는 0이었다.
- 모든 문서는 한 개의 `main`·`h1`, 44px 이상 target, 자연스러운 keyboard order, visible
  skip-link와 item-link focus, client script 0, inline handler 0, 외부 asset/request 0을 유지했다.
- no-store, no-referrer, `script-src 'none'`, nosniff, frame deny, cookie 없음과 recent/historical
  fact-set header 분리를 확인했다.
- [axe receipt](axe-results.json): local axe-core 4.13.0으로 ready 6면, validation 400, catalog
  server-error 503, generic 404를 검사해 WCAG 2/2.1/2.2 A·AA violation 0과 unexpected
  incomplete 0을 확인했다. 자동 판정이 불가능한 decorative symbol과 SVG label contrast는
  별도 palette gate의 4.5:1 검사를 통과했다.

## 도구

- Playwright CLI `0.1.18`, Playwright core `1.63.0-alpha-2026-08-05`
- Google Chrome `151.0.7922.174`, Node.js `23.11.0`
- local axe-core `4.13.0`, SHA-256
  `c24f097bd2f451d4f933e8bc7d8d539f8672a2ebcb5cc9f9f3eec8ca9470a0c1`
- browser acceptance script SHA-256
  `84680477b653b2c504b3f3dc8459bd201dd405f5d9e7ee27cf13b52de6f9ec12`
- axe acceptance script SHA-256
  `721fa8dc645c5b58ff1b791a166cad0e9b595feb1988b836a1990e928669cb14`

## 통합 디자인 리뷰

- Product: category 탐색과 실제 조사값 장부가 보조 검색보다 앞서며 mobile 첫 결과가 fold 안에
  들어온다. 선택 화면은 결과를 편집 control보다 먼저 보여준다.
- Copy: KAMIS 제공 사실, 조사 조건, 날짜, 개별 판매처 금액이 아니라는 caveat를 유지하고 내부
  source·series·응답 용어를 노출하지 않는다.
- Brand/UI: warm paper, forest/harvest/data-blue와 굵은 장부선을 사용하되 photo, gradient,
  glass, dashboard card grid를 사용하지 않는다. 기능 제목은 system sans, 브랜드와 큰 editorial
  제목은 Hahmlet Bold, `초록장부` wordmark는 나눔손글씨 야채장수 백금례 outline이다.
- Engineering: Django SSR, no-JS, active-publication-only read, bounded query, template 산술 금지,
  fail-closed validation과 보안 header를 유지한다.

첫 실제 render 리뷰에서 dark masthead focus/hover 대비, historical 503의 빈 제목과 잘못된 복구
label, detail의 market 탐색 단서, desktop catalog 열 배치, SVG 끝 월 label clipping을 수정했다.
axe가 찾은 generic `div`의 unsupported `aria-label`도 제거한 뒤 전체 matrix를 다시 생성했다.
후속 typography review에서는 둥근 Gowun Batang을 Hahmlet Bold로 교체하고, 네이버 공식
나눔손글씨 야채장수 백금례의 `초록장부` outline만 wordmark SVG로 사용했다. font·license
provenance와 11KB local SVG를 고정한 뒤 같은 browser·axe matrix를 다시 통과했다.

## 파일 무결성

| 파일 | SHA-256 |
|---|---|
| `390x844-catalog.png` | `778a9820da4eefaba3d792d61271fd624278b053ce9252b6cf938683a0f07c3e` |
| `390x844-detail.png` | `65e2dd8ce241468f001ac59626d3a2acc5843048d808dce7f7732b039813e584` |
| `390x844-history.png` | `18d698d4ed1b92359d435f4e95ef5fc544a10924ee59b342e409cd6e10f5516b` |
| `390x844-selection.png` | `c1841dc220adeb20d9cc6072b0315a12159a32f020633045f12c170e158325b2` |
| `1440x900-catalog.png` | `328d70224d5dcb153f18237760c05569adc8943a60ff8701afc14804fd39a5bb` |
| `1440x900-detail.png` | `876a9a4932db55277ee0fdf30d8988dcda98529311304fed7c2042245b27d702` |
| `1440x900-history.png` | `8a54376df6f53658d47ecfdc231ce82d7a0610d2cd72a66475ae796ad8df4b98` |
| `1440x900-selection.png` | `4bb04b3c028b6a6baffaf6e6f592196339ca502060f2026c0bf9c7afd59c2855` |
| `browser-results.json` | `e9de4eba8ef4690731536ce284adabc392598c9533ac5ebb51d5402ef878c0f1` |
| `axe-results.json` | `c8be5cc052b0a3c6ce544c279bf2ca9882118df1b6aab604396e31c77763a7e7` |
| `live-source-results.json` | `8ec68cea621e6a77cf6dd588093f67320f0bc604b4d3b7029e2140831ff4aa21` |

이 evidence는 production platform, production database, human publication approval, full-catalog
historical coverage, deployment·traffic switch, domain·DNS, trademark clearance를 증명하지 않는다.
