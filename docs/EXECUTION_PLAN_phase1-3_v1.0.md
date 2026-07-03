# 실행계획: Phase 1~3 완성 로드맵 (Execution Plan)

**문서버전:** v1.0
**작성일:** 2026-07-03
**기준문서:** PRD v1.4 (`docs/PRD_public_procurement_tracker_v1.4.md`)
**상태:** Draft

본 문서는 Phase 1(한전 어댑터 중심) 완성 계획, Phase 2 실행 계획, Phase 3 개요를 정의하고,
**사용자(운영자)가 직접 수행해야 하는 항목**을 별도 섹션(§6)에 체크리스트로 표시한다.

> 표기 규약: 본문 중 ⚠️**[사용자]** 표시는 개발 환경에서 자동화할 수 없어 사용자가 직접 수행해야 하는 작업이다. 전체 목록은 §6 참조.

---

## 1. 현황 스냅샷 (2026-07-03)

### 1.1 어댑터 상태

| 어댑터 | 파일 | 코드 | 단위테스트 | 실서비스 검증 | 비고 |
|---|---|:---:|:---:|:---:|---|
| 나라장터(G2B) | `src/adapters/g2b_opnstd.py` | ✅ | ✅ | 🔲 | PubDataOpnStdService |
| LH | `src/adapters/lh.py` | ✅ | 🔲 | 🔲 | e-Bid OpenAPI 3종 |
| 국가철도공단 | `src/adapters/kr_rail.py` | ✅ | 🔲 | 🔲 | G2B 상속 + instNm 필터 |
| **한국전력공사** | `src/adapters/kepco.py` | 🔲 **미개발** | 🔲 | 🔲 | 본 계획의 핵심 작업 |

### 1.2 코드베이스 갭 (Phase 1 완성을 위해 해소 필요)

| # | 갭 | 위치 | 조치 |
|---|---|---|---|
| G-1 | `run_monthly.py`가 LH 단독 실행 구조 | `src/run_monthly.py` | 어댑터 레지스트리 기반 멀티 소스 오케스트레이션으로 개편 (§2.3) |
| G-2 | `agencies.yaml`이 구버전 14개 기관 (KOSPO·KOEN·KOWEPO·GH 누락, KOGAS가 Phase 3, BPA·IIAC은 v1.4에서 제외됨) | `config/agencies.yaml` | PRD v1.4 기준 16개 기관으로 동기화 (§2.4) |
| G-3 | monthly 워크플로에 `G2B_API_KEY`만 주입 | `.github/workflows/monthly.yml` | `LH_API_KEY`, `KEPCO_API_KEY` 추가 (§2.5) — 시크릿 등록은 ⚠️**[사용자]** |
| G-4 | LH·KR_RAIL 단위테스트 부재 | `tests/` | fixture 기반 테스트 추가 (§2.6) |

### 1.3 개발 환경 제약 (중요)

원격 개발 환경(Claude Code 컨테이너)에서는 한국 공공기관 도메인 접속이 프록시에서 **차단**된다.
확인된 차단 대상: `srm.kepco.net`, `www.data.go.kr`, `apis.data.go.kr`, `openapi.kepco.co.kr`

**결론:**
- 어댑터 개발·단위테스트는 **fixture(저장된 응답 샘플) 기반**으로 진행한다.
- 실서비스 검증(실제 API 호출·스크래핑 확인)은 ⚠️**[사용자]** 로컬 PC 또는 GitHub Actions(`workflow_dispatch`)에서 수행한다.
- Phase 2~3 스크래핑 대상 사이트의 XHR 분석도 ⚠️**[사용자]** 브라우저 DevTools 캡처 제공이 필요하다.

---

## 2. Phase 1 완성 계획 — 한전(KEPCO) 중심

### 2.1 한전 수집 방식 결정

| 구분 | 내용 |
|---|---|
| **1차 경로 (확정)** | 공공데이터포털 **한국전력공사_전자입찰계약정보 OpenAPI** |
| 근거 데이터셋 | data.go.kr 데이터셋 [15148223](https://www.data.go.kr/data/15148223/openapi.do) (신버전 권장), 구버전 [3068324](https://www.data.go.kr/data/3068324/openapi.do) |
| 엔드포인트 | `http://openapi.kepco.co.kr/service/bidInfoService/getBidSearchList` |
| 인증 | 공공데이터포털 활용신청 → 인증키 발급 (개발/운영 **자동승인**) → 환경변수 `KEPCO_API_KEY` |
| **폴백 경로** | `srm.kepco.net` 비로그인 영역 XHR 스크래핑 |
| 폴백 발동 조건 | OpenAPI 커버리지 부족(공고 누락·필드 부실) 또는 서비스 중단이 실서비스 검증에서 확인될 때 |

**결정 근거:**
- PRD v1.4 §4는 srm.kepco.net XHR을 명시했으나, 동시에 §8 리스크표에서 "KEPCO srm 로그인 세션 필요"를 **High**로 평가하고 "비로그인 영역 우선, 필요 시 세션 방식 전환"을 대응책으로 명시했다.
- 실제로 srm.kepco.net 입찰공고 목록은 로그인 후 접근이 기본이다.
- 공식 OpenAPI는 인증키만으로 접근 가능하고 스키마가 안정적이어서 스크래핑 차단·개편 리스크가 없다.
- 따라서 **OpenAPI를 1차 경로로, srm XHR을 폴백으로** 구현한다. (PRD 차기 개정 시 §4 수집방법 갱신 필요 — §6 참조)

### 2.2 `kepco.py` 어댑터 설계

`src/adapters/kepco.py` 신규 작성. 기존 패턴을 최대한 재사용한다.

| 설계 항목 | 내용 | 재사용 대상 |
|---|---|---|
| 클래스 | `KEPCOAdapter(BaseProcurementAdapter)`, `source="kepco"`, `agency_codes=["KEPCO"]` | `src/adapters/base.py` |
| 인증 | 생성자에서 `KEPCO_API_KEY` 환경변수 로드, 없으면 `ValueError` | `lh.py` / `g2b_opnstd.py` 패턴 |
| HTTP | `requests` + 페이지네이션(`numOfRows`/`pageNo` 계열) + rate limit 1 req/s | `lh.py`의 `_paginate`, `REQUEST_INTERVAL` 패턴 |
| 응답 파싱 | XML(`ElementTree`) 우선, JSON 지원 시 JSON — 실서비스 응답 확인 후 확정 | `lh.py`의 `_items` |
| `fetch_notices` | `getBidSearchList` 공고 조회 (기간 파라미터는 실서비스 명세 확인 후 확정) | — |
| `fetch_awards` / `fetch_contracts` | 동일 서비스의 낙찰·계약 오퍼레이션 존재 여부 확인 후 매핑. 미제공 시 srm 폴백 또는 G2B 계약정보로 보완 | — |
| `normalize` | 공통 스키마(`src/normalizer.py`)로 변환, `notice_id = "kepco:{공고번호}:{차수}"` | `normalizer.normalize_notice` |
| 장기계속 판별 | 공고명·계약방식 텍스트 기반 | `src/long_term_detector.detect_long_term_from_raw` |
| 추정가격 | VAT 포함 여부 확인 후 제외가 환산 (포함 시 `/1.1`) | `g2b_opnstd._estimated_price_vat_excl` 패턴 |
| `health_check` | 최소 조회 1건 호출로 응답코드 확인 | `lh.health_check` 패턴 |
| `source_hash` | raw payload 해시 (스키마 변경 모니터링용) | `lh._hash` |

> **명세 확정 절차:** 오퍼레이션 목록·요청 파라미터·응답 필드는 공공데이터포털 활용신청 후 제공되는 기술문서/샘플코드로 확정한다. ⚠️**[사용자]** 인증키 발급 후 참고문서(WORD/스웨거)와 샘플 응답 1~2건을 저장소 `tests/fixtures/`용으로 제공하면 개발이 즉시 진행 가능하다.

**srm 폴백 설계 (필요 시):**
- 비로그인 공고 목록 XHR 엔드포인트를 ⚠️**[사용자]** DevTools 캡처로 확보 (요청 URL·헤더·페이로드·응답 JSON)
- 동일 `KEPCOAdapter` 내부에서 `mode="srm"` 분기 또는 `KEPCOSrmAdapter` 서브클래스로 구현
- User-Agent 지정, 요청 간격 ≥ 2초, 실패 시 지수 백오프

### 2.3 `run_monthly.py` 멀티 어댑터 오케스트레이션

현재 LH 단독 구조를 다음과 같이 개편:

```
ADAPTERS = {
    "g2b_opnstd": G2BOpnStdAdapter,   # G2B_API_KEY
    "lh":         LHAdapter,          # LH_API_KEY
    "kepco":      KEPCOAdapter,       # KEPCO_API_KEY
    "kr_rail":    KRRailAdapter,      # G2B_API_KEY 공유
}
```

- `--sources g2b_opnstd,lh,...` 인자로 부분 실행 지원 (기본: 전체)
- **소스별 독립 try/except**: 한 어댑터 실패가 다른 소스 수집을 막지 않도록 격리하고, 실패 시 `source_runs.status='error'` + `error_message` 기록
- API 키 미설정 소스는 경고 로그 후 skip (부분 운영 가능)
- 공고→`passes_filter`→`upsert_notices`, 미공개가는 `notices_unpriced` 격리(PRD §3), 소스별 CSV `output/YYYYMM/{source}_joined_YYYYMM.csv`
- LH 전용 `join_all`은 소스별 조인 키가 다르므로(LH=`bidNum`, G2B=`bidNtceNo`) 어댑터별 조인 로직으로 일반화

### 2.4 `config/agencies.yaml` v1.4 동기화

PRD v1.4 §2 기준 16개 기관으로 갱신:
- 추가: `KOSPO`(남부발전), `KOEN`(남동발전), `KOWEPO`(서부발전) → Phase 2 / `GH`(경기주택도시공사) → Phase 3
- 이동: `KOGAS` Phase 3 → **Phase 2**
- 제거: `BPA`(부산항만공사), `IIAC`(인천국제공항공사) — v1.4 대상 목록에서 제외됨
- README §Phase 문단도 동일 기준으로 갱신

### 2.5 GitHub Actions 워크플로 갱신

- `monthly.yml`·`quarterly_backfill.yml` env에 `LH_API_KEY`, `KEPCO_API_KEY` 추가
- 시크릿 등록은 ⚠️**[사용자]** (§6)
- cron `0 22 28-31 * *`은 "매월 1일 KST"보다 넓게 잡혀 있으므로 스텝에서 `[ "$(date -u +%d)" = "말일" ]` 가드 추가 검토 (28~31일 매일 실행 방지)

### 2.6 테스트 전략 (네트워크 차단 환경 대응)

| 테스트 | 방식 |
|---|---|
| `tests/test_kepco_adapter.py` | fixture 응답(mock) 기반: normalize 필드 매핑, VAT 환산, 100억 필터, 장기계속 판별, 페이지네이션 |
| `tests/test_lh_adapter.py` (보강) | LH XML 샘플 fixture로 normalize_notice/award/contract 검증 |
| `tests/test_kr_rail_adapter.py` (보강) | `_is_kr_rail` 필터, source 덮어쓰기 검증 |
| `tests/test_run_monthly.py` | 어댑터 mock 주입으로 오케스트레이션·실패 격리·source_runs 기록 검증 |

### 2.7 Phase 1 실서비스 검증 절차 — ⚠️**[사용자]**

1. 로컬에서 `.env` 또는 셸에 3개 키 설정 후:
   ```bash
   python -m src.run_monthly --month 2026-06
   ```
2. 소스별 확인 항목:
   - `source_runs` 4행 모두 `success`
   - KEPCO: 공고 건수가 srm.kepco.net 화면과 표본 대조 (월 5건 내외 표본)
   - KR_RAIL: G2B 필터 결과가 ebid.kr.or.kr 게재 건과 표본 대조 (PRD §8 커버리지 리스크)
   - LH: 실서비스 필드명 불일치 여부 확인 (PRD §8 High 리스크)
3. GitHub Actions `workflow_dispatch`로 동일 월 재실행하여 CI 환경 동작 확인
4. 검증 결과를 PRD §9 표에 반영

### 2.8 Phase 1 완료 기준 (Definition of Done)

- [ ] 4개 어댑터 코드 + 단위테스트 전부 통과
- [ ] `run_monthly` 4개 소스 통합 실행 성공 (1개월분)
- [ ] 실서비스 검증 표본 대조 완료 (⚠️ 사용자)
- [ ] agencies.yaml·README·워크플로 v1.4 정합
- [ ] KPI: 4개 기관 100억↑ 커버리지 ≥ 95% (PRD §7)

---

## 3. Phase 2 실행 계획 — 대형 SOC·발전 9개 기관

### 3.1 접근 원칙

1. **OpenAPI 우선 원칙**: 각 기관에 대해 공공데이터포털(data.go.kr) 등록 API를 먼저 조사하고, 없을 때만 스크래핑한다. (한전 사례처럼 PRD의 "스크래핑 예정" 표기와 달리 공식 API가 존재할 수 있음)
2. **기관별 4단계 파이프라인**: 조사 → fixture 확보 → 어댑터 구현 → 실서비스 검증
   - 조사·fixture 확보 단계에서 ⚠️**[사용자]** 협조 필요 (네트워크 차단, §1.3)
3. **웨이브 분할**로 리스크 분산:

| 웨이브 | 기관 | 근거 |
|---|---|---|
| **Wave A** (4개) | EX(도공), KWATER(수공), KHNP(한수원), KOGAS(가스) | 연간 발주규모 상위, 시스템이 서로 상이 → 개별 설계 |
| **Wave B** (5개) | EWP, KOMIPO, KOSPO, KOEN, KOWEPO (발전 5사) | 한전 계열 발전 자회사로 전자조달시스템 구조 유사 → **공통 스크래퍼 베이스 클래스** 후보 |

### 3.2 기관별 계획표

| # | 기관 | 조달시스템 | 예상 수집방법 | 어댑터 | 예상 난이도 | 주요 리스크 |
|---|---|---|---|---|:---:|---|
| 1 | 한국도로공사(EX) | ebid.ex.co.kr (EX 전자조달) | data.go.kr API 조사 → 없으면 XHR | `ex.py` | 중 | 스크래핑 차단 |
| 2 | 한국수자원공사(KWATER) | ebid.kwater.or.kr | K-water OpenAPI 조사 → XHR | `kwater.py` | 중 | 스키마 변경 |
| 3 | 한국수력원자력(KHNP) | ebiz.khnp.co.kr | XHR/HTML 스크래핑 | `khnp.py` | 상 | 보안 강한 사이트, 세션 요구 가능 |
| 4 | 한국가스공사(KOGAS) | bid.kogas.or.kr | 입찰정보 스크래핑 | `kogas.py` | 중 | — |
| 5~9 | 발전 5사 (EWP·KOMIPO·KOSPO·KOEN·KOWEPO) | 각사 전자조달 | 공통 베이스 + 사별 파라미터 | `ewp.py` 외 4종 | 사별 하 (베이스 확립 후) | User-Agent 차단, 요청 간격 |

> 시스템 URL은 조사 단계에서 확정한다(개편 빈번). 위 표의 URL은 참고용 후보.

### 3.3 공통 인프라 작업 (Wave A 착수 전)

| 작업 | 내용 |
|---|---|
| `ScraperBaseAdapter` | `BaseProcurementAdapter` 상속 스크래핑 공통층: 세션 관리, User-Agent 로테이션, 요청 간격·재시도(지수 백오프), HTML/JSON 파서 훅 |
| fixture 규격화 | `tests/fixtures/{source}/notices.json` 등 표준 배치, 어댑터 테스트 공통 헬퍼 |
| 스키마 모니터링 연동 | `source_hash` 변화 감지 → `schema_monitor.yml` GitHub Issue 자동 생성에 신규 소스 포함 |
| 커버리지 대시보드 | `source_runs` 기반 월간 수집 리포트(기관×건수×금액)를 `reporter.py`로 출력 |

### 3.4 Phase 2 일정(안)

Phase 1 안정화(월간 배치 2회 무장애) 이후 착수. 1개 기관당 조사~검증 1~2주 기준:

| 마일스톤 | 내용 | 기간(안) |
|---|---|---|
| M2-0 | 공통 인프라(ScraperBase, fixture 규격) | 1주 |
| M2-1 | Wave A: EX → KWATER → KOGAS → KHNP (난이도 순) | 4~6주 |
| M2-2 | Wave B: 발전 5사 공통 베이스 + 사별 어댑터 | 3~4주 |
| M2-3 | 통합 검증·KPI 측정 (16개 중 13개 기관 가동) | 1주 |

### 3.5 Phase 2 완료 기준

- [ ] 9개 어댑터 코드·테스트·실서비스 검증 완료
- [ ] 월간 배치에 13개 소스 통합, 실패 격리 동작 확인
- [ ] KPI: 커버리지 ≥ 90%, 시장규모 추정 커버 ~85% 경로 확인 (PRD §7)

---

## 4. Phase 3 계획 (개요) — 도시·환경

Phase 2 안정화 후 착수. 상세 실행계획은 Phase 2 완료 시점에 v1.1로 증보한다.

| # | 기관 | 방법 | 작업 |
|---|---|---|---|
| 1 | 서울주택도시공사(SH) | i-sh.co.kr 입찰정보 스크래핑 (OpenAPI 선조사) | `sh.py` |
| 2 | 경기주택도시공사(GH) | gh.or.kr 입찰정보 스크래핑 (경기데이터드림 API 선조사) | `gh.py` |
| 3 | 한국환경공단(KECO) | **별도 어댑터 없음** — G2B 수집분에서 `dmndInsttNm=한국환경공단` 커버리지 검증만 수행 | 검증 스크립트 |

- Phase 4(발주계획 plans 테이블)는 본 문서 범위 외 (PRD 참조)

---

## 5. 다음 구현 작업 목록 (개발 측, 우선순위순)

본 문서 승인 후 코드 작업은 아래 순서로 진행한다 (이번 커밋에는 미포함):

1. `config/agencies.yaml` + README v1.4 동기화 (G-2)
2. `src/adapters/kepco.py` OpenAPI 어댑터 + `tests/test_kepco_adapter.py` — ⚠️ 사용자 fixture 제공 후 필드 매핑 확정
3. `src/run_monthly.py` 멀티 어댑터 오케스트레이션 + 테스트 (G-1)
4. 워크플로 env 갱신 (G-3)
5. LH·KR_RAIL 테스트 보강 (G-4)
6. (실서비스 검증에서 필요 판정 시) srm 폴백 스크래퍼

---

## 6. ⚠️ 사용자 직접 수행 항목 (체크리스트)

개발 환경 네트워크 차단(§1.3) 및 계정·권한 문제로 자동화 불가한 항목. **Phase 1 항목은 개발 착수 전 완료가 선행조건.**

### Phase 1 (선행조건)

- [ ] **KEPCO API 키 발급**: [공공데이터포털](https://www.data.go.kr) 로그인 → "한국전력공사_전자입찰계약정보"([15148223](https://www.data.go.kr/data/15148223/openapi.do)) 활용신청(자동승인) → 인증키 확보
- [ ] **KEPCO API 기술문서·샘플 제공**: 활용신청 페이지의 참고문서(오퍼레이션·파라미터·응답필드 명세) 및 실제 응답 샘플 1~2건(공고·낙찰·계약 각각)을 개발 세션에 전달 → fixture로 사용
- [ ] **GitHub 저장소 시크릿 등록** (Settings → Secrets and variables → Actions):
  - [ ] `G2B_API_KEY` (기존 발급분 확인)
  - [ ] `LH_API_KEY`
  - [ ] `KEPCO_API_KEY`
- [ ] **실서비스 검증 실행** (§2.7): 로컬 `python -m src.run_monthly --month 2026-06` 또는 Actions `workflow_dispatch` → 결과 로그·CSV 공유
- [ ] **표본 대조**: KEPCO(srm 화면), KR_RAIL(ebid.kr.or.kr), LH(e-Bid 화면) 각 월 5건 내외 육안 대조
- [ ] **(폴백 발동 시)** srm.kepco.net 입찰공고 목록 화면에서 브라우저 DevTools(Network 탭)로 XHR 요청 URL·헤더·페이로드·응답 JSON 캡처 제공
- [ ] **PRD 갱신·승인**: 한전 수집방법을 "OpenAPI 우선 + srm 폴백"으로 PRD §4 개정, §10 승인란(Owner/Reviewer) 기입

### Phase 2 (착수 시)

- [ ] 9개 기관 조달시스템 각각에 대해: 이용약관·robots.txt의 자동수집 관련 조항 확인 (법무 검토 필요 시 사내 절차)
- [ ] data.go.kr에서 기관별 입찰정보 OpenAPI 존재 여부 1차 확인 후 존재 시 활용신청·키 발급
- [ ] 스크래핑 대상 사이트별 XHR/HTML 샘플 캡처 제공 (기관당 공고 목록 1페이지 + 상세 1건)
- [ ] 신규 API 키 GitHub 시크릿 등록

### Phase 3 (착수 시)

- [ ] SH·GH 사이트 XHR 캡처 및 약관 확인 (Phase 2와 동일 절차)
- [ ] KECO 커버리지 검증용: 한국환경공단 발주 실적 대조 자료(내부 집계 또는 G2B 화면) 확보

---

## 7. 마일스톤·KPI 체크포인트

| 체크포인트 | 시점 | 측정 항목 (PRD §7) |
|---|---|---|
| CP-1 | Phase 1 실서비스 검증 완료 | 4개 기관 커버리지 ≥ 95%, 어댑터 장애 감지 100% |
| CP-2 | Phase 1 월간 배치 2회 무장애 | Phase 2 착수 게이트 |
| CP-3 | Wave A 완료 | 8개 기관 가동, 시장규모 커버 추이 |
| CP-4 | Phase 2 완료 | 13개 기관, 커버리지 ≥ 90% |
| CP-5 | Phase 3 완료 | 16개 기관, 시장규모 추정 커버 ~85% |

---

## 8. 리스크 (본 계획 고유분 — PRD §8 보완)

| 리스크 | 영향도 | 대응 |
|---|:---:|---|
| KEPCO OpenAPI가 srm 게재 공고 전체를 커버하지 못함 | High | 실서비스 검증 시 표본 대조(§2.7) → 미달 시 srm 폴백 발동 |
| KEPCO OpenAPI에 낙찰·계약 오퍼레이션 부재 | Medium | 기술문서 확인 후 G2B 계약정보(`dmndInsttNm=한국전력공사`)로 보완 검토 |
| 개발환경 네트워크 차단으로 개발-검증 사이클 지연 | Medium | fixture 우선 개발 + 사용자 검증 절차 표준화(§6) |
| 사용자 선행조건(키 발급·fixture) 지연 | Medium | §6 Phase 1 체크리스트를 착수 게이트로 관리 |
