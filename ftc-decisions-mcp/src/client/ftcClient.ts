/**
 * 국가법령정보센터 Open API 클라이언트.
 *
 * - 인증값(OC)은 config에서만 읽는다.
 * - 모든 요청에 target=ftc, type=JSON 을 명시한다.
 * - 타임아웃(AbortController), 재시도(지수 백오프), 오류 분류를 처리한다.
 * - 응답 검증: HTTP 상태 -> Content-Type/BOM -> JSON 파싱 -> 오류 문구.
 */

import type { Config } from '../config.js';
import { FtcError } from '../errors.js';
import { logger } from '../logger.js';
import { stripOcFromUrl } from '../utils/mask.js';

const SEARCH_PATH = '/DRF/lawSearch.do';
const SERVICE_PATH = '/DRF/lawService.do';

const RETRYABLE_STATUS = new Set([429, 500, 502, 503, 504]);
const BACKOFF_MS = [300, 900];

export interface FtcClientDeps {
  fetchFn?: typeof fetch;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export class FtcClient {
  private readonly config: Config;
  private readonly fetchFn: typeof fetch;

  constructor(config: Config, deps: FtcClientDeps = {}) {
    this.config = config;
    this.fetchFn = deps.fetchFn ?? fetch;
  }

  private buildUrl(path: string, params: Record<string, string | number | undefined>): URL {
    const url = new URL(path, this.config.baseUrl);
    const search = new URLSearchParams();
    search.set('OC', this.config.oc);
    search.set('target', 'ftc');
    search.set('type', 'JSON');
    for (const [key, value] of Object.entries(params)) {
      if (value === undefined || value === null || value === '') continue;
      search.set(key, String(value));
    }
    url.search = search.toString();
    return url;
  }

  /**
   * 재시도 로직을 포함한 GET 요청. 응답 본문(텍스트)을 반환한다.
   */
  private async requestText(url: URL, toolName: string): Promise<string> {
    const maxAttempts = this.config.maxRetries + 1;
    let lastError: FtcError | null = null;

    for (let attempt = 0; attempt < maxAttempts; attempt++) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), this.config.timeoutMs);
      const startedAt = Date.now();
      try {
        const res = await this.fetchFn(url, {
          method: 'GET',
          signal: controller.signal,
          headers: { Accept: 'application/json' },
        });

        const elapsed = Date.now() - startedAt;

        if (RETRYABLE_STATUS.has(res.status)) {
          lastError =
            res.status === 429
              ? new FtcError('UPSTREAM_RATE_LIMITED', '외부 API 호출 제한(429)에 도달했습니다.', {
                  status: 429,
                })
              : new FtcError('UPSTREAM_HTTP_ERROR', `외부 API HTTP 오류(${res.status})`, {
                  status: res.status,
                });
          logger.warn('upstream retryable status', {
            tool: toolName,
            status: res.status,
            attempt,
            elapsed_ms: elapsed,
          });
          if (attempt < maxAttempts - 1) {
            await sleep(BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)]);
            continue;
          }
          throw lastError;
        }

        if (res.status === 401 || res.status === 403) {
          throw new FtcError('AUTH_FAILED', '인증에 실패했습니다. OC 값을 확인하세요.', {
            status: res.status,
          });
        }
        if (res.status === 400) {
          throw new FtcError('UPSTREAM_HTTP_ERROR', '외부 API가 잘못된 요청(400)으로 응답했습니다.', {
            status: 400,
          });
        }
        if (res.status === 404) {
          throw new FtcError('UPSTREAM_HTTP_ERROR', '외부 API가 404로 응답했습니다.', {
            status: 404,
          });
        }
        if (!res.ok) {
          throw new FtcError('UPSTREAM_HTTP_ERROR', `외부 API HTTP 오류(${res.status})`, {
            status: res.status,
          });
        }

        const text = await res.text();
        logger.debug('upstream ok', {
          tool: toolName,
          status: res.status,
          elapsed_ms: elapsed,
          bytes: text.length,
          attempt,
        });
        return text;
      } catch (err) {
        if (err instanceof FtcError) {
          // 재시도 불가 오류는 즉시 전파
          if (!err.retryable) throw err;
          lastError = err;
          if (attempt < maxAttempts - 1) {
            await sleep(BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)]);
            continue;
          }
          throw err;
        }
        // AbortError -> 타임아웃
        const isAbort =
          (err as { name?: string })?.name === 'AbortError' ||
          (err as Error)?.message?.includes('aborted');
        if (isAbort) {
          lastError = new FtcError('UPSTREAM_TIMEOUT', '외부 API 요청이 타임아웃되었습니다.', {
            timeout_ms: this.config.timeoutMs,
          });
        } else {
          // 네트워크 오류
          lastError = new FtcError(
            'UPSTREAM_HTTP_ERROR',
            `외부 API 연결 오류: ${(err as Error)?.message ?? 'unknown'}`,
            undefined,
            true
          );
        }
        logger.warn('upstream network error', {
          tool: toolName,
          attempt,
          error: (err as Error)?.message,
        });
        if (attempt < maxAttempts - 1) {
          await sleep(BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)]);
          continue;
        }
        throw lastError;
      } finally {
        clearTimeout(timer);
      }
    }
    throw lastError ?? new FtcError('INTERNAL_ERROR', '알 수 없는 오류');
  }

  /**
   * 텍스트를 JSON으로 파싱한다. BOM 제거, HTML 오류 페이지/빈 본문/200 오류
   * 메시지를 방어적으로 처리한다.
   */
  private parseJson(text: string, url: URL): unknown {
    const safeUrl = stripOcFromUrl(url.toString());
    // 빈 본문
    if (!text || text.trim() === '') {
      throw new FtcError('UPSTREAM_INVALID_JSON', '외부 API가 빈 응답을 반환했습니다.', {
        url: safeUrl,
      });
    }
    // BOM 제거
    let body = text;
    if (body.charCodeAt(0) === 0xfeff) body = body.slice(1);
    body = body.trim();

    // HTML 오류 페이지 감지
    if (body.startsWith('<')) {
      // 인증 오류 문구 탐지
      if (/등록되지\s*않은|인증|권한|OC/i.test(body) && /오류|실패|없/.test(body)) {
        throw new FtcError('AUTH_FAILED', '외부 API가 인증 오류 페이지를 반환했습니다.', {
          url: safeUrl,
        });
      }
      throw new FtcError(
        'UPSTREAM_INVALID_JSON',
        '외부 API가 JSON이 아닌 HTML 응답을 반환했습니다.',
        { url: safeUrl }
      );
    }

    let parsed: unknown;
    try {
      parsed = JSON.parse(body);
    } catch {
      throw new FtcError('UPSTREAM_INVALID_JSON', '외부 API 응답 JSON 파싱에 실패했습니다.', {
        url: safeUrl,
      });
    }

    // 200 상태이지만 오류 메시지를 담은 경우 감지
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      const obj = parsed as Record<string, unknown>;
      const msg = obj.message ?? obj.error ?? obj.Result ?? obj.RESULT;
      if (typeof msg === 'string' && /등록되지\s*않은|인증\s*오류|권한이\s*없/.test(msg)) {
        throw new FtcError('AUTH_FAILED', `외부 API 인증 오류: ${msg}`, { url: safeUrl });
      }
    }

    return parsed;
  }

  async search(params: {
    query: string;
    search: 1 | 2;
    display: number;
    page: number;
    sort?: string;
    gana?: string;
  }): Promise<unknown> {
    const url = this.buildUrl(SEARCH_PATH, {
      search: params.search,
      query: params.query,
      display: params.display,
      page: params.page,
      sort: params.sort,
      gana: params.gana,
    });
    const text = await this.requestText(url, 'search_ftc_decisions');
    return this.parseJson(text, url);
  }

  async getDecision(decisionId: string): Promise<unknown> {
    const url = this.buildUrl(SERVICE_PATH, { ID: decisionId });
    const text = await this.requestText(url, 'get_ftc_decision');
    return this.parseJson(text, url);
  }
}
