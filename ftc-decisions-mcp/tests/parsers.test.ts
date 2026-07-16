import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import { parseSearchResponse } from '../src/parsers/searchParser.js';
import { parseDecisionResponse } from '../src/parsers/decisionParser.js';

const here = dirname(fileURLToPath(import.meta.url));
function fixture(name: string): any {
  return JSON.parse(readFileSync(join(here, 'fixtures', name), 'utf-8'));
}

describe('parseSearchResponse', () => {
  it('복수 결과를 배열로 정규화하고 상세링크의 OC를 제거한다', () => {
    const parsed = parseSearchResponse(fixture('search_multiple.json'));
    expect(parsed.totalCount).toBe(10);
    expect(parsed.results).toHaveLength(2);
    expect(parsed.results[0].decision_id).toBe('8111');
    expect(parsed.results[0].decision_date).toBe('2011-02-22');
    expect(parsed.results[0].result_index).toBe(1);
    expect(parsed.results[0].detail_link).not.toContain('secret_oc');
  });

  it('단일 결과도 배열로 정규화한다', () => {
    const parsed = parseSearchResponse(fixture('search_single.json'));
    expect(parsed.results).toHaveLength(1);
    expect(parsed.results[0].decision_id).toBe('8111');
  });

  it('0건은 빈 배열', () => {
    const parsed = parseSearchResponse(fixture('search_empty.json'));
    expect(parsed.results).toHaveLength(0);
    expect(parsed.totalCount).toBe(0);
  });
});

describe('parseDecisionResponse - 의결서', () => {
  it('FtcService 래퍼를 벗기고 공통 필드를 정규화한다', () => {
    const d = parseDecisionResponse(fixture('decision_8111.json'), '8111');
    expect(d.decision_id).toBe('8111');
    expect(d.document_type).toBe('의결서');
    expect(d.metadata.case_number).toBe('2011카총0367');
    expect(d.metadata.decision_date).toBe('2011-02-22');
    expect(d.content.order).toContain('인용한다');
    expect(d.content.reason).toContain('자금사정');
    // 각주 표식 보존
    expect(d.content.reason).toContain('<각주>1</각주>');
  });

  it('피심정보를 label/content로 변환한다', () => {
    const d = parseDecisionResponse(fixture('decision_8111.json'), '8111');
    expect(d.party_info).toEqual({ label: '신청인', content: '○○건설 주식회사 외 9인' });
  });

  it('병렬 각주 배열을 결합한다', () => {
    const d = parseDecisionResponse(fixture('decision_8111.json'), '8111');
    expect(d.footnotes).toEqual([{ number: '1', content: '자금사정 관련 소명자료 참조' }]);
  });

  it('HTML 엔티티를 디코딩한다', () => {
    const d = parseDecisionResponse(fixture('decision_8111.json'), '8111');
    // &lt;각주&gt; -> <각주>
    expect(d.content.reason).not.toContain('&lt;');
  });
});

describe('parseDecisionResponse - 시정권고서', () => {
  it('시정권고 필드를 정규화한다', () => {
    const d = parseDecisionResponse(fixture('decision_recommendation.json'), '9001');
    expect(d.document_type).toBe('시정권고서');
    expect(d.recommendation).not.toBeNull();
    expect(d.recommendation?.recommended_action).toContain('중지');
    expect(d.recommendation?.reference_law).toContain('표시·광고');
    expect(d.recommendation?.correction_deadline).toContain('30일');
  });
});

describe('parseDecisionResponse - 미정의 문서유형', () => {
  it('공통 필드는 파싱하고 미정의 필드는 additional_fields에 보존하며 경고를 추가한다', () => {
    const d = parseDecisionResponse(fixture('decision_unknown_type.json'), '9999');
    expect(d.document_type).toBe('신규심결유형');
    expect(d.content.order).toBe('테스트 주문');
    expect(d.additional_fields['미정의필드A']).toBe('보존되어야 하는 값 A');
    expect(d.warnings.some((w) => w.code === 'UNKNOWN_DOCUMENT_TYPE')).toBe(true);
  });
});
