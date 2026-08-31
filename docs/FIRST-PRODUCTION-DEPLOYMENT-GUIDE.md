# 초록장부 첫 Production 배포 가이드

기준일: 2026-08-31(KST)

이 문서는 배포를 처음 해보는 소유자가 초록장부를 실제 운영 환경에 올릴 때 사용할 화면 중심
가이드다. 실행 여부를 기록하는 문서는 [Production 배포 체크리스트](PRODUCTION-DEPLOYMENT-CHECKLIST.md),
정확한 보안·복구 명령의 기준은 [운영 런북](OPERATIONS-RUNBOOK.md)이다.

## 1. 먼저 알아둘 결론

초록장부의 기본 배포 조합은 다음으로 고정한다.

| 역할 | 선택 | 하는 일 |
|---|---|---|
| source repository | GitHub | 승인된 `main`과 exact release SHA 보관 |
| application host | Render 유료 Web Service | Django SSR과 Gunicorn 실행 |
| database | 유료 Render Postgres 18 | 승인 publication과 감사 기록 보관 |
| scheduler | Render Cron Jobs | 네 KAMIS ingestion command를 서로 독립 실행 |
| private operation | Render의 보호된 환경과 승인된 one-off job 경계 | migration·review·seal·activation 실행 |
| domain·DNS | Cloudflare DNS | 사용자 domain을 Render에 연결 |
| TLS | Render managed certificate, 필요 시 Cloudflare Full (strict) | browser부터 origin까지 HTTPS 유지 |
| alert | Render email/Slack + 별도 외부 monitor | deploy·job·health·freshness 실패 통지 |

```text
사용자
  └─ Cloudflare DNS
       └─ Render Web Service ── 읽기 ── Render Postgres 18

KAMIS API
  └─ Render Cron Jobs ── 정규화·후보 저장 ── Render Postgres 18

사람 reviewer / publisher
  └─ 2FA가 적용된 private operation ── 승인·봉인·활성화 ── Render Postgres 18
```

Vercel은 Python runtime과 Django 사용이 가능하지만 현재 Python runtime이 beta이고 cron도 function
실행 모델이다. 초록장부의 고정 Gunicorn process, 역할이 분리된 DB command, singleton ingestion,
사람 publication 경계를 첫 배포부터 옮기기에는 기본 선택으로 삼지 않는다. Cloudflare는 DNS·TLS·
proxy 역할로 사용하고 Django application 자체는 Workers로 옮기지 않는다.

## 2. 이 가이드의 표시

- **직접 수행**: 소유자가 dashboard에서 선택하거나 승인하는 단계
- **Codex와 수행**: repository 변경·검증이 필요한 단계
- **STOP**: 완료되기 전에는 다음 production 단계로 넘어가지 않는 관문
- **기록**: 값 자체가 아니라 service ID·release SHA·receipt 위치만 change record에 남기는 단계

secret, database URL, KAMIS key, deploy hook과 API token은 이 문서·Git·대화·screenshot·ticket에
붙여 넣지 않는다. dashboard의 secret 입력칸이나 승인된 password manager에서만 다룬다.

## 3. 현재 어디까지 준비됐는가

### 이미 준비된 것

- Django WSGI와 고정 Gunicorn dependency
- PostgreSQL migration `0001`~`0028`
- WhiteNoise hashed static delivery
- `/health/live`, `/health/ready`, `/health/freshness`
- recent·monthly·regional·market ingestion command
- recent와 historical의 독립 review·seal·activation command
- no-JS SSR, no-store, CSP `script-src 'none'`과 public fact-set header
- local actual KAMIS source-to-SSR smoke와 browser acceptance
- application rollback·recent publication rollback·PITR 원칙

### Production 전에 추가로 끝내야 하는 것 — STOP

- [ ] GitHub 필수 CI workflow 추가
- [ ] Render용 build/start wrapper 또는 `render.yaml`을 별도 검토 가능한 commit으로 추가
- [ ] Render proxy의 forwarded-proto 신뢰 계약을 공식 문서나 support 답변으로 고정
- [ ] Render ingress·request log가 query·IP·User-Agent·검색어 비수집 계약을 만족하는지 검증
- [ ] production DB 역할별 user·grant를 재현하는 provisioning 절차 추가
- [ ] historical authoritative repeatable-read inspection command 추가
- [ ] historical freshness monitor와 alert command 추가
- [ ] historical canonical backup restore 검사 추가
- [ ] `PREVIOUS_RELEASE_SHA`가 schema `0028`을 읽는 rollback rehearsal 수행
- [ ] production 전체 code manifest·mapping·coverage를 사람이 검수
- [ ] production historical route load profile을 승인하고 실행

이 목록이 남아 있는 동안 Render 계정과 비용을 검토할 수는 있지만 production database에 migration을
실행하거나 KAMIS 자료를 activate하거나 domain traffic을 열지 않는다.

## 4. 결제 전에 결정할 것

Render의 Free Web Service와 Free Postgres는 화면 확인용일 뿐 production에 사용하지 않는다. Free
web은 유휴 시 중지되고, Free Postgres는 30일 뒤 만료되며 managed backup이 없다.

다음 비용 항목을 Render Billing 화면에서 현재 가격으로 확인한다.

- [ ] Pro workspace 또는 요구되는 audit 기능을 제공하는 상위 plan
- [ ] 항상 실행되는 paid Web Service 1개
- [ ] paid Render Postgres 18 1개와 필요한 storage
- [ ] recent·monthly·regional·market Cron Job 4개
- [ ] migration·review·publisher operation을 위한 격리 실행 비용
- [ ] database 외부 TLS 경로를 쓸 경우 dedicated outbound IP
- [ ] external uptime/freshness monitor
- [ ] domain 등록·갱신 비용

Render workspace plan과 각 compute plan은 별도 과금 항목이다. 최종 결제 화면의 월 예상액을 확인하고
소유자가 승인하기 전에는 resource를 생성하지 않는다.

## 5. 계정과 보안 준비

### GitHub

1. **직접 수행**: GitHub 계정에서 2FA를 켠다.
2. **직접 수행**: repository의 기본 branch가 `main`인지 확인한다.
3. **직접 수행**: branch protection에서 직접 force-push와 삭제를 막는다.
4. **STOP**: 현재 repository에는 GitHub Actions가 없으므로 CI 추가 전 Render의
   `After CI Checks Pass`를 선택하지 않는다.

### Render

1. **직접 수행**: [Render](https://dashboard.render.com/) 계정을 만든다.
2. **직접 수행**: Account Settings에서 본인 2FA를 켠다.
3. **직접 수행**: Workspace Settings에서 workspace 전체 2FA를 강제한다.
4. **직접 수행**: GitHub 연결 권한은 이 repository에만 부여한다.
5. **직접 수행**: `chorokjangbu` project와 `Production` environment를 만든다.
6. **직접 수행**: `Production` environment를 Protected로 설정한다.
7. **직접 수행**: region은 사용자와 KAMIS 접근 지연을 고려해 우선 `Singapore`를 선택한다.
8. **기록**: workspace ID·project ID·environment ID만 change record에 남긴다.

### Cloudflare와 domain

1. **직접 수행**: 사용할 domain을 결정하고 상표·domain clearance를 사람이 확인한다.
2. **직접 수행**: Cloudflare 계정 2FA를 켠다.
3. **직접 수행**: domain의 DNS zone을 Cloudflare에 추가한다.
4. **STOP**: Render custom domain 검증 전에는 기존 production DNS를 변경하지 않는다.

## 6. Render Postgres 만들기

Render Dashboard에서 `New` → `Postgres`를 선택한다.

| 화면 항목 | 입력·선택 |
|---|---|
| Name | `chorokjangbu-production-db`처럼 production임을 구분할 이름 |
| Region | Web Service와 같은 `Singapore` |
| PostgreSQL Version | `18` |
| Instance type | Free가 아닌 paid plan |
| Storage | 첫 범위와 backup 여유를 포함한 승인 용량 |
| High Availability | 예산·허용 downtime을 검토해 결정 |

생성 직후 다음을 수행한다.

1. **직접 수행**: 생성된 server의 정확한 major·minor version을 확인한다. Render가 관리하는
   PostgreSQL `18` 선택을 repository 기준 `18.6`으로 추정하지 않는다.
2. **직접 수행**: Recovery 화면에서 PITR가 활성 상태인지 확인한다.
3. **직접 수행**: 첫 logical export를 만들 수 있는 plan인지 확인한다.
4. **직접 수행**: 외부 IP allowlist 기본값 `0.0.0.0/0`을 그대로 production 승인하지 않는다.
5. **Codex와 수행**: migration·web·ingestion·reviewer·publisher·backup 역할별 DB user와 grant
   provisioning을 추가하고 production clone에서 검증한다.
6. **STOP**: 정확한 server version의 호환성, 역할별 credential과 restore rehearsal이 끝나기 전
   migration을 실행하지 않는다.

### Database 연결 경로 선택 — STOP

현재 운영 계약은 hostname과 CA를 검증하는 `sslmode=verify-full` 동등 연결을 요구한다. Render의
same-region internal URL은 private network를 쓰지만 Render 안내상 TLS 경로가 아니다. 다음 중 하나를
보안 검토로 확정해야 한다.

#### 기본 권고: external TLS + 제한된 outbound IP

1. Render Production environment에 dedicated outbound IP set을 만든다.
2. Postgres external allowlist에는 그 고정 IP만 허용한다.
3. 각 role의 external database URL에 `sslmode=verify-full`을 사용한다.
4. certificate와 hostname 검증이 실제 연결에서 통과하는지 확인한다.
5. 임시 operator IP를 열었다면 migration·restore 검증 직후 제거한다.

#### 대안: internal private network

현재 `verify-full` 계약을 바꾸지 않고 임의로 선택하지 않는다. internal URL을 사용하려면 위협 모델,
Render private network 보장과 compensating control을 문서화하고 보안 결정을 별도 승인한다.

database URL 전체를 terminal history, build log 또는 완료 보고서에 출력하지 않는다.

## 7. Render에 Web Service 만들기

`New` → `Web Service` → GitHub repository
`seungwoo7050/audience-foundry-grocery-seasonality`를 선택한다.

| 화면 항목 | 값 |
|---|---|
| Name | `chorokjangbu-web` |
| Project / Environment | `chorokjangbu` / `Production` |
| Region | `Singapore` |
| Branch | `main` |
| Language | `Python 3` |
| Root Directory | 비워 둠 |
| Auto-Deploy | `Off` |
| Health Check Path | 첫 bootstrap은 `/health/live`, recent activation 뒤 `/health/ready` |
| Plan | Free가 아닌 승인된 paid plan |

첫 deploy는 active publication이 없어서 `/health/ready`가 실패한다. exact build artifact를 만들고
private operation을 준비하는 bootstrap 동안에는 custom domain을 연결하지 않고 paid service의
Maintenance Mode를 사용한다. recent activation이 끝나면 health path를 `/health/ready`로 바꾸고
같은 exact SHA를 다시 deploy한다. `/health/live` 상태로 사용자 traffic을 열지 않는다.

Render용 wrapper가 repository에 추가되기 전까지 아래 문자열을 dashboard에 확정 입력하지 않는다.
wrapper가 검토된 뒤 기대하는 동작은 다음과 같다.

```sh
uv sync --frozen --no-dev
env DEPLOY_VERSION="$RENDER_GIT_COMMIT" .venv/bin/python manage.py collectstatic --noinput
```

```sh
env DEPLOY_VERSION="$RENDER_GIT_COMMIT" \
  .venv/bin/gunicorn config.wsgi:application \
  --bind "0.0.0.0:$PORT" --workers 2 --threads 4 --access-logfile /dev/null
```

`RENDER_GIT_COMMIT`은 Render가 제공하는 exact deploy commit SHA다. application의
`DEPLOY_VERSION`에는 이 40자 SHA가 들어가야 한다. migration을 build command나 web start command에
붙이지 않는다.

## 8. 환경변수 나누기

환경변수는 한 묶음을 모든 service에 공유하지 않는다. 특히 web process에는 KAMIS key를 절대
넣지 않는다.

### Web Service

| 이름 | 설정 원칙 |
|---|---|
| `PYTHON_VERSION` | `3.14.7` |
| `UV_VERSION` | `0.12.6` |
| `DJANGO_DEBUG` | `0` |
| `ADMIN_ENABLED` | `0` |
| `QA_STATE_PREVIEWS_ENABLED` | `0` |
| `CONTROL_PLANE_OPERATIONS_ENABLED` | `0` |
| `DJANGO_SECRET_KEY` | Render secret generator로 만든 50자 이상 값 |
| `DJANGO_ALLOWED_HOSTS` | 현재 Render hostname과 승인 custom domain만 comma로 구분 |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | 같은 host의 `https://` origin만 comma로 구분 |
| `DATABASE_URL` | web read role의 승인 URL |
| `DATABASE_CONN_MAX_AGE` | 처음에는 `60` |
| `DJANGO_SECURE_SSL_REDIRECT` | `1` |
| `DJANGO_SECURE_HSTS_SECONDS` | `31536000` |
| `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS` | domain 전체 검토 전 `0` |
| `DJANGO_SECURE_HSTS_PRELOAD` | 별도 사람 승인 전 `0` |
| `DJANGO_TRUST_X_FORWARDED_PROTO` | Render proxy 계약 검증 전 설정 금지; 검증 후 `1` |
| `KAMIS_CONFIRMATION_MAX_AGE_HOURS` | `36` |
| `KAMIS_HISTORICAL_MONTHLY_MAX_AGE_HOURS` | `192` |
| `KAMIS_HISTORICAL_DAILY_MAX_AGE_HOURS` | `36` |

`DEPLOY_VERSION`은 dashboard의 고정 secret으로 두지 않고 exact deploy의 `RENDER_GIT_COMMIT`에서
wrapper가 주입한다. `KAMIS_API_KEY`는 Web Service에 만들지 않는다.

### Ingestion Cron Jobs

각 job은 별도 service로 만들고 다음만 추가한다.

- ingestion role의 `DATABASE_URL`
- managed secret `KAMIS_API_KEY`
- `CONTROL_PLANE_OPERATIONS_ENABLED=0`
- 같은 exact release·Python·uv·Django production 설정

네 command를 하나의 shell chain으로 묶지 않는다.

```text
ingest-kamis-recent          → manage.py ingest_kamis_recent
ingest-kamis-monthly         → manage.py ingest_kamis_monthly ...승인된 typed arguments...
ingest-kamis-regional-daily  → manage.py ingest_kamis_regional_daily ...승인된 typed arguments...
ingest-kamis-market-daily    → manage.py ingest_kamis_market_daily ...승인된 typed arguments...
```

- recent·regional·market: 실행 간격 최대 24시간
- monthly: 실행 간격 최대 168시간
- schedule은 Render 기준 UTC로 기록하고 change record에 KST 환산 시각도 남김
- Render는 같은 Cron Job의 동시 실행을 하나로 제한하지만, source별 job을 서로 합치지 않음
- 수동 Trigger Run은 실행 중 job을 취소할 수 있으므로 active run 확인 후 사용
- 네 Cron Job 모두 Web Service와 같은 exact Git SHA를 deploy한 뒤에만 schedule을 활성화

### Migration·reviewer·publisher operation

- migration job: migration role DB credential, KAMIS key 없음, control plane `0`
- reviewer job: reviewer DB credential, KAMIS key 없음, control plane `1`
- publisher job: publisher DB credential, KAMIS key 없음, control plane `1`
- 각 job: workspace 2FA·protected environment·exact release SHA·immutable audit 필수

Render one-off job은 기반 service의 build와 환경변수를 상속한다. web service를 기반으로 만들면 web
credential을 그대로 상속하므로 금지한다. 역할별 operation base를 먼저 설계·검증한 뒤 실행한다.

## 9. 첫 deploy 전에 CI 만들기 — STOP

GitHub Actions에서 최소한 다음을 exact commit에 실행한다.

1. locked dependency sync
2. formatting·lint·type
3. migration drift
4. pytest
5. Django system check
6. production-like deploy check
7. dependency·license·secret boundary 검사

CI가 추가되고 GitHub의 required check로 지정되기 전까지 Render Auto-Deploy는 `Off`로 둔다. 이후에도
첫 production은 Render Dashboard의 `Manual Deploy` → `Deploy a specific commit`에서 승인된 full
SHA를 선택한다.

## 10. Migration과 첫 자료 공개 — STOP

아래 단계는 dashboard shell에서 즉흥적으로 실행하지 않는다. [운영 런북](OPERATIONS-RUNBOOK.md)의
exact command와 role credential을 사용하고 각 단계 사이에서 receipt를 검토한다.

1. production clone에서 migration plan·lock·실행 시간을 측정한다.
2. managed backup/PITR checkpoint를 만든다.
3. migration role의 one-off job에서 forward migration만 실행한다.
4. 네 ingestion job을 각각 한 번 실행한다.
5. 사람이 source 권리·mapping·identity·단위·coverage를 검수한다.
6. reviewer job에서 recent와 세 historical collection을 독립 승인한다.
7. publisher job에서 recent revision을 seal한다.
8. `inspect_recent_publication`으로 exact current/version/fact-set을 다시 읽는다.
9. publisher job에서 recent를 optimistic CAS로 activate한다.
10. historical authoritative inspection이 준비된 뒤 historical revision을 seal·activate한다.
11. ingestion 성공을 자동 approve·seal·activate로 연결하지 않는다.
12. Web Service health path를 `/health/ready`로 바꾸고 같은 `RELEASE_SHA`를 다시 deploy한다.

첫 recent activation 전 `/health/ready` 실패는 정상이다. readiness를 통과시키기 위해 DB pointer를
직접 수정하거나 local test actor를 production으로 복사하지 않는다.

## 11. Render hostname에서 먼저 검사하기

custom domain을 연결하기 전에 Render가 제공한 `onrender.com` 주소에서 다음을 확인한다.

1. `/health/live`가 `200`
2. `/health/ready`가 `200`
3. `/health/freshness`가 `CURRENT`와 `200`
4. catalog·detail·history·regions·markets·selection이 승인 자료를 표시
5. HTML·health에 `Cache-Control: no-store`
6. CSP에 `script-src 'none'`
7. public response에 cookie 없음
8. recent·historical fact-set header가 각각 고정
9. hashed CSS/font/SVG가 `200`과 immutable cache header를 반환
10. Render ingress·request·application log에 query·IP·User-Agent·검색어가 수집되지 않도록 실제
    log pipeline 확인

10번은 단순 점검 항목이 아니라 **STOP**이다. platform이 관리하는 요청 로그를 끄거나 필요한 범위로
제한·삭제할 수 없고 이 계약을 다른 검증된 계층에서 지킬 수도 없다면 Render를 application host로
확정하지 않는다. 민감값이 실제로 남았는지 확인하려고 real 검색어·credential을 시험 입력하지 않는다.

Render health check path는 `/health/ready`로 둔다. `/health/live`만 사용하면 DB·migration·active
publication이 없는 release도 traffic에 들어갈 수 있다. `/health/freshness` 실패만으로 process를
재시작하거나 publication을 바꾸지 않는다.

## 12. Cloudflare에 domain 연결하기

Render Web Service의 `Settings` → `Custom Domains`에서 먼저 domain을 추가한다.

1. Render가 보여주는 DNS target과 verification record를 복사한다.
2. Cloudflare `DNS` → `Records`에서 정확히 그 record만 추가한다.
3. ownership verification용 record는 `DNS only`로 둔다.
4. Render Dashboard에서 `Verify`를 눌러 certificate 발급 완료를 확인한다.
5. custom domain을 `DJANGO_ALLOWED_HOSTS`와 `DJANGO_CSRF_TRUSTED_ORIGINS`에 추가한다.
6. web service를 exact release로 다시 deploy한다.
7. custom domain에서 health와 전체 public smoke를 반복한다.

첫 연결은 Render 공식 Cloudflare 안내에 맞춰 proxy 상태를 결정한다. Cloudflare proxy를 켜기로
승인했다면 SSL/TLS mode는 `Full (strict)`만 사용하고, `Flexible`은 사용하지 않는다. HTML은
no-store 계약을 유지하고 static만 immutable cache한다. proxy를 켠 뒤 redirect loop·Host·CSP·
fact-set header를 다시 확인한다.

HSTS include-subdomains와 preload는 domain 아래의 다른 service까지 모두 HTTPS로 운영할 수 있다는
사람 검토가 끝난 뒤에만 켠다.

## 13. 실제 traffic을 여는 날

1. [Production 배포 체크리스트](PRODUCTION-DEPLOYMENT-CHECKLIST.md)의 모든 `STOP`을 다시 확인한다.
2. `RELEASE_SHA`, `PREVIOUS_RELEASE_SHA`, DB backup/PITR와 rollback target을 기록한다.
3. Auto-Deploy가 `Off`인지 확인한다.
4. Render `Manual Deploy` → `Deploy a specific commit`에서 `RELEASE_SHA`를 선택한다.
5. build·startup·`/health/ready`가 모두 성공할 때까지 domain traffic을 열지 않는다.
6. custom domain DNS를 승인 target으로 전환한다.
7. live→ready→freshness→public SSR→static 순서로 다시 검사한다.
8. error rate·latency·cron·freshness·database alert를 관찰한다.
9. 이상이 없으면 상태를 `DEPLOYED`로 기록한다.

GitHub push는 production deploy가 아니다. Render Events에 exact SHA deploy가 성공하고 custom domain
검사가 끝난 시점이 application 배포 완료다. approved recent/historical pointer까지 확인해야 자료
공개 완료다.

## 14. 문제가 생겼을 때

### 새 화면이 열리지 않음

- Render Events에서 새 deploy가 health check를 통과했는지 확인
- `/health/live`와 `/health/ready`를 분리 확인
- secret이나 URL을 log에 복사하지 않음
- 이전 release가 계속 serving 중이면 migration과 publication을 건드리지 않음

### 새 code만 문제

1. Render Events에서 검증된 이전 deploy를 선택한다.
2. `Rollback`을 누른다.
3. live→ready→freshness→전체 public SSR을 다시 확인한다.
4. reverse migration은 실행하지 않는다.

### 공개 자료가 잘못됨

- application rollback과 분리해 처리
- recent는 `inspect_recent_publication`으로 current/version을 다시 읽은 뒤 publisher job에서
  append-only `ROLLBACK` 또는 `WITHDRAW`
- historical은 authoritative inspection이 없으면 임의 rollback하지 않고 해당 traffic을 중단
- revision row·fact·pointer를 SQL로 직접 수정하지 않음

### Database 복구 필요

- 기존 database에 덮어쓰지 않음
- Render Recovery에서 PITR로 새 database instance 생성
- 새 instance의 migration·row·audit·publication·SSR을 검증
- 사람 승인 뒤 connection을 새 instance로 전환
- 검증 전 원본 database를 삭제하지 않음

## 15. 배포 후 반복 운영

### 매일

- [ ] recent ingestion과 freshness 결과 확인
- [ ] cron·web·database failure notification 확인
- [ ] stale이면 last-known-good를 유지하고 review backlog 조사

### 매주

- [ ] monthly·regional·market job과 historical monitor 확인
- [ ] failed deploy·job과 public read error code 검토
- [ ] search term·query·IP·User-Agent가 log에 남지 않는지 표본 확인

### 매월

- [ ] Render invoice와 사용량 확인
- [ ] dependency·security update 검토
- [ ] database storage·connection·latency 추세 확인
- [ ] logical backup export와 장기 retention 확인

### 분기마다

- [ ] 새 PITR instance restore rehearsal
- [ ] RPO 24시간·RTO 4시간 측정
- [ ] application·recent publication·historical publication rollback rehearsal
- [ ] role·credential·2FA·alert 담당자 재검토

## 16. 화면에서 멈추고 도움을 요청해야 하는 경우

- plan·비용 선택이 예상과 다름
- Render가 Python `3.14.7` 또는 uv `0.12.6`을 설치하지 못함
- database URL의 TLS certificate·hostname 검증이 실패
- proxy가 `X-Forwarded-Proto`를 안전하게 보장한다는 근거가 없음
- migration plan이 `0028`과 다름
- health가 예상 status가 아니거나 redirect loop가 발생
- KAMIS response schema·coverage·mapping이 local evidence와 다름
- active publication의 version·revision·fact-set이 receipt와 다름
- log나 화면에 secret·query·사용자 입력이 노출됨
- rollback target이 current schema를 읽는다는 evidence가 없음

이 경우 값을 추측해 입력하거나 production DB를 직접 수정하지 않는다. 화면 이름, 비민감 error code,
service ID와 release SHA만 기록하고 다음 승인을 요청한다.

## 17. 공식 참고 문서

- [Render Django 배포](https://render.com/docs/deploy-django)
- [Render Python version](https://render.com/docs/python-version)
- [Render deploy와 exact commit](https://render.com/docs/deploys)
- [Render default environment variables](https://render.com/docs/environment-variables)
- [Render health checks](https://render.com/docs/health-checks)
- [Render Cron Jobs 실행 모델](https://render.com/docs/cronjobs)
- [Render environment variables와 secrets](https://render.com/docs/configure-environment-variables)
- [Render Postgres 생성·연결](https://render.com/docs/postgresql-creating-connecting)
- [Render Postgres backup·PITR](https://render.com/docs/postgresql-backups)
- [Render rollback](https://render.com/docs/rollbacks)
- [Render custom domains](https://render.com/docs/custom-domains)
- [Render 2FA와 login policy](https://render.com/docs/login-settings)
- [Render protected environments](https://render.com/docs/projects)
- [Render notifications](https://render.com/docs/notifications)
- [Render Free plan 제한](https://render.com/docs/free)
- [Cloudflare DNS record](https://developers.cloudflare.com/dns/manage-dns-records/how-to/create-dns-records/)
- [Cloudflare proxy status](https://developers.cloudflare.com/dns/proxy-status/)
- [Cloudflare Full (strict)](https://developers.cloudflare.com/ssl/origin-configuration/ssl-modes/full-strict/)
- [Vercel Python runtime](https://vercel.com/docs/functions/runtimes/python)
- [Vercel Cron Job 관리](https://vercel.com/docs/cron-jobs/manage-cron-jobs)
