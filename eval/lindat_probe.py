"""
eval/lindat_probe.py – Is one LINDAT translation replica answering with garbage?

Why this exists (issue #46)
---------------------------
Sequential ALTO runs against the public endpoint showed a very specific pattern: the
first request of every batch came back as one Czech word repeated until a length cap
(``"pravidla pravidla …"``), the byte-identical re-request succeeded, and two runs
agreed reply for reply — the same inputs failed with the same token counts. The
garbage is deterministic, yet an identical retry cures it: the signature of **one
broken replica behind a round-robin load balancer**. The pipeline's guard recovers
every such reply (``LINDAT_GUARD_RETRIES``), but at the cost of a second request each
time — the real fix is on the service side.

This script produces the evidence for that report. It sends the same short Czech text
N times, first with a fresh connection per request (what the pipeline does — plain
``requests.post``), then over one keep-alive ``requests.Session``, and for each reply
records the status, latency, length, the pipeline's own verdict
(``processors.quality.degeneration_reason``) and any response header that names a
backend. It then prints the good/bad pattern per mode and a verdict:

* ``✗✓✗✓✗✓…``    alternating — one of two replicas is broken (round-robin);
* periodic       one of *k* replicas is broken;
* all ✗ / all ✓  the service as a whole is broken / healthy right now;
* irregular      mixed; other users' traffic scrambles a round-robin — re-run with
                 a larger ``--n``, or at a quieter time.

The ``session`` line tells whether the balancer pins a keep-alive connection to one
replica (all ✓ or all ✗) or balances per request (alternating again).

It is a *script*, not a test: it calls the live endpoint (``TRANSLATION_URL`` /
``LINDAT_BASE_URL`` / the LINDAT default, exactly as the pipeline resolves it).

Usage
-----
    python -m eval.lindat_probe
    python -m eval.lindat_probe --n 30 --text "Nálezová zpráva" --pause 0.5
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests  # noqa: E402

from processors.quality import degeneration_reason  # noqa: E402
from processors.translator import resolve_translation_url  # noqa: E402

DEFAULT_TEXT = "Záchranný archeologický výzkum"
GOOD, BAD = "✓", "✗"

#: Response headers that commonly name the server / upstream that answered.
_BACKEND_HEADERS = ("server", "via", "x-served-by", "x-backend", "x-backend-server", "x-upstream", "x-cache")


def classify(pattern: str) -> str:
    """Verdict for a string of ✓/✗ results, in request order."""
    n = len(pattern)
    bad = pattern.count(BAD)
    if n == 0:
        return "no replies"
    if bad == 0:
        return "all good — the endpoint answered every request correctly"
    if bad == n:
        return "all bad — every request degenerated (the service as a whole is broken)"
    if n >= 4 and all(pattern[i] != pattern[i + 1] for i in range(n - 1)):
        return "alternating — consistent with one of two replicas behind a round-robin balancer being broken"
    for period in range(3, n // 2 + 1):
        if all(pattern[i] == pattern[i + period] for i in range(n - period)):
            broken = pattern[:period].count(BAD)
            return (
                f"periodic (period {period}) — consistent with {broken} of {period} replicas "
                "behind a round-robin balancer being broken"
            )
    return (
        f"irregular — {bad}/{n} degenerate; other traffic scrambles a round-robin pattern, "
        "so re-run with a larger --n or at a quieter time"
    )


def _backend_headers(response) -> dict:
    found = {}
    for key, value in response.headers.items():
        lowered = key.lower()
        if lowered in _BACKEND_HEADERS or (lowered.startswith("x-") and "backend" in lowered):
            found[key] = value
    if response.cookies:
        found["set-cookie"] = ",".join(sorted(response.cookies.keys()))
    return found


def probe(post, url: str, text: str, n: int, pause: float) -> list[dict]:
    """POST *text* to *url* *n* times through *post*; one result dict per request."""
    rows = []
    for index in range(n):
        started = time.monotonic()
        row = {"index": index + 1}
        try:
            response = post(url, data={"input_text": text}, timeout=60)
            row["elapsed"] = time.monotonic() - started
            row["status"] = response.status_code
            response.encoding = "utf-8"
            row["reply"] = response.text.strip() if response.status_code == 200 else ""
            row["reason"] = (
                degeneration_reason(text, row["reply"])
                if response.status_code == 200
                else f"HTTP {response.status_code}"
            )
            row["headers"] = _backend_headers(response)
        except requests.RequestException as exc:
            row.update(elapsed=time.monotonic() - started, status=None, reply="", headers={})
            row["reason"] = f"{type(exc).__name__}: {exc}"
        rows.append(row)
        if pause > 0:
            time.sleep(pause)
    return rows


def pattern_of(rows: list[dict]) -> str:
    return "".join(BAD if row["reason"] else GOOD for row in rows)


def _print_rows(label: str, rows: list[dict]) -> None:
    print(f"\n── {label} ──")
    for row in rows:
        mark = BAD if row["reason"] else GOOD
        tokens = len(row["reply"].split())
        headers = " ".join(f"{k}={v}" for k, v in row["headers"].items())
        detail = row["reason"] or row["reply"][:60]
        print(f"#{row['index']:02d} {mark} {row['elapsed']:5.2f}s {tokens:4d} tok  {detail}  {headers}".rstrip())


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe the LINDAT translation endpoint for a broken replica.")
    parser.add_argument("--n", type=int, default=12, help="requests per mode (default 12)")
    parser.add_argument("--text", default=DEFAULT_TEXT, help=f"Czech text to translate (default {DEFAULT_TEXT!r})")
    parser.add_argument("--model", default="cs-en", help="model pair (default cs-en)")
    parser.add_argument("--pause", type=float, default=0.0, help="seconds between requests (default 0)")
    args = parser.parse_args()

    src, tgt = args.model.split("-", 1)
    base = resolve_translation_url().rstrip("/")
    url = f"{base}/models/{args.model}?src={src}&tgt={tgt}"
    print(f"endpoint: {url}\ntext: {args.text!r}   requests per mode: {args.n}")

    fresh = probe(requests.post, url, args.text, args.n, args.pause)
    _print_rows("fresh connection per request (what the pipeline does)", fresh)
    with requests.Session() as session:
        pinned = probe(session.post, url, args.text, args.n, args.pause)
    _print_rows("one keep-alive session", pinned)

    fresh_pattern, pinned_pattern = pattern_of(fresh), pattern_of(pinned)
    bad_sample = next((row["reply"][:120] for row in fresh + pinned if row["reason"] and row["reply"]), "")
    good_sample = next((row["reply"][:120] for row in fresh + pinned if not row["reason"]), "")

    print("\n── summary (paste into a report) ──")
    print(f"when:     {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    print(f"endpoint: {url}")
    print(f"input:    {args.text!r} (identical for every request)")
    print(f"fresh:    {fresh_pattern}  → {classify(fresh_pattern)}")
    print(f"session:  {pinned_pattern}  → {classify(pinned_pattern)}")
    if bad_sample:
        print(f"bad reply:  {bad_sample!r}")
    if good_sample:
        print(f"good reply: {good_sample!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
