# 보안·운영 경계

## 기본 자세

- 명시적으로 분류하기 전에는 credential, session, recovery material, 개인정보,
  provider identifier, raw source artifact와 production configuration을 민감정보로
  취급합니다.
- secret을 commit하거나 문서, prompt, URL, fixture, snapshot, log, audit event,
  생성물 또는 완료 증거에 넣지 않습니다.
- 추적 설정에는 환경변수 이름만 기록합니다. 값은 승인된 대화형 입력 또는 managed
  secret 경계를 통해 주입합니다.
- 최소 권한, 유한 timeout, 입력 상한, TLS, secure cookie, CSRF 보호, 안전한 응답
  header와 민감정보를 제거한 외부 오류를 사용합니다.
- 계정, 동의, provider capability, source 권리, production identifier 또는 운영
  준비 상태를 꾸며내지 않습니다.

## 사람 전용 checkpoint

소유자가 그 단계를 명시적으로 승인하지 않았다면 로그인, key·password 입력, 결제,
billing 동의, 2FA, recovery code 처리, 약관 동의, 다른 사람에게 연락, production
배포, 파괴적 migration 또는 되돌릴 수 없는 외부 변경 전에 멈춥니다. 민감값을
대화에 붙여 넣지 않습니다.

## 데이터와 권한

영속 저장이나 상태 변경 전에 제품 결정 문서가 다음을 정의해야 합니다.

- data owner, 분류, source of truth, 공개 허용 field와 retention
- actor identity, trust boundary, authorization과 사람 전용 결정
- 상태 전이, 불변식, concurrency, replay, idempotency와 recovery
- audit atomicity와 observation, decision, publication, action의 분리
- backup, restore, migration, rollback과 compatibility 기대치

공개 source라는 이유만으로 반환된 모든 field를 안전하게 재공개할 수 있는 것은
아닙니다. 명시적 allowlist를 사용하고 개인정보를 최소화하며 source의 관할시장,
effective date, observation time, provenance와 uncertainty를 보존합니다.

## 외부 조회와 source artifact

의존 구현 전에 정확한 owner, interface revision, 약관, 인증, quota, schema,
identity, pagination, 삭제 의미와 재배포 권리를 확인합니다. 외부 응답은 후보
증거이며 publication 권한이 아닙니다.

모든 호출은 별도의 민감정보 제거 receipt를 가집니다. content-addressed artifact만
중복 제거할 수 있고 observation freshness는 중복 제거하지 않습니다. source 약관이
명시적으로 허용할 때만 raw bytes를 보존합니다. 그렇지 않으면 cryptographic hash,
최소 receipt와 audit에 필요한 정규화 사실만 보존합니다.

timeout, rate limit, 호환되지 않는 schema, 불완전 coverage, parse 실패 또는
publication 실패가 발생하면 last-known-good revision을 보존하고 사실에 맞는
freshness를 표시합니다. 불완전한 generation을 조용히 공개하지 않습니다.

## Production gate

공개 production 노출 전에 다음을 필수로 요구합니다.

- HTTPS와 안전한 production 설정
- 관리자 MFA 또는 동등한 검토를 거친 identity-aware control
- managed secret injection과 최소 권한 database·provider credential
- 민감정보를 제거한 structured log, health/readiness check, source freshness
  monitoring과 경보 담당자
- PostgreSQL backup/PITR 정책과 성공한 restore rehearsal
- dependency, license, migration, privacy와 secret scan
- 상한이 있는 retry·rate-limit 동작과 검증된 rollback

provider 선택, domain 생성, credential과 배포는 명시적으로 승인되기 전까지 사람
전용 checkpoint로 남습니다.
