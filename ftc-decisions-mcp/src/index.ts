#!/usr/bin/env node
/**
 * FTC Decisions MCP Server 엔트리포인트 (stdio 전송).
 */

import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';

import { loadConfig } from './config.js';
import { FtcError, toErrorPayload } from './errors.js';
import { logger, setLogLevel } from './logger.js';
import { createContext, createServer } from './server.js';

async function main(): Promise<void> {
  let config;
  try {
    config = loadConfig();
  } catch (err) {
    // 설정 오류는 즉시 종료 (stderr 로만 출력)
    const payload = toErrorPayload(err);
    logger.error('config load failed', {
      code: payload.error.code,
      message: payload.error.message,
    });
    process.exitCode = 1;
    return;
  }

  setLogLevel(config.logLevel);

  const ctx = createContext(config);
  const server = createServer(ctx);
  const transport = new StdioServerTransport();

  await server.connect(transport);
  logger.info('FTC Decisions MCP Server started', {
    transport: 'stdio',
    cache_enabled: config.cacheEnabled,
  });
}

main().catch((err) => {
  if (err instanceof FtcError) {
    logger.error('fatal', { code: err.code, message: err.message });
  } else {
    logger.error('fatal', { message: err instanceof Error ? err.message : String(err) });
  }
  process.exitCode = 1;
});
