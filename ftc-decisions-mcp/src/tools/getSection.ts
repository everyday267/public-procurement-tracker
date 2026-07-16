/**
 * get_ftc_decision_section 도구의 비즈니스 로직.
 *
 * 긴 결정문의 특정 필드를 유니코드 코드포인트 오프셋 기준으로 나누어 조회한다.
 * 원문을 요약·수정하지 않는다.
 */

import { FtcError } from '../errors.js';
import { logger } from '../logger.js';
import { sliceByCodePoints } from '../utils/text.js';
import { getSectionInputSchema, type GetSectionInput } from '../schemas/inputs.js';
import type { NormalizedDecision } from '../types/index.js';
import { type ToolContext } from './context.js';
import { fetchNormalizedDecision } from './fetchDecision.js';

/**
 * section 값 -> 정규화된 결정문에서 텍스트를 추출하는 함수.
 */
function resolveSectionText(decision: NormalizedDecision, section: string): string | null {
  switch (section) {
    case 'reason':
      return decision.content.reason ?? decision.recommendation?.reason ?? null;
    case 'order':
      return decision.content.order ?? null;
    case 'summary':
      return decision.content.summary ?? null;
    case 'appendix':
      return decision.content.appendix ?? null;
    case 'decision_text':
      return decision.content.decision_text ?? null;
    case 'party_info':
      return decision.party_info?.content ?? null;
    case 'recommendation_reason':
      return decision.recommendation?.reason ?? null;
    case 'violation_content':
      return decision.recommendation?.violation_content ?? null;
    case 'legal_basis':
      return (
        decision.recommendation?.applicable_provisions ??
        decision.recommendation?.application_of_law ??
        null
      );
    default:
      return null;
  }
}

export interface SectionOutput {
  decision_id: string;
  section: string;
  offset: number;
  limit: number;
  total_length: number;
  returned_length: number;
  next_offset: number;
  has_more: boolean;
  text: string;
  source: { provider: string; target: string };
}

export async function runGetSection(
  ctx: ToolContext,
  rawInput: unknown
): Promise<SectionOutput> {
  const input: GetSectionInput = getSectionInputSchema.parse(rawInput);
  const decision = await fetchNormalizedDecision(ctx, input.decision_id);

  const text = resolveSectionText(decision, input.section);
  if (text === null || text === undefined) {
    // 해당 문서유형에 존재하지 않는 구간
    throw new FtcError(
      'UNSUPPORTED_SECTION',
      `이 결정문에는 '${input.section}' 구간이 존재하지 않습니다.`,
      { decision_id: input.decision_id, section: input.section, document_type: decision.document_type }
    );
  }

  const sliced = sliceByCodePoints(text, input.offset, input.limit);

  logger.info('get_section done', {
    tool: 'get_ftc_decision_section',
    decision_id: input.decision_id,
    section: input.section,
    offset: sliced.offset,
    returned: sliced.returnedLength,
    total: sliced.totalLength,
  });

  return {
    decision_id: input.decision_id,
    section: input.section,
    offset: sliced.offset,
    limit: input.limit,
    total_length: sliced.totalLength,
    returned_length: sliced.returnedLength,
    next_offset: sliced.nextOffset,
    has_more: sliced.hasMore,
    text: sliced.text,
    source: { provider: '국가법령정보센터', target: 'ftc' },
  };
}
