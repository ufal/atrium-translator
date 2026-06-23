"""
processors/llm_translator.py – OpenAI-compatible chat-completions translation
backend (issue #4, Phase 1).

One adapter, many (free) providers
----------------------------------
A single ``/v1/chat/completions`` client reaches a wide range of free or
low-cost LLM API tiers through environment configuration only — no vendor SDK,
so the base install stays light (see ``requirements-llm.txt``):

    LLM_BASE_URL    Base URL of the OpenAI-compatible endpoint, e.g.
                      https://openrouter.ai/api/v1
                      https://generativelanguage.googleapis.com/v1beta/openai
                      https://api.mistral.ai/v1
                      https://api.groq.com/openai/v1
                      http://localhost:11434/v1            (Ollama)
    LLM_MODEL       Model id served there, e.g.
                      "meta-llama/llama-3.3-70b-instruct:free"
    LLM_API_KEY     Bearer token (omit for a keyless local server).
    LLM_PROVIDER    Optional free-text label, recorded in paradata only.
    LLM_LANGUAGES   Optional comma-separated ISO codes the model is trusted for;
                    advertised via supported_languages() (empty -> []).
    LLM_MIN_INTERVAL_S / LLM_MAX_RETRIES / LLM_BACKOFF_BASE_S
                    Rate-limit + retry knobs (mirror the LINDAT_* ones).

Why this backend exists
-----------------------
CUBBITT (the default ``lindat`` backend) is cs-centric and CC BY-NC-SA.  A free
LLM API gives broad language coverage with zero local infrastructure, making it
the chosen prototype for the translator-base evaluation in issue #4.  See
``docs/translation-backends.md`` for the comparison and the licensing caveats
(an LLM API's output terms are governed by the provider ToS, not a CC/OSS
licence).

OCR-faithfulness guardrails
---------------------------
Archival OCR text is noisy and must be translated, not "fixed".  This adapter
mirrors ``translator.py``'s fail-loud philosophy:
  * temperature 0 and a strict system prompt (translate only, preserve numbers /
    codes / line structure, do not explain or correct garbled tokens);
  * a post-check that raises :class:`TranslationError` on an empty response or an
    implausible output/input length ratio, so a hallucinated or truncated chunk
    skips the file instead of corrupting the archive.

Glossary (``supports_glossary = True``)
--------------------------------------
When a vocabulary CSV is supplied, the relevant term pairs are injected into the
prompt, so controlled terminology is handled natively by the model and the
UDPipe Tag-and-Protect workaround is *not* used for this backend.
"""

from __future__ import annotations

import os
from pathlib import Path

import requests

from .chunking import chunk_text
from .http_retry import Throttle, request_with_retry
from .translator import TranslationError
from .vocab import get_matching_terms, load_vocabulary


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


# Output-faithfulness guardrail thresholds (see _guard_output).
_MIN_RATIO_CHARS = _env_int("LLM_GUARD_MIN_CHARS", 16)
_MIN_LEN_RATIO = _env_float("LLM_GUARD_MIN_RATIO", 0.25)
_MAX_LEN_RATIO = _env_float("LLM_GUARD_MAX_RATIO", 4.0)
# Cap on glossary lines injected into a single prompt (keep the request small).
_MAX_GLOSSARY_TERMS = _env_int("LLM_MAX_GLOSSARY_TERMS", 40)
# Default output-token cap — prevents silent truncation by providers that impose
# their own hard ceiling without returning an error (M2).
_LLM_MAX_TOKENS = _env_int("LLM_MAX_TOKENS", 2048)


class LLMTranslator:
    """OpenAI-compatible chat-completions backend.

    Implements the ``processors.backend.TranslationBackend`` Protocol
    (``name`` / ``supports_glossary`` / ``translate`` / ``supported_languages``)
    plus the small surface ``main.process_single_file`` relies on
    (``vocabulary`` / ``reset_protected_count`` / ``protected_count``), so it is
    a drop-in replacement for ``LindatTranslator``.
    """

    name: str = "openai_compatible"
    # Terminology is handled natively via prompt glossary injection.
    supports_glossary: bool = True

    def __init__(
        self,
        vocab_path=None,
        *,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        provider: str | None = None,
        languages: list | None = None,
    ) -> None:
        self.base_url = (base_url if base_url is not None else os.environ.get("LLM_BASE_URL", "")).rstrip("/")
        self.model = model if model is not None else os.environ.get("LLM_MODEL", "")
        self.api_key = api_key if api_key is not None else os.environ.get("LLM_API_KEY", "")
        self.provider = provider if provider is not None else os.environ.get("LLM_PROVIDER", "")
        # Per-instance override; falls back to the module-level default so the
        # env var is respected whether or not it was set before import (M2).
        self.max_tokens: int = _env_int("LLM_MAX_TOKENS", _LLM_MAX_TOKENS)

        if languages is not None:
            self._languages = list(languages)
        else:
            env_langs = os.environ.get("LLM_LANGUAGES", "")
            self._languages = [c.strip() for c in env_langs.split(",") if c.strip()]

        self._throttle = Throttle(_env_float("LLM_MIN_INTERVAL_S", 0.0))
        self._max_retries = _env_int("LLM_MAX_RETRIES", 4)
        self._backoff_base_s = _env_float("LLM_BACKOFF_BASE_S", 1.0)

        # Vocabulary -> prompt glossary (no UDPipe / Tag-and-Protect here).
        self.vocabulary: dict = load_vocabulary(Path(vocab_path)) if vocab_path else {}
        if self.vocabulary:
            print("[INFO] Vocabulary loaded. LLM prompt-glossary injection enabled.")

        # Per-document tally of glossary terms injected (mirrors LindatTranslator's
        # protected_count so main.py's per-document stats keep working).
        self._protected_count: int = 0

    # ── TranslationBackend Protocol ───────────────────────────────────────────

    def translate(self, text: str, src_lang: str, tgt_lang: str = "en") -> str:
        if not text or not text.strip() or src_lang == tgt_lang:
            return text
        self._require_config()
        chunks = chunk_text(text)
        translated_chunks = [self._translate_chunk(chunk, src_lang, tgt_lang) for chunk in chunks]
        return "\n".join(translated_chunks)

    def supported_languages(self) -> list:
        """ISO codes the operator declared trustworthy (``LLM_LANGUAGES``).

        Empty by default: a general LLM nominally covers many languages, but the
        repo languages it is *trusted* for is a deployment decision, so the
        pipeline makes no coverage claim unless one is configured.
        """
        return list(self._languages)

    # ── main.process_single_file compatibility surface ────────────────────────

    def reset_protected_count(self) -> None:
        self._protected_count = 0

    @property
    def protected_count(self) -> int:
        return self._protected_count

    def license_components(self, vocab_loaded: bool = False) -> list:
        """Paradata component names this backend contributes (see para_config.txt).

        The LLM API itself maps to ``llm_api`` (provider ToS — deliberately
        treated conservatively by para_licenses).  A loaded glossary still
        derives from the AMCR/TEATER vocabulary, so those are recorded too.
        """
        comps = ["llm_api"]
        if vocab_loaded:
            comps += ["amcr_vocab", "teater_data"]
        return comps

    # ── internals ─────────────────────────────────────────────────────────────

    def _require_config(self) -> None:
        missing = [n for n, v in (("LLM_BASE_URL", self.base_url), ("LLM_MODEL", self.model)) if not v]
        if missing:
            raise TranslationError(
                "LLMTranslator is not configured: missing " + ", ".join(missing) + ". "
                "Set the LLM_* environment variables (see docs/translation-backends.md)."
            )

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _system_prompt(self, src_lang: str, tgt_lang: str) -> str:
        return (
            f"You are a professional translator of archival OCR text. "
            f"Translate the user's text from language '{src_lang}' to '{tgt_lang}'. "
            "Output ONLY the translation — no preamble, notes, quotes, or explanation. "
            "Preserve all numbers, dates, measurements, codes, identifiers, and the original "
            "line and paragraph structure exactly. Do not correct, complete, or comment on "
            "garbled, partial, or misspelled OCR tokens; translate what is present. "
            "If a glossary is given, use those exact target terms."
        )

    def _glossary_lines(self, text: str) -> list:
        if not self.vocabulary:
            return []
        # get_matching_terms uses word-boundary regex to avoid short keys
        # over-matching inside unrelated words (e.g. "kost" ∉ "kostel") — L1.
        pairs = get_matching_terms(text, self.vocabulary)
        pairs.sort(key=lambda kv: len(kv[0]), reverse=True)
        pairs = pairs[:_MAX_GLOSSARY_TERMS]
        return [f"{src} = {tgt}" for src, tgt in pairs]

    def _build_messages(self, text: str, src_lang: str, tgt_lang: str) -> list:
        messages = [{"role": "system", "content": self._system_prompt(src_lang, tgt_lang)}]
        glossary = self._glossary_lines(text)
        if glossary:
            self._protected_count += len(glossary)
            messages.append(
                {
                    "role": "system",
                    "content": "Glossary — translate each source term on the left exactly as the "
                    "right-hand target term:\n" + "\n".join(glossary),
                }
            )
        messages.append({"role": "user", "content": text})
        return messages

    def _translate_chunk(self, chunk: str, src_lang: str, tgt_lang: str) -> str:
        payload = {
            "model": self.model,
            "messages": self._build_messages(chunk, src_lang, tgt_lang),
            "temperature": 0,
            "max_tokens": self.max_tokens,
        }
        url = f"{self.base_url}/chat/completions"
        response = request_with_retry(
            lambda: requests.post(url, json=payload, headers=self._headers(), timeout=120),
            max_retries=self._max_retries,
            backoff_base_s=self._backoff_base_s,
            throttle=self._throttle,
            error_cls=TranslationError,
            label=f"LLM translation ({self.provider or self.model or 'openai_compatible'})",
        )
        translated = self._extract_content(response)
        self._guard_output(chunk, translated)
        return translated

    @staticmethod
    def _extract_content(response) -> str:
        try:
            data = response.json()
        except ValueError as e:
            raise TranslationError(f"LLM response was not valid JSON: {e}")
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise TranslationError(f"Unexpected LLM response shape ({e}); body starts: {str(data)[:200]}")
        return (content or "").strip()

    @staticmethod
    def _guard_output(source: str, translated: str) -> None:
        if not translated or not translated.strip():
            raise TranslationError("LLM returned an empty translation for a non-empty source chunk.")
        src_len = len(source.strip())
        if src_len >= _MIN_RATIO_CHARS:
            ratio = len(translated.strip()) / src_len
            if ratio < _MIN_LEN_RATIO or ratio > _MAX_LEN_RATIO:
                raise TranslationError(
                    f"LLM output/input length ratio {ratio:.2f} outside "
                    f"[{_MIN_LEN_RATIO}, {_MAX_LEN_RATIO}] — suspected hallucination or truncation."
                )
