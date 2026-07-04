"""probe_phase2.py — Phase 2 Wave A 조사 스크립트 (실행계획 §3.1 조사 단계)

개발 컨테이너에서 한국 공공기관 도메인이 차단되므로(§1.3), GitHub Actions
러너에서 실행해 다음을 조사한다:

  1. data.go.kr 데이터셋 검색: 기관별 "입찰" 관련 OpenAPI 존재 여부 (OpenAPI 우선 원칙)
  2. 각 기관 전자조달 사이트 접근성: HTTP 상태, 리다이렉트, 응답 유형
  3. 응답 샘플을 probe_output/ 에 저장 → Actions 아티팩트로 회수해 fixture 후보로 사용

사용: python scripts/probe_phase2.py  (Actions probe.yml에서 호출)
"""
import json
import re
import sys
from pathlib import Path

import requests

OUT = Path("probe_output")
OUT.mkdir(exist_ok=True)

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
TIMEOUT = 20

# Wave A 조사 대상 (실행계획 §3.2)
AGENCIES = {
    "EX":     {"name": "한국도로공사",   "keywords": ["한국도로공사 입찰", "한국도로공사 전자조달"],
               "sites": ["https://ebid.ex.co.kr", "https://www.ex.co.kr"]},
    "KWATER": {"name": "한국수자원공사", "keywords": ["수자원공사 입찰", "K-water 입찰"],
               "sites": ["https://ebid.kwater.or.kr", "https://www.kwater.or.kr"]},
    "KOGAS":  {"name": "한국가스공사",   "keywords": ["가스공사 입찰", "한국가스공사 전자입찰"],
               "sites": ["https://bid.kogas.or.kr", "https://www.kogas.or.kr"]},
    "KHNP":   {"name": "한국수력원자력", "keywords": ["수력원자력 입찰", "한수원 입찰"],
               "sites": ["https://ebiz.khnp.co.kr", "https://www.khnp.co.kr"]},
}

DATA_GO_KR_SEARCH = "https://www.data.go.kr/tcs/dss/selectDataSetList.do"


def fetch(url, params=None, label=""):
    try:
        r = requests.get(url, params=params, headers=UA, timeout=TIMEOUT,
                         allow_redirects=True)
        return r, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def probe_datago(code, keyword):
    """data.go.kr 검색 결과 HTML에서 데이터셋 제목·링크 추출."""
    r, err = fetch(DATA_GO_KR_SEARCH,
                   {"dType": "API", "keyword": keyword, "operator": "AND"})
    if err or r.status_code != 200:
        print(f"  [data.go.kr] '{keyword}' → 실패: {err or r.status_code}")
        return []
    html = r.text
    (OUT / f"datago_{code}_{keyword.replace(' ', '_')}.html").write_text(
        html, encoding="utf-8")
    # 검색 결과 항목: /data/<id>/openapi.do 링크와 제목
    items = re.findall(
        r'href="(/data/(\d+)/openapi\.do[^"]*)"[^>]*>\s*<span[^>]*>([^<]+)</span>',
        html)
    if not items:  # 마크업 변형 대비 완화 패턴
        links = re.findall(r'/data/(\d+)/openapi\.do', html)
        titles = re.findall(r'class="title"[^>]*>\s*([^<]+?)\s*<', html)
        items = [(f"/data/{i}/openapi.do", i, t)
                 for i, t in zip(links, titles)]
    found = []
    for href, dsid, title in items[:10]:
        title = title.strip()
        found.append({"dataset_id": dsid, "title": title,
                      "url": f"https://www.data.go.kr{href.split('?')[0]}"})
    print(f"  [data.go.kr] '{keyword}' → {len(found)}건")
    for f in found:
        print(f"    - [{f['dataset_id']}] {f['title']}")
    return found


def probe_site(code, url):
    r, err = fetch(url)
    if err:
        print(f"  [site] {url} → 접속 실패: {err}")
        return {"url": url, "error": err}
    info = {
        "url": url, "status": r.status_code, "final_url": r.url,
        "content_type": r.headers.get("Content-Type", ""),
        "server": r.headers.get("Server", ""),
        "length": len(r.text),
    }
    fname = f"site_{code}_{re.sub(r'[^a-z0-9]', '_', url.split('//')[1])}.html"
    (OUT / fname).write_text(r.text[:200_000], encoding="utf-8", errors="replace")
    # 페이지 내 XHR 후보 단서: .do/.json/api 경로, 입찰 관련 링크
    hints = sorted(set(re.findall(
        r'["\'((]((?:/|https?://)[^"\'()\s]*(?:bid|Bid|tender|openapi|api)[^"\'()\s]*\.(?:do|json|jsp))',
        r.text)))[:20]
    info["endpoint_hints"] = hints
    print(f"  [site] {url} → {r.status_code} ({info['content_type']}), "
          f"최종 {r.url}, 단서 {len(hints)}개")
    for h in hints[:8]:
        print(f"    hint: {h}")
    return info


def main():
    report = {}
    for code, cfg in AGENCIES.items():
        print(f"\n===== {code} ({cfg['name']}) =====")
        datasets = []
        for kw in cfg["keywords"]:
            datasets += probe_datago(code, kw)
        sites = [probe_site(code, u) for u in cfg["sites"]]
        report[code] = {"datasets": datasets, "sites": sites}

    (OUT / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n===== 요약 (probe_output/report.json 저장) =====")
    for code, r in report.items():
        ok_sites = [s["url"] for s in r["sites"] if s.get("status") == 200]
        print(f"{code}: OpenAPI 후보 {len(r['datasets'])}건, "
              f"접속가능 사이트 {len(ok_sites)}/{len(r['sites'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
