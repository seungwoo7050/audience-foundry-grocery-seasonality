# Audience Foundry Grocery Seasonality

한국 소비자가 한국농수산식품유통공사(KAMIS)의 채소·과일 소매 조사 평균을
동일 품목·품종·등급·판매 단위 안에서 조사일, 1주 전, 1개월 전, 1년 전과
중립적으로 비교하는 한국어 서비스의 문서 기준선입니다.

`seasonality`는 저장소 코드명입니다. 첫 MVP는 한 해 전 값 하나를 계절성·평년·제철의
증거로 바꾸지 않습니다. 공개 화면은 공식 조사값과 결정적인 차이만 표시하며
`제철`, `저렴하다`, `비싸다`, `지금 사기 좋다`, `추천`, `최저가`, `가격 예측`을
주장하지 않습니다.

## 저장소 상태

- 저장소: `audience-foundry-grocery-seasonality`
- 기본 브랜치: `main`
- 정책 기준선: `0cc95e7`
- 원격 저장소: 없음
- 공개 상태: 로컬·미공개
- 구현 상태: runtime, dependency, database, credential, 외부 연동과 배포 없음

## 고정된 첫 범위

- 첫 source는 공공데이터포털의 KAMIS
  [최근일자 도·소매가격정보 API `15156063`](https://www.data.go.kr/data/15156063/openapi.do)입니다.
- 공개 대상은 source가 `소매`로 식별한 채소류·과일류입니다.
- 상세 화면은 정확히 한 품목·품종·등급·판매 단위·검증된 조사범위의 profile입니다.
- 비교 기준은 source가 제공한 조사일, 1주 전, 1개월 전, 1년 전 가격입니다.
- 1일 비교, 도매, 수산·축산·곡물, 지역 간 비교, 순위와 알림은 첫 범위가 아닙니다.
- 계정, 검색 이력, 장바구니, 위치, GPS와 개인화는 만들지 않습니다.
- 공개 request는 외부 source를 호출하지 않고 승인된 PostgreSQL publication만 읽습니다.

## 구현 전 필수 source gate

공공데이터포털 메타데이터는 API가 무료이고 이용허락 제한 없음이며 JSON/XML,
개발·운영 자동승인이라고 안내하지만 live contract를 증명하지는 않습니다. 파일을
바꾸기 전에 사람이 key 발급·입력 단계에서 멈추고 실제 최소 요청으로 HTTPS 접근,
권리, quota, pagination, 오류 envelope, 코드 identity, 결측, 평균·조사범위,
단위와 세 비교기간의 의미를 검증해야 합니다. 실패한 live evidence를 fixture로
대체하지 않습니다.

비교기간 의미만 실패하면 현재 공식 소매 조사 평균 조회로 축소합니다. API가
운영에 부적합하지만 공식
[월별 소매가격 파일 `15087482`](https://www.data.go.kr/data/15087482/fileData.do)의
권리·identity·단위가 통과하면 파일 공표본과 각 row의 기준 연월을 명시한 별도 정적
월별 탐색기로 축소할 수 있습니다. KAMIS HTML scraping, 비공식 미러와 quota 우회는
허용하지 않습니다.

## 계약 문서

- [도메인 개요](docs/DOMAIN-BRIEF.md)
- [제품 결정](docs/PRODUCT-DECISIONS.md)
- [시스템 경계](docs/SYSTEM-BOUNDARIES.md)
- [데이터·감사 모델](docs/DATA-AND-AUDIT-MODEL.md)
- [기술 결정](docs/TECHNOLOGY-DECISIONS.md)
- [MVP 인수 기준](docs/MVP-ACCEPTANCE.md)

구현자는 여섯 문서와 root 정책을 처음부터 끝까지 읽고 Git 기준선을 확인한 뒤,
저장소 변경 없이 source gate를 수행합니다. 안전한 공개 path가 통과한 경우에만
`docs/IMPLEMENTATION-PLAN.md`를 만들고 작은 검증 가능한 commit으로 첫 루프를
구현합니다.
