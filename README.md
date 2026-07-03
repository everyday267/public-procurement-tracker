# Public Procurement Tracker

14개 발주처의 공사(종합·전문) 입찰공고·낙찰·계약 정보를 월 1회 자동 수집하는 시스템입니다.

## 수집 기준
- work_type: **공사만** (종합·전문)
- 추정가격: **≥ 100억원 (VAT 제외)**
- 주기: 매월 1일 07:00 KST (전월 1일~말일)
- 분기 백필: 매분기 첫째달 1일 09:00 KST (직전 3개월)

## Phase
- Phase 0: 인프라 — 완료
- Phase 1: 나라장터 + LH + 한전(KEPCO) + 국가철도공단 — **진행 중** (아래 "현재 진행 현황" 참고)
- Phase 2: 한수원·도공·수공·동서·중부발전
- Phase 3: SH·부산항만·인천공항·가스·환경
- Phase 4: 발주계획(plans)

## 현재 진행 현황 (Phase 1)

| 어댑터 | 코드 | run_monthly 연동 | 실서비스 검증 |
|---|:---:|:---:|:---:|
| 나라장터 (`g2b_opnstd.py`) | ✅ | ✅ | 🔲 |
| LH (`lh.py`) | ✅ | ✅ | 🔲 |
| 국가철도공단 (`kr_rail.py`, 나라장터 경유) | ✅ | ✅ | 🔲 |
| 한전 KEPCO (`kepco.py`) | 🔲 미착수 | — | — |

> KEPCO는 srm.kepco.net XHR 기반 자체 조달망이라 로그인/세션 처리 방식 확인이 먼저 필요합니다.
> 어댑터 코드가 없으므로 `run_monthly.py`의 `SOURCES`에도 아직 포함되어 있지 않습니다.

## 실행

```bash
pip install -r requirements.txt
export G2B_API_KEY=...
export LH_API_KEY=...
python -m src.run_monthly --month 2026-05

# 특정 소스만 실행
python -m src.run_monthly --month 2026-05 --sources lh,kr_rail
```

`run_monthly`는 지정된 모든 소스를 순회하며 하나가 실패해도 나머지는 계속 진행합니다
(소스별 성공/실패는 `source_runs` 테이블에 기록). 소스별 CSV(`output/{source}_joined_YYYYMM.csv`)와
전체 통합 CSV(`output/all_joined_YYYYMM.csv`)가 함께 생성됩니다.

## GitHub Actions 시크릿

| 시크릿 | 용도 |
|---|---|
| `G2B_API_KEY` | 나라장터 + 국가철도공단(나라장터 경유) |
| `LH_API_KEY` | LH e-Bid 자체 OpenAPI |

두 시크릿 모두 Settings → Secrets and variables → Actions에 등록되어 있어야
`monthly-collect` / `quarterly-backfill` 워크플로우가 정상 동작합니다.

## 디렉토리
```
src/
  adapters/        사이트별 어댑터 (base, lh, g2b_opnstd, kr_rail)
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
