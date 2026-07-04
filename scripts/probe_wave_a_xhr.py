"""probe_wave_a_xhr.py — Wave A 3차 조사: Playwright로 입찰공고 목록 XHR 캡처

2차 조사 결과 EX·KWATER·KHNP는 SPA(WebComponents/WebSquare)라 정적 크롤링으로
XHR이 드러나지 않는다. 헤드리스 브라우저로 사이트를 렌더링하고 "입찰/공고"
메뉴를 눌러가며 발생하는 XHR 요청·응답(JSON)을 캡처한다.

실행 전제: pip install playwright && python -m playwright install --with-deps chromium
(monthly.yml probe 잡에서 처리)
"""
import json
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path("probe_output")
OUT.mkdir(exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

SITES = [
    ("EX",     "https://ebid.ex.co.kr/default.do"),
    ("KWATER", "https://ebid.kwater.or.kr/"),
    ("KHNP",   "https://ebiz.khnp.co.kr/"),
    ("KOGAS",  "https://bid.kogas.or.kr:9443/supplier/index.jsp"),
]

STATIC_EXT = re.compile(r"\.(css|js|png|jpe?g|gif|svg|woff2?|ttf|ico|html?)(\?|$)", re.I)
MENU_TEXT = re.compile(r"입찰공고|입찰정보|입찰안내|공고목록|입찰참가|발주")


def probe_site(pw, code, url):
    print(f"\n===== {code}: {url} =====")
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context(ignore_https_errors=True, user_agent=UA, locale="ko-KR")
    page = ctx.new_page()
    captured = []

    def on_response(resp):
        try:
            req = resp.request
            if req.resource_type not in ("xhr", "fetch", "document"):
                return
            if STATIC_EXT.search(resp.url):
                return
            entry = {
                "method": req.method, "url": resp.url, "status": resp.status,
                "type": req.resource_type,
                "post": (req.post_data or "")[:300],
                "content_type": resp.headers.get("content-type", ""),
            }
            body = ""
            try:
                body = resp.text()
            except Exception:
                pass
            if body.strip()[:1] in ("{", "["):
                entry["json_head"] = body[:700]
                fname = f"xhr_{code}_{len(captured):02d}_{re.sub(r'[^a-zA-Z0-9]', '_', resp.url)[:60]}.json"
                (OUT / fname).write_text(body[:300_000], encoding="utf-8", errors="replace")
            captured.append(entry)
        except Exception:
            pass

    page.on("response", on_response)
    ctx.on("page", lambda p: p.on("response", on_response))  # 팝업도 캡처

    try:
        page.goto(url, wait_until="networkidle", timeout=45_000)
    except Exception as e:
        print(f"  goto 실패/타임아웃: {type(e).__name__} — 캡처된 것까지 분석 계속")

    # 프레임 구조 출력 (구형 JSP 프레임셋 대응)
    for f in page.frames:
        print(f"  frame: name={f.name!r} url={f.url}")

    # "입찰/공고" 계열 메뉴 클릭 시도 (최대 4개)
    clicked = 0
    try:
        for frame in page.frames:
            if clicked >= 4:
                break
            for el in frame.get_by_text(MENU_TEXT).all()[:6]:
                if clicked >= 4:
                    break
                try:
                    label = (el.inner_text(timeout=1_000) or "")[:30].strip()
                    el.click(timeout=3_000, no_wait_after=True)
                    page.wait_for_timeout(4_000)
                    print(f"  click[{clicked}] {label!r} (frame={frame.name!r})")
                    clicked += 1
                except Exception:
                    continue
    except Exception as e:
        print(f"  메뉴 클릭 단계 오류: {type(e).__name__}")

    # 캡처 요약
    print(f"  --- 캡처된 요청 {len(captured)}건 ---")
    for c in captured:
        print(f"  [{c['type']}] {c['method']} {c['status']} {c['url'][:150]}")
        if c.get("post"):
            print(f"      post: {c['post'][:200]}")
        if c.get("json_head"):
            print(f"      json: {c['json_head'][:400]}")

    (OUT / f"xhr_summary_{code}.json").write_text(
        json.dumps(captured, ensure_ascii=False, indent=2), encoding="utf-8")
    browser.close()


def main():
    with sync_playwright() as pw:
        for code, url in SITES:
            try:
                probe_site(pw, code, url)
            except Exception as e:
                print(f"===== {code} 실패: {type(e).__name__}: {e}")
    print("\n===== 완료 =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
