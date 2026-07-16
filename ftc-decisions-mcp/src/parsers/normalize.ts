/**
 * 방어적 정규화 헬퍼.
 *
 * 국가법령정보센터 응답은 단일/배열, 문자열/객체, 결정일자/의결일자 등
 * 구조가 요청마다 달라질 수 있으므로 모든 접근을 방어적으로 처리한다.
 */

import { decodeHtmlEntities } from '../utils/text.js';
import type { Footnote, Warning } from '../types/index.js';

/** 값이 null/undefined/빈 문자열인지. */
export function isBlank(v: unknown): boolean {
  return v === null || v === undefined || (typeof v === 'string' && v === '');
}

/** 단일 값 또는 배열을 항상 배열로 정규화한다. null/undefined는 빈 배열. */
export function toArray<T = unknown>(value: unknown): T[] {
  if (value === null || value === undefined) return [];
  return Array.isArray(value) ? (value as T[]) : [value as T];
}

/**
 * 숫자형 ID를 문자열로 변환한다. 누락 시 null.
 */
export function toIdString(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value === 'number') return String(value);
  if (typeof value === 'string') {
    const trimmed = value.trim();
    return trimmed === '' ? null : trimmed;
  }
  return null;
}

/**
 * 문자열 필드를 정규화한다.
 * - 누락: null
 * - 원본 빈 문자열: "" 유지
 * - HTML 엔티티 디코딩
 * - 본문은 원문 보존(트림하지 않음), 메타데이터는 trim 옵션
 */
export function toStringField(
  value: unknown,
  opts: { trim?: boolean; decode?: boolean } = {}
): string | null {
  if (value === null || value === undefined) return null;
  let str: string;
  if (typeof value === 'string') {
    str = value;
  } else if (typeof value === 'number' || typeof value === 'boolean') {
    str = String(value);
  } else if (Array.isArray(value)) {
    // 배열이면 문자열 요소를 줄바꿈으로 합친다.
    str = value.map((x) => (typeof x === 'string' ? x : JSON.stringify(x))).join('\n');
  } else if (typeof value === 'object') {
    // 객체면 그대로 JSON 문자열화 (예상치 못한 구조 보존용)
    str = JSON.stringify(value);
  } else {
    str = String(value);
  }
  if (opts.decode !== false) str = decodeHtmlEntities(str);
  if (opts.trim) str = str.trim();
  return str;
}

/**
 * 여러 후보 키 중 처음으로 존재하는 값을 반환한다.
 */
export function pick(obj: Record<string, unknown>, keys: string[]): unknown {
  for (const key of keys) {
    if (key in obj && obj[key] !== undefined) return obj[key];
  }
  return undefined;
}

/**
 * 응답 객체에서 `FtcService` 등 최상위 래퍼를 벗겨낸다.
 * 래퍼 후보를 순차 확인하며, 없으면 원본을 반환한다.
 */
export function unwrapEnvelope(
  data: unknown,
  candidateKeys: string[]
): { body: Record<string, unknown>; wrapperKey: string | null } {
  if (data === null || typeof data !== 'object' || Array.isArray(data)) {
    return { body: {}, wrapperKey: null };
  }
  const obj = data as Record<string, unknown>;
  for (const key of candidateKeys) {
    const inner = obj[key];
    if (inner && typeof inner === 'object' && !Array.isArray(inner)) {
      return { body: inner as Record<string, unknown>, wrapperKey: key };
    }
  }
  return { body: obj, wrapperKey: null };
}

/**
 * 날짜 문자열을 ISO(YYYY-MM-DD)로 정규화한다.
 * `2011.2.22.`, `2011. 2. 22`, `2011-02-22`, `20110222` 등을 처리한다.
 * 파싱 실패 시 null.
 */
export function normalizeDate(raw: unknown): string | null {
  const s = toStringField(raw, { trim: true });
  if (!s) return null;
  const cleaned = s.trim();

  // YYYYMMDD
  let m = /^(\d{4})(\d{2})(\d{2})$/.exec(cleaned);
  if (m) return `${m[1]}-${m[2]}-${m[3]}`;

  // YYYY[.-/년] M[.-/월] D
  m = /^(\d{4})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})/.exec(cleaned);
  if (m) {
    const y = m[1];
    const mo = m[2].padStart(2, '0');
    const d = m[3].padStart(2, '0');
    if (Number(mo) >= 1 && Number(mo) <= 12 && Number(d) >= 1 && Number(d) <= 31) {
      return `${y}-${mo}-${d}`;
    }
  }
  return null;
}

/**
 * 결정일자와 의결일자를 병합한다.
 * - 하나만 있으면 그것을 사용
 * - 둘 다 있고 다르면 경고 생성, 원본 보존
 */
export function mergeDecisionDate(
  decisionDateRaw: unknown,
  resolutionDateRaw: unknown
): {
  decision_date: string | null;
  decision_date_raw: string | null;
  resolution_date_raw: string | null;
  warnings: Warning[];
} {
  const warnings: Warning[] = [];
  const dRaw = toStringField(decisionDateRaw, { trim: true });
  const rRaw = toStringField(resolutionDateRaw, { trim: true });

  const dNorm = normalizeDate(dRaw);
  const rNorm = normalizeDate(rRaw);

  let chosen: string | null = null;

  const hasD = !isBlank(dRaw);
  const hasR = !isBlank(rRaw);

  if (hasD && hasR) {
    // 둘 다 존재. 정규화 값 비교.
    if (dNorm && rNorm && dNorm !== rNorm) {
      warnings.push({
        code: 'DATE_MISMATCH',
        message: '결정일자와 의결일자가 서로 다릅니다. 원본 두 값을 모두 보존합니다.',
        details: { decision_date: dRaw, resolution_date: rRaw },
      });
    }
    chosen = dNorm ?? rNorm;
  } else if (hasD) {
    chosen = dNorm;
    if (!dNorm && dRaw) {
      warnings.push({
        code: 'DATE_PARSE_FAILED',
        message: '결정일자 파싱에 실패했습니다. 원문을 보존합니다.',
        details: { decision_date: dRaw },
      });
    }
  } else if (hasR) {
    chosen = rNorm;
    if (!rNorm && rRaw) {
      warnings.push({
        code: 'DATE_PARSE_FAILED',
        message: '의결일자 파싱에 실패했습니다. 원문을 보존합니다.',
        details: { resolution_date: rRaw },
      });
    }
  }

  return {
    decision_date: chosen,
    decision_date_raw: hasD ? dRaw : null,
    resolution_date_raw: hasR ? rRaw : null,
    warnings,
  };
}

/**
 * 라벨-내용 형태의 중첩 필드(피심정보, 심의정보 등)를 정규화한다.
 * 다양한 원본 구조(객체, 문자열)를 방어적으로 처리한다.
 */
export function normalizeLabeled(
  value: unknown,
  labelKeys: string[],
  contentKeys: string[]
): { label: string; content: string } | null {
  if (isBlank(value)) return null;
  if (typeof value === 'string') {
    return { label: '', content: decodeHtmlEntities(value) };
  }
  if (Array.isArray(value)) {
    // 배열이면 첫 요소 사용 시도, 나머지는 병합
    const parts = value
      .map((v) => normalizeLabeled(v, labelKeys, contentKeys))
      .filter((v): v is { label: string; content: string } => v !== null);
    if (parts.length === 0) return null;
    return {
      label: parts[0].label,
      content: parts.map((p) => p.content).join('\n'),
    };
  }
  if (typeof value === 'object') {
    const obj = value as Record<string, unknown>;
    const label = toStringField(pick(obj, labelKeys), { trim: true }) ?? '';
    const content = toStringField(pick(obj, contentKeys)) ?? '';
    return { label, content };
  }
  return null;
}

/**
 * 병렬 각주 배열(번호 배열 + 내용 배열)을 객체 배열로 결합한다.
 * - 단일 문자열/객체도 배열로 변환
 * - 길이 불일치 시 누락값 null 유지 + 경고
 */
export function normalizeFootnotes(value: unknown): {
  footnotes: Footnote[];
  warnings: Warning[];
} {
  const warnings: Warning[] = [];
  if (isBlank(value)) return { footnotes: [], warnings };

  // 케이스 1: 병렬 배열 { 각주번호: [...], 각주내용: [...] }
  // (배열 형태의 각주 객체보다 먼저 확인해야 단일 래퍼 객체를 오인하지 않는다)
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const obj = value as Record<string, unknown>;
    const numbersRaw = pick(obj, ['각주번호', '번호', 'number']);
    const contentsRaw = pick(obj, ['각주내용', '내용', 'content']);
    if (numbersRaw !== undefined || contentsRaw !== undefined) {
      const numbers = toArray(numbersRaw);
      const contents = toArray(contentsRaw);
      const len = Math.max(numbers.length, contents.length);
      if (numbers.length !== contents.length) {
        warnings.push({
          code: 'FOOTNOTE_LENGTH_MISMATCH',
          message: '각주 번호와 내용 배열의 길이가 다릅니다. 누락값은 null로 유지합니다.',
          details: { numbers: numbers.length, contents: contents.length },
        });
      }
      const footnotes: Footnote[] = [];
      for (let i = 0; i < len; i++) {
        footnotes.push({
          number: i < numbers.length ? toIdString(numbers[i]) : null,
          content: i < contents.length ? toStringField(contents[i]) : null,
        });
      }
      return { footnotes, warnings };
    }
  }

  // 케이스 2: 배열 형태의 각주 객체들 [{번호, 내용}, ...]
  const arr = toArray(value);
  if (arr.length > 0 && arr.every((x) => x && typeof x === 'object' && !Array.isArray(x))) {
    const footnotes: Footnote[] = arr.map((x) => {
      const obj = x as Record<string, unknown>;
      return {
        number: toIdString(pick(obj, ['각주번호', '번호', 'number', 'FOOTNOTE_NO'])),
        content:
          toStringField(pick(obj, ['각주내용', '내용', 'content', 'FOOTNOTE_CONTENT'])) ?? null,
      };
    });
    return { footnotes, warnings };
  }

  // 케이스 3: 단일 문자열 (또는 문자열 배열)
  if (typeof value === 'string') {
    return { footnotes: [{ number: null, content: decodeHtmlEntities(value) }], warnings };
  }
  if (arr.length > 0 && arr.every((x) => typeof x === 'string')) {
    return {
      footnotes: arr.map((x) => ({ number: null, content: decodeHtmlEntities(x as string) })),
      warnings,
    };
  }

  return { footnotes: [], warnings };
}
