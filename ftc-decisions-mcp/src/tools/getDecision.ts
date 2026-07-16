/**
 * get_ftc_decision 도구의 비즈니스 로직.
 *
 * fields 로 반환 필드를 선택하고, max_text_length 로 각 본문 필드의 길이를
 * 제한한다(코드포인트 기준 절단).
 */

import { logger } from '../logger.js';
import { truncateByCodePoints } from '../utils/text.js';
import { getDecisionInputSchema, type GetDecisionInput } from '../schemas/inputs.js';
import type { DecisionContent, DecisionOutput, Warning } from '../types/index.js';
import { SOURCE_DETAIL, type ToolContext } from './context.js';
import { fetchNormalizedDecision } from './fetchDecision.js';

// content 하위 키 집합
const CONTENT_KEYS: (keyof DecisionContent)[] = [
  'decision_text',
  'order',
  'application_purpose',
  'reason',
  'original_decision',
  'recalculated_decision',
  'subsequent_decision',
  'committee_members',
  'appendix',
  'summary',
];

// fields 값이 content 하위 어떤 키에 대응하는지
const FIELD_TO_CONTENT: Record<string, keyof DecisionContent> = {
  decision_text: 'decision_text',
  order: 'order',
  application_purpose: 'application_purpose',
  reason: 'reason',
  original_decision: 'original_decision',
  recalculated_decision: 'recalculated_decision',
  subsequent_decision: 'subsequent_decision',
  committee_members: 'committee_members',
  appendix: 'appendix',
  summary: 'summary',
};

export async function runGetDecision(
  ctx: ToolContext,
  rawInput: unknown
): Promise<DecisionOutput> {
  const input: GetDecisionInput = getDecisionInputSchema.parse(rawInput);
  const normalized = await fetchNormalizedDecision(ctx, input.decision_id);

  const wantAll = input.fields.includes('all');
  const fieldSet = new Set<string>(input.fields);
  const warnings: Warning[] = [...normalized.warnings];
  const truncatedFields: string[] = [];

  const output: DecisionOutput = {
    decision_id: normalized.decision_id,
    document_type: normalized.document_type,
    metadata: normalized.metadata,
    truncation: { is_truncated: false, truncated_fields: [] },
    warnings,
    source: {
      ...SOURCE_DETAIL,
      decision_id: normalized.decision_id,
      retrieved_at: new Date().toISOString(),
    },
  };

  const want = (f: string): boolean => wantAll || fieldSet.has(f);

  if (want('party_info') && normalized.party_info) {
    output.party_info = normalized.party_info;
  }
  if (want('deliberation_info') && normalized.deliberation_info) {
    output.deliberation_info = normalized.deliberation_info;
  }

  // content 필드 선택 및 절단
  const contentKeysWanted = new Set<keyof DecisionContent>();
  if (wantAll) {
    for (const k of CONTENT_KEYS) contentKeysWanted.add(k);
  } else {
    for (const f of input.fields) {
      const ck = FIELD_TO_CONTENT[f];
      if (ck) contentKeysWanted.add(ck);
    }
  }

  if (contentKeysWanted.size > 0) {
    const content: DecisionContent = {};
    for (const key of contentKeysWanted) {
      const value = normalized.content[key];
      if (value === undefined) continue;
      const { text, truncated } = truncateByCodePoints(value, input.max_text_length);
      content[key] = text;
      if (truncated) truncatedFields.push(`content.${key}`);
    }
    if (Object.keys(content).length > 0) output.content = content;
  }

  if (want('footnotes') && normalized.footnotes.length > 0) {
    output.footnotes = normalized.footnotes;
  }

  if (want('recommendation')) {
    output.recommendation = normalized.recommendation;
  }

  // legal_basis 는 시정권고서의 적용법조/법령의적용에 대응
  if (want('legal_basis') && normalized.recommendation) {
    output.recommendation = normalized.recommendation;
  }

  // 미정의 필드 보존
  if (wantAll && Object.keys(normalized.additional_fields).length > 0) {
    output.additional_fields = normalized.additional_fields;
  }

  if (input.include_raw) {
    output.raw = normalized.additional_fields;
  }

  if (truncatedFields.length > 0) {
    output.truncation = { is_truncated: true, truncated_fields: truncatedFields };
    warnings.push({
      code: 'CONTENT_TRUNCATED',
      message: `일부 본문 필드가 max_text_length(${input.max_text_length})로 절단되었습니다. 전체 내용은 get_ftc_decision_section 으로 구간 조회하세요.`,
      details: { truncated_fields: truncatedFields },
    });
  }

  logger.info('get_decision done', {
    tool: 'get_ftc_decision',
    decision_id: normalized.decision_id,
    document_type: normalized.document_type,
    truncated: truncatedFields.length,
  });

  return output;
}
