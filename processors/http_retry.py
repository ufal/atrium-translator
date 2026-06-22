"""
processors/http_retry.py – Shared HTTP retry / throttle / back-off helper.

Why this module exists
----------------------
``processors/translator.py`` grew a careful ``_post_with_retry`` / ``_throttle``
pair (bounded exponential back-off with jitter, an optional minimum inter-request
interval, and *fail-loud* behaviour that raises instead of returning a corrupt
body).  Issue #4 adds a second backend (``processors/llm_translator.py``) that
needs the very same behaviour against a different endpoint shape.  Rather than
copy the loop — the exact mistake ``processors/chunking.py`` was created to
avoid — both backends now call ``request_with_retry`` here, so the retry policy
lives in exactly one place.

Design
------
``request_with_retry`` is transport-agnostic: the caller supplies a zero-arg
``perform`` callable that issues *one* HTTP request and returns a
``requests.Response``.  This keeps the actual ``requests.post(...)`` /
``requests.get(...)`` call inside the caller's module, so test suites that patch
``processors.<backend>.requests.post`` keep working unchanged.
"""

from __future__ import annotations

import random
import time
from typing import Callable, Iterable, Optional

import requests

# Default HTTP status codes worth retrying (transient server / rate-limit).
DEFAULT_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class Throttle:
    """Callable enforcing an optional minimum interval between requests.

    ``Throttle(0.0)`` (the default) is a no-op.  Set a positive value to keep a
    chatty caller (e.g. ALTO dual-pass, ``1 + N`` calls per block) from flooding
    a shared / free-tier endpoint.
    """

    def __init__(self, min_interval_s: float = 0.0) -> None:
        self.min_interval_s = min_interval_s
        self._last_call_ts: float = 0.0

    def __call__(self) -> None:
        if self.min_interval_s <= 0:
            return
        now = time.monotonic()
        wait = self._last_call_ts + self.min_interval_s - now
        if wait > 0:
            time.sleep(wait)
        self._last_call_ts = time.monotonic()


def request_with_retry(
    perform: Callable[[], requests.Response],
    *,
    max_retries: int,
    backoff_base_s: float,
    retryable_status: Iterable[int] = DEFAULT_RETRYABLE_STATUS,
    throttle: Optional[Callable[[], None]] = None,
    error_cls: type = RuntimeError,
    label: str = "request",
) -> requests.Response:
    """Issue *perform* with bounded exponential back-off; return the 200 Response.

    *perform* must send a single request and return a ``requests.Response``.
    Network errors and any status in *retryable_status* are retried up to
    *max_retries* times (``max_retries + 1`` total attempts) with
    ``backoff_base_s * 2**attempt + jitter`` seconds between tries.  A
    non-retryable non-200 status raises *error_cls* immediately; exhausting the
    retries raises *error_cls* too.  The function never returns a non-200
    response, so callers cannot accidentally treat an error body as content.
    """
    retryable = set(retryable_status)
    last_reason = "unknown error"

    for attempt in range(max_retries + 1):
        if throttle is not None:
            throttle()
        try:
            response = perform()
        except requests.exceptions.RequestException as e:
            last_reason = f"network error: {e}"
        else:
            if response.status_code == 200:
                return response
            if response.status_code in retryable:
                last_reason = f"HTTP {response.status_code}"
            else:
                raise error_cls(f"{label} failed: HTTP {response.status_code}.")

        if attempt < max_retries:
            sleep_s = backoff_base_s * (2**attempt) + random.uniform(0, 0.25)
            print(
                f"[WARN] {label} failed ({last_reason}); retrying in "
                f"{sleep_s:.1f}s (attempt {attempt + 1}/{max_retries})."
            )
            time.sleep(sleep_s)

    raise error_cls(f"{label} failed after {max_retries} retries ({last_reason}).")
