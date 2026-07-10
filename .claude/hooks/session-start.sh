#!/bin/bash
# Claude Code on the web 세션 시작 훅: 테스트·수집 실행에 필요한 파이썬
# 의존성을 설치한다. 컨테이너 상태가 캐시되므로 install 계열을 사용한다.
set -euo pipefail

# 원격(웹) 세션에서만 실행. 로컬 개발 환경은 건드리지 않는다.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-.}"

# 런타임 의존성 + 테스트 러너(pytest는 requirements.txt에 없음).
# (pip 자체 업그레이드는 debian 관리형 pip에서 실패하므로 하지 않는다.)
python -m pip install -r requirements.txt pytest

# 소스 임포트를 위해 프로젝트 루트를 PYTHONPATH에 유지
echo 'export PYTHONPATH="${CLAUDE_PROJECT_DIR:-.}:${PYTHONPATH:-}"' >> "${CLAUDE_ENV_FILE:-/dev/null}"

echo "session-start: 의존성 설치 완료"
