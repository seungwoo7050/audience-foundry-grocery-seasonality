# Production 배포 체크리스트

- 처음 배포하는 사람을 위한 화면별 안내: [초록장부 첫 Production 배포 가이드](FIRST-PRODUCTION-DEPLOYMENT-GUIDE.md)
- 기준 절차: [운영 런북](OPERATIONS-RUNBOOK.md)
- 완료 증거 형식: secret·query·사용자 입력을 제외한 immutable locator와 SHA-256
- 판정: `STOP` 항목이 하나라도 미완료면 migration·publication·traffic 전환 중단

## 0. 변경 기록 고정

- [ ] `RELEASE_SHA`: ________________________________
- [ ] `PREVIOUS_RELEASE_SHA`: ________________________________
- [ ] change record: ________________________________
- [ ] 배포 책임자: ________________________________
- [ ] reviewer / publisher / on-call 담당자: ________________________________
- [ ] maintenance window와 rollback 판단 시각: ________________________________
- [ ] `RELEASE_SHA`와 승인 범위가 동일함을 사람이 확인
- [ ] 최종 상태: `BLOCKED / READY / DEPLOYED / ROLLED_BACK`

## 1. Production 기반 승인 — STOP

- [ ] Python 3.14.7·Django 5.2.17·uv 0.12.6·PostgreSQL 18.6 호환 platform 승인
- [ ] immutable artifact upload·release·atomic traffic switch·rollback 명령과 대상 ID 승인
- [ ] private managed PostgreSQL과 TLS hostname/CA `verify-full` 동등 검증
- [ ] web·migration·ingestion·reviewer·publisher·backup/restore 역할별 credential·grant 분리
- [ ] managed secret store와 회전·폐기 담당자 지정
- [ ] 승인 domain·DNS·certificate·`ALLOWED_HOSTS`·CSRF origin 고정
- [ ] trusted proxy hop 검증 후 forwarded-proto 사용 여부 결정
- [ ] HSTS include-subdomains·preload 영향 범위 승인
- [ ] external MFA/IAM private reviewer·publisher job과 immutable audit 구성
- [ ] query·IP·User-Agent·검색어를 제거한 ingress/application log pipeline 검증
- [ ] liveness·recent readiness/freshness·historical freshness monitor와 alert 수신 검증
- [ ] 암호화 backup·PITR·retention·실패 alert 구성
- [ ] 새 instance restore rehearsal에서 RPO 24시간·RTO 4시간 충족
- [ ] historical authoritative repeatable-read inspection과 canonical restore 검증
- [ ] `PREVIOUS_RELEASE_SHA`가 migration `0028` schema와 vNext route를 읽는 rollback rehearsal 통과

## 2. Exact release gate — STOP

- [ ] owner-controlled clean release checkout 사용
- [ ] 실제 source credential을 shell·argument·출력에 넣지 않음
- [ ] 아래 Git gate 통과

```sh
test "$(git branch --show-current)" = "main"
test "$(git rev-parse HEAD)" = "$RELEASE_SHA"
test -z "$(git status --porcelain)"
git fsck --full
```

- [ ] `make sync` 통과
- [ ] [운영 런북의 local release gate](OPERATIONS-RUNBOOK.md)와 동일한 synthetic 설정으로 `make production-check` 통과
- [ ] 검사 뒤 `git status --porcelain`이 계속 비어 있음
- [ ] dependency audit 결과 검토
- [ ] `THIRD_PARTY_NOTICES.md`·license inventory·실제 locked runtime 대사
- [ ] 승인 remote가 exact `RELEASE_SHA`를 포함하고 해당 SHA의 필수 CI가 통과
- [ ] 승인된 최종 `RELEASE_SHA`를 change record에 재고정

## 3. Immutable build

- [ ] 새 `RELEASE_DIRECTORY` 생성
- [ ] `make runtime-sync` 통과
- [ ] production-like 설정에서 아래 static build 통과

```sh
.venv/bin/python manage.py collectstatic --noinput
```

- [ ] `collectstatic --clear`를 사용하지 않음
- [ ] application·template·migration·lockfile·hashed static·release SHA·license notice만 artifact allowlist에 포함
- [ ] `.env.local`·backup·test DB·cache·browser evidence를 artifact에서 제외
- [ ] artifact checksum·크기·notice bundle 기록
- [ ] 대표 CSS/font/SVG hashed asset의 content type과 immutable cache header 확인

## 4. Database preflight와 forward migration — STOP

- [ ] 배포 직전 managed backup/PITR checkpoint 성공과 복구 가능 상태 확인
- [ ] production 복제 DB에서 `0028`까지의 lock·실행 시간·기존 row 적합성·이전 release 호환성 측정
- [ ] migration 역할로 아래 plan 검토

```sh
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py showmigrations --plan
.venv/bin/python manage.py migrate --plan
.venv/bin/python manage.py check
```

- [ ] 승인된 maintenance window에서만 forward migration 실행

```sh
.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py migrate --check
.venv/bin/python manage.py check --deploy --fail-level WARNING
```

- [ ] `--fake`·reverse migration·기존 DB overwrite를 사용하지 않음
- [ ] 실패 시 새 release traffic을 열지 않고 migration 상태와 audit만 확인

## 5. Runtime·secret·scheduler 구성 — STOP

- [ ] web: `DEBUG=0`, Admin·QA preview·control plane 비활성
- [ ] web: exact 40자 lowercase `DEPLOY_VERSION=$RELEASE_SHA`
- [ ] web: managed Django secret·host·CSRF·HTTPS·DB 설정 주입
- [ ] web process에 `KAMIS_API_KEY`와 `.env.local`을 주입하지 않음
- [ ] ingestion worker에만 managed `KAMIS_API_KEY`와 최소 DB 권한 주입
- [ ] recent·monthly·regional·market ingestion을 독립 singleton job으로 등록
- [ ] recent·regional·market 최대 24시간, monthly 최대 168시간 interval과 overlap 방지 검증
- [ ] scheduler가 approve·seal·activate를 자동 연결하지 않음
- [ ] local `live-source-e2e-smoke`와 browser fixture를 CI·배포·production worker에 연결하지 않음
- [ ] private control job만 `CONTROL_PLANE_OPERATIONS_ENABLED=1` 사용
- [ ] 외부 client가 forwarded-proto header를 주입할 수 없음을 확인
- [ ] 새 release를 traffic 없이 Gunicorn으로 시작

## 6. Production 자료 검수·공개 — STOP

- [ ] `bootstrap_control_plane_actors --expected-release-sha "$RELEASE_SHA"`를 승인된 actor provisioning job에서 1회 실행
- [ ] recent·monthly·regional·market ingestion 성공 receipt와 audit 확인
- [ ] source 권리·공식 code·series·region·market mapping·기간·단위·coverage를 사람이 검수
- [ ] incomplete·quarantined·schema mismatch collection이 없음을 확인
- [ ] reviewer job에서 recent generation 승인
- [ ] publisher job에서 recent revision seal
- [ ] `inspect_recent_publication`의 exact current revision·version·fact-set을 기록
- [ ] publisher job에서 optimistic state와 exact release를 사용해 recent revision 활성화
- [ ] monthly·regional·market collection을 각각 독립 승인
- [ ] 세 historical review의 compatibility report 승인
- [ ] publisher job에서 historical revision seal
- [ ] authoritative historical inspection에서 current/version/fact-set을 다시 읽음
- [ ] publisher job에서 exact optimistic state와 release를 사용해 historical revision 활성화
- [ ] 기존 revision row·source fact·pointer를 직접 수정하지 않음
- [ ] publication별 revision·version·fact-set·activation ID 기록

## 7. Traffic 전 acceptance — STOP

- [ ] `/health/live` 성공
- [ ] `/health/ready` 성공
- [ ] `/health/freshness` 성공
- [ ] 승인된 historical freshness monitor 성공
- [ ] catalog와 대표 detail SSR 성공
- [ ] history·regions·markets·selection SSR 성공
- [ ] recent·historical fact-set header가 실제 읽은 publication과 각각 일치
- [ ] HTML·health `Cache-Control: no-store` 확인
- [ ] CSP `script-src 'none'`·no-referrer·nosniff·DENY frame·same-origin COOP/CORP·Permissions-Policy 확인
- [ ] public response의 `Set-Cookie` 부재와 `/admin/` generic 404 확인
- [ ] `document.scripts.length === 0`과 외부 font/image/script 요청 0 확인
- [ ] hashed CSS/font/SVG 응답·content type·immutable cache 확인
- [ ] 390×844·1440×900 핵심 흐름 육안 검수
- [ ] 360×800·768×1024 horizontal overflow 0 확인
- [ ] keyboard order·focus·44px target·긴 한글·대표 axe violation 0 확인
- [ ] 실제 공개값의 품목·단위·조사일·범위·비교 의미를 승인 publication과 대사
- [ ] 고정 900초 recent load profile 통과와 publication hash 불변 확인
- [ ] vNext historical route의 승인된 production 성능 profile 통과

## 8. Rollback 준비 — STOP

- [ ] 이전 application과 static artifact를 즉시 선택 가능한 상태로 유지
- [ ] platform-specific atomic rollback 명령·권한·timeout을 change record에 연결
- [ ] rollback 후 실행할 live→ready→freshness→전체 SSR smoke 준비
- [ ] recent publication rollback/withdraw의 target·expected version·expected current 확인 절차 승인
- [ ] historical rollback/withdraw의 authoritative inspection·last-known-good membership 확인 절차 승인
- [ ] application rollback과 publication rollback을 별도 change로 유지
- [ ] code rollback 시 reverse migration을 실행하지 않음
- [ ] DB 복구 시 in-place overwrite 대신 새 PITR instance와 승인된 connection switch 사용
- [ ] rollback 의사결정자와 abort 기준 확인

## 9. Traffic 전환

- [ ] change record의 모든 `STOP` 항목 재확인
- [ ] 배포 책임자·reviewer·publisher·on-call의 최종 GO 승인
- [ ] platform의 승인된 atomic traffic switch 실행
- [ ] live→ready→freshness→catalog/detail→historical routes 재검사
- [ ] static·security·fact-set header 재검사
- [ ] error rate·latency·DB·scheduler·freshness·public read alert 관찰
- [ ] abort 기준 충족 시 즉시 application traffic rollback
- [ ] 공개 사실 문제 시 별도 publication rollback 또는 withdraw 승인

## 10. 배포 종료 기록

- [ ] exact release·artifact·migration·static·process evidence locator 기록
- [ ] recent·historical publication revision/version/fact-set/activation 기록
- [ ] health·browser·load·accessibility 결과 locator 기록
- [ ] backup/PITR·restore·RPO/RTO evidence locator 기록
- [ ] alert route·담당자·known gap·다음 검토 시각 기록
- [ ] previous release·rollback command locator 기록
- [ ] receipt·log·ticket·screenshot에 secret·query·사용자 입력이 없음을 확인
- [ ] 배포한 `RELEASE_SHA`를 바꾸지 않는 별도 docs commit으로 `docs/COMPLETION-REPORT.md`에 production 결과 기록
- [ ] 배포 SHA와 후속 evidence docs SHA를 각각 기록
- [ ] `git status --porcelain`이 비어 있음을 확인

## 11. 중단 조건 부재 — STOP

- [ ] 실제 secret·query·raw response·사용자 입력 노출 없음
- [ ] migration drift·부분 적용·constraint 위반 없음
- [ ] backup/PITR·restore evidence 누락 없음
- [ ] MFA/IAM·role separation·actor audit 누락 없음
- [ ] historical inspection·freshness monitor·rollback gap 없음
- [ ] readiness/freshness/public read/fact-set/static/security 검사 실패 없음
- [ ] source 권리·mapping·unit·coverage 불확실성 없음
- [ ] 이전 release 호환성과 rollback 명령 검증 완료
