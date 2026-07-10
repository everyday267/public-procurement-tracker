"""probe_kiscon.py — KISCON 건설공사대장 통보 통계서비스(ConStatInfoSvc) 조사.

목표 (검증 층 2 구축 선행 작업):
  1. StatAmt(금액 리스트, 스펙 확보됨) 실호출 → 응답 envelope·필드 확정,
     원문을 probe_output/에 저장 (tests/fixtures/kiscon/ 픽스처 후보)
  2. StatCnt(건수 리스트) 실호출 → 응답 필드명(cnt?) 확정
  3. 건별 리스트 오퍼레이션 탐색 — 서비스 소개에 공사명·업체명·계약금액·착공일·
     준공예정일 필드가 명시되어 있어 존재 추정. 후보명 호출로 엔드포인트 확정
     → 확정되면 KISCON_RECORDS_OP로 수집 활성화 (src/kiscon.py)
  4. 날짜범위 상한 확인 (2020-07-15~오늘 일괄 조회 가능 여부 → 청킹 전략)
  5. 볼륨 확인 (한 달 totalCount → 트래픽 한도 일 10,000 내 백필 계획)

실행: monthly.yml workflow_dispatch에서 probe_script=probe_kiscon.py
      (KISCON_API_KEY 시크릿 필요)
"""
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import unquote

import requests

OUT = Path("probe_output")
OUT.mkdir(exist_ok=True)

BASES = [
    "http://apis.data.go.kr/1613000/ConStatInfoSvc",
    "https://apis.data.go.kr/1613000/ConStatInfoSvc",
]
TIMEOUT = 30

# 건별 리스트 오퍼레이션 후보명
RECORD_OP_CANDIDATES = [
    "StatList", "ConList", "ConstList", "NotiList", "List",
    "StatDetail", "ConStatList", "TotalList", "AmtList",
]


def section(title):
    print(f"\n===== {title} =====")


def call(base, op, params, key, key_param="ServiceKey"):
    url = f"{base}/{op}"
    query = {key_param: key, "pageNo": 1, "numOfRows": 5, "_type": "json", **params}
    try:
        r = requests.get(url, params=query, timeout=TIMEOUT)
        return r, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def summarize(r):
    body = r.text.strip()
    kind = "json" if body[:1] in ("{", "[") else ("xml" if body[:1] == "<" else "?")
    return f"{r.status_code} len={len(body)} {kind}", body


def main():
    section("시크릿 확인")
    raw_key = os.getenv("KISCON_API_KEY", "")
    print(f"  KISCON_API_KEY: {'설정됨 (len=%d)' % len(raw_key) if raw_key else '미설정'}")
    if not raw_key:
        print("  → 시크릿 등록 후 재실행 필요. 게이트웨이 생존만 확인한다.")
    key = unquote(raw_key) if raw_key else "test"
    print(f"  unquote 후 길이 변화: {len(raw_key)} → {len(key)}")

    week_ago = date.today() - timedelta(days=14)
    s_date = week_ago.strftime("%Y%m%d")
    e_date = (week_ago + timedelta(days=6)).strftime("%Y%m%d")
    base_params = {"sDate": s_date, "eDate": e_date, "balju": "0", "dogub": "1"}

    # 1·2. StatAmt / StatCnt 스모크 (프로토콜·키 파라미터 케이싱 교차 시도)
    working_base = None
    for op in ("StatAmt", "StatCnt"):
        section(f"{op} 스모크 ({s_date}~{e_date}, 공공×원도급)")
        for base in BASES:
            for key_param in ("ServiceKey", "serviceKey"):
                r, err = call(base, op, base_params, key, key_param)
                if err:
                    print(f"  {base} [{key_param}] → {err}")
                    continue
                head, body = summarize(r)
                print(f"  {base} [{key_param}] → {head}")
                print(f"    head: {body[:400]}")
                if r.status_code == 200 and body[:1] in ("{", "<"):
                    fname = f"kiscon_{op.lower()}_{s_date}.{'json' if body[:1]=='{' else 'xml'}"
                    (OUT / fname).write_text(body[:500_000], encoding="utf-8", errors="replace")
                    print(f"    저장: probe_output/{fname}")
                    working_base = working_base or (base, key_param)
                    break
            if working_base and working_base[0] == base:
                break

    if not raw_key:
        print("\n키 미설정 — 이후 단계 생략")
        return 0
    if not working_base:
        print("\nStatAmt 호출 실패 — 파라미터/키 확인 필요")
        return 1
    base, key_param = working_base

    # 3. 건별 리스트 오퍼레이션 탐색
    section("건별 리스트 오퍼레이션 후보 탐색")
    for op in RECORD_OP_CANDIDATES:
        r, err = call(base, op, base_params, key, key_param)
        if err:
            print(f"  {op} → {err}")
            continue
        head, body = summarize(r)
        marker = ""
        if r.status_code == 200 and any(k in body for k in
                                        ("공사", "constNm", "workNm", "cmpNm", "contAmt")):
            marker = "  ★ 건별 필드 감지"
            fname = f"kiscon_records_{op.lower()}.json"
            (OUT / fname).write_text(body[:500_000], encoding="utf-8", errors="replace")
        print(f"  {op} → {head}{marker}")

    # 4. 날짜범위 상한: 전체 기간 일괄 조회 시도
    section("날짜범위 상한 (20200715 ~ 오늘)")
    r, err = call(base, "StatAmt",
                  {**base_params, "sDate": "20200715",
                   "eDate": date.today().strftime("%Y%m%d")}, key, key_param)
    if err:
        print(f"  → {err}")
    else:
        head, body = summarize(r)
        print(f"  → {head}")
        print(f"    head: {body[:400]}")

    # 5. 볼륨: 최근 완결 1개월 totalCount (셀 수 = 백필 비용 추정)
    section("볼륨 (직전월 totalCount)")
    first_of_month = date.today().replace(day=1)
    prev_end = first_of_month - timedelta(days=1)
    prev_start = prev_end.replace(day=1)
    for balju, dogub, desc in [("0", "1", "공공×원도급"), (None, None, "전체")]:
        params = {"sDate": prev_start.strftime("%Y%m%d"), "eDate": prev_end.strftime("%Y%m%d")}
        if balju:
            params.update({"balju": balju, "dogub": dogub})
        r, err = call(base, "StatAmt", params, key, key_param)
        if err:
            print(f"  {desc} → {err}")
            continue
        try:
            total = r.json().get("response", {}).get("body", {}).get("totalCount")
        except (ValueError, AttributeError):
            total = "?"
        print(f"  {desc}: totalCount={total}")

    print("\n===== 완료 =====")
    print(json.dumps({"working_base": base, "key_param": key_param}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
