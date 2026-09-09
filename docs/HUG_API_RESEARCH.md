# HUG 분양보증현황 API 조사 (2026-09-09)

목표: **HUG(주택도시보증공사) OPEN API로 오피스텔·상가 분양보증 현황을 확인**한다.

근거 자료: 사용자 제공 프로그램사양서 `HUG-PA_프로그램사양서` v0.7 (2019-03-11,
"HUG 빅데이터 분석 플랫폼 구축 및 모델 개발") — **[OPEN API] 지역별 분양보증현황**
(등록일 2017-02-15, 업데이트주기 수시, 무료·이용허락조건 제한 없음,
담당 051-955-5433).

## 1. 확정된 명세

| 항목 | 값 |
|---|---|
| 프로그램ID | `AreaBusinessDistributionGuaranteeStatus` |
| URL | `https://www.khug.or.kr/areaBusinessDistributionGuaranteeStatus.do` |
| 인증 | 쿼리스트링 `API_KEY` (data.go.kr의 `serviceKey`가 아님) |
| 목록 조회 | `?API_KEY=<키>&AREA_DCD=<지역>` |
| 상세 조회 | `?API_KEY=<키>&BUNO=<사업장번호>` |
| 응답 | XML — `response > header(resultCode/resultMsg) + body > items > item` |
| 정상 코드 | `resultCode = 00` (`NORMAL SERVICE.`) |

목록 응답 필드(사양서 명세 전부):

| 필드 | 국문 | 크기 |
|---|---|---|
| `BUNO` | 사업장번호 | VARCHAR(6) |
| `BSU_NM` | 사업장명 | VARCHAR(100) |
| `CUST_NM1` | 시행업체명 | VARCHAR(100) |
| `CUST_NM2` | 시공업체명 | VARCHAR(100) |
| `ADDR` | 주소 | VARCHAR(300) |

## 2. 핵심 결론 — 이 API만으로는 오피스텔·상가를 '구분'할 수 없다

목록 응답에 **주택유형(아파트/오피스텔/상가) 필드가 없다.** 보증종류·세대수·
분양가·보증금액도 없다. 즉 "지역별 분양보증 사업장 명부"에 가깝다.

따라서 오피스텔·상가만 뽑으려면 아래 셋 중 하나가 필요하다.

1. **`BUNO` 상세 응답에 유형 필드가 있는 경우** → 그대로 사용.
   사양서에 상세 응답 필드 명세가 **없어 미확정**이다. → probe로 확인 (§4)
2. **다른 HUG OPEN API** — HUG는 OPEN API를 30여 종 공개하고 있고
   (`https://www.khug.or.kr/openapi/web/se/ap/seap000002.jsp`),
   `오피스텔분양보증`·`주상복합주택분양보증`은 별도 보증상품이므로
   상품별 API가 따로 있을 가능성이 있다. → probe 목록 크롤로 확인 (§4)
3. **사업장명 키워드 추정** — 위 둘이 모두 없을 때의 차선책.
   `src/hug_guarantee.py`가 구현하고 있으며 결과에 `category_basis=name`으로
   **'추정'임을 반드시 표기**한다 (오탐·누락 있음).

> 분류 우선순위: 응답의 유형 필드(확정) → 사업장명 키워드(추정) → 미상.
> 시행사·시공사명은 "(주)○○상가개발"처럼 오탐을 유발하므로 판정에서 제외한다.

## 3. 미확정 항목

| # | 항목 | 왜 미확정인가 |
|---|---|---|
| 1 | `AREA_DCD` 코드표 | 사양서에 코드 목록 없이 샘플 `02`만 제시. 샘플 응답은 부산 사업장이라 `02`=부산인지 서울인지도 불명. |
| 2 | `BUNO` 상세 응답 필드 | 사양서 응답 명세가 목록 기준 5개뿐. 상세 전용 필드 유무 불명. |
| 3 | 페이징 | 파라미터·응답 모두에 페이징 항목 없음 → 지역별 전건 반환으로 보이나 미검증. |
| 4 | 현재 서비스 여부 | 사양서가 2019년 문서다. 엔드포인트 생존·응답 스키마 변경 여부 미검증. |
| 5 | 오피스텔/상가 전용 API | HUG OPEN API 목록 크롤 필요. |

## 4. 검증 방법 (네트워크 제약)

개발 컨테이너에서는 `www.khug.or.kr`·`www.data.go.kr` 모두
**egress proxy 403(정책 차단)** 이라 호출·문서 열람이 불가능하다.
(Wave A 조사에서 확인했듯 data.go.kr은 해외 IP도 차단한다.)

→ `scripts/probe_hug_api.py`를 **GitHub Actions 러너 또는 국내 IP**에서 실행한다.

```bash
# 로컬(국내 IP)
export HUG_API_KEY=<발급받은 서비스키>
python scripts/probe_hug_api.py     # 결과: probe_output/

# GitHub Actions — monthly-collect 워크플로우 수동 실행
#   probe_script = probe_hug_api.py   (Secrets에 HUG_API_KEY 등록 필요)
```

probe가 확인하는 것: ①khug.or.kr 접근 가능 여부 ②유효 `AREA_DCD` 코드
③`BUNO` 상세 응답 필드(★유형 필드 존재 여부) ④OPEN API 목록의 오피스텔·상가 API.

## 5. 조회 도구

`src/hug_guarantee.py` — 사양서대로 구현한 클라이언트 + CLI.

```bash
export HUG_API_KEY=<서비스키>
python -m src.hug_guarantee --areas 02 --categories officetel,retail
python -m src.hug_guarantee --areas all --categories all --detail --out output/hug.csv
```

- `--areas all`: 코드표가 없어 `00`~`30` 후보를 순회하고 실패 코드는 건너뛴다.
- `--detail`: 사업장마다 `BUNO` 상세를 호출해 필드를 보강(미지 필드 전부 보존).
- 출력 CSV: `AREA_DCD,BUNO,BSU_NM,CUST_NM1,CUST_NM2,ADDR,category,category_label,category_basis`
  + 상세에서 발견된 추가 컬럼.

## 6. 이 저장소와의 관계

본 저장소는 **공공 발주 공사 입찰·계약 추적기**로, 스키마(공고/낙찰/계약)와
수집 주기가 분양보증과 맞지 않는다. 그래서 HUG는 `src/adapters/`의 어댑터가 아닌
**독립 조회 도구**(`src/hug_guarantee.py`)로 구현했고 `run_monthly` 파이프라인에
연결하지 않았다. 정기 수집이 필요해지면 별도 테이블·별도 워크플로우로 확장할 것.

## 7. 다음 단계

1. ⚠️**[사용자]** HUG OPEN API 서비스키 발급 → `HUG_API_KEY` (Actions Secret 등록)
2. probe 실행 → §3 미확정 5개 항목 확정, 본 문서 갱신
3. 상세/다른 API에 유형 필드가 있으면 `CATEGORY_KEYWORDS` 추정 로직을
   **필드 기반 확정 로직으로 대체**, `tests/fixtures/hug/detail.xml`을 실응답으로 교체
