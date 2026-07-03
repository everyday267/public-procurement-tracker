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

| 역할 | 이름 | 승인일 |
|---|---|---|
| Owner | | |
| Reviewer | | |
