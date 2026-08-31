# vNext source gate

이 문서는 2026-08-31(KST)에 승인된 개발계정으로 수행한 최소 live viability check의
비민감 증거다. production 활성화, 전체 수집, coverage 승인 또는 운영 권리 판정을 대신하지
않는다.

## 실행 경계

- 대상은 공공데이터포털 public gateway의 `15156060`, `15156062`, `15156065`다.
- 각 진단 round는 dataset별 공식 예시 기간의 첫 page·최대 1행만 GET했다.
- redirect와 retry를 허용하지 않았고, 응답 크기를 512 KiB로 제한했다.
- 기존 프로젝트 credential은 저장소의 owner-only loader로 읽고 HTTP 요청을 만드는 즉시
  release했다. 값, 길이, 일부, encoding과 완성 URL은 검사하거나 출력하지 않았다.
- response body, 가격, 이름, raw row와 전체 query는 파일, log, fixture 또는 report에 남기지
  않았다. schema key·container type과 검증 결과만 process output으로 확인했다.
- wrapper 차이를 안전하게 진단하고 수정하기까지 dataset별 네 번 이하의 단일 호출을
  수행했다. 자동 재시도와 추가 page 조회는 없었다.

## 결과

| dataset | endpoint path | HTTP | retail·non-empty | field count | field/type schema SHA-256 |
|---|---|---:|---|---:|---|
| `15156060` | `/B552845/perYearMonth/price` | 200 | pass | 28 | `97c0ec5188c96b982880d67724816cae04f10745a4d08be4dbbc27abb342af6a` |
| `15156062` | `/B552845/perRegion/price` | 200 | pass | 21 | `d8bcd211de68e26e1fdc83da50a702a8316e05a586137d0056ca8dc21e83b6e1` |
| `15156065` | `/B552845/periodRetail/price` | 200 | pass | 20 | `15b731c8fced378d3741f5ec061ef48e359d370a5fcaabd781f7345e44968406` |

세 응답은 모두 JSON이며 live envelope는 공식 Swagger 설명과 달리 최상위
`response.header`와 `response.body` wrapper를 사용했다. `body.items.item`은 배열이고,
page metadata는 integer, item property는 이번 non-empty canary에서 모두 string이었다.
adapter는 wrapper 없는 JSON을 허용하거나 자동 추측하지 않고 이 live shape에 실패 폐쇄한다.

## 증명한 것과 증명하지 않은 것

이 gate는 승인 credential이 세 public endpoint에 접근할 수 있고, non-empty 소매 응답의
envelope와 필드 집합이 typed adapter를 만들 만큼 안정적임을 증명한다.

다음은 아직 증명하지 않았다.

- zero-result serialization, nullable·blank·sentinel과 가격 문자열의 모든 변형
- 전체 기간·지역·시장 pagination과 quota 최악치
- 네 source 사이 모든 채소·과일 exact series의 identity와 36·60개월 completeness
- production scheduler, egress, credential rotation, review, seal과 activation

parser는 값 변형과 결측을 fixture로 추정하지 않는다. 전체 code manifest 생성은 bounded
collection을 거쳐 별도 사람이 승인하며, cross-source identity 또는 completeness가 하나라도
맞지 않는 series는 공개 후보에서 제외한다. production first collection·review·seal·activation은
계속 사람 checkpoint다.
