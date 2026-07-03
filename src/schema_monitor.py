"""schema_monitor.py — monthly-collect 실행 후 어댑터 장애·스키마 변경 의심을 감지한다.

monthly-collect 워크플로우가 남긴 procurement.db의 source_runs / notices
테이블을 검사해:
  1. 가장 최근 실행에서 status != 'success'인 소스 (어댑터 장애)
  2. 최근 수집분 raw_payload에서 핵심 필드가 전부 사라진 소스 (스키마 변경 의심)
을 찾아 GitHub Issue로 등록한다. GITHUB_TOKEN/GITHUB_REPOSITORY가 없으면
콘솔 로그만 남기고 종료한다 (로컬 실행 지원).
"""
import argparse
import json
import logging
import os
import sqlite3
from typing import Dict, List, Optional

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("schema_monitor")

# 소스별로 실서비스 응답에 항상 있어야 하는 핵심 필드.
# raw_payload 샘플 전체에서 전부 사라지면 API 필드명이 바뀐 것으로 의심한다.
EXPECTED_KEYS: Dict[str, List[str]] = {
    "lh":         ["bidNum", "bidnmKor", "presmtPrc"],
    "g2b_opnstd": ["bidNtceNo", "bidNtceNm", "presmptPrce"],
    "kr_rail":    ["bidNtceNo", "bidNtceNm", "presmptPrce"],
}

ISSUE_LABEL = "schema-monitor"


def latest_runs(conn: sqlite3.Connection) -> List[dict]:
    cur = conn.execute("""
        SELECT source, run_id, status, error_message, fetched_count, filtered_count, started_at
        FROM source_runs sr
        WHERE started_at = (
            SELECT MAX(started_at) FROM source_runs WHERE source = sr.source
        )
    """)
    return [dict(row) for row in cur.fetchall()]


def sample_raw_payloads(conn: sqlite3.Connection, source: str, limit: int = 20) -> List[dict]:
    cur = conn.execute(
        "SELECT raw_payload FROM notices WHERE source=? ORDER BY collected_at DESC LIMIT ?",
        (source, limit),
    )
    out = []
    for (raw,) in cur.fetchall():
        try:
            out.append(json.loads(raw))
        except (TypeError, json.JSONDecodeError):
            continue
    return out


def check_schema_drift(conn: sqlite3.Connection, source: str) -> Optional[str]:
    expected = EXPECTED_KEYS.get(source)
    if not expected:
        return None
    samples = sample_raw_payloads(conn, source)
    if not samples:
        return None
    missing_everywhere = [k for k in expected if all(k not in s for s in samples)]
    if missing_everywhere:
        return "최근 수집 {}건 전체에서 필드 누락: {}".format(len(samples), ", ".join(missing_everywhere))
    return None


def find_problems(db_path: str) -> List[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    problems = []
    try:
        for run in latest_runs(conn):
            source = run["source"]
            if run["status"] != "success":
                problems.append({
                    "source": source,
                    "title": f"[schema-monitor] {source} 어댑터 실행 실패 (run_id={run['run_id']})",
                    "body": (
                        f"run_id={run['run_id']}\n"
                        f"status={run['status']}\n"
                        f"error={run['error_message']}\n"
                        f"started_at={run['started_at']}"
                    ),
                })
                continue
            drift = check_schema_drift(conn, source)
            if drift:
                problems.append({
                    "source": source,
                    "title": f"[schema-monitor] {source} 응답 스키마 변경 의심",
                    "body": drift,
                })
    finally:
        conn.close()
    return problems


def _open_issue_titles(token: str, repo: str) -> List[str]:
    resp = requests.get(
        f"https://api.github.com/repos/{repo}/issues",
        headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"},
        params={"state": "open", "labels": ISSUE_LABEL, "per_page": 100},
        timeout=15,
    )
    resp.raise_for_status()
    return [i["title"] for i in resp.json()]


def create_github_issue(title: str, body: str, token: str, repo: str) -> None:
    if title in _open_issue_titles(token, repo):
        logger.info("이미 열려있는 이슈 존재, 건너뜀: %s", title)
        return
    resp = requests.post(
        f"https://api.github.com/repos/{repo}/issues",
        headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"},
        json={"title": title, "body": body, "labels": [ISSUE_LABEL]},
        timeout=15,
    )
    resp.raise_for_status()
    logger.info("이슈 생성: %s", title)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="procurement.db")
    args = ap.parse_args()

    problems = find_problems(args.db)
    if not problems:
        logger.info("이상 없음")
        return 0

    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY")
    for p in problems:
        logger.warning("%s", p["title"])
        if token and repo:
            create_github_issue(p["title"], p["body"], token, repo)
        else:
            logger.info("GITHUB_TOKEN/GITHUB_REPOSITORY 없음 — 이슈 생성 생략 (로컬 실행)")

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
