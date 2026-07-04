"""probe_kogas_rows.py — KOGAS 목록 페이지 HTML 전체 덤프 (7차)

6차에서 중첩 테이블로 행 추출이 실패해, 목록 페이지의 본문 HTML을
script/style 제거 후 통째로 로그에 출력한다 (행 마크업·검색폼 옵션 확인용).
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
    r = s.get(f"{BASE}/supplier/contents/bid/bid_list_notice_frm.jsp",
              params={"page": 1}, timeout=25, verify=False)
    html = r.text
    print(f"status={r.status_code} len={len(html)}")
    body = re.sub(r"<script.*?</script>", "[SCRIPT]", html, flags=re.S | re.I)
    body = re.sub(r"<style.*?</style>", "[STYLE]", body, flags=re.S | re.I)
    body = re.sub(r"[ \t\r]+", " ", body)
    body = re.sub(r"\n\s*\n+", "\n", body)
    print("===== BODY DUMP BEGIN =====")
    print(body[:16000])
    print("===== BODY DUMP END =====")
    # 스크립트 내 상세 이동 함수도 확인 (goView 등)
    for m in re.findall(r"function\s+\w+\([^)]*\)\s*{[^}]{0,400}}", html)[:8]:
        print("FUNC:", re.sub(r"\s+", " ", m)[:400])
    return 0


if __name__ == "__main__":
    sys.exit(main())
