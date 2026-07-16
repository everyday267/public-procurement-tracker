/**
 * stderr 전용 구조화 로거.
 *
 * stdout은 MCP 프로토콜 메시지 전용이므로, 모든 로그는 반드시 stderr로 출력한다.
 * 인증값(OC)은 항상 마스킹된다.
 */

import { maskOc } from './utils/mask.js';

export type LogLevel = 'error' | 'warn' | 'info' | 'debug';

const LEVEL_ORDER: Record<LogLevel, number> = {
  error: 0,
  warn: 1,
  info: 2,
  debug: 3,
};

let currentLevel: LogLevel = 'info';

export function setLogLevel(level: LogLevel): void {
  currentLevel = level;
}

function maskValue(value: unknown): unknown {
  if (typeof value === 'string') return maskOc(value);
  if (Array.isArray(value)) return value.map(maskValue);
  if (value && typeof value === 'object') {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      // 인증 관련 키는 값 자체를 가린다.
      if (/^oc$/i.test(k) || /api_?oc/i.test(k)) {
        out[k] = '***';
      } else {
        out[k] = maskValue(v);
      }
    }
    return out;
  }
  return value;
}

function emit(level: LogLevel, message: string, meta?: Record<string, unknown>): void {
  if (LEVEL_ORDER[level] > LEVEL_ORDER[currentLevel]) return;
  const entry: Record<string, unknown> = {
    ts: new Date().toISOString(),
    level,
    message: maskOc(message),
  };
  if (meta && Object.keys(meta).length > 0) {
    entry.meta = maskValue(meta);
  }
  // stderr 전용
  process.stderr.write(`${JSON.stringify(entry)}\n`);
}

export const logger = {
  error: (message: string, meta?: Record<string, unknown>) => emit('error', message, meta),
  warn: (message: string, meta?: Record<string, unknown>) => emit('warn', message, meta),
  info: (message: string, meta?: Record<string, unknown>) => emit('info', message, meta),
  debug: (message: string, meta?: Record<string, unknown>) => emit('debug', message, meta),
};
