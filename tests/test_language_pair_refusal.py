"""
tests/test_language_pair_refusal.py
===================================
An explicit language pair the backend cannot translate is refused, never rerouted (translator#4).

``LindatTranslator._basic_translate`` used to send any pair LINDAT has no model for to ``cs-en``
without a word: ``--source_lang cs --target_lang it`` wrote English into ``<doc>_it.xml``, and
``--source_lang hu`` translated Hungarian as if it were Czech. AMČR named that quiet fallback the
defect that matters most to them (grounding report of 30 September, item 8).

Now the pair is checked before anything is translated:

* the CLI exits 1 (``EXIT_USAGE``) and says which pairs the backend offers;
* the service answers 422 (``reason: null``, ``cause: "unsupported_language_pair"``, the offered
  source languages in ``errors``);
* in ``auto`` mode the language a segment falls back to (``--default-source-lang`` /
  ``DEFAULT_SOURCE_LANG``) is checked the same way;
* ``_basic_translate`` raises :class:`UnsupportedLanguagePairError` as the backstop.

A confidently detected unsupported language inside an ``auto`` run is still reported, not refused
(translator#52, ``tests/test_unsupported_language.py``).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import main as main_module
from main import EXIT_USAGE
from processors.language import language_pair_problem
from processors.translator import LindatTranslator, TranslationError, UnsupportedLanguagePairError
from service.api import app

LINDAT_MODELS = ["cs-en", "de-en", "fr-en", "pl-en", "ru-en", "uk-en", "en-cs", "uk-cs"]

_ALTO = """<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v2#">
  <Layout><Page ID="P1" PHYSICAL_IMG_NR="1" WIDTH="100" HEIGHT="100">
    <PrintSpace><TextBlock ID="B1"><TextLine ID="L1">
      <String ID="S1" CONTENT="Ahoj" HPOS="0" VPOS="0" WIDTH="10" HEIGHT="5"/>
    </TextLine></TextBlock></PrintSpace>
  </Page></Layout>
</alto>
"""


def _lindat(models=LINDAT_MODELS, fallback=False):
    return SimpleNamespace(name="lindat", supported_models=list(models), models_fallback=fallback, vocabulary={})


def _llm(languages=()):
    return SimpleNamespace(name="openai_compatible", supported_languages=lambda: list(languages), vocabulary={})


# ── the check ─────────────────────────────────────────────────────────────────


def test_a_pair_lindat_offers_passes():
    assert language_pair_problem(_lindat(), "cs", "en") is None
    assert language_pair_problem(_lindat(), "CES", "en-GB") is None  # codes are normalised
    assert language_pair_problem(_lindat(), "cs", "cs") is None  # nothing to translate


def test_an_untranslatable_target_names_what_lindat_has():
    problem = language_pair_problem(_lindat(), "cs", "it")
    assert "no 'cs-it' model" in problem and "translates nothing into 'it'" in problem


def test_an_untranslatable_source_names_the_sources_offered_into_the_target():
    problem = language_pair_problem(_lindat(), "hu", "en")
    assert "no 'hu-en' model" in problem
    assert "into 'en' it translates from cs, de, fr, pl, ru, uk" in problem


def test_the_offline_fallback_list_is_named_as_such():
    assert "built-in fallback list" in language_pair_problem(_lindat(fallback=True), "hu", "en")


def test_a_backend_that_lists_no_languages_claims_and_refuses_nothing():
    assert language_pair_problem(_llm(), "hu", "en") is None


def test_a_backend_held_to_its_listed_languages():
    assert language_pair_problem(_llm(["cs", "en"]), "cs", "en") is None
    assert "'de'" in language_pair_problem(_llm(["cs", "en"]), "de", "en")


# ── the backstop ──────────────────────────────────────────────────────────────


def test_basic_translate_raises_instead_of_rerouting():
    models = MagicMock(status_code=200)
    models.json.return_value = LINDAT_MODELS
    with patch("processors.translator.requests.get", return_value=models):
        translator = LindatTranslator()
    with patch("processors.translator.requests.post") as post:
        with pytest.raises(UnsupportedLanguagePairError, match="no 'cs-it' model"):
            translator.translate("Archeologický výzkum.", "cs", "it")
    post.assert_not_called()


def test_the_backstop_fails_the_file_not_the_segment():
    from processors.translator import DegenerateTranslationError

    assert issubclass(UnsupportedLanguagePairError, TranslationError)
    assert not issubclass(UnsupportedLanguagePairError, DegenerateTranslationError)


# ── the CLI ───────────────────────────────────────────────────────────────────


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Run from a directory with no config.txt, so argv alone decides."""
    monkeypatch.chdir(tmp_path)
    doc = tmp_path / "sample.alto.xml"
    doc.write_text(_ALTO)
    return tmp_path


def _run(monkeypatch, translator, *argv) -> int:
    monkeypatch.setattr(main_module, "get_backend", lambda name, **kwargs: translator)
    monkeypatch.setattr(main_module, "process_single_file", lambda **kwargs: pytest.fail("nothing may be translated"))
    monkeypatch.setattr("sys.argv", ["main.py", *argv])
    return main_module.main()


def test_explicit_untranslatable_source_is_a_usage_error(isolated, monkeypatch, capsys):
    code = _run(monkeypatch, _lindat(), str(isolated / "sample.alto.xml"), "--alto", "--source_lang", "hu")
    assert code == EXIT_USAGE
    out = capsys.readouterr().out
    assert "Cannot translate hu → en" in out and "--source_lang" in out


def test_untranslatable_target_is_refused(isolated, monkeypatch, capsys):
    argv = (str(isolated / "sample.alto.xml"), "--alto", "--source_lang", "cs", "--target_lang", "it")
    assert _run(monkeypatch, _lindat(), *argv) == EXIT_USAGE
    assert "no 'cs-it' model" in capsys.readouterr().out


def test_unsupported_default_source_lang_is_refused_in_auto_mode(isolated, monkeypatch, capsys):
    argv = (str(isolated / "sample.alto.xml"), "--alto", "--source_lang", "auto", "--default-source-lang", "la")
    assert _run(monkeypatch, _lindat(), *argv) == EXIT_USAGE
    assert "--default-source-lang" in capsys.readouterr().out


def test_a_missing_vocabulary_is_a_usage_error(isolated, monkeypatch, capsys):
    argv = (str(isolated / "sample.alto.xml"), "--alto", "--source_lang", "cs", "--vocabulary", "nope.csv")
    assert _run(monkeypatch, _lindat(), *argv) == EXIT_USAGE
    assert "nope.csv does not exist" in capsys.readouterr().out


def test_a_backend_that_cannot_start_is_a_usage_error(isolated, monkeypatch, capsys):
    def broken(name, **kwargs):
        raise TranslationError("LLM_EXTRA_BODY is not valid JSON")

    monkeypatch.setattr(main_module, "get_backend", broken)
    monkeypatch.setattr("sys.argv", ["main.py", str(isolated / "sample.alto.xml"), "--alto", "--source_lang", "cs"])
    assert main_module.main() == EXIT_USAGE
    assert "cannot start: LLM_EXTRA_BODY" in capsys.readouterr().out


# ── the service ───────────────────────────────────────────────────────────────


def _post(source_lang=None, target_lang=None):
    query = "&".join(f"{k}={v}" for k, v in (("source_lang", source_lang), ("target_lang", target_lang)) if v)
    return TestClient(app).post(
        f"/translate?{query}",
        files={"file": ("test.alto.xml", _ALTO.encode(), "application/xml")},
        data={"is_alto": "true"},
    )


@pytest.mark.parametrize(
    ("source_lang", "target_lang", "field", "value"),
    [("hu", "en", "source_lang", "hu"), ("cs", "it", "source_lang", "cs")],
)
def test_translate_refuses_an_untranslatable_pair_with_422(source_lang, target_lang, field, value):
    with patch("service.api.models", {"translator": _lindat(), "identifier": MagicMock(), "xpaths_list": []}):
        response = _post(source_lang, target_lang)
    assert response.status_code == 422
    body = response.json()
    assert body["reason"] is None and body["cause"] == "unsupported_language_pair"
    assert body["errors"][0]["field"] == field and body["errors"][0]["value"] == value
    assert f"Cannot translate {source_lang} → {target_lang}" in body["detail"]


def test_auto_mode_checks_the_fallback_language(monkeypatch):
    monkeypatch.setenv("DEFAULT_SOURCE_LANG", "la")
    with patch("service.api.models", {"translator": _lindat(), "identifier": MagicMock(), "xpaths_list": []}):
        response = _post("auto")
    assert response.status_code == 422
    error = response.json()["errors"][0]
    assert error["field"] == "DEFAULT_SOURCE_LANG" and "cs" in error["supported_source_langs"]
