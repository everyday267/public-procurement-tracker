"""probe_ex_api.py — EX(한국도로공사) OpenAPI 조사 (Wave A 4차)

목표: 사용자가 활용신청한 EX 입찰 OpenAPI의 요청주소·파라미터·응답 구조 확정.
data.go.kr 웹은 해외 IP 차단이지만 API 게이트웨이(apis.data.go.kr)는 러너에서
접속 가능(G2B 수집으로 검증). EX 자체 공공데이터포털(data.ex.co.kr)도 조사한다.

1. 시크릿 존재 확인: EX_API_KEY / DATA_EX_API_KEY (monthly.yml probe env로 주입)
2. data.ex.co.kr 카탈로그 크롤: '입찰' 관련 API 상세 페이지에서 요청주소 추출
3. 키가 있으면 발견된 엔드포인트 호출 → 응답 구조·필드 출력 (fixture 후보 저장)
"""
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

import requests

OUT = Path("probe_output")
OUT.mkdir(exist_ok=True)

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
TIMEOUT = 20


def get(url, params=None):
    try:
        r = requests.get(url, params=params, headers=UA, timeout=TIMEOUT,
                         allow_redirects=True, verify=True)
        return r, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def section(title):
    print(f"\n===== {title} =====")


def main():
    # 1. 시크릿 존재 확인
    section("시크릿 확인")
    keys = {}
    for name in ["EX_API_KEY", "DATA_EX_API_KEY"]:
        v = os.getenv(name, "")
        keys[name] = v
        print(f"  {name}: {'설정됨 (len=%d)' % len(v) if v else '미설정'}")
    api_key = keys["EX_API_KEY"] or keys["DATA_EX_API_KEY"]

    # 2. data.ex.co.kr 카탈로그 크롤
    section("data.ex.co.kr 접근성/카탈로그")
    catalog_hits = []
    for root in ["https://data.ex.co.kr", "http://data.ex.co.kr"]:
        r, err = get(root)
        if err:
            print(f"  {root} → {err}")
            continue
        print(f"  {root} → {r.status_code} (최종 {r.url}, len={len(r.text)})")
        (OUT / "ex_data_root.html").write_text(r.text[:300_000], encoding="utf-8",
                                               errors="replace")
        # 입찰 관련 링크 수집
        links = set()
        for href, text in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.{0,80}?)</a>',
                                     r.text, re.S):
            if re.search(r"입찰|계약|조달", text) or re.search(r"입찰|bid", href, re.I):
                links.add((urljoin(r.url, href), re.sub(r"\s+", " ", text).strip()[:60]))
        print(f"  입찰/계약 관련 링크 {len(links)}개:")
        for u, t in sorted(links)[:20]:
            print(f"    {t!r} → {u}")
        # 검색 페이지 시도 (포털 통합검색: 입찰)
        for search_url, params in [
            (urljoin(r.url, "/portal/services/svcSearch"), {"searchWord": "입찰"}),
            (urljoin(r.url, "/openapi/apidata/searchList"), {"searchWord": "입찰"}),
        ]:
            sr, serr = get(search_url, params)
            if not serr and sr.status_code == 200 and len(sr.text) > 500:
                print(f"  [검색] {search_url} → 200 len={len(sr.text)}")
                (OUT / "ex_search.html").write_text(sr.text[:300_000],
                                                    encoding="utf-8", errors="replace")
                for href, text in re.findall(
                        r'<a[^>]+href="([^"]+)"[^>]*>(.{0,100}?)</a>', sr.text, re.S):
                    text2 = re.sub(r"\s+", " ", text).strip()
                    if "입찰" in text2:
                        catalog_hits.append((urljoin(sr.url, href), text2[:80]))
        break

    if catalog_hits:
        print(f"  검색 결과 '입찰' 항목 {len(catalog_hits)}개:")
        for u, t in catalog_hits[:15]:
            print(f"    {t!r} → {u}")
        # 상세 페이지에서 요청주소(openapi URL) 추출
        for u, t in catalog_hits[:5]:
            dr, derr = get(u)
            if derr:
                continue
            urls = sorted(set(re.findall(
                r'(https?://data\.ex\.co\.kr/openapi/[^\s"\'<>]+)', dr.text)))
            if urls:
                print(f"  [상세] {t!r}: 요청주소 후보 {urls[:5]}")

    # 3. 알려진 후보 엔드포인트 직접 시도 (키 필요)
    section("후보 엔드포인트 호출")
    candidates = [
        # data.ex.co.kr 계열 (type/key 파라미터 규격)
        ("https://data.ex.co.kr/openapi/business/bidList", {"type": "json"}),
        ("https://data.ex.co.kr/openapi/bidinfo/bidNoticeList", {"type": "json"}),
        ("https://data.ex.co.kr/openapi/business/bidNoticeList", {"type": "json"}),
    ]
    if not api_key:
        print("  API 키 미설정 → 호출 생략 (시크릿 EX_API_KEY 등록 필요)")
    for url, base_params in candidates:
        if not api_key:
            break
        params = {**base_params, "key": api_key, "numOfRows": 3, "pageNo": 1}
        r, err = get(url, params)
        if err:
            print(f"  {url} → {err}")
            continue
        body = r.text.strip()
        print(f"  {url} → {r.status_code} len={len(body)} "
              f"json={body[:1] in ('{', '[')}")
        if body[:1] in ("{", "["):
            fname = f"ex_api_{re.sub(r'[^a-zA-Z0-9]', '_', url)[:60]}.json"
            (OUT / fname).write_text(body[:300_000], encoding="utf-8", errors="replace")
            print(f"    sample: {body[:600]}")

    # 4. apis.data.go.kr 게이트웨이 생존 확인 (참고)
    section("apis.data.go.kr 게이트웨이")
    r, err = get("https://apis.data.go.kr/1230000/ao/PubDataOpnStdService/getDataSetOpnStdBidPblancInfo",
                 {"serviceKey": "test", "numOfRows": 1})
    print(f"  게이트웨이 응답: {err or r.status_code} (도달 가능 여부 확인용)")

    print("\n===== 완료 =====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
