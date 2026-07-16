/**
 * 상세 결정문을 조회·정규화하고 캐시하는 공유 헬퍼.
 * get_ftc_decision 과 get_ftc_decision_section 이 함께 사용한다.
 */

import { DETAIL_TTL_MS } from '../cache.js';
import { FtcError } from '../errors.js';
import { parseDecisionResponse } from '../parsers/decisionParser.js';
import type { NormalizedDecision } from '../types/index.js';
import type { ToolContext } from './context.js';

export async function fetchNormalizedDecision(
  ctx: ToolContext,
  decisionId: string
): Promise<NormalizedDecision> {
  const cached = ctx.detailCache.get(decisionId);
  if (cached) return cached;

  const raw = await ctx.client.getDecision(decisionId);

  // 미조회(빈 응답) 감지: 파서가 빈 body를 만나면 스키마 오류를 던지므로
  // 여기서 별도로 "결과 없음"을 판별한다.
  if (isEmptyDetail(raw)) {
    throw new FtcError('DECISION_NOT_FOUND', '해당 결정문을 찾을 수 없습니다.', {
      decision_id: decisionId,
    });
  }

  const normalized = parseDecisionResponse(raw, decisionId);
  ctx.detailCache.set(decisionId, normalized, DETAIL_TTL_MS);
  return normalized;
}

function isEmptyDetail(raw: unknown): boolean {
  if (raw === null || raw === undefined) return true;
  if (typeof raw !== 'object') return false;
  const obj = raw as Record<string, unknown>;
  // FtcService 래퍼가 비어있거나, 전체가 빈 객체인 경우
  if (Object.keys(obj).length === 0) return true;
  const service = obj.FtcService ?? obj.ftcService;
  if (service && typeof service === 'object' && Object.keys(service as object).length === 0) {
    return true;
  }
  return false;
}
