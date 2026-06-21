"""
tests/test_backend.py – Tests for the pluggable backend interface.

No network, no models, no GPU required.
"""

from unittest.mock import patch

import pytest

from processors.backend import TranslationBackend, get_backend


@patch("processors.translator.requests.get")
def test_default_backend_is_lindat(mock_get):
    """get_backend() with no arguments returns a LindatTranslator."""
    mock_get.return_value.status_code = 404
    backend = get_backend()
    assert type(backend).__name__ == "LindatTranslator"


@patch("processors.translator.requests.get")
def test_explicit_lindat_backend(mock_get):
    """get_backend("lindat") returns a LindatTranslator."""
    mock_get.return_value.status_code = 404
    backend = get_backend("lindat")
    assert type(backend).__name__ == "LindatTranslator"


def test_unknown_backend_raises():
    """get_backend with an unregistered name raises ValueError."""
    with pytest.raises(ValueError, match="Unknown translation backend"):
        get_backend("nonexistent")


@patch("processors.translator.requests.get")
def test_backend_has_translate(mock_get):
    """The returned backend has a callable translate method."""
    mock_get.return_value.status_code = 404
    backend = get_backend()
    assert callable(backend.translate)


@patch("processors.translator.requests.get")
def test_lindat_satisfies_protocol(mock_get):
    """LindatTranslator structurally satisfies TranslationBackend."""
    mock_get.return_value.status_code = 404
    from processors.translator import LindatTranslator
    translator = LindatTranslator(vocab_path=None)
    assert isinstance(translator, TranslationBackend)


@patch("processors.translator.requests.get")
def test_env_var_selects_backend(mock_get, monkeypatch):
    """TRANSLATION_BACKEND env var is respected when name is None."""
    mock_get.return_value.status_code = 404
    monkeypatch.setenv("TRANSLATION_BACKEND", "lindat")
    backend = get_backend()
    assert type(backend).__name__ == "LindatTranslator"


def test_env_var_unknown_raises(monkeypatch):
    """TRANSLATION_BACKEND set to unknown name raises ValueError."""
    monkeypatch.setenv("TRANSLATION_BACKEND", "bogus")
    with pytest.raises(ValueError, match="Unknown translation backend"):
        get_backend()
