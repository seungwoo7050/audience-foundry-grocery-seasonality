# 개발 규칙

> Build less. Reuse more. Ship solid. Change safely.

이 규칙은 사람이 작성하는 모든 변경에 적용합니다. 제품별 불변식은
`docs/PRODUCT-DECISIONS.md`에 둡니다.

## 다섯 가지 실행 원칙

1. **Open Source and Standards First.** 유지보수되는 오픈소스, 공개 표준과
   프레임워크 기본 기능을 우선합니다. 도입 전에 정확한 버전, 라이선스, 무결성
   정보, 유지보수 상태와 보안 권고를 검토합니다.
2. **Less Code, Less Complexity.** 검증된 사용자 루프를 가장 적은 runtime,
   service, abstraction, dependency와 이동 부품으로 해결합니다. 범용 platform이나
   추측성 확장 지점을 만들지 않습니다.
3. **Production Quality from Day One.** 첫 구현 atom부터 type safety, testability,
   observability, security, privacy, provenance, idempotency, backup, recovery와
   rollback을 보존합니다.
4. **Small, Reversible Commits.** 각 commit은 하나의 주된 검토 질문에 답하고,
   집중된 증거를 포함하며, 관계없는 손상 없이 되돌릴 수 있어야 합니다.
5. **Prove Risky Assumptions Before Build.** 외부 접근, 권리, schema, identity,
   semantics, completeness와 실패 동작을 가장 작은 실제 viability check로 먼저
   증명한 뒤에만 의존 schema나 adapter code를 확장합니다.

## 우선순위

요구사항은 다음 순서로 적용합니다.

1. 안전, 보안, 법률, 개인정보와 라이선스 의무
2. 명시된 사용자 의도와 고정된 제품 결정
3. 정확성, 무결성과 호환성
4. 검증 가능한 전달과 rollback 경계
5. commit 크기 규칙

빠른 전달처럼 보이게 하려고 안전이나 정확성을 약화하지 않습니다.

## 작업 준비

첫 파일을 변경하기 전에 다음을 수행합니다.

- 추적된 모든 정책과 필수 결정 문서를 처음부터 끝까지 읽습니다.
- 저장소 경로, `main`, 예상 시작 SHA, remote 상태와 clean working tree를
  확인합니다.
- 필수 결정이 없거나 서로 모순되거나 증명되지 않았으면 중단합니다.
- legacy 경계를 확인하고 제외된 구현을 열람하거나 가져오지 않습니다.
- 로그인, key 입력, 결제, 2FA, 약관 승인, 파괴적 migration, production 배포 등
  사람만 수행할 checkpoint를 식별합니다.
- source 의존 저장·파싱 설계보다 먼저 문서가 요구하는 실제 source gate를
  수행합니다.

실패한 source gate도 증거입니다. 문서의 stop 또는 pivot 조건을 따르며 실패한
live evidence를 fixture로 대체해 호환성이 증명됐다고 주장하지 않습니다.

## 검토 가능한 commit atom

필수 source gate가 통과한 뒤에만 `docs/IMPLEMENTATION-PLAN.md`를 만들고 첫 루프를
독립적으로 설명·시험·검토·rollback할 수 있는 atom으로 나눕니다. 각 atom에는
목적, 주된 검토 질문, 의존성, 예상 파일, 집중 검증, rollback 경계와 의미 있는
크기 예외를 기록합니다.

구현과 그 구현의 가장 작은 집중 test는 일반적으로 같은 commit에 둡니다.
검증 또는 rollback 경계가 다르면 dependency 변경, formatting, 생성물, import
data, migration, backfill, traffic switch와 무관한 정리를 분리합니다.

사람이 작성한 변경은 의미 있는 20~80줄과 주된 production file 1~2개를 목표로
합니다. 100줄 또는 3개 파일을 넘으면 다시 나눌지 검토하고, 150줄을 넘으면
기본적으로 분리하며, 200줄 또는 5개 파일을 넘으면 검토와 rollback을 분리할 수
없는 이유를 설명합니다. 초기 정책과 제품 계약 commit은 명시적인 문서 예외입니다.

명령형 Conventional Commit 제목을 사용합니다. 본문에는 이유, 실제 실행한 검증과
명확하지 않은 rollback 또는 크기 예외를 적습니다. 명시적 승인 없이 공개된 이력을
rewrite하거나 force-push하거나 branch를 삭제하지 않습니다.

## 검증과 증거

개발 중에는 가장 좁은 관련 검증을, 완료 시에는 정확한 최종 commit에서 전체
repository gate를 실행합니다. 자동, 모의, sandbox, 수동과 live evidence를
구분합니다. 건너뛴 검사, mock, 다른 SHA 또는 사용할 수 없는 환경을 통과 증거로
보고하지 않습니다.

보안, 권한, 데이터 무결성, 파괴적 migration, secret 노출, idempotency, audit
atomicity와 외부 interface 결함은 의존 작업을 차단합니다. source receipt와
fixture는 민감값을 제거하고 재현 가능해야 하며 정확한 interface·parser revision에
연결해야 합니다.

## 의존성과 외부 시스템

- 직접 dependency를 고정하고 무결성 lock을 commit합니다.
- upstream code를 복사하거나 fork하기보다 dependency 사용을 우선합니다.
- dependency, vendored code, 생성물과 대량 import 변경을 격리합니다.
- 라이선스 고지와 source provenance를 보존합니다.
- provider가 보장하지 않은 내용을 만들지 않고 소유한 adapter로 외부 시스템을
  정의합니다.
- Redis, queue, replica, search engine, analytics store, spatial extension 또는
  별도 service는 측정된 근거와 전용 결정 뒤에만 추가합니다.

## 작업 트리와 완료

관계없는 사용자 작업을 보존하고 비파괴적 Git 작업을 사용합니다. secret을 Git,
prompt, fixture, log, receipt와 report에 넣지 않습니다. 권한이나 값을 꾸며내지 않고
사람 전용 checkpoint에서 멈춥니다.

구현 완료 시 `docs/COMPLETION-REPORT.md`를 만들고 정확한 local·승인된 remote SHA,
branch, remote, clean 상태, 검사, acceptance evidence, 보안·개인정보·라이선스 영향,
외부 변경, 복구 경로, 수동 checkpoint, blocker와 증명하지 못한 주장을 기록합니다.
