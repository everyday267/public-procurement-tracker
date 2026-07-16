/**
 * search_ftc_decisions 도구의 비즈니스 로직.
 */

import { logger } from '../logger.js';
import { SEARCH_TTL_MS, stableKey } from '../cache.js';
import { parseSearchResponse } from '../parsers/searchParser.js';
import {
  SEARCH_SCOPE_TO_API,
  SORT_TO_API,
  searchInputSchema,
  type SearchInput,
} from '../schemas/inputs.js';
import type { SearchOutput, SearchResultItem, Warning } from '../types/index.js';
import { SOURCE_SEARCH, type ToolContext } from './context.js';

export async function runSearch(ctx: ToolContext, rawInput: unknown): Promise<SearchOutput> {
  const input: SearchInput = searchInputSchema.parse(rawInput);
  const warnings: Warning[] = [];

  const apiSearch = SEARCH_SCOPE_TO_API[input.search_scope];
  const apiSort = SORT_TO_API[input.sort];

  const cacheKey = stableKey('search', {
    query: input.query,
    search: apiSearch,
    display: input.page_size,
    page: input.page,
    sort: apiSort,
    gana: input.gana ?? undefined,
  });

  let parsed = ctx.searchCache.get(cacheKey) as ReturnType<typeof parseSearchResponse> | undefined;
  const cacheHit = parsed !== undefined;

  if (!parsed) {
    const raw = await ctx.client.search({
      query: input.query,
      search: apiSearch,
      display: input.page_size,
      page: input.page,
      sort: apiSort,
      gana: input.gana ?? undefined,
    });
    parsed = parseSearchResponse(raw);
    ctx.searchCache.set(cacheKey, parsed, SEARCH_TTL_MS);
  }

  warnings.push(...parsed.warnings);

  let results: SearchResultItem[] = parsed.results;
  const apiReturnedCount = results.length;

  // document_type 은 API 공식 조건이 아니므로 현재 페이지 결과에만 후처리 필터 적용
  if (input.document_type) {
    const filterVal = input.document_type;
    results = results.filter((r) => r.document_type != null && r.document_type.includes(filterVal));
    // 필터 후 인덱스 재부여
    results = results.map((r, i) => ({ ...r, result_index: i + 1 }));
    warnings.push({
      code: 'POST_FILTER_APPLIED',
      message:
        'document_type 은 API가 지원하지 않아 현재 페이지 결과에만 후처리 필터를 적용했습니다. 전체 건수와 반환 건수가 다를 수 있습니다.',
      details: {
        document_type: filterVal,
        api_returned_count: apiReturnedCount,
        filtered_count: results.length,
      },
    });
  }

  const totalCount = parsed.totalCount;
  const returnedCount = results.length;
  const hasNextPage =
    totalCount != null ? input.page * input.page_size < totalCount : apiReturnedCount >= input.page_size;

  if (returnedCount === 0) {
    warnings.push({
      code: 'NO_SEARCH_RESULTS',
      message: '검색 결과가 없습니다. (기술적 오류가 아닙니다.)',
    });
  }

  logger.info('search done', {
    tool: 'search_ftc_decisions',
    scope: input.search_scope,
    page: input.page,
    returned: returnedCount,
    total: totalCount,
    cache_hit: cacheHit,
  });

  return {
    query: {
      text: input.query,
      scope: input.search_scope,
      page: input.page,
      page_size: input.page_size,
      sort: input.sort,
      document_type: input.document_type ?? null,
    },
    pagination: {
      total_count: totalCount,
      current_page: parsed.currentPage ?? input.page,
      page_size: input.page_size,
      returned_count: returnedCount,
      has_next_page: hasNextPage,
    },
    results,
    warnings,
    source: { ...SOURCE_SEARCH },
  };
}
