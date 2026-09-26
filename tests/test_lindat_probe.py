"""tests/test_lindat_probe.py — the verdict logic of eval/lindat_probe.py, no network.

The probe exists to turn "LINDAT fails from time to time" into evidence: the same
input sent repeatedly, and the good/bad pattern that tells one broken replica behind
a round-robin balancer (✗✓✗✓…) apart from a broken service (all ✗) or noise.
"""

from unittest.mock import MagicMock

import pytest

from eval.lindat_probe import BAD, GOOD, classify, pattern_of, probe

LOOP = " ".join(["pravidla"] * 137)


@pytest.mark.parametrize(
    "pattern,expected",
    [
        ("✗✓✗✓✗✓✗✓", "alternating"),
        ("✓✗✓✗✓✗", "alternating"),
        ("✗✓✓✗✓✓✗✓✓", "periodic (period 3)"),
        ("✓✓✓✓", "all good"),
        ("✗✗✗✗", "all bad"),
        ("✓✗✗✓✓✓✗✓", "irregular"),
        ("", "no replies"),
    ],
)
def test_classify(pattern, expected):
    assert classify(pattern).startswith(expected)


def test_periodic_verdict_counts_the_broken_replicas():
    assert "1 of 3 replicas" in classify("✗✓✓✗✓✓✗✓✓")


def _response(text, status=200, headers=None):
    response = MagicMock()
    response.status_code = status
    response.text = text
    response.headers = headers or {"Server": "nginx", "X-Backend-Server": "replica-a"}
    response.cookies = {}
    return response


def test_probe_records_the_pattern_and_the_backend_headers():
    replies = iter([_response(LOOP), _response("Rescue archaeological research"), _response("", status=503)])
    rows = probe(lambda url, **kw: next(replies), "http://x/models/cs-en", "Záchranný archeologický výzkum", 3, 0)
    assert pattern_of(rows) == BAD + GOOD + BAD
    assert rows[0]["reason"].startswith("runaway length")
    assert rows[2]["reason"] == "HTTP 503"
    assert rows[0]["headers"]["X-Backend-Server"] == "replica-a"
