"""probe_kogas_detail.py — KOGAS 공고 상세 POST 덤프 (8차)

목록의 viewBid()는 myform POST로 상세를 연다. 공사 공고 1건을 POST로 열어
예산/추정가격·공고일 등 필드 위치를 확인한다 (파서 설계 근거).
"""
import re
import sys

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
BASE = "https://bid.kogas.or.kr:9443"


def main():
    import urllib3
    urllib3.disable_warnings()
    s = requests.Session()
    s.headers.update(UA)
    s.get(f"{BASE}/supplier/index.jsp", timeout=25, verify=False)

    # 목록에서 공사(worktype=C) 최신 1건의 notice_code 확보
    r = s.get(f"{BASE}/supplier/contents/bid/bid_list_notice_frm.jsp",
              params={"page": 1, "worktype": "C"}, timeout=25, verify=False)
    print(f"목록(worktype=C) → {r.status_code} len={len(r.text)}")
    total = re.search(r"Total Records\s*:\s*(\d+)", r.text)
    print(f"  Total Records: {total.group(1) if total else '?'}")
    rows = re.findall(r"viewBid\('(\d+)','(\d+)','(\d+)','(\w)'\)", r.text)
    print(f"  행 {len(rows)//2}건(중복 포함 {len(rows)}) 예시: {rows[:6]}")
    if not rows:
        print("  공사 행 없음 — 전체 목록에서 공사 행 탐색")
        r = s.get(f"{BASE}/supplier/contents/bid/bid_list_notice_frm.jsp",
                  params={"page": 1}, timeout=25, verify=False)
        rows = re.findall(r"viewBid\('(\d+)','(\d+)','(\d+)','(\w)'\)", r.text)
    if not rows:
        print("행 확보 실패")
        return 1

    nc, bc, rd, tp = rows[0]
    print(f"\n상세 POST: notice_code={nc} bid_code={bc} round={rd} type={tp}")
    dr = s.post(f"{BASE}/supplier/contents/bid/bid_detail_view_notice.jsp",
                data={"notice_code": nc, "bid_code": bc, "round": rd,
                      "is_gongo": "true", "is_estimate": "false",
                      "is_mine": "false"},
                timeout=25, verify=False)
    print(f"→ {dr.status_code} len={len(dr.text)}")
    body = re.sub(r"<script.*?</script>", "[S]", dr.text, flags=re.S | re.I)
    body = re.sub(r"<style.*?</style>", "[C]", body, flags=re.S | re.I)
    body = re.sub(r"[ \t\r]+", " ", body)
    body = re.sub(r"\n\s*\n+", "\n", body)
    print("===== DETAIL DUMP BEGIN =====")
    print(body[:14000])
    print("===== DETAIL DUMP END =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
