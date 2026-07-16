/**
 * MCP 서버 구성 및 도구 등록.
 *
 * stdio 전송을 사용하며, stdout 은 MCP 프로토콜 전용이다.
 */

import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';

import type { Config } from './config.js';
import { FtcClient } from './client/ftcClient.js';
import { LruTtlCache } from './cache.js';
import { toErrorPayload } from './errors.js';
import { logger } from './logger.js';
import {
  getDecisionInputSchema,
  getSectionInputSchema,
  searchInputSchema,
} from './schemas/inputs.js';
import { runSearch } from './tools/search.js';
import { runGetDecision } from './tools/getDecision.js';
import { runGetSection } from './tools/getSection.js';
import type { ToolContext } from './tools/context.js';
import type { NormalizedDecision } from './types/index.js';

function jsonResult(payload: unknown) {
  return {
    content: [{ type: 'text' as const, text: JSON.stringify(payload, null, 2) }],
  };
}

export function createContext(config: Config, deps: { fetchFn?: typeof fetch } = {}): ToolContext {
  return {
    config,
    client: new FtcClient(config, deps),
    searchCache: new LruTtlCache({
      maxEntries: config.cacheMaxEntries,
      enabled: config.cacheEnabled,
    }),
    detailCache: new LruTtlCache<NormalizedDecision>({
      maxEntries: config.cacheMaxEntries,
      enabled: config.cacheEnabled,
    }),
  };
}

/**
 * 도구 핸들러를 공통 오류 처리로 감싼다.
 * 검증/업스트림 오류는 표준 오류 페이로드로 변환해 반환한다(예외를 던지지 않음).
 */
function wrap<T>(
  toolName: string,
  handler: (input: unknown) => Promise<T>
): (input: unknown) => Promise<ReturnType<typeof jsonResult>> {
  return async (input: unknown) => {
    try {
      const result = await handler(input);
      return jsonResult(result);
    } catch (err) {
      const payload = toErrorPayload(err);
      logger.warn('tool error', {
        tool: toolName,
        code: payload.error.code,
        message: payload.error.message,
      });
      return jsonResult(payload);
    }
  };
}

export function createServer(ctx: ToolContext): McpServer {
  const server = new McpServer({
    name: 'ftc-decisions-mcp',
    version: '1.0.0',
  });

  server.registerTool(
    'search_ftc_decisions',
    {
      title: '공정위 결정문 검색',
      description:
        '공정거래위원회가 공개한 의결서, 시정권고서 등 결정문 목록을 사건명 또는 본문 키워드로 검색한다. 특정 결정문의 전문이 필요하면 결과의 decision_id 를 get_ftc_decision 에 전달한다.',
      inputSchema: searchInputSchema.shape,
    },
    wrap('search_ftc_decisions', (input) => runSearch(ctx, input))
  );

  server.registerTool(
    'get_ftc_decision',
    {
      title: '공정위 결정문 상세 조회',
      description:
        '결정문 일련번호(decision_id)로 주문, 이유, 피심정보, 결정요지, 각주, 별지 등을 조회한다. 사건번호가 아니라 decision_id 를 입력해야 한다. fields 로 필요한 부분만, max_text_length 로 길이를 제한할 수 있다.',
      inputSchema: getDecisionInputSchema.shape,
    },
    wrap('get_ftc_decision', (input) => runGetDecision(ctx, input))
  );

  server.registerTool(
    'get_ftc_decision_section',
    {
      title: '공정위 결정문 구간 조회',
      description:
        '긴 결정문의 이유, 주문, 별지 등 특정 필드를 유니코드 문자 오프셋(offset)과 길이(limit)로 나누어 조회한다. has_more 와 next_offset 으로 이어서 조회한다.',
      inputSchema: getSectionInputSchema.shape,
    },
    wrap('get_ftc_decision_section', (input) => runGetSection(ctx, input))
  );

  return server;
}
