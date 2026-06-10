# Public Procurement Tracker

14개 발주처의 공사(종합·전문) 입찰공고·낙찰·계약 정보를 월 1회 자동 수집하는 시스템입니다.

## 수집 기준
- work_type: **공사만** (종합·전문)
- 추정가격: **≥ 100억원 (VAT 제외)**
- 주기: 매월 1일 07:00 KST (전월 1일~말일)
- 분기 백필: 매분기 첫째달 1일 09:00 KST (직전 3개월)

## Phase
- Phase 0: 인프라 (현재)
- Phase 1: 나라장터 + LH + 한전 + 국가철도공단
- Phase 2: 한수원·도공·수공·동서·중부발전
- Phase 3: SH·부산항만·인천공항·가스·환경
- Phase 4: 발주계획(plans)

## 실행
```bash
pip install -r requirements.txt
export G2B_API_KEY=...
python -m src.run_monthly --month 2026-05
```

## 디렉토리
```
src/
  adapters/   사이트별 어댑터
  db.py       SQLite 스키마/연결
  normalizer.py
  long_term_detector.py
  reporter.py
  run_monthly.py
config/agencies.yaml
tests/
.github/workflows/
```
