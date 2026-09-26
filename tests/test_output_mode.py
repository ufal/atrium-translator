"""tests/test_output_mode.py — the replace/append switch and the batch-fallback counter.

Issue #46 asked the tool's central open question: given an XML file, should the
translation REPLACE the source-language field or be APPENDED beside it? This file
covers the switch that makes the question answerable on real data instead of in the
abstract, plus the two defects found while designing it.

What is asserted here, and why each assertion exists:

* **Replace stays byte-for-byte what it was.** `--output-mode` defaults to replace, so
  every existing caller, the shipped samples and the Dockerfile's batch CMD are
  unaffected. A switch that quietly changed the default would be a far worse bug than
  the one it fixes.
* **Append keeps the source.** The entire point. `agent_dev_logs/digests/46.digest.md`
  measured what replace costs: on the shipped ALTO sample, 224 blanked and 304
  over-filled `String` boxes out of 7229, none resized, and the Czech recoverable only
  from a sidecar CSV.
* **Append is idempotent.** Replace mode re-translates English into English on a second
  pass with nothing to notice it. Append can do better, because the `xml:lang` marker
  that makes the output readable also makes the re-run detectable.
* **ALTO append keeps the source.** Every `String` keeps its `CONTENT` and geometry and
  gains `<ALTERNATIVE PURPOSE="translation:<tgt>">` — ALTO's own element for "an
  alternative for the word" — carrying exactly what replace mode would have written
  into that box. The block keeps its source-language label; the `String` inventory
  stays 1:1; a second pass skips blocks that already carry a translation. (This
  supersedes the first design, which labelled blocks `LANG=<tgt>` and overwrote
  `CONTENT` — i.e. replace with a label, losing the Czech from the ALTO.)
* **Replace relabels what is already labelled.** Scanner ALTO arrives with
  `TextBlock LANG="cs"`; replace used to leave that on English text.
* **The batch fallback is counted.** It used to degrade from ~2 calls per page to one
  per block plus one per line behind `except Exception: pass`, changing wall-clock by
  an order of magnitude and the output not at all.
"""

from __future__ import annotations

import logging

import pytest
from lxml import etree

from utils import (
    DEFAULT_OUTPUT_MODE,
    OUTPUT_MODE_APPEND,
    OUTPUT_MODE_REPLACE,
    XML_LANG,
    BatchFallbackCounter,
    normalize_output_mode,
    process_alto_xml,
    process_metadata_xml,
)

ALTO_NS = "http://www.loc.gov/standards/alto/ns-v4#"

AMCR_NS = "https://api.aiscr.cz/schema/amcr/2.2/"

# Shaped like the real records in data_samples/my_documents/: a free-text field with
# no xml:lang (what the tool translates) beside a controlled-vocabulary field that
# carries one (what it deliberately leaves alone).
_METADATA_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<amcr:amcr xmlns:amcr="{AMCR_NS}">
  <amcr:lokalita>
    <amcr:okres xml:lang="cs" id="ruian-3210">Praha-zapad</amcr:okres>
    <amcr:chranene_udaje>
      <amcr:nazev>Davle - kultovni areal</amcr:nazev>
      <amcr:popis>Benediktinsky klaster.</amcr:popis>
    </amcr:chranene_udaje>
  </amcr:lokalita>
</amcr:amcr>
""".encode()

_XPATHS = [
    "//amcr:amcr/amcr:lokalita/amcr:chranene_udaje/amcr:nazev",
    "//amcr:amcr/amcr:lokalita/amcr:chranene_udaje/amcr:popis",
]

_ALTO_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#">
  <Layout>
    <Page ID="P1" HEIGHT="100" WIDTH="100">
      <PrintSpace>
        <TextBlock ID="B1">
          <TextLine ID="L1">
            <String CONTENT="Zachranny" HEIGHT="10" WIDTH="50" VPOS="1" HPOS="1"/>
            <String CONTENT="vyzkum" HEIGHT="10" WIDTH="40" VPOS="1" HPOS="60"/>
          </TextLine>
          <!-- Two lines, deliberately: page-level batching joins a block's lines with
               "\n" into ONE request, so a single-line fixture can never exercise the
               line-count-mismatch path that the fallback counter exists to measure. -->
          <TextLine ID="L2">
            <String CONTENT="Vrani" HEIGHT="10" WIDTH="30" VPOS="20" HPOS="1"/>
            <String CONTENT="kanalizace" HEIGHT="10" WIDTH="45" VPOS="20" HPOS="40"/>
          </TextLine>
        </TextBlock>
      </PrintSpace>
    </Page>
  </Layout>
</alto>
"""


class _StubTranslator:
    """Deterministic stand-in: prefixes EN: so a translation is identifiable."""

    name = "lindat"
    vocabulary: dict = {}
    protected_count = 0

    def __init__(self):
        self.calls: list[str] = []

    def translate(self, text, src_lang, tgt_lang="en"):
        self.calls.append(text)
        return "\n".join(f"EN:{line}" for line in text.split("\n"))

    def reset_protected_count(self):
        self.protected_count = 0


def _write(tmp_path, name, payload):
    path = tmp_path / name
    path.write_bytes(payload)
    return path


def _parse(path):
    return etree.parse(str(path)).getroot()


def _find(root, localname):
    # isinstance guard: root.iter() also yields comments/PIs, whose .tag is a callable
    # that etree.QName rejects. The ALTO fixture below deliberately contains a comment.
    return [e for e in root.iter() if isinstance(e.tag, str) and etree.QName(e).localname == localname]


# ──────────────────────────────────────────────────────────────────────────────
# normalize_output_mode
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, OUTPUT_MODE_REPLACE),
        ("", OUTPUT_MODE_REPLACE),
        ("   ", OUTPUT_MODE_REPLACE),
        ("replace", OUTPUT_MODE_REPLACE),
        ("APPEND", OUTPUT_MODE_APPEND),
        ("  Append  ", OUTPUT_MODE_APPEND),
    ],
)
def test_normalize_output_mode_accepts_the_documented_spellings(raw, expected):
    assert normalize_output_mode(raw) == expected


def test_normalize_output_mode_degrades_loudly_rather_than_raising(caplog):
    """A typo in config.txt or an env var must not take a batch run down mid-corpus.

    The effective mode is recorded in paradata and in the document record either way,
    so degrading is safe; degrading *silently* would not be.
    """
    with caplog.at_level(logging.WARNING, logger="utils"):
        assert normalize_output_mode("apend") == DEFAULT_OUTPUT_MODE
    assert "apend" in caplog.text


# ──────────────────────────────────────────────────────────────────────────────
# Metadata — the actual replace/append question
# ──────────────────────────────────────────────────────────────────────────────


def test_replace_mode_is_unchanged_and_is_the_default(tmp_path):
    """The regression floor: default behaviour must be identical to before the switch."""
    src = _write(tmp_path, "in.xml", _METADATA_XML)
    for mode in (None, OUTPUT_MODE_REPLACE):
        out = tmp_path / f"out-{mode}.xml"
        process_metadata_xml(
            src,
            out,
            _XPATHS,
            _StubTranslator(),
            "cs",
            "en",
            **({} if mode is None else {"output_mode": mode}),
        )
        root = _parse(out)
        nazev = _find(root, "nazev")
        assert len(nazev) == 1, "replace must not add elements"
        assert nazev[0].text == "EN:Davle - kultovni areal"
        assert nazev[0].get(XML_LANG) is None, "replace must not invent language markers"


def test_append_keeps_the_source_and_labels_both_halves(tmp_path):
    """The headline behaviour: the Czech survives, and the pair is self-describing."""
    src = _write(tmp_path, "in.xml", _METADATA_XML)
    out = tmp_path / "out.xml"
    process_metadata_xml(src, out, _XPATHS, _StubTranslator(), "cs", "en", output_mode=OUTPUT_MODE_APPEND)

    root = _parse(out)
    nazev = _find(root, "nazev")
    assert len(nazev) == 2, "append must emit a sibling, not overwrite"

    czech, english = nazev
    assert czech.text == "Davle - kultovni areal", "the source text must be untouched"
    assert czech.get(XML_LANG) == "cs", "the source half must be labelled too"
    assert english.text == "EN:Davle - kultovni areal"
    assert english.get(XML_LANG) == "en"
    assert english.tag == czech.tag, "same tag, distinguished by xml:lang (AMCR's own shape)"

    # The sibling lands immediately after its source, not at the end of the parent.
    assert czech.getnext() is english


def test_append_copies_attributes_so_the_translation_stays_attributable(tmp_path):
    """An `id="HES-…"` identifies the CONCEPT, so the English label belongs under it."""
    payload = f"""<?xml version="1.0" encoding="utf-8"?>
<amcr:amcr xmlns:amcr="{AMCR_NS}">
  <amcr:popis id="HES-001122">Benediktinsky klaster.</amcr:popis>
</amcr:amcr>
""".encode()
    src = _write(tmp_path, "attr.xml", payload)
    out = tmp_path / "attr-out.xml"
    process_metadata_xml(
        src,
        out,
        ["//amcr:amcr/amcr:popis"],
        _StubTranslator(),
        "cs",
        "en",
        output_mode=OUTPUT_MODE_APPEND,
    )
    popis = _find(_parse(out), "popis")
    assert len(popis) == 2
    assert popis[1].get("id") == "HES-001122"
    assert popis[1].get(XML_LANG) == "en"


def test_append_is_idempotent(tmp_path):
    """Re-running must not stack a second English sibling, and must not re-translate.

    Replace mode has no equivalent guard — it re-translates English into English and
    nothing notices. Append earns one for free from the marker it already writes.
    """
    src = _write(tmp_path, "in.xml", _METADATA_XML)
    first = tmp_path / "pass1.xml"
    process_metadata_xml(src, first, _XPATHS, _StubTranslator(), "cs", "en", output_mode=OUTPUT_MODE_APPEND)

    second_translator = _StubTranslator()
    second = tmp_path / "pass2.xml"
    process_metadata_xml(first, second, _XPATHS, second_translator, "cs", "en", output_mode=OUTPUT_MODE_APPEND)

    assert second_translator.calls == [], "a second pass must not call the backend at all"
    root = _parse(second)
    assert len(_find(root, "nazev")) == 2, "no duplicate siblings on re-run"
    assert len(_find(root, "popis")) == 2


def test_append_leaves_untargeted_controlled_vocabulary_alone(tmp_path):
    """Only the XPath targets are touched; thesaurus-backed fields keep their own lang."""
    src = _write(tmp_path, "in.xml", _METADATA_XML)
    out = tmp_path / "out.xml"
    process_metadata_xml(src, out, _XPATHS, _StubTranslator(), "cs", "en", output_mode=OUTPUT_MODE_APPEND)
    okres = _find(_parse(out), "okres")
    assert len(okres) == 1
    assert okres[0].text == "Praha-zapad"
    assert okres[0].get(XML_LANG) == "cs"


def test_output_mode_reaches_the_document_record(tmp_path):
    """A consumer holding the artifact must be able to tell which contract produced it."""

    class _Doc:
        def __init__(self):
            self.blocks = {}

        def set_block(self, name, value):
            self.blocks[name] = value

    for mode in (OUTPUT_MODE_REPLACE, OUTPUT_MODE_APPEND):
        doc = _Doc()
        src = _write(tmp_path, f"rec-{mode}.xml", _METADATA_XML)
        process_metadata_xml(
            src,
            tmp_path / f"rec-out-{mode}.xml",
            _XPATHS,
            _StubTranslator(),
            "cs",
            "en",
            doc=doc,
            output_mode=mode,
        )
        assert doc.blocks["translations"]["output_mode"] == mode


# ──────────────────────────────────────────────────────────────────────────────
# ALTO — append keeps the source and adds ALTERNATIVEs; replace relabels
# ──────────────────────────────────────────────────────────────────────────────


def _alternatives(string_elem):
    return [c for c in string_elem if isinstance(c.tag, str) and etree.QName(c).localname == "ALTERNATIVE"]


def test_alto_append_keeps_content_and_adds_translation_alternatives(tmp_path):
    """The Czech stays in CONTENT; the English sits beside it, one ALTERNATIVE per box."""
    src = _write(tmp_path, "in.alto.xml", _ALTO_XML)
    source_strings = _find(_parse(src), "String")
    source_content = [s.get("CONTENT") for s in source_strings]

    out = tmp_path / "out.alto.xml"
    process_alto_xml(
        src,
        out,
        _StubTranslator(),
        "cs",
        "en",
        line_anchors=False,
        output_mode=OUTPUT_MODE_APPEND,
    )

    root = _parse(out)
    strings = _find(root, "String")
    assert len(strings) == len(source_strings), "ALTO must stay 1:1 with the source"
    assert [s.get("CONTENT") for s in strings] == source_content, "append must not touch CONTENT"
    for s in strings:
        assert s.get("HPOS") and s.get("WIDTH"), "geometry is untouched"

    alternatives = [_alternatives(s) for s in strings]
    assert all(len(a) == 1 for a in alternatives), "exactly one translation ALTERNATIVE per filled String"
    assert all(a[0].get("PURPOSE") == "translation:en" for a in alternatives)
    assert [a[0].text for a in alternatives] == ["EN:Zachranny", "vyzkum", "Vrani", "kanalizace"]
    # Serialised in the document's own namespace, not under an invented prefix.
    assert all(etree.QName(a[0]).namespace == ALTO_NS for a in alternatives)
    assert b"ns0:" not in out.read_bytes()

    blocks = _find(root, "TextBlock")
    assert blocks and all(b.get("LANG") == "cs" for b in blocks), "CONTENT is Czech, so the block says so"


def test_alto_append_is_idempotent(tmp_path):
    """A second append pass must not call the backend nor stack a second ALTERNATIVE."""
    src = _write(tmp_path, "in.alto.xml", _ALTO_XML)
    first = tmp_path / "pass1.alto.xml"
    process_alto_xml(src, first, _StubTranslator(), "cs", "en", output_mode=OUTPUT_MODE_APPEND)

    second_translator = _StubTranslator()
    second = tmp_path / "pass2.alto.xml"
    process_alto_xml(first, second, second_translator, "cs", "en", output_mode=OUTPUT_MODE_APPEND)

    assert second_translator.calls == [], "a translated block must not be sent again"
    assert first.read_bytes() == second.read_bytes()
    assert all(len(_alternatives(s)) == 1 for s in _find(_parse(second), "String"))


def test_alto_replace_mode_adds_no_language_attribute(tmp_path):
    """An unlabelled input stays unlabelled in replace mode."""
    src = _write(tmp_path, "in.alto.xml", _ALTO_XML)
    out = tmp_path / "out.alto.xml"
    process_alto_xml(src, out, _StubTranslator(), "cs", "en", line_anchors=False)
    assert all(b.get("LANG") is None for b in _find(_parse(out), "TextBlock"))


def test_alto_replace_relabels_an_existing_source_language(tmp_path):
    """ABBYY writes TextBlock LANG="cs"; once the CONTENT is English, the label must say so."""
    labelled = _ALTO_XML.replace(b'<TextBlock ID="B1">', b'<TextBlock ID="B1" LANG="cs">')
    src = _write(tmp_path, "in.alto.xml", labelled)
    out = tmp_path / "out.alto.xml"
    process_alto_xml(src, out, _StubTranslator(), "cs", "en", line_anchors=False)

    root = _parse(out)
    assert [b.get("LANG") for b in _find(root, "TextBlock")] == ["en"]
    assert _find(root, "String")[0].get("CONTENT") == "EN:Zachranny"
    assert all(not _alternatives(s) for s in _find(root, "String")), "replace adds no ALTERNATIVE"


# ──────────────────────────────────────────────────────────────────────────────
# Batch fallback counter
# ──────────────────────────────────────────────────────────────────────────────


def test_counter_reports_clean_batching():
    counter = BatchFallbackCounter()
    counter.batched = 3
    assert counter.fallbacks == 0
    assert counter.as_dict()["fallbacks"] == 0
    assert "3/3" in counter.summary()


def test_counter_separates_mismatch_from_transport_error():
    """The two degradations have different causes and different fixes, so they are counted apart."""
    counter = BatchFallbackCounter()
    counter.fallback_mismatch = 2
    counter.fallback_error = 1
    counter.fallback_implausible = 4
    counter.items_retried = 17
    assert counter.fallbacks == 7
    summary = counter.summary()
    assert "2 line-count mismatch" in summary
    assert "1 transport error" in summary
    assert "4 implausible reply" in summary
    assert "17 extra requests" in summary


def test_counter_reports_flagged_recovered_and_untranslated_segments():
    counter = BatchFallbackCounter()
    counter.segments_flagged = 3
    counter.segments_recovered = 2
    counter.segments_untranslated = 1
    counter.anchors_flagged = 5
    counter.anchors_recovered = 5
    counter.anchors_approximated = 4
    assert counter.needs_attention
    summary = counter.summary()
    assert "3 segment(s) and 5 line anchor(s) flagged" in summary
    assert "2 segment(s) and 5 anchor(s) recovered" in summary
    assert "1 segment(s) left untranslated" in summary
    assert "4 line(s) placed by source word count" in summary
    assert counter.as_dict()["segments_untranslated"] == 1


def test_alto_run_counts_a_line_count_mismatch(tmp_path, caplog):
    """A backend that collapses newlines forces per-item retries — and now says so."""

    class _CollapsingTranslator(_StubTranslator):
        def translate(self, text, src_lang, tgt_lang="en"):
            self.calls.append(text)
            # Answer a multi-line request with ONE line: the exact shape that used to
            # fall through `except Exception: pass` unnoticed.
            return "EN:collapsed"

    src = _write(tmp_path, "in.alto.xml", _ALTO_XML)
    translator = _CollapsingTranslator()
    with caplog.at_level(logging.WARNING, logger="utils"):
        process_alto_xml(src, tmp_path / "out.alto.xml", translator, "cs", "en", line_anchors=True)

    assert "fell back to per-item requests" in caplog.text
    assert "line-count mismatch" in caplog.text


def test_alto_run_counts_a_transport_error(tmp_path, caplog):
    """A raised backend error must be logged with its reason, not swallowed."""

    class _FlakyTranslator(_StubTranslator):
        def __init__(self):
            super().__init__()
            self.first = True

        def translate(self, text, src_lang, tgt_lang="en"):
            if self.first and "\n" in text:
                self.first = False
                raise RuntimeError("upstream 503")
            return super().translate(text, src_lang, tgt_lang)

    src = _write(tmp_path, "in.alto.xml", _ALTO_XML)
    with caplog.at_level(logging.WARNING, logger="utils"):
        process_alto_xml(src, tmp_path / "out.alto.xml", _FlakyTranslator(), "cs", "en", line_anchors=True)

    assert "upstream 503" in caplog.text, "the swallowed reason is now reported"
    assert "transport error" in caplog.text
