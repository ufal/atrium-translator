"""
processors/vocab.py – Shared vocabulary CSV loader.

Both ``LindatTranslator`` (Tag-and-Protect) and ``LLMTranslator`` (prompt
glossary injection) read the same ``source_lemma,target_translation`` CSV.  The
loader lives here so the parsing rules (header detection, key lower-casing,
tolerant row handling) are defined once.

Two loaders are offered over the same parsing rules:

* :func:`load_vocabulary` – the translation path.  Returns the historical
  ``{source_lemma_lower: target_translation}`` mapping and nothing else.
* :func:`load_vocabulary_records` – the provenance path.  Returns the whole row,
  so the optional ``source,source_id,uri`` columns written by
  :mod:`load_vocab` (and by the sibling ``atrium-nlp-enrich`` ``*_flat.csv``
  exports) survive the load and a translated term stays traceable to the
  thesaurus concept it came from.

Any column past the second is optional: a plain two-column CSV loads through
both functions unchanged, with the provenance fields empty.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import NamedTuple, Union

# First-column values recognised as a header row and skipped.
_HEADER_KEYS = ("source_lemma", "source", "src", "term", "lemma", "cs")

# Optional provenance columns, in the order they are written after the two
# required ones.  The names match the leading columns of the ``*_flat.csv``
# emitted by atrium-nlp-enrich (``…,source,source_id,uri,scheme,sub,broader,
# sort``), so those wider files are prefix-compatible with ours.
_EXTRA_FIELDS = ("source", "source_id", "uri")


class VocabRecord(NamedTuple):
    """One vocabulary row.  Provenance fields are ``""`` when not in the file."""

    source_lemma: str
    target_translation: str
    source: str = ""
    source_id: str = ""
    uri: str = ""


def _is_header_row(index: int, first_cell: str) -> bool:
    """Header detection: row 0 only, judged by its first column."""
    return index == 0 and first_cell.lower() in _HEADER_KEYS


def _extra_field_columns(header: list[str] | None) -> dict[str, int]:
    """Map each optional provenance field to its column index.

    With a header row the names are honoured wherever they sit (so a wider
    ``*_flat.csv`` reads correctly); without one the documented positions 3-5
    are assumed.  A field the header does not mention is simply absent.
    """
    if header is None:
        return {name: 2 + offset for offset, name in enumerate(_EXTRA_FIELDS)}
    lowered = [cell.strip().lower() for cell in header]
    return {name: lowered.index(name) for name in _EXTRA_FIELDS if name in lowered}


def _cell(row: list[str], index: int | None) -> str:
    """Stripped value of *row* at *index*, or ``""`` when the row is shorter."""
    if index is None or index >= len(row):
        return ""
    return row[index].strip()


def load_vocabulary_records(path: Union[str, Path]) -> dict:
    """Load a ``{source_lemma_lower: VocabRecord}`` mapping from *path*.

    Parsing rules are those of :func:`load_vocabulary` (same header detection,
    same lower-cased keys, same tolerant row handling, same warn-and-continue on
    an unreadable file); the difference is only how much of the row is kept.
    """
    records: dict = {}
    try:
        with open(path, mode="r", encoding="utf-8", newline="") as fh:
            reader = csv.reader(fh)
            extra = _extra_field_columns(None)
            for i, row in enumerate(reader):
                if len(row) < 2:
                    continue
                src, tgt = row[0].strip(), row[1].strip()
                if _is_header_row(i, src):
                    extra = _extra_field_columns(row)
                    continue
                if src:
                    records[src.lower()] = VocabRecord(
                        src,
                        tgt,
                        _cell(row, extra.get("source")),
                        _cell(row, extra.get("source_id")),
                        _cell(row, extra.get("uri")),
                    )
    except Exception as e:
        print(f"[WARN] Could not load vocabulary from '{path}': {e}")
    return records


def load_vocabulary(path: Union[str, Path]) -> dict:
    """Load a ``{source_lemma_lower: target_translation}`` mapping from *path*.

    * The first row is skipped when its first column looks like a header.
    * Keys are stored lower-cased (matching is case-insensitive); values verbatim.
    * Rows with fewer than two columns are ignored.
    * Any column past the second is ignored here — see
      :func:`load_vocabulary_records` to read the provenance columns too.
    * A missing/unreadable file yields an empty dict (with a warning) rather than
      raising, so a bad vocab path never aborts a run.
    """
    return {key: rec.target_translation for key, rec in load_vocabulary_records(path).items()}


def get_matching_terms(text: str, vocab: dict) -> list[tuple[str, str]]:
    """Return ``(source, target)`` pairs from *vocab* that are present in *text*
    as whole words.

    Two-pass matching:
    1. Fast substring pre-filter (``src in low_text``) to skip most misses.
    2. ``\\b``-anchored regex confirm to reject short keys that are substrings of
       longer, unrelated words (e.g. "kost" must not match inside "kostel").
    """
    matched = []
    low_text = text.lower()
    for src, tgt in vocab.items():
        if src in low_text:
            if re.search(rf"\b{re.escape(src)}\b", low_text):
                matched.append((src, tgt))
    return matched
