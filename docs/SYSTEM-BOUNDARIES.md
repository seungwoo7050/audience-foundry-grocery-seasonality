# 시스템 경계

## 소유권과 신뢰 경계

- aT와 공공데이터포털은 가격 source, code list, 조사·집계 의미, 인증, quota와
  이용조건을 소유합니다.
- 이 서비스는 source 후보를 수집·검증·공개할 책임을 지지만 원천 사실을 수정해
  꾸며내거나 source authority를 대신하지 않습니다.
- PostgreSQL의 append-only `PublicationActivation`이 현재로 가리키는 승인된
  `PublicationRevision`만 공개 화면의 단일 진실 원천입니다.
- 사용자 browser는 비신뢰 입력 경계이며 공개 결과를 변경할 권한이 없습니다.
- Django Admin은 비공개 운영 경계이고 production에서 일반 사용자 경로와 분리하며
  MFA와 최소권한을 요구합니다.

## 외부 source 경계

### 1. 최근일자 도·소매가격정보 API `15156063`

첫 구현의 유일한 live 가격 후보입니다. 공공데이터포털 메타데이터는 REST JSON/XML,
무료, 이용허락 제한 없음, 개발·운영 자동승인과 개발계정 10,000회를 안내합니다.
그 설명은 조사일과 1일·1주·1개월·1년 전 평균 가격 field를 열거합니다. 이 문서
checkpoint에서는 live key, 응답, production quota와 재배포 동작을 확보했다고
주장하지 않습니다.

gate는 다음 경계를 독립적으로 검증합니다.

- key 발급·입력과 약관 판단: 사람 전용
- HTTPS endpoint, method, query allowlist, timeout, redirect와 TLS
- 실제 JSON media type·encoding·error envelope와 HTTP 200 내부 오류
- pagination, total count, duplicate, quota·429와 재시도 조건
- 소매·채소·과일 code, 품목·품종·등급·단위 identity와 조사범위
- 현재·과거 값의 동일 series, 결측·0·sentinel·휴일·정정 의미
- raw body 저장, 정규화·파생값 공개와 출처표시 권리

공개 request path에서는 이 API를 호출하지 않습니다.

### 2. 월별 소매가격 파일 `15087482`

live API가 운영에 부적합할 때만 평가하는 공식 정적 fallback입니다. CSV의 공표본,
row별 기준 연월, 도시·시장, 품목·품종·등급, 단위, 행 identity, 갱신주기와 권리를 별도
gate로 검증합니다. file path가 통과해도 이를 현재가격, 실시간 또는 live API의 빈 구간을
채우는 자료로 사용하지 않습니다. 별도 typed monthly model, publication channel, route와
rollback을 사용합니다.

### 3. 후속 API `15156060`과 `15156065`

연월별·기간별 소매가격은 다년 월별 패턴을 위한 비활성 source 후보입니다. 첫
MVP schema, ingestion schedule, 공개 UI와 acceptance에 포함하지 않습니다. 별도
gate와 제품 결정 없이 이 데이터를 가져오거나 기존 profile에 섞지 않습니다.

### 제외된 외부 경계

- KAMIS HTML, 게시물·첨부 이미지와 로고 scraping
- 검색엔진 cache, 비공식 mirror, 뉴스·블로그 가격
- 쇼핑몰·마트·전통시장 크롤링과 제휴 feed
- nutrition, recipe, weather, 생산량, 도매시장과 다른 price source
- CAPTCHA, key, quota, robots 또는 이용조건 우회

## 내부 경계

첫 시스템은 하나의 Django 배포 단위와 하나의 PostgreSQL database인 modular monolith입니다.

### ingestion 경계

- platform cron이 제한된 management command를 실행합니다.
- command는 source allowlist, timeout, pagination·row 상한과 제한 재시도를 사용합니다.
- 전체 pagination을 완성하려는 논리적 획득마다 `FetchAttempt`를 만들고, 순서가 있는
  page receipt에서 key·query secret을 제거합니다. 논리적 재시도는 새 attempt입니다.
- raw bytes는 승인된 권리와 retention이 있을 때만 private artifact storage에 둡니다.
- 모든 page와 row의 대사가 끝나기 전에는 `SourceArtifact`를 만들지 않습니다.

### parsing·reconciliation 경계

- parser는 exact source/interface revision에 연결됩니다.
- 입력 artifact를 수정하지 않고 typed candidate와 deterministic result hash를 만듭니다.
- 모든 source 행은 공개 가능, reference가 누락된 공개 가능, 범위 밖 또는 quarantine으로
  대사합니다. 현재값 누락은 공개 가능 상태가 아닙니다.
- 코드·이름 충돌, malformed Decimal, 중복 series, 단위 drift와 pagination 누락은
  publication을 차단합니다. 첫 승인 generation은 전체 key set과 상태별 count의 대사
  기준선이 되며 후속 key 소실·추가·차원 변경은 사람 검토를 요구합니다.

### review·publication 경계

- worker는 candidate를 만들 수 있지만 승인·공개하지 못합니다.
- reviewer는 evidence와 reconciliation report로 generation 전체를 승인·거부합니다.
- publisher만 승인 generation의 immutable publication을 대상으로 append-only
  `ACTIVATE | ROLLBACK | WITHDRAW` 사건과 current pointer를 한 transaction으로 전환합니다.
- source·parser rollback과 publication activation은 각각 별도 사건과 audit를 가집니다.

### public read 경계

- Django form은 부류와 공식 품목명 검색만 받습니다.
- 검색 길이, 문자와 result 수를 제한하며 검색어를 공개 방문자 session, cache, analytics,
  log와 audit에 남기지 않습니다. Django Admin의 보안 인증 session은 별도 운영 정책을
  따릅니다.
- 목록·상세는 published read model만 조회하고 외부 source, 운영 candidate와 raw
  artifact에 접근하지 않습니다.
- 공개 URL에는 stable internal slug만 사용하며 source key나 secret query를 넣지 않습니다.

## 데이터 흐름

```text
platform cron
  -> Django management command
  -> KAMIS HTTPS source
  -> FetchAttempt + redacted receipt
  -> content-addressed SourceArtifact
  -> versioned ParseRun + reconciliation
  -> typed recent retail facts or typed monthly retail snapshots
  -> ReviewDecision
  -> immutable PublicationRevision
  -> append-only PublicationActivation + atomic channel pointer
  -> Django server-rendered list/detail
```

한 단계의 성공을 다음 단계의 성공으로 간주하지 않습니다. HTTP 200은 artifact 승인,
parse 성공, reviewer 승인 또는 publication 성공을 뜻하지 않습니다.

## 실패 경계

- timeout, 429와 일시적 5xx만 bounded retry 대상입니다.
- auth·rights 실패, unsupported schema, identity·unit·coverage 충돌은 terminal 또는
  review-required 상태입니다.
- 부분 page, 오래된 page와 새 page, 이전 generation의 결측 행을 섞지 않습니다.
- 새 generation 실패는 activation을 만들거나 current pointer를 이동하지 않고
  last-known-good와 stale 사유를 유지합니다.
- source 신뢰 또는 공개 권리가 철회되면 오래된 publication을 안전하다고 가정하지
  않고 `no current publication`으로 철회할 수 있습니다.

## 개인정보 allowlist

공개 가능 field는 공식 부류·품목·품종·등급 code와 표시명, 원문 단위·크기,
검증된 조사범위, 조사일, 현재·1주·1개월·1년 제공값, 결정적 차이와 방향, source URL,
publication, source 조사일과 마지막 확인 상태입니다.

다음은 저장·로그·공개하지 않습니다.

- API key, secret, 전체 query string과 gateway trace
- 방문자의 IP, User-Agent, 검색어, 클릭 이력과 관심 profile
- 운영자 email·이름 등 공개에 필요 없는 identity
- source response의 allowlist 밖 field
- 광고·remarketing identifier와 정밀 위치

보안 운영에 필요한 request log도 URL query를 제거하고 짧은 정책 retention을 사용합니다.

## 운영 경계

production 노출 전 HTTPS, secure settings, 관리자 MFA, managed secret injection,
구조화 로그, health·readiness·source freshness 경보, 자동 PostgreSQL backup과 실제 restore
rehearsal을 통과해야 합니다. source credential, database credential, artifact storage와
publisher 권한은 서로 분리하고 최소권한을 사용합니다.

## Legacy와 portability

기존 repository, 경쟁 서비스 DB, 수집 이력과 다른 Audience Foundry artifact를 이
서비스의 관측 이력으로 가져오지 않습니다. 검증된 오픈소스 dependency는 exact version,
license, integrity와 purpose를 승인한 격리 commit에서만 도입합니다. 범용 플랫폼을
만들기보다 이 도메인의 typed model과 Django 기본 기능을 우선합니다.
