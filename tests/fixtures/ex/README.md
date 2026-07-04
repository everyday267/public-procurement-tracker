# EX fixture

`contracts.json`의 필드명·요청/출력 변수는 2026-07-04 사용자 제공
**"전자조달 계약공개현황" 기술문서**(data.ex.co.kr) 기준이다 (데이터 값은 합성).

- Request URL: `https://data.ex.co.kr/openapi/elctPrcmInfo/elctPrcmCntrtOppubPrss`
- 공고구분코드: CT=공사, SV=용역, MT=물품 등 13종

응답 JSON의 **목록 키 이름("list" 가정)과 code 성공값("SUCCESS" 가정)은
문서에 명시가 없어** 실서비스 검증에서 확정 후 이 fixture를 갱신한다.
(`src/adapters/ex.py`의 `_extract_rows`가 방어적으로 파싱)
