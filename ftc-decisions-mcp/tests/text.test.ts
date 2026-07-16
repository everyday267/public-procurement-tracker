import { describe, expect, it } from 'vitest';
import {
  decodeHtmlEntities,
  sliceByCodePoints,
  truncateByCodePoints,
} from '../src/utils/text.js';

describe('decodeHtmlEntities', () => {
  it('명명 엔티티를 디코딩한다', () => {
    expect(decodeHtmlEntities('a&amp;b&lt;c&gt;')).toBe('a&b<c>');
  });
  it('숫자 엔티티를 디코딩한다', () => {
    expect(decodeHtmlEntities('&#44397;')).toBe('국');
  });
  it('의미 있는 표식은 보존한다', () => {
    // <각주>1</각주> 는 실제 HTML 엔티티가 아니므로 그대로 보존
    expect(decodeHtmlEntities('<각주>1</각주>')).toBe('<각주>1</각주>');
  });
});

describe('sliceByCodePoints', () => {
  it('offset/limit 구간을 반환한다', () => {
    const r = sliceByCodePoints('abcdefghij', 0, 4);
    expect(r.text).toBe('abcd');
    expect(r.totalLength).toBe(10);
    expect(r.returnedLength).toBe(4);
    expect(r.nextOffset).toBe(4);
    expect(r.hasMore).toBe(true);
  });
  it('마지막 구간이면 hasMore=false', () => {
    const r = sliceByCodePoints('abcde', 3, 10);
    expect(r.text).toBe('de');
    expect(r.hasMore).toBe(false);
    expect(r.nextOffset).toBe(5);
  });
  it('offset이 전체 길이 이상이면 빈 문자열', () => {
    const r = sliceByCodePoints('abc', 5, 10);
    expect(r.text).toBe('');
    expect(r.hasMore).toBe(false);
  });
  it('서로게이트 페어(이모지)를 코드포인트 단위로 자른다', () => {
    const r = sliceByCodePoints('😀😁😂', 0, 2);
    expect(r.text).toBe('😀😁');
    expect(r.totalLength).toBe(3);
  });
});

describe('truncateByCodePoints', () => {
  it('max 이하면 그대로', () => {
    expect(truncateByCodePoints('abc', 5)).toEqual({ text: 'abc', truncated: false });
  });
  it('max 초과면 절단 + truncated=true', () => {
    expect(truncateByCodePoints('abcdef', 3)).toEqual({ text: 'abc', truncated: true });
  });
});
