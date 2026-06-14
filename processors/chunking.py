"""
processors/chunking.py – Shared sentence-aware text chunker for the ATRIUM
translation pipeline.

Why this module exists
----------------------
``processors/translator.py`` and ``processors/lemmatizer.py`` each used to carry
a byte-identical private ``_chunk_text`` implementation.  Both copies documented
a boundary *priority order* (newline → sentence → clause → word → hard cut) but
neither enforced it: the loop kept the **rightmost** candidate across *all*
separators with no early exit, so a later comma could override an earlier
sentence-terminal period and the text still split mid-sentence.  The priority
list was inert.

This module provides a single corrected ``chunk_text`` that both classes now
delegate to, so the documented behaviour is actually delivered and the two
sites can never drift apart again.

Boundary priority (now genuinely enforced, highest first)
---------------------------------------------------------
  1. ``\\n``                          – paragraph / OCR-line break
  2. ``'. '`` / ``'! '`` / ``'? '``   – sentence-terminal punctuation
  3. ``'; '`` / ``', '``              – clause-level punctuation
  4. ``' '``                          – word boundary (fallback)
  5. hard cut at ``chunk_size``       – last resort for oversized single tokens

The search for a boundary is only accepted when the candidate split point is at
least 25 % into the current window (``_MIN_SPLIT``), which prevents pathological
behaviour on texts that begin with a very long sentence.
"""

from __future__ import annotations

# Single source of truth for the default chunk size, shared by the translator,
# the lemmatizer, and the paradata config snapshot in main.py (finding #13).
DEFAULT_CHUNK_SIZE: int = 4000

# Separators grouped into priority *tiers*.  The first tier (scanned in order)
# that yields any acceptable split point wins; lower-priority tiers are not
# consulted once a higher tier matches.  The second element of each pair is the
# number of characters from the separator to retain in the *left* chunk (keep
# the terminal punctuation, drop the following space / newline).
_SEP_TIERS: list[list[tuple[str, int]]] = [
    [("\n", 0)],                          # paragraph / OCR-line break
    [(". ", 1), ("! ", 1), ("? ", 1)],    # sentence-terminal punctuation
    [("; ", 1), (", ", 1)],               # clause-level punctuation
]


def chunk_text(text: str, chunk_size: int = DEFAULT_CHUNK_SIZE) -> list[str]:
    """
    Split *text* into chunks no longer than *chunk_size* characters, keeping
    whole sentences together wherever possible.

    Boundaries are tried tier by tier (see ``_SEP_TIERS``); the highest-priority
    tier that has a match inside the current window determines the split point.
    Only if no separator tier yields an acceptable point does the function fall
    back to the last word boundary, and finally to a hard cut for a single token
    longer than *chunk_size*.
    """
    if not text or not text.strip():
        return []
    text = text.strip()
    if len(text) <= chunk_size:
        return [text]

    _MIN_SPLIT = chunk_size // 4  # never split in the first 25 % of the window

    chunks: list[str] = []
    remaining = text

    while len(remaining) > chunk_size:
        window = remaining[:chunk_size]
        best = -1

        # Highest-priority tier with a usable match wins.
        for tier in _SEP_TIERS:
            for sep, keep in tier:
                pos = window.rfind(sep)
                if pos > _MIN_SPLIT:
                    candidate = pos + keep  # include terminal punct, drop separator
                    if candidate > best:
                        best = candidate
            if best > _MIN_SPLIT:
                break  # do not let a lower-priority tier override this one

        # Fallback: word boundary (UNCHANGED from the original behaviour).
        # Note: a window whose only space sits within the first 25 % must still
        # split at that space rather than hard-cutting mid-word, so the guard
        # here is intentionally `pos > 0`, NOT `pos > _MIN_SPLIT`.
        if best <= _MIN_SPLIT:
            pos = window.rfind(" ")
            best = pos if pos > 0 else chunk_size  # hard cut as last resort

        chunks.append(remaining[:best].strip())
        remaining = remaining[best:].lstrip()

    if remaining.strip():
        chunks.append(remaining.strip())

    return [c for c in chunks if c]