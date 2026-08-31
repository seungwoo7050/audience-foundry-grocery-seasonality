# vNext 제품·source 계약

승인일은 2026-08-31(KST)다. 이 문서는 제품 소유자가 승인한 첫 MVP 이후의
소비자 확장 계약이며, 실제 source gate가 통과하기 전에는 source-dependent schema나
adapter 구현을 허용하지 않는다.

## 제품 목표

초록장부는 비로그인 한국어 사용자가 같은 품목·품종·등급·판매 단위 안에서 다음을
확인하는 소비자용 조사 장부로 확장한다.

- 최근 KAMIS 소매 조사 평균과 1주·1개월·1년 제공값의 차이
- 선택한 지역의 월별 과거 가격 패턴
- 같은 조사일의 지역별 소매 조사 평균·최저·최고
- 선택 지역·조사일의 KAMIS 시장별 조사값
- 계정 없이 최대 5개 품목을 모아 보는 URL 기반 선택 목록

기능 수를 강조하는 dashboard가 아니라 품목을 중심으로 최근값, 월별 기록, 지역별 값과
시장 근거를 순서대로 읽는 것이 핵심 사용자 흐름이다.

## 승인 source와 역할

| dataset | 승인 역할 | 금지된 결합 |
|---|---|---|
| `15156063` 최근일자 도·소매가격정보 | 최근 조사 평균과 1주·1개월·1년 제공값 | 역사·지역·시장 행을 채우는 fallback으로 사용하지 않음 |
| `15156060` 연월별 도·소매가격정보 | provider 월 평균·최저·최고의 유일한 정본 | 일별·시장 행에서 월 값을 재계산하거나 보충하지 않음 |
| `15156062` 지역별 품목별 도·소매가격정보 | provider 지역별 일 평균·최저·최고 | 시장 관측값을 합쳐 지역 평균을 재구성하지 않음 |
| `15156065` 기간별 소매가격정보 | 지역·시장별 조사일 관측값 | 시장명 문자열로 시장유형을 추정하지 않음 |

모든 source는 공공데이터포털과 한국농수산식품유통공사가 제공한 공식 계약만 사용한다.
KAMIS HTML, 검색 cache, 비공식 mirror, 쇼핑몰·마트 crawling과 다른 가격 source는 사용하지
않는다.

## 공개 coverage

다음 조건을 모두 만족하는 모든 exact series를 공개 후보로 삼는다.

1. source가 소매, 채소류 또는 과일류로 식별한다.
2. 네 API의 품목·품종·등급·원문 단위·단위크기가 공식 code와 이름까지 일치한다.
3. 적어도 한 검증 지역에서 완전한 최근 36개월 월별 행을 제공한다.
4. 지역·시장 code와 소속, 조사일 identity가 공식 문서와 반복 canary에서 안정적이다.
5. duplicate, 단위 drift, 의미를 확인하지 못한 결측·sentinel과 coverage 충돌이 없다.

이 조건을 통과한 series 수를 임의의 목표 숫자에 맞추지 않는다. 채소·과일이 각각 하나도
없거나 네 source의 cross-source identity가 증명되지 않으면 vNext source path를 중단한다.
이름 유사도, 자동 단위환산 또는 사람이 추정한 지역 mapping으로 coverage를 늘리지 않는다.

## 기간과 수집 범위

- 월별 공개 기본값은 최근 36개월이다.
- 12개월은 36개월 gate를 통과한 series·지역에서 제공한다.
- 60개월은 해당 series·지역에 완전한 60개월이 있을 때만 제공한다.
- 지역·시장 일별 자료는 최근 31 calendar day 안의 실제 조사일만 공개한다.
- 지역·시장 기본일은 두 source가 같은 series·region에서 공유하는 최신 조사일이다.
- provider가 공식 aggregate `전체` 지역을 보장할 때만 월별 기본 지역으로 사용한다.
  그렇지 않으면 사용자가 지역을 선택하기 전까지 월별 chart를 만들지 않는다.

월별 source는 주 1회, 지역·시장 source는 24시간마다 platform singleton으로 확인한다.
source gate가 계산한 최악 호출량이 개발계정 일일 quota의 50%를 넘으면 해당 schedule과
구현을 승인하지 않는다.

## 공개 사실과 표현

허용하는 첫 표현은 다음과 같다.

- `월별 과거 가격 패턴`
- `지역별 소매 조사값`
- `시장별 소매 조사값`
- `2026년 7월 KAMIS 소매 조사 평균`
- `조사일 평균이 1주 전 제공값보다 52원 높음 (+3.4%)`
- `KAMIS가 이 기간의 값을 제공하지 않았습니다.`

다음 표현과 기능은 계속 금지한다.

- `제철`, `평년`, 품질·신선도·맛·영양 판단
- `저렴하다`, `비싸다`, `최저가`, `시장 최저`, 구매 추천·구매 시점·절약액
- 실시간 매장가격, 가격 전망과 미래 예측
- 서로 다른 품종·등급·단위·지역을 합산하거나 직접 우열 비교
- 시장명에 포함된 문자열로 대형마트·SSM·전통시장 같은 유형을 추정
- 결측을 0원, 변화 없음, 품절 또는 비제철로 표현

## 사용자 상태와 개인정보

- 사용자 계정, 위치·GPS, 개인화, 알림, 최근 본 품목, server-side 즐겨찾기, analytics와
  광고 audience를 만들지 않는다.
- 검색·부류·비교기간·방향·정렬·날짜·지역·선택 품목은 allowlist된 GET state로만 유지한다.
- 정상화된 유효값은 form과 canonical URL에 다시 표시할 수 있다.
- query state는 cookie, session, database, cache, analytics, application·proxy log와 audit에
  저장하지 않는다.
- public response는 `Cache-Control: no-store`, `Referrer-Policy: no-referrer`와
  `script-src 'none'`을 사용하고 `Set-Cookie`를 만들지 않는다.
- invalid raw input, 내부 source code, credential과 전체 query를 response·error·log에
  반사하지 않는다.

선택 목록은 internal series UUID를 URL 순서대로 최대 5개까지 받는다. 중복은 첫 항목만
유지하고 malformed 또는 5개 초과는 고정 문구의 400이다. active publication에서 사라진
UUID는 원문을 노출하지 않고 제외된 수만 알리는 200 partial state로 처리한다. 목록은 각
품목의 자체 변화만 보여주며 합계·절약액·품목 간 가격순을 만들지 않는다.

## 기술·publication 경계

- Django SSR modular monolith, PostgreSQL, no-JavaScript 공개 화면을 유지한다.
- 공개 request는 외부 source, candidate, raw artifact와 운영 control plane을 호출하지 않는다.
- 기존 `RECENT_RETAIL`은 보존하고 세 역사 source의 승인 collection을 하나의 별도
  `HISTORICAL_RETAIL` bundle로 봉인한다.
- 세 source 중 하나가 실패·검토 대기이면 새 bundle을 만들지 않고 historical
  last-known-good를 유지한다.
- recent와 historical freshness·fact-set hash·rollback은 서로 합성하지 않고 독립적으로
  표시·운영한다.
- 월별, 지역별, 시장별 fact는 별도 typed model을 사용하며 범용 EAV나 범용 ingestion
  framework를 만들지 않는다.
- raw source body는 process memory 밖에 보존하지 않고 redacted receipt와 content hash만
  감사 경계에 남긴다.

## 고정 비목표와 사람 checkpoint

CSV export, 지도, 시장유형 taxonomy, 공개 JSON API, native app, SPA, Redis, Celery, queue,
search engine과 새 외부 source는 이번 버전의 비목표다.

API key 발급·입력, source 권리 판정, code manifest 승인, 첫 historical review·seal·activation,
production database migration, deployment와 traffic switch는 사람 전용 checkpoint다. 이
구현 요청은 개발계정 live source gate를 승인하지만 production 활성화나 배포를 승인하지
않는다.

## frontend 기준

외부 기준 문서
`/Users/woopinbell/Desktop/content-foundry-worktree/production-grade-frontend-design-rule.md`
전체를 적용하며 승인 시 SHA-256은
`ce467f732623722f657155275c40c0667f9819b8d0ad088b27a768bb784fd69b`이다.

초록장부의 따뜻한 종이·장부 visual language를 발전시키되 generic SaaS card grid,
gradient, glassmorphism, 장식용 blob, 무의미한 badge와 과도한 rounded rectangle을 사용하지
않는다. Product, Copy, Brand, UI, Frontend Engineering 관점의 첫 렌더 리뷰를 통과한 뒤에만
browser acceptance를 고정한다.
