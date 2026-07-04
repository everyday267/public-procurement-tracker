# KWATER fixture

`notices.json`은 실제 응답 기반이다:
- 응답 골격·필드명: 2026-07-04 3차 조사(run #19, Playwright XHR 캡처) +
  실서비스 수집(run #20)의 `[schema]` 로그로 확정한 11개 필드
  (cntrctDeptNm·cntrctDivNm·ctrmthdCdNm·rqestAmt·tndrPbanno·tndrPblancDe·
  tndrPblancEnddt·tndrPblancNm·tndrStat 등)
- 데이터 값은 합성

`rqestAmt`(요청금액)의 추정가격/기초금액 여부와 VAT 포함 여부는
ebid.kwater.or.kr 화면 표본 대조로 확인 필요 (⚠️ 사용자).
전체 캡처본은 Actions probe-output-19 아티팩트에 있다.
