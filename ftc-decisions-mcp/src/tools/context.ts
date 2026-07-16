/**
 * 도구 핸들러가 공유하는 실행 컨텍스트.
 * 비즈니스 로직과 MCP 핸들러를 분리하기 위한 의존성 묶음.
 */

import type { Config } from '../config.js';
import type { FtcClient } from '../client/ftcClient.js';
import type { LruTtlCache } from '../cache.js';
import type { NormalizedDecision } from '../types/index.js';

export interface ToolContext {
  config: Config;
  client: FtcClient;
  searchCache: LruTtlCache;
  detailCache: LruTtlCache<NormalizedDecision>;
}

export const SOURCE_SEARCH = {
  provider: '국가법령정보센터',
  service: '공정거래위원회 결정문 목록',
  target: 'ftc',
} as const;

export const SOURCE_DETAIL = {
  provider: '국가법령정보센터',
  service: '공정거래위원회 결정문 본문',
  target: 'ftc',
} as const;
