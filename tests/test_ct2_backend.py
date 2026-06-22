"""
tests/test_ct2_backend.py – Hermetic tests for the CTranslate2 self-host backend
(processors/ct2_translator.py, issue #4 Phase 3).

No ctranslate2, no sentencepiece, no model, no network: only behaviour that does
not require the (lazily loaded) engine is exercised here.
"""

import pytest

from processors.backend import TranslationBackend
from processors.ct2_translator import CT2Translator
from processors.translator import TranslationError


def test_name_and_protocol():
    b = CT2Translator(model_dir="/tmp/fake", family="eurollm")
    assert b.name == "ct2"
    assert isinstance(b, TranslationBackend)


def test_eurollm_supports_glossary_madlad_does_not():
    assert CT2Translator(model_dir="/x", family="eurollm").supports_glossary is True
    assert CT2Translator(model_dir="/x", family="madlad").supports_glossary is False
    assert CT2Translator(model_dir="/x", family="nllb").supports_glossary is False


def test_pipeline_compat_surface_present():
    b = CT2Translator(model_dir="/x")
    assert hasattr(b, "vocabulary")
    assert callable(b.reset_protected_count)
    assert isinstance(b.protected_count, int)
    assert callable(b.license_components)


def test_trivial_short_circuits_need_no_engine():
    b = CT2Translator(model_dir="/x")
    assert b.translate("   ", "cs", "en") == "   "
    assert b.translate("text", "en", "en") == "text"


def test_missing_model_dir_raises_on_translate():
    b = CT2Translator(model_dir="")
    with pytest.raises(TranslationError, match="not configured"):
        b.translate("Ahoj", "cs", "en")


def test_license_components_permissive_stack():
    eurollm = CT2Translator(model_dir="/x", family="eurollm")
    assert eurollm.license_components(False) == ["ctranslate2", "eurollm"]
    assert eurollm.license_components(True) == ["ctranslate2", "eurollm", "amcr_vocab", "teater_data"]
    madlad = CT2Translator(model_dir="/x", family="madlad")
    assert madlad.license_components(False) == ["ctranslate2", "madlad400"]


def test_supported_languages_from_kwarg_and_env(monkeypatch):
    assert CT2Translator(model_dir="/x", languages=["cs", "en"]).supported_languages() == ["cs", "en"]
    monkeypatch.setenv("CT2_LANGUAGES", "de, fr")
    assert CT2Translator(model_dir="/x").supported_languages() == ["de", "fr"]


def test_ctranslate2_missing_is_reported_clearly():
    """When ctranslate2 is absent, translate() raises a clear install hint
    rather than a bare ImportError (only meaningful if ctranslate2 is not
    installed in the test env)."""
    pytest.importorskip
    b = CT2Translator(model_dir="/definitely/not/a/real/model", family="eurollm")
    try:
        import ctranslate2  # noqa: F401
    except ImportError:
        with pytest.raises(TranslationError):
            b.translate("Ahoj světe dnes", "cs", "en")
