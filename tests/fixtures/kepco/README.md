# KEPCO fixture

`notices.json`은 2026-07-03 사용자 제공 **"전자입찰 계약정보" API 기술문서**의
응답 필드 명세·샘플을 기반으로 작성한 fixture다 (필드명·코드값은 실명세와 일치,
데이터 값은 합성).

- 엔드포인트: `https://bigdata.kepco.co.kr/openapi/v1/electContract.do`
- 응답: JSON `{"data": [...]}` — 페이지네이션 없음, 기간 최대 90일
- 값 없는 필드는 `"-"` 문자열로 온다 (어댑터에서 None 처리)
- 코드값: `itemType` Construction/Service, `purchaseType` Product/ConstructionService,
  `competitionType` Open/Destination/Limited/Private, `progressState` 6종

실서비스 검증(실행계획 §2.7) 시 실제 응답 1~2건을 이 파일에 추가·교체하면
매핑 검증이 더 정확해진다.
