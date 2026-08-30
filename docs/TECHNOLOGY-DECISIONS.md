# 기술 결정

## 문서 상태

이 문서는 첫 구현이 따라야 할 기술 기준선이다. 현재 저장소에는 런타임 코드, 의존성,
잠금 파일, 수집 데이터와 배포 구성이 없다. 아래 버전과 구조는 source gate를 통과한 뒤
구현 계획에서 실제 호환성을 다시 증명하고 도입한다.

## 고정 기준 스택

| 구성 요소 | 기준 버전 | 용도 |
|---|---:|---|
| Python | `3.14.7` | 애플리케이션, 수집, 파싱 런타임 |
| Django | `5.2.17` | Templates, Forms, Admin, Auth, ORM, 관리 명령 |
| PostgreSQL | `18.6` | 출처, 감사, typed 가격 사실, 승인 공개 리비전 |
| uv | `0.12.6` | Python 버전, 의존성, 잠금, 명령 실행 |

구현 시작 시 공식 배포물, 배포 플랫폼 지원, 보안 공지와 패키지 호환성을 확인한다.
하나라도 재현할 수 없으면 조용히 다른 버전을 선택하지 않고 근거와 migration·rollback
영향을 기술 결정으로 승인받는다. 도입한 직접·전이 의존성은 해시가 포함된 잠금 파일로
고정한다.

## 개발 원칙 적용

- **Open Source and Standards First**: Python, Django, PostgreSQL, uv와 HTTP·TLS·JSON·
  SHA-256 표준을 우선 사용하고 제품 고유 코드에는 source 계약, typed 변환, 대사와 공개
  표현 규칙만 남긴다.
- **Less Code, Less Complexity**: 서버 렌더링 Django 애플리케이션 하나, PostgreSQL 하나,
  관리 명령과 platform cron 하나로 시작한다.
- **Production Quality from Day One**: 출처 감사, DB 제약, 실패 폐쇄, 테스트, 최소 권한,
  관측성, 백업·복원과 publication rollback을 첫 공개 조건으로 둔다.
- **Small, Reversible Commits**: live source 증거, schema, adapter, parser, review, publication,
  UI와 운영 변경을 각각 독립적으로 검증하고 되돌릴 수 있게 나눈다.
- **Prove Risky Assumptions Before Build**: 실제 응답의 권리, 인증, quota, schema, 코드
  identity, 단위, 기준일과 비교 기간 의미가 승인되기 전에는 그 결과에 의존하는 schema나
  adapter를 만들지 않는다.

전체 모토는 `Build less. Reuse more. Ship solid. Change safely.`이다.

## 애플리케이션 구조

하나의 Django modular monolith 안에서 다음 책임만 분리한다. 실제 package 이름은 구현
계획에서 이 경계를 가장 적은 코드로 표현하도록 정한다.

- **sources**: source configuration, 요청 영수증, content-addressed artifact와 권리 결정
- **prices**: 결정적 파싱, typed identity, recent·monthly snapshot, reference price와 대사
- **publication**: review decision, 불변 revision, append-only activation과 channel별
  last-known-good pointer
- **public**: 품목 목록·검색, exact series 상세와 중립적 가격 변화 표현
- **operations**: Admin 권한, 관리 명령, source freshness, health와 감사 조회

Django ORM과 migration이 schema를 소유한다. public read는 현재 activation이 가리키는
승인된 `PublicationRevision`만 PostgreSQL에서 읽는다. 웹 요청 안에서 외부 API를 호출하거나
긴 수집·파싱을 실행하지 않는다. 수집은 중복 실행에 안전한 관리 명령으로 수행하고 platform
cron이 호출한다.

## 서버 렌더링 UI

사용자 화면은 Django Templates와 Forms로 구현한다. 품목 목록, 공식 품목명 검색, 한
`PriceSeriesKey`의 상세 화면만 첫 navigation에 둔다. 핵심 읽기와 검색은 JavaScript 없이도
동작해야 하며 다음을 만족한다.

- semantic HTML, 명시적 label, 논리적인 heading과 keyboard focus를 사용한다.
- 색만으로 상승·하락·동일·비교 불가를 구분하지 않고 텍스트와 기호를 함께 제공한다.
- recent source 조사일 또는 monthly row 기준 연월, coverage, 판매 단위, grade와 검토일을
  가격 가까이에 표시한다.
- 값이 없거나 비교할 수 없으면 빈 숫자나 `0원` 대신 `비교 정보 없음`을 표시한다.
- 광고나 analytics SDK는 첫 MVP에 넣지 않는다.

CSS와 필수 정적 자산은 Django static files로 제공한다. 외부 CDN JavaScript와 외부 font가
핵심 기능, 개인정보 경계 또는 가용성의 단일 실패점이 되지 않게 한다.

## 데이터베이스와 타입 규칙

- 범용 EAV와 schema-less domain JSON 대신 닫힌 enum, 외래 키, `DecimalField`, 날짜 필드와
  명시적 DB 제약을 사용한다.
- `PriceSeriesKey`의 모든 차원을 unique constraint에 포함하고 표시 이름을 identity로 쓰지
  않는다.
- 가격은 source gate가 증명한 Decimal scale로 원문 정밀도를 보존하고 float를 사용하지
  않는다. 화면 반올림·원화 표시 규칙은 실제 scale 확인 후 승인한다. 퍼센트는 저장된
  float가 아니라 현재값과 기준값으로 계산하고 `Decimal`의 `ROUND_HALF_UP`으로 소수 첫째
  자리까지 낸다.
- recent row의 `source_effective_date: LocalDate`와 monthly row의
  `source_effective_month: YearMonth`를 분리한다. `source_recorded_at`, `fetched_at`,
  `revision_created_at`, `activated_at`도 서로 다른 필드로 유지한다. 월을 임의의 첫날로
  바꾸거나 source가 주지 않은 날짜·시각을 다른 값으로 채우지 않는다.
- 현재 channel pointer 전환과 append-only activation 사건은 한 transaction에서 이루어진다.
  이전 승인 revision은 상태를 바꾸지 않는 불변 내용으로 남긴다.
- source·module별 current pointer를 분리해 한 source 실패가 다른 승인 공개본을 바꾸지 않게
  한다.

## HTTP 수집과 파싱 규칙

- source gate가 승인한 공식 HTTPS host, path, method와 query parameter만 허용한다.
- API key는 배포 secret store에서 주입하며 URL, 로그, 오류, artifact와 Git에 기록하지
  않는다.
- 연결·읽기 timeout, 최대 response 크기, redirect, content type, encoding, pagination과
  호출 예산을 명시한다. `429`와 일시적 `5xx`만 제한된 backoff 대상으로 분류한다.
- HTTP `200`이어도 provider error code·message가 있으면 성공으로 처리하지 않는다.
- raw bytes는 권리가 명시적으로 허용할 때만 격리 저장한다. 그렇지 않으면 body hash,
  byte length, 최소 response receipt와 정규화 사실만 남긴다.
- parser는 network, 현재 시각과 mutable global state를 참조하지 않는 결정적 함수로 만든다.
- 알 수 없는 코드, 누락 필드, sentinel, 잘못된 숫자, 단위 변경, 중복 identity와 coverage
  급변은 추정 보정하지 않고 격리한다.
- 같은 artifact와 parser version을 다시 처리하면 typed row 집합 hash가 같아야 한다.
  `fetched_at`은 idempotency key에서 제외한다.

표준 라이브러리와 Django 내장 기능을 먼저 사용한다. 외부 HTTP·retry·parser library는 실제
source 증거가 내장 기능으로 안전하게 충족되지 않음을 보인 뒤 최소 하나만 선택하고 버전과
license를 고정한다.

## 테스트와 품질 게이트

모든 구현 변경은 영향 범위에 맞춰 다음 검사를 자동화한다.

- 잠금 상태의 깨끗한 설치와 Python·Django 실제 버전 확인
- formatter, linter, static type checker와 migration 누락 검사
- enum·identity·단위·날짜·`Decimal` 계산의 단위 테스트
- 승인된 최소 artifact에 대한 parser golden test와 동일 artifact replay test
- fetch 실패, provider error, schema drift, missing value, duplicate row와 승인 key set 변경 테스트
- review 전 비공개, transaction 실패, 동시 publication과 last-known-good 통합 테스트
- Forms·route·template의 양성·음성 경로, HTML 유효성, keyboard와 screen reader 점검
- `django check`와 production 설정의 `django check --deploy`
- secret scan, dependency vulnerability·license 검사와 clean Git tree 확인

live source gate가 실패한 것을 합성 fixture의 성공으로 대체하지 않는다. 원시 데이터 보존
권리가 없으면 승인된 schema와 경계 사례를 재현한 최소 합성 fixture만 Git에 둘 수 있으며,
그 fixture는 source 접근·권리·운영 가능성의 증거가 아니다.

## 의도적으로 제외한 기술

첫 구현에는 Node.js, Astro, SPA, 별도 public JSON API, GraphQL, Redis, Celery, Kafka,
OpenSearch, PostGIS, data warehouse, Kubernetes와 microservice를 도입하지 않는다. 사용자
위치와 지도 기능이 없으므로 GPS·geospatial schema도 없다. 측정된 운영 병목과 승인된 새
요구가 생기기 전에는 cache server나 queue를 추가하지 않는다.

## 보안과 운영 게이트

- 운영은 `DEBUG=False`, 정확한 `ALLOWED_HOSTS`·CSRF origin, HTTPS, HSTS, secure cookie와
  보안 header를 사용한다.
- Admin은 public route와 분리하고 운영자별 최소 권한, MFA 또는 동등한 강한 인증, 로그인
  rate limit을 적용한다.
- application, migration, backup DB 역할을 분리하고 secret은 환경별 secret store에서
  주입·회전한다.
- 구조화 로그에는 request ID, deploy version, command run ID와 lifecycle 내부 ID·상태만
  남긴다. API key, query string, response body, 검색어와 사용자 식별자는 기록하지 않는다.
- liveness는 process 응답, readiness는 DB와 승인 publication read를 검사한다. source
  freshness와 last-known-good 나이는 readiness와 분리해 경보한다.
- 연속 fetch 실패, parse quarantine 증가, schema·coverage 대사 실패, publication transaction
  실패, backup 실패에 운영자 alert를 연결한다.

공개 전 PostgreSQL 암호화 backup과 point-in-time recovery를 구성한다. 목표는 `RPO 24시간`,
`RTO 4시간`이며 빈 환경 복원으로 승인 revision, audit chain, row count·hash와 Admin/public
read를 검증한다. 분기마다 복원 훈련을 반복한다.

## 배포와 rollback

개발 server가 아닌 고정 WSGI process와 관리형 HTTPS 경계를 사용한다. 배포는 backup 확인,
호환 migration, static assets, application 전환, health와 핵심 공개 읽기 순으로 한다.
application version과 승인 `PublicationRevision`은 서로 독립적으로 이전 상태로 되돌릴 수
있어야 한다. publication rollback은 이전 revision을 수정하지 않고 새 `ROLLBACK` activation을
추가한다. 파괴적 migration은 expand·migrate·contract 단계로 나누며 복원 검증 전에 contract
단계를 실행하지 않는다. key 발급·약관 동의·결제·배포·첫 publication은 사람 승인 지점이다.
