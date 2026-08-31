# 첫 구현 계획

> 이 문서는 Phase 0 A path의 구현·증거 기록이다. 2026-08-31 이후 historical consumer
> 확장은 `VNEXT-PRODUCT-CONTRACT.md`, `VNEXT-SOURCE-GATE.md`와
> `VNEXT-PUBLIC-READ-CONTRACT.md`를 따른다.

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
- clean Git의 exact release SHA에서 locked dependency·forward migration·static·process를 다시
  만들 수 있는 platform-independent deploy 순서와 application·publication rollback 절차를
  기록한다. vendor CLI와 production packaging은 platform 선택 뒤 사람 checkpoint로 분리한다.

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
- `SourceConfiguration.rights_evidence_locator`는 위 공식 dataset landing이고
  `rights_evidence_sha256`은 그 landing에서 받은 **첨부 명세·코드 ZIP**의 위 hash다. 동적으로
  바뀌는 landing HTML 자체의 hash라고 해석하지 않는다. 권리 표시는 landing의
  `이용허락범위 제한 없음`, 포털
  [이용정책](https://www.data.go.kr/ugs/selectPortalPolicyView.do), 확인 시각과 아래의 보수적
  공개 판정을 함께 검수한다.
- 비용 무료, 개발·운영 자동승인, 개발계정 트래픽 10,000. 기간 단위와 초당
  수치는 문서에 없으므로 호출 예산은 수집 실행당 최대 12회로 더 좁게 제한한다.
- dataset 표시는 `이용허락범위 제한 없음`이고 포털 이용정책은 자유이용에
  상업·비상업 이용과 변형·2차 저작물 작성을 허용한다. 제품은 그보다 좁게
  정규화 사실만 공개하고 aT·공공데이터포털·dataset ID·조사일·검토일을 표시한다.
- raw payload 보존을 별도로 열거한 문구는 확인하지 못했다. 따라서 artifact
  retention은 `HASH_ONLY`이고 원문 재배포도 하지 않는다.
- 실제 generation 승인에 연결된 private gate evidence는 ReviewDecision
  `330cad14-2102-4dcf-a023-93a7368c7efb`의 `acceptance_evidence_sha256`
  `2e6dcf9df27077396b8aedf8abaaf69d10bbca2f3a036d6c0127ccb1f434cca6`으로 고정했다.
  이 hash는 첨부 ZIP hash나 landing HTML hash와 다른 private evidence digest/commitment이며
  원문 locator가 아니다.

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
- current 가격 452개와 available week·month·year reference는 모두 0이 아닌 scale-0 Decimal
  string이었다. reference missing은 JSON `null`이며 week 27, month 61, year 91개였다. 0, 음수,
  dash 또는
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
  가공식품을 [22개 도시에서 매일(휴일 제외) 조사](https://www.kamis.or.kr/customer/price/knowhow/knowhow.do)하고,
  공개 화면은 지역 `전체`를
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

합성 fixture는 secret이나 raw body 없이 failure·lifecycle contract를 먼저 검증하는 데만
사용했고 live 접근·권리 증거로 주장하지 않았다. 그 뒤 실제 local ingestion command가
`.env.local`을 process 안에서만 읽어 다음 live 폐쇄 루프를 완료했다.

### 실제 첫 폐쇄 루프 결과

- FetchAttempt `6c4bcbeb-d47c-4648-b6ce-01988316b7dc`와
  `4207e628-3ab9-4a12-a7ae-0cdbec67d744`는 각각 5 page·452 row를 완성했고, 같은 ordered
  manifest `dd893ef82f1f1597a2b65ca6024f31fb7b62ae3f10b13c6d6185365eca2798ba`를 만들었다.
- 두 attempt는 하나의 hash-only SourceArtifact
  `70955f24-b61d-4b43-a7de-8e603f6ae459`와 ParseRun
  `0c7fad64-e49b-4c8c-9929-aece2782354d`로 수렴했다. 첫 parse는 10 accepted·442
  out-of-scope, 두 번째는 replay였고 result hash는
  `512c65031cdfe2b734af4245d974390073999a32ad494cd9c94b33c2f165261e`다.
- ReviewDecision `330cad14-2102-4dcf-a023-93a7368c7efb`가 generation을 승인했다.
  불변 revision v1 `dc6f5c83-92cc-48e7-8103-76f3fd1a668b`과 v2
  `2e2b1468-0d41-4635-92a8-c868a80ece1e`를 seal한 뒤 v1 activate, v2 activate, v1 rollback을
  append-only activation으로 수행했다.
- 현재 channel version은 `3`, current는 v1이고 entry count는 10이다. canonical active fact-set
  hash는 `6de8e26c22dcee4a7ce4a6e1a0640999399d126d62124cc1b1d7aefcf9aa66a9`다.
- public request는 이 current pointer만 읽으며 source API를 호출하지 않는다. actual raw body는
  저장·fixture·Git·publication에 남기지 않았다.
- 기존 source row의 `state_changed_at`·`rights_confirmed_at`은 실제 관측 시각이 아니라
  `2026-08-29T15:00:00Z`(KST 자정) date-precision 값이었고 정확한 live 관측 시각은 보존되지
  않았다. 원본은 수정·삭제하지 않았다. migration `0010`은 correction
  `49143c27-d2dd-5fbd-b1dc-4aa3cc002fab`을 append-only로 추가한다. 증거 commit
  `d23e5707e1fc3bf6e032d459b149b946b0451e00`의 `2026-08-30T02:23:44Z`는 정확한 관측값이 아니라
  durable source-gate-decision recorded-at upper bound다. helper·DB trigger·review·inspection은
  base 일치와 chronology를 검사한 이 effective 값만 사용하고 correction update/delete를 거부한다.
  새 DB bootstrap은 처음부터 exact effective 값을 쓰므로 correction row를 만들지 않는다.

## typed schema와 migration

하나의 Django app `grocery`가 다음 닫힌 model을 소유한다.

- `SourceConfiguration`: dataset/interface/endpoint allowlist, mode, coverage revision,
  rights evidence hash, `HASH_ONLY`, logical secret name, timeout·retry·page budget,
  `PLATFORM_SINGLETON` schedule mode와 24시간 cadence
- `SourceConfigurationGateTimestampCorrection`: 고정 legacy source row의 date-precision 원본을
  보존하면서 검증된 gate-decision recorded-at upper bound를 append-only로 연결
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

현재 migration은 create/add 중심의 forward migration이다. local schema 검증은 새 빈 PostgreSQL에
`0001`부터 최신까지 처음부터 적용하는 방식이며 역방향 migration을 실행하지 않는다.
application rollback은 최신 forward schema를 그대로 둔 채, 그 schema를 읽을 수 있다고 검증한
이전 application SHA와 static으로 되돌린다.

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
- 공개 화면과 운영자 inspection evidence에 aT, dataset 15156063, landing URL, 조사일, 확인·검토일,
  coverage revision을 표시한다.
- structured audit log는 lifecycle ID·상태만 기록한다. health와 DB/publication readiness,
  last-known-good 나이는 분리한다.
- PostgreSQL logical dump를 암호화 production backup으로 주장하지 않는다. local gate는
  고정 local Docker socket에서 발견·identity-pin한 exact Compose DB container만 사용한다.
  backup receipt의 non-secret manifest SHA-256을 out-of-band expected 값으로 반드시 다시 전달하고,
  disposable DB의 `pg_dump`/`pg_restore`로 row count·canonical publication·current pointer를
  검증한다. restore 실패 시 같은 invocation이 만든 exact target만 자동 정리하고 부재를 확인하며,
  production에는 managed encryption/PITR·RPO 24h/RTO 4h가 여전히 필요함을 기록한다.

## Phase 0 local candidate 검증 결과

- 실제 browser E2E는 Chromium 152에서 `360x800`, `390x844`, `768x1024`, `1440x900`을
  통과했고 18개 full-page screenshot을 추적했다. mobile 중복 breadcrumb와 query reflection
  결함을 수정한 뒤 overflow, touch target, 긴 한국어, 상태 matrix, keyboard·focus와 semantic
  label을 다시 검증했다.
- axe-core actual catalog/detail의 WCAG A/AA violation은 0이다. 별도 palette test는 gradient
  양 끝을 포함해 4.5:1 이상을 보장한다.
- WhiteNoise compressed manifest storage가 hashed CSS를 만들었고 production-like Gunicorn에서
  HTML의 hashed reference, CSS `200`, media type과 immutable cache를 확인했다.
- PostgreSQL custom backup을 새 격리 DB에 restore해 25개 public table, 28개 migration,
  row count·audit chain·active revision·fact-set hash를 대사했다. 별도 빈 DB에는 migration
  `0001`~`0010`을 처음부터 적용하고 empty fail-closed health를 확인했다.
- application rollback rehearsal target `d6d7d08c9de9a78eb597fec6e232b0e2d24a1ec1`와 현재
  application이 같은 최신 schema·publication에서
  live/ready/freshness/catalog/detail·static을 읽는 application rollback을 훈련했다. publication은
  v2에서 이전 sealed v1으로 append-only rollback했다.
- production publication command는 external MFA/IAM private job을 전제로 default-off flag,
  `DEBUG`·Admin·QA off, exact release SHA, fixed reviewer/publisher와 exact Django permission을
  요구한다. 실제 IAM·role DB grant·actor provisioning은 production 사람 checkpoint다.
- structured JSON allowlist, request correlation·deploy SHA, liveness/readiness/freshness 판정과
  fixed scheduler failure code를 검증했다. 실제 alert delivery·retention은 platform checkpoint다.
- active artifact의 마지막 source 확인 시각은 `2026-08-30T04:00:48.696744Z`이고 36시간 다음
  확인 경계는 `2026-08-31T16:00:48.696744Z`(`2026-09-01 01:00:48 KST`)다.
- final locked full suite, clean Git evidence와 고정 900초·9,000요청 profile 결과를
  `docs/COMPLETION-REPORT.md`에 고정했다. 성능 profile은 nominal `100 ms` cadence, bounded
  recovery floor `90 ms`, effective paced deadline jitter p95 `100 ms 이하`, catch-up burst `0`,
  20개 고정 logical virtual-user session의 전원 참여·round-robin과 elapsed `900~903초`를 함께
  요구했고 최종 corrected run이 모두 통과했다. 실제 in-flight peak는 논리 사용자 수와 분리해
  기록했다.

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

각 commit은 한 가지 가역적 의도와 가장 가까운 test를 함께 둔다. generated migration·lockfile·
browser evidence와 한 불변식을 함께 바꾸는 model/service/test 묶음은 줄 수·파일 수가 커질 수
있지만, source·schema·parser·review·publication·UI·operations의 rollback 경계는 섞지 않는다.
실제 history는 이 단일 의도 경계를 기준으로 검증하며 임의의 100줄·3파일 상한을 통과했다고
주장하지 않는다.
