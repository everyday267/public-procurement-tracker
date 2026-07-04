# KWATER fixture

`notices.json`의 응답 골격(message/data/pagination/list)과 확인된 필드
(tndrPblancDe·tndrPbanno·tndrPblancNm·ctrmthdCdNm·tndrStat·cntrctDivNm 등)는
2026-07-04 3차 조사(run #19, Playwright XHR 캡처)의 **실제 응답** 기반이다.

단, 캡처 샘플이 잘려 **금액 필드명(presmtPc 등)과 cntrctDivNm의 공사 표기는
추정**이며, 실서비스 수집의 `[schema]` 로그로 확정 후 이 fixture와
`src/adapters/kwater.py`의 `_PRICE_KEYS`·`_work_type`을 갱신한다.
전체 캡처본은 Actions probe-output-19 아티팩트에 있다.
