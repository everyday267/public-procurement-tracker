# PRD: 공공발주 규모 모니터링 시스템 (Public Procurement Tracker)

**문서버전:** v1.2  
**작성일:** 2026-06-17  
**상태:** Review  
**Owner:** Credit Analysis / Quant Engineering

**v1.2 변경요약:**
- LH OpenAPI 3개 엔드포인트 확정 (입찰공고 / 개찰결과 / 계약현황)
- 예정가격: 개찰결과 API `expectPrc` 사용 (별도 예정가격 API 신청 불필요)
- 종심제 여부: 입찰공고 `sunjungNm` 필드로 판별
- 공종/면허제한: 입찰공고 `req1~10LicGbNm`, `req*Reqlic*Nm`, `vndrrstrctNm1~4` 수집
- `bidNum` 기준 공고-낙찰-계약 3단계 조인 확정

---

## 1. 배경

자사 공사이행 이력에서 확인된 주요 발주처 14개의 실제 공공발주 규모를 정량 추적하고,
영업기획·여신심사·시장규모 추정의 근거 데이터로 활용한다.

## 2. 수집 목표

| KPI | 목표값 |
|---|---|
| 14개 발주처 공사 공고 커버리지 (추정가격 100억 이상) | ≥ 95% |
| 수집 주기 | 월 1회 (매월 1일 07:00 KST) |
| 백필 기간 | 사이트별 보존기간 최대치 |

## 3. LH OpenAPI 명세

| 엔드포인트 | 조회 기준 파라미터 | 비고 |
|---|---|---|
| `OpenBidInfoList.dev` | `tndrbidRegDtStart/End` | 입찰공고 |
| `OpenTenderopenList.dev` | `openDtmStart/End` | 개찰결과 |
| `OpenContractInfoList.dev` | `contractDtStart/End` | 계약현황 |

### 3.1 핵심 필드

**입찰공고**
- `bidNum` — 공고번호 (조인 키)
- `bidnmKor` — 공고명
- `sunjungNm` — 낙찰방식 (종심제/적격심사 판별)
- `presmtPrc` — 추정가격 (VAT 제외)
- `req1~10LicGbNm` — 종합/전문 구분
- `req1~10Reqlic1~10Nm` — 요구 면허명
- `req1~10LicctNm` — 면허 조건
- `vndrrstrctNm1~4` — 업체제한 (지역제한 등)

**개찰결과**
- `expectPrc` — 예정가격 ← 예정가격정보 API 대체 사용
- `decTndrAmt` — 낙찰금액
- `invtgtRate` — 낙찰률
- `tndrVndrNm` — 낙찰업체명
- `vndrSccfBidStatusNm` — 낙찰여부 ("낙찰" 포함 여부로 낙찰자 식별)

**계약현황**
- `ctrctAmt` — 계약금액
- `ctrctVndrNm` — 계약업체명
- `ctrctCntrctgDt` — 계약체결일
- `initBgnwrkDt` / `finlCompwrkDt` — 착공일 / 준공일

## 4. 필터 규칙

- 공사만 수집 (`work_type = '공사'`)
- 추정가격 ≥ 100억 (VAT 제외)
- 추정가격 누락 건 → `notices_unpriced` 격리
- 종심제 여부: `sunjungNm` 값에 "종합심사" 포함 여부
- 공종제한: `req*LicGbNm` = "종합" / "전문" 분류

## 5. 아키텍처

```
GitHub Actions (monthly cron)
  → run_monthly.py
      → LHAdapter.fetch_notices  (입찰공고)
      → LHAdapter.fetch_awards   (개찰결과)
      → LHAdapter.fetch_contracts (계약현황)
      → bidNum 기준 join_all()
      → SQLite (procurement.db)
      → CSV (output/lh_joined_YYYYMM.csv)
```

## 6. 실행

```bash
export LH_API_KEY=your_service_key
python -m src.run_monthly --month 2026-05
```

## 7. 승인

| 역할 | 이름 | 승인일 |
|---|---|---|
| Owner | | |
| Reviewer | | |
