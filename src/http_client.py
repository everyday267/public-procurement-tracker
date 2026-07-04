"""http_client.py — 어댑터 공용 HTTP 요청 헬퍼.

- keep-alive(Session) 재사용으로 새 커넥션 churn을 줄인다 (LH e-Bid가 짧은 시간
  다수 신규 커넥션을 거부하는 패턴 대응).
- 네트워크 오류/타임아웃/5xx에 대해 지수 백오프 재시도.
- 4xx(403 등)는 재시도해도 소용없으므로 즉시 예외를 올려 원인을 빠르게 노출한다.
- 기본 User-Agent를 지정한다 (일부 WAF가 기본 python-requests UA를 차단).
"""
import logging
import time

import requests

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; procurement-tracker/1.0)",
    "Accept": "*/*",
}

RETRYABLE_STATUS = (500, 502, 503, 504)


def get_with_retry(url, params, timeout=30, session=None, headers=None,
                   max_retries=4, backoff_base=2.0, sleep_before=0.0, label=""):
    """GET 요청 + 재시도. 성공 시 requests.Response 반환.

    max_retries 회 모두 실패하면 마지막 예외를 다시 올린다.
    ConnectionError/Timeout/5xx만 재시도하며, 그 외 HTTP 오류(4xx)는 즉시 올린다.
    """
    return _request_with_retry("GET", url, params=params, timeout=timeout,
                               session=session, headers=headers,
                               max_retries=max_retries, backoff_base=backoff_base,
                               sleep_before=sleep_before, label=label)


def post_with_retry(url, json_body=None, data=None, timeout=30, session=None,
                    headers=None, max_retries=4, backoff_base=2.0,
                    sleep_before=0.0, label=""):
    """POST 요청 + 재시도 (XHR JSON API용). 재시도 정책은 GET과 동일."""
    return _request_with_retry("POST", url, json_body=json_body, data=data,
                               timeout=timeout, session=session, headers=headers,
                               max_retries=max_retries, backoff_base=backoff_base,
                               sleep_before=sleep_before, label=label)


def _request_with_retry(method, url, params=None, json_body=None, data=None,
                        timeout=30, session=None, headers=None,
                        max_retries=4, backoff_base=2.0, sleep_before=0.0, label=""):
    sess = session or requests
    hdrs = {**DEFAULT_HEADERS, **(headers or {})}
    last_exc = None

    for attempt in range(max_retries):
        if sleep_before:
            time.sleep(sleep_before)
        try:
            # sess.get/post로 디스패치 (requests 모듈·Session 양쪽 지원)
            if method == "GET":
                resp = sess.get(url, params=params, timeout=timeout, headers=hdrs)
            else:
                resp = sess.post(url, params=params, json=json_body, data=data,
                                 timeout=timeout, headers=hdrs)
            if resp.status_code in RETRYABLE_STATUS:
                raise requests.HTTPError(
                    "{} Server Error (retryable)".format(resp.status_code), response=resp
                )
            resp.raise_for_status()
            return resp
        except requests.HTTPError as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status not in RETRYABLE_STATUS:
                raise  # 4xx 등 → 재시도 무의미, 즉시 전달
            last_exc = e
        except (requests.ConnectionError, requests.Timeout) as e:
            last_exc = e

        if attempt < max_retries - 1:
            wait = backoff_base * (2 ** attempt)
            logger.warning("[%s] 요청 실패 (attempt %d/%d): %s — %.1fs 후 재시도",
                           label or url, attempt + 1, max_retries, last_exc, wait)
            time.sleep(wait)

    raise last_exc
