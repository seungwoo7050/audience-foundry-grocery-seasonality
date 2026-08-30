# Audience Foundry 프로젝트 기준선

이 저장소는 기본적으로 로컬·비공개인 Audience Foundry 제품 기준선입니다. 이
commit에는 정책만 있으며 runtime, dependency graph, account, credential,
database, deployment, 외부 연동 또는 production 준비 완료 주장이 없습니다.

제품 계약은 `docs/` 아래 여섯 결정 문서를 완성해 별도 문서 checkpoint로
commit할 때 고정됩니다. 구현은 그 checkpoint와 clean working tree를 확인하고,
위험한 외부 interface마다 문서에 정한 실제 viability gate를 파일 변경 전에
통과한 뒤에만 시작합니다.

저장소 기본값은 다음과 같습니다.

- branch: `main`
- remote: 없음
- 공개 상태: 로컬·미공개
- legacy 구현 또는 이력 재사용: 이후의 고정 결정이 범위, provenance, 검증과
  rollback을 명시적으로 승인하기 전에는 금지
