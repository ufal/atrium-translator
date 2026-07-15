"""
tests/test_identifier.py – Unit tests for processors/identifier.py.

The module imports fasttext + huggingface_hub at module scope and downloads a
model in ``__init__``; both the download and the FastText loader are patched so
the tests are hermetic (no network, no model file). ``detect`` is then driven
with a mocked model.
"""

from unittest.mock import MagicMock

import pytest

pytest.importorskip("fasttext")
pytest.importorskip("huggingface_hub")

from processors import identifier  # noqa: E402
from processors.identifier import LanguageIdentifier  # noqa: E402


@pytest.fixture
def ident(monkeypatch):
    monkeypatch.setattr(identifier, "hf_hub_download", lambda *a, **k: "dummy-model.bin")
    monkeypatch.setattr(identifier.fasttext, "load_model", lambda *a, **k: MagicMock())
    return LanguageIdentifier()


def test_code_map_iso3_to_iso1():
    assert LanguageIdentifier.CODE_MAP["ces"] == "cs"
    assert LanguageIdentifier.CODE_MAP["deu"] == "de"
    assert LanguageIdentifier.CODE_MAP["eng"] == "en"


def test_detect_maps_iso3_label_to_iso1(ident):
    ident.model.predict.return_value = (["__label__ces_Latn"], [0.99])
    lang, score = ident.detect("nějaký český text")
    assert lang == "cs"
    assert score == pytest.approx(0.99)


def test_detect_unknown_iso3_passes_through(ident):
    ident.model.predict.return_value = (["__label__jpn_Jpan"], [0.80])
    lang, _ = ident.detect("some text")
    assert lang == "jpn"


def test_detect_empty_text_defaults_to_en(ident):
    assert ident.detect("   ") == ("en", 0.0)


def test_detect_model_none_defaults_to_en(ident):
    ident.model = None
    assert ident.detect("text") == ("en", 0.0)


def test_detect_prediction_error_defaults_to_en(ident):
    ident.model.predict.side_effect = RuntimeError("boom")
    assert ident.detect("text") == ("en", 0.0)
