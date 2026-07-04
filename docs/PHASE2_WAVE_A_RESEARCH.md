# Phase 2 Wave A 조사 결과 (2026-07-04)

실행계획 §3.1 조사 단계 산출물. GitHub Actions 러너(probe 모드)에서 3차에
걸쳐 조사했다. 전체 캡처본은 Actions 아티팩트 `probe-output-17/18/19`.

## 공통 발견

- **data.go.kr은 해외 IP(Actions 러너 포함) 차단** → 기관별 OpenAPI 존재 여부
  1차 확인은 ⚠️**[사용자]** 국내 IP에서 수행 필요 (실행계획 §6 Phase 2 항목).
- 전자조달 사이트 자체는 대부분 러너에서 접속 가능 → **XHR 스크래핑 경로 유효**.
- EX·KWATER·KHNP 모두 SPA(WebComponents/WebSquare5) → 정적 크롤링 불가,
  Playwright XHR 캡처(scripts/probe_wave_a_xhr.py)로 조사.

## 기관별 상태

### KWATER (한국수자원공사) — ✅ 목록 XHR 확정, 어댑터 구현됨 (`kwater.py`)

- `POST https://ebid.kwater.or.kr/bidpblanc/bidpblancsttus/retrievePaginatedBidPblancList.do`
- 요청: `{"dmaSearchData": {"cntrctDivNm", "recordCountPerPage",
  "tndrPblancStartDe", "tndrPblancEndDe"}, "ktagTokenField": "BID_savedToken",
  "BID_savedToken": null}` — **비로그인 정상 응답** 확인 (6월 한 달 1,203건)
- 응답 필드(확인): tndrPblancDe·tndrPbanno·tndrPblancNm·ctrmthdCdNm·tndrStat·
  cntrctDivNm·tndrPartcptEntrpsCo·tndrPrqudoCo (+캡처 잘림, 금액 필드 미확정)
- 잔여: 금액 필드명 확정(실서비스 [schema] 로그), 상세 XHR(추정가격 미노출 시)

### EX (한국도로공사) — ✅ 계약 OpenAPI 확정, 어댑터 구현됨 (`ex.py`)

**(2026-07-04 갱신)** 사용자가 EX 자체 공공데이터포털(data.ex.co.kr)에서
**"전자조달 계약공개현황"** OpenAPI 활용신청 완료 (`EX_API_KEY`, 10자리):
- `GET https://data.ex.co.kr/openapi/elctPrcmInfo/elctPrcmCntrtOppubPrss`
- 파라미터: key·type(json/xml)·sCntrtCntgDates/eCntrtCntgDates(체결일 범위)·
  pbanClssCd(CT=공사 등 13종)·pageNo/numOfRows
- 출력: 공고번호·계약명·계약방법·계약업체명/사업자번호·계약금액·부서·체결일
- **계약(체결일 기준) 데이터**라 fetch_contracts 경로로 구현 — 핵심 산출물
  (100억↑ 공사계약 집계)에 직결. 입찰공고는 아래 포털 XHR로 후속 구현.

#### 입찰공고 (포털 XHR — 후속 과제)

- 비로그인 JSON 확인:
  - `POST https://ebid.ex.co.kr/ui/bp/portal/findPagingPortalBidNotiList.do`
    (`{"noti_cls":"CT"}` = 공사) → noti_no·noti_nm·noti_date·bid_rev·menu_cd
  - `POST .../findPagingPortalBidRstlList.do` (개찰결과) → open_dt·prog_sts 포함
  - 구분코드(`findMdiSearchItems.do`): CM009 01=공사 02=용역 03=물품 …
- 잔여: 포털 위젯이 아닌 **전용 공고검색 화면**(menu NPRO23001,
  `ui/sp/expro/bidnoti/em-sp-bid-noti-cs.html`)의 검색 XHR(기간·페이징 파라미터)
  캡처, 금액 필드 위치 확인 → 어댑터 구현

### KHNP (한국수력원자력) — 🔶 메뉴/공지 API까지 확인

- `ebiz.khnp.co.kr` → `/login.do` 리다이렉트되나 NoSession 계열 API 다수 존재
  (`findListMenu.do`, `totalFindListByNoSession.do` 등)
- 계약정보공개 메뉴 코드 확인: `CNTIO` (attr_03=EBIZ13100, attr_02=TPRO13001)
- 잔여: 입찰공고 목록 화면 진입 경로 확보(메뉴 URL 직접 탐색) 후 XHR 캡처.
  PRD §8 예상대로 보안이 강해 Wave A 중 난이도 최상.

### KOGAS (한국가스공사) — 🔶 구형 JSP 프레임, 추가 조사 필요

- `bid.kogas.or.kr`(443) 해외 차단, **`bid.kogas.or.kr:9443`은 접속 가능**
  (구형 프레임 사이트, `/supplier/index.jsp`)
- `www.kogas.or.kr/site/koGas/referenceBidList.do`(참고용 입찰공고)는 직접
  접근 시 244바이트 셸만 반환 — 세션/리퍼러 필요 추정
- 잔여: 9443 프레임 내부(buyer/supplier) 공고 목록 JSP 경로 추적

## 다음 단계 (우선순위)

1. kwater 실서비스 검증 → 금액 필드 확정 → 매핑 마감
2. EX 전용 공고검색 화면 XHR 캡처(probe 스크립트에 화면 URL 직접 로드 추가) → `ex.py`
3. KOGAS 9443 프레임 크롤 → 목록 JSP 확인 → `kogas.py`
4. KHNP 메뉴 URL 기반 진입 → XHR 캡처 → `khnp.py`
5. ⚠️**[사용자]**: data.go.kr에서 4개 기관 입찰정보 OpenAPI 존재 여부 확인
   (있으면 XHR 대신 OpenAPI 우선 원칙 적용), 각 사이트 이용약관·robots.txt 확인
