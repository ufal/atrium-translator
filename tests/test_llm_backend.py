"""
tests/test_llm_backend.py – Tests for the OpenAI-compatible LLM backend
(processors/llm_translator.py, issue #4).

No network, no models, no GPU required: the HTTP client is patched and the
backend is configured via constructor kwargs / monkeypatched env vars.
"""

from unittest.mock import MagicMock, patch

import pytest

from processors.backend import TranslationBackend, get_backend
from processors.llm_translator import LLMTranslator
from processors.translator import TranslationError


def _resp(status=200, payload=None, text=""):
    r = MagicMock()
    r.status_code = status
    if payload is not None:
        r.json.return_value = payload
    else:
        r.json.side_effect = ValueError("no json body")
    r.text = text
    return r


def _completion(content):
    return {"choices": [{"message": {"content": content}}]}


def _backend(**kw):
    kw.setdefault("base_url", "https://example.test/v1")
    kw.setdefault("model", "dummy-model")
    return LLMTranslator(**kw)


# ── registry / protocol ───────────────────────────────────────────────────────


def test_registered_in_factory():
    backend = get_backend("openai_compatible", base_url="https://x/v1", model="m")
    assert isinstance(backend, LLMTranslator)


def test_name_and_glossary_flag():
    b = _backend()
    assert b.name == "openai_compatible"
    assert b.supports_glossary is True


def test_satisfies_protocol():
    assert isinstance(_backend(), TranslationBackend)


def test_pipeline_compat_surface_present():
    """main.process_single_file relies on these members existing."""
    b = _backend()
    assert hasattr(b, "vocabulary")
    assert callable(b.reset_protected_count)
    assert isinstance(b.protected_count, int)
    assert callable(b.license_components)


# ── trivial short-circuits ─────────────────────────────────────────────────────


def test_empty_input_returned_unchanged():
    assert _backend().translate("   ", "cs", "en") == "   "


def test_same_lang_short_circuits():
    assert _backend().translate("text", "en", "en") == "text"


# ── happy path ─────────────────────────────────────────────────────────────────


@patch("processors.llm_translator.requests.post")
def test_translate_extracts_content(mock_post):
    mock_post.return_value = _resp(200, _completion("Hello world"))
    out = _backend(api_key="secret").translate("Ahoj světe", "cs", "en")
    assert out == "Hello world"
    assert mock_post.called


@patch("processors.llm_translator.requests.post")
def test_authorization_header_sent_when_key_present(mock_post):
    captured = {}

    def capture(url, json=None, headers=None, timeout=None):
        captured["headers"] = headers
        return _resp(200, _completion("Hi"))

    mock_post.side_effect = capture
    _backend(api_key="tok-123").translate("Ahoj", "cs", "en")
    assert captured["headers"]["Authorization"] == "Bearer tok-123"


@patch("processors.llm_translator.requests.post")
def test_payload_uses_temperature_zero_and_model(mock_post):
    captured = {}

    def capture(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return _resp(200, _completion("Hi"))

    mock_post.side_effect = capture
    _backend(model="my-model").translate("Ahoj", "cs", "en")
    assert captured["json"]["temperature"] == 0
    assert captured["json"]["model"] == "my-model"


# ── configuration guard ────────────────────────────────────────────────────────


@patch("processors.llm_translator.requests.post")
def test_missing_config_raises_before_any_request(mock_post):
    b = LLMTranslator(base_url="", model="")
    with pytest.raises(TranslationError, match="not configured"):
        b.translate("Ahoj", "cs", "en")
    mock_post.assert_not_called()


# ── OCR-faithfulness guardrails ────────────────────────────────────────────────


@patch("processors.llm_translator.requests.post")
def test_empty_completion_raises(mock_post):
    mock_post.return_value = _resp(200, _completion("   "))
    with pytest.raises(TranslationError, match="empty translation"):
        _backend().translate("Nějaký delší český text k překladu.", "cs", "en")


@patch("processors.llm_translator.requests.post")
def test_length_ratio_guard_raises_on_runaway_output(mock_post):
    mock_post.return_value = _resp(200, _completion("word " * 200))
    with pytest.raises(TranslationError, match="length ratio"):
        _backend().translate("Stručný popis nálezu.", "cs", "en")


@patch("processors.llm_translator.requests.post")
def test_short_source_skips_ratio_guard(mock_post):
    """Sources below the min-char threshold are not ratio-checked (avoids false
    positives on tiny metadata fields)."""
    mock_post.return_value = _resp(200, _completion("ok"))
    assert _backend().translate("Ahoj", "cs", "en") == "ok"


@patch("processors.llm_translator.requests.post")
def test_malformed_response_shape_raises(mock_post):
    mock_post.return_value = _resp(200, {"unexpected": "shape"})
    with pytest.raises(TranslationError, match="Unexpected LLM response shape"):
        _backend().translate("Ahoj", "cs", "en")


# ── glossary injection (supports_glossary path) ────────────────────────────────


@patch("processors.llm_translator.requests.post")
def test_glossary_injected_into_prompt(mock_post):
    captured = {}

    def capture(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return _resp(200, _completion("the church"))

    mock_post.side_effect = capture
    b = _backend()
    b.vocabulary = {"kostel": "church"}
    b.translate("Našli jsme kostel poblíž.", "cs", "en")

    glossary_msgs = [m for m in captured["json"]["messages"] if "Glossary" in m.get("content", "")]
    assert glossary_msgs, "glossary system message missing"
    assert "kostel = church" in glossary_msgs[0]["content"]
    assert b.protected_count >= 1


@patch("processors.llm_translator.requests.post")
def test_no_glossary_message_when_vocab_absent(mock_post):
    captured = {}

    def capture(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return _resp(200, _completion("a church"))

    mock_post.side_effect = capture
    _backend().translate("Našli jsme kostel poblíž.", "cs", "en")
    assert not [m for m in captured["json"]["messages"] if "Glossary" in m.get("content", "")]


# ── retry / throttle reuse (shared http_retry helper) ──────────────────────────


@patch("processors.llm_translator.requests.post")
def test_retry_then_success(mock_post):
    mock_post.side_effect = [_resp(429), _resp(200, _completion("ok"))]
    b = _backend()
    b._backoff_base_s = 0.0  # no real sleep
    assert b.translate("Ahoj", "cs", "en") == "ok"
    assert mock_post.call_count == 2


@patch("processors.llm_translator.requests.post")
def test_persistent_5xx_raises_translation_error(mock_post):
    mock_post.return_value = _resp(503)
    b = _backend()
    b._max_retries = 2
    b._backoff_base_s = 0.0
    with pytest.raises(TranslationError):
        b.translate("Ahoj", "cs", "en")
    assert mock_post.call_count == 3  # 1 + 2 retries


@patch("processors.llm_translator.requests.post")
def test_non_retryable_4xx_raises_immediately(mock_post):
    mock_post.return_value = _resp(400)
    b = _backend()
    with pytest.raises(TranslationError):
        b.translate("Ahoj", "cs", "en")
    assert mock_post.call_count == 1


# ── supported_languages / stats / licensing ────────────────────────────────────


def test_supported_languages_from_env(monkeypatch):
    monkeypatch.setenv("LLM_LANGUAGES", "cs, en ,de")
    assert _backend().supported_languages() == ["cs", "en", "de"]


def test_supported_languages_empty_by_default(monkeypatch):
    monkeypatch.delenv("LLM_LANGUAGES", raising=False)
    assert _backend().supported_languages() == []


def test_supported_languages_from_kwarg():
    assert _backend(languages=["cs", "uk"]).supported_languages() == ["cs", "uk"]


def test_reset_and_protected_count():
    b = _backend()
    b._protected_count = 5
    b.reset_protected_count()
    assert b.protected_count == 0


def test_license_components_with_and_without_vocab():
    b = _backend()
    assert b.license_components(False) == ["llm_api"]
    assert b.license_components(True) == ["llm_api", "amcr_vocab", "teater_data"]


def test_vocab_loaded_from_csv(tmp_path):
    p = tmp_path / "v.csv"
    p.write_text("source_lemma,target_translation\nkostel,church\n", encoding="utf-8")
    b = _backend(vocab_path=str(p))
    assert b.vocabulary.get("kostel") == "church"


# ── long input is chunked (shared chunker reuse) ───────────────────────────────


@patch("processors.llm_translator.requests.post")
def test_long_input_is_chunked(mock_post):
    mock_post.side_effect = lambda url, json=None, headers=None, timeout=None: _resp(
        200, _completion(json["messages"][-1]["content"])
    )
    b = _backend()
    long_text = ". ".join(f"Sentence number {i} of the document" for i in range(400))
    b.translate(long_text, "cs", "en")
    assert mock_post.call_count > 1  # split into multiple chunks
