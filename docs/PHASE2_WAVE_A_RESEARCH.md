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
- **실서비스 검증 완료** (run #24, 2026-06): 계약 620건 수집 →
  100억↑ 공사계약 5건 적재 (제천~영월 고속국도 1~5공구, 합계 약 1.2조원).
  실스키마 = 문서 + dateGubun. 관찰: 계약명 "(제N차)" 표기가 장기계속
  감지 패턴에 안 걸림 — 차수 표기 오탐 위험 검토 후 패턴 확장 여부 결정.

#### 입찰공고 (포털 XHR — 후속 과제)

- 비로그인 JSON 확인:
  - `POST https://ebid.ex.co.kr/ui/bp/portal/findPagingPortalBidNotiList.do`
    (`{"noti_cls":"CT"}` = 공사) → noti_no·noti_nm·noti_date·bid_rev·menu_cd
  - `POST .../findPagingPortalBidRstlList.do` (개찰결과) → open_dt·prog_sts 포함
  - 구분코드(`findMdiSearchItems.do`): CM009 01=공사 02=용역 03=물품 …
- 잔여: 포털 위젯이 아닌 **전용 공고검색 화면**(menu NPRO23001,
  `ui/sp/expro/bidnoti/em-sp-bid-noti-cs.html`)의 검색 XHR(기간·페이징 파라미터)
  캡처, 금액 필드 위치 확인 → 어댑터 구현

### KHNP (한국수력원자력) — 🔶 메뉴/공지 API까지 확인 (Wave A 유일 잔여)

- `ebiz.khnp.co.kr` → `/login.do` 리다이렉트되나 NoSession 계열 API 다수 존재
  (`findListMenu.do`, `totalFindListByNoSession.do` 등)
- 계약정보공개 메뉴 코드 확인: `CNTIO` (attr_03=EBIZ13100, attr_02=TPRO13001)
- **(2026-07-04 갱신)** requests 직접 호출 시 `findListMenu.do` 등이 403
  (브라우저 컨텍스트에서만 200 — CSRF/헤더 보호). Playwright 세션 기반
  재조사 필요. PRD §8 예상대로 난이도 최상 — Wave A 유일 잔여 과제.

### KOGAS (한국가스공사) — ✅ 스크래핑 구조 확정, 어댑터 구현됨 (`kogas.py`)

- `bid.kogas.or.kr`(443) 해외 차단, **`bid.kogas.or.kr:9443`은 접속 가능**
  (구형 프레임 사이트, `/supplier/index.jsp`)
- `www.kogas.or.kr/site/koGas/referenceBidList.do`(참고용 입찰공고)는 직접
  접근 시 244바이트 셸만 반환 — 세션/리퍼러 필요 추정
- **(2026-07-04 갱신, 5~8차 조사)** 비로그인 경로 확정:
  - 목록: `GET /supplier/contents/bid/bid_list_notice_frm.jsp?page&worktype=C`
    (euc-kr, 15행/페이지, Total Records 표기, 공고번호 앞 8자리=공고일)
  - 상세: `POST /supplier/contents/bid/bid_detail_view_notice.jsp` —
    추정가격(부가세 별도)·공고일시·마감/개찰일시
  - **실서비스 검증 완료** (run #29, 2026-06): 공사 45건 중 6월 17건 수집,
    17건 전부 추정가격 파싱 성공(미공개 0), 100억↑ 0건(표본 대조 필요)

## 다음 단계 (우선순위)

1. ~~kwater 실서비스 검증 → 금액 필드 확정~~ ✅ (rqestAmt, run #20/#21)
2. ~~EX 계약 OpenAPI 어댑터~~ ✅ (run #23/#24) — 입찰공고 포털 XHR은 후속
3. ~~KOGAS 목록/상세 스크래핑 어댑터~~ ✅ (run #25~#29)
4. KHNP: Playwright 세션 기반 재조사 → XHR 캡처 → `khnp.py` (Wave A 잔여)
5. ⚠️**[사용자]**: data.go.kr에서 KHNP 등 입찰정보 OpenAPI 존재 여부 확인
   (있으면 XHR 대신 OpenAPI 우선 원칙), 각 스크래핑 사이트(kwater·kogas)
   이용약관·robots.txt 자동수집 조항 확인, 표본 대조
   (KWATER rqestAmt 의미, KOGAS 6월 100억↑ 부재, EX 제천~영월 5건)
