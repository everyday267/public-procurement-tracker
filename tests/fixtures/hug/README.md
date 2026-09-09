# HUG fixture

사용자 제공 프로그램사양서 `HUG-PA_프로그램사양서` v0.7(2019-03-11)
**[OPEN API] 지역별 분양보증현황**(프로그램ID `AreaBusinessDistributionGuaranteeStatus`)의
응답 예제를 기반으로 작성했다.

- 엔드포인트: `https://www.khug.or.kr/areaBusinessDistributionGuaranteeStatus.do`
- 파라미터: `API_KEY`(인증키), `AREA_DCD`(지역) 또는 `BUNO`(사업장번호, 상세)
- 응답: XML `response > header(resultCode/resultMsg) + body > items > item`

## 파일

| 파일 | 근거 |
|---|---|
| `area_list.xml` | 사양서 응답 예제 4건 + 합성 1건. **사업장명 일부를 오피스텔/상가 케이스로 변형**해 분류 로직을 검증한다. |
| `detail.xml` | ⚠️ **가정**. 사양서에 BUNO 상세의 응답 필드 명세가 없어, "목록 5개 필드 + 유형/세대수 추가 필드"를 가정한 합성 fixture다. 미지 필드 보존과 필드 기반 분류를 검증하는 용도이며 **실제 필드명(`HOUS_DCD_NM` 등)은 미확정**이다. |
| `error.xml` | resultCode 비정상(인증키 오류) 응답 형태 — 코드값은 공공 OpenAPI 관례를 따른 합성값. |

실서비스 검증(`scripts/probe_hug_api.py`) 후 실제 응답으로 `detail.xml`을
교체하고 `docs/HUG_API_RESEARCH.md`의 미확정 항목을 갱신할 것.
