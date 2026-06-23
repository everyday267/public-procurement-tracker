# PRD: 공공발주 규모 모니터링 시스템 (Public Procurement Tracker)

**문서버전:** v1.3  
**작성일:** 2026-06-23  
**상태:** Review  
**Owner:** 전략영업부

**v1.3 변경요약:**
- Phase 1에 국가철도공단(KR) 추가 (Phase 1 = 나라장터 + LH + 한전 + 국가철도공단)
- 국가철도공단은 자체 OpenAPI 없음 → 나라장터 G2B OpenAPI (`instNm=국가철도공단`) 경로로 수집
- Phase 2, 3은 Phase 1 안정화 후 진행
- 각 어댑터별 역할 명확화

---

## 1. 배경

자사 공사이행 이력에서 확인된 주요 발주처 14개의 실제 공공발주 규모를 정량 추적하고,
영업기획·여신심사·시장규모 추정의 근거 데이터로 활용한다.

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

| # | 발주처 | 수집 방법 | 어댑터 | API 키 시크릿 |
|---|---|---|---|---|
| 1 | 조달청(나라장터) | G2B OpenAPI (공개표준) | `g2b_opnstd.py` | `G2B_API_KEY` |
| 2 | 한국토지주택공사(LH) | LH e-Bid 자체 OpenAPI | `lh.py` | `LH_API_KEY` |
| 3 | 한국전력공사(KEPCO) | srm.kepco.net XHR | `kepco.py` | — |
| 4 | 국가철도공단(KR) | **나라장터 G2B OpenAPI** (`instNm` 필터) | `kr_rail.py` | `G2B_API_KEY` 공유 |

> **국가철도공단 수집 경로 결정 근거:**  
> ebid.kr.or.kr 자체 OpenAPI 없음. 공공데이터포털에 파일 데이터셋(CSV)만 제공되어 자동화 부적합.  
> 국가철도공단은 나라장터를 통해 공고를 게재하므로 G2B OpenAPI에서 `instNm=국가철도공단` 필터로 수집 가능.  
> 단, 자체 전자조달시스템(ebid.kr.or.kr)에 게재하는 건은 나라장터에 미게재될 수 있어 커버리지 모니터링 필요.

### Phase 2, 3 — Phase 1 안정화 후 진행

- **Phase 2**: 한수원·도공·수공·동서발전·중부발전 (5개)
- **Phase 3**: SH·부산항만·인천공항·가스·환경 (5개)

## 4. 수집 기준

- **work_type**: 공사만 수집 (종합·전문)
- **추정가격**: ≥ 100억원 (VAT 제외)
- **추정가격 누락**: `notices_unpriced` 별도 테이블 격리
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
- 국가철도공단 필터: `instNm=국가철도공단` 또는 기관코드 필터
- 공사 필터: `cntrctCnclsMthdNm`, `bsnsDivNm=공사`
- 장기계속: `cntrctCnclsMthdNm` 키워드 매칭

## 7. 아키텍처

```
GitHub Actions (monthly cron, 매월 1일 07:00 KST)
  → run_monthly.py
      → G2BOpnStdAdapter  (나라장터 — 조달청 전체)
      → KRRailAdapter     (나라장터 — 국가철도공단 필터)
      → LHAdapter         (LH 자체 OpenAPI 3종)
      → KEPCOAdapter      (srm.kepco.net)
      → 각 어댑터 → normalize → passes_filter
      → SQLite (procurement.db)
      → output/YYYYMM/*.csv
```

## 8. 실행

```bash
# 환경변수 설정
export G2B_API_KEY=나라장터_서비스키
export LH_API_KEY=LH_서비스키

# 월간 수집 실행
python -m src.run_monthly --month 2026-05

# 특정 어댑터만 실행
python -m src.run_monthly --month 2026-05 --sources lh,kr_rail
```

## 9. 현재 진행 현황 (Phase 1)

| 어댑터 | 코드 | 테스트 | 실서비스 검증 |
|---|:---:|:---:|:---:|
| G2B (`g2b_opnstd.py`) | ✅ | ✅ | 🔲 |
| LH (`lh.py`) | ✅ | 🔲 | 🔲 |
| KEPCO (`kepco.py`) | 🔲 | 🔲 | 🔲 |
| KR Rail (`kr_rail.py`) | ✅ | 🔲 | 🔲 |

## 10. 리스크

| 리스크 | 영향도 | 대응 |
|---|---|---|
| 국가철도공단 자체 게재 건 나라장터 미등록 | Medium | ebid.kr.or.kr 커버리지 주기적 샘플 비교 |
| LH API 필드명 실서비스 불일치 | High | 실서비스 키로 호출 검증 후 확정 |
| KEPCO srm 로그인 필요 여부 | High | 비로그인 영역 우선 확인 |
| 스키마 변경 | High | source_hash 모니터링 + GitHub Issue 자동 생성 |

## 11. 승인

| 역할 | 이름 | 승인일 |
|---|---|---|
| Owner | | |
| Reviewer | | |
