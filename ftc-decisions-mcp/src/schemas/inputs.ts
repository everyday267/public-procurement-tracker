/**
 * zod 기반 도구 입력 스키마 및 API 매핑 상수.
 */

import { z } from 'zod';

export const SEARCH_SCOPE_TO_API: Record<string, 1 | 2> = {
  case_name: 1,
  full_text: 2,
};

export const SORT_TO_API: Record<string, string> = {
  case_name_asc: 'lasc',
  case_name_desc: 'ldes',
  decision_date_asc: 'dasc',
  decision_date_desc: 'ddes',
  case_number_asc: 'nasc',
  case_number_desc: 'ndes',
};

export const searchInputSchema = z.object({
  query: z.string().min(1, 'query 는 1자 이상이어야 합니다.'),
  search_scope: z.enum(['case_name', 'full_text']).default('case_name'),
  page: z.number().int().min(1).default(1),
  page_size: z.number().int().min(1).max(100).default(20),
  sort: z
    .enum([
      'case_name_asc',
      'case_name_desc',
      'decision_date_asc',
      'decision_date_desc',
      'case_number_asc',
      'case_number_desc',
    ])
    .default('decision_date_desc'),
  gana: z.string().nullish(),
  document_type: z.string().nullish(),
});

export type SearchInput = z.infer<typeof searchInputSchema>;

export const DECISION_FIELDS = [
  'metadata',
  'party_info',
  'deliberation_info',
  'decision_text',
  'order',
  'application_purpose',
  'reason',
  'original_decision',
  'recalculated_decision',
  'subsequent_decision',
  'committee_members',
  'footnotes',
  'appendix',
  'summary',
  'recommendation',
  'legal_basis',
  'all',
] as const;

// decision_id: 숫자로 구성된 문자열 또는 양의 정수
const decisionIdSchema = z
  .union([z.string(), z.number()])
  .transform((v) => String(v).trim())
  .refine((v) => /^\d+$/.test(v), {
    message: 'decision_id 는 숫자로만 구성되어야 합니다.',
  });

export const getDecisionInputSchema = z.object({
  decision_id: decisionIdSchema,
  fields: z.array(z.enum(DECISION_FIELDS)).default(['all']),
  include_raw: z.boolean().default(false),
  max_text_length: z.number().int().min(1).max(100000).default(30000),
});

export type GetDecisionInput = z.infer<typeof getDecisionInputSchema>;

export const SECTION_VALUES = [
  'reason',
  'order',
  'summary',
  'appendix',
  'decision_text',
  'party_info',
  'recommendation_reason',
  'violation_content',
  'legal_basis',
] as const;

export const getSectionInputSchema = z.object({
  decision_id: decisionIdSchema,
  section: z.enum(SECTION_VALUES),
  offset: z.number().int().min(0).default(0),
  limit: z.number().int().min(1).max(20000).default(8000),
});

export type GetSectionInput = z.infer<typeof getSectionInputSchema>;
