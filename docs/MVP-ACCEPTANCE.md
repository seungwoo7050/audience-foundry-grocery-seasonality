# MVP 인수 기준

## 판정 원칙

이 저장소의 두 번째 커밋은 제품 계약 완료 지점이지 구현 완료가 아니다. MVP는 실제 공식
source, 승인된 권리 판단, 고정 artifact와 운영과 같은 build에서 아래 필수 항목을 증명해야
완료된다. 필수 항목 하나라도 실패하면 해당 path를 공개하지 않고 문서의 current-only,
정적 월별 file 또는 stop 조건으로 이동한다.

## 게이트 0: Git 기준선

첫 파일 변경 전에 구현 세션은 다음을 검증한다.

- [ ] 현재 경로가 `audience-foundry-grocery-seasonality`이고 branch는 `main`이다.
- [ ] 이 문서 계약 commit의 부모가 공통 정책 기준선 `0cc95e70824e02a78207fe983f076e38a59c764f`이다.
- [ ] 추적 파일은 README와 공통 정책 4개, 제품 문서 6개로 정확히 11개다.
- [ ] remote가 없고 working tree가 깨끗하며 `git fsck`가 통과한다.
- [ ] 런타임 코드, dependency, lockfile, credential, 수집 데이터와 배포 설정이 아직 없다.

기준이 다르면 구현을 시작하지 않고 차이를 사람에게 보고한다.

## 게이트 1: live source·권리 생존성

source-dependent schema나 adapter를 만들기 전에 저장소 소유자가 다음 실제 증거를 승인한다.

- [ ] 공식 소유자와 공공데이터포털의
  [최근일자 도·소매가격정보 API `15156063`](https://www.data.go.kr/data/15156063/openapi.do)
  landing URL, API 문서 버전과 운영 host가 기록되어 있다.
- [ ] 사람이 발급받아 secret으로 주입한 key로 공식 HTTPS endpoint의 실제 응답을 재현한다.
- [ ] 개발·운영 quota, 호출 단위, pagination, timeout, `429`, provider error와 이용 가능 시간이
  실제 요청과 공식 문서로 확인된다.
- [ ] 원시 응답 보존, 내부 변환, 파생 가격 사실 공개, cache와 출처 표시의 허용 범위를 각각
  판정한다.
- [ ] JSON 또는 XML의 실제 field, type, encoding, missing·sentinel, 중복과 error envelope를
  기록한다.
- [ ] 소매·채소·과일 범위에서 category, item, variety, grade, unit와 unit size code를
  안정적으로 식별할 수 있다. region·market field가 있으면 그 code를 검증하고, 없으면
  제공자 문서와 실제 응답으로 aggregate 범위와 안정적인 `coverage_identity`를 증명한다.
- [ ] 현재 조사 평균과 1주·1개월·1년 전 평균의 비교기간 의미, coverage, 단위와 산출
  의미가 비교 가능한 같은 series로 확인된다. 정확한 reference date는 source가 제공할 때
  검증하고, 제공하지 않으면 `SOURCE_REFERENCE_DATE_UNAVAILABLE`을 보존한다.
- [ ] 휴일·비조사일, 늦은 갱신, 부분 응답과 과거 날짜 요청에서 source가 반환하는 기준일
  의미를 확인한다.
- [ ] 동일 요청 반복의 stable field와 변동 field, content hash와 idempotency 규칙을
  재현한다.
- [ ] 최소 채소 5개·과일 5개의 서로 다른 exact series를 포함한 bounded canary matrix로
  code, 단위, 결측과 반복 조회를 검증한다. source가 이 수를 제공하지 않거나 검증된 호출
  예산 안에서 재현할 수 없으면 범위를 임의로 채우지 않고 path 선택을 다시 승인한다.
- [ ] 허용된 호출 예산으로 정기 수집, 대사, retry와 운영자 확인을 지속할 수 있다.
- [ ] 전체 pagination을 완성하는 한 논리적 획득을 `FetchAttempt` 하나로 기록하고 각 page의
  순서·row count·body hash를 대사한다. 논리적 재시도는 새 attempt이며 서로 다른 attempt의
  page를 한 artifact로 합치지 않는다.

key 발급·로그인·약관 동의·유료 전환이 필요하면 자동화하지 않고 사람에게 멈춘다. live
evidence 실패를 fixture, 비공식 mirror, HTML scraping 또는 quota 우회로 대체하지 않는다.

## 활성 path 판정

### A. 최근 비교 path

게이트 1 전체를 통과하면 현재 KAMIS 소매 조사 평균과 같은 series의 1주·1개월·1년 전
reference를 공개한다.

### B. current-only path

현재값의 identity·단위·권리·운영성은 통과했지만 reference 기간의 의미나 같은 series임을
증명하지 못하면 현재 조사 평균만 공개한다. 차액, 퍼센트와 방향 문구는 렌더링하지 않는다.

### C. 정적 월별 file path

API 운영이 불가능할 때만
[월별 소매가격 파일 `15087482`](https://www.data.go.kr/data/15087482/fileData.do)를 별도 gate로
검증한다. 공식성, 재배포 권리, file 공표본 identity, row별 기준 연월, code identity,
unit와 coverage가 통과하면 공표본과 각 row의 기준 연월을 명시한 정적 탐색 profile만
공개한다. 실제 공식 file이 `FetchAttempt →
SourceArtifact → ParseRun → MonthlyRetailPriceSnapshot → ReviewDecision →
PublicationRevision → PublicationActivation`을 통과해야 한다. 별도
`STATIC_MONTHLY` publication channel·route·copy·rollback을 사용하고 recent comparison
snapshot과 섞지 않는다. 이를 현재 가격이나 실시간 정보로 표현하지 않는다.

### D. stop

어느 path도 source 공식성, 권리, identity, 단위, coverage와 반복 가능한 운영을 증명하지
못하면 공개 출시와 source-dependent 구현을 중단한다. 실패 이유와 증거만 남긴다.

## 필수 양성 시나리오

### 수집·감사·공개

- [ ] 한 실제 공식 응답이 `FetchAttempt → SourceArtifact → ParseRun → typed
  RetailPriceSnapshot/ReferencePrice/PriceChangeFact → ReviewDecision → PublicationRevision`
  전 단계를 거쳐 사람 승인 후 activation으로 공개된다.
- [ ] 각 단계에서 source, 이전 단계, 실행자 또는 process, 시각, code·parser version, hash와
  상태를 역추적할 수 있다.
- [ ] 동일 ordered page manifest를 다시 획득하면 새 `FetchAttempt`는 생기지만
  `SourceArtifact`는 중복되지 않는다.
- [ ] 같은 content의 재확인은 source의 마지막 성공 확인 상태만 갱신하고 artifact,
  source 조사일과 공개 데이터 freshness를 바꾸지 않는다.
- [ ] 동일 artifact와 parser version 재실행은 같은 typed row 집합 hash를 만들며 snapshot을
  중복하지 않는다.
- [ ] `fetched_at`만 다른 재수집은 새 content나 새 publication의 근거가 되지 않는다.
- [ ] parser version이 바뀌면 새 `ParseRun`과 review candidate가 생기고 승인 전에는 공개되지
  않는다.
- [ ] 승인 revision의 row count, code별 count, coverage, missing·quarantine count와 집합
  hash가 대사 보고서와 일치한다.
- [ ] 새 source 실패 중에도 last-known-good가 유지되고 사용자에게 그 기준일과 검토일을
  표시한다.

### 사용자 읽기

- [ ] 소매·채소 또는 과일 목록에서 공식 품목명으로 검색하고 한 exact series 상세로 이동할
  수 있다.
- [ ] 상세 화면은 item·variety·grade·unit·unit size와 `market` 또는 검증된 aggregate
  coverage를 source가 제공한 범위 안에서 명확히 표시한다.
- [ ] 현재값 8,000원과 1주 기준값 10,000원이면 `2,000원 낮음`, `-20.0%`를 표시한다.
- [ ] 현재값 12,500원과 1개월 기준값 10,000원이면 `2,500원 높음`, `+25.0%`를 표시한다.
- [ ] 현재값과 1년 기준값이 같으면 `같음`, `0.0%`를 표시한다.
- [ ] 기준값이 `0`이거나 없으면 차액·퍼센트를 계산하지 않고 `비교 정보 없음`을 표시한다.
- [ ] 가격 가까이에 `KAMIS 소매 조사 평균`, source 조사일, coverage, 단위와 publication
  검토일을 표시한다. reference별 실제 날짜가 있으면 그 날짜를 표시하고 없으면
  `source가 비교 기준일을 별도로 제공하지 않음`을 표시한다.
- [ ] 방향은 중립적 사실로만 표현하며 구매·품질·영양·제철·미래 가격 판단을 덧붙이지
  않는다.

## 필수 음성 시나리오

- [ ] 소매와 도매, 채소와 과일, 서로 다른 item·variety·grade·unit·unit size의 값은 비교하지
  않는다. source가 region·market을 제공하면 그 code가 다른 값도, 제공하지 않으면 검증된
  aggregate coverage가 다른 값도 비교하지 않는다.
- [ ] 이름이 비슷하다는 이유로 다른 code를 자동 결합하지 않는다. code가 사라지거나 바뀌면
  새 review 없이는 이전 series에 연결하지 않는다.
- [ ] 임의 kg 환산, 지역 간 평균 합성, 시장 최저가, 쇼핑몰 가격, 할인과 배송비를 만들지
  않는다.
- [ ] `null`, 빈 문자열, sentinel, 음수, 잘못된 decimal, 알 수 없는 단위와 중복 identity는
  `0원`으로 보정하지 않고 격리한다.
- [ ] 기준값이 `0`일 때 division을 수행하지 않는다. float rounding을 사용하지 않는다.
- [ ] source가 주지 않은 effective date나 recorded time을 fetch·publish time으로 대신하지
  않는다.
- [ ] HTTP timeout, `429`, 일시적 `5xx`, TLS 오류, 허용되지 않은 redirect, 응답 크기 초과,
  content type 변경과 provider error는 성공으로 기록하지 않는다.
- [ ] field 제거·추가, type·encoding·unit 변경과 duplicate는 publication을 차단한다. 첫 승인
  generation의 전체 key set·상태별 count와 비교한 후속 key 소실·추가·차원 변경은 사람
  검토 전까지 last-known-good를 유지한다.
- [ ] 일부 row만 parse되거나 publication transaction이 실패하면 current pointer를 바꾸지
  않고 전부 rollback한다.
- [ ] 늦게 도착한 과거 기준일 응답은 명시적 review 없이 최신 승인 revision을 대체하지
  않는다.
- [ ] current-only path에서 reference 값, 차액, 퍼센트와 방향 문구를 노출하지 않는다.
- [ ] 정적 월별 file path를 현재값, 실시간, 최저가, 예측 또는 개인 추천으로 표현하지 않는다.
- [ ] 개인정보가 포함된 unexpected field는 domain snapshot과 public output에 복사하지
  않는다.
- [ ] 인증되지 않았거나 권한이 부족한 사용자는 Admin, source 설정, review와 publication에
  접근하지 못한다.
- [ ] API key, 전체 query string, raw body, 검색어와 운영자 개인정보가 Git, log, error,
  analytics와 public response에 나타나지 않는다.

## 개인정보·license 인수 기준

- [ ] public 사용에는 계정, 주소, 위치, 장바구니와 구매 이력이 필요하지 않다.
- [ ] 검색어는 DB, 공개 방문자 session, cache, analytics와 application log에 저장하지
  않는다. Admin 인증 session은 별도 보안 정책을 따른다.
- [ ] source·domain·public allowlist에 없는 field는 저장·공개하지 않는다.
- [ ] raw bytes 보존 권리가 없으면 body 저장은 실패 폐쇄되고 SHA-256, byte length, 최소
  receipt와 정규화 사실만 남는다.
- [ ] source 이름, landing URL, recent 조사일 또는 monthly row 기준 연월, coverage, 단위,
  변환 설명과 검토일을 공개 화면에 표시한다.
- [ ] dependency와 정적 자산의 license·notice 의무가 `THIRD_PARTY_NOTICES.md`와 실제 배포에
  반영된다.

## 월별 과거 패턴 module gate

내부 repository 이름만으로 이 module을 활성화하지 않는다. 별도 제품 결정을 승인하기 전에
다음을 모두 증명한다.

- [ ] 공식 source가 최소 3개 완전 연도의 월별 retail rows를 제공하고 공개·보존 권리가 있다.
- [ ] 기간 전체에서 item·variety·grade·unit code와 coverage identity가 안정적이고,
  region·market code가 제공되면 그 의미도 안정적이거나 사람이 승인한 명시적 migration
  map이 있다.
- [ ] 결측 월, 조사 빈도, 시장 구성 변화와 명목가격의 한계를 사용자에게 표시할 수 있다.
- [ ] 한두 달의 값이나 단순 최저 월을 농산물의 자연적 제철·품질·가용성으로 해석하지 않는다.
- [ ] 독립된 source configuration, parse version, review와 publication rollback이 있다.

통과해도 첫 public 명칭은 `월별 과거 가격 패턴`이다. 계절성 추정, 구매 추천과 forecast는
새 근거와 새 제품 결정 없이는 범위 밖이다.

## Phase 0 배포 직전 production candidate 인수 기준

이 gate는 실제 배포 전 local candidate의 완료 조건이다. 통과는 `Phase 0 배포 직전 완료`를
뜻하며 production platform·database·credential·domain이 정해졌거나 실제 배포가 끝났다는
뜻이 아니다. 네이티브 모바일 앱, 앱스토어 배포와 별도 SPA는 범위 밖이다.

- [ ] 문서가 허용한 source path로 실제 generation 하나가 수집·검수·publication되어 핵심
  사용자 폐쇄 루프가 last-known-good revision만 읽는다.
- [ ] Django server-rendered responsive web 하나가 desktop과 mobile을 함께 지원한다.
- [ ] 실제 배포에서 사용할 migration, release SHA, deploy·application rollback·publication
  rollback 명령이 runbook과 clean Git 상태로 재현된다.
- [ ] 실제 배포에 필요한 platform, PostgreSQL, secret injection, domain·DNS와 운영자 계정은
  사람 전용 잔여 작업으로 분리되어 있다.

- [ ] 깨끗한 잠금 설치에서 Python `3.14.7`, Django `5.2.17`, PostgreSQL `18.6`, uv
  `0.12.6`이 실제 실행 버전으로 확인된다.
- [ ] formatter, lint, type, unit, integration, parser replay, negative route와 concurrent
  publication test가 통과한다.
- [ ] 새 빈 DB와 운영 복제 DB에서 migration, `django check`와 `django check --deploy`가
  통과한다.
- [ ] 운영은 HTTPS, `DEBUG=False`, 정확한 host·CSRF 설정, HSTS와 secure cookie를 사용한다.
- [ ] Admin은 운영자별 최소 권한, MFA 또는 동등한 강한 인증과 로그인 제한을 사용한다.
- [ ] local production-like 설정에서 env secret injection contract·rotation 절차, structured
  log, liveness·readiness와 source freshness alert 판단이 동작한다. 실제 production secret
  주입은 배포 checkpoint에 남긴다.
- [ ] 연속 fetch 실패, quarantine 증가, 대사 불일치, publication·backup 실패가 운영자에게
  경보된다.
- [ ] disposable PostgreSQL의 실제 `pg_dump`/`pg_restore`가 빈 환경에서 승인 revision,
  audit chain, row count·hash·current pointer를 복원한다. production의 매일 암호화 backup,
  point-in-time recovery, `RPO 24시간`·`RTO 4시간`은 platform 선택 뒤 별도 확인한다.
- [ ] 이전 application과 이전 승인 publication으로 각각 rollback하는 훈련이 성공한다.
- [ ] secret, dependency vulnerability와 license 검사에 해결되지 않은 차단 항목이 없다.

## responsive browser·성능·접근성 인수 기준

실제 browser와 end-to-end test로 `360x800`, `390x844`, `768x1024`, `1440x900`을 각각
검수하고 viewport별 screenshot을 completion evidence에 연결한다.

- [ ] 어느 viewport에도 document 가로 scroll이 없다.
- [ ] typography와 heading·metadata 계층이 읽기 쉽고 interactive touch target이 충분하다.
- [ ] mobile에서 검색 form 입력·제출·validation error 확인·수정이 실제 동작한다.
- [ ] 긴 한국어 품목·품종·등급·단위·출처·freshness가 잘리거나 겹치지 않는다.
- [ ] loading, empty, unavailable, stale, validation과 server error 상태가 결정적으로
  재현되고 screenshot·test로 검증된다.
- [ ] keyboard-only navigation 순서와 visible focus가 동작한다.
- [ ] semantic landmark·heading·form label·error association과 screen reader accessible name이
  자동 검사 및 browser 검수에서 유효하다.
- [ ] success·warning·error·direction을 색상만으로 전달하지 않는다.

승인된 catalog 크기로 운영과 같은 환경에서 15분 동안 평균 10 requests/s, 동시 사용자 20명,
목록·검색 70%와 상세 30%의 read-only 부하를 건다. 응답 p95는 500 ms 이하, `5xx`는 0.5%
미만이며 DB 연결 고갈과 revision 혼합이 없어야 한다. 이 profile을 넘는 실제 수요가 측정될
때만 별도 cache를 검토한다.

핵심 page와 form은 keyboard만으로 사용할 수 있고 visible focus, 한국어 label, 오류 요약,
logical heading, 충분한 contrast와 screen reader로 읽히는 방향 문구를 제공해야 한다.

## 사람 승인과 완료 증거

저장소 소유자는 key 발급·이용조건, live source gate, path 선택, 첫 publication, 배포와
rollback을 각각 승인한다. 구현 이후 만드는 `docs/COMPLETION-REPORT.md`에는 다음 증거를
연결한다.

- source 문서·요청 시각, redacted receipt, 권리 판정과 code·unit·기간 의미
- artifact hash, parser·schema·application version, 대사와 test 결과
- 선택한 A·B·C path 또는 D stop 사유와 비활성 module 목록
- 보안·license 검사, 접근성·성능 결과, backup restore와 rollback 결과
- 현재 승인 `PublicationRevision`, 최근 `PublicationActivation`, 알려진 비목표와 다음
  review 날짜
- 네 viewport의 실제 browser screenshot·E2E 결과와 발견·수정한 UI/UX 결함
- exact release SHA, clean status·`git fsck`, deploy·rollback 명령과 production
  platform·database·secret·domain의 사람 전용 잔여 작업

local candidate의 모든 필수 항목이 실제 증거에 연결되면 `Phase 0 배포 직전 완료`로 판정한다.
실제 배포와 production 전용 항목은 해당 사람 checkpoint 이후에만 Phase 0 완료 여부를 별도로
판정한다.
