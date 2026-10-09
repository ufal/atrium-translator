"""
tests/test_unsupported_language.py
==================================
A source language the backend cannot translate is reported (translator#52, option (a)).

With ``source_lang=auto`` a segment the identifier places, confidently, in a language the backend
cannot translate (Latin ``la`` 0.97, Hungarian ``hu`` 0.99 on LINDAT) falls back to the element's
label, the document's language, then the default — and goes to that language's model. Until #52
the only trace was one log line per document; the ``_log.csv`` said ``ok`` and the record said
nothing. The segment is still translated from the fallback (that was decided: report, do not
refuse), and now:

* its ``_log.csv`` rows say ``unsupported_lang`` (an ``untranslated`` row stays ``untranslated``);
* the record's ``translations`` block counts the languages in ``unsupported_source_langs``;
* the log line lists them.

A German segment is translated as before, and an untrustworthy guess (a low score, an exotic
FastText label, a text too short to judge) falls back as before and is not reported.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from unittest.mock import MagicMock, patch

from lxml import etree

from processors.language import SourceLanguagePolicy
from utils import STATUS_OK, STATUS_UNSUPPORTED_LANG, STATUS_UNTRANSLATED, process_alto_xml, process_metadata_xml

LINDAT_MODELS = ["cs-en", "de-en", "fr-en", "pl-en", "ru-en", "uk-en", "en-cs", "uk-cs"]

CZECH = "Benediktinský klášter vznikl kolem roku tisíc jako nejstarší mužský klášter."
LATIN = "Fossa cum vasis fictilibus et ossibus animalium inventa est prope murum."
HUNGARIAN = "A régészeti feltárás során kerámiatöredékeket és állatcsontokat találtak."
GERMAN = "Die Ausgrabung brachte Keramikscherben und Tierknochen zutage."

#: What the stub identifier answers, by the text's first word: FastText-like candidates.
ANSWERS = {
    "Benediktinský": [("cs", 0.98)],
    "Fossa": [("la", 0.97)],
    "A": [("hu", 0.99)],
    "Die": [("de", 0.95)],
}


class _Identifier:
    """FastText stand-in: the candidates of the text's first word; the whole document is Czech."""

    def candidates(self, text, k=5, max_chars=2000):
        return list(ANSWERS.get(text.split()[0], [("cs", 0.9)])) if text.split() else []


class _Lindat:
    """LINDAT stand-in: the model pairs of the real service, and a recognisable translation."""

    name = "lindat"
    vocabulary: dict = {}
    protected_count = 0
    supported_models = LINDAT_MODELS

    def __init__(self, degenerate=()):
        self.calls = []
        self.degenerate = set(degenerate)

    def reset_protected_count(self):
        pass

    def translate(self, text, src_lang, tgt_lang="en"):
        self.calls.append((text, src_lang))
        if text in self.degenerate:
            return "x x x x x x x x x x x x x x x x x x x x x x x x x"
        return "\n".join(f"EN[{src_lang}]:{line}" if line.strip() else line for line in text.split("\n"))


class _Doc:
    def __init__(self):
        self.blocks = {}

    def set_block(self, name, value):
        self.blocks[name] = value


AMCR_NS = "https://api.aiscr.cz/schema/amcr/2.2/"
_META = f"""<?xml version="1.0" encoding="utf-8"?>
<amcr:amcr xmlns:amcr="{AMCR_NS}">
  <amcr:popis>{CZECH}</amcr:popis>
  <amcr:poznamka>{LATIN}</amcr:poznamka>
  <amcr:nazev>{HUNGARIAN}</amcr:nazev>
  <amcr:komentar>{GERMAN}</amcr:komentar>
</amcr:amcr>
""".encode()
_XPATHS = ["//amcr:amcr/amcr:popis", "//amcr:amcr/amcr:poznamka", "//amcr:amcr/amcr:nazev", "//amcr:amcr/amcr:komentar"]


def _rows(buffer):
    return {row[3]: row[5] for row in csv.reader(io.StringIO(buffer.getvalue()))}


def _metadata(tmp_path, translator=None):
    src = tmp_path / "rec.xml"
    src.write_bytes(_META)
    translator, doc, buffer = translator or _Lindat(), _Doc(), io.StringIO()
    process_metadata_xml(
        src,
        tmp_path / "out.xml",
        _XPATHS,
        translator,
        "auto",
        "en",
        csv_writer=csv.writer(buffer),
        identifier=_Identifier(),
        doc=doc,
    )
    return translator, doc, _rows(buffer)


def test_metadata_fields_in_latin_and_hungarian_are_reported_and_still_translated(tmp_path, caplog):
    with caplog.at_level(logging.INFO, logger="utils"):
        translator, doc, rows = _metadata(tmp_path)
    assert rows == {
        CZECH: STATUS_OK,
        LATIN: STATUS_UNSUPPORTED_LANG,
        HUNGARIAN: STATUS_UNSUPPORTED_LANG,
        GERMAN: STATUS_OK,
    }
    # Translated from the fallback (the record's language) as before; German keeps its own model.
    assert dict(translator.calls) == {CZECH: "cs", LATIN: "cs", HUNGARIAN: "cs", GERMAN: "de"}
    block = doc.blocks["translations"]
    assert block["detected_source_lang"] == "cs"
    assert block["unsupported_source_langs"] == {"hu": 1, "la": 1}
    assert "cannot translate, translated from the fallback: " in caplog.text
    assert "la×1" in caplog.text and "hu×1" in caplog.text


def test_a_field_kept_as_source_stays_untranslated(tmp_path, monkeypatch):
    """`untranslated` (the source text was kept) says more than `unsupported_lang`."""
    monkeypatch.setenv("TRANSLATION_RERUN_ROUNDS", "0")
    _, doc, rows = _metadata(tmp_path, _Lindat(degenerate={LATIN}))
    assert rows[LATIN] == STATUS_UNTRANSLATED and rows[HUNGARIAN] == STATUS_UNSUPPORTED_LANG
    assert doc.blocks["translations"]["unsupported_source_langs"] == {"hu": 1, "la": 1}


def test_an_explicit_source_language_reports_nothing(tmp_path):
    src = tmp_path / "rec.xml"
    src.write_bytes(_META)
    doc, buffer = _Doc(), io.StringIO()
    process_metadata_xml(
        src, tmp_path / "o.xml", _XPATHS, _Lindat(), "cs", "en", csv_writer=csv.writer(buffer), doc=doc
    )
    assert set(_rows(buffer).values()) == {STATUS_OK}
    assert "unsupported_source_langs" not in doc.blocks["translations"]


def test_a_backend_that_translates_latin_reports_nothing(tmp_path):
    """EuroLLM or MADLAD (`ct2`) list `la` and `hu`: then the detection is simply used."""
    translator = _Lindat()
    translator.supported_models = None
    translator.supported_languages = lambda: ["cs", "de", "la", "hu", "en"]
    _, doc, rows = _metadata(tmp_path, translator)
    assert set(rows.values()) == {STATUS_OK}
    assert dict(translator.calls)[LATIN] == "la" and dict(translator.calls)[HUNGARIAN] == "hu"
    assert "unsupported_source_langs" not in doc.blocks["translations"]


ALTO_NS = "http://www.loc.gov/standards/alto/ns-v3#"


def _block(block_id, text):
    words = "".join(f'<String CONTENT="{w}" HPOS="{i}" WIDTH="9"/>' for i, w in enumerate(text.split()))
    return f'<TextBlock ID="{block_id}"><TextLine ID="{block_id}L1">{words}</TextLine></TextBlock>'


_ALTO = f"""<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="{ALTO_NS}"><Layout><Page ID="P1"><PrintSpace>
{_block("B1", CZECH)}{_block("B2", LATIN)}{_block("B3", HUNGARIAN)}{_block("B4", GERMAN)}
</PrintSpace></Page></Layout></alto>
""".encode()


def test_alto_blocks_in_latin_and_hungarian_are_reported_line_by_line(tmp_path):
    src = tmp_path / "doc.alto.xml"
    src.write_bytes(_ALTO)
    translator, doc, buffer = _Lindat(), _Doc(), io.StringIO()
    process_alto_xml(
        src,
        tmp_path / "out.xml",
        translator,
        "auto",
        "en",
        csv_writer=csv.writer(buffer),
        identifier=_Identifier(),
        doc=doc,
    )
    statuses = {row[2]: row[5] for row in csv.reader(io.StringIO(buffer.getvalue()))}
    assert statuses == {
        "B1L1": STATUS_OK,
        "B2L1": STATUS_UNSUPPORTED_LANG,
        "B3L1": STATUS_UNSUPPORTED_LANG,
        "B4L1": STATUS_OK,
    }
    assert doc.blocks["translations"]["unsupported_source_langs"] == {"hu": 1, "la": 1}
    out = etree.parse(str(tmp_path / "out.xml")).getroot()
    latin = [s.get("CONTENT") for s in out.iter(f"{{{ALTO_NS}}}String") if s.getparent().get("ID") == "B2L1"]
    assert latin[0].startswith("EN[cs]:"), "translated from the fallback, as before"


def test_an_untrustworthy_guess_is_not_reported(tmp_path):
    """Below the threshold, an exotic label, or too short to judge: the #46 fallback, unreported."""
    from processors.language import resolve_source_language

    policy = SourceLanguagePolicy(default="cs", allowed=frozenset({"cs", "de", "en"}))

    class _Fixed:
        def __init__(self, answer):
            self.answer = answer

        def candidates(self, text, k=5, max_chars=2000):
            return list(self.answer)

    for answer, text in (
        ([("la", 0.3)], LATIN),
        ([("yue", 0.95)], LATIN),
        ([("la", 0.97)], "Fossa est"),
    ):
        assert resolve_source_language(_Fixed(answer), text, policy, context="cs").unsupported is None


# ── the service ─────────────────────────────────────────────────────────────────────────────


def test_the_service_returns_the_count_in_the_record(monkeypatch):
    from fastapi.testclient import TestClient

    from service.api import app

    translator = _Lindat()
    translator.license_components = MagicMock(return_value=["lindat_cubbitt"])
    seed = {"doc_id": "C-TEST", "source": {"sha512": "c" * 128, "filename": "rec.xml", "media_type": "application/xml"}}
    models = {"translator": translator, "identifier": _Identifier(), "xpaths_list": _XPATHS}
    with patch("service.api.models", models):
        response = TestClient(app).post(
            "/translate?source_lang=auto&target_lang=en",
            files={
                "file": ("rec.xml", _META, "application/xml"),
                "document_json": ("seed.document.json", json.dumps(seed).encode(), "application/json"),
            },
            data={"is_alto": "false", "response_format": "json"},
        )
    assert response.status_code == 200, response.text[:400]
    translations = response.json()["document_json"]["translations"]
    assert translations["unsupported_source_langs"] == {"hu": 1, "la": 1}
    assert translations["detected_source_lang"] == "cs"
