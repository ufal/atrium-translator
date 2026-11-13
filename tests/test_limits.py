"""tests/test_limits.py — every limit is a setting, and none cuts a translation quietly.

atrium-project#53 (factor III), for this repo: the limits are declared in tool_limits.py,
reported in /info, and each one that shapes a result without refusing it writes a
``limits_applied`` note (processors/limit_notes.py) that reaches the run's paradata and the
/translate response. tests/test_limits_contract.py (canonical) checks the declaration
against .env.example and service/README.md; this file checks the behaviour.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from lxml import etree

from atrium_limits import ContentLimitError, LimitNotes
from processors.chunking import chunk_for_translation
from processors.ct2_translator import CT2Translator
from processors.limit_notes import collecting, note
from processors.llm_translator import LLMTranslator
from processors.translator import DegenerateTranslationError
from service.api import _safe_file_name, app
from tool_limits import LIMITS, TRANSLATION_CHUNK_CHARS
from utils import _alto_document_text, _metadata_record_text, _rerun_flagged_metadata, _SourceLanguages

client = TestClient(app)


# ── the collector ────────────────────────────────────────────────────────────────────────


def test_note_outside_a_collector_is_a_no_op():
    note(TRANSLATION_CHUNK_CHARS, "split")  # must not raise


def test_collectors_nest_and_restore():
    with collecting() as outer:
        with collecting() as inner:
            note(TRANSLATION_CHUNK_CHARS, "split")
        note(TRANSLATION_CHUNK_CHARS, "split", 2)
    assert inner.as_list()[0]["count"] == 1
    assert outer.as_list()[0]["count"] == 2


# ── chunking ─────────────────────────────────────────────────────────────────────────────


def test_a_split_segment_is_recorded(monkeypatch):
    monkeypatch.setenv("TRANSLATION_CHUNK_CHARS", "100")
    with collecting() as notes:
        chunks = chunk_for_translation("Věta číslo jedna. " * 20)
    assert len(chunks) > 1
    [entry] = notes.as_list()
    assert (entry["limit"], entry["effect"], entry["value"]) == ("translation_chunk_chars", "split", 100)


def test_a_segment_within_the_limit_records_nothing():
    with collecting() as notes:
        assert chunk_for_translation("Krátký text.") == ["Krátký text."]
    assert notes.as_list() == []


# ── source-language windows ──────────────────────────────────────────────────────────────


class _Identifier:
    """Answers Czech for everything; records the window it was given."""

    def __init__(self):
        self.max_chars = []

    def candidates(self, text, k=5, max_chars=2000):
        self.max_chars.append(max_chars)
        return [("cs", 0.99)]


class _Backend:
    name = "lindat"

    def supported_languages(self):
        return ["cs", "en"]


def test_a_long_segment_is_recorded_as_sampled(monkeypatch):
    monkeypatch.setenv("LANG_ID_SEGMENT_CHARS", "50")
    identifier = _Identifier()
    with collecting() as notes:
        languages = _SourceLanguages("auto", identifier, _Backend(), "en", None, "krátký dokument")
        assert languages.resolve("slovo " * 40) == "cs"
        assert languages.resolve("krátce") == "cs"
    # Only the long segment reaches FastText: the short texts are under LANG_ID_MIN_LETTERS.
    assert identifier.max_chars == [50], "the segment window reaches the identifier"
    [entry] = notes.as_list()
    assert (entry["limit"], entry["effect"], entry["count"]) == ("lang_id_segment_chars", "sampled", 1)


def test_a_long_alto_document_is_recorded_as_sampled(monkeypatch):
    monkeypatch.setenv("LANG_ID_DOCUMENT_CHARS", "30")
    strings = "".join(f'<String CONTENT="slovo{i}"/>' for i in range(20))
    root = etree.fromstring(f"<alto><Layout><TextLine>{strings}</TextLine></Layout></alto>".encode())
    with collecting() as notes:
        text = _alto_document_text(root)
    assert 30 <= len(text) < 40
    assert notes.as_list()[0]["limit"] == "lang_id_document_chars"


def test_an_alto_document_that_fits_is_not_recorded():
    root = etree.fromstring(b'<alto><Layout><TextLine><String CONTENT="dobry"/></TextLine></Layout></alto>')
    with collecting() as notes:
        assert _alto_document_text(root) == "dobry"
    assert notes.as_list() == []


def test_a_long_metadata_record_is_recorded_as_sampled(monkeypatch):
    monkeypatch.setenv("LANG_ID_DOCUMENT_CHARS", "10")
    root = etree.fromstring(b"<r><a>" + "dlouhy text zaznamu".encode() + b"</a></r>")
    with collecting() as notes:
        assert _metadata_record_text(root, ["//a"], {}) == "dlouhy tex"
    assert "19 characters" in notes.as_list()[0]["detail"]


# ── a segment left in the source language ────────────────────────────────────────────────


def test_a_field_kept_as_source_after_the_rerun_fails_the_job(monkeypatch):
    """D13 (atrium-project#84): a field left in the source language is not a translation."""
    monkeypatch.setenv("TRANSLATION_RERUN_ROUNDS", "0")
    flagged = [
        {
            "text": "pravidla",
            "lang": "cs",
            "elem": MagicMock(),
            "row": ["d", "", "//a", "pravidla", "", "ok"],
            "reason": "repetition loop",
        }
    ]
    with collecting(), pytest.raises(ContentLimitError) as raised:
        _rerun_flagged_metadata(flagged, MagicMock(), "en", "replace", "doc")
    assert flagged[0]["row"][5] == "untranslated"
    assert (raised.value.limit, raised.value.effect, raised.value.count) == ("translation_rerun_rounds", "skipped", 1)


# ── backends ─────────────────────────────────────────────────────────────────────────────


def _resp(payload):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = payload
    return r


def test_an_llm_reply_cut_at_max_tokens_is_not_used(monkeypatch):
    monkeypatch.setenv("LLM_MAX_TOKENS", "64")
    backend = LLMTranslator(base_url="https://example.test/v1", model="m")
    cut = {"choices": [{"message": {"content": "The first half of the"}, "finish_reason": "length"}]}
    with patch("processors.llm_translator.requests.post", return_value=_resp(cut)) as post:
        with pytest.raises(DegenerateTranslationError, match="LLM_MAX_TOKENS=64"):
            backend.translate("První polovina věty a druhá polovina věty.", "cs", "en")
    assert post.call_args.kwargs["json"]["max_tokens"] == 64
    assert post.call_args.kwargs["timeout"] == 120.0


def test_the_llm_timeout_is_a_setting(monkeypatch):
    monkeypatch.setenv("LLM_TIMEOUT_S", "7.5")
    backend = LLMTranslator(base_url="https://example.test/v1", model="m")
    done = {"choices": [{"message": {"content": "A whole sentence here."}, "finish_reason": "stop"}]}
    with patch("processors.llm_translator.requests.post", return_value=_resp(done)) as post:
        assert backend.translate("Celá věta tady je.", "cs", "en") == "A whole sentence here."
    assert post.call_args.kwargs["timeout"] == 7.5


def _lindat():
    from processors.translator import LindatTranslator

    with patch("processors.translator.requests.get", side_effect=OSError("offline")):
        return LindatTranslator(vocab_path=None)


def _lindat_reply(text):
    r = MagicMock()
    r.status_code = 200
    r.text = text
    return r


def test_the_lindat_timeout_and_retries_are_settings(monkeypatch):
    monkeypatch.setenv("LINDAT_TIMEOUT_S", "7.5")
    monkeypatch.setenv("LINDAT_MAX_RETRIES", "0")
    backend = _lindat()
    with patch("processors.translator.requests.post", return_value=_lindat_reply("A castle.")) as post:
        assert backend.translate("Hrad.", "cs", "en") == "A castle."
    assert post.call_args.kwargs["timeout"] == 7.5


def test_lindat_max_retries_bounds_the_attempts(monkeypatch):
    from processors.translator import TranslationError

    monkeypatch.setenv("LINDAT_MAX_RETRIES", "0")
    backend = _lindat()
    failing = MagicMock(status_code=503, text="")
    with patch("processors.translator.requests.post", return_value=failing) as post:
        with pytest.raises(TranslationError):
            backend.translate("Hrad.", "cs", "en")
    assert post.call_count == 1  # 0 retries: the first attempt only

    monkeypatch.setenv("LINDAT_MAX_RETRIES", "2")
    with patch("processors.translator.requests.post", return_value=failing) as post:
        with pytest.raises(TranslationError):
            backend.translate("Hrad.", "cs", "en")
    assert post.call_count == 3


def test_a_split_field_reaches_the_response_header_through_the_real_pipeline(monkeypatch, tmp_path):
    """The real process_single_file, LindatTranslator and paradata logger, only requests.post patched:
    the note a split field records has to travel collecting() -> paradata -> X-Atrium-Limits-Applied.
    (Every other service test writes its notes into the logger itself, so removing that link stayed green.)"""
    import re
    from pathlib import Path

    import service.api as api
    from processors.translator import LindatTranslator

    monkeypatch.setenv("TRANSLATION_CHUNK_CHARS", "100")
    with patch("processors.translator.requests.get", side_effect=OSError("offline")):
        translator = LindatTranslator(vocab_path=None)
    models = {"translator": translator, "identifier": None}
    # The field list comes with the request (D34); amcr-fields.txt serves as the example list.
    xpaths = Path("amcr-fields.txt").read_text(encoding="utf-8")
    source = Path("data_samples/my_documents/C-TX-195304352.xml").read_text(encoding="utf-8")
    long_popis = "\n".join(["Hrad stojí na kopci nad řekou."] * 12)
    body = re.sub(
        r"(<amcr:popis[^>]*>)(.*?)(</amcr:popis>)",
        lambda m: m.group(1) + long_popis + m.group(3),
        source,
        count=1,
        flags=re.S,
    )

    def reply(url, data, timeout):
        return _lindat_reply(
            "\n".join("A castle stands on a hill above the river." for _ in data["input_text"].split("\n"))
        )

    with patch.object(api, "models", models), patch("processors.translator.requests.post", side_effect=reply):
        response = TestClient(api.app).post(
            "/translate",
            files={"file": ("record.xml", body.encode(), "application/xml")},
            data={"is_alto": "false", "source_lang": "cs", "output_mode": "replace", "xpaths": xpaths},
        )
    assert response.status_code == 200, response.content[:300]
    assert "translation_chunk_chars=split:" in response.headers["x-atrium-limits-applied"]


def test_a_lindat_segment_split_into_chunks_is_recorded(monkeypatch):
    monkeypatch.setenv("TRANSLATION_CHUNK_CHARS", "100")
    backend = _lindat()
    text = "\n".join(["Věta o hradu a mostu, která je dlouhá."] * 6)

    def reply(url, data, timeout):  # one English line per Czech line, so no guard fires
        return _lindat_reply(
            "\n".join(["A sentence about a castle and a bridge, which is long."] * len(data["input_text"].splitlines()))
        )

    with patch("processors.translator.requests.post", side_effect=reply) as post:
        with collecting() as notes:
            backend.translate(text, "cs", "en")
    assert post.call_count > 1
    [entry] = notes.as_list()
    assert (entry["limit"], entry["effect"], entry["value"]) == ("translation_chunk_chars", "split", 100)


def test_the_lindat_guard_retries_are_read_per_call(monkeypatch):
    monkeypatch.setenv("LINDAT_GUARD_RETRIES", "0")
    backend = _lindat()
    with patch("processors.translator.requests.post", return_value=_lindat_reply("")) as post:
        with pytest.raises(DegenerateTranslationError, match="after 1 attempt"):
            backend.translate("Hrad stojí na kopci nad řekou.", "cs", "en")
    assert post.call_count == 1


def test_an_llm_glossary_over_the_cap_is_recorded(monkeypatch):
    monkeypatch.setenv("LLM_MAX_GLOSSARY_TERMS", "2")
    backend = LLMTranslator(base_url="https://example.test/v1", model="m")
    backend.vocabulary = {"hrad": "castle", "most": "bridge", "kostel": "church"}
    with collecting() as notes:
        lines = backend._glossary_lines("hrad most kostel")
    assert len(lines) == 2
    assert notes.as_list()[0]["limit"] == "llm_max_glossary_terms"


class _Sp:
    def encode(self, text, out_type=str):
        return text.split()

    def decode(self, tokens):
        return " ".join(tokens)


class _Hyp:
    def __init__(self, tokens):
        self.hypotheses = [tokens]


class _Engine:
    def __init__(self):
        self.calls = []

    def translate_batch(self, batch, **kwargs):
        self.calls.append((len(batch[0]), kwargs))
        return [_Hyp(list(batch[0]))]


def _ct2():
    backend = CT2Translator(model_dir="/tmp/fake", family="opus")
    backend._sp, backend._engine = _Sp(), _Engine()
    backend._guard = lambda source, translated: None
    return backend


def test_ct2_input_over_the_limit_is_re_split_not_truncated(monkeypatch):
    monkeypatch.setenv("CT2_MAX_INPUT_TOKENS", "16")
    backend = _ct2()
    text = " ".join(f"slovo{i}" for i in range(40))
    with collecting() as notes:
        out = backend._translate_nmt(text, "cs", "en")
    assert out.split() == text.split(), "every word was translated; none was dropped"
    assert all(n <= 16 for n, _ in backend._engine.calls)
    assert all(kw["max_input_length"] == 16 for _, kw in backend._engine.calls)
    assert notes.as_list()[0]["limit"] == "ct2_max_input_tokens"


def test_ct2_output_at_the_decoding_cap_is_not_used(monkeypatch):
    monkeypatch.setenv("CT2_MAX_DECODING_TOKENS", "3")
    with pytest.raises(DegenerateTranslationError, match="CT2_MAX_DECODING_TOKENS=3"):
        _ct2()._translate_nmt("jedna dva tri ctyri", "cs", "en")


# ── the service ──────────────────────────────────────────────────────────────────────────


def test_info_reports_every_limit():
    data = client.get("/info").json()
    assert data["limits"] == LIMITS.values()
    assert data["limits"]["max_request_mb"] == 101.0
    assert data["limits_meta"]["lang_id_document_chars"]["env"] == "LANG_ID_DOCUMENT_CHARS"
    assert data["limits_meta"]["max_request_mb"]["source"] == "derived"


def _fake_models():
    translator = MagicMock()
    translator.name = "lindat"
    translator.vocabulary = {}
    translator.license_components.return_value = ["lindat_cubbitt"]
    return {"translator": translator, "identifier": MagicMock()}


def _process_noting_a_sample(file_path=None, output_file=None, _logger=None, **kwargs):
    notes = LimitNotes()
    notes.note("lang_id_document_chars", "sampled", 1, "first 20000 characters — příliš dlouhý", value=20000)
    _logger.note_limits(notes)
    output_file.write_bytes(b"<alto/>")
    return True, 0


@patch("service.api.process_single_file", side_effect=_process_noting_a_sample)
def test_limits_applied_reach_the_response_header(_mock):
    with patch("service.api.models", _fake_models()):
        response = client.post(
            "/translate",
            files={"file": ("page.alto.xml", b"<alto/>", "application/xml")},
            data={"is_alto": "true"},
        )
    assert response.status_code == 200
    assert response.headers["x-atrium-limits-applied"] == "lang_id_document_chars=sampled:1"


@patch("service.api.process_single_file")
def test_the_multipart_response_carries_limits_applied_json(mock_process):
    def process(file_path=None, output_file=None, args=None, _logger=None, **kwargs):
        _process_noting_a_sample(file_path, output_file, _logger)
        args.document_json_out.write_text(json.dumps({"doc_id": "page"}), encoding="utf-8")
        return True, 0

    mock_process.side_effect = process
    with patch("service.api.models", _fake_models()):
        response = client.post(
            "/translate",
            files={
                "file": ("page.alto.xml", b"<alto/>", "application/xml"),
                "document_json": ("seed.json", b'{"doc_id": "page"}', "application/json"),
            },
            data={"is_alto": "true"},
        )
    assert response.status_code == 200
    part = response.content.split(b'filename="limits_applied.json"\r\n\r\n', 1)[1].split(b"\r\n--", 1)[0]
    [entry] = json.loads(part)
    assert entry["detail"].endswith("příliš dlouhý")


@pytest.mark.parametrize(
    "given, expected",
    [
        ("page.alto.xml", "page.alto.xml"),
        ("../../etc/page.alto.xml", "page.alto.xml"),
        ("..\\..\\page.alto.xml", "page.alto.xml"),
        ("..", None),
        ("", None),
        ("a\nb.xml", None),
        ("x" * 250 + ".xml", None),
    ],
)
def test_client_names_are_reduced_to_a_plain_file_name(given, expected):
    assert _safe_file_name(given) == expected


@patch("service.api.process_single_file")
def test_an_upload_name_cannot_climb_out_of_the_work_dir(mock_process):
    seen = {}

    def process(file_path=None, output_file=None, **kwargs):
        seen["input"] = file_path
        output_file.write_bytes(b"<alto/>")
        return True, 0

    mock_process.side_effect = process
    with patch("service.api.models", _fake_models()):
        response = client.post(
            "/translate",
            files={"file": ("../../escape.alto.xml", b"<alto/>", "application/xml")},
            data={"is_alto": "true"},
        )
    assert response.status_code == 200
    assert seen["input"].name == "escape.alto.xml"
    assert seen["input"].parent.name != "etc" and ".." not in seen["input"].parts


def test_an_oversized_upload_gets_the_harmonised_body(monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "0.001")
    response = client.post(
        "/translate",
        files={"file": ("page.alto.xml", b"x" * 5000, "application/xml")},
        data={"is_alto": "true"},
    )
    assert response.status_code == 413
    body = response.json()
    assert body["reason"] == "limit_exceeded" and body["limit"]["env"] == "MAX_UPLOAD_MB"
    assert body["detail"] == "File too large: over 0.001 MB (MAX_UPLOAD_MB)."


def test_a_segment_split_twice_is_one_split_note(monkeypatch):
    """(atrium-project#53 follow-up) the end-of-document re-run sends a split segment again;
    the note counts segments, not calls."""
    monkeypatch.setenv("TRANSLATION_CHUNK_CHARS", "100")
    segment = "\n".join(["Věta o hradu a mostu, která je dlouhá."] * 6)
    other = "\n".join(["Jiná věta o kostele na náměstí, také dlouhá."] * 6)
    with collecting() as notes:
        chunk_for_translation(segment)
        chunk_for_translation(segment)  # the re-run
        chunk_for_translation(other)
    [entry] = notes.as_list()
    assert (entry["limit"], entry["effect"], entry["count"]) == ("translation_chunk_chars", "split", 2)


def test_notes_without_a_key_still_add_up():
    with collecting() as notes:
        note(TRANSLATION_CHUNK_CHARS, "split", 1)
        note(TRANSLATION_CHUNK_CHARS, "split", 1)
    assert notes.as_list()[0]["count"] == 2


def test_the_seen_keys_belong_to_one_collector(monkeypatch):
    monkeypatch.setenv("TRANSLATION_CHUNK_CHARS", "100")
    segment = "\n".join(["Věta o hradu a mostu, která je dlouhá."] * 6)
    with collecting() as first:
        chunk_for_translation(segment)
    with collecting() as second:  # the next document
        chunk_for_translation(segment)
    assert first.as_list()[0]["count"] == second.as_list()[0]["count"] == 1
