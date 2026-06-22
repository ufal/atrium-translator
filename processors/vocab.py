"""
processors/vocab.py – Shared two-column vocabulary CSV loader.

Both ``LindatTranslator`` (Tag-and-Protect) and ``LLMTranslator`` (prompt
glossary injection) read the same ``source_lemma,target_translation`` CSV.  The
loader lives here so the parsing rules (header detection, key lower-casing,
tolerant row handling) are defined once.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Union

# First-column values recognised as a header row and skipped.
_HEADER_KEYS = ("source_lemma", "source", "src", "term", "lemma", "cs")


def load_vocabulary(path: Union[str, Path]) -> dict:
    """Load a ``{source_lemma_lower: target_translation}`` mapping from *path*.

    * The first row is skipped when its first column looks like a header.
    * Keys are stored lower-cased (matching is case-insensitive); values verbatim.
    * Rows with fewer than two columns are ignored.
    * A missing/unreadable file yields an empty dict (with a warning) rather than
      raising, so a bad vocab path never aborts a run.
    """
    vocab: dict = {}
    try:
        with open(path, mode="r", encoding="utf-8", newline="") as fh:
            reader = csv.reader(fh)
            for i, row in enumerate(reader):
                if len(row) < 2:
                    continue
                src, tgt = row[0].strip(), row[1].strip()
                if i == 0 and src.lower() in _HEADER_KEYS:
                    continue
                if src:
                    vocab[src.lower()] = tgt
    except Exception as e:
        print(f"[WARN] Could not load vocabulary from '{path}': {e}")
    return vocab
