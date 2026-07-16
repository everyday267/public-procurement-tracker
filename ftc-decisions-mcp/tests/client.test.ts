import { describe, expect, it, vi } from 'vitest';
import { FtcClient } from '../src/client/ftcClient.js';
import { FtcError } from '../src/errors.js';
import type { Config } from '../src/config.js';

function makeConfig(overrides: Partial<Config> = {}): Config {
  return {
    oc: 'testoc',
    baseUrl: 'https://www.law.go.kr',
    timeoutMs: 1000,
    maxRetries: 2,
    cacheEnabled: false,
    cacheMaxEntries: 10,
    logLevel: 'error',
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(typeof body === 'string' ? body : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('FtcClient - 요청 구성', () => {
  it('모든 요청에 OC/target/type을 포함한다', async () => {
    const fetchFn = vi.fn(async (url: any) => {
      const u = new URL(url);
      expect(u.searchParams.get('OC')).toBe('testoc');
      expect(u.searchParams.get('target')).toBe('ftc');
      expect(u.searchParams.get('type')).toBe('JSON');
      expect(u.searchParams.get('query')).toBe('과징금');
      return jsonResponse({ FtcSearch: { totalCnt: '0' } });
    });
    const client = new FtcClient(makeConfig(), { fetchFn: fetchFn as any });
    await client.search({ query: '과징금', search: 1, display: 20, page: 1 });
    expect(fetchFn).toHaveBeenCalledOnce();
  });
});

describe('FtcClient - 오류 분류', () => {
  it('401은 AUTH_FAILED (재시도 없음)', async () => {
    const fetchFn = vi.fn(async () => new Response('unauthorized', { status: 401 }));
    const client = new FtcClient(makeConfig(), { fetchFn: fetchFn as any });
    await expect(client.getDecision('8111')).rejects.toMatchObject({ code: 'AUTH_FAILED' });
    expect(fetchFn).toHaveBeenCalledOnce();
  });

  it('HTML 오류 페이지는 UPSTREAM_INVALID_JSON', async () => {
    const fetchFn = vi.fn(async () =>
      new Response('<html><body>error</body></html>', {
        status: 200,
        headers: { 'Content-Type': 'text/html' },
      })
    );
    const client = new FtcClient(makeConfig({ maxRetries: 0 }), { fetchFn: fetchFn as any });
    await expect(client.getDecision('8111')).rejects.toMatchObject({
      code: 'UPSTREAM_INVALID_JSON',
    });
  });

  it('빈 응답은 UPSTREAM_INVALID_JSON', async () => {
    const fetchFn = vi.fn(async () => new Response('', { status: 200 }));
    const client = new FtcClient(makeConfig({ maxRetries: 0 }), { fetchFn: fetchFn as any });
    await expect(client.getDecision('8111')).rejects.toMatchObject({
      code: 'UPSTREAM_INVALID_JSON',
    });
  });

  it('잘못된 JSON은 UPSTREAM_INVALID_JSON', async () => {
    const fetchFn = vi.fn(async () =>
      new Response('{not json', { status: 200, headers: { 'Content-Type': 'application/json' } })
    );
    const client = new FtcClient(makeConfig({ maxRetries: 0 }), { fetchFn: fetchFn as any });
    await expect(client.getDecision('8111')).rejects.toMatchObject({
      code: 'UPSTREAM_INVALID_JSON',
    });
  });

  it('BOM이 있어도 파싱한다', async () => {
    const fetchFn = vi.fn(async () =>
      new Response('﻿{"FtcService":{"문서유형":"의결서"}}', { status: 200 })
    );
    const client = new FtcClient(makeConfig({ maxRetries: 0 }), { fetchFn: fetchFn as any });
    const r = (await client.getDecision('8111')) as any;
    expect(r.FtcService.문서유형).toBe('의결서');
  });

  it('200 상태의 인증 오류 메시지를 감지한다', async () => {
    const fetchFn = vi.fn(async () =>
      jsonResponse({ message: '등록되지 않은 OC입니다.' })
    );
    const client = new FtcClient(makeConfig({ maxRetries: 0 }), { fetchFn: fetchFn as any });
    await expect(client.getDecision('8111')).rejects.toMatchObject({ code: 'AUTH_FAILED' });
  });
});

describe('FtcClient - 재시도', () => {
  it('503은 재시도 후 성공하면 결과를 반환한다', async () => {
    let calls = 0;
    const fetchFn = vi.fn(async () => {
      calls += 1;
      if (calls < 2) return new Response('busy', { status: 503 });
      return jsonResponse({ FtcSearch: { totalCnt: '1' } });
    });
    const client = new FtcClient(makeConfig(), { fetchFn: fetchFn as any });
    const r = (await client.search({ query: 'x', search: 1, display: 20, page: 1 })) as any;
    expect(r.FtcSearch.totalCnt).toBe('1');
    expect(calls).toBe(2);
  });

  it('타임아웃(AbortError)은 UPSTREAM_TIMEOUT', async () => {
    const fetchFn = vi.fn(async (_url: any, init: any) => {
      // AbortController가 트리거되면 reject
      return await new Promise<Response>((_resolve, reject) => {
        init.signal.addEventListener('abort', () => {
          const err = new Error('The operation was aborted');
          err.name = 'AbortError';
          reject(err);
        });
      });
    });
    const client = new FtcClient(makeConfig({ timeoutMs: 20, maxRetries: 0 }), {
      fetchFn: fetchFn as any,
    });
    await expect(client.getDecision('8111')).rejects.toMatchObject({ code: 'UPSTREAM_TIMEOUT' });
  });

  it('429는 UPSTREAM_RATE_LIMITED (재시도 소진 후)', async () => {
    const fetchFn = vi.fn(async () => new Response('rate', { status: 429 }));
    const client = new FtcClient(makeConfig({ maxRetries: 1 }), { fetchFn: fetchFn as any });
    await expect(client.getDecision('8111')).rejects.toBeInstanceOf(FtcError);
    await expect(client.getDecision('8111')).rejects.toMatchObject({
      code: 'UPSTREAM_RATE_LIMITED',
    });
  });
});
