/**
 * 프로세스 메모리 LRU + TTL 캐시.
 *
 * - 목록 검색 TTL: 5분
 * - 상세 결정문 TTL: 24시간
 * - 인증값(OC)은 캐시 키에 포함하지 않는다.
 * - 캐시 장애가 본 요청 실패로 이어지지 않도록 방어적으로 동작한다.
 */

interface Entry<V> {
  value: V;
  expiresAt: number;
}

export class LruTtlCache<V = unknown> {
  private readonly map = new Map<string, Entry<V>>();
  private readonly maxEntries: number;
  private readonly enabled: boolean;

  constructor(opts: { maxEntries: number; enabled: boolean }) {
    this.maxEntries = Math.max(1, opts.maxEntries);
    this.enabled = opts.enabled;
  }

  get(key: string): V | undefined {
    if (!this.enabled) return undefined;
    try {
      const entry = this.map.get(key);
      if (!entry) return undefined;
      if (entry.expiresAt <= Date.now()) {
        this.map.delete(key);
        return undefined;
      }
      // LRU: 최근 사용으로 이동
      this.map.delete(key);
      this.map.set(key, entry);
      return entry.value;
    } catch {
      return undefined;
    }
  }

  set(key: string, value: V, ttlMs: number): void {
    if (!this.enabled) return;
    try {
      if (this.map.has(key)) this.map.delete(key);
      this.map.set(key, { value, expiresAt: Date.now() + ttlMs });
      // 용량 초과 시 가장 오래된 항목 제거
      while (this.map.size > this.maxEntries) {
        const oldest = this.map.keys().next().value;
        if (oldest === undefined) break;
        this.map.delete(oldest);
      }
    } catch {
      // 캐시 실패는 무시
    }
  }

  get size(): number {
    return this.map.size;
  }

  clear(): void {
    this.map.clear();
  }
}

export const SEARCH_TTL_MS = 5 * 60 * 1000; // 5분
export const DETAIL_TTL_MS = 24 * 60 * 60 * 1000; // 24시간

/**
 * 정렬된 파라미터로 안정적인 캐시 키를 생성한다 (OC 제외).
 */
export function stableKey(prefix: string, params: Record<string, unknown>): string {
  const entries = Object.entries(params)
    .filter(([k]) => !/^oc$/i.test(k))
    .filter(([, v]) => v !== undefined && v !== null)
    .sort(([a], [b]) => a.localeCompare(b));
  return `${prefix}:${JSON.stringify(entries)}`;
}
