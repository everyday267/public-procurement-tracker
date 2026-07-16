/**
 * 표준 오류 모델.
 *
 * 모든 도구는 실패 시 `{ error: { code, message, retryable, details } }`
 * 형태의 JSON을 반환한다.
 */

export type ErrorCode =
  | 'CONFIG_MISSING'
  | 'INVALID_INPUT'
  | 'INVALID_DECISION_ID'
  | 'INVALID_PAGE_SIZE'
  | 'AUTH_FAILED'
  | 'DECISION_NOT_FOUND'
  | 'NO_SEARCH_RESULTS'
  | 'UPSTREAM_TIMEOUT'
  | 'UPSTREAM_RATE_LIMITED'
  | 'UPSTREAM_HTTP_ERROR'
  | 'UPSTREAM_INVALID_JSON'
  | 'UPSTREAM_SCHEMA_CHANGED'
  | 'UNSUPPORTED_SECTION'
  | 'INTERNAL_ERROR';

export interface ErrorPayload {
  error: {
    code: ErrorCode;
    message: string;
    retryable: boolean;
    details?: Record<string, unknown>;
  };
}

const RETRYABLE: Set<ErrorCode> = new Set([
  'UPSTREAM_TIMEOUT',
  'UPSTREAM_RATE_LIMITED',
  'UPSTREAM_HTTP_ERROR',
  'UPSTREAM_INVALID_JSON',
]);

export class FtcError extends Error {
  readonly code: ErrorCode;
  readonly retryable: boolean;
  readonly details?: Record<string, unknown>;

  constructor(
    code: ErrorCode,
    message: string,
    details?: Record<string, unknown>,
    retryable?: boolean
  ) {
    super(message);
    this.name = 'FtcError';
    this.code = code;
    this.retryable = retryable ?? RETRYABLE.has(code);
    this.details = details;
  }

  toPayload(): ErrorPayload {
    return {
      error: {
        code: this.code,
        message: this.message,
        retryable: this.retryable,
        ...(this.details ? { details: this.details } : {}),
      },
    };
  }
}

/** zod의 ZodError를 런타임 의존 없이 감지한다 (덕 타이핑). */
function isZodError(err: unknown): err is { issues: Array<{ path: (string | number)[]; message: string }> } {
  return (
    typeof err === 'object' &&
    err !== null &&
    (err as { name?: string }).name === 'ZodError' &&
    Array.isArray((err as { issues?: unknown }).issues)
  );
}

export function toErrorPayload(err: unknown): ErrorPayload {
  if (err instanceof FtcError) return err.toPayload();

  if (isZodError(err)) {
    const issues = err.issues.map((i) => ({
      field: i.path.join('.') || '(root)',
      message: i.message,
    }));
    const pathHit = err.issues.map((i) => i.path.join('.'));
    let code: ErrorCode = 'INVALID_INPUT';
    if (pathHit.some((p) => p === 'decision_id')) code = 'INVALID_DECISION_ID';
    else if (pathHit.some((p) => p === 'page_size')) code = 'INVALID_PAGE_SIZE';
    return {
      error: {
        code,
        message: `입력 검증에 실패했습니다: ${issues.map((i) => `${i.field} - ${i.message}`).join('; ')}`,
        retryable: false,
        details: { issues },
      },
    };
  }

  const message = err instanceof Error ? err.message : String(err);
  return {
    error: {
      code: 'INTERNAL_ERROR',
      message,
      retryable: false,
    },
  };
}
