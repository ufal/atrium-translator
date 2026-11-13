"""
tests/test_atrium_document.py

Tests the ATRIUM Document Schema & Accretion Policy ("paradata pair" model)
contract (six rules) for the translator integration.
"""

import argparse
import json

import pytest

from atrium_document import DocumentRecord, canonical_doc_id
from atrium_paradata import ParadataLogger


@pytest.fixture
def mock_paradata(tmp_path):
    """Generates a mock paradata record and returns the run_id and ref."""
    para_dir = tmp_path / "paradata"
    para_dir.mkdir()
    logger = ParadataLogger("translator", {}, paradata_dir=str(para_dir))
    logger.log_component("lindat_cubbitt")
    paradata_ref = logger.finalize()
    return logger._run_id, paradata_ref


@pytest.fixture
def baseline_json(tmp_path):
    """Creates a mock baseline ATRIUM Document JSON from an upstream tool."""
    baseline = {
        "schema_version": "1.0",
        "doc_id": "CTX000000001",
        "source": {"filename": "CTX000000001.alto.xml"},
        "pages": [{"page": "1", "quality_score": 0.98}],
        "entities": [{"surface": "gotického kostela", "lemma": "gotický kostel", "page": "1", "line": 14}],
        "unknown_future_block": {"key": "value"},
    }
    baseline_path = tmp_path / "CTX000000001.document.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    return baseline_path


def test_rule_1_and_2_baseline_in_record_out_and_own_block_only(tmp_path, baseline_json, mock_paradata):
    """Rule 1 & 2: Takes baseline, writes own block, deep-copies others unchanged."""
    run_id, paradata_ref = mock_paradata
    out_path = tmp_path / "out.json"

    with DocumentRecord.open(
        doc_id="CTX000000001",
        program="translator",
        baseline=baseline_json,
        run_id=run_id,
        paradata_ref=paradata_ref,
    ) as doc:
        doc.set_block("translations", {"en": "Full translation text here."})
        # MUST include the alignment keys ("page" and "line") so merge_block updates the existing row
        rows = [{"page": "1", "line": 14, "surface": "gotického kostela", "translation_en": "gothic church"}]
        doc.merge_block("entities", rows)
        # D8 (atrium-project#10): the §1b round-trip check, on the ONE field this repo is
        # granted in entities[]. jsonschema cannot catch a field-ownership drop — a row
        # stripped of translation_en still validates, because entities[] only requires its
        # key fields — so this assertion is the only thing that would fail if the grant in
        # BLOCK_FIELD_OWNERS and the fields written here ever disagree.
        doc.assert_fields_survived("entities", rows, fields=["translation_en"])
        doc.finalize(str(out_path))

    assert out_path.exists()
    result = json.loads(out_path.read_text(encoding="utf-8"))

    # Assert own block is written
    assert result["translations"]["en"] == "Full translation text here."

    # Assert merged block is correctly updated
    assert result["entities"][0]["translation_en"] == "gothic church"
    assert result["entities"][0]["lemma"] == "gotický kostel"  # Preserved from baseline

    # Assert other blocks remain untouched
    assert result["pages"][0]["quality_score"] == 0.98


def test_the_translator_writes_no_block_into_the_record(tmp_path, mock_translator):
    """Record schema 2.0 (atrium-project#84): the translator leaves the document chain. A record
    sent with the file comes back with nothing added: the same blocks, under its own doc_id."""
    input_file = tmp_path / "CTX08.alto.xml"
    input_file.write_text(_alto_xml_source(), encoding="utf-8")
    baseline = tmp_path / "CTX08.document.json"
    sent = {
        "schema_version": "2.0",
        "record_type": "atrium-document",
        "doc_id": "CTX08",
        "pages": [{"page": "1", "page_index": 1, "quality_score": 0.98}],
    }
    baseline.write_text(json.dumps(sent), encoding="utf-8")

    success, out_dir = _run_process_single_file(tmp_path, input_file, mock_translator, baseline=baseline)

    assert success
    assert json.loads((out_dir / "CTX08.document.json").read_text(encoding="utf-8")) == sent


def test_no_record_is_written_when_none_was_sent(tmp_path, mock_translator):
    """Rule 3 no longer applies: a standalone translation has no block to write, so no record."""
    input_file = tmp_path / "CTX09.alto.xml"
    input_file.write_text(_alto_xml_source(), encoding="utf-8")

    success, out_dir = _run_process_single_file(tmp_path, input_file, mock_translator)

    assert success
    assert not (out_dir / "CTX09.document.json").exists()


def test_rule_6_unknown_blocks_preserved(tmp_path, baseline_json, mock_paradata):
    """Rule 6: Unknown or newer blocks are preserved verbatim."""
    run_id, paradata_ref = mock_paradata
    out_path = tmp_path / "out_unknown.json"

    with DocumentRecord.open(
        doc_id="CTX000000001",
        program="translator",
        baseline=baseline_json,
        run_id=run_id,
        paradata_ref=paradata_ref,
    ) as doc:
        doc.set_block("translations", {"en": "Text."})
        doc.finalize(str(out_path))

    result = json.loads(out_path.read_text(encoding="utf-8"))
    assert result["unknown_future_block"]["key"] == "value"


# ── P0.5 regression: process_metadata_xml/process_alto_xml's actual `translations`
# shape, not just the generic DocumentRecord primitive above ──────────────────────


def test_process_metadata_xml_reports_its_facts(amcr_xml_file, tmp_path, mock_translator):
    """What shaped the translation goes to the run's paradata through `facts` — the language
    pair, the backend and the output mode — never the translated text (D34)."""
    from utils import process_metadata_xml

    facts = {}
    process_metadata_xml(
        amcr_xml_file,
        tmp_path / "out.xml",
        ["//amcr:amcr/amcr:dokument/amcr:popis"],
        mock_translator,
        "cs",
        "en",
        facts=facts,
        backend="lindat",
    )
    # Exact equality on purpose: a silent extra key here is what this test exists to catch.
    assert facts == {"source_lang": "cs", "target_lang": "en", "backend": "lindat", "output_mode": "replace"}


def test_process_alto_xml_reports_its_facts(alto_xml_file, tmp_path, mock_translator):
    from utils import process_alto_xml

    facts = {}
    process_alto_xml(
        alto_xml_file, tmp_path / "out.alto.xml", mock_translator, "cs", "en", facts=facts, backend="ctranslate2"
    )
    assert facts == {"source_lang": "cs", "target_lang": "en", "backend": "ctranslate2", "output_mode": "replace"}


def test_the_facts_carry_no_field_text(amcr_xml_file, tmp_path, mock_translator):
    """D34: `paradata/metadata-en` carries no field text, so neither do the facts it is built from."""
    from utils import process_metadata_xml

    facts = {}
    process_metadata_xml(
        amcr_xml_file,
        tmp_path / "out.xml",
        ["//amcr:amcr/amcr:dokument/amcr:popis"],
        mock_translator,
        "cs",
        "en",
        facts=facts,
    )
    source_text = amcr_xml_file.read_text(encoding="utf-8")
    assert all(not isinstance(v, str) or len(v) < 20 for v in facts.values())
    assert not any(isinstance(v, str) and v and v in source_text and len(v) > 3 for v in facts.values())


# ── D3 (atrium-project#10): one doc_id derivation, for a doc_id with an embedded dot ──
#
# This repo hand-rolled `name.split(".")[0]` in five places (main.py ×2, utils.py ×2,
# service/api.py). Every sample filename in data_samples/ happens to carry no dot before its
# pipeline suffix, so the hand-rolled form agreed with canonical_doc_id() BY LUCK OF NAMING
# CONVENTION. The fixtures below use `CTX000000001.v2` — a doc_id that does contain a dot —
# because that is the case in which the old derivations forked this repo's record away from
# every other repo's record for the same physical document.

MULTI_DOT_DOC_ID = "CTX000000001.v2"

MULTI_DOT_FILENAMES = [
    f"{MULTI_DOT_DOC_ID}.alto.xml",
    f"{MULTI_DOT_DOC_ID}.teitok.xml",
    f"{MULTI_DOT_DOC_ID}.udpipe.conllu",
    f"{MULTI_DOT_DOC_ID}.document.json",
    f"{MULTI_DOT_DOC_ID}.csv",
]


@pytest.mark.parametrize("filename", MULTI_DOT_FILENAMES)
def test_canonical_doc_id_is_stable_across_pipeline_suffixes(filename):
    """One id per physical document, whichever stage's filename it is derived from."""
    assert canonical_doc_id(filename) == MULTI_DOT_DOC_ID
    # …and the derivation this repo used to hand-roll would have truncated all five.
    assert filename.split(".")[0] != MULTI_DOT_DOC_ID


def _make_logger(tmp_path) -> ParadataLogger:
    """A real ParadataLogger for process_single_file, unfinalised (as main() leaves it)."""
    para_dir = tmp_path / "paradata"
    para_dir.mkdir(parents=True, exist_ok=True)
    return ParadataLogger("translator", {}, paradata_dir=str(para_dir))


def _alto_xml_source() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v2#">
  <Layout>
    <Page ID="P1" PHYSICAL_IMG_NR="1" WIDTH="1000" HEIGHT="1000">
      <PrintSpace>
        <TextBlock ID="TB1">
          <TextLine ID="L1">
            <String ID="S1" CONTENT="Dobrý"/>
            <String ID="S2" CONTENT="den"/>
          </TextLine>
        </TextBlock>
      </PrintSpace>
    </Page>
  </Layout>
</alto>
"""


def _run_process_single_file(tmp_path, input_file, mock_translator, baseline=None):
    """Drive main.process_single_file exactly as main() and service/api.py both do."""
    import main

    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    args = argparse.Namespace(
        source_lang="cs",
        target_lang="en",
        alto=True,
        fast_align=False,
        xsd=None,
        backend="lindat",
        document_json=baseline,
        document_json_out=None,
    )
    success, _ = main.process_single_file(
        file_path=input_file,
        output_file=out_dir / f"{input_file.stem}_en.xml",
        args=args,
        translator=mock_translator,
        identifier=None,
        xpaths_list=[],
        _logger=_make_logger(tmp_path),
    )
    return success, out_dir


def test_process_single_file_keeps_an_embedded_dot_in_the_doc_id(tmp_path, mock_translator):
    """
    D3 regression at the CLI chokepoint: a baseline keyed `CTX000000001.v2` must be accreted
    onto, not forked. With the old `name.split(".")[0]`, DocumentRecord re-stamped `doc_id`
    as `CTX000000001` (`__init__` sets it unconditionally), so the emitted record described a
    document no other stage in the pipeline had ever seen — while still carrying the upstream
    blocks, which makes the fork invisible in the output.
    """
    input_file = tmp_path / f"{MULTI_DOT_DOC_ID}.alto.xml"
    input_file.write_text(_alto_xml_source(), encoding="utf-8")

    baseline = tmp_path / f"{MULTI_DOT_DOC_ID}.document.json"
    baseline.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "record_type": "atrium-document",
                "doc_id": MULTI_DOT_DOC_ID,
                "pages": [{"page": "1", "quality_score": 0.98}],
            }
        ),
        encoding="utf-8",
    )

    success, out_dir = _run_process_single_file(tmp_path, input_file, mock_translator, baseline=baseline)
    assert success

    record_path = out_dir / f"{MULTI_DOT_DOC_ID}.document.json"
    assert record_path.exists(), f"record written under a forked doc_id: {sorted(p.name for p in out_dir.iterdir())}"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["doc_id"] == MULTI_DOT_DOC_ID
    assert record["pages"][0]["quality_score"] == 0.98  # upstream block accreted, not orphaned

    # The CSV log is keyed on the SAME single derivation — filename and `file` column both.
    csv_log = out_dir / f"{MULTI_DOT_DOC_ID}_log.csv"
    assert csv_log.exists()
    rows = [line.split(",") for line in csv_log.read_text(encoding="utf-8").splitlines()[1:]]
    assert rows and all(row[0] == MULTI_DOT_DOC_ID for row in rows)


def test_direct_utils_callers_still_get_a_canonical_csv_doc_id(tmp_path, mock_translator, csv_sink):
    """
    The `doc_id=` parameter is how main.py hands its one derivation down, but process_alto_xml
    is also called directly (unit tests, any future caller). That fallback must go through
    canonical_doc_id() too — not back to a hand-rolled split.
    """
    from utils import process_alto_xml

    writer, rows = csv_sink
    input_file = tmp_path / f"{MULTI_DOT_DOC_ID}.alto.xml"
    input_file.write_text(_alto_xml_source(), encoding="utf-8")

    process_alto_xml(input_file, tmp_path / "out.alto.xml", mock_translator, "cs", "en", csv_writer=writer)

    assert rows and all(row[0] == MULTI_DOT_DOC_ID for row in rows)


# ── D1/D3 (atrium-project#10): the doc_id a PAGE-SPLIT input must NOT fork ──
#
# The tests above pin the derivation against a multi-dot document NAME. This pair pins the
# other half of the same contract — the one the E2E caught and no unit test could
# (hub run 31076188660, `assert_doc_id_stable`):
#
#     "work/doc_json/2_alto.json":     "CTX000000003"
#     "work/doc_json/3_translate.json": "CTX000000003-1"   ← this repo
#     "work/doc_json/4_nlp.json":      "CTX000000003"
#
# The translator is the one stage whose input is never the original document: alto-postprocess
# hands it `PAGE_ALTO/<doc>/<doc>-1.alto.xml`, so canonical_doc_id() answers `<doc>-1` — right
# about the FILE, wrong about the RECORD. Nothing was mis-derived and nothing was dropped; the
# record simply arrived under a key no other stage would look up again.

PAGE_SPLIT_DOC_ID = "CTX000000003"
PAGE_SPLIT_FILE_KEY = f"{PAGE_SPLIT_DOC_ID}-1"


def _page_split_baseline(tmp_path):
    """What alto-postprocess hands the translator: a record already keyed on the DOCUMENT."""
    baseline = tmp_path / f"{PAGE_SPLIT_DOC_ID}.document.json"
    baseline.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "record_type": "atrium-document",
                "doc_id": PAGE_SPLIT_DOC_ID,
                "pages": [{"page": "1", "page_index": 1, "quality_score": 0.98, "category": "TEXT"}],
                "page_categories": {"1": "TEXT"},
            }
        ),
        encoding="utf-8",
    )
    return baseline


def test_page_split_input_inherits_the_documents_doc_id(tmp_path, mock_translator):
    """A page of a document is filed under the DOCUMENT's key, and the record goes back under it."""
    input_file = tmp_path / f"{PAGE_SPLIT_FILE_KEY}.alto.xml"
    input_file.write_text(_alto_xml_source(), encoding="utf-8")

    success, out_dir = _run_process_single_file(
        tmp_path, input_file, mock_translator, baseline=_page_split_baseline(tmp_path)
    )
    assert success

    record_path = out_dir / f"{PAGE_SPLIT_DOC_ID}.document.json"
    assert record_path.exists(), f"record written under a forked doc_id: {sorted(p.name for p in out_dir.iterdir())}"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["doc_id"] == PAGE_SPLIT_DOC_ID
    # Upstream blocks kept under the same key, which is the whole point of not re-keying.
    assert record["page_categories"] == {"1": "TEXT"}
    assert "translations" not in record

    # The CSV log stays per-FILE — it holds per-line rows, so page 2 of a batch must not
    # truncate page 1's log — while its `file` column names the document the record does.
    csv_log = out_dir / f"{PAGE_SPLIT_FILE_KEY}_log.csv"
    assert csv_log.exists(), sorted(p.name for p in out_dir.iterdir())
    rows = [line.split(",") for line in csv_log.read_text(encoding="utf-8").splitlines()[1:]]
    assert rows and all(row[0] == PAGE_SPLIT_DOC_ID for row in rows)


def test_page_split_input_without_a_baseline_keeps_the_file_key(tmp_path, mock_translator):
    """Rule 3: a standalone run has no document context, so the filename is all there is.

    The repair is "inherit rather than re-derive", NOT "strip a trailing `-<n>`" — `sbn.2019-1`
    is a legal document name, so a filename rule cannot tell a page label from a document's own
    last segment. With no baseline there is nothing to inherit and the file key stands.
    """
    input_file = tmp_path / f"{PAGE_SPLIT_FILE_KEY}.alto.xml"
    input_file.write_text(_alto_xml_source(), encoding="utf-8")

    success, out_dir = _run_process_single_file(tmp_path, input_file, mock_translator)
    assert success

    # No record without a baseline (record schema 2.0); the file key names the log.
    assert (out_dir / f"{PAGE_SPLIT_FILE_KEY}_log.csv").exists()
    assert not (out_dir / f"{PAGE_SPLIT_FILE_KEY}.document.json").exists()


def test_documentrecord_refuses_to_rekey_an_inherited_record(tmp_path, capsys):
    """The shared module's half of the fix, which holds for every tool, not just this one.

    `DocumentRecord.__init__` used to do a bare `self._data["doc_id"] = doc_id` — the caller's
    guess overwrote the key the deep-copied baseline arrived with, no comparison made. So a
    tool could not opt out of forking by being careful at ONE call site; the last write always
    won. Now the baseline wins and the divergence is reported.
    """
    baseline = _page_split_baseline(tmp_path)

    with DocumentRecord.open(doc_id=PAGE_SPLIT_FILE_KEY, program="translator", baseline=baseline) as doc:
        assert doc.doc_id == PAGE_SPLIT_DOC_ID
        assert doc.derived_doc_id == PAGE_SPLIT_FILE_KEY  # still available for per-file naming
        doc.set_block("translations", {"source_lang": "cs", "target_lang": "en", "backend": "lindat"})
        out_path = doc.finalize(str(tmp_path / "out.json"))

    assert json.loads(open(out_path, encoding="utf-8").read())["doc_id"] == PAGE_SPLIT_DOC_ID
    # Visible, and never fatal: the record that came out is the correct one.
    assert PAGE_SPLIT_FILE_KEY in capsys.readouterr().err


# ── D4 (atrium-project#10): the Layer D validation gate, wired at main.py's chokepoint ──


def test_a_content_limit_fails_the_file_and_writes_no_record(tmp_path, mock_translator, monkeypatch):
    """D13: a limit that skipped content raises out of process_single_file (the job fails),
    and nothing is handed on — not even the record that was sent."""
    import main
    from atrium_limits import ContentLimitError

    def _skip(*_args, **_kwargs):
        raise ContentLimitError("translation_rerun_rounds", "skipped", 1)

    monkeypatch.setattr(main, "process_alto_xml", _skip)
    input_file = tmp_path / "CTX04.alto.xml"
    input_file.write_text(_alto_xml_source(), encoding="utf-8")
    baseline = tmp_path / "CTX04.document.json"
    baseline.write_text(json.dumps({"schema_version": "2.0", "doc_id": "CTX04", "source": {}}), encoding="utf-8")

    with pytest.raises(ContentLimitError):
        _run_process_single_file(tmp_path, input_file, mock_translator, baseline=baseline)
    assert not (tmp_path / "out" / "CTX04.document.json").exists()


def test_invalid_inherited_baseline_warns_and_still_emits(tmp_path, mock_translator, capsys):
    """
    An invalid INHERITED baseline is warned about and passed through, never refused: the
    defect belongs to the upstream tool, and refusing would turn one bad record into a
    stalled pipeline (rule 6 already commits to carrying unknown content forward). The record
    goes back as it came: the translator writes nothing into it (record schema 2.0).
    """
    input_file = tmp_path / "CTX05.alto.xml"
    input_file.write_text(_alto_xml_source(), encoding="utf-8")

    baseline = tmp_path / "CTX05.document.json"
    baseline.write_text(
        # `pages[]` requires `page`, and `quality_score` is capped at 1 — two violations.
        json.dumps(
            {
                "schema_version": "2.0",
                "record_type": "atrium-document",
                "doc_id": "CTX05",
                "pages": [{"page_index": 1, "quality_score": 5}],
            }
        ),
        encoding="utf-8",
    )

    success, out_dir = _run_process_single_file(tmp_path, input_file, mock_translator, baseline=baseline)

    assert success is True
    record_path = out_dir / "CTX05.document.json"
    assert record_path.exists()
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["pages"] == [{"page_index": 1, "quality_score": 5}]  # passed through untouched

    err = capsys.readouterr().err
    assert "inherited baseline" in err
    assert "does not validate" in err


def test_missing_jsonschema_degrades_loudly_instead_of_silently(tmp_path, mock_translator, monkeypatch, capsys):
    """
    `validate_document()` raises RuntimeError when jsonschema is absent, deliberately: a gate
    that quietly becomes a no-op is indistinguishable from a passing one. The gate must then
    say so ONCE, loudly, and let the run finish — rule 3's standalone safety.

    The baseline check hits the unavailable branch twice in a two-file batch; the warning
    must still appear exactly once, which is the whole point of the latch.
    """
    import main

    monkeypatch.setattr(main, "_VALIDATION_UNAVAILABLE_WARNED", False)

    def _no_jsonschema(_record):
        raise RuntimeError("jsonschema is not installed, so the record cannot be validated.")

    monkeypatch.setattr(main, "validate_baseline", _no_jsonschema)

    input_file = tmp_path / "CTX06.alto.xml"
    input_file.write_text(_alto_xml_source(), encoding="utf-8")

    baseline = tmp_path / "CTX06.document.json"
    baseline.write_text(
        json.dumps({"schema_version": "2.0", "record_type": "atrium-document", "doc_id": "CTX06", "source": {}}),
        encoding="utf-8",
    )

    success, out_dir = _run_process_single_file(tmp_path, input_file, mock_translator, baseline=baseline)
    assert success is True
    assert (out_dir / "CTX06.document.json").exists()
    success, _ = _run_process_single_file(tmp_path, input_file, mock_translator, baseline=baseline)
    assert success is True

    err = capsys.readouterr().err
    assert "DISABLED" in err
    assert "jsonschema" in err
    assert err.count("schema validation is DISABLED") == 1, "the unavailable-gate warning must be latched"


def test_unreadable_baseline_is_left_to_documentrecord_and_emits_nothing(tmp_path, mock_translator):
    """
    A baseline that is not even JSON is NOT the validation gate's business — it returns False
    and lets ``DocumentRecord.open()`` report it, which is where the right message lives. The
    run then fails the file rather than emitting a half-record under a stale identity.
    """
    input_file = tmp_path / "CTX07.alto.xml"
    input_file.write_text(_alto_xml_source(), encoding="utf-8")

    baseline = tmp_path / "CTX07.document.json"
    baseline.write_text("{ this is not json", encoding="utf-8")

    success, out_dir = _run_process_single_file(tmp_path, input_file, mock_translator, baseline=baseline)

    assert success is False
    assert not (out_dir / "CTX07.document.json").exists()


# ── D8 (atrium-project#10): a field outside the grant must raise, not vanish ──


def test_assert_fields_survived_flags_a_field_outside_the_grant(tmp_path, baseline_json, mock_paradata):
    """
    `pid` belongs to llm-enrich (`BLOCK_FIELD_OWNERS["entities"]["llm-enrich"]`), so
    merge_block() filters it out of a translator write — silently, and the result still
    validates, because entities[] only requires its key fields. This is the check that turns
    that silence into a failure at development time, and it is what the second pass described
    in utils.py (D7, entities[].translation_en) must call once it exists.
    """
    run_id, paradata_ref = mock_paradata

    with DocumentRecord.open(
        doc_id="CTX000000001",
        program="translator",
        baseline=baseline_json,
        run_id=run_id,
        paradata_ref=paradata_ref,
    ) as doc:
        rows = [{"page": "1", "line": 14, "pid": "https://amcr.example/pid/1"}]
        doc.merge_block("entities", rows)

        with pytest.raises(RuntimeError, match="pid"):
            doc.assert_fields_survived("entities", rows, fields=["pid"])

        doc.set_block("translations", {"source_lang": "cs", "target_lang": "en", "backend": "lindat"})
        doc.finalize(str(tmp_path / "out_grant.json"))

    record = json.loads((tmp_path / "out_grant.json").read_text(encoding="utf-8"))
    assert "pid" not in record["entities"][0]
