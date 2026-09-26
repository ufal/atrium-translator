"""
eval/langid_report.py – Per-block source-language report for ``--source_lang auto``.

Prints one CSV row per ALTO ``TextBlock`` (or per metadata field) with what
FastText guessed and what the pipeline's policy resolved it to, so the two
thresholds can be tuned on real data where the model is available:

    unit, letters, top1, top2, top3, label, resolved, basis

``basis`` is ``detected`` / ``hint`` / ``context`` / ``default`` (see
processors/language.py). A summary — the document language and the per-language
tally, including the FastText guesses that were overridden — goes to stderr.

It is a *script*, not a test: it loads the real FastText model (Hugging Face
download on first use). The policy honours the same environment as the pipeline
(``DEFAULT_SOURCE_LANG``, ``LANG_ID_MIN_CONFIDENCE``, ``LANG_ID_MIN_LETTERS``,
``LANG_ID_LANGUAGES``), so a threshold can be tried before a run changes.

Usage
-----
    python -m eval.langid_report data_samples/my_documents/MTX201501307_anon.alto.xml > langid.csv
    LANG_ID_MIN_CONFIDENCE=0.7 python -m eval.langid_report doc.alto.xml > strict.csv
    python -m eval.langid_report record.xml --xpaths amcr-fields.txt
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lxml import etree  # noqa: E402

from processors.identifier import LanguageIdentifier  # noqa: E402
from processors.language import (  # noqa: E402
    DOCUMENT_SAMPLE_CHARS,
    LanguageTally,
    SourceLanguagePolicy,
    letter_count,
    resolve_source_language,
)
from utils import (  # noqa: E402
    XML_LANG,
    _alto_document_text,
    _alto_language_label,
    _metadata_record_text,
    _resolve_namespaces,
)

#: LINDAT's cs-centric source set for an English target — what the default backend
#: accepts. Override with --languages to mirror another backend.
LINDAT_SOURCES = "cs,de,fr,pl,ru,uk,en"


def _units(path: Path, xpaths: list[str]):
    """Yield ``(unit_id, text, label)`` per ALTO block or metadata field, plus the document text."""
    root = etree.parse(str(path)).getroot()
    if xpaths:
        ns = _resolve_namespaces(root)
        units = []
        for xpath in xpaths:
            for i, elem in enumerate(root.xpath(xpath, namespaces=ns), 1):
                if isinstance(getattr(elem, "text", None), str) and elem.text.strip():
                    units.append((f"{xpath}[{i}]", elem.text.strip(), elem.get(XML_LANG)))
        return _metadata_record_text(root, xpaths, ns), units

    units = []
    for block in root.iter():
        if not isinstance(block.tag, str) or etree.QName(block).localname != "TextBlock":
            continue
        words = [
            s.get("CONTENT")
            for s in block.iter()
            if isinstance(s.tag, str) and etree.QName(s).localname == "String" and s.get("CONTENT")
        ]
        if words:
            units.append((block.get("ID", "?"), " ".join(words), _alto_language_label(block)))
    return _alto_document_text(root), units


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("path", type=Path, help="ALTO XML, or metadata XML with --xpaths")
    parser.add_argument("--xpaths", type=Path, help="metadata mode: file of XPath targets (one per line)")
    parser.add_argument(
        "--languages",
        default=LINDAT_SOURCES,
        help=f"languages the backend can translate from (CSV; default LINDAT's: {LINDAT_SOURCES})",
    )
    args = parser.parse_args()

    xpaths = []
    if args.xpaths:
        xpaths = [
            line.strip()
            for line in args.xpaths.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]

    allowed = {code.strip() for code in args.languages.split(",") if code.strip()}
    policy = SourceLanguagePolicy.from_env(allowed=allowed)
    identifier = LanguageIdentifier()
    document_text, units = _units(args.path, xpaths)
    document = resolve_source_language(identifier, document_text, policy, max_chars=DOCUMENT_SAMPLE_CHARS)

    tally = LanguageTally()
    writer = csv.writer(sys.stdout)
    writer.writerow(["unit", "letters", "top1", "top2", "top3", "label", "resolved", "basis"])
    for unit_id, text, label in units:
        top = identifier.candidates(text, k=3) if letter_count(text) >= policy.min_letters else []
        resolution = resolve_source_language(identifier, text, policy, hint=label, context=document.lang)
        tally.add(resolution)
        cells = [f"{lang}:{score:.2f}" for lang, score in top] + [""] * (3 - len(top))
        writer.writerow([unit_id, letter_count(text), *cells, label or "", resolution.lang, resolution.basis])

    guess = f", FastText top guess {document.raw[0]} {document.raw[1]:.2f}" if document.raw else ""
    print(f"policy: {policy.describe()}", file=sys.stderr)
    print(f"document: {document.lang} ({document.basis}{guess})", file=sys.stderr)
    print(f"units: {tally.summary()}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
