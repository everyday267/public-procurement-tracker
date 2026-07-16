/**
 * 본문(상세) 응답 파서.
 *
 * lawService.do 응답은 `FtcService` 최상위 래퍼 아래에 상세 데이터가 위치할 수
 * 있다. 의결서 계열과 시정권고서 계열을 각각 정규화하며, 미정의 문서유형도
 * 공통 필드는 정상 파싱하고 미정의 필드는 additional_fields에 보존한다.
 */

import type {
  DecisionContent,
  DecisionMetadata,
  NormalizedDecision,
  Recommendation,
  Warning,
} from '../types/index.js';
import {
  mergeDecisionDate,
  normalizeFootnotes,
  normalizeLabeled,
  pick,
  toStringField,
  unwrapEnvelope,
} from './normalize.js';

const DETAIL_ENVELOPE_KEYS = ['FtcService', 'ftcService', 'ftc', 'Ftc', 'Law', 'law'];

// 공통 콘텐츠 필드: 표준키 -> 원본 후보 키 목록
const CONTENT_FIELD_MAP: Record<string, string[]> = {
  decision_text: ['의결문', 'decisionText'],
  order: ['주문', 'order'],
  application_purpose: ['신청취지', 'applicationPurpose'],
  reason: ['이유', 'reason'],
  original_decision: ['원심결', 'originalDecision'],
  recalculated_decision: ['재산정심결', 'recalculatedDecision'],
  subsequent_decision: ['후속심결', 'subsequentDecision'],
  committee_members: ['위원정보', 'committeeMembers'],
  appendix: ['별지', 'appendix'],
  summary: ['결정요지', 'summary'],
};

// 시정권고서 필드: 표준키 -> 원본 후보 키
const RECOMMENDATION_FIELD_MAP: Record<keyof Recommendation, string[]> = {
  decision_subtype: ['의결서종류', 'decisionSubtype'],
  reference_law: ['시정권고참조법률', 'referenceLaw'],
  recommended_action: ['시정권고사항', 'recommendedAction'],
  reason: ['시정권고이유', 'recommendationReason'],
  violation_content: ['법위반내용', 'violationContent'],
  applicable_provisions: ['적용법조', 'applicableProvisions'],
  application_of_law: ['법령의적용', 'applicationOfLaw'],
  correction_deadline: ['시정기한', 'correctionDeadline'],
  acceptance_notice_period: ['수락여부통지기간', 'acceptanceNoticePeriod'],
  acceptance_notice_deadline: ['수락여부통지기한', 'acceptanceNoticeDeadline'],
  action_on_rejection: ['수락거부시의조치', 'actionOnRejection'],
  policy_on_rejection: ['수락거부시조치방침', 'policyOnRejection'],
};

// 메타/구조 필드로 이미 소비되어 additional_fields에서 제외할 원본 키
const CONSUMED_KEYS = new Set<string>([
  '결정문일련번호',
  '일련번호',
  'decisionId',
  'id',
  'ID',
  '문서유형',
  'documentType',
  '사건번호',
  'caseNumber',
  '사건명',
  'caseName',
  '회의종류',
  'meetingType',
  '결정번호',
  'decisionNumber',
  '결정일자',
  'decisionDate',
  '의결일자',
  'resolutionDate',
  '피심정보',
  'partyInfo',
  '심의정보',
  'deliberationInfo',
  '각주목록',
  '각주',
  'footnotes',
]);

function isRecommendationType(documentType: string | null, body: Record<string, unknown>): boolean {
  if (documentType && /시정권고/.test(documentType)) return true;
  // 시정권고 고유 필드가 존재하면 시정권고서로 판단
  return (
    '시정권고사항' in body ||
    '시정권고이유' in body ||
    '시정권고참조법률' in body ||
    'recommendedAction' in body
  );
}

function buildMetadata(body: Record<string, unknown>): {
  metadata: DecisionMetadata;
  warnings: Warning[];
} {
  const dateMerge = mergeDecisionDate(
    pick(body, ['결정일자', 'decisionDate']),
    pick(body, ['의결일자', 'resolutionDate'])
  );
  const metadata: DecisionMetadata = {
    case_number: toStringField(pick(body, ['사건번호', 'caseNumber']), { trim: true }),
    case_name: toStringField(pick(body, ['사건명', 'caseName']), { trim: true }),
    meeting_type: toStringField(pick(body, ['회의종류', 'meetingType']), { trim: true }),
    decision_number: toStringField(pick(body, ['결정번호', 'decisionNumber']), { trim: true }),
    decision_date: dateMerge.decision_date,
    decision_date_raw: dateMerge.decision_date_raw,
    resolution_date_raw: dateMerge.resolution_date_raw,
  };
  return { metadata, warnings: dateMerge.warnings };
}

function buildContent(body: Record<string, unknown>): DecisionContent {
  const content: DecisionContent = {};
  for (const [stdKey, candidates] of Object.entries(CONTENT_FIELD_MAP)) {
    const value = pick(body, candidates);
    if (value !== undefined) {
      // 본문은 원문 보존(trim 안 함), HTML 엔티티만 디코딩
      content[stdKey] = toStringField(value, { trim: false }) ?? '';
    }
    // 소비된 원본 키 기록
    for (const c of candidates) CONSUMED_KEYS.add(c);
  }
  return content;
}

function buildRecommendation(body: Record<string, unknown>): Recommendation {
  const rec = {} as Recommendation;
  for (const [stdKey, candidates] of Object.entries(RECOMMENDATION_FIELD_MAP)) {
    rec[stdKey as keyof Recommendation] = toStringField(pick(body, candidates), { trim: false });
    for (const c of candidates) CONSUMED_KEYS.add(c);
  }
  return rec;
}

function collectAdditionalFields(body: Record<string, unknown>): Record<string, unknown> {
  const extra: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(body)) {
    if (!CONSUMED_KEYS.has(key)) {
      extra[key] = value;
    }
  }
  return extra;
}

/**
 * 상세 응답을 정규화된 결정문으로 변환한다.
 * @param decisionId 조회에 사용된 일련번호(응답에 없을 때 대비)
 */
export function parseDecisionResponse(data: unknown, decisionId: string): NormalizedDecision {
  const warnings: Warning[] = [];
  const { body } = unwrapEnvelope(data, DETAIL_ENVELOPE_KEYS);

  const documentType = toStringField(pick(body, ['문서유형', 'documentType', '결정문유형']), {
    trim: true,
  });

  const idFromBody = toStringField(pick(body, ['결정문일련번호', '일련번호', 'decisionId']), {
    trim: true,
  });

  const { metadata, warnings: metaWarnings } = buildMetadata(body);
  warnings.push(...metaWarnings);

  const partyInfo = normalizeLabeled(
    pick(body, ['피심정보', 'partyInfo']),
    ['피심인구분', '구분', 'label', '라벨'],
    ['피심인', '내용', 'content', '피심정보내용']
  );

  const deliberationInfo = normalizeLabeled(
    pick(body, ['심의정보', 'deliberationInfo']),
    ['구분', 'label', '라벨'],
    ['내용', 'content']
  );

  const { footnotes, warnings: fnWarnings } = normalizeFootnotes(
    pick(body, ['각주목록', '각주', 'footnotes'])
  );
  warnings.push(...fnWarnings);

  const content = buildContent(body);

  let recommendation: Recommendation | null = null;
  const isRec = isRecommendationType(documentType, body);
  if (isRec) {
    recommendation = buildRecommendation(body);
  }

  // 미정의 문서유형 경고
  const KNOWN_TYPES = /(의결서|결정서|재결서|시정권고서)/;
  if (documentType && !KNOWN_TYPES.test(documentType)) {
    warnings.push({
      code: 'UNKNOWN_DOCUMENT_TYPE',
      message: `미정의 문서유형(${documentType})입니다. 공통 필드만 정규화하고 나머지는 additional_fields에 보존합니다.`,
      details: { document_type: documentType },
    });
  }

  const additionalFields = collectAdditionalFields(body);

  return {
    decision_id: idFromBody ?? decisionId,
    document_type: documentType,
    metadata,
    party_info: partyInfo,
    deliberation_info: deliberationInfo,
    content,
    footnotes,
    recommendation,
    additional_fields: additionalFields,
    warnings,
  };
}
