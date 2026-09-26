"""tests/test_language.py — source-language resolution for ``--source_lang auto``.

The specification is a real run over the Czech ALTO sample: FastText answered
``krc``, ``yue``, ``bod``, ``epo`` and ``swh`` for short OCR blocks, and every such
guess used to become the block's source language (UDPipe had no model, batches
split per bogus language, append mode would have stamped it as ``LANG``). When
detection could not run at all the answer was ``"en"`` — the target — so the block
was left untranslated.

Asserted here, from the pure policy up to both document paths:

* a guess is used only if the text is long enough, the score is high enough, and
  the backend can translate that language — a lower-ranked plausible candidate
  can still win;
* otherwise: the element's own label, then the document's language, then the
  default source language — never ``en`` by accident, never an exotic code;
* the per-document log line says what was overridden.
"""

from __future__ import annotations

import csv
import io
import logging
from unittest.mock import MagicMock

import pytest
from lxml import etree

from processors.language import (
    LanguageTally,
    Resolution,
    SourceLanguagePolicy,
    allowed_source_languages,
    letter_count,
    normalise_for_detection,
    normalise_lang_code,
    resolve_source_language,
)
from utils import OUTPUT_MODE_APPEND, process_alto_xml, process_metadata_xml

LINDAT_MODELS = ["cs-en", "de-en", "fr-en", "pl-en", "ru-en", "uk-en", "en-cs", "uk-cs"]
LINDAT_SOURCES = frozenset({"cs", "de", "fr", "pl", "ru", "uk", "en"})
POLICY = SourceLanguagePolicy(default="cs", min_confidence=0.5, min_letters=20, allowed=LINDAT_SOURCES)
LONG_CZECH = "Záchranný archeologický výzkum proběhl v obci Vraní"


class _Candidates:
    """Identifier double returning a fixed candidate list and recording its calls."""

    def __init__(self, candidates):
        self._candidates = candidates
        self.calls = []

    def candidates(self, text, k=5, max_chars=2000):
        self.calls.append(text)
        return list(self._candidates)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def test_detection_text_keeps_letters_only():
    assert normalise_for_detection("Parc. č. 41/1, 41/5 Vraní") == "parc č vraní"
    assert normalise_for_detection("2014 — 41/11") == ""
    assert letter_count("Mgr. Bohumil Sýkora, 2014") == 16


@pytest.mark.parametrize(
    "raw,expected",
    [("en-US", "en"), ("CES", "cs"), ("cs", "cs"), ("lvs", "lv"), ("sk_SK", "sk"), ("jpn", "jpn"), ("", None)],
)
def test_language_codes_are_normalised(raw, expected):
    assert normalise_lang_code(raw) == expected


def test_backend_languages_come_from_pairs_into_the_target():
    translator = MagicMock(supported_models=LINDAT_MODELS)
    assert allowed_source_languages(translator, "en") == LINDAT_SOURCES
    assert "uk" in allowed_source_languages(translator, "cs"), "uk-cs makes Ukrainian a source for Czech"


def test_backend_languages_fall_back_to_supported_languages():
    class _Backend:
        def supported_languages(self):
            return ["cs", "de-AT"]

    assert allowed_source_languages(_Backend(), "en") == frozenset({"cs", "de", "en"})


def test_a_backend_that_says_nothing_is_unrestricted():
    assert allowed_source_languages(MagicMock(), "en") is None
    assert allowed_source_languages(object(), "en") is None


def test_policy_from_env(monkeypatch):
    monkeypatch.setenv("DEFAULT_SOURCE_LANG", "de")
    monkeypatch.setenv("LANG_ID_MIN_CONFIDENCE", "0.7")
    monkeypatch.setenv("LANG_ID_MIN_LETTERS", "5")
    monkeypatch.setenv("LANG_ID_LANGUAGES", "cs, DE, sk")
    policy = SourceLanguagePolicy.from_env(allowed={"cs", "de", "fr", "en"})
    assert policy == SourceLanguagePolicy("de", 0.7, 5, frozenset({"cs", "de"}))
    assert SourceLanguagePolicy.from_env(default="pl").default == "pl", "the caller's default wins"
    assert policy.accepts("de") and not policy.accepts("fr")


def test_policy_defaults(monkeypatch):
    for name in ("DEFAULT_SOURCE_LANG", "LANG_ID_MIN_CONFIDENCE", "LANG_ID_MIN_LETTERS", "LANG_ID_LANGUAGES"):
        monkeypatch.delenv(name, raising=False)
    policy = SourceLanguagePolicy.from_env()
    assert (policy.default, policy.min_confidence, policy.min_letters, policy.allowed) == ("cs", 0.5, 20, None)
    assert policy.describe()["default_source_lang"] == "cs"


# ──────────────────────────────────────────────────────────────────────────────
# The resolver
# ──────────────────────────────────────────────────────────────────────────────


def test_a_confident_supported_detection_is_used():
    assert resolve_source_language(_Candidates([("de", 0.97)]), "Die Ausgrabung fand im Dorf statt", POLICY) == (
        Resolution("de", "detected", ("de", 0.97))
    )


def test_an_exotic_top_guess_yields_to_a_supported_candidate():
    resolution = resolve_source_language(_Candidates([("yue", 0.7), ("cs", 0.6)]), LONG_CZECH, POLICY)
    assert (resolution.lang, resolution.basis, resolution.raw) == ("cs", "detected", ("yue", 0.7))


@pytest.mark.parametrize("guess", ["yue", "krc", "bod", "eo", "swh", "sk"])
def test_an_untranslatable_guess_is_never_used(guess):
    resolution = resolve_source_language(_Candidates([(guess, 0.95)]), LONG_CZECH, POLICY, context="cs")
    assert resolution.lang == "cs" and resolution.basis == "context"


def test_low_confidence_is_not_trusted():
    resolution = resolve_source_language(_Candidates([("de", 0.4)]), LONG_CZECH, POLICY, context="cs")
    assert resolution.basis == "context"


def test_short_text_is_not_sent_to_the_identifier():
    identifier = _Candidates([("yue", 0.99)])
    resolution = resolve_source_language(identifier, "Vojtěch Marek", POLICY, context="cs")
    assert identifier.calls == [] and resolution == Resolution("cs", "context", None)


def test_the_elements_label_comes_before_the_document_language():
    assert resolve_source_language(None, "Kurz", POLICY, hint="de", context="cs").basis == "hint"
    assert resolve_source_language(None, "Kurz", POLICY, hint="en-US", context="cs").lang == "en"


def test_a_label_the_backend_cannot_translate_is_ignored():
    resolution = resolve_source_language(None, "Krátky text", POLICY, hint="sk", context="cs")
    assert resolution == Resolution("cs", "context", None)


def test_without_context_the_default_applies():
    policy = SourceLanguagePolicy(default="pl", allowed=LINDAT_SOURCES)
    assert resolve_source_language(None, LONG_CZECH, policy) == Resolution("pl", "default", None)


def test_an_unavailable_model_never_means_english():
    """The old contract answered ("en", 0.0) — the target — and the block went untranslated."""
    resolution = resolve_source_language(_Candidates([]), LONG_CZECH, POLICY)
    assert resolution.lang == "cs" and resolution.basis == "default"


def test_identifiers_with_only_detect_still_work():
    class _Legacy:
        def detect(self, text):
            return "ces", 0.9

    assert resolve_source_language(_Legacy(), LONG_CZECH, POLICY).lang == "cs"


def test_tally_reports_what_was_overridden():
    tally = LanguageTally()
    tally.add(Resolution("cs", "detected", ("cs", 0.99)))
    tally.add(Resolution("cs", "context", ("yue", 0.62)))
    tally.add(Resolution("cs", "context", None))
    tally.add(Resolution("de", "hint", None))
    summary = tally.summary()
    assert summary.startswith("cs 3 (context 2, detected 1); de 1 (hint 1)")
    assert "FastText guesses not used: yue×1" in summary


# ──────────────────────────────────────────────────────────────────────────────
# The documents
# ──────────────────────────────────────────────────────────────────────────────


class _OcrLikeIdentifier:
    """Answers the way FastText does on OCR text: an exotic guess below 40 letters."""

    def __init__(self):
        self.calls = []

    def candidates(self, text, k=5, max_chars=2000):
        self.calls.append(text)
        if letter_count(text) < 40:
            return [("yue", 0.62), ("cs", 0.2)]
        return [("cs", 0.98)]


class _Translator:
    name = "lindat"
    vocabulary: dict = {}
    protected_count = 0
    supported_models = LINDAT_MODELS

    def __init__(self):
        self.languages = []

    def reset_protected_count(self):
        pass

    def translate(self, text, src_lang, tgt_lang="en"):
        self.languages.append(src_lang)
        return "\n".join(f"EN:{line}" if line.strip() else line for line in text.split("\n"))


class _Doc:
    def __init__(self):
        self.blocks = {}

    def set_block(self, name, value):
        self.blocks[name] = value


ALTO_NS = "http://www.loc.gov/standards/alto/ns-v3#"


def _string(word, x):
    return f'<String CONTENT="{word}" HPOS="{x}" WIDTH="9"/>'


def _line(line_id, words):
    return f'<TextLine ID="{line_id}">' + "".join(_string(w, i) for i, w in enumerate(words.split())) + "</TextLine>"


_ALTO = f"""<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="{ALTO_NS}"><Layout><Page ID="P1"><PrintSpace>
<TextBlock ID="B1">{_line("B1L1", LONG_CZECH)}</TextBlock>
<TextBlock ID="B2" LANG="sk">{_line("B2L1", "Vojtěch Marek")}</TextBlock>
<TextBlock ID="B3" LANG="de">{_line("B3L1", "Grabungsbericht Nummer drei")}</TextBlock>
<TextBlock ID="B4">{_line("B4L1", "Objednatel: Obec Vraní")}</TextBlock>
<TextBlock ID="B5" LANG="cs">{_line("B5L1", "Nálezová zpráva z výzkumu v roce")}</TextBlock>
</PrintSpace></Page></Layout></alto>
""".encode()


def _run_alto(tmp_path, identifier, **kwargs):
    src = tmp_path / "doc.alto.xml"
    src.write_bytes(_ALTO)
    out = tmp_path / "doc_en.alto.xml"
    translator, doc = _Translator(), _Doc()
    process_alto_xml(src, out, translator, "auto", "en", identifier=identifier, doc=doc, **kwargs)
    blocks = {b.get("ID"): b for b in etree.parse(str(out)).getroot().iter(f"{{{ALTO_NS}}}TextBlock")}
    return translator, doc, blocks


def test_alto_blocks_only_ever_get_a_translatable_language(tmp_path, caplog):
    with caplog.at_level(logging.INFO, logger="utils"):
        translator, doc, _ = _run_alto(tmp_path, _OcrLikeIdentifier())

    assert set(translator.languages) == {"cs", "de"}, "no yue, no sk"
    assert doc.blocks["translations"]["detected_source_lang"] == "cs"
    assert "document cs (detected" in caplog.text
    assert "FastText guesses not used: yue" in caplog.text
    assert any(r.levelno == logging.WARNING and "source language" in r.getMessage() for r in caplog.records)


def test_alto_append_labels_blocks_with_a_real_language(tmp_path):
    _, _, blocks = _run_alto(tmp_path, _OcrLikeIdentifier(), output_mode=OUTPUT_MODE_APPEND)
    assert blocks["B1"].get("LANG") == "cs", "detected"
    assert blocks["B4"].get("LANG") == "cs", "inherited from the document, not 'yue'"
    assert blocks["B2"].get("LANG") == "sk", "an existing label is never rewritten in append mode"
    assert blocks["B3"].get("LANG") == "de"


def test_alto_with_no_identifier_or_no_model_falls_back_not_to_english(tmp_path):
    for identifier in (None, _Candidates([])):
        translator, doc, _ = _run_alto(tmp_path, identifier)
        assert "en" not in translator.languages
        assert set(translator.languages) == {"cs", "de"}
        assert doc.blocks["translations"]["detected_source_lang"] == "cs"


def test_alto_default_source_language_is_configurable(tmp_path):
    policy = SourceLanguagePolicy(default="pl", allowed=LINDAT_SOURCES)
    translator, doc, _ = _run_alto(tmp_path, _Candidates([]), lang_policy=policy)
    assert doc.blocks["translations"]["detected_source_lang"] == "pl"
    # Blocks with a supported label keep it; the rest take the (default) document language.
    assert set(translator.languages) == {"pl", "de", "cs"}


def test_alto_explicit_source_language_detects_nothing(tmp_path):
    identifier = _OcrLikeIdentifier()
    src = tmp_path / "doc.alto.xml"
    src.write_bytes(_ALTO)
    translator, doc = _Translator(), _Doc()
    process_alto_xml(src, tmp_path / "out.xml", translator, "cs", "en", identifier=identifier, doc=doc)
    assert identifier.calls == [] and set(translator.languages) == {"cs"}
    assert "detected_source_lang" not in doc.blocks["translations"]


AMCR_NS = "https://api.aiscr.cz/schema/amcr/2.2/"
_META = f"""<?xml version="1.0" encoding="utf-8"?>
<amcr:amcr xmlns:amcr="{AMCR_NS}" xmlns:xml="http://www.w3.org/XML/1998/namespace">
  <amcr:nazev>Davle - kultovní areál</amcr:nazev>
  <amcr:popis>Benediktinský klášter vznikl kolem roku tisíc jako nejstarší mužský klášter.</amcr:popis>
  <amcr:poznamka xml:lang="de">Kurze Notiz</amcr:poznamka>
</amcr:amcr>
""".encode()


def test_metadata_fields_resolve_through_the_record_language(tmp_path):
    src = tmp_path / "rec.xml"
    src.write_bytes(_META)
    translator, doc, buffer = _Translator(), _Doc(), io.StringIO()
    xpaths = ["//amcr:amcr/amcr:nazev", "//amcr:amcr/amcr:popis", "//amcr:amcr/amcr:poznamka"]
    process_metadata_xml(
        src,
        tmp_path / "out.xml",
        xpaths,
        translator,
        "auto",
        "en",
        csv_writer=csv.writer(buffer),
        identifier=_OcrLikeIdentifier(),
        doc=doc,
    )
    # nazev: short → record language; popis: detected; poznamka: its own xml:lang.
    assert translator.languages == ["cs", "cs", "de"]
    assert doc.blocks["translations"]["detected_source_lang"] == "cs"
