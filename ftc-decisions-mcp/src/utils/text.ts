/**
 * 텍스트 처리 유틸리티: HTML 엔티티 디코딩, 유니코드 코드포인트 기준 절단 등.
 */

const NAMED_ENTITIES: Record<string, string> = {
  amp: '&',
  lt: '<',
  gt: '>',
  quot: '"',
  apos: "'",
  nbsp: ' ',
};

/**
 * 자주 등장하는 HTML 엔티티를 일반 문자로 디코딩한다.
 * `<img>`, `<표>`, `<각주>` 등 의미 있는 표식은 보존한다.
 */
export function decodeHtmlEntities(input: string): string {
  return input.replace(/&(#x?[0-9a-fA-F]+|[a-zA-Z]+);/g, (match, body) => {
    if (body[0] === '#') {
      const isHex = body[1] === 'x' || body[1] === 'X';
      const codeStr = isHex ? body.slice(2) : body.slice(1);
      const code = parseInt(codeStr, isHex ? 16 : 10);
      if (Number.isFinite(code) && code >= 0 && code <= 0x10ffff) {
        try {
          return String.fromCodePoint(code);
        } catch {
          return match;
        }
      }
      return match;
    }
    const named = NAMED_ENTITIES[body.toLowerCase()];
    return named ?? match;
  });
}

/**
 * 유니코드 코드포인트 배열로 변환한다 (서로게이트 페어 안전 처리).
 */
export function toCodePoints(input: string): string[] {
  return Array.from(input);
}

export interface SliceResult {
  text: string;
  totalLength: number;
  returnedLength: number;
  offset: number;
  limit: number;
  nextOffset: number;
  hasMore: boolean;
}

/**
 * 유니코드 코드포인트 기준으로 문자열을 offset~limit 구간만큼 자른다.
 * offset이 전체 길이 이상이면 빈 문자열과 hasMore=false를 반환한다.
 */
export function sliceByCodePoints(input: string, offset: number, limit: number): SliceResult {
  const cps = toCodePoints(input);
  const totalLength = cps.length;
  const safeOffset = Math.max(0, offset);
  if (safeOffset >= totalLength) {
    return {
      text: '',
      totalLength,
      returnedLength: 0,
      offset: safeOffset,
      limit,
      nextOffset: totalLength,
      hasMore: false,
    };
  }
  const end = Math.min(totalLength, safeOffset + limit);
  const text = cps.slice(safeOffset, end).join('');
  const returnedLength = end - safeOffset;
  const hasMore = end < totalLength;
  return {
    text,
    totalLength,
    returnedLength,
    offset: safeOffset,
    limit,
    nextOffset: hasMore ? end : totalLength,
    hasMore,
  };
}

/**
 * 코드포인트 기준으로 max 길이까지 절단하고, 잘렸는지 여부를 반환한다.
 */
export function truncateByCodePoints(
  input: string,
  max: number
): { text: string; truncated: boolean } {
  const cps = toCodePoints(input);
  if (cps.length <= max) return { text: input, truncated: false };
  return { text: cps.slice(0, max).join(''), truncated: true };
}
