/**
 * 목록(검색) 응답 파서.
 *
 * lawSearch.do 응답은 최상위 래퍼(FtcSearch 등) 아래에 결과 배열이 위치할 수
 * 있으며, 0/1/복수 건 모두 배열로 정규화한다.
 */

import { FtcError } from '../errors.js';
import { stripOcFromUrl } from '../utils/mask.js';
import type { SearchResultItem, Warning } from '../types/index.js';
import {
  normalizeDate,
  pick,
  toArray,
  toIdString,
  toStringField,
  unwrapEnvelope,
} from './normalize.js';

// 목록 응답 최상위 래퍼 후보
const SEARCH_ENVELOPE_KEYS = ['FtcSearch', 'ftcSearch', 'LawSearch', 'ftc', 'Ftc'];

// 결과 배열이 담긴 키 후보
const RESULT_ARRAY_KEYS = ['ftc', 'law', 'list', 'items', 'result', '결정문'];

export interface ParsedSearch {
  totalCount: number | null;
  currentPage: number | null;
  results: SearchResultItem[];
  warnings: Warning[];
}

function findResultArray(body: Record<string, unknown>): unknown {
  for (const key of RESULT_ARRAY_KEYS) {
    if (key in body && body[key] !== undefined && body[key] !== null) {
      return body[key];
    }
  }
  // 못 찾으면, 배열형 값을 가진 첫 필드를 사용
  for (const [, v] of Object.entries(body)) {
    if (Array.isArray(v) && v.length > 0 && typeof v[0] === 'object') {
      return v;
    }
  }
  return undefined;
}

export function parseSearchResponse(data: unknown): ParsedSearch {
  const warnings: Warning[] = [];

  const { body } = unwrapEnvelope(data, SEARCH_ENVELOPE_KEYS);

  if (Object.keys(body).length === 0) {
    throw new FtcError('UPSTREAM_SCHEMA_CHANGED', '검색 응답 구조를 인식하지 못했습니다.');
  }

  const totalCount = toIdString(pick(body, ['totalCnt', 'totalCount', '전체건수']));
  const currentPage = toIdString(pick(body, ['page', 'currentPage', '현재페이지']));

  const rawResults = findResultArray(body);
  const resultArr = toArray(rawResults);

  const results: SearchResultItem[] = resultArr.map((item, idx) => {
    const obj = (item && typeof item === 'object' ? item : {}) as Record<string, unknown>;
    const detailLinkRaw = toStringField(
      pick(obj, ['상세링크', 'detailLink', 'link', '결정문상세링크']),
      { trim: true }
    );
    return {
      result_index: idx + 1,
      decision_id: toIdString(
        pick(obj, ['결정문일련번호', '일련번호', 'decisionId', 'id', 'ID', '결정문ID'])
      ),
      case_name: toStringField(pick(obj, ['사건명', 'caseName', 'title']), { trim: true }),
      case_number: toStringField(pick(obj, ['사건번호', 'caseNumber']), { trim: true }),
      document_type: toStringField(pick(obj, ['문서유형', 'documentType', '결정문유형']), {
        trim: true,
      }),
      meeting_type: toStringField(pick(obj, ['회의종류', 'meetingType']), { trim: true }),
      decision_number: toStringField(pick(obj, ['결정번호', 'decisionNumber']), { trim: true }),
      decision_date:
        normalizeDate(pick(obj, ['결정일자', '의결일자', 'decisionDate'])) ??
        toStringField(pick(obj, ['결정일자', '의결일자', 'decisionDate']), { trim: true }),
      // 상세링크에 인증값(OC)이 포함될 수 있으므로 제거한다.
      detail_link: detailLinkRaw ? stripOcFromUrl(detailLinkRaw) : null,
    };
  });

  return {
    totalCount: totalCount !== null ? Number(totalCount) : null,
    currentPage: currentPage !== null ? Number(currentPage) : null,
    results,
    warnings,
  };
}
