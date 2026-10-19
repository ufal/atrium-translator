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


class _Refusal:
    """A non-200 response with a body, as vLLM and OpenAI-compatible servers send one."""

    def __init__(self, status_code, body=None, text=""):
        self.status_code, self._body, self.text = status_code, body, text

    def json(self):
        if self._body is None:
            raise ValueError("no JSON")
        return self._body


@pytest.mark.parametrize(
    "response, said",
    [
        (
            _Refusal(400, {"error": {"message": "System message must be at the beginning."}}),
            "System message must be at the beginning.",
        ),
        (_Refusal(400, {"detail": "bad   request\n body"}), "bad request body"),
        (_Refusal(422, None, "plain text refusal"), "plain text refusal"),
    ],
)
def test_a_refusal_names_what_the_server_said(response, said):
    """translator#4, job 7144164: 198 segments failed with "HTTP 400." and no cause."""
    with pytest.raises(RuntimeError) as info:
        request_with_retry(lambda: response, max_retries=0, backoff_base_s=0.0, label="LLM translation")
    assert str(info.value) == f"LLM translation failed: HTTP {response.status_code}: {said}"


def test_a_bodyless_refusal_keeps_the_short_message():
    with pytest.raises(RuntimeError, match=r"^x failed: HTTP 404\.$"):
        request_with_retry(lambda: _Resp(404), max_retries=0, backoff_base_s=0.0, label="x")


def test_a_long_provider_text_is_cut():
    long = _Refusal(400, {"error": {"message": "a" * 1000}})
    with pytest.raises(RuntimeError) as info:
        request_with_retry(lambda: long, max_retries=0, backoff_base_s=0.0)
    assert len(str(info.value)) < 360 and str(info.value).endswith("…")


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


# ── Retry policy is configuration, not a suggestion (atrium-project#53, factor III) ──
#
# request_with_retry used to clamp its own arguments upward:
#
#     max_retries = max(10, max_retries)
#     backoff_base_s = max(2, backoff_base_s)
#
# so LINDAT_MAX_RETRIES / LINDAT_BACKOFF_BASE_S / LLM_MAX_RETRIES /
# LLM_BACKOFF_BASE_S were read from the environment and then discarded for every
# value below the floor -- while .env.example and service/README.md documented
# them as working knobs. The effective policy was 11 attempts backing off
# 2*2**attempt, i.e. 2+4+...+1024 = 2046 s of sleep for ONE failing chunk, which
# no /translate request could survive against GRACEFUL_SHUTDOWN_S=20 plus
# serve_lifecycle's drain: the container was SIGKILLed mid-retry every time.
#
# The back-off half was invisible: pytest.ini sets LINDAT_BACKOFF_BASE_S=0.0,
# max(2, 0.0) restored 2, and conftest.py patches time.sleep globally, so no test
# could see the sleeps at all. The retry-count half was worse than invisible --
# tests/test_translator.py and tests/test_llm_backend.py had both been updated to
# assert 11 attempts and to call that "10 default retries", while the declared
# default in the code, in .env.example and in service/README.md was 4. The suite
# was green on a policy no caller had asked for, and said so in a comment.
#
# These tests assert the arguments are used AS GIVEN, and they fail loudly if the
# clamp is ever reintroduced.


def test_max_retries_is_honoured_exactly():
    """max_retries=1 means 2 attempts total, not 11."""
    perform = MagicMock(return_value=_Resp(503))
    with pytest.raises(RuntimeError):
        request_with_retry(perform, max_retries=1, backoff_base_s=0.0)
    assert perform.call_count == 2


def test_zero_retries_means_a_single_attempt():
    perform = MagicMock(return_value=_Resp(503))
    with pytest.raises(RuntimeError):
        request_with_retry(perform, max_retries=0, backoff_base_s=0.0)
    assert perform.call_count == 1


def test_backoff_base_is_honoured_exactly(monkeypatch):
    """sleep = base * 2**attempt (+ <0.25 jitter), with base as passed in."""
    slept = []
    monkeypatch.setattr(http_retry.time, "sleep", lambda s: slept.append(s))

    with pytest.raises(RuntimeError):
        request_with_retry(lambda: _Resp(500), max_retries=3, backoff_base_s=0.5)

    # Three sleeps for three retries; jitter is uniform(0, 0.25).
    assert len(slept) == 3
    for actual, base in zip(slept, (0.5, 1.0, 2.0)):
        assert base <= actual < base + 0.25


def test_default_policy_fits_inside_the_shutdown_drain(monkeypatch):
    """The call sites' defaults (4 retries, base 1.0s) must fit the drain budget.

    GRACEFUL_SHUTDOWN_S defaults to 20 (Dockerfile `ENV`, service/api.py's
    __main__). A retry policy whose worst case exceeds that guarantees the
    SIGKILL that issue #55's drain contract exists to prevent, so the two
    numbers are coupled and this test is where they are compared.
    """
    slept = []
    monkeypatch.setattr(http_retry.time, "sleep", lambda s: slept.append(s))

    with pytest.raises(RuntimeError):
        request_with_retry(lambda: _Resp(503), max_retries=4, backoff_base_s=1.0)

    assert sum(slept) < 20, f"default back-off totals {sum(slept):.1f}s, over the 20s drain budget"
