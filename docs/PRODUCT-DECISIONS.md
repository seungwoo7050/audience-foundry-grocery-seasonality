# 고정 제품 결정

이 문서는 첫 구현 세션의 변경 승인 없이는 바뀌지 않는 계약입니다. 변경은 제품
소유자의 명시적 승인과 이유·evidence·영향·migration·rollback을 담은 작은 전용
문서 commit으로만 수행합니다.

## 저장소와 공개 상태

| 항목 | 결정 |
|---|---|
| 저장소 | `audience-foundry-grocery-seasonality` |
| 정책 기준선 | `0cc95e7` |
| 기본 브랜치 | `main` |
| remote | 없음 |
| 공개 상태 | 로컬·미공개 |
| 구현 상태 | source gate와 구현계획 승인 전에는 구현하지 않음 |

legacy 코드, 경쟁 서비스 이력, 비공식 가격 dump, KAMIS HTML과 다른 Audience Foundry
제품의 구현을 가져오지 않습니다. 재사용은 exact source·license·revision·검증·rollback을
별도 결정으로 승인한 뒤에만 가능합니다.

## 변경할 수 없는 MVP 결정

1. 첫 live comparison source는 공공데이터포털의 KAMIS 최근일자 도·소매가격정보 API
   `15156063` 하나입니다. 정적 월별 file은 A·B path를 운영할 수 없을 때만 별도
   source configuration과 제품 profile로 평가하는 fallback입니다.
2. 실제 HTTPS 응답의 접근·권리·schema·identity·평균·조사범위·비교기간·단위·결측을
   증명하기 전에 source 종속 schema나 adapter를 구현하지 않습니다.
3. 공개 대상은 source가 `소매`로 식별한 채소류·과일류뿐입니다.
4. 공개 profile은 공식 품목·품종·등급·판매 단위·단위크기·검증된 조사범위가 모두
   같은 하나의 typed series입니다.
5. 공개 비교 기준은 source가 제공한 1주, 1개월, 1년입니다. 1일 값은 저장·공개하지
   않습니다.
6. 화면은 조사일 평균, 비교값, 원화 차이, 변화 방향과 조건부 비율만 표시합니다.
7. 비율은 같은 series의 유효한 기준값이 0보다 클 때만 Decimal로 계산하고 소수점
   첫째 자리에서 half-up 반올림합니다.
8. source가 정확한 과거 기준일을 제공하지 않으면 조사일에서 날짜를 역산하지 않고
   `source reference date unavailable` 상태를 보존합니다.
9. 공식 kg 환산값은 의미와 변환 계약을 별도 승인하기 전에는 domain snapshot과
   공개 화면에 넣지 않습니다. 개·포기·단·봉과 kg를 자체 변환하지 않습니다.
10. 결측, 빈 문자열, `0`, `-`, sentinel과 malformed 값은 의미를 실제로 검증하기
    전에는 가격이나 변동 없음으로 바꾸지 않습니다.
11. 공식 이름은 표시·검색 보조이며 identity가 아닙니다. 이름 유사도로 코드·등급·단위가
    다른 행을 병합하지 않습니다.
12. `seasonality`는 저장소 코드명입니다. 다년 월별 source를 별도 gate와 결정으로
    승인하기 전에는 제철·평년·계절성 공개 문구와 module을 활성화하지 않습니다.
13. 공개 request는 외부 source를 호출하지 않고 PostgreSQL의 승인된 publication만
    읽습니다.
14. source 실패나 불완전 generation은 마지막 정상 publication을 바꾸지 않습니다.
15. 사용자 계정·검색 이력·위치·개인화·장바구니·알림·광고 audience를 만들지 않습니다.
16. 범용 EAV, 범용 상품 모델과 공용 자체 ingestion framework를 만들지 않습니다.
17. measured bottleneck과 별도 결정 없이 Node, SPA, Redis, Celery, queue, search engine,
    analytics store, spatial extension 또는 microservice를 도입하지 않습니다.

## 공개 표현 계약

허용되는 표현은 다음 형태입니다.

- `KAMIS 소매 조사 평균`
- `1주 전 제공값보다 2,000원 낮음 (-20.0%)`
- `비교값 없음`
- `조사범위 확인 필요`인 candidate는 공개하지 않음
- source가 보장한 정확한 조사일·단위·품종·등급·coverage

다음 표현은 첫 MVP에서 금지합니다.

- `제철`, `평년보다`, `저렴하다`, `비싸다`, `가성비`, `사세요`, `추천`
- `실시간 가격`, `전국 평균`, `마트 가격`, `최저가`, `시장 최저`
- `곧 오른다`, `곧 내린다`, 미래가격·절약액·구매 시점
- 행 소실에 대한 `품절`, `판매 종료`, `철 종료`, `비제철`

내부 enum `LOWER`와 `HIGHER`는 동일 제공값 간 산술 방향일 뿐 가치 판단이 아닙니다.

## 행위자와 권한

- **방문자**: 공개된 HTML 목록·상세를 읽습니다. canonical state를 변경하지 않습니다.
- **ingestion worker**: 승인된 read-only source를 호출해 attempt·artifact·candidate를
  만들 수 있지만 검토와 publication을 수행하지 못합니다.
- **reviewer**: reconciliation, 결측·단위·identity·coverage report를 보고 generation을
  승인하거나 거부합니다.
- **publisher**: 승인된 generation만 publication으로 전환하거나 이전 revision으로
  rollback합니다.
- **제품 소유자**: source 권리, 첫 live contract, pivot, production 배포와 고정 결정
  변경을 승인합니다.
- **aT·공공데이터포털**: source, 인증, quota, schema와 이용조건을 소유하는 외부
  경계입니다.

production reviewer·publisher·administrator는 최소권한과 MFA를 사용합니다. key 발급·입력,
약관 판단, raw 보존 승인, 첫 source 활성화, 파괴적 migration, production 배포·rollback은
사람 전용 checkpoint입니다.

## 공통 수집·공개 lifecycle

```text
FetchAttempt → content-addressed SourceArtifact → versioned ParseRun
  → (RetailPriceSnapshot + ReferencePrice + PriceChangeFact
     | MonthlyRetailPriceSnapshot)
  → ReviewDecision → PublicationRevision → PublicationActivation
```

- 전체 pagination을 완성하려는 논리적 획득과 그 재시도마다 별도 `FetchAttempt`를
  남기고, 각 HTTP page는 순서가 있는 redacted receipt로 기록합니다.
- `SourceArtifact`만 `(source identity, ordered page manifest SHA-256)`로 중복 제거합니다.
- `fetched_at`·`observed_at`·실행 UUID는 semantic identity에 넣지 않습니다.
- 한 publication은 하나의 완전하고 승인된 generation만 가리킵니다.
- 승인 decision은 불변 revision을 만들고 append-only activation 사건과 공개 pointer 전환은
  한 PostgreSQL transaction입니다.
- 같은 bytes의 재획득은 마지막 성공 확인 상태만 갱신하고 artifact·snapshot을 중복하지
  않습니다. source 조사일이나 공개 데이터 freshness를 새 값으로 바꾸지 않습니다.
- 조사일, source 원본등록일시가 있을 때의 그 시각, 실제 fetch 시각과 공개시각을
  분리합니다.

정적 월별 fallback은 `MonthlyRetailPriceSnapshot`을 사용하며 최근 비교 snapshot에 섞지
않습니다. source 수명주기는 같지만 별도 publication channel·route·copy와 rollback을
가집니다.

## 필수 live source gate

key가 필요한 단계에서 사람에게 멈추고, 파일을 변경하지 않은 상태에서 최소 실제
요청으로 다음을 증명합니다.

1. aT 소유권, 공공데이터포털 랜딩과 HTTPS 배포 URL, interface revision
2. 무료 여부, 저장·변환·파생값 공개·재배포 권리와 출처표시 의무
3. key 전달 방식, 개발·운영 quota, timeout, pagination과 오류 envelope
4. JSON content type·encoding, page total, 중복, 빈값, sentinel과 숫자 형식
5. 도·소매, 부류, 품목, 품종, 등급, 단위와 단위크기의 공식 코드·의미
6. 조사일 평균의 표본, 시장·공간범위, 집계 의미와 휴일 처리
7. 1주·1개월·1년 전 값이 현재와 같은 series라는 제공자 계약과 기간 의미
8. 반복 요청에서 안정적인 series identity, 행 수와 code/name 일치
9. 채소·과일에 공개 가능한 유효 profile이 각각 하나 이상 존재함
10. HTTP 200 내부 오류, 429, timeout, TLS·redirect·schema drift의 실패 동작

메타데이터, 합성 fixture, 비공식 예시와 실패한 live evidence는 이 gate를 통과시키지
못합니다. key와 실제 query string은 prompt, command history 출력, URL, log, receipt,
fixture와 문서에 남기지 않습니다.

## source 활성화와 pivot 순서

### A. 최근 비교 path

`15156063`의 live gate가 모두 통과하면 1주·1개월·1년 비교를 활성화합니다.

### B. 현재값-only path

권리·identity·현재 평균은 통과하지만 비교기간 의미 또는 동일 series 보장이 실패하면
비교값·차이·방향을 모두 제거하고 현재 공식 소매 조사 평균 조회로 축소합니다.

### C. 정적 월별 file path

API 자동 운영이 불가능하고 공식 월별 소매가격 파일 `15087482`의 접근·권리·schema·
공표본 identity·row별 기준 연월·series identity·단위가 통과하면 운영자 승인형 정적 월별
탐색기로 축소할 수 있습니다. 이 path는 별도 `MonthlyRetailPriceSnapshot`, publication
channel과 route를 사용합니다. 화면은 파일 공표본, 각 row의 기준 연월과 조사범위를
명시하고 현재가·freshness·알림을 주장하지 않습니다.

### D. stop

조사범위, 평균 의미, identity, 등급·단위, 재공개 권리와 안전한 acquisition 중 하나라도
증명하지 못하면 가격 공개와 dependent code를 중단합니다. HTML scraping, 비공식 미러,
CAPTCHA·quota 우회와 다른 가격 source의 자동 보충은 pivot이 아닙니다.

## 후속 월별 패턴 module

연월별 API `15156060`과 기간별 소매가격 API `15156065`는 첫 MVP source가 아닙니다.
동일 series의 최소 3개 완전한 연도, 코드·등급·단위·coverage 연속성, 정정·결측 의미,
권리와 재현 가능한 월별 집계를 별도 gate로 증명한 뒤 전용 제품 결정 commit에서만
활성화합니다. 통과해도 첫 표현은 `월별 과거 가격 패턴`이며 제철·품질·추천·예측이
아닙니다.

## 변경 승인

고정 결정 변경에는 source evidence, 사용자 문구, data compatibility, privacy·license,
schema·migration, acceptance와 rollback 영향을 기록합니다. 두 실제 vertical에서
안정된 중복이 확인되기 전에는 공용 ingestion package를 추출하지 않습니다.
