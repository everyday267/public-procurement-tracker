import { describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import type { Config } from '../src/config.js';
import { createContext } from '../src/server.js';
import { runSearch } from '../src/tools/search.js';
import { runGetDecision } from '../src/tools/getDecision.js';
import { runGetSection } from '../src/tools/getSection.js';

const here = dirname(fileURLToPath(import.meta.url));
function fixtureText(name: string): string {
  return readFileSync(join(here, 'fixtures', name), 'utf-8');
}

function makeConfig(overrides: Partial<Config> = {}): Config {
  return {
    oc: 'testoc',
    baseUrl: 'https://www.law.go.kr',
    timeoutMs: 1000,
    maxRetries: 0,
    cacheEnabled: true,
    cacheMaxEntries: 10,
    logLevel: 'error',
    ...overrides,
  };
}

function response(text: string, status = 200): Response {
  return new Response(text, { status, headers: { 'Content-Type': 'application/json' } });
}

/** 경로에 따라 fixture를 반환하는 mock fetch. */
function routeFetch(map: { search?: string; service?: string }) {
  return vi.fn(async (url: any) => {
    const u = new URL(url);
    if (u.pathname.includes('lawSearch') && map.search) return response(fixtureText(map.search));
    if (u.pathname.includes('lawService') && map.service) return response(fixtureText(map.service));
    return response('{}', 404);
  });
}

describe('runSearch', () => {
  it('사건명 검색 결과를 정규화하여 반환한다', async () => {
    const fetchFn = routeFetch({ search: 'search_multiple.json' });
    const ctx = createContext(makeConfig(), { fetchFn: fetchFn as any });
    const out = await runSearch(ctx, { query: '과징금 분할납부', search_scope: 'case_name' });
    expect(out.results).toHaveLength(2);
    expect(out.pagination.total_count).toBe(10);
    expect(out.source.target).toBe('ftc');
  });

  it('올바른 sort/scope가 API 값으로 매핑된다', async () => {
    const fetchFn = vi.fn(async (url: any) => {
      const u = new URL(url);
      expect(u.searchParams.get('search')).toBe('2');
      expect(u.searchParams.get('sort')).toBe('ddes');
      return response(fixtureText('search_empty.json'));
    });
    const ctx = createContext(makeConfig(), { fetchFn: fetchFn as any });
    await runSearch(ctx, {
      query: '자금사정',
      search_scope: 'full_text',
      sort: 'decision_date_desc',
    });
  });

  it('0건이면 빈 배열과 경고를 반환한다', async () => {
    const fetchFn = routeFetch({ search: 'search_empty.json' });
    const ctx = createContext(makeConfig(), { fetchFn: fetchFn as any });
    const out = await runSearch(ctx, { query: '없는사건' });
    expect(out.results).toHaveLength(0);
    expect(out.warnings.some((w) => w.code === 'NO_SEARCH_RESULTS')).toBe(true);
  });

  it('document_type 후처리 필터를 적용하고 경고한다', async () => {
    const fetchFn = routeFetch({ search: 'search_multiple.json' });
    const ctx = createContext(makeConfig(), { fetchFn: fetchFn as any });
    const out = await runSearch(ctx, { query: '과징금', document_type: '시정권고서' });
    expect(out.results).toHaveLength(1);
    expect(out.results[0].document_type).toBe('시정권고서');
    expect(out.warnings.some((w) => w.code === 'POST_FILTER_APPLIED')).toBe(true);
  });

  it('page_size 범위 위반은 검증 오류를 던진다', async () => {
    const ctx = createContext(makeConfig(), { fetchFn: vi.fn() as any });
    await expect(runSearch(ctx, { query: 'x', page_size: 500 })).rejects.toBeTruthy();
  });
});

describe('runGetDecision', () => {
  it('의결서 상세를 조회하고 필드를 선택 반환한다', async () => {
    const fetchFn = routeFetch({ service: 'decision_8111.json' });
    const ctx = createContext(makeConfig(), { fetchFn: fetchFn as any });
    const out = await runGetDecision(ctx, {
      decision_id: '8111',
      fields: ['metadata', 'order', 'reason'],
    });
    expect(out.metadata.case_name).toContain('건설사');
    expect(out.content?.order).toBeTruthy();
    expect(out.content?.reason).toBeTruthy();
    // 선택하지 않은 summary는 없어야 한다
    expect(out.content?.summary).toBeUndefined();
  });

  it('max_text_length로 절단하고 truncation 정보를 채운다', async () => {
    const fetchFn = routeFetch({ service: 'decision_8111.json' });
    const ctx = createContext(makeConfig(), { fetchFn: fetchFn as any });
    const out = await runGetDecision(ctx, {
      decision_id: '8111',
      fields: ['reason'],
      max_text_length: 5,
    });
    expect(out.truncation.is_truncated).toBe(true);
    expect(out.truncation.truncated_fields).toContain('content.reason');
  });

  it('숫자 decision_id도 허용한다', async () => {
    const fetchFn = routeFetch({ service: 'decision_8111.json' });
    const ctx = createContext(makeConfig(), { fetchFn: fetchFn as any });
    const out = await runGetDecision(ctx, { decision_id: 8111 as any });
    expect(out.decision_id).toBe('8111');
  });

  it('잘못된 decision_id는 검증 오류', async () => {
    const ctx = createContext(makeConfig(), { fetchFn: vi.fn() as any });
    await expect(runGetDecision(ctx, { decision_id: 'abc' })).rejects.toBeTruthy();
  });

  it('시정권고서는 recommendation을 채운다', async () => {
    const fetchFn = routeFetch({ service: 'decision_recommendation.json' });
    const ctx = createContext(makeConfig(), { fetchFn: fetchFn as any });
    const out = await runGetDecision(ctx, { decision_id: '9001' });
    expect(out.recommendation?.recommended_action).toContain('중지');
  });
});

describe('runGetSection', () => {
  it('reason 구간을 offset/limit으로 조회한다', async () => {
    const fetchFn = routeFetch({ service: 'decision_8111.json' });
    const ctx = createContext(makeConfig(), { fetchFn: fetchFn as any });
    const out = await runGetSection(ctx, {
      decision_id: '8111',
      section: 'reason',
      offset: 0,
      limit: 5,
    });
    expect(out.returned_length).toBe(5);
    expect(out.has_more).toBe(true);
    expect(out.total_length).toBeGreaterThan(5);
  });

  it('존재하지 않는 구간은 UNSUPPORTED_SECTION', async () => {
    const fetchFn = routeFetch({ service: 'decision_8111.json' });
    const ctx = createContext(makeConfig(), { fetchFn: fetchFn as any });
    await expect(
      runGetSection(ctx, { decision_id: '8111', section: 'violation_content' })
    ).rejects.toMatchObject({ code: 'UNSUPPORTED_SECTION' });
  });

  it('offset이 전체 길이 이상이면 빈 문자열', async () => {
    const fetchFn = routeFetch({ service: 'decision_8111.json' });
    const ctx = createContext(makeConfig(), { fetchFn: fetchFn as any });
    const out = await runGetSection(ctx, {
      decision_id: '8111',
      section: 'order',
      offset: 100000,
      limit: 100,
    });
    expect(out.text).toBe('');
    expect(out.has_more).toBe(false);
  });
});

describe('검색 후 상세 조회 (통합)', () => {
  it('검색 결과의 decision_id로 상세를 조회한다', async () => {
    const fetchFn = routeFetch({
      search: 'search_multiple.json',
      service: 'decision_8111.json',
    });
    const ctx = createContext(makeConfig(), { fetchFn: fetchFn as any });
    const search = await runSearch(ctx, { query: '과징금' });
    const id = search.results[0].decision_id!;
    const detail = await runGetDecision(ctx, { decision_id: id });
    expect(detail.decision_id).toBe('8111');
  });
});
