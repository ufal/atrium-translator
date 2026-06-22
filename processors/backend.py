"""
processors/backend.py – Pluggable translation backend interface.

Defines a ``TranslationBackend`` Protocol that captures the contract the
pipeline depends on, matching the design recorded in
``docs/translation-backends.md`` (issue #4):

    name: str
    supports_glossary: bool
    translate(text, src_lang, tgt_lang="en") -> str
    supported_languages() -> list[str]

``supports_glossary`` is the load-bearing flag the design relies on: a
glossary-native backend (e.g. the ``openai_compatible`` LLM adapter, which
injects term pairs into the prompt) sets it ``True`` so the pipeline can hand
terminology to the backend instead of running the in-process Tag-and-Protect
workaround.  ``LindatTranslator`` (CUBBITT) sets it ``False`` because CUBBITT has
no glossary API.

A ``get_backend`` factory returns the configured implementation.  The default
backend is ``"lindat"`` (the existing ``LindatTranslator``), which structurally
satisfies the Protocol without any behavioural changes to
``processors/translator.py``.  ``"openai_compatible"`` selects the free/low-cost
LLM API adapter (``processors/llm_translator.py``).

Beyond the Protocol, ``main.process_single_file`` relies on a small
compatibility surface every backend must expose: ``vocabulary`` (dict),
``reset_protected_count()`` and the ``protected_count`` property, and
(optionally) ``license_components(vocab_loaded)`` so paradata records the
components the backend actually exercised.  Both shipped backends provide all of
these.

See ``docs/translation-backends.md`` for the full evaluation of candidate
backends and integration guidance.
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable


@runtime_checkable
class TranslationBackend(Protocol):
    """Contract every translation backend must satisfy.

    ``translate`` is the single coupling point used by
    ``utils.process_alto_xml``, ``utils.process_metadata_xml``,
    ``service/api.py``, and ``main.process_single_file``.  ``name`` and
    ``supports_glossary`` are class/instance attributes; ``supported_languages``
    lets the pipeline check coverage and fall back to another backend for
    unsupported pairs (see the "Language coverage gaps" section of the design
    doc).

    NOTE: because this Protocol is ``@runtime_checkable`` and declares data
    members, ``isinstance(obj, TranslationBackend)`` checks for the *presence*
    of ``name`` and ``supports_glossary`` too — so every backend must expose
    them (``LindatTranslator`` does).
    """

    name: str
    supports_glossary: bool

    def translate(self, text: str, src_lang: str, tgt_lang: str = "en") -> str: ...

    def supported_languages(self) -> list[str]: ...


_REGISTRY: dict[str, type] = {}


def _ensure_registry() -> None:
    if _REGISTRY:
        return
    from .llm_translator import LLMTranslator
    from .translator import LindatTranslator

    _REGISTRY["lindat"] = LindatTranslator
    _REGISTRY["openai_compatible"] = LLMTranslator
    # Phase 3 (issue #4): a CTranslate2 self-host backend lives in
    # processors/ct2_translator.py.  It is intentionally NOT registered by
    # default so importing this module never pulls in the heavy ctranslate2 /
    # sentencepiece dependencies.  To enable it, install requirements-ct2.txt and
    # uncomment the two lines below:
    # from .ct2_translator import CT2Translator
    # _REGISTRY["ct2"] = CT2Translator


def get_backend(name: str | None = None, **kwargs) -> TranslationBackend:
    """Return a ``TranslationBackend`` instance for *name*.

    *name* defaults to the ``TRANSLATION_BACKEND`` environment variable, then
    to ``"lindat"``.  Unknown names raise ``ValueError``.

    *kwargs* are forwarded to the backend constructor (e.g. ``vocab_path``
    for ``LindatTranslator``).
    """
    _ensure_registry()
    if name is None:
        name = os.environ.get("TRANSLATION_BACKEND", "lindat")
    name = name.lower().strip()
    if name not in _REGISTRY:
        available = ", ".join(sorted(_REGISTRY))
        raise ValueError(
            f"Unknown translation backend {name!r}. "
            f"Available: {available}. "
            f"See docs/translation-backends.md for integration guidance."
        )
    return _REGISTRY[name](**kwargs)
