"""tests/test_degeneration.py — degenerate backend output: detect, re-request, flag, re-run, keep source.

The 2026-09-26 refresh of `data_samples/` (commit 8522167, "TODO: fix alto alignment")
is the specification for this file. Against the public LINDAT endpoint roughly a third
of all replies came back HTTP 200 as one Czech word repeated up to ~150 times:

    Záchranný archeologický výzkum  →  pravidla pravidla pravidla … (×150)

for headings and whole sentences alike, on the ALTO and the metadata path alike, and
nondeterministically — the same field was garbage in one run and correct in the next.
Nothing looked at reply content, so the garbage reached the XML and the QA log, and,
through the page-batched ALTO line anchors (never logged), the word-to-box alignment
of every block on the page: 2489 blanked `String`s where the June sample had 224.

What is asserted here, layer by layer:

* **The detector** (`processors/quality.py`) flags the observed shapes and passes real
  translations — including the odd-but-genuine ones from the June log.
* **The LINDAT client** re-requests a degenerate reply and gives up with the per-segment
  `DegenerateTranslationError`, never with garbage.
* **The batch mapper** trusts a batched reply only if every item is plausible.
* **The documents** — a flagged segment is re-run at the end of the same document and
  logged `rerun`; one that never recovers keeps its source and is logged `untranslated`;
  the CSV is in document order either way.
"""

from __future__ import annotations

import csv
import io
import logging
from unittest.mock import MagicMock, patch

import pytest
from lxml import etree

import processors.translator as translator_module
from processors.ct2_translator import CT2Translator
from processors.llm_translator import LLMTranslator
from processors.quality import degeneration_reason
from processors.translator import DegenerateTranslationError, LindatTranslator, TranslationError
from utils import (
    OUTPUT_MODE_APPEND,
    STATUS_APPROX,
    STATUS_OK,
    STATUS_RERUN,
    STATUS_UNTRANSLATED,
    BatchFallbackCounter,
    _translate_items,
    process_alto_xml,
    process_metadata_xml,
)

LOOP = " ".join(["pravidla"] * 150)

# ──────────────────────────────────────────────────────────────────────────────
# The detector
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "source,translated,fragment",
    [
        ("Záchranný archeologický výzkum", LOOP, "runaway length"),
        ("Vojtěch Marek", "pravidla pravidla", "repetition loop"),
        ("Vraní, ČOV a kanalizace", "pravidla pravidla pravidla pravidla", "repetition loop"),
        ("Popis nálezu", "the rules of the rules of the rules of the rules of", "repetition loop"),
        ("Nálezová zpráva", "", "empty translation"),
        ("Nálezová zpráva", "   ", "empty translation"),
        (
            "Předložená práce vznikla tvůrčím zpracováním výsledků archeologického výzkumu a je tedy chráněna",
            "Yes.",
            "truncated",
        ),
        (
            "a b c d e f g h i j k l",
            "rules x rules y rules z rules w rules v rules",
            "repetition loop",
        ),
    ],
)
def test_detector_flags_the_observed_failure_shapes(source, translated, fragment):
    reason = degeneration_reason(source, translated)
    assert reason is not None and fragment in reason


@pytest.mark.parametrize(
    "source,translated",
    [
        # Real June v0.5.0 LINDAT output, odd ones included — the guard must not be a style judge.
        ("Vedoucí archeologického výzkumu:", "Head of Archaeological Research:"),
        ("Vraní 37", "Crow Crow 37"),
        ("parc. č. 41/1, 41/5, 41/11, 42,137/1", "parc. Nos. 41/1, 41/5, 41/11, 42,137/1"),
        ("Mgr. Bohumil Sýkora", "Mgr. Bohumil Sýkora"),
        ("2014", "2014"),
        ("zákona).", "law)."),
        # Repetition that is in the source is not a loop.
        ("Úvod . . . . . . . . . . 3", "Introduction . . . . . . . . . . 3"),
        ("0 0 0 0 0 0", "0 0 0 0 0 0"),
        ("kostel kostel kostel kostel", "church church church church"),
        # A one-word vocabulary term restored as a phrase.
        ("kostel", "parish church of St. James the Greater"),
        # Punctuation-only sources have no wording to loop on (found by the offline
        # end-to-end run: a "— . ." line echoed back token for token).
        ("•", ""),
        ("— . .", "—~mt .~mt .~mt"),
        ("", ""),
    ],
)
def test_detector_passes_real_translations(source, translated):
    assert degeneration_reason(source, translated) is None


# ──────────────────────────────────────────────────────────────────────────────
# LINDAT: re-request, then give up per segment
# ──────────────────────────────────────────────────────────────────────────────


def _http(text):
    response = MagicMock()
    response.status_code = 200
    response.text = text
    return response


@pytest.fixture
def lindat():
    with patch.object(LindatTranslator, "_fetch_models", return_value=["cs-en"]):
        t = LindatTranslator()
    t._throttle = MagicMock()
    return t


@patch("processors.translator.requests.post")
def test_lindat_re_requests_a_degenerate_reply(mock_post, lindat):
    mock_post.side_effect = [_http(LOOP), _http("Rescue archaeological research")]
    assert lindat.translate("Záchranný archeologický výzkum", "cs", "en") == "Rescue archaeological research"
    assert mock_post.call_count == 2


@patch("processors.translator.requests.post")
def test_lindat_gives_up_with_the_per_segment_error(mock_post, lindat):
    mock_post.return_value = _http(LOOP)
    with pytest.raises(DegenerateTranslationError, match="degenerate output"):
        lindat.translate("Záchranný archeologický výzkum", "cs", "en")
    assert mock_post.call_count == 1 + translator_module._GUARD_RETRIES
    # Callers that only know the base class still fail loudly.
    assert issubclass(DegenerateTranslationError, TranslationError)


@patch("processors.translator.requests.post")
def test_lindat_checks_every_line_of_a_batched_reply(mock_post, lindat):
    """One looping line among clean ones is enough to re-request the batch."""
    source = "Nálezová zpráva\nVojtěch Marek\nObjednatel:"
    mock_post.side_effect = [
        _http("Excavation report\npravidla pravidla\nOrderer:"),
        _http("Excavation report\nVojtěch Marek\nOrderer:"),
    ]
    assert lindat.translate(source, "cs", "en") == "Excavation report\nVojtěch Marek\nOrderer:"
    assert mock_post.call_count == 2


def test_placeholder_scrub_never_eats_a_line_break():
    """`\\s+` before punctuation used to merge two batch items into one line."""
    cleaned = LindatTranslator._scrub_placeholder_fragments("first Xtermzzz7z\n, second")
    assert cleaned.count("\n") == 1


def test_fuzzy_sentinel_match_does_not_span_lines():
    fuzzy = LindatTranslator._tag_fuzzy_re(0)
    assert fuzzy.search("X t e r m z z z 0 z") is not None
    assert fuzzy.search("Xtermzzz0\nz") is None


# ──────────────────────────────────────────────────────────────────────────────
# LLM / CT2 guards
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("guard", [LLMTranslator._guard_output, CT2Translator._guard])
def test_backend_guards_reject_a_loop_the_ratio_cannot_see(guard):
    # 29 chars for a 37-char source: well inside the length ratio, still a loop.
    with pytest.raises(DegenerateTranslationError, match="repetition loop"):
        guard("Záchranný archeologický výzkum v obci", "rules rules rules rules rules")


@pytest.mark.parametrize("guard", [LLMTranslator._guard_output, CT2Translator._guard])
def test_backend_guards_raise_the_per_segment_error_for_ratio_failures(guard):
    with pytest.raises(DegenerateTranslationError, match="length ratio"):
        guard("Stručný popis nálezu.", "word " * 200)


# ──────────────────────────────────────────────────────────────────────────────
# The batch mapper
# ──────────────────────────────────────────────────────────────────────────────


class _Backend:
    """Test double: `EN:`-prefixes each line; subclasses decide when to misbehave."""

    name = "lindat"
    vocabulary: dict = {}
    protected_count = 0

    def __init__(self):
        self.calls: list[str] = []

    def reset_protected_count(self):
        self.protected_count = 0

    def good(self, text):
        return "\n".join(f"EN:{line}" if line.strip() else line for line in text.split("\n"))

    def translate(self, text, src_lang, tgt_lang="en"):
        self.calls.append(text)
        return self.good(text)


class _ShiftingBackend(_Backend):
    """Keeps the line count of a batch but piles line 1+2 into slot 1 and blanks slot 2."""

    def translate(self, text, src_lang, tgt_lang="en"):
        self.calls.append(text)
        lines = text.split("\n")
        if len(lines) >= 3:
            return "\n".join([f"EN:{lines[0]} EN:{lines[1]}", ""] + [f"EN:{x}" for x in lines[2:]])
        return self.good(text)


class _AlwaysLooping(_Backend):
    def translate(self, text, src_lang, tgt_lang="en"):
        self.calls.append(text)
        return "\n".join(LOOP for _ in text.split("\n"))


class _FlakyBackend(_Backend):
    """Loops on the first `fail_times` requests for each distinct text, then recovers.

    That is the observed failure: nondeterministic, and cured by asking again later.
    """

    def __init__(self, fail_times=1, only=None):
        super().__init__()
        self.fail_times = fail_times
        self.only = only
        self.seen: dict[str, int] = {}

    def translate(self, text, src_lang, tgt_lang="en"):
        self.calls.append(text)
        self.seen[text] = self.seen.get(text, 0) + 1
        eligible = self.only is None or self.only(text)
        if eligible and self.seen[text] <= self.fail_times:
            return "\n".join(LOOP for _ in text.split("\n"))
        return self.good(text)


def test_batch_with_a_shifted_mapping_is_not_trusted():
    counter = BatchFallbackCounter()
    texts = ["jedna dva", "tri ctyri", "pet"]
    results, failed = _translate_items(_ShiftingBackend(), texts, "cs", "en", counter)
    assert results == ["EN:jedna dva", "EN:tri ctyri", "EN:pet"]
    assert failed == set()
    assert counter.fallback_implausible == 1 and counter.items_retried == 3


def test_batch_items_that_stay_degenerate_are_returned_as_failed():
    counter = BatchFallbackCounter()
    results, failed = _translate_items(_AlwaysLooping(), ["jedna dva", "", "tri"], "cs", "en", counter)
    assert results == ["", "", ""]
    assert failed == {0, 2}, "blank inputs are never flagged"


def test_batch_degenerate_error_falls_back_per_item():
    class _RaisesOnBatch(_Backend):
        def translate(self, text, src_lang, tgt_lang="en"):
            self.calls.append(text)
            if "\n" in text:
                raise DegenerateTranslationError("loop")
            return self.good(text)

    counter = BatchFallbackCounter()
    results, failed = _translate_items(_RaisesOnBatch(), ["jedna", "dva"], "cs", "en", counter)
    assert results == ["EN:jedna", "EN:dva"] and not failed
    assert counter.fallback_implausible == 1


def test_transport_failure_in_the_per_item_loop_still_skips_the_file():
    class _Down(_Backend):
        def translate(self, text, src_lang, tgt_lang="en"):
            raise TranslationError("HTTP 503 after 5 attempts")

    with pytest.raises(TranslationError, match="503"):
        _translate_items(_Down(), ["jedna", "dva"], "cs", "en", BatchFallbackCounter())


# ──────────────────────────────────────────────────────────────────────────────
# ALTO documents
# ──────────────────────────────────────────────────────────────────────────────

ALTO_NS = "http://www.loc.gov/standards/alto/ns-v3#"

_ALTO = f"""<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="{ALTO_NS}"><Layout><Page ID="P1" WIDTH="1000" HEIGHT="1000"><PrintSpace>
<TextBlock ID="B1" LANG="cs">
  <TextLine ID="B1L1"><String CONTENT="Záchranný" HPOS="1" WIDTH="9"/><String CONTENT="archeologický" HPOS="11" WIDTH="9"/></TextLine>
  <TextLine ID="B1L2"><String CONTENT="výzkum" HPOS="1" WIDTH="9"/><String CONTENT="Vraní" HPOS="11" WIDTH="9"/></TextLine>
  <TextLine ID="B1L3"><String CONTENT="ČOV" HPOS="1" WIDTH="9"/><String CONTENT="kanalizace" HPOS="11" WIDTH="9"/></TextLine>
</TextBlock>
<TextBlock ID="B2" LANG="cs">
  <TextLine ID="B2L1"><String CONTENT="Nálezová" HPOS="1" WIDTH="9"/><String CONTENT="zpráva" HPOS="11" WIDTH="9"/></TextLine>
  <TextLine ID="B2L2"><String CONTENT="Vojtěch" HPOS="1" WIDTH="9"/><String CONTENT="Marek" HPOS="11" WIDTH="9"/></TextLine>
</TextBlock>
</PrintSpace></Page></Layout></alto>
""".encode()


@pytest.fixture(autouse=True)
def _no_cooldown(monkeypatch):
    monkeypatch.setenv("TRANSLATION_RERUN_DELAY_S", "0")
    monkeypatch.setenv("TRANSLATION_RERUN_ROUNDS", "1")


_BLOCK_TEXTS = {"Záchranný archeologický výzkum Vraní ČOV kanalizace", "Nálezová zpráva Vojtěch Marek"}


def _is_block_request(text):
    """True for a block request, single or page-batched (every line is a whole block)."""
    return all(line in _BLOCK_TEXTS for line in text.split("\n"))


def _run_alto(tmp_path, backend, **kwargs):
    src = tmp_path / "doc.alto.xml"
    src.write_bytes(_ALTO)
    out = tmp_path / "doc_en.alto.xml"
    buffer = io.StringIO()
    process_alto_xml(src, out, backend, "cs", "en", csv_writer=csv.writer(buffer), **kwargs)
    root = etree.parse(str(out)).getroot()
    strings = root.findall(f".//{{{ALTO_NS}}}String")
    rows = list(csv.reader(io.StringIO(buffer.getvalue())))
    return root, strings, rows


def test_alto_healthy_backend_is_ok_throughout(tmp_path):
    _, strings, rows = _run_alto(tmp_path, _Backend())
    assert {r[5] for r in rows} == {STATUS_OK}
    assert all(s.get("CONTENT") for s in strings), "no String is blanked"


def test_alto_flaky_backend_is_recovered_by_the_end_of_document_rerun(tmp_path, caplog):
    # Every block request loops the first time it is seen — in the batch, and again
    # in the per-item fallback — so both blocks are flagged and only the re-run helps.
    backend = _FlakyBackend(fail_times=1, only=_is_block_request)
    with caplog.at_level(logging.WARNING, logger="utils"):
        root, strings, rows = _run_alto(tmp_path, backend)

    assert b"pravidla" not in etree.tostring(root)
    assert all(s.get("CONTENT") for s in strings)
    assert [s.get("CONTENT") for s in strings[:2]] == ["EN:Záchranný", "archeologický"]
    # Document order, even though every block was finalised after the re-run.
    assert [r[2] for r in rows] == ["B1L1", "B1L2", "B1L3", "B2L1", "B2L2"]
    assert {r[5] for r in rows} == {STATUS_RERUN}
    assert "flagged during processing" in caplog.text
    assert "0 segment(s) left untranslated" not in caplog.text


def test_alto_block_that_never_recovers_keeps_its_source(tmp_path, caplog):
    source_content = [s.get("CONTENT") for s in etree.fromstring(_ALTO).iter(f"{{{ALTO_NS}}}String")]
    with caplog.at_level(logging.WARNING, logger="utils"):
        root, strings, rows = _run_alto(tmp_path, _AlwaysLooping())

    assert [s.get("CONTENT") for s in strings] == source_content, "never blanked, never garbage"
    assert [b.get("LANG") for b in root.iter(f"{{{ALTO_NS}}}TextBlock")] == ["cs", "cs"], "still Czech"
    assert all(r[4] == "" and r[5] == STATUS_UNTRANSLATED for r in rows)
    assert "left untranslated (source text kept)" in caplog.text


def test_alto_rerun_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSLATION_RERUN_ROUNDS", "0")
    backend = _FlakyBackend(fail_times=1, only=_is_block_request)
    _, _, rows = _run_alto(tmp_path, backend)
    assert {r[5] for r in rows} == {STATUS_UNTRANSLATED}


def test_alto_recovered_block_with_unusable_anchors_reports_the_alignment(tmp_path):
    """`approx_alignment` outranks `rerun`: the reviewer needs to know the split is by word count."""
    _, strings, rows = _run_alto(tmp_path, _FlakyBackend(fail_times=1))
    assert all(s.get("CONTENT") for s in strings)
    assert {r[5] for r in rows} == {STATUS_APPROX}


def test_alto_flagged_anchor_is_recovered_by_the_rerun(tmp_path):
    # Each anchor loops in the page batch and in its first per-item request, and
    # succeeds when the re-run asks again.
    backend = _FlakyBackend(fail_times=1, only=lambda text: not _is_block_request(text))
    _, strings, rows = _run_alto(tmp_path, backend)
    assert all(s.get("CONTENT") for s in strings)
    assert {r[5] for r in rows} == {STATUS_RERUN}


def test_alto_unusable_anchors_fall_back_to_word_count_placement(tmp_path):
    """Blocks fine, every line anchor looping — the old aligner would starve or flood lines."""
    # Block requests (single or batched) succeed; every anchor request — batched per
    # page or one line at a time — loops.
    backend = _FlakyBackend(fail_times=99, only=lambda text: not _is_block_request(text))

    _, strings, rows = _run_alto(tmp_path, backend)
    assert all(s.get("CONTENT") for s in strings), "every String keeps a word"
    assert [len(r[4].split()) for r in rows] == [2, 2, 2, 2, 2]
    assert {r[5] for r in rows} == {STATUS_APPROX}


def test_alto_append_rerun_writes_alternatives_not_content(tmp_path):
    backend = _FlakyBackend(fail_times=1, only=_is_block_request)
    root, strings, rows = _run_alto(tmp_path, backend, output_mode=OUTPUT_MODE_APPEND)
    assert strings[0].get("CONTENT") == "Záchranný"
    alternative = strings[0].find(f"{{{ALTO_NS}}}ALTERNATIVE")
    assert alternative is not None and alternative.text == "EN:Záchranný"
    assert alternative.get("PURPOSE") == "translation:en"
    assert {r[5] for r in rows} == {STATUS_RERUN}


def test_alto_append_untranslated_block_gets_no_alternative(tmp_path):
    root, strings, _ = _run_alto(tmp_path, _AlwaysLooping(), output_mode=OUTPUT_MODE_APPEND)
    assert root.find(f".//{{{ALTO_NS}}}ALTERNATIVE") is None


# ──────────────────────────────────────────────────────────────────────────────
# Metadata documents
# ──────────────────────────────────────────────────────────────────────────────

AMCR_NS = "https://api.aiscr.cz/schema/amcr/2.2/"
_META = f"""<?xml version="1.0" encoding="utf-8"?>
<amcr:amcr xmlns:amcr="{AMCR_NS}">
  <amcr:nazev>Davle - kultovní areál 1</amcr:nazev>
  <amcr:popis>Benediktinský klášter vznikl kolem r. 1000.</amcr:popis>
</amcr:amcr>
""".encode()
_XPATHS = ["//amcr:amcr/amcr:nazev", "//amcr:amcr/amcr:popis"]


def _run_meta(tmp_path, backend, **kwargs):
    src = tmp_path / "rec.xml"
    src.write_bytes(_META)
    out = tmp_path / "rec_en.xml"
    buffer = io.StringIO()
    process_metadata_xml(src, out, _XPATHS, backend, "cs", "en", csv_writer=csv.writer(buffer), **kwargs)
    root = etree.parse(str(out)).getroot()
    rows = list(csv.reader(io.StringIO(buffer.getvalue())))
    return root, rows


def _texts(root, name):
    return [e.text for e in root.iter(f"{{{AMCR_NS}}}{name}")]


def test_metadata_flagged_field_is_recovered_by_the_rerun(tmp_path):
    backend = _FlakyBackend(fail_times=1, only=lambda text: text.startswith("Davle"))
    root, rows = _run_meta(tmp_path, backend)
    assert _texts(root, "nazev") == ["EN:Davle - kultovní areál 1"]
    assert [r[5] for r in rows] == [STATUS_RERUN, STATUS_OK]
    assert [r[2] for r in rows] == _XPATHS, "rows stay in document order"


def test_metadata_field_that_never_recovers_is_left_untouched(tmp_path):
    backend = _FlakyBackend(fail_times=99, only=lambda text: text.startswith("Davle"))
    root, rows = _run_meta(tmp_path, backend)
    assert _texts(root, "nazev") == ["Davle - kultovní areál 1"]
    assert _texts(root, "popis") == ["EN:Benediktinský klášter vznikl kolem r. 1000."]
    assert rows[0][4] == "" and rows[0][5] == STATUS_UNTRANSLATED


def test_metadata_append_adds_no_sibling_for_an_untranslated_field(tmp_path):
    backend = _FlakyBackend(fail_times=99, only=lambda text: text.startswith("Davle"))
    root, _ = _run_meta(tmp_path, backend, output_mode=OUTPUT_MODE_APPEND)
    assert _texts(root, "nazev") == ["Davle - kultovní areál 1"], "no English sibling carrying garbage"
    assert len(_texts(root, "popis")) == 2


# ──────────────────────────────────────────────────────────────────────────────
# Re-request timing and the per-document count
# ──────────────────────────────────────────────────────────────────────────────
#
# Sequential runs showed every batch's first attempt degenerate and its identical
# re-request succeed — one broken replica behind a round-robin balancer. Waiting
# does not help there; reaching the next replica does. So the first re-request is
# immediate, back-off starts with the second, and the count is reported once per
# document instead of as one warning per reply.


@patch("processors.translator.requests.post")
def test_the_first_re_request_is_immediate(mock_post, lindat):
    mock_post.side_effect = [_http(LOOP), _http("Rescue archaeological research")]
    with patch("processors.translator.time.sleep") as sleep:
        lindat.translate("Záchranný archeologický výzkum", "cs", "en")
    sleep.assert_not_called()
    assert lindat.degenerate_replies == 1


@patch("processors.translator.requests.post")
def test_back_off_starts_with_the_second_re_request(mock_post, lindat):
    mock_post.side_effect = [_http(LOOP), _http(LOOP), _http("Rescue archaeological research")]
    with patch("processors.translator.time.sleep") as sleep:
        lindat.translate("Záchranný archeologický výzkum", "cs", "en")
    assert sleep.call_count == 1
    assert lindat.degenerate_replies == 2
    lindat.reset_degenerate_count()
    assert lindat.degenerate_replies == 0


@patch("processors.translator.requests.post")
def test_a_recovered_reply_is_info_and_a_repeated_one_a_warning(mock_post, lindat, caplog):
    mock_post.side_effect = [_http(LOOP), _http(LOOP), _http("Rescue archaeological research")]
    with caplog.at_level(logging.INFO, logger="processors.translator"):
        lindat.translate("Záchranný archeologický výzkum", "cs", "en")
    levels = [r.levelno for r in caplog.records if "looks degenerate" in r.getMessage()]
    assert levels == [logging.INFO, logging.WARNING]
