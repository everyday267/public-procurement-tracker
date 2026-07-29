"""G2B 개방표준 낙찰·계약 API의 서버측 필터 동작 조사.

run_monthly의 G2B 낙찰/계약 스코프 조회가 공고(수백) × 주간창(52) = 수만 회
호출로 폭주(2021 수집 5.6시간)한 원인을 규명한다. totalCount=47946이 반복
관측됨 → 개찰일시 창(opengBgnDt/opengEndDt)·계약일 창·bidNtceNo 필터가 서버에서
무시되는 것으로 의심. 어떤 파라미터가 실제로 totalCount를 줄이는지 확인해
효율적 수집 방식(주간 스윕 vs 전량 1회 후 클라이언트 필터)을 결정한다.

인증키: G2B_API_KEY. 실행: monthly.yml probe 모드.
"""
import json
import os
import sys
import urllib.parse
import urllib.request

BASE = "https://apis.data.go.kr/1230000/ao/PubDataOpnStdService"
AWARD = "getDataSetOpnStdScsbidInfo"
CONTRACT = "getDataSetOpnStdCntrctInfo"

KEY = urllib.parse.unquote(os.environ.get("G2B_API_KEY", ""))
if not KEY:
    print("G2B_API_KEY 미설정 — 종료")
    sys.exit(1)


def call(op, params, num=1):
    q = {"serviceKey": KEY, "type": "json", "numOfRows": num, "pageNo": 1}
    q.update(params)
    url = "{}/{}?{}".format(BASE, op, urllib.parse.urlencode(q))
    with urllib.request.urlopen(url, timeout=60) as r:
        data = json.loads(r.read().decode("utf-8"))
    body = data.get("response", {}).get("body", {})
    total = body.get("totalCount")
    items = body.get("items", [])
    if isinstance(items, dict):
        items = items.get("item", [])
    if isinstance(items, dict):
        items = [items]
    return total, (items or [])


def total(op, params):
    try:
        t, _ = call(op, params, num=1)
        return t
    except Exception as e:
        return "ERR:{}".format(e)


print("=" * 70)
print("[낙찰 getDataSetOpnStdScsbidInfo] 파라미터별 totalCount")
print("  (a) 파라미터 없음        :", total(AWARD, {}))
print("  (b) bsnsDivCd=3(공사)     :", total(AWARD, {"bsnsDivCd": "3"}))
print("  (c) 개찰일시 1주(6/1~6/7) :", total(AWARD, {
    "opengBgnDt": "202106010000", "opengEndDt": "202106072359"}))
print("  (d) 개찰일시 1개월(6월)   :", total(AWARD, {
    "opengBgnDt": "202106010000", "opengEndDt": "202106302359"}))
print("  (e) 공사+개찰 1주         :", total(AWARD, {
    "bsnsDivCd": "3", "opengBgnDt": "202106010000", "opengEndDt": "202106072359"}))
print("  (f) bidNtceNo=20211031258 :", total(AWARD, {"bidNtceNo": "20211031258"}))
print("  (g) 공사+bidNtceNo         :", total(AWARD, {
    "bsnsDivCd": "3", "bidNtceNo": "20211031258"}))

print("\n[낙찰 샘플 행 필드]")
try:
    _, rows = call(AWARD, {"opengBgnDt": "202106010000", "opengEndDt": "202106072359"}, num=2)
    if rows:
        print("  필드:", sorted(rows[0].keys()))
        for k in ("bidNtceNo", "bidNtceNm", "opengDt", "rlOpengDt", "sucsfbidDt",
                  "cntrctCnclsDate", "scsbidAmt", "bsnsDivNm"):
            if k in rows[0]:
                print("    {} = {}".format(k, rows[0][k]))
except Exception as e:
    print("  샘플 조회 실패:", e)

print("\n" + "=" * 70)
print("[계약 getDataSetOpnStdCntrctInfo] 파라미터별 totalCount")
print("  (a) 파라미터 없음         :", total(CONTRACT, {}))
print("  (b) 계약일 1주(6/1~6/7)   :", total(CONTRACT, {
    "cntrctCnclsBgnDate": "20210601", "cntrctCnclsEndDate": "20210607"}))
print("  (c) 계약일 1개월(6월)     :", total(CONTRACT, {
    "cntrctCnclsBgnDate": "20210601", "cntrctCnclsEndDate": "20210630"}))
print("  (d) bidNtceNo=20211031258 :", total(CONTRACT, {"bidNtceNo": "20211031258"}))

print("\n[계약 샘플 행 필드]")
try:
    _, rows = call(CONTRACT, {
        "cntrctCnclsBgnDate": "20210601", "cntrctCnclsEndDate": "20210607"}, num=2)
    if rows:
        print("  필드:", sorted(rows[0].keys()))
        for k in ("bidNtceNo", "cntrctNm", "cntrctCnclsDate", "cntrctPrce",
                  "bsnsDivNm", "cntrctInsttNm"):
            if k in rows[0]:
                print("    {} = {}".format(k, rows[0][k]))
except Exception as e:
    print("  샘플 조회 실패:", e)
