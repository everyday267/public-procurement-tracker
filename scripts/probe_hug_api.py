"""probe_hug_api.py — HUG(주택도시보증공사) OPEN API 조사.

개발 컨테이너는 khug.or.kr / data.go.kr 접근이 차단되므로, 다른 어댑터와 동일하게
GitHub Actions 러너(monthly.yml probe 모드) 또는 국내 IP에서 실행해 확정한다.

확정 대상 (사양서만으로는 알 수 없는 것들):
  1. khug.or.kr 접근 가능 여부 (해외 IP 차단 여부)
  2. `AREA_DCD` 유효 코드값 — 사양서에 코드표 없이 샘플 "02"만 제시됨
  3. `BUNO` 상세 조회의 **응답 필드** — 사양서에 명세 없음.
     ★ 여기에 주택유형(아파트/오피스텔/상가) 필드가 있는지가 핵심.
  4. HUG OPEN API 목록에 오피스텔·상가 전용 API가 따로 있는지

출력: probe_output/ (Actions 아티팩트로 회수)
"""
import json
import os
import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.hug_guarantee import BASE_URL, CANDIDATE_AREA_CODES, parse_response  # noqa: E402

OUT = Path("probe_output")
OUT.mkdir(exist_ok=True)

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
TIMEOUT = 20

# 코드 후보 외에 지역명·시도코드 표기도 함께 시험한다.
EXTRA_AREA_CANDIDATES = ["서울", "부산", "경기", "11", "26", "41"]

API_LIST_PAGES = [
    "https://www.khug.or.kr/openapi/web/se/ap/seap000002.jsp",
    "https://www.khug.or.kr/openapi/web/se/ap/seap000003.jsp",
]


def section(title):
    print(f"\n===== {title} =====", flush=True)


def get(url, params=None):
    try:
        r = requests.get(url, params=params, headers=UA, timeout=TIMEOUT)
        return r, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def probe_area_codes(api_key):
    """유효한 AREA_DCD 코드와 응답 필드 구성을 찾는다."""
    section("2. AREA_DCD 코드 탐색")
    results = {}
    hits = []
    for code in CANDIDATE_AREA_CODES + EXTRA_AREA_CANDIDATES:
        r, err = get(BASE_URL, {"API_KEY": api_key, "AREA_DCD": code})
        if err:
            print(f"  AREA_DCD={code:<4} → {err}")
            results[code] = {"error": err}
            continue
        entry = {"status": r.status_code, "len": len(r.text)}
        try:
            parsed = parse_response(r.text)
            entry.update(result_code=parsed.result_code, result_msg=parsed.result_msg,
                         count=len(parsed.items),
                         fields=sorted({k for it in parsed.items for k in it}))
            if parsed.items:
                entry["sample"] = parsed.items[0]
                hits.append((code, parsed.items))
            print(f"  AREA_DCD={code:<4} → {r.status_code} code={parsed.result_code} "
                  f"count={len(parsed.items)} fields={entry['fields']}")
        except Exception as e:
            entry["parse_error"] = f"{type(e).__name__}: {e}"
            entry["head"] = r.text[:400]
            print(f"  AREA_DCD={code:<4} → {r.status_code} 파싱실패 {e} | {r.text[:150]!r}")
        results[code] = entry
    (OUT / "hug_area_codes.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return hits


def probe_detail(api_key, hits):
    """BUNO 상세 응답 필드 — 유형(오피스텔/상가) 필드 존재 여부가 핵심."""
    section("3. BUNO 상세 응답 필드")
    if not hits:
        print("  목록 조회 성공 건이 없어 상세 조회를 건너뜁니다.")
        return
    code, items = hits[0]
    details = []
    for item in items[:3]:
        buno = item.get("BUNO", "")
        r, err = get(BASE_URL, {"API_KEY": api_key, "BUNO": buno})
        if err:
            print(f"  BUNO={buno} → {err}")
            continue
        try:
            parsed = parse_response(r.text)
            detail = parsed.items[0] if parsed.items else {}
            print(f"  BUNO={buno} → code={parsed.result_code} 필드={sorted(detail)}")
            print(f"          값: {json.dumps(detail, ensure_ascii=False)[:400]}")
            details.append({"buno": buno, "detail": detail, "raw": r.text[:2000]})
        except Exception as e:
            print(f"  BUNO={buno} → 파싱실패 {e} | {r.text[:200]!r}")
            details.append({"buno": buno, "parse_error": str(e), "raw": r.text[:2000]})
    (OUT / "hug_detail.json").write_text(
        json.dumps(details, ensure_ascii=False, indent=2), encoding="utf-8")

    list_fields = {k for _, its in hits for it in its for k in it}
    detail_fields = {k for d in details for k in d.get("detail", {})}
    extra = sorted(detail_fields - list_fields)
    print(f"\n  ▶ 상세 전용 필드: {extra or '없음'}")
    if not extra:
        print("  ▶ 상세에도 유형 필드가 없다면 오피스텔·상가 구분은 "
              "사업장명 추정 또는 다른 API가 필요하다.")


def probe_api_catalog():
    """HUG OPEN API 목록에서 오피스텔·상가 관련 API를 찾는다."""
    section("4. HUG OPEN API 목록 크롤")
    keywords = ["오피스텔", "상가", "분양보증", "사업장", "보증현황"]
    for url in API_LIST_PAGES:
        r, err = get(url)
        if err:
            print(f"  {url} → {err}")
            continue
        print(f"  {url} → {r.status_code} (len={len(r.text)})")
        name = url.rstrip("/").split("/")[-1].replace(".jsp", "")
        (OUT / f"hug_{name}.html").write_text(r.text[:500_000], encoding="utf-8")
        text = re.sub(r"<[^>]+>", " ", r.text)
        text = re.sub(r"\s+", " ", text)
        for kw in keywords:
            found = {m.group(0).strip()
                     for m in re.finditer(rf".{{0,40}}{kw}.{{0,40}}", text)}
            if found:
                print(f"    [{kw}] {len(found)}건")
                for line in sorted(found)[:10]:
                    print(f"      - {line}")


def main():
    section("1. 접근성 / 시크릿")
    api_key = os.getenv("HUG_API_KEY", "")
    print(f"  HUG_API_KEY: {'설정됨 (len=%d)' % len(api_key) if api_key else '미설정'}")
    for root in ["https://www.khug.or.kr/", "https://www.data.go.kr/"]:
        r, err = get(root)
        print(f"  {root} → {err or f'{r.status_code} (len={len(r.text)})'}")

    if api_key:
        hits = probe_area_codes(api_key)
        probe_detail(api_key, hits)
    else:
        print("\n  ⚠️ HUG_API_KEY 미설정 → API 호출 조사 생략 "
              "(Actions Secrets에 HUG_API_KEY 등록 후 재실행)")

    probe_api_catalog()
    print(f"\n완료. 산출물: {OUT.resolve()}")


if __name__ == "__main__":
    main()
