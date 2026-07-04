"""probe_kogas_khnp.py — Wave A 잔여 조사 (5차): KOGAS 9443 프레임 + KHNP 메뉴

KOGAS: bid.kogas.or.kr:9443 구형 JSP 사이트. index.jsp에서 시작해 같은 호스트의
  jsp/html 링크를 2~3단계 추적, '입찰공고' 목록 페이지·파라미터를 찾는다.
KHNP: ebiz.khnp.co.kr는 EX ebid와 같은 sc-component 계열 SPA.
  findListMenu.do로 전체 메뉴 트리를 받아 입찰/공고/계약정보공개 메뉴의
  menu_url(html)을 로드하고, 그 안의 *.do 서비스명을 수집해 NoSession 계열은
  직접 호출해본다.
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
session = requests.Session()
session.headers.update(UA)


def get(url, **kw):
    try:
        r = session.get(url, timeout=TIMEOUT, allow_redirects=True, verify=False, **kw)
        return r, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def post_json(url, body):
    try:
        r = session.post(url, json=body, timeout=TIMEOUT, verify=False)
        return r, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def save(name, text):
    (OUT / name).write_text(text[:300_000], encoding="utf-8", errors="replace")


# ── KOGAS ────────────────────────────────────────────────────────────────

def probe_kogas():
    print("===== KOGAS bid.kogas.or.kr:9443 =====")
    base = "https://bid.kogas.or.kr:9443"
    seeds = [f"{base}/supplier/index.jsp", f"{base}/buyer/index.jsp", f"{base}/"]
    seen = set()
    frontier = list(seeds)
    hit_pages = []
    for depth in range(3):
        nxt = []
        for url in frontier[:20]:
            if url in seen:
                continue
            seen.add(url)
            r, err = get(url)
            if err:
                print(f"  [{depth}] {url} → {err}")
                continue
            body = r.text
            has_bid = ("입찰" in body) or ("공고" in body)
            print(f"  [{depth}] {url} → {r.status_code} len={len(body)} 입찰/공고={has_bid}")
            fname = f"kogas_{re.sub(r'[^a-zA-Z0-9]', '_', urlparse(url).path)[:60]}.html"
            save(fname, body)
            if has_bid:
                hit_pages.append((url, body))
            # frame/iframe/href/JS 링크 수집 (같은 호스트 jsp/html/do)
            links = set()
            for m in re.findall(r'(?:src|href|action)\s*=\s*["\']([^"\'>\s]+)', body):
                links.add(m)
            for m in re.findall(r'["\']((?:/|\./|\.\./)[^"\'\s]{2,120}\.(?:jsp|do|html?))',
                                body):
                links.add(m)
            for l in links:
                full = urljoin(url, l)
                if urlparse(full).netloc == urlparse(base).netloc and full not in seen:
                    if re.search(r"\.(jsp|do|html?)(\?|$)", full):
                        nxt.append(full)
        frontier = nxt

    print(f"  --- '입찰/공고' 포함 페이지 {len(hit_pages)}개 분석 ---")
    for url, body in hit_pages[:8]:
        print(f"  ● {url}")
        # 목록 페이지 후보 링크 (bid/notice/list 계열)
        for m in sorted(set(re.findall(
                r'["\']([^"\'\s]{2,140}(?:[Ll]ist|[Bb]id|notice|Notice|gongo)[^"\'\s]{0,80}\.(?:jsp|do)[^"\'\s]{0,80})',
                body)))[:12]:
            print(f"     cand: {m}")
        for m in list(re.finditer(r".{60}입찰공고.{60}", body))[:4]:
            print("     ctx: …" + re.sub(r"\s+", " ", m.group(0)) + "…")


# ── KHNP ─────────────────────────────────────────────────────────────────

def probe_khnp():
    print("\n===== KHNP ebiz.khnp.co.kr =====")
    base = "https://ebiz.khnp.co.kr"
    get(base + "/")  # 세션 쿠키 확보

    menus = []
    for ptype in ["T", "P", "N", "S"]:
        r, err = post_json(base + "/findListMenu.do", {"portalType": ptype})
        if err or r.status_code != 200:
            print(f"  findListMenu({ptype}) → {err or r.status_code}")
            continue
        try:
            data = r.json()
        except ValueError:
            print(f"  findListMenu({ptype}) → 비JSON")
            continue
        print(f"  findListMenu({ptype}) → {len(data)}개")
        save(f"khnp_menu_{ptype}.json", json.dumps(data, ensure_ascii=False, indent=1))
        menus += data

    # 입찰/공고/계약 관련 메뉴 출력
    targets = []
    for m in menus:
        nm = str(m.get("menu_nm") or "")
        if re.search(r"입찰|공고|계약|조달", nm):
            targets.append(m)
    print(f"  --- 입찰/공고/계약 메뉴 {len(targets)}개 ---")
    for m in targets[:25]:
        print(f"    [{m.get('menu_id')}] {m.get('menu_nm')!r} url={m.get('menu_url')}")

    # menu_url html 로드 → 내부 .do 서비스명 수집
    do_candidates = set()
    for m in targets[:12]:
        mu = m.get("menu_url")
        if not mu or not str(mu).endswith((".html", ".htm")):
            continue
        r, err = get(urljoin(base + "/", mu.lstrip("/")))
        if err or r.status_code != 200:
            print(f"    {mu} → {err or r.status_code}")
            continue
        dos = sorted(set(re.findall(r'["\']([^"\'\s]{2,120}\.do)["\']', r.text)))
        print(f"    {mu} → 200, .do {len(dos)}개: {dos[:10]}")
        save(f"khnp_{re.sub(r'[^a-zA-Z0-9]', '_', mu)[:60]}.html", r.text)
        do_candidates.update(dos)

    # NoSession/find 계열 .do 직접 호출
    print("  --- .do 후보 호출 ---")
    tried = 0
    for d in sorted(do_candidates):
        if tried >= 10:
            break
        if not re.search(r"(NoSession|noSession|find.*List)", d):
            continue
        url = urljoin(base + "/", d.lstrip("/"))
        r, err = post_json(url, {})
        tried += 1
        if err:
            print(f"    {d} → {err}")
            continue
        body = r.text.strip()
        is_json = body[:1] in ("{", "[")
        print(f"    {d} → {r.status_code} len={len(body)} json={is_json}")
        if is_json:
            save(f"khnp_do_{re.sub(r'[^a-zA-Z0-9]', '_', d)[:60]}.json", body)
            print(f"      sample: {body[:400]}")


def main():
    import urllib3
    urllib3.disable_warnings()
    probe_kogas()
    probe_khnp()
    print("\n===== 완료 =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
