import { describe, expect, it } from 'vitest';
import { loadConfig } from '../src/config.js';

describe('loadConfig', () => {
  it('OC 누락 시 CONFIG_MISSING', () => {
    expect(() => loadConfig({} as NodeJS.ProcessEnv)).toThrowError(/FTC_LAW_API_OC/);
  });

  it('기본값을 적용한다', () => {
    const cfg = loadConfig({ FTC_LAW_API_OC: 'abc' } as NodeJS.ProcessEnv);
    expect(cfg.oc).toBe('abc');
    expect(cfg.baseUrl).toBe('https://www.law.go.kr');
    expect(cfg.timeoutMs).toBe(15000);
    expect(cfg.maxRetries).toBe(2);
    expect(cfg.cacheEnabled).toBe(true);
    expect(cfg.logLevel).toBe('info');
  });

  it('law.go.kr 이외 호스트는 거부한다 (SSRF 방지)', () => {
    expect(() =>
      loadConfig({
        FTC_LAW_API_OC: 'abc',
        FTC_LAW_API_BASE_URL: 'https://evil.example.com',
      } as NodeJS.ProcessEnv)
    ).toThrowError(/law\.go\.kr/);
  });

  it('http는 거부한다', () => {
    expect(() =>
      loadConfig({
        FTC_LAW_API_OC: 'abc',
        FTC_LAW_API_BASE_URL: 'http://www.law.go.kr',
      } as NodeJS.ProcessEnv)
    ).toThrowError(/https/);
  });

  it('숫자 환경변수를 파싱한다', () => {
    const cfg = loadConfig({
      FTC_LAW_API_OC: 'abc',
      FTC_LAW_API_TIMEOUT_MS: '5000',
      FTC_CACHE_ENABLED: 'false',
    } as NodeJS.ProcessEnv);
    expect(cfg.timeoutMs).toBe(5000);
    expect(cfg.cacheEnabled).toBe(false);
  });
});
