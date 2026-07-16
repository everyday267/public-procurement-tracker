# FTC Decisions MCP Server

공정거래위원회(공정위)가 공개한 **결정문**(의결서·시정권고서 등)을 Claude
Desktop / Claude Code 에서 **자연어로 검색·조회**할 수 있는 MCP(Model Context
Protocol) 서버입니다. 데이터 원천은 **국가법령정보센터 Open API**
(`https://www.law.go.kr`, `target=ftc`) 입니다.

> "과징금 분할납부 사건을 찾아줘", "결정문 8111의 주문과 이유를 보여줘",
> "이유 부분 앞 8,000자만 보여줘" 와 같은 자연어 요청을 그대로 처리합니다.

## 지원 기능

- **사건명 / 본문 키워드 검색** (`search_ftc_decisions`)
- **일련번호(decision_id) 기반 상세 본문 조회** (`get_ftc_decision`)
- **긴 결정문의 특정 필드 구간 조회** (`get_ftc_decision_section`)
- 의결서 · 시정권고서 · **미정의 문서유형**을 공통 구조로 정규화
- `피심정보`·`심의정보`·`각주목록` 표준화, `결정일자`/`의결일자` 편차 처리
- 필드 선택·본문 길이 제한·유니코드 코드포인트 기준 구간 조회
- 인증값(OC) 마스킹, 타임아웃·재시도·오류 분류, 메모리 캐시

## 요구 환경

- **Node.js 20 이상**
- 국가법령정보센터 Open API 인증값(OC) — [open.law.go.kr](https://open.law.go.kr) 에서 발급

## 설치 및 빌드

```bash
cd ftc-decisions-mcp
npm install
npm run build      # dist/ 생성
npm test           # 단위·통합 테스트
npm run lint       # eslint
```

## 환경변수

`.env.example` 를 참고하세요. **인증값은 반드시 환경변수로만** 주입하며,
코드·응답·로그 어디에도 노출되지 않습니다.

| 변수 | 필수 | 기본값 | 설명 |
|---|:---:|---|---|
| `FTC_LAW_API_OC` | ✅ | — | 국가법령정보센터 OC(인증) 값 |
| `FTC_LAW_API_BASE_URL` | | `https://www.law.go.kr` | API 호스트 (law.go.kr 도메인만 허용) |
| `FTC_LAW_API_TIMEOUT_MS` | | `15000` | 요청 타임아웃(ms) |
| `FTC_LAW_API_MAX_RETRIES` | | `2` | 재시도 횟수 |
| `FTC_CACHE_ENABLED` | | `true` | 메모리 캐시 사용 |
| `FTC_CACHE_MAX_ENTRIES` | | `200` | 캐시 최대 항목 수 |
| `FTC_LOG_LEVEL` | | `info` | `error`\|`warn`\|`info`\|`debug` |

## 로컬 실행

```bash
FTC_LAW_API_OC=YOUR_OC node dist/index.js
```

stdio 로 동작하므로 일반적으로는 직접 실행하지 않고 Claude 클라이언트가
프로세스를 띄웁니다. `stdout` 은 MCP 프로토콜 전용이며, 모든 로그는 `stderr` 로
출력됩니다.

## Claude 연결법

### Claude Desktop

`claude_desktop_config.json` (macOS: `~/Library/Application Support/Claude/`)
에 다음을 추가합니다.

```json
{
  "mcpServers": {
    "ftc-decisions": {
      "command": "node",
      "args": ["/ABSOLUTE/PATH/ftc-decisions-mcp/dist/index.js"],
      "env": {
        "FTC_LAW_API_OC": "YOUR_API_OC_VALUE"
      }
    }
  }
}
```

### Claude Code

```bash
claude mcp add ftc-decisions \
  --env FTC_LAW_API_OC=YOUR_API_OC_VALUE \
  -- node /ABSOLUTE/PATH/ftc-decisions-mcp/dist/index.js
```

설정 후 Claude 에서 도구 목록에 `search_ftc_decisions`,
`get_ftc_decision`, `get_ftc_decision_section` 이 보이면 정상입니다.

## 도구별 입력 예시

### `search_ftc_decisions`

```json
{
  "query": "과징금 분할납부",
  "search_scope": "case_name",
  "page": 1,
  "page_size": 20,
  "sort": "decision_date_desc",
  "document_type": null
}
```

- `search_scope`: `case_name`(사건명) | `full_text`(본문). 기본 `case_name`
- `sort`: `case_name_asc|desc`, `decision_date_asc|desc`, `case_number_asc|desc`
- `page_size`: 1~100 (기본 20)
- `document_type`: API 공식 필터가 아니므로 **현재 페이지 결과에만** 후처리 필터가
  적용되고 경고가 붙습니다.
- 0/1/복수 건 모두 `results` 배열로 반환합니다.

### `get_ftc_decision`

```json
{
  "decision_id": "8111",
  "fields": ["metadata", "party_info", "order", "reason", "footnotes"],
  "include_raw": false,
  "max_text_length": 30000
}
```

- `decision_id`: **사건번호가 아니라** 일련번호(숫자 문자열/정수)
- `fields`: `all` 또는 `metadata`, `party_info`, `deliberation_info`,
  `decision_text`, `order`, `application_purpose`, `reason`,
  `original_decision`, `recalculated_decision`, `subsequent_decision`,
  `committee_members`, `footnotes`, `appendix`, `summary`,
  `recommendation`, `legal_basis`
- `max_text_length`: 본문 필드 최대 길이(코드포인트). 절단 시 `truncation` 에
  표시되며 구간 조회를 안내합니다.

### `get_ftc_decision_section`

```json
{
  "decision_id": "8111",
  "section": "reason",
  "offset": 0,
  "limit": 8000
}
```

- `section`: `reason | order | summary | appendix | decision_text |
  party_info | recommendation_reason | violation_content | legal_basis`
- 유니코드 코드포인트 기준으로 절단하며, `has_more`/`next_offset` 으로 이어서
  조회합니다. 원문을 요약·수정하지 않습니다.

## 자연어 사용 예시

- "과징금 분할납부 사건을 찾아줘." → `search_ftc_decisions` (사건명)
- "본문에 '자금사정에 현저한 어려움'이 포함된 의결서를 찾아줘." → `search_ftc_decisions` (본문)
- "결정문 8111의 주문과 이유를 조회해줘." → `get_ftc_decision` (`fields: ["order","reason"]`)
- "입찰담합 관련 최신 의결서 5건을 찾고 첫 번째 본문을 확인해줘." → 검색 후 상세 조회
- "이유 부분의 앞 8,000자만 보여줘." → `get_ftc_decision_section`

## 오류 해결

응답이 실패하면 표준 오류 페이로드가 반환됩니다.

```json
{ "error": { "code": "DECISION_NOT_FOUND", "message": "...", "retryable": false } }
```

| 코드 | 원인 / 대처 |
|---|---|
| `CONFIG_MISSING` | `FTC_LAW_API_OC` 미설정. 환경변수를 확인하세요. |
| `INVALID_INPUT` / `INVALID_DECISION_ID` / `INVALID_PAGE_SIZE` | 입력값을 확인하세요. |
| `AUTH_FAILED` | OC 값이 잘못되었거나 미등록. 발급 상태를 확인하세요. |
| `DECISION_NOT_FOUND` | 존재하지 않는 일련번호. 사건번호가 아닌 `decision_id` 인지 확인. |
| `NO_SEARCH_RESULTS` | 결과 0건(정상). 검색어/범위를 바꿔보세요. |
| `UPSTREAM_TIMEOUT` / `UPSTREAM_RATE_LIMITED` | 잠시 후 재시도(자동 재시도 포함). |
| `UPSTREAM_HTTP_ERROR` / `UPSTREAM_INVALID_JSON` / `UPSTREAM_SCHEMA_CHANGED` | 외부 API 상태 문제 또는 응답 구조 변경. |

## 공개 자료 사용 유의사항

- 본 서버는 **공개된 결정문만** 조회하며, 원문을 요약·생성·변형하지 않습니다.
- 법률 자문·위법성 판정을 제공하지 않으며, 비실명 처리된 이름을 복원하지
  않습니다. 반환 데이터는 원문 인용·검토 용도로만 사용하세요.
- 인증값(OC)은 로그·응답·오류 URL 어디에도 기록되지 않습니다.

## 아키텍처

```text
Claude ──stdio──▶ MCP 서버
                   ├─ Tool handlers (tools/)
                   ├─ Input validation (schemas/, zod)
                   ├─ FTC API client (client/)  ── HTTPS GET ─▶ law.go.kr
                   ├─ Response parsers/normalizer (parsers/)   ├─ lawSearch.do
                   ├─ Text chunker (utils/text)                └─ lawService.do
                   ├─ LRU+TTL cache (cache.ts)
                   └─ Error mapper (errors.ts)
```

- **검색 캐시** TTL 5분, **상세 캐시** TTL 24시간 (OC 는 캐시 키에서 제외).
- 응답은 요청마다 구조가 달라질 수 있으므로 `FtcService`/`FtcSearch` 래퍼 해제,
  단일/배열 정규화, 병렬 각주 배열 결합, `결정일자`/`의결일자` 병합 등
  **런타임 shape detection** 을 적용합니다.

## 알려진 제한사항

- `document_type` 필터는 API 공식 조건이 아니므로 현재 페이지 결과에만
  적용됩니다(경고 포함).
- 첨부파일/별지 이미지 다운로드, OCR, 벡터 검색, 영구 저장은 제공하지 않습니다.
- MCP 프로토콜 경계에서 스키마 검증에 실패한 입력은 MCP 표준 오류(-32602)로,
  그 밖의 실패는 위 표준 오류 페이로드로 반환됩니다.
- 원본 API 응답의 실제 키 이름은 서비스 상황에 따라 달라질 수 있어, 파서는
  다수의 후보 키를 방어적으로 탐색합니다.

## 라이선스

MIT
