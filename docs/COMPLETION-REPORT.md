# Phase 0 배포 직전 완료 보고서 (역사적 기준선)

검증일은 2026-08-30(KST)다. 이 보고서는 local production candidate의 증거이며 실제
production 배포, traffic 공개나 `Phase 0 완료`를 주장하지 않는다.

이 보고서는 tracked commit `d682908`까지의 Phase 0 증거를 보존합니다. 이후 frontend
redesign commit이 추가되어 vNext 시작 기준선은
`bb0b28038243c539db2eafcfebc05144d9d59d66`입니다. vNext를 시작할 당시 `main`은 GitHub 공개
remote `origin`과 동기화되어 있었습니다. 아래의 `remote 없음`과 당시 SHA·검증값은 수정된
현재 상태가 아니라 해당 시점의 역사적 사실로 읽습니다.

현재 상태는 **Phase 0 배포 직전 완료**다. 아래 결과는 local production candidate에만 적용되며
production platform 선택, 실제 배포와 traffic 공개는 포함하지 않는다.

## 1. release SHA와 Git 상태

- 900초 성능 profile 실행 대상 application SHA:
  `02f1e5c14e84757d8929da710a41e844bd94bac3`.
- 최종 release SHA: 이 tracked 보고서를 포함하는 마지막 clean commit이므로 문서 안에서
  자기 SHA를 참조할 수 없다. 세션 완료 응답이 `git rev-parse HEAD` exact 값을 고정한다.
- final gate: branch `main`, remote 없음, `git status --porcelain` empty, `git fsck --full` 통과.
- `.env.local`은 untracked·ignored 상태를 유지한다.

## 2. 구현된 사용자 흐름과 비목표

실제 Path A generation을 검수·승인·seal·activate한 뒤, public request가 active
`RECENT_RETAIL` revision만 읽는 한국어 Django SSR 폐쇄 루프를 구현했다. 사용자는 채소·과일
목록에서 공식 품목명을 검색·필터링하고 exact 품목·품종·등급·raw 단위·검증된 aggregate
coverage 상세로 이동한다. 상세는 source 조사일의 KAMIS 소매 조사 평균과 같은 row의 1주·1개월·
1년 제공값, 결정적 차이·퍼센트·방향, reference date unavailable, 검토일과 출처를 표시한다.

desktop과 mobile은 별도 SPA 없이 같은 server-rendered responsive route를 쓴다. 네이티브 앱,
앱스토어 배포, 계정·위치·개인화·analytics, 도매·지역 비교·1일 비교·쇼핑몰 정보·알림과 월별
과거 패턴 module은 비목표다. 공개 문구는 구매·품질·자연적 시기·미래값 판단으로 확대하지
않는다.

## 3. 선택 source path와 권리 증거

- 선택: **A — 최근 비교 MVP**.
- owner: 한국농수산식품유통공사(aT), dataset
  [15156063](https://www.data.go.kr/data/15156063/openapi.do).
- endpoint: `GET https://apis.data.go.kr/B552845/recent/price`, Swagger `1.0.0`.
- 실제 HTTPS·인증·JSON/XML UTF-8·provider success/error envelope와 5-page ordered pagination을
  검증했다. 452행 schema를 대사했고 소매 채소 58·과일 37 중 exact 5+5를 승인 범위로 삼았다.
- 두 획득의 ordered manifest는
  `dd893ef82f1f1597a2b65ca6024f31fb7b62ae3f10b13c6d6185365eca2798ba`로 같았다.
- 실제 request audit 시각(UTC)은 첫 attempt
  `2026-08-30T04:00:36.497949Z`~`04:00:37.338969Z`, 두 번째 attempt
  `2026-08-30T04:00:47.994140Z`~`04:00:48.696744Z`다. body·query 없는 redacted receipt와
  hash는 [구현 계획](IMPLEMENTATION-PLAN.md#source-gate-증거)에 연결한다.
- 기존 source configuration의 `state_changed_at`·`rights_confirmed_at`은
  `2026-08-29T15:00:00Z`(KST 자정)라는 date-precision 값으로 남아 있어 실제 gate 관측 시각으로
  해석할 수 없다. 정확한 live 관측 시각은 보존되지 않았으며 원본 두 값은 수정·삭제하지 않았다.
  migration `grocery 0010`은 correction
  `49143c27-d2dd-5fbd-b1dc-4aa3cc002fab`을 append-only로 추가한다. effective 값
  `2026-08-30T02:23:44Z`는 증거 commit
  `d23e5707e1fc3bf6e032d459b149b946b0451e00`의 기록 시각을 사용한 **durable gate-decision
  recorded-at upper bound**이지 정확한 관측 시각이 아니다. DB trigger가 correction의 base·chronology를
  검사하고 update/delete를 거부하며 bootstrap·review·inspection은 검증된 effective helper만 쓴다.
  새 DB는 정확한 effective 값으로 생성되어 correction row가 필요 없다.
- portal의 `이용허락범위 제한 없음`과 자유이용·상업/비상업 이용·변형 허용을 확인했다.
  제품은 더 좁게 정규화 사실과 출처만 공개한다. raw payload 보존·재배포 문구는 명시적이지
  않아 `HASH_ONLY`이고 raw body를 파일·Git·publication에 저장하지 않았다.
- source configuration의 권리 locator는 공식 dataset landing이다. 저장된
  `rights_evidence_sha256`은 동적 landing HTML이 아니라 그 페이지의 공식 첨부 명세·코드 ZIP
  `07417ea9eb882a33615721256ff8be3b131cdb10bbc9c7b40472bf049a7e0f88`이다. landing의 권리 표시,
  [포털 이용정책](https://www.data.go.kr/ugs/selectPortalPolicyView.do), 2026-08-30(KST) 확인 시각과
  보수적 공개 판정을 함께 검수했고 ReviewDecision의 별도 private evidence
  digest/commitment는
  `2e6dcf9df27077396b8aedf8abaaf69d10bbca2f3a036d6c0127ccb1f434cca6`이다.
- identity는 소매 `01`, 채소 `200`, 과일 `400`과 item·variety·grade·raw unit·unit size,
  coverage `KAMIS_RETAIL_ALL_REGIONS_22_CITIES_V1`이다. coverage는 공식
  [KAMIS 조사요령](https://www.kamis.or.kr/customer/price/knowhow/knowhow.do)의 22개 도시 조사와
  API의 region·market field 부재를 함께 고정했다. 정확한 reference date는 제공되지 않아
  `SOURCE_REFERENCE_DATE_UNAVAILABLE`이다.
- quota 기간·초당 한도는 문서에 없고 실제 429 유발을 위한 소진은 하지 않았다. 수집당 최대
  12회, timeout·size·page·bounded retry로 더 좁게 운영한다.

## 4. test·migration·parser replay

- final locked verification: Ruff format `111 files`, Ruff lint, mypy `97 source files`, migration
  drift, Django system/deploy check가 모두 통과했고 pytest는 `619 passed`다. production 환경이
  route test에 HTTPS redirect를 누출한 첫 orchestration run은 `585 passed, 12 failed`로 실패
  처리한 뒤 test runtime을 deterministic local settings로 격리하고 전체 gate를 다시 통과했다.
- runtime-only locked sync에서 Python `3.14.7`, Django `5.2.17`, Gunicorn `23.0.0`, psycopg
  `3.3.4`, WhiteNoise `6.12.0`, uv `0.12.6`을 확인했다. collectstatic은 `129`개 asset과
  `387`개 post-processed 결과를 재현했다.
- 실제 FetchAttempt:
  `6c4bcbeb-d47c-4648-b6ce-01988316b7dc`,
  `4207e628-3ab9-4a12-a7ae-0cdbec67d744`.
- SourceArtifact `70955f24-b61d-4b43-a7de-8e603f6ae459`, ParseRun
  `0c7fad64-e49b-4c8c-9929-aece2782354d`; 452 received, 10 accepted, 442 out-of-scope.
  두 번째 parse는 replay였고 result hash는
  `512c65031cdfe2b734af4245d974390073999a32ad494cd9c94b33c2f165261e`다.
- version 묶음은 parser `kamis-recent-price-v1`, schema migration `grocery 0010`, application
  `0.1.0`, 성능 검증 application SHA `02f1e5c14e84757d8929da710a41e844bd94bac3`이다.
- 새 빈 PostgreSQL에 Django·grocery migration `0001`~`0010`을 적용했고 drift가 없었다. 빈
  상태는 live 200, ready/freshness 503, catalog 200으로 의도대로 실패 폐쇄했다.
- 실제 publication backup 복원 DB는 migration inventory 28개·public table 25개와 row count,
  audit/publication contract를 모두 대사했고 live/ready/freshness/catalog가 모두 200이었다.

## 5. desktop·mobile screenshot 검수

`scripts/browser_acceptance.js`가 Chromium 152에서 `360x800`, `390x844`, `768x1024`,
`1440x900`의 실제 catalog/detail과 상태 matrix를 통과했다. 18개 full-page PNG와 hash는
[browser evidence](../output/playwright/phase0/README.md)에 있다.

모든 viewport에서 가로 overflow 없음, 최소 44px target, 읽을 수 있는 계층, 긴 한국어
identity·단위·출처·freshness, mobile 입력·제출·validation 수정, loading·empty·unavailable·
stale·server error, keyboard 순서·visible focus, landmark·heading·label·accessible name과
색상 외 text 상태를 확인했다. 발견된 mobile 중복 breadcrumb와 query reflection/cache 결함은
수정 후 재촬영했다.

## 6. 접근성·성능·보안·license

- axe-core 4.13.0의 실제 catalog/detail WCAG A/AA violation은 각각 0이다. decorative/gradient
  contrast 한 incomplete 항목은 모든 실제 foreground/background와 양쪽 gradient endpoint가
  4.5:1 이상인 별도 palette test로 보강했다. axe receipt hash는
  `a994c5a00a9f5f75213381c5be2ef49624eb796e202d3becd0daef4818d076a6`다.
- corrected 900초 성능 profile은 exact `9,000/9,000` 성공, catalog·list·search `6,300`, detail
  `2,700`, error·5xx `0`, p50 `26.858 ms`, p95 `40.656 ms`, max `551.135 ms`, elapsed
  `900.017초`, throughput `10.0 rps`, revision 단일값으로 통과했다. 고정 logical user는 구성·참여
  모두 `20`이고 전원 round-robin이었다. 이와 별개로 실제 in-flight peak는 `5`, 상한은 `20`이었다.
  nominal cadence `100 ms`, bounded recovery floor `90 ms`, effective deadline jitter p95
  `5.35 ms`·max `76.784 ms`, 실제 최소 submit interval `90.028 ms`, burst `0`, `passed=true`였다.
  `551.135 ms` max는 관측값이고 통과 gate는 end-to-end p95 `500 ms` 이하다.
- 두 실패 run도 보존한다. 첫 진단 run은 응답 `9,000`개와 elapsed `900.056초`를 완료했지만 단일
  scheduler stall을 뒤 요청 전체의 lag로 잘못 누적해 `passed=false`였다. 그 오판을 고친 두 번째
  run은 응답 `9,000/9,000`, error·5xx `0`, p95 `41.716 ms`였으나 strict `100 ms` floor가 정상
  overhead를 누적해 elapsed `947.317초`, `9.501 rps`로 실패했다. 최종 runner는 정상 `100 ms`
  cadence를 유지하면서 stall만 요청당 최대 `10 ms`씩 회복하고 `90 ms` 미만을 burst로 거부한다.
- security: production setting validation, secure headers/CSP, request ID, no-store HTML,
  immutable hashed static, GET-only public SSR, default-off Admin·QA·control-plane, exact release
  lock과 fixed non-login reviewer/publisher permission 경계를 검증했다.
- secret gate는 `present=yes`, `ignored=yes`, `permissions=ok`, `current_match=no`,
  `history_match=no`였고
  key 값·길이·일부·encoding을 출력하지 않았다. `pip-audit`는 알려진 취약점 `0`, locked package
  license inventory는 해결되지 않은 차단 항목 `0`이었다. Browser assurance 도구까지
  `THIRD_PARTY_NOTICES.md`에 고정했다. production artifact의 bundled notices는 실제 platform
  packaging checkpoint다.
- Make는 ambient `KAMIS_API_KEY`를 모든 recipe child에서 unexport한다. synthetic marker를 parent
  environment에 둔 negative test에서도 assurance child 경계는 `source_secret_environment=absent`였고
  marker가 stdout·stderr에 반사되지 않았다.

## 7. backup restore와 publication rollback

- hardened local PostgreSQL 18 custom backup ID `4e74a867-fb92-42be-9e2f-4718a5a276d0`.
- dump SHA-256 `bcf282944defefc995e7f309fb10e2b5a81fb0c8bd40b08416c86b2780ddb0a5`,
  manifest SHA-256 `23644fec396e3310c1bd807a3b8321fec62261452323574a5a64d48d15922cf2`.
- 남긴 local evidence 경계는 directory `0700`, dump·manifest `0600`이며 다른 rehearsal backup과
  disposable restore DB는 제거했다.
- 새 격리 DB restore에서 rows·28 migrations·active revision·fact-set·activation chain이 모두
  일치했다. 이 local dump는 production 암호화 backup/PITR가 아니다.
- hardened restore는 receipt에서 out-of-band로 보존한 위 manifest SHA-256을
  `--expected-manifest-sha256 "$BACKUP_MANIFEST_SHA256"`로 반드시 전달하고, 고정 local Docker
  socket에서 발견·identity-pin한 exact Compose DB container만 사용하는 것이다. target 생성 뒤
  실패하면 같은 invocation이 만든 exact disposable target만 자동 삭제하고 부재를 확인한다.
  `25`개 public table·`28`개 migration, row counts, publication metadata와 actual ordered payload를
  재계산한 canonical fact-set이 모두 일치했고 restored live/ready/freshness/catalog/detail은 모두
  `200`이었다. 잘못된 manifest receipt는 Docker preflight와 target 생성 전에 fixed code로
  거부했고 target 부재를 확인했다.
- publication은 v1 activate → v2 activate → v1 `ROLLBACK`을 append-only로 훈련했다. 현재
  channel version `3`, v1 `dc6f5c83-92cc-48e7-8103-76f3fd1a668b`, 10 entries, fact-set
  `6de8e26c22dcee4a7ce4a6e1a0640999399d126d62124cc1b1d7aefcf9aa66a9`다.
- 승인 ReviewDecision `330cad14-2102-4dcf-a023-93a7368c7efb`는
  `2026-08-30T04:19:49.128258Z`, 최신 ROLLBACK activation
  `cd1f3064-2920-4395-9469-7f4b3e0b969d`는 `2026-08-30T04:20:22.343137Z`에 기록됐다.
- application rollback rehearsal target SHA `d6d7d08c9de9a78eb597fec6e232b0e2d24a1ec1`도 최신 schema에서
  live/ready/freshness/catalog/detail와 hashed static 200, 같은 publication hash를 확인했다.

## 8. 구조화 log·health·freshness alert

`grocery.audit`는 allowlist된 single-line JSON만 stdout에 내고 arbitrary message, exception,
query, search term, body, credential·사용자 정보를 받지 않는다. valid production
`DEPLOY_VERSION`은 event에 자동 포함되며 request correlation UUID가 응답과 log에 연결된다.

`/health/live`, `/health/ready`, `/health/freshness`는 bounded no-store JSON이다. 현재 실제
candidate는 모두 200이고 freshness는 `CURRENT`다. active artifact의 마지막 source 확인은
`2026-08-30T04:00:48.696744Z`이며 36시간 경계는
`2026-08-31T16:00:48.696744Z`(`2026-09-01 01:00:48 KST`)다. 이 시각 전에도 newer
content·실패 attempt가 있으면 즉시 stale로 바뀐다. stale·unavailable, DB/migration/publication
오류, fetch·parse failure는 fixed message code와 non-zero exit로 구분한다. production
notification route, retention, on-call 담당자, ingress access-log privacy와 backup failure alert는
platform 선택 뒤 실제 검증해야 한다.

## 9. 실제 배포에 필요한 것

- Python 3.14.7·Django 5.2.17·uv 0.12.6와 PostgreSQL 18.6 호환 platform
- private managed PostgreSQL, TLS hostname/CA 검증, application·migration·ingestion·reviewer·
  publisher·backup 역할별 credential/grant
- managed `DJANGO_SECRET_KEY`, ingestion worker 전용 `KAMIS_API_KEY`, rotation·revocation 절차
- outbound HTTPS allowlist를 가진 singleton ingestion scheduler, 24시간 cadence, overlap 방지와
  fixed failure/freshness alert route
- 승인 domain·DNS·certificate, exact host/CSRF, HSTS subdomain/preload 판단과 trusted proxy 계약
- external MFA/IAM private operation job, actor provisioning과 첫 production publication 승인
- encrypted scheduled backup, PITR, retention, restore rehearsal, RPO 24h/RTO 4h evidence
- health probe, alert route/on-call, log 수집·보존과 query/IP/User-Agent 제거

## 10. deploy·rollback 절차와 사람 작업

아래 순서는 clean `RELEASE_SHA`에서 locked dependency, forward migration, static과 process를
platform과 무관하게 재현하는 요약이다. authoritative 순서와 environment wrapper는
[운영 런북](OPERATIONS-RUNBOOK.md)에 있으며 local candidate에서는 synthetic/local assurance
설정으로 이를 검증한다. production에서는 그 런북의 승인된 environment와 역할별 credential을
managed injection한 process에서 실행한다.

```sh
make runtime-sync
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py showmigrations --plan
.venv/bin/python manage.py migrate --plan
.venv/bin/python manage.py check
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py migrate --check
.venv/bin/python manage.py check --deploy --fail-level WARNING
exec .venv/bin/gunicorn config.wsgi:application \
  --bind "$GUNICORN_BIND" --workers "$GUNICORN_WORKERS" --threads "$GUNICORN_THREADS"
```

새 release를 traffic 없이 시작해 live→ready→freshness→catalog/detail→hashed static을 검사한 뒤
사람이 atomic traffic switch를 실행한다. application rollback은 DB·publication을 그대로 두고
local rehearsal에서 검증한 `PREVIOUS_RELEASE_SHA`
`d6d7d08c9de9a78eb597fec6e232b0e2d24a1ec1`와 그 static으로 code를 되돌린다. reverse migration은
하지 않는다. 이 SHA는 local 호환성 evidence이며 vendor traffic rollback 승인이 아니다.
publication rollback은 먼저 `inspect_recent_publication`을 실행한 뒤 external-MFA
publisher job에서 `transition_recent_publication --operation ROLLBACK`과 exact expected state·
release SHA를 사용한다. DB 복구는 in-place overwrite가 아니라 managed PITR의 새 instance를
검증한 뒤 connection을 전환한다.

production platform 선택 뒤 artifact 포맷·bundled notice, upload/release, atomic traffic switch와
application rollback의 exact vendor CLI·account·application scope를 별도로 승인하기 전에는
배포하지 않는다.

남은 사람 전용 작업은 platform·database·secret store·role/IAM·domain/DNS 선택, production
backup/PITR·alert 검증, vendor deploy/traffic/rollback 명령 확정과 실제 배포다.
추가 API key·로그인·약관·결제, 고정 제품 결정 변경과 destructive migration이 필요해져도
자동 진행하지 않고 별도 사람 승인에서 멈춘다.

---

# vNext local implementation candidate 완료 부록

검증일은 2026-08-31(KST)다. 이 부록은 시작 기준선
`bb0b28038243c539db2eafcfebc05144d9d59d66` 이후의 local vNext 구현과 합성 검증을 기록한다.
production readiness, live historical data 검증, 배포 또는 traffic 공개 완료를 주장하지 않는다.

## 1. 구현과 Git 경계

- 제품·source 계약을 먼저 고정한 뒤 source adapter·typed historical model·collection review·
  independent publication·public-read·frontend 순서의 선형 commit으로 통합했다. backend commit
  뒤에 public-read와 frontend commit이 이어지며 merge commit이나 history rewrite는 없다.
- 최근 공개본과 historical 공개본은 독립 active pointer, freshness와 fact-set을 유지한다.
  recent detail은 historical mapping이나 publication 오류에도 계속 제공하고 확장 링크만 숨긴다.
- catalog는 기간·방향·정렬과 최대 100개 bounded read를 제공하고, history는 선택 지역의 월별
  기록, regions는 실제 공통 조사일의 지역 범위, markets는 해당 지역·조사일 시장 관측,
  selection은 URL에 최대 5개 series를 담는 no-account 비교를 제공한다.
- public GET은 canonical state만 허용하고 source API를 호출하지 않는다. current publication의
  전체 shape를 먼저 검증하며 malformed hidden fact도 503으로 실패 폐쇄한다.
- 검증 대상 application·운영 문서 exact SHA는
  `cb0d4264ceee434fd66ff230cac0c29fe28308a2`, evidence commit은 `5119b3b`다. 이 부록을 포함하는
  최종 local `main` SHA는 문서가 자기 commit을 참조할 수 없으므로 완료 응답에서 고정한다.
- 이 부록 최초 작성 시점에는 `origin`이 vNext 시작 기준선에 남아 있었다. 이후 source-to-SSR
  follow-up과 승인된 remote 고정 결과는 아래 추가 증거와 세션 완료 응답에서 구분해 기록한다.

## 2. schema·fixture·필수 gate

- 빈 loopback PostgreSQL의 exact disposable DB
  `grocery_vnext_verify_20260831_01`에 Django와 grocery migration `0001`~`0028`을 순방향 적용했다.
- browser fixture는 production command가 아니라 DEBUG+QA, Admin off, loopback, 빈 DB와
  `grocery_vnext_` 이름을 모두 확인한 뒤 정상 service로 review·seal·activate했다. 합성 범위는
  recent series 5개, region 2개, market 31개, 36개월 monthly fact 360개다.
- final `make check`는 Ruff format `218 files`, lint, mypy `201 source files`, migration drift,
  Django system check와 pytest `859 passed`를 통과했다. synthetic production 설정의
  `check --deploy --fail-level WARNING`도 warning 없이 통과했다.
- 선행 gate 실패는 모두 실패로 처리하고 수정했다. 첫 통합 gate는 37개 format drift, 다음은
  test fixture typing 44건, 그다음은 source schedule constraint와 catalog validation recovery
  3건을 발견했다. schedule을 monthly `1..168`, 나머지 `1..24`로 DB에 고정하고 구체 오류 복구를
  복원한 뒤 전체 gate를 통과했다.
- 문서대로 fixture script를 직접 실행했을 때 repository root import가 누락된 결함도 발견해
  수정했고, 그 뒤 같은 command로 fixture를 성공 생성했다. 테스트를 삭제하거나 약화하지 않았다.

## 3. browser·접근성·짧은 load smoke

[vNext browser evidence](../output/playwright/vnext/README.md)는 exact tested SHA, 도구 버전,
8개 PNG와 JSON receipt의 SHA-256을 고정한다.

- `390×844`와 `1440×900`에서 catalog → detail → history region GET → regions → markets →
  detail → 2개 품목 selection GET 흐름을 통과했다. `360×800`, `768×1024`에서는 같은 6개
  surface의 horizontal overflow가 없다.
- 모든 검사 화면은 client script·inline event·외부 request 0, 44px target, 한 개의 main·h1,
  keyboard 순서와 skip-link focus, no-store·no-referrer·CSP·fact-set header 분리를 통과했다.
- 첫 render에서 390px 첫 record가 fold 아래에서 시작하고 detail `392/390`, regions `364/360`
  overflow가 있음을 발견했다. secondary search disclosure, compact publication summary와 mobile
  stacked facts로 수정했다. 최종 첫 record는 844px 안에 완전히 보인다.
- local axe-core 4.13.0은 390px의 ready 6개 surface와 validation 400에서 WCAG A·AA violation 0,
  unexpected incomplete 0이다. 자동 판정 불가인 aria-hidden 장식 기호와 SVG label만 수동검토로
  분리했고 실제 palette 4.5:1 단위 gate로 보강했다. 불필요한 generic div의
  `aria-labelledby`는 제거했다.
- 최종 60초 recent read smoke는 600/600 성공, error·5xx 0, p95 74.8ms, 9.999rps와 single
  revision을 확인했다. 첫 smoke는 active pointer가 아닌 과거 revision series를 잘못 선택해 detail
  180건이 의도대로 503을 반환했으며, active channel의 exact series를 다시 읽어 재실행했다.
- 이 60초 결과는 `SMOKE_NON_ACCEPTANCE`다. 역사적 Phase 0 900초 profile을 재실행하거나
  history·regions·markets·selection의 capacity를 검증했다고 해석하지 않는다.

## 4. 폐기·미접근·사람 checkpoint

- 검증 후 임시 Gunicorn과 이 작업이 연 Playwright session만 종료했다. exact disposable DB
  `grocery_vnext_verify_20260831_01`을 삭제했고 PostgreSQL catalog에서 잔존 개수 `0`을 확인했다.
  기존 container·volume과 다른 database는 변경하지 않았다.
- KAMIS source API, `.env.local`, API key의 값·길이·일부·encoding, production database,
  production credential, platform, domain·DNS에는 접근하지 않았다. raw source data, credential,
  검색어 또는 사용자 입력을 evidence artifact로 만들지 않았다.
- 실제 full collection과 cross-source mapping 검수, production actor/IAM/MFA, `ko-v4` review·seal·
  activation, migration과 traffic switch·rollback은 사람 checkpoint다.
- repository health·freshness와 `inspect_recent_publication`, 기존 backup canonical 검증은
  recent-only다. historical 전용 health/authoritative inspection, canonical backup restore와
  이전 release의 migration `0028`·vNext route rollback 호환성을 별도로 증명하기 전에는
  historical production traffic을 열지 않는다.

## 5. 실제 source-to-SSR follow-up evidence

2026-08-31(KST), test commit `0eb9d62`의 명시적 opt-in smoke를 exact disposable loopback
PostgreSQL에서 실제 실행했다. 네 공식 API를 normal adapter로 호출해 최근 10행, 월별 36행,
지역별 1행, 시장별 9행을 typed model에 통과시킨 뒤 test-only mapping·review·seal·activation으로
catalog·detail·history·regions·markets 5개 SSR route를 검증했다. SSR 처리 중 source call은 0이고
각 화면에 저장된 제공값이 존재함을 확인했다.

모든 write는 outer transaction으로 rollback했고 root table이 다시 비었는지 확인한 뒤 exact 전용
DB를 삭제했다. source row·response body·URL query·credential·사용자 입력은 log, fixture, receipt나
artifact에 남기지 않았고 고정 count receipt만 출력했다. 이 결과는 key·provider schema·adapter·
persistence·SSR 연결의 실제 smoke evidence다. 동적으로 파생한 test mapping과 자동 approval은
사람의 cross-source identity·권리·전체 coverage 검수, production publication·activation,
scheduler·traffic 검증을 대신하지 않는다.

## 6. Frontend redesign v2 실제 자료 browser evidence

Frontend redesign v2는 `27bb0cc3e9c65309c567fb6b4e08ad8b989907a6`에서 시작했다. browser
evidence 대상 frontend commit은 `d97888885e8a2e5b8db88005ddf0bf3a336dcdc6`, evidence commit은
`db906b4`다. 이 절을 포함한 최종 local `main` SHA는 문서가 자기 commit을 참조할 수 없으므로
완료 응답에서 고정한다.

명시적 opt-in 실행에서 실제 네 KAMIS 공공 API 응답을 normal adapter와 typed persistence에
통과시키고, disposable PostgreSQL에서 test-only mapping·review·seal·activation으로 local active
test publication을 만들었다. recent 10행, monthly 36행, regional 1행, market 9행을 정규화했고
command 단계의 catalog·detail·history·regions·markets 5개 SSR route는 source 재호출 0,
raw response 보존 없음으로 통과했다. browser ready 화면의 가격값은 synthetic fixture가 아니라
이 local active test publication에서 읽은 실제 API 기반 정규화 값이다.

[Frontend redesign v2 browser evidence](../output/playwright/vnext-redesign-v2/README.md)는
`390×844`와 `1440×900`의 catalog·detail·history·regions·markets·selection 6개 소비자 surface,
`360×800`과 `768×1024`의 overflow 0, mobile 첫 catalog record의 fold 안 노출, client script와
외부 request 0을 고정한다. local axe-core 4.13.0은 ready 6면, validation 400, catalog 503,
generic 404의 9면에서 WCAG 2/2.1/2.2 A·AA violation 0과 unexpected incomplete 0을 확인했다.

따라서 application code, local 실제 자료 흐름과 browser 인수 범위에서는 배포 직전 candidate다.
그러나 자동 mapping·approval과 representative historical scope는 첫 catalog 품목과 한 지역에
한정되며, 사람이 검토한 full-catalog production publication이 아니다. production platform·DB·
secret·domain·DNS, 전체 cross-source mapping·권리·coverage 검수, production review·seal·activation,
historical monitor·authoritative inspection·backup, traffic switch와 rollback은 사람 checkpoint로
남는다. §4의 source API와 `.env.local` 미접근 기록은 최초 합성 검증 시점의 사실이며, §5와 이 절은
그 뒤 소유자가 승인한 별도 opt-in live 실행 결과다.
