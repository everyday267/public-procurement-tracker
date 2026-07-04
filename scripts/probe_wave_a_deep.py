"""probe_wave_a_deep.py — Wave A 심층 조사 (2차): 입찰공고 목록 XHR 탐색

1차 조사(probe_phase2.py) 결과:
  - data.go.kr은 해외 IP 차단 → OpenAPI 확인은 사용자(국내 IP) 몫
  - ebid.ex.co.kr / ebid.kwater.or.kr / ebiz.khnp.co.kr 러너에서 접속 가능
  - KOGAS: www.kogas.or.kr/site/koGas/referenceBidList.do + bid.kogas.or.kr:9443 단서

2차 조사: 각 사이트의 메인/목록 페이지에서 입찰 관련 링크·XHR 후보를 추적하고,
JSON 응답이 나오는 엔드포인트는 키·샘플을 로그로 출력한다 (fixture 설계용).
"""
import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

OUT = Path("probe_output")
OUT.mkdir(exist_ok=True)

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
TIMEOUT = 20
KEYWORD = re.compile(r"입찰|공고|공사|bid|Bid|tender|notice|Notice")

session = requests.Session()
session.headers.update(UA)


def get(url, **kw):
    try:
        r = session.get(url, timeout=TIMEOUT, allow_redirects=True, **kw)
        return r, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def save(name, text):
    (OUT / name).write_text(text[:300_000], encoding="utf-8", errors="replace")


def extract_links(base_url, html):
    """href/src/JS 문자열에서 같은 호스트의 입찰 관련 URL 추출."""
    urls = set()
    for m in re.findall(r'(?:href|src|action)\s*=\s*["\']([^"\'>\s]+)', html):
        urls.add(m)
    # 인라인 JS의 경로 문자열 (.do/.jsp/.json)
    for m in re.findall(r'["\']((?:/|\./|https?://)[^"\'\s]{3,120}\.(?:do|jsp|json))', html):
        urls.add(m)
    host = urlparse(base_url).netloc
    out = []
    for u in urls:
        full = urljoin(base_url, u)
        if urlparse(full).netloc != host:
            continue
        if KEYWORD.search(full):
            out.append(full)
    return sorted(set(out))


def sniff_json(url, label, params=None, method="GET", data=None):
    """엔드포인트 호출 → JSON이면 구조 출력."""
    try:
        if method == "POST":
            r = session.post(url, timeout=TIMEOUT, data=data or {}, params=params)
        else:
            r = session.get(url, timeout=TIMEOUT, params=params)
    except Exception as e:
        print(f"    [{label}] {url} → {type(e).__name__}")
        return False
    ct = r.headers.get("Content-Type", "")
    body = r.text.strip()
    is_json = body[:1] in ("{", "[")
    print(f"    [{label}] {method} {url} → {r.status_code} {ct.split(';')[0]} "
          f"len={len(body)} json={is_json}")
    if is_json:
        fname = f"json_{re.sub(r'[^a-zA-Z0-9]', '_', url)[:80]}.json"
        save(fname, body)
        try:
            obj = json.loads(body)
            print(f"      keys: {list(obj.keys())[:15] if isinstance(obj, dict) else 'list len=' + str(len(obj))}")
            print(f"      sample: {body[:500]}")
        except ValueError:
            pass
        return True
    return False


def crawl(code, seeds, follow=6):
    print(f"\n===== {code} =====")
    seen, frontier, xhr_found = set(), list(seeds), []
    for depth in range(2):
        next_frontier = []
        for url in frontier[:follow]:
            if url in seen:
                continue
            seen.add(url)
            r, err = get(url)
            if err:
                print(f"  [page] {url} → {err}")
                continue
            print(f"  [page] {url} → {r.status_code} len={len(r.text)}")
            save(f"page_{code}_{re.sub(r'[^a-zA-Z0-9]', '_', url)[:70]}.html", r.text)
            links = extract_links(r.url, r.text)
            print(f"    입찰 관련 링크 {len(links)}개:")
            for l in links[:15]:
                print(f"      {l}")
            next_frontier += [l for l in links if l not in seen]
            # JSON 의심 엔드포인트 직접 시도
            for l in links:
                if re.search(r"(List|list|Ajax|ajax|search|Search)[^/]*\.(do|json)", l):
                    if sniff_json(l, code):
                        xhr_found.append(l)
        frontier = next_frontier
    return xhr_found


def main():
    results = {}

    # EX 한국도로공사 — ebid.ex.co.kr (1차에서 200 확인)
    results["EX"] = crawl("EX", ["https://ebid.ex.co.kr/"])

    # KWATER — WebSquare SPA. 메인 xml 경로와 통상적 목록 API 후보 시도
    results["KWATER"] = crawl("KWATER", [
        "https://ebid.kwater.or.kr/wq/index.do?w2xPath=/ui/index.xml",
        "https://ebid.kwater.or.kr/",
    ])

    # KHNP — ebiz.khnp.co.kr
    results["KHNP"] = crawl("KHNP", ["https://ebiz.khnp.co.kr/"])

    # KOGAS — 메인 사이트의 입찰공고(참조) 목록 + 9443 공급사 포털
    print("\n===== KOGAS =====")
    kogas_list = "https://www.kogas.or.kr/site/koGas/referenceBidList.do"
    r, err = get(kogas_list)
    if err:
        print(f"  [page] {kogas_list} → {err}")
    else:
        print(f"  [page] {kogas_list} → {r.status_code} len={len(r.text)}")
        save("page_KOGAS_referenceBidList.html", r.text)
        # 목록 행 구조 힌트 출력 (테이블 행/링크)
        rows = re.findall(r'<a[^>]+referenceBidView\.do[^>]*>[^<]{2,80}', r.text)
        print(f"    referenceBidView 링크 {len(rows)}개 (상위 5):")
        for row in rows[:5]:
            print(f"      {row[:150]}")
        pager = re.findall(r'referenceBidList\.do\?[^"\']{0,120}', r.text)[:5]
        print(f"    페이지네이션 파라미터 후보: {pager}")
    r9, err9 = get("https://bid.kogas.or.kr:9443/supplier/index.jsp")
    print(f"  [9443] → {err9 or r9.status_code}")
    if not err9:
        save("page_KOGAS_9443_index.html", r9.text)
        for l in extract_links("https://bid.kogas.or.kr:9443/supplier/index.jsp", r9.text)[:15]:
            print(f"      {l}")

    (OUT / "deep_report.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n===== 완료 =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
