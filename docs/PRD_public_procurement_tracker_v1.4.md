# PRD: 공공발주 규모 모니터링 시스템 (Public Procurement Tracker)

**문서버전:** v1.4
**작성일:** 2026-07-03
**상태:** Review
**Owner:** 전략영업부

**v1.4 변경요약 — 2026-06-28~07-01 실행 실패 원인 조사 및 수정:**
- `LH_API_KEY`가 `monthly.yml`/`quarterly_backfill.yml`에 전달되지 않아 매 실행이 즉시 실패하던 문제 수정
- `run_monthly.py`가 LH 어댑터만 실행하던 것을 G2B(`g2b_opnstd.py`)·KR Rail(`kr_rail.py`)까지 포함하도록 재구성
- `kr_rail.py`의 `notice_id`/`agency_code` 재라벨링이 문자열 불일치로 동작하지 않아 KR Rail 건이 G2B 소스로 섞여 저장되던 버그 수정
- `monthly.yml` cron이 28~31일 매일 실행되어 월 3~4회 중복 수집되던 문제에 날짜 가드 추가 (매월 1일 0시 UTC 부근만 실제 수집)
- 존재하지 않던 `src/schema_monitor.py`를 구현 (`schema_monitor.yml`이 항상 실패하던 문제 해결) — `source_runs` 기반 어댑터 장애 감지 + raw_payload 핵심 필드 소실 기반 스키마 변경 의심 감지, GitHub Issue 자동 등록
- `db.py`/`test_db.py` 불일치(함수명, `notices_unpriced`/`notice_revisions` 테이블 부재)로 `pytest` 전체가 수집 단계에서 실패하던 문제 수정
- `notices_unpriced` 실제 적재 반영 (기존에는 카운트만 로그로 남기고 버려짐)

---

## 1. 배경

자사 공사이행 이력에서 확인된 주요 발주처 14개의 실제 공공발주 규모를 정량 추적하고,
영업기획·여신심사·시장규모 추정의 근거 데이터로 활용한다. 최종 목적 중 하나는
**추정가격 100억원 이상 공사계약 중 공사이행보증서 발급대상 규모를 파악**하는 것이다.

기존 수기 집계의 한계:
- 나라장터 미게재 자체조달 건 다수 (LH·한전 등)
- 과거 이력(3년 초과) 백필 불가
- 장기계속계약 식별이 수기로만 가능

## 2. 수집 목표

| KPI | 목표값 |
|---|---|
| Phase 1 발주처 4개 공사 공고 커버리지 (추정가격 100억 이상) | ≥ 95% |
| 수집 주기 | 월 1회 (매월 1일 07:00 KST) |
| 백필 기간 | 사이트별 보존기간 최대치 |
| 어댑터 장애 자동 감지율 | 100% |

## 3. Phase 정의

### Phase 1 — MVP (현재 진행 중)

| # | 발주처 | 수집 방법 | 어댑터 | API 키 시크릿 | run_monthly 연동 |
|---|---|---|---|---|:---:|
| 1 | 조달청(나라장터) | G2B OpenAPI (공개표준) | `g2b_opnstd.py` | `G2B_API_KEY` | ✅ |
| 2 | 한국토지주택공사(LH) | LH e-Bid 자체 OpenAPI | `lh.py` | `LH_API_KEY` | ✅ |
| 3 | 한국전력공사(KEPCO) | srm.kepco.net XHR | `kepco.py` (미착수) | — | 🔲 |
| 4 | 국가철도공단(KR) | **나라장터 G2B OpenAPI** (`instCd` 필터) | `kr_rail.py` | `G2B_API_KEY` 공유 | ✅ |

> **KEPCO는 여전히 어댑터 코드가 없다.** srm.kepco.net이 로그인/세션을 요구하는지 여부부터
> 실제 브라우저 네트워크 탭으로 확인이 필요하며, 확인 전에는 스크래핑 로직을 임의로 작성하지 않는다.

### Phase 2, 3 — Phase 1 안정화 후 진행

- **Phase 2**: 한수원·도공·수공·동서발전·중부발전 (5개)
- **Phase 3**: SH·부산항만·인천공항·가스·환경 (5개)

## 4. 수집 기준

- **work_type**: 공사만 수집 (종합·전문)
- **추정가격**: ≥ 100억원 (VAT 제외)
- **추정가격 누락**: `notices_unpriced` 테이블에 격리 적재 (v1.4부터 실제 적재됨)
- **장기계속계약**: `is_long_term_continuing` 플래그로 식별

## 5. LH 수집 명세 (자체 OpenAPI)
**문서버전:** v1.4  
**작성일:** 2026-07-03  
**상태:** Draft  
**Owner:** 전략영업부

**v1.4 변경요약 (vs v1.3):**
- 수집 대상 발주처를 14개 → **16개**로 확장
- Phase 2에 발전 5사 전체(동서·중부·남동·남부·서부) 포함 확정
- Phase 3에 GH(경기주택도시공사) 추가
- 발주처별 연간 발주규모·조달시스템 유형 명세 추가
- 시장규모 추정 목적 및 KPI 구체화

---

## 1. 배경 및 목적

서울보증보험 전략영업부는 공사이행보증서의 **전체 시장규모**를 정량적으로 파악하기 위해, 추정가격 100억원 이상 공공 공사계약의 공고·낙찰·유찰 데이터를 자동 수집한다.

### 1.1 필요성

- 나라장터(G2B)에 미게재되는 자체조달 건이 다수 존재 (LH·한전·도공·발전사 등)
- 수기 집계로는 발주규모 연도별 추이·기관별 비중 파악 불가
- 공사이행보증서 시장의 발주처별 커버리지를 전략영업 근거로 활용

### 1.2 활용 방안

- 연간 시장 총규모 추정 (추정가격 합산)
- 발주처별 낙찰률·유찰률 분석
- 장기계속계약 물량 별도 추적
- 영업기획·여신심사 의사결정 근거 데이터

---

## 2. 수집 대상 기관 (16개)

발주규모 순서 기준 (2024년 조달청 시설공사 발주계획 분석자료).  
**★ = 나라장터와 별개의 고유 조달시스템 운영**

| Phase | # | 기관코드 | 기관명 | 연간 발주규모(추정) | 조달시스템 유형 |
|:---:|---:|---|---|---:|:---:|
| 1 | — | G2B | 나라장터 (조달청 경유 전체) | 27조 1,749억원 | 플랫폼 |
| 1 | 1 | KR_RAIL | 국가철도공단 | 6조 1,056억원 | ★ 자체 (G2B 병행) |
| 1 | 2 | KEPCO | 한국전력공사 | 4조 2,912억원 | ★ 자체 (srm.kepco.net) |
| 1 | 3 | LH | 한국토지주택공사 | 별도 집계 | ★ 자체 (ebid.lh.or.kr) |
| 2 | 4 | EX | 한국도로공사 | 3조 6,338억원 | ★ 자체 |
| 2 | 5 | KWATER | 한국수자원공사 | 2조 7,955억원 | ★ 자체 |
| 2 | 6 | KOGAS | 한국가스공사 | 1조 5,247억원 | ★ 자체 |
| 2 | 7 | KHNP | 한국수력원자력 | 1조 3,151억원 | ★ 자체 |
| 2 | 8 | EWP | 한국동서발전 | — | ★ 자체 |
| 2 | 9 | KOMIPO | 한국중부발전 | — | ★ 자체 |
| 2 | 10 | KOSPO | 한국남부발전 | — | ★ 자체 |
| 2 | 11 | KOEN | 한국남동발전 | — | ★ 자체 |
| 2 | 12 | KOWEPO | 한국서부발전 | — | ★ 자체 |
| 3 | 13 | SH | 서울주택도시공사 | 6,892억원 | ★ 자체 |
| 3 | 14 | GH | 경기주택도시공사 | 5,124억원 | ★ 자체 |
| 3 | 15 | KECO | 한국환경공단 | 9,228억원 | G2B 이용 |

> **나라장터(G2B) 수집 범위:**  
> G2B 어댑터는 조달청을 통한 모든 기관 공고를 포함하므로, 위 16개 기관 중 G2B를 이용하는 기관(KECO, 한국농어촌공사 등)은 G2B 수집으로 커버된다.

---

## 3. 수집 기준

| 항목 | 기준 |
|---|---|
| 공사 유형 | 공사만 (종합·전문) |
| 추정가격 | ≥ 100억원 (VAT 제외) |
| 추정가격 누락 건 | `notices_unpriced` 테이블 별도 격리 |
| 장기계속계약 | `is_long_term_continuing` 플래그 식별 |
| 수집 주기 | 매월 1일 07:00 KST (전월 전체) |
| 분기 백필 | 매분기 첫째달 1일 09:00 KST (직전 3개월) |

---

## 4. Phase 정의

### Phase 1 — MVP (진행 중)

| # | 발주처 | 수집 방법 | 어댑터 파일 | API 시크릿 | 상태 |
|---|---|---|---|---|:---:|
| 1 | 나라장터(G2B) | G2B OpenAPI (공공데이터 개방표준) | `g2b_opnstd.py` | `G2B_API_KEY` | ✅ 코드 완료 |
| 2 | LH | LH e-Bid 자체 OpenAPI 3종 | `lh.py` | `LH_API_KEY` | ✅ 코드 완료 |
| 3 | 한국전력공사 | srm.kepco.net XHR | `kepco.py` | — | 🔲 개발 중 |
| 4 | 국가철도공단 | G2B OpenAPI (`instNm` 필터) | `kr_rail.py` | `G2B_API_KEY` 공유 | ✅ 코드 완료 |

> **국가철도공단 수집 경로 결정 근거:**  
> 자체 OpenAPI 없음. 나라장터를 통해 공고를 게재하므로 G2B API `instNm=국가철도공단` 필터로 수집.  
> 자체 전자조달시스템(ebid.kr.or.kr) 독자 게재 건은 커버리지 주기적 샘플 비교로 모니터링.

### Phase 2 — 대형 SOC·발전 (Phase 1 안정화 후)

| # | 발주처 | 주요 수집 방법(예정) | 어댑터 |
|---|---|---|---|
| 4 | 한국도로공사 | EX 전자조달 (exway.co.kr) XHR/스크래핑 | `ex.py` |
| 5 | 한국수자원공사 | K-water 전자조달 OpenAPI/스크래핑 | `kwater.py` |
| 6 | 한국가스공사 | KOGAS 입찰정보 스크래핑 | `kogas.py` |
| 7 | 한국수력원자력 | KHNP 전자조달 스크래핑 | `khnp.py` |
| 8 | 한국동서발전 | 자체 전자조달 스크래핑 | `ewp.py` |
| 9 | 한국중부발전 | 자체 전자조달 스크래핑 | `komipo.py` |
| 10 | 한국남부발전 | 자체 전자조달 스크래핑 | `kospo.py` |
| 11 | 한국남동발전 | 자체 전자조달 스크래핑 | `koen.py` |
| 12 | 한국서부발전 | 자체 전자조달 스크래핑 | `kowepo.py` |

> **발전 5사 공통 특성:**  
> 한국전력 계열 발전 자회사로 각사별 독립 전자조달시스템 운영.  
> 발전소 토목·건축·기계 공사 발주가 주를 이루며, 공사이행보증서 발행 수요가 높음.

### Phase 3 — 도시·환경 (Phase 2 안정화 후)

| # | 발주처 | 주요 수집 방법(예정) | 어댑터 |
|---|---|---|---|
| 13 | 서울주택도시공사(SH) | SH 전자조달 (i-sh.co.kr) 스크래핑 | `sh.py` |
| 14 | 경기주택도시공사(GH) | GH 입찰정보 (gh.or.kr) 스크래핑 | `gh.py` |
| 15 | 한국환경공단 | G2B 수집으로 커버 (별도 어댑터 불필요) | G2B 통합 |

---

## 5. 수집 명세

### 5.1 LH 자체 OpenAPI

| 엔드포인트 | 파라미터 | 용도 |
|---|---|---|
| `OpenBidInfoList.dev` | `tndrbidRegDtStart/End` | 입찰공고 |
| `OpenTenderopenList.dev` | `openDtmStart/End` | 개찰결과 |
| `OpenContractInfoList.dev` | `contractDtStart/End` | 계약현황 |

- 조인 키: `bidNum`
- 예정가격: 개찰결과 `expectPrc` 사용
- 종심제: `sunjungNm`
- 공종/면허제한: `req1~10LicGbNm`, `req*Reqlic*Nm`, `vndrrstrctNm1~4`

## 6. G2B 수집 명세 (나라장터 — KR 포함)

- 엔드포인트: `PubDataOpnStdService` (공공데이터 개방표준)
- 국가철도공단 필터: `KRRailAdapter`가 `G2BOpnStdAdapter`를 상속해 `instCd=5270000`(또는 `instNm` 포함) 응답만 통과시킴
- 공사 필터: `bsnsDivNm` / `bidNtceNm`에 "공사" 포함 여부
- 장기계속: `cntrctCnclsMthdNm` 등 키워드 매칭 (`long_term_detector.py`)

## 7. 아키텍처

```
GitHub Actions (monthly cron, 매월 1일 07:00 KST 부근에서만 날짜 가드 통과)
  → run_monthly.py
      → SOURCES = {lh, g2b_opnstd, kr_rail}  (소스별로 독립 try/except)
      → 각 어댑터 → fetch_notices/awards/contracts → normalize → passes_filter
      → SQLite (procurement.db: notices / notices_unpriced / awards / contracts / source_runs)
      → output/{source}_joined_YYYYMM.csv + output/all_joined_YYYYMM.csv
  → (아티팩트 업로드)
  → schema_monitor.py (workflow_run 트리거)
      → 아티팩트에서 procurement.db 다운로드
      → source_runs 최신 실행 상태 확인 (장애 감지)
      → notices.raw_payload 핵심 필드 소실 여부 확인 (스키마 변경 의심)
      → 문제 발견 시 GitHub Issue 자동 생성 (label: schema-monitor)
```

## 8. 실행

```bash
# 환경변수 설정
export G2B_API_KEY=나라장터_서비스키
export LH_API_KEY=LH_서비스키

# 월간 수집 실행 (전체 소스)
python -m src.run_monthly --month 2026-05

# 특정 소스만 실행
python -m src.run_monthly --month 2026-05 --sources lh,kr_rail
```

## 9. 현재 진행 현황 (Phase 1)

| 어댑터 | 코드 | 테스트 | run_monthly 연동 | 실서비스 검증 |
|---|:---:|:---:|:---:|:---:|
| G2B (`g2b_opnstd.py`) | ✅ | ✅ | ✅ | 🔲 |
| LH (`lh.py`) | ✅ | 🔲 | ✅ | 🔲 |
| KEPCO (`kepco.py`) | 🔲 | 🔲 | 🔲 | 🔲 |
| KR Rail (`kr_rail.py`) | ✅ | 🔲 | ✅ | 🔲 |

## 10. 리스크

| 리스크 | 영향도 | 대응 |
|---|---|---|
| 국가철도공단 자체 게재 건 나라장터 미등록 | Medium | ebid.kr.or.kr 커버리지 주기적 샘플 비교 |
| LH API 필드명 실서비스 불일치 | High | 실서비스 키로 호출 검증 후 확정 |
| KEPCO srm 로그인 필요 여부 | High | 비로그인 영역 우선 확인 (미착수) |
| 스키마 변경 | High | source_hash 모니터링 + GitHub Issue 자동 생성 (v1.4에서 구현 완료) |
| 어댑터 부분 장애가 전체 실행을 막음 | Medium | v1.4에서 소스별 try/except로 격리, 나머지 소스는 계속 진행 |

## 11. 다음 액션 (실서비스 키 확보 후)

1. `LH_API_KEY`를 GitHub repo secrets에 등록 (Settings → Secrets and variables → Actions)
2. `workflow_dispatch`로 `monthly.yml` 수동 실행 → 실제 API 응답으로 필드명 검증
3. KEPCO srm.kepco.net 네트워크 탭 확인 → 어댑터 착수 여부 결정
4. `notice_revisions` 실제 적재 로직 설계 (현재는 스키마만 존재, 공고 정정 이력 추적은 미착수)

## 12. 승인
- 공종/면허제한: `req1~10LicGbNm`, `vndrrstrctNm1~4`

### 5.2 G2B OpenAPI (나라장터 — KR_RAIL 포함)

- 엔드포인트: `PubDataOpnStdService` (공공데이터 개방표준)
- 국가철도공단 필터: `instNm=국가철도공단` 또는 기관코드 필터
- 공사 필터: `bsnsDivNm=공사`
- 장기계속: `cntrctCnclsMthdNm` 키워드 매칭

---

## 6. 아키텍처

```
GitHub Actions (monthly cron)
  ├── 매월 1일 07:00 KST  → run_monthly.py
  └── 분기 첫째달 1일 09:00 KST → run_monthly.py (backfill 3개월)

run_monthly.py
  ├── Phase 1 어댑터
  │     ├── G2BOpnStdAdapter   (나라장터 전체)
  │     ├── KRRailAdapter      (나라장터 국가철도공단 필터)
  │     ├── LHAdapter          (LH 자체 OpenAPI 3종)
  │     └── KEPCOAdapter       (srm.kepco.net)
  ├── Phase 2 어댑터 (예정)
  │     └── EX / KWATER / KOGAS / KHNP / 발전5사
  └── Phase 3 어댑터 (예정)
        └── SH / GH / KECO(G2B 통합)

각 어댑터 → normalize() → passes_filter() → SQLite(procurement.db)
                                            → output/YYYYMM/*.csv
```

---

## 7. KPI

| KPI | Phase 1 목표 | Phase 2~3 목표 |
|---|---:|---:|
| 발주처 커버리지 (추정가격 100억↑) | ≥ 95% (4개 기관) | ≥ 90% (16개 기관) |
| 수집 주기 | 월 1회 | 월 1회 |
| 어댑터 장애 자동 감지율 | 100% | 100% |
| 시장 총규모 추정 커버 (연간 발주액 기준) | ~60% | ~85% |

---

## 8. 리스크

| 리스크 | 영향도 | 대응 |
|---|:---:|---|
| 국가철도공단 자체 게재 건 G2B 미등록 | Medium | ebid.kr.or.kr 월간 샘플 비교 |
| LH API 필드명 실서비스 불일치 | High | 실서비스 키 검증 후 스키마 확정 |
| KEPCO srm 로그인 세션 필요 | High | 비로그인 영역 우선, 필요 시 세션 방식 전환 |
| 발전 5사 스크래핑 차단 | Medium | User-Agent 로테이션, 요청 간격 조정 |
| 스키마 변경 | High | source_hash 모니터링 + GitHub Issue 자동 생성 |

---

## 9. 현재 진행 현황 (Phase 1)

| 어댑터 | 코드 | 단위 테스트 | 실서비스 검증 |
|---|:---:|:---:|:---:|
| `g2b_opnstd.py` (나라장터) | ✅ | ✅ | 🔲 |
| `lh.py` (LH) | ✅ | 🔲 | 🔲 |
| `kepco.py` (한전) | 🔲 | 🔲 | 🔲 |
| `kr_rail.py` (국가철도공단) | ✅ | 🔲 | 🔲 |

---

## 10. 승인

| 역할 | 이름 | 승인일 |
|---|---|---|
| Owner | | |
| Reviewer | | |
