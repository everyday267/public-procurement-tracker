import { describe, expect, it } from 'vitest';
import { FtcError, toErrorPayload } from '../src/errors.js';
import { getDecisionInputSchema, searchInputSchema } from '../src/schemas/inputs.js';

describe('FtcError', () => {
  it('재시도 가능 여부를 코드에서 유추한다', () => {
    expect(new FtcError('UPSTREAM_TIMEOUT', 'x').retryable).toBe(true);
    expect(new FtcError('DECISION_NOT_FOUND', 'x').retryable).toBe(false);
  });
  it('표준 페이로드 형태를 만든다', () => {
    const p = new FtcError('DECISION_NOT_FOUND', '없음', { decision_id: '9' }).toPayload();
    expect(p.error.code).toBe('DECISION_NOT_FOUND');
    expect(p.error.details).toEqual({ decision_id: '9' });
  });
});

describe('toErrorPayload - ZodError 매핑', () => {
  it('decision_id 오류는 INVALID_DECISION_ID', () => {
    const parsed = getDecisionInputSchema.safeParse({ decision_id: 'abc' });
    expect(parsed.success).toBe(false);
    if (!parsed.success) {
      expect(toErrorPayload(parsed.error).error.code).toBe('INVALID_DECISION_ID');
    }
  });
  it('page_size 오류는 INVALID_PAGE_SIZE', () => {
    const parsed = searchInputSchema.safeParse({ query: 'x', page_size: 999 });
    expect(parsed.success).toBe(false);
    if (!parsed.success) {
      expect(toErrorPayload(parsed.error).error.code).toBe('INVALID_PAGE_SIZE');
    }
  });
  it('일반 검증 오류는 INVALID_INPUT', () => {
    const parsed = searchInputSchema.safeParse({ query: '' });
    expect(parsed.success).toBe(false);
    if (!parsed.success) {
      expect(toErrorPayload(parsed.error).error.code).toBe('INVALID_INPUT');
    }
  });
});
