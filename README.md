# Audience Foundry Grocery Seasonality

한국 소비자가 한국농수산식품유통공사(KAMIS)의 채소·과일 소매 조사값을
동일 품목·품종·등급·판매 단위 안에서 살피는 Django server-rendered responsive web
서비스입니다. 최근 조사 평균과 1주·1개월·1년 제공값뿐 아니라, 별도로 승인된 historical
publication이 있을 때 선택 지역의 월별 기록, 지역별 조사 범위와 시장별 관측을 제공합니다.

`seasonality`는 저장소 코드명입니다. 첫 MVP는 한 해 전 값 하나를 계절성·평년·제철의
증거로 바꾸지 않습니다. 공개 화면은 공식 조사값과 결정적인 차이만 표시하며
`제철`, `저렴하다`, `비싸다`, `지금 사기 좋다`, `추천`, `최저가`, `가격 예측`을
주장하지 않습니다.

## 저장소 상태

- 저장소: `audience-foundry-grocery-seasonality`
- 기본 브랜치: `main`
- 정책 기준선: `0cc95e7`
- 원격 저장소: `origin`
  (`https://github.com/seungwoo7050/audience-foundry-grocery-seasonality.git`)
- 공개 상태: GitHub 공개 저장소·production service 미배포
- 구현 상태: **vNext local implementation candidate**; production 미배포·미활성
- 시작 기준선: `bb0b28038243c539db2eafcfebc05144d9d59d66`
- source path: 최근 비교 `15156063`; historical `15156060`, `15156062`, `15156065`

vNext 변경은 시작 기준선 이후 local에서만 만들었고 이 문서 갱신 시점에는 `origin`으로
push하거나 production에 배포하지 않았습니다. exact 최종 local SHA와 검증 상태는 완료
보고서에서 고정합니다.

## 역사적 첫 범위

- 첫 source는 공공데이터포털의 KAMIS
  [최근일자 도·소매가격정보 API `15156063`](https://www.data.go.kr/data/15156063/openapi.do)입니다.
- 공개 대상은 source가 `소매`로 식별한 채소류·과일류입니다.
- 첫 publication은 실제 452행을 대사해 승인한 exact 채소 5개·과일 5개 series입니다.
- 상세 화면은 정확히 한 품목·품종·등급·판매 단위·검증된 조사범위의 profile입니다.
- 비교 기준은 source가 같은 row에 제공한 조사일 값과 1주·1개월·1년 제공값입니다.
- 1일 비교, 도매, 수산·축산·곡물, 지역 간 비교, 순위와 알림은 첫 범위가 아닙니다.
- 계정, 검색 이력, 장바구니, 위치, GPS와 개인화는 만들지 않습니다.
- 공개 request는 외부 source를 호출하지 않고 승인된 PostgreSQL publication만 읽습니다.

## 승인된 vNext 소비자 확장

- 월별 화면은 선택한 지역의 최근 36개월 KAMIS 제공 평균·최저·최고와 결측 구간을 그대로
  표시합니다. 12개월과 60개월 선택지는 completeness 계약을 통과한 경우에만 노출합니다.
- 지역별 화면은 동일 series·조사일의 공식 지역명과 제공 평균·최저·최고를 표시합니다.
- 시장별 화면은 선택 지역·조사일의 공식 시장명과 KAMIS 제공값을 가격순이나 시장유형 추정
  없이 표시합니다.
- 선택 목록은 계정·cookie·session 없이 URL에 최대 5개 exact series를 담아 각 품목 자체의
  최근 변화만 나란히 봅니다. 합계·절약액·서로 다른 단위의 우열은 만들지 않습니다.
- `RECENT_RETAIL`과 `HISTORICAL_RETAIL`은 독립적으로 검수·봉인·활성화·rollback하며 freshness와
  fact-set hash를 합성하지 않습니다.
- historical publication이 없거나 exact mapping이 없으면 최근 상세는 계속 작동하고 확장
  링크만 숨깁니다. 공개 request는 어느 화면에서도 source API를 호출하지 않습니다.

## 완료된 source gate

공식 HTTPS API의 실제 요청으로 인증, JSON/XML, 452행 ordered pagination, provider error,
소매·채소·과일 code, exact identity·단위·22개 도시 aggregate coverage와 같은 row의
current/week/month/year 계약을 검증했습니다. 정확한 reference date는 source가 제공하지 않아
`SOURCE_REFERENCE_DATE_UNAVAILABLE`을 보존합니다. raw 보존 권리는 명시적이지 않아
`HASH_ONLY`를 사용하며 정규화 사실과 출처만 공개합니다. 세부 증거는
[구현 계획](docs/IMPLEMENTATION-PLAN.md)에 있습니다.

비교기간 의미만 실패할 때의 B current-only와 API 운영만 실패할 때의 C monthly file은
이번 gate에서 선택되지 않은 **해당 없음(N/A)** fallback입니다. API가
운영에 부적합하지만 공식
[월별 소매가격 파일 `15087482`](https://www.data.go.kr/data/15087482/fileData.do)의
권리·identity·단위가 통과하면 파일 공표본과 각 row의 기준 연월을 명시한 별도 정적
월별 탐색기로 축소할 수 있습니다. KAMIS HTML scraping, 비공식 미러와 quota 우회는
허용하지 않습니다.

vNext 개발계정의 최소 live gate는 공식 API `15156060`, `15156062`, `15156065`의 HTTPS 접근,
non-empty 소매 응답과 exact wrapper·field/type schema를 확인했습니다. 이는 전체 pagination,
모든 series의 cross-source identity, 36·60개월 completeness, production schedule·권리 판정 또는
첫 publication 승인이 아닙니다. 현재 구현의 browser evidence는 disposable PostgreSQL의 합성
fixture를 사용하며 live historical coverage로 해석하지 않습니다. 자세한 경계는
[vNext source gate](docs/VNEXT-SOURCE-GATE.md)에 있습니다.

## local 실행

```sh
make sync
docker compose up -d db
env DATABASE_URL=postgresql://grocery:local-grocery-only@127.0.0.1:55434/grocery \
  .venv/bin/python manage.py migrate --noinput
env DATABASE_URL=postgresql://grocery:local-grocery-only@127.0.0.1:55434/grocery \
  make serve
```

public request는 PostgreSQL의 승인 publication만 읽고 KAMIS를 호출하지 않습니다. 실제 ingestion은
owner-only·Git ignored `.env.local`을 해당 worker process에서만 읽습니다. key를 command argument,
URL, log, fixture 또는 receipt에 넣지 않습니다. production-like 검증과 실제 배포 전 checkpoint는
[운영 런북](docs/OPERATIONS-RUNBOOK.md)을 따릅니다.
local 명령은 ambient `DATABASE_URL`을 상속하지 않도록 위의 고정 loopback Compose database를
각 process에 명시한다. 다른 database를 대상으로 lifecycle rehearsal을 실행하지 않는다.

local candidate 재현은 clean exact release SHA에서 잠금 설치, 새 빈 DB의 forward migration,
collectstatic, production-like process와 health·public smoke를 같은 순서로 수행합니다. application
rollback은 reverse migration 없이 최신 schema를 유지하고 검증된 이전 code·static으로
되돌립니다. 실제 artifact packaging, bundled notice, vendor deploy·traffic·rollback CLI는
platform을 선택한 뒤 사람이 별도로 승인하는 production checkpoint입니다.

## 계약·증거 문서

- [도메인 개요](docs/DOMAIN-BRIEF.md)
- [제품 결정](docs/PRODUCT-DECISIONS.md)
- [시스템 경계](docs/SYSTEM-BOUNDARIES.md)
- [데이터·감사 모델](docs/DATA-AND-AUDIT-MODEL.md)
- [기술 결정](docs/TECHNOLOGY-DECISIONS.md)
- [MVP 인수 기준](docs/MVP-ACCEPTANCE.md)
- [첫 구현 계획과 source gate 증거](docs/IMPLEMENTATION-PLAN.md)
- [vNext 제품·source 계약](docs/VNEXT-PRODUCT-CONTRACT.md)
- [vNext source gate](docs/VNEXT-SOURCE-GATE.md)
- [vNext public-read 계약](docs/VNEXT-PUBLIC-READ-CONTRACT.md)
- [Phase 0 역사적 기준을 포함한 현재 운영 런북](docs/OPERATIONS-RUNBOOK.md)
- [Phase 0 배포 직전 완료 보고서](docs/COMPLETION-REPORT.md) — local gate 결과와 production
  사람 checkpoint를 보존하며 vNext 결과는 별도 부록으로 기록

production platform·PostgreSQL·role credential·secret store·domain·DNS 선택, historical code
manifest·mapping 승인, 첫 production collection·review·seal·activation, 실제 배포와 traffic
전환은 사람 전용 작업입니다. historical 전용 health·authoritative inspection·backup canonical
검증과 이전 application의 migration `0028` 호환성도 production 전 별도 증명이 필요합니다.
이 저장소는 네이티브 앱, 앱스토어 배포나 별도 SPA를 포함하지 않습니다.
