"""
processors/quality.py – Backend-agnostic plausibility checks for machine translation output.

Why this module exists
----------------------
On 2026-09-26 the shipped ``data_samples/`` were refreshed against the public LINDAT
endpoint and roughly a third of all replies came back as a single Czech word repeated
up to ~150 times (``"pravidla pravidla pravidla …"``) — for one-word headings and for
full sentences alike, on the ALTO path and on the metadata path alike. The failure was
**nondeterministic**: the same field was garbage in one run and correct in the next.
Nothing in the pipeline looked at the *content* of a reply, only at its line count, so
the garbage was written into the XML, the QA CSV, and — through the ALTO line anchors,
which are never logged — into the word-to-box alignment of every block on the page.

``degeneration_reason`` is the one place that judges whether a translation is usable.
It is deliberately:

* **conservative** — every rule compares the output against its own source, so text
  that legitimately repeats (dot leaders, number tables, a heading repeated in the
  source) passes, and ordinary cs→en length drift is far inside the bounds;
* **backend-agnostic** — the LINDAT client uses it to re-request, the LLM and CT2
  backends use it in their output guards, and ``utils.py`` uses it on every batched
  item and every ALTO line anchor, so a backend without its own guard (or a test
  double) is still covered;
* **dependency-free** — importable from ``utils.py`` without pulling in a backend.
"""

from __future__ import annotations

from collections import Counter

#: Output may be at most ``MAX_TOKEN_RATIO * source + MAX_TOKEN_SLACK`` tokens long.
#: cs→en typically lands at 1.0–1.4x; a vocabulary term restored into a one-word
#: slot adds a few tokens, which the slack absorbs even for one-word sources.
MAX_TOKEN_RATIO = 3.0
MAX_TOKEN_SLACK = 8

#: A source of at least ``MIN_TOKENS_FOR_SHORT_CHECK`` tokens must yield at least
#: ``MIN_TOKEN_RATIO`` as many — anything shorter is truncation, or the reply for a
#: different line landing in this slot.
MIN_TOKEN_RATIO = 0.2
MIN_TOKENS_FOR_SHORT_CHECK = 10

#: A unit of 1..``MAX_REPEAT_UNIT`` tokens repeated ``MAX_REPEAT_RUN`` times in a row
#: is a decoder loop — unless the source itself repeats about as much.
MAX_REPEAT_UNIT = 3
MAX_REPEAT_RUN = 4

#: One token filling at least ``DOMINANCE_SHARE`` of the output, at least
#: ``DOMINANCE_MIN_COUNT`` times, is a loop that is not strictly consecutive.
DOMINANCE_SHARE = 0.5
DOMINANCE_MIN_COUNT = 6

# Punctuation stripped from token edges before tokens are compared, so that
# "pravidla," and "Pravidla" count as the same repeated word.
_EDGE_PUNCT = ".,;:!?()[]{}\"'«»„“”‚‘’-–—…/\\*"


def _normalise(tokens: list[str]) -> list[str]:
    out = []
    for tok in tokens:
        bare = tok.casefold().strip(_EDGE_PUNCT)
        out.append(bare or tok)
    return out


def _longest_repeat_run(tokens: list[str], max_unit: int = MAX_REPEAT_UNIT) -> int:
    """Longest run of back-to-back repetitions of any 1..*max_unit*-token unit.

    ``["a", "b", "a", "b", "a", "b"]`` → 3; ``["x", "x", "x", "x"]`` → 4; no
    repetition → 1. Linear in ``len(tokens)`` per unit size: a run is skipped over
    once measured, which keeps a 3000-token loop cheap to diagnose.
    """
    n = len(tokens)
    if n == 0:
        return 0
    best = 1
    for unit in range(1, max_unit + 1):
        i = 0
        while i + 2 * unit <= n:
            run = 1
            j = i + unit
            while j + unit <= n and tokens[j : j + unit] == tokens[i : i + unit]:
                run += 1
                j += unit
            if run > best:
                best = run
            i += unit * (run - 1) + 1 if run > 1 else 1
    return best


def degeneration_reason(source: str, translated: str | None) -> str | None:
    """Return why *translated* is not a usable translation of *source*, or ``None``.

    A ``None`` result is not a quality judgement — it only means the reply is not
    one of the failure shapes below, all of which were observed on real LINDAT
    output or on the ALTO line anchors derived from it:

    * empty output for a source with any letter or digit in it;
    * runaway length (more than ``3x + 8`` the source's tokens);
    * truncation (a 10+-token source answered with under a fifth of that);
    * a decoder loop: a 1–3-token unit repeated 4+ times in a row, more than the
      source itself repeats;
    * one token dominating the output (≥ 50 %, ≥ 6 times) without doing so in the
      source;
    * a multi-token output that is one alphanumeric token over and over, for a
      source that is not (``"pravidla pravidla"`` for ``"Vojtěch Marek"``).
    """
    src_tokens = (source or "").split()
    if not src_tokens:
        return None
    out_tokens = (translated or "").split()
    ns, no = len(src_tokens), len(out_tokens)

    if no == 0:
        if any(ch.isalnum() for ch in source):
            return "empty translation for a non-empty source"
        return None

    if no > MAX_TOKEN_RATIO * ns + MAX_TOKEN_SLACK:
        return f"runaway length: {no} tokens for a {ns}-token source"

    if ns >= MIN_TOKENS_FOR_SHORT_CHECK and no < MIN_TOKEN_RATIO * ns:
        return f"truncated: {no} tokens for a {ns}-token source"

    # A source with no letter or digit (a dash rule, dot leaders, "— . .") has no
    # wording to loop on; whatever the backend echoes back is judged on length alone.
    if not any(ch.isalnum() for ch in source):
        return None

    src_norm = _normalise(src_tokens)
    out_norm = _normalise(out_tokens)

    out_run = _longest_repeat_run(out_norm)
    if out_run >= MAX_REPEAT_RUN and out_run > _longest_repeat_run(src_norm) + 1:
        return f"repetition loop: a phrase repeats {out_run} times in a row"

    top, count = Counter(out_norm).most_common(1)[0]
    if any(ch.isalnum() for ch in top):
        if count >= DOMINANCE_MIN_COUNT and count >= DOMINANCE_SHARE * no and Counter(src_norm)[top] * 2 < count:
            return f"repetition loop: {top!r} is {count} of {no} output tokens"
        if no >= 2 and count == no and len(set(src_norm)) >= 2:
            return f"repetition loop: output is {top!r} repeated {no} times"

    return None
