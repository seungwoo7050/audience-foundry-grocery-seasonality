# 데이터·감사 모델

## 설계 원칙

- 범용 EAV나 원문 JSON을 domain model로 사용하지 않습니다.
- source receipt, bytes, parsing, domain fact, 사람 결정과 publication을 분리합니다.
- 공식 코드가 identity이고 이름은 표시·검색 보조입니다.
- 돈은 float가 아닌 검증된 자릿수의 Decimal로 저장합니다.
- 조사일, source 기록시각, fetch 시각과 공개시각을 서로 바꾸어 쓰지 않습니다.
- 불변 publication, append-only activation과 원자적인 current pointer로 rollback을
  수행합니다.

## 상태 수명주기

```text
FetchAttempt
  -> SourceArtifact
  -> ParseRun
  -> RetailPriceSnapshot + ReferencePrice + PriceChangeFact
     | MonthlyRetailPriceSnapshot
  -> ReviewDecision
  -> PublicationRevision
  -> PublicationActivation + channel current pointer
```

### SourceConfiguration

`DRAFT → RIGHTS_APPROVED → ACTIVE | PAUSED | REJECTED`

- source owner, dataset ID, interface revision과 endpoint allowlist
- authentication mode, quota, timeout, retry policy와 schedule
- rights evidence locator·hash·확인시각, raw retention 결정
- 활성 공개 mode: `RECENT_COMPARISON`, `CURRENT_ONLY`, `STATIC_MONTHLY_FILE`

credential 값은 저장하지 않고 managed secret의 논리 이름만 참조합니다.

### FetchAttempt

`STARTED → SUCCEEDED | RETRYABLE_FAILED | TERMINAL_FAILED`

- 전체 pagination을 완성하려는 하나의 논리적 획득 시도와 `attempt_ordinal`
- source configuration revision, 시작·종료시각과 redacted normalized request
- 순서가 있는 `PageReceipt`: request ordinal, page identity, HTTP status, provider result code,
  declared total, received row count와 body hash
- 전체 received rows·pages, page 대사 결과와 failure class
- response body hash 또는 body를 받지 못한 이유

논리적 획득의 재시도는 새 attempt입니다. 한 attempt의 page를 다른 attempt와 섞지 않으며
모든 page·row 대사가 끝나기 전에는 `SourceArtifact`를 만들지 않습니다. key, raw query
string, response body와 개인정보를 receipt에 넣지 않습니다.

### SourceArtifact

`RECEIVED → VALIDATED | REVIEW_REQUIRED | REJECTED`

- source identity와 ordered page body hash manifest의 SHA-256 content hash
- 전체 byte length, page media type·encoding과 acquisition method
- first observed timestamp, rights·retention decision
- private object locator 또는 `HASH_ONLY`

같은 source identity와 ordered page manifest hash만 중복 제거합니다. page가 하나인 source도
같은 manifest 규칙을 사용합니다. body가 같아도 새 attempt가 기존 artifact를 참조하며
artifact 자체는 바꾸지 않습니다. `last_checked_at`은 source별 성공 attempt에서 계산하고
`fetched_at`은 artifact identity가 아닙니다. 같은 bytes 확인은 source 조사일이나 공개 데이터
freshness를 갱신하지 않습니다.

### ParseRun

`STARTED → VALIDATED | QUARANTINED | FAILED`

- artifact, parser revision, source schema/interface revision
- deterministic configuration hash와 result hash
- total, accepted, missing-reference, out-of-scope, quarantined row count
- duplicate series, code/name conflict, unit drift와 error summary

같은 artifact·parser·configuration의 replay는 같은 result hash와 candidate를 만들어야
하며 중복 snapshot이나 review task를 만들지 않습니다.

## Typed domain model

### ProductClass

첫 공개 값은 `RETAIL`뿐입니다. source의 도매 행은 `out_of_scope`로 대사합니다.

### GroceryCategory

- `VEGETABLE`
- `FRUIT`

공식 source code와 표시명을 함께 보존합니다. code mapping이 실제 응답과 code 문서에서
증명되지 않으면 category를 추측하지 않습니다.

### CommodityKey

- 공식 부류 code
- 공식 품목 code
- 공식 품종 code
- 각 공식 원문 표시명

이름 수정은 identity 변경이 아닐 수 있으므로 code/name 충돌을 검토 대상으로 둡니다.
공식 code가 재사용되거나 범위가 바뀌면 자동 병합하지 않습니다.

### GradeKey

- 공식 등급 code
- 공식 원문 등급명

등급 없음과 등급 미제공을 같은 값으로 만들지 않습니다.

### SaleUnit

- 원문 단위
- 원문 단위크기
- 의미를 증명한 source contract revision

`개`, `포기`, `단`, `봉`, `kg` 사이를 자체 변환하지 않습니다. 단위 표현을 정규화해
검색할 수 있어도 원문과 semantic identity를 보존합니다.

### MarketCoverage

- source가 제공하거나 gate에서 검증한 coverage code
- 공개 가능한 정확한 설명
- 집계 수준과 평균 의미의 evidence reference

source가 지역 차원을 제공하지 않으면 `전국`을 만들어내지 않습니다. 검증된 source
aggregate를 별도 typed 값으로 둡니다. coverage를 설명하지 못한 candidate는 공개하지
않습니다.

### PriceSeriesKey

다음 tuple의 immutable semantic identity입니다.

`(RETAIL, category_code, item_code, variety_code, grade_code, raw_unit, raw_unit_size, coverage_identity)`

공식 code뿐 아니라 단위·coverage가 모두 같아야 비교할 수 있습니다. 명칭 유사도와
관측시각은 identity에 포함하지 않습니다.

### RetailPriceSnapshot

- `PriceSeriesKey`
- source 조사일
- source 원본등록일시가 실제 제공될 때 그 값
- 현재 조사 평균 Decimal과 통화 `KRW`
- source row identity 또는 deterministic semantic hash
- artifact·parse run·source contract revision

`0`이 실제 가격인지 sentinel인지 gate에서 확인하기 전에는 유효 Decimal로 받지 않습니다.

### ReferencePrice

- snapshot identity
- period: `WEEK | MONTH | YEAR`
- source 제공 Decimal 또는 `UNAVAILABLE`
- source가 실제 기준일을 제공할 때만 reference date
- 결측·invalid·unsupported reason

source가 `1주 전` 값만 제공하면 조사일에서 7일을 빼서 날짜를 만들지 않습니다.

### PriceChangeFact

- reference price identity
- direction: `LOWER | EQUAL | HIGHER | UNAVAILABLE`
- signed KRW difference
- optional signed percentage
- calculation version과 rounding mode

비율은 기준값이 0보다 크고 두 값이 같은 series·currency·unit일 때만
`(current - reference) / reference × 100`으로 계산해 소수점 첫째 자리 half-up으로
반올림합니다. direction은 산술 결과일 뿐 `저렴`, `비싸`, `추천`의 의미가 없습니다.

### MonthlyRetailPriceSnapshot

정적 월별 file path 전용 typed fact이며 recent comparison model에 넣지 않습니다.

- 공식 file publication identity와 artifact·parse run
- source row의 `year_month`
- `PriceSeriesKey`
- source gate가 증명한 Decimal scale의 monthly mean과 통화 `KRW`
- source row identity 또는 deterministic semantic hash

이 snapshot은 현재 조사 평균, 1주·1개월·1년 reference와 `PriceChangeFact`를 만들지 않습니다.
별도 `STATIC_MONTHLY` publication channel과 route에서 file 공표본·row 기준 연월을 표시합니다.
recent source의 결측 기간을 채우거나 두 model을 한 graph에 섞지 않습니다.

## ReviewDecision

append-only decision type은 `APPROVE | REJECT`입니다.

- reviewer actor, decision timestamp와 reason code
- source configuration, artifact와 parse run
- reconciliation report hash와 acceptance evidence
- 승인된 mode와 coverage
- 이전 결정을 교체할 때의 optional `supersedes_decision`

reviewer는 source fact를 편집해 맞추지 않습니다. 수정은 새 source artifact 또는 새
parser run으로 표현합니다. 이전 decision의 상태를 바꾸지 않고 새 decision으로 교체합니다.

## PublicationRevision

승인된 공개 내용의 불변 revision입니다.

- immutable generation identity, channel과 mode
- 승인 decision, typed fact set hash와 parser revision
- revision 생성시각과 public copy revision
- source 조사일 범위 또는 file 공표본·row 기준 연월 범위

revision은 활성화·대체·철회 때 상태를 바꾸지 않습니다. 하나의 revision에 이전
generation의 빠진 행을 채워 넣지 않습니다. channel은 `RECENT_RETAIL`과
`STATIC_MONTHLY`를 분리합니다.

## PublicationActivation

`ACTIVATE | ROLLBACK | WITHDRAW`

- publication channel과 대상 revision; `WITHDRAW`에는 대상이 없을 수 있음
- actor, reason code, 승인 evidence와 append-only event timestamp
- 직전 current revision과 전환 결과

activation event 추가, channel current pointer와 성공 audit는 한 PostgreSQL transaction에서
전환합니다. rollback은 이전 revision을 수정하거나 상태를 되돌리지 않고 그 revision을
가리키는 새 `ROLLBACK` event를 추가합니다.

## 멱등성과 중복 방지

- request retry: 별도 FetchAttempt
- artifact: `(source identity, ordered page manifest SHA-256)`
- parse: `(artifact, parser revision, configuration hash)`
- snapshot: `(parse run, PriceSeriesKey, source survey date)`
- reference: `(snapshot, period)`
- monthly snapshot: `(parse run, PriceSeriesKey, source year_month)`
- publication: `(channel, approved generation set hash, public copy revision)`
- activation: append-only event identity; 재활성화도 새 event

`fetched_at`, `observed_at`, 실행 ID, attempt ordinal과 database sequence는 semantic key에
넣지 않습니다. 동일 bytes 재확인은 마지막 성공 확인 상태만 갱신하고 source 조사일,
publication revision과 domain fact를 바꾸거나 복제하지 않습니다.

## 시간 모델

- `source_effective_date: LocalDate`: recent source의 조사일
- `source_effective_month: YearMonth`: monthly row의 기준 연월; 임의의 첫날 날짜로 바꾸지 않음
- `source_recorded_at`: source가 실제 제공하는 원본등록일시
- `fetch_started_at`·`fetch_completed_at`: 실제 외부 호출 시각
- `artifact_first_seen_at`: artifact를 처음 만든 성공 attempt 시각
- `last_checked_at`: source configuration의 최근 성공 attempt에서 계산한 확인시각
- `reviewed_at`: 사람 결정 시각
- `revision_created_at`: 불변 publication revision 생성시각
- `activated_at`: 현재 pointer 전환 event 시각

공공데이터포털의 `실시간` 갱신 표시는 매장 실시간 가격이 아닙니다. 어느 시각도 다른
시각의 fallback으로 사용하지 않습니다.

## 전체 대사와 publication gate

각 source 행은 정확히 다음 중 하나여야 합니다.

- `PUBLISHABLE`
- `PUBLISHABLE_WITH_MISSING_REFERENCE`
- `OUT_OF_SCOPE`
- `QUARANTINED`

page total과 네 상태의 합이 일치해야 합니다. 현재값 누락은 항상 `QUARANTINED`이며
reference 누락만 period별 `UNAVAILABLE`을 가진 공개 가능 상태입니다. pagination 누락,
중복 series, code/name conflict, unknown category·grade, unit drift, malformed Decimal,
coverage 부재와 비결정적 replay는 generation 전체 publication을 차단합니다.

첫 승인 generation은 전체 `PriceSeriesKey` 집합과 상태별 count의 대사 기준선입니다.
후속 generation에서 key 소실·추가, identity 차원 변화 또는 상태 이동이 생기면 자동으로
안전하다고 보지 않고 사람 검토를 요구합니다.

비교값이 일부 없는 유효 snapshot은 현재값을 공개할 수 있지만 해당 period를
`비교값 없음`으로 표시합니다. 누락을 0원·변화 없음·비제철로 해석하지 않습니다.

## 개인정보와 retention

- 공개 profile에는 사용자 데이터가 없습니다.
- 검색어, IP, User-Agent, 클릭·관심 이력, 공개 방문자 session과 analytics identifier를
  domain·audit에 저장하지 않습니다. Django Admin 인증 session은 별도 보안 retention과
  최소권한 정책을 따릅니다.
- source key, 전체 query와 gateway trace는 어느 table에도 저장하지 않습니다.
- raw bytes는 권리와 운영 필요가 승인된 경우에만 private storage와 정해진 retention으로
  보존합니다. 아니면 hash·최소 receipt·정규화 사실만 남깁니다.
- audit·publication은 정책상 필요한 기간 append-only로 보존하고, 삭제·축약 정책도
  별도 승인과 증거를 요구합니다.

## 복구와 rollback

- 잘못된 publication은 history를 삭제하거나 revision을 수정하지 않고 `WITHDRAW` 또는 이전
  승인 revision을 대상으로 한 새 `ROLLBACK` activation으로 처리합니다.
- parser rollback과 publication rollback을 분리합니다.
- schema 변경은 expand → compatible write/read → migrate → contract 단계로 나눕니다.
- backup은 암호화하고 PITR 정책을 가지며 production 공개 전 실제 restore를 연습합니다.
- source 권리가 철회되면 raw artifact retention과 공개 publication을 각각 재평가합니다.
