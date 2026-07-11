#!/bin/bash
set -euo pipefail

# Claude Code on the web 원격 세션에서만 실행 (로컬 세션은 건너뜀)
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

# 런타임 의존성 + 테스트 러너 설치 (멱등 — 이미 설치돼 있으면 no-op)
pip install -r requirements.txt
pip install pytest
