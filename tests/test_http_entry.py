"""
tests/test_http_retry.py – Unit tests for processors/http_retry.py, the shared
bounded-exponential-backoff helper used by both translation backends.

``time.sleep`` is patched out so the retry loop runs instantly; the transport is
faked with a zero-arg ``perform`` callable, mirroring the module's design.
"""

from unittest.mock import MagicMock

import pytest
import requests

from processors import http_retry
from processors.http_retry import DEFAULT_RETRYABLE_STATUS, Throttle, request_with_retry


class _Resp:
    def __init__(self, status_code: int):
        self.status_code = status_code


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr(http_retry.time, "sleep", lambda *_: None)


def _perform_sequence(*items):
    """Return a perform() that yields each item in turn; Exceptions are raised."""
    it = iter(items)

    def perform():
        item = next(it)
        if isinstance(item, Exception):
            raise item
        return item

    return perform


# ── Throttle ────────────────────────────────────────────────────────────────
def test_throttle_zero_is_noop(monkeypatch):
    slept = []
    monkeypatch.setattr(http_retry.time, "sleep", lambda s: slept.append(s))
    throttle = Throttle(0.0)
    throttle()
    throttle()
    assert slept == []


def test_throttle_enforces_min_interval(monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr(http_retry.time, "monotonic", lambda: clock["now"])
    slept = []
    monkeypatch.setattr(http_retry.time, "sleep", lambda s: slept.append(s))

    throttle = Throttle(2.0)
    throttle()  # first call: no wait, records t=100
    clock["now"] = 100.5  # 0.5 s elapsed → must wait 1.5 s
    throttle()

    assert slept == [pytest.approx(1.5)]


# ── request_with_retry ──────────────────────────────────────────────────────
def test_returns_immediately_on_200():
    perform = MagicMock(return_value=_Resp(200))
    out = request_with_retry(perform, max_retries=3, backoff_base_s=0.0)
    assert out.status_code == 200
    assert perform.call_count == 1


def test_retries_retryable_status_then_succeeds():
    perform = _perform_sequence(_Resp(503), _Resp(200))
    out = request_with_retry(perform, max_retries=3, backoff_base_s=0.0)
    assert out.status_code == 200


def test_non_retryable_status_raises_immediately():
    calls = {"n": 0}

    def perform():
        calls["n"] += 1
        return _Resp(404)

    with pytest.raises(RuntimeError):
        request_with_retry(perform, max_retries=3, backoff_base_s=0.0)
    assert calls["n"] == 1


def test_network_error_is_retried_then_succeeds():
    perform = _perform_sequence(requests.exceptions.ConnectionError("boom"), _Resp(200))
    out = request_with_retry(perform, max_retries=3, backoff_base_s=0.0)
    assert out.status_code == 200


def test_exhausted_retries_raises_custom_error():
    class MyError(Exception):
        pass

    with pytest.raises(MyError):
        request_with_retry(lambda: _Resp(500), max_retries=2, backoff_base_s=0.0, error_cls=MyError)


def test_throttle_invoked_every_attempt():
    throttle = MagicMock()
    perform = _perform_sequence(_Resp(500), _Resp(500), _Resp(200))
    request_with_retry(perform, max_retries=3, backoff_base_s=0.0, throttle=throttle)
    assert throttle.call_count == 3


def test_default_retryable_status_contents():
    assert DEFAULT_RETRYABLE_STATUS >= {429, 500, 502, 503, 504}
