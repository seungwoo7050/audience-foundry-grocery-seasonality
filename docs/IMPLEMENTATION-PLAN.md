# 첫 구현 계획

## 결정

선택 path는 **A — 최근 비교 MVP**다. 공개 표현은 `KAMIS 소매 조사 평균`과
source가 같은 row에 제공한 `1주 전·1개월 전·1년 전 제공값`의 결정적 차이로
제한한다. `1일 전` 및 kg 환산값은 저장·공개하지 않는다.

정확한 reference date는 source에 없으므로 모든 reference에
`SOURCE_REFERENCE_DATE_UNAVAILABLE`을 보존한다. 조사일에서 날짜를 역산하지
않는다. `제철`, `평년`, `저렴`, `비싸`, `추천`, `최저가`, `실시간 매장가격`,
예측을 만들지 않는다.

## Phase 0 배포 직전 완료 정의

이 세션의 종료점은 실제 배포가 아니라 **Phase 0 배포 직전 production candidate**다.
승인된 A source path의 실제 수집·검수·publication과 핵심 읽기 흐름을 하나의 Django
server-rendered responsive web으로 완성한다. 별도 SPA, 네이티브 앱과 앱스토어 배포는
만들지 않는다.

candidate는 다음을 모두 실제 증거로 통과해야 한다.

- PostgreSQL에 실제 live generation을 수집하고 사람이 승인한 revision을 activate한다.
- desktop·mobile이 같은 SSR route와 semantic HTML을 사용하며 `360x800`, `390x844`,
  `768x1024`, `1440x900`에서 실제 브라우저 screenshot과 end-to-end flow를 남긴다.
- 각 viewport에서 가로 overflow, typography·정보 계층, touch target, 긴 한국어
  identity·단위·출처·freshness, form 입력·제출·오류 수정을 검수한다.
- loading, empty, unavailable, stale, validation, server error를 결정적으로 재현하고 상태를
  색상 외의 text·icon·semantic markup으로도 전달한다.
- keyboard-only 순서, visible focus, label·error association와 screen-reader accessible name을
  자동 검사와 수동 browser 검수로 확인한다.
- production-like `DEBUG=False` check, 보안·dependency·license scan, 고정 부하 profile,
  구조화 log, liveness/readiness와 freshness alert 판단을 검증한다.
- disposable PostgreSQL에서 backup/restore로 audit chain·row count·hash·current pointer를
  대사하고 이전 승인 publication rollback을 훈련한다.
- clean Git의 exact release SHA, migration·deploy·application rollback·publication rollback
  절차와 production platform·database·secret·domain의 사람 전용 잔여 작업을 기록한다.

production platform·database·credential과 domain·DNS 선택, 실제 secret 주입 및 실제 배포는
사람 checkpoint다. 따라서 위 local gate가 끝나도 `Phase 0 완료`나 `배포 완료`라고 표현하지
않고 `Phase 0 배포 직전 완료`라고만 보고한다.

## source gate 증거

검증일은 2026-08-30(KST)이며 raw response body는 파일이나 Git에 저장하지 않았다.

### 공식성·권리

- landing: `https://www.data.go.kr/data/15156063/openapi.do`
- 제공기관: 한국농수산식품유통공사(aT), 관리부서: 디지털AI운영부
- interface: Swagger `1.0.0`, `GET https://apis.data.go.kr/B552845/recent/price`
- 공식 첨부 명세·코드 ZIP SHA-256:
  `07417ea9eb882a33615721256ff8be3b131cdb10bbc9c7b40472bf049a7e0f88`
- 비용 무료, 개발·운영 자동승인, 개발계정 트래픽 10,000. 기간 단위와 초당
  수치는 문서에 없으므로 호출 예산은 수집 실행당 최대 12회로 더 좁게 제한한다.
- dataset 표시는 `이용허락범위 제한 없음`이고 포털 이용정책은 자유이용에
  상업·비상업 이용과 변형·2차 저작물 작성을 허용한다. 제품은 그보다 좁게
  정규화 사실만 공개하고 aT·공공데이터포털·dataset ID·조사일·검토일을 표시한다.
- raw payload 보존을 별도로 열거한 문구는 확인하지 못했다. 따라서 artifact
  retention은 `HASH_ONLY`이고 원문 재배포도 하지 않는다.

### 인증·HTTP·오류

- 공식 Swagger 가이드는 params client에 일반 인증키(Decoding)를 넣으라고 한다.
  env 값이 percent-encoded 형태이면 process memory에서 한 번만 decode한 뒤 client가
  한 번 encode한다. key와 전체 query는 어느 receipt에도 남기지 않는다.
- 최소 live 요청은 HTTPS, redirect 없음, HTTP 200, provider `resultCode=0`,
  `application/json; charset=utf-8`로 성공했다.
- XML canary도 HTTP 200, provider code `0`, `application/xml`, UTF-8과 같은 23개
  item field를 반환했다.
- key 누락 canary는 HTTP 401과 JSON
  `OpenAPI_ServiceResponse.cmmMsgHeader` envelope, reason code `20`,
  `SERVICE_KEY_IS_NULL`을 반환했다.
- portal 문서는 timeout `05`, 일 허용량 `22`, 초당 허용량 `23`, provider 명세는
  내부 오류 `-1`, 미등록 `-3`, 서버 오류 `-5`, 트래픽 초과 `-10`을 정의한다.
  실제 429를 만들기 위한 quota 소진은 하지 않았다. client는 HTTP 429,
  timeout·일시적 5xx와 provider `-1|-5|-10|22|23`만 bounded retry하고 auth,
  invalid parameter, schema·identity 오류는 retry하지 않는다.

### pagination·schema·멱등성

- 실제 `totalCount=452`. `numOfRows=100`으로 5 page를 각각
  `100,100,100,100,52`행 대사했다.
- 독립된 두 FetchAttempt의 page body hash sequence와 row sequence가 같았다.
  ordered manifest SHA-256은 두 번 모두
  `dd893ef82f1f1597a2b65ca6024f31fb7b62ae3f10b13c6d6185365eca2798ba`였다.
- page별 declared page·total, media type, charset, byte length, body hash만 receipt로
  남길 수 있다. artifact identity는 ordered page body-hash manifest다.
- 모든 452 row가 같은 23-field schema였고 semantic series 중복은 0이었다.
  identity field 13개는 모두 non-empty string이었다.
- current 가격 452개는 모두 0이 아닌 scale-0 Decimal string이었다. reference
  missing은 JSON `null`이며 week 27, month 61, year 91개였다. 0, 음수, dash 또는
  malformed sentinel은 이번 generation에서 없었다. 이후 등장하면 격리한다.
- 실제 조사일은 row별 최신값이며 하나의 dataset-wide 날짜가 아니다. 오래된
  series도 존재하므로 row의 `exmn_ymd`를 그대로 보존하고 최근성 cutoff를
  추측하지 않는다.

### code·series·coverage

- 공식 code와 live response가 `01=소매`, `200=채소류`, `400=과일류`로 일치했다.
- 공식 code workbook의 item 136, variety 332, grade 693 row에는 동일 semantic code의
  name conflict가 없었다.
- exact identity는
  `(01, category, item, variety, grade, raw unit, raw unit size,
  KAMIS_RETAIL_ALL_REGIONS_22_CITIES_V1)`이다.
- API에는 region·market field가 없다. 공식 KAMIS 조사요령은 일반 소매 농·수산물과
  가공식품을 22개 도시에서 매일(휴일 제외) 조사하고, 공개 화면은 지역 `전체`를
  제공한다. 이 evidence revision을 aggregate coverage identity에 고정하고 도시 목록,
  조사방법 또는 API field가 바뀌면 publication을 차단한다.
- source row 안에 identity와 current/week/month/year field가 함께 있고 exact filter가
  동일 code tuple 한 row만 반환했다. reference date field는 없으므로 날짜 상태는
  unavailable이다. KAMIS 조사방법상 일시 품절 값이 일정 기간 전일값으로 입력될 수
  있으므로 공개 문구는 source의 `제공값` 이상으로 확대하지 않는다.

### bounded 5+5 canary

공식 code workbook의 item·variety·grade가 모두 exact match했고 live current/week/month/year가
유효한 다음 series를 확인했다. unit과 unit size는 live row의 raw identity다.

- 채소: `200/212/00/04 포기×1`, `200/213/00/04 g×100`,
  `200/214/01/04 g×100`, `200/214/02/04 g×100`,
  `200/215/00/04 kg×1`
- 과일: `400/414/12/24 kg×2`, `400/430/00/04 개×1`,
  `400/411/06/04 개×10`, `400/420/02/04 개×1`,
  `400/419/02/04 개×10`

필터 canary는 소매 채소 58, 소매 과일 37 total을 선언했고 요청한 첫 5 row가 모두
요청 code와 일치했다. exact 과일 series filter는 total 1, received 1이었다.

## 가장 작은 첫 폐쇄 루프

1. hash-only 합성 canary 한 generation을 `FetchAttempt → PageReceipt → SourceArtifact →
   ParseRun`으로 기록한다.
2. exact 소매 채소·과일 typed snapshot과 세 reference를 만들고 전체 row를 대사한다.
3. reviewer가 generation을 승인한다.
4. 승인 decision으로 불변 `PublicationRevision`을 만들고 publisher가 한 transaction에서
   `ACTIVATE` 사건과 `RECENT_RETAIL` current pointer를 전환한다.
5. server-rendered 목록에서 category와 공식 item name을 검색하고 exact series 상세에서
   source 조사일, 22개 도시 aggregate coverage, raw unit, current와 reference 제공값,
   결정적 차이, publication 검토일을 읽는다.
6. 같은 artifact replay는 snapshot과 publication을 중복하지 않고 새 FetchAttempt만 남긴다.

첫 local demo는 secret이나 raw body 없이 고정된 최소 합성 fixture로 lifecycle을
증명한다. fixture는 live 접근·권리 증거로 주장하지 않으며 위 live receipt hash와
분리한다. 실제 local ingestion command는 `.env.local`을 process에서만 읽고 hash-only
artifact를 생성한다.

## typed schema와 migration

하나의 Django app `grocery`가 다음 닫힌 model을 소유한다.

- `SourceConfiguration`: dataset/interface/endpoint allowlist, mode, coverage revision,
  rights evidence hash, `HASH_ONLY`, logical secret name, timeout·retry·page budget
- `FetchAttempt`, `PageReceipt`: 상태, attempt ordinal, redacted request shape, ordered page,
  HTTP/provider code, counts, byte length, body SHA-256, failure class
- `SourceArtifact`: source identity + ordered manifest SHA-256 unique, total bytes,
  media type·encoding, first seen, `HASH_ONLY`
- `ParseRun`: artifact/parser/config unique, deterministic result hash, reconciliation counts
- `PriceSeriesKey`: product class·category/item/variety/grade **code**, raw unit·size와 coverage
  identity의 semantic tuple unique. name은 display·drift 검수 field이며 unique identity에
  포함하지 않는다. 같은 code의 name drift는 generation을 차단한다.
- `RetailPriceSnapshot`: parse run + series + source effective date unique, 0보다 큰 current
  Decimal KRW
- `ReferencePrice`: snapshot + `WEEK|MONTH|YEAR` unique,
  `value_status=AVAILABLE|UNAVAILABLE`, nullable Decimal·unavailable reason,
  `reference_date_status=PROVIDED|SOURCE_REFERENCE_DATE_UNAVAILABLE`, nullable source
  reference date를 서로 독립적으로 보존한다.
- `PriceChangeFact`: reference unique, direction, nullable difference·percentage,
  calculation version `decimal-half-up-v1`
- `ReviewDecision`: append-only `APPROVE|REJECT`, reviewer actor, report hash, reason
- `PublicationRevision`: immutable approved generation, `RECENT_RETAIL`, fact-set hash,
  copy revision
- `PublicationEntry`: revision과 snapshot의 ordered immutable membership
- `PublicationActivation`: append-only `ACTIVATE|ROLLBACK|WITHDRAW`, previous/target revision
- `PublicationChannel`: channel별 nullable current revision pointer

각 enum에는 명시적 DB `CheckConstraint`를 두고 count·byte length는 0 이상, available
price는 0보다 큼, identity는 complete임을 보강한다. 상태 간·row 간 불변식은 service
transaction과 row lock으로 검사하고 PostgreSQL constraint trigger로 fail-closed한다.
모든 audit foreign key는 `PROTECT`/`RESTRICT`다. `ReviewDecision`, `PublicationRevision`,
`PublicationEntry`, `PublicationActivation`은 DB trigger로 update/delete를 막고
`PublicationChannel.current_revision`만 activation transaction에서 변경할 수 있다.
activation event insert와 pointer update는 `transaction.atomic()`과 row lock으로 한
transaction에서 처리한다.

첫 migration은 additive create-only다. rollback은 application rollback 후 새 빈 local DB에
역방향 migration을 실행하며 production contract 단계는 없다.

## 구현·검증 범위

### positive

- actual live shape와 같은 합성 JSON의 deterministic parse, 5+5 exact series
- null reference의 `UNAVAILABLE`, valid Decimal의 signed difference/direction/half-up percent
- full reconciliation, artifact·parse·snapshot replay idempotency
- approve 후 atomic activation, 목록·검색·상세와 provenance 표시
- 이전 승인 revision으로 append-only rollback
- 실제 live generation의 검수·activation과 네 viewport browser E2E/screenshot

### negative

- timeout, 401, 429, 5xx, provider error, response-size·page-budget 초과
- field/type drift, unknown code, code/name conflict, malformed·zero·negative price,
  missing current, duplicate semantic identity, unit·coverage drift
- partial page, total mismatch, nondeterministic replay, unapproved activation,
  transaction failure와 concurrent pointer change
- query secret·검색어·raw body가 log/receipt/public response에 없음
- 서로 다른 series comparison 금지와 source date 역산 금지

### 보안·개인정보·license·운영

- 공개 account/session/analytics 없이 GET-only SSR. 검색어는 DB·session·log에 저장하지 않는다.
- `.env.local`은 ignore·owner-only이고 key는 URL/log/error/fixture/receipt에 넣지 않는다.
- endpoint allowlist, HTTPS-only, redirect 거부, 10초 timeout, 4 MB/page와 12 call/attempt
  상한, bounded retry를 적용한다.
- raw artifact는 저장하지 않고 SHA-256·byte length·redacted receipt만 보존한다.
- 공개 화면과 Admin evidence에 aT, dataset 15156063, landing URL, 조사일, 확인·검토일,
  coverage revision을 표시한다.
- structured audit log는 lifecycle ID·상태만 기록한다. health와 DB/publication readiness,
  last-known-good 나이는 분리한다.
- PostgreSQL logical dump를 암호화 production backup으로 주장하지 않는다. local gate는
  disposable DB의 `pg_dump`/`pg_restore`로 row count·hash·current pointer를 검증하고,
  production에는 managed encryption/PITR·RPO 24h/RTO 4h가 여전히 필요함을 기록한다.

## 작은 가역적 commit 순서

1. `docs: record approved source gate plan` — 이 문서만; rollback은 문서 commit revert.
2. `docs: define phase zero release gate` — responsive·browser·release acceptance 보강.
3. `build: pin django runtime` — Python/Django/PostgreSQL/uv 호환성과 lock·license 고지.
4. `feat(audit): record source fetch attempts` — configuration/fetch/page + state constraints.
5. `feat(audit): deduplicate source artifacts` — hash manifest + two-attempt replay.
6. `feat(audit): reconcile deterministic parses` — parse counts·hash·nondeterminism gate.
7. `feat(price): identify exact retail series` — code identity·name drift·coverage constraints.
8. `feat(price): store current retail snapshots` — positive price·date·idempotency.
9. `feat(price): derive typed reference changes` — value/date 상태 XOR·half-up·sign constraints.
10. `feat(review): append generation decisions` — approve/reject chain과 DB immutability.
11. `feat(publication): seal immutable revisions` — canonical membership·fact-set hash.
12. `feat(publication): activate revisions atomically` — operation idempotency·pointer·rollback.
13. `feat(source): parse kamis recent rows` — deterministic parser/reconciliation + fixtures/tests.
14. `feat(source): fetch kamis receipts safely` — management command, redaction, bounds/tests.
15. `feat(public): render published prices` — Forms/routes/templates/CSS + 상태·접근성 tests.
16. `test(web): verify responsive browser flows` — 네 viewport E2E·screenshot과 결함 수정.
17. `ops: verify release operations` — 관측성, 부하, PostgreSQL backup/restore와 rollback gates.
18. `docs: record predeploy completion evidence` — exact release SHA, 배포·rollback 절차와
    production 사람 checkpoint.

각 commit은 구현과 가장 가까운 test를 함께 둔다. 100줄 또는 3개 파일을 넘으면 migration,
검토 또는 rollback 경계를 더 분리하며 generated migration과 lockfile은 크기 예외를 기록한다.
