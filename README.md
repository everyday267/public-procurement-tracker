# Public Procurement Tracker

16개 발주처의 공사(종합·전문) 입찰공고·낙찰·계약 정보를 월 1회 자동 수집하는 시스템입니다 (PRD v1.4 기준).

## 수집 기준
- work_type: **공사만** (종합·전문)
- 추정가격: **≥ 100억원 (VAT 제외)**
- 주기: 매월 1일 07:00 KST (전월 1일~말일)
- 분기 백필: 매분기 첫째달 1일 09:00 KST (직전 3개월)

## Phase
- Phase 0: 인프라 — 완료
- Phase 1: 나라장터 + LH + 한전(KEPCO) + 국가철도공단 — **진행 중** (아래 "현재 진행 현황" 참고)
- Phase 2: 도공(EX)·수공(KWATER)·가스(KOGAS)·한수원(KHNP) + 발전 5사(EWP·KOMIPO·KOSPO·KOEN·KOWEPO)
- Phase 3: SH·GH(경기주택도시공사)·환경(KECO, G2B 커버리지 검증만)
- Phase 4: 발주계획(plans)

## 현재 진행 현황 (Phase 1)

| 어댑터 | 코드 | run_monthly 연동 | 실서비스 검증 |
|---|:---:|:---:|:---:|
| 나라장터 (`g2b_opnstd.py`) | ✅ | ✅ | 🔲 |
| LH (`lh.py`) | ✅ | ✅ | 🔲 |
| 국가철도공단 (`kr_rail.py`, 나라장터 경유) | ✅ | ✅ | 🔲 |
| 한전 KEPCO (`kepco.py`, 공공데이터포털 OpenAPI) | ✅ | ✅ | 🔲 |

> KEPCO는 한전 빅데이터플랫폼 "전자입찰 계약정보" OpenAPI
> (`bigdata.kepco.co.kr/openapi/v1/electContract.do`)로 수집합니다 (명세 확정,
> 기간 최대 90일 → 자동 분할). 이 API는 입찰공고 전용이라 낙찰·계약은 G2B
> 계약정보(`dmndInsttNm=한국전력공사`)로 보완하며, srm.kepco.net XHR은 커버리지
> 미달 시 폴백입니다. `KEPCO_API_KEY` 미설정 시 해당 소스는 경고 후 skip 됩니다.
> 같은 API가 `companyId`로 발전 자회사(서부·남부·중부·남동·동서발전)도 지원하므로
> Phase 2 발전 5사(Wave B)는 이 API를 재사용합니다 — `src/adapters/kepco_family.py`의
> 5개 서브클래스(ewp·komipo·kospo·koen·kowepo)가 companyId만 바꿔 수집하며,
> KEPCO_API_KEY를 공유합니다. KHNP는 보류 상태입니다 (조사 문서 참고).

## 실행

```bash
pip install -r requirements.txt
export G2B_API_KEY=...
export LH_API_KEY=...
export KEPCO_API_KEY=...
python -m src.run_monthly --month 2026-05

# 특정 소스만 실행
python -m src.run_monthly --month 2026-05 --sources lh,kr_rail

# 짧은 임의 기간으로 빠르게 스모크 테스트 (전국 계약 수집이 무거우므로
# 먼저 좁은 기간으로 정상 동작을 확인한 뒤 기간을 넓히는 것을 권장)
python -m src.run_monthly --since 2026-06-01 --until 2026-06-07 --sources g2b_opnstd
```

> **성능 참고:** 나라장터 개방표준(OpnStd) 계약 API는 서버측 기관/공사 필터를
> 제공하지 않아 대상 기간의 전국 계약을 순회해야 합니다. `g2b_opnstd`/`kr_rail`은
> 필터된 공고(공사 100억↑)의 `bidNtceNo`로 좁혀 조회를 시도하되, 서버가 그 필터를
> 지원하지 않으면 전국 순회로 폴백합니다. 따라서 월 단위 실행은 수십 분이 걸릴 수
> 있습니다(월 1회 배치라 GitHub Actions 6시간 한도 내에서 문제없음). 동작 검증은
> 위처럼 `--since/--until`로 짧은 기간부터 하는 것을 권장합니다.

`run_monthly`는 지정된 모든 소스를 순회하며 하나가 실패해도 나머지는 계속 진행합니다
(소스별 성공/실패는 `source_runs` 테이블에 기록). 소스별 CSV(`output/{source}_joined_YYYYMM.csv`)와
전체 통합 CSV(`output/all_joined_YYYYMM.csv`)가 함께 생성됩니다.

## GitHub Actions 시크릿

| 시크릿 | 용도 |
|---|---|
| `G2B_API_KEY` | 나라장터 + 국가철도공단(나라장터 경유) |
| `LH_API_KEY` | LH e-Bid 자체 OpenAPI |
| `KEPCO_API_KEY` | 한전 전자입찰계약정보 OpenAPI (공공데이터포털 발급) |
| `EX_API_KEY` | 한국도로공사 전자조달 계약공개현황 OpenAPI (data.ex.co.kr 발급) |
| `KISCON_API_KEY` | 키스콘 건설공사대장 통보 통계 OpenAPI (공공데이터포털 발급, KISCON 대조 검증용) |
| `KOSIS_API_KEY` | KOSIS 건설업 통계 OpenAPI (kosis.kr 발급, 종합·전문·전기 공사규모별 계약실적) |

시크릿이 Settings → Secrets and variables → Actions에 등록되어 있어야
`monthly-collect` / `quarterly-backfill` 워크플로우가 정상 동작합니다.

## KISCON 대조 검증 (검증 층 2)

월간 수집 후 `validate_kiscon`이 우리 계약 합계를 KISCON(건설공사대장 통보 통계,
`ConStatInfoSvc`) 공공×원도급 집계와 대조합니다. 우리 DB는 100억↑ 부분집합이므로
`ratio = 우리 ÷ KISCON < 1`이 불변식이며, 위반(`RATIO_GE_1`)·밴드 이탈(`OUT_OF_BAND`)
등의 플래그는 `kiscon_recon` 테이블에 기록되고 schema-monitor 워크플로우가
`kiscon-validation` 라벨 이슈로 등록합니다.

```bash
export KISCON_API_KEY=...
python -m src.validate_kiscon --db procurement.db --month 2026-06
python -m src.validate_kiscon --db procurement.db --skip-fetch   # 수집 생략, 대조만
```

- 산출물: `output/kiscon_recon_{label}.csv` / `.md` (monthly-collect 로그에도 전문 출력)
- 건별 대조(L2)·모집단 추정은 건별 리스트 오퍼레이션 확정 후 활성화됩니다:
  `scripts/probe_kiscon.py`를 probe_script로 디스패치해 엔드포인트를 확정하고,
  리포지토리 변수 `KISCON_RECORDS_OP`에 오퍼레이션명을 등록하면 수집이 켜집니다.

## KOSIS 건설업 통계 (검증 보조 소스)

KISCON `StatAmt`에 없던 **공사규모(금액구간)** 축을 KOSIS 건설협회 통계에서
보완합니다. 종합·전문·전기 건설업의 공사규모별 × 발주기관별 계약실적을
`kosis_stats` 테이블에 long-format으로 저장합니다 (표마다 분류축 순서가 달라
축이름 `Cn_OBJ_NM`을 함께 보관하고 이름으로 매핑).

| 표 | orgId / tblId | 항목 |
|---|---|---|
| 종합건설업 | 365 / DT_365001_A072 | 계약액·계약건수 |
| 전문건설업 | 366 / TX_36601_A089 | 계약액·계약건수 |
| 전기공사업 | 370 / DT_370001_A010 | 공사건수·실적액 |

```bash
export KOSIS_API_KEY=...
python -m src.kosis --db procurement.db                 # 3종 전체 수집 + 축 요약
python -m src.kosis --db procurement.db --tables gen --prd-se M --periods 12
python -m src.kosis --db procurement.db --skip-fetch    # 저장분 축 요약만
```

> **⚠️ 네트워크 제약 (실측 확인됨):** kosis.kr은 **해외 IP의 443 연결을
> 차단**한다. GitHub 호스티드 러너(미국)에서 probe 실행 시 3개 표 모두
> `ConnectTimeout`으로 실패했다 (data.go.kr 게이트웨이는 러너에서 열려 KISCON은
> 정상). 따라서 KOSIS 수집은 **한국 IP 경로**가 필요하다:
> - **로컬 실행**: 한국 소재 PC/서버에서 위 CLI를 직접 실행 (키·코드 그대로 동작)
> - **KR 프록시**: 한국 소재 HTTPS 프록시를 `KOSIS_HTTPS_PROXY` 시크릿으로
>   등록하면 monthly-collect의 KOSIS 스텝이 자동으로 그걸 통해 호출한다
>   (requests가 `HTTPS_PROXY` 환경변수를 자동 사용). 로컬에서도
>   `HTTPS_PROXY=http://<kr-proxy> python -m src.kosis ...`로 동일하게 가능.
> - **KR self-hosted 러너**: 한국 소재 러너를 수집 job에 지정

- 표별 분류축(어느 C가 공사규모/발주기관인지)·100억↑ 구간 존재 여부는
  `scripts/probe_kosis.py`를 probe_script로 디스패치해 실측 확인합니다
  (위 KR 경로 확보 후 — 미국 러너에서는 타임아웃).
- 저장 후 `scale_agency_summary()`가 공사규모 × 발주기관 피벗을 이름 기반으로
  산출합니다. `python -m src.validate_kosis`가 **우리 100억↑ 공공 계약액 ↔ KOSIS
  종합 100억↑ 공공 계약액**을 연도별로 대조해 `kosis_recon` 테이블·리포트를 남깁니다.

**실측으로 확인된 표별 특성 (2024년 기준):**

| 산업 | 공사규모 구간 | 100억↑ | 발주기관(공공) |
|---|---|---|---|
| 종합(365) | 범위형(`100~200억미만`…`1000억이상`) | ✅ 있음 (연 176.98조) | 정부기관·지자체·공공단체·공기업 |
| 전문(366) | `TX_36601_A083` (100억↑ 구간 포함 표) | ✅ 있음 | 동일 |
| 전기(370) | `N억이상` 라벨이나 값은 **배타적 구간**(disjoint) | ✅ 있음 (연 14.04조) | +한국전력 |

- **월별이 분류축(C3)** 이라 `합계+월` 이중계상 방지 처리(연간=월 합계만).
- **구간 스킴은 데이터로 판별**(`_detect_scheme`): 각 구간 합이 `합계`와 같으면
  배타적(disjoint, 합산) · 훨씬 크면 중첩(cumulative, 단일 구간). 라벨(`N이상`)
  추정 금지 — 전기는 라벨이 `이상`이지만 값이 배타적이라 disjoint로 잡힌다.
- **공공 집합**: `{정부기관, 지방자치단체, 공공단체, 공기업}` (`kosis.PUBLIC_AGENCIES`).
- 대조는 **종합+전문 합산 100억↑ 공공** 기준(우리 모집단과 apples-to-apples).
  정밀 일치가 아닌 자릿수·추세 확인용(설계 §4.1).
- **kosis.kr 대용량 호출이 간헐적으로 `objL 누락` 오탐 오류를 냄** → 표 단위
  재시도 후 **연도별 개별 수집(`startPrdDe=endPrdDe`)으로 폴백**해 다년치를
  확보(`--periods 10` 기본). 단년 요청은 작아 대체로 성공하므로 종합·전기도
  10년치 백필된다(개별 연도 실패는 건너뜀).

## 디렉토리
```
src/
  adapters/        사이트별 어댑터 (base, scraper_base, lh, g2b_opnstd, kr_rail, kepco, kwater, ex, kogas)
  db.py            SQLite 스키마/연결
  normalizer.py
  long_term_detector.py
  schema_monitor.py  어댑터 장애·스키마 변경 감지 (schema-monitor 워크플로우)
  run_monthly.py
config/agencies.yaml
tests/
.github/workflows/
docs/PRD_public_procurement_tracker_v1.4.md
```
