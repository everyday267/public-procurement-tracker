"""probe_kogas_list.py — KOGAS 입찰공고 목록/상세 HTML 구조 추출 (6차)

5차에서 확인한 비로그인 목록 bid_list_notice_frm.jsp의 테이블 행 구조와
상세 페이지의 금액/일자 영역을 로그로 출력해 파서 설계 근거를 확보한다.
"""
import re
import sys
from pathlib import Path

import requests

OUT = Path("probe_output")
OUT.mkdir(exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
BASE = "https://bid.kogas.or.kr:9443"
session = requests.Session()
session.headers.update(UA)


def get(url, params=None):
    try:
        r = session.get(url, params=params, timeout=25, verify=False)
        return r, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def dump_rows(label, html):
    print(f"\n--- {label}: 테이블 행 구조 ---")
    # 공고 상세 링크가 든 tr 블록 추출
    trs = re.findall(r"<tr[^>]*>.*?</tr>", html, re.S | re.I)
    print(f"  <tr> 총 {len(trs)}개")
    shown = 0
    for tr in trs:
        if "notice_code" in tr or "bid_detail" in tr:
            clean = re.sub(r"\s+", " ", tr)
            print(f"  ROW[{shown}]: {clean[:900]}")
            shown += 1
            if shown >= 4:
                break
    if shown == 0:
        # 헤더 행이라도 출력
        for tr in trs[:3]:
            print("  HDR:", re.sub(r"\s+", " ", tr)[:400])


def main():
    import urllib3
    urllib3.disable_warnings()

    # 세션 쿠키 확보
    get(f"{BASE}/supplier/index.jsp")

    for page in (1, 2):
        url = f"{BASE}/supplier/contents/bid/bid_list_notice_frm.jsp"
        r, err = get(url, {"page": page})
        if err:
            print(f"page={page} → {err}")
            continue
        print(f"page={page} → {r.status_code} len={len(r.text)}")
        (OUT / f"kogas_list_p{page}.html").write_text(r.text[:400_000],
                                                      encoding="utf-8", errors="replace")
        dump_rows(f"목록 page={page}", r.text)
        # 페이지네이션/총건수 단서
        for m in list(re.finditer(r".{40}(총|건수|page|Page).{60}", r.text))[:5]:
            print("  pgctx: …" + re.sub(r"\s+", " ", m.group(0)) + "…")
        # 상세 링크 하나 뽑기
        if page == 1:
            dm = re.search(
                r'bid_detail_view_notice\.jsp\?notice_code=(\d+)&(?:amp;)?bid_code=(\w+)&(?:amp;)?round=(\w+)',
                r.text)
            if dm:
                nc, bc, rd = dm.groups()
                durl = (f"{BASE}/supplier/contents/bid/bid_detail_view_notice.jsp"
                        f"?notice_code={nc}&bid_code={bc}&round={rd}")
                dr, derr = get(durl)
                print(f"\n상세 {nc} → {derr or dr.status_code} len={len(dr.text) if dr else 0}")
                if not derr:
                    (OUT / "kogas_detail.html").write_text(
                        dr.text[:400_000], encoding="utf-8", errors="replace")
                    # 금액/일자/구분 관련 컨텍스트
                    for kw in ["예산", "추정", "금액", "기초", "구분", "마감", "개찰", "공고일"]:
                        for m in list(re.finditer(r".{50}" + kw + r".{110}", dr.text))[:2]:
                            ctx = re.sub(r"\s+", " ", m.group(0))
                            print(f"  [{kw}] …{ctx}…")
    print("\n===== 완료 =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
