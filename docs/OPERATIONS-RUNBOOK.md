# Phase 0 배포 직전 운영 런북

## 문서 상태와 범위

이 문서는 현재 저장소를 **Phase 0 배포 직전 production candidate**로 검증하고, 사람이
production platform을 선택한 뒤 따라야 할 안전한 배포·복구 순서를 정의한다. 실제
production platform, PostgreSQL, credential, domain·DNS는 아직 선택하거나 변경하지 않았고,
이 문서는 실제 배포 완료를 주장하지 않는다.

명령의 `$NAME`은 운영자가 승인된 CI 변수 또는 managed configuration·secret store에서
주입해야 하는 placeholder다. 이 문서, shell history, process argument, URL, log, ticket와
receipt에 실제 secret 값을 적지 않는다. 모든 명령은 별도 설명이 없으면 exact release의
repository root에서 실행한다.

다음은 사람 전용 checkpoint다.

- production platform·database·domain·DNS 선택과 생성
- production credential·secret store 주입과 provider key 발급·폐기
- production 배포·traffic switch·application rollback
- production reviewer·publisher identity, MFA, 최소권한과 첫 publication
- 파괴적 migration, database 삭제·교체, in-place restore와 PITR 전환
- 고정 제품 결정, source 권리 또는 공개 범위 변경

이 checkpoint는 명시적 승인과 change record 없이는 실행하지 않는다.

## 현재 candidate가 제공하는 것과 남은 차단점

| 영역 | 현재 제공 범위 | production 판단 |
|---|---|---|
| web | Django WSGI와 고정 Gunicorn dependency | platform process·HTTPS·traffic switch가 필요 |
| schema | `0001`부터 `0010`까지 create/add 중심 migration과 DB trigger | production 복제 DB에서 plan·lock·시간 검증 필요 |
| ingestion | `ingest_kamis_recent`의 bounded fetch·parse·audit와 typed 24시간 singleton schedule 계약 | managed `KAMIS_API_KEY`, 실제 singleton scheduler·egress·alert 필요 |
| review/publication | local rehearsal과 fail-closed production command boundary | external MFA·IAM, 역할별 DB credential과 실제 actor provisioning 필요 |
| public read | active `RECENT_RETAIL` revision만 읽는 SSR | production DB의 승인 pointer와 smoke test 필요 |
| health | liveness, readiness, freshness endpoint와 scheduler command | platform probe·alert routing이 필요 |
| backup | local Compose DB용 custom dump·isolated restore 검증 | production backup, encryption, PITR를 제공하지 않음 |
| logs | allowlist 기반 JSON event, 고정 message code와 exact deploy version | log 수집·보존·alert 담당자와 platform access-log 제거가 필요 |

`approve_recent_generation`, `seal_recent_publication`과
`transition_recent_publication`은 local rehearsal과 production 경계를 구분한다. production에서는
Admin·QA가 꺼지고 별도 control-plane enable, exact release SHA, command별 고정 non-login actor와
최소 permission, fixed reason code가 모두 맞아야 한다. enable flag는 인증 수단이 아니므로
external MFA·IAM, 역할별 DB credential·grant와 실제 actor provisioning이 승인되기 전에는 첫
production publication과 traffic 공개가 차단된다. production에서 `DEBUG`를 켜거나 DB pointer를
직접 수정해 우회하지 않는다.

## 안전 등급

- **읽기 전용**: Git 확인, migration plan, Django check, health 조회, freshness 조회.
- **비파괴적 생성**: static build, local backup 파일, 새 격리 restore database 생성.
- **상태 변경·가역적**: forward migration, ingestion, approve, seal, publication pointer 전환.
  실행 전에 대상·actor·expected state와 rollback을 기록한다.
- **파괴적**: reverse migration, table/database 삭제, 기존 database에 restore, volume 삭제,
  backup 삭제, credential 폐기, DNS·traffic의 되돌릴 수 없는 변경. 단, identity-pin한 고정 local
  container에서 같은 restore invocation이 새로 만든 exact disposable target을 실패 보상으로
  삭제하고 부재를 확인하는 동작만 자동 허용한다. 그 밖에는 사람 승인 없이는 금지한다.

특히 다음 명령 또는 동등 작업은 이 런북의 자동 절차에 포함되지 않는다.

- `migrate ... zero`, 과거 migration으로의 reverse migration, `--fake`, `flush`
- 기존 database를 대상으로 한 `pg_restore --clean` 또는 schema overwrite
- 일반 `dropdb`, `DROP DATABASE`, `DROP TABLE`, `docker compose down -v` 또는 pre-existing target
  삭제. 위의 invocation-owned disposable restore target 실패 보상만 예외다.
- 공유 static root의 `collectstatic --clear`
- Git history rewrite, 강제 push, broad checkout/reset
- backup directory의 재귀 삭제

필요해지면 exact target을 read-only로 다시 확인하고 별도 파괴적 change approval을 받는다.

## 환경과 역할 계약

### application configuration 이름

production web process에는 최소한 다음 이름이 필요하다.

- `DJANGO_DEBUG`
- `ADMIN_ENABLED`
- `DJANGO_SECRET_KEY`
- `DJANGO_ALLOWED_HOSTS`
- `DJANGO_CSRF_TRUSTED_ORIGINS`
- `DATABASE_URL`
- `DEPLOY_VERSION`
- `DJANGO_SECURE_SSL_REDIRECT`
- `DJANGO_SECURE_HSTS_SECONDS`
- `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS`
- `DJANGO_SECURE_HSTS_PRELOAD`
- `DJANGO_TRUST_X_FORWARDED_PROTO`
- `DATABASE_CONN_MAX_AGE`
- `KAMIS_CONFIRMATION_MAX_AGE_HOURS`
- `QA_STATE_PREVIEWS_ENABLED`
- `CONTROL_PLANE_OPERATIONS_ENABLED`

production validation은 debug 비활성화, Admin 비활성화, 충분한 Django secret, wildcard가 아닌
host, path·query·credential이 없는 HTTPS CSRF origin을 요구한다. `DEPLOY_VERSION`에는 배포할
exact lowercase full Git SHA를 주입한다. QA preview는 production에서 활성화할 수 없다. HSTS
subdomain·preload와 forwarded-proto 신뢰는 기본 비활성이고, domain 전체와 trusted proxy hop을
사람이 확인한 뒤에만 명시적으로 켠다.

ingestion worker에는 application·database configuration 외에 `KAMIS_API_KEY`가 필요하다.
web process에는 source credential을 주입하지 않는다. loader는 process environment를 먼저
사용하므로 production에 `.env.local`을 복사하거나 mount하지 않는다.

build와 운영 명령에서는 다음 non-secret placeholder를 사용할 수 있다.

- `RELEASE_SHA`, `PREVIOUS_RELEASE_SHA`, `RELEASE_DIRECTORY`
- `LOCAL_ASSURANCE_DJANGO_SECRET`, `PUBLIC_SERIES_ID`
- `GUNICORN_BIND`, `GUNICORN_WORKERS`, `GUNICORN_THREADS`
- `HEALTH_BASE_URL`, `SMOKE_SERIES_PATH`
- `BACKUP_OUTPUT_DIRECTORY`, `BACKUP_DIRECTORY`, `RESTORE_DATABASE_NAME`
- `PARSE_RUN_ID`, `REVIEW_DECISION_ID`, `REVIEW_EVIDENCE_SHA256`
- `PUBLIC_COPY_REVISION`, `PUBLICATION_REVISION_ID`, `ROLLBACK_TARGET_REVISION_ID`
- `PUBLICATION_OPERATION_ID`, `PUBLICATION_ACCEPTANCE_EVIDENCE_SHA256`
- `EXPECTED_PUBLICATION_VERSION`, `EXPECTED_CURRENT_REVISION_ID`

### 최소권한 분리

platform 선택 시 다음 권한을 서로 분리한다.

- web: 승인 publication과 필요한 Django metadata를 읽는 권한
- ingestion: source configuration, attempt, artifact hash, parse와 typed candidate 생성 권한
- reviewer: 검수 evidence에 기반한 decision 생성 권한
- publisher: sealed revision의 atomic pointer 전환 권한
- migration: schema DDL과 trigger 설치 권한
- backup/restore: backup 읽기와 새 복구 database 생성 권한

repository의 production command boundary는 actor와 Django permission을 다시 확인하지만
production DB grant, 외부 identity-aware MFA와 역할별 credential을 대신하지 않는다. 동일
credential 하나에 모든 권한을 합치거나 enable flag를 인증으로 취급하는 것은 production
해법이 아니다.

## Release preflight

### 1. 사람 확인

아래 항목이 하나라도 없으면 배포를 시작하지 않는다.

- 승인된 `RELEASE_SHA`와 이전에 검증된 `PREVIOUS_RELEASE_SHA`
- Python·Django·PostgreSQL·uv 고정 버전을 지원하는 platform
- 관리형 PostgreSQL, private network와 분리된 application/migration/backup 역할
- HTTPS endpoint, 승인된 domain·DNS 변경 계획과 인증서
- managed secret injection과 회전·폐기 담당자
- query string, IP, User-Agent와 search term을 제거한 log pipeline
- liveness/readiness/freshness probe와 on-call alert route
- 암호화 backup, PITR, retention과 restore rehearsal 계획
- production review·publication control plane과 MFA

### 2. local release gate

아래 명령은 production host가 아니라 owner-only `.env.local`이 있는 통제된 local release
checkout에서 실행한다. `make production-check`의 `secret-check`는 그 local 파일이 Git에서
제외되고 credential bytes가 현재·과거 Git blob에 없는지 검사한다. key, 길이 또는 일부를
출력하지 않는다.

```sh
set -eu
make sync
test "$(git rev-parse HEAD)" = "$RELEASE_SHA"
test -z "$(git status --porcelain)"
git fsck --full

DJANGO_DEBUG=0 \
ADMIN_ENABLED=0 \
DJANGO_SECRET_KEY="$LOCAL_ASSURANCE_DJANGO_SECRET" \
DJANGO_ALLOWED_HOSTS=candidate.invalid \
DJANGO_CSRF_TRUSTED_ORIGINS=https://candidate.invalid \
DATABASE_URL=postgresql://grocery:local-grocery-only@127.0.0.1:55434/grocery \
DEPLOY_VERSION="$RELEASE_SHA" \
DJANGO_SECURE_SSL_REDIRECT=1 \
DJANGO_SECURE_HSTS_SECONDS=31536000 \
DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=1 \
DJANGO_SECURE_HSTS_PRELOAD=1 \
make production-check
```

`LOCAL_ASSURANCE_DJANGO_SECRET`은 실제 credential이 아닌 50자 이상의 local check 전용 synthetic
값이다. 위 HSTS include-subdomains·preload 값은 Django warning gate를 통과시키는 synthetic
configuration일 뿐, 실제 domain의 모든 subdomain 보호나 browser preload 제출 승인이 아니다.
검사 뒤 `git status --porcelain`이 계속 비어 있는지도 다시 확인한다.

이 local gate와 immutable build는 vendor와 무관한 재현 계약이다. 같은 clean `RELEASE_SHA`에서
잠금 설치, forward migration plan, collectstatic, production-like process, health·catalog/detail
smoke와 이전 SHA application rollback 순서를 다시 수행할 수 있어야 한다. 실제 artifact 포맷,
upload·release·traffic switch CLI와 bundled license notice 검사는 platform을 선택한 뒤 별도 사람
checkpoint에서 고정한다.

`make production-check`는 ambient `KAMIS_API_KEY`를 모든 recipe child에서 unexport하고 그 부재를
값 없이 확인한 뒤, `DATABASE_URL`이 repository의 고정 loopback Compose database와 정확히
일치하는지 검사한다. 따라서 source credential이 assurance tool로 전파되지 않고 production 또는
다른 local database에 release test를 실행하지 않는다. 이어 format, lint, type, migration drift,
전체 test, Django system check, `check --deploy --fail-level WARNING`, local secret scan, dependency
audit와 license inventory를 실행한다. 그래도 운영자는 key를 shell에 export하지 않는다.
license inventory의 성공 exit만으로 라이선스 정책 승인을 대신하지 않으며
`THIRD_PARTY_NOTICES.md`와 결과를 사람이 대조한다.

production host나 CI에 `.env.local`을 만들기 위해 `secret-check`를 실행하지 않는다. managed
production secret은 이 local Git-history 검사의 대상이 아니므로 production secret injection
검증으로 과장하지 않는다.

### 3. immutable build

release마다 별도 `RELEASE_DIRECTORY`에서 잠금 파일 그대로 runtime dependency와 static
artifact를 만든다.

```sh
make runtime-sync

env \
  DJANGO_DEBUG=0 \
  ADMIN_ENABLED=0 \
  QA_STATE_PREVIEWS_ENABLED=0 \
  CONTROL_PLANE_OPERATIONS_ENABLED=0 \
  DJANGO_SECRET_KEY="$LOCAL_ASSURANCE_DJANGO_SECRET" \
  DJANGO_ALLOWED_HOSTS=candidate.invalid \
  DJANGO_CSRF_TRUSTED_ORIGINS=https://candidate.invalid \
  DATABASE_URL=postgresql://grocery:local-grocery-only@127.0.0.1:55434/grocery \
  DEPLOY_VERSION="$RELEASE_SHA" \
  DJANGO_SECURE_SSL_REDIRECT=1 \
  DJANGO_SECURE_HSTS_SECONDS=31536000 \
  DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=1 \
  DJANGO_SECURE_HSTS_PRELOAD=1 \
  .venv/bin/python manage.py collectstatic --noinput
```

production storage는 WhiteNoise compressed manifest backend로 content-hashed filename을 만든다.
application과 exact release의 `staticfiles`를 한 immutable 단위로 보존하고, 대표 CSS가 hashed
URL에서 `200`, 올바른 content type과 immutable cache header로 응답하는지 traffic 전 확인한다.
`collectstatic --clear`는 공유 자산 삭제 위험 때문에 사용하지 않는다.

build artifact에는 application source·template, migration, lockfile, 생성된 static files, exact
`RELEASE_SHA`, `THIRD_PARTY_NOTICES.md`와 platform packaging 방식이 요구하는 runtime dependency
license·notice bundle만 명시적 allowlist로 넣는다. artifact 내부 notice와 실제 locked runtime을
대사한다. browser evidence는 Git에 추적되지만 deployment artifact allowlist에서는 제외한다.
`.env.local`, backup, test database와 cache directory도 포함하지 않는다. platform packaging이 이
allowlist와 bundled notice 검사를 구현·검증하기 전에는 traffic을 열지 않는다.

## Database preflight와 migration

production database 생성·credential 주입과 아래 schema 변경 실행은 사람 checkpoint다.
배포 직전 managed backup/PITR checkpoint가 성공했고 복구 가능한지 먼저 확인한다. local dump를
production backup의 대체물로 사용하지 않는다.

production `DATABASE_URL`은 TLS certificate와 hostname을 검증하는 `verify-full` 동등 설정 및
승인된 CA 경로를 사용해야 한다. 현재 repository는 특정 managed PostgreSQL CA를 제공하거나 이
platform 계약을 자동 검증하지 않으므로, 실제 connection과 server identity evidence가 없으면
migration과 traffic 공개를 중단한다.

새 release 환경에서 application 전환 전에 다음 순서로 확인한다.

```sh
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py showmigrations --plan
.venv/bin/python manage.py migrate --plan
.venv/bin/python manage.py check
```

plan을 exact release의 migration 파일과 대조한다. 현재 `0001`~`0010`은 새 model, constraint와
trigger를 만드는 방향이다. `0010`은 고정 legacy source row의 date-precision
`state_changed_at`·`rights_confirmed_at` 원본을 수정·삭제하지 않고, 그 원본에만 적용되는
append-only correction을 추가한다. 정확한 live 관측 시각은 보존되지 않았다. effective
`2026-08-30T02:23:44Z`는 commit
`d23e5707e1fc3bf6e032d459b149b946b0451e00`의 durable gate-decision recorded-at upper bound이지
정확한 관측 시각이 아니다. correction
`49143c27-d2dd-5fbd-b1dc-4aa3cc002fab`의 insert는 base·chronology trigger로 검증되고 update/delete는
DB에서 거부된다. bootstrap·review·inspection은 effective helper를 쓰며 새 DB는 처음부터 exact
effective 값으로 생성되어 correction row가 없다. 기존 canonical row를 삭제·변환하는 contract
migration은 없다.
그래도 production data 규모에서 DDL lock과 실행 시간을 복제 DB로 측정하고, 예상 밖 operation이
보이면 중단한다.

승인된 maintenance/change window에서만 forward migration을 실행한다.

```sh
.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py migrate --check
.venv/bin/python manage.py check --deploy --fail-level WARNING
```

`migrate --noinput`은 현재 release에서 forward-only이지만 실제 schema를 변경한다. 실패하면
traffic을 새 application으로 전환하지 않고 이전 application을 유지한다. 부분 적용 상태는
`showmigrations --plan`과 database audit로 확인하며 임의 `--fake`나 reverse migration으로
감추지 않는다.

첫 빈 database는 아직 active publication이 없으므로 readiness가 실패하는 것이 정상이다.
production reviewer/publisher 경계가 준비되지 않은 상태에서 readiness를 통과시키려고 local
actor를 복사하거나 pointer를 직접 수정하지 않는다.

## WSGI·static·traffic 전환

`manage.py runserver`는 production에서 사용하지 않는다. `make serve`는 고정된 local bind와
worker 설정을 가진 smoke 명령이므로 platform sizing을 대신하지 않는다. production process는
승인된 platform 설정으로 Gunicorn을 실행한다.

```sh
exec .venv/bin/gunicorn config.wsgi:application \
  --bind "$GUNICORN_BIND" \
  --workers "$GUNICORN_WORKERS" \
  --threads "$GUNICORN_THREADS"
```

WhiteNoise middleware는 Django WSGI process 안에서 manifest가 가리키는 hashed static을 제공한다.
대표 HTML이 hashed CSS URL을 참조하고 해당 asset이 올바른 media type과 immutable cache header로
응답하는지 smoke한다. HTML·health의 `Cache-Control: no-store`와 static의 immutable cache는
서로 다른 의도적 계약이다. CDN을 추가한다면 WhiteNoise 결과 앞의 선택적 delivery layer로만
두고 새 cache purge·rollback 검증을 거친다.

platform proxy가 HTTPS를 종료할 때 forwarding header 신뢰 범위와 Django HTTPS 인식을 별도
검증한다. `DJANGO_TRUST_X_FORWARDED_PROTO=1`은 platform이 외부 client가 해당 header를 주입하지
못하게 제거·재작성하고 단일 trusted proxy contract를 보장할 때만 켠다. 기본값은 꺼짐이며,
platform 선택 뒤 검증 없이 proxy 구성을 추정하지 않는다.

traffic 전환 순서는 다음과 같다.

1. 새 release를 traffic 없이 시작한다.
2. 새 release의 liveness를 확인한다.
3. database migration과 active publication을 포함한 readiness를 확인한다.
4. freshness를 별도 확인한다. stale은 last-known-good가 제공됨을 뜻하며 readiness와 다르다.
5. catalog와 승인된 `SMOKE_SERIES_PATH`를 query string 없이 읽어 source date, unit, coverage와
   publication fact-set header가 한 revision인지 확인한다.
6. platform의 atomic traffic switch를 사람이 승인·실행한다.
7. 전환 뒤 같은 검사를 반복하고 이전 release를 즉시 rollback 가능한 상태로 유지한다.

platform이 아직 선택되지 않았으므로 실제 traffic switch와 release rollback CLI는 이 문서에
꾸며내지 않는다. 선택 후 vendor의 exact command, account, application ID, timeout과 rollback
동작을 별도 승인된 보충 절차로 기록해야 한다.

### local 고정 부하 profile

candidate의 public read 성능은 production-like `DEBUG=False` Gunicorn을 고정 loopback Compose
DB에 연결해 측정한다. 이 검사는 신뢰된 local peer만 대상으로 하며 production capacity,
network TLS·proxy latency 또는 managed PostgreSQL sizing을 대신하지 않는다. local HTTP 측정을
위해 이 process에서만 SSL redirect를 끄고, 별도 `check --deploy --fail-level WARNING`에서는
production transport contract를 검증한다.

첫 terminal에서 다음 exact candidate process를 시작한다.

```sh
env \
  DJANGO_DEBUG=0 \
  ADMIN_ENABLED=0 \
  QA_STATE_PREVIEWS_ENABLED=0 \
  CONTROL_PLANE_OPERATIONS_ENABLED=0 \
  DJANGO_SECRET_KEY="$LOCAL_ASSURANCE_DJANGO_SECRET" \
  DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost \
  DJANGO_CSRF_TRUSTED_ORIGINS=https://candidate.invalid \
  DATABASE_URL=postgresql://grocery:local-grocery-only@127.0.0.1:55434/grocery \
  DEPLOY_VERSION="$RELEASE_SHA" \
  DJANGO_SECURE_SSL_REDIRECT=0 \
  DATABASE_CONN_MAX_AGE=60 \
  .venv/bin/gunicorn config.wsgi:application \
    --bind 127.0.0.1:8000 \
    --workers 2 \
    --threads 4 \
    --access-logfile /dev/null
```

두 번째 terminal에서 `inspect_recent_publication`으로 active hash·version을 고정한 뒤 실행한다.

```sh
.venv/bin/python scripts/http_load_profile.py \
  --port 8000 \
  --detail-id "$PUBLIC_SERIES_ID"
```

인수를 위한 명령에는 `--profile`이나 `--duration-seconds` override를 주지 않는다. 고정 계약은
900초, 10 requests/s, 총 9,000개, catalog·list·search 6,300개와 detail 2,700개다. 20개의 고정
logical virtual-user session이 모두 참여하고 request index를 round-robin으로 나눈다. 실제
in-flight 요청 peak는 논리 사용자 수와 별도로 측정하며 20을 넘을 수 없다. queue를 포함한
end-to-end p95가 500 ms 이하, 5xx가 0.5% 미만, 오류 0,
publication fact-set header 단일값과 elapsed 900~903초를 모두 만족해야 통과한다. effective
paced deadline 대비 schedule jitter p95가 100 ms 이하이고, nominal 제출 cadence는
100 ms, bounded recovery 제출 간격 최솟값은 90 ms 이상이며 catch-up burst가 0이어야 한다.
max jitter는 관측값으로
receipt에 남기되 이후 요청에 반복 전가하지 않는다. profile 중 lifecycle·migration·backup처럼
DB state를 바꾸는 작업을 병행하지 않는다.
종료 직후 `inspect_recent_publication`을 다시 실행해 active revision·version·fact-set hash가 시작
receipt와 같은지 대사한다.

## Health와 freshness

health URL에는 credential이나 query string을 붙이지 않는다. 아래 조회는 고정된 작은 JSON만
반환하며 `Cache-Control: no-store`를 사용한다.

```sh
curl --proto '=https' --tlsv1.2 --fail --silent --show-error \
  --connect-timeout 3 --max-time 10 "$HEALTH_BASE_URL/health/live"
curl --proto '=https' --tlsv1.2 --fail --silent --show-error \
  --connect-timeout 3 --max-time 10 "$HEALTH_BASE_URL/health/ready"
curl --proto '=https' --tlsv1.2 --fail --silent --show-error \
  --connect-timeout 3 --max-time 10 "$HEALTH_BASE_URL/health/freshness"
```

- `/health/live`: Django process가 응답하면 성공한다. DB와 publication을 검사하지 않는다.
- `/health/ready`: DB 연결, migration currency와 sealed active publication을 검사한다. current와
  stale publication 모두 last-known-good read가 가능하므로 성공할 수 있다.
- `/health/freshness`: active publication이 current일 때만 성공한다. stale 또는 unavailable은
  실패 상태이며 operator alert 대상이다.

platform liveness는 process restart 판단에, readiness는 traffic 편입 판단에 사용한다.
freshness 실패만으로 application을 재시작하거나 승인 pointer를 자동 변경하지 않는다.

scheduler에서도 동일 freshness 경계를 확인할 수 있다.

```sh
.venv/bin/python manage.py check_recent_publication_freshness
```

성공은 fixed `CURRENT` receipt를 출력한다. stale, unavailable 또는 검사 실패는 각각 고정
`RECENT_PUBLICATION_FRESHNESS_*` code와 non-zero exit를 반환한다. scheduler는 stdout의 raw
재가공이나 exception text 대신 exit status와 fixed code만 alert에 연결한다.

현재 local evidence에서 active artifact의 마지막 source 확인 시각은
`2026-08-30T04:00:48.696744Z`다. 36시간 다음 확인 경계는
`2026-08-31T16:00:48.696744Z`(`2026-09-01 01:00:48 KST`)이며, 이 시각은 배포 시점이나
production scheduler 성공을 뜻하지 않는다. 경계 전에 새 실패 attempt가 있거나 경계를 넘으면
freshness contract에 따라 다시 확인하고 alert를 판정한다.

## 수집부터 공개까지

### 역할 분리와 자동화 한계

ingestion 성공은 review, seal 또는 activation 성공이 아니다. scheduler는 ingestion까지만
실행하고 자동 approve·seal·activate를 연결하지 않는다. source 실패, parse 실패 또는 새
candidate는 현재 pointer를 바꾸지 않는다.

active A-path `SourceConfiguration`은 `schedule_execution_mode=PLATFORM_SINGLETON`,
`schedule_interval_hours=24`를 기록한다. production platform scheduler는 성공 여부와 관계없이
인접한 scheduled start 사이를 24시간보다 길게 두지 않고, 이전 실행이 남아 있으면 새 실행을
겹치거나 catch-up burst로 만들지 않는다. bounded attempt가 끝난 뒤 fixed exit·audit를 alert에
연결한다. 이 값은 자동 review·seal·activation 권한이 아니며 36시간 freshness 경계 전 확인과
운영 대응 시간을 확보하기 위한 최대 cadence 계약이다.

production scheduler는 중첩 실행을 막는 singleton job으로 다음 명령만 실행한다.

```sh
.venv/bin/python manage.py ingest_kamis_recent
```

`KAMIS_API_KEY`는 worker process 안에서만 managed secret으로 주입한다. command argument, URL,
shell trace, log 또는 receipt로 전달하지 않는다. 실패 시 key를 확인하려고 `env`, `printenv`,
`echo`, shell tracing 또는 exception traceback을 사용하지 않는다.

성공 receipt의 `parse_run_id`, row count와 replay 상태는 민감하지 않은 audit locator다. reviewer는
그 parse run의 typed identity, coverage, unit, missing reference, row counts, reconciliation hash와
source rights 상태를 별도 승인된 private review surface에서 확인한다. 이 repository에는
production MFA review surface가 아직 없다.

### local Phase 0 lifecycle rehearsal

아래 형태는 `DEBUG=True`, Admin·QA preview·production control plane 비활성 환경의 local
rehearsal이다. 각 명령에 이 안전 전제를 명시하며, `DEBUG` 검사를 우회하지 말고
disposable/local database에서만 lifecycle과 rollback을 재현한다.

먼저 fixed non-login local actor를 준비한다.

```sh
env DJANGO_DEBUG=1 ADMIN_ENABLED=0 QA_STATE_PREVIEWS_ENABLED=0 \
  CONTROL_PLANE_OPERATIONS_ENABLED=0 \
  DATABASE_URL=postgresql://grocery:local-grocery-only@127.0.0.1:55434/grocery \
  .venv/bin/python manage.py bootstrap_local_phase0_operator
```

검수 evidence 자체는 private 경계에 두고 그 canonical bytes의 SHA-256만 전달한다. UUID와 hash는
새 logical action마다 외부의 안전한 operator tooling으로 생성하며 command substitution으로
secret을 만들거나 출력하지 않는다.

```sh
env DJANGO_DEBUG=1 ADMIN_ENABLED=0 QA_STATE_PREVIEWS_ENABLED=0 \
  CONTROL_PLANE_OPERATIONS_ENABLED=0 \
  DATABASE_URL=postgresql://grocery:local-grocery-only@127.0.0.1:55434/grocery \
  .venv/bin/python manage.py approve_recent_generation \
  --parse-run-id "$PARSE_RUN_ID" \
  --decision-id "$REVIEW_DECISION_ID" \
  --acceptance-evidence-sha256 "$REVIEW_EVIDENCE_SHA256"

env DJANGO_DEBUG=1 ADMIN_ENABLED=0 QA_STATE_PREVIEWS_ENABLED=0 \
  CONTROL_PLANE_OPERATIONS_ENABLED=0 \
  DATABASE_URL=postgresql://grocery:local-grocery-only@127.0.0.1:55434/grocery \
  .venv/bin/python manage.py seal_recent_publication \
  --decision-id "$REVIEW_DECISION_ID" \
  --public-copy-revision "$PUBLIC_COPY_REVISION"

env DJANGO_DEBUG=1 ADMIN_ENABLED=0 QA_STATE_PREVIEWS_ENABLED=0 \
  CONTROL_PLANE_OPERATIONS_ENABLED=0 \
  DATABASE_URL=postgresql://grocery:local-grocery-only@127.0.0.1:55434/grocery \
  .venv/bin/python manage.py transition_recent_publication \
  --operation ACTIVATE \
  --operation-id "$PUBLICATION_OPERATION_ID" \
  --acceptance-evidence-sha256 "$PUBLICATION_ACCEPTANCE_EVIDENCE_SHA256" \
  --expected-version "$EXPECTED_PUBLICATION_VERSION" \
  --expected-current-revision "$EXPECTED_CURRENT_REVISION_ID" \
  --target-revision "$PUBLICATION_REVISION_ID"
```

approve는 validated generation과 exact reconciliation을, seal은 latest approval과 immutable fact
set을, activation은 sealed target과 optimistic current state를 다시 검증한다. 같은 logical
operation의 uncertain retry에만 같은 operation UUID를 재사용한다. 다른 target·evidence·expected
state로 UUID를 재사용하지 않는다.

### production private operation job boundary

repository는 HTTP Admin이나 로그인 화면을 production control plane으로 제공하지 않는다.
사람이 승인한 platform의 외부 MFA·IAM private job만 다음 command를 실행할 수 있어야 하고,
job별 role-specific database credential·grant와 immutable audit를 별도로 구성한다.
`CONTROL_PLANE_OPERATIONS_ENABLED=1`은 accident-prevention flag일 뿐 인증이 아니며 web,
ingestion과 scheduler process에서는 항상 `0`이다.

actor provisioning credential은 승인된 change에서 한 번만 다음 명령을 실행한다. 두 actor는
로그인할 수 없고 PII·staff·superuser·group이 없으며 reviewer와 publisher가 각각 recent와
historical에서 자기 역할에 해당하는 Django permission만 가진다. 기존 actor에 drift가 있으면
둘 중 어느 것도 부분 수정하지 않는다.

```sh
.venv/bin/python manage.py bootstrap_control_plane_actors \
  --expected-release-sha "$RELEASE_SHA"
```

외부 MFA job은 실행 release와 같은 exact SHA를 모든 write command에 전달한다. reviewer job은
approve만, publisher job은 seal과 transition만 실행한다. production reason code는 local
rehearsal reason과 분리된다.

```sh
.venv/bin/python manage.py approve_recent_generation \
  --parse-run-id "$PARSE_RUN_ID" \
  --decision-id "$REVIEW_DECISION_ID" \
  --acceptance-evidence-sha256 "$REVIEW_EVIDENCE_SHA256" \
  --expected-release-sha "$RELEASE_SHA"

.venv/bin/python manage.py seal_recent_publication \
  --decision-id "$REVIEW_DECISION_ID" \
  --public-copy-revision "$PUBLIC_COPY_REVISION" \
  --expected-release-sha "$RELEASE_SHA"

.venv/bin/python manage.py transition_recent_publication \
  --operation ACTIVATE \
  --operation-id "$PUBLICATION_OPERATION_ID" \
  --acceptance-evidence-sha256 "$PUBLICATION_ACCEPTANCE_EVIDENCE_SHA256" \
  --expected-version "$EXPECTED_PUBLICATION_VERSION" \
  --expected-current-revision "$EXPECTED_CURRENT_REVISION_ID" \
  --target-revision "$PUBLICATION_REVISION_ID" \
  --expected-release-sha "$RELEASE_SHA"
```

### historical collection review와 첫 publication

`ingest_kamis_monthly`, `ingest_kamis_regional_daily`, `ingest_kamis_market_daily`는 각각 사람이
승인한 exact source configuration·code manifest·partition 범위로 실행한다. 각 명령은 하나의
완전한 `VALIDATED` collection에서 멈춘다. scheduler나 같은 job에서 review, seal 또는 activation을
이어 실행하지 않는다. code manifest와 cross-source series·region·market mapping 등록은 독립적인
사람 검토 checkpoint이며 command가 새 mapping을 추측하지 않는다.

외부 MFA reviewer job은 세 collection을 각각 아래 명령으로 승인한다. 첫 decision의
`--supersedes-decision`은 생략하고, 재검수 decision만 현재 tail UUID를 명시한다. evidence 원문은
private 경계에 두고 canonical SHA-256만 전달한다.

```sh
.venv/bin/python manage.py approve_historical_collection \
  --collection-id "$HISTORICAL_COLLECTION_ID" \
  --decision-id "$HISTORICAL_REVIEW_ID" \
  --reconciliation-report-sha256 "$RECONCILIATION_REPORT_SHA256" \
  --acceptance-evidence-sha256 "$REVIEW_EVIDENCE_SHA256" \
  --expected-release-sha "$RELEASE_SHA"
```

세 current APPROVE review가 준비된 뒤 publisher job이 별도 change에서 봉인하고, 결과 revision을
검사한 뒤 다시 별도 change에서 exact current/version CAS로 활성화한다. 이 두 명령을 하나의
자동 chain으로 묶지 않는다.

```sh
.venv/bin/python manage.py seal_historical_publication \
  --monthly-review-id "$MONTHLY_REVIEW_ID" \
  --regional-review-id "$REGIONAL_REVIEW_ID" \
  --market-review-id "$MARKET_REVIEW_ID" \
  --compatibility-report-sha256 "$COMPATIBILITY_REPORT_SHA256" \
  --expected-release-sha "$RELEASE_SHA"

.venv/bin/python manage.py transition_historical_publication \
  --operation ACTIVATE \
  --operation-id "$HISTORICAL_PUBLICATION_OPERATION_ID" \
  --acceptance-evidence-sha256 "$HISTORICAL_PUBLICATION_EVIDENCE_SHA256" \
  --expected-version "$EXPECTED_HISTORICAL_VERSION" \
  --expected-current-revision "$EXPECTED_HISTORICAL_REVISION_OR_NONE" \
  --target-revision "$HISTORICAL_PUBLICATION_REVISION_ID" \
  --expected-release-sha "$RELEASE_SHA"
```

최초 activation은 version `0`과 current literal `NONE`을 사용한다. 같은 logical retry에만 같은
decision·operation UUID를 재사용한다. `ACTIVATE`는 current review tail을 요구하지만,
`ROLLBACK`은 activation history에서 previously-current였음이 확인된 sealed last-known-good를
대상으로 한다. production actor provisioning, 첫 review·seal·activation, traffic 전환과 rollback은
모두 사람 전용 checkpoint다.

browser evidence fixture는 production command가 아니다. `DEBUG=1`, Admin 비활성, QA preview
활성, loopback PostgreSQL, `grocery_vnext_`로 시작하는 비어 있는 DB를 모두 확인한 뒤에만 실행된다.
핵심 source·domain·publication row가 하나라도 있으면 실패 폐쇄한다.

```sh
DJANGO_DEBUG=1 ADMIN_ENABLED=0 QA_STATE_PREVIEWS_ENABLED=1 \
  DATABASE_URL="$DISPOSABLE_VNEXT_DATABASE_URL" \
  .venv/bin/python scripts/build_vnext_browser_fixture.py
```

`PUBLIC_COPY_REVISION`은 현재 `ko-v1`, `ko-v2`, `ko-v3` 또는 `ko-v4`만 허용한다. `ko-v3`는
초록장부 frontend redesign copy이고 `ko-v4`는 historical consumer 확장 copy다. 기존 revision
row를 수정하지 않고 새 sealed revision으로만 만든다. production의 `ko-v4` seal·activation,
traffic 전환과 rollback 결정은 사람 checkpoint다. 첫 activation은 authoritative
inspection의 `version=0`, current literal `NONE`을 사용한다. production receipt는 actor,
release SHA와 evidence hash를 출력하지 않는다. ReviewDecision·Activation은 actor를 DB audit에
보존하지만 seal invoker는 revision row에 저장되지 않으므로 외부 MFA job audit와 change record가
필수다. actor bootstrap, IAM, grant와 첫 production publication은 사람 checkpoint다.

## Publication rollback과 withdraw

publication rollback은 application rollback과 별개다. revision row를 수정하거나 삭제하지
않고 이전에 current였던 sealed revision을 대상으로 새 append-only `ROLLBACK` activation을
추가한다.
withdraw는 권리·identity·공개 안전성이 더 이상 보장되지 않을 때 current pointer를 비우는 새
activation이다.

실행 직전에 승인된 read-only 운영 화면에서 channel의 exact current revision과 version을 다시
읽는다. 과거 receipt나 기억한 값을 사용하지 않는다. 두 값이 예상과 다르면 concurrent change로
간주하고 실패 폐쇄한다.

현재 repository의 authoritative read-only 명령은 다음과 같다.

```sh
.venv/bin/python manage.py inspect_recent_publication
```

이 명령은 PostgreSQL `REPEATABLE READ, READ ONLY` snapshot에서 activation history, sealed revision,
canonical entry 집합과 fact-set hash를 다시 계산한다. `AVAILABLE` receipt의 `version`,
`current_revision_id`, 마지막 activation을 바로 다음 transition의 expected state로 사용한다.
첫 빈 channel 또는 withdraw 상태는 current revision을 literal `NONE`으로 출력한다. `ERROR`나
non-zero exit이면 transition하지 않는다.

local rehearsal rollback 명령은 다음과 같다.

```sh
env DJANGO_DEBUG=1 ADMIN_ENABLED=0 QA_STATE_PREVIEWS_ENABLED=0 \
  CONTROL_PLANE_OPERATIONS_ENABLED=0 \
  DATABASE_URL=postgresql://grocery:local-grocery-only@127.0.0.1:55434/grocery \
  .venv/bin/python manage.py transition_recent_publication \
  --operation ROLLBACK \
  --operation-id "$PUBLICATION_OPERATION_ID" \
  --acceptance-evidence-sha256 "$PUBLICATION_ACCEPTANCE_EVIDENCE_SHA256" \
  --expected-version "$EXPECTED_PUBLICATION_VERSION" \
  --expected-current-revision "$EXPECTED_CURRENT_REVISION_ID" \
  --target-revision "$ROLLBACK_TARGET_REVISION_ID"
```

local rehearsal withdraw 명령은 target revision을 전달하지 않는다.

```sh
env DJANGO_DEBUG=1 ADMIN_ENABLED=0 QA_STATE_PREVIEWS_ENABLED=0 \
  CONTROL_PLANE_OPERATIONS_ENABLED=0 \
  DATABASE_URL=postgresql://grocery:local-grocery-only@127.0.0.1:55434/grocery \
  .venv/bin/python manage.py transition_recent_publication \
  --operation WITHDRAW \
  --operation-id "$PUBLICATION_OPERATION_ID" \
  --acceptance-evidence-sha256 "$PUBLICATION_ACCEPTANCE_EVIDENCE_SHA256" \
  --expected-version "$EXPECTED_PUBLICATION_VERSION" \
  --expected-current-revision "$EXPECTED_CURRENT_REVISION_ID"
```

withdraw 후 readiness와 freshness가 unavailable이 되고 public catalog에 공개 자료가 없으며
detail이 숨겨지는지 확인한다. 이는 장애가 아니라 안전한 철회 상태일 수 있으므로 운영 change
record와 alert suppression window를 함께 남긴다.

production rollback·withdraw는 위 publisher private job에서 같은 명령에
`--expected-release-sha "$RELEASE_SHA"`를 추가해 실행한다. external MFA·IAM, 역할별 credential,
default-off enable flag, exact release lock과 authoritative optimistic state 중 하나라도 없으면
실패해야 한다. `DEBUG`를 켜거나 DB pointer를 직접 update하지 않는다.

## Application rollback

application rollback은 database와 publication을 독립적으로 유지한 채 이전 immutable release와
그 static directory로 traffic을 되돌리는 작업이다.

1. 새 traffic 편입을 중단하고 `RELEASE_SHA`, request ID, health 상태와 최초 이상 시각만
   기록한다. query, user input 또는 secret은 기록하지 않는다.
2. migration이 이미 성공했다면 schema를 그대로 둔다. 현재 migration은 additive 방향이지만,
   `PREVIOUS_RELEASE_SHA`가 최신 schema를 읽을 수 있다고 release별로 실제 검증한 경우에만
   rollback한다.
3. platform의 승인된 atomic release switch로 application과 static을 함께
   `PREVIOUS_RELEASE_SHA`에 되돌린다.
4. liveness, readiness, freshness와 catalog/detail smoke를 다시 수행한다.
5. publication 내용 자체가 문제면 application rollback과 별도로 rollback 또는 withdraw한다.

production에서 migration을 역방향 실행하여 code rollback을 맞추지 않는다. 이전 code가 최신
schema와 호환되지 않으면 traffic을 전환하지 말고 중단한다. database 복구가 필요하면 기존
database를 덮어쓰지 않고 managed PITR로 새 instance를 만든 뒤 검증·승인된 connection switch를
수행한다.

local application rollback rehearsal에서 실제로 검증한 `PREVIOUS_RELEASE_SHA`는
`d6d7d08c9de9a78eb597fec6e232b0e2d24a1ec1`다. 이는 local 호환성 evidence의 고정 target이지,
향후 production vendor rollback 명령이나 실제 traffic 전환을 승인한 값이 아니다.

## Local backup·restore rehearsal

`scripts.postgres_backup_restore`는 `docker --host unix:///var/run/docker.sock`의 exact local
socket만 사용한다. ambient Docker context·host와 `DATABASE_URL`을 읽거나 전달하지 않는다.
고정 Compose project·`db` service label로 실행 중인 container를 하나만 발견하고, immutable
container ID·image·project/service identity를 한 invocation 동안 pin한 뒤 모든 `docker exec`에
그 ID를 직접 사용한다. container가 교체·중지되거나 identity가 달라지면 실패 폐쇄하므로
production database나 다른 Compose service를 대상으로 사용할 수 없다. PostgreSQL custom dump,
manifest·dump hash, migration inventory, 모든 public table row count, active revision, canonical
publication hash와 activation chain을 대사한다.
이 도구는 sealed active publication이 있는 candidate DB만 허용하며 빈 DB나 withdraw 상태의
범용 backup 도구가 아니다.

`BACKUP_OUTPUT_DIRECTORY`는 repository 밖의 기존 absolute owner-controlled directory여야 하고
symlink가 아니어야 한다. 도구는 directory와 parent의 owner·write 경계를 검사하고 열린 directory
FD에 identity를 고정한다. 새 generated backup directory는 `0700`, 파일은 `0600`이며 dump는 같은
열린 FD를 checksum·검사·restore까지 사용한다. backup은 source DB를 변경하지 않지만 새 private
directory와 파일을 만든다.

```sh
.venv/bin/python -m scripts.postgres_backup_restore backup \
  --output-dir "$BACKUP_OUTPUT_DIRECTORY"
```

성공 receipt가 가리키는 exact generated directory를 `BACKUP_DIRECTORY`로 선택한다. 실제 이름은
`BACKUP_OUTPUT_DIRECTORY/postgres-backup-<backup UUID>`이며 output root 자체를 restore 입력으로
사용하지 않는다. 같은 receipt의 `manifest_sha256`은 secret이 아닌 out-of-band integrity 값이다.
그 값을 변경 없이 `BACKUP_MANIFEST_SHA256`에 전달해야 하며, manifest 파일에서 다시 계산한 값을
expected 값으로 쓰면 독립 대사가 되지 않는다. 값이 누락되거나 형식·내용이 다르면 target 생성
전에 실패한다. `RESTORE_DATABASE_NAME`은 source와 다른, 존재하지 않는 disposable name이어야
한다. `grocery_restore_`로 시작하는 소문자·숫자·underscore의 bounded 식별자만 허용하며,
restore는 새 target database를 만들고 source database를 변경하지 않는다.

```sh
.venv/bin/python -m scripts.postgres_backup_restore restore \
  --backup-dir "$BACKUP_DIRECTORY" \
  --expected-manifest-sha256 "$BACKUP_MANIFEST_SHA256" \
  --target-database "$RESTORE_DATABASE_NAME"
```

성공 receipt에서 row counts, migration inventory와 publication contract가 모두 일치하는지
확인한다. 그 뒤 별도 process의 `DATABASE_URL`을 격리 target에 managed 방식으로 연결하여 다음을
실행한다.

```sh
env DATABASE_URL="postgresql://grocery:local-grocery-only@127.0.0.1:55434/$RESTORE_DATABASE_NAME" \
  .venv/bin/python manage.py migrate --check
env DATABASE_URL="postgresql://grocery:local-grocery-only@127.0.0.1:55434/$RESTORE_DATABASE_NAME" \
  .venv/bin/python manage.py check
```

복원 target으로 시작한 local application에서 readiness와 대표 catalog/detail도 확인한다.
원본 Compose DB를 가리키고 있지 않은지 database name을 먼저 read-only로 확인한다.

target 생성 뒤 restore·inventory·canonical publication 검사 중 어느 단계가 실패해도 도구는 같은
invocation이 만든 exact target만 identity-pin한 container에서 자동 삭제하고 실제 부재를 다시
확인한다. pre-existing target, source `grocery`, 다른 이름이나 다른 container는 삭제하지 않는다.
자동 cleanup 자체가 실패하거나 target 부재를 증명하지 못하면 성공 receipt를 내지 않고 별도 fixed
cleanup failure로 중단해 사람이 조사한다. 성공한 restore target과 실패한 backup directory는
evidence 검토 전 자동 삭제하지 않는다. `docker compose down -v`는 source volume까지 삭제하므로
cleanup으로 사용하지 않는다.

local dump는 application-level 암호화를 제공하지 않는다. storage volume의 암호화가 별도로
증명되지 않았다면 민감 backup으로 취급하고 owner-only 경계와 명시적 retention을 적용한다.

## Production backup, PITR와 복구 gap

production 공개 전 managed PostgreSQL에서 다음을 실제로 증명해야 한다.

- 암호화된 자동 backup과 WAL 기반 point-in-time recovery
- application, migration, backup·restore 역할의 분리와 감사
- backup retention, region·account 격리와 실패 alert
- 기존 database를 덮어쓰지 않는 새 instance restore
- migration inventory, row counts, audit chain, active revision, fact-set hash와 public read 대사
- 목표 `RPO 24시간`과 `RTO 4시간` 안의 timed rehearsal
- 분기별 restore rehearsal과 evidence retention

현재 candidate에는 production platform, encrypted scheduled backup, PITR, production restore,
backup failure structured event와 실제 RPO/RTO 측정이 없다. local Compose rehearsal을 이 항목의
통과로 기록하지 않는다. backup/PITR가 구성·복원 검증되지 않으면 production migration과
traffic 공개를 중단한다.

PITR는 항상 새 database instance로 복원하고 검증 후 connection을 전환한다. 기존 instance
삭제, in-place overwrite, retention 단축과 old backup 폐기는 파괴적 사람 checkpoint다.

## Structured log와 alert

application의 `grocery.audit` logger는 allowlist된 single-line JSON만 stdout에 보낸다. 허용
field는 timestamp, severity, message code, request ID, deploy version, command run ID와 lifecycle
ID·status·event뿐이다. arbitrary message, exception, query, response body, key와 user identity를
넣지 않는다. Django request/server logger는 application에서 버려지므로 platform proxy의 access
log도 query string, IP, User-Agent와 search term을 별도로 제거해야 한다.

다음 structured `message_code`를 즉시 또는 짧은 반복 window의 alert에 연결한다.

| code | 의미 | 초기 대응 |
|---|---|---|
| `health.readiness.unavailable` | DB, migration 또는 active publication read 불가 | traffic 편입 중단; DB·schema·pointer 분리 확인 |
| `health.freshness.unavailable` | active publication 또는 freshness 판단 불가 | 자동 publication 금지; 권리·pointer·attempt 확인 |
| `health.freshness.stale` | last-known-good는 있으나 새 확인 필요 | 공개본 유지; ingestion·review backlog 조사 |
| `public.catalog.unavailable` | catalog read 실패 | request ID로 DB/read 경계 확인; 반복 시 application rollback 검토 |
| `public.detail.unavailable` | detail read 실패 | request ID로 active revision membership/read 확인 |
| `ingest.source.start_failed` | source/audit 시작 실패 | DB와 source configuration 확인; pointer 유지 |
| `ingest.fetch.failed` | bounded fetch 실패 | HTTP class·quota·credential 상태를 redacted receipt로 확인 |
| `ingest.fetch.finalization_failed` | 실패 attempt 종료 기록 실패 | audit 불완전 incident로 즉시 escalation |
| `ingest.parse.start_failed` | parse audit 시작 실패 | artifact·DB 상태 확인; candidate 공개 금지 |
| `ingest.parse.failed` | schema·identity·unit·reconciliation 실패 또는 quarantine | 자동 retry·승인 금지; reviewer 조사 |
| `ingest.parse.finalization_failed` | parse 실패 상태 기록도 실패 | audit 불완전 incident로 즉시 escalation |

`ingest.fetch.started`, `ingest.fetch.succeeded`, `ingest.parse.started`,
`ingest.parse.resumed`, `ingest.parse.replay_started`, `ingest.parse.validated`와
`ingest.command.succeeded`는 정상 lifecycle evidence이며 단독 alert 대상이 아니다. 성공 없이
started만 남는 run, 연속 fetch 실패, quarantined lifecycle status 증가와 새 validation 부재를
platform 집계 규칙으로 경보한다.

freshness command의 non-zero `RECENT_PUBLICATION_FRESHNESS_*` code와 ingest command의 fixed
`INGEST_*` failure code도 scheduler alert에 연결한다. review·seal·transition은 현재 structured
log를 내지 않고 DB audit와 fixed CLI receipt만 남긴다. private job은
`CONTROL_PLANE_*`, `RECENT_PUBLICATION_INSPECTION_FAILED`와 non-zero exit를 인자나 원문 오류 없이
별도 platform audit·alert로 연결한다. local backup script도 structured logger에 연결되지 않으므로
production control plane과 backup platform은 non-zero job, DB audit gap과 backup failure alert를
추가해야 한다. actor/bootstrap·seal job audit 누락이나 ReviewDecision/Activation actor chain
불일치도 production traffic 차단 신호다.

유효한 `DEPLOY_VERSION`은 application JSON event에 자동 포함된다. production settings는 exact
40자 lowercase Git SHA가 없으면 시작을 거부한다. platform도 immutable release metadata로 같은
SHA를 보존해 application event와 ingress·runtime signal을 대조할 수 있어야 한다.

## Secret rotation

공통 금지 사항은 다음과 같다.

- secret을 command argument, URL, Git, fixture, receipt, log, screenshot 또는 ticket에 넣지 않음
- `env`, `printenv`, `echo`, shell tracing, exception repr 또는 길이·fragment로 값을 확인하지 않음
- 같은 secret을 web, ingestion, migration, database와 backup 역할 사이에 재사용하지 않음
- 새 secret 검증 전에 old secret을 폐기하지 않음

### KAMIS key

1. provider login·key 발급·폐기는 사람이 수행한다.
2. 새 값을 managed secret store의 새 version으로 넣고 ingestion worker에만 연결한다.
3. 새 worker process를 시작하여 값 자체를 출력하지 않고 한 번의 bounded ingestion을 실행한다.
4. fixed success receipt, attempt audit와 parse result를 확인한다. 자동 activate하지 않는다.
5. 성공 후 scheduler를 새 secret version으로 전환하고 old process를 종료한다.
6. propagation이 확인된 뒤 사람이 old provider credential을 폐기한다.

인증 실패나 provider propagation 지연은 source viability 실패나 fallback 전환으로 자동 판정하지
않는다. old key가 여전히 유효하면 last-known-good와 old worker를 유지하고 원인을 확인한다.

### Django secret

`DJANGO_SECRET_KEY`를 managed store에서 새 version으로 교체하고 rolling restart한다. 현재
settings는 이전 key fallback을 구성하지 않으므로 rotation은 기존 signed session·token을
무효화할 수 있다. Admin은 production에서 비활성 상태이며, 향후 MFA Admin을 열기 전에는 session
영향과 fallback 제거 시점을 별도 검토한다. 새 release health가 통과하기 전에 old version을
폐기하지 않는다.

### Database credential

role별 새 database credential을 만들고 managed `DATABASE_URL` reference를 새 version으로
전환한 뒤 process를 rolling restart한다. readiness, migration check, ingestion audit와 backup
job을 역할별로 검증하고 old connection이 drain된 뒤 old credential을 폐기한다. database URL
전체 또는 password를 어느 command에도 출력하지 않는다.

`make secret-check`는 local ignored `.env.local`의 Git 누출 검사이지 managed production secret
rotation 도구가 아니다.

## Incident별 안전한 대응

| 신호 | 유지할 것 | 금지할 자동 대응 |
|---|---|---|
| liveness 실패 | DB·publication을 그대로 두고 process/release 조사 | migration reverse, publication 전환 |
| readiness 실패 | 이전 release traffic과 last-known-good 유지 | pointer 직접 update, 빈 DB를 ready로 가장 |
| freshness stale | last-known-good 공개와 stale 표시 유지 | 미검수 candidate activate, 비공식 source 보충 |
| freshness unavailable | 권리·active pointer·DB를 분리 조사 | 오래된 publication이 안전하다고 추정 |
| fetch/parse 실패 | 현재 publication 유지, 새 attempt audit 보존 | partial generation 혼합, 무한 retry |
| public read 반복 실패 | request ID와 release를 대조하고 application rollback 검토 | 오류 원문·query logging |
| publication 오류 | optimistic state를 다시 읽고 별도 rollback/withdraw 승인 | revision 수정·삭제, 같은 UUID의 다른 action 재사용 |
| backup 실패 | migration·deploy 중단, 기존 verified backup 보존 | 실패 backup으로 restore, old backup 조기 삭제 |
| secret 노출 의심 | incident 보존, 새 version 검증 후 revoke | ticket/log에 노출값 복사, 무검증 즉시 폐기 |

source 권리 철회, identity·unit·coverage 불신 또는 잘못된 공개 사실은 단순 freshness 장애가
아니다. 사람이 public safety를 판단하고 필요하면 production publisher를 통해 `WITHDRAW`해야
한다.

## 배포 종료와 rollback 준비 확인

traffic 공개 전 change record에는 값 자체가 아니라 다음 evidence locator만 남긴다.

- exact `RELEASE_SHA`, clean pre-build Git 상태와 `git fsck`
- locked dependency build, `production-check`, migration plan과 forward migration 결과
- collectstatic artifact와 Gunicorn process revision
- liveness, readiness, freshness와 catalog/detail smoke 결과
- 현재 publication revision, pointer version과 마지막 activation ID
- backup/PITR checkpoint, restore rehearsal ID와 RPO/RTO 측정
- alert route와 담당자, known gap와 다음 검토 시각
- `PREVIOUS_RELEASE_SHA`, 이전 static artifact와 platform-specific rollback command locator

다음 항목이 남아 있으면 상태는 계속 **Phase 0 배포 직전**이다.

- production platform·PostgreSQL·secret store·domain·DNS가 미승인
- production MFA reviewer/publisher control이 없음
- production backup/PITR·restore와 RPO/RTO가 미검증
- platform-specific deploy·traffic switch·application rollback command가 미기록
- production alert delivery와 ingress log privacy가 미검증

실제 deploy, production publication, domain 전환과 rollback rehearsal은 각각 사람 승인 뒤에
수행하고, 그때 별도의 production 완료 evidence를 남긴다.
