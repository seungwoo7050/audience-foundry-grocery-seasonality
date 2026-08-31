# vNext public-read 계약

이 문서는 source·review·publication과 공개 Django SSR 사이의 유일한 vNext read 계약이다.
공개 view와 template은 이 계약을 벗어나 ORM 산술, candidate 조회 또는 source 호출을 하지
않는다.

## 공개 경로

| 경로 | 목적 | 읽는 publication |
|---|---|---|
| `/` | 품목 탐색과 최근 비교 필터 | `RECENT_RETAIL` |
| `/series/<uuid>/` | 최근 조사값과 1주·1개월·1년 비교 | `RECENT_RETAIL`; historical 가용성만 별도 확인 |
| `/series/<uuid>/history/` | 선택 지역의 월별 기록 | `HISTORICAL_RETAIL` |
| `/series/<uuid>/regions/` | 한 조사일의 지역별 범위 | `HISTORICAL_RETAIL` |
| `/series/<uuid>/regions/<uuid>/markets/` | 선택 지역·조사일의 시장별 관측 | `HISTORICAL_RETAIL` |
| `/selection/` | URL 순서의 최대 다섯 품목 최근 비교 | `RECENT_RETAIL` |

모든 경로는 GET과 HEAD만 허용한다. 공개 request는 source client, raw artifact, candidate
collection, review row와 운영 command를 호출하지 않는다. UUID가 current recent publication에
없으면 404이며 candidate 존재 여부를 드러내지 않는다.

## GET state

catalog는 다음 값만 허용한다.

- `q`: Unicode control·line break가 없는 공식 품목명 부분 문자열, trim 후 최대 80자
- `category`: 빈 값, `vegetable`, `fruit`
- `period`: `week`, `month`, `year`; 기본 `week`
- `direction`: `all`, `lower`, `equal`, `higher`, `unavailable`; 기본 `all`
- `sort`: `name`, `change_asc`, `change_desc`; 기본 `name`
- `page`: canonical decimal integer 1–100; 기본 1. `q`가 있으면 1만 허용

한 page는 30개다. 유효한 `q`도 HTML, heading, input value와 generated URL에 반사하지 않는 기존
privacy 계약을 유지하므로 검색 결과는 최대 30개 단일 page다. 검색 없는 탐색만 pagination한다.
change sort는 available signed percentage를 정렬하고 unavailable을 항상 뒤에 둔다. 동률은
category, 공식 품목명과 exact series identity 순서로 고정한다. period, direction과 sort는 sealed
recent reference·change fact만 사용하며 view나 template에서 다시 계산하지 않는다.

history는 `range=12|36|60`과 `region=<uuid>`만 받는다. 기본 range는 36이다. 12개월은 36개월
completeness를 통과한 series·region에서만, 60개월은 완전한 60개월이 있을 때만 선택지로
노출한다. 공식 aggregate 지역이 없는 series는 region 선택 전 안내 상태를 표시하고 chart를
만들지 않는다.

regions는 `date=YYYY-MM-DD`만 받는다. markets는 같은 `date`와 `page=1..100`을 받는다. date를
생략하면 active bundle 안에서 해당 series의 regional·market source가 공유하는 최신 조사일을
사용한다. 선택 가능한 날짜는 bundle 확인 시각 기준 최근 31 calendar day 안의 실제 공통
조사일뿐이다. 시장 목록은 공식 이름 순서, 30개 page와 stable market identity tie-break를
사용하며 가격순·시장유형 filter를 제공하지 않는다.

selection은 반복 `series=<uuid>`만 받는다. URL 순서를 보존하고 중복은 첫 항목만 유지한다.
malformed UUID 또는 중복 제거 전후 어느 쪽이든 5개 초과면 고정 문구 400이다. current recent
publication에서 사라진 valid UUID는 값 자체를 반사하지 않고 제외 수만 알리는 200 partial
state다. 합계, 절약액, 서로 다른 단위의 정렬과 품목 간 우열은 계산하지 않는다.

알 수 없는 parameter, 중복이 허용되지 않은 parameter, 비canonical page·date·UUID와 범위 밖
값은 400이다. 검색어를 제외한 정상화된 유효 enum·date·UUID만 form과 link에 다시 표시한다.
검색어, invalid raw value와 전체 query는 response, log, metric, audit와 artifact에 남기지 않는다.

## active publication 결합

`RECENT_RETAIL`과 `HISTORICAL_RETAIL`은 각각 sealed current pointer와 independent
last-known-good를 갖는다. 두 revision을 합쳐 새 freshness나 hash를 만들지 않는다.

historical fact는 `series_identity_sha256`로 recent exact series와 연결한다. 품목·품종·등급,
원문 단위·단위크기와 retail category code가 모두 일치해야 한다. 이름 유사도, 단위환산과
fallback join은 금지한다. historical bundle 안에 대응 series가 없으면 recent detail은 그대로
작동하고 확장 link를 숨긴다. 직접 요청한 확장 화면은 공개 recent series라면 200 unavailable,
공개 recent series가 아니면 404다.

catalog·detail·selection은 기존 `X-Publication-Fact-Set`를 유지한다. historical 화면은
`X-Historical-Publication-Fact-Set`를 제공한다. 두 publication을 실제로 읽은 response만 두
header를 모두 제공한다. header 값은 sealed revision의 검증된 lowercase SHA-256 literal이다.

## presentation context

public-read layer는 format과 validation을 끝낸 다음 template-safe primitive만 반환한다.

- catalog item: exact identity, current price, source date, 선택 period comparison, detail URL
- detail: 기존 recent series·comparisons·provenance와 `historical_links`
- history: series identity, selected region/range, available ranges, chronological monthly points,
  provider mean·min·max와 gap flag
- regions: series identity, selected date, selectable dates와 공식 이름 순 regional mean·min·max
- markets: series·region identity, selected date, selectable dates, paginated market name·price
- selection: URL 순서의 recent item facts, excluded count와 add/remove canonical URLs

가격은 검증된 Decimal에서 원화 표시 문자열과 `<data>`용 finite decimal string으로 만든다.
날짜는 ISO machine value와 한국어 display를 함께 만든다. chart geometry는 server-side Decimal
계산 결과만 숫자 SVG attribute로 전달하며 inline style, data-driven CSS와 template 산술은
금지한다.

월별 chart는 provider 평균 line과 최저–최고 범위만 표현한다. 결측 구간을 선으로 잇거나 0으로
채우지 않는다. 지역 화면은 공식 이름순 range/dot ledger다. 시장 화면은 ruled list다. 어떤
표면도 추세, 제철, 예측, 저렴함, 추천 또는 시장유형을 추론하지 않는다.

## 상태와 HTTP 계약

| 상태 | HTTP | 계약 |
|---|---:|---|
| ready | 200 | current sealed fact만 표시 |
| empty | 200 | 유효 filter 결과 없음; controls와 전체 보기 제공 |
| unavailable | 200 | current publication 또는 exact historical facts 없음; 작동하지 않는 controls 제거 |
| stale | 200 | last-known-good 사실과 publication별 경고·확인 시각 표시 |
| validation | 400 | 고정 오류 요약과 유효 복구 link; raw input 비반사 |
| not found | 404 | current recent series가 아님; candidate 존재 비공개 |
| server error | 503 | DB·shape·integrity 실패; 고정 문구와 안전한 retry link |

loading은 no-JavaScript SSR runtime 상태가 아니다. DEBUG에서만 제공되는 deterministic browser
acceptance fixture가 loading copy·layout을 검증하며 production 공개 경로는 준비된 response를
한 번에 반환한다. malformed active fact, incomplete range, duplicate identity와 hash 불일치는
부분 표시하지 않고 해당 surface 전체를 503으로 실패 폐쇄한다.

recent stale과 historical stale은 각각 자신의 상태 문구와 확인 시각을 사용한다. 한쪽 stale을
다른 쪽에 전파하지 않는다. 새 candidate 수집 실패는 active pointer를 바꾸지 않으므로 기존
last-known-good를 계속 표시한다.

## 보안·접근성 계약

모든 public response는 `Cache-Control: no-store`, `Referrer-Policy: no-referrer`,
`X-Content-Type-Options: nosniff`와 `script-src 'none'` CSP를 유지하고 `Set-Cookie`를 만들지
않는다. 외부 font, image, script, analytics와 browser source request는 없다. outbound KAMIS
attribution link는 `rel="external noreferrer"`를 사용한다.

페이지에는 `main`과 h1이 각각 하나이며 skip link, semantic heading/list/table 또는 definition
list, visible keyboard focus와 44px target을 유지한다. server SVG는 정확한 HTML 값의 보조
표현이고 `aria-hidden`이다. 360px에서 horizontal overflow와 긴 한글 절단이 없어야 한다.

## copy revision과 운영 checkpoint

vNext 공개 문구는 `ko-v4`로만 새 historical revision에 봉인한다. `ko-v1`–`ko-v3` row는
수정하지 않는다. disposable local DB에서만 fixture bundle과 browser evidence를 만들며,
production migration, first collection, code manifest 승인, review, seal, activation, traffic
switch와 rollback은 사람 checkpoint다.
