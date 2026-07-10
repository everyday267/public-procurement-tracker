from unittest.mock import MagicMock, patch

import pytest
import requests

from src.http_client import get_with_retry


def _resp(status):
    r = MagicMock()
    r.status_code = status
    if status >= 400:
        err = requests.HTTPError(response=r)
        r.raise_for_status.side_effect = err
    else:
        r.raise_for_status.return_value = None
    return r


def test_retries_then_succeeds():
    sess = MagicMock()
    sess.get.side_effect = [requests.ConnectionError("refused"), _resp(200)]
    with patch("src.http_client.time.sleep"):
        resp = get_with_retry("http://x", {}, session=sess, max_retries=4)
    assert resp.status_code == 200
    assert sess.get.call_count == 2


def test_gives_up_after_max_retries():
    sess = MagicMock()
    sess.get.side_effect = requests.ConnectionError("refused")
    with patch("src.http_client.time.sleep"):
        with pytest.raises(requests.ConnectionError):
            get_with_retry("http://x", {}, session=sess, max_retries=3)
    assert sess.get.call_count == 3


def test_403_not_retried():
    """4xx는 재시도해도 소용없으므로 즉시 올라와야 한다 (호출 1회)."""
    sess = MagicMock()
    sess.get.return_value = _resp(403)
    with patch("src.http_client.time.sleep"):
        with pytest.raises(requests.HTTPError):
            get_with_retry("http://x", {}, session=sess, max_retries=4)
    assert sess.get.call_count == 1


def test_503_is_retried():
    sess = MagicMock()
    sess.get.side_effect = [_resp(503), _resp(200)]
    with patch("src.http_client.time.sleep"):
        resp = get_with_retry("http://x", {}, session=sess, max_retries=4)
    assert resp.status_code == 200
    assert sess.get.call_count == 2


def test_429_is_retried():
    """429(Too Many Requests)는 레이트리밋 — 재시도 대상이어야 한다."""
    sess = MagicMock()
    sess.get.side_effect = [_resp(429), _resp(200)]
    with patch("src.http_client.time.sleep"):
        resp = get_with_retry("http://x", {}, session=sess, max_retries=4)
    assert resp.status_code == 200
    assert sess.get.call_count == 2


def test_429_waits_at_least_min_wait():
    """429 재시도 대기는 RATE_LIMIT_MIN_WAIT 이상이어야 한다 (짧은 백오프 방지)."""
    from src.http_client import RATE_LIMIT_MIN_WAIT
    sess = MagicMock()
    sess.get.side_effect = [_resp(429), _resp(200)]
    with patch("src.http_client.time.sleep") as slept:
        get_with_retry("http://x", {}, session=sess, max_retries=4, backoff_base=2.0)
    assert slept.call_args_list[0].args[0] >= RATE_LIMIT_MIN_WAIT


def test_429_honors_retry_after_header():
    """Retry-After 헤더가 있으면 그 값을 존중한다."""
    sess = MagicMock()
    r429 = _resp(429)
    r429.headers = {"Retry-After": "45"}
    r429.raise_for_status.side_effect = requests.HTTPError(response=r429)
    sess.get.side_effect = [r429, _resp(200)]
    with patch("src.http_client.time.sleep") as slept:
        get_with_retry("http://x", {}, session=sess, max_retries=4, backoff_base=2.0)
    assert slept.call_args_list[0].args[0] == 45.0


def test_429_gives_up_after_max_retries():
    sess = MagicMock()
    sess.get.return_value = _resp(429)
    with patch("src.http_client.time.sleep"):
        with pytest.raises(requests.HTTPError):
            get_with_retry("http://x", {}, session=sess, max_retries=3)
    assert sess.get.call_count == 3
