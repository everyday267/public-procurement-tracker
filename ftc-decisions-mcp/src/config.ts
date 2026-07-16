/**
 * 환경변수 기반 설정 로딩 및 검증.
 *
 * 인증값(OC)은 오직 환경변수에서만 읽으며, 하드코딩하지 않는다.
 */

import { FtcError } from './errors.js';
import type { LogLevel } from './logger.js';

export interface Config {
  oc: string;
  baseUrl: string;
  timeoutMs: number;
  maxRetries: number;
  cacheEnabled: boolean;
  cacheMaxEntries: number;
  logLevel: LogLevel;
}

const ALLOWED_LOG_LEVELS: LogLevel[] = ['error', 'warn', 'info', 'debug'];

function parseIntEnv(name: string, raw: string | undefined, fallback: number, min: number): number {
  if (raw === undefined || raw.trim() === '') return fallback;
  const parsed = Number(raw);
  if (!Number.isFinite(parsed) || !Number.isInteger(parsed) || parsed < min) {
    throw new FtcError(
      'CONFIG_MISSING',
      `환경변수 ${name} 값이 올바르지 않습니다. ${min} 이상의 정수여야 합니다.`,
      { name, value: raw }
    );
  }
  return parsed;
}

function parseBoolEnv(raw: string | undefined, fallback: boolean): boolean {
  if (raw === undefined || raw.trim() === '') return fallback;
  return /^(1|true|yes|on)$/i.test(raw.trim());
}

/**
 * 허용된 호스트만 사용하도록 baseUrl을 검증한다 (SSRF 방지).
 */
function validateBaseUrl(raw: string | undefined): string {
  const value = raw && raw.trim() !== '' ? raw.trim() : 'https://www.law.go.kr';
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new FtcError('CONFIG_MISSING', `FTC_LAW_API_BASE_URL 형식이 올바르지 않습니다.`, {
      value,
    });
  }
  if (url.protocol !== 'https:') {
    throw new FtcError('CONFIG_MISSING', 'FTC_LAW_API_BASE_URL 은 https 여야 합니다.', {
      value,
    });
  }
  // law.go.kr 계열 호스트만 허용
  if (!/(^|\.)law\.go\.kr$/i.test(url.hostname)) {
    throw new FtcError(
      'CONFIG_MISSING',
      'FTC_LAW_API_BASE_URL 은 law.go.kr 도메인만 허용됩니다.',
      { host: url.hostname }
    );
  }
  // 경로/쿼리 제거, origin만 사용
  return url.origin;
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): Config {
  const oc = env.FTC_LAW_API_OC?.trim();
  if (!oc) {
    throw new FtcError(
      'CONFIG_MISSING',
      '환경변수 FTC_LAW_API_OC 가 설정되지 않았습니다. 국가법령정보센터에서 발급받은 OC 값을 설정하세요.'
    );
  }

  const logLevelRaw = (env.FTC_LOG_LEVEL?.trim().toLowerCase() ?? 'info') as LogLevel;
  const logLevel = ALLOWED_LOG_LEVELS.includes(logLevelRaw) ? logLevelRaw : 'info';

  return {
    oc,
    baseUrl: validateBaseUrl(env.FTC_LAW_API_BASE_URL),
    timeoutMs: parseIntEnv('FTC_LAW_API_TIMEOUT_MS', env.FTC_LAW_API_TIMEOUT_MS, 15000, 1),
    maxRetries: parseIntEnv('FTC_LAW_API_MAX_RETRIES', env.FTC_LAW_API_MAX_RETRIES, 2, 0),
    cacheEnabled: parseBoolEnv(env.FTC_CACHE_ENABLED, true),
    cacheMaxEntries: parseIntEnv('FTC_CACHE_MAX_ENTRIES', env.FTC_CACHE_MAX_ENTRIES, 200, 1),
    logLevel,
  };
}
