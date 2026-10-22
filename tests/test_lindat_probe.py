"""tests/test_lindat_probe.py — the verdict logic of eval/lindat_probe.py, no network.

The probe exists to turn "LINDAT fails from time to time" into evidence: the same
input sent repeatedly, and the good/bad pattern that tells one broken replica behind
a round-robin balancer (✗✓✗✓…) apart from a broken service (all ✗) or noise.
"""

from unittest.mock import MagicMock

import pytest

from eval import lindat_probe
from eval.lindat_probe import BAD, DAILY_TEXT, GOOD, classify, content_reason, pattern_of, probe, verdict

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


# ── The daily check (atrium-project#78) ──────────────────────────────────────

GOOD_DAILY = "The excavation in 1932 uncovered 3 graves and 120 shards."


@pytest.mark.parametrize(
    "reply,expected",
    [
        (GOOD_DAILY, None),
        ("The excavation in 1932 uncovered graves and 120 shards.", "numbers lost: 3"),
        (DAILY_TEXT, "not translated"),
        ("The excavation in 1932 uncovered 3 hroby and 120 střepů.", "not in en: the reply keeps řů"),
        ("", None),  # an empty reply is the guard's to judge
    ],
)
def test_content_reason(reply, expected):
    reason = content_reason(DAILY_TEXT, reply)
    assert reason == expected if expected is None else reason.startswith(expected)


def test_a_same_language_pair_is_not_held_to_the_letter_rule():
    assert content_reason("Výzkum 3", "Výzkum 3", src="cs", tgt="cs") is None


def test_a_reply_that_loses_a_number_is_bad_in_the_probe():
    replies = iter([_response(GOOD_DAILY), _response("The excavation uncovered graves and shards.")])
    rows = probe(lambda url, **kw: next(replies), "http://x/models/cs-en", DAILY_TEXT, 2, 0)
    assert pattern_of(rows) == GOOD + BAD
    assert rows[1]["reason"] == "numbers lost: 1932, 3, 120"


def test_verdict_fails_only_over_the_maximum_and_reports_only_without_one():
    rows = [{"reason": None}, {"reason": "HTTP 503"}]
    assert verdict(rows, None) == 0
    assert verdict(rows, 1) == 0
    assert verdict(rows, 0) == 1


class _Session:
    def __init__(self, post):
        self.post = post

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.mark.parametrize("bad_every,expected", [(0, 0), (2, 1)])
def test_the_daily_run_exits_1_on_a_bad_reply(monkeypatch, capsys, bad_every, expected):
    sent = []

    def post(url, data, timeout):
        sent.append(data["input_text"])
        bad = bad_every and len(sent) % bad_every == 0
        return _response(" ".join(["pravidla"] * 40) if bad else GOOD_DAILY)

    monkeypatch.setattr(lindat_probe, "resolve_translation_url", lambda: "http://lindat.test/api/v2")
    monkeypatch.setattr(lindat_probe.requests, "post", post)
    monkeypatch.setattr(lindat_probe.requests, "Session", lambda: _Session(post))
    assert lindat_probe.main(["--text", "daily", "--n", "3", "--max-bad", "0"]) == expected
    assert set(sent) == {DAILY_TEXT} and len(sent) == 6
    assert ("FAIL" if expected else "OK") in capsys.readouterr().out
