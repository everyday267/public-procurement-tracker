# KEPCO fixture

⚠️ 현재 파일들은 **합성 샘플(placeholder)** 이다. 개발 환경에서
openapi.kepco.co.kr·data.go.kr 접속이 차단되어 실제 응답을 확보할 수 없었다
(실행계획 §1.3).

사용자가 공공데이터포털 "한국전력공사_전자입찰계약정보"(데이터셋 15148223)
활용신청 후 실제 응답 샘플(공고·낙찰·계약 각 1~2건)을 제공하면:

1. 이 디렉토리의 XML/JSON을 실제 응답으로 교체
2. `src/adapters/kepco.py` 의 `_FIELD_CANDIDATES`·`_PARAM_DATE_*`·오퍼레이션명 확정
3. `tests/test_kepco_adapter.py` 재실행으로 매핑 검증
