import { describe, expect, it } from 'vitest';
import {
  mergeDecisionDate,
  normalizeDate,
  normalizeFootnotes,
  normalizeLabeled,
  toArray,
  toIdString,
  unwrapEnvelope,
} from '../src/parsers/normalize.js';

describe('toArray', () => {
  it('단일 값을 배열로 변환한다', () => {
    expect(toArray({ a: 1 })).toEqual([{ a: 1 }]);
  });
  it('배열은 그대로 유지한다', () => {
    expect(toArray([1, 2])).toEqual([1, 2]);
  });
  it('null/undefined는 빈 배열', () => {
    expect(toArray(null)).toEqual([]);
    expect(toArray(undefined)).toEqual([]);
  });
});

describe('toIdString', () => {
  it('숫자 ID를 문자열로 변환한다', () => {
    expect(toIdString(8111)).toBe('8111');
  });
  it('빈 문자열/누락은 null', () => {
    expect(toIdString('')).toBeNull();
    expect(toIdString(null)).toBeNull();
  });
});

describe('normalizeDate', () => {
  it('2011.2.22. -> 2011-02-22', () => {
    expect(normalizeDate('2011.2.22.')).toBe('2011-02-22');
  });
  it('2011. 2. 22 형식 처리', () => {
    expect(normalizeDate('2011. 2. 22')).toBe('2011-02-22');
  });
  it('YYYYMMDD 처리', () => {
    expect(normalizeDate('20110222')).toBe('2011-02-22');
  });
  it('이미 ISO 형식', () => {
    expect(normalizeDate('2011-02-22')).toBe('2011-02-22');
  });
  it('파싱 실패 시 null', () => {
    expect(normalizeDate('알 수 없음')).toBeNull();
    expect(normalizeDate('')).toBeNull();
  });
});

describe('mergeDecisionDate', () => {
  it('둘 다 있고 같으면 경고 없음', () => {
    const r = mergeDecisionDate('2011.02.22.', '2011.02.22.');
    expect(r.decision_date).toBe('2011-02-22');
    expect(r.warnings).toHaveLength(0);
  });
  it('둘 다 있고 다르면 경고와 원본 보존', () => {
    const r = mergeDecisionDate('2011.02.22.', '2011.03.01.');
    expect(r.decision_date).toBe('2011-02-22');
    expect(r.decision_date_raw).toBe('2011.02.22.');
    expect(r.resolution_date_raw).toBe('2011.03.01.');
    expect(r.warnings[0].code).toBe('DATE_MISMATCH');
  });
  it('의결일자만 있으면 그것을 사용', () => {
    const r = mergeDecisionDate(null, '2011.02.22.');
    expect(r.decision_date).toBe('2011-02-22');
    expect(r.warnings).toHaveLength(0);
  });
  it('파싱 실패 시 원문 보존 + null 정규화', () => {
    const r = mergeDecisionDate('날짜미상', null);
    expect(r.decision_date).toBeNull();
    expect(r.decision_date_raw).toBe('날짜미상');
    expect(r.warnings[0].code).toBe('DATE_PARSE_FAILED');
  });
});

describe('normalizeFootnotes', () => {
  it('병렬 배열을 객체 배열로 결합한다', () => {
    const { footnotes, warnings } = normalizeFootnotes({
      각주번호: ['1', '2'],
      각주내용: ['내용1', '내용2'],
    });
    expect(footnotes).toEqual([
      { number: '1', content: '내용1' },
      { number: '2', content: '내용2' },
    ]);
    expect(warnings).toHaveLength(0);
  });
  it('길이 불일치 시 누락값 null + 경고', () => {
    const { footnotes, warnings } = normalizeFootnotes({
      각주번호: ['1', '2'],
      각주내용: ['내용1'],
    });
    expect(footnotes[1]).toEqual({ number: '2', content: null });
    expect(warnings[0].code).toBe('FOOTNOTE_LENGTH_MISMATCH');
  });
  it('객체 배열 형태도 처리한다', () => {
    const { footnotes } = normalizeFootnotes([{ 번호: '1', 내용: 'a' }]);
    expect(footnotes).toEqual([{ number: '1', content: 'a' }]);
  });
  it('단일 문자열을 배열로 변환한다', () => {
    const { footnotes } = normalizeFootnotes('단일각주');
    expect(footnotes).toEqual([{ number: null, content: '단일각주' }]);
  });
  it('빈 값은 빈 배열', () => {
    expect(normalizeFootnotes(null).footnotes).toEqual([]);
  });
});

describe('normalizeLabeled', () => {
  it('피심정보 객체를 label/content로 변환한다', () => {
    const r = normalizeLabeled(
      { 피심인구분: '신청인', 피심인: '○○건설' },
      ['피심인구분', '구분'],
      ['피심인', '내용']
    );
    expect(r).toEqual({ label: '신청인', content: '○○건설' });
  });
  it('문자열은 content로', () => {
    expect(normalizeLabeled('내용만', ['구분'], ['내용'])).toEqual({
      label: '',
      content: '내용만',
    });
  });
  it('빈 값은 null', () => {
    expect(normalizeLabeled(null, ['구분'], ['내용'])).toBeNull();
  });
});

describe('unwrapEnvelope', () => {
  it('FtcService 래퍼를 벗겨낸다', () => {
    const { body, wrapperKey } = unwrapEnvelope({ FtcService: { a: 1 } }, ['FtcService']);
    expect(body).toEqual({ a: 1 });
    expect(wrapperKey).toBe('FtcService');
  });
  it('래퍼가 없으면 원본을 반환한다', () => {
    const { body, wrapperKey } = unwrapEnvelope({ a: 1 }, ['FtcService']);
    expect(body).toEqual({ a: 1 });
    expect(wrapperKey).toBeNull();
  });
});
