# Audience Foundry Grocery Seasonality

한국 소비자가 한국농수산식품유통공사(KAMIS)의 채소·과일 소매 조사 평균을
동일 품목·품종·등급·판매 단위 안에서 source row가 제공한 조사일 값과 1주·1개월·1년
제공값을 중립적으로 비교하는 Django server-rendered responsive web 서비스입니다.

`seasonality`는 저장소 코드명입니다. 첫 MVP는 한 해 전 값 하나를 계절성·평년·제철의
증거로 바꾸지 않습니다. 공개 화면은 공식 조사값과 결정적인 차이만 표시하며
`제철`, `저렴하다`, `비싸다`, `지금 사기 좋다`, `추천`, `최저가`, `가격 예측`을
주장하지 않습니다.

## 저장소 상태

- 저장소: `audience-foundry-grocery-seasonality`
- 기본 브랜치: `main`
- 정책 기준선: `0cc95e7`
- 원격 저장소: 없음
- 공개 상태: 로컬·미공개
- 구현 상태: **Phase 0 배포 직전 완료**; production 미배포
- 활성 source path: **A — 최근 비교 MVP**

## 고정된 첫 범위

- 첫 source는 공공데이터포털의 KAMIS
  [최근일자 도·소매가격정보 API `15156063`](https://www.data.go.kr/data/15156063/openapi.do)입니다.
- 공개 대상은 source가 `소매`로 식별한 채소류·과일류입니다.
- 첫 publication은 실제 452행을 대사해 승인한 exact 채소 5개·과일 5개 series입니다.
- 상세 화면은 정확히 한 품목·품종·등급·판매 단위·검증된 조사범위의 profile입니다.
- 비교 기준은 source가 같은 row에 제공한 조사일 값과 1주·1개월·1년 제공값입니다.
- 1일 비교, 도매, 수산·축산·곡물, 지역 간 비교, 순위와 알림은 첫 범위가 아닙니다.
- 계정, 검색 이력, 장바구니, 위치, GPS와 개인화는 만들지 않습니다.
- 공개 request는 외부 source를 호출하지 않고 승인된 PostgreSQL publication만 읽습니다.

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
- [Phase 0 배포 직전 운영 런북](docs/OPERATIONS-RUNBOOK.md)
- [Phase 0 배포 직전 완료 보고서](docs/COMPLETION-REPORT.md) — local gate 결과와 production
  사람 checkpoint를 고정

production platform·PostgreSQL·role credential·secret store·domain·DNS 선택, 실제 배포와 traffic
전환은 사람 전용 작업입니다. 이 저장소는 네이티브 앱, 앱스토어 배포나 별도 SPA를 포함하지
않습니다.
