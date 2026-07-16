/**
 * 공통 타입 정의.
 */

export interface Source {
  provider: string;
  service: string;
  target: string;
  [key: string]: unknown;
}

export interface Warning {
  code: string;
  message: string;
  details?: Record<string, unknown>;
}

/** 목록(검색) 정규화 결과의 개별 항목. */
export interface SearchResultItem {
  result_index: number;
  decision_id: string | null;
  case_name: string | null;
  case_number: string | null;
  document_type: string | null;
  meeting_type: string | null;
  decision_number: string | null;
  decision_date: string | null;
  detail_link: string | null;
}

export interface SearchOutput {
  query: {
    text: string;
    scope: string;
    page: number;
    page_size: number;
    sort: string;
    document_type: string | null;
  };
  pagination: {
    total_count: number | null;
    current_page: number;
    page_size: number;
    returned_count: number;
    has_next_page: boolean;
  };
  results: SearchResultItem[];
  warnings: Warning[];
  source: Source;
}

export interface LabeledField {
  label: string;
  content: string;
}

export interface Footnote {
  number: string | null;
  content: string | null;
}

export interface DecisionMetadata {
  case_number: string | null;
  case_name: string | null;
  meeting_type: string | null;
  decision_number: string | null;
  decision_date: string | null;
  decision_date_raw?: string | null;
  resolution_date_raw?: string | null;
}

export interface DecisionContent {
  decision_text?: string;
  order?: string;
  application_purpose?: string;
  reason?: string;
  original_decision?: string;
  recalculated_decision?: string;
  subsequent_decision?: string;
  committee_members?: string;
  appendix?: string;
  summary?: string;
  [key: string]: string | undefined;
}

export interface Recommendation {
  decision_subtype: string | null;
  reference_law: string | null;
  recommended_action: string | null;
  reason: string | null;
  violation_content: string | null;
  applicable_provisions: string | null;
  application_of_law: string | null;
  correction_deadline: string | null;
  acceptance_notice_period: string | null;
  acceptance_notice_deadline: string | null;
  action_on_rejection: string | null;
  policy_on_rejection: string | null;
}

export interface DecisionOutput {
  decision_id: string;
  document_type: string | null;
  metadata: DecisionMetadata;
  party_info?: LabeledField;
  deliberation_info?: LabeledField;
  content?: DecisionContent;
  footnotes?: Footnote[];
  recommendation?: Recommendation | null;
  additional_fields?: Record<string, unknown>;
  truncation: {
    is_truncated: boolean;
    truncated_fields: string[];
  };
  warnings: Warning[];
  source: Source;
  raw?: unknown;
}

/** 정규화 과정에서 파서가 채우는 전체(비절단) 결정문 표현. */
export interface NormalizedDecision {
  decision_id: string;
  document_type: string | null;
  metadata: DecisionMetadata;
  party_info: LabeledField | null;
  deliberation_info: LabeledField | null;
  content: DecisionContent;
  footnotes: Footnote[];
  recommendation: Recommendation | null;
  additional_fields: Record<string, unknown>;
  warnings: Warning[];
}
